"""Reading the SEC Financial Statement Data Sets into fundamental facts.

The free, authoritative source for as-filed statement values from 2009. One ZIP
per quarter containing tab-separated ``sub.txt`` (one row per filing) and
``num.txt`` (one row per number), joined on the accession ``adsh``.

**``filed`` is the knowledge time, and ``ddate`` is never allowed to be.**
`RESEARCH_01_DATA_CONTRACT` §6 states the rule and this is where it bites: one
10-K carries several years of comparatives, so a single filing contains numbers
whose periods end long before it was submitted. Dating any of them by their
period would make a 2011 comparative usable in 2011, which it was not -- the
restated view of it did not exist until the 2014 filing disclosed it.

**Only consolidated rows are loaded.** ``num.txt`` carries dimensional
breakdowns in ``segments`` and ``coreg`` -- revenue by geography, by business
unit, by co-registrant. A row with either populated is **skipped and counted**,
never summed into or confused with the consolidated figure of the same name.
Flattening them would silently double-count exactly the headline metrics
everything downstream reads.

**A known and recorded modelling gap.** Fundamentals are an *issuer's* facts —
revenue belongs to a company, not to a share class — while this table keys on
``security_id``. Each seeded issuer currently holds exactly one security, so the
attachment is unambiguous today; an issuer with several securities is reported
as ambiguous rather than having its revenue arbitrarily assigned to one class.
Whether these facts should key on the issuer instead is a real question and is
**left open rather than settled by whichever import ran first**.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01.filings import resolve_issuer
from tradeit.research01.importer import ImportResult, RejectedBar, RejectReason
from tradeit.storage.tables import Security, SecurityFundamentalFact

__all__ = ["FsdsFact", "FsdsSubmission", "import_fsds_quarter", "read_quarter"]

#: How ``qtrs`` renders into the four characters ``fiscal_period`` allows.
#: Anything else keeps its own duration in ``duration_qtrs`` and is labelled
#: generically rather than forced into a quarter it does not describe.
_PERIOD_LABEL = {0: "I", 1: "Q", 2: "H", 4: "FY"}


@dataclass(frozen=True, slots=True)
class FsdsSubmission:
    """One filing from ``sub.txt``. ``filed`` is the only date that may date it."""

    adsh: str
    cik: int
    name: str
    form: str
    filed: dt.date
    period: dt.date | None
    fiscal_year: int | None
    fiscal_period: str


@dataclass(frozen=True, slots=True)
class FsdsFact:
    """One consolidated number from ``num.txt``."""

    adsh: str
    tag: str
    ddate: dt.date
    qtrs: int
    uom: str
    value: Decimal | None


def _date(raw: str) -> dt.date | None:
    raw = raw.strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return dt.date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError:
        return None


def read_quarter(path: Path) -> tuple[dict[str, FsdsSubmission], Iterator[FsdsFact]]:
    """Read one quarterly ZIP: submissions keyed by accession, and its numbers.

    ``num.txt`` is streamed rather than materialised -- a single quarter runs to
    hundreds of megabytes and several million rows, and holding one in memory to
    load it is a needless way to make the importer fail on a laptop.
    """
    archive = zipfile.ZipFile(path)

    submissions: dict[str, FsdsSubmission] = {}
    with archive.open("sub.txt") as handle:
        reader = csv.DictReader(io.TextIOWrapper(handle, "latin-1"), delimiter="\t")
        for row in reader:
            filed = _date(row.get("filed", ""))
            cik = row.get("cik", "").strip()
            if filed is None or not cik.isdigit():
                continue
            fy = row.get("fy", "").strip()
            submissions[row["adsh"]] = FsdsSubmission(
                adsh=row["adsh"],
                cik=int(cik),
                name=row.get("name", "").strip(),
                form=row.get("form", "").strip(),
                filed=filed,
                period=_date(row.get("period", "")),
                fiscal_year=int(fy) if fy.isdigit() else None,
                fiscal_period=row.get("fp", "").strip(),
            )

    def facts() -> Iterator[FsdsFact]:
        with archive.open("num.txt") as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, "latin-1"), delimiter="\t")
            for row in reader:
                # Dimensional rows are not consolidated figures and are not
                # loaded. Skipped here rather than filtered later, so nothing
                # downstream ever sees one.
                if row.get("segments", "").strip() or row.get("coreg", "").strip():
                    continue
                ddate = _date(row.get("ddate", ""))
                qtrs = row.get("qtrs", "").strip()
                if ddate is None or not qtrs.isdigit():
                    continue
                raw = row.get("value", "").strip()
                try:
                    value = Decimal(raw) if raw else None
                except InvalidOperation:
                    value = None
                yield FsdsFact(
                    adsh=row["adsh"],
                    tag=row.get("tag", "").strip(),
                    ddate=ddate,
                    qtrs=int(qtrs),
                    uom=row.get("uom", "").strip() or "USD",
                    value=value,
                )

    return submissions, facts()


def _security_for_issuer(session: Session, issuer_id: int) -> tuple[int | None, str]:
    rows = session.scalars(
        select(Security.security_id).where(Security.issuer_id == issuer_id)
    ).all()
    if not rows:
        return None, "issuer has no security"
    if len(rows) > 1:
        return None, f"issuer holds {len(rows)} securities; fundamentals are issuer-level"
    return rows[0], ""


def import_fsds_quarter(session: Session, path: Path, *, limit: int | None = None) -> ImportResult:
    """Load one quarterly ZIP for issuers we have evidenced identity for.

    Everything else is reported. Only CIKs already in ``issuer_identifiers``
    resolve -- the corpus is not grown by an import, here or anywhere.
    """
    submissions, facts = read_quarter(path)
    result = ImportResult()

    # CIK -> security, resolved once per filing rather than per number: a
    # quarter holds millions of numbers across a few thousand filings.
    resolved: dict[str, tuple[int | None, str]] = {}
    for adsh, sub in submissions.items():
        issuer_id = resolve_issuer(session, namespace="sec_cik", value=str(sub.cik))
        if issuer_id is None:
            resolved[adsh] = (None, f"no issuer carries sec_cik:{sub.cik}")
            continue
        resolved[adsh] = _security_for_issuer(session, issuer_id)

    seen: set[tuple[int, str, int, str, int, dt.datetime]] = set()
    landed = 0
    for fact in facts:
        if limit is not None and landed >= limit:
            break
        submission = submissions.get(fact.adsh)
        if submission is None:
            continue
        security_id, _why = resolved[fact.adsh]
        if security_id is None:
            # Reported once per filing, not once per number: a single unmapped
            # registrant would otherwise produce thousands of identical rejects.
            continue

        knowledge_time = dt.datetime.combine(submission.filed, dt.time.min, tzinfo=dt.UTC)
        event_time = dt.datetime.combine(fact.ddate, dt.time.min, tzinfo=dt.UTC)
        period_label = _PERIOD_LABEL.get(fact.qtrs, "D")
        key = (
            security_id,
            fact.tag,
            fact.ddate.year,
            period_label,
            fact.qtrs,
            knowledge_time,
        )
        if key in seen:
            continue
        seen.add(key)

        session.add(
            SecurityFundamentalFact(
                security_id=security_id,
                metric=fact.tag,
                fiscal_year=fact.ddate.year,
                fiscal_period=period_label,
                duration_qtrs=fact.qtrs,
                period_end=fact.ddate,
                event_time=event_time,
                knowledge_time=knowledge_time,
                knowledge_source="sec_fsds",
                value=fact.value,
                unit=fact.uom,
                basis="as_reported",
                source=path.name,
            )
        )
        landed += 1

    unmapped = {
        submissions[adsh].cik: why
        for adsh, (security_id, why) in resolved.items()
        if security_id is None
    }
    for cik, why in unmapped.items():
        result.rejected.append(RejectedBar(None, RejectReason.NO_ISSUER, f"sec_cik:{cik}", why))
    result.landed = landed
    result.securities_touched = {s for s, _ in resolved.values() if s is not None}
    session.flush()
    return result
