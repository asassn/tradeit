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
import pathlib
import statistics as st
from collections import defaultdict

HORIZONS = (5, 10, 21, 63, 126)
#: A single leg beyond this multiple of its entry price is treated as a data
#: defect rather than a trade, and the table it would have entered is refused.
#: Set from §0.10's own signature: a price step of 5x with no recorded action is
#: the documented shape of an uncorrected split in this corpus. A trade that
#: really returned +400% is rare enough that stopping to look at it is right.
#:
#: This exists because an atlas built on 2026-09-24 ran with only five of the
#: eight jump-guard shards -- the file list was built from a truncated
#: `ls | head` -- and one placebo leg returning +460,516% over five sessions
#: carried a reported edge to -44.30%. The table printed it without complaint.
#: A guard that depends on being handed every one of its own shards is not a
#: guard, so this checks the numbers themselves.
EXTREME_RETURN = 4.0


Pair = tuple[dict[str, str], dict[str, str]]


def share(pairs: list[Pair], column: str, leg: int) -> float | None:
    values = [p[leg][column] for p in pairs if p[0][column] and p[1][column]]
    return sum(1 for v in values if v == "1") / len(values) if values else None


def extremes(pairs: list[Pair], column: str) -> list[tuple[str, str, float]]:
    """Legs whose return is too large to be a trade. Named, never averaged."""
    out: list[tuple[str, str, float]] = []
    for legs in pairs:
        for leg in legs:
            if leg[column] and abs(float(leg[column])) > EXTREME_RETURN:
                out.append((leg["security_id"], leg["entry_date"], float(leg[column])))
    return out


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


def table(grouped: dict[tuple[str, ...], list[Pair]], minimum: int, markdown: bool) -> list[str]:
    """One block of rows, as fixed-width text or as a markdown table."""
    head = ("setup / arm / regime / attempt", "n", "+1R first", "placebo", "edge")
    columns = (*head, *[f"net {h}" for h in HORIZONS], "own 126")
    lines: list[str] = []
    if markdown:
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("|" + "---|" * len(columns))
    else:
        text = f"{columns[0]:<58}{columns[1]:>8}{columns[2]:>11}{columns[3]:>9}{columns[4]:>8}"
        text += "".join(f"{c:>10}" for c in columns[5:])
        lines.extend((text, "-" * len(text)))
    refused: list[str] = []
    for key in sorted(grouped):
        pairs = grouped[key]
        if len(pairs) < minimum:
            continue
        # Refuse the cell rather than print a mean one bad print decided.
        # Fail closed: an unexplained number is worse than no number, because
        # it will be acted on.
        bad = [b for h in HORIZONS for b in extremes(pairs, f"net_{h}")]
        if bad:
            worst = max(bad, key=lambda b: abs(b[2]))
            refused.append(
                f"{' / '.join(k for k in key if k != 'all')}: {len(bad)} leg(s) beyond "
                f"{EXTREME_RETURN:.0%}, worst security {worst[0]} on {worst[1]} "
                f"at {worst[2]:+,.0%}"
            )
            continue
        hit = share(pairs, "reached_1r_first", 0)
        base = share(pairs, "reached_1r_first", 1)
        cells = [
            " / ".join(k for k in key if k != "all"),
            f"{len(pairs):,}",
            f"{hit:.1%}" if hit is not None else "--",
            f"{base:.1%}" if base is not None else "--",
            f"{(hit - base) * 100:+.1f}" if hit is not None and base is not None else "--",
        ]
        for horizon in HORIZONS:
            edge = paired_edge(pairs, f"net_{horizon}")
            cells.append(f"{edge * 100:+.2f}" if edge is not None else "--")
        own = own_mean(pairs, f"net_{max(HORIZONS)}")
        cells.append(f"{own * 100:+.2f}" if own is not None else "--")
        if markdown:
            lines.append("| " + " | ".join(cells) + " |")
        else:
            row = f"{cells[0]:<58}{cells[1]:>8}{cells[2]:>11}{cells[3]:>9}{cells[4]:>8}"
            lines.append(row + "".join(f"{c:>10}" for c in cells[5:]))
    if refused:
        lines.append("")
        lines.append(
            f"**{len(refused)} cell(s) REFUSED** -- a leg moved further than a trade can. "
            "This is a corpus defect reaching the table, not a result:"
        )
        lines.extend(f"* {r}" for r in refused)
    return lines


def group(
    rows: list[dict[str, str]], by: str, split_attempts: bool, restrict: list[str]
) -> dict[tuple[str, ...], list[Pair]]:
    """Pair the legs and key them by the conditions asked for."""
    by_trade: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        by_trade[row["trade_id"]][row["leg"]] = row
    keep: set[str] | None = None
    for clause in restrict:
        column, _, value = clause.partition("=")
        matched = {
            tid
            for tid, legs in by_trade.items()
            if "rule" in legs and legs["rule"].get(column, "") == value
        }
        keep = matched if keep is None else (keep & matched)
    grouped: dict[tuple[str, ...], list[Pair]] = defaultdict(list)
    for tid, legs in by_trade.items():
        rule, placebo = legs.get("rule"), legs.get("placebo")
        if rule is None or placebo is None or (keep is not None and tid not in keep):
            continue
        attempt = rule.get("attempt", "1")
        conditions = {
            "trend": (rule["regime"],),
            "volatility": (rule.get("vol_regime", "?"),),
            "both": (rule["regime"], rule.get("vol_regime", "?")),
            "pooled": (),
        }[by]
        grouped[
            (
                rule.get("direction", "long"),
                rule.get("pattern", "?"),
                rule["arm"],
                *conditions,
                ("first" if attempt == "1" else "repeat") if split_attempts else "all",
            )
        ].append((rule, placebo))
    return grouped


def book(rows: list[dict[str, str]], path: str, minimum: int) -> None:
    """The whole atlas as one document, so it can be read in one sitting.

    Forty setups reported one command at a time is forty chances to quote a
    table without the caveat beside it. Everything the corpus can currently say
    goes in one file, each section with the condition that produced it named in
    its heading.
    """
    shapes = sorted({k for k in rows[0] if k.startswith("cs_")})
    out: list[str] = [
        "# The pattern atlas — every entry, one document",
        "",
        "Generated by `scripts/pattern_atlas_report.py --book`. **Stage A: descriptive,",
        "no trial charged, 2010-2019 only.** An atlas number is not evidence and may not",
        "be quoted as one — see `PATTERN_PROGRAM.md`.",
        "",
        "Every row is paired with a placebo bought or sold the same session with the same",
        "stop distance, and `edge` columns are the mean of (rule minus placebo) **pair by",
        "pair**. `own 126` is the rule leg's own six-month return, which the edge columns",
        "cancel: a rule that beats its placebo while both lose money is not a trade.",
        "",
    ]
    sections: list[tuple[str, str, bool, list[str]]] = [
        ("Every setup, by trend regime", "trend", False, []),
        ("Every setup, by volatility regime", "volatility", False, []),
        ("Trend and volatility together", "both", False, []),
        ("First breakout attempt against later ones", "trend", True, []),
        (
            "Item 20 — the bull trap: events that ended in a failed breakout",
            "trend",
            False,
            ["final_state=failed_breakout"],
        ),
    ]
    for title, by, split, restrict in sections:
        grouped = group(rows, by, split, restrict)
        out.extend((f"## {title}", ""))
        body = table(grouped, minimum, markdown=True)
        out.extend(body if len(body) > 2 else ["*Nothing cleared the reporting floor.*"])
        out.append("")
    out.extend(
        (
            "## Items 21-40 — the candlesticks, as entry filters",
            "",
            "Each section restricts to trades whose **signal bar** printed that shape.",
            "A shape absent below did not clear the reporting floor on any setup.",
            "",
        )
    )
    for shape in shapes:
        grouped = group(rows, "trend", False, [f"{shape}=1"])
        body = table(grouped, minimum, markdown=True)
        if len(body) > 2:
            out.extend((f"### {shape[3:].replace('_', ' ')}", "", *body, ""))
    pathlib.Path(path).write_text("\n".join(out) + "\n")
    print(f"{len(out):,} lines -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True, help="comma-separated pair files")
    ap.add_argument("--pattern", default="", help="one pattern, or empty for every one")
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument(
        "--book",
        default="",
        help="write the whole atlas to one markdown file instead of printing one table: "
        "every setup, both directions, both regime readings, the candlestick filters "
        "and the failed-breakout slice",
    )
    ap.add_argument("--split-attempts", action="store_true")
    ap.add_argument(
        "--where",
        default="",
        action="append",
        help="restrict to rule legs matching column=value; repeatable. The candlestick "
        "filters (cs_hammer=1 and so on) are items 21-40, and item 20 is "
        "final_state=failed_breakout",
    )
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
    # A restriction is applied to the RULE leg and its placebo is kept with it.
    # Filtering both legs on the same condition would ask whether a random
    # security that happened to print a hammer did well, which is a different
    # question and not a control for this one.
    for clause in [c for c in (args.where or []) if c]:
        column, _, value = clause.partition("=")
        keep = {r["trade_id"] for r in rows if r["leg"] == "rule" and r.get(column, "") == value}
        if not keep:
            raise SystemExit(f"no rule leg matched {clause}")
        rows = [r for r in rows if r["trade_id"] in keep]
    if not rows:
        raise SystemExit("no rows matched")

    if args.book:
        book(rows, args.book, args.min_trades)
        return 0
    grouped = group(rows, args.by, args.split_attempts, [c for c in (args.where or []) if c])

    # The SAME builder the book uses. This path had its own copy of the loop
    # until 2026-09-25, and the copy is how a refused cell still printed: the
    # guard-rail went into one of the two and the terminal used the other.
    for line in table(grouped, args.min_trades, markdown=False):
        print(line)
    print("\n'+1R first' is the share reaching one unit of risk in favour before the stop;")
    print("'net' columns are the rule's mean return minus its placebo's, in percent,")
    print("stop honoured and costs charged; 'own 126' is the rule leg's OWN six-month")
    print("return, which the edge columns cancel. Descriptive only -- no trial is charged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
