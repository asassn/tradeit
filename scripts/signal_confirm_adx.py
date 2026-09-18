#!/usr/bin/env python
"""Scan for the `adx_14` confirmation, exactly as registered.

Implements ``docs/prereg/ADX_CONFIRMATION_2026-09-18.md`` (committed at
``324e26b`` before any scan). It **scans** and writes rows; it does not judge.
``signal_verdict_adx.py`` applies the registered criteria to what this writes,
for the same reason the indicator screen kept the two apart: no criterion can be
adjusted while looking at the number it is about to judge.

**Every row the registration's inclusion rules admit is written**, including
those below the liquidity floor, because the positive control is measured
without it. The floor is applied to `adx_14` in the verdict, from the registered
constant -- fixed in writing before the run, which is what the indicator screen
applied it at sampling time to guarantee.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.calendar import get_calendar
from tradeit.research01.series import VolumeBasis, price_series
from tradeit.signals.sampling import common_grid, grid_points
from tradeit.storage.session import install_sqlite_busy_timeout

#: All registered. Changing any of these is a new registration, not a rerun.
HORIZON = 63
STRIDE = 5
MIN_HISTORY = 100
MAX_ATR_PERCENT = 1.0
#: Calendar days fetched before the window, so a grid date near the start can
#: still have MIN_HISTORY bars behind it. 100 sessions is ~145 calendar days;
#: the margin covers holidays and a thin name's gaps.
LOOKBACK_DAYS = 220
COLUMNS = (
    "security_id",
    "session_date",
    "adx_14",
    "realized_volatility_60",
    "atr_percent",
    "avg_dollar_volume_20",
    "volume_undetermined",
    "forward",
)


def _universe(path: str, start: dt.date, end: dt.date) -> list[int]:
    """Every security whose raw prints overlap the window -- and nothing else.

    **No whole-life bar count.** Every earlier study filtered on bars over a
    security's entire life, which conditions on the future. History is required
    point-in-time instead, by ``grid_points(min_history=...)``.
    """
    ids = []
    with open(path) as handle:
        for sid, first, last, _count in csv.reader(handle):
            if first <= end.isoformat() and last >= start.isoformat():
                ids.append(int(sid))
    return sorted(ids)


def _cell(value: float) -> str:
    return "" if not np.isfinite(value) else f"{value:.8f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
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
        f"last outcome {grid.outcome_dates[-1]}; universe {len(universe):,} securities"
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]
    print(f"  this run: {len(universe):,} securities")

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
                    adx = k.adx(high, low, close, 14)[2]
                    rv60 = k.realized_volatility(close, 60)
                    atr_pct = k.atr_percent(high, low, close, 14)
                    turnover = k.average_dollar_volume(high, low, close, volume, 20)
                # Amendment 1: a floor may not rest on a volume whose basis the
                # read could not establish. Flag the observation if ANY bar in
                # the 20-session turnover window is UNDETERMINED.
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
                            _cell(adx[i]),
                            _cell(rv60[i]),
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

    print(f"\n{tally}\n  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
