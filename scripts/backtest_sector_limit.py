#!/usr/bin/env python
"""The sector limit, on real prices, for the first time.

``SectorExposureRule`` has shipped with 20 passing tests since Phase 8 and had
never been installed in a run against the corpus, because ``sectors`` held
**zero rows** -- every name would have landed in the single ``UNCLASSIFIED``
bucket and a 30% cap would have held the entire portfolio to 30%.
``derive_sectors_from_sic.py`` fixed that on 2026-10-17 by deriving the table
from SIC codes already in EDGAR filing headers: **16,768 interval rows over
13,738 securities, 76.4% of those with prices, every row citing its
accession.**

**The rule takes a flat ``Mapping[int, str]``, and the table is intervals.**
That mismatch is a point-in-time decision, not a formatting one, and it is made
explicitly here rather than defaulted: the map is built **as of the first
session of the window**, so a name reclassified mid-run keeps the sector it had
when the backtest started. 881 securities changed SIC code at some point, so
this is a real simplification and not a hypothetical one. A live system would
re-read the map as the clock advanced; stating the choice is the honest
alternative to pretending it does not exist.

The run compares the same moving-average baseline with the limit installed and
without, and reports what it refused.

``risk/contextual.py`` has shipped ``CorrelationRule`` with 20 passing tests
since Phase 8 and it has **never been installed in a run against the corpus**.
The Phase 8 audit of 2026-10-17 found why, and the reason is the rule working
rather than failing: *an unknown correlation blocks rather than permits*, so
installing it without a correlation source would refuse every new position and
halt trading. No source had been built, so the rule was left out — and every
real-data backtest this project has run, including this week's walk-forward,
also passed ``correlation_weight=0.0`` to the ranker.

**That was never written down.** The line immediately above it in
``baselines.py`` carries a careful note about §38's cost-guard defect; this one
disabled a named Phase 8 requirement across every backtest and carried nothing.
A reader would reasonably assume diversification had been tested and found not
to matter.

**The sector half was closed the same day.** When this script was written the
corpus's ``sectors`` table held **zero rows** against 17,433 securities with
prices, so every name landed in one pooled ``UNCLASSIFIED`` bucket and a 30%
sector cap would have held the whole portfolio to 30%. That was a missing-data
gap, not a code gap. ``derive_sectors_from_sic.py`` closed it from SIC codes
already in the corpus on 2026-10-17; see ``backtest_sector_limit.py``.

What this script does
---------------------

Builds a real ``MatrixCorrelationSource`` from the corpus -- pairwise Pearson
correlation of daily returns over ``--lookback`` sessions, computed **once at
the start of the window and held fixed**, which is a stated simplification: a
live system would recompute it as the window rolls, and a correlation measured
across the whole span uses information from after some decisions. It is
acceptable here because the question is whether the *rule* functions on real
evidence, not what the rule earns.

Then runs the same moving-average baseline twice -- once with the rule
installed and once without -- and reports what the limit refused.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector
    FROM sectors
    WHERE scheme = 'SIC'
      AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


def sector_map(session: Session, as_of: dt.date) -> dict[int, str]:
    """The classification in force on ``as_of``, from the interval table.

    One row per instrument by construction: the intervals do not overlap,
    because ``derive_sectors_from_sic.py`` closes each with the next
    observation's date and the schema enforces ``valid_to > valid_from``.
    """
    rows = session.execute(SECTORS_AS_OF, {"as_of": as_of.isoformat()}).all()
    return {int(instrument_id): str(sector) for instrument_id, sector in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument(
        "--tickers", default="AAPL,MSFT,IBM,KO,JNJ,XOM,GE,WMT,PG,INTC,CSCO,PFE,MRK,DIS,T"
    )
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.02)
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, default=Decimal(1))
    # Like the correlation run: the configured 0.30 may not bind on a small
    # large-cap universe, and "it ran" would then prove only that it does not
    # crash. This overrides the cap for the EXERCISE and is not a proposal.
    ap.add_argument(
        "--max-sector",
        type=float,
        default=None,
        help="override risk.max_sector_exposure_pct, to make the rule bind",
    )
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    resolved = _universe(session, tickers)
    config = StrategyConfig(name="ma_cross_baseline")
    print(f"universe: {len(resolved)} of {len(tickers)} tickers resolved")

    cap = config.risk.max_sector_exposure_pct if args.max_sector is None else args.max_sector
    risk_config = (
        config.risk
        if args.max_sector is None
        else config.risk.model_copy(update={"max_sector_exposure_pct": cap})
    )

    sectors = sector_map(session, start)
    universe_ids = set(resolved.values())
    mine = {k: v for k, v in sectors.items() if k in universe_ids}
    print(f"sector map as of {start}: {len(sectors):,} instruments classified corpus-wide")
    print(f"  of this universe: {len(mine)} of {len(universe_ids)} classified")
    buckets: dict[str, int] = {}
    for sector in mine.values():
        buckets[sector] = buckets.get(sector, 0) + 1
    unclassified = len(universe_ids) - len(mine)
    if unclassified:
        buckets["UNCLASSIFIED (pooled)"] = unclassified
    for name, count in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"    {name[:46]:<46} {count:>4}")
    print(f"  cap: {cap:.0%} of equity per sector")

    def run(with_rule: bool) -> tuple[int, float]:
        rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
        run_data = CorpusSessionData(
            session=session,
            universe=tuple(sorted(resolved.values())),
            start=start,
            end=end,
            candidate_source=rule,
        )
        rule.splits_on = run_data.splits_on
        rules: list[object] = [
            PortfolioHeatRule(config=config.risk),
            MaxPositionsRule(config=config.risk),
            PositionSizeRule(max_position_pct_of_equity=config.sizing.max_position_pct_of_equity),
            GrossExposureRule(config=config.risk),
        ]
        if with_rule:
            rules.append(SectorExposureRule(config=risk_config, sectors=mine))
        spec = BacktestSpec(
            name="ma_cross_baseline",
            start=start,
            end=end,
            universe=",".join(sorted(resolved)),
            initial_capital=args.capital,
            strategy_config_digest=config.digest,
            cost_model="participation",
            fill_model="bar",
        )
        engine = EventDrivenEngine(
            cycle=PortfolioCycle(
                sizer=RiskBasedSizer(
                    sizing=config.sizing, risk=config.risk, max_participation=args.participation
                ),
                engine=MostRestrictiveEngine(rule_set=tuple(rules), sizing=config.sizing),
                ranker=DiversityAwareRanker(
                    correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
                ),
                ladder=StopLadder(config=config.exits),
            ),
            costs=ParticipationCostModel(config=config.costs),
            fills=BarFillModel(max_participation=args.participation),
            data=run_data,
            analyzer=StandardPerformanceAnalyzer(
                annualisation_factor=config.indicators.annualisation_factor,
                risk_free_rate=args.risk_free,
            ),
            delisting_after_sessions=args.delisting_after,
            delisting_recovery=args.delisting_recovery,
            manifest=_manifest(config, dt.datetime.now(dt.UTC), run_data.bar_count),
        )
        result = engine.run(spec)
        metrics = result.metrics
        return len(result.trades), (metrics.total_return_pct if metrics else 0.0)

    outcomes: dict[bool, tuple[int, float]] = {}
    for label, flag in (("sector limit OFF", False), ("sector limit ON", True)):
        t0 = time.time()
        trades, ret = run(flag)
        outcomes[flag] = (trades, ret)
        print(f"  {label:<22} {trades:>5,} trades   return {ret:>8.2%}   [{time.time() - t0:.1f}s]")
    off_trades, off_return = outcomes[False]
    on_trades, on_return = outcomes[True]
    # Compare BOTH. The first version of this check compared trade counts only
    # and reported "changed NOTHING" for a run where the return moved 5.19% to
    # 5.09%: a sector cap binds by RESIZING a position as readily as by
    # refusing it, and a refusal-only test is blind to the commoner case.
    refused = off_trades - on_trades
    resized = abs(on_return - off_return) > 1e-9
    if refused:
        print(f"\n  The cap refused {refused:,} trades. Exercised on real evidence.")
    elif resized:
        print(
            f"\n  The cap refused no trades but RESIZED: return moved {off_return:.2%}"
            f" -> {on_return:.2%}. Exercised on real evidence."
        )
    else:
        print(
            "\n  The rule changed NOTHING: same trades AND same return. Either the cap\n"
            "  does not bind here or the rule is inert, and this run cannot tell them\n"
            "  apart. Lower --max-sector until something moves."
        )
    print(
        "\nThe sector map is built as of the FIRST session and held fixed, which is a\n"
        "stated simplification: 881 securities changed SIC code at some point. The\n"
        "unclassified 23.6% corpus-wide are pooled into one bucket, so the cap also\n"
        "limits them collectively -- conservative, and a real constraint on what any\n"
        "sector-limited result means."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
