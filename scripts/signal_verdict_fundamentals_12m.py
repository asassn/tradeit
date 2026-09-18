#!/usr/bin/env python
"""Judge the four fundamental signals at twelve months, exactly as registered.

``docs/prereg/FUNDAMENTALS_12M_2026-09-18.md`` at ``9547237``, with Amendment 1
(criterion 2 at recovery 0.0). For each signal, at each recovery: calibration
first (a failure voids the signal and nothing of it is printed), the 5%
undetermined-volume rule, then the five criteria.

**Orientation.** Every panel here holds ``sign * value``, so the favoured end
is the HIGH end for every signal and every IC printed is in the declared
direction: positive supports the registration. §40's verdict held the opposite
orientation and had to undo it with a factor, and the first version undid it
wrongly; holding one orientation throughout removes the step.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from tradeit.backtesting.overfitting import small_sample_hurdle, student_t_quantile
from tradeit.signals.cross_section import (
    DAYS_PER_SESSION,
    MIN_PER_DATE,
    CrossSectionalIC,
    cross_sectional_ic,
)

TRIALS = 112
HORIZON = 252
FLOOR = 1_000_000.0
MIN_PRICE = 5.0
SPLIT = dt.date(2019, 1, 1)
PERMUTATIONS = 200
SEED = 20260918
#: §40 allowed 2.3 against the normal's 1.96; the same ratio over Student-t's.
CALIBRATION_RATIO = 2.3 / 1.96
UNDETERMINED_BOUND = 0.05
BANDS = 5
BANDS_REQUIRED = 4
QUANTILE = 0.2
RECOVERIES = (1.0, 0.0)
DECLARED = {"sue": +1, "gross_profitability": +1, "asset_growth": -1, "accruals": -1}
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


@dataclass(frozen=True)
class Panel:
    dates: np.ndarray
    signal: np.ndarray  # sign * value: HIGH is favoured
    outcome: np.ndarray
    late: np.ndarray  # after the first 63 sessions; NaN where not measurable
    volatility: np.ndarray

    def where(self, mask: np.ndarray) -> Panel:
        return Panel(
            self.dates[mask],
            self.signal[mask],
            self.outcome[mask],
            self.late[mask],
            self.volatility[mask],
        )


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
    out["quarter"] = floats("quarter")
    out["terminal"] = np.array([v == "1" for v in columns["terminal"]])
    out["dates"] = np.array([dt.date.fromisoformat(v) for v in columns["session_date"]])
    out["unsure"] = np.array([v == "1" for v in columns["volume_undetermined"]])
    return out


def priced(data: dict[str, np.ndarray], recovery: float) -> tuple[np.ndarray, np.ndarray]:
    """The outcome and its after-the-first-quarter part at one recovery."""
    outcome = np.where(data["terminal"], (1.0 + data["forward"]) * recovery - 1.0, data["forward"])
    with np.errstate(divide="ignore", invalid="ignore"):
        late = (1.0 + outcome) / (1.0 + data["quarter"]) - 1.0
    return outcome, late


def _groups(dates: np.ndarray) -> list[np.ndarray]:
    order = np.argsort(dates, kind="mergesort")
    ordered = dates[order]
    cuts = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
    return np.split(order, cuts)


def _ic(panel: Panel, outcome: np.ndarray | None = None) -> CrossSectionalIC | None:
    target = panel.outcome if outcome is None else outcome
    return cross_sectional_ic(panel.signal, target, list(panel.dates), HORIZON)


def _geometric(values: np.ndarray) -> float:
    if (values <= -1.0).any():
        raise ValueError("a geometric mean over a total loss is undefined -- Amendment 1")
    return float(np.expm1(np.mean(np.log1p(values))))


def favoured_edge(panel: Panel, geometric: bool) -> tuple[float, float, int]:
    """Criterion 2: the favoured fifth over its date's eligible universe.

    ``geometric`` as registered; otherwise the equal-weight buy-and-hold return
    (the arithmetic mean), which Amendment 1 uses at recovery 0.0.
    """
    mean = _geometric if geometric else (lambda v: float(np.mean(v)))
    per_date: list[tuple[dt.date, float]] = []
    for rows in _groups(panel.dates):
        if rows.shape[0] < MIN_PER_DATE:
            continue
        order = rows[np.argsort(panel.signal[rows], kind="mergesort")]
        favoured = order[-max(1, int(order.shape[0] * QUANTILE)) :]
        per_date.append(
            (panel.dates[rows[0]], mean(panel.outcome[favoured]) - mean(panel.outcome[rows]))
        )
    if not per_date:
        return float("nan"), float("nan"), 0
    origin = per_date[0][0]
    width = max(1, round(HORIZON * DAYS_PER_SESSION))
    blocks: dict[int, list[float]] = {}
    for date, edge in per_date:
        blocks.setdefault((date - origin).days // width, []).append(edge)
    block_mean = float(np.mean([np.mean(v) for v in blocks.values()]))
    return block_mean, float(np.median([e for _, e in per_date])), len(per_date)


def _bands(panel: Panel) -> list[np.ndarray]:
    band = np.full(panel.dates.shape[0], -1)
    for rows in _groups(panel.dates):
        if rows.shape[0] < BANDS:
            continue
        order = rows[np.argsort(panel.volatility[rows], kind="mergesort")]
        band[order] = (np.arange(order.shape[0]) * BANDS) // order.shape[0]
    return [band == b for b in range(BANDS)]


def calibration(panel: Panel) -> float:
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


def judge(panel: Panel, recovery: float) -> bool:
    """The five criteria at one recovery. Every IC is in the declared direction."""
    whole = _ic(panel)
    if whole is None or whole.t is None:
        print("  too few dates or blocks for an estimate -- FAIL")
        return False
    hurdle = small_sample_hurdle(TRIALS, whole.blocks)
    c1 = whole.ic > 0 and whole.t > hurdle
    print(
        f"  1  IC {whole.ic:+.4f}  t {whole.t:+.2f}  (hurdle {hurdle:.4f} at {whole.blocks} "
        f"blocks; median breadth {whole.median_breadth:.0f})  {'PASS' if c1 else 'FAIL'}"
    )
    shown = whole.detectable(hurdle)
    print(f"     smallest detectable IC: {'--' if shown is None else f'{shown:.4f}'}")

    geometric = recovery == 1.0
    block_mean, median, n = favoured_edge(panel, geometric)
    c2 = block_mean > 0 and median > 0
    kind = "geometric" if geometric else "equal-weight buy-and-hold (Amendment 1)"
    print(
        f"  2  favoured fifth over its universe, {kind}: block mean {block_mean:+.2%}/yr, "
        f"median {median:+.2%}  ({n} dates)  {'PASS' if c2 else 'FAIL'}"
    )

    halves = [_ic(panel.where(panel.dates < SPLIT)), _ic(panel.where(panel.dates >= SPLIT))]
    c3 = all(h is not None and h.ic > 0 for h in halves)
    shown_h = ", ".join("--" if h is None else f"{h.ic:+.4f}" for h in halves)
    print(f"  3  IC in 2013-18 and 2019-24: {shown_h}  {'PASS' if c3 else 'FAIL'}")

    bands = [_ic(panel.where(mask)) for mask in _bands(panel)]
    held = sum(1 for b in bands if b is not None and b.ic > 0)
    c4 = held >= BANDS_REQUIRED
    shown_b = ", ".join("--" if b is None else f"{b.ic:+.4f}" for b in bands)
    print(
        f"  4  volatility bands (calmest first): {shown_b} -> {held}/5 {'PASS' if c4 else 'FAIL'}"
    )

    measurable = np.isfinite(panel.late)
    late = _ic(panel.where(measurable), panel.late[measurable])
    c5 = late is not None and late.ic > 0
    print(
        f"  5  IC after the first 63 sessions: "
        f"{'--' if late is None else f'{late.ic:+.4f} (t {late.t:+.2f})'}"
        f"  on {int(measurable.sum()):,} points  {'PASS' if c5 else 'FAIL'}"
    )
    return c1 and c2 and c3 and c4 and c5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=",".join(str(OUT / f"fund12_s{k}.csv") for k in range(8)))
    args = ap.parse_args()
    data = _load([Path(p.strip()) for p in args.points.split(",")])
    base = (
        np.isfinite(data["forward"])
        & np.isfinite(data["realized_volatility_60"])
        & (np.nan_to_num(data["raw_close"]) >= MIN_PRICE)
        & (np.nan_to_num(data["adv"]) >= FLOOR)
    )
    terminal = data["terminal"] & base
    print(
        f"{data['dates'].shape[0]:,} scanned rows; {int(base.sum()):,} pass the inclusion rules; "
        f"{int(terminal.sum()):,} of those ({terminal.sum() / max(1, base.sum()):.1%}) are "
        f"securities that never traded again within the year"
    )

    passed: list[str] = []
    for name, sign in DECLARED.items():
        print(f"\n{'=' * 78}\n{name}  (declared {'POSITIVE' if sign > 0 else 'NEGATIVE'})")
        defined = base & np.isfinite(data[name])
        excluded = defined & ~data["unsure"]
        unsure_share = float(data["unsure"][defined].mean()) if defined.any() else 0.0
        verdicts: list[bool] = []
        for recovery in RECOVERIES:
            outcome, late = priced(data, recovery)

            def panel(
                mask: np.ndarray,
                outcome: np.ndarray = outcome,
                late: np.ndarray = late,
                name: str = name,
                sign: int = sign,
            ) -> Panel:
                return Panel(
                    data["dates"][mask],
                    sign * data[name][mask],
                    outcome[mask],
                    late[mask],
                    data["realized_volatility_60"][mask],
                )

            print(f"\n -- recovery {recovery:.1f} --")
            first = _ic(panel(excluded))
            blocks = first.blocks if first is not None else 2
            limit = CALIBRATION_RATIO * student_t_quantile(0.95, max(1, blocks - 1))
            p95 = calibration(panel(excluded))
            print(f"  calibration: 95th percentile |t| {p95:.2f} (limit {limit:.3f})")
            if p95 > limit:
                print("  VOID -- the standard error is too narrow for this signal.")
                verdicts.append(False)
                continue
            print(f"  undetermined volume {unsure_share:.1%} (bound {UNDETERMINED_BOUND:.0%})")
            print(f"  -- undetermined EXCLUDED, {int(excluded.sum()):,} observations --")
            ok = judge(panel(excluded), recovery)
            if unsure_share > UNDETERMINED_BOUND:
                print(f"  -- undetermined INCLUDED, {int(defined.sum()):,} observations --")
                ok = ok and judge(panel(defined), recovery)
            verdicts.append(ok)
        ok = all(verdicts)
        print(f"\n  -> {name}: {'PASSES' if ok else 'FAILS'} (both recoveries required)")
        if ok:
            passed.append(name)

    print(f"\n{'=' * 78}")
    if passed:
        print(
            f"PASSING: {', '.join(passed)}. Each licenses a separate registration testing it\n"
            "as a Retirement-horizon portfolio against random selection -- nothing else."
        )
    else:
        print(
            "NONE PASSES. The stop rule applies: these four are closed at both horizons on\n"
            "this corpus, and the next fundamental question needs different information."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
