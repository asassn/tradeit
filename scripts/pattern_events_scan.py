#!/usr/bin/env python
"""Pattern breakouts as EVENTS, with the excursion a trader would have lived.

**Why this exists.** §13 and §31 measured the twelve detectors as a continuous
*quality score* ranked across the market, and found nothing. That design cannot
see what a trader means by a pattern: an entry above a boundary, a stop below
the structure, and an asymmetric payoff. A rule that wins two trades in five at
three times its risk is profitable and reads as **zero** in a rank correlation.
This measures the thing the earlier design was blind to.

**This is the DESIGN window, 2003-2009.** Its purpose is to be looked at. Rules
are chosen here, with the looking on the record, and then tested on 2010-2019
and confirmed on 2020-2025, neither of which this script may touch. That split
is the honest alternative to inventing a rule and calling it a prior.

**Nothing here is a trial and nothing here is evidence.** It is descriptive:
base rates, and where a stop could sit without being hit first.

The chain, point-in-time at every step:

1. Scan every ``stride`` sessions on a trailing window that ends at the scan
   session. Keep patterns that are MATURE or NEAR_BREAKOUT -- structures that
   have *not yet* broken out -- and that carry a resistance boundary.
2. **Freeze that boundary.** The trigger is the first session within
   ``--trigger-window`` whose close exceeds the boundary as it was known at the
   scan. A boundary redrawn on later bars would be hindsight.
3. Enter at the NEXT session's open, never the triggering close.
4. Stop at the pattern's own invalidation price where it has one, else its
   support boundary, else a volatility stop -- and record which, because the
   three are not the same claim.
5. Walk forward and record, in units of risk R = entry - stop: how far price
   ran in favour (MFE), how far against (MAE), and which came first.

One event per pattern identity: a structure that stays broken out across
several scans is one trade, not five.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_research_pattern import ACTIONABLE, DetectorBar, _universe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.core.enums import Bartimeframe
from tradeit.patterns.base import PatternState
from tradeit.patterns.scanner import PatternScanner
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

#: Structures that have not broken out yet. BROKEN_OUT_UNCONFIRMED is excluded
#: deliberately: its breakout already happened, so the trigger below would be
#: reading an event the scan should have caught earlier, at an entry price the
#: scan cannot know it could have had.
PENDING = tuple(s for s in ACTIONABLE if s is not PatternState.BROKEN_OUT_UNCONFIRMED)
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 1_000_000.0
MAX_ATR_PERCENT = 1.0
#: Sessions after the scan in which the boundary may be crossed. Beyond this the
#: structure is stale and the frozen boundary is no longer what a trader sees.
TRIGGER_WINDOW = 10
#: Sessions of forward path recorded after entry.
FORWARD = 63
#: Fallback stop when the pattern offers no level: 1.5 x ATR(14) below entry.
ATR_STOP_MULTIPLE = 1.5
COLUMNS = (
    "security_id",
    "pattern",
    "identity_key",
    "quality",
    "scan_date",
    "trigger_date",
    "entry_date",
    "entry",
    "stop",
    "stop_source",
    "risk_fraction",
    "atr_percent",
    "avg_dollar_volume_20",
    "sessions_to_trigger",
    "mfe_r",
    "mae_r",
    "first_touch",
    "bars_to_first_touch",
    "r_at_5",
    "r_at_10",
    "r_at_21",
    "r_at_63",
    "ret_5",
    "ret_10",
    "ret_21",
    "ret_63",
    "bars_available",
)


@dataclass(slots=True)
class Event:
    row: tuple[object, ...]


def level_on(boundary: object, index: int, anchor_index: int | None) -> float | None:
    """A boundary's price on the bar at ``index``, in TRADING sessions.

    ``Boundary.price_at`` refuses a sloped boundary without a session count,
    because calendar-day arithmetic drifts across weekends -- so the count is
    computed here from the security's own bar list, which is what knows it. A
    boundary whose anchor is not in that list cannot be projected honestly and
    returns None rather than a guess.
    """
    if boundary.slope_per_session == 0.0:  # type: ignore[attr-defined]
        return float(boundary.level)  # type: ignore[attr-defined]
    if anchor_index is None:
        return None
    return float(boundary.price_at(dt.date.min, index - anchor_index))  # type: ignore[attr-defined]


def excursion(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_index: int,
    entry: float,
    stop: float,
) -> dict[str, object]:
    """The path after entry, in units of risk.

    ``first_touch`` answers what a trader lives: did the stop go first, or did
    price reach +1R first? When a single session's range covers both, the STOP
    is taken first. Daily bars cannot say which came first inside the session,
    and the pessimistic reading is the only one that cannot flatter the rule.
    """
    risk = entry - stop
    n = min(FORWARD, high.shape[0] - entry_index - 1)
    if risk <= 0 or n <= 0:
        return {}
    window = slice(entry_index + 1, entry_index + 1 + n)
    highs, lows, closes = high[window], low[window], close[window]
    mfe = float((highs.max() - entry) / risk)
    mae = float((entry - lows.min()) / risk)
    first_touch, bars_to = "none", ""
    for j in range(n):
        if lows[j] <= stop:
            first_touch, bars_to = "stop", j + 1
            break
        if highs[j] >= entry + risk:
            first_touch, bars_to = "target_1r", j + 1
            break
    out: dict[str, object] = {
        "mfe_r": f"{mfe:.4f}",
        "mae_r": f"{mae:.4f}",
        "first_touch": first_touch,
        "bars_to_first_touch": bars_to,
        "bars_available": n,
    }
    for h in (5, 10, 21, 63):
        if h <= n:
            out[f"r_at_{h}"] = f"{(closes[h - 1] - entry) / risk:.4f}"
            out[f"ret_{h}"] = f"{closes[h - 1] / entry - 1.0:.6f}"
        else:
            out[f"r_at_{h}"] = ""
            out[f"ret_{h}"] = ""
    return out


def events_for(
    scanner: PatternScanner,
    session: Session,
    security_id: int,
    start: dt.date,
    end: dt.date,
    as_of: dt.datetime,
    history: int,
    stride: int,
) -> tuple[list[Event], int]:
    bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
    if len(bars) < history + FORWARD + 2:
        return [], 0
    dates = [b.session_date for b in bars]
    position = {d: n for n, d in enumerate(dates)}
    high = np.array([float(b.high) for b in bars])
    low = np.array([float(b.low) for b in bars])
    close = np.array([float(b.close) for b in bars])
    open_ = np.array([float(b.open) for b in bars])
    volume = np.array([float(b.volume) for b in bars])
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = k.atr_percent(high, low, close, 14)
        turnover = k.average_dollar_volume(high, low, close, volume, 20)
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
    out: list[Event] = []
    seen: set[str] = set()
    scans = 0
    last = len(bars) - FORWARD - 2
    for i in range(history - 1, last, stride):
        if (
            close[i] <= 0
            or volume[i] <= 0
            or close[i] < MIN_PRICE
            or not np.isfinite(turnover[i])
            or turnover[i] < MIN_DOLLAR_VOLUME
            or not np.isfinite(atr_pct[i])
            or atr_pct[i] > MAX_ATR_PERCENT
        ):
            continue
        result = scanner.scan(
            security_id, Bartimeframe.D1, shaped[i - history + 1 : i + 1], dates[i], track=False
        )
        scans += 1
        for pattern in result.instances:
            if pattern.state not in PENDING or pattern.identity_key in seen:
                continue
            boundary = pattern.geometry.resistance
            if boundary is None:
                continue
            # The frozen boundary: crossed on a later session, priced as the
            # scan knew it, never redrawn on bars the scan could not see.
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
            if entry <= 0:
                continue
            support = pattern.geometry.support
            if pattern.invalidation_price is not None and 0 < pattern.invalidation_price < entry:
                stop, source = float(pattern.invalidation_price), "invalidation"
            elif (
                support is not None
                and (level := level_on(support, entry_index, position.get(support.anchor_date)))
                is not None
                and 0 < level < entry
            ):
                stop, source = level, "support"
            elif np.isfinite(atr_pct[trigger]):
                stop, source = entry * (1.0 - ATR_STOP_MULTIPLE * float(atr_pct[trigger])), "atr"
            else:
                continue
            path = excursion(high, low, close, entry_index, entry, stop)
            if not path:
                continue
            seen.add(pattern.identity_key)
            out.append(
                Event(
                    (
                        security_id,
                        str(pattern.pattern_type),
                        pattern.identity_key,
                        f"{pattern.quality:.2f}",
                        dates[i].isoformat(),
                        dates[trigger].isoformat(),
                        dates[entry_index].isoformat(),
                        f"{entry:.6f}",
                        f"{stop:.6f}",
                        source,
                        f"{(entry - stop) / entry:.6f}",
                        f"{atr_pct[i]:.6f}",
                        f"{turnover[i]:.2f}",
                        trigger - i,
                        path["mfe_r"],
                        path["mae_r"],
                        path["first_touch"],
                        path["bars_to_first_touch"],
                        path["r_at_5"],
                        path["r_at_10"],
                        path["r_at_21"],
                        path["r_at_63"],
                        path["ret_5"],
                        path["ret_10"],
                        path["ret_21"],
                        path["ret_63"],
                        path["bars_available"],
                    )
                )
            )
    return out, scans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2003-01-02", help="DESIGN window only")
    ap.add_argument("--end", default="2009-12-31", help="2010+ is not this script's to read")
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end >= dt.date(2010, 1, 1):
        raise SystemExit("this is the design window: 2010 onward is held for the registered test")
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    scanner = PatternScanner()
    history = scanner.required_history(Bartimeframe.D1)
    universe = _universe(args.spans, args.start, args.end, args.min_bars, 10**9)
    print(
        f"{len([n for n, e in scanner.registry.entries.items() if e.enabled])} detectors, "
        f"window {history} bars, stride {args.stride}; universe {len(universe):,}",
        flush=True,
    )
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
    if args.limit:
        universe = universe[: args.limit]

    t0 = time.time()
    total = scans = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for n, security_id in enumerate(universe, 1):
            got, count = events_for(
                scanner, session, security_id, start, end, as_of, history, args.stride
            )
            scans += count
            for event in got:
                writer.writerow(event.row)
            total += len(got)
            if n % 50 == 0 or n == len(universe):
                print(
                    f"  {n}/{len(universe)}  {total:,} events from {scans:,} scans "
                    f"[{time.time() - t0:.0f}s]",
                    flush=True,
                )
    print(f"\n{total:,} events, {scans:,} scans\n  -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
