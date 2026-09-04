#!/usr/bin/env python
"""Seed issuer identity for dated exits the XBRL cohort could not reach.

**The gap this closes, measured.** The corpus holds identity for 8,064 of the
29,180 registrants EDGAR shows exiting on a dated, confirmed basis. The other
21,116 were never seeded, because both existing cohorts were built from sources
that cannot see them: the dot-com cohort was name-matched against a vendor's
symbol list, and the XBRL cohort came from the Financial Statement Data Sets,
which only 48.1% of post-2009 exits ever filed into.

2006-2008 is the sharpest case -- **3,648 dated exits and one of them priced**.
It falls between the two cohorts: after the dot-com selection, before XBRL. It
is also the financial crisis, and a backtest that skips it skips the stress it
most needs to survive.

**The EDGAR full index reaches all of them**, because it is a record of every
filing rather than of a subset that used one technology. It supplies CIK,
company name, form, filing date and accession -- everything an issuer needs and
nothing it does not.

What this creates, and what it deliberately does not
----------------------------------------------------

Identical in kind to ``research01_seed_xbrl_cohort.py`` and for the same
reasons: **issuers, not mappings**, since a CIK is EDGAR's own key for a
registrant and there is nothing to corroborate; **no ticker alias**, since
nothing here reads a filing's words and the price backfill resolves on aliases,
so it cannot reach these securities by accident; **one security per issuer with
a NULL class_label**, since no filing text was read and inventing a label is
the fabrication that column exists to prevent.

The **earliest** filing is kept as the citation. A later one would date the
evidence after the fact it supports.

    PYTHONPATH=src .venv/bin/python scripts/research01_seed_from_index.py \\
        --from-year 2006 --to-year 2008 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.index import IndexQuarter, LocalFullIndexSource
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import Filing, Issuer, IssuerIdentifier, Security

DEFAULT_INDEX = "/Users/ericsasson/Documents/TradeItData/edgar/full-index"
DEFAULT_CACHE = "/Users/ericsasson/Documents/TradeItData/out/edgar_facts.json"
SOURCE = "edgar_full_index"


@dataclass(frozen=True, slots=True)
class Registrant:
    cik: int
    name: str
    accession: str
    form: str
    filed: dt.date


def _scan(
    index_root: Path, wanted: set[int]
) -> tuple[dict[int, Registrant], list[tuple[str, int, str, dt.date]]]:
    """One pass over the index, collecting only the registrants asked for.

    The pass is the expensive part and its cost does not depend on how many
    CIKs are wanted, so the filter is applied per row rather than by reading
    the archive twice.
    """
    source = LocalFullIndexSource(root=index_root)
    registrants: dict[int, Registrant] = {}
    filings: list[tuple[str, int, str, dt.date]] = []
    for row in source.rows(IndexQuarter(1994, 3), IndexQuarter(2026, 2)):
        if row.cik not in wanted:
            continue
        filings.append((row.accession, row.cik, row.form_type, row.filed_at))
        held = registrants.get(row.cik)
        if held is None or row.filed_at < held.filed:
            registrants[row.cik] = Registrant(
                cik=row.cik,
                name=row.company_name.strip() or f"CIK {row.cik}",
                accession=row.accession,
                form=row.form_type,
                filed=row.filed_at,
            )
        elif row.filed_at >= held.filed and row.company_name.strip():
            # The name kept is the most recent one seen, matching how
            # build_timelines names an issuer; the citation keeps the earliest
            # filing, which is a different question.
            registrants[row.cik] = Registrant(
                cik=held.cik,
                name=row.company_name.strip(),
                accession=held.accession,
                form=held.form,
                filed=held.filed,
            )
    return registrants, filings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-root", default=DEFAULT_INDEX)
    ap.add_argument("--edgar-cache", default=DEFAULT_CACHE)
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--from-year", type=int, required=True)
    ap.add_argument("--to-year", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    exits = {
        int(cik): dt.date.fromisoformat(value)
        for cik, value in json.loads(Path(args.edgar_cache).read_text())["exits"].items()
    }
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    held = {
        int(v)
        for v in session.scalars(
            select(IssuerIdentifier.value_normalized).where(IssuerIdentifier.namespace == "sec_cik")
        ).all()
    }

    wanted = {
        cik
        for cik, when in exits.items()
        if args.from_year <= when.year <= args.to_year and cik not in held
    }
    in_window = [c for c, w in exits.items() if args.from_year <= w.year <= args.to_year]
    print(f"dated exits in {args.from_year}-{args.to_year}: {len(in_window):,}")
    print(f"  already held    : {sum(1 for c in in_window if c in held):,}")
    print(f"  to seed         : {len(wanted):,}")
    if not wanted:
        return 0

    print("one pass over the EDGAR full index ...")
    registrants, filings = _scan(Path(args.index_root), wanted)
    print(f"  registrants found in the index: {len(registrants):,}")
    missing = wanted - set(registrants)
    if missing:
        # A CIK with a dated exit and no row in the index would mean the
        # denominator and the archive disagree, which is worth saying out loud.
        print(f"  WANTED BUT ABSENT FROM THE INDEX: {len(missing):,}")

    if args.dry_run:
        for cik in sorted(registrants)[:8]:
            r = registrants[cik]
            print(f"    {r.cik:>9}  {r.name[:42]:<42} {r.form:<8} {r.filed}  {r.accession}")
        print("\n(dry run; nothing written)")
        return 0

    created = 0
    for cik in sorted(registrants):
        r = registrants[cik]
        issuer = Issuer(
            display_name=r.name[:256], note=f"index-cohort/{args.from_year}", source=SOURCE
        )
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
                    f"registrant {cik} filed {r.form} {r.accession} on {r.filed} as "
                    f'"{r.name}" (SEC EDGAR full index). CIK is EDGAR\'s own key for a '
                    "registrant."
                ),
                source=SOURCE,
            )
        )
        session.add(
            Security(
                issuer_id=issuer.issuer_id,
                security_type="common_stock",
                class_label=None,
                currency="USD",
                note="placeholder for the reporting entity's equity; no class evidenced",
                source=SOURCE,
            )
        )
        created += 1
        if created % 500 == 0:
            session.commit()
            print(f"    issuers {created:,}/{len(registrants):,}", flush=True)
    session.commit()

    by_cik = {
        int(v): i
        for v, i in session.execute(
            select(IssuerIdentifier.value_normalized, IssuerIdentifier.issuer_id).where(
                IssuerIdentifier.namespace == "sec_cik"
            )
        ).all()
    }
    have = {a for (a,) in session.execute(select(Filing.accession)).all()}
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
    print(f"\nissuers created: {created:,}   filings created: {added:,}")
    print("No ticker alias was created: nothing here read a filing's words.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
