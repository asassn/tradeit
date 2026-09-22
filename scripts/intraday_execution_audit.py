#!/usr/bin/env python
"""Do daily bars flatter the stops and exits every backtest here has assumed?

Two conventions were chosen blind when only daily bars existed, and §44 traded
147,916 trades on both:

1. **A stop fills at the stop price.** The daily low touches it, the simulation
   books that exact price. Intraday, the fill is the next trade after the level
   breaks, which in a fast move is lower.
2. **A session spanning both stop and target counts as STOPPED.** Daily bars
   cannot order the two, and the pessimistic reading was chosen because it
   cannot flatter a rule. Whether it is *right* has never been measured.

This measures both against real 1-minute paths from Tiingo's IEX feed, on the
subscription already held.

**Scope, and why it is narrow on purpose.** The feed serves survivors only and
its accuracy tracks liquidity (§2a-i), so this runs on the most liquid names
this corpus holds. That is legitimate *here* and would not be for a return
study: these are questions about **bar mechanics**, not about which securities
pay, and a stop's slippage on a liquid name is not a claim about the market's
cross-section.

**2020-2025 is not read.** It is the held-out confirmation window for other
registrations; this uses 2019, where the feed's depth begins, and 2026.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_probe_intraday_plans import env_keys, get

#: §44's stop band at its edges and middle, plus two tighter levels. The tight
#: ones are not part of that rule; they are here because a 3% stop on a mega-cap
#: is rarely touched in one session, and the mechanics being measured -- what a
#: stop fills at, and whether stop or target comes first -- need events.
STOP_FRACTIONS = (0.01, 0.02, 0.03, 0.05, 0.08, 0.13)
#: Regular session in UTC, as the feed stamps it.
SESSION = (dt.time(13, 30), dt.time(19, 59))
WINDOWS = (
    (dt.date(2019, 8, 12), dt.date(2019, 12, 31)),
    (dt.date(2026, 1, 2), dt.date(2026, 9, 11)),
)


def minute_bars(ticker: str, start: dt.date, end: dt.date, key: str, pause: float) -> dict:
    """A month at a time, and a refusal is raised rather than skipped.

    **The first version asked for a week at a time and treated any non-200 as
    an empty week.** Tiingo answered `429 -- hourly request allocation` from the
    fifth ticker on, and the run reported "0 sessions" for each while continuing
    happily: a 60-ticker sample built from four, with nothing in the output
    saying so. Silence is the one thing a data loader must never do with a
    refusal, so this raises, and a 429 waits and retries rather than dropping
    the window.
    """
    out: dict[dt.date, list[dict]] = collections.defaultdict(list)
    month = start
    while month <= end:
        finish = min(month + dt.timedelta(days=30), end)
        query = urllib.parse.urlencode(
            {
                "startDate": month.isoformat(),
                "endDate": finish.isoformat(),
                "resampleFreq": "1min",
                "columns": "open,high,low,close,volume",
            }
        )
        url = f"https://api.tiingo.com/iex/{ticker}/prices?{query}"
        for attempt in range(6):
            status, text = get(url, key, {"Authorization": f"Token {key}"})
            if status == 200:
                break
            if status == 429:
                wait = 300 * (attempt + 1)
                print(f"    429 on {ticker} {month}: waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{ticker} {month}..{finish}: HTTP {status} {text[:120]}")
        else:
            raise RuntimeError(f"{ticker} {month}: still rate-limited after six waits")
        rows = json.loads(text)
        for row in rows:
            stamp = dt.datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
            if SESSION[0] <= stamp.time() <= SESSION[1]:
                out[stamp.date()].append({**row, "stamp": stamp})
        month = finish + dt.timedelta(days=1)
        time.sleep(pause)
    for bars in out.values():
        bars.sort(key=lambda r: r["stamp"])
    return out


def audit_session(bars: list[dict], fraction: float) -> dict[str, object] | None:
    """One synthetic trade: buy the open, stop `fraction` below, target as far above.

    Returns what the daily bar would have concluded and what the minutes say.
    """
    if len(bars) < 60:
        return None
    entry = float(bars[0]["open"])
    if entry <= 0:
        return None
    stop, target = entry * (1 - fraction), entry * (1 + fraction)
    day_low = min(float(b["low"]) for b in bars)
    day_high = max(float(b["high"]) for b in bars)
    daily_says_stopped = day_low <= stop
    daily_says_target = day_high >= target
    first_stop = first_target = None
    fill = None
    for index, bar in enumerate(bars):
        if first_stop is None and float(bar["low"]) <= stop:
            first_stop = index
            # The fill a market order would actually get: the next trade after
            # the level breaks, which is the following minute's open where one
            # exists and this minute's close where it does not.
            fill = float(bars[index + 1]["open"]) if index + 1 < len(bars) else float(bar["close"])
        if first_target is None and float(bar["high"]) >= target:
            first_target = index
        if first_stop is not None and first_target is not None:
            break
    return {
        "entry": entry,
        "stop": stop,
        "fraction": fraction,
        "daily_stopped": daily_says_stopped,
        "daily_ambiguous": daily_says_stopped and daily_says_target,
        "stop_index": first_stop,
        "target_index": first_target,
        "fill": fill,
        "slippage_r": ((fill - stop) / (entry - stop)) if fill is not None else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True, help="CSV of security_id,ticker")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    names = [
        line.split(",")[1].strip()
        for line in Path(args.universe).read_text().splitlines()
        if line.strip()
    ]
    if args.limit:
        names = names[: args.limit]
    key = env_keys(Path(".env"))["TIINGO_API_KEY"]
    print(f"{len(names)} tickers; windows {[f'{a}..{b}' for a, b in WINDOWS]}", flush=True)

    t0 = time.time()
    written = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "ticker",
                "session_date",
                "fraction",
                "daily_stopped",
                "daily_ambiguous",
                "stop_index",
                "target_index",
                "entry",
                "stop",
                "fill",
                "slippage_r",
            )
        )
        for n, ticker in enumerate(names, 1):
            sessions: dict[dt.date, list[dict]] = {}
            for start, end in WINDOWS:
                sessions |= minute_bars(ticker, start, end, key, args.pause)
            if not sessions:
                raise RuntimeError(
                    f"{ticker}: no sessions returned -- refusing to report a gap as data"
                )
            for day, bars in sorted(sessions.items()):
                for fraction in STOP_FRACTIONS:
                    result = audit_session(bars, fraction)
                    if result is None:
                        continue
                    writer.writerow(
                        (
                            ticker,
                            day.isoformat(),
                            fraction,
                            int(result["daily_stopped"]),
                            int(result["daily_ambiguous"]),
                            result["stop_index"],
                            result["target_index"],
                            f"{result['entry']:.4f}",
                            f"{result['stop']:.4f}",
                            "" if result["fill"] is None else f"{result['fill']:.4f}",
                            "" if result["slippage_r"] is None else f"{result['slippage_r']:.6f}",
                        )
                    )
                    written += 1
            print(
                f"  {n}/{len(names)} {ticker}: {len(sessions)} sessions, "
                f"{written:,} rows [{time.time() - t0:.0f}s]",
                flush=True,
            )
    print(f"\n{written:,} rows -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
