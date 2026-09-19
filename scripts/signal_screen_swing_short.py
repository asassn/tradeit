#!/usr/bin/env python
"""Scan the eight swing indicators at 5 and 10 sessions, exactly as registered.

Implements ``docs/prereg/SWING_SHORT_HORIZON_2026-09-19.md`` (``88816df``,
committed before any signal was computed at either horizon). It scans and
writes rows; the verdict script judges them.

Both horizons come from **one** pass over the grid: the signal values on a date
do not depend on the horizon, only the outcome does, so scanning twice would
read the same bars twice and risk the two panels disagreeing about a date.

The outcome rule is §41's -- a traded bar on the outcome session, or the last
traded close of a security that never trades again -- so a company that dies
inside the window stays in the sample instead of leaving it to the survivors.
``LOOKBACK_DAYS`` is wider than §35's because two arms need 252 and 200 bars of
their own history before they are defined.
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

from signal_confirm_adx import MAX_ATR_PERCENT, MIN_HISTORY, _cell, _universe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.calendar import get_calendar
from tradeit.research01.series import VolumeBasis, price_series
from tradeit.signals.sampling import common_grid, outcome_or_terminal
from tradeit.storage.session import install_sqlite_busy_timeout

HORIZONS = (5, 10)
STRIDE = 5
#: 252 sessions of warm-up plus MIN_HISTORY, in calendar days.
LOOKBACK_DAYS = 560
SIGNALS = (
    "rsi_14",
    "stochastic_k_14_3",
    "bollinger_percent_b_20",
    "rate_of_change_10",
    "macd_histogram_norm",
    "ema_9_21_distance",
    "sma_50_200_distance",
    "percent_rank_close_252",
)
COLUMNS = (
    "security_id",
    "session_date",
    "realized_volatility_60",
    "raw_close",
    "atr_percent",
    "avg_dollar_volume_20",
    "volume_undetermined",
    *[f"{field}_{h}" for h in HORIZONS for field in ("forward", "terminal")],
    *SIGNALS,
)


def indicators(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict[str, np.ndarray]:
    """The eight signals, each as the registration defines it."""
    _, _, macd_hist = k.macd(close)
    _, upper, lower, _ = k.bollinger_bands(close, 20, 2.0)
    span = upper - lower
    ema9, ema21 = k.ema(close, 9), k.ema(close, 21)
    sma50, sma200 = k.sma(close, 50), k.sma(close, 200)
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "rsi_14": k.rsi(close, 14),
            "stochastic_k_14_3": k.stochastic_k(high, low, close, 14, 3),
            "bollinger_percent_b_20": np.where(span > 0, (close - lower) / span, np.nan),
            "rate_of_change_10": k.rate_of_change(close, 10),
            "macd_histogram_norm": np.where(close > 0, macd_hist / close, np.nan),
            "ema_9_21_distance": np.where(ema21 > 0, (ema9 - ema21) / ema21, np.nan),
            "sma_50_200_distance": np.where(sma200 > 0, (sma50 - sma200) / sma200, np.nan),
            "percent_rank_close_252": k.percent_rank(close, 252),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31", help="2020-2025 is held out and not read")
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
    calendar = get_calendar()
    grid = common_grid(calendar, start, end, STRIDE, min(HORIZONS))
    sessions = calendar.sessions_between(start, end)
    place = {d: n for n, d in enumerate(sessions)}
    # The outcome session for each horizon, or None where it would fall past
    # the registered window -- never past it, which is what holds 2020 out.
    outcomes = {
        d: tuple(sessions[place[d] + h] if place[d] + h < len(sessions) else None for h in HORIZONS)
        for d in grid.dates
    }
    universe = _universe(args.spans, start, end)
    print(
        f"grid: {len(grid.dates)} dates, {grid.dates[0]} .. {grid.dates[-1]}; "
        f"last session read {sessions[-1]}; universe {len(universe):,} securities",
        flush=True,
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    tally = {"points": 0, "rows": 0, "untraded": 0, "bad_print": 0}
    tally |= {f"terminal_{h}": 0 for h in HORIZONS}
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
            dates = [b.session_date for b in bars]
            position = {d: i for i, d in enumerate(dates)}
            points = [
                (d, position[d])
                for d in grid.dates
                if d in position and position[d] + 1 >= MIN_HISTORY
            ]
            if not points:
                continue
            high = np.array([float(b.high) for b in bars])
            low = np.array([float(b.low) for b in bars])
            close = np.array([float(b.close) for b in bars])
            volume = np.array([float(b.volume) for b in bars])
            traded = ((close > 0) & (volume > 0)).tolist()
            with np.errstate(divide="ignore", invalid="ignore"):
                rv60 = k.realized_volatility(close, 60)
                atr_pct = k.atr_percent(high, low, close, 14)
                turnover = k.average_dollar_volume(high, low, close, volume, 20)
            values = indicators(high, low, close)
            unsure = np.array([float(b.volume_basis is VolumeBasis.UNDETERMINED) for b in bars])
            unsure_window = k.rolling_max(unsure, 20)
            for day, i in points:
                tally["points"] += 1
                if not traded[i]:
                    tally["untraded"] += 1
                    continue
                if not np.isfinite(atr_pct[i]) or atr_pct[i] > MAX_ATR_PERCENT:
                    tally["bad_print"] += 1
                    continue
                measured = []
                for h, target in zip(HORIZONS, outcomes[day], strict=True):
                    found = (
                        outcome_or_terminal(dates, traded, i, target)
                        if target is not None
                        else None
                    )
                    if found is None:
                        measured += ["", ""]
                        continue
                    measured += [
                        f"{close[found.index] / close[i] - 1.0:.8f}",
                        "1" if found.terminal else "0",
                    ]
                    tally[f"terminal_{h}"] += int(found.terminal)
                if not any(measured):
                    tally["untraded"] += 1
                    continue
                writer.writerow(
                    (
                        security_id,
                        day.isoformat(),
                        _cell(rv60[i]),
                        f"{float(bars[i].raw_close):.6f}",
                        _cell(atr_pct[i]),
                        _cell(turnover[i]),
                        "1" if unsure_window[i] >= 1.0 else "0",
                        *measured,
                        *[_cell(values[name][i]) for name in SIGNALS],
                    )
                )
                tally["rows"] += 1
            if n % 250 == 0 or n == len(universe):
                print(f"  {n}/{len(universe)}  {tally}  [{time.time() - t0:.0f}s]", flush=True)

    print(f"\n{tally}\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
