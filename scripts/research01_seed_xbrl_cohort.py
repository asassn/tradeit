#!/usr/bin/env python
"""Seed issuer identity for every XBRL filer, 2009-2026.

**Why the corpus needs this.** ``research-01``'s identity is a dot-com cohort
that stopped filing between 1998 and 2005, and the free Financial Statement Data
Sets begin in 2009. The two barely meet: fifteen of 891 CIKs, all large
survivors. A fundamentals set consisting entirely of survivors is worse than
none, so the fix is not more fundamentals for that cohort -- there are none --
but a cohort the fundamentals can actually describe.

Measured over all seventy quarterly archives:

===========================================  ======
distinct CIKs that filed XBRL 2009-2026      17,015
... with a confirmed dated EDGAR exit 2009+   7,255
... still filing in the last four quarters    7,261
===========================================  ======

**Forty-three per cent dead and forty-three per cent alive, in one population
the SEC itself defines.** That is what a survivorship-measurable universe looks
like, against 2.71% coverage of dated exits in the corpus as it stands.

What this creates, and what it deliberately does not
----------------------------------------------------

**It creates issuers, not mappings.** A CIK is EDGAR's own primary key for a
registrant, so recording one is not the kind of claim ``MappingEvidence`` ranks
-- there is nothing to corroborate. Each issuer carries the accession, form and
filing date that evidences it, so any row walks back to a document.

**It creates no ticker alias, and that is the point.** Nothing here reads a
filing's words, so nothing here knows a symbol. Without an alias the price
backfill cannot reach these securities by accident, which keeps the boundary
between "we know who this registrant is" and "we know which series is its
stock" exactly where the rest of the corpus keeps it.

**One security per issuer, marked as a placeholder.** ``class_label`` is the
filing's own words elsewhere in this schema and is left NULL here, because no
filing text was read. Fundamentals are an issuer's facts and the table keys on
``security_id``, so a single unambiguous security is what makes the attachment
resolvable at all -- ``fsds.py`` refuses an issuer holding several.

**Existing issuers are never touched.** The 892 seeded from filing text keep
their evidenced identity, their citations and their aliases.

    PYTHONPATH=src .venv/bin/python scripts/research01_seed_xbrl_cohort.py --dry-run
    PYTHONPATH=src .venv/bin/python scripts/research01_seed_xbrl_cohort.py
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.storage.tables import Filing, Issuer, IssuerIdentifier, Security

DEFAULT_FSDS = "/Users/ericsasson/Documents/TradeItData/edgar/fsds"
SOURCE = "sec_fsds_sub"


@dataclass(frozen=True, slots=True)
class Registrant:
    """One CIK, with the earliest filing that evidences it."""

    cik: int
    name: str
    accession: str
    form: str
    filed: dt.date


def _scan(fsds: Path) -> tuple[dict[int, Registrant], list[tuple[str, int, str, dt.date]]]:
    """Every registrant in ``sub.txt``, and every filing row, across all quarters.

    ``sub.txt`` alone -- a couple of megabytes a quarter against ``num.txt``'s
    half a gigabyte. Nothing here needs a number.
    """
    registrants: dict[int, Registrant] = {}
    filings: list[tuple[str, int, str, dt.date]] = []
    for path in sorted(fsds.glob("*.zip")):
        with zipfile.ZipFile(path).open("sub.txt") as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, "latin-1"), delimiter="\t"):
                cik = row.get("cik", "").strip()
                filed = row.get("filed", "").strip()
                if not cik.isdigit() or len(filed) != 8 or not filed.isdigit():
                    continue
                when = dt.date(int(filed[:4]), int(filed[4:6]), int(filed[6:]))
                key = int(cik)
                name = row.get("name", "").strip() or f"CIK {key}"
                accession = row["adsh"]
                form = row.get("form", "").strip()
                filings.append((accession, key, form, when))
                # The EARLIEST filing is kept as the citation: it is the first
                # document in this source that evidences the registrant, and a
                # later one would date the evidence after the fact it supports.
                existing = registrants.get(key)
                if existing is None or when < existing.filed:
                    registrants[key] = Registrant(key, name, accession, form, when)
    return registrants, filings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fsds", default=DEFAULT_FSDS)
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-filings", action="store_true")
    args = ap.parse_args()

    session: Session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()

    print("scanning sub.txt across every quarter ...")
    registrants, filings = _scan(Path(args.fsds))
    print(f"  registrants: {len(registrants):,}   filings: {len(filings):,}")

    held = {
        int(v)
        for v in session.scalars(
            select(IssuerIdentifier.value_normalized).where(IssuerIdentifier.namespace == "sec_cik")
        ).all()
    }
    new = sorted(set(registrants) - held)
    print(f"  already held: {len(held):,}   to create: {len(new):,}")

    if args.dry_run:
        for cik in new[:10]:
            r = registrants[cik]
            print(f"    {r.cik:>9}  {r.name[:44]:<44}  {r.form:<7} {r.filed}  {r.accession}")
        print("\n(dry run; nothing written)")
        return 0

    created = 0
    for cik in new:
        registrant = registrants[cik]
        issuer = Issuer(display_name=registrant.name[:256], note="xbrl-cohort", source=SOURCE)
        session.add(issuer)
        session.flush()
        session.add(
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace="sec_cik",
                value=str(cik),
                value_normalized=str(cik),
                role="primary",
                citation=(
                    f"registrant {cik} filed {registrant.form} {registrant.accession} on "
                    f'{registrant.filed} as "{registrant.name}" (SEC Financial Statement '
                    f"Data Sets, sub.txt). CIK is EDGAR's own key for a registrant."
                ),
                source=SOURCE,
            )
        )
        session.add(
            Security(
                issuer_id=issuer.issuer_id,
                security_type="common_stock",
                # NULL deliberately: no filing text was read, so there are no
                # filing's-own-words to record, and inventing a label would be
                # exactly the fabrication class_label exists to prevent.
                class_label=None,
                currency="USD",
                note="placeholder for the reporting entity's equity; no class evidenced",
                source=SOURCE,
            )
        )
        created += 1
        if created % 2000 == 0:
            session.commit()
            print(f"    created {created:,}/{len(new):,}", flush=True)
    session.commit()
    print(f"  issuers created: {created:,}")

    if not args.skip_filings:
        have = {a for (a,) in session.execute(select(Filing.accession)).all()}
        by_cik = {
            int(v): i
            for v, i in session.execute(
                select(IssuerIdentifier.value_normalized, IssuerIdentifier.issuer_id).where(
                    IssuerIdentifier.namespace == "sec_cik"
                )
            ).all()
        }
        added = 0
        seen: set[str] = set()
        for accession, cik, form, filed in filings:
            if accession in have or accession in seen:
                continue
            issuer_id = by_cik.get(cik)
            if issuer_id is None:
                continue
            seen.add(accession)
            session.add(
                Filing(
                    issuer_id=issuer_id,
                    accession=accession,
                    form_type=form,
                    filed_at=filed,
                    source=SOURCE,
                )
            )
            added += 1
            if added % 20000 == 0:
                session.commit()
                print(f"    filings {added:,}", flush=True)
        session.commit()
        print(f"  filings created: {added:,}")

    print("\nNo ticker alias was created for any of these, deliberately: nothing here")
    print("read a filing's words, so nothing here knows a symbol, and the price")
    print("backfill resolves on aliases and therefore cannot reach them by accident.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
