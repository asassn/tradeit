#!/usr/bin/env python
"""Scan for the low-volatility re-test on 2020-2025, exactly as registered.

Implements ``docs/prereg/LOW_VOLATILITY_2020_2025_2026-09-18.md`` (committed at
``e12ec7e`` before any scan). It scans and writes rows; ``signal_verdict_lowvol.py``
judges them, for the reason every registered study here keeps the two apart.

Every admitted row is written, above and below the floor, because the
registration's declared diagnostics read the unfloored sample. The floor itself
is applied in the verdict, from the registered constant.

**The raw share price is carried** for criterion 4's within-date price bands.
It is the stored print at the signal session -- ``AdjustedBar.raw_close`` --
not the split-adjusted close, which is restated to the window's end and would
put a 2020 price on a 2025 share count.
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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.calendar import get_calendar
from tradeit.research01.series import VolumeBasis, price_series
from tradeit.signals.sampling import common_grid, grid_points
from tradeit.storage.session import install_sqlite_busy_timeout

#: Registered. Changing any of these is a new registration, not a rerun.
HORIZON = 63
STRIDE = 5
COLUMNS = (
    "security_id",
    "session_date",
    "realized_volatility_60",
    "raw_close",
    "atr_percent",
    "avg_dollar_volume_20",
    "volume_undetermined",
    "forward",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2020-01-02")
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
                    writer.writerow(
                        (
                            security_id,
                            p.session_date.isoformat(),
                            _cell(rv60[i]),
                            f"{float(bars[i].raw_close):.6f}",
                            _cell(atr_pct[i]),
                            _cell(turnover[i]),
                            "1" if unsure_window[i] >= 1.0 else "0",
                            f"{close[j] / close[i] - 1.0:.8f}",
                        )
                    )
                    tally["rows"] += 1
                    wrote = True
                tally["securities"] += int(wrote)
            if n % 250 == 0 or n == len(universe):
                print(f"  {n}/{len(universe)}  {tally}  [{time.time() - t0:.0f}s]", flush=True)

    print(f"\n{tally}\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
