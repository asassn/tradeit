#!/usr/bin/env python
"""Judge the 24-indicator screen against its four registered criteria.

Reads what ``signal_screen_indicators.py`` wrote and applies
``docs/prereg/INDICATOR_SCREEN_2026-09-17.md`` -- nothing else. The criteria are
transcribed here as code so that what was registered and what was computed can
be diffed against each other rather than compared by reading.

**The judging is separated from the scanning on purpose.** A criterion adjusted
while looking at the number it is about to be applied to is not a criterion. The
scan writes a file; this reads it; neither can see the other's intermediate
state.

**Direction is read from the registration, never from the data.** ``DECLARED``
lives in the scan module and is imported rather than restated, so the two halves
cannot disagree about what was promised.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections.abc import Sequence

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_screen_indicators import CONTROLS, DECLARED, HORIZON
from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.study import (
    Observation,
    Orientation,
    PromotionRule,
    SignalStudy,
    StudyTarget,
    TargetKind,
)
from tradeit.strategy.config import CostConfig, StrategyConfig

#: The registered sub-period boundary, and the registered conditioning count.
BOUNDARY = dt.date(2005, 1, 1)
VOLATILITY_BANDS = 5
#: Criterion 4 as registered: the declared sign must hold in at least 4 of 5.
BANDS_REQUIRED = 4
#: A band or a half with fewer than this many observations is reported as
#: unusable rather than scored, so a criterion cannot be passed by a cell too
#: small to mean anything.
MIN_CELL = 200
#: Amendment 2. An average true range larger than the whole share price is not a
#: volatile security, it is a series with a bad print -- security 79 round-trips
#: 39.50 -> 2,420 -> 40.00 in single sessions, 119 times. Signal-side and
#: computed from the trailing window only, so it is point-in-time.
MAX_ATR_PERCENT = 1.0


def _study(
    dates: Sequence[dt.date],
    ids: Sequence[int],
    signal: np.ndarray,
    outcome: np.ndarray,
    declared: int,
    stride: int,
) -> SignalStudy:
    return SignalStudy(
        name="arm",
        target=StudyTarget(
            kind=TargetKind.FORWARD_RETURN,
            horizon_sessions=HORIZON,
            description=f"{HORIZON}-session forward total return",
        ),
        orientation=Orientation.POSITIVE if declared > 0 else Orientation.NEGATIVE,
        observations=tuple(
            Observation(session_date=d, instrument_id=i, signal=float(s), outcome=float(o))
            for d, i, s, o in zip(dates, ids, signal, outcome, strict=True)
        ),
        sampling_stride_sessions=stride,
    )


def _ic(
    dates: Sequence[dt.date],
    ids: Sequence[int],
    signal: np.ndarray,
    outcome: np.ndarray,
    declared: int,
    stride: int,
) -> float | None:
    if len(signal) < MIN_CELL:
        return None
    return _study(dates, ids, signal, outcome, declared, stride).information_coefficient()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="comma-separated screen CSVs")
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--quantile", type=float, default=0.2)
    ap.add_argument("--trials", type=int, default=74, help="ledger size including this screen")
    ap.add_argument("--min-observations", type=int, default=500)
    ap.add_argument(
        "--max-atr-percent",
        type=float,
        default=MAX_ATR_PERCENT,
        help="Amendment 2: refuse a signal session whose 14-session ATR exceeds "
        "this multiple of the share price. Exposed as a flag so the empty-gap "
        "claim can be re-measured at other thresholds, not so it can be tuned.",
    )
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for path in args.points.split(","):
        with open(path.strip()) as handle:
            rows.extend(csv.DictReader(handle))
    if not rows:
        print("no observations")
        return 2

    # -- Amendment 2, applied before anything is measured --------------------
    offered = len(rows)
    raw_atr = np.array([float(r["atr_percent"]) for r in rows])
    keep = raw_atr <= args.max_atr_percent
    refused_ids = {rows[i]["security_id"] for i in np.where(~keep)[0]}
    # The gap the amendment rests on, re-measured on the full sample exactly as
    # it said it would be -- and expressed as the gap itself rather than as a
    # count inside an arbitrary window, which is how the staged claim came to
    # be misstated in the first place.
    below = raw_atr[keep]
    above = raw_atr[~keep]
    widest_kept = float(below.max()) if below.size else float("nan")
    narrowest_refused = float(above.min()) if above.size else float("inf")
    ratio = narrowest_refused / widest_kept if widest_kept > 0 else float("inf")
    print(
        f"Amendment 2: refused {int((~keep).sum()):,} of {offered:,} observations "
        f"({int((~keep).sum()) / offered:.2%}) with ATR above "
        f"{args.max_atr_percent:.1f}x the share price, across {len(refused_ids):,} securities.\n"
        f"  atr_percent p99 {np.percentile(raw_atr, 99):.3f}, "
        f"p99.9 {np.percentile(raw_atr, 99.9):.3f}, max {raw_atr.max():,.1f}\n"
        f"  the gap the threshold sits in: largest kept {widest_kept:.3f}, "
        f"smallest refused {narrowest_refused:,.3f} -- a factor of {ratio:,.1f}"
        f"{'' if ratio >= 5.0 else '  <- NARROW: the threshold is doing real work, report it'}"
    )
    rows = [r for r, ok in zip(rows, keep, strict=True) if ok]

    dates = [dt.date.fromisoformat(r["session_date"]) for r in rows]
    ids = [int(r["security_id"]) for r in rows]
    outcome = np.array([float(r["forward"]) for r in rows])
    volatility = np.array([float(r["atr_percent"]) for r in rows])
    panel = {name: np.array([float(r[name]) for r in rows]) for name in DECLARED}

    hurdle = expected_max_of_normals(args.trials)
    first = np.array([d < BOUNDARY for d in dates])
    edges = np.quantile(volatility, np.linspace(0, 1, VOLATILITY_BANDS + 1))
    costs: CostConfig = StrategyConfig(name="baseline").costs
    rule = PromotionRule(
        min_observations=args.min_observations,
        min_abs_t_statistic=hurdle,
        quantile_fraction=args.quantile,
    )
    price = 12.0  # median of per-security medians, as every prior run used

    print(
        f"{len(rows):,} observations, {len(set(ids)):,} securities, "
        f"horizon {HORIZON}, stride {args.stride}\n"
        f"multiple-testing hurdle at {args.trials} trials: |t| > {hurdle:.4f}\n"
    )
    header = (
        f"{'arm':<26}{'dir':>4}{'IC':>9}{'t':>8}{'mean sp':>10}{'med sp':>9}"
        f"{'halves':>8}{'vol bands':>11}  criteria  verdict"
    )
    print(header)
    print("-" * len(header))

    flagged: list[str] = []
    summary: dict[str, dict[str, object]] = {}
    for name, declared in DECLARED.items():
        signal = panel[name]
        study = _study(dates, ids, signal, outcome, declared, args.stride)
        ic = study.information_coefficient()
        t = study.t_statistic()
        mean_spread = study.quantile_spread(args.quantile)
        median_spread = study.robust_quantile_spread(args.quantile)
        verdict, _ = study.verdict(rule, costs, average_price=price)

        # -- criterion 1: hurdle cleared IN THE DECLARED DIRECTION -----------
        c1 = ic is not None and t is not None and abs(t) > hurdle and np.sign(ic) == declared
        # -- criterion 2: spread in the declared direction, means and medians
        #    agreeing, which is what OUTLIER_DEPENDENT grades -----------------
        c2 = (
            mean_spread is not None
            and median_spread is not None
            and np.sign(mean_spread) == declared
            and np.sign(median_spread) == declared
        )
        # -- criterion 3: the declared sign holds in BOTH halves --------------
        halves = [
            _ic(
                [d for d, m in zip(dates, mask, strict=True) if m],
                [i for i, m in zip(ids, mask, strict=True) if m],
                signal[mask],
                outcome[mask],
                declared,
                args.stride,
            )
            for mask in (first, ~first)
        ]
        held = sum(1 for h in halves if h is not None and np.sign(h) == declared)
        c3 = held == 2
        # -- criterion 4: the declared sign holds in >= 4 of 5 volatility bands
        bands = 0
        usable = 0
        for lo, hi in zip(edges[:-1], edges[1:], strict=True):
            mask = (volatility >= lo) & (volatility <= hi)
            band = _ic(
                [d for d, m in zip(dates, mask, strict=True) if m],
                [i for i, m in zip(ids, mask, strict=True) if m],
                signal[mask],
                outcome[mask],
                declared,
                args.stride,
            )
            if band is None:
                continue
            usable += 1
            if np.sign(band) == declared:
                bands += 1
        c4 = bands >= BANDS_REQUIRED

        passed = [c1, c2, c3, c4]
        marks = "".join("1234"[n] if ok else "-" for n, ok in enumerate(passed))
        note = f"  <- {CONTROLS[name]} control" if name in CONTROLS else ""
        if all(passed):
            flagged.append(name)
        print(
            f"{name:<26}{'+' if declared > 0 else '-':>4}"
            f"{0.0 if ic is None else ic:>+9.4f}{0.0 if t is None else t:>+8.2f}"
            f"{0.0 if mean_spread is None else mean_spread:>+10.2%}"
            f"{0.0 if median_spread is None else median_spread:>+9.2%}"
            f"{held:>5}/2{bands:>8}/{usable}  {marks:<9} {verdict}{note}"
        )
        summary[name] = {"ic": ic, "t": t, "bands": bands, "halves": held}

    print(
        "\ncriteria: 1 hurdle in the declared direction, 2 spread not outlier-dependent,\n"
        "          3 sign holds in both halves, 4 sign holds in >=4 of 5 volatility bands"
    )

    # -- the controls, read as the registration said they would be -----------
    print("\n-- controls --")
    negative = summary["atr_percent_14"]
    positive = summary["rate_of_change_252"]
    print(
        f"  negative (atr_percent_14): IC {negative['ic']:+.4f} "  # type: ignore[str-format]
        f"(t {negative['t']:+.2f}), holds in {negative['bands']} of 5 volatility bands"  # type: ignore[str-format]
    )
    print(
        f"  positive (rate_of_change_252): IC {positive['ic']:+.4f} "  # type: ignore[str-format]
        f"(t {positive['t']:+.2f})"  # type: ignore[str-format]
    )

    # -- which arms are really the same arm ----------------------------------
    names = list(DECLARED)
    ranked = {n: np.argsort(np.argsort(panel[n])).astype(float) for n in names}
    pairs: list[tuple[float, str, str]] = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            r = float(np.corrcoef(ranked[names[a]], ranked[names[b]])[0, 1])
            pairs.append((abs(r), names[a], names[b]))
    pairs.sort(reverse=True)
    print("\n-- the most collinear pairs, because 24 arms are not 24 independent claims --")
    for r, a, b in pairs[:6]:
        print(f"  |rank rho| {r:.3f}   {a} / {b}")

    print(f"\n{'=' * 70}")
    if flagged:
        print(f"FLAGGED, all four criteria: {', '.join(flagged)}")
        print("Licenses a confirmation stage only -- no weight, no gate. See the registration.")
    else:
        print("NOTHING FLAGGED. The registered stop rule applies: the single classical")
        print("indicator line closes on this corpus at this horizon, and no further")
        print("indicator family is tried without a new registration saying what would")
        print("be different.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
