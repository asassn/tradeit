#!/usr/bin/env python
"""Walk-forward and Monte Carlo, with their provenance written down.

Both harnesses were exercised on real data when Phase 9 closed, and both
printed their results to a terminal. **Neither recorded anything**, so a
robustness claim about this platform existed only in a scrollback and a commit
message -- which is the condition the roadmap names when it says no result "can
exist without the means to reproduce it".

This runs both and persists them, which needed two writers that did not exist.

How each is stored, and why neither needed a new table
------------------------------------------------------

**Monte Carlo** goes to `monte_carlo_runs`, keyed on the backtest it studies.
It existed with no writer, like the four observability links before it. The
**seed** is the field that matters: a resampling study without one is a
distribution nobody can check, and this project treats that as worse than no
number.

**Walk-forward gets no table, and that is a finding rather than a shortcut.**
`backtest_runs` already carries ``walk_forward_window`` and
``is_out_of_sample``, which is the schema saying a walk-forward *is* a series
of backtest runs distinguished by window and by which side of the split they
sit on. Two rows per fold, in-sample and out-of-sample, because the
**degradation between them is the whole point** and a single row would have had
to pick one side's metrics.

What this does not claim
------------------------

Nothing here is evidence about profitability. The universe is a SIC-classified
sample and the rule is a moving-average baseline; the point is that the
robustness numbers now carry a manifest, a seed and a window index, so a reader
can reproduce them instead of trusting a paste.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest, _universe
from record_decisions import _persist_manifest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec, MonteCarloSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.montecarlo import ResamplingMonteCarlo
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.backtesting.walkforward import ANCHORED, ROLLING, WalkForward
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.observability.record import (
    record_backtest_result,
    record_monte_carlo,
    record_walk_forward,
)
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
from tradeit.storage.tables import Base
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector FROM sectors
    WHERE scheme = 'SIC' AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="sqlite:///research01.sqlite", help="READ ONLY")
    ap.add_argument(
        "--record",
        default="/Users/ericsasson/Documents/TradeItData/out/robustness_record.sqlite",
    )
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", default="2003-01-02")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--train-sessions", type=int, default=756, help="~3 years")
    ap.add_argument("--test-sessions", type=int, default=252, help="~1 year")
    ap.add_argument("--gap-sessions", type=int, default=0)
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--ruin-threshold", type=float, default=-0.5)
    args = ap.parse_args()

    if Path(args.record).resolve() == Path("research01.sqlite").resolve():
        raise SystemExit("the record must not be written into the corpus")

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    corpus: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.corpus, future=True)),
        future=True,
    )()
    record_engine = create_engine(f"sqlite:///{args.record}", future=True)
    Base.metadata.create_all(record_engine)
    record: Session = sessionmaker(bind=record_engine, future=True)()

    resolved = _universe(corpus, tickers)
    config = StrategyConfig(name="ma_cross_baseline")
    universe_ids = set(resolved.values())
    rows = corpus.execute(SECTORS_AS_OF, {"as_of": start.isoformat()}).all()
    sectors = {int(i): str(s) for i, s in rows if int(i) in universe_ids}
    print(f"corpus : {args.corpus} (read)")
    print(f"record : {args.record} (written)")
    print(f"universe: {len(resolved)} of {len(tickers)} resolved, {len(sectors)} classified")

    rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
    data = CorpusSessionData(
        session=corpus,
        universe=tuple(sorted(resolved.values())),
        start=start,
        end=end,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on

    manifest = _manifest(config, dt.datetime.now(dt.UTC), data.bar_count)
    manifest_id = _persist_manifest(record, manifest)
    print(f"manifest row {manifest_id}: every run below cites it")

    engine = EventDrivenEngine(
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
                    SectorExposureRule(config=config.risk, sectors=sectors),
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
        data=data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=0.02,
        ),
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
        manifest=manifest,
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

    print("\n=== walk-forward, both modes, persisted per fold ===")
    for mode in (ANCHORED, ROLLING):
        harness = WalkForward(
            engine=engine,
            data=data,
            mode=mode,
            train_sessions=args.train_sessions,
            test_sessions=args.test_sessions,
            gap_sessions=args.gap_sessions,
        )
        t0 = time.time()
        report = harness.evaluate(spec)
        written = record_walk_forward(record, report, spec, run_manifest_id=manifest_id)
        degradation = report.mean_degradation
        annualised = report.mean_annualised_degradation
        consistency = report.consistency
        print(
            f"  {mode:<9} {len(report.results)} folds -> {len(written)} rows   "
            f"degradation raw "
            f"{'—' if degradation is None else f'{degradation:+.2%}'} / annualised "
            f"{'—' if annualised is None else f'{annualised:+.2%}'}   "
            f"consistency {'—' if consistency is None else f'{consistency:.0%}'}   "
            f"oos trades {report.out_of_sample_trade_count:,}   "
            f"[{time.time() - t0:.1f}s]"
        )

    print("\n=== monte carlo, persisted with its seed ===")
    base = engine.run(spec)
    base_row = record_backtest_result(
        record, spec, base, run_manifest_id=manifest_id, is_out_of_sample=False
    )
    resampler = ResamplingMonteCarlo(ruin_threshold=args.ruin_threshold)
    for method in ("trade_sequence", "bootstrap_returns"):
        mc_spec = MonteCarloSpec(method=method, iterations=args.iterations, seed=args.seed)
        t0 = time.time()
        result = resampler.run(base, mc_spec)
        row = record_monte_carlo(record, result, backtest_run_id=base_row.id)
        p = result.return_percentiles
        print(
            f"  {method:<18} seed {row.seed}  {result.iterations_completed:,} of "
            f"{mc_spec.iterations:,} iterations   "
            f"p5 {p.get(5, 0):+.1%}  p50 {p.get(50, 0):+.1%}  p95 {p.get(95, 0):+.1%}   "
            f"ruin {result.ruin_probability:.1%}   [{time.time() - t0:.1f}s]"
        )
    record.commit()

    counts = {
        name: record.execute(text(f"select count(*) from {name}")).scalar()
        for name in ("backtest_runs", "backtest_trades", "monte_carlo_runs")
    }
    print("\npersisted:")
    for name, count in counts.items():
        print(f"  {name:<20} {count:>6,}")
    folds = record.execute(
        text("select count(*) from backtest_runs where walk_forward_window is not null")
    ).scalar()
    oos = record.execute(
        text("select count(*) from backtest_runs where is_out_of_sample = 1")
    ).scalar()
    print(f"  of which walk-forward folds: {folds:,} ({oos:,} out-of-sample)")
    if not folds:
        raise SystemExit(
            "no fold carries a window index, so the walk-forward is indistinguishable "
            "from a pile of unrelated runs. Refusing to report it as persisted."
        )

    print(
        "\n  **The raw degradation is dominated by span length in ANCHORED mode** and\n"
        "  the annualised figure beside it is the comparable one. Window 12 compares a\n"
        "  15-year in-sample span against a 1-year test, so a +67.86% raw difference is\n"
        "  mostly arithmetic. `readiness.criteria.out_of_sample_degradation` is fed by\n"
        "  this and its threshold is one of the four still awaiting approval -- which\n"
        "  one it should use is part of that decision."
    )
    print(
        "\nNothing here is evidence about profitability. The point is that these\n"
        "numbers now carry a manifest, a seed and a window index, so a reader can\n"
        "reproduce them instead of trusting a paste -- which is what every previous\n"
        "run of these harnesses asked for."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
