#!/usr/bin/env python
"""The registered pattern-breakout rule, scanned over the test window.

Implements the scan half of ``docs/prereg/PATTERN_BREAKOUT_TRADES_2026-09-19.md``
(``d94a971``, committed before any 2010-2019 pattern statistic existed). It
emits trades and the eligibility index the placebo control is drawn from; the
replay script prices both and the verdict script judges them.

Two outputs, and the second is not an afterthought:

* **trades** -- one per security per session, the highest-quality instance,
  with the entry and stop the registration fixes.
* **eligibility** -- every (security, session) passing the floors, which is the
  population a placebo is drawn from. Drawing from the securities that *had*
  patterns would compare breakouts to breakouts and answer nothing.
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

from pattern_events_scan import MAX_ATR_PERCENT, MIN_DOLLAR_VOLUME, MIN_PRICE, PENDING, level_on
from signal_research_pattern import DetectorBar, _universe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.enums import Bartimeframe
from tradeit.patterns.scanner import PatternScanner
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

#: Registered: the design window's median quality, where the gradient is monotone.
MIN_QUALITY = 58.5
#: Registered: outside this band the design window is flat to negative.
MIN_STOP_FRACTION = 0.03
MAX_STOP_FRACTION = 0.13
TRIGGER_WINDOW = 10
HOLD = 63
STRIDE = 5
MIN_HISTORY = 100
LOOKBACK_DAYS = 420
TRADE_COLUMNS = (
    "security_id",
    "pattern",
    "quality",
    "scan_date",
    "entry_date",
    "entry",
    "stop",
    "stop_fraction",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31", help="2020+ is held out")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--eligible", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end >= dt.date(2020, 1, 1):
        raise SystemExit("2020-2025 is held out for confirmation and is not read here")
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    scanner = PatternScanner()
    history = scanner.required_history(Bartimeframe.D1)
    universe = _universe(args.spans, args.start, args.end, 250, 10**9)
    print(f"window {history} bars, stride {STRIDE}; universe {len(universe):,}", flush=True)
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    tally = {"scans": 0, "candidates": 0, "trades": 0, "outside_stop_band": 0, "low_quality": 0}
    t0 = time.time()
    with (
        open(args.trades, "w", newline="") as trade_handle,
        open(args.eligible, "w", newline="") as eligible_handle,
    ):
        trades = csv.writer(trade_handle)
        trades.writerow(TRADE_COLUMNS)
        eligible = csv.writer(eligible_handle)
        eligible.writerow(("security_id", "session_date"))
        for n, security_id in enumerate(universe, 1):
            bars = price_series(
                session,
                security_id,
                as_of=as_of,
                start=start - dt.timedelta(days=LOOKBACK_DAYS),
                end=end,
            )
            if len(bars) < history + HOLD + 2:
                continue
            dates = [b.session_date for b in bars]
            position = {d: i for i, d in enumerate(dates)}
            high = np.array([float(b.high) for b in bars])
            low = np.array([float(b.low) for b in bars])
            close = np.array([float(b.close) for b in bars])
            open_ = np.array([float(b.open) for b in bars])
            volume = np.array([float(b.volume) for b in bars])
            with np.errstate(divide="ignore", invalid="ignore"):
                atr_pct = k.atr_percent(high, low, close, 14)
                turnover = k.average_dollar_volume(high, low, close, volume, 20)
            ok = (
                (close >= MIN_PRICE)
                & (volume > 0)
                & np.isfinite(turnover)
                & (turnover >= MIN_DOLLAR_VOLUME)
                & np.isfinite(atr_pct)
                & (atr_pct <= MAX_ATR_PERCENT)
            )
            for index, day in enumerate(dates):
                if ok[index] and start <= day <= end:
                    eligible.writerow((security_id, day.isoformat()))

            shaped = [
                DetectorBar(
                    instrument_id=security_id,
                    timeframe=Bartimeframe.D1,
                    session_date=b.session_date,
                    knowledge_time=dt.datetime.combine(b.session_date, dt.time(21), tzinfo=dt.UTC),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
                for b in bars
            ]
            last = len(bars) - HOLD - 2
            taken: dict[dt.date, tuple[float, tuple[object, ...]]] = {}
            seen: set[str] = set()
            for i in range(history - 1, last, STRIDE):
                if not ok[i] or dates[i] < start:
                    continue
                result = scanner.scan(
                    security_id,
                    Bartimeframe.D1,
                    shaped[i - history + 1 : i + 1],
                    dates[i],
                    track=False,
                )
                tally["scans"] += 1
                for pattern in result.instances:
                    if pattern.state not in PENDING or pattern.identity_key in seen:
                        continue
                    if pattern.quality < MIN_QUALITY:
                        tally["low_quality"] += 1
                        continue
                    boundary = pattern.geometry.resistance
                    if boundary is None or pattern.invalidation_price is None:
                        continue
                    anchor = position.get(boundary.anchor_date)
                    trigger = None
                    for j in range(i + 1, min(i + 1 + TRIGGER_WINDOW, last + 1)):
                        level = level_on(boundary, j, anchor)
                        if level is None:
                            break
                        if close[j] > level > 0:
                            trigger = j
                            break
                    if trigger is None or trigger + 1 >= len(bars):
                        continue
                    entry_index = trigger + 1
                    entry = float(open_[entry_index])
                    stop = float(pattern.invalidation_price)
                    if entry <= 0 or stop <= 0 or stop >= entry:
                        continue
                    fraction = (entry - stop) / entry
                    tally["candidates"] += 1
                    if not MIN_STOP_FRACTION <= fraction <= MAX_STOP_FRACTION:
                        tally["outside_stop_band"] += 1
                        continue
                    seen.add(pattern.identity_key)
                    day = dates[entry_index]
                    row = (
                        security_id,
                        str(pattern.pattern_type),
                        f"{pattern.quality:.2f}",
                        dates[i].isoformat(),
                        day.isoformat(),
                        f"{entry:.6f}",
                        f"{stop:.6f}",
                        f"{fraction:.6f}",
                    )
                    # One per security per session: the highest-quality instance.
                    if day not in taken or pattern.quality > taken[day][0]:
                        taken[day] = (pattern.quality, row)
            for _, (_quality, row) in sorted(taken.items()):
                trades.writerow(row)
                tally["trades"] += 1
            if n % 100 == 0 or n == len(universe):
                print(f"  {n}/{len(universe)}  {tally}  [{time.time() - t0:.0f}s]", flush=True)
    print(f"\n{tally}\n  -> {args.trades}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
