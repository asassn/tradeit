#!/usr/bin/env python
"""Judge the four fundamental signals exactly as registered.

``docs/prereg/FUNDAMENTALS_2026-09-18.md`` at ``adc037d``. For each signal, in
the registration's order: calibration first (a failure voids that signal's run
and its reading is not printed), the 5% undetermined-volume rule, then the four
criteria in the declared direction.

The helpers are the ones §34 and §35 were judged with, reused rather than
rewritten, so every signal on this scoreboard is judged by one arithmetic.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_verdict_adx import Panel, _bands_within_date, _ic
from signal_verdict_lowvol import _calibration, _geometric_edge

from tradeit.backtesting.overfitting import expected_max_of_normals

TRIALS = 108
FLOOR = 1_000_000.0
MIN_PRICE = 5.0
SPLIT = dt.date(2019, 1, 1)
CALIBRATION_LIMIT = 2.3
UNDETERMINED_BOUND = 0.05
BANDS_REQUIRED = 4
#: Declared in the registration: +1, higher is better; -1, lower is better.
DECLARED = {"sue": +1, "gross_profitability": +1, "asset_growth": -1, "accruals": -1}
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


def _load(paths: list[Path]) -> dict[str, np.ndarray]:
    columns: dict[str, list[str]] = {}
    for path in paths:
        with path.open() as handle:
            for row in csv.DictReader(handle):
                for key, value in row.items():
                    columns.setdefault(key, []).append(value)

    def floats(name: str) -> np.ndarray:
        return np.array([float(v) if v else np.nan for v in columns[name]])

    out = {name: floats(name) for name in (*DECLARED, "realized_volatility_60", "raw_close")}
    out["adv"] = floats("avg_dollar_volume_20")
    out["forward"] = floats("forward")
    out["dates"] = np.array([dt.date.fromisoformat(v) for v in columns["session_date"]])
    out["unsure"] = np.array([v == "1" for v in columns["volume_undetermined"]])
    return out


def _judge(name: str, sign: int, panel: Panel, hurdle: float) -> bool:
    """The four criteria. ``panel.signal`` is already oriented so LOWER is favoured."""
    whole = _ic(panel)
    assert whole is not None and whole.t is not None
    # The panel holds -sign * value, so its IC is -sign times the signal's own.
    # Undo it with the same factor. A first version undid it with a bare minus,
    # right only for signals declared POSITIVE: the synthetic test planted a
    # negative asset-growth effect and watched it read +0.2151 and fail.
    ic, t = -sign * whole.ic, -sign * whole.t
    c1 = sign * ic > 0 and sign * t > hurdle
    print(
        f"  1  IC {ic:+.4f}  t {t:+.2f} (declared {'+' if sign > 0 else '-'}; hurdle {hurdle:.4f})"
        f"  {whole.blocks} blocks, median breadth {whole.median_breadth:.0f}  "
        f"{'PASS' if c1 else 'FAIL'}"
    )
    print(f"     smallest detectable IC: {whole.detectable(hurdle):.4f}")
    block_mean, median, n = _geometric_edge(panel)
    c2 = block_mean > 0 and median > 0
    print(
        f"  2  favoured fifth compounds ahead of its date's universe: block mean "
        f"{block_mean:+.3%}/hold ({(1 + block_mean) ** 4 - 1:+.2%}/yr), median {median:+.3%}"
        f"  ({n} dates)  {'PASS' if c2 else 'FAIL'}"
    )
    halves = [_ic(panel.where(panel.dates < SPLIT)), _ic(panel.where(panel.dates >= SPLIT))]
    c3 = all(h is not None and sign * (-sign * h.ic) > 0 for h in halves)
    shown = ", ".join("--" if h is None else f"{-sign * h.ic:+.4f}" for h in halves)
    print(f"  3  IC in 2013-18 and 2019-25: {shown}  {'PASS' if c3 else 'FAIL'}")
    bands = [_ic(panel.where(mask)) for mask in _bands_within_date(panel)]
    held = sum(1 for b in bands if b is not None and sign * (-sign * b.ic) > 0)
    c4 = held >= BANDS_REQUIRED
    shown = ", ".join("--" if b is None else f"{-sign * b.ic:+.4f}" for b in bands)
    print(
        f"  4  within-date volatility bands (calmest first): {shown} -> {held}/5 "
        f"{'PASS' if c4 else 'FAIL'}"
    )
    return c1 and c2 and c3 and c4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=",".join(str(OUT / f"fund_s{k}.csv") for k in range(8)))
    args = ap.parse_args()
    data = _load([Path(p.strip()) for p in args.points.split(",")])
    hurdle = expected_max_of_normals(TRIALS)
    base = (
        np.isfinite(data["forward"])
        & np.isfinite(data["realized_volatility_60"])
        & (np.nan_to_num(data["raw_close"]) >= MIN_PRICE)
        & (np.nan_to_num(data["adv"]) >= FLOOR)
    )
    print(f"{data['dates'].shape[0]:,} scanned rows; {int(base.sum()):,} pass the inclusion rules")

    passed: list[str] = []
    for name, sign in DECLARED.items():
        print(f"\n{'=' * 78}\n{name}  (declared {'POSITIVE' if sign > 0 else 'NEGATIVE'})")
        defined = base & np.isfinite(data[name])
        unsure_share = float(data["unsure"][defined].mean()) if defined.any() else 0.0

        def panel(mask: np.ndarray, name: str = name, sign: int = sign) -> Panel:
            # Oriented so the favoured end is the LOW end, which is what the
            # reused geometric-edge helper takes as its favoured fifth.
            return Panel(
                data["dates"][mask],
                -sign * data[name][mask],
                data["forward"][mask],
                data["realized_volatility_60"][mask],
            )

        excluded = defined & ~data["unsure"]
        p95 = _calibration(panel(excluded))
        print(f"  calibration: 95th percentile |t| {p95:.2f} (limit {CALIBRATION_LIMIT})")
        if p95 > CALIBRATION_LIMIT:
            print("  VOID -- the standard error is too narrow for this signal; nothing printed.")
            continue
        print(f"  undetermined volume {unsure_share:.1%} (bound {UNDETERMINED_BOUND:.0%})")
        print(f"  -- undetermined EXCLUDED, {int(excluded.sum()):,} observations --")
        ok = _judge(name, sign, panel(excluded), hurdle)
        if unsure_share > UNDETERMINED_BOUND:
            print(f"  -- undetermined INCLUDED, {int(defined.sum()):,} observations --")
            ok = ok and _judge(name, sign, panel(defined), hurdle)
        print(f"  -> {name}: {'PASSES' if ok else 'FAILS'}")
        if ok:
            passed.append(name)

    print(f"\n{'=' * 78}")
    if passed:
        print(
            f"PASSING: {', '.join(passed)}. Each licenses a separate registration testing it\n"
            "as a portfolio against the random benchmark -- nothing else."
        )
    else:
        print(
            "NONE PASSES. The stop rule applies: these four are closed on this corpus, and\n"
            "no further fundamental signal is tested without a registration saying what it adds."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
