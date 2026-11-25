#!/usr/bin/env python
"""How many names can this portfolio actually hold, and does breadth pay?

`THE_FLOOR_2026-11-22.md` ends on a structural finding it records and does not
act on:

    The platform cannot hold a broad book, because twelve positions and no
    re-entry leave it in cash.

Reading the floor's own CSVs closes half of that immediately. `equal_weight`
re-offers the whole universe every 63 sessions, so re-entry *is* available to
it, and its exposure is 98.5%. It is not sitting in cash. What it is doing is
holding **thirteen securities** out of 500 offered, for six years, on 35
trades -- `securities_held` in `floor_results_s0.csv` says 13.

So the floor of **7.74%/yr at a 16.7% maximum drawdown is not a diversified
benchmark.** It is a thirteen-name concentrated portfolio. That matters for
every comparison the scoreboard makes against it, and it is why this runs.

What binds, and why raising one number would have measured nothing
-----------------------------------------------------------------

`max_positions` defaults to 12 and looks like the cap. It is not the only one.
`max_portfolio_heat_pct` is 0.06 and `risk_per_trade_pct` is 0.005, so the
**heat rule binds at exactly twelve positions too** -- 0.06 / 0.005 = 12, the
same number by a different route, and `StrategyConfig` computes that quotient
itself. Raising `max_positions` alone would have changed nothing and the run
would have reported "breadth does not help" when breadth had never happened.

So each arm here **holds the risk budget constant and spends it across more
names**: heat stays 6%, and `risk_per_trade_pct` becomes 0.06 / N with
`max_positions` set to N. Gross exposure is then invariant as well -- with a
fixed 8% stop, notional per position is equity x (0.06/N) / 0.08, which times N
is 75% of equity at every N. **The only thing that varies between arms is how
many names the same risk is spread across.** That is the comparison worth
having; "more positions and more risk" would confound breadth with leverage.

Is diversification a switch anybody can throw?
----------------------------------------------

§54 records that *"every portfolio-level result on this scoreboard was produced
with diversification switched off"*, by one line in `build_engine` holding
`correlation_weight` and `sector_penalty_per_holding` at 0.0. Read as a
configuration decision, that invites turning them up.

It is not one, on this path, and the `diversify` arm below measures rather than
asserts it:

* **Correlation weighting is unreachable from any backtest.**
  `DiversityAwareRanker.rank` takes a `correlation` matrix, `PortfolioCycle`
  passes one through, and `EventDrivenEngine` (engine.py:397) calls
  `cycle.plan` with three arguments and no fourth. Every backtest ever run here
  ranked with `correlation=None`, so the weight multiplies nothing.
* **Sector penalties are unreachable from these arms.** `_candidate` in
  `baselines.py` constructs every baseline candidate with `sector=None`, and
  `_penalty` adds nothing for a candidate with no sector.

The arm therefore runs N=12 -- directly comparable to the floor's control --
with **both weights turned well up**, and the expected result is a line
*identical to the control*. If it is identical, "turn on diversification" is a
change in two source files, not a parameter, and §54's sentence understates
what it would cost. If it differs, this docstring is wrong and that is the
finding instead.

What this is, and is not
------------------------

**Not a trial. Nothing registered, nothing graded, the ledger stays at 144.**
A reference measurement in the same category as §54 and THE_FLOOR. **No
default changes**: every number below is set per arm on a local
`StrategyConfig`, and the ranker arrives through `build_engine`'s existing
optional `ranker=` argument, which exists so the archive can be re-run under
constraints without any published result moving.

In sample, one window (2020-01-02 to 2025-12-31), four samples, a strong equity
market. **Nothing here is a return result.**
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import EqualWeightBenchmark, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import ArtifactKind, RiskDecision, RiskLimitType
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.risk.base import RiskAssessment
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

#: Read from `measure_the_floor.py` rather than retyped, for the same reason it
#: read them from §37: a constant that drifts makes the comparison a different
#: comparison while still looking like this one.
START = dt.date(2020, 1, 2)
END = dt.date(2025, 12, 31)
CADENCE = 63
SEED = 20260918
SAMPLE_CAP = 500
CAPITAL = Decimal(100_000)
PARTICIPATION = Decimal("0.02")
RISK_FREE = 0.03
DELISTING_AFTER = 10
STOP_PCT = Decimal("0.08")
HEAT = 0.06
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
POPULATION = OUT / "lowvol_bt_population.json"

#: The breadth ladder. 12 is the floor's own book and is run again here rather
#: than copied from its CSV, because a control that was not produced by this
#: script cannot catch this script being wrong.
#:
#: **150 and 200 straddle a hard ceiling, and it is not a position-count rule.**
#: `SizingConfig.min_position_notional` is $500 and refuses anything below it
#: rather than rounding up. With heat 6% and a fixed 8% stop, notional per
#: position is equity x (0.06/N) / 0.08 = equity x 0.75 / N, so at $100,000 it
#: is $75,000/N -- $500 exactly at N=150 and $375 at N=200, where every
#: candidate is refused and the arm trades nothing. The ladder spans the
#: boundary on purpose: an arm that returns zero needs the arm beside it to show
#: that the cause is capital, not breadth.
#:
#: The general form, which is the number the owner actually needs:
#: **max names = capital x (heat / stop_pct) / min_position_notional.** At
#: $100,000 that is 150. Holding 500 names would take $333,000.
BREADTHS = (12, 25, 50, 100, 150, 200)

COLUMNS = (
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


def _manifest(config: StrategyConfig, label: str, bars: int) -> RunManifest:
    """A real manifest: the actual configuration, and what the corpus held."""
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"breadth-{label}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{label}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="measure-breadth",
        created_at=now,
    )


def samples(pop: list[int]) -> list[list[int]]:
    """§37's four disjoint samples, drawn with its seed. Copied, not re-derived."""
    order = [int(x) for x in np.random.default_rng(SEED).permutation(sorted(pop))]
    size = min(SAMPLE_CAP, len(order) // 4)
    return [sorted(order[k * size : (k + 1) * size]) for k in range(4)]


def _config(name: str, breadth: int) -> StrategyConfig:
    """One arm's configuration: the same risk budget, spread across `breadth`.

    `max_positions` and `risk_per_trade_pct` are set **together**, because
    either one alone leaves the other binding at twelve and the arm would
    silently measure the control again.
    """
    return StrategyConfig(
        name=name,
        exits={"time_stop_sessions": None},
        sizing={
            "allow_pyramiding": False,
            "risk_per_trade_pct": HEAT / breadth,
        },
        risk={
            "max_positions": breadth,
            "max_portfolio_heat_pct": HEAT,
        },
    )


@dataclass
class _BookWidth:
    """A risk rule that never refuses anything and only writes down the width.

    **Why a rule.** `securities_held` counts distinct names *ever traded* and is
    not the width of the book -- thirteen names could be thirteen held at once
    or one held thirteen times, and without telling those apart an arm that
    merely churned would read as an arm that diversified. The engine's
    `equity_curve` is `(date, equity)` pairs and carries no position count, and
    the alternative to this was editing `backtesting/engine.py` -- a production
    module every archived result runs through -- to collect a diagnostic.

    `build_engine` already takes `extra_rules`, every rule is handed the same
    `PortfolioState`, and `MostRestrictiveEngine` takes the most restrictive
    answer, so a rule that always returns ALLOW cannot change an outcome. It
    observes **at each proposal**, which with a 63-session re-offer cadence
    means roughly two dozen readings over the window rather than one per
    session; stated because "mean open positions" would otherwise read as a
    daily average.
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
        # The *largest* count seen in a session, because entries open one at a
        # time and the first proposal of a session sees a narrower book than the
        # last. Taking the first would understate every arm, and by more at
        # higher breadth -- biasing the very comparison this exists to make.
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--sample", type=int, required=True, choices=range(4))
    ap.add_argument("--pilot", type=int, default=0, help="first N securities only, for a smoke run")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not POPULATION.exists():
        print(f"no cached population at {POPULATION}; run backtest_lowvol_tilt.py first")
        return 1
    pop = list(json.loads(POPULATION.read_text()))
    universe = samples(pop)[args.sample]
    if args.pilot:
        universe = universe[: args.pilot]
    print(f"population {len(pop):,}; sample {args.sample}: {len(universe):,} securities")

    engine_ = create_engine(args.db)
    install_sqlite_busy_timeout(engine_)
    session: Session = sessionmaker(bind=engine_)()

    # (arm, breadth, ranker) -- the ranker is None for every breadth arm, which
    # means `build_engine` installs the same diversification-off ranker every
    # published result used. The one arm that supplies its own says so in its
    # name and in its own row.
    arms: list[tuple[str, int, DiversityAwareRanker | None]] = [
        (f"breadth_{n:03d}", n, None) for n in BREADTHS
    ]
    arms.append(
        (
            "diversify_012",
            12,
            # Deliberately large weights. A small penalty that changed nothing
            # would be indistinguishable from a penalty that cannot apply, and
            # telling those two apart is the entire point of this arm.
            DiversityAwareRanker(
                correlation_weight=0.5, sector_penalty_per_holding=0.25, held_sectors={}
            ),
        )
    )

    rows: list[tuple[object, ...]] = []
    for recovery in (Decimal("1.0"), Decimal("0.0")):
        for arm, breadth, ranker in arms:
            source = EqualWeightBenchmark(cadence_sessions=CADENCE, stop_pct=STOP_PCT)
            data = CorpusSessionData(
                session=session,
                universe=tuple(universe),
                start=START,
                end=END,
                candidate_source=source,
            )
            config = _config(f"breadth_{arm}", breadth)
            label = f"s{args.sample}-{arm}-r{recovery}"
            # Fresh per arm: a shared observer would blend two books.
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
                ranker=ranker,
            )
            spec = BacktestSpec(
                name=f"breadth/{label}",
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
            # **Costs, because breadth buys more of them.** At breadth 100 this
            # run takes roughly eight times the trades it takes at 12, and
            # without this column "breadth earns less" cannot be separated from
            # "breadth pays more" -- two findings with opposite consequences:
            # the first says concentration is better, the second says the cost
            # model or the capital is too small for a broad book.
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
            sharpe = "n/a" if metrics.sharpe is None else f"{metrics.sharpe:6.3f}"
            print(
                f"  {label:<30} CAGR {metrics.cagr:>7.2%}  maxDD {metrics.max_drawdown_pct:>7.2%}  "
                f"Sharpe {sharpe}  exposure {metrics.exposure_pct:>6.1%}  "
                f"{len(result.trades):>6,} trades  {held:>4} names  "
                f"open mean {mean_open:>5.1f} peak {peak_open:>4}  "
                f"costs {float(total_costs):>8,.0f}  notional {float(mean_notional):>7,.0f}  "
                f"[{time.time() - started:.0f}s]",
                flush=True,
            )

    out = Path(args.out) if args.out else OUT / f"breadth_results_s{args.sample}.csv"
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"\nwrote {out}")
    print(
        "\nNot a trial; the ledger stays at 144. Reference numbers, in sample, one\n"
        "window -- see this module's docstring for what they can and cannot support."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
