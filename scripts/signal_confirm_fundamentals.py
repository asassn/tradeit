#!/usr/bin/env python
"""Scan for the four fundamental signals, exactly as registered.

Implements ``docs/prereg/FUNDAMENTALS_2026-09-18.md`` (``adc037d``, committed
before any signal was computed). It scans and writes rows; the verdict script
judges them. The price side -- grid, history, endpoints, bad prints, turnover and
the undetermined-volume flag -- is §35's, unchanged; the fundamentals come from
:mod:`tradeit.research01.fundamental_signals`, which holds every point-in-time
rule and is tested for each.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_confirm_adx import LOOKBACK_DAYS, MAX_ATR_PERCENT, MIN_HISTORY, _cell, _universe
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.calendar import get_calendar
from tradeit.research01.fundamental_signals import FundamentalHistory, first_filed
from tradeit.research01.series import VolumeBasis, price_series
from tradeit.signals.sampling import common_grid, grid_points
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityFundamentalFact

HORIZON = 63
STRIDE = 5
METRICS = (
    "NetIncomeLoss",
    "Assets",
    "GrossProfit",
    "NetCashProvidedByUsedInOperatingActivities",
)
SIGNALS = ("sue", "gross_profitability", "asset_growth", "accruals")
COLUMNS = (
    "security_id",
    "session_date",
    "realized_volatility_60",
    "raw_close",
    "atr_percent",
    "avg_dollar_volume_20",
    "volume_undetermined",
    "forward",
    *SIGNALS,
)


def _history(session: Session, security_id: int) -> FundamentalHistory:
    fact = SecurityFundamentalFact
    rows = session.execute(
        select(
            fact.metric, fact.period_end, fact.duration_qtrs, fact.value, fact.knowledge_time
        ).where(fact.security_id == security_id, fact.metric.in_(METRICS))
    ).all()
    return FundamentalHistory(
        first_filed(
            (metric, period_end, int(duration), float(value), known.date())
            for metric, period_end, duration, value, known in rows
            if period_end is not None and duration is not None and value is not None
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2013-01-02")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="first N securities (staging)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    grid = common_grid(get_calendar(), start, end, STRIDE, HORIZON)
    universe = _universe(args.spans, start, end)
    print(
        f"grid: {len(grid.dates)} dates, {grid.dates[0]} .. {grid.dates[-1]}, "
        f"last outcome {grid.outcome_dates[-1]}; universe {len(universe):,} securities",
        flush=True,
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    tally = {"points": 0, "rows": 0, "untraded": 0, "bad_print": 0, "securities": 0}
    defined = dict.fromkeys(SIGNALS, 0)
    t0 = time.time()
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for n, security_id in enumerate(universe, 1):
            bars = price_series(
                session,
                security_id,
                as_of=as_of,
                start=start - dt.timedelta(days=LOOKBACK_DAYS),
                end=end,
            )
            points = grid_points([b.session_date for b in bars], grid, min_history=MIN_HISTORY)
            if points:
                history = _history(session, security_id)
                high = np.array([float(b.high) for b in bars])
                low = np.array([float(b.low) for b in bars])
                close = np.array([float(b.close) for b in bars])
                volume = np.array([float(b.volume) for b in bars])
                with np.errstate(divide="ignore", invalid="ignore"):
                    rv60 = k.realized_volatility(close, 60)
                    atr_pct = k.atr_percent(high, low, close, 14)
                    turnover = k.average_dollar_volume(high, low, close, volume, 20)
                unsure = np.array([float(b.volume_basis is VolumeBasis.UNDETERMINED) for b in bars])
                unsure_window = k.rolling_max(unsure, 20)
                wrote = False
                for p in points:
                    i, j = p.signal_index, p.outcome_index
                    tally["points"] += 1
                    if close[i] <= 0 or close[j] <= 0 or volume[i] <= 0 or volume[j] <= 0:
                        tally["untraded"] += 1
                        continue
                    if not np.isfinite(atr_pct[i]) or atr_pct[i] > MAX_ATR_PERCENT:
                        tally["bad_print"] += 1
                        continue
                    day = p.session_date
                    values = (
                        history.sue(day),
                        history.gross_profitability(day),
                        history.asset_growth(day),
                        history.accruals(day),
                    )
                    if all(v is None for v in values):
                        continue
                    for name, value in zip(SIGNALS, values, strict=True):
                        defined[name] += value is not None
                    writer.writerow(
                        (
                            security_id,
                            day.isoformat(),
                            _cell(rv60[i]),
                            f"{float(bars[i].raw_close):.6f}",
                            _cell(atr_pct[i]),
                            _cell(turnover[i]),
                            "1" if unsure_window[i] >= 1.0 else "0",
                            f"{close[j] / close[i] - 1.0:.8f}",
                            *["" if v is None else f"{v:.8g}" for v in values],
                        )
                    )
                    tally["rows"] += 1
                    wrote = True
                tally["securities"] += int(wrote)
            if n % 250 == 0 or n == len(universe):
                print(
                    f"  {n}/{len(universe)}  {tally}  defined {defined}  [{time.time() - t0:.0f}s]",
                    flush=True,
                )

    print(f"\n{tally}\ndefined {defined}\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
