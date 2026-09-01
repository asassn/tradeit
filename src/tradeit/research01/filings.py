"""Landing EDGAR filings against seeded issuer identity.

A filing belongs to a **registrant**, so this keys on ``issuer_id`` and never on
a security: a 10-K is the issuer's, and a Form 25 names a class the quarterly
index does not identify.

**The rule that makes this more than a join.** An issuer is found through
``issuer_identifiers`` only. ``issuer_related_identities`` is not consulted and
must never be, because a related identity is one that *appears* to describe an
issuer and has not been shown to. ``FRC`` is the live case: SEC CIK ``1132979``
sits in its related table, so filings under that CIK **do not attach to FRC** —
they are reported unresolved. Resolving them would assert a sameness the
evidence explicitly declines to establish, and it would do so silently.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.edgar.index import IndexQuarter, LocalFullIndexSource
from tradeit.research01.importer import ImportResult, RejectedBar, RejectReason
from tradeit.storage.tables import Filing, IssuerIdentifier

__all__ = [
    "FilingRow",
    "import_filings",
    "import_filings_from_index",
    "resolve_issuer",
]


@dataclass(frozen=True, slots=True)
class FilingRow:
    """One index row, before any identity is attached. Form type kept verbatim."""

    cik: int
    form_type: str
    filed_at: dt.date
    accession: str
    source_path: str = ""
    period_end: dt.date | None = None


def resolve_issuer(session: Session, *, namespace: str, value: str) -> int | None:
    """Which issuer this registry identifier belongs to, or ``None``.

    Both ``primary`` and ``corroborating`` identifiers resolve: each is an
    identifier *of* this issuer, and the roles differ in what discriminates,
    not in what they identify. Related identities resolve to nothing.
    """
    return session.scalars(
        select(IssuerIdentifier.issuer_id).where(
            IssuerIdentifier.namespace == namespace,
            IssuerIdentifier.value_normalized == str(int(value)),
        )
    ).first()


def import_filings(session: Session, rows: list[FilingRow], source: str) -> ImportResult:
    """Land filings for issuers we have evidenced identity for; report the rest.

    Idempotent on ``accession``, which is the SEC's own identifier, so replaying
    an index range inserts nothing new.
    """
    result = ImportResult()
    for row in rows:
        issuer_id = resolve_issuer(session, namespace="sec_cik", value=str(row.cik))
        if issuer_id is None:
            result.rejected.append(
                RejectedBar(
                    row,
                    RejectReason.NO_ISSUER,
                    f"sec_cik:{row.cik}",
                    "no issuer carries this identifier",
                )
            )
            continue
        if session.scalars(
            select(Filing.filing_id).where(Filing.accession == row.accession)
        ).first():
            result.rejected.append(
                RejectedBar(row, RejectReason.DUPLICATE, f"sec_cik:{row.cik}", row.accession)
            )
            continue
        session.add(
            Filing(
                issuer_id=issuer_id,
                accession=row.accession,
                form_type=row.form_type,
                filed_at=row.filed_at,
                period_end=row.period_end,
                source_path=row.source_path or None,
                source=source,
            )
        )
        result.landed += 1
        result.securities_touched.add(issuer_id)
    session.flush()
    return result


def import_filings_from_index(
    session: Session,
    *,
    index_root: Path,
    start: IndexQuarter,
    end: IndexQuarter,
) -> ImportResult:
    """Load filings from the quarterly full-index, for issuers we already hold.

    **Filtered while streaming, not after.** The index carries tens of millions
    of rows and the seeded corpus is a few dozen registrants, so every row is
    tested against the known CIK set as it is read and discarded if it does not
    match. Materialising the index to filter it afterwards would need gigabytes
    to end up with a few thousand rows.

    The CIK set is read once, up front: an import may not grow the corpus, so
    the set cannot change while the scan runs.
    """
    known: dict[int, int] = {
        int(value): issuer_id
        for value, issuer_id in session.execute(
            select(IssuerIdentifier.value_normalized, IssuerIdentifier.issuer_id).where(
                IssuerIdentifier.namespace == "sec_cik"
            )
        ).all()
    }
    if not known:
        return ImportResult()

    source = LocalFullIndexSource(root=index_root)
    rows = [
        FilingRow(
            cik=row.cik,
            form_type=row.form_type,
            filed_at=row.filed_at,
            accession=row.accession,
            source_path=row.path,
        )
        for row in source.rows(start, end)
        if row.cik in known
    ]
    return import_filings(session, rows, "edgar_full_index")
