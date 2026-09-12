#!/usr/bin/env python
"""Apply the pre-registered criteria to the `obv_trend` gate test.

``docs/prereg/OBV_TREND_GATE_2026-09-12.md`` as written. The PRIMARY test is the
candidate stream -- refused candidates must underperform admitted ones past the
42-trial hurdle, in both halves of the period, with the admitted geometric mean
beating that of all candidates in both halves. The SECONDARY test is the
portfolio, scored from the eight paired backtests.

**The registration did not name the forward horizons.** ``(21, 63)`` is the pair
every prior section used and the pair ``obv_trend`` was measured at in §24; it is
fixed here without reference to this run's numbers, and nothing else is tried.

**Deaths are reported three ways and the criterion uses one.** The registered
convention is the one every prior section used -- a forward return exists only
where a served bar exists -- so a candidate whose series stops inside the horizon
leaves the sample. That silently removes outcomes, and which side it removes them
from is exactly what a gate test is about, so the two recovery views are printed
beside it and labelled as diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from tradeit.backtesting.overfitting import expected_max_of_normals

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HORIZONS = (21, 63)
THRESHOLD = 0.80
#: §18's filter: a forward return above +1000% is a corpus artefact, not a trade.
IMPLAUSIBLE = 10.0
SPLIT = dt.date(2023, 1, 1)


def _geometric(values: np.ndarray) -> float:
    usable = values[values > -1.0]
    return float(np.expm1(np.mean(np.log1p(usable)))) if len(usable) else float("nan")


def _welch(a: np.ndarray, b: np.ndarray, horizon: int) -> tuple[float, float, float]:
    """Difference in means and its overlap-corrected t.

    Candidates are emitted every session, so a horizon-``h`` forward return
    overlaps the ``h - 1`` that follow it. §18's correction divides the count by
    ``horizon // stride``; the stride here is one session, so it divides by the
    horizon itself. That is the most conservative reading available and it is
    the one the criterion uses.
    """
    na, nb = len(a) / horizon, len(b) / horizon
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    diff = float(np.mean(a) - np.mean(b))
    se = float(np.sqrt(np.var(a, ddof=1) / na + np.var(b, ddof=1) / nb))
    return diff, diff / se if se > 0 else float("nan"), se


def _clustered_t(a: np.ndarray, b: np.ndarray, days_a: list, days_b: list) -> float:
    """The same difference with sessions as clusters -- a diagnostic, not the test."""
    both = sorted(set(days_a) | set(days_b))
    idx = {d: i for i, d in enumerate(both)}
    per = np.full((len(both), 2), np.nan)
    for arr, dd, col in ((a, days_a, 0), (b, days_b, 1)):
        acc: dict[int, list[float]] = {}
        for v, d in zip(arr, dd, strict=True):
            acc.setdefault(idx[d], []).append(float(v))
        for i, vals in acc.items():
            per[i, col] = float(np.mean(vals))
    ok = ~np.isnan(per).any(axis=1)
    d = per[ok, 0] - per[ok, 1]
    if len(d) < 2:
        return float("nan")
    return float(np.mean(d) / (np.std(d, ddof=1) / np.sqrt(len(d))))


def _load(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open()))


def _column(
    rows: list[dict[str, str]], horizon: int, view: str
) -> list[tuple[dt.date, float, bool]]:
    """(session, forward return, refused) under one death view."""
    out: list[tuple[dt.date, float, bool]] = []
    for r in rows:
        raw = r[str(horizon)]
        if not raw:
            continue
        imputed = r[f"imputed_{horizon}"] == "1"
        if view == "served" and imputed:
            continue
        value = -1.0 if (view == "recovery0" and imputed) else float(raw)
        if value >= IMPLAUSIBLE:
            continue
        out.append((dt.date.fromisoformat(r["session_date"]), value, r["admitted"] == "0"))
    return out


def _report(rows: list[tuple[dt.date, float, bool]], horizon: int, hurdle: float) -> bool:
    ref = np.array([v for _, v, x in rows if x])
    adm = np.array([v for _, v, x in rows if not x])
    d, t, se = _welch(ref, adm, horizon)
    ct = _clustered_t(ref, adm, [dd for dd, _, x in rows if x], [dd for dd, _, x in rows if not x])
    print(
        f"    n {len(rows):,} ({len(ref):,} refused, {len(adm):,} admitted); "
        f"n_eff {len(rows) / horizon:,.0f}"
    )
    print(
        f"    mean refused {np.mean(ref):+.2%}  admitted {np.mean(adm):+.2%}  "
        f"difference {d:+.2%}  t {t:+.2f}  (by-session t {ct:+.2f})"
    )
    # What this test could have seen. §20's width was reported after the fact;
    # here it is printed beside every result, pass or fail.
    print(
        f"    smallest difference this could have resolved: {hurdle * se:.2%} "
        f"over {horizon} sessions"
    )
    c1 = d < 0 and abs(t) > hurdle
    print(
        f"    criterion 1 (refused below admitted, |t| > {hurdle:.3f}): {'PASS' if c1 else 'fail'}"
    )

    halves = []
    for label, sel in (
        ("2020-2022", lambda dd: dd < SPLIT),
        ("2023-2024", lambda dd: dd >= SPLIT),
    ):
        part = [r for r in rows if sel(r[0])]
        hr = np.array([v for _, v, x in part if x])
        ha = np.array([v for _, v, x in part if not x])
        hd, ht, _ = _welch(hr, ha, horizon)
        holds = hd < 0
        halves.append(holds)
        print(
            f"    {label}: n {len(part):,}  refused {np.mean(hr):+.2%} vs admitted "
            f"{np.mean(ha):+.2%}  difference {hd:+.2%}  t {ht:+.2f}  "
            f"{'holds' if holds else 'does not hold'}"
        )
    c2 = all(halves)
    print(f"    criterion 2 (holds in both halves): {'PASS' if c2 else 'fail'}")

    geo = []
    for label, sel in (
        ("2020-2022", lambda dd: dd < SPLIT),
        ("2023-2024", lambda dd: dd >= SPLIT),
    ):
        part = [r for r in rows if sel(r[0])]
        ga = _geometric(np.array([v for _, v, x in part if not x]))
        gall = _geometric(np.array([v for _, v, _x in part]))
        geo.append(ga > gall)
        print(
            f"    {label}: geometric admitted {ga:+.2%} vs all candidates {gall:+.2%}  "
            f"{'better' if ga > gall else 'not better'}"
        )
    c3 = all(geo)
    print(
        f"    criterion 3 (admitted compounds above all, both halves): {'PASS' if c3 else 'fail'}"
    )
    passed = c1 and c2 and c3
    print(f"    -> {'PASSES' if passed else 'does not pass'} at {horizon} sessions")
    return passed


def _secondary() -> None:
    rows = []
    for fn in sorted(glob.glob(str(OUT / "obvgate_bt_*.log"))):
        for ln in Path(fn).read_text().splitlines():
            if ln.startswith("RESULT"):
                p = ln.strip().split(",")
                rows.append(
                    dict(
                        off=int(p[1]),
                        rec=float(p[2]),
                        ur=float(p[3]),
                        gr=float(p[4]),
                        uc=float(p[5]),
                        gc=float(p[6]),
                        ud=float(p[7]),
                        gd=float(p[8]),
                    )
                )
    for rec in (1.0, 0.0):
        s = sorted([r for r in rows if r["rec"] == rec], key=lambda r: r["off"])
        wins = sum(1 for r in s if r["gr"] > r["ur"])
        d = [r["gc"] - r["uc"] for r in s]
        dd = [r["gd"] - r["ud"] for r in s]
        mu, sd = st.mean(d), st.stdev(d)
        tot = [(r["gr"] - r["ur"]) for r in s]
        print(f"\n  recovery {rec}")
        print(f"    gated beat ungated in {wins} of {len(s)} samples (needs >= 6)")
        print(
            f"    mean CAGR: ungated {st.mean([r['uc'] for r in s]):+.2%}  "
            f"gated {st.mean([r['gc'] for r in s]):+.2%}  change {mu:+.2%} (needs > 0)"
        )
        print(f"    mean drawdown change {st.mean(dd):+.2%} (needs <= +1.00pp)")
        print(
            f"    paired CAGR difference: sd {sd:.2%}, se {sd / len(d) ** 0.5:.2%}, "
            f"t {mu / (sd / len(d) ** 0.5):+.2f}"
        )
        print(
            f"    paired total-return difference: sd {st.stdev(tot):.2%} "
            f"(§20 measured 24.8% at recovery 1.0 and 9.8% at 0.0, on four samples)"
        )
        ok = wins >= 6 and mu > 0 and st.mean(dd) <= 0.01
        print(f"    -> {'PASSES' if ok else 'FAILS'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="obvgate")
    ap.add_argument("--trials", type=int, default=42)
    args = ap.parse_args()
    hurdle = expected_max_of_normals(args.trials)

    rows = _load(OUT / f"{args.tag}_candidates.csv")
    refused = sum(1 for r in rows if r["admitted"] == "0")
    print(f"{'=' * 78}\nPRIMARY -- the candidate stream")
    print(
        f"  {len(rows):,} candidate observations, {refused:,} refused "
        f"({refused / len(rows):.1%}) at rank >= {THRESHOLD}; hurdle |t| > {hurdle:.3f} "
        f"at {args.trials} trials"
    )
    passed: dict[tuple[str, int], bool] = {}
    for view, note in (
        ("served", "registered: a forward return needs a served bar"),
        ("recovery1", "diagnostic: a stopped series pays its last traded close"),
        ("recovery0", "diagnostic: a stopped series pays nothing"),
    ):
        print(f"\n  view '{view}' -- {note}")
        for horizon in HORIZONS:
            print(f"\n  horizon {horizon}:")
            passed[(view, horizon)] = _report(_column(rows, horizon, view), horizon, hurdle)
    reg = [h for h in HORIZONS if passed[("served", h)]]
    print(f"\n  PRIMARY: {'PASSES at ' + str(reg) if reg else 'does not pass at either horizon'}")
    print(f"\n{'=' * 78}\nSECONDARY -- the portfolio")
    _secondary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
