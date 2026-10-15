#!/usr/bin/env python
"""Every unexplained price step and its magnitude, so a threshold can be priced.

``unexplained_moves`` takes a ``factor`` and flags a session when the close
moves beyond it with no recorded action. The factor has always been **5.0**,
chosen in §0.10 against the defects then visible -- YELL's 7,653x and moves of
1e5 and beyond. The atlas of 2026-09-25 then found ten securities stepping
**4.0 to 4.4x**, under the threshold and quite large enough to destroy a mean,
and two more turned up inside the held-out window (§52 criterion 4).

Lowering the factor is a detector threshold and therefore the owner's decision.
This script exists so that decision is made against a measurement rather than
an anecdote: it records **every** unexplained step at or beyond
``--floor``, with its exact ratio, so the cost of any candidate threshold can
be counted afterwards instead of guessed.

**The discriminator worth looking at is roundness.** An unrecorded split moves
price by a *ratio* -- 2, 3, 4, 1/10 -- because that is what a split is. A
buyout pop or a biotech readout lands wherever the market puts it. So the
distance from each step to the nearest simple split ratio separates the two
populations without anybody having to inspect securities one at a time, and it
is reported per step rather than thresholded here.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from fractions import Fraction

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series, recorded_splits
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityPriceFact

#: Ratios a split actually takes. **Listed, not generated.** The first version
#: generated every a/b for a <= 20 over seven denominators, which made 4.25x,
#: 4.33x and 4.67x count as "split ratios" -- a 17-for-4 split is not a thing,
#: and the error inflated the share of steps that looked like splits from 33%
#: to 66% in exactly the band being argued about. An instrument wide enough to
#: confirm whatever it is pointed at is not evidence.
#:
#: Forward splits, the common stock-dividend ratios, and every reciprocal.
_FORWARD = (
    Fraction(2),
    Fraction(3),
    Fraction(4),
    Fraction(5),
    Fraction(6),
    Fraction(8),
    Fraction(10),
    Fraction(15),
    Fraction(20),
    Fraction(25),
    Fraction(50),
    Fraction(100),
    Fraction(3, 2),
    Fraction(5, 4),
    Fraction(7, 4),
    Fraction(5, 2),
    Fraction(7, 2),
    Fraction(21, 20),
    Fraction(11, 10),
    Fraction(6, 5),
    Fraction(4, 3),
)
SPLIT_RATIOS = sorted({float(r) for r in _FORWARD} | {float(1 / r) for r in _FORWARD})


def roundness(ratio: float) -> tuple[float, float]:
    """Nearest plausible split ratio, and the relative distance to it."""
    nearest = min(SPLIT_RATIOS, key=lambda r: abs(r - ratio))
    return nearest, abs(ratio - nearest) / nearest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--floor", type=float, default=2.0, help="report steps beyond this factor")
    ap.add_argument("--start", default="1990-01-01")
    ap.add_argument("--end", default="2026-09-14")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    ids = [
        row[0]
        for row in session.execute(
            select(SecurityPriceFact.security_id).distinct().order_by(SecurityPriceFact.security_id)
        ).all()
    ]
    ids = [s for n, s in enumerate(ids) if n % args.shards == args.shard]
    print(f"shard {args.shard}: {len(ids):,} securities, floor {args.floor}", flush=True)

    t0 = time.time()
    written = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "security_id",
                "session_date",
                "ratio",
                "previous_close",
                "close",
                "volume_ratio",
                "nearest_split_ratio",
                "distance_to_split",
            )
        )
        for n, security_id in enumerate(ids, 1):
            bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
            if len(bars) < 2:
                continue
            near_split = {s.ex_date for s in recorded_splits(session, security_id)}
            previous: tuple[float, float] | None = None
            for index, bar in enumerate(bars):
                close, volume = float(bar.close), float(bar.volume)
                if close > 0 and previous is not None and previous[0] > 0:
                    ratio = close / previous[0]
                    explained = bar.session_date in near_split or (
                        index > 0 and bars[index - 1].session_date in near_split
                    )
                    if not explained and (ratio >= args.floor or ratio <= 1.0 / args.floor):
                        nearest, distance = roundness(ratio)
                        writer.writerow(
                            (
                                security_id,
                                bar.session_date.isoformat(),
                                f"{ratio:.6f}",
                                f"{previous[0]:.4f}",
                                f"{close:.4f}",
                                f"{volume / previous[1]:.4f}" if previous[1] > 0 else "",
                                f"{nearest:.6f}",
                                f"{distance:.6f}",
                            )
                        )
                        written += 1
                if close > 0:
                    previous = (close, volume)
            if n % 500 == 0 or n == len(ids):
                print(f"  {n}/{len(ids)}  {written:,} steps  [{time.time() - t0:.0f}s]", flush=True)
    print(f"\n{written:,} steps -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
