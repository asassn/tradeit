#!/usr/bin/env python
"""What intraday history do the subscriptions we ALREADY hold actually serve?

Before anything is bought. The vendor matrix §2a-i priced Kibot and FirstRate;
this asks the cheaper question first -- whether EODHD, Tiingo or FMP, all of
which this project already pays for or holds keys to, carry minute bars deep
enough to answer the execution question daily bars cannot: did the session that
closed above a level first trade well below it?

Three things are asked of each vendor, in this order, because a later answer is
worthless without the earlier one:

1. does the plan return 1-minute bars at all, or a plan-restriction message;
2. how far back does it go -- probed at 30, 180, 400 and 1,000 days;
3. does it serve a security that has stopped trading, which is the question
   that decides whether any of it is survivorship-safe.

Keys are read from ``.env`` by name, never printed, and redacted from every
response echoed. Nothing is written to the corpus.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "scripts")

#: A live mega-cap, a mid-cap, and two securities that stopped trading -- SIVB
#: in March 2023 and BBBY in September 2023. The dead pair is the whole point.
SYMBOLS = ("AAPL", "CROX", "SIVB", "BBBY")
#: Calendar days back from today, to find where each plan's history stops.
DEPTHS = (30, 400, 1000, 1800, 2600)


def env_keys(path: Path) -> dict[str, str]:
    """Every key in .env, by name. Never shell-sourced, never printed.

    ``research01_probe_vendor_coverage._keys`` reads only two of them, which is
    why the first run of this probe never asked EODHD anything.
    """
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip().endswith(("_API_KEY", "_API_TOKEN")):
            found[name.strip()] = value.strip().strip('"').strip("'")
    return found


def get(url: str, key: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "TradeIt research probe", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")[:2000]
    except urllib.error.URLError as error:
        return 0, str(error.reason)


def summarise(status: int, text: str, key: str) -> str:
    """Bars returned and their span, or the vendor's refusal -- never the key."""
    text = text.replace(key, "***")
    if status != 200:
        return f"HTTP {status}: {text[:150]}"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return f"not JSON: {text[:150]}"
    rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("results")
    if not isinstance(rows, list):
        return f"no rows: {str(payload)[:150]}"
    if not rows:
        return "0 bars"
    stamps = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in ("datetime", "date", "t", "timestamp"):
            if row.get(field):
                stamps.append(str(row[field]))
                break
    span = f"{min(stamps)} .. {max(stamps)}" if stamps else "no timestamps"
    return f"{len(rows):,} bars, {span}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    keys = env_keys(Path(".env"))
    print("keys present:", ", ".join(sorted(keys)))
    today = dt.datetime.now(dt.UTC).date()

    for symbol in args.symbols.split(","):
        print(f"\n{'=' * 78}\n{symbol}")
        for days in DEPTHS:
            start = today - dt.timedelta(days=days)
            print(f"  -- {days} days back ({start}) --")

            if "EODHD_API_KEY" in keys:
                key = keys["EODHD_API_KEY"]
                url = (
                    f"https://eodhd.com/api/intraday/{symbol}.US?interval=1m&fmt=json"
                    f"&from={int(dt.datetime.combine(start, dt.time()).timestamp())}"
                    f"&to={int(dt.datetime.combine(start + dt.timedelta(days=3), dt.time()).timestamp())}"  # noqa: E501
                    f"&api_token={key}"
                )
                status, text = get(url, key)
                print(f"    EODHD 1m      {summarise(status, text, key)}")
                time.sleep(args.interval)

            if "TIINGO_API_KEY" in keys:
                key = keys["TIINGO_API_KEY"]
                query = urllib.parse.urlencode(
                    {
                        "startDate": start.isoformat(),
                        "endDate": (start + dt.timedelta(days=3)).isoformat(),
                        "resampleFreq": "1min",
                        "columns": "open,high,low,close,volume",
                    }
                )
                status, text = get(
                    f"https://api.tiingo.com/iex/{symbol}/prices?{query}",
                    key,
                    {"Authorization": f"Token {key}"},
                )
                print(f"    Tiingo IEX 1m {summarise(status, text, key)}")
                time.sleep(args.interval)

            if "FMP_API_KEY" in keys:
                key = keys["FMP_API_KEY"]
                url = (
                    f"https://financialmodelingprep.com/stable/historical-chart/1min"
                    f"?symbol={symbol}&from={start}&to={start + dt.timedelta(days=3)}&apikey={key}"
                )
                status, text = get(url, key)
                print(f"    FMP 1m        {summarise(status, text, key)}")
                time.sleep(args.interval)
    print(
        "\nSharadar is not probed: its products are end-of-day (SEP/SF1) and it "
        "sells no intraday history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
