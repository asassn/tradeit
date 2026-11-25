#!/usr/bin/env python
"""Trial 145 -- does breadth protect against drawdown, out of sample?

**A REGISTERED TRIAL.** The hypothesis, the window, the universe rule, the
seed, the two arms, the block definition and all four pass criteria were
committed in `docs/prereg/BREADTH_DRAWDOWN_2026-11-25.md` before this script
read a single bar of 2010-2019. Read that file before reading this one; this
module implements it and adds nothing to it.

**Ledger 144 -> 145. Hurdle at twenty blocks: |t| > 2.9713.**

This script only measures and records. The four criteria are graded by
`trial_breadth_drawdown_verdict.py`, which reads the CSVs this writes --
separated for the same reason every other verdict here is separated: a script
that measures and grades in one pass can have its criterion quietly adjusted to
the number it just produced, and the separation makes that edit visible as a
diff to a file with no measurement in it.

Why a new universe, and not the screening one
---------------------------------------------

`lowvol_bt_population.json` is *"alive on 2020-01-02 and liquid across 2019"*.
Every name in it survived to 2020. Testing a **write-off** hypothesis on a
universe selected for not having been written off would be testing it where the
mechanism cannot act -- the hypothesis would fail for a reason that has nothing
to do with whether it is true.

So this builds its own population on the registered rule: span covers
2010-01-04, >= 100 bars with volume in 2009, median daily dollar volume >= $1m,
read at `as_of` 2009-12-31. **No survival condition.** Measured before
registering: 5,823 securities are alive on 2010-01-04 and 46.8% of them have no
bar after 2019-12-31.

What is deliberately NOT here
-----------------------------

The 25, 50, 150 and 200 breadth arms, the diversification arm, the screening
window, any other block length, and any return criterion. The registration
fixes two arms and one primary statistic. Everything else would be screening
dressed as thoroughness, and the hurdle prices trials rather than screens.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import EqualWeightBenchmark, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import ArtifactKind, RiskDecision, RiskLimitType
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.research01.series import price_series
from tradeit.risk.base import RiskAssessment
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

# ---------------------------------------------------------------- registered
#: Every constant below is fixed by the registration. Changing one changes the
#: trial, not the measurement, and the verdict script re-reads them from here.
START = dt.date(2010, 1, 4)
END = dt.date(2019, 12, 31)
SCREEN_YEAR_START = dt.date(2009, 1, 2)
SCREEN_YEAR_END = dt.date(2009, 12, 31)
SCREEN_AS_OF = dt.datetime.combine(SCREEN_YEAR_END, dt.time(21), tzinfo=dt.UTC)
SCREEN_MIN_BARS = 100
SCREEN_MIN_MEDIAN_DOLLAR_VOLUME = 1_000_000.0
SEED = 20261125
SAMPLE_CAP = 500
CADENCE = 63
CAPITAL = Decimal(100_000)
PARTICIPATION = Decimal("0.02")
RISK_FREE = 0.03
DELISTING_AFTER = 10
STOP_PCT = Decimal("0.08")
HEAT = 0.06
#: The two arms, and only these two.
NARROW = 12
WIDE = 100

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
SPANS = OUT / "security_spans_2026-09-18.csv"
POPULATION = OUT / "breadth_dd_population_2010.json"

RESULT_COLUMNS = (
    "sample",
    "arm",
    "max_positions",
    "risk_per_trade_pct",
    "recovery",
    "cagr",
    "total_return",
    "max_drawdown",
    "sharpe",
    "trades",
    "exposure",
    "securities_held",
    "mean_open_positions",
    "peak_open_positions",
    "total_costs",
    "mean_notional",
)
CURVE_COLUMNS = ("sample", "arm", "recovery", "date", "equity")


def population(session: Session) -> list[int]:
    """The registered universe rule, cached. **No survival condition.**"""
    if POPULATION.exists():
        return list(json.loads(POPULATION.read_text()))
    with SPANS.open() as handle:
        alive = sorted(
            int(row[0]) for row in csv.reader(handle) if row[1] <= START.isoformat() <= row[2]
        )
    keep: list[int] = []
    for n, sid in enumerate(alive, 1):
        bars = price_series(
            session, sid, as_of=SCREEN_AS_OF, start=SCREEN_YEAR_START, end=SCREEN_YEAR_END
        )
        traded = [float(bar.close * bar.volume) for bar in bars if bar.volume > 0]
        if (
            len(traded) >= SCREEN_MIN_BARS
            and statistics.median(traded) >= SCREEN_MIN_MEDIAN_DOLLAR_VOLUME
        ):
            keep.append(sid)
        if n % 250 == 0:
            print(f"  population: {n:,}/{len(alive):,} read, {len(keep):,} liquid", flush=True)
    POPULATION.write_text(json.dumps(keep))
    return keep


def samples(pop: list[int]) -> list[list[int]]:
    """Four disjoint samples on the registered seed. The 2020 construction."""
    order = [int(x) for x in np.random.default_rng(SEED).permutation(sorted(pop))]
    size = min(SAMPLE_CAP, len(order) // 4)
    return [sorted(order[k * size : (k + 1) * size]) for k in range(4)]


def _config(breadth: int) -> StrategyConfig:
    """The same total risk budget, spread across `breadth` names.

    Both numbers move together. `max_positions` alone would change nothing --
    heat 0.06 over risk 0.005 binds at twelve by a second route, and the wide
    arm would silently re-measure the narrow one. That is recorded as Finding 2
    of `BREADTH_2026-11-24.md` and it is the error this function exists to
    make impossible.
    """
    return StrategyConfig(
        name=f"trial145_breadth_{breadth:03d}",
        exits={"time_stop_sessions": None},
        sizing={"allow_pyramiding": False, "risk_per_trade_pct": HEAT / breadth},
        risk={"max_positions": breadth, "max_portfolio_heat_pct": HEAT},
    )


@dataclass
class _BookWidth:
    """An observer rule: never refuses, only records how wide the book got.

    Criterion 4 needs the width of the book, and `securities_held` is not it --
    it counts names ever traded, so an arm that churned twelve names a hundred
    times would read as an arm holding a hundred. `equity_curve` carries no
    position count either. `build_engine` already takes `extra_rules` and
    `MostRestrictiveEngine` takes the most restrictive answer, so a rule that
    always ALLOWs cannot change an outcome.
    """

    seen: dict[dt.date, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "book_width_observer"

    @property
    def limit_type(self) -> RiskLimitType:
        return RiskLimitType.MAX_POSITIONS

    @property
    def parameters(self) -> dict[str, object]:
        return {"observer": True}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        day = portfolio.as_of.date()
        # The largest count seen in a session: entries open one at a time, so
        # the first proposal of a session sees a narrower book than the last,
        # and taking the first would understate the wide arm by more than the
        # narrow one -- biasing the comparison criterion 4 guards.
        self.seen[day] = max(self.seen.get(day, 0), portfolio.position_count)
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="observer; never refuses",
            measured=float(portfolio.position_count),
        )

    def summary(self) -> tuple[float, int]:
        if not self.seen:
            return (-1.0, -1)
        counts = list(self.seen.values())
        return (sum(counts) / len(counts), max(counts))


def _manifest(config: StrategyConfig, label: str, bars: int) -> RunManifest:
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"trial145-{label}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{label}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="trial-145-breadth-drawdown",
        created_at=now,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--sample", type=int, required=True, choices=range(4))
    ap.add_argument("--population-only", action="store_true")
    args = ap.parse_args()

    engine_ = create_engine(args.db)
    install_sqlite_busy_timeout(engine_)
    session: Session = sessionmaker(bind=engine_)()

    pop = population(session)
    print(f"population {len(pop):,} liquid in 2009, alive 2010-01-04", flush=True)
    if args.population_only:
        return 0
    universe = samples(pop)[args.sample]
    print(f"sample {args.sample}: {len(universe):,} securities", flush=True)

    rows: list[tuple[object, ...]] = []
    curves: list[tuple[object, ...]] = []
    for recovery in (Decimal("0.0"), Decimal("1.0")):
        for arm, breadth in (("narrow", NARROW), ("wide", WIDE)):
            source = EqualWeightBenchmark(cadence_sessions=CADENCE, stop_pct=STOP_PCT)
            data = CorpusSessionData(
                session=session,
                universe=tuple(universe),
                start=START,
                end=END,
                candidate_source=source,
            )
            config = _config(breadth)
            label = f"s{args.sample}-{arm}-r{recovery}"
            observer = _BookWidth()
            backtester = build_engine(
                config,
                data,
                _manifest(config, label, data.bar_count),
                participation=PARTICIPATION,
                risk_free_rate=RISK_FREE,
                delisting_after_sessions=DELISTING_AFTER,
                delisting_recovery=recovery,
                extra_rules=(observer,),
            )
            spec = BacktestSpec(
                name=f"trial145/{label}",
                start=START,
                end=END,
                universe=f"sample-{args.sample}",
                initial_capital=CAPITAL,
                strategy_config_digest=config.digest,
                cost_model="participation",
                fill_model="bar",
            )
            started = time.time()
            result = backtester.run(spec)
            metrics = result.metrics
            held = len({trade.instrument_id for trade in result.trades})
            mean_open, peak_open = observer.summary()
            total_costs = sum((trade.costs for trade in result.trades), Decimal(0))
            notionals = [trade.entry_price * trade.quantity for trade in result.trades]
            mean_notional = sum(notionals, Decimal(0)) / len(notionals) if notionals else Decimal(0)
            rows.append(
                (
                    args.sample,
                    arm,
                    breadth,
                    HEAT / breadth,
                    str(recovery),
                    metrics.cagr,
                    metrics.total_return_pct,
                    metrics.max_drawdown_pct,
                    metrics.sharpe,
                    len(result.trades),
                    metrics.exposure_pct,
                    held,
                    mean_open,
                    peak_open,
                    total_costs,
                    mean_notional,
                )
            )
            # The whole curve, because the PRIMARY statistic is a within-block
            # drawdown and the engine reports only the whole-window maximum.
            # Recording the curve means the verdict script computes the
            # registered statistic from data rather than from a summary that
            # answers a different question.
            curves.extend(
                (args.sample, arm, str(recovery), day.isoformat(), str(equity))
                for day, equity in result.equity_curve
            )
            sharpe = "n/a" if metrics.sharpe is None else f"{metrics.sharpe:6.3f}"
            print(
                f"  {label:<24} CAGR {metrics.cagr:>7.2%}  maxDD {metrics.max_drawdown_pct:>7.2%}  "
                f"Sharpe {sharpe}  exposure {metrics.exposure_pct:>6.1%}  "
                f"{len(result.trades):>6,} trades  {held:>4} names  "
                f"open mean {mean_open:>5.1f} peak {peak_open:>4}  "
                f"curve {len(result.equity_curve):>5,}  [{time.time() - started:.0f}s]",
                flush=True,
            )

    res = OUT / f"trial145_results_s{args.sample}.csv"
    with res.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RESULT_COLUMNS)
        writer.writerows(rows)
    cur = OUT / f"trial145_curves_s{args.sample}.csv"
    with cur.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CURVE_COLUMNS)
        writer.writerows(curves)
    print(f"\nwrote {res}\nwrote {cur}")
    print(
        "\nTRIAL 145. Nothing above is a verdict -- the four registered criteria are\n"
        "graded by trial_breadth_drawdown_verdict.py from these CSVs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
