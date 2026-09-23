#!/usr/bin/env python
"""Low volatility x earnings surprise, as a portfolio, exactly as registered.

``docs/prereg/COMBINATION_PORTFOLIO_2026-09-22.md`` at ``bd75cc4``, committed
before this file existed. Built on §37's runner because the question is §37's
question asked of a different selection: four disjoint samples, four arms
identical in everything except which securities they nominate, and the
platform's own sizer, stops, costs, fills and delisting handling deciding what
is actually bought.

The criterion the registration exists for is criterion 2: **the combination must
beat both of its own components**, not merely the random control. A combination
that beats only the control has rediscovered whichever component was carrying
it.

``sue`` is computed here rather than inside the strategy, because it is
point-in-time evidence about a filing and ``FactorTilt`` only ever sees bars.
Each security's fundamental history is read once and evaluated at each
rebalance date, with §40's rules: as first filed, usable only if filed strictly
before that session.
"""

from __future__ import annotations

import argparse
import collections
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

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.factor import FactorTilt, Selection
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest

#: DELISTING_RECOVERY_2026-09-23's mapping, from what a holder actually received.
PAID = {"acquired", "extinguished"}
WIPED = {"bankrupt"}

from tradeit.research01.fundamental_signals import FundamentalHistory, first_filed
from tradeit.research01.series import price_series
from tradeit.signals.sampling import common_grid
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityFundamentalFact
from tradeit.strategy.config import StrategyConfig

#: Every number here is registered.
START = dt.date(2013, 1, 2)
END = dt.date(2019, 12, 31)
WARMUP_FROM = dt.date(2012, 1, 3)
HORIZON = 63
SEED = 20260918
SAMPLE_CAP = 500
FLOOR = Decimal(1_000_000)
#: LiquidityConfig.min_price -- the platform's declared minimum tradeable price,
#: read from the default rather than typed. Amendment 1 to the registration.
MIN_PRICE = Decimal(str(StrategyConfig(name="defaults").liquidity.min_price))
CAPITAL = Decimal(100_000)
PARTICIPATION = Decimal("0.02")
RISK_FREE = 0.03
DELISTING_AFTER = 10
SPLIT = dt.date(2016, 7, 1)
COSTS = {"base": {}, "stress": {"spread_bps": 15.0, "slippage_bps": 25.0}}
ARMS = (Selection.COMBINED, Selection.CALM, Selection.SURPRISE, Selection.RANDOM)
#: §40's metrics, needed for SUE alone.
METRICS = ("NetIncomeLoss", "Assets", "GrossProfit", "NetCashProvidedByUsedInOperatingActivities")
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
    """Alive on 2013-01-02 and liquid across 2012 on the corrected read."""
    if cache.exists():
        return list(json.loads(cache.read_text()))
    alive = sorted(
        int(sid)
        for sid, first, last, _ in csv.reader(spans.open())
        if first <= START.isoformat() <= last
    )
    as_of = dt.datetime.combine(dt.date(2012, 12, 31), dt.time(21), tzinfo=dt.UTC)
    keep: list[int] = []
    for n, sid in enumerate(alive, 1):
        bars = price_series(
            session, sid, as_of=as_of, start=dt.date(2012, 1, 3), end=dt.date(2012, 12, 31)
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


def surprise_scores(
    session: Session, universe: list[int], dates: frozenset[dt.date]
) -> dict[tuple[int, dt.date], float]:
    """``sue`` per (security, rebalance date), by §40's point-in-time rules."""
    fact = SecurityFundamentalFact
    out: dict[tuple[int, dt.date], float] = {}
    ordered = sorted(dates)
    for n, security_id in enumerate(universe, 1):
        rows = session.execute(
            select(
                fact.metric, fact.period_end, fact.duration_qtrs, fact.value, fact.knowledge_time
            ).where(fact.security_id == security_id, fact.metric.in_(METRICS))
        ).all()
        history = FundamentalHistory(
            first_filed(
                (metric, period_end, int(duration), float(value), known.date())
                for metric, period_end, duration, value, known in rows
                if period_end is not None and duration is not None and value is not None
            )
        )
        for day in ordered:
            value = history.sue(day)
            if value is not None:
                out[(security_id, day)] = float(value)
        if n % 100 == 0 or n == len(universe):
            print(f"  sue: {n}/{len(universe)} securities, {len(out):,} scores", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--sample", type=int, required=True, choices=range(4))
    ap.add_argument("--pilot", type=int, default=0, help="first N securities; mechanics only")
    ap.add_argument(
        "--causes",
        type=Path,
        default=None,
        help="DELISTING_RECOVERY_2026-09-23: a measured per-security recovery, "
        "from research01_exit_causes. Replaces the single number.",
    )
    ap.add_argument(
        "--residual",
        type=float,
        default=None,
        help="what an unexplained exit recovers, 1.0 or 0.0. The registration "
        "brackets it rather than choosing, so both are run and both reported.",
    )
    ap.add_argument(
        "--regime",
        type=int,
        default=None,
        help="MARKET_REGIME_GATE_2026-09-22: nominate nothing on a rebalance "
        "where the sample's own equal-weighted index is below its average over "
        "this many sessions",
    )
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="COMBINATION_CONFIRMATION_2026-09-22: run the held-out 2020-2025 "
        "window on §37's own population and samples, unchanged",
    )
    ap.add_argument(
        "--atr-stop",
        type=float,
        default=None,
        help="VOLATILITY_STOP_2026-09-22: stop at this many ATR(14) below entry, "
        "floored at 3%% and capped at 13%%, instead of the fixed 8%%",
    )
    ap.add_argument(
        "--exits",
        default=None,
        help="write per-arm exit-reason counts here. Diagnostic only: it explains "
        "a result, it does not judge one.",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="results CSV; defaults to one file per sample so parallel runs never share one",
    )
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    if args.confirm:
        # §37's window, population and samples, so its CALM and RANDOM numbers
        # stand beside this run without a second sampling difference.
        globals()["START"] = dt.date(2020, 1, 2)
        globals()["END"] = dt.date(2025, 12, 31)
        globals()["WARMUP_FROM"] = dt.date(2019, 1, 2)
        globals()["SPLIT"] = dt.date(2023, 1, 1)
        cache = OUT / "lowvol_bt_population.json"
        if not cache.exists():
            raise SystemExit("§37's population cache is missing; the samples would not match")
    else:
        cache = OUT / "combo_bt_population.json"
    pop = population(session, Path(args.spans), cache)
    if args.confirm and len(pop) != 2806:
        # §37 recorded 2,806 securities alive and liquid on 2020-01-02. If this
        # is not that number the samples are not §37's, and the comparison the
        # registration promises does not exist.
        raise SystemExit(f"population is {len(pop):,}, not §37's 2,806: samples would not match")
    drawn = samples(pop)
    print(f"window {START} .. {END} (warmup from {WARMUP_FROM})", flush=True)
    universe = drawn[args.sample][: args.pilot] if args.pilot else drawn[args.sample]
    print(
        f"population {len(pop):,}; sample {args.sample}: {len(universe):,} securities"
        f"{' (PILOT -- mechanics only)' if args.pilot else ''}",
        flush=True,
    )

    measured: dict[int, Decimal] = {}
    if args.causes is not None:
        if args.residual is None:
            raise SystemExit("--causes needs --residual: the unexplained are bracketed, not chosen")
        residual = Decimal(str(args.residual))
        with args.causes.open() as handle:
            for row in csv.DictReader(handle):
                cause = row["cause"]
                measured[int(row["security_id"])] = (
                    Decimal(1) if cause in PAID else Decimal(0) if cause in WIPED else residual
                )
        counts = collections.Counter(
            "paid" if v == 1 else "wiped" if v == 0 else "residual" for sid, v in measured.items()
        )
        print(
            f"measured recovery for {len(measured):,} securities "
            f"(residual at {residual}): {dict(counts)}",
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
    surprise = surprise_scores(session, list(universe), rebalance)
    covered = len({sid for sid, _ in surprise})
    print(
        f"  sue: {len(surprise):,} scores over {covered:,} of {len(universe):,} securities "
        f"and {len(rebalance)} rebalances",
        flush=True,
    )
    warmup = [d for d in data.sessions(WARMUP_FROM, END) if d < START]

    # A pilot writes nothing: an empty file left behind would make the real
    # run skip its header.
    out = Path(args.out) if args.out else OUT / f"combo_bt_results_s{args.sample}.csv"
    if not args.pilot and out.exists():
        print(f"{out} exists; refusing to append a second run to it")
        return 2
    with open("/dev/null", "w") if args.pilot else out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        # With a measured map the bracket IS the residual: every classified
        # death already carries its own number, so looping the old pair would
        # differ only on securities the classifier did not cover.
        recoveries = (Decimal(str(args.residual)),) if args.causes else RECOVERIES
        for recovery in recoveries:
            for cost_label, cost_override in COSTS.items():
                for arm in ARMS:
                    tilt = FactorTilt(
                        selection=arm,
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
                        external=surprise,
                        atr_stop_multiple=args.atr_stop,
                        regime_lookback=args.regime,
                    )
                    for day in warmup:
                        tilt.observe(day, data.bars(day))
                    data.candidate_source = tilt
                    config = StrategyConfig(
                        name=f"combo_{arm.value}",
                        exits={"time_stop_sessions": HORIZON},
                        sizing={"allow_pyramiding": False},
                        costs=cost_override,
                    )
                    stop_label = "fixed" if args.atr_stop is None else f"atr{args.atr_stop:g}"
                    stop_label += "" if args.regime is None else f"-gate{args.regime}"
                    stop_label += "" if args.causes is None else f"-measured{args.residual:g}"
                    label = f"s{args.sample}-{arm.value}-{cost_label}-r{recovery}-{stop_label}"
                    engine = build_engine(
                        config,
                        data,
                        _manifest(config, label, data.bar_count),
                        participation=PARTICIPATION,
                        risk_free_rate=RISK_FREE,
                        delisting_after_sessions=DELISTING_AFTER,
                        delisting_recovery=recovery,
                        delisting_recovery_by_instrument=measured,
                    )
                    spec = BacktestSpec(
                        name=f"combo/{label}",
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
                    if args.exits:
                        tally = collections.Counter(str(t.exit_reason) for t in result.trades)
                        with open(args.exits, "a", newline="") as exit_handle:
                            csv.writer(exit_handle).writerow(
                                (
                                    args.sample,
                                    arm.value,
                                    cost_label,
                                    str(recovery),
                                    len(result.trades),
                                    json.dumps(dict(tally)),
                                )
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
