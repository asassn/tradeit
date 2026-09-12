#!/usr/bin/env python
"""Apply the pre-registered criteria to the volume_accumulation runs.

``docs/prereg/VOLUME_ACCUMULATION_2026-09-12.md`` as written: two signals,
judged separately, each surviving at a horizon only if the pooled quantile
spread is established in the declared direction, the geometric top-quintile
edge is positive in both halves of the decade, and the Spearman IC has the
declared sign in at least three of the four samples.

The statistics come from the relative-strength verdict module rather than being
written again -- one implementation, so the two factors are judged by the same
arithmetic. Only the shape differs: there, two columns were two benchmark views
of one signal and both had to hold; here they are two different signals.
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
    ESTABLISHED,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="volacc")
    ap.add_argument("--signals", default="volume_momentum,obv_trend")
    ap.add_argument("--trials", type=int, default=33)
    ap.add_argument("--start", default="2000-01-04")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--split", default="2005-01-01")
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument(
        "--direction",
        choices=("positive", "negative"),
        default="positive",
        help="the declared direction. It decides three things at once: which sign of "
        "quantile spread counts as declared, which quintile criterion 2 measures (a "
        "negative signal is expected to win at its BOTTOM), and which sign of IC "
        "counts for criterion 3.",
    )
    args = ap.parse_args()
    positive = args.direction == "positive"
    orientation = Orientation.POSITIVE if positive else Orientation.NEGATIVE
    side = "top" if positive else "bottom"

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
        f"{len(rows):,} observations across {args.samples} samples; hurdle |t| > {hurdle:.3f} "
        f"at {args.trials} trials; median price ${price:,.2f}"
    )

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
            verdict, reason = study.verdict(rule, costs, average_price=price)
            mean_sp = study.quantile_spread(QUANTILE)
            ic, t = study.information_coefficient(), study.t_statistic()
            spread_t = study.spread_t_statistic(QUANTILE)
            median_sp = study.robust_quantile_spread(QUANTILE)
            net = study.net_annual_spread(QUANTILE, costs, average_price=price)
            print(
                f"\n  horizon {horizon}: n {study.count:,}  IC {ic:+.4f}  t {t:+.2f}  "
                f"spread t {spread_t:+.2f}  mean sp {mean_sp:+.2%}  "
                f"median sp {median_sp:+.2%}  net/yr {net:+.1%}"
            )
            print(f"    verdict {verdict}: {reason}")
            spread = mean_sp or 0.0
            c1 = verdict in ESTABLISHED and (spread > 0 if positive else spread < 0)
            print(f"    criterion 1 (established, declared direction): {'PASS' if c1 else 'fail'}")

            s_arr, o_arr, d_arr = np.array(sig), np.array(out), np.array(dates)
            overlap = max(1, horizon // STRIDE)
            halves = []
            for name, mask in ((labels[0], d_arr < split), (labels[1], d_arr >= split)):
                ss, oo = s_arr[mask], o_arr[mask]
                chosen = (
                    oo[ss >= np.quantile(ss, 1 - QUANTILE)]
                    if positive
                    else oo[ss <= np.quantile(ss, QUANTILE)]
                )
                edge = _geometric(chosen) - _geometric(oo)
                draws = np.array(
                    [
                        _geometric(rng.choice(chosen, len(chosen)))
                        - _geometric(rng.choice(oo, len(oo)))
                        for _ in range(300)
                    ]
                )
                lo, hi = np.percentile(draws, [2.5, 97.5])
                w = np.sqrt(overlap)
                lo, hi = edge - (edge - lo) * w, edge + (hi - edge) * w
                turns = 252 / horizon
                annual = (1 + _geometric(chosen)) ** turns - (1 + _geometric(oo)) ** turns
                halves.append(edge > 0)
                print(
                    f"    {name}: geometric {side}-quintile edge {edge:+.3%}/hold "
                    f"({annual:+.2%}/yr)  CI [{lo:+.3%}, {hi:+.3%}]"
                )
            c2 = all(halves)
            print(
                f"    criterion 2 ({side} quintile ahead in both halves): "
                f"{'PASS' if c2 else 'fail'}"
            )

            signs = []
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
                signs.append((sic or 0) > 0 if positive else (sic or 0) < 0)
                print(f"    sample {k}: n {sub.count:,}  IC {sic:+.4f}  t {sub.t_statistic():+.2f}")
            c3 = sum(signs) >= 3
            print(
                f"    criterion 3 (declared sign in >= 3 of {args.samples}): {sum(signs)}/"
                f"{args.samples} {'PASS' if c3 else 'fail'}"
            )
            survives[(signal, horizon)] = c1 and c2 and c3
            print(
                f"    -> {'SURVIVES' if survives[(signal, horizon)] else 'does not survive'}"
                f" at {horizon} sessions"
            )

    print(f"\n{'=' * 78}")
    for signal in signals:
        state = [h for h in HORIZONS if survives[(signal, h)]]
        outcome = f"SURVIVES at {state} sessions" if state else "does not survive at either horizon"
        print(f"  {signal:<18}: {outcome}")
    # Reported because the two signals are different claims about one factor:
    # volume_momentum is direction-blind, obv_trend carries the sign. Only
    # meaningful when both were run; a single-signal run says nothing about it.
    if len(signals) < 2:
        return 0
    both = [r for r in rows if r[signals[0]] and r[signals[1]]]
    a = np.array([float(r[signals[0]]) for r in both])
    b = np.array([float(r[signals[1]]) for r in both])
    print(
        f"\n  {signals[0]} vs {signals[1]}: rank correlation "
        f"{np.corrcoef(ranks(a), ranks(b))[0, 1]:+.4f} on {len(both):,} shared observations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
