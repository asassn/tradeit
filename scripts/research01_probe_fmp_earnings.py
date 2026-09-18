#!/usr/bin/env python
"""Does the FMP plan this project holds carry historical earnings surprises?

A capability probe, not a study. §40 found time-series earnings surprise to be
the closest miss among four fundamental signals, and the literature's stronger
measure is surprise against ANALYST CONSENSUS -- information the corpus does not
hold. Before anything is registered, this asks the vendor already subscribed
what it serves: which endpoints answer on this plan, how far back, whether the
estimate is actually populated, and whether a dead company's history is there
at all -- a feed with only today's listed names would be survivor-biased by
construction.

The key is read from ``.env`` by name, never printed, and redacted from any
response text echoed back. Nothing is written to the corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "scripts")

from research01_probe_vendor_coverage import _keys

BASE = "https://financialmodelingprep.com"
#: Candidate endpoints, current ("stable") API first, legacy v3 second.
#: The first run asked limit=1000 and was refused: this plan allows 0..5, and
#: serves the endpoint at all only for some symbols. Both are kept so the
#: refusal stays measurable.
ENDPOINTS = {
    "stable/earnings": "/stable/earnings?symbol={symbol}&limit=1000",
    "stable/earnings limit=5": "/stable/earnings?symbol={symbol}&limit=5",
    "stable/earnings (no limit)": "/stable/earnings?symbol={symbol}",
    "v3/earnings-surprises": "/api/v3/earnings-surprises/{symbol}",
    "v3/historical/earning_calendar": "/api/v3/historical/earning_calendar/{symbol}",
}
#: Large and small, current and dead. SIVB: Silicon Valley Bank, failed 2023.
#: FRC: First Republic, failed 2023. BBBY: Bed Bath & Beyond, bankrupt 2023.
DEFAULT_SYMBOLS = ("AAPL", "MSFT", "CROX", "SIVB", "FRC", "BBBY")


def _get(path: str, key: str) -> tuple[int, str]:
    url = f"{BASE}{path}{'&' if '?' in path else '?'}apikey={key}"
    request = urllib.request.Request(url, headers={"User-Agent": "TradeIt research probe"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")
    except urllib.error.URLError as error:
        return 0, str(error.reason)


def _summary(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"not_json": text[:160]}
    if isinstance(payload, dict):
        # Plan restrictions and errors arrive as an object with a message.
        return {"message": str(payload)[:200]}
    if not isinstance(payload, list) or not payload:
        return {"records": 0}
    rows = [r for r in payload if isinstance(r, dict)]
    dates = sorted(str(r.get("date", "")) for r in rows if r.get("date"))
    estimate_keys = [k for k in rows[0] if "estimat" in k.lower()]
    actual_keys = [k for k in rows[0] if "actual" in k.lower()]
    populated = {k: sum(1 for r in rows if r.get(k) not in (None, "")) for k in estimate_keys}
    return {
        "records": len(rows),
        "first": dates[0] if dates else None,
        "last": dates[-1] if dates else None,
        "fields": sorted(rows[0]),
        "estimate_populated": populated,
        "actual_fields": actual_keys,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()

    key = _keys(Path(".env")).get("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY is not set in .env")
        return 2

    for symbol in args.symbols.split(","):
        print(f"\n== {symbol}")
        for name, template in ENDPOINTS.items():
            status, text = _get(template.format(symbol=symbol), key)
            summary = _summary(text.replace(key, "***"))
            print(f"  {name:<32} HTTP {status}  {json.dumps(summary, default=str)[:400]}")
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
