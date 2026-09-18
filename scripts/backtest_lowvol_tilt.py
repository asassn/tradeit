#!/usr/bin/env python
"""Run the registered low-volatility portfolio backtest.

Implements ``docs/prereg/LOW_VOLATILITY_BACKTEST_2026-09-18.md`` (``452e59d``,
committed before the strategy existed). It **runs and records**; the verdict is
applied by ``backtest_lowvol_verdict.py`` from the results file, for the reason
every registered study here keeps the two apart.

One sample per invocation, so the four can run side by side. Each sample's bars
are loaded **once** and every run for it -- two arms, two cost settings, two
delisting assumptions -- reuses them, swapping only the candidate source.

``--pilot`` runs the mechanics on a handful of securities and prints only what
shows the machinery works -- bars loaded, trades taken, seconds -- never a
return. Staging on real data must not become an early look at the answer.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.factor import FactorTilt, Selection
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.research01.series import price_series
from tradeit.signals.sampling import common_grid
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

#: Every number here is registered.
START = dt.date(2020, 1, 2)
END = dt.date(2025, 12, 31)
WARMUP_FROM = dt.date(2019, 1, 2)
HORIZON = 63
SEED = 20260918
SAMPLE_CAP = 500
FLOOR = Decimal(1_000_000)
CAPITAL = Decimal(100_000)
PARTICIPATION = Decimal("0.02")
RISK_FREE = 0.03
DELISTING_AFTER = 10
SPLIT = dt.date(2023, 1, 1)
COSTS = {"base": {}, "stress": {"spread_bps": 15.0, "slippage_bps": 25.0}}
RECOVERIES = (Decimal("1.0"), Decimal("0.0"))
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
COLUMNS = (
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
    "securities",
)


def population(session: Session, spans: Path, cache: Path) -> list[int]:
    """Alive on 2020-01-02 and liquid across 2019 on the corrected read."""
    if cache.exists():
        return list(json.loads(cache.read_text()))
    alive = sorted(
        int(sid)
        for sid, first, last, _ in csv.reader(spans.open())
        if first <= START.isoformat() <= last
    )
    as_of = dt.datetime.combine(dt.date(2019, 12, 31), dt.time(21), tzinfo=dt.UTC)
    keep: list[int] = []
    for n, sid in enumerate(alive, 1):
        bars = price_series(
            session, sid, as_of=as_of, start=dt.date(2019, 1, 2), end=dt.date(2019, 12, 31)
        )
        traded = [float(b.close * b.volume) for b in bars if b.volume > 0]
        if len(traded) >= 100 and statistics.median(traded) >= float(FLOOR):
            keep.append(sid)
        if n % 500 == 0:
            print(f"  population: {n:,}/{len(alive):,} read, {len(keep):,} liquid", flush=True)
    cache.write_text(json.dumps(keep))
    return keep


def samples(pop: list[int]) -> list[list[int]]:
    """Four disjoint samples, drawn in proportion -- not balanced by fate."""
    order = [int(x) for x in np.random.default_rng(SEED).permutation(sorted(pop))]
    size = min(SAMPLE_CAP, len(order) // 4)
    return [sorted(order[k * size : (k + 1) * size]) for k in range(4)]


def _halves(result: BacktestResult) -> tuple[float, float]:
    """Annualised return of each half, from the arm's own equity curve."""
    curve = result.equity_curve
    first = [(d, e) for d, e in curve if d < SPLIT]
    second = [(d, e) for d, e in curve if d >= SPLIT]

    def annualised(opening: Decimal, closing: Decimal, sessions: int) -> float:
        if sessions == 0 or opening <= 0:
            return float("nan")
        return float((closing / opening) ** Decimal(252 / sessions)) - 1.0

    mid = first[-1][1] if first else CAPITAL
    return (
        annualised(CAPITAL, mid, len(first)),
        annualised(mid, second[-1][1] if second else mid, len(second)),
    )


def _manifest(config: StrategyConfig, label: str, bars: int) -> RunManifest:
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"lowvol-{label}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{label}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="lowvol-tilt",
        created_at=now,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--sample", type=int, required=True, choices=range(4))
    ap.add_argument("--pilot", type=int, default=0, help="first N securities; mechanics only")
    ap.add_argument(
        "--out",
        default=None,
        help="results CSV; defaults to one file per sample so parallel runs never share one",
    )
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    pop = population(session, Path(args.spans), OUT / "lowvol_bt_population.json")
    drawn = samples(pop)
    universe = drawn[args.sample][: args.pilot] if args.pilot else drawn[args.sample]
    print(
        f"population {len(pop):,}; sample {args.sample}: {len(universe):,} securities"
        f"{' (PILOT -- mechanics only)' if args.pilot else ''}",
        flush=True,
    )

    t0 = time.time()
    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=WARMUP_FROM,
        end=END,
        # Replaced by each run's FactorTilt before the engine calls it.
        candidate_source=lambda d, b: [],
    )
    print(f"  {data.bar_count:,} bars loaded in {time.time() - t0:.0f}s", flush=True)
    rebalance = frozenset(common_grid(get_calendar(), START, END, HORIZON, HORIZON).dates)
    warmup = [d for d in data.sessions(WARMUP_FROM, END) if d < START]

    # A pilot writes nothing: an empty file left behind would make the real
    # run skip its header.
    out = Path(args.out) if args.out else OUT / f"lowvol_bt_results_s{args.sample}.csv"
    if not args.pilot and out.exists():
        print(f"{out} exists; refusing to append a second run to it")
        return 2
    with open("/dev/null", "w") if args.pilot else out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for recovery in RECOVERIES:
            for cost_label, cost_override in COSTS.items():
                for arm in (Selection.CALM, Selection.RANDOM):
                    tilt = FactorTilt(
                        selection=arm,
                        rebalance_dates=rebalance,
                        splits_on=data.splits_on,
                        volatility_lookback=60,
                        fraction=0.2,
                        min_history=100,
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
                        name=f"lowvol_{arm.value}",
                        exits={"time_stop_sessions": HORIZON},
                        sizing={"allow_pyramiding": False},
                        costs=cost_override,
                    )
                    label = f"s{args.sample}-{arm.value}-{cost_label}-r{recovery}"
                    engine = build_engine(
                        config,
                        data,
                        _manifest(config, label, data.bar_count),
                        participation=PARTICIPATION,
                        risk_free_rate=RISK_FREE,
                        delisting_after_sessions=DELISTING_AFTER,
                        delisting_recovery=recovery,
                    )
                    spec = BacktestSpec(
                        name=f"lowvol/{label}",
                        start=START,
                        end=END,
                        universe=f"sample-{args.sample}",
                        initial_capital=CAPITAL,
                        strategy_config_digest=config.digest,
                        cost_model="participation",
                        fill_model="bar",
                    )
                    t1 = time.time()
                    result = engine.run(spec)
                    m = result.metrics
                    print(
                        f"  {label:<28} {len(result.trades):>5,} trades, "
                        f"{len(tilt.nominated)} rebalances, {time.time() - t1:>5.0f}s",
                        flush=True,
                    )
                    if args.pilot or m is None:
                        continue
                    first, second = _halves(result)
                    writer.writerow(
                        (
                            args.sample,
                            arm.value,
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
                            len(universe),
                        )
                    )
                    handle.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
