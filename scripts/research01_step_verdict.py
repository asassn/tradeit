#!/usr/bin/env python
"""Price each candidate jump-guard factor from the step census. Decide nothing.

``research01_step_census.py`` records every unexplained price step at or beyond
2.0x. This reads that and answers the one question the owner needs answered:
**what does each candidate threshold flag, and what does it cost?**

It deliberately produces a table and a recommendation, not a change. The factor
is a detector threshold; ``CLAUDE.md`` reserves those for the owner, and a
script that silently lowered one would be the exact failure this project keeps
a section about.

**The roundness split is the argument.** A step whose ratio sits within
``--tolerance`` of a simple split ratio is almost certainly an unrecorded
split: splits move price by a ratio because that is what they are. A step that
lands nowhere near one is a market event -- a buyout, a readout, a collapse --
and flagging it costs a study a real observation. The two populations are
counted separately at every candidate factor so the trade-off is visible
rather than asserted.
"""

from __future__ import annotations

import argparse
import collections
import csv

CANDIDATES = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True, help="comma-separated census files")
    ap.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        help="relative distance within which a step counts as landing ON a split ratio",
    )
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for path in args.census.split(","):
        with open(path.strip()) as handle:
            rows.extend(csv.DictReader(handle))
    if not rows:
        raise SystemExit("the census is empty -- nothing to price")
    print(
        f"{len(rows):,} unexplained steps beyond 2.0x, in "
        f"{len({r['security_id'] for r in rows}):,} securities\n"
    )

    print(
        f"{'factor':>7}{'steps':>10}{'securities':>12}{'on a split ratio':>18}"
        f"{'elsewhere':>11}{'split share':>13}"
    )
    print("-" * 71)
    for factor in CANDIDATES:
        hit = [r for r in rows if float(r["ratio"]) >= factor or float(r["ratio"]) <= 1.0 / factor]
        if not hit:
            continue
        roundish = [r for r in hit if float(r["distance_to_split"]) <= args.tolerance]
        print(
            f"{factor:>7.1f}{len(hit):>10,}{len({r['security_id'] for r in hit}):>12,}"
            f"{len(roundish):>18,}{len(hit) - len(roundish):>11,}"
            f"{len(roundish) / len(hit):>12.1%}"
        )

    print("\n--- the band the current 5.0 factor misses: 4.0x to 5.0x ---")
    band = [
        r
        for r in rows
        if (4.0 <= float(r["ratio"]) < 5.0) or (1 / 5.0 < float(r["ratio"]) <= 1 / 4.0)
    ]
    roundish = [r for r in band if float(r["distance_to_split"]) <= args.tolerance]
    print(
        f"  {len(band):,} steps in {len({r['security_id'] for r in band}):,} securities; "
        f"{len(roundish):,} ({len(roundish) / max(1, len(band)):.1%}) land on a split ratio"
    )
    near = collections.Counter(r["nearest_split_ratio"] for r in roundish)
    print(
        "  the ratios they land on: "
        + ", ".join(f"{float(k):g}x({v})" for k, v in near.most_common(8))
    )
    print("\nA step landing on a split ratio is a corpus defect: excluding it costs a study")
    print("nothing real. A step landing elsewhere is a market event, and excluding it")
    print("discards evidence. The factor should sit where the first population ends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
