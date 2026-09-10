#!/usr/bin/env python
"""Ingest EDGAR full-index rows the corpus does not yet have.

**No download.** All 129 quarters of ``form.idx`` from 1994 to 2026 are already
on disk -- 27,084,670 rows, 3.9 GB. The corpus holds 3,528,865 filings, which
is 11.4% of what the local index lists for CIKs it already tracks. The gap is
an ingest gap, not an acquisition gap, and closing it costs nothing but time.

Two decisions shape what gets ingested.

**Company-filed forms only.** EDGAR lists a filing once *per filer*, and an
ownership form has two: the reporting owner and the subject company. A Form 4
filed by Bank of Nova Scotia about Foamex International appears under both
CIKs, and ``filings`` has ``UNIQUE (accession)`` -- one row, one issuer. The
existing corpus resolves those to the **subject**, which is the useful answer
because "filings about company X" is what a research query means. The index
cannot say which CIK is the subject, so ownership forms (3/4/5, SC 13*, SC 14*,
13F, 144) are left out rather than attributed by guess. They are also the bulk
of the index and contribute nothing to point-in-time classification, which is
what the gap was blocking.

**Ambiguity is skipped, not resolved by coin toss.** Even among company-filed
forms, 1.48% of new accessions appear under more than one tracked CIK. Those
are counted and left out. A wrong issuer is worse than a missing filing: the
first is read as evidence, the second as absence.

Idempotent. ``INSERT OR IGNORE`` against the accession constraint means a
re-run adds only what is missing, and an interrupted run resumes.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import sys
import time
from collections import Counter

sys.path.insert(0, "src")

SOURCE = "edgar_full_index"

#: Filed by someone else *about* a company. Their filer CIK is not the subject,
#: and the index does not say which of the two rows is.
_OWNERSHIP_PREFIXES = ("SC 13", "SC 14", "13F", "144")
_OWNERSHIP_EXACT = frozenset({"3", "4", "5", "3/A", "4/A", "5/A"})

#: ``FORM  COMPANY NAME  CIK  YYYY-MM-DD  edgar/data/CIK/ACCESSION.txt``
#: The company name contains spaces, so the form is anchored at the start and
#: the rest is found from the right by the parts that cannot contain spaces.
_ROW = re.compile(
    r"^(\S.*?)\s{2,}.*?\s(\d{1,10})\s+(\d{4}-\d{2}-\d{2})\s+edgar/data/\d+/(\S+?)\.txt"
)


def company_filed(form: str) -> bool:
    return form not in _OWNERSHIP_EXACT and not form.startswith(_OWNERSHIP_PREFIXES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="research01.sqlite")
    ap.add_argument(
        "--index",
        default="/Users/ericsasson/Documents/TradeItData/edgar/full-index",
    )
    ap.add_argument("--since", default=None, help="only filings on or after YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="only filings before YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("pragma busy_timeout=900000")
    con.execute("pragma synchronous=NORMAL")

    cik_to_issuer = {
        int(cik): issuer
        for cik, issuer in con.execute(
            "select value_normalized, issuer_id from issuer_identifiers where namespace='sec_cik'"
        )
    }
    print(f"tracked CIKs: {len(cik_to_issuer):,}")
    before = con.execute("select count(*) from filings").fetchone()[0]
    print(f"filings before: {before:,}")

    files = sorted(pathlib.Path(args.index).rglob("form.idx"))
    print(f"index quarters on disk: {len(files)}")
    tally: Counter[str] = Counter()
    started = time.time()

    for number, path in enumerate(files, start=1):
        # One quarter at a time: a filing appears in exactly one, so ambiguity
        # can be resolved within a file without holding the whole index.
        candidates: dict[str, list[tuple[int, str, str]]] = {}
        for line in path.read_text(errors="replace").splitlines():
            match = _ROW.match(line)
            if match is None:
                continue
            form, cik, filed, accession = (
                match.group(1).strip(),
                int(match.group(2)),
                match.group(3),
                match.group(4),
            )
            if cik not in cik_to_issuer:
                tally["untracked_cik"] += 1
                continue
            if not company_filed(form):
                tally["ownership_form"] += 1
                continue
            if args.since and filed < args.since:
                tally["before_window"] += 1
                continue
            if args.until and filed >= args.until:
                tally["after_window"] += 1
                continue
            candidates.setdefault(accession, []).append((cik, form, filed))

        rows = []
        for accession, entries in candidates.items():
            if len({cik for cik, _, _ in entries}) > 1:
                # Two tracked filers, and the index cannot say which is the
                # subject. A wrong issuer reads as evidence; a missing filing
                # reads as absence.
                tally["ambiguous_issuer"] += 1
                continue
            cik, form, filed = entries[0]
            rows.append((cik_to_issuer[cik], accession, form, filed, SOURCE))

        if rows and not args.dry_run:
            con.executemany(
                "insert or ignore into filings "
                "(issuer_id, accession, form_type, filed_at, source) values (?, ?, ?, ?, ?)",
                rows,
            )
            con.commit()
        tally["offered"] += len(rows)

        if number % 20 == 0 or number == len(files):
            elapsed = time.time() - started
            print(
                f"  {number:>3}/{len(files)} quarters  offered {tally['offered']:,}  "
                f"ambiguous {tally['ambiguous_issuer']:,}  [{elapsed:.0f}s]"
            )

    after = con.execute("select count(*) from filings").fetchone()[0]
    print(f"\nfilings after: {after:,}  (+{after - before:,})")
    print(f"tally: {dict(tally)}")
    if args.dry_run:
        print("(dry run; nothing written)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
