#!/usr/bin/env python
"""Merge the index shards and compound them into a level. Reproducibly.

``market_index_build.py`` writes, per shard, the *sum* of eligible one-session
returns and the *count* of securities contributing. Turning eight of those into
the single levelled series every regime split in ``PATTERN_PROGRAM.md`` reads
was done once in a throwaway one-liner, which meant the series underpinning
every regime number in the program could not be rebuilt from the repository.
This is that step, written down.

**Sum and count, not eight means.** Each shard holds a disjoint slice of the
universe, so the session's equal-weighted mean return is the total of the
shards' sums over the total of their counts. Averaging the shards' own means
would weight a shard holding 40 securities the same as one holding 400.

**A session with too few securities is dropped, not carried.** The corpus's
earliest sessions clear the liquidity floors for a handful of names, and a mean
of four returns is not a market. The floor is recorded rather than tuned: it
was set once, before the regime split was looked at.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from collections import defaultdict

#: Securities a session needs before its mean counts as a market reading.
MIN_SECURITIES = 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True, help="comma-separated shard files")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    total: dict[dt.date, float] = defaultdict(float)
    count: dict[dt.date, int] = defaultdict(int)
    for path in args.shards.split(","):
        with open(path.strip()) as handle:
            for row in csv.DictReader(handle):
                day = dt.date.fromisoformat(row["session_date"])
                total[day] += float(row["return_sum"])
                count[day] += int(row["securities"])

    thin = sum(1 for day in total if count[day] < MIN_SECURITIES)
    level = 1.0
    rows: list[tuple[str, str, int]] = []
    for day in sorted(total):
        if count[day] < MIN_SECURITIES:
            continue
        level *= 1.0 + total[day] / count[day]
        rows.append((day.isoformat(), f"{level:.8f}", count[day]))

    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("session_date", "level", "securities"))
        writer.writerows(rows)
    print(
        f"{len(rows):,} sessions ({thin} dropped below {MIN_SECURITIES} securities), "
        f"{rows[0][0]} .. {rows[-1][0]}, final level {rows[-1][1]}\n  -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
