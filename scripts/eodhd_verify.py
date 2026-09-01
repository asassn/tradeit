#!/usr/bin/env python
"""First thing to run after subscribing: does EODHD actually have what it says?

This is the sample-file test, run by us instead of asked for. It answers the
coverage question **from the raw response**, before identity is involved, so it
works even though most control tickers have no curated alias interval yet:

* ``ETYS`` and ``WBVN`` listed in 1999 and were gone by 2001. If bars come back
  covering that window, EODHD's "almost all delisted companies from Jan 2000"
  is real for the population that matters. **If they start in 2000, that is the
  answer to the 1998-1999 question** and it is a finding either way.
* ``GM`` and ``GM_old`` are two unrelated registrants. **A continuous series
  across mid-2009 in a single symbol is a SPLICE and a failure.** Two symbols
  covering disjoint windows is the correct result.

Usage:

    export EODHD_API_KEY="...."          # never paste the key anywhere else
    PYTHONPATH=src .venv/bin/python scripts/eodhd_verify.py

Costs 12 API calls (4 symbols x 3 endpoints) out of a 100,000/day allowance.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, "src")

from tradeit.research01.eodhd_client import (
    HttpEodhdClient,
    parse_bars,
    parse_dividends,
    parse_splits,
)

SYMBOLS = ("ETYS.US", "WBVN.US", "GM.US", "GM_old.US")
START, END = dt.date(1998, 1, 1), dt.date(2026, 9, 1)
BREAK = dt.date(2009, 7, 1)


def main() -> int:
    token = os.environ.get("EODHD_API_KEY", "")
    if not token:
        print("EODHD_API_KEY is not set.", file=sys.stderr)
        print('  export EODHD_API_KEY="your-key"', file=sys.stderr)
        return 1

    client = HttpEodhdClient(api_token=token)
    spans: dict[str, tuple[dt.date, dt.date] | None] = {}

    for symbol in SYMBOLS:
        try:
            bars = parse_bars(symbol, client.eod(symbol, START, END))
            splits = parse_splits(symbol, client.splits(symbol, START, END))
            dividends = parse_dividends(symbol, client.dividends(symbol, START, END))
        except Exception as exc:
            print(f"{symbol:12s} FAILED  {type(exc).__name__}: {exc}")
            spans[symbol] = None
            continue

        raw = sorted({b.session_date for b in bars if b.adjustment_basis == "raw"})
        if not raw:
            print(f"{symbol:12s} NO BARS RETURNED")
            spans[symbol] = None
            continue
        spans[symbol] = (raw[0], raw[-1])
        print(
            f"{symbol:12s} {len(raw):>6,} sessions  {raw[0]} -> {raw[-1]}  "
            f"splits={len(splits)} dividends={len(dividends)}"
        )

    print("\n--- what this establishes ---")
    for symbol in ("ETYS.US", "WBVN.US"):
        span = spans.get(symbol)
        if span is None:
            print(f"{symbol}: no data. Coverage for this delisted name is NOT there.")
        elif span[0] <= dt.date(1999, 12, 31):
            print(f"{symbol}: reaches {span[0]} -- covers 1998-1999. Better than stated.")
        else:
            print(f"{symbol}: starts {span[0]} -- consistent with a Jan 2000 delisted floor.")

    old, new = spans.get("GM_old.US"), spans.get("GM.US")
    print()
    if old and new:
        if old[1] >= BREAK and new[0] <= BREAK:
            print("GM: BOTH symbols span the 2009 break -- OVERLAP. Investigate before trusting.")
        else:
            print(f"GM: GM_old ends {old[1]}, GM starts {new[0]} -- disjoint, no splice. PASS.")
    elif new and not old:
        print("GM: only GM.US returned. Check whether the pre-2009 company is under another name.")
    else:
        print("GM: incomplete pair; cannot judge the splice test.")

    print(f"\napi calls issued: {client.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
