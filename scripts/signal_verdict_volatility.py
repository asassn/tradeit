#!/usr/bin/env python
"""Apply the pre-registered criteria to the 2010-2019 volatility run.

``docs/prereg/VOLATILITY_OUT_OF_SAMPLE_2026-09-12.md`` as written, and its
criteria are **not** the ones every earlier verdict script used. There,
criterion 1 was an ESTABLISHED arithmetic quantile spread; here it is a
GEOMETRIC edge holding in both halves, because `SIGNAL_RESEARCH_01.md` §7
measured the arithmetic estimator structurally unfit for this signal --
sorting on volatility sorts on the variance of the thing being averaged, and
in sample that produced mean spreads of +0.69% and +1.03% against medians of
-5.54% and -5.28%, the two disagreeing in sign.

The arithmetic spread and its `SignalStudy` verdict are still computed and
printed, as the registration requires, so that swapping the aggregator cannot
hide a result. They are diagnostics and decide nothing.

The statistics come from the relative-strength verdict module rather than being
written again, so every factor this project has judged is judged by the same
arithmetic -- including the bootstrap's seed, its draw count and its overlap
widening.
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

from signal_research_relative_strength_verdict import (
    IMPLAUSIBLE,
    QUANTILE,
    SEED,
    STRIDE,
    _geometric,
    _median_price,
    _study,
    ranks,
)

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.study import Orientation, PromotionRule
from tradeit.strategy.config import StrategyConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HORIZONS = (21, 63)
#: Bootstrap draws and seed, taken from the module above so the interval here is
#: the same interval §13, §19, §22 and §24 reported.
DRAWS = 300


def _edge(
    chosen: np.ndarray, universe: np.ndarray, overlap: int, rng: np.random.Generator
) -> tuple[float, float, float]:
    """Geometric edge of a quintile over all candidates, with its interval.

    The interval is widened by ``sqrt(overlap)`` because a 63-session return
    sampled every 21 sessions is three overlapping observations of one thing.
    """
    edge = _geometric(chosen) - _geometric(universe)
    draws = np.array(
        [
            _geometric(rng.choice(chosen, len(chosen)))
            - _geometric(rng.choice(universe, len(universe)))
            for _ in range(DRAWS)
        ]
    )
    lo, hi = np.percentile(draws, [2.5, 97.5])
    w = np.sqrt(overlap)
    return edge, edge - (edge - lo) * w, edge + (hi - edge) * w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="vol")
    ap.add_argument("--signals", default="realized_volatility_60,atr_percent")
    ap.add_argument("--trials", type=int, default=46)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--split", default="2015-01-01")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    # Declared NEGATIVE for both signals: a higher volatility reading predicts a
    # lower forward return, so the favoured quintile is the BOTTOM one.
    orientation = Orientation.NEGATIVE
    signals = args.signals.split(",")
    split = dt.date.fromisoformat(args.split)
    labels = (f"{args.start[:4]}-{split.year - 1}", f"{split.year}-{args.end[:4]}")

    rows: list[dict[str, str]] = []
    for k in range(args.samples):
        with (OUT / f"{args.tag}_observations_{k}.csv").open() as handle:
            rows.extend(csv.DictReader(handle))
    hurdle = max(2.0, expected_max_of_normals(args.trials))
    rule = PromotionRule(
        min_observations=500, min_abs_t_statistic=hurdle, quantile_fraction=QUANTILE
    )
    costs = StrategyConfig(name="baseline").costs
    price = _median_price({int(r["security_id"]) for r in rows}, args.start, args.end)
    rng = np.random.default_rng(SEED)
    print(
        f"{len(rows):,} observations across {args.samples} samples; hurdle |t| > {hurdle:.4f} "
        f"at {args.trials} trials; median price ${price:,.2f}"
    )
    print("direction declared NEGATIVE for both signals; the favoured quintile is the BOTTOM")

    survives: dict[tuple[str, int], bool] = {}
    for signal in signals:
        print(f"\n{'#' * 78}\nsignal {signal}")
        for horizon in HORIZONS:
            usable = [
                r
                for r in rows
                if r[signal] and r[str(horizon)] and float(r[str(horizon)]) < IMPLAUSIBLE
            ]
            sig = [float(r[signal]) for r in usable]
            out = [float(r[str(horizon)]) for r in usable]
            dates = [dt.date.fromisoformat(r["session_date"]) for r in usable]
            ids = [int(r["security_id"]) for r in usable]
            study = _study(sig, out, dates, ids, horizon, signal, orientation)
            ic, t = study.information_coefficient(), study.t_statistic()
            overlap = max(1, horizon // STRIDE)
            print(f"\n  horizon {horizon}: n {study.count:,}  IC {ic:+.4f}  t {t:+.2f}")

            s_arr, o_arr, d_arr = np.array(sig), np.array(out), np.array(dates)

            # -- criterion 1, the registered PRIMARY ---------------------------
            halves: list[bool] = []
            for name, mask in ((labels[0], d_arr < split), (labels[1], d_arr >= split)):
                ss, oo = s_arr[mask], o_arr[mask]
                bottom = oo[ss <= np.quantile(ss, QUANTILE)]
                edge, lo, hi = _edge(bottom, oo, overlap, rng)
                turns = 252 / horizon
                annual = (1 + _geometric(bottom)) ** turns - (1 + _geometric(oo)) ** turns
                holds = edge > 0 and lo > 0
                halves.append(holds)
                print(
                    f"    {name}: geometric bottom-quintile edge {edge:+.3%}/hold "
                    f"({annual:+.2%}/yr)  CI [{lo:+.3%}, {hi:+.3%}]  "
                    f"{'holds' if holds else 'does not hold'}"
                )
            c1 = all(halves)
            print(
                f"    criterion 1 (bottom quintile compounds ahead, CI excluding zero, "
                f"both halves): {'PASS' if c1 else 'fail'}"
            )

            # -- criterion 2 ---------------------------------------------------
            c2 = (ic or 0) < 0 and abs(t or 0) > hurdle
            print(
                f"    criterion 2 (IC negative, |t| > {hurdle:.4f}): IC {ic:+.4f} "
                f"t {t:+.2f} {'PASS' if c2 else 'fail'}"
            )

            # -- criterion 3 ---------------------------------------------------
            signs: list[bool] = []
            for k in range(args.samples):
                idx = [i for i, r in enumerate(usable) if r["sample"] == str(k)]
                sub = _study(
                    [sig[i] for i in idx],
                    [out[i] for i in idx],
                    [dates[i] for i in idx],
                    [ids[i] for i in idx],
                    horizon,
                    signal,
                    orientation,
                )
                sic = sub.information_coefficient()
                signs.append((sic or 0) < 0)
                print(f"    sample {k}: n {sub.count:,}  IC {sic:+.4f}  t {sub.t_statistic():+.2f}")
            c3 = sum(signs) >= 3
            print(
                f"    criterion 3 (IC negative in >= 3 of {args.samples}): "
                f"{sum(signs)}/{args.samples} {'PASS' if c3 else 'fail'}"
            )

            survives[(signal, horizon)] = c1 and c2 and c3
            print(
                f"    -> {'SURVIVES' if survives[(signal, horizon)] else 'does not survive'}"
                f" at {horizon} sessions"
            )

            # -- declared diagnostics: no trials charged, nothing promotable ----
            verdict, reason = study.verdict(rule, costs, average_price=price)
            print(
                f"    [diagnostic] arithmetic spread: mean "
                f"{study.quantile_spread(QUANTILE) or 0:+.2%}  median "
                f"{study.robust_quantile_spread(QUANTILE) or 0:+.2%}  "
                f"spread t {study.spread_t_statistic(QUANTILE) or 0:+.2f}"
            )
            print(f"    [diagnostic] SignalStudy verdict {verdict}: {reason}")
            for name, mask in ((labels[0], d_arr < split), (labels[1], d_arr >= split)):
                ss, oo = s_arr[mask], o_arr[mask]
                top = oo[ss >= np.quantile(ss, 1 - QUANTILE)]
                edge, lo, hi = _edge(top, oo, overlap, rng)
                print(
                    f"    [diagnostic] {name}: geometric TOP-quintile edge {edge:+.3%}/hold "
                    f"CI [{lo:+.3%}, {hi:+.3%}] -- §19's avoidance reading"
                )

    print(f"\n{'=' * 78}")
    for signal in signals:
        state = [h for h in HORIZONS if survives[(signal, h)]]
        outcome = f"SURVIVES at {state} sessions" if state else "does not survive at either horizon"
        print(f"  {signal:<24}: {outcome}")
    if len(signals) >= 2:
        both = [r for r in rows if r[signals[0]] and r[signals[1]]]
        a = np.array([float(r[signals[0]]) for r in both])
        b = np.array([float(r[signals[1]]) for r in both])
        print(
            f"\n  {signals[0]} vs {signals[1]}: rank correlation "
            f"{np.corrcoef(ranks(a), ranks(b))[0, 1]:+.4f} on {len(both):,} shared observations"
            "\n  (two measures of one effect, or two effects -- this says which)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
