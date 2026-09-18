#!/usr/bin/env python
"""Scan for the four fundamental signals against a twelve-month outcome.

Implements ``docs/prereg/FUNDAMENTALS_12M_2026-09-18.md`` (``9547237``,
committed before any signal was computed at this horizon). The signals and the
signal-side inclusion rules are §40's, imported rather than restated; what is
new is the outcome.

**Why this does not use ``grid_points``.** That function drops a point whose
security has no print on the outcome session. At 63 sessions that loses few;
at 252 it loses every company that died during the year, which is survivorship
bias. Here a security with no traded bar on or after its outcome session is
kept as TERMINAL and measured to its last traded close; the verdict prices it
at both registered recoveries. A security untraded on the outcome session but
trading later is still dropped, as before -- it did not die, and moving it to
a neighbouring print would measure a different window from everyone else's.

Columns written, all as price relatives so the verdict applies recovery:

* ``forward``  -- close(outcome or last traded) / close(signal) - 1
* ``terminal`` -- 1 when the security never traded again on or after the
  outcome session
* ``quarter``  -- close(63rd session) / close(signal) - 1 when the security
  traded on that session, else empty; criterion 5's denominator
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
from signal_confirm_fundamentals import SIGNALS, _history
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.calendar import get_calendar
from tradeit.research01.series import VolumeBasis, price_series
from tradeit.signals.sampling import common_grid, outcome_or_terminal
from tradeit.storage.session import install_sqlite_busy_timeout

HORIZON = 252
#: The Swing horizon §40 measured; criterion 5 asks what happens after it.
SEEN = 63
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
    "terminal",
    "quarter",
    *SIGNALS,
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
    calendar = get_calendar()
    grid = common_grid(calendar, start, end, STRIDE, HORIZON)
    sessions = calendar.sessions_between(start, end)
    seen_date = {d: sessions[sessions.index(d) + SEEN] for d in grid.dates}
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

    tally = {"points": 0, "rows": 0, "terminal": 0, "untraded": 0, "bad_print": 0}
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
            dates = [b.session_date for b in bars]
            position = {d: i for i, d in enumerate(dates)}
            points = [
                (d, position[d], target)
                for d, target in zip(grid.dates, grid.outcome_dates, strict=True)
                if d in position and position[d] + 1 >= MIN_HISTORY
            ]
            if points:
                history = _history(session, security_id)
                high = np.array([float(b.high) for b in bars])
                low = np.array([float(b.low) for b in bars])
                close = np.array([float(b.close) for b in bars])
                volume = np.array([float(b.volume) for b in bars])
                traded = (close > 0) & (volume > 0)
                traded_list = traded.tolist()
                with np.errstate(divide="ignore", invalid="ignore"):
                    rv60 = k.realized_volatility(close, 60)
                    atr_pct = k.atr_percent(high, low, close, 14)
                    turnover = k.average_dollar_volume(high, low, close, volume, 20)
                unsure = np.array([float(b.volume_basis is VolumeBasis.UNDETERMINED) for b in bars])
                unsure_window = k.rolling_max(unsure, 20)
                for day, i, target in points:
                    tally["points"] += 1
                    if not traded[i]:
                        tally["untraded"] += 1
                        continue
                    measured = outcome_or_terminal(dates, traded_list, i, target)
                    if measured is None:
                        tally["untraded"] += 1
                        continue
                    j, terminal = measured.index, measured.terminal
                    if not np.isfinite(atr_pct[i]) or atr_pct[i] > MAX_ATR_PERCENT:
                        tally["bad_print"] += 1
                        continue
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
                    m = position.get(seen_date[day])
                    quarter = (
                        f"{close[m] / close[i] - 1.0:.8f}"
                        if m is not None and m <= j and traded[m]
                        else ""
                    )
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
                            "1" if terminal else "0",
                            quarter,
                            *["" if v is None else f"{v:.8g}" for v in values],
                        )
                    )
                    tally["rows"] += 1
                    tally["terminal"] += int(terminal)
            if n % 250 == 0 or n == len(universe):
                print(
                    f"  {n}/{len(universe)}  {tally}  defined {defined}  [{time.time() - t0:.0f}s]",
                    flush=True,
                )

    print(f"\n{tally}\ndefined {defined}\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
