#!/usr/bin/env python
"""Price every registered trade and the placebo drawn against it.

The placebo is the whole argument. The design window said a long-only breakout
rule earns +0.49R a trade in 2003 and loses 0.53R in 2008, so a rule measured
against zero would be reporting the decade it ran in. For each trade this draws
one security at random from the SAME session's eligible universe, enters at its
open, and gives it the SAME stop distance and the SAME 63-session exit. What
survives the subtraction is the pattern.

Both legs are priced identically -- same costs, same dead-name recovery, same
§0.10 exclusion -- and a pair is dropped whole if either leg is unusable, so
the difference is never taken between two different populations.

Implements ``docs/prereg/PATTERN_BREAKOUT_TRADES_2026-09-19.md`` at ``d94a971``.
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

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

HOLD = 63
SEED = 20260919
#: Registered: 10 bps a side, and the double-cost variant criterion 5 needs.
COSTS = (0.0010, 0.0020)
RECOVERIES = (1.0, 0.0)
COLUMNS = (
    "leg",
    "pair_id",
    "security_id",
    "pattern",
    "quality",
    "entry_date",
    "stop_fraction",
    "exit_reason",
    "bars_held",
    *[f"r_c{int(c * 10000)}_rec{int(r)}" for c in COSTS for r in RECOVERIES],
)


def _walk(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start: int,
    entry: float,
    stop_fraction: float,
    cost: float,
    recovery: float,
) -> tuple[float, str, int] | None:
    stop = entry * (1.0 - stop_fraction)
    risk = entry - stop
    available = min(HOLD, close.shape[0] - start - 1)
    if risk <= 0:
        return None
    paid = entry * (1.0 + cost)
    if available <= 0:
        # The series ends at entry: the security never traded again.
        return ((entry * recovery * (1.0 - cost)) - paid) / risk, "terminal", 0
    for j in range(start + 1, start + 1 + available):
        if low[j] <= stop:
            return ((stop * (1.0 - cost)) - paid) / risk, "stop", j - start
    last = start + available
    exit_price = float(close[last])
    if available < HOLD:
        # Dead before the horizon: the last traded close, at the recovery.
        return ((exit_price * recovery * (1.0 - cost)) - paid) / risk, "terminal", available
    return ((exit_price * (1.0 - cost)) - paid) / risk, "horizon", available


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--trades", required=True)
    ap.add_argument("--eligible", required=True)
    ap.add_argument("--jumps", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    trades: list[dict[str, str]] = []
    for path in args.trades.split(","):
        with open(path.strip()) as handle:
            trades.extend(csv.DictReader(handle))
    trades.sort(key=lambda r: (r["entry_date"], int(r["security_id"])))
    print(f"{len(trades):,} registered trades", flush=True)

    pool: dict[str, list[int]] = defaultdict(list)
    for path in args.eligible.split(","):
        with open(path.strip()) as handle:
            for row in csv.DictReader(handle):
                pool[row["session_date"]].append(int(row["security_id"]))
    print(
        f"eligibility: {sum(len(v) for v in pool.values()):,} pairs on {len(pool):,} sessions",
        flush=True,
    )

    # Drawn once, before any outcome is priced, as the registration requires.
    rng = np.random.default_rng(SEED)
    legs: dict[int, list[tuple[str, int, dict[str, str]]]] = defaultdict(list)
    placebo_of: dict[int, int] = {}
    for pair_id, trade in enumerate(trades):
        candidates = pool.get(trade["entry_date"])
        if not candidates:
            continue
        drawn = int(candidates[rng.integers(len(candidates))])
        legs[int(trade["security_id"])].append(("rule", pair_id, trade))
        legs[drawn].append(("placebo", pair_id, trade))
        placebo_of[pair_id] = drawn
    print(f"{len(placebo_of):,} pairs drawn over {len(legs):,} securities", flush=True)

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
        for leg, pair_id, trade in wanted:
            start = place.get(dt.date.fromisoformat(trade["entry_date"]))
            if start is None or open_[start] <= 0:
                continue
            window = {b.session_date for b in bars[start : start + 1 + HOLD]}
            if window & suspect:
                continue
            fraction = float(trade["stop_fraction"])
            results: list[str] = []
            reason, held = "", 0
            for cost in COSTS:
                for recovery in RECOVERIES:
                    walked = _walk(
                        high, low, close, start, float(open_[start]), fraction, cost, recovery
                    )
                    if walked is None:
                        results = []
                        break
                    value, reason, held = walked
                    results.append(f"{value:.6f}")
            if len(results) != len(COSTS) * len(RECOVERIES):
                continue
            rows[pair_id][leg] = (
                leg,
                pair_id,
                security_id,
                trade["pattern"] if leg == "rule" else "",
                trade["quality"] if leg == "rule" else "",
                trade["entry_date"],
                f"{fraction:.6f}",
                reason,
                held,
                *results,
            )
        if n % 500 == 0 or n == len(legs):
            print(f"  {n}/{len(legs)} securities [{time.time() - t0:.0f}s]", flush=True)

    written = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for pair_id in sorted(rows):
            pair = rows[pair_id]
            # A pair is kept whole or not at all: a difference between a trade
            # and a missing placebo is not a difference.
            if "rule" in pair and "placebo" in pair:
                writer.writerow(pair["rule"])
                writer.writerow(pair["placebo"])
                written += 1
    print(f"\n{written:,} complete pairs -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
