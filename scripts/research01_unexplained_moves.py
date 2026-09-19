#!/usr/bin/env python
"""Every session §0.10 flags, for every security, through the supported path.

Writes the table studies exclude on. Computed from ``price_series`` with the
split adjustment applied, so it reports what a study actually reads rather than
what the raw table holds -- the distinction that made two earlier counts of this
defect wrong (21,634, then 18,865; the answer is 10,020-scale).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series, recorded_splits, unexplained_moves
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityPriceFact


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime.now(dt.UTC)
    ids = [
        row[0]
        for row in session.execute(
            select(SecurityPriceFact.security_id).distinct().order_by(SecurityPriceFact.security_id)
        ).all()
    ]
    ids = [s for n, s in enumerate(ids) if n % args.shards == args.shard]
    print(f"shard {args.shard}: {len(ids):,} securities", flush=True)
    t0 = time.time()
    found = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("security_id", "session_date"))
        for n, security_id in enumerate(ids, 1):
            bars = price_series(session, security_id, as_of=as_of)
            if len(bars) < 2:
                continue
            for day in sorted(unexplained_moves(bars, recorded_splits(session, security_id))):
                writer.writerow((security_id, day.isoformat()))
                found += 1
            if n % 500 == 0 or n == len(ids):
                print(f"  {n}/{len(ids)}  {found:,} flagged [{time.time() - t0:.0f}s]", flush=True)
    print(f"shard {args.shard}: {found:,} flagged sessions -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
