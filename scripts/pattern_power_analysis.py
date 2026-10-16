#!/usr/bin/env python
"""What effect can this corpus resolve at all? Measured, not assumed.

Written 2026-10-16, after two Stage B tests failed the same way: §52's VCP went
from t 3.82 in-sample to 1.02 out, §53's pennant from -3.02 to -0.53 and +4.44
to +2.05. Both kept their direction and lost two thirds of their magnitude,
which is the signature of an effect near the limit of what the data can
establish rather than of a rule that stopped working.

So this asks the prior question the program should have asked before spending
two trials: **given the measured block variance and the ledger hurdle, what is
the smallest per-trade effect this corpus could confirm?**

Three readings, in order of how much they change the plan:

1. **The minimum detectable effect**, `hurdle x block_sd / sqrt(blocks)`, beside
   the effect actually observed. If the two are the same size, the program has
   been trying to measure something slightly smaller than its own resolution.

2. **Whether the placebo is leaking.** If the block variance were market
   variance that the control failed to remove, the limit would be a design flaw
   and fixable. Measured by the correlation between the rule leg's and placebo
   leg's block means and by how much of the rule's own block volatility the
   difference cancels. A high correlation and a large reduction mean the control
   works and the residual is real dispersion.

3. **How much history a target effect would need.** `(hurdle x sd / target)^2`
   blocks, in years. This is the number that decides whether more screening or
   more data is the binding constraint.

Legs beyond `EXTREME_LEG` are excluded throughout, for the reason
`pattern_atlas_report.py` withholds their means: one §0.10 discontinuity moves
a mean over ten thousand trades and would make the variance it is measuring
meaningless.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import math
import statistics as st
import sys

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import small_sample_hurdle

#: Matches `pattern_atlas_report.EXTREME_RETURN`; see §0.10.
EXTREME_LEG = 4.0
#: Trials on the ledger after §53.
TRIALS = 145


def pairs(path: str, pattern: str, arm: str, regime: str) -> list[tuple[dict, dict]]:
    with open(path) as handle:
        rows = list(csv.DictReader(handle))
    by: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for row in rows:
        by[row["trade_id"]][row["leg"]] = row
    return [
        (v["rule"], v["placebo"])
        for v in by.values()
        if len(v) == 2
        and v["rule"]["pattern"] == pattern
        and v["rule"]["arm"] == arm
        and v["rule"]["regime"] == regime
    ]


def block_means(
    selected: list[tuple[dict, dict]], column: str, months: int, floor: int
) -> list[tuple[float, float]]:
    """Per-block mean of the rule leg and of its placebo, on disjoint buckets."""
    grouped: dict[int, tuple[list[float], list[float]]] = collections.defaultdict(lambda: ([], []))
    for rule, placebo in selected:
        if not rule[column] or not placebo[column]:
            continue
        if abs(float(rule[column])) > EXTREME_LEG or abs(float(placebo[column])) > EXTREME_LEG:
            continue
        day = dt.date.fromisoformat(rule["entry_date"])
        key = (day.year * 12 + day.month - 1) // months
        grouped[key][0].append(float(rule[column]))
        grouped[key][1].append(float(placebo[column]))
    return [(st.fmean(r), st.fmean(p)) for _, (r, p) in sorted(grouped.items()) if len(r) >= floor]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--column", default="net_126")
    ap.add_argument("--months", type=int, default=6, help="block width")
    ap.add_argument("--floor", type=int, default=30, help="minimum trades per block")
    ap.add_argument(
        "--cells",
        default="volatility_contraction:confirmed,pennant:closed_above,"
        "bull_flag:closed_above,cup_with_handle:confirmed",
    )
    ap.add_argument("--regime", default="uptrend")
    ap.add_argument("--targets", default="0.005,0.0075,0.01")
    args = ap.parse_args()

    print(f"block width {args.months} months, floor {args.floor} trades, {args.column}\n")
    print("=== 1. MINIMUM DETECTABLE EFFECT vs WHAT WAS OBSERVED ===")
    header = f"{'cell':<40}{'blocks':>7}{'block sd':>10}{'hurdle':>8}{'MDE':>9}{'observed':>10}"
    print(header)
    print("-" * len(header))
    sds: list[float] = []
    for spec in args.cells.split(","):
        pattern, arm = spec.split(":")
        got = block_means(
            pairs(args.atlas, pattern, arm, args.regime), args.column, args.months, args.floor
        )
        if len(got) < 4:
            continue
        diff = [r - p for r, p in got]
        sd = st.stdev(diff)
        sds.append(sd)
        hurdle = small_sample_hurdle(TRIALS, len(diff))
        mde = hurdle * sd / math.sqrt(len(diff))
        print(
            f"{pattern + '/' + arm:<40}{len(diff):>7}{sd * 100:>9.2f}%{hurdle:>8.2f}"
            f"{mde * 100:>8.2f}%{st.fmean(diff) * 100:>+9.2f}%"
        )

    print("\n=== 2. IS THE PLACEBO REMOVING THE MARKET? ===")
    header = f"{'cell':<40}{'rule sd':>9}{'plac sd':>9}{'corr':>7}{'diff sd':>9}{'removed':>9}"
    print(header)
    print("-" * len(header))
    for spec in args.cells.split(","):
        pattern, arm = spec.split(":")
        got = block_means(
            pairs(args.atlas, pattern, arm, args.regime), args.column, args.months, args.floor
        )
        if len(got) < 4:
            continue
        rule = [r for r, _ in got]
        plac = [p for _, p in got]
        diff = [r - p for r, p in got]
        sr, sp, sd = st.stdev(rule), st.stdev(plac), st.stdev(diff)
        mr, mp = st.fmean(rule), st.fmean(plac)
        corr = sum((a - mr) * (b - mp) for a, b in zip(rule, plac, strict=True)) / (
            (len(rule) - 1) * sr * sp
        )
        print(
            f"{pattern + '/' + arm:<40}{sr * 100:>8.2f}%{sp * 100:>8.2f}%{corr:>7.2f}"
            f"{sd * 100:>8.2f}%{(1 - sd / sr) * 100:>8.0f}%"
        )
    print("\nA high correlation and a large reduction mean the control works and the")
    print("residual is real dispersion, not market noise it failed to remove.")

    if sds:
        typical = st.median(sds)
        print(f"\n=== 3. HISTORY NEEDED, at the median block sd of {typical * 100:.2f}% ===")
        for target in (float(x) for x in args.targets.split(",")):
            blocks = (small_sample_hurdle(TRIALS, 20) * typical / target) ** 2
            print(
                f"  to resolve {target * 100:.2f}% per trade: {blocks:.0f} disjoint "
                f"{args.months}-month blocks = {blocks * args.months / 12:.0f} years"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
