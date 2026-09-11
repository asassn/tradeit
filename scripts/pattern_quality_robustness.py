#!/usr/bin/env python
"""Is ``pattern_quality``'s edge real, or an artefact of how it is averaged?

:mod:`signal_research_pattern` reported an information coefficient that clears
its multiple-testing hurdle (t +2.86 at 21 sessions, t +4.51 at 63) alongside a
quantile spread that does not (spread t +0.16 and -0.04). Those two facts
disagree, and this script is the adjudication.

It asks three questions the headline number cannot answer on its own.

**Does the sign survive the choice of statistic?** Spearman, Pearson, winsorized
Pearson and a drop-the-top-tail Pearson are four defensible ways to correlate a
signal with a return, and a real effect does not change sign between them.

**Does a portfolio actually compound it?** The quantile machinery in
:class:`SignalStudy` works on **arithmetic** spreads, and on this corpus the
arithmetic cross-sectional mean is dominated by a handful of names that went up
several hundred percent -- ``signal_research`` already found it reporting a
21.9%/yr buy-and-hold for a decade the market spent flat. What a portfolio earns
is the **geometric** mean, and the two can disagree in sign when one bucket is
far more volatile than the other. That is not a technicality here: it is the
difference between "low quality wins" and "low quality loses".

**Does it hold in both halves of its own sample?** The strongest single test
available without an out-of-sample corpus. ``sector_strength`` was significant
in *both directions* by decade and was retired for it; a factor that reverses
between 2000-2004 and 2005-2009 has told us its edge is a period, not a signal.

**This script cannot make the factor work.** It can only find that an apparent
edge does not survive contact with the obvious objections, which is what a
robustness check is for.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt

import numpy as np

SEED = 20260910
#: A 21-session return above +900% is residual vendor noise, not a trade. The
#: tradability filter removes bars that never traded; a handful of rows survive
#: it with a price that still makes no sense. Stated as a constant because a
#: threshold chosen per-run is a free parameter.
IMPLAUSIBLE = 10.0


def _ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    out = np.empty(len(values), dtype=float)
    out[order] = np.arange(len(values), dtype=float)
    return out


def _geometric(values: np.ndarray) -> float:
    """What a pound actually compounds to, not what the average bet paid.

    Total losses are dropped rather than making the whole mean negative
    infinity. They are counted and reported by the caller, because silently
    discarding the worst outcomes in a survivorship study would flatter exactly
    the thing the study is trying to measure.
    """
    usable = values[values > -1.0]
    if len(usable) == 0:
        return float("nan")
    return float(np.expm1(np.mean(np.log1p(usable))))


def _t_from_ic(ic: float, effective: float) -> float:
    return float(ic * np.sqrt(max(effective - 2.0, 1.0) / max(1e-12, 1.0 - ic**2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="tradability-filtered scan-point CSV")
    ap.add_argument("--horizons", default="21,63")
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--quantile", type=float, default=0.2)
    ap.add_argument("--split", default="2005-01-01", help="sub-period boundary")
    ap.add_argument("--bootstrap", type=int, default=400)
    args = ap.parse_args()

    rng = np.random.default_rng(SEED)
    horizons = [int(h) for h in args.horizons.split(",")]
    boundary = dt.date.fromisoformat(args.split)

    with open(args.points) as handle:
        rows = [r for r in csv.DictReader(handle) if r["quality"]]
    quality = np.array([float(r["quality"]) for r in rows])
    dates = np.array([dt.date.fromisoformat(r["session_date"]) for r in rows])
    print(f"{len(rows):,} observations carrying a live pattern")

    for horizon in horizons:
        outcome = np.array([float(r[str(horizon)]) for r in rows])
        overlap = max(1, horizon // args.stride)
        plausible = outcome < IMPLAUSIBLE
        print(f"\n{'=' * 78}\nhorizon {horizon} sessions")
        print(
            f"  dropped {int((~plausible).sum()):,} implausible "
            f"(> +{IMPLAUSIBLE - 1:.0%}); {int((outcome <= -1.0).sum()):,} total losses"
        )

        # -- 1. does the sign survive the statistic? ------------------------
        q, y = quality[plausible], outcome[plausible]
        effective = len(y) / overlap
        low, high = np.percentile(y, [1, 99])
        keep = y <= np.percentile(y, 99.9)
        variants = {
            "Spearman (ranks)": float(np.corrcoef(_ranks(q), _ranks(y))[0, 1]),
            "Pearson (levels)": float(np.corrcoef(q, y)[0, 1]),
            "Pearson winsorized 1/99": float(np.corrcoef(q, np.clip(y, low, high))[0, 1]),
            "Pearson less top 0.1%": float(np.corrcoef(q[keep], y[keep])[0, 1]),
        }
        print("  -- information coefficient under four defensible statistics --")
        for label, ic in variants.items():
            print(f"     {label:<26}{ic:>+9.4f}   t {_t_from_ic(ic, effective):>+6.2f}")
        signs = {np.sign(v) for v in variants.values() if abs(v) > 1e-4}
        print(f"     -> {'SIGN IS STABLE' if len(signs) == 1 else 'SIGN FLIPS between statistics'}")

        # -- 2 and 3. compounding, whole sample and each half ---------------
        turns = 252.0 / horizon

        def annual(rate: float, turns: float = turns) -> float:
            return (1.0 + rate) ** turns - 1.0

        print("  -- geometric, which is what a portfolio compounds --")
        header = (
            f"     {'period':<12}{'n':>9}{'top':>10}{'all':>10}{'bottom':>10}"
            f"{'edge/yr':>10}   95% CI on the per-hold edge"
        )
        print(header)
        for label, mask in (
            ("full sample", plausible),
            (f"< {boundary}", plausible & (dates < boundary)),
            (f">= {boundary}", plausible & (dates >= boundary)),
        ):
            qs, ys = quality[mask], outcome[mask]
            lo, hi = np.quantile(qs, [args.quantile, 1.0 - args.quantile])
            top, bottom = ys[qs >= hi], ys[qs <= lo]
            g_top, g_bottom, g_all = _geometric(top), _geometric(bottom), _geometric(ys)
            edge = g_top - g_all
            draws = np.array(
                [
                    _geometric(rng.choice(top, len(top))) - _geometric(rng.choice(ys, len(ys)))
                    for _ in range(args.bootstrap)
                ]
            )
            # Widen for overlap rather than believing a denser sample.
            widen = np.sqrt(overlap)
            lower, upper = np.percentile(draws, [2.5, 97.5])
            lower, upper = edge - (edge - lower) * widen, edge + (upper - edge) * widen
            verdict = "excludes 0" if lower * upper > 0 else "INCLUDES 0"
            print(
                f"     {label:<12}{len(ys):>9,}{annual(g_top):>+10.2%}{annual(g_all):>+10.2%}"
                f"{annual(g_bottom):>+10.2%}{annual(g_top) - annual(g_all):>+10.2%}"
                f"   [{lower:+.3%}, {upper:+.3%}] {verdict}"
            )

    print(
        "\nAn edge that is present in one half of its own sample and reversed in the\n"
        "other is a period, not a signal. sector_strength was retired for exactly\n"
        "this shape, and the standard does not move because a different factor is\n"
        "the one failing it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
