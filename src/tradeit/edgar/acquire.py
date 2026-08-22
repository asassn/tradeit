"""Acquire and extract control-identity evidence. It never concludes anything.

**This tool proposes; a human disposes.** It performs the repetitive half of a
Milestone 0b control investigation -- find the candidate registrant, fetch the
filing, validate the envelope, locate the primary document, pull out the
passages that bear on identity -- and then stops and prints what it found. It
writes no evidence record, assigns no ``MappingStatus``, and cannot move a
control's status, because promotion to ``MANUAL_VERIFIED`` means *a person read
the filing*, and a program that reads it for you has not satisfied that.

That boundary is enforced structurally rather than by convention: nothing in this
module imports :mod:`tradeit.edgar.control_evidence`, so there is no code path
from here to the evidence file at all.

**What it optimises for, and what it does not.** The currently-listed survivor
pattern -- MSFT, CSCO, QCOM -- where a recent annual report's cover page carries
the Section 12(b) table binding class, symbol and exchange. Bankruptcies, ticker
reuse, mergers and delistings are *not* solved here. Those need adjudication of
which registrant a sentence is about, which is exactly the judgment this module
refuses to make. The form list is a parameter rather than a constant so a later
control class can supply its own, but supplying one does not make the extraction
appropriate for it.

**Every step fails closed.** An ambiguous CIK is reported as ambiguous rather
than resolved by picking the busiest candidate. A submission whose header
disagrees with the index row it was fetched for is rejected outright. A primary
document is chosen by matching its own ``<TYPE>`` against the validated form,
never by being first. Evidence that cannot be confidently associated is reported
as needing review rather than assembled into a conclusion.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradeit.data.providers.http import HttpTransport
from tradeit.edgar.controls import CONTROL_UNIVERSE, ControlSecurity
from tradeit.edgar.index import FullIndexRow, parse_index
from tradeit.edgar.submission import (
    SubmissionDocument,
    header_mismatches,
    parse_submission_header,
    split_documents,
)
from tradeit.errors import ConfigError

__all__ = [
    "DEFAULT_FORMS",
    "DEFAULT_MIN_INTERVAL_S",
    "USER_AGENT_ENV",
    "AcquisitionReport",
    "CikCandidate",
    "EvidenceExtract",
    "ExtractionStatus",
    "Outcome",
    "Section12bRow",
    "SymbolStatement",
    "acquire_control_evidence",
    "candidate_ciks",
    "extract_identity_evidence",
    "filing_path",
    "find_control",
    "pick_primary_document",
    "resolve_user_agent",
    "strip_html",
]

#: Forms the survivor pattern is written for. A parameter, not a constant, but
#: passing a different one does not make this extraction right for it.
DEFAULT_FORMS: tuple[str, ...] = ("10-K",)

#: Seconds between requests. The SEC publishes a ceiling far above this; a
#: control investigation makes a handful of requests, so there is nothing to buy
#: by approaching it and an IP block to lose.
DEFAULT_MIN_INTERVAL_S = 1.0

#: The SEC asks that automated requests identify who is making them. It is
#: supplied by the operator and never lives in source: a contact address in a
#: repository is both a leak and a lie the moment someone else runs the code.
USER_AGENT_ENV = "EDGAR_USER_AGENT"

_ARCHIVE = "https://www.sec.gov/Archives"


class Outcome(StrEnum):
    """What the run achieved. Never a verification verdict."""

    #: A validated filing was located and identity passages were extracted.
    EXTRACTED = "extracted"
    #: A validated filing was located; the passages need a human's eye.
    REVIEW_NEEDED = "review_needed"
    #: Several defensible registrants. Deliberately not resolved here.
    CIK_AMBIGUOUS = "cik_ambiguous"
    #: No candidate registrant in the local index.
    CIK_NOT_FOUND = "cik_not_found"
    #: A candidate registrant, but no filing of a requested form.
    NO_FILING = "no_filing"
    #: The submission's own header contradicted the index row.
    VALIDATION_FAILED = "validation_failed"
    #: The filing is not local and the run was told not to fetch it.
    NOT_LOCAL = "not_local"
    #: Retrieval was attempted and did not succeed.
    DOWNLOAD_FAILED = "download_failed"


class ExtractionStatus(StrEnum):
    FOUND = "found"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class CikCandidate:
    """One registrant the local index offers for a control's name."""

    cik: int
    names: tuple[str, ...]
    filings: int
    first_filed: dt.date
    last_filed: dt.date

    def summary(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "names": list(self.names),
            "filings": self.filings,
            "first_filed": self.first_filed.isoformat(),
            "last_filed": self.last_filed.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Section12bRow:
    """One row of the cover-page table, exactly as the filing renders it."""

    cells: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {"cells": list(self.cells)}


@dataclass(frozen=True, slots=True)
class SymbolStatement:
    """A narrative sentence naming a security and a symbol together.

    The verbatim matched text, never a rewritten paraphrase and never a display
    window wider than what matched: a summary of a filing is not a filing.
    """

    text: str

    def summary(self) -> dict[str, Any]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class EvidenceExtract:
    """Identity passages, reported without being interpreted.

    No field here says "the ticker is X". The table headings and row cells are
    reproduced as found so a reader can see the association the filing itself
    makes -- including where two parts of one filing render an exchange name
    differently, which is preserved rather than reconciled into an equivalence
    the filing does not assert.
    """

    status: ExtractionStatus
    section_12b_heading: str = ""
    section_12b_headers: tuple[str, ...] = ()
    section_12b_rows: tuple[Section12bRow, ...] = ()
    symbol_statements: tuple[SymbolStatement, ...] = ()
    notes: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "section_12b_heading": self.section_12b_heading,
            "section_12b_headers": list(self.section_12b_headers),
            "section_12b_rows": [r.summary() for r in self.section_12b_rows],
            "symbol_statements": [s.summary() for s in self.symbol_statements],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class AcquisitionReport:
    """Everything the run established, and nothing it did not.

    Shaped to be pasted into a later evidence-record proposition, which is why
    it carries the local path and the raw passages rather than a verdict.
    """

    control_id: str
    expected_company: str
    control_class: str
    verification_route: str
    outcome: Outcome
    cik: int | None = None
    candidates: tuple[CikCandidate, ...] = ()
    accession: str = ""
    form: str = ""
    filed_at: dt.date | None = None
    conformed_company_name: str = ""
    primary_document: str = ""
    primary_document_type: str = ""
    source_path: str = ""
    downloaded: bool = False
    evidence: EvidenceExtract | None = None
    problems: tuple[str, ...] = ()

    @property
    def status_unchanged_statement(self) -> str:
        """Printed and serialised on every run, success or failure."""
        return (
            "NO CONTROL STATUS WAS CHANGED. This tool acquires and extracts only. "
            "It did not write docs/research/control_identity_evidence.json, did not "
            "assign RESOLVED or MANUAL_VERIFIED, and did not alter any Milestone 0b "
            "count. Promotion requires a human to read the filing and a separately "
            "scoped evidence-record proposition."
        )

    def summary(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "expected_company": self.expected_company,
            "control_class": self.control_class,
            "verification_route": self.verification_route,
            "outcome": str(self.outcome),
            "cik": self.cik,
            "candidates": [c.summary() for c in self.candidates],
            "accession": self.accession,
            "form": self.form,
            "filed_at": self.filed_at.isoformat() if self.filed_at else None,
            "conformed_company_name": self.conformed_company_name,
            "primary_document": self.primary_document,
            "primary_document_type": self.primary_document_type,
            "source_path": self.source_path,
            "downloaded": self.downloaded,
            "evidence": self.evidence.summary() if self.evidence else None,
            "problems": list(self.problems),
            "control_status_changed": False,
            "statement": self.status_unchanged_statement,
        }


# ---------------------------------------------------------------------------
# control lookup
# ---------------------------------------------------------------------------


def find_control(ticker: str) -> ControlSecurity:
    """The fixture entry, or a refusal. Never an invented control."""
    wanted = ticker.strip().upper()
    for control in CONTROL_UNIVERSE:
        if control.ticker.upper() == wanted:
            return control
    raise ConfigError(
        f"unknown control {ticker!r}; it is not one of the 30 control securities. "
        f"Known: {', '.join(sorted(c.ticker for c in CONTROL_UNIVERSE))}"
    )


# ---------------------------------------------------------------------------
# candidate discovery, from the local index only
# ---------------------------------------------------------------------------


def _index_files(index_root: Path) -> list[Path]:
    return sorted(Path(index_root).glob("*/QTR*/form.idx"))


def _rows(path: Path) -> list[FullIndexRow]:
    return parse_index(
        path.read_text(encoding="latin-1"),
        quarter_label=f"{path.parent.parent.name}-{path.parent.name}",
        strict=False,
    ).rows


def _search_terms(name: str) -> list[str]:
    return [t for t in re.split(r"[^A-Z0-9&]+", name.upper()) if len(t) > 2][:2]


def candidate_ciks(control: ControlSecurity, index_root: Path) -> tuple[CikCandidate, ...]:
    """Registrants whose indexed name matches the control's, with their spans.

    A name match is a **lead**. It is the only way in -- the CIK is what is being
    discovered -- and it can never establish a mapping on its own, which is why
    the caller stops rather than choosing when several come back.

    **Two passes, narrow first.** The control fixture holds a company's ordinary
    name and EDGAR holds its conformed one, and they differ in exactly the way
    that breaks a conjunctive search: ``Microsoft Corporation`` is indexed as
    ``MICROSOFT CORP``, so requiring both words finds nothing. The narrow search
    runs first because when it hits it is far more specific; only if it finds
    nothing does the leading word alone run. Broadening returns *more*
    candidates, not a chosen one, so the cost of the fallback is that a human is
    asked to look -- never that the wrong registrant is picked.

    Once a CIK is a candidate, every row it has is counted, not only the rows
    whose name matched: a registrant that renamed still filed those documents,
    and counting only matched rows under-reports the span as well as the total.
    """
    terms = _search_terms(control.name)
    if not terms:
        return ()

    files = _index_files(index_root)
    matched: set[int] = set()
    for attempt in [terms, terms[:1]] if len(terms) > 1 else [terms]:
        for path in files:
            for row in _rows(path):
                if all(term in row.company_name.upper() for term in attempt):
                    matched.add(row.cik)
        if matched:
            break
    if not matched:
        return ()

    found: dict[int, dict[str, Any]] = {}
    for path in files:
        for row in _rows(path):
            if row.cik not in matched:
                continue
            entry = found.setdefault(
                row.cik,
                {"names": set(), "first": row.filed_at, "last": row.filed_at, "n": 0},
            )
            entry["names"].add(row.company_name)
            entry["first"] = min(entry["first"], row.filed_at)
            entry["last"] = max(entry["last"], row.filed_at)
            entry["n"] += 1

    return tuple(
        sorted(
            (
                CikCandidate(
                    cik=cik,
                    names=tuple(sorted(entry["names"])),
                    filings=int(entry["n"]),
                    first_filed=entry["first"],
                    last_filed=entry["last"],
                )
                for cik, entry in found.items()
            ),
            key=lambda c: c.cik,
        )
    )


def _latest_row(
    cik: int, index_root: Path, forms: Sequence[str], accession: str = ""
) -> FullIndexRow | None:
    """The most recent index row for this CIK matching an exact form value.

    Exact whole-value equality on the form, so ``10-K/A`` never answers for
    ``10-K``. An explicit accession overrides the recency choice entirely.
    """
    wanted = {f.strip().upper() for f in forms}
    rows = [
        row
        for path in _index_files(index_root)
        for row in _rows(path)
        if row.cik == cik and row.form_type.strip().upper() in wanted
    ]
    if accession:
        rows = [r for r in rows if r.accession == accession]
    if not rows:
        return None
    rows.sort(key=lambda r: (r.filed_at, r.accession))
    return rows[-1]


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def resolve_user_agent(explicit: str | None = None, env: dict[str, str] | None = None) -> str:
    """The SEC identification string, from the operator. Never from source.

    Fails loudly when absent rather than sending a default: an unidentified
    automated request is the one the SEC is entitled to block, and discovering
    that through a block is expensive.
    """
    value = explicit or (env if env is not None else dict(os.environ)).get(USER_AGENT_ENV) or ""
    value = value.strip()
    if not value:
        raise ConfigError(
            "SEC requests must identify the requester. Set the "
            f"{USER_AGENT_ENV} environment variable, or pass --user-agent, to a string "
            "carrying a project name and a contact address, e.g. "
            f"{USER_AGENT_ENV}='TradeIt research (you@example.com)'. It is deliberately "
            "not defaulted in source: a contact address committed to a repository is "
            "both a leak and wrong for whoever runs it next."
        )
    return value


def filing_path(filings_root: Path, cik: int, accession: str) -> Path:
    """``<filings-root>/<CIK>/<accession>.txt``. Constructed, never searched.

    A glob for the accession would find a copy filed under some other CIK and
    call it this one's.
    """
    return Path(filings_root) / str(cik) / f"{accession}.txt"


def _submission_url(path: str) -> str:
    return f"{_ARCHIVE}/{path.lstrip('/')}"


def download_submission(
    row: FullIndexRow,
    filings_root: Path,
    *,
    user_agent: str,
    transport: HttpTransport | None = None,
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
) -> Path:
    """Fetch one complete submission, or leave nothing behind.

    The bytes land in a run-owned ``.part`` file first and are published with
    :func:`os.link`, which fails rather than overwriting if the destination
    appeared meanwhile. ``os.replace`` is not used anywhere in this path: it
    would silently clobber a filing already validated by an earlier run.

    A failed request therefore cannot leave an HTML error page sitting at a
    ``.txt`` path looking like a filing -- the ``.part`` is removed and nothing
    is published.
    """
    target = filing_path(filings_root, row.cik, row.accession)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(f".part-{os.getpid()}")

    client = transport or HttpTransport(cache=None, min_interval_s=min_interval_s)
    try:
        body = client.get(_submission_url(row.path), headers={"User-Agent": user_agent})
        part.write_bytes(body)
        # Another run publishing first is a race, not an error: theirs stands and
        # ours is discarded, because a filing already on disk has already been
        # validated and overwriting it would be the one destructive move here.
        with contextlib.suppress(FileExistsError):
            os.link(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


# ---------------------------------------------------------------------------
# document selection
# ---------------------------------------------------------------------------


def pick_primary_document(
    documents: Sequence[SubmissionDocument], form: str
) -> tuple[SubmissionDocument | None, tuple[str, ...]]:
    """The document whose own ``<TYPE>`` is the validated form, exactly.

    Not ``documents[0]``. A complete submission carries exhibits, graphics and
    XBRL alongside the report, their order is not a contract, and the envelope's
    submission type is a statement about the submission rather than about any
    one document inside it.

    Returns ``(None, reasons)`` when zero or several match: two documents both
    typed ``10-K`` is a real shape, and picking one of them would be choosing.
    """
    wanted = form.strip().upper()
    hits = [d for d in documents if d.document_type.strip().upper() == wanted]
    if len(hits) == 1:
        return hits[0], ()
    if not hits:
        seen = sorted({d.document_type for d in documents if d.document_type})
        return None, (f"no <DOCUMENT> has <TYPE> {form!r}; types present: {seen}",)
    return None, (
        f"{len(hits)} documents share <TYPE> {form!r} "
        f"({', '.join(d.filename or '?' for d in hits)}); not choosing between them",
    )


# ---------------------------------------------------------------------------
# evidence extraction
# ---------------------------------------------------------------------------

_CELL_END = re.compile(r"</\s*(td|th|p|div|tr|li|h[1-6])\s*>", re.IGNORECASE)
_ROW_END = re.compile(r"</\s*(tr|table)\s*>", re.IGNORECASE)
_BREAK = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
#: Collapses runs of spaces, tabs and the non-breaking spaces EDGAR HTML is
#: full of, which are otherwise invisible in a quotation and break comparison.
_WS = re.compile("[ \t\u00a0]+")

_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#8217;": "'",
    "&#8220;": '"',
    "&#8221;": '"',
    "&#160;": " ",
}

#: Cell and row markers survive tag stripping so a table's shape is still
#: readable afterwards. Without them every cover-page cell runs into the next
#: and "Common stock" / "MSFT" / "Nasdaq" becomes one unsplittable string.
_CELL = "␟"
_ROW = "␞"

_SECTION_12B = re.compile(
    r"Securities\s+registered\s+pursuant\s+to\s+Section\s+12\s*\(\s*b\s*\)[^\n␞]*",
    re.IGNORECASE,
)
_TITLE_HEADER = re.compile(r"Title\s+of\s+each\s+class", re.IGNORECASE)
_SYMBOL_HEADER = re.compile(r"Trading\s+Symbol", re.IGNORECASE)
_EXCHANGE_HEADER = re.compile(r"Name\s+of\s+each\s+exchange", re.IGNORECASE)

#: A sentence that names a security and a symbol in one construction. The match
#: is bounded so it cannot run across a paragraph and present two unrelated
#: clauses as one statement.
_SYMBOL_SENTENCE = re.compile(
    r"[^.␞␟]{0,200}?\b(?:common stock|common shares|class [A-Z] common stock)\b"
    r"[^.␞␟]{0,200}?\bunder the symbol\b[^.␞␟]{0,80}",
    re.IGNORECASE,
)

#: How far past the Section 12(b) heading to look for its table. Bounded so a
#: heading with no table beneath it cannot absorb an unrelated later one.
_TABLE_WINDOW = 4000


def strip_html(raw: str) -> str:
    """HTML to text, keeping cell and row boundaries visible.

    Deliberately not a parser: the goal is to make a cover-page table readable
    as cells, not to build a document model. Anything it cannot resolve stays as
    text and is reported to a human rather than guessed at.
    """
    text = _BREAK.sub(_CELL, raw)
    text = _ROW_END.sub(_ROW, text)
    text = _CELL_END.sub(_CELL, text)
    text = _TAG.sub(" ", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = re.sub(r"&#\d+;", " ", text)
    text = _WS.sub(" ", text)
    return text


def _clean(value: str) -> str:
    """Report text without the boundary markers this module inserted.

    The sentinels exist to keep a table's shape readable while tags are being
    stripped; they are an implementation detail and must never appear in a
    quotation, which is meant to be the filing's own words.
    """
    return _WS.sub(" ", value.replace(_CELL, " ").replace(_ROW, " ")).strip()


def _cells(segment: str) -> list[str]:
    return [_clean(c) for c in segment.split(_CELL) if _clean(c)]


def extract_identity_evidence(document_text: str) -> EvidenceExtract:
    """Identity passages from one filing document, reported not interpreted.

    Two independent things are looked for, and either may be absent:

    1. The cover-page table headed *Securities registered pursuant to Section
       12(b) of the Act*, with its column headings and the cells beneath them.
    2. Narrative sentences naming the security and its symbol together.

    Nothing is asserted about which cell is the symbol. The headings and the
    cells are reproduced in the order the filing puts them, and where the filing
    renders an exchange name two ways -- a table saying ``Nasdaq`` and a sentence
    saying ``NASDAQ Stock Market`` -- both survive, because treating them as
    interchangeable is a claim the filing does not make.
    """
    text = strip_html(document_text)
    notes: list[str] = []

    heading = ""
    headers: tuple[str, ...] = ()
    rows: list[Section12bRow] = []

    match = _SECTION_12B.search(text)
    if match is not None:
        heading = _clean(match.group(0))
        window = text[match.end() : match.end() + _TABLE_WINDOW]
        found = [
            label
            for label, pattern in (
                ("Title of each class", _TITLE_HEADER),
                ("Trading Symbol", _SYMBOL_HEADER),
                ("Name of each exchange on which registered", _EXCHANGE_HEADER),
            )
            if pattern.search(window)
        ]
        headers = tuple(found)
        if len(found) < 3:
            notes.append(
                "the Section 12(b) heading was found but its column headings were not all "
                f"present within {_TABLE_WINDOW} characters (found {found or 'none'}); "
                "the table may be laid out in a way this extractor cannot follow"
            )
        # Rows after the heading row, each reported whole. Which cell is the
        # symbol is left to the reader: a filing may register several classes,
        # and column order is a convention rather than a guarantee.
        last_header = max(
            (
                m.end()
                for m in (
                    p.search(window) for p in (_TITLE_HEADER, _SYMBOL_HEADER, _EXCHANGE_HEADER)
                )
                if m
            ),
            default=0,
        )
        # Collection stops at the first segment too narrow to be a row of this
        # table, because that is where the table ended. Without the stop the
        # prose after it -- an Item 5 heading and the paragraph under it -- comes
        # back as a two-cell "row" and reads like registered-securities data.
        # The scan begins mid-heading-row, so narrow segments before the first
        # real row are residue to skip. Once a row has been collected the next
        # narrow segment means the table has ended, and everything after it is
        # ordinary prose.
        width = max(len(headers), 2)
        for segment in window[last_header:].split(_ROW):
            cells = _cells(segment)
            if not cells:
                continue
            if len(cells) < width:
                if rows:
                    break
                continue
            rows.append(Section12bRow(cells=tuple(cells)))
            if len(rows) >= 8:
                break
        if not rows:
            notes.append("no table rows were readable beneath the Section 12(b) heading")
    else:
        notes.append("no 'Securities registered pursuant to Section 12(b)' heading was found")

    statements = tuple(
        # The matched core, never a widened display window: a quotation must be
        # what matched, not the neighbourhood it was found in.
        SymbolStatement(text=_clean(m.group(0)))
        for m in _SYMBOL_SENTENCE.finditer(text)
    )[:5]
    if not statements:
        notes.append("no narrative '... under the symbol ...' statement was found")

    has_table = bool(heading and len(headers) == 3 and rows)
    if has_table and statements:
        status = ExtractionStatus.FOUND
    elif has_table or statements:
        status = ExtractionStatus.PARTIAL
        notes.append(
            "only one of the two independent constructions was extracted; a human must "
            "decide whether it carries the mapping on its own"
        )
    else:
        status = ExtractionStatus.NOT_FOUND

    return EvidenceExtract(
        status=status,
        section_12b_heading=heading,
        section_12b_headers=headers,
        section_12b_rows=tuple(rows),
        symbol_statements=statements,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# the workflow
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Run:
    control: ControlSecurity
    candidates: tuple[CikCandidate, ...] = ()
    problems: list[str] = field(default_factory=list)


def acquire_control_evidence(
    ticker: str,
    *,
    index_root: Path,
    filings_root: Path,
    cik: int | None = None,
    accession: str = "",
    forms: Sequence[str] = DEFAULT_FORMS,
    offline: bool = False,
    user_agent: str | None = None,
    transport: HttpTransport | None = None,
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
) -> AcquisitionReport:
    """Run the acquisition for one control and report. Changes no status.

    ``cik`` short-circuits candidate discovery, for the case where a human has
    already established it. That is a human supplying a value, which is not the
    same as this module remembering one -- it has none to remember.

    ``offline`` refuses all network access, so an already-downloaded filing can
    be re-analysed reproducibly, and so this runs at all in an environment whose
    egress policy denies ``sec.gov``.
    """
    control = find_control(ticker)
    # Every early return is this record with more filled in. `replace` on a
    # frozen dataclass keeps that type-checked, where splatting a dict of the
    # shared fields would not: a typo in a field name would only surface when a
    # run took that particular path.
    base = AcquisitionReport(
        control_id=control.ticker,
        expected_company=control.name,
        control_class=control.control_class,
        verification_route=control.verification_route,
        outcome=Outcome.CIK_NOT_FOUND,
    )

    candidates: tuple[CikCandidate, ...] = ()
    if cik is None:
        candidates = candidate_ciks(control, Path(index_root))
        if not candidates:
            return replace(
                base,
                outcome=Outcome.CIK_NOT_FOUND,
                problems=(
                    "no registrant in the local index matches this control's name; "
                    "record the control as UNRESOLVED with this as the reason, or widen "
                    "the index coverage",
                ),
            )
        if len(candidates) > 1:
            return replace(
                base,
                outcome=Outcome.CIK_AMBIGUOUS,
                candidates=candidates,
                problems=(
                    f"{len(candidates)} registrants match this control's name. Choosing "
                    "between them is a judgment about identity, which this tool does not "
                    "make. Inspect them with `tradeit edgar cik-filings <CIK>` and re-run "
                    "with --cik once a human has decided.",
                ),
            )
        cik = candidates[0].cik

    row = _latest_row(cik, Path(index_root), forms, accession=accession)
    if row is None:
        detail = f" and accession {accession}" if accession else ""
        return replace(
            base,
            outcome=Outcome.NO_FILING,
            cik=cik,
            candidates=candidates,
            problems=(
                f"CIK {cik} has no indexed filing of form {list(forms)}{detail}. Try "
                "another form with --form, or a different accession.",
            ),
        )

    target = filing_path(Path(filings_root), row.cik, row.accession)
    downloaded = False
    if not target.exists():
        if offline:
            return replace(
                base,
                outcome=Outcome.NOT_LOCAL,
                cik=cik,
                candidates=candidates,
                accession=row.accession,
                form=row.form_type,
                filed_at=row.filed_at,
                source_path=str(target),
                problems=(
                    f"{target} is not present and --offline forbids retrieval. Re-run "
                    "without --offline in an environment that may reach sec.gov.",
                ),
            )
        agent = resolve_user_agent(user_agent)
        try:
            download_submission(
                row,
                Path(filings_root),
                user_agent=agent,
                transport=transport,
                min_interval_s=min_interval_s,
            )
        except Exception as exc:
            return replace(
                base,
                outcome=Outcome.DOWNLOAD_FAILED,
                cik=cik,
                candidates=candidates,
                accession=row.accession,
                form=row.form_type,
                filed_at=row.filed_at,
                source_path=str(target),
                problems=(f"{type(exc).__name__}: {exc}",),
            )
        downloaded = target.exists()
        if not downloaded:
            return replace(
                base,
                outcome=Outcome.DOWNLOAD_FAILED,
                cik=cik,
                candidates=candidates,
                accession=row.accession,
                form=row.form_type,
                filed_at=row.filed_at,
                source_path=str(target),
                problems=("the request returned but no file was published",),
            )

    raw = target.read_text(encoding="latin-1")
    header = parse_submission_header(raw)
    mismatches = header_mismatches(
        header,
        accession=row.accession,
        form_type=row.form_type,
        filed_at=row.filed_at,
        cik=row.cik,
    )
    if mismatches:
        return replace(
            base,
            outcome=Outcome.VALIDATION_FAILED,
            cik=cik,
            candidates=candidates,
            accession=row.accession,
            form=row.form_type,
            filed_at=row.filed_at,
            conformed_company_name=header.company_names[0] if header.company_names else "",
            source_path=str(target),
            downloaded=downloaded,
            problems=mismatches,
        )

    documents = split_documents(raw)
    primary, reasons = pick_primary_document(documents, row.form_type)
    validated = replace(
        base,
        cik=cik,
        candidates=candidates,
        accession=row.accession,
        form=row.form_type,
        filed_at=row.filed_at,
        conformed_company_name=header.company_names[0] if header.company_names else "",
        source_path=str(target),
        downloaded=downloaded,
        outcome=Outcome.REVIEW_NEEDED,
    )
    if primary is None:
        return replace(validated, problems=reasons)

    evidence = extract_identity_evidence(primary.raw)
    outcome = (
        Outcome.EXTRACTED if evidence.status is ExtractionStatus.FOUND else Outcome.REVIEW_NEEDED
    )
    return replace(
        validated,
        outcome=outcome,
        primary_document=primary.filename,
        primary_document_type=primary.document_type,
        evidence=evidence,
    )
