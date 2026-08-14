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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tradeit.edgar.controls import CONTROL_UNIVERSE, ControlSecurity
from tradeit.edgar.identity import MappingEvidence, MappingStatus, SecurityMapping
from tradeit.errors import ConfigError

__all__ = [
    "DEFAULT_EVIDENCE_PATH",
    "SCHEMA_VERSION",
    "ControlEvidence",
    "ControlEvidenceFile",
    "IssuerMapping",
    "ResolvedControl",
    "load_control_evidence",
    "resolve_controls",
]

SCHEMA_VERSION = 1

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
    verified_on: dt.date | None = None
    #: When this issuer held this ticker. Both ends may be unknown.
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    #: Which lifecycle this record is about, where it matters.
    scope_notes: str = ""
    #: Why it is not resolved. Required whenever the status is not resolved.
    unresolved_reason: str = ""

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
        }


@dataclass(frozen=True, slots=True)
class ControlEvidence:
    control_id: str
    expected_company: str
    mappings: tuple[IssuerMapping, ...]
    #: Required true when a control carries more than one issuer.
    identity_break: bool = False
    notes: str = ""


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
            "mappings": [m.summary() for m in self.mappings],
            "notes": self.notes,
            "verification_route": self.control.verification_route,
        }


# ---------------------------------------------------------------------------
# loading and validation
# ---------------------------------------------------------------------------


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

    return ControlEvidence(
        control_id=control_id,
        expected_company=str(raw.get("expected_company", "") or "").strip(),
        mappings=mappings,
        identity_break=identity_break,
        notes=str(raw.get("notes", "") or "").strip(),
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
            )
        )
    return out
