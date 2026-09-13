#!/usr/bin/env python
"""Apply the pre-registered criteria to the market-capitalisation run.

``docs/prereg/MARKET_CAP_2026-09-13.md``. Four criteria, not three: criterion 4
requires the signal to survive conditioning on PRICE, because §9 recorded that
every test in this project had been univariate and that §26's confound surfaced
only from a post-hoc double sort. Here the double sort is a criterion.

The liquidity floor is applied as part of the specification rather than as a
robustness pass afterwards -- again §26's addendum, where the same effect read
+15.73pp/yr with no floor and +2.56pp/yr at $25M a day.

Statistics, bootstrap seed, draw count and overlap widening are imported from
the relative-strength verdict module, so this is judged by the arithmetic that
judged §13, §19, §22, §24 and §26.
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
)

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.study import Orientation, PromotionRule
from tradeit.strategy.config import StrategyConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HORIZONS = (21, 63)
DRAWS = 300
SIGNAL = "market_cap"


def _edge(chosen: np.ndarray, universe: np.ndarray, turns: float) -> float:
    return (1 + _geometric(chosen)) ** turns - (1 + _geometric(universe)) ** turns


def _edge_ci(
    chosen: np.ndarray, universe: np.ndarray, overlap: int, rng: np.random.Generator
) -> tuple[float, float, float]:
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


def _conditioned(
    inner: np.ndarray, outer: np.ndarray, out: np.ndarray, turns: float, top: bool
) -> list[float]:
    """`inner`'s favoured-quintile edge inside each quintile of `outer`."""
    qs = np.quantile(outer, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    edges: list[float] = []
    for i in range(5):
        m = (outer >= qs[i]) & (outer <= qs[i + 1])
        ii, oo = inner[m], out[m]
        if len(ii) < 500:
            edges.append(float("nan"))
            continue
        band = (
            oo[ii >= np.quantile(ii, 1 - QUANTILE)] if top else oo[ii <= np.quantile(ii, QUANTILE)]
        )
        edges.append(_edge(band, oo, turns))
    return edges


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="mcap")
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--floor", type=float, default=1_000_000.0)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--split", default="2015-01-01")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    split = dt.date.fromisoformat(args.split)
    labels = (f"{args.start[:4]}-{split.year - 1}", f"{split.year}-{args.end[:4]}")
    with (OUT / f"{args.tag}_observations.csv").open() as handle:
        allrows = list(csv.DictReader(handle))
    rows = [
        r
        for r in allrows
        if r["avg_dollar_volume_20"] and float(r["avg_dollar_volume_20"]) >= args.floor
    ]
    hurdle = max(2.0, expected_max_of_normals(args.trials))
    rule = PromotionRule(
        min_observations=500, min_abs_t_statistic=hurdle, quantile_fraction=QUANTILE
    )
    costs = StrategyConfig(name="baseline").costs
    price = _median_price({int(r["security_id"]) for r in rows}, args.start, args.end)
    rng = np.random.default_rng(SEED)
    print(
        f"{len(allrows):,} observations, {len(rows):,} above the registered "
        f"${args.floor:,.0f}/day floor ({len(rows) / len(allrows):.1%}); "
        f"hurdle |t| > {hurdle:.4f} at {args.trials} trials"
    )
    print("direction declared POSITIVE (larger capitalisation, higher return); favoured end is TOP")

    survives: dict[int, bool] = {}
    for horizon in HORIZONS:
        usable = [
            r
            for r in rows
            if r[str(horizon)] and float(r[str(horizon)]) < IMPLAUSIBLE and r[SIGNAL]
        ]
        sig = [float(r[SIGNAL]) for r in usable]
        out = [float(r[str(horizon)]) for r in usable]
        dates = [dt.date.fromisoformat(r["session_date"]) for r in usable]
        ids = [int(r["security_id"]) for r in usable]
        study = _study(sig, out, dates, ids, horizon, SIGNAL, Orientation.POSITIVE)
        ic, t = study.information_coefficient(), study.t_statistic()
        overlap = max(1, horizon // STRIDE)
        turns = 252 / horizon
        s_arr, o_arr, d_arr = np.array(sig), np.array(out), np.array(dates)
        p_arr = np.array([float(r["close"]) for r in usable])
        print(f"\n{'#' * 78}\n  horizon {horizon}: n {study.count:,}  IC {ic:+.4f}  t {t:+.2f}")

        halves: list[bool] = []
        for name, mask in ((labels[0], d_arr < split), (labels[1], d_arr >= split)):
            ss, oo = s_arr[mask], o_arr[mask]
            top = oo[ss >= np.quantile(ss, 1 - QUANTILE)]
            edge, lo, hi = _edge_ci(top, oo, overlap, rng)
            holds = edge > 0 and lo > 0
            halves.append(holds)
            print(
                f"    {name}: geometric top-quintile edge {edge:+.3%}/hold "
                f"({_edge(top, oo, turns):+.2%}/yr)  CI [{lo:+.3%}, {hi:+.3%}]  "
                f"{'holds' if holds else 'does not hold'}"
            )
        c1 = all(halves)
        print(
            "    criterion 1 (top quintile compounds ahead, both halves): "
            f"{'PASS' if c1 else 'fail'}"
        )

        c2 = (ic or 0) > 0 and abs(t or 0) > hurdle
        print(f"    criterion 2 (IC positive, |t| > {hurdle:.4f}): {'PASS' if c2 else 'fail'}")

        signs: list[bool] = []
        for k in range(args.samples):
            idx = [i for i, r in enumerate(usable) if r["sample"] == str(k)]
            sub = _study(
                [sig[i] for i in idx],
                [out[i] for i in idx],
                [dates[i] for i in idx],
                [ids[i] for i in idx],
                horizon,
                SIGNAL,
                Orientation.POSITIVE,
            )
            sic = sub.information_coefficient()
            signs.append((sic or 0) > 0)
            print(f"    sample {k}: n {sub.count:,}  IC {sic:+.4f}  t {sub.t_statistic():+.2f}")
        c3 = sum(signs) >= 3
        print(
            f"    criterion 3 (IC positive in >= 3 of {args.samples}): "
            f"{sum(signs)}/{args.samples} {'PASS' if c3 else 'fail'}"
        )

        band_edges = _conditioned(s_arr, p_arr, o_arr, turns, top=True)
        good = sum(1 for e in band_edges if e == e and e > 0)
        print("    market cap's top-quintile edge INSIDE each price quintile:")
        for i, e in enumerate(band_edges, 1):
            print(
                f"      price quintile {i}: {e:+.2%}/yr"
                if e == e
                else f"      price quintile {i}: too few"
            )
        c4 = good >= 4
        print(
            f"    criterion 4 (positive in >= 4 of 5 price bands): "
            f"{good}/5 {'PASS' if c4 else 'fail'}"
        )

        survives[horizon] = c1 and c2 and c3 and c4
        print(
            f"    -> {'SURVIVES' if survives[horizon] else 'does not survive'} "
            f"at {horizon} sessions"
        )
        if c1 and c2 and c3 and not c4:
            print(
                "    NOTE: criteria 1-3 pass and 4 fails -- the finding is PRICE, not market cap."
            )

        # -- declared diagnostics -------------------------------------------
        sym = _conditioned(p_arr, s_arr, o_arr, turns, top=True)
        sym_good = sum(1 for e in sym if e == e and e > 0)
        print(
            "    [diagnostic] symmetric: PRICE's top-quintile edge inside each market-cap quintile:"
        )
        print(
            "      "
            + "  ".join(f"{e:+.2%}" if e == e else "n/a" for e in sym)
            + f"   ({sym_good}/5 positive)"
        )
        print("    [diagnostic] both ends by tail fraction (the §9 lesson):")
        for frac in (0.20, 0.05, 0.01, 0.002):
            lo_q, hi_q = np.quantile(s_arr, frac), np.quantile(s_arr, 1 - frac)
            small, large = o_arr[s_arr <= lo_q], o_arr[s_arr >= hi_q]
            print(
                f"      {frac:>6.1%}: smallest {_edge(small, o_arr, turns):+8.2%}/yr   "
                f"largest {_edge(large, o_arr, turns):+8.2%}/yr   n {len(small):,}"
            )
        verdict, _reason = study.verdict(rule, costs, average_price=price)
        print(
            f"    [diagnostic] arithmetic spread mean "
            f"{study.quantile_spread(QUANTILE) or 0:+.2%} median "
            f"{study.robust_quantile_spread(QUANTILE) or 0:+.2%}; verdict {verdict}"
        )

    print(f"\n{'=' * 78}")
    state = [h for h in HORIZONS if survives[h]]
    print(
        f"  market_cap: "
        f"{'SURVIVES at ' + str(state) if state else 'does not survive at either horizon'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
