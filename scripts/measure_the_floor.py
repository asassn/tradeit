#!/usr/bin/env python
"""Buy-and-hold, measured — the comparison this project has never made.

`BuyAndHold` and `EqualWeightBenchmark` have been in `backtesting/baselines.py`
since Phase 9, with twenty passing tests, and **no script has ever run them.**
`BuyAndHold`'s own docstring names what that costs:

    The floor every trading strategy has to clear. A rule that turns over
    constantly and cannot beat this has paid spread, commission and slippage
    for nothing -- which is the comparison §44 through §53 never made, because
    they measured against a random security rather than against inaction.

Every benchmark on the scoreboard is a *random selection inside the platform*:
§37's RANDOM arm, §38's two numbers. Those answer "is this signal better than
picking at random?". None answers the first-order question, which is **"is any
of this better than doing nothing?"**

What this is, and is not
------------------------

**Not a trial. Nothing is registered and the ledger does not move: 144 before,
144 after.** No hypothesis about returns is tested here and nothing is graded
pass or fail. This measures and records a reference number, in the same
category as §54. Framed as *"does the platform beat buy-and-hold"* it would be
a hypothesis about returns and would cost a trial against a hurdle of
|t| > 2.6563; framed as *"what is the buy-and-hold number for this window"* it
is a benchmark measurement. The distinction is deliberate and the framing is
the honest one, because the point is to see where everything already sits
rather than to win an argument.

Why the numbers are comparable to §37
-------------------------------------

Same cached population (2,806 securities alive on 2020-01-02 and liquid across
2019), same seed, same four disjoint samples of 500, same window, same capital,
same costs, same participation, same delisting handling, same `build_engine`.
The only things that differ are stated below.

The exits, which cannot be held identical
-----------------------------------------

§37's arms ran a **63-session time stop**, which is right for a rebalanced
factor tilt and wrong for buy-and-hold: a position closed every 63 sessions is
not held. So the floor arms run with `time_stop_sessions=None`, and a third arm
runs buy-and-hold **under §37's exact exit rules** so the reader can see what
that difference alone is worth. Stating it rather than quietly picking one is
the whole reason there are three arms.

**The position limit binds, and it is the finding, not a nuisance.**
`MaxPositionsRule` caps the book at `RiskConfig.max_positions`. Told to buy
everything, the platform takes the first names that pass sizing and then
refuses the rest -- so "buy and hold the universe" is not something this system
can do. What is measured is what the platform *actually does* when told to hold
everything, which is the number that matters.

**Read the `exposure` column before any CAGR here.** On a 25-security pilot
`buy_and_hold` ran at 52% exposure and `buy_and_hold_timed` at 7%, against
`equal_weight`'s 98.5%. A return earned sitting in cash is not comparable to
one earned invested, and two of these four arms spend much of the window in
cash for structural reasons stated beside them.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import BuyAndHold, EqualWeightBenchmark, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

#: Every constant below is §37's, read from `backtest_lowvol_tilt.py` rather
#: than retyped, so the comparison cannot drift from the thing it compares to.
START = dt.date(2020, 1, 2)
END = dt.date(2025, 12, 31)
HORIZON = 63
SEED = 20260918
SAMPLE_CAP = 500
CAPITAL = Decimal(100_000)
PARTICIPATION = Decimal("0.02")
RISK_FREE = 0.03
DELISTING_AFTER = 10
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
POPULATION = OUT / "lowvol_bt_population.json"

COLUMNS = (
    "sample",
    "arm",
    "recovery",
    "cagr",
    "total_return",
    "max_drawdown",
    "sharpe",
    "trades",
    "exposure",
    "securities_held",
)


def _manifest(config: StrategyConfig, label: str, bars: int) -> RunManifest:
    """A real manifest: the actual configuration, and what the corpus held.

    **Taken, never invented** -- the engine refuses to mint its own, because a
    manifest an engine created always validates and never reproduces.
    """
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"floor-{label}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{label}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="measure-the-floor",
        created_at=now,
    )


def samples(pop: list[int]) -> list[list[int]]:
    """§37's four disjoint samples, drawn with its seed. Copied, not re-derived."""
    order = [int(x) for x in np.random.default_rng(SEED).permutation(sorted(pop))]
    size = min(SAMPLE_CAP, len(order) // 4)
    return [sorted(order[k * size : (k + 1) * size]) for k in range(4)]


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

    rows: list[tuple[object, ...]] = []
    # Recovery 1.0 and 0.0 are the two delisting assumptions §37 reported. The
    # spread between them is the whole exposure a survivorship-honest backtest
    # has, and reporting one alone would pick the flattering half.
    for recovery in (Decimal("1.0"), Decimal("0.0")):
        for arm, time_stop in (
            # Buys each name once and never re-offers. Watch `exposure`: the
            # position cap binds, and because nothing is re-offered the book
            # cannot refill as positions close.
            ("buy_and_hold", None),
            # Re-offers the whole universe every 63 sessions, so sizing pulls
            # weights back toward equal and the book stays invested. **This is
            # the honest "hold the universe" floor.**
            ("equal_weight", None),
            # The floor under §37's exact exits, and therefore the row directly
            # comparable to its CALM and RANDOM arms.
            ("equal_weight_timed", HORIZON),
            # Kept, and degenerate on purpose. A time stop on a rule that never
            # re-offers holds for 63 sessions and then sits in cash for the rest
            # of the window -- measured exposure 7%. It is reported because it
            # is the cleanest demonstration of why a time stop needs a rule that
            # re-nominates, which is what §37's arms had and this does not.
            ("buy_and_hold_timed", HORIZON),
        ):
            source = (
                EqualWeightBenchmark(cadence_sessions=HORIZON, stop_pct=Decimal("0.08"))
                if arm.startswith("equal_weight")
                else BuyAndHold(stop_pct=Decimal("0.08"))
            )
            data = CorpusSessionData(
                session=session,
                universe=tuple(universe),
                start=START,
                end=END,
                candidate_source=source,
            )
            config = StrategyConfig(
                name=f"floor_{arm}",
                exits={"time_stop_sessions": time_stop},
                sizing={"allow_pyramiding": False},
            )
            label = f"s{args.sample}-{arm}-r{recovery}"
            backtester = build_engine(
                config,
                data,
                _manifest(config, label, data.bar_count),
                participation=PARTICIPATION,
                risk_free_rate=RISK_FREE,
                delisting_after_sessions=DELISTING_AFTER,
                delisting_recovery=recovery,
            )
            spec = BacktestSpec(
                name=f"floor/{label}",
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
            rows.append(
                (
                    args.sample,
                    arm,
                    str(recovery),
                    metrics.cagr,
                    metrics.total_return_pct,
                    metrics.max_drawdown_pct,
                    metrics.sharpe,
                    len(result.trades),
                    metrics.exposure_pct,
                    held,
                )
            )
            sharpe = "n/a" if metrics.sharpe is None else f"{metrics.sharpe:6.3f}"
            # Exposure is printed beside every arm because without it two of
            # these rows are unreadable: a CAGR earned at 7% exposure and one
            # earned at 98% are not the same measurement.
            print(
                f"  {label:<36} CAGR {metrics.cagr:>7.2%}  maxDD {metrics.max_drawdown_pct:>7.2%}  "
                f"Sharpe {sharpe}  exposure {metrics.exposure_pct:>6.1%}  "
                f"{len(result.trades):>5,} trades  {held:>3} held  "
                f"[{time.time() - started:.0f}s]",
                flush=True,
            )

    out = Path(args.out) if args.out else OUT / f"floor_results_s{args.sample}.csv"
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"\nwrote {out}")
    print(
        "\nNot a trial; the ledger does not move. These are reference numbers, not\n"
        "a graded comparison -- see this module's docstring for why that framing\n"
        "is the honest one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
