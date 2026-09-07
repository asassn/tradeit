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
import html
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
    "CORPORATE_FAMILY",
    "DEFAULT_FORMS",
    "DEFAULT_MIN_INTERVAL_S",
    "FUND_TRUST_FAMILY",
    "USER_AGENT_ENV",
    "AcquisitionReport",
    "CikCandidate",
    "EvidenceExtract",
    "ExtractionStatus",
    "FundListingRow",
    "FundTrustExtract",
    "Outcome",
    "Section12bRow",
    "SymbolStatement",
    "acquire_control_evidence",
    "candidate_ciks",
    "extract_fund_trust_identity",
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

#: The two identity structures this extractor knows how to read. Named so a
#: report says which one its evidence came from, because "a table row" and "a
#: prospectus sentence" are different kinds of thing to review.
CORPORATE_FAMILY = "corporate_section_12b"
FUND_TRUST_FAMILY = "fund_trust_listing"

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
class FundListingRow:
    """One row of an explicit fund/exchange/ticker table, as the filing wrote it.

    The strongest listing evidence available, because the association is the
    filing's own: three values on one row under three headings. Nothing here
    inferred which ticker belongs to which fund, which is the inference every
    prose match has to make and this one does not.
    """

    fund: str
    exchange: str
    ticker: str

    def summary(self) -> dict[str, Any]:
        return {"fund": self.fund, "exchange": self.exchange, "ticker": self.ticker}


@dataclass(frozen=True, slots=True)
class FundTrustExtract:
    """Identity passages from a fund or trust filing, reported not interpreted.

    A trust does not have a Section 12(b) cover-page table, so the corporate
    extractor correctly finds nothing in one and the run stops at NOT_FOUND. The
    identity is still there, expressed differently: a legal name under an
    explicit label, a shorthand the filing defines for itself, and a sentence
    naming the listing exchange and the symbol together.

    **Former names are text here, not events.** ``formerly known as X prior to
    <date>`` is reproduced exactly as written and nothing reads a rename, a
    validity window or a lifecycle fact out of it. A date inside a parenthetical
    is a date the filing mentions, not a date this tool has established, and the
    difference is the whole reason the field is a string rather than a date.

    **The legal name comes only from an explicit label.** A sponsor, adviser or
    distributor named elsewhere in a prospectus is not the trust, and populating
    this from any capitalised name in the document is how a sponsor's identity
    would be recorded as a security's.
    """

    #: The name itself, separated from the label that introduced it.
    exact_name: str = ""
    #: Label and value together, for citation context.
    exact_name_statement: str = ""
    former_names: tuple[str, ...] = ()
    shorthand_definitions: tuple[str, ...] = ()
    listing_statements: tuple[str, ...] = ()
    trading_statements: tuple[str, ...] = ()
    #: Rows of an explicit fund/exchange/ticker table, if the filing has one.
    listing_rows: tuple[FundListingRow, ...] = ()

    @property
    def has_legal_identity(self) -> bool:
        """A labelled legal name, and nothing weaker.

        **A shorthand is not a legal name.** ``("SPY" or the "Trust")`` tells a
        reader what the filing will call the thing for the next hundred pages; it
        does not state what the thing is. Nor do former names, which describe
        what it stopped being called. Both are reported because they are useful
        to a person reading the evidence, and neither counts here.
        """
        return bool(self.exact_name)

    @property
    def has_listing_identity(self) -> bool:
        """A construction naming a place of trading and a symbol together.

        Matching the pattern is not enough: the retained text must actually
        contain both halves. A units sentence that reaches "under the symbol"
        through two hundred characters of description, without ever naming an
        exchange or a market, describes trading in general rather than this
        security on that venue -- and a symbol with no venue attached is the sort
        of near-miss that reads like evidence at a glance.
        """
        if len(self.listing_rows) == 1:
            return True
        return any(
            _EXCHANGE_NOUN.search(statement) and _SYMBOL_CLAUSE.search(statement)
            for statement in (*self.listing_statements, *self.trading_statements)
        )

    @property
    def is_complete(self) -> bool:
        return self.has_legal_identity and self.has_listing_identity

    @property
    def has_anything(self) -> bool:
        return bool(
            self.exact_name
            or self.exact_name_statement
            or self.listing_rows
            or self.former_names
            or self.shorthand_definitions
            or self.listing_statements
            or self.trading_statements
        )

    def summary(self) -> dict[str, Any]:
        return {
            "exact_name": self.exact_name,
            "exact_name_statement": self.exact_name_statement,
            "former_names": list(self.former_names),
            "shorthand_definitions": list(self.shorthand_definitions),
            "listing_statements": list(self.listing_statements),
            "trading_statements": list(self.trading_statements),
            "listing_rows": [r.summary() for r in self.listing_rows],
        }


@dataclass(frozen=True, slots=True)
class EvidenceExtract:
    """Identity passages, reported without being interpreted.

    No field here says "the ticker is X". The table headings and row cells are
    reproduced as found so a reader can see the association the filing itself
    makes -- including where two parts of one filing render an exchange name
    differently, which is preserved rather than reconciled into an equivalence
    the filing does not assert.

    **Two evidence families, reported separately.** ``corporate_section_12b`` is
    the operating company's cover-page table plus its narrative sentences;
    ``fund_trust_listing`` is a fund or trust prospectus naming itself and its
    listing. They are not alternatives to choose between and neither is a
    fallback for the other: both run over every document, and :attr:`families`
    names the ones that produced anything, so a reader can see which structure
    the evidence actually came from.
    """

    status: ExtractionStatus
    section_12b_heading: str = ""
    section_12b_headers: tuple[str, ...] = ()
    section_12b_rows: tuple[Section12bRow, ...] = ()
    symbol_statements: tuple[SymbolStatement, ...] = ()
    fund_trust: FundTrustExtract = field(default_factory=FundTrustExtract)
    notes: tuple[str, ...] = ()

    @property
    def families(self) -> tuple[str, ...]:
        found: list[str] = []
        if self.section_12b_heading or self.section_12b_rows or self.symbol_statements:
            found.append(CORPORATE_FAMILY)
        if self.fund_trust.has_anything:
            found.append(FUND_TRUST_FAMILY)
        return tuple(found)

    def summary(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "families": list(self.families),
            "section_12b_heading": self.section_12b_heading,
            "section_12b_headers": list(self.section_12b_headers),
            "section_12b_rows": [r.summary() for r in self.section_12b_rows],
            "symbol_statements": [s.summary() for s in self.symbol_statements],
            "fund_trust": self.fund_trust.summary(),
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


def resolve_user_agent(
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    *,
    env_file: Path | None = None,
) -> str:
    """The SEC identification string, from the operator. Never from source.

    Fails loudly when absent rather than sending a default: an unidentified
    automated request is the one the SEC is entitled to block, and discovering
    that through a block is expensive.

    Looks in the environment, then in the gitignored ``.env`` at the repository
    root -- the same two places, in the same order, as the EODHD token. A
    contact address is not a secret the way a key is, but it is still the
    operator's and still must not reach a commit, and one home for both means
    one thing to set up rather than two.

    **The file is consulted only when no explicit ``env`` mapping was given.** A
    caller that supplies ``env`` is stating the environment exhaustively, and
    reaching past it to a file on disk would contradict that -- which would also
    make a test's outcome depend on whether the machine running it happened to
    have a ``.env``.
    """
    value = explicit or (env if env is not None else dict(os.environ)).get(USER_AGENT_ENV) or ""
    value = value.strip()
    if not value and env is None:
        value = _from_env_file(env_file or Path(__file__).resolve().parents[3] / ".env")
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


def _from_env_file(path: Path) -> str:
    """``EDGAR_USER_AGENT`` out of a ``.env`` file, or empty.

    An empty value counts as absent, exactly as it does for the API token: the
    first thing anyone does is copy the example file, and returning "" from
    here would hand the caller a contact-address-shaped nothing.
    """
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        if name.strip() == USER_AGENT_ENV:
            return raw.strip().strip("\"'")
    return ""


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
_ROW_END = re.compile(r"</\s*tr\s*>", re.IGNORECASE)
#: Kept distinct from the row marker. Collapsing the two loses the only signal
#: in the token stream that says *the table itself has ended*, and everything
#: after that point is prose no matter how many cell breaks it happens to have.
_TABLE_END_TAG = re.compile(r"</\s*table\s*>", re.IGNORECASE)
_BREAK = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
#: Collapses runs of spaces, tabs and the non-breaking spaces EDGAR HTML is
#: full of, which are otherwise invisible in a quotation and break comparison.
_WS = re.compile("[ \t\u00a0]+")

#: Cell, row and table markers survive tag stripping so a table's shape is still
#: readable afterwards. Without them every cover-page cell runs into the next
#: and "Common stock" / "MSFT" / "Nasdaq" becomes one unsplittable string.
_CELL = "␟"
_ROW = "␞"
_TABLE = "␝"

#: **Any** statutory registration heading, not only 12(b). A cover page states
#: 12(b) and 12(g) one after the other, and the second one starting is the first
#: one's region ending -- a structural fact about the document, not a phrase to
#: be filtered out of the results.
_SECTION_HEADING = re.compile(
    r"Securities\s+registered\s+pursuant\s+to\s+Section\s+12\s*\(\s*[a-z]\s*\)",
    re.IGNORECASE,
)
_SECTION_12B = re.compile(
    r"Securities\s+registered\s+pursuant\s+to\s+Section\s+12\s*\(\s*b\s*\)[^\n␞␝]*",
    re.IGNORECASE,
)
_TITLE_HEADER = re.compile(r"Title\s+of\s+each\s+class", re.IGNORECASE)
_SYMBOL_HEADER = re.compile(r"Trading\s+Symbol", re.IGNORECASE)
_EXCHANGE_HEADER = re.compile(r"Name\s+of\s+each\s+exchange", re.IGNORECASE)

#: A sentence that names a security and a symbol in one construction. The match
#: is bounded so it cannot run across a paragraph and present two unrelated
#: clauses as one statement.
#:
#: Every gap is ``\s+`` rather than a literal space. Filing HTML wraps its source
#: lines wherever the generator felt like it, so ``under the\nsymbol CSCO`` is
#: ordinary and a pattern requiring single spaces silently misses it -- silently,
#: because the result is an absent statement rather than an error.
_SYMBOL_SENTENCE = re.compile(
    r"[^.␞␟␝]{0,200}?\b(?:common\s+stock|common\s+shares|class\s+[A-Z]\s+common\s+stock)\b"
    r"[^.␞␟␝]{0,200}?\bunder\s+the\s+symbol\b[^.␞␟␝]{0,80}",
    re.IGNORECASE,
)

#: Straight and typographic quotation marks, both of which EDGAR HTML uses,
#: sometimes in the same document.
#: Escaped rather than written literally: the curly forms are indistinguishable
#: from the straight ones at a glance, and a quote class is a bad place for a
#: character nobody can see.
_Q = "\"'\u201c\u201d\u2018\u2019"

#: The legal-name label, through its colon and no further. A prospectus names its
#: sponsor, its depositor, its adviser and its distributor; a pattern that looked
#: for a capitalised name instead would capture any of them, and a depositor
#: recorded as the security's identity is a wrong mapping that reads like a right
#: one. The value is found structurally -- see :func:`_labelled_legal_name` --
#: rather than by letting this pattern run on, because in a real filing the label
#: and its value are in different table cells with an unknown number of empty
#: ones between them.
_EXACT_NAME_LABEL = re.compile(
    r"Exact\s+name\s+of\s+(?:the\s+)?(?:Trust|Fund|Registrant|Issuer)[^:␞␟␝]{0,60}:",
    re.IGNORECASE,
)

#: The principal-listing label, likewise through its colon. The trust name inside
#: it is what ties the listing to a security rather than to the document at large.
_PRINCIPAL_LISTING_LABEL = re.compile(
    r"Principal\s+(?:U\.?\s*S\.?\s+)?Listing\s+Exchange[^:␞␟␝]{0,160}:",
    re.IGNORECASE,
)

#: A block that is a field label and nothing else. Reaching one while looking for
#: a value means the value is absent: the document has moved on to the next
#: field, and whatever follows belongs to that field rather than this one.
_LABEL_ONLY_BLOCK = re.compile(r"^(?:[A-Za-z0-9]{1,3}[.)]\s*)?[A-Za-z][^:]{2,70}:$")

#: An enumerator cell -- "A.", "(1)", "iii." -- which carries no content and sits
#: between a field's number and its label in most SEC form layouts.
_ENUMERATOR_BLOCK = re.compile(r"^\(?[A-Za-z0-9]{1,3}[.)]?$")

#: The registration-statement caption. A different convention entirely from the
#: colon-terminated field: it carries no colon, it is usually parenthesised, and
#: the name it labels comes *before* it rather than after. A registration
#: statement is laid out as a title page, so the registrant is the title and this
#: is the note underneath saying what the title was.
_REGISTRANT_CAPTION = re.compile(
    r"^\(?\s*Exact\s+Name\s+of\s+(?:the\s+)?(?:Registrant|Trust|Fund|Issuer)"
    r"[^)␞␟␝]{0,80}\)?\.?$",
    re.IGNORECASE,
)

#: Roles a prospectus names beside the registrant. The adviser, the sponsor and
#: the depositor all appear within a block or two of the registrant's own name,
#: and any of them recorded as the security's identity is a wrong mapping that
#: reads like a right one -- which is exactly what this control's own
#: verification route warns about.
_ROLE_WORD = re.compile(
    r"\b(?:Adviser|Advisor|Sponsor|Depositor|Trustee|Distributor|Custodian|"
    r"Underwriter|Administrator|Manager|Agent|Counsel|Auditor)s?\b",
    re.IGNORECASE,
)

#: Column headings of the registration-statement listing table. Recognised as a
#: set rather than a fixed order, because the order is a layout choice.
_FUND_COLUMN = re.compile(r"^Funds?(?:\s+Name)?$", re.IGNORECASE)
_EXCHANGE_COLUMN = re.compile(r"^Principal\s+(?:U\.?\s*S\.?\s+)?Listing\s+Exchange$", re.IGNORECASE)
_TICKER_COLUMN = re.compile(r"^(?:Ticker|Trading)?\s*(?:Ticker|Symbol)$", re.IGNORECASE)

#: A plausible ticker cell. Short, and not a sentence.
_TICKER_VALUE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")

#: How many non-empty blocks past a label may be inspected for its value. Two,
#: because a label and its value are adjacent by definition and the allowance
#: exists only for the spacer blocks that layout puts between them. Scanning
#: further is how a later field's value becomes this field's answer.
_VALUE_LOOKAHEAD = 2

#: Generic listing vocabulary, deliberately not a list of venues. A construction
#: that ties a symbol to a place of trading says "Exchange" or "Market" almost
#: without exception, and requiring the noun rather than the name means a venue
#: this project has never seen still counts.
_EXCHANGE_NOUN = re.compile(r"\b(?:Exchange|Market)\b", re.IGNORECASE)

#: The symbol half of the same construction.
_SYMBOL_CLAUSE = re.compile(r"\bunder\s+the\s+(?:market\s+)?symbol\b", re.IGNORECASE)

#: Reported verbatim and never parsed. The dates inside are dates the filing
#: mentions, not dates this tool has established.
_FORMER_NAME = re.compile(r"formerly\s+known\s+as\b[^)␞␟␝]{0,300}", re.IGNORECASE)

#: A shorthand the filing defines for itself: ``... Trust ("SPY" or the
#: "Trust")``. The preceding name text is part of the matched core, because the
#: shorthand without what it abbreviates establishes nothing.
_SHORTHAND = re.compile(
    rf"[^.␞␟␝]{{0,150}}?\(\s*[{_Q}]\s*([A-Za-z0-9.\-]{{1,10}})\s*[{_Q}]\s+or\s+the\s+"
    rf"[{_Q}]\s*(?:Trust|Fund|Company|Registrant|Partnership)\s*[{_Q}]\s*\)",
    re.IGNORECASE,
)

#: An exchange and a symbol inside one construction. The fund vocabulary in the
#: lead-in is what keeps this off an operating company's "our common stock is
#: traded on X under the symbol Y", which the corporate family already reports.
#:
#: The gaps exclude the cell and row sentinels but NOT the full stop, which is
#: what bounds the corporate patterns. "NYSE Arca, Inc." and "Principal U.S.
#: Listing Exchange" are both full of periods, so a sentence-ending bound cuts
#: this family's evidence in half. A paragraph boundary is the right bound here
#: and the sentinels already are one.
_FUND_LISTING = re.compile(
    r"[^␞␟␝]{0,200}?\b(?:Principal\s+(?:U\.?\s*S\.?\s+)?Listing\s+Exchange|Trust|Fund|ETF)\b"
    r"[^␞␟␝]{0,200}?\bunder\s+the\s+(?:market\s+)?symbol\b[^␞␟␝]{0,60}",
    re.IGNORECASE,
)

#: The units/shares construction, which is a second and independent statement of
#: the same relationship in most prospectuses.
_FUND_TRADING = re.compile(
    r"[^␞␟␝]{0,200}?\b(?:Units|Shares)\b[^␞␟␝]{0,200}?"
    r"\b(?:purchased\s+and\s+sold|bought\s+and\s+sold|listed|traded)\b"
    r"[^␞␟␝]{0,200}?\bunder\s+the\s+(?:market\s+)?symbol\b[^␞␟␝]{0,60}",
    re.IGNORECASE,
)

#: How far past the Section 12(b) heading to look for its table. Bounded so a
#: heading with no table beneath it cannot absorb an unrelated later one.
_TABLE_WINDOW = 4000

#: The longest a cover-page table cell plausibly is. Cells here hold a class
#: title, a ticker and an exchange name; the longest real one in the fixtures is
#: well under half this. A cell holding a sentence is prose that happens to sit
#: between two cell breaks, and prose is not a registered security.
_MAX_CELL_CHARS = 160


def strip_html(raw: str) -> str:
    """HTML to text, keeping cell and row boundaries visible.

    Deliberately not a parser: the goal is to make a cover-page table readable
    as cells, not to build a document model. Anything it cannot resolve stays as
    text and is reported to a human rather than guessed at.
    """
    text = _BREAK.sub(_CELL, raw)
    text = _TABLE_END_TAG.sub(_TABLE, text)
    text = _ROW_END.sub(_ROW, text)
    text = _CELL_END.sub(_CELL, text)
    text = _TAG.sub(" ", text)
    # Every named and numeric entity, not a hand-written handful. A trust's name
    # is routinely written "SPDR&reg; S&P 500&reg; ETF TRUST", and evidence a
    # person is meant to read and cite must not carry markup syntax through into
    # the quotation. Decoding happens after tags are removed, so an entity can
    # never introduce one. It is decoding, not normalization: the characters
    # change, the words do not.
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    return text


def _clean(value: str) -> str:
    """Report text without the boundary markers this module inserted.

    The sentinels exist to keep a table's shape readable while tags are being
    stripped; they are an implementation detail and must never appear in a
    quotation, which is meant to be the filing's own words.
    """
    without_markers = value.replace(_CELL, " ").replace(_ROW, " ").replace(_TABLE, " ")
    # All whitespace, newlines included: a quotation that spans a source line
    # break is one sentence in the filing and should read as one here. `_WS`
    # deliberately leaves newlines alone, because the heading patterns use them
    # as a bound while matching; this is the reporting end, where they are noise.
    return re.sub(r"\s+", " ", without_markers).strip()


def _cells(segment: str) -> list[str]:
    return [_clean(c) for c in segment.split(_CELL) if _clean(c)]


_TICKER_CELL = re.compile(r"^[A-Z][A-Z0-9]{0,7}(?:[.\-][A-Z0-9]{1,3})?$")
_VENUE_CELL = re.compile(r"exchange|market|nasdaq|nyse|cboe|arca|bats", re.IGNORECASE)

#: The em dash a filing puts in the symbol column for a security that has no
#: ticker -- registered notes, mostly. It is a value, not a missing cell.
_NO_SYMBOL = {"\u2014", "\u2013", "-", "N/A", "None"}


def _transpose_column_major(cells: list[str], width: int) -> list[list[str]] | None:
    """A whole table that arrived as one segment, column by column.

    Berkshire Hathaway's cover page renders its ten registered securities so
    that the markup produces no row breaks at all: the segment holds ten class
    titles, then ten trading symbols, then ten exchange names, in that order and
    in one list. Read row-major it is a single thirty-cell row naming nothing;
    read column-major it is exactly the table.

    **Shape alone is not enough to act on**, because a wide segment of prose has
    the same arithmetic. So the transposition is only applied when the columns
    look like the columns of a 12(b) table: one whose every entry is
    ticker-shaped or the em dash a filing uses for an unlisted note, and a last
    one whose every entry names a venue. A segment that does not match is
    returned as ``None`` and left exactly as it was, because a misread table is
    worse than an unread one.
    """
    if width < 2 or len(cells) < 2 * width or len(cells) % width:
        return None
    depth = len(cells) // width
    columns = [cells[i * depth : (i + 1) * depth] for i in range(width)]
    symbol_like = any(
        all(cell in _NO_SYMBOL or _TICKER_CELL.match(cell) for cell in column)
        for column in columns[1:]
    )
    venue_like = all(_VENUE_CELL.search(cell) for cell in columns[-1])
    if not (symbol_like and venue_like):
        return None
    return [[column[row] for column in columns] for row in range(depth)]


def _collect_rows(region: str, *, width: int) -> tuple[list[Section12bRow], list[str]]:
    """Rows of the Section 12(b) table, stopping where the table stops.

    **Three structural boundaries, because width alone is not one.** The first
    version of this stopped at a segment with too few cells, which worked on a
    fixture whose post-table prose happened to be two cells wide and failed on
    the first real filing it met. Cisco's cover page puts its 12(g) line and its
    check-box paragraphs in ``<p>`` elements after ``</table>``; those are cell
    breaks, not row breaks, so the whole block arrived as one **six-cell**
    segment -- wider than the table's three columns, and so accepted as a row of
    registered securities. Being too narrow was never the thing that made a
    segment prose.

    What actually bounds the region:

    1. **A new statutory heading.** ``Securities registered pursuant to Section
       12(g)`` beginning is ``12(b)``'s region ending. Every cover page states
       them in sequence, so this is the document's own structure rather than a
       phrase being filtered out of the output.
    2. **The end of the table.** ``</table>`` carries its own marker precisely so
       this question can be asked; anything after it is outside the table
       whatever its shape. It is only treated as terminal once a row has been
       collected, so a layout that wraps the heading row in its own table is not
       cut off before it starts.
    3. **A cell holding prose.** A cover-page cell is a class title, a symbol or
       an exchange name. One holding a sentence is prose that happens to fall
       between two cell breaks.

    Each is a fact about document structure, so none of them knows anything
    about Cisco, and a filing that lays its cover page out differently is
    reported as unreadable rather than mis-read.
    """
    notes: list[str] = []

    # Boundary 1, applied before any segmentation: the region cannot extend past
    # the next statutory heading, wherever the markup happens to break.
    next_heading = _SECTION_HEADING.search(region)
    if next_heading is not None:
        region = region[: next_heading.start()]

    rows: list[Section12bRow] = []
    padded = region + _ROW
    position = 0
    for separator in re.finditer(f"[{_ROW}{_TABLE}]", padded):
        segment = padded[position : separator.start()]
        position = separator.end()
        cells = _cells(segment)

        if cells:
            if len(cells) < width:
                # Narrow: heading residue before the body starts, or a
                # continuation inside it. **Never a boundary.** Alphabet's cover
                # puts "(Nasdaq Global Select Market)" on its own one-cell row
                # between Class A and Class C; breaking here dropped Class C and
                # with it every multi-class issuer's second security. The three
                # structural boundaries below already bound the region, and
                # narrowness was never one of them -- as this docstring said
                # before the code did.
                continue
            if any(len(cell) > _MAX_CELL_CHARS for cell in cells):
                if rows:
                    notes.append(
                        "collection stopped at a block whose cells hold prose rather than "
                        "table values; a registered-security cell is a class title, a "
                        "symbol or an exchange name"
                    )
                    break
                continue
            transposed = _transpose_column_major(cells, width)
            if transposed is not None:
                notes.append(
                    "the table arrived as one column-major segment and was read "
                    f"as {len(transposed)} rows of {width}"
                )
                for row in transposed[: 8 - len(rows)]:
                    rows.append(Section12bRow(cells=tuple(row)))
            else:
                rows.append(Section12bRow(cells=tuple(cells)))
            if len(rows) >= 8:
                break

        # Boundary 2, checked after the segment it closes so the table's own last
        # row is kept.
        if separator.group(0) == _TABLE and rows:
            break

    return rows, notes


def _matches(pattern: re.Pattern[str], text: str, limit: int = 5) -> tuple[str, ...]:
    """Matched cores, cleaned and de-duplicated, never a widened window."""
    seen: list[str] = []
    for match in pattern.finditer(text):
        cleaned = _clean(match.group(0))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return tuple(seen)


def extract_fund_trust_identity(document_text: str) -> FundTrustExtract:
    """The prospectus identity constructions, if this document has any.

    Runs over every document rather than being switched on by form type. A form
    is a routing label: ``485BPOS`` is where this structure usually lives, but an
    N-1A, an S-6, a 424 prospectus or a form not yet met can carry the same
    sentences, and a filing that does carry them should not be missed because a
    list of form codes had not been updated. What decides is the text.

    Nothing is combined or interpreted. A legal name, the parenthetical history
    beside it, the shorthand the filing defines, and the sentences naming an
    exchange and a symbol are each reported as written, for a person to read.
    """
    text = strip_html(document_text)

    exact_name, exact_statement = _labelled_legal_name(text)

    listing, trading = _listing_constructions(text)

    return FundTrustExtract(
        listing_rows=tuple(_listing_table_rows(text)),
        exact_name=exact_name,
        exact_name_statement=exact_statement,
        former_names=_matches(_FORMER_NAME, text),
        shorthand_definitions=_matches(_SHORTHAND, text),
        listing_statements=listing,
        trading_statements=trading,
    )


@dataclass(frozen=True, slots=True)
class _Block:
    """One cell or paragraph of the stripped document, with its source span."""

    start: int
    end: int
    text: str


def _blocks(text: str) -> list[_Block]:
    """The document as the ordered non-empty blocks the markup separated it into.

    **Flattened text cannot express a label/value pair, and real filings are
    full of them.** ``Exact name of Trust:`` sits in one table cell and the name
    in another; ``Principal U.S. Listing Exchange for <trust>:`` is one block and
    ``NYSE Arca, Inc. under the symbol "SPY"`` the next. A regex whose gaps stop
    at the cell sentinel -- which they must, or a match would run across the
    whole page -- can never see the second half. Splitting on those same
    sentinels turns the boundary from an obstacle into the structure it actually
    is.
    """
    found: list[_Block] = []
    position = 0
    for separator in re.finditer(f"[{_CELL}{_ROW}{_TABLE}]", text + _CELL):
        cleaned = _clean(text[position : separator.start()])
        if cleaned:
            found.append(_Block(position, separator.start(), cleaned))
        position = separator.end()
    return found


def _cell_rows(text: str) -> list[tuple[int, int, list[str]]]:
    """The document as rows of cells, for the tables a block list flattens.

    :func:`_blocks` answers "what are the pieces, in order", which is what a
    label/value pair needs. A table needs the second question a block list cannot
    answer: which pieces are on the same row. Both views are built from the same
    sentinels -- ``␟`` between cells, ``␞`` and ``␝`` between rows -- so neither
    is a second parse of the document, only a second way of reading one.
    """
    out: list[tuple[int, int, list[str]]] = []
    position = 0
    for separator in re.finditer(f"[{_ROW}{_TABLE}]", text + _ROW):
        segment = text[position : separator.start()]
        cells = [_clean(cell) for cell in segment.split(_CELL)]
        kept = [cell for cell in cells if cell]
        if kept:
            out.append((position, separator.start(), kept))
        position = separator.end()
    return out


def _listing_table_rows(text: str) -> list[FundListingRow]:
    """Rows of an explicit fund/exchange/ticker table, associated by column.

    A registration statement states the relationship as a table rather than a
    sentence: ``Fund | Principal U.S. Listing Exchange | Ticker`` over a row
    naming all three. That is stronger evidence than any prose, because the
    filing itself has joined the three values -- nothing here has to infer which
    ticker belongs to which fund.

    **The three headings must be contiguous.** Their positions give the columns,
    and normalising them against the leftmost is what survives a layout where
    ordinary paragraphs share a row with the heading cells -- which happens
    whenever a table follows prose without an intervening row break. Requiring
    contiguity is also the sanity check: three headings scattered through a row
    are not a header row.
    """
    rows = _cell_rows(text)
    out: list[FundListingRow] = []

    for index, (_, _, cells) in enumerate(rows):
        columns: dict[str, int] = {}
        for position, cell in enumerate(cells):
            for name, pattern in (
                ("fund", _FUND_COLUMN),
                ("exchange", _EXCHANGE_COLUMN),
                ("ticker", _TICKER_COLUMN),
            ):
                if name not in columns and pattern.match(cell):
                    columns[name] = position
        if len(columns) < 3:
            continue
        offsets = sorted(columns.values())
        if offsets[-1] - offsets[0] != 2:
            continue
        base = offsets[0]
        width = 3

        for _, _, data in rows[index + 1 :]:
            if len(data) < width:
                break
            fund = data[columns["fund"] - base]
            exchange = data[columns["exchange"] - base]
            ticker = data[columns["ticker"] - base]
            if not _TICKER_VALUE.match(ticker):
                break
            if not any(ch.isalpha() for ch in fund) or not any(ch.isalpha() for ch in exchange):
                break
            out.append(FundListingRow(fund=fund, exchange=exchange, ticker=ticker))
            if len(out) >= 8:
                break
        break

    return out


def _value_blocks(blocks: list[_Block], index: int) -> list[_Block]:
    """The blocks that may hold the value of the label in ``blocks[index]``.

    Bounded on three sides. At most :data:`_VALUE_LOOKAHEAD` blocks are
    inspected; enumerator cells are stepped over because they are layout rather
    than content; and any block that is itself a field label ends the search
    immediately, because the document has moved on to the next field and its
    value is not this one's.
    """
    out: list[_Block] = []
    for block in blocks[index + 1 : index + 1 + _VALUE_LOOKAHEAD + 1]:
        if _ENUMERATOR_BLOCK.match(block.text):
            continue
        if _LABEL_ONLY_BLOCK.match(block.text):
            break
        out.append(block)
        if len(out) >= _VALUE_LOOKAHEAD:
            break
    return out


def _is_plausible_legal_name(candidate: str) -> bool:
    """Whether this text could be a registrant's name rather than a sentence.

    A name carries no colon, names no exchange and quotes no ticker. A candidate
    doing any of those is prose that happened to be adjacent, and refusing it is
    what keeps a listing sentence or a neighbouring field out of the one place a
    reader will read as the security's identity.
    """
    if not candidate or not any(ch.isalpha() for ch in candidate):
        return False
    if ":" in candidate:
        return False
    if _ROLE_WORD.search(candidate):
        return False
    if _REGISTRANT_CAPTION.match(candidate) or _EXACT_NAME_LABEL.search(candidate):
        return False
    return not (_EXCHANGE_NOUN.search(candidate) or _SYMBOL_CLAUSE.search(candidate))


def _labelled_legal_name(text: str) -> tuple[str, str]:
    """The value beside an explicit legal-name label, or nothing at all.

    Returns ``(value, label and value together)``. Empty when there is no label,
    when the label has no value beside it, or when what sits beside it is not
    name-shaped -- and empty is the right answer in all three, because the caller
    treats a missing legal name as incomplete rather than substituting something
    weaker. A depositor two fields down is never reached: its own label stops the
    search before its value is in view.
    """
    blocks = _blocks(text)
    for index, block in enumerate(blocks):
        match = _EXACT_NAME_LABEL.search(block.text)
        if match is None:
            continue

        inline = _clean(block.text[match.end() :])
        candidates = [inline] if inline else [b.text for b in _value_blocks(blocks, index)]
        for candidate in candidates:
            # A parenthetical history beside the name is reported separately, so
            # it is trimmed off here rather than folded into the name itself.
            name = _clean(candidate.split("(")[0])
            if _is_plausible_legal_name(name):
                return name, _clean(f"{match.group(0)} {name}")

    # The registration-statement convention: a title-page caption with no colon,
    # labelling the name ABOVE it. Tried second so a colon-terminated field is
    # preferred where a filing has both, and searched backwards first because
    # that is where this convention puts its value.
    for index, block in enumerate(blocks):
        if not _REGISTRANT_CAPTION.match(block.text):
            continue
        for value_block in _caption_value_blocks(blocks, index):
            name = _clean(value_block.text.split("(")[0])
            if _is_plausible_legal_name(name):
                return name, _clean(f"{name} {block.text}")
    return "", ""


def _caption_value_blocks(blocks: list[_Block], index: int) -> list[_Block]:
    """Blocks that may hold the name a caption labels: above first, then below.

    Bounded exactly as the forward search is, and for the same reason. Above,
    because a title page puts the name over its caption; below as well, because
    the same wording appears in cell-pair layouts where it sits beside the value.
    Enumerators are stepped over and a labelled field ends the search, so a
    caption whose own value is missing reaches nothing rather than reaching the
    adviser named underneath it.
    """
    out: list[_Block] = []
    for block in reversed(blocks[max(0, index - _VALUE_LOOKAHEAD) : index]):
        if _ENUMERATOR_BLOCK.match(block.text):
            continue
        if _LABEL_ONLY_BLOCK.match(block.text):
            break
        out.append(block)
    return out + _value_blocks(blocks, index)


def _principal_listing_constructions(blocks: list[_Block]) -> list[tuple[int, int, str]]:
    """Listing headings joined to the value block that completes them.

    The strongest listing evidence a prospectus offers, because the heading names
    *which* trust the listing is about and the value names the venue and the
    symbol. Reported as one construction so a reader sees the association the
    filing made rather than three fragments they must join themselves.

    Where the heading and its value are one block already, that block is used
    unchanged. Where they are adjacent blocks, the two are joined -- and only
    adjacent ones, so a ticker mentioned later in the document can never complete
    a heading whose own value is missing.
    """
    out: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        if _PRINCIPAL_LISTING_LABEL.search(block.text) is None:
            continue
        if _SYMBOL_CLAUSE.search(block.text) and _EXCHANGE_NOUN.search(block.text):
            out.append((block.start, block.end, block.text))
            continue
        for candidate in _value_blocks(blocks, index):
            if _SYMBOL_CLAUSE.search(candidate.text):
                out.append((block.start, candidate.end, f"{block.text} {candidate.text}"))
                break
    return out


def _listing_constructions(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Listing and units statements, with overlapping matches collapsed.

    **Two matchers over one document produce fake corroboration unless overlap is
    resolved by position.** The first real filing showed both failure directions
    at once: the broad matcher swallowed the specific *Principal U.S. Listing
    Exchange* sentence so it never appeared under its own label, and elsewhere it
    sliced a run of trust-description prose into a second "independent"
    construction beside a genuine one. Two passages a reviewer would read as
    mutual confirmation were one passage counted twice.

    Resolution is by **source span, shortest first**. A shorter match is the more
    specific reading of the same text, so it is kept and anything overlapping it
    is dropped, whichever pattern produced it. Comparing rendered strings cannot
    do this -- two overlapping matches often share no substring relationship --
    and preferring one pattern over the other was the bug, since either can be
    the broader one depending on the sentence.
    """
    # An explicitly labelled construction outranks a prose match over the same
    # text, whatever their relative lengths. The heading states which trust the
    # listing is about; a generic sentence match inside it does not, and letting
    # the shortest-span rule prefer the fragment would discard the association
    # that makes this the strongest evidence in the document.
    principal = _principal_listing_constructions(_blocks(text))
    protected = [(start, end) for start, end, _ in principal]

    found: list[tuple[int, int, str, bool]] = []
    trading_spans: list[tuple[int, int]] = []
    for pattern, is_trading in ((_FUND_LISTING, False), (_FUND_TRADING, True)):
        for match in pattern.finditer(text):
            cleaned = _clean(match.group(0))
            if not cleaned:
                continue
            found.append((match.start(), match.end(), cleaned, is_trading))
            if is_trading:
                trading_spans.append((match.start(), match.end()))

    kept: list[tuple[int, int, str, bool]] = [
        (start, end, statement, False) for start, end, statement in principal
    ]
    for start, end, cleaned, is_trading in sorted(
        found, key=lambda item: (item[1] - item[0], item[0])
    ):
        if any(start < other[1] and other[0] < end for other in protected):
            continue
        if any(start < other[1] and other[0] < end for other in kept):
            continue
        # The span decides which text is kept; the more specific pattern decides
        # what to call it. Where a units construction covers the same passage,
        # the surviving text is a units construction however it was matched --
        # otherwise the shortest-span rule quietly relabels the filing's own
        # purchase-and-sale sentence as a generic listing mention.
        labelled = is_trading or any(start < other[1] and other[0] < end for other in trading_spans)
        kept.append((start, end, cleaned, labelled))

    kept.sort(key=lambda item: item[0])
    listing = tuple(item[2] for item in kept if not item[3])[:5]
    trading = tuple(item[2] for item in kept if item[3])[:5]
    return listing, trading


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
        rows, row_notes = _collect_rows(window[last_header:], width=max(len(headers), 2))
        notes.extend(row_notes)
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
    corporate_complete = bool(has_table and statements)
    corporate_partial = bool(has_table or statements)
    if corporate_partial and not corporate_complete:
        notes.append(
            "only one of the two independent constructions was extracted; a human must "
            "decide whether it carries the mapping on its own"
        )

    # The second family, run over the same document rather than instead of it.
    # A filing is not required to be one kind of thing: whichever structures are
    # present are reported, and a document with neither is NOT_FOUND as before.
    fund = extract_fund_trust_identity(document_text)
    if len(fund.listing_rows) > 1:
        notes.append(
            f"the listing table has {len(fund.listing_rows)} fund rows; which one this "
            "control refers to has NOT been decided here, and every row is reported for "
            "a human to choose between"
        )
    if fund.has_anything and not fund.is_complete:
        missing = []
        if not fund.has_legal_identity:
            missing.append("a labelled legal name")
        if not fund.has_listing_identity:
            missing.append(
                "a single unambiguous listing row, or a construction naming an "
                "exchange and a symbol together"
            )
        notes.append(
            "fund/trust identity passages were found but incomplete, missing "
            + " and ".join(missing)
            + "; a human must decide what the filing actually establishes"
        )
    if fund.former_names:
        notes.append(
            "a 'formerly known as' passage is reported verbatim and has NOT been read "
            "as a rename, a validity window or any dated event; the dates inside it are "
            "dates the filing mentions, not dates this tool established"
        )

    if corporate_complete or fund.is_complete:
        status = ExtractionStatus.FOUND
    elif corporate_partial or fund.has_anything:
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.NOT_FOUND

    return EvidenceExtract(
        status=status,
        section_12b_heading=heading,
        section_12b_headers=headers,
        section_12b_rows=tuple(rows),
        symbol_statements=statements,
        fund_trust=fund,
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
