#!/usr/bin/env python
"""Judge the low-volatility portfolio backtest exactly as registered.

``docs/prereg/LOW_VOLATILITY_BACKTEST_2026-09-18.md`` at ``452e59d``. Reads the
four per-sample result files ``backtest_lowvol_tilt.py`` writes and applies the
five criteria to the PRIMARY runs -- default costs, delisting recovery 1.0 --
with the cost-stress runs for criterion 5. Everything else is printed after the
verdict and labelled as deciding nothing.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import expected_max_of_normals

TRIALS = 102
RISK_FREE = 0.03
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


def _load(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open() as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def _pick(rows: list[dict[str, str]], costs: str, recovery: str) -> dict[tuple[int, str], dict]:
    return {
        (int(r["sample"]), r["arm"]): r
        for r in rows
        if r["costs"] == costs and r["recovery"] == recovery
    }


def _paired(cell: dict[tuple[int, str], dict], field: str) -> list[float]:
    samples = sorted({s for s, _ in cell})
    return [float(cell[(s, "calm")][field]) - float(cell[(s, "random")][field]) for s in samples]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results",
        default=",".join(str(OUT / f"lowvol_bt_results_s{k}.csv") for k in range(4)),
    )
    args = ap.parse_args()
    rows = _load([Path(p.strip()) for p in args.results.split(",")])
    hurdle = expected_max_of_normals(TRIALS)

    primary = _pick(rows, "base", "1.0")
    stress = _pick(rows, "stress", "1.0")
    samples = sorted({s for s, _ in primary})
    if len(samples) != 4 or len(primary) != 8 or len(stress) != 8:
        print(f"incomplete: {len(samples)} samples, {len(primary)} primary, {len(stress)} stress")
        return 2

    print(f"hurdle |t| > {hurdle:.4f} at {TRIALS} trials; risk-free {RISK_FREE:.1%}\n")
    print(f"{'sample':<8}{'CALM CAGR':>11}{'RANDOM CAGR':>13}{'paired':>9}")
    for s in samples:
        calm, rnd = float(primary[(s, "calm")]["cagr"]), float(primary[(s, "random")]["cagr"])
        print(f"{s:<8}{calm:>11.2%}{rnd:>13.2%}{calm - rnd:>+9.2%}")

    diffs = _paired(primary, "cagr")
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs)
    t = mean / (sd / math.sqrt(len(diffs))) if sd > 0 else float("inf") * (1 if mean > 0 else -1)

    c1 = all(d > 0 for d in diffs)
    c2 = mean > 0 and t > hurdle
    beats_rf = sum(1 for s in samples if float(primary[(s, "calm")]["cagr"]) > RISK_FREE)
    c3 = beats_rf >= 3
    first = statistics.mean(_paired(primary, "first_half_annualised"))
    second = statistics.mean(_paired(primary, "second_half_annualised"))
    c4 = first > 0 and second > 0
    stress_diffs = _paired(stress, "cagr")
    stress_rf = sum(1 for s in samples if float(stress[(s, "calm")]["cagr"]) > RISK_FREE)
    c5 = all(d > 0 for d in stress_diffs) and stress_rf >= 3

    print()
    print(
        f"  1  CALM beats RANDOM in all four samples: {sum(d > 0 for d in diffs)}/4  "
        f"{'PASS' if c1 else 'FAIL'}"
    )
    print(
        f"  2  mean paired CAGR {mean:+.2%}, sd {sd:.2%}, t {t:+.2f} > {hurdle:.4f}  "
        f"{'PASS' if c2 else 'FAIL'}"
    )
    print(
        f"  3  CALM beats the {RISK_FREE:.0%} risk-free rate in {beats_rf}/4 samples  "
        f"{'PASS' if c3 else 'FAIL'}"
    )
    print(
        f"  4  walk-forward: mean paired annualised return 2020-22 {first:+.2%}, "
        f"2023-25 {second:+.2%}  {'PASS' if c4 else 'FAIL'}"
    )
    print(
        f"  5  at 5x spread and slippage: CALM beats RANDOM in "
        f"{sum(d > 0 for d in stress_diffs)}/4, beats risk-free in {stress_rf}/4  "
        f"{'PASS' if c5 else 'FAIL'}"
    )

    passed = c1 and c2 and c3 and c4 and c5
    print(f"\n{'=' * 78}")
    if passed:
        print(
            "PROFITABLE AS REGISTERED. Licenses a scoped proposition for paper trading\n"
            "(RESEARCH -> ROBUST_BACKTEST) and nothing else. No live parameter moves."
        )
    else:
        print(
            "NOT PROFITABLE AS REGISTERED. The stop rule applies: low volatility stays a\n"
            "measured signal (§35) and does not become a candidate strategy on this corpus."
        )

    print("\n-- diagnostics; they decide nothing --")
    for label, cell in (
        ("base costs, recovery 1.0", primary),
        ("5x costs, recovery 1.0", stress),
        ("base costs, recovery 0.0", _pick(rows, "base", "0.0")),
    ):
        if len(cell) != 8:
            continue
        print(f"  {label}")
        for arm in ("calm", "random"):
            picked = [cell[(s, arm)] for s in samples]
            print(
                f"    {arm:<7} CAGR {statistics.mean(float(r['cagr']) for r in picked):>+7.2%}  "
                f"max DD {statistics.mean(float(r['max_drawdown']) for r in picked):>7.2%}  "
                f"Sharpe {statistics.mean(float(r['sharpe'] or 'nan') for r in picked):>6.2f}  "
                f"trades {statistics.mean(int(r['trades']) for r in picked):>6.0f}  "
                f"exposure {statistics.mean(float(r['exposure']) for r in picked):>6.1%}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
