#!/usr/bin/env python
"""Run the registered stop-ladder test: the default exits against holding.

Implements ``docs/prereg/STOP_LADDER_2026-09-18.md`` (``dc3499a``, committed
before the hold switch existed). It runs and records; ``backtest_stop_ladder_verdict.py``
judges.

``--window primary`` is the registered test, 2010-2019. ``--window diagnostic``
is 2020-2025 -- the data that suggested the question, which can only illustrate
it. It doubles as a machinery check: its LADDER arm is §37's RANDOM portfolio
exactly, so it must reproduce §37's numbers.

``--pilot`` prints mechanics only, never a return.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_lowvol_tilt import (
    CAPITAL,
    DELISTING_AFTER,
    FLOOR,
    HORIZON,
    MIN_PRICE,
    PARTICIPATION,
    RISK_FREE,
    SEED,
    _manifest,
    samples,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.factor import FactorTilt, Selection
from tradeit.core.calendar import get_calendar
from tradeit.signals.sampling import common_grid
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


@dataclass(frozen=True)
class Window:
    start: dt.date
    end: dt.date
    warmup_from: dt.date
    split: dt.date
    population: Path
    runs: tuple[tuple[str, Decimal], ...]  # (costs, recovery)


WINDOWS = {
    "primary": Window(
        dt.date(2010, 1, 4),
        dt.date(2019, 12, 31),
        dt.date(2009, 1, 2),
        dt.date(2015, 1, 1),
        OUT / "population_2010.json",
        (
            ("base", Decimal("1.0")),
            ("stress", Decimal("1.0")),
            ("base", Decimal("0.0")),
            ("stress", Decimal("0.0")),
        ),
    ),
    "diagnostic": Window(
        dt.date(2020, 1, 2),
        dt.date(2025, 12, 31),
        dt.date(2019, 1, 2),
        dt.date(2023, 1, 1),
        OUT / "lowvol_bt_population.json",
        (("base", Decimal("1.0")),),
    ),
}
COSTS = {"base": {}, "stress": {"spread_bps": 15.0, "slippage_bps": 25.0}}
EXITS = ("stop_loss", "trailing_stop", "partial_profit", "time_stop", "delisted_exit")
COLUMNS = (
    "window",
    "sample",
    "arm",
    "costs",
    "recovery",
    "cagr",
    "total_return",
    "max_drawdown",
    "sharpe",
    "trades",
    "exposure",
    "first_half_annualised",
    "second_half_annualised",
    *EXITS,
)


def _halves(result: BacktestResult, split: dt.date) -> tuple[float, float]:
    curve = result.equity_curve
    first = [e for d, e in curve if d < split]
    second = [e for d, e in curve if d >= split]

    def annualised(opening: Decimal, closing: Decimal, sessions: int) -> float:
        if sessions == 0 or opening <= 0:
            return float("nan")
        return float((closing / opening) ** Decimal(252 / sessions)) - 1.0

    mid = first[-1] if first else CAPITAL
    return (
        annualised(CAPITAL, mid, len(first)),
        annualised(mid, second[-1] if second else mid, len(second)),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--window", required=True, choices=sorted(WINDOWS))
    ap.add_argument("--sample", type=int, required=True, choices=range(4))
    ap.add_argument("--pilot", type=int, default=0, help="first N securities; mechanics only")
    args = ap.parse_args()
    window = WINDOWS[args.window]

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    pop = list(json.loads(window.population.read_text()))
    drawn = samples(pop)[args.sample]
    universe = drawn[: args.pilot] if args.pilot else drawn
    print(
        f"{args.window}: population {len(pop):,}; sample {args.sample}: "
        f"{len(universe):,} securities{' (PILOT -- mechanics only)' if args.pilot else ''}",
        flush=True,
    )

    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=window.warmup_from,
        end=window.end,
        candidate_source=lambda d, b: [],
    )
    rebalance = frozenset(
        common_grid(get_calendar(), window.start, window.end, HORIZON, HORIZON).dates
    )
    warmup = [d for d in data.sessions(window.warmup_from, window.end) if d < window.start]

    out = OUT / f"stopladder_{args.window}_s{args.sample}.csv"
    if not args.pilot and out.exists():
        print(f"{out} exists; refusing to overwrite a registered run")
        return 2
    with open("/dev/null", "w") if args.pilot else out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for cost_label, recovery in window.runs:
            for arm in ("ladder", "hold"):
                tilt = FactorTilt(
                    selection=Selection.RANDOM,
                    rebalance_dates=rebalance,
                    splits_on=data.splits_on,
                    volatility_lookback=60,
                    fraction=0.2,
                    min_history=100,
                    min_price=MIN_PRICE,
                    min_dollar_volume=FLOOR,
                    dollar_volume_lookback=20,
                    max_atr_percent=1.0,
                    stop_pct=Decimal("0.08"),
                    seed=SEED,
                )
                for day in warmup:
                    tilt.observe(day, data.bars(day))
                data.candidate_source = tilt
                config = StrategyConfig(
                    name=f"stopladder_{arm}",
                    exits={"time_stop_sessions": HORIZON, "price_exits": arm == "ladder"},
                    sizing={"allow_pyramiding": False},
                    costs=COSTS[cost_label],
                )
                label = f"{args.window[:4]}-s{args.sample}-{arm}-{cost_label}-r{recovery}"
                engine = build_engine(
                    config,
                    data,
                    _manifest(config, label, data.bar_count),
                    participation=PARTICIPATION,
                    risk_free_rate=RISK_FREE,
                    delisting_after_sessions=DELISTING_AFTER,
                    delisting_recovery=recovery,
                )
                t0 = time.time()
                result = engine.run(
                    BacktestSpec(
                        name=f"stopladder/{label}",
                        start=window.start,
                        end=window.end,
                        universe=f"{args.window}-sample-{args.sample}",
                        initial_capital=CAPITAL,
                        strategy_config_digest=config.digest,
                        cost_model="participation",
                        fill_model="bar",
                    )
                )
                reasons = collections.Counter(
                    str(t.exit_reason).split(".")[-1].lower() for t in result.trades
                )
                print(
                    f"  {label:<34} {len(result.trades):>5,} trades  "
                    + " ".join(f"{k} {reasons.get(k, 0)}" for k in EXITS)
                    + f"  {time.time() - t0:>4.0f}s",
                    flush=True,
                )
                m = result.metrics
                if args.pilot or m is None:
                    continue
                first, second = _halves(result, window.split)
                writer.writerow(
                    (
                        args.window,
                        args.sample,
                        arm,
                        cost_label,
                        str(recovery),
                        f"{m.cagr:.6f}",
                        f"{m.total_return_pct:.6f}",
                        f"{m.max_drawdown_pct:.6f}",
                        "" if m.sharpe is None else f"{m.sharpe:.4f}",
                        m.trade_count,
                        f"{m.exposure_pct:.4f}",
                        f"{first:.6f}",
                        f"{second:.6f}",
                        *[reasons.get(k, 0) for k in EXITS],
                    )
                )
                handle.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
