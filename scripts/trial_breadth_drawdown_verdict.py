#!/usr/bin/env python
"""Grade trial 145 against its four registered criteria. Nothing else.

**This file contains no measurement.** It reads the CSVs
`trial_breadth_drawdown.py` wrote and applies
`docs/prereg/BREADTH_DRAWDOWN_2026-11-25.md` to them. The separation is the
point: a script that measures and grades in one pass can have a criterion
adjusted to the number it just produced, and keeping the grader in a file with
no measurement in it makes that edit visible as a diff.

**Written and committed before the window was read.** The four thresholds
below, the block boundaries, the drawdown definition and the sign of the
primary test were all fixed in advance.

Ledger 144 -> 145. Hurdle at twenty blocks: |t| > 2.9713.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from trial_breadth_drawdown import END, OUT, START

from tradeit.backtesting.overfitting import small_sample_hurdle

#: The ledger position this trial occupies. 144 before, 145 after.
TRIALS = 145
SAMPLES = (0, 1, 2, 3)
#: Criterion 2's agreement test: the weaker half at least this fraction of the
#: stronger. Shared sign alone passed §52 on a half of +0.024% against +1.360%.
HALF_AGREEMENT = 1.0 / 3.0
#: Criterion 3: the recovery-1.0 difference may be at most this fraction of the
#: recovery-0.0 difference. Above it, a wide book draws down less whether or not
#: anything is written off, and the mechanism is not the write-off.
MECHANISM_CEILING = 0.5
#: Criterion 4: the wide arm must hold at least this many names on average, or
#: `min_position_notional` refused positions and the comparison is not 12 v 100.
MIN_WIDE_WIDTH = 80.0


def blocks() -> list[tuple[str, dt.date, dt.date]]:
    """Twenty non-overlapping six-month calendar blocks, 2010-H1 .. 2019-H2."""
    out: list[tuple[str, dt.date, dt.date]] = []
    for year in range(START.year, END.year + 1):
        out.append((f"{year}-H1", dt.date(year, 1, 1), dt.date(year, 6, 30)))
        out.append((f"{year}-H2", dt.date(year, 7, 1), dt.date(year, 12, 31)))
    return out


def within_block_drawdown(curve: list[tuple[dt.date, float]], lo: dt.date, hi: dt.date) -> float:
    """Maximum peak-to-trough fall inside the block, **peak reset at `lo`**.

    Resetting is registered, and the alternative is worse in a way that is hard
    to see afterwards: carrying the running peak in from earlier would make
    every block following a large one read as deep, and the twenty observations
    would be one correlated path wearing twenty labels -- destroying exactly the
    independence the block t-statistic needs.
    """
    points = [value for day, value in curve if lo <= day <= hi]
    if len(points) < 2:
        return 0.0
    peak, worst = points[0], 0.0
    for value in points[1:]:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


@dataclass(frozen=True)
class Paired:
    """The twenty block differences and the t-statistic built from them."""

    label: str
    differences: list[float]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.differences)

    @property
    def t(self) -> float | None:
        if len(self.differences) < 2:
            return None
        sd = statistics.stdev(self.differences)
        if sd == 0.0:
            return None
        return self.mean / (sd / len(self.differences) ** 0.5)


def load_curves(sample: int) -> dict[tuple[str, str], list[tuple[dt.date, float]]]:
    path = OUT / f"trial145_curves_s{sample}.csv"
    series: dict[tuple[str, str], list[tuple[dt.date, float]]] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            key = (row["arm"], row["recovery"])
            series.setdefault(key, []).append(
                (dt.date.fromisoformat(row["date"]), float(row["equity"]))
            )
    for value in series.values():
        value.sort()
    return series


def load_results(sample: int) -> list[dict[str, str]]:
    with (OUT / f"trial145_results_s{sample}.csv").open() as handle:
        return list(csv.DictReader(handle))


def paired(recovery: str) -> Paired:
    """Per block, the mean across samples of narrow-minus-wide drawdown."""
    curves = {sample: load_curves(sample) for sample in SAMPLES}
    differences: list[float] = []
    for _label, lo, hi in blocks():
        per_sample = [
            within_block_drawdown(curves[s][("narrow", recovery)], lo, hi)
            - within_block_drawdown(curves[s][("wide", recovery)], lo, hi)
            for s in SAMPLES
        ]
        differences.append(statistics.fmean(per_sample))
    return Paired(label=f"recovery {recovery}", differences=differences)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    missing = [
        str(OUT / f"trial145_{kind}_s{s}.csv")
        for s in SAMPLES
        for kind in ("results", "curves")
        if not (OUT / f"trial145_{kind}_s{s}.csv").exists()
    ]
    if missing:
        print("cannot grade; missing:\n  " + "\n  ".join(missing))
        return 1

    names = [label for label, _lo, _hi in blocks()]
    primary = paired("0.0")
    control = paired("1.0")
    hurdle = small_sample_hurdle(TRIALS, len(primary.differences))

    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    say(f"TRIAL 145 -- breadth as drawdown protection, {START} .. {END}")
    say(f"blocks {len(primary.differences)}  hurdle |t| > {hurdle:.4f}  (trials {TRIALS})")
    say()
    say(f"{'block':<10}{'narrow-wide r0.0':>20}{'narrow-wide r1.0':>20}")
    for label, a, b in zip(names, primary.differences, control.differences, strict=True):
        say(f"{label:<10}{a:>19.2%} {b:>19.2%}")
    say()

    t_primary = primary.t
    c1 = primary.mean > 0 and t_primary is not None and abs(t_primary) > hurdle
    shown_t = "n/a" if t_primary is None else f"{t_primary:.3f}"
    say(
        f"1 PRIMARY        mean {primary.mean:+.2%}  t {shown_t}  vs {hurdle:.4f}"
        f"   {'PASS' if c1 else 'FAIL'}"
    )

    half = len(primary.differences) // 2
    first = statistics.fmean(primary.differences[:half])
    second = statistics.fmean(primary.differences[half:])
    weaker, stronger = min(first, second), max(first, second)
    c2 = first > 0 and second > 0 and weaker >= HALF_AGREEMENT * stronger
    say(
        f"2 HALVES         {START.year}-{START.year + 4} {first:+.2%}   "
        f"{START.year + 5}-{END.year} {second:+.2%}   {'PASS' if c2 else 'FAIL'}"
    )

    c3 = primary.mean > 0 and control.mean <= MECHANISM_CEILING * primary.mean
    say(
        f"3 MECHANISM      r0.0 {primary.mean:+.2%}  r1.0 {control.mean:+.2%}  "
        f"ceiling {MECHANISM_CEILING * primary.mean:+.2%}   {'PASS' if c3 else 'FAIL'}"
    )

    widths = [
        (row["sample"], row["recovery"], float(row["mean_open_positions"]))
        for sample in SAMPLES
        for row in load_results(sample)
        if row["arm"] == "wide"
    ]
    c4 = all(width >= MIN_WIDE_WIDTH for _s, _r, width in widths)
    worst = min(widths, key=lambda item: item[2]) if widths else ("-", "-", -1.0)
    say(
        f"4 WIDE IS WIDE   narrowest wide book {worst[2]:.1f} names "
        f"(sample {worst[0]}, recovery {worst[1]})  vs {MIN_WIDE_WIDTH:.0f}"
        f"   {'PASS' if c4 else 'FAIL'}"
    )
    say()
    verdict = "PASS" if (c1 and c2 and c3 and c4) else "FAIL"
    say(f"VERDICT  {verdict}  -- all four criteria are required.")
    say()
    if verdict == "PASS":
        say("Authorises raising max_positions and risk_per_trade_pct together as a")
        say("portfolio default, a walk-forward on this statistic, and reporting breadth")
        say("in the readiness framework. NO return claim. config.py still gates capital.")
    else:
        say("STOP RULE: breadth as drawdown protection is CLOSED on this corpus. No other")
        say("breadth level, capital level, window, block length, delisting assumption or")
        say("drawdown definition is substituted, and this is not re-read on 2020-2025.")

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
