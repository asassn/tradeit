#!/usr/bin/env python
"""Apply the pre-registered survival criteria to the relative_strength runs.

Reads ``rs_observations_{0..3}.csv`` from :mod:`signal_research_relative_strength`
and applies ``docs/prereg/RELATIVE_STRENGTH_2026-09-12.md`` as written -- the
three criteria, under both benchmarks (amendment 1), at each horizon. Nothing
here is chosen after seeing a number: the thresholds are the registration's.

The factor survives at a horizon only if, **under QQQ and under FLAT alike**:

1. pooled, the quantile spread is established in the declared direction --
   past ``SPREAD_NOT_ESTABLISHED`` and ``OUTLIER_DEPENDENT``, spread t above
   the 26-trial hurdle;
2. the geometric top-quintile edge over all candidates is positive in both
   2000-2004 and 2005-2009;
3. the Spearman IC has the declared sign in at least three of four samples.

Costs need a share price, so the median of per-security median raw closes is
read from the corpus. Raw, not adjusted: commission is charged per share
actually traded. It affects only the economic tier of the verdict, which is
reported and is not one of the three criteria.
"""

from __future__ import annotations

import csv
import datetime as dt
import sqlite3
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.study import (
    Observation,
    Orientation,
    PromotionRule,
    SignalStudy,
    SignalVerdict,
    StudyTarget,
    TargetKind,
)
from tradeit.strategy.config import StrategyConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
TRIALS = 26
HORIZONS = (21, 63)
STRIDE = 21
QUANTILE = 0.2
SPLIT = dt.date(2005, 1, 1)
SEED = 20260912
#: Past the spread gate: the spread is established, whatever its economics.
ESTABLISHED = {
    SignalVerdict.DETECTABLE_NOT_PROFITABLE,
    SignalVerdict.BEATEN_BY_BASELINE,
    SignalVerdict.ECONOMICALLY_USEFUL,
}
#: A 63-session return above +900% is residual vendor noise, as in §13.
IMPLAUSIBLE = 10.0


def _load() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for k in range(4):
        with (OUT / f"rs_observations_{k}.csv").open() as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def _median_price(ids: set[int]) -> float:
    con = sqlite3.connect("file:research01.sqlite?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=300000")
    medians = []
    for sid in sorted(ids):
        closes = [
            c
            for (c,) in con.execute(
                "select close from security_price_facts where security_id = ? and "
                "adjustment_basis = 'raw' and volume > 0 and close > 0 and "
                "session_date between '2000-01-03' and '2009-12-31'",
                (sid,),
            )
        ]
        if closes:
            medians.append(statistics.median(closes))
    return float(statistics.median(medians))


def ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    out = np.empty(len(values))
    out[order] = np.arange(len(values))
    return out


def _geometric(values: np.ndarray) -> float:
    usable = values[values > -1.0]
    return float(np.expm1(np.mean(np.log1p(usable)))) if len(usable) else float("nan")


def _study(
    signal: list[float], outcome: list[float], dates: list[dt.date], ids: list[int], horizon: int
) -> SignalStudy:
    return SignalStudy(
        name="rs_score",
        target=StudyTarget(kind=TargetKind.FORWARD_RETURN, horizon_sessions=horizon),
        orientation=Orientation.POSITIVE,
        observations=tuple(
            Observation(session_date=d, instrument_id=i, signal=s, outcome=o)
            for s, o, d, i in zip(signal, outcome, dates, ids, strict=True)
        ),
        sampling_stride_sessions=STRIDE,
    )


def main() -> int:
    rows = _load()
    hurdle = max(2.0, expected_max_of_normals(TRIALS))
    rule = PromotionRule(
        min_observations=500, min_abs_t_statistic=hurdle, quantile_fraction=QUANTILE
    )
    costs = StrategyConfig(name="baseline").costs
    price = _median_price({int(r["security_id"]) for r in rows})
    rng = np.random.default_rng(SEED)
    print(
        f"{len(rows):,} observations across 4 samples; hurdle |t| > {hurdle:.2f} "
        f"at {TRIALS} trials; median price ${price:,.2f}"
    )

    # -- agreement between the two benchmarks --------------------------------
    q = np.array([float(r["rs_score"]) for r in rows])
    f = np.array([float(r["rs_flat"]) for r in rows])

    moved = int(np.sum(np.abs(q - f) > 1e-9))
    print(
        f"\nQQQ vs FLAT: scores differ on {moved:,} of {len(rows):,} observations "
        f"({moved / len(rows):.2%}); rank correlation {np.corrcoef(ranks(q), ranks(f))[0, 1]:.6f}; "
        f"largest difference {np.max(np.abs(q - f)):.2f} points"
    )

    survives: dict[tuple[str, int], bool] = {}
    for variant in ("rs_score", "rs_flat"):
        label = "QQQ" if variant == "rs_score" else "FLAT"
        print(f"\n{'#' * 78}\nbenchmark {label}")
        for horizon in HORIZONS:
            usable = [r for r in rows if r[str(horizon)] and float(r[str(horizon)]) < IMPLAUSIBLE]
            sig = [float(r[variant]) for r in usable]
            out = [float(r[str(horizon)]) for r in usable]
            dates = [dt.date.fromisoformat(r["session_date"]) for r in usable]
            ids = [int(r["security_id"]) for r in usable]
            study = _study(sig, out, dates, ids, horizon)
            verdict, reason = study.verdict(rule, costs, average_price=price)
            ic, t = study.information_coefficient(), study.t_statistic()
            spread_t = study.spread_t_statistic(QUANTILE)
            mean_sp, med_sp = (
                study.quantile_spread(QUANTILE),
                study.robust_quantile_spread(QUANTILE),
            )
            net = study.net_annual_spread(QUANTILE, costs, average_price=price)
            c1 = verdict in ESTABLISHED and (mean_sp or 0) > 0
            print(
                f"\n  horizon {horizon}: n {study.count:,}  IC {ic:+.4f}  t {t:+.2f}  "
                f"spread t {spread_t:+.2f}  mean sp {mean_sp:+.2%}  median sp {med_sp:+.2%}  "
                f"net/yr {net:+.1%}"
            )
            print(f"    verdict {verdict}: {reason}")
            print(f"    criterion 1 (established, declared direction): {'PASS' if c1 else 'fail'}")

            # criterion 2: geometric top-quintile edge over all, each half
            s_arr, o_arr = np.array(sig), np.array(out)
            d_arr = np.array(dates)
            overlap = max(1, horizon // STRIDE)
            halves = []
            for name, mask in (("2000-2004", d_arr < SPLIT), ("2005-2009", d_arr >= SPLIT)):
                ss, oo = s_arr[mask], o_arr[mask]
                top = oo[ss >= np.quantile(ss, 1 - QUANTILE)]
                edge = _geometric(top) - _geometric(oo)
                draws = np.array(
                    [
                        _geometric(rng.choice(top, len(top))) - _geometric(rng.choice(oo, len(oo)))
                        for _ in range(300)
                    ]
                )
                lo, hi = np.percentile(draws, [2.5, 97.5])
                w = np.sqrt(overlap)
                lo, hi = edge - (edge - lo) * w, edge + (hi - edge) * w
                turns = 252 / horizon
                annual = (1 + _geometric(top)) ** turns - (1 + _geometric(oo)) ** turns
                halves.append(edge > 0)
                print(
                    f"    {name}: geometric top-quintile edge {edge:+.3%}/hold "
                    f"({annual:+.2%}/yr)  CI [{lo:+.3%}, {hi:+.3%}]"
                )
            c2 = all(halves)
            print(f"    criterion 2 (positive in both halves): {'PASS' if c2 else 'fail'}")

            # criterion 3: sign by sample
            signs = []
            for k in range(4):
                idx = [i for i, r in enumerate(usable) if r["sample"] == str(k)]
                sub = _study(
                    [sig[i] for i in idx],
                    [out[i] for i in idx],
                    [dates[i] for i in idx],
                    [ids[i] for i in idx],
                    horizon,
                )
                sic, st_ = sub.information_coefficient(), sub.t_statistic()
                signs.append((sic or 0) > 0)
                print(f"    sample {k}: n {sub.count:,}  IC {sic:+.4f}  t {st_:+.2f}")
            c3 = sum(signs) >= 3
            print(
                f"    criterion 3 (declared sign in >= 3 of 4): {sum(signs)}/4 "
                f"{'PASS' if c3 else 'fail'}"
            )
            survives[(label, horizon)] = c1 and c2 and c3
            print(
                f"    -> {'SURVIVES' if survives[(label, horizon)] else 'does not survive'} "
                f"under {label} at {horizon} sessions"
            )

    # -- diagnostics, not trials -----------------------------------------------
    print(f"\n{'#' * 78}\ndiagnostics (not trials): each lookback's percentile alone, QQQ")
    for horizon in HORIZONS:
        usable = [r for r in rows if r[str(horizon)] and float(r[str(horizon)]) < IMPLAUSIBLE]
        for lb in (20, 60, 120, 250):
            pairs = [
                (float(r[f"pct_{lb}"]), float(r[str(horizon)])) for r in usable if r[f"pct_{lb}"]
            ]
            a, b = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
            ic = np.corrcoef(ranks(a), ranks(b))[0, 1]
            eff = len(a) / max(1, horizon // STRIDE)
            t_lb = ic * np.sqrt((eff - 2) / (1 - ic * ic))
            print(f"  horizon {horizon}  lookback {lb:>3}: IC {ic:+.4f}  t {t_lb:+.2f}")

    by_horizon = {h: survives[("QQQ", h)] and survives[("FLAT", h)] for h in HORIZONS}
    split = {h: survives[("QQQ", h)] != survives[("FLAT", h)] for h in HORIZONS}
    print(f"\n{'=' * 78}")
    for h in HORIZONS:
        state = (
            "SURVIVES under both benchmarks"
            if by_horizon[h]
            else "BENCHMARK-DEPENDENT -- no verdict; SPY needed"
            if split[h]
            else "does not survive under either benchmark"
        )
        print(f"  {h} sessions: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
