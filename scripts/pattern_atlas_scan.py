#!/usr/bin/env python
"""Stage A's scan: every long pattern, every breakout attempt, one pass.

``PATTERN_PROGRAM.md``. Generalises ``breakout_confirmation_scan.py``, which was
hard-coded to ``bull_flag`` because item 1 was the only question on the table.
Two changes, and nothing else moves -- the window, the stride, the profile, the
eligibility floors, the stop and the entry rule are item 1's, so items 2 onward
are *comparable* to the bull flag rather than merely adjacent to it.

**Every detector in one pass, because the scan already runs them all.**
``PatternScanner.scan`` evaluates all twelve detectors on every window and item
1 threw eleven of the results away at ``!= PATTERN``. Keeping them costs the
breakout engine's advance loop and nothing else, so the marginal price of items
2-11 is a fraction of item 1's. Running them separately would have cost ten
times the compute for the same numbers.

**Repeat attempts, which item 1 could not see.** The owner's objection to §51:
*a failed breakout in a long bull market is not a loss, it is a wait* -- the
structure re-forms and breaks out again, and a trader who takes the second
attempt lived a different experiment from the one §44 and §51 scored.
``PATTERN_PROGRAM.md`` records this as accepted design fault 3. Item 1's scan
carried a ``seen`` set that dropped a pattern identity forever after its first
event; this allows up to ``--attempts`` events per identity, each required to
open only *after* the previous one resolved, and stamps every row with its
attempt number so first attempts and later ones are reported separately and
never pooled.

**And the short side, through the mirror.** ``--direction short`` runs exactly
this scan over the reciprocal series, where a bear flag is a bull flag and a
breakdown through support is a breakout through resistance
(``tradeit.patterns.mirror``). Nothing in the detectors or the engine changes,
which is the point: the bearish structures are measured by the same audited
machinery rather than by a second copy of it. **Prices come back out in real
terms** -- entry, stop and the stop fraction are unmirrored before they are
written, so the replay never sees a reciprocal and can never mistake a mirrored
long's return for a short's.
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
from tradeit.patterns.mirror import mirror_bars, unmirror_price
from tradeit.patterns.scanner import PatternScanner
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

#: Item 1's constants, reused verbatim so the entries are comparable.
HOLD = 63
STRIDE = 5
LOOKBACK_DAYS = 420
MAX_ADVANCE = 60
STATES = ("closed_above", "retest_confirmed", "confirmed")
COLUMNS = (
    "security_id",
    "direction",
    "pattern",
    "pattern_key",
    "attempt",
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
    ap.add_argument("--patterns", default="", help="comma-separated; empty means every detector")
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31", help="2020+ is held out")
    ap.add_argument("--attempts", type=int, default=3, help="events allowed per pattern identity")
    ap.add_argument("--direction", choices=("long", "short"), default="long")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end >= dt.date(2020, 1, 1):
        raise SystemExit("2020-2025 is held out and is not read here")
    wanted = {p.strip() for p in args.patterns.split(",") if p.strip()}
    short = args.direction == "short"
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
        f"patterns {sorted(wanted) or 'ALL'}; attempts {args.attempts}; "
        f"direction {args.direction}; "
        f"universe {len(universe):,}",
        flush=True,
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    tally: dict[str, int] = {"flags": 0, "events": 0, "repeat_events": 0}
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
            close = np.array([float(b.close) for b in bars])
            low = np.array([float(b.low) for b in bars])
            open_ = np.array([float(b.open) for b in bars])
            volume = np.array([float(b.volume) for b in bars])
            with np.errstate(divide="ignore", invalid="ignore"):
                # Eligibility is judged on REAL prices always: a $5 floor or a
                # $1m turnover floor means nothing applied to a reciprocal,
                # where a $5 security prices at 0.2.
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
            raw = [
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
            # The mirror is undefined at zero, and this corpus records zero and
            # placeholder prints (§0.7, §0.8). A security carrying one is
            # dropped from the short side outright rather than clamped, because
            # a clamped price is a fabricated one.
            if short and not (
                np.all(open_ > 0) and np.all(high > 0) and np.all(low > 0) and np.all(close > 0)
            ):
                tally["dropped_nonpositive"] = tally.get("dropped_nonpositive", 0) + 1
                continue
            # The boundary's tolerance is quoted in the units the detector saw,
            # so the ATR handed to it must be mirrored when the series is.
            shaped = mirror_bars(raw) if short else raw
            with np.errstate(divide="ignore", invalid="ignore"):
                atr_abs = k.atr(
                    *((1.0 / low, 1.0 / high, 1.0 / close) if short else (high, low, close)),
                    14,
                )
            last = len(bars) - HOLD - 2
            # identity -> (events opened so far, index the last one resolved on).
            history_of: dict[str, tuple[int, int]] = {}
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
                    name = str(pattern.pattern_type)
                    if (wanted and name not in wanted) or pattern.state not in PENDING:
                        continue
                    if pattern.invalidation_price is None:
                        continue
                    taken, freed = history_of.get(pattern.identity_key, (0, -1))
                    # A repeat attempt is a *later* signal from the same
                    # structure, so it may not open until the previous event
                    # has resolved. Without this the same breakout would be
                    # counted once per stride.
                    if taken >= args.attempts or i <= freed:
                        continue
                    resistance = pattern.geometry.resistance
                    if resistance is None or resistance.kind != "resistance":
                        continue
                    tally["flags"] += 1
                    boundary = boundary_from_pattern(
                        resistance,
                        atr=float(atr_abs[i]) if np.isfinite(atr_abs[i]) else None,
                        config=breakouts.config.tolerance,
                        pattern_key=pattern.identity_key,
                        pattern_type=name,
                        pattern_quality=float(pattern.quality),
                    )
                    event = breakouts.open_event(
                        instrument_id=security_id,
                        timeframe=Bartimeframe.D1,
                        boundary=boundary,
                        session=dates[i],
                        pattern_detector_name=name,
                    )
                    first: dict[str, int] = {}
                    j = i
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
                    history_of[pattern.identity_key] = (taken + 1, j)
                    if not first:
                        continue
                    tally["events"] += 1
                    tally["repeat_events"] += int(taken > 0)
                    tally[name] = tally.get(name, 0) + 1
                    # Out of the mirror before anything is written down.
                    stop = float(pattern.invalidation_price)
                    if short:
                        stop = float(unmirror_price(stop))
                    entries: dict[str, tuple[str, str]] = {}
                    for label, index in first.items():
                        if index + 1 >= len(bars):
                            continue
                        entry = float(open_[index + 1])
                        if entry <= 0:
                            continue
                        # A short's stop sits ABOVE its entry. Same distance,
                        # opposite sign, so the ladder's floors still bind.
                        fraction = (stop - entry) / entry if short else (entry - stop) / entry
                        if MIN_STOP_FRACTION <= fraction <= MAX_STOP_FRACTION:
                            entries[label] = (dates[index + 1].isoformat(), f"{entry:.6f}")
                    if not entries:
                        continue
                    writer.writerow(
                        (
                            security_id,
                            args.direction,
                            name,
                            pattern.identity_key,
                            taken + 1,
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
