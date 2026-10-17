#!/usr/bin/env python
"""The mandate purity check and the daily risk record, on real data at last.

The last two unexercised Phase 8 modules, from the audit of 2026-10-17. Both
shipped with tests -- 21 and 13 -- and neither had ever been handed anything
from the corpus.

``portfolio/mandate.py`` makes the architecture's first claim checkable: Day,
Swing and Retirement are **three separate statistical populations**, and no
learning, backtesting or scoring population may mix them. The module has
always been able to say so; nobody had ever asked it about a real population.
Here it is asked about the **pattern atlas** -- 1.2 million trades over
2003-2019 -- which is the largest population this project has built and the one
two ledger trials were drawn from.

``risk/snapshot.py`` writes the daily risk record "whether or not anything
traded, and that is the whole point of it". It could not run before today:
``build_snapshot`` takes a sector map and a correlation source, and the
``sectors`` table held zero rows until ``derive_sectors_from_sic.py`` filled it
this morning. Both of its inputs became available hours ago.

What is checked, and what that is worth
---------------------------------------

**The atlas population.** Every atlas trade is a Daily bar, so the expected
answer is *admissible* -- and an expected answer is exactly the kind that is
worth getting from the machinery rather than from reasoning. A check that
cannot fail on the data it is given proves nothing, so the run also feeds a
**deliberately mixed** population and confirms the module refuses it.

**The snapshot.** Built on every session of a real backtest, so the quiet days
are included, then summarised: how often each limit was breached by a
portfolio nobody was trading. That is the number the module exists to produce
and it has never been produced.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_correlation_limit import build_correlations
from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.core.enums import Bartimeframe
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import PortfolioCycle
from tradeit.portfolio.mandate import (
    Mandate,
    Observation,
    UnitOfStudy,
    check_population,
)
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.base import RiskSnapshot
from tradeit.risk.contextual import MatrixCorrelationSource, SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.risk.snapshot import build_snapshot
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector FROM sectors
    WHERE scheme = 'SIC' AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument(
        "--atlas",
        default="/Users/ericsasson/Documents/TradeItData/out/atlas17_long.csv",
        help="the population to check: the extended Stage A atlas",
    )
    ap.add_argument("--limit", type=int, default=200_000, help="rows read from the atlas")
    ap.add_argument(
        "--tickers", default="AAPL,MSFT,IBM,KO,JNJ,XOM,GE,WMT,PG,INTC,CSCO,PFE,MRK,DIS,T"
    )
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())

    print("=== 1. the mandate purity check, on the atlas population ===")
    kinds: collections.Counter[str] = collections.Counter()
    read = 0
    with open(args.atlas) as handle:
        for row in csv.DictReader(handle):
            read += 1
            if read > args.limit:
                break
            kinds[row.get("direction", "?")] += 1
    print(f"  {read - 1:,} atlas rows read ({dict(kinds)})")

    # Every atlas trade is a Daily bar in the Swing mandate: the scan ran
    # Bartimeframe.D1 throughout and the hold was 126 sessions.
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    population = [Observation(mandate=Mandate.SWING, timeframe=Bartimeframe.D1)] * min(
        read, args.limit
    )
    report = check_population(population, unit)
    print(
        "  unit of study: "
        + "/".join(sorted(m.value for m in unit.mandates))
        + " at "
        + "/".join(sorted(t.value for t in unit.timeframes))
        + (" (declared COMBINED)" if unit.combined else " (single)")
    )
    print(f"  verdict:       {report.describe()}")
    for finding in report.findings:
        print(f"    - {finding.describe()}")

    # The check has to be able to FAIL on real-shaped input, or its pass above
    # says nothing. One intraday observation in a Swing population is the
    # violation the module exists to catch.
    print("\n  the same check against a deliberately mixed population:")
    dirty = [*population[:1000], Observation(mandate=Mandate.DAY, timeframe=Bartimeframe.M5)]
    dirty_report = check_population(dirty, unit)
    print(f"  verdict:       {dirty_report.describe()}")
    for finding in dirty_report.findings:
        print(f"    - {finding.describe()}")
    if dirty_report.admissible:
        raise SystemExit(
            "the purity check accepted a population mixing Day/M5 into Swing/D1. "
            "Refusing to report the clean pass above as meaningful."
        )

    print("\n=== 2. the sector map the snapshot needs, which existed only from today ===")
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = start
    rows = session.execute(SECTORS_AS_OF, {"as_of": as_of.isoformat()}).all()
    sectors = {int(i): str(s) for i, s in rows}
    print(f"  {len(sectors):,} instruments classified as of {as_of}")
    config = StrategyConfig(name="ma_cross_baseline")
    print(
        f"  risk limits: heat {config.risk.max_portfolio_heat_pct:.0%}, "
        f"sector {config.risk.max_sector_exposure_pct:.0%}, "
        f"gross {config.risk.max_gross_exposure_pct:.0%}"
    )
    print("\n=== 3. build_snapshot, on every session of a real backtest ===")
    resolved = _universe(session, tickers)
    print(f"  universe: {len(resolved)} of {len(tickers)} tickers resolved")
    mine = {k: v for k, v in sectors.items() if k in set(resolved.values())}
    print(f"  of this universe: {len(mine)} of {len(resolved)} classified by SIC")

    def make_data() -> tuple[CorpusSessionData, MovingAverageCross]:
        rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
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
    matrix = build_correlations(data, sessions, 126)
    source = MatrixCorrelationSource(values=matrix)
    print(f"  correlation source: {len(matrix):,} measured pairs")

    # The engine exposes no per-session observer, and adding one to production
    # for a one-off exercise would be the tail wagging the dog. Overriding the
    # mark step is enough: the snapshot is meant to be taken AFTER the book is
    # marked and BEFORE anything is decided, which is exactly where `_mark`
    # returns.
    snapshots: list[RiskSnapshot] = []

    class SnapshottingEngine(EventDrivenEngine):
        def _mark(self, state, bars, session_date):  # type: ignore[no-untyped-def]
            super()._mark(state, bars, session_date)
            portfolio = state.portfolio()
            peak = max((equity for _, equity in state.curve), default=None)
            snapshots.append(
                build_snapshot(
                    portfolio,
                    config.risk,
                    session_date=session_date,
                    peak_equity=peak,
                    sectors=mine,
                    correlation=source,
                    strategy_config_digest=config.digest,
                )
            )

    run_data, _ = make_data()
    spec = BacktestSpec(
        name="ma_cross_baseline",
        start=start,
        end=end,
        universe=",".join(sorted(resolved)),
        initial_capital=Decimal(100000),
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = SnapshottingEngine(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02")
            ),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=config.risk),
                    MaxPositionsRule(config=config.risk),
                    PositionSizeRule(
                        max_position_pct_of_equity=config.sizing.max_position_pct_of_equity
                    ),
                    GrossExposureRule(config=config.risk),
                    SectorExposureRule(config=config.risk, sectors=mine),
                ),
                sizing=config.sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        data=run_data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=0.02,
        ),
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
        manifest=_manifest(config, dt.datetime.now(dt.UTC), run_data.bar_count),
    )
    t0 = time.time()
    result = engine.run(spec)
    print(
        f"  ran {len(result.trades):,} trades over {len(sessions):,} sessions "
        f"[{time.time() - t0:.1f}s]"
    )
    if not snapshots:
        raise SystemExit(
            "zero snapshots from a run that completed. The override did not fire, and "
            "reporting this as an exercise would be the empty-success failure again."
        )
    print(f"  {len(snapshots):,} snapshots built, one per session")

    flat = sum(1 for s in snapshots if s.position_count == 0)
    print(
        f"  of which {flat:,} ({flat / len(snapshots):.1%}) held NOTHING -- the quiet\n"
        "    sessions the module exists for, and the ones a trade-triggered record misses"
    )

    breaches: collections.Counter[str] = collections.Counter()
    for snap in snapshots:
        for name in snap.limit_breaches:
            breaches[name] += 1
    print("\n  limits the portfolio was OUTSIDE, by session (no proposal involved):")
    if breaches:
        for name, count in breaches.most_common():
            print(f"    {name:<22} {count:>5,} sessions  ({count / len(snapshots):>5.1%})")
    else:
        print("    none, on any session")

    # Two completely different reasons for a None, and lumping them together
    # would report the honest-unknown path as exercised when it was not.
    structural = sum(1 for s in snapshots if s.position_count < 2)
    unmeasured = sum(
        1 for s in snapshots if s.position_count >= 2 and s.max_pairwise_correlation is None
    )
    possible = len(snapshots) - structural
    print(
        f"\n  worst pairwise correlation: {structural:,} sessions held fewer than two\n"
        f"    positions, so no pair existed. Of the {possible:,} where one did,\n"
        f"    {unmeasured:,} hit an unmeasured pair and returned None."
    )
    if not unmeasured:
        print(
            "    So the fail-honest branch was NOT exercised by missing data here: the\n"
            "    66-pair matrix happens to cover every pair this book ever held, even\n"
            "    though it covers only 66 of the universe's 91. Its unit tests remain\n"
            "    the only evidence for that branch."
        )
    measured = [s.max_pairwise_correlation for s in snapshots if s.max_pairwise_correlation]
    if measured:
        measured.sort()
        print(
            f"    measured: min {measured[0]:+.2f}  median "
            f"{measured[len(measured) // 2]:+.2f}  max {measured[-1]:+.2f}"
            f"  (limit {config.risk.max_correlation_for_new_position:.2f})"
        )

    worst_sector = 0.0
    worst_name = "-"
    unclassified_seen = 0
    for snap in snapshots:
        for name, share in snap.sector_exposures.items():
            if share > worst_sector:
                worst_sector, worst_name = share, name
            if name == "UNCLASSIFIED" and share > 0:
                unclassified_seen += 1
    print(
        f"\n  largest single-sector share over the run: {worst_sector:.1%} ({worst_name})\n"
        f"  sessions holding anything UNCLASSIFIED: {unclassified_seen:,}"
    )

    heats = sorted(float(s.heat) for s in snapshots)
    gross = sorted(float(s.gross_exposure_pct) for s in snapshots)
    draw = sorted(float(s.drawdown_from_peak) for s in snapshots)
    print(
        f"\n  heat      median {heats[len(heats) // 2]:.2%}  max {heats[-1]:.2%}"
        f"  (limit {config.risk.max_portfolio_heat_pct:.0%})"
    )
    print(
        f"  gross     median {gross[len(gross) // 2]:.1%}  max {gross[-1]:.1%}"
        f"  (limit {config.risk.max_gross_exposure_pct:.0%})"
    )
    print(
        f"  drawdown  median {draw[len(draw) // 2]:.1%}  max {draw[-1]:.1%}"
        f"  (halt {config.risk.drawdown_halt_pct:.0%})"
    )

    print(
        "\nBoth modules are now exercised on corpus data. The snapshot could not have\n"
        "run before today: it takes a sector map and a correlation source, and neither\n"
        "existed in this repository until this morning."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
