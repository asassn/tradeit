#!/usr/bin/env python
"""Judge the low-volatility re-test on 2020-2025 exactly as registered.

``docs/prereg/LOW_VOLATILITY_2020_2025_2026-09-18.md`` at ``e12ec7e``. The
order is the registration's:

1. **Calibration first.** If the within-date permutation shows the standard error
   too narrow, the run is void and the hypothesis is never printed.
2. **The 5% undetermined-volume rule** decides whether it is judged once or must
   pass twice.
3. **The four criteria.**
4. **The declared diagnostics last**, labelled as deciding nothing, so they
   cannot be read before the verdict or substituted for it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_research_relative_strength_verdict import QUANTILE, _geometric
from signal_verdict_adx import PERMUTATIONS, SEED, Panel, _groups, _ic

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.cross_section import DAYS_PER_SESSION, MIN_PER_DATE, cross_sectional_ic

#: All registered.
TRIALS = 101
HORIZON = 63
FLOOR = 1_000_000.0
DIAGNOSTIC_FLOOR = 10_000_000.0
SPLIT = dt.date(2023, 1, 1)
CALIBRATION_LIMIT = 2.3
UNDETERMINED_BOUND = 0.05
BANDS = 5
BANDS_REQUIRED = 4


def _load(paths: list[str]) -> dict[str, np.ndarray]:
    import csv

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
        "rv60": floats("realized_volatility_60"),
        "price": floats("raw_close"),
        "adv": floats("avg_dollar_volume_20"),
        "unsure": np.array([v == "1" for v in columns["volume_undetermined"]]),
        "forward": floats("forward"),
    }


def _calibration(panel: Panel) -> float:
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
    return float(np.percentile(ts, 95))


def _geometric_edge(panel: Panel) -> tuple[float, float, int]:
    """Criterion 2: the least-volatile quintile's geometric return over its date's."""
    per_date: list[tuple[dt.date, float]] = []
    for rows in _groups(panel.dates):
        if rows.shape[0] < MIN_PER_DATE:
            continue
        order = rows[np.argsort(panel.signal[rows], kind="mergesort")]
        calm = order[: max(1, int(order.shape[0] * QUANTILE))]
        edge = _geometric(panel.outcome[calm]) - _geometric(panel.outcome[rows])
        per_date.append((panel.dates[rows[0]], edge))
    origin = per_date[0][0]
    width = max(1, round(HORIZON * DAYS_PER_SESSION))
    blocks: dict[int, list[float]] = {}
    for date, edge in per_date:
        blocks.setdefault((date - origin).days // width, []).append(edge)
    block_mean = float(np.mean([np.mean(v) for v in blocks.values()]))
    return block_mean, float(np.median([e for _, e in per_date])), len(per_date)


def _price_bands(panel: Panel) -> list[np.ndarray]:
    """Criterion 4: each date's own raw-price quintiles (``volatility`` holds price)."""
    band = np.full(panel.dates.shape[0], -1)
    for rows in _groups(panel.dates):
        if rows.shape[0] < BANDS:
            continue
        order = rows[np.argsort(panel.volatility[rows], kind="mergesort")]
        band[order] = (np.arange(order.shape[0]) * BANDS) // order.shape[0]
    return [band == b for b in range(BANDS)]


def _judge(label: str, panel: Panel, hurdle: float) -> bool:
    print(f"\n-- realized_volatility_60, {label}: {panel.dates.shape[0]:,} observations --")
    whole = _ic(panel)
    assert whole is not None and whole.t is not None
    c1 = whole.ic < 0 and whole.t < -hurdle
    print(
        f"  1  block t < -{hurdle:.4f}, IC negative:  IC {whole.ic:+.4f}  t {whole.t:+.2f}  "
        f"({whole.blocks} blocks, {whole.dates} dates, median breadth "
        f"{whole.median_breadth:.0f})  {'PASS' if c1 else 'FAIL'}"
    )
    print(f"     smallest detectable IC at this hurdle: {whole.detectable(hurdle):.4f}")

    block_mean, median, n = _geometric_edge(panel)
    c2 = block_mean > 0 and median > 0
    turns = 252 / HORIZON
    print(
        f"  2  least-volatile quintile compounds ahead of its date's universe:  "
        f"block mean {block_mean:+.3%}/hold ({(1 + block_mean) ** turns - 1:+.2%}/yr)  "
        f"median {median:+.3%}  ({n} dates)  {'PASS' if c2 else 'FAIL'}"
    )

    halves = [_ic(panel.where(panel.dates < SPLIT)), _ic(panel.where(panel.dates >= SPLIT))]
    c3 = all(h is not None and h.ic < 0 for h in halves)
    shown = ", ".join(
        "--" if h is None else f"{h.ic:+.4f} (t {'--' if h.t is None else f'{h.t:+.2f}'})"
        for h in halves
    )
    print(f"  3  IC negative in 2020-22 and 2023-25:  {shown}  {'PASS' if c3 else 'FAIL'}")

    band_ics = [_ic(panel.where(mask)) for mask in _price_bands(panel)]
    negative = sum(1 for b in band_ics if b is not None and b.ic < 0)
    c4 = negative >= BANDS_REQUIRED
    shown = ", ".join("--" if b is None else f"{b.ic:+.4f}" for b in band_ics)
    print(
        f"  4  IC negative in >= {BANDS_REQUIRED} of {BANDS} within-date PRICE bands "
        f"(cheapest first):  {shown}  -> {negative}/{BANDS}  {'PASS' if c4 else 'FAIL'}"
    )
    passed = c1 and c2 and c3 and c4
    print(f"  -> {'PASSES' if passed else 'FAILS'} ({label})")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="comma-separated scan CSVs")
    args = ap.parse_args()

    data = _load([p.strip() for p in args.points.split(",")])
    hurdle = expected_max_of_normals(TRIALS)
    usable = np.isfinite(data["forward"]) & np.isfinite(data["rv60"]) & np.isfinite(data["price"])
    adv = np.nan_to_num(data["adv"])
    print(
        f"{data['dates'].shape[0]:,} scanned observations; hurdle |t| > {hurdle:.4f} "
        f"at {TRIALS} trials; direction declared NEGATIVE"
    )

    def panel(mask: np.ndarray) -> Panel:
        return Panel(
            data["dates"][mask], data["rv60"][mask], data["forward"][mask], data["price"][mask]
        )

    floored = usable & (adv >= FLOOR)
    unsure_share = float(data["unsure"][floored].mean())
    excluded = floored & ~data["unsure"]

    p95 = _calibration(panel(excluded))
    state = "CALIBRATED" if p95 <= CALIBRATION_LIMIT else "MISCALIBRATED"
    print(
        f"\nCALIBRATION  shuffled within date x{PERMUTATIONS}: 95th percentile |t| {p95:.2f} "
        f"(limit {CALIBRATION_LIMIT})  -> {state}"
    )
    if p95 > CALIBRATION_LIMIT:
        print("\nRUN VOID. The standard error is too narrow on this panel; nothing is printed.")
        return 1

    print(
        f"\nUndetermined volume: {unsure_share:.1%} of floored observations "
        f"(bound {UNDETERMINED_BOUND:.0%})"
    )
    verdicts = [_judge("undetermined volume EXCLUDED", panel(excluded), hurdle)]
    if unsure_share > UNDETERMINED_BOUND:
        print("  above the bound: it must pass with them included as well")
        verdicts.append(_judge("undetermined volume INCLUDED", panel(floored), hurdle))

    print(f"\n{'=' * 78}")
    if all(verdicts):
        print(
            "LOW VOLATILITY PASSES out of sample among tradeable securities.\n"
            "Licenses a scoped proposition for it as a candidate factor -- nothing more."
        )
    else:
        print(
            "LOW VOLATILITY FAILS. The registered stop rule applies: the line is closed on\n"
            "this corpus for good, and §27's closure is restored with this as its reason."
        )

    print("\n-- declared diagnostics; they decide nothing --")
    for label, mask in (
        ("no floor", usable),
        (
            f"above ${DIAGNOSTIC_FLOOR / 1e6:.0f}M/day",
            usable & (adv >= DIAGNOSTIC_FLOOR) & ~data["unsure"],
        ),
    ):
        reading = _ic(panel(mask))
        if reading is not None and reading.t is not None:
            print(
                f"  {label:<18} IC {reading.ic:+.4f}  t {reading.t:+.2f}  obs {int(mask.sum()):,}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
