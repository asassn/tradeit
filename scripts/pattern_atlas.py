#!/usr/bin/env python
"""Stage A of the pattern program: conditional probabilities, no claims.

``PATTERN_PROGRAM.md``. For every entry the scan produced, this records what a
trader would have lived — at **five horizons**, tagged by **market regime**, and
beside a **placebo** drawn from the same session. It tests nothing and charges
no trial. Its output is a fact about 2010–2019, and the program's rule is that
an atlas number may never be quoted as evidence.

Three things the earlier pattern work did not do, and the owner was right about
all three:

* **regime**: every prior probability pooled a decade that was 80% uptrend by
  this corpus's own index, so "a bull flag works" and "the market rose" were
  never separated;
* **horizon**: everything was 63 sessions, which is one arbitrary choice out of
  many a trader might hold for;
* **the stop**: reported both ways here, because "did it continue" and "did it
  continue without first taking me out" are different questions and the second
  is the one a stop-using trader lives.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics as st
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_jump_guard import load_jumps
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

HORIZONS = (5, 10, 21, 63, 126)
SEED = 20260924
#: Sessions of index history the regime is judged against.
REGIME_LOOKBACK = 200
ARMS = ("closed_above", "retest_confirmed", "confirmed")
COLUMNS = (
    "arm",
    "leg",
    "trade_id",
    "security_id",
    "entry_date",
    "regime",
    "stop_fraction",
    "stopped_within_63",
    "reached_1r_first",
    *[f"up_{h}" for h in HORIZONS],
    *[f"ret_{h}" for h in HORIZONS],
    *[f"net_{h}" for h in HORIZONS],
)


def regimes(path: str) -> dict[dt.date, str]:
    """Uptrend when the index is at or above its own 200-session average."""
    rows = []
    with open(path) as handle:
        for row in csv.DictReader(handle):
            rows.append((dt.date.fromisoformat(row["session_date"]), float(row["level"])))
    levels = [level for _, level in rows]
    out: dict[dt.date, str] = {}
    for index, (day, level) in enumerate(rows):
        if index < REGIME_LOOKBACK:
            continue
        out[day] = (
            "uptrend" if level >= st.fmean(levels[index - REGIME_LOOKBACK : index]) else "downtrend"
        )
    return out


def outcomes(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start: int,
    entry: float,
    stop: float,
    cost: float = 0.0010,
) -> dict[str, str] | None:
    """What happened after entry, with and without the stop."""
    risk = entry - stop
    if risk <= 0 or start + 1 >= close.shape[0]:
        return None
    out: dict[str, str] = {}
    # First touch, over the longest horizon a stop-using trader would hold.
    available = min(63, close.shape[0] - start - 1)
    first = ""
    stop_at = None
    for j in range(start + 1, start + 1 + available):
        if low[j] <= stop:
            first, stop_at = first or "stop", j
            break
        if high[j] >= entry + risk:
            first = first or "target"
            break
    out["reached_1r_first"] = "1" if first == "target" else "0"
    out["stopped_within_63"] = "1" if first == "stop" else "0"
    for horizon in HORIZONS:
        if start + horizon >= close.shape[0]:
            out[f"up_{horizon}"] = out[f"ret_{horizon}"] = out[f"net_{horizon}"] = ""
            continue
        raw = float(close[start + horizon]) / entry - 1.0
        out[f"up_{horizon}"] = "1" if raw > 0 else "0"
        out[f"ret_{horizon}"] = f"{raw:.6f}"
        # With the stop honoured: if it fired inside this horizon, the trade
        # ended there. Reported separately because a continuation probability
        # that ignores the stop describes a trade nobody took.
        if stop_at is not None and stop_at <= start + horizon:
            net = (stop * (1 - cost)) / (entry * (1 + cost)) - 1.0
        else:
            net = (float(close[start + horizon]) * (1 - cost)) / (entry * (1 + cost)) - 1.0
        out[f"net_{horizon}"] = f"{net:.6f}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--events", required=True)
    ap.add_argument("--eligible", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--jumps", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    regime = regimes(args.index)
    print(
        f"regime: {sum(1 for v in regime.values() if v == 'uptrend'):,} uptrend, "
        f"{sum(1 for v in regime.values() if v == 'downtrend'):,} downtrend sessions",
        flush=True,
    )
    events: list[dict[str, str]] = []
    for path in args.events.split(","):
        with open(path.strip()) as handle:
            events.extend(csv.DictReader(handle))
    pool: dict[str, list[int]] = defaultdict(list)
    for path in args.eligible.split(","):
        with open(path.strip()) as handle:
            for row in csv.DictReader(handle):
                pool[row["session_date"]].append(int(row["security_id"]))

    rng = np.random.default_rng(SEED)
    legs: dict[int, list[tuple[str, str, int, dict[str, str]]]] = defaultdict(list)
    trade_id = 0
    for event in events:
        for arm in ARMS:
            day, entry = event[f"{arm}_date"], event[f"{arm}_entry"]
            # The regime table is keyed by date, and this is a string from a
            # CSV: comparing the two silently skipped every trade the first
            # time, and produced an empty file rather than an error.
            if not day or not entry or dt.date.fromisoformat(day) not in regime:
                continue
            candidates = pool.get(day)
            if not candidates:
                continue
            trade = {"arm": arm, "entry_date": day, "entry": entry, "stop": event["stop"]}
            legs[int(event["security_id"])].append((arm, "rule", trade_id, trade))
            legs[int(candidates[rng.integers(len(candidates))])].append(
                (arm, "placebo", trade_id, trade)
            )
            trade_id += 1
    print(f"{trade_id:,} trades over {len(legs):,} securities", flush=True)

    flagged = load_jumps(args.jumps.split(",")) if args.jumps else {}
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime(2019, 12, 31, 21, tzinfo=dt.UTC)
    rows: dict[int, dict[str, tuple[object, ...]]] = defaultdict(dict)
    t0 = time.time()
    for n, (security_id, wanted) in enumerate(sorted(legs.items()), 1):
        bars = price_series(
            session, security_id, as_of=as_of, start=dt.date(2009, 1, 1), end=dt.date(2019, 12, 31)
        )
        if len(bars) < 2:
            continue
        place = {b.session_date: i for i, b in enumerate(bars)}
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        close = np.array([float(b.close) for b in bars])
        open_ = np.array([float(b.open) for b in bars])
        suspect = set(flagged.get(security_id, ()))
        for arm, leg, tid, trade in wanted:
            day = dt.date.fromisoformat(trade["entry_date"])
            start = place.get(day)
            if start is None or open_[start] <= 0:
                continue
            if {b.session_date for b in bars[start : start + 1 + max(HORIZONS)]} & suspect:
                continue
            entry = float(open_[start])
            fraction = (float(trade["entry"]) - float(trade["stop"])) / float(trade["entry"])
            found = outcomes(high, low, close, start, entry, entry * (1.0 - fraction))
            if found is None:
                continue
            rows[tid][leg] = (
                arm,
                leg,
                tid,
                security_id,
                trade["entry_date"],
                regime[day],
                f"{fraction:.6f}",
                found["stopped_within_63"],
                found["reached_1r_first"],
                *[found[f"up_{h}"] for h in HORIZONS],
                *[found[f"ret_{h}"] for h in HORIZONS],
                *[found[f"net_{h}"] for h in HORIZONS],
            )
        if n % 500 == 0 or n == len(legs):
            print(f"  {n}/{len(legs)} securities [{time.time() - t0:.0f}s]", flush=True)

    written = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for tid in sorted(rows):
            pair = rows[tid]
            if "rule" in pair and "placebo" in pair:
                writer.writerow(pair["rule"])
                writer.writerow(pair["placebo"])
                written += 1
    print(f"\n{written:,} complete pairs -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
