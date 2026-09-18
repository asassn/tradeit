#!/usr/bin/env python
"""Which securities were alive on a date and liquid across the year before it.

The population rule every registered backtest from §37 on uses: a security
printing on ``--date`` whose median session dollar volume across ``--year`` was
at least ``--floor`` on at least 100 traded sessions, read through
``price_series`` so each session's dollar volume is the money that traded
(DATA_DICTIONARY §0.9).

That read classifies every split's price and volume basis, which makes it slow
-- about 1.7 seconds a security -- so it shards. Run ``--shards N --shard k`` for
each k, then ``--merge``; the merged file is what a runner reads. Read-only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--date", required=True, help="the security must print on this date")
    ap.add_argument("--year", type=int, required=True, help="the admission year")
    ap.add_argument("--floor", type=float, default=1_000_000.0)
    ap.add_argument("--out", required=True, help="the merged population file")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    parts = [out.with_suffix(f".part{k}of{args.shards}.json") for k in range(args.shards)]
    if args.merge:
        missing = [p for p in parts if not p.exists()]
        if missing:
            print(f"cannot merge: {len(missing)} shard(s) missing")
            return 2
        merged = sorted({sid for p in parts for sid in json.loads(p.read_text())})
        out.write_text(json.dumps(merged))
        print(f"{len(merged):,} securities -> {out}")
        return 0

    with open(args.spans) as handle:
        alive = sorted(
            int(sid) for sid, first, last, _ in csv.reader(handle) if first <= args.date <= last
        )
    mine = [sid for n, sid in enumerate(alive) if n % args.shards == args.shard]
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    start, end = dt.date(args.year, 1, 1), dt.date(args.year, 12, 31)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    keep: list[int] = []
    for n, sid in enumerate(mine, 1):
        bars = price_series(session, sid, as_of=as_of, start=start, end=end)
        traded = [float(b.close * b.volume) for b in bars if b.volume > 0]
        if len(traded) >= 100 and statistics.median(traded) >= args.floor:
            keep.append(sid)
        if n % 100 == 0:
            print(f"  {n:,}/{len(mine):,} read, {len(keep):,} liquid", flush=True)
    parts[args.shard].write_text(json.dumps(keep))
    print(f"shard {args.shard}: {len(keep):,} of {len(mine):,} liquid -> {parts[args.shard]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
