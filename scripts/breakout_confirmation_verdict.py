#!/usr/bin/env python
"""Judge the confirmed-breakout question exactly as registered.

``docs/prereg/BREAKOUT_CONFIRMATION_2026-09-23.md`` at ``30b9a60``. The
continuation probabilities are printed whatever the criteria do, because they
are the question as asked; the criteria then decide whether *waiting* for the
platform's own confirmation earns its cost.

Arms enter on different sessions and so have different trade counts in a block.
The statistic is therefore the difference of the two arms' **block means**, not
a paired difference per trade -- there is no pairing to have.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import small_sample_hurdle
from tradeit.signals.cross_section import DAYS_PER_SESSION

TRIALS = 142
HOLD = 63
SPLIT = dt.date(2015, 1, 1)
ARMS = ("closed_above", "retest_confirmed", "confirmed")


def blocks_of(dates: list[dt.date]) -> np.ndarray:
    origin = min(dates)
    width = max(1, round(HOLD * DAYS_PER_SESSION))
    return np.array([(d - origin).days // width for d in dates])


def block_means(values: list[float], block: np.ndarray) -> dict[int, float]:
    out: dict[int, list[float]] = collections.defaultdict(list)
    for v, b in zip(values, block, strict=True):
        out[int(b)].append(v)
    return {b: float(np.mean(v)) for b, v in out.items()}


def compare(left: dict[int, float], right: dict[int, float]) -> tuple[float, float | None, int]:
    shared = sorted(set(left) & set(right))
    diff = np.array([left[b] - right[b] for b in shared])
    n = diff.shape[0]
    if n < 3:
        return (float(diff.mean()) if n else float("nan")), None, n
    centred = diff - diff.mean()
    variance = float(np.dot(centred, centred)) / n
    if variance == 0:
        return float(diff.mean()), None, n
    lag1 = float(np.dot(centred[1:], centred[:-1])) / n
    inflation = max(1.0, 1.0 + lag1 / variance)
    se = float(np.sqrt(variance * inflation / (n - 1)))
    return float(diff.mean()), (float(diff.mean() / se) if se else None), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    args = ap.parse_args()
    with Path(args.pairs).open() as handle:
        rows = list(csv.DictReader(handle))
    legs: dict[tuple[str, str], list[dict[str, str]]] = collections.defaultdict(list)
    for r in rows:
        legs[(r["arm"], r["leg"])].append(r)

    print("CONTINUATION -- the question as asked, reported whatever follows\n")
    print(
        f"{'arm':<18}{'trades':>9}{'+1R first':>11}{'stopped first':>15}"
        f"{'up @21':>9}{'up @63':>9}{'median 63s':>12}"
    )
    for arm in ARMS:
        r = legs[(arm, "rule")]
        if not r:
            continue

        def share(key: str, rows_: list[dict[str, str]] = r) -> float:
            v = [x[key] for x in rows_ if x[key]]
            return sum(1 for x in v if x == "1") / len(v) if v else float("nan")

        ret = [float(x["ret_63"]) for x in r if x["ret_63"]]
        print(
            f"{arm:<18}{len(r):>9,}{share('reached_1r_first'):>10.1%}{share('stopped_first'):>15.1%}"
            f"{share('up_at_21'):>9.1%}{share('up_at_63'):>9.1%}{st.median(ret):>11.2%}"
        )

    passed: dict[str, bool] = {}
    for recovery in (1, 0):
        for cost in (10, 20):
            column = f"r_c{cost}_rec{recovery}"
            print(f"\n{'=' * 78}\nrecovery {recovery}.0, {cost} bps a side")
            per_arm_block = {}
            for arm in ARMS:
                for leg in ("rule", "placebo"):
                    r = legs[(arm, leg)]
                    if not r:
                        continue
                    dates = [dt.date.fromisoformat(x["entry_date"]) for x in r]
                    per_arm_block[(arm, leg)] = (
                        block_means([float(x[column]) for x in r], blocks_of(dates)),
                        dates,
                    )
            for arm in ("retest_confirmed", "confirmed"):
                if (arm, "rule") not in per_arm_block:
                    continue
                rule = per_arm_block[(arm, "rule")][0]
                placebo = per_arm_block[(arm, "placebo")][0]
                base = per_arm_block[("closed_above", "rule")][0]
                for label, other in (("vs at-breakout", base), ("vs placebo", placebo)):
                    mean, t, n = compare(rule, other)
                    hurdle = small_sample_hurdle(TRIALS, max(3, n))
                    ok = mean > 0 and t is not None and t > hurdle
                    if cost == 10:
                        passed[f"{arm}|{label}|{recovery}"] = ok
                    shown = "--" if t is None else f"{t:+.2f}"
                    print(
                        f"  {arm:<17}{label:<16}{mean:+.4f}R  t {shown:>6}  "
                        f"({n} blocks, hurdle {hurdle:.3f})  {'PASS' if ok else 'FAIL'}"
                    )
    print(f"\n{'=' * 78}")
    for arm in ("retest_confirmed", "confirmed"):
        keys = [k for k in passed if k.startswith(arm)]
        verdict = all(passed[k] for k in keys) if keys else False
        print(
            f"{arm}: {'PASSES' if verdict else 'FAILS'} "
            f"({sum(passed[k] for k in keys)}/{len(keys)} comparisons)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
