"""Curated, versioned identity evidence for the 30 control securities.

**Why a file rather than Python source.** Verifying a control is research work
that happens over weeks, one primary source at a time. Editing a module every
time a CIK is confirmed makes the provenance a diff rather than a record, and
makes the curated mappings invisible to anything but a code reader. The bulk SEC archive
stays outside Git because it is large and re-downloadable; **the curated mappings
and their citations are neither** -- they are the research output, and they
belong in version control next to the code that consumes them.

**The identity rules are not re-implemented here.** Every record is validated by
constructing a :class:`~tradeit.edgar.identity.SecurityMapping`, so the approved
invariants -- ``MANUAL_VERIFIED`` requires a citation, ``RESOLVED`` requires a
ticker -- are enforced by the same code that enforces them everywhere else. This
module adds only the rules a *file* needs: no duplicate controls, no unknown
control ids, no conflicting mappings, and no promotion on name resemblance.

**One control may have several issuers.** ``GM`` is the reason. General Motors
Corporation and General Motors Company are different registrants with different
CIKs that happened to share a ticker across a bankruptcy. A model with one CIK
per control cannot say that, and a model that *forces* it says something false.
So a control carries a **list** of issuer mappings, each with its own CIK,
ticker, validity window and evidence -- and a control with more than one must
declare ``identity_break: true``, which makes the merge impossible to perform by
accident.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradeit.edgar.controls import CONTROL_UNIVERSE, ControlSecurity
from tradeit.edgar.evidence import LifecycleScope
from tradeit.edgar.identity import MappingEvidence, MappingStatus, SecurityMapping
from tradeit.errors import ConfigError

__all__ = [
    "DEFAULT_EVIDENCE_PATH",
    "SCHEMA_VERSION",
    "Adjudication",
    "ControlEvidence",
    "ControlEvidenceFile",
    "FactDateSource",
    "IssuerMapping",
    "LifecycleFact",
    "ResolvedControl",
    "controls_awaiting_adjudication",
    "controls_awaiting_manual_verification",
    "load_control_evidence",
    "resolve_controls",
    "unresolved_controls",
]

#: Bumped to 2 when lifecycle_facts was added. A version-1 reader would have
#: silently dropped dated exchange-listing facts, and silently dropping a
#: lifecycle date is precisely the failure class this project keeps guarding.
#:
#: Bumped to 3 when ``adjudication`` was added, on the same reasoning one level
#: up. An adjudication changes what *complete* means for a control: it is how a
#: control's issuer question is closed without a mapping to show for it. A build
#: that does not understand the field would read such a file and report a
#: reassuring number computed from a rule it does not have. The loader refuses a
#: version mismatch outright, so the bump converts that quiet wrong answer into
#: a loud refusal -- which is the whole point of versioning this file.
SCHEMA_VERSION = 3

# Why the version-3 discussion below is kept.
#
# It records a schema change that was *declined*, and the reasoning outlived the
# version number it was written under. The nullable-date question is still open;
# the bump to 3 was made for an unrelated field and settles nothing about it.
#
# A nullable ``LifecycleFact.date`` was proposed during the IPET pilot, for one
# genuinely undated fact: the 10-K states the common stock continued to trade
# over the counter under the symbol IPETZ after the 2001-01-18 delisting, and
# gives no start or end date. A dependency audit found the change technically
# safe -- ``date`` has three consumers (the parse guard here, ``summary()``'s
# ``isoformat()``, and one diagnostic f-string), nothing sorts, groups or orders
# facts by date, and ``control_evidence`` is imported by ``cli_edgar`` alone, so
# a null could not reach any denominator count.
#
# It was still declined, for three reasons worth keeping:
#
# 1. **One instance is not a demonstration.** Two of the three originally
#    proposed undated facts turned out to be metadata and a legal qualification
#    rather than lifecycle events, and belonged in notes. The pressure for the
#    schema change was mostly a modelling error, not a gap.
# 2. **The damage would land on a consumer that does not exist yet.**
#    Point-in-time logic ("what was true as of D") naturally filters
#    ``fact.date <= D``, and a null silently drops out of every window -- no
#    error, no diagnostic. That is the silent-omission failure class this
#    project has repeatedly dug out of the parser.
# 3. **A nullable scalar is probably the wrong shape.** The IPETZ fact is an
#    interval with unknown bounds, not a point with no date: it is bounded below
#    by the delisting. ``earliest``/``latest``, or an explicit date certainty,
#    would carry more truth than a null -- and designing that on a single example
#    would be designing it blind.
#
# Revisit when two further controls produce a genuinely undated lifecycle fact,
# or when point-in-time research logic is specified, whichever comes first.
# Until then an undated fact is preserved in ``scope_notes`` with its citation,
# and the cost -- that it is readable but not queryable by scope -- is stated in
# the note itself rather than left for a reader to discover.

DEFAULT_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "research" / "control_identity_evidence.json"
)

#: A CIK is a positive integer of at most ten digits. Anything else is a typo or
#: a fabrication, and both should fail loudly.
_MAX_CIK = 9_999_999_999

#: Evidence that may never, on its own, carry a mapping to RESOLVED or above.
#: Mirrors :mod:`tradeit.edgar.identity`; duplicated as a file-level check so a
#: hand-written record cannot smuggle past it.
_INSUFFICIENT_ALONE = frozenset({MappingEvidence.NAME_MATCH, MappingEvidence.FULL_TEXT_SEARCH})

_CONTROL_IDS = {c.ticker for c in CONTROL_UNIVERSE}

#: The characters that follow a backslash in a JSON string escape. After
#: ``json.loads`` every one of these has already been consumed: ``\"`` on disk
#: *is* a plain quote in memory. So a backslash still sitting in front of one of
#: them at runtime means the text was escaped twice -- once by whoever wrote it,
#: once by the serializer -- and the reader is looking at a serialization
#: artifact rather than at what the cited filing says.
_JSON_ESCAPE_CHARS = '"\\/bfnrtu'

#: Only a backslash *immediately before* one of the above is rejected. A lone
#: backslash used deliberately is left alone, because the defect being caught is
#: specifically over-escaping, not the character itself.
_ESCAPE_ARTIFACT = re.compile(r"\\[" + re.escape(_JSON_ESCAPE_CHARS) + r"]")


class FactDateSource(StrEnum):
    """Where a lifecycle date came from, because the sources disagree.

    A Form 25-NSE carries an ``EFFECTIVENESS DATE`` in its SGML header *and* a
    removal date in its body text, and they are **not the same fact**. GM's
    common-stock Form 25-NSE has a header effectiveness of 2009-07-08 and a body
    stating removal "at the opening of business on July 20, 2009". Recording
    either without saying which it is would silently pick one.
    """

    BODY_TEXT = "body_text"
    HEADER_FIELD = "header_field"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class LifecycleFact:
    """One dated, cited fact about one lifecycle scope.

    Exists because ``valid_from``/``valid_to`` mean *generic ticker validity*,
    which is a weaker and broader claim than "the NYSE removed this common stock
    from listing on this date". A Form 25-NSE proves the second and says nothing
    about the first -- a delisted symbol can still appear in another venue, and
    the issuer can outlive its listing by years. Overloading ``valid_to`` with an
    exchange-listing boundary would assert more than the filing supports, so
    exchange-listing facts live here, scoped and cited.
    """

    scope: LifecycleScope
    fact: str
    date: dt.date
    date_source: FactDateSource
    citation: str
    note: str = ""

    def summary(self) -> dict[str, object]:
        return {
            "scope": str(self.scope),
            "fact": self.fact,
            "date": self.date.isoformat(),
            "date_source": str(self.date_source),
            "citation": self.citation,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class IssuerMapping:
    """One issuer's identity within a control, with its provenance."""

    #: Distinguishes issuers inside one control, e.g. ``old_gm`` / ``new_gm``.
    issuer_label: str
    cik: int | None
    ticker: str | None
    status: MappingStatus
    evidence: MappingEvidence | None = None
    #: Accession, URL, or another checkable pointer. Required for MANUAL_VERIFIED.
    citation: str = ""
    #: **The operator's local calendar date on which a human reviewed the cited
    #: evidence.** Supplied explicitly during authoring, and never derived from a
    #: clock -- not UTC, not system time, not commit time, not the filing's own
    #: date, not the acquisition run's date. The field records *when a person did
    #: something*, so the only authority on the day is that person's calendar; a
    #: machine's clock answers a different question. Deriving it also breaks the
    #: ordinary case where verification precedes recording by days.
    #:
    #: This was undocumented until three records authored either side of a UTC
    #: midnight disagreed -- one took the UTC date while local was still the
    #: previous day, one took the local date while UTC had advanced, one took UTC
    #: in the same circumstance the other took local. None was wrong under a rule,
    #: because there was no rule; they are left as they stand. This comment is the
    #: rule.
    #:
    #: If the review date is genuinely unknown, leave it null and let the status
    #: fall below MANUAL_VERIFIED rather than reading a clock to fill it.
    verified_on: dt.date | None = None
    #: When this issuer held this ticker, in the **generic** sense. Both ends may
    #: be unknown, and an exchange-listing boundary does not belong here -- see
    #: :class:`LifecycleFact`.
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    #: Which lifecycle this record is about, where it matters.
    scope_notes: str = ""
    #: Why it is not resolved. Required whenever the status is not resolved.
    unresolved_reason: str = ""
    #: Dated, cited, scope-tagged lifecycle facts. Never merged into validity.
    lifecycle_facts: tuple[LifecycleFact, ...] = ()

    def as_security_mapping(self) -> SecurityMapping:
        """Round-trip through the shared type so the approved rules apply."""
        return SecurityMapping(
            cik=self.cik,
            ticker=self.ticker,
            status=self.status,
            evidence=self.evidence,
            citation=self.citation,
            note=self.scope_notes or self.unresolved_reason,
        )

    @property
    def counts_in_numerator(self) -> bool:
        return self.status in {MappingStatus.RESOLVED, MappingStatus.MANUAL_VERIFIED}

    def summary(self) -> dict[str, object]:
        return {
            "issuer_label": self.issuer_label,
            "cik": self.cik,
            "ticker": self.ticker,
            "status": str(self.status),
            "evidence": str(self.evidence) if self.evidence else None,
            "citation": self.citation,
            "verified_on": self.verified_on.isoformat() if self.verified_on else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "scope_notes": self.scope_notes,
            "unresolved_reason": self.unresolved_reason,
            "lifecycle_facts": [f.summary() for f in self.lifecycle_facts],
        }


@dataclass(frozen=True, slots=True)
class Adjudication:
    """A cited finding that a control's issuer question has been settled.

    **Why this exists at all.** ``ControlSecurity.required_issuer_investigations``
    can say a control must settle whether a second issuer held its ticker. That
    obligation is normally discharged by recording the second mapping -- but the
    honest answer is sometimes "we looked and did not establish one", and there
    is no mapping to write for an issuer that was not found. Without this, the
    only way to finish such a control would be to find what was expected, which
    is how an expectation turns into a fabrication.

    **Why ``complete: true`` alone is refused.** "Investigated and no further
    issuer was established" is an affirmative research conclusion, not an
    absence of one. It has exactly the standing of a mapping or a
    :class:`LifecycleFact` and is held to the same bar: a non-empty finding
    saying what was searched and concluded, a citation making it checkable, and
    an explicitly supplied ``verified_on``. A bare boolean would be the easiest
    claim in this file to set carelessly, and the hardest to audit later.
    """

    complete: bool
    #: What was investigated and what was concluded. Required when complete.
    finding: str
    #: What makes the finding checkable -- the sources consulted. Required when
    #: complete, for the same reason a mapping needs one.
    citation: str
    #: Operator-local calendar date, explicitly supplied. Never clock-derived;
    #: see :attr:`IssuerMapping.verified_on`.
    verified_on: dt.date | None = None

    def summary(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "finding": self.finding,
            "citation": self.citation,
            "verified_on": self.verified_on.isoformat() if self.verified_on else None,
        }


@dataclass(frozen=True, slots=True)
class ControlEvidence:
    control_id: str
    expected_company: str
    mappings: tuple[IssuerMapping, ...]
    #: Required true when a control carries more than one issuer.
    identity_break: bool = False
    notes: str = ""
    #: Present only when the issuer obligation was closed without enough
    #: evidenced mappings to satisfy it. Absent is the normal case.
    adjudication: Adjudication | None = None


@dataclass(frozen=True, slots=True)
class ControlEvidenceFile:
    schema_version: int
    controls: dict[str, ControlEvidence] = field(default_factory=dict)
    source_path: Path | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedControl:
    """A control fixture joined to whatever evidence exists for it."""

    control: ControlSecurity
    mappings: tuple[IssuerMapping, ...]
    identity_break: bool = False
    notes: str = ""
    adjudication: Adjudication | None = None

    @property
    def status(self) -> MappingStatus:
        """The control's overall state: the weakest of its issuers.

        A control with two issuers is only as verified as its least verified
        one, because a half-mapped identity break is exactly the case that
        produces a spliced series.
        """
        if not self.mappings:
            return MappingStatus.UNRESOLVED
        order = [
            MappingStatus.UNRESOLVED,
            MappingStatus.AMBIGUOUS,
            MappingStatus.RESOLVED,
            MappingStatus.MANUAL_VERIFIED,
        ]
        return min(self.mappings, key=lambda m: order.index(m.status)).status

    @property
    def counts_in_numerator(self) -> bool:
        """Whether every issuer this control names has acceptable evidence.

        The same predicate :class:`IssuerMapping` uses, applied across the whole
        control rather than restated for it. ``all`` is the rule :attr:`status`
        expresses by taking the weakest issuer: a half-mapped identity break is
        not a verified control, because the half without evidence is exactly the
        segment that would splice a series.
        """
        return bool(self.mappings) and all(m.counts_in_numerator for m in self.mappings)

    @property
    def is_manually_verified(self) -> bool:
        """Whether every issuer was checked by a human against primary evidence.

        Strictly stronger than :attr:`counts_in_numerator`, and deliberately so.
        ``RESOLVED`` means one defensible mapping from a dated primary source --
        enough to count a control as identified, and enough for the survivorship
        numerator. ``MANUAL_VERIFIED`` additionally means a person read a filing
        and recorded a citation to it. Milestone 0b asks for the second, so a
        ``RESOLVED`` control clears the identity question and still leaves the
        milestone's work to do.
        """
        return self.status is MappingStatus.MANUAL_VERIFIED

    @property
    def issuer_obligation_discharged(self) -> bool:
        """Whether this control's issuer question has been settled either way.

        **Path A** -- enough independently evidenced mappings exist to satisfy
        the control's design. Recording the second issuer *is* the adjudication;
        no separate object is needed.

        **Path B** -- a cited adjudication closes the remaining question without
        inventing a mapping to represent an issuer that was not found.

        Ordinary controls require one investigation, so a single recorded
        mapping discharges them and nothing about them changes.
        """
        if len(self.mappings) >= self.control.required_issuer_investigations:
            return True
        return self.adjudication is not None and self.adjudication.complete

    @property
    def is_fully_adjudicated(self) -> bool:
        """Milestone 0b's gate: the control is finished, not merely well-cited.

        Two independent conditions, and both must hold:

        * **quality** -- every *recorded* mapping is ``MANUAL_VERIFIED``
          (:attr:`is_manually_verified`, unchanged);
        * **completeness** -- the issuer obligation is discharged
          (:attr:`issuer_obligation_discharged`).

        They are genuinely independent, and ``GM`` shows why keeping them apart
        matters: it has both its issuers recorded, so its obligation is
        discharged, yet its weakest mapping is only ``RESOLVED`` -- so it fails
        on quality. ``BBBY`` is the mirror image: one impeccably cited mapping,
        obligation still open. Collapsing the two would let either kind of gap
        hide behind the other.

        **Why this is not folded into** :attr:`is_manually_verified`: that
        property means "every recorded mapping was read by a person", and it is
        the honest answer to a different question. Overloading it would make one
        number mean both "the evidence is good" and "the work is done", which is
        the confusion the two-measurement design already exists to prevent.
        """
        return self.is_manually_verified and self.issuer_obligation_discharged

    @property
    def unresolved_reason(self) -> str:
        reasons = [m.unresolved_reason for m in self.mappings if m.unresolved_reason]
        return "; ".join(reasons)

    def summary(self) -> dict[str, object]:
        return {
            "control_id": self.control.ticker,
            "control_class": self.control.control_class,
            "expected_company": self.control.name,
            "status": str(self.status),
            "identity_break": self.identity_break,
            "required_issuer_investigations": self.control.required_issuer_investigations,
            "issuer_obligation_discharged": self.issuer_obligation_discharged,
            "fully_adjudicated": self.is_fully_adjudicated,
            "adjudication": self.adjudication.summary() if self.adjudication else None,
            "mappings": [m.summary() for m in self.mappings],
            "notes": self.notes,
            "verification_route": self.control.verification_route,
        }


# ---------------------------------------------------------------------------
# loading and validation
# ---------------------------------------------------------------------------


def _reject_escape_artifacts(value: Any, where: str) -> None:
    """Reject text that reached memory still carrying its own JSON escapes.

    **The defect this catches, exactly.** A citation quotes a filing, and filings
    quote things: ``the Over-the-Counter ("OTC") market``. Written into a Python
    builder the inner quotes need escaping *for Python*, and it is easy to escape
    them once more "for the JSON" -- at which point ``json.dump`` escapes the
    backslash too, and the file on disk holds ``\\\\"OTC\\\\"``. That is valid JSON
    and it round-trips perfectly, so nothing downstream complains; it simply means
    something different from what the filing says. The operator reading
    ``controls --diagnose`` sees ``(\\"OTC\\")`` in a passage presented as a verbatim
    quotation of a primary source, which is the one thing a citation may not be.

    **Why it is checked here and not at the field level.** The rule is about how
    text was *serialized*, so it applies to every string the file carries and to
    every field added later, not to the prose fields someone remembers to list.
    One walk over the parsed payload cannot be forgotten by a future field.

    **Why the rule is narrow.** It fires only on a backslash standing immediately
    before a JSON escape character -- the signature of double-escaping. Real
    newlines, real tabs, quotation marks, apostrophes, accession numbers, ``®``
    and em dashes are all ordinary text and are untouched; the shipped corpus
    contains 246 quotation marks and 238 newlines and none of them is affected. A
    backslash used deliberately in front of anything else is also allowed: the
    defect is the double escape, not the character.
    """
    if isinstance(value, str):
        found = _ESCAPE_ARTIFACT.search(value)
        if found is not None:
            start = max(0, found.start() - 40)
            raise ConfigError(
                f"{where}: text contains the literal escape sequence {found.group(0)!r}, "
                f"which is a serialization artifact rather than the source's own wording "
                f"-- near ...{value[start : found.end() + 40]}... "
                f"JSON escaping is undone by the parser, so a backslash surviving in front "
                f"of {found.group(0)[1]!r} means the text was escaped twice. Write the "
                f"character the cited document actually uses."
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_escape_artifacts(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_escape_artifacts(item, f"{where}[{index}]")


def _as_date(value: Any, field_name: str, where: str) -> dt.date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {field_name} must be an ISO date string, got {value!r}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{where}: {field_name} is not an ISO date: {value!r}") from exc


def _as_enum(value: Any, enum: Any, field_name: str, where: str) -> Any:
    if value is None or value == "":
        return None
    try:
        return enum(value)
    except ValueError as exc:
        options = sorted(str(member) for member in enum)
        raise ConfigError(
            f"{where}: unknown {field_name} {value!r}; expected one of {options}"
        ) from exc


def _parse_cik(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: cik must be an integer or null, got {value!r}")
    if value <= 0 or value > _MAX_CIK:
        raise ConfigError(f"{where}: cik {value} is out of range (1..{_MAX_CIK})")
    return value


def _parse_lifecycle_fact(raw: Any, where: str) -> LifecycleFact:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each lifecycle fact must be an object")

    scope = _as_enum(raw.get("scope"), LifecycleScope, "scope", where)
    if scope is None:
        raise ConfigError(f"{where}: scope is required; a dated fact about nothing is not a fact")
    date = _as_date(raw.get("date"), "date", where)
    if date is None:
        raise ConfigError(f"{where}: date is required")
    source = _as_enum(raw.get("date_source"), FactDateSource, "date_source", where)
    if source is None:
        raise ConfigError(
            f"{where}: date_source is required. A Form 25-NSE header effectiveness date "
            "and a body-stated removal date are different facts, and a record that does "
            "not say which it holds has silently picked one"
        )
    fact = str(raw.get("fact", "") or "").strip()
    if not fact:
        raise ConfigError(f"{where}: fact text is required")
    citation = str(raw.get("citation", "") or "").strip()
    if not citation:
        raise ConfigError(f"{where}: citation is required; an uncited lifecycle date is a claim")

    return LifecycleFact(
        scope=scope,
        fact=fact,
        date=date,
        date_source=source,
        citation=citation,
        note=str(raw.get("note", "") or "").strip(),
    )


def _parse_adjudication(raw: Any, where: str) -> Adjudication:
    """Parse a closing finding, and refuse one that asserts without support."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: adjudication must be an object")

    complete = bool(raw.get("complete", False))
    finding = str(raw.get("finding", "") or "").strip()
    citation = str(raw.get("citation", "") or "").strip()
    verified_on = _as_date(raw.get("verified_on"), "verified_on", where)

    if complete:
        if not finding:
            raise ConfigError(
                f"{where}: a complete adjudication requires a finding. "
                "'Investigated and no further issuer was established' is a research "
                "conclusion, not the absence of one, and an unstated conclusion is a claim"
            )
        if not citation:
            raise ConfigError(
                f"{where}: a complete adjudication requires a citation. "
                "A finding that closes a control's issuer question must be checkable "
                "against the sources it rests on, exactly as a mapping must be"
            )
        if verified_on is None:
            raise ConfigError(
                f"{where}: a complete adjudication requires verified_on -- the "
                "operator-local date on which the research was reviewed"
            )

    return Adjudication(
        complete=complete, finding=finding, citation=citation, verified_on=verified_on
    )


def _parse_mapping(raw: Any, where: str) -> IssuerMapping:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each mapping must be an object")

    label = str(raw.get("issuer_label", "")).strip()
    if not label:
        raise ConfigError(f"{where}: issuer_label is required")

    status = _as_enum(raw.get("status"), MappingStatus, "status", where)
    if status is None:
        raise ConfigError(f"{where}: status is required")
    evidence = _as_enum(raw.get("evidence"), MappingEvidence, "evidence", where)

    ticker_raw = raw.get("ticker")
    ticker = str(ticker_raw).strip().upper() if ticker_raw else None
    citation = str(raw.get("citation", "") or "").strip()
    unresolved_reason = str(raw.get("unresolved_reason", "") or "").strip()

    raw_facts = raw.get("lifecycle_facts", [])
    if not isinstance(raw_facts, list):
        raise ConfigError(f"{where}: lifecycle_facts must be a list")
    facts = tuple(
        _parse_lifecycle_fact(item, f"{where}.lifecycle_facts[{i}]")
        for i, item in enumerate(raw_facts)
    )

    mapping = IssuerMapping(
        issuer_label=label,
        cik=_parse_cik(raw.get("cik"), where),
        ticker=ticker,
        status=status,
        evidence=evidence,
        citation=citation,
        verified_on=_as_date(raw.get("verified_on"), "verified_on", where),
        valid_from=_as_date(raw.get("valid_from"), "valid_from", where),
        valid_to=_as_date(raw.get("valid_to"), "valid_to", where),
        scope_notes=str(raw.get("scope_notes", "") or "").strip(),
        unresolved_reason=unresolved_reason,
        lifecycle_facts=facts,
    )

    # The approved identity rules, enforced by the shared type rather than
    # restated: MANUAL_VERIFIED needs a citation, RESOLVED needs a ticker.
    try:
        mapping.as_security_mapping()
    except ConfigError as exc:
        raise ConfigError(f"{where}: {exc}") from exc

    if mapping.counts_in_numerator:
        if mapping.cik is None:
            raise ConfigError(f"{where}: status {status} requires a cik")
        if evidence is None:
            raise ConfigError(f"{where}: status {status} requires an evidence type")
        if evidence in _INSUFFICIENT_ALONE:
            raise ConfigError(
                f"{where}: evidence {evidence} cannot establish a mapping on its own; "
                f"a company name resembling the control's is not evidence"
            )
        if mapping.verified_on is None:
            raise ConfigError(f"{where}: status {status} requires verified_on")
    elif not unresolved_reason:
        raise ConfigError(f"{where}: status {status} requires an unresolved_reason")

    if mapping.valid_from and mapping.valid_to and mapping.valid_to < mapping.valid_from:
        raise ConfigError(f"{where}: valid_to precedes valid_from")

    return mapping


def _parse_control(raw: Any, where: str) -> ControlEvidence:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each control must be an object")
    control_id = str(raw.get("control_id", "")).strip().upper()
    if not control_id:
        raise ConfigError(f"{where}: control_id is required")
    if control_id not in _CONTROL_IDS:
        raise ConfigError(
            f"{where}: unknown control_id {control_id!r}; it is not in the 30-control universe"
        )

    raw_mappings = raw.get("mappings", [])
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ConfigError(f"{where}: at least one mapping is required")

    mappings = tuple(
        _parse_mapping(item, f"{where}.mappings[{i}]") for i, item in enumerate(raw_mappings)
    )

    labels = [m.issuer_label for m in mappings]
    if len(set(labels)) != len(labels):
        raise ConfigError(f"{where}: duplicate issuer_label among mappings: {sorted(labels)}")

    ciks = [m.cik for m in mappings if m.cik is not None]
    if len(set(ciks)) != len(ciks):
        raise ConfigError(f"{where}: the same cik appears on two issuers: {sorted(ciks)}")

    identity_break = bool(raw.get("identity_break", False))
    if len(mappings) > 1 and not identity_break:
        raise ConfigError(
            f"{where}: {len(mappings)} issuers but identity_break is false. "
            "Several issuers sharing a ticker is an identity break and must say so; "
            "silently merging them is the failure the control exists to detect"
        )
    if len(mappings) == 1 and identity_break:
        raise ConfigError(f"{where}: identity_break is true but only one issuer is recorded")

    raw_adjudication = raw.get("adjudication")
    adjudication = (
        _parse_adjudication(raw_adjudication, f"{where}.adjudication")
        if raw_adjudication is not None
        else None
    )

    return ControlEvidence(
        control_id=control_id,
        expected_company=str(raw.get("expected_company", "") or "").strip(),
        mappings=mappings,
        identity_break=identity_break,
        notes=str(raw.get("notes", "") or "").strip(),
        adjudication=adjudication,
    )


def load_control_evidence(path: Path | str | None = None) -> ControlEvidenceFile:
    """Read and validate the curated evidence file.

    Raises :class:`~tradeit.errors.ConfigError` on any schema violation. There is
    no lenient mode: a malformed provenance record is worse than none, because it
    looks like provenance.
    """
    resolved = Path(path) if path is not None else DEFAULT_EVIDENCE_PATH
    if not resolved.exists():
        return ControlEvidenceFile(schema_version=SCHEMA_VERSION, source_path=resolved)

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{resolved}: not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{resolved}: top level must be an object")

    # Before any field is interpreted: the text must say what its source says.
    _reject_escape_artifacts(payload, resolved.name)

    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"{resolved}: schema_version {version!r}; this build understands {SCHEMA_VERSION}"
        )

    raw_controls = payload.get("controls", [])
    if not isinstance(raw_controls, list):
        raise ConfigError(f"{resolved}: 'controls' must be a list")

    controls: dict[str, ControlEvidence] = {}
    for index, raw in enumerate(raw_controls):
        control = _parse_control(raw, f"{resolved.name}.controls[{index}]")
        if control.control_id in controls:
            raise ConfigError(f"{resolved}: duplicate control_id {control.control_id!r}")
        controls[control.control_id] = control

    return ControlEvidenceFile(
        schema_version=version,
        controls=controls,
        source_path=resolved,
        notes=str(payload.get("notes", "") or ""),
    )


def resolve_controls(evidence: ControlEvidenceFile | None = None) -> list[ResolvedControl]:
    """Join the 30-control fixture to the evidence file.

    A control with no record in the file is ``UNRESOLVED`` with the reason
    stated -- absence of evidence is recorded as absence, never as a pass.
    """
    evidence = evidence if evidence is not None else load_control_evidence()
    out: list[ResolvedControl] = []
    for control in CONTROL_UNIVERSE:
        record = evidence.controls.get(control.ticker)
        if record is None:
            out.append(
                ResolvedControl(
                    control=control,
                    mappings=(
                        IssuerMapping(
                            issuer_label="primary",
                            cik=None,
                            ticker=None,
                            status=MappingStatus.UNRESOLVED,
                            unresolved_reason="no evidence record in the evidence file",
                        ),
                    ),
                )
            )
            continue
        out.append(
            ResolvedControl(
                control=control,
                mappings=record.mappings,
                identity_break=record.identity_break,
                notes=record.notes,
                adjudication=record.adjudication,
            )
        )
    return out


# Two questions, two functions, one resolution.
#
# "Do we know which registrant this control is?" and "has a person confirmed it
# against a filing?" are different questions with different answers, and a single
# count cannot report both. Answering them with one number is how a milestone
# that asks for MANUAL_VERIFIED gets reported complete on RESOLVED evidence.
#
# Both read the same :func:`resolve_controls` join, so there is one parse of the
# evidence file and one definition of each status; only the predicate differs.


def unresolved_controls(
    evidence: ControlEvidenceFile | None = None,
) -> tuple[ResolvedControl, ...]:
    """Controls whose identity is still not established. **Measurement A.**

    Outstanding while the effective status is ``UNRESOLVED`` or ``AMBIGUOUS``.
    Both ``RESOLVED`` and ``MANUAL_VERIFIED`` clear it: each is a defensible
    mapping from a dated primary source, and this is the same predicate the
    survivorship numerator uses, so a control that counts there counts here.

    What does **not** clear it is the mere existence of a record. An
    ``UNRESOLVED`` mapping must state why it is unresolved, and saying so
    honestly leaves the control counted.

    **This is not the Milestone 0b gate** -- see
    :func:`controls_awaiting_manual_verification`.
    """
    return tuple(c for c in resolve_controls(evidence) if not c.counts_in_numerator)


def controls_awaiting_manual_verification(
    evidence: ControlEvidenceFile | None = None,
) -> tuple[ResolvedControl, ...]:
    """Milestone 0b's remaining work. **Measurement B, and the completion gate.**

    The milestone is *30 controls to* ``MANUAL_VERIFIED``, so only
    ``MANUAL_VERIFIED`` clears it and the gate closes at 30/30. A ``RESOLVED``
    control -- ``AAPL`` from the SEC ticker file, ``GM`` whose weakest issuer is
    ``RESOLVED`` -- is a known identity and is still counted here, because the
    milestone asks for a human who read a filing and cited it, and no ticker
    reference file supplies that.

    **This gate is strictly harder than** :func:`unresolved_controls`, and its
    count is therefore never lower. Reporting the easier number against the
    milestone's wording is the confusion this pair exists to prevent.

    **Neither gate replaced the one that could not move.** ``controls.unverified()``
    read ``ControlSecurity.mapping``, a placeholder pinned at ``UNRESOLVED`` for
    all thirty so that a remembered CIK can never be written into source. It
    never opened the evidence file, so it reported thirty outstanding whatever
    had been verified -- and would have reported thirty on the day the last
    control was confirmed.
    """
    return tuple(c for c in resolve_controls(evidence) if not c.is_manually_verified)


def controls_awaiting_adjudication(
    evidence: ControlEvidenceFile | None = None,
) -> tuple[ResolvedControl, ...]:
    """Milestone 0b's remaining work. **Measurement C, and the completion gate.**

    The milestone's own wording is *confirm the 30 control securities*, which is
    a statement about **controls**, not about mappings. A control is confirmed
    when every recorded mapping was read by a person **and** the control's
    issuer question has been settled -- see :attr:`ResolvedControl.is_fully_adjudicated`.

    **This gate is strictly harder than** :func:`controls_awaiting_manual_verification`,
    and its count is therefore never lower. That pair replaced an earlier
    single number for exactly this reason, one level down: a milestone asking
    for ``MANUAL_VERIFIED`` was being reported complete on ``RESOLVED``
    evidence. The same shape recurred a level up. A control whose whole purpose
    is proving two issuers held one ticker could reach ``MANUAL_VERIFIED`` on a
    single mapping, and the gate could have closed at 30/30 with the corpus's
    most important control half-investigated -- not because anything
    malfunctioned, but because nothing had ever told the machine how many
    issuers the control was supposed to settle.
    """
    return tuple(c for c in resolve_controls(evidence) if not c.is_fully_adjudicated)
