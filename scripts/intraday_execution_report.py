#!/usr/bin/env python
"""Read the execution audit: what a stop fills at, and which came first.

Both numbers are stated in **R** -- units of the trade's own risk -- because
that is how every result on the scoreboard is expressed, so the correction
transfers without rescaling.

``slippage_r`` is ``(fill - stop) / (entry - stop)``. Zero is the simulation's
assumption. **Negative is the simulation being wrong in the flattering
direction**: the fill was below the stop and the loss larger than 1R.
"""

from __future__ import annotations

import argparse
import collections
import csv
import statistics
from pathlib import Path


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    args = ap.parse_args()
    with Path(args.audit).open() as handle:
        rows = list(csv.DictReader(handle))
    print(
        f"{len(rows):,} synthetic sessions, {len({r['ticker'] for r in rows})} tickers, "
        f"{r'' if not rows else min(r['session_date'] for r in rows)} .. "
        f"{max(r['session_date'] for r in rows)}"
    )

    print(
        f"\n{'stop':<7}{'sessions':>10}{'stopped':>10}{'fill < stop':>13}{'median R':>11}"
        f"{'p10 R':>9}{'worst R':>10}"
    )
    for fraction in sorted({r["fraction"] for r in rows}, key=float):
        band = [r for r in rows if r["fraction"] == fraction]
        stopped = [r for r in band if r["daily_stopped"] == "1" and r["slippage_r"]]
        if not stopped:
            print(f"{float(fraction):<7.0%}{len(band):>10,}{0:>10}")
            continue
        slip = [float(r["slippage_r"]) for r in stopped]
        worse = sum(1 for s in slip if s < 0)
        print(
            f"{float(fraction):<7.0%}{len(band):>10,}{len(stopped):>10,}"
            f"{worse / len(slip):>12.1%}{statistics.median(slip):>11.4f}"
            f"{quantile(slip, 0.10):>9.4f}{min(slip):>10.4f}"
        )

    print("\n-- the ambiguous sessions: daily bars span BOTH stop and target --")
    print(f"{'stop':<7}{'ambiguous':>11}{'share of stopped':>18}{'stop truly first':>18}")
    for fraction in sorted({r["fraction"] for r in rows}, key=float):
        band = [r for r in rows if r["fraction"] == fraction]
        stopped = [r for r in band if r["daily_stopped"] == "1"]
        ambiguous = [
            r for r in band if r["daily_ambiguous"] == "1" and r["stop_index"] and r["target_index"]
        ]
        if not ambiguous:
            print(f"{float(fraction):<7.0%}{0:>11}")
            continue
        first = sum(1 for r in ambiguous if int(r["stop_index"]) < int(r["target_index"]))
        print(
            f"{float(fraction):<7.0%}{len(ambiguous):>11,}"
            f"{len(ambiguous) / max(1, len(stopped)):>17.1%}{first / len(ambiguous):>18.1%}"
        )

    every = [float(r["slippage_r"]) for r in rows if r["daily_stopped"] == "1" and r["slippage_r"]]
    if every:
        print(
            f"\nacross every stop level: {len(every):,} stop events, "
            f"median {statistics.median(every):+.4f}R, mean {statistics.fmean(every):+.4f}R, "
            f"p10 {quantile(every, 0.10):+.4f}R, worst {min(every):+.4f}R"
        )
        print(f"  fills at or above the stop: {sum(1 for s in every if s >= 0) / len(every):.1%}")
    by_ticker = collections.defaultdict(list)
    for r in rows:
        if r["daily_stopped"] == "1" and r["slippage_r"]:
            by_ticker[r["ticker"]].append(float(r["slippage_r"]))
    worst = sorted(by_ticker.items(), key=lambda kv: statistics.median(kv[1]))[:5]
    print(
        "  worst five tickers by median slippage: "
        + ", ".join(f"{t} {statistics.median(v):+.3f}R" for t, v in worst)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
