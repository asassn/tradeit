#!/usr/bin/env python
"""The correlation limit, on real prices, for the first time.

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
import itertools
import sys
import time
from decimal import Decimal

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine
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
from tradeit.risk.contextual import CorrelationRule, MatrixCorrelationSource
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig


def build_correlations(
    data: CorpusSessionData, sessions: list[dt.date], lookback: int
) -> dict[tuple[int, int], float]:
    """Pairwise correlation of daily returns over the first ``lookback`` sessions.

    Computed once and held fixed. A live system recomputes as the window rolls;
    this is the stated simplification the module docstring names.
    """
    span = sessions[:lookback]
    closes: dict[int, dict[dt.date, float]] = {}
    for day in span:
        for instrument_id, bar in data.bars(day).items():
            closes.setdefault(instrument_id, {})[day] = float(bar.close)
    series: dict[int, np.ndarray] = {}
    for instrument_id, by_day in closes.items():
        values = np.array([by_day[d] for d in span if d in by_day])
        if len(values) < lookback // 2 or (values <= 0).any():
            continue
        series[instrument_id] = np.diff(values) / values[:-1]
    out: dict[tuple[int, int], float] = {}
    for a, b in itertools.combinations(sorted(series), 2):
        x, y = series[a], series[b]
        n = min(len(x), len(y))
        if n < 20:
            continue
        x, y = x[-n:], y[-n:]
        if x.std() == 0 or y.std() == 0:
            continue
        out[(a, b)] = float(np.corrcoef(x, y)[0, 1])
    return out


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
    ap.add_argument(
        "--lookback", type=int, default=126, help="matches risk.correlation_lookback_sessions"
    )
    # The configured 0.75 does not bind on fifteen large caps -- the first run
    # of this script found 0 of 66 pairs above it, so the rule changed nothing
    # and "it ran" proved only that it does not crash. A binding threshold is
    # needed to show it actually refuses. This overrides the limit for the
    # EXERCISE only and is not a proposal to change risk config.
    ap.add_argument(
        "--max-correlation",
        type=float,
        default=None,
        help="override risk.max_correlation_for_new_position, to make the rule bind",
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

    def make_data() -> tuple[CorpusSessionData, MovingAverageCross]:
        rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
        data = CorpusSessionData(
            session=session,
            universe=tuple(sorted(resolved.values())),
            start=start,
            end=end,
            candidate_source=rule,
        )
        rule.splits_on = data.splits_on
        return data, rule

    data, _ = make_data()
    sessions = list(data.sessions(start, end))
    print(f"  {data.bar_count:,} raw bars over {len(sessions):,} sessions")

    limit = (
        config.risk.max_correlation_for_new_position
        if args.max_correlation is None
        else args.max_correlation
    )
    risk_config = (
        config.risk
        if args.max_correlation is None
        else config.risk.model_copy(update={"max_correlation_for_new_position": limit})
    )

    t0 = time.time()
    matrix = build_correlations(data, sessions, args.lookback)
    print(
        f"correlations: {len(matrix):,} pairs over the first {args.lookback} sessions "
        f"[{time.time() - t0:.1f}s]"
    )
    if matrix:
        values = sorted(matrix.values())
        over = sum(1 for v in values if v > limit)
        print(
            f"  min {values[0]:+.2f}  median {values[len(values) // 2]:+.2f}  max {values[-1]:+.2f}"
        )
        print(f"  {over:,} of {len(values):,} pairs exceed the {limit:.2f} limit")

    def run(with_rule: bool) -> tuple[int, float]:
        run_data, _ = make_data()
        rules: list[object] = [
            PortfolioHeatRule(config=config.risk),
            MaxPositionsRule(config=config.risk),
            PositionSizeRule(max_position_pct_of_equity=config.sizing.max_position_pct_of_equity),
            GrossExposureRule(config=config.risk),
        ]
        if with_rule:
            rules.append(
                CorrelationRule(config=risk_config, source=MatrixCorrelationSource(values=matrix))
            )
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
        # BacktestResult carries no record of declined proposals. The first
        # draft counted `getattr(result, "planned", ())`, which does not exist
        # on that class and silently reported 0 as though it were measured --
        # the exact silent-empty failure this project keeps finding. The rule's
        # effect is read from the trade count instead, which is a real field.
        return len(result.trades), (metrics.total_return_pct if metrics else 0.0)

    print(f"\nrunning twice at a {limit:.2f} limit: installed, and not installed ...")
    outcomes = {}
    for label, flag in (("correlation limit OFF", False), ("correlation limit ON", True)):
        t0 = time.time()
        trades, ret = run(flag)
        outcomes[flag] = trades
        print(f"  {label:<24} {trades:>5,} trades   return {ret:>8.2%}   [{time.time() - t0:.1f}s]")
    if outcomes[True] == outcomes[False]:
        print(
            "\n  The rule changed NOTHING. Either the limit does not bind on this\n"
            "  universe or the rule is inert, and this run cannot tell them apart.\n"
            "  Lower --max-correlation until the counts differ before calling it exercised."
        )
    else:
        print(
            f"\n  The limit refused {outcomes[False] - outcomes[True]:,} trades. The rule is "
            "exercised on real evidence."
        )

    print(
        "\nThe rule fails closed on an unknown pair, so a run with it installed and no\n"
        "source would refuse everything. That is why it had never been run, and why a\n"
        "source had to be built before it could be."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
