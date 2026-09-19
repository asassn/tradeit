#!/usr/bin/env python
"""Judge the eight swing indicators at 5 and 10 sessions, exactly as registered.

``docs/prereg/SWING_SHORT_HORIZON_2026-09-19.md`` at ``88816df``. Per horizon:
the positive control and each arm's calibration are judged FIRST -- a control
failure voids the horizon and no arm of it is printed -- then the five criteria
per arm, at recovery 1.0 and 0.0, both required.

Orientation, as in §41: every panel holds ``sign * value``, so the favoured end
is the HIGH end and every IC printed is in the declared direction.
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

from signal_jump_guard import load_jumps, spans_jump

from tradeit.backtesting.overfitting import small_sample_hurdle
from tradeit.signals.cross_section import (
    DAYS_PER_SESSION,
    MIN_PER_DATE,
    CrossSectionalIC,
    cross_sectional_ic,
)

TRIALS = 128
HORIZONS = (5, 10)
FLOOR = 1_000_000.0
MIN_PRICE = 5.0
SPLIT = dt.date(2015, 1, 1)
PERMUTATIONS = 100
SEED = 20260919
CALIBRATION_LIMIT = 2.3
UNDETERMINED_BOUND = 0.05
BANDS = 5
BANDS_REQUIRED = 4
QUANTILE = 0.2
RECOVERIES = (1.0, 0.0)
#: One round trip at 10 bps a side. Criterion 5.
COST_PER_HOLD = 0.0020
DECLARED = {
    "rsi_14": -1,
    "stochastic_k_14_3": -1,
    "bollinger_percent_b_20": -1,
    "rate_of_change_10": -1,
    "macd_histogram_norm": +1,
    "ema_9_21_distance": +1,
    "sma_50_200_distance": +1,
    "percent_rank_close_252": +1,
}
CONTROL = "realized_volatility_60"
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")


@dataclass(frozen=True)
class Panel:
    dates: np.ndarray
    signal: np.ndarray  # sign * value: HIGH is favoured
    outcome: np.ndarray
    volatility: np.ndarray

    def where(self, mask: np.ndarray) -> Panel:
        return Panel(self.dates[mask], self.signal[mask], self.outcome[mask], self.volatility[mask])


def load(paths: list[Path]) -> dict[str, np.ndarray]:
    columns: dict[str, list[str]] = {}
    for path in paths:
        with path.open() as handle:
            for row in csv.DictReader(handle):
                for key, value in row.items():
                    columns.setdefault(key, []).append(value)

    def floats(name: str) -> np.ndarray:
        return np.array([float(v) if v else np.nan for v in columns[name]])

    data = {name: floats(name) for name in (*DECLARED, CONTROL, "raw_close")}
    data["adv"] = floats("avg_dollar_volume_20")
    for h in HORIZONS:
        data[f"forward_{h}"] = floats(f"forward_{h}")
        data[f"terminal_{h}"] = np.array([v == "1" for v in columns[f"terminal_{h}"]])
    data["dates"] = np.array([dt.date.fromisoformat(v) for v in columns["session_date"]])
    data["security_id"] = np.array([int(v) for v in columns["security_id"]])
    data["unsure"] = np.array([v == "1" for v in columns["volume_undetermined"]])
    return data


def groups(dates: np.ndarray) -> list[np.ndarray]:
    order = np.argsort(dates, kind="mergesort")
    ordered = dates[order]
    cuts = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
    return np.split(order, cuts)


def ic_of(panel: Panel, horizon: int) -> CrossSectionalIC | None:
    return cross_sectional_ic(panel.signal, panel.outcome, list(panel.dates), horizon)


def geometric(values: np.ndarray) -> float:
    if (values <= -1.0).any():
        raise ValueError("a geometric mean over a total loss is undefined -- §41 Amendment 1")
    return float(np.expm1(np.mean(np.log1p(values))))


def favoured_edge(panel: Panel, horizon: int, use_geometric: bool) -> tuple[float, float, int]:
    """Criterion 2/5: the favoured fifth's return over its date's universe."""
    mean = geometric if use_geometric else (lambda v: float(np.mean(v)))
    per_date: list[tuple[dt.date, float]] = []
    for rows in groups(panel.dates):
        if rows.shape[0] < MIN_PER_DATE:
            continue
        order = rows[np.argsort(panel.signal[rows], kind="mergesort")]
        fifth = order[-max(1, int(order.shape[0] * QUANTILE)) :]
        per_date.append(
            (panel.dates[rows[0]], mean(panel.outcome[fifth]) - mean(panel.outcome[rows]))
        )
    if not per_date:
        return float("nan"), float("nan"), 0
    origin = per_date[0][0]
    width = max(1, round(horizon * DAYS_PER_SESSION))
    blocks: dict[int, list[float]] = {}
    for date, edge in per_date:
        blocks.setdefault((date - origin).days // width, []).append(edge)
    block_mean = float(np.mean([np.mean(v) for v in blocks.values()]))
    return block_mean, float(np.median([e for _, e in per_date])), len(per_date)


def volatility_bands(panel: Panel) -> list[np.ndarray]:
    band = np.full(panel.dates.shape[0], -1)
    for rows in groups(panel.dates):
        if rows.shape[0] < BANDS:
            continue
        order = rows[np.argsort(panel.volatility[rows], kind="mergesort")]
        band[order] = (np.arange(order.shape[0]) * BANDS) // order.shape[0]
    return [band == b for b in range(BANDS)]


def calibration(panel: Panel, horizon: int) -> float:
    rng = np.random.default_rng(SEED)
    blocks = groups(panel.dates)
    ts: list[float] = []
    for _ in range(PERMUTATIONS):
        shuffled = panel.signal.copy()
        for rows in blocks:
            shuffled[rows] = panel.signal[rng.permutation(rows)]
        result = cross_sectional_ic(shuffled, panel.outcome, list(panel.dates), horizon)
        if result is not None and result.t is not None:
            ts.append(abs(result.t))
    return float(np.percentile(ts, 95))


def judge(panel: Panel, horizon: int, recovery: float) -> bool:
    whole = ic_of(panel, horizon)
    if whole is None or whole.t is None:
        print("    too few dates or blocks for an estimate -- FAIL")
        return False
    hurdle = small_sample_hurdle(TRIALS, whole.blocks)
    c1 = whole.ic > 0 and whole.t > hurdle
    print(
        f"    1  IC {whole.ic:+.4f}  t {whole.t:+.2f}  (hurdle {hurdle:.4f}, {whole.blocks} "
        f"blocks, median breadth {whole.median_breadth:.0f})  {'PASS' if c1 else 'FAIL'}"
    )
    detectable = whole.detectable(hurdle)
    print(f"       smallest detectable IC: {'--' if detectable is None else f'{detectable:.4f}'}")

    use_geometric = recovery == 1.0
    block_mean, median, n = favoured_edge(panel, horizon, use_geometric)
    c2 = block_mean > 0 and median > 0
    c5 = block_mean > COST_PER_HOLD and median > COST_PER_HOLD
    kind = "geometric" if use_geometric else "equal-weight"
    print(
        f"    2  favoured fifth over its universe, {kind}: block mean {block_mean:+.3%}/hold, "
        f"median {median:+.3%}  ({n} dates)  {'PASS' if c2 else 'FAIL'}"
    )
    print(f"    5  edge above {COST_PER_HOLD:.2%} round-trip costs  {'PASS' if c5 else 'FAIL'}")

    halves = [
        ic_of(panel.where(panel.dates < SPLIT), horizon),
        ic_of(panel.where(panel.dates >= SPLIT), horizon),
    ]
    c3 = all(h is not None and h.ic > 0 for h in halves)
    shown = ", ".join("--" if h is None else f"{h.ic:+.4f}" for h in halves)
    print(f"    3  IC in 2010-14 and 2015-19: {shown}  {'PASS' if c3 else 'FAIL'}")

    bands = [ic_of(panel.where(mask), horizon) for mask in volatility_bands(panel)]
    held = sum(1 for b in bands if b is not None and b.ic > 0)
    c4 = held >= BANDS_REQUIRED
    shown = ", ".join("--" if b is None else f"{b.ic:+.4f}" for b in bands)
    print(
        f"    4  volatility bands (calmest first): {shown} -> {held}/5 {'PASS' if c4 else 'FAIL'}"
    )
    return c1 and c2 and c3 and c4 and c5


def panel_of(
    data: dict[str, np.ndarray], name: str, sign: int, mask: np.ndarray, outcome: np.ndarray
) -> Panel:
    return Panel(data["dates"][mask], sign * data[name][mask], outcome[mask], data[CONTROL][mask])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=",".join(str(OUT / f"swing_s{k}.csv") for k in range(8)))
    ap.add_argument(
        "--jumps",
        default="",
        help="§0.10 discontinuity tables; observations whose outcome window "
        "crosses one are excluded, because a return across one is not a return",
    )
    args = ap.parse_args()
    data = load([Path(p.strip()) for p in args.points.split(",")])
    liquid = (np.nan_to_num(data["raw_close"]) >= MIN_PRICE) & (np.nan_to_num(data["adv"]) >= FLOOR)
    print(f"{data['dates'].shape[0]:,} scanned rows; {int(liquid.sum()):,} above the floors")
    jumps = load_jumps(args.jumps.split(",")) if args.jumps else {}
    if jumps:
        print(f"§0.10 guard: {sum(len(v) for v in jumps.values()):,} flagged sessions loaded")

    passed: list[str] = []
    for horizon in HORIZONS:
        outcomes = {
            r: np.where(
                data[f"terminal_{horizon}"],
                (1.0 + data[f"forward_{horizon}"]) * r - 1.0,
                data[f"forward_{horizon}"],
            )
            for r in RECOVERIES
        }
        measured = np.isfinite(data[f"forward_{horizon}"]) & np.isfinite(data[CONTROL])
        if jumps:
            crossed = spans_jump(data["security_id"], data["dates"], horizon, jumps)
            measured &= ~crossed
            print(f"  §0.10: {int((crossed & liquid).sum()):,} observations excluded")
        terminal = int((data[f"terminal_{horizon}"] & measured & liquid).sum())
        print(f"\n{'#' * 78}\nHORIZON {horizon} sessions")
        print(
            f"  {int((measured & liquid).sum()):,} observations above the floors; "
            f"{terminal:,} from securities that never traded again"
        )

        # -- the machinery, judged before any arm --
        control = ic_of(
            panel_of(data, CONTROL, -1, measured, outcomes[1.0]), horizon
        )  # no liquidity floor, as §34 did
        ok_control = control is not None and control.t is not None and control.ic > 0
        if ok_control:
            assert control is not None and control.t is not None
            ok_control = control.t > small_sample_hurdle(TRIALS, control.blocks)
        print(
            f"  positive control {CONTROL} (declared NEGATIVE, no floor): "
            + (
                "--"
                if control is None or control.t is None
                else f"IC {control.ic:+.4f}, t {control.t:+.2f}, "
                f"median breadth {control.median_breadth:.0f}"
            )
            + f"  -> {'PASSES' if ok_control else 'FAILS'}"
        )
        if not ok_control:
            print("  VOID -- this panel cannot see the one effect this corpus has established.")
            continue

        for name, sign in DECLARED.items():
            defined = measured & liquid & np.isfinite(data[name])
            excluded = defined & ~data["unsure"]
            unsure_share = float(data["unsure"][defined].mean()) if defined.any() else 0.0
            print(f"\n  {'=' * 74}\n  {name}  (declared {'POSITIVE' if sign > 0 else 'NEGATIVE'})")
            verdicts: list[bool] = []
            for recovery in RECOVERIES:
                print(f"   -- recovery {recovery:.1f}, {int(excluded.sum()):,} observations --")
                panel = panel_of(data, name, sign, excluded, outcomes[recovery])
                p95 = calibration(panel, horizon)
                print(f"    calibration: 95th percentile |t| {p95:.2f} (limit {CALIBRATION_LIMIT})")
                if p95 > CALIBRATION_LIMIT:
                    print("    VOID -- standard error too narrow for this arm; nothing printed.")
                    verdicts.append(False)
                    continue
                ok = judge(panel, horizon, recovery)
                if unsure_share > UNDETERMINED_BOUND:
                    print(f"    -- undetermined INCLUDED, {int(defined.sum()):,} observations --")
                    ok = ok and judge(
                        panel_of(data, name, sign, defined, outcomes[recovery]), horizon, recovery
                    )
                verdicts.append(ok)
            ok = all(verdicts)
            print(f"   -> {name} at {horizon}: {'PASSES' if ok else 'FAILS'}")
            if ok:
                passed.append(f"{name}@{horizon}")

    print(f"\n{'#' * 78}")
    if passed:
        print(
            f"PASSING: {', '.join(passed)}. Each licenses ONE confirmation registration on the\n"
            "held-out 2020-2025, naming the arm and its horizon. Nothing else moves."
        )
    else:
        print(
            "NOTHING PASSES. The stop rule applies: single classical technical indicators are\n"
            "closed on this corpus at 5, 10, 21 and 63 sessions, and the next indicator question\n"
            "needs data this corpus does not hold."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
