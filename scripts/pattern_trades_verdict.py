#!/usr/bin/env python
"""Judge the pattern-breakout rule exactly as registered.

``docs/prereg/PATTERN_BREAKOUT_TRADES_2026-09-19.md`` at ``d94a971``. The
machinery check is judged first: if the placebo does not reproduce the market
it is not a control, and nothing about the rule is printed.

**The statistic is the paired difference within a calendar block.** Trades
overlap -- hundreds are open on any day and share the market's moves -- so a
t-statistic over trades would count one decade as 148,000 independent facts.
Each 63-session block contributes one number, the mean of (rule - placebo) over
the trades entered in it, and the t comes from those block means with a
Newey-West lag-1 correction for the overlap neighbouring blocks still share.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import small_sample_hurdle
from tradeit.signals.cross_section import DAYS_PER_SESSION

TRIALS = 130
HOLD = 63
SPLIT = dt.date(2015, 1, 1)
QUALITY_SPLIT = 58.5
#: Amendment 1's machinery check: the placebo must look like the market.
PLACEBO_STOP_RATE = (0.15, 0.65)
PLACEBO_MIN_HOLD = 30.0
PLACEBO_RETURN = (0.0, 0.06)


def load(paths: list[Path]) -> dict[int, dict[str, dict[str, str]]]:
    pairs: dict[int, dict[str, dict[str, str]]] = defaultdict(dict)
    for path in paths:
        with path.open() as handle:
            for row in csv.DictReader(handle):
                pairs[int(row["pair_id"])][row["leg"]] = row
    return {k: v for k, v in pairs.items() if "rule" in v and "placebo" in v}


def blocks_of(dates: list[dt.date]) -> np.ndarray:
    origin = min(dates)
    width = max(1, round(HOLD * DAYS_PER_SESSION))
    return np.array([(d - origin).days // width for d in dates])


def block_statistics(values: np.ndarray, block: np.ndarray) -> tuple[float, float | None, int]:
    """Mean of the block means, and a Newey-West lag-1 t from them."""
    means = np.array([values[block == b].mean() for b in np.unique(block)])
    n = means.shape[0]
    if n < 3:
        return float(means.mean()) if n else float("nan"), None, n
    centred = means - means.mean()
    variance = float(np.dot(centred, centred)) / n
    if variance == 0:
        return float(means.mean()), None, n
    lag1 = float(np.dot(centred[1:], centred[:-1])) / n
    inflation = max(1.0, 1.0 + lag1 / variance)
    standard_error = float(np.sqrt(variance * inflation / (n - 1)))
    if standard_error == 0:
        return float(means.mean()), None, n
    return float(means.mean()), float(means.mean() / standard_error), n


def report(
    label: str, difference: np.ndarray, block: np.ndarray, hurdle: float | None = None
) -> bool:
    mean, t, n = block_statistics(difference, block)
    hurdle = small_sample_hurdle(TRIALS, n) if hurdle is None else hurdle
    passed = mean > 0 and t is not None and t > hurdle
    shown = "--" if t is None else f"{t:+.2f}"
    print(
        f"    {label:<34} {mean:+.4f}R  t {shown:>6}  ({n} blocks, hurdle {hurdle:.3f})  "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    args = ap.parse_args()
    pairs = load([Path(p.strip()) for p in args.pairs.split(",")])
    print(f"{len(pairs):,} complete pairs")

    dates = [dt.date.fromisoformat(v["rule"]["entry_date"]) for v in pairs.values()]
    block = blocks_of(dates)
    years = np.array([d.year for d in dates])
    quality = np.array([float(v["rule"]["quality"]) for v in pairs.values()])
    fraction = np.array([float(v["rule"]["stop_fraction"]) for v in pairs.values()])
    reason = np.array([v["rule"]["exit_reason"] for v in pairs.values()])
    placebo_reason = np.array([v["placebo"]["exit_reason"] for v in pairs.values()])
    placebo_held = np.array([float(v["placebo"]["bars_held"]) for v in pairs.values()])
    print(
        f"  {min(dates)} .. {max(dates)}; exits: "
        + ", ".join(f"{r} {(reason == r).mean():.1%}" for r in sorted(set(reason)))
    )

    def column(leg: str, cost: int, recovery: int) -> np.ndarray:
        return np.array([float(v[leg][f"r_c{cost}_rec{recovery}"]) for v in pairs.values()])

    passed_all = True
    for recovery in (1, 0):
        print(f"\n{'=' * 78}\nRECOVERY {recovery}.0")
        rule, placebo = column("rule", 10, recovery), column("placebo", 10, recovery)
        # -- the machinery check, first --
        placebo_mean, _, _ = block_statistics(placebo, block)
        stop_rate = float((placebo_reason == "stop").mean())
        held = float(placebo_held.mean())
        market = float(np.mean(placebo * fraction))
        checks = (
            PLACEBO_STOP_RATE[0] <= stop_rate <= PLACEBO_STOP_RATE[1],
            held >= PLACEBO_MIN_HOLD,
            PLACEBO_RETURN[0] <= market <= PLACEBO_RETURN[1],
        )
        print(
            f"  machinery (Amendment 1): placebo stopped {stop_rate:.1%} "
            f"{'OK' if checks[0] else 'FAIL'}, held {held:.1f} sessions "
            f"{'OK' if checks[1] else 'FAIL'}, {market:+.2%}/trade "
            f"{'OK' if checks[2] else 'FAIL'}   ({placebo_mean:+.4f}R)"
        )
        if not all(checks):
            print("  VOID -- the placebo is not the market; no rule statistic is printed.")
            passed_all = False
            continue

        print("  criteria:")
        difference = rule - placebo
        c1 = report("1 rule - placebo, 10 bps", difference, block)
        c2 = report("2 rule alone, 10 bps", rule, block)
        halves = []
        for label, mask in (
            ("2010-2014", np.array(dates) < SPLIT),
            ("2015-2019", np.array(dates) >= SPLIT),
        ):
            halves.append(report(f"3 {label}", difference[mask], block[mask]))
        c3 = all(halves)
        worst_year = max(set(years), key=lambda y: float(difference[years == y].mean()))
        keep = years != worst_year
        c4 = report(f"4 without {worst_year}", difference[keep], block[keep])
        c5 = report(
            "5 double costs (20 bps)",
            column("rule", 20, recovery) - column("placebo", 20, recovery),
            block,
        )
        ok = c1 and c2 and c3 and c4 and c5
        passed_all = passed_all and ok
        print(f"  -> recovery {recovery}.0: {'PASSES' if ok else 'FAILS'}")

        high, low = quality >= QUALITY_SPLIT, quality < QUALITY_SPLIT
        if high.any() and low.any():
            top, _, _ = block_statistics(difference[high], block[high])
            bottom, _, _ = block_statistics(difference[low], block[low])
            print(f"    quality gradient: top half {top:+.4f}R, bottom half {bottom:+.4f}R")

    print(f"\n{'=' * 78}")
    if passed_all:
        print(
            "PASSES. Licenses ONE portfolio registration: the rule through the platform's\n"
            "own engine against the random-selection benchmark of §37/§38. Nothing else."
        )
    else:
        print(
            "FAILS. The stop rule applies: pattern breakouts as single-pattern trade rules\n"
            "are closed on this corpus -- no other exit, target, trailing multiple, quality\n"
            "threshold or stop band is tried on daily bars."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
