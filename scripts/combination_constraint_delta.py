#!/usr/bin/env python
"""What the diversification constraint costs §46 and §48's result.

Both were produced on an **unconstrained book**: `sectors` held zero rows, no
correlation source existed, and `build_engine` hard-coded
`correlation_weight=0.0` by one uncommented line. Both inputs now exist, so the
archive can be re-measured under the constraints the platform actually applies.

**This is a re-measurement, not a trial.** No hypothesis is tested, nothing is
registered, and the ledger does not move. The question is narrow and worth
asking precisely because the answer is predictable in direction and unknown in
size: *a diversification limit refuses or shrinks positions, so it cannot add
return — how much does it take away?*

**What a null result would mean here.** If the constrained and unconstrained
runs agree, that does not vindicate the archive; it says the cap did not bind
on this universe, which is a fact about the universe. The script reports how
many arms actually moved, so a reader can tell the two apart.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

KEY = ("sample", "arm", "costs", "recovery")


def _load(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open() as handle:
        return {tuple(row[k] for k in KEY): row for row in csv.DictReader(handle)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/Users/ericsasson/Documents/TradeItData/out")
    ap.add_argument("--samples", default="0,1,2,3")
    args = ap.parse_args()
    root = Path(args.out)

    free: dict[tuple[str, ...], dict[str, str]] = {}
    constrained: dict[tuple[str, ...], dict[str, str]] = {}
    missing: list[str] = []
    for sample in args.samples.split(","):
        for label, target in (("free", free), ("cons", constrained)):
            path = root / f"combo_recheck_s{sample}_{label}.csv"
            if not path.exists():
                missing.append(path.name)
                continue
            target.update(_load(path))
    if missing:
        raise SystemExit(
            "these result files do not exist, so the comparison would be partial and "
            f"would not say so: {', '.join(missing)}"
        )

    shared = sorted(set(free) & set(constrained))
    if not shared:
        raise SystemExit(
            "no arm appears in both sets, so there is nothing to compare. Refusing to "
            "report an empty comparison as a null result."
        )

    print(f"{len(shared)} arms present in both sets\n")
    print(f"{'arm':<34} {'free':>9} {'constrained':>12} {'delta':>9} {'trades':>14}")
    moved = 0
    deltas: list[float] = []
    for key in shared:
        a, b = free[key], constrained[key]
        ra, rb = float(a["total_return"]), float(b["total_return"])
        ta, tb = int(a["trades"]), int(b["trades"])
        delta = rb - ra
        if abs(delta) > 1e-9 or ta != tb:
            moved += 1
        deltas.append(delta)
        label = "-".join(key)
        print(f"{label:<34} {ra:>8.2%} {rb:>11.2%} {delta:>+8.2%} {ta:>6,} -> {tb:>5,}")

    print(f"\n  arms where the constraint changed anything: {moved} of {len(shared)}")
    if not moved:
        print(
            "\n  The cap did not bind on any arm. **That is a fact about this universe,\n"
            "  not a vindication of the archive** -- a constraint that never binds has\n"
            "  not been shown to be harmless, it has been shown not to apply here."
        )
        return 0

    mean = statistics.fmean(deltas)
    print(f"  mean change in total return: {mean:+.2%}")
    worse = sum(1 for d in deltas if d < -1e-9)
    better = sum(1 for d in deltas if d > 1e-9)
    print(f"  {worse} arms worse, {better} better, {len(deltas) - worse - better} unchanged")
    if better:
        print(
            "\n  Some arms improved, which deserves care rather than celebration: a\n"
            "  diversification limit cannot add return, so an improvement means it\n"
            "  declined a position that went on to lose. That is a real effect and it\n"
            "  is also exactly what would happen by chance on some arms."
        )
    print(
        "\n  Not a trial. No hypothesis, no registration, ledger unchanged. The archived\n"
        "  §46 and §48 numbers are not retracted: they describe an unconstrained book,\n"
        "  and this says what the constraint costs on the same arms."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
