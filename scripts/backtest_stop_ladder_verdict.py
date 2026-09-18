#!/usr/bin/env python
"""Judge the stop-ladder test exactly as registered: two-sided, five criteria each way.

``docs/prereg/STOP_LADDER_2026-09-18.md`` at ``dc3499a``, Amendment 1 alongside
the engine fix. Reads the PRIMARY window's four result files; the 2020-2025
diagnostic and every other number are printed after the verdict and decide
nothing.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import expected_max_of_normals

TRIALS = 104
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


def _load(window: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for k in range(4):
        path = OUT / f"stopladder_{window}_s{k}.csv"
        if path.exists():
            with path.open() as handle:
                rows.extend(csv.DictReader(handle))
    return rows


def _cell(rows: list[dict[str, str]], costs: str, recovery: str) -> dict[tuple[int, str], dict]:
    return {
        (int(r["sample"]), r["arm"]): r
        for r in rows
        if r["costs"] == costs and r["recovery"] == recovery
    }


def _diffs(cell: dict[tuple[int, str], dict], field: str) -> list[float]:
    samples = sorted({s for s, _ in cell})
    return [float(cell[(s, "ladder")][field]) - float(cell[(s, "hold")][field]) for s in samples]


def main() -> int:
    rows = _load("primary")
    primary = _cell(rows, "base", "1.0")
    stress = _cell(rows, "stress", "1.0")
    if len(primary) != 8 or len(stress) != 8:
        print(f"incomplete: {len(primary)} primary, {len(stress)} stress rows")
        return 2
    hurdle = expected_max_of_normals(TRIALS)
    samples = sorted({s for s, _ in primary})

    print(f"2010-2019; two-sided hurdle |t| > {hurdle:.4f} at {TRIALS} trials\n")
    print(f"{'sample':<8}{'LADDER CAGR':>13}{'HOLD CAGR':>11}{'paired':>9}")
    for s in samples:
        ladder, hold = float(primary[(s, "ladder")]["cagr"]), float(primary[(s, "hold")]["cagr"])
        print(f"{s:<8}{ladder:>13.2%}{hold:>11.2%}{ladder - hold:>+9.2%}")

    cagr = _diffs(primary, "cagr")
    mean, sd = statistics.mean(cagr), statistics.stdev(cagr)
    t = mean / (sd / math.sqrt(len(cagr))) if sd > 0 else math.copysign(math.inf, mean)
    sharpe = _diffs(primary, "sharpe")
    first = statistics.mean(_diffs(primary, "first_half_annualised"))
    second = statistics.mean(_diffs(primary, "second_half_annualised"))
    stressed = _diffs(stress, "cagr")

    def verdict(sign: int) -> list[bool]:
        return [
            all(sign * d > 0 for d in cagr),
            sign * t > hurdle,
            sum(1 for d in sharpe if sign * d > 0) >= 3,
            sign * first > 0 and sign * second > 0,
            all(sign * d > 0 for d in stressed),
        ]

    adds, costs = verdict(+1), verdict(-1)
    print(
        f"\n  1  LADDER ahead in {sum(d > 0 for d in cagr)}/4 samples, HOLD in "
        f"{sum(d < 0 for d in cagr)}/4\n"
        f"  2  mean paired CAGR {mean:+.2%}, sd {sd:.2%}, t {t:+.2f}\n"
        f"  3  LADDER's Sharpe ahead in {sum(d > 0 for d in sharpe)}/4\n"
        f"  4  walk-forward: 2010-14 {first:+.2%}, 2015-19 {second:+.2%}\n"
        f"  5  at 5x costs, LADDER ahead in {sum(d > 0 for d in stressed)}/4\n"
    )
    print(f"  'the ladder adds value'  criteria met: {sum(adds)}/5  {adds}")
    print(f"  'the ladder costs money' criteria met: {sum(costs)}/5  {costs}")
    print(f"\n{'=' * 78}")
    if all(adds):
        print("THE LADDER ADDS VALUE. The default exits are confirmed as measured.")
    elif all(costs):
        print(
            "THE LADDER COSTS MONEY. Licenses a scoped proposition to revisit the exit\n"
            "defaults, put to the owner. No parameter moves on a measurement."
        )
    else:
        print("INCONCLUSIVE. Neither direction meets all five of its criteria.")

    print("\n-- diagnostics; they decide nothing --")
    for label, window, costs_, recovery in (
        ("2010-19, base, recovery 1.0", "primary", "base", "1.0"),
        ("2010-19, 5x costs, recovery 1.0", "primary", "stress", "1.0"),
        ("2010-19, base, recovery 0.0", "primary", "base", "0.0"),
        ("2020-25, base, recovery 1.0 (the data that suggested it)", "diagnostic", "base", "1.0"),
    ):
        cell = _cell(_load(window), costs_, recovery)
        if len(cell) != 8:
            continue
        print(f"  {label}")
        for arm in ("ladder", "hold"):
            picked = [cell[(s, arm)] for s in sorted({s for s, _ in cell})]

            def avg(key: str, rows: list[dict] = picked) -> float:
                return statistics.mean(float(r[key] or "nan") for r in rows)

            print(
                f"    {arm:<7} CAGR {avg('cagr'):>+7.2%}  max DD {avg('max_drawdown'):>7.2%}  "
                f"Sharpe {avg('sharpe'):>6.2f}  trades {avg('trades'):>5.0f}  "
                f"stop-loss {avg('stop_loss'):>4.0f}  trailing {avg('trailing_stop'):>4.0f}  "
                f"partial {avg('partial_profit'):>4.0f}  delisted {avg('delisted_exit'):>3.0f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
