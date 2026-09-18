#!/usr/bin/env python
"""Write a dated ``security_spans`` file from the corpus as it stands today.

Every signal study reads its universe from a spans file -- one row per security:
first raw session, last raw session, count of raw sessions. The file every study
until now used, ``security_spans.csv``, was produced by hand on 2026-09-11 and no
script in the repository could rebuild it. That is a reproducibility gap, and
this closes it for every file written from here on.

**Why a new dated file rather than a rebuilt one.** The 2026-09-11 file predates
the Sharadar backfill that priced 2,877 more dead companies. §29 used the old
file deliberately, to isolate the split corrections from a change of universe,
and §26, §27, §30 and §31 all rest on it. Overwriting it would silently change
the universe under every one of them. A new study should use the corpus as it
now is -- anything less is more survivor-biased than the data allows -- so it
gets a new file, named by the date it was built, and the old one is untouched.

**Read-only**, by URI, so it can run while another tool holds the file.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
from pathlib import Path

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="research01.sqlite")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args()

    target = OUT / f"security_spans_{args.date}.csv"
    if target.exists():
        print(f"{target} already exists; refusing to overwrite a universe file")
        return 2

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.execute("PRAGMA busy_timeout = 60000")
    rows = connection.execute(
        """
        SELECT security_id, MIN(session_date), MAX(session_date), COUNT(DISTINCT session_date)
        FROM security_price_facts
        WHERE adjustment_basis = 'raw'
        GROUP BY security_id
        ORDER BY security_id
        """
    ).fetchall()
    with target.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    print(f"{len(rows):,} securities -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
