#!/usr/bin/env python
"""What are the exchange-listed symbols the corpus has no identity for?

EODHD carries 110,986 US symbols and the corpus holds identity for a fraction.
Calling the remainder "56,000 missing stocks" was misleading: ~48,000 sit on
``NMFQS``, the Nasdaq **Mutual Fund** Quotation Service, and were never
exchange-traded, and ~20,000 more are PINK or OTCGREY.

**What is worth looking at is narrower and real: 13,305 delisted symbols typed
Common or Preferred Stock on NASDAQ, NYSE, NYSE MKT, AMEX, ARCA or BATS.**
Among them is ``AABA`` -- Altaba, the entity Yahoo! became, NASDAQ-listed until
it liquidated in 2019 -- which plainly belongs in a survivorship corpus.

Also among them: ADRs of foreign issuers, SPAC units, warrants and rights,
preferred series, and the vendor's own ``_old`` disambiguators for tickers the
corpus may already hold under a different label. Those are four different
populations wearing one label, and no count of them means anything.

**So this ranks them by what actually traded.** Median dollar volume over the
security's own life separates a company somebody owned from a warrant that
printed twice. It writes a file and changes nothing: the decision about which
of these belong in the corpus is the owner's, and it needs the names in front
of him rather than a policy inferred from a total.

Read-only against the corpus, and writes no database rows at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import statistics
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.research01.eodhd_client import HttpEodhdClient, resolve_api_token

MAJOR = {"NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT", "AMEX", "BATS"}
EQUITY = {"Common Stock", "Preferred Stock"}
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="research01.sqlite")
    ap.add_argument("--shard", default="", metavar="I/N")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    listing = json.loads((OUT / "eodhd_delisted_us.json").read_text())
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=900)
    con.execute("pragma busy_timeout=900000")
    held = {
        value.upper()
        for (value,) in con.execute(
            "select distinct alias_value from symbol_aliases where alias_kind='ticker'"
        )
    }
    targets = [
        row
        for row in listing
        if row.get("Code", "").upper() not in held
        and row.get("Type") in EQUITY
        and row.get("Exchange") in MAJOR
    ]
    targets.sort(key=lambda r: r["Code"])
    print(f"delisted equity symbols on a major exchange, unmapped: {len(targets):,}", flush=True)

    suffix = ""
    if args.shard:
        index, _, count = args.shard.partition("/")
        i, n = int(index), int(count)
        # zlib.crc32, not hash(): Python salts string hashing per process, so
        # hash(code) % n gives a different partition in every shard and the
        # shards overlap instead of dividing. That produced duplicate rows the
        # first time this ran.
        targets = [r for r in targets if zlib.crc32(r["Code"].encode()) % n == i]
        suffix = f"_{i}"
        print(f"  shard {i}/{n}: {len(targets):,}", flush=True)
    if args.limit:
        targets = targets[: args.limit]

    client = HttpEodhdClient(api_token=resolve_api_token(env_file=Path(".env")))
    out_path = OUT / f"unmapped_census{suffix}.jsonl"
    written = empty = failed = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for position, row in enumerate(targets, 1):
            code = row["Code"]
            try:
                bars = client.eod(f"{code}.US", dt.date(1990, 1, 1), dt.date(2026, 9, 30))
            except Exception as error:
                # One bad symbol must not end a run of thirteen thousand.
                failed += 1
                if failed <= 3:
                    print(f"    {code}: {type(error).__name__}", flush=True)
                continue
            if not bars:
                empty += 1
                continue
            # Every bar, including the untraded ones. The first version kept
            # only bars where close and volume were both truthy -- and a volume
            # of zero is falsy, so it dropped exactly the days that make a
            # security illiquid and reported the survivors' median. That ranked
            # a series with three traded days above a real company.
            volumes = [float(b.get("volume") or 0) for b in bars]
            closes = [float(b.get("close") or 0) for b in bars]
            dollars = [c * v for c, v in zip(closes, volumes, strict=True)]
            handle.write(
                json.dumps(
                    {
                        "code": code,
                        "name": row.get("Name"),
                        "exchange": row.get("Exchange"),
                        "type": row.get("Type"),
                        "isin": row.get("Isin"),
                        "bars": len(bars),
                        "first": bars[0]["date"],
                        "last": bars[-1]["date"],
                        # Kept apart so an implausible product is attributable.
                        # SSW-P-D returns a close of 4,945 for a preferred
                        # share; multiplied by volume that is a number nobody
                        # can sanity-check, and separated it is obvious.
                        "median_close": statistics.median(closes) if closes else 0.0,
                        "median_volume": statistics.median(volumes) if volumes else 0.0,
                        "zero_volume_share": (
                            sum(1 for v in volumes if v == 0) / len(volumes) if volumes else 1.0
                        ),
                        "median_dollar_volume": statistics.median(dollars) if dollars else 0.0,
                    }
                )
                + "\n"
            )
            written += 1
            if position % 250 == 0:
                handle.flush()
                print(
                    f"  {position:,}/{len(targets):,}  written {written:,} "
                    f"empty {empty:,} failed {failed:,}",
                    flush=True,
                )
            time.sleep(0.05)
    print(f"\nwritten {written:,}  vendor-empty {empty:,}  failed {failed:,}")
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
