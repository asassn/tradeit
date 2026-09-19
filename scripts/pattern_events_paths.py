#!/usr/bin/env python
"""Replay each design-window event against several exit rules.

The scan already decided entry and stop. Detection was the expensive part and
does not change, so this re-walks prices only -- which makes the exit rule a
cheap question instead of a three-hour one.

Rules replayed, all long-only, all with the pattern's own stop:

* **target R**: exit at +1R, +2R or +3R, whichever the rule names, else at the
  63rd session's close. A session touching both stop and target counts as
  STOPPED, for the reason the scan gives: daily bars cannot order them.
* **trail**: after the trade is +1R ahead, a stop that follows the highest
  close since entry by ``TRAIL_ATR`` x ATR(14) and never falls back.
* **hold**: no target at all -- stop, or the close at 21 or 63 sessions.

Nothing here is a trial. It is the design window being read, which is what it
is for. The rule chosen from it is then registered and tested on 2010-2019.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_jump_guard import load_jumps
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.research01.series import price_series, recorded_splits, unexplained_moves
from tradeit.storage.session import install_sqlite_busy_timeout

HORIZON = 63
TRAIL_ATR = 2.0
TARGETS = (1.0, 2.0, 3.0)
COLUMNS = (
    "security_id",
    "pattern",
    "quality",
    "entry_date",
    "risk_fraction",
    *[f"r_target_{int(t)}" for t in TARGETS],
    *[f"bars_target_{int(t)}" for t in TARGETS],
    "r_trail",
    "bars_trail",
    "r_hold_21",
    "r_hold_63",
    "mfe_r",
)


def replay(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    start: int,
    entry: float,
    stop: float,
) -> dict[str, object] | None:
    """One event under every rule. ``start`` is the entry bar's index."""
    risk = entry - stop
    n = min(HORIZON, high.shape[0] - start - 1)
    if risk <= 0 or n <= 0:
        return None
    out: dict[str, object] = {}
    highs, lows, closes = (a[start + 1 : start + 1 + n] for a in (high, low, close))

    for target in TARGETS:
        level = entry + target * risk
        result, bars = (closes[-1] - entry) / risk, n
        for j in range(n):
            if lows[j] <= stop:
                result, bars = -1.0, j + 1
                break
            if highs[j] >= level:
                result, bars = target, j + 1
                break
        out[f"r_target_{int(target)}"] = f"{result:.4f}"
        out[f"bars_target_{int(target)}"] = bars

    # Trailing: the stop only rises, and only once the trade is 1R ahead.
    trail, peak, result, bars = stop, entry, (closes[-1] - entry) / risk, n
    for j in range(n):
        if lows[j] <= trail:
            result, bars = (trail - entry) / risk, j + 1
            break
        peak = max(peak, closes[j])
        if peak >= entry + risk and np.isfinite(atr[start + 1 + j]):
            trail = max(trail, peak - TRAIL_ATR * float(atr[start + 1 + j]))
    out["r_trail"], out["bars_trail"] = f"{result:.4f}", bars

    for horizon in (21, 63):
        if horizon > n:
            out[f"r_hold_{horizon}"] = ""
            continue
        result = (closes[horizon - 1] - entry) / risk
        for j in range(horizon):
            if lows[j] <= stop:
                result = -1.0
                break
        out[f"r_hold_{horizon}"] = f"{result:.4f}"
    out["mfe_r"] = f"{(highs.max() - entry) / risk:.4f}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--events", required=True)
    ap.add_argument("--jumps", default="")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    by_security: dict[int, list[dict[str, str]]] = defaultdict(list)
    for path in args.events.split(","):
        with open(path.strip()) as handle:
            for row in csv.DictReader(handle):
                by_security[int(row["security_id"])].append(row)
    ids = sorted(by_security)
    ids = [s for n, s in enumerate(ids) if n % args.shards == args.shard]
    flagged = load_jumps(args.jumps.split(",")) if args.jumps else {}

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime(2009, 12, 31, 21, tzinfo=dt.UTC)
    t0 = time.time()
    written = dropped = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for n, security_id in enumerate(ids, 1):
            bars = price_series(
                session,
                security_id,
                as_of=as_of,
                start=dt.date(2002, 1, 1),
                end=dt.date(2009, 12, 31),
            )
            if len(bars) < 2:
                continue
            place = {b.session_date: i for i, b in enumerate(bars)}
            high = np.array([float(b.high) for b in bars])
            low = np.array([float(b.low) for b in bars])
            close = np.array([float(b.close) for b in bars])
            with np.errstate(divide="ignore", invalid="ignore"):
                atr = k.atr(high, low, close, 14)
            suspect = set(flagged.get(security_id, ())) or unexplained_moves(
                bars, recorded_splits(session, security_id)
            )
            for row in by_security[security_id]:
                entry_date = dt.date.fromisoformat(row["entry_date"])
                start = place.get(entry_date)
                if start is None:
                    continue
                window = {b.session_date for b in bars[start + 1 : start + 1 + HORIZON]}
                if window & suspect:
                    dropped += 1
                    continue
                replayed = replay(
                    high, low, close, atr, start, float(row["entry"]), float(row["stop"])
                )
                if replayed is None:
                    continue
                writer.writerow(
                    (
                        security_id,
                        row["pattern"],
                        row["quality"],
                        row["entry_date"],
                        row["risk_fraction"],
                        *[replayed[c] for c in COLUMNS[5:]],
                    )
                )
                written += 1
            if n % 250 == 0 or n == len(ids):
                print(
                    f"  {n}/{len(ids)}  {written:,} replayed, {dropped:,} dropped by §0.10 "
                    f"[{time.time() - t0:.0f}s]",
                    flush=True,
                )
    print(f"shard {args.shard}: {written:,} events -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
