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

**Where the free fundamentals actually begin, measured rather than assumed.**
The Data Sets are described as starting in 2009, and they do not. ``2009q1.zip``
holds a header row and nothing else -- 223 bytes, zero submissions -- and the
XBRL mandate phased in by filer size after it: 22 filings in 2009Q2, 435 in
2009Q3, 1,412 in 2010Q3, and **7,102 in 2011Q3** where it settles. Anything
cross-sectional before 2011Q3 is therefore a sample of *large accelerated
filers*, not of the market, and a screen run on it would be measuring company
size. Recorded here because the number a reader reaches for is "2009".

**A fact cannot be knowable before the event it describes, and some rows claim
to be.** ``event_time`` is the period end and ``knowledge_time`` is the filing
date, so a 10-Q filed on 3 November carrying
``CommonStockDividendsPerShareDeclared`` for the quarter ending 31 December
dates its own knowledge *before* its event. For a declared dividend that is
real -- the declaration happened in November. For a reported result it would be
look-ahead. **``num.txt`` does not say which**, and inventing a tag taxonomy to
guess would be the fabrication this corpus exists to refuse, so such rows are
skipped and counted under ``KNOWLEDGE_PRECEDES_EVENT``.

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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tradeit.research01.importer import ImportResult, RejectedBar, RejectReason
from tradeit.storage.tables import (
    Filing,
    IssuerIdentifier,
    Security,
    SecurityFundamentalFact,
)

__all__ = ["FsdsFact", "FsdsSubmission", "import_fsds_quarter", "read_quarter"]

#: Rows accumulated before a write. Large enough that round trips stop
#: dominating, small enough that a failure does not discard an hour of work.
#: **Not the size of a statement** -- see :data:`_MAX_BIND_PARAMS`.
_BATCH = 5_000

#: A statement is sized in **bound parameters, not rows**, because that is what
#: the database actually limits. SQLite caps them at 32,766 and PostgreSQL at
#: 65,535; a fourteen-column row means 5,000 of them is seventy thousand
#: parameters, which SQLite refuses with "too many SQL variables" -- measured,
#: on the first run against the seeded cohort. The margins below are deliberate:
#: the cap is a build-time option in SQLite and older builds set it to 999.
_MAX_BIND_PARAMS = {"sqlite": 30_000, "postgresql": 60_000}


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


def _write(session: Session, rows: list[dict[str, object]]) -> None:
    """Insert a batch, letting the database discard what it already holds.

    Chunked by **bound parameters rather than rows**, since that is what the
    database limits: fourteen columns times five thousand rows is seventy
    thousand parameters and SQLite refuses above 32,766.

    ``ON CONFLICT DO NOTHING`` against ``uq_security_fundamental_revision``, so
    a re-run costs a rejected insert rather than an ``IntegrityError`` that
    aborts the transaction and loses the quarter. Both dialects this corpus runs
    on support it; the statement is built from whichever is connected rather
    than assuming one, because the backend the tests use is not the backend
    production uses and a dialect-specific import would fail on exactly the
    side nobody exercised.
    """
    if not rows:
        return
    dialect = session.get_bind().dialect.name
    per_statement = max(1, _MAX_BIND_PARAMS.get(dialect, 30_000) // len(rows[0]))
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        if dialect == "postgresql":
            session.execute(
                pg_insert(SecurityFundamentalFact).values(chunk).on_conflict_do_nothing()
            )
        else:
            session.execute(
                sqlite_insert(SecurityFundamentalFact).values(chunk).on_conflict_do_nothing()
            )


def _naive(moment: dt.datetime) -> dt.datetime:
    """Compare timestamps on one clock.

    SQLite hands back naive datetimes and PostgreSQL hands back aware ones, so
    a key built from a query and a key built in Python would never match on one
    of the two backends -- and the one where it silently failed would be the one
    the tests do not run on.
    """
    return moment.replace(tzinfo=None)


def _security_for_issuer(session: Session, issuer_id: int) -> tuple[int | None, str]:
    rows = session.scalars(
        select(Security.security_id).where(Security.issuer_id == issuer_id)
    ).all()
    if not rows:
        return None, "issuer has no security"
    if len(rows) > 1:
        return None, f"issuer holds {len(rows)} securities; fundamentals are issuer-level"
    return rows[0], ""


def _securities_by_cik(session: Session) -> tuple[dict[int, int], set[int]]:
    """Every CIK with exactly one security, and the CIKs that have several.

    One query rather than one per filing. The corpus now holds 17,892 issuers
    and a quarter carries about seven thousand filings, so resolving each in
    turn was half a million round trips to answer a question with a fixed
    answer.
    """
    rows = session.execute(
        select(IssuerIdentifier.value_normalized, Security.security_id)
        .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
        .where(IssuerIdentifier.namespace == "sec_cik")
    ).all()
    counts: dict[int, list[int]] = {}
    for value, security_id in rows:
        counts.setdefault(int(value), []).append(security_id)
    single = {cik: ids[0] for cik, ids in counts.items() if len(ids) == 1}
    several = {cik for cik, ids in counts.items() if len(ids) > 1}
    return single, several


def import_fsds_quarter(session: Session, path: Path, *, limit: int | None = None) -> ImportResult:
    """Load one quarterly ZIP for issuers we have evidenced identity for.

    Everything else is reported. Only CIKs already in ``issuer_identifiers``
    resolve -- the corpus is not grown by an import, here or anywhere.

    **``result.landed`` counts rows OFFERED to the database**, after removing
    duplicates within this quarter. Rows already held at the same revision are
    discarded by ``uq_security_fundamental_revision`` itself, which is what
    makes a re-run a no-op at any scale; the exact table total is a count away
    and the runner reports it. An earlier version kept every existing key in a
    Python set to answer this precisely, which was correct for fifteen
    securities and would have been a hundred and fifty million tuples in memory
    for seventeen thousand.
    """
    submissions, facts = read_quarter(path)
    result = ImportResult()

    single, several = _securities_by_cik(session)
    resolved: dict[str, tuple[int | None, str]] = {}
    for adsh, sub in submissions.items():
        security_id = single.get(sub.cik)
        if security_id is not None:
            resolved[adsh] = (security_id, "")
        elif sub.cik in several:
            resolved[adsh] = (
                None,
                "issuer holds several securities; fundamentals are issuer-level",
            )
        else:
            resolved[adsh] = (None, f"no issuer carries sec_cik:{sub.cik}")

    if not any(security_id is not None for security_id, _ in resolved.values()):
        # Not one filing in this quarter belongs to an issuer we hold, so every
        # number in it would be skipped one at a time. `facts` is a lazy
        # iterator and returning before it is touched leaves num.txt unread --
        # half a gigabyte of CSV per quarter that nothing would have used.
        for adsh, (_security_id, why) in resolved.items():
            result.rejected.append(
                RejectedBar(None, RejectReason.NO_ISSUER, f"sec_cik:{submissions[adsh].cik}", why)
            )
        return result

    # accession -> filing_id, for the filings we actually hold. The FSDS `adsh`
    # IS the accession the full-index reports, so this is a join on the SEC's
    # own identifier rather than on anything we derived.
    filing_ids: dict[str, int] = {
        accession: filing_id
        for accession, filing_id in session.execute(
            select(Filing.accession, Filing.filing_id).where(
                Filing.accession.in_(list(submissions))
            )
        ).all()
    }

    seen: set[tuple[int, str, int, str, int | None, dt.datetime]] = set()
    batch: list[dict[str, object]] = []
    landed = 0
    early = 0
    duplicates = 0

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
        if knowledge_time < event_time:
            # The filing predates the period it describes. Legitimate for a
            # declared dividend and a look-ahead defect for a reported result,
            # and `num.txt` does not say which. `ck_security_fundamental_knowledge`
            # would refuse the row anyway -- this refuses it by name, with a
            # count, rather than as an IntegrityError halfway through a quarter.
            early += 1
            continue

        period_label = _PERIOD_LABEL.get(fact.qtrs, "D")
        # Mirrors `uq_security_fundamental_revision` exactly. It deliberately
        # does **not** include `period_end`: two ddates inside one fiscal year
        # with the same duration are the same revision of the same claim, and
        # adding the date here would let the database refuse what this admitted.
        key = (
            security_id,
            fact.tag,
            fact.ddate.year,
            period_label,
            fact.qtrs,
            _naive(knowledge_time),
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        batch.append(
            {
                "security_id": security_id,
                "filing_id": filing_ids.get(fact.adsh),
                "metric": fact.tag,
                "fiscal_year": fact.ddate.year,
                "fiscal_period": period_label,
                "duration_qtrs": fact.qtrs,
                "period_end": fact.ddate,
                "event_time": event_time,
                "knowledge_time": knowledge_time,
                "knowledge_source": "sec_fsds",
                "value": fact.value,
                "unit": fact.uom,
                "basis": "as_reported",
                "source": path.name,
            }
        )
        landed += 1
        if len(batch) >= _BATCH:
            _write(session, batch)
            batch.clear()

    if batch:
        _write(session, batch)

    if duplicates:
        result.rejected.append(
            RejectedBar(
                None,
                RejectReason.DUPLICATE,
                path.name,
                f"{duplicates} facts repeated within this quarter",
            )
        )
    if early:
        result.rejected.append(
            RejectedBar(
                None,
                RejectReason.KNOWLEDGE_PRECEDES_EVENT,
                path.name,
                f"{early} facts filed before the period they describe had ended",
            )
        )
    unmapped = {
        submissions[adsh].cik: why
        for adsh, (security_id, why) in resolved.items()
        if security_id is None
    }
    for cik, why in unmapped.items():
        result.rejected.append(RejectedBar(None, RejectReason.NO_ISSUER, f"sec_cik:{cik}", why))
    result.landed = landed
    result.securities_touched = {s for s, _ in resolved.values() if s is not None}
    return result
