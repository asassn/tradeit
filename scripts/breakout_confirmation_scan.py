#!/usr/bin/env python
"""Run the Phase 5 breakout engine over real prices, for the first time.

Implements the scan half of ``docs/prereg/BREAKOUT_CONFIRMATION_2026-09-23.md``
(``30b9a60``, committed before the engine saw a real security). §11 recorded
that ``breakout_events``, ``breakout_observations`` and ``breakout_labels`` are
empty and that filling them is "a project, not a study". This is that project,
scoped to the one question asked: **does a bull-flag breakout that the platform
itself calls confirmed go on to continue?**

For each bull flag that has not broken out, the scan freezes its resistance into
a ``BreakoutBoundary`` exactly as `boundary_from_pattern` does, opens a
``BreakoutEvent``, and advances it session by session with the engine's own
policy — no threshold, zone or evidence requirement is touched. It then records
the session on which the event first reached each of three states:

* ``CLOSED_ABOVE``     -- §44's entry, re-measured inside this run;
* ``RETEST_CONFIRMED`` -- the owner's question;
* ``CONFIRMED``        -- any of the profile's paths.

Entry for every arm is the **next** session's open, the stop is the pattern's
invalidation price, and the exit is the stop or the 63rd session — §44's rule,
reused so the two studies are comparable rather than merely adjacent.
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

from pattern_events_scan import MAX_ATR_PERCENT, MIN_DOLLAR_VOLUME, MIN_PRICE, PENDING
from pattern_trades_scan import MAX_STOP_FRACTION, MIN_STOP_FRACTION
from signal_research_pattern import DetectorBar, _universe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.breakouts.boundary import boundary_from_pattern
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.core.enums import Bartimeframe
from tradeit.patterns.scanner import PatternScanner
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

PATTERN = "bull_flag"
HOLD = 63
STRIDE = 5
MIN_HISTORY = 100
LOOKBACK_DAYS = 420
#: Sessions an event is advanced before it is abandoned. The engine expires it
#: on its own clock; this only bounds the work.
MAX_ADVANCE = 60
STATES = ("closed_above", "retest_confirmed", "confirmed")
COLUMNS = (
    "security_id",
    "pattern_key",
    "quality",
    "scan_date",
    *[f"{s}_date" for s in STATES],
    *[f"{s}_entry" for s in STATES],
    "stop",
    "final_state",
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
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end >= dt.date(2020, 1, 1):
        raise SystemExit("2020-2025 is held out and is not read here")
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    scanner = PatternScanner()
    breakouts = BreakoutEngine()
    history = scanner.required_history(Bartimeframe.D1)
    universe = _universe(args.spans, args.start, args.end, 250, 10**9)
    print(
        f"profile {breakouts.profile_name}; window {history} bars, stride {STRIDE}; "
        f"universe {len(universe):,}",
        flush=True,
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    tally = {"flags": 0, "events": 0, "closed_above": 0, "confirmed": 0, "retest_confirmed": 0}
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
            if len(bars) < history + HOLD + 2:
                continue
            dates = [b.session_date for b in bars]
            high = np.array([float(b.high) for b in bars])
            low = np.array([float(b.low) for b in bars])
            close = np.array([float(b.close) for b in bars])
            open_ = np.array([float(b.open) for b in bars])
            volume = np.array([float(b.volume) for b in bars])
            with np.errstate(divide="ignore", invalid="ignore"):
                atr_pct = k.atr_percent(high, low, close, 14)
                atr_abs = k.atr(high, low, close, 14)
                turnover = k.average_dollar_volume(high, low, close, volume, 20)
            ok = (
                (close >= MIN_PRICE)
                & (volume > 0)
                & np.isfinite(turnover)
                & (turnover >= MIN_DOLLAR_VOLUME)
                & np.isfinite(atr_pct)
                & (atr_pct <= MAX_ATR_PERCENT)
            )
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
                for pattern in result.instances:
                    if str(pattern.pattern_type) != PATTERN or pattern.state not in PENDING:
                        continue
                    if pattern.identity_key in seen or pattern.invalidation_price is None:
                        continue
                    resistance = pattern.geometry.resistance
                    if resistance is None or resistance.kind != "resistance":
                        continue
                    tally["flags"] += 1
                    seen.add(pattern.identity_key)
                    boundary = boundary_from_pattern(
                        resistance,
                        atr=float(atr_abs[i]) if np.isfinite(atr_abs[i]) else None,
                        config=breakouts.config.tolerance,
                        pattern_key=pattern.identity_key,
                        pattern_type=str(pattern.pattern_type),
                        pattern_quality=float(pattern.quality),
                    )
                    event = breakouts.open_event(
                        instrument_id=security_id,
                        timeframe=Bartimeframe.D1,
                        boundary=boundary,
                        session=dates[i],
                        pattern_detector_name=PATTERN,
                    )
                    first: dict[str, int] = {}
                    for j in range(i + 1, min(i + 1 + MAX_ADVANCE, last + 1)):
                        event = breakouts.advance(
                            event,
                            SessionInputs(
                                bars=shaped[max(0, j - history + 1) : j + 1],
                                as_of_session=dates[j],
                                knowledge_time=dt.datetime.combine(
                                    dates[j], dt.time(21), tzinfo=dt.UTC
                                ),
                                pattern_length=pattern.session_count or None,
                            ),
                        )
                        state = event.state
                        if state is BreakoutState.CLOSED_ABOVE:
                            first.setdefault("closed_above", j)
                        elif state is BreakoutState.RETEST_CONFIRMED:
                            first.setdefault("closed_above", j)
                            first.setdefault("retest_confirmed", j)
                        elif state is BreakoutState.CONFIRMED:
                            first.setdefault("closed_above", j)
                            first.setdefault("confirmed", j)
                        if state.is_resolved:
                            break
                    if not first:
                        continue
                    tally["events"] += 1
                    for name in STATES:
                        tally[name] += int(name in first)
                    stop = float(pattern.invalidation_price)
                    entries: dict[str, tuple[str, str]] = {}
                    for name, index in first.items():
                        if index + 1 >= len(bars):
                            continue
                        entry = float(open_[index + 1])
                        fraction = (entry - stop) / entry if entry > 0 else 0.0
                        if MIN_STOP_FRACTION <= fraction <= MAX_STOP_FRACTION:
                            entries[name] = (dates[index + 1].isoformat(), f"{entry:.6f}")
                    if not entries:
                        continue
                    writer.writerow(
                        (
                            security_id,
                            pattern.identity_key,
                            f"{pattern.quality:.2f}",
                            dates[i].isoformat(),
                            *[entries.get(s, ("", ""))[0] for s in STATES],
                            *[entries.get(s, ("", ""))[1] for s in STATES],
                            f"{stop:.6f}",
                            str(event.state),
                        )
                    )
            if n % 100 == 0 or n == len(universe):
                print(f"  {n}/{len(universe)}  {tally}  [{time.time() - t0:.0f}s]", flush=True)
    print(f"\n{tally}\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
