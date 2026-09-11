#!/usr/bin/env python
"""Drop scan points whose endpoints were never traded.

The full-universe pattern run produced a pooled mean forward return of
**+8,511,217%**, which is not a finding but a defect. Traced to securities like
4565, whose bars run::

    2005-11-09  o/h/l/c  0.0001   volume 0
    2005-11-10  o/h/l/c  92000    volume 0
    2005-11-11  o/h/l/c  0.0001   volume 0

Every bar volume 0, every bar ``o=h=l=c``, the price alternating between a
``0.0001`` sentinel and five-figure nonsense. It is the **same failure the
corpus already documents for zero-price bars** -- the vendor keeps emitting rows
after a security stops trading -- one notch along: ``price_series`` refuses
``close <= 0`` and serves ``0.0001`` happily.

Measured corpus-wide before writing this: **2,245,866 raw bars (6.339%) across
7,582 securities carry volume 0**, and 2,059,986 of those are also ``o=h=l=c``.

The rule
--------

A scan point survives only if **both endpoints traded** -- session T and session
T+h each have volume > 0.

It is a statement about whether a return exists, not about whether patterns
work: you cannot buy at a price nobody transacted and sell at another price
nobody transacted. It applies identically to both arms and both horizons.

**Why the endpoints and not the whole window.** Requiring all 236 bars of a
detector's window to have traded would be a different and much heavier claim --
it would throw away any name that ever had a quiet day, and quiet days are
normal in small caps rather than fabricated. The endpoints are where the
arithmetic actually happens.

**What this does not do.** It does not repair the corpus and does not change
what ``price_series`` serves. It bounds what this study reads, and leaves the
corpus-level question to be decided rather than answered by a side effect.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--inputs", required=True, help="comma-separated scan-point CSVs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--horizons", default="21,63")
    args = ap.parse_args()

    horizons = [int(h) for h in args.horizons.split(",")]
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    rows: dict[int, list[dict[str, str]]] = {}
    fieldnames: list[str] = []
    for path in args.inputs.split(","):
        with open(path.strip()) as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            for row in reader:
                rows.setdefault(int(row["security_id"]), []).append(row)
    total = sum(len(v) for v in rows.values())
    print(f"{total:,} scan points across {len(rows):,} securities")

    kept_rows: list[dict[str, str]] = []
    dropped = untraded_signal = untraded_outcome = 0
    for n, (security_id, points) in enumerate(sorted(rows.items()), 1):
        bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
        index = {b.session_date: i for i, b in enumerate(bars)}
        volume = [float(b.volume) for b in bars]
        for row in points:
            i = index.get(dt.date.fromisoformat(row["session_date"]))
            if i is None:
                dropped += 1
                continue
            if volume[i] <= 0:
                untraded_signal += 1
                dropped += 1
                continue
            if any(i + h >= len(bars) or volume[i + h] <= 0 for h in horizons):
                untraded_outcome += 1
                dropped += 1
                continue
            kept_rows.append(row)
        if n % 500 == 0:
            print(f"  {n:,}/{len(rows):,} securities")

    with open(args.out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    print(
        f"\nkept {len(kept_rows):,} of {total:,} ({len(kept_rows) / max(1, total):.1%});"
        f" dropped {dropped:,}"
    )
    print(f"  signal bar never traded   {untraded_signal:,}")
    print(f"  outcome bar never traded  {untraded_outcome:,}")
    print(f"  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
