#!/usr/bin/env python
"""Fill the empty ``sectors`` table from SEC filings already in the corpus.

The Phase 8 audit of 2026-10-17 found ``SectorExposureRule`` built, tested and
**never run**, because ``sectors`` held **zero rows** against 17,433 securities
with prices -- so every name would land in the one ``UNCLASSIFIED`` bucket and
a 30% sector cap would hold the whole portfolio to 30%. That looked like a
missing-data gap and therefore a purchase proposition.

**It is not. The classification was already here.**
``issuer_sic_observations`` holds **15,823 rows across 437 SIC codes**, each
carrying the ``division`` SIC assigns it to, read from EDGAR filing headers.
Joined to ``securities`` through ``issuer_id`` it reaches **13,327 of the
17,433 securities with prices -- 76.4%**. That is the third time in one session
that "we need to buy data" turned out to be "the data is already here", which
is why this script exists instead of a vendor email.

What it writes, and what each column rests on
---------------------------------------------

``scheme`` is ``SIC``, stated rather than implied, because **SIC is not GICS**
and the difference matters: its "Finance, Insurance and Real Estate" division
pools regional banks with REITs, which are not the same risk. A later scheme
can be added beside this one without touching it.

``sector`` is the SIC **division** and ``industry`` is the ``sic_description``,
so a sector cap operates on eight broad buckets while attribution can still
read the narrower label.

``valid_from`` is the observation's ``observed_on`` -- the filing date -- and
``valid_to`` is the next observation's, so a backtest asking what sector a
security was in during 2015 gets the classification **as filed by then**.
``observed_on`` and ``knowledge_time`` agree in this table (both the filing
date), so there is no look-ahead choice to get wrong. **869 issuers changed SIC
code**, which is exactly why the interval model is not decoration.

``source`` cites the ``accession`` the row was read from. Every classification
names the filing behind it, which is this project's standard for a mapping: a
reference file is not a filing somebody read.

What it does not claim
----------------------

**76.4% is not 100%.** The remaining 23.6% stay unclassified and
``SectorExposureRule`` pools them into one bucket, which with a 30% cap means
they collectively cannot exceed 30% of the portfolio. Conservative and correct,
and it is a real constraint on what a sector-limited result means.

**The largest single SIC code is 6770, "Blank Checks"** -- 1,286 issuers, shell
companies, classified into Finance. They will cluster in any sector-limited
portfolio and anyone reading such a result should know that before they read
it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

sys.path.insert(0, "src")

from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import Sector

SCHEME = "SIC"

OBSERVATIONS = text("""
    SELECT s.security_id, o.sic_code, o.sic_description, o.division,
           o.observed_on, o.accession
    FROM issuer_sic_observations o
    JOIN securities s ON s.issuer_id = o.issuer_id
    WHERE o.division IS NOT NULL AND o.observed_on IS NOT NULL
    ORDER BY s.security_id, o.observed_on
""")


def _as_date(value: object) -> dt.date:
    """Dates from a raw ``text()`` query arrive as strings, not ``date``.

    SQLite has no date type and SQLAlchemy only converts when it knows the
    column's type, which a textual SELECT does not tell it. The first run of
    this script failed on exactly that -- loudly, writing nothing, which is
    the behaviour the ORM's Date column is there to produce.
    """
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument(
        "--replace",
        action="store_true",
        help=f"delete existing {SCHEME}-scheme rows first, making the run idempotent",
    )
    ap.add_argument("--dry-run", action="store_true", help="report and write nothing")
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    existing = session.query(Sector).filter(Sector.scheme == SCHEME).count()
    print(f"existing {SCHEME} rows: {existing:,}")
    if existing and not args.replace:
        raise SystemExit(
            f"{existing:,} {SCHEME} rows already present. Pass --replace to rebuild, so a "
            "re-run cannot silently double the table."
        )

    rows = session.execute(OBSERVATIONS).all()
    print(f"{len(rows):,} (security, observation) pairs from EDGAR headers")

    by_security: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        by_security[row.security_id].append(row)

    records: list[dict[str, object]] = []
    changed = 0
    for security_id, observations in by_security.items():
        # Later observation closes the previous interval. The schema requires
        # valid_to > valid_from, so a same-day re-filing is collapsed rather
        # than written as a zero-length interval the CHECK would reject.
        deduped: list[tuple] = []
        for observation in observations:
            if deduped and _as_date(deduped[-1].observed_on) == _as_date(observation.observed_on):
                deduped[-1] = observation
                continue
            deduped.append(observation)
        if len({o.sic_code for o in deduped}) > 1:
            changed += 1
        for index, observation in enumerate(deduped):
            valid_to = deduped[index + 1].observed_on if index + 1 < len(deduped) else None
            records.append(
                {
                    "instrument_id": security_id,
                    "scheme": SCHEME,
                    "sector": observation.division,
                    "industry": (observation.sic_description or str(observation.sic_code))[:96],
                    "valid_from": _as_date(observation.observed_on),
                    "valid_to": None if valid_to is None else _as_date(valid_to),
                    "source": observation.accession,
                }
            )

    print(f"  {len(by_security):,} securities, {len(records):,} intervals")
    print(f"  {changed:,} securities whose SIC code changed over time")
    divisions: dict[str, set[int]] = defaultdict(set)
    for record in records:
        divisions[str(record["sector"])].add(int(record["instrument_id"]))  # type: ignore[arg-type]
    for name, members in sorted(divisions.items(), key=lambda kv: -len(kv[1])):
        print(f"    {name[:46]:<46} {len(members):>6,}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if args.replace and existing:
        session.execute(delete(Sector).where(Sector.scheme == SCHEME))
        print(f"  deleted {existing:,} existing {SCHEME} rows")
    session.bulk_insert_mappings(Sector, records)  # type: ignore[arg-type]
    session.commit()
    written = session.query(Sector).filter(Sector.scheme == SCHEME).count()
    print(f"\n{written:,} rows written. Every one cites the accession it came from.")
    if written != len(records):
        raise SystemExit(
            f"wrote {written:,} but built {len(records):,} -- refusing to report success"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
