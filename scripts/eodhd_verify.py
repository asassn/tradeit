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

    # put EODHD_API_KEY=... in the repository's .env file (gitignored),
    # or export it in your shell. Either works.
    PYTHONPATH=src .venv/bin/python scripts/eodhd_verify.py

Costs 12 API calls (4 symbols x 3 endpoints) out of a 100,000/day allowance.
"""

from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, "src")

from tradeit.errors import DataError
from tradeit.research01.eodhd_client import (
    HttpEodhdClient,
    parse_bars,
    parse_dividends,
    parse_splits,
    resolve_api_token,
)

SYMBOLS = ("ETYS.US", "WBVN.US", "GM.US", "GM_old.US")
START, END = dt.date(1998, 1, 1), dt.date(2026, 9, 1)
BREAK = dt.date(2009, 7, 1)


def main() -> int:
    try:
        token = resolve_api_token()
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    client = HttpEodhdClient(api_token=token)
    # Three outcomes, kept apart on purpose: a span, "asked and got nothing",
    # and "never asked". The third is not a finding about the vendor.
    spans: dict[str, tuple[dt.date, dt.date] | None] = {}
    unasked: set[str] = set()

    for symbol in SYMBOLS:
        try:
            bars = parse_bars(symbol, client.eod(symbol, START, END))
            splits = parse_splits(symbol, client.splits(symbol, START, END))
            dividends = parse_dividends(symbol, client.dividends(symbol, START, END))
        except Exception as exc:
            print(f"{symbol:12s} FAILED  {type(exc).__name__}: {exc}")
            unasked.add(symbol)
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
    if unasked:
        print(
            f"NOTHING, for {len(unasked)} of {len(SYMBOLS)} symbols: the request never "
            "completed.\nA failed call is not evidence about coverage. Fix the error above "
            "and re-run."
        )
        for symbol in sorted(unasked):
            print(f"  not asked: {symbol}")
        if len(unasked) == len(SYMBOLS):
            print(f"\napi calls issued: {client.calls}")
            return 1
        print()
    for symbol in ("ETYS.US", "WBVN.US"):
        if symbol in unasked:
            continue
        span = spans.get(symbol)
        if span is None:
            print(f"{symbol}: asked, and the vendor returned no bars. Coverage is NOT there.")
        elif span[0] <= dt.date(1999, 12, 31):
            print(f"{symbol}: reaches {span[0]} -- covers 1998-1999. Better than stated.")
        else:
            print(f"{symbol}: starts {span[0]} -- consistent with a Jan 2000 delisted floor.")

    old, new = spans.get("GM_old.US"), spans.get("GM.US")
    print()
    if {"GM.US", "GM_old.US"} & unasked:
        print("GM: not asked -- the splice test did not run.")
        print(f"\napi calls issued: {client.calls}")
        return 1
    if old and new:
        # Compare the two spans to EACH OTHER. An earlier version of this check
        # compared each span against a hardcoded 2009 date, which reported PASS
        # on spans that plainly overlapped -- the expected-looking answer to a
        # question it had not asked.
        overlaps = old[0] <= new[1] and new[0] <= old[1]
        if overlaps:
            days = (min(old[1], new[1]) - max(old[0], new[0])).days
            print(
                f"GM: GM_old {old[0]}..{old[1]} and GM {new[0]}..{new[1]} OVERLAP by {days} days."
            )
            print(
                "     Not automatically a splice: new GM listed while old GM was still\n"
                "     winding down, so the two securities genuinely coexisted. What it\n"
                "     DOES mean is that one ticker cannot be resolved by date alone here,\n"
                "     and symbol_aliases must carry both with disjoint intervals decided\n"
                "     from evidence rather than from the vendor's spans."
            )
        else:
            print(f"GM: GM_old ends {old[1]}, GM starts {new[0]} -- disjoint. No overlap.")
    elif new and not old:
        print("GM: only GM.US returned. Check whether the pre-2009 company is under another name.")
    else:
        print("GM: incomplete pair; cannot judge the splice test.")

    print(f"\napi calls issued: {client.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
