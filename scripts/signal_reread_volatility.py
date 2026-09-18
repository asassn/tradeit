#!/usr/bin/env python
"""Re-read §26 -- the only positive verdict on the scoreboard -- honestly.

§32 found that every pooled IC in ``SIGNAL_SCOREBOARD.md`` carries an inflated
t-statistic: it corrects for horizon overlap and then treats hundreds of
securities sharing one date's market move as independent. It recorded an
obligation: *every t quoted as evidence FOR something must be re-read.* §26 is
the one section where that obligation bites, because it is the one section
whose verdict was a pass -- ``realized_volatility_60`` and ``atr_percent`` both
**SURVIVE** at both horizons, at t −26 to −38.

**Only the standard errors change.** Same panel (``voliq2``, the corrected-corpus
re-run §29 used -- 232,830 observations), same four samples, same halves, same
registered hurdle (2.2442 at 46 trials, the ledger when §26 was judged), same
IMPLAUSIBLE cut. Each registered criterion is re-judged with the dependence it
ignored put back:

1. **The geometric edge.** Same point estimate. The registered interval came
   from resampling *observations* independently and widening by sqrt(3); here
   whole calendar blocks, one horizon long, are resampled with every
   observation in them, so a date's shared move travels together.
2. **The IC past the hurdle.** Per-date (Fama-MacBeth) IC with a calendar-block
   Newey-West t from :mod:`tradeit.signals.cross_section`, beside the pooled
   figure it replaces.
3. **Sign in >= 3 of 4 samples.** The same count, on per-date ICs.

**No trials are charged, and why that is not the free look Amendment 3 refused.**
There the corrected reading was a second chance at a *failed* test, so it could
confer a pass the first look had not. Here the test already *passed*; an honest
standard error can only widen an interval and shrink a t, so this re-read can
withdraw a pass and cannot award one. A re-read that can only move a verdict in
the unfavourable direction is an audit, not a trial.
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

from signal_research_relative_strength_verdict import IMPLAUSIBLE, QUANTILE, SEED, _geometric

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.signals.cross_section import DAYS_PER_SESSION, cross_sectional_ic, spearman

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HORIZONS = (21, 63)
SIGNALS = ("realized_volatility_60", "atr_percent")
#: The ledger when §26 was judged. Re-reading at a later, higher hurdle would
#: change two things at once, and this re-read exists to change one.
REGISTERED_TRIALS = 46
SPLIT = dt.date(2015, 1, 1)
DRAWS = 300
FLOOR = 1_000_000.0


def _blocks(dates: np.ndarray, horizon: int) -> np.ndarray:
    """Calendar block index per observation, one horizon wide."""
    origin = min(dates)
    width = max(1, round(horizon * DAYS_PER_SESSION))
    return np.array([(d - origin).days // width for d in dates])


def _block_bootstrap_edge(
    signal: np.ndarray,
    outcome: np.ndarray,
    blocks: np.ndarray,
    rng: np.random.Generator,
) -> tuple[float, float, float]:
    """The registered edge, with an interval that resamples calendar blocks.

    The quintile cut is recomputed inside every draw, exactly as the point
    estimate computes it, so the interval covers the whole statistic rather
    than only the averaging at the end of it.
    """

    def edge(s: np.ndarray, o: np.ndarray) -> float:
        cut = np.quantile(s, QUANTILE)
        return _geometric(o[s <= cut]) - _geometric(o)

    point = edge(signal, outcome)
    ids = np.unique(blocks)
    members = {b: np.flatnonzero(blocks == b) for b in ids}
    draws = []
    for _ in range(DRAWS):
        chosen = rng.choice(ids, len(ids))
        rows = np.concatenate([members[b] for b in chosen])
        draws.append(edge(signal[rows], outcome[rows]))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="voliq2")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for k in range(args.samples):
        with (OUT / f"{args.tag}_observations_{k}.csv").open() as handle:
            rows.extend(csv.DictReader(handle))
    hurdle = expected_max_of_normals(REGISTERED_TRIALS)
    rng = np.random.default_rng(SEED)
    print(
        f"{len(rows):,} observations, {args.samples} samples; the registered hurdle "
        f"|t| > {hurdle:.4f} at {REGISTERED_TRIALS} trials; direction declared NEGATIVE"
    )

    verdicts: dict[tuple[str, int], tuple[bool, bool, bool]] = {}
    for signal in SIGNALS:
        for horizon in HORIZONS:
            usable = [
                r
                for r in rows
                if r[signal] and r[str(horizon)] and float(r[str(horizon)]) < IMPLAUSIBLE
            ]
            s = np.array([float(r[signal]) for r in usable])
            o = np.array([float(r[str(horizon)]) for r in usable])
            d = np.array([dt.date.fromisoformat(r["session_date"]) for r in usable])
            sample = np.array([int(r["sample"]) for r in usable])
            turnover = np.array([float(r["avg_dollar_volume_20"] or 0.0) for r in usable])
            blocks = _blocks(d, horizon)
            turns = 252 / horizon

            print(f"\n{'=' * 78}\n{signal}, {horizon} sessions  (n {len(s):,})")

            # -- criterion 1: the registered edge, block-bootstrapped -----------
            c1_halves = []
            for label, mask in (("2010-14", d < SPLIT), ("2015-19", d >= SPLIT)):
                point, lo, hi = _block_bootstrap_edge(s[mask], o[mask], blocks[mask], rng)
                holds = point > 0 and lo > 0
                c1_halves.append(holds)
                print(
                    f"  C1 {label}: edge {point:+.3%}/hold "
                    f"({(1 + point) ** turns - 1:+.2%}/yr)  block CI [{lo:+.3%}, {hi:+.3%}]  "
                    f"{'holds' if holds else 'DOES NOT HOLD'}"
                )
            c1 = all(c1_halves)

            # -- criterion 2: per-date IC, block t -------------------------------
            pooled = spearman(s, o)
            cs = cross_sectional_ic(s, o, list(d), horizon)
            assert cs is not None
            c2 = cs.ic < 0 and cs.t is not None and abs(cs.t) > hurdle
            detect = cs.detectable(hurdle)
            print(
                f"  C2 pooled IC {pooled:+.4f}  ->  cross-sectional IC {cs.ic:+.4f} "
                f"(t {cs.t:+.2f})  on {cs.dates:,} dates in {cs.blocks} blocks  "
                f"{'PASS' if c2 else 'FAIL'}\n"
                f"     smallest detectable |IC| at the hurdle: {detect:.4f}"
            )

            # -- criterion 3: sign across the four samples -----------------------
            signs = []
            for k in range(args.samples):
                m = sample == k
                sub = cross_sectional_ic(s[m], o[m], list(d[m]), horizon)
                negative = sub is not None and sub.ic < 0
                signs.append(negative)
                t_text = "--" if sub is None or sub.t is None else f"{sub.t:+.2f}"
                ic_text = "--" if sub is None else f"{sub.ic:+.4f}"
                print(f"     sample {k}: cross-sectional IC {ic_text} (t {t_text})")
            c3 = sum(signs) >= 3
            print(
                f"  C3 negative in {sum(signs)}/{args.samples} samples  {'PASS' if c3 else 'FAIL'}"
            )

            # -- §27's condition, as a diagnostic that decides nothing -----------
            liquid = turnover >= FLOOR
            fl = cross_sectional_ic(s[liquid], o[liquid], list(d[liquid]), horizon)
            if fl is not None:
                print(
                    f"  [diagnostic, $1M/day floor] cross-sectional IC {fl.ic:+.4f} "
                    f"(t {'--' if fl.t is None else f'{fl.t:+.2f}'}) on {int(liquid.sum()):,} obs"
                )

            verdicts[(signal, horizon)] = (c1, c2, c3)
            print(
                f"  -> {'SURVIVES' if all((c1, c2, c3)) else 'DOES NOT SURVIVE'} "
                f"the honest standard errors"
            )

    print(f"\n{'=' * 78}")
    for (signal, horizon), (c1, c2, c3) in verdicts.items():
        marks = "".join(n if ok else "-" for n, ok in zip("123", (c1, c2, c3), strict=True))
        print(
            f"  {signal:<24}{horizon:>3}  §26: SURVIVES   now: "
            f"{'SURVIVES' if all((c1, c2, c3)) else 'does not survive'}  [{marks}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
