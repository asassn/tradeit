"""Populate the identity backbone from curated control evidence. Nothing else.

The importer cannot create identity by design, so until this runs the
``research-01`` tables are empty and **every import resolves to
``UNRESOLVED_NO_ALIAS``** -- including the EODHD sample files. This is the
bridge, and it reads exactly one source.

**Only ``control_identity_evidence.json``.** No identifier is invented, none is
inferred from a ticker file, and nothing is derived from a company name. A
control whose evidence does not establish something is seeded **as
not-established and reported**, never skipped quietly and never filled with a
plausible value -- an identity backbone that guessed once is worth less than an
empty one, because nothing downstream can tell which rows were guessed.

**One issuer per mapping, not per control.** A control with an identity break
carries two mappings and becomes **two issuers and two securities**. Collapsing
them would destroy the distinction the control was selected to expose, and would
make the ``GM`` splice test pass on a corpus that had already lost it -- the
third opportunity this repository has had to let a test go vacuous.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.edgar.control_evidence import (
    ControlEvidence,
    ControlEvidenceFile,
    IdentifierRole,
    IssuerMapping,
    load_control_evidence,
    primary_issuer_key,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    IssuerRelatedIdentity,
    Security,
    SymbolAlias,
)

__all__ = ["NotEstablished", "SeedReport", "seed_identity"]


@dataclass(frozen=True, slots=True)
class NotEstablished:
    """Something the evidence does not support, recorded rather than filled in."""

    control_id: str
    issuer_label: str
    what: str
    detail: str = ""


@dataclass(slots=True)
class SeedReport:
    issuers: int = 0
    securities: int = 0
    identifiers: int = 0
    related_identities: int = 0
    aliases: int = 0
    reused: int = 0
    not_established: list[NotEstablished] = field(default_factory=list)

    def gaps_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for gap in self.not_established:
            out[gap.what] = out.get(gap.what, 0) + 1
        return out

    def summary(self) -> dict[str, object]:
        return {
            "issuers": self.issuers,
            "securities": self.securities,
            "identifiers": self.identifiers,
            "related_identities": self.related_identities,
            "aliases": self.aliases,
            "reused_existing_issuers": self.reused,
            "not_established": len(self.not_established),
            "not_established_by_kind": self.gaps_by_kind(),
        }


def _normalize(value: str) -> str:
    """The comparison form, matching ``IdentifierRef.key``.

    All issuer namespaces are numeric registries, so ``0001132979`` and
    ``1132979`` are one identifier written two ways. Storage keeps the verbatim
    string; only this form is compared.
    """
    return str(int(value))


def _existing_issuer(session: Session, namespace: str, normalized: str) -> int | None:
    return session.scalars(
        select(IssuerIdentifier.issuer_id).where(
            IssuerIdentifier.namespace == namespace,
            IssuerIdentifier.value_normalized == normalized,
        )
    ).first()


def _seed_mapping(
    session: Session, control: ControlEvidence, mapping: IssuerMapping, report: SeedReport
) -> None:
    def gap(what: str, detail: str = "") -> None:
        report.not_established.append(
            NotEstablished(control.control_id, mapping.issuer_label, what, detail)
        )

    key = primary_issuer_key(mapping)
    if key is None:
        # An UNRESOLVED control legitimately has no discriminating key. It gets
        # no issuer row, because a row with no key cannot be identified later
        # and would be indistinguishable from one whose key we simply lost.
        gap("issuer_key", mapping.unresolved_reason or "no primary identifier in evidence")
        return

    namespace, normalized = key
    existing = _existing_issuer(session, namespace, normalized)
    if existing is not None:
        report.reused += 1
        return

    issuer = Issuer(
        display_name=control.expected_company or mapping.issuer_label,
        note=f"{control.control_id}/{mapping.issuer_label}",
        source="control_identity_evidence",
    )
    session.add(issuer)
    session.flush()
    report.issuers += 1

    # Identifiers: the explicit ones when present, otherwise the legacy `cik`
    # promoted to an explicit SEC_CIK primary. Never both for the same value --
    # primary_issuer_key already collapses them into one key, and writing two
    # rows would trip the global uniqueness constraint on the same number.
    written: set[tuple[str, str]] = set()
    for identifier in mapping.identifiers:
        pair = (str(identifier.ref.namespace), _normalize(identifier.ref.value))
        if pair in written:
            continue
        session.add(
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace=pair[0],
                value=identifier.ref.value,
                value_normalized=pair[1],
                role=str(identifier.role),
                citation=identifier.ref.citation,
                source="control_identity_evidence",
            )
        )
        written.add(pair)
        report.identifiers += 1

    if (namespace, normalized) not in written:
        if not mapping.citation:
            gap("identifier_citation", "primary key carries no citation")
        session.add(
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace=namespace,
                value=str(mapping.cik) if mapping.cik is not None else normalized,
                value_normalized=normalized,
                role=str(IdentifierRole.PRIMARY),
                citation=mapping.citation or "",
                source="control_identity_evidence",
            )
        )
        report.identifiers += 1

    for related in mapping.related_identities:
        session.add(
            IssuerRelatedIdentity(
                issuer_id=issuer.issuer_id,
                namespace=str(related.ref.namespace),
                value=related.ref.value,
                value_normalized=_normalize(related.ref.value),
                relation=str(related.relation),
                citation=related.ref.citation,
                note=related.finding,
                source="control_identity_evidence",
            )
        )
        report.related_identities += 1

    security = Security(
        issuer_id=issuer.issuer_id,
        security_type="common_stock",
        class_label=mapping.scope_notes or None,
        currency="USD",
        note=f"{control.control_id}/{mapping.issuer_label}",
        source="control_identity_evidence",
    )
    session.add(security)
    session.flush()
    report.securities += 1

    # The ticker interval is a SEPARATE evidence question and the corpus mostly
    # does not answer it. Without valid_from there is no interval, and inventing
    # one would manufacture the very attribution the importer refuses to guess.
    if mapping.ticker is None:
        gap("ticker")
        return
    if mapping.valid_from is None:
        gap("ticker_interval", f"ticker {mapping.ticker} has no established valid_from")
        return
    if mapping.verified_on is None:
        gap("alias_knowledge_time", "no verified_on to serve as knowledge_time")
        return

    session.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value=mapping.ticker,
            valid_from=mapping.valid_from,
            valid_to=mapping.valid_to,
            knowledge_time=_as_instant(mapping.verified_on),
            knowledge_source="control_identity_evidence",
            citation=mapping.citation or None,
            source="control_identity_evidence",
        )
    )
    report.aliases += 1


def _as_instant(day: dt.date) -> dt.datetime:
    """A verified-on date read as an instant. Midnight UTC, never a guess at a time."""
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)


def seed_identity(session: Session, evidence: ControlEvidenceFile | None = None) -> SeedReport:
    """Seed issuers, identifiers, securities and any establishable aliases.

    Idempotent: an issuer whose primary key already exists is reused rather than
    duplicated, so re-running changes nothing. That is the same anti-splice rule
    the schema enforces -- one registry value names one issuer -- applied before
    the constraint has to.
    """
    evidence = evidence or load_control_evidence()
    report = SeedReport()
    for control in evidence.controls.values():
        for mapping in control.mappings:
            _seed_mapping(session, control, mapping, report)
    session.flush()
    return report
