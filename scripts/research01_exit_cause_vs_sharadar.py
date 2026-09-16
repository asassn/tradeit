#!/usr/bin/env python
"""Does a second source agree about why each dead company stopped trading?

§16 of ``SIGNAL_SCOREBOARD.md`` classified 1,737 died-arm securities from their
own EDGAR filings -- 8-K item numbers, Form 15 holder counts, bankruptcy
headings read from the document text. That is one source reading one kind of
evidence, and it has never been checked against another.

Sharadar's ``actions`` table publishes lifecycle events of its own:
``bankruptcyliquidation``, ``delisted``, ``acquisitionby``, ``acquisitionstock``,
``acquisitionelectcash``, ``spinoff``. This asks it about the same companies and
cross-tabulates.

**Neither source is the referee.** EDGAR is primary and the vendor is not, so a
disagreement is not automatically the vendor being wrong -- §16's own residue is
17.2% unexplained, and a vendor event can name what a filing did not. What this
measures is agreement where both speak, and where each is silent.

**Nothing is written.** This is a measurement; any change to what the corpus
claims about a company's fate would be a separate, scoped piece of work.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_probe_sharadar import OUT, PAGE, TICKERS, _get, _key, _rows

CIK_IN_URL = re.compile(r"CIK=0*(\d+)")
LEDGER = OUT / "exit_cause_vs_sharadar.jsonl"
CAUSES = OUT / "exit_causes.csv"

#: Events that say **this** company was bought. ``acquisitionof`` is deliberately
#: absent: it appears on the acquirer's record and means the opposite.
ACQUIRED = frozenset(
    {
        "acquisitionby",
        "acquisitionstock",
        "acquisitionelectcash",
        "acquisitionelectstock",
        "acquisitioncash",
        "mergerfrom",
        # This company merged INTO another, which ends it just as surely.
        "mergerto",
        "spacmerger",
    }
)
BANKRUPT = frozenset({"bankruptcyliquidation"})
#: The exchange removed it. A reason in its own right, and not the same claim as
#: a bare ``delisted``, which says only that trading stopped there.
REGULATORY = frozenset({"regulatorydelisting"})
#: The company left the exchange of its own accord -- a going-private, a move to
#: the over-the-counter market. Not the exchange's decision and not a failure.
VOLUNTARY = frozenset({"voluntarydelisting"})


def _verdict(actions: list[str]) -> str:
    """What Sharadar's events say on their own, before any comparison."""
    if BANKRUPT.intersection(actions):
        return "bankrupt"
    if ACQUIRED.intersection(actions):
        return "acquired"
    if REGULATORY.intersection(actions):
        return "delisted by the exchange"
    if VOLUNTARY.intersection(actions):
        return "delisted voluntarily"
    if "delisted" in actions:
        return "delisted, reason unstated"
    return "no lifecycle event"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--interval", type=float, default=0.25)
    args = ap.parse_args()

    key = _key()
    if not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2

    by_cik: dict[int, list[dict]] = collections.defaultdict(list)
    for row in json.loads(TICKERS.read_text()):
        found = CIK_IN_URL.search(row.get("secfilings") or "")
        if found:
            by_cik[int(found.group(1))].append(row)

    done: dict[str, dict] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            record = json.loads(line)
            done[str(record["cik"])] = record

    rows = list(csv.DictReader(CAUSES.open()))
    todo = [r for r in rows if r["cik"] not in done and by_cik.get(int(r["cik"]))]
    print(
        f"classified securities: {len(rows):,}; with a Sharadar row under the same CIK: "
        f"{sum(1 for r in rows if by_cik.get(int(r['cik']))):,}; this run "
        f"{min(len(todo), args.limit):,}"
    )

    unknown: collections.Counter[str] = collections.Counter()
    with LEDGER.open("a") as ledger:
        for row in todo[: args.limit]:
            candidates = by_cik[int(row["cik"])]
            status, text = _get(
                f"actions?ticker={candidates[0]['ticker']}&fields=date,action&limit={PAGE}", key
            )
            time.sleep(args.interval)
            if status != 200:
                continue
            actions = [str(a.get("action", "")) for a in _rows(text)]
            for action in actions:
                if action not in ACQUIRED | BANKRUPT | REGULATORY | VOLUNTARY and action not in {
                    "spunofffrom",
                    "namechangeto",
                    "namechangefrom",
                    "delisted",
                    "dividend",
                    "split",
                    "listed",
                    "initiated",
                    "tickerchangeto",
                    "tickerchangefrom",
                    "sicchangeto",
                    "sicchangefrom",
                    "acquisitionof",
                    "relation",
                    "spinoff",
                    "spinoffdividend",
                }:
                    unknown[action] += 1
            record = {
                "cik": int(row["cik"]),
                "name": row["name"],
                "edgar": row["cause"],
                "sharadar": _verdict(actions),
                "ticker": candidates[0]["ticker"],
            }
            ledger.write(json.dumps(record) + "\n")
            ledger.flush()
            done[row["cik"]] = record

    graded = [r for r in done.values()]
    table: collections.Counter[tuple[str, str]] = collections.Counter(
        (r["edgar"], r["sharadar"]) for r in graded
    )
    edgar_totals = collections.Counter(r["edgar"] for r in graded)
    columns = [
        "bankrupt",
        "acquired",
        "delisted by the exchange",
        "delisted voluntarily",
        "delisted, reason unstated",
        "no lifecycle event",
    ]
    print(f"\n{len(graded):,} companies compared\n")
    print(f"{'EDGAR says':<26}" + "".join(f"{c:>25}" for c in columns) + f"{'total':>8}")
    for cause, total in edgar_totals.most_common():
        cells = "".join(f"{table[(cause, c)]:>25,}" for c in columns)
        print(f"{cause:<26}{cells}{total:>8,}")
    if unknown:
        print(f"\naction types this script does not classify: {dict(unknown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
