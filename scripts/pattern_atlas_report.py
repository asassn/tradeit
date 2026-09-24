#!/usr/bin/env python
"""Read an atlas file and print what a trader would have lived. Nothing else.

``PATTERN_PROGRAM.md`` Stage A. Item 1 was reported from a throwaway one-liner,
which is fine once and indefensible across forty setups: the tables have to be
produced the same way every time or the entries are not comparable, which is
the whole point of an atlas.

**Every figure here is descriptive and paired.** Each rule trade carries a
placebo drawn from the same session with the same stop distance, and the only
number worth reading is the difference between them -- in a decade that was 80%
uptrend by this corpus's own index, a rule that does nothing still shows a
profit. No t-statistic is printed and no trial is charged, deliberately:
**an atlas number is not evidence and may not be quoted as one.**

First attempts and repeat attempts are reported **separately**. The owner's
objection to §51 was that a failed breakout in a bull market is a wait rather
than a loss; pooling the two would answer that question by averaging it away.

``--by`` chooses the regime reading. ``trend`` is item 1's; ``volatility``
splits turbulent months from quiet ones, which is *not* the same question --
this corpus's downtrends are 89% turbulent but its uptrends are 69% quiet, so
the two readings disagree on 686 sessions of the decade and those are the only
sessions that can tell "needs a rising market" from "needs a calm one" apart.

**Differences are taken pair by pair, not between two separate means.** A rule
trade and its placebo can disagree about which horizons they reach -- one
security delists at session 80 and the other does not -- so averaging the two
legs independently silently compares different samples. The first draft of this
file did exactly that and moved item 1's six-month edge from +1.37% to +1.35%,
which is how the difference was noticed at all. Pairing reproduces item 1
exactly, so its published table stands.
"""

from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict

HORIZONS = (5, 10, 21, 63, 126)


Pair = tuple[dict[str, str], dict[str, str]]


def share(pairs: list[Pair], column: str, leg: int) -> float | None:
    values = [p[leg][column] for p in pairs if p[0][column] and p[1][column]]
    return sum(1 for v in values if v == "1") / len(values) if values else None


def own_mean(pairs: list[Pair], column: str) -> float | None:
    """The rule leg's own return, unadjusted.

    The edge columns are differences, so anything both legs pay -- costs, and
    on the short side the borrow fee -- cancels out of them. A rule that beats
    its placebo by half a point while both lose money is not a trade, and
    without this column the table could not tell the two apart.
    """
    values = [float(rule[column]) for rule, placebo in pairs if rule[column] and placebo[column]]
    return st.fmean(values) if values else None


def paired_edge(pairs: list[Pair], column: str) -> float | None:
    """Mean of (rule - placebo), over the pairs where both legs reported."""
    both = [
        float(rule[column]) - float(placebo[column])
        for rule, placebo in pairs
        if rule[column] and placebo[column]
    ]
    return st.fmean(both) if both else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True, help="comma-separated pair files")
    ap.add_argument("--pattern", default="", help="one pattern, or empty for every one")
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--split-attempts", action="store_true")
    ap.add_argument(
        "--by",
        choices=("trend", "volatility", "both", "pooled"),
        default="trend",
        help="which regime reading to condition on; 'pooled' is the number the program exists "
        "to stop anybody quoting on its own",
    )
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for path in args.atlas.split(","):
        with open(path.strip()) as handle:
            rows.extend(csv.DictReader(handle))
    wanted = {p.strip() for p in args.pattern.split(",") if p.strip()}
    if wanted:
        rows = [r for r in rows if r.get("pattern", "") in wanted]
    if not rows:
        raise SystemExit("no rows matched")

    by_trade: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        by_trade[row["trade_id"]][row["leg"]] = row
    grouped: dict[tuple[str, ...], list[Pair]] = defaultdict(list)
    for legs in by_trade.values():
        rule, placebo = legs.get("rule"), legs.get("placebo")
        if rule is None or placebo is None:
            continue
        attempt = rule.get("attempt", "1")
        conditions = {
            "trend": (rule["regime"],),
            "volatility": (rule.get("vol_regime", "?"),),
            "both": (rule["regime"], rule.get("vol_regime", "?")),
            "pooled": (),
        }[args.by]
        grouped[
            (
                rule.get("direction", "long"),
                rule.get("pattern", "?"),
                rule["arm"],
                *conditions,
                ("first" if attempt == "1" else "repeat") if args.split_attempts else "all",
            )
        ].append((rule, placebo))

    header = (
        f"{'setup / arm / regime / attempt':<58}{'n':>8}{'+1R first':>11}{'placebo':>9}{'edge':>8}"
    )
    full = header + "".join(f"{f'net {h}':>10}" for h in HORIZONS) + f"{'own 126':>10}"
    print(full)
    print("-" * len(full))
    for key in sorted(grouped):
        pairs = grouped[key]
        if len(pairs) < args.min_trades:
            continue
        hit = share(pairs, "reached_1r_first", 0)
        base = share(pairs, "reached_1r_first", 1)
        label = " / ".join(k for k in key if k != "all")
        line = f"{label:<58}{len(pairs):>8,}"
        line += f"{hit:>10.1%}" if hit is not None else f"{'--':>10}"
        line += f"{base:>9.1%}" if base is not None else f"{'--':>9}"
        line += (
            f"{(hit - base) * 100:>+8.1f}" if hit is not None and base is not None else f"{'--':>8}"
        )
        for horizon in HORIZONS:
            edge = paired_edge(pairs, f"net_{horizon}")
            line += f"{edge * 100:>+10.2f}" if edge is not None else f"{'--':>10}"
        own = own_mean(pairs, f"net_{max(HORIZONS)}")
        line += f"{own * 100:>+10.2f}" if own is not None else f"{'--':>10}"
        print(line)
    print("\n'+1R first' is the share reaching one unit of risk in favour before the stop;")
    print("'net' columns are the rule's mean return minus its placebo's, in percent,")
    print("stop honoured and costs charged; 'own 126' is the rule leg's OWN six-month")
    print("return, which the edge columns cancel. Descriptive only -- no trial is charged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
