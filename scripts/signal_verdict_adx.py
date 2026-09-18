#!/usr/bin/env python
"""Judge the `adx_14` confirmation exactly as registered.

``docs/prereg/ADX_CONFIRMATION_2026-09-18.md`` at ``324e26b``, Amendment 1 at
``f87a15c``. This transcribes it as code, in the order the registration states
it, and adds nothing:

1. **The two machinery checks come first.** The positive control and the
   permutation calibration are judged before `adx_14` is printed, and either
   failing voids the run -- so neither can be reconsidered in the light of a
   result it would void.
2. **Amendment 1's bound** decides whether `adx_14` is judged once (undetermined
   volume excluded) or twice (excluded and included, both required to pass).
3. **The four criteria**, each in the registration's own words beside its code.

The estimator is :func:`tradeit.signals.cross_section.cross_sectional_ic`. Its
rank function was vectorised after registration and before this run; the change
was verified identical to brute-force average ranks and alters no number.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.cross_section import (
    DAYS_PER_SESSION,
    MIN_PER_DATE,
    cross_sectional_ic,
)

#: All registered.
TRIALS = 100
HORIZON = 63
FLOOR = 1_000_000.0
SPLIT = dt.date(2015, 1, 1)
PERMUTATIONS = 200
SEED = 20260918
CALIBRATION_LIMIT = 2.3
UNDETERMINED_BOUND = 0.05
BANDS = 5
BANDS_REQUIRED = 4


@dataclass(frozen=True)
class Panel:
    dates: np.ndarray
    signal: np.ndarray
    outcome: np.ndarray
    volatility: np.ndarray

    def where(self, mask: np.ndarray) -> Panel:
        return Panel(self.dates[mask], self.signal[mask], self.outcome[mask], self.volatility[mask])


def _load(paths: list[str]) -> dict[str, np.ndarray]:
    columns: dict[str, list[str]] = {}
    for path in paths:
        with open(path) as handle:
            for row in csv.DictReader(handle):
                for key, value in row.items():
                    columns.setdefault(key, []).append(value)

    def floats(name: str) -> np.ndarray:
        return np.array([float(v) if v else np.nan for v in columns[name]])

    return {
        "dates": np.array([dt.date.fromisoformat(v) for v in columns["session_date"]]),
        "adx": floats("adx_14"),
        "rv60": floats("realized_volatility_60"),
        "atr": floats("atr_percent"),
        "adv": floats("avg_dollar_volume_20"),
        "unsure": np.array([v == "1" for v in columns["volume_undetermined"]]),
        "forward": floats("forward"),
    }


def _groups(dates: np.ndarray) -> list[np.ndarray]:
    order = np.argsort(dates, kind="mergesort")
    ordered = dates[order]
    cuts = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
    return np.split(order, cuts)


def _ic(panel: Panel):  # type: ignore[no-untyped-def]
    return cross_sectional_ic(panel.signal, panel.outcome, list(panel.dates), HORIZON)


def _quintile_spread(panel: Panel) -> tuple[float, float, int]:
    """Criterion 2: top minus bottom quintile on each date; block mean and median."""
    per_date: list[tuple[dt.date, float]] = []
    for rows in _groups(panel.dates):
        if rows.shape[0] < MIN_PER_DATE:
            continue
        order = rows[np.argsort(panel.signal[rows], kind="mergesort")]
        fifth = order.shape[0] // 5
        top = panel.outcome[order[-fifth:]].mean()
        bottom = panel.outcome[order[:fifth]].mean()
        per_date.append((panel.dates[rows[0]], float(top - bottom)))
    origin = per_date[0][0]
    width = max(1, round(HORIZON * DAYS_PER_SESSION))
    blocks: dict[int, list[float]] = {}
    for date, spread in per_date:
        blocks.setdefault((date - origin).days // width, []).append(spread)
    block_mean = float(np.mean([np.mean(v) for v in blocks.values()]))
    return block_mean, float(np.median([s for _, s in per_date])), len(per_date)


def _bands_within_date(panel: Panel) -> list[np.ndarray]:
    """Criterion 4's bands: each date's own atr_percent quintiles."""
    band = np.full(panel.dates.shape[0], -1)
    for rows in _groups(panel.dates):
        if rows.shape[0] < BANDS:
            continue
        order = rows[np.argsort(panel.volatility[rows], kind="mergesort")]
        band[order] = (np.arange(order.shape[0]) * BANDS) // order.shape[0]
    return [band == b for b in range(BANDS)]


def _calibration(panel: Panel, hurdle: float) -> tuple[float, float]:
    """95th percentile of |t| with the signal shuffled within each date."""
    rng = np.random.default_rng(SEED)
    groups = _groups(panel.dates)
    ts: list[float] = []
    for _ in range(PERMUTATIONS):
        shuffled = panel.signal.copy()
        for rows in groups:
            shuffled[rows] = panel.signal[rng.permutation(rows)]
        result = cross_sectional_ic(shuffled, panel.outcome, list(panel.dates), HORIZON)
        if result is not None and result.t is not None:
            ts.append(abs(result.t))
    values = np.array(ts)
    return float(np.percentile(values, 95)), float((values > hurdle).mean())


def _judge(label: str, panel: Panel, hurdle: float) -> bool:
    print(f"\n-- adx_14, {label}: {panel.dates.shape[0]:,} observations --")
    whole = _ic(panel)
    assert whole is not None and whole.t is not None
    c1 = whole.ic > 0 and whole.t > hurdle
    print(
        f"  1  block t > {hurdle:.4f}, IC positive:  IC {whole.ic:+.4f}  t {whole.t:+.2f}  "
        f"({whole.blocks} blocks, {whole.dates} dates, median breadth "
        f"{whole.median_breadth:.0f})  {'PASS' if c1 else 'FAIL'}"
    )
    print(f"     smallest detectable IC at this hurdle: {whole.detectable(hurdle):.4f}")

    block_mean, median, n = _quintile_spread(panel)
    c2 = block_mean > 0 and median > 0
    print(
        f"  2  quintile spread positive by block mean AND by median across dates:  "
        f"block mean {block_mean:+.3%}  median {median:+.3%}  ({n} dates)  "
        f"{'PASS' if c2 else 'FAIL'}"
    )

    halves = [
        _ic(panel.where(panel.dates < SPLIT)),
        _ic(panel.where(panel.dates >= SPLIT)),
    ]
    c3 = all(h is not None and h.ic > 0 for h in halves)
    shown = ", ".join(
        "--" if h is None else f"{h.ic:+.4f} (t {'--' if h.t is None else f'{h.t:+.2f}'})"
        for h in halves
    )
    print(f"  3  IC positive in 2010-14 and 2015-19:  {shown}  {'PASS' if c3 else 'FAIL'}")

    band_ics = [_ic(panel.where(mask)) for mask in _bands_within_date(panel)]
    positive = sum(1 for b in band_ics if b is not None and b.ic > 0)
    c4 = positive >= BANDS_REQUIRED
    shown = ", ".join("--" if b is None else f"{b.ic:+.4f}" for b in band_ics)
    print(
        f"  4  IC positive in >= {BANDS_REQUIRED} of {BANDS} within-date atr% bands "
        f"(least volatile first):  {shown}  -> {positive}/{BANDS}  {'PASS' if c4 else 'FAIL'}"
    )
    confirmed = c1 and c2 and c3 and c4
    print(f"  -> {'CONFIRMED' if confirmed else 'NOT CONFIRMED'} ({label})")
    return confirmed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="comma-separated scan CSVs")
    args = ap.parse_args()

    data = _load([p.strip() for p in args.points.split(",")])
    hurdle = expected_max_of_normals(TRIALS)
    finite = np.isfinite(data["forward"]) & np.isfinite(data["atr"])
    print(
        f"{data['dates'].shape[0]:,} scanned observations; hurdle |t| > {hurdle:.4f} "
        f"at {TRIALS} trials"
    )

    # -- machinery check 1: the positive control, unfloored -------------------
    control_mask = finite & np.isfinite(data["rv60"])
    control = Panel(
        data["dates"][control_mask],
        data["rv60"][control_mask],
        data["forward"][control_mask],
        data["atr"][control_mask],
    )
    reading = _ic(control)
    assert reading is not None and reading.t is not None
    control_ok = reading.ic < 0 and abs(reading.t) > hurdle
    print(
        f"\nPOSITIVE CONTROL  realized_volatility_60, no floor, {control_mask.sum():,} obs:  "
        f"IC {reading.ic:+.4f}  t {reading.t:+.2f}  median breadth "
        f"{reading.median_breadth:.0f}  -> {'PASSES' if control_ok else 'FAILS'}"
    )

    # -- the adx_14 panels, per Amendment 1 -----------------------------------
    floored = finite & np.isfinite(data["adx"]) & (np.nan_to_num(data["adv"]) >= FLOOR)
    unsure_share = float(data["unsure"][floored].mean())
    excluded = floored & ~data["unsure"]

    def panel(mask: np.ndarray) -> Panel:
        return Panel(
            data["dates"][mask], data["adx"][mask], data["forward"][mask], data["atr"][mask]
        )

    # -- machinery check 2: calibration on the panel adx_14 is judged on ------
    p95, beyond = _calibration(panel(excluded), hurdle)
    calibrated = p95 <= CALIBRATION_LIMIT
    print(
        f"CALIBRATION  adx_14 shuffled within date x{PERMUTATIONS}:  95th percentile |t| "
        f"{p95:.2f} (limit {CALIBRATION_LIMIT}); share beyond the hurdle {beyond:.1%}  "
        f"-> {'CALIBRATED' if calibrated else 'MISCALIBRATED'}"
    )

    if not (control_ok and calibrated):
        print(
            "\nRUN VOID. A machinery check failed, and the registration says no reading "
            "of adx_14 from this run may be believed. adx_14 is not printed."
        )
        return 1

    print(
        f"\nAmendment 1: {unsure_share:.1%} of floored observations rest on an UNDETERMINED "
        f"volume basis (bound {UNDETERMINED_BOUND:.0%})"
    )
    verdicts = [_judge("undetermined volume EXCLUDED", panel(excluded), hurdle)]
    if unsure_share > UNDETERMINED_BOUND:
        print("  above the bound: confirmation must hold with them included as well")
        verdicts.append(_judge("undetermined volume INCLUDED", panel(floored), hurdle))

    print(f"\n{'=' * 78}")
    if all(verdicts):
        print("adx_14 CONFIRMED out of sample. Licenses a scoped proposition, nothing more.")
    else:
        print(
            "adx_14 NOT CONFIRMED. The registered stop rule applies: the lead is closed,\n"
            "and no ADX variant is tried on this data."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
