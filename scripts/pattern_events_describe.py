#!/usr/bin/env python
"""Describe the design-window pattern events. No hypothesis, no trial.

Answers the questions a rule has to be built on, per pattern family:

* how often a structure that has not broken out does break out, and how many
  events that is per year;
* **where a stop can sit**: the distribution of adverse excursion in units of
  risk, which says how often the pattern's own invalidation level is hit
  before the trade goes anywhere;
* the payoff shape: favourable excursion, what fraction reach 1R, 2R and 3R
  before the stop, and the expectancy of a plain 1R-target rule and of holding
  to a fixed horizon;
* whether any of it survives 20 bps a side.

Expectancy is stated in R and in per-trade percent, because a rule with a big
R-multiple and a 2% risk per trade is a different object from one with 12%.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
from collections import defaultdict
from pathlib import Path

import numpy as np

#: One round trip, as §42 registered it: 10 bps a side.
COSTS = 0.0020


def load(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open() as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def numeric(rows: list[dict[str, str]], name: str) -> np.ndarray:
    return np.array([float(r[name]) if r[name] else np.nan for r in rows])


def describe(label: str, rows: list[dict[str, str]], years: float) -> None:
    if not rows:
        return
    mfe, mae = numeric(rows, "mfe_r"), numeric(rows, "mae_r")
    risk = numeric(rows, "risk_fraction")
    touch = np.array([r["first_touch"] for r in rows])
    r63, r21 = numeric(rows, "r_at_63"), numeric(rows, "r_at_21")
    ret21, ret63 = numeric(rows, "ret_21"), numeric(rows, "ret_63")
    stopped = touch == "stop"
    hit_1r = touch == "target_1r"

    # A 1R-target rule, resolved pessimistically: stop first when a session
    # spans both. Trades that reach neither in 63 sessions exit at the close.
    outcome = np.where(stopped, -1.0, np.where(hit_1r, 1.0, np.nan_to_num(r63)))
    # Costs in R: a round trip costs COSTS of price, and risk_fraction is the
    # stop distance as a fraction of price, so the cost is COSTS / risk in R.
    cost_r = COSTS / np.where(risk > 0, risk, np.nan)
    net = outcome - cost_r

    print(f"\n{label}  ({len(rows):,} events, {len(rows) / years:.0f}/yr)")
    print(
        f"  stop distance: median {np.nanmedian(risk):.1%} of price   "
        f"cost in R: median {np.nanmedian(cost_r):.3f}R"
    )
    print(
        f"  adverse excursion (R): p25 {np.nanpercentile(mae, 25):.2f}  "
        f"median {np.nanmedian(mae):.2f}  p75 {np.nanpercentile(mae, 75):.2f}   "
        f"-> stop hit at some point in 63 sessions: {(mae >= 1.0).mean():.1%}"
    )
    print(
        f"  favourable excursion (R): median {np.nanmedian(mfe):.2f}  "
        f"p75 {np.nanpercentile(mfe, 75):.2f}   reached 2R {(mfe >= 2).mean():.1%}, "
        f"3R {(mfe >= 3).mean():.1%}"
    )
    print(
        f"  first touch: stop {stopped.mean():.1%}   +1R {hit_1r.mean():.1%}   "
        f"neither in 63 sessions {(~stopped & ~hit_1r).mean():.1%}"
    )
    print(
        f"  1R-target rule: expectancy {np.nanmean(outcome):+.3f}R gross, "
        f"{np.nanmean(net):+.3f}R net of {COSTS:.2%} round trip"
    )
    print(
        f"  hold to horizon: 21s {np.nanmean(ret21):+.2%} mean / "
        f"{np.nanmedian(ret21):+.2%} median;  63s {np.nanmean(ret63):+.2%} / "
        f"{np.nanmedian(ret63):+.2%}   (in R at 63: {np.nanmean(r63):+.2f})"
    )
    print(f"  r_at_21 mean {np.nanmean(r21):+.2f}R")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, help="comma-separated event CSVs")
    ap.add_argument("--min-events", type=int, default=200)
    args = ap.parse_args()
    rows = load([Path(p.strip()) for p in args.events.split(",")])
    dates = [dt.date.fromisoformat(r["entry_date"]) for r in rows]
    years = max(1e-9, (max(dates) - min(dates)).days / 365.25)
    print(
        f"{len(rows):,} events, {len({r['security_id'] for r in rows}):,} securities, "
        f"{min(dates)} .. {max(dates)} ({years:.1f} years)"
    )
    print("stop source: ", end="")
    sources = defaultdict(int)
    for r in rows:
        sources[r["stop_source"]] += 1
    print(", ".join(f"{k} {v / len(rows):.0%}" for k, v in sorted(sources.items())))

    describe("ALL PATTERNS", rows, years)
    families = defaultdict(list)
    for r in rows:
        families[r["pattern"]].append(r)
    for name, group in sorted(families.items(), key=lambda kv: -len(kv[1])):
        if len(group) >= args.min_events:
            describe(name, group, years)

    print("\n-- by pattern quality, all families pooled --")
    quality = numeric(rows, "quality")
    edges = np.nanpercentile(quality, [0, 25, 50, 75, 100])
    for lo, hi in itertools.pairwise(edges):
        band = [r for r, q in zip(rows, quality, strict=True) if lo <= q <= hi]
        describe(f"quality {lo:.0f}-{hi:.0f}", band, years)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
