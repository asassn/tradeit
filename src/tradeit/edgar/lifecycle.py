"""Turning filings into lifecycle conclusions -- and refusing to over-conclude.

The rule this module enforces, and the reason it exists as its own layer:

> **Filing cessation is a candidate signal, never a confirmed security death.**

A registrant whose 10-Ks stop may have been acquired, gone private, been
liquidated, become a non-reporting subsidiary, or fallen delinquent and resumed
two years later. Only the first three are exits, they have different dates, and
the absence of filings distinguishes none of them. So a cessation-only case
resolves to :data:`~tradeit.edgar.evidence.EvidenceType.POSSIBLE_EXIT_FILING_CESSATION`,
**carries no lifecycle date at all**, and stays that way until corroborating
evidence says what happened.

:func:`assert_cessation_undated` exists so that rule is checkable rather than
merely intended.

The second discipline is scope. A Form 15 ends a *reporting* obligation; a Form
25 ends an *exchange listing*; neither on its own says the *security class* was
extinguished or that the *issuer* ceased to exist.
:data:`~tradeit.edgar.evidence.EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED` is
therefore never read off a single form — it is derived, only from corroborating
evidence across scopes, and it is marked ``FORM_INFERRED`` so it can never be
mistaken for something a filing said outright.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from tradeit.edgar.evidence import (
    EvidenceStrength,
    EvidenceType,
    FormRole,
    LifecycleEvidence,
    LifecycleScope,
)
from tradeit.errors import DataError

__all__ = [
    "CESSATION_QUIET_QUARTERS",
    "ExitResolution",
    "IssuerTimeline",
    "assert_cessation_undated",
    "build_timelines",
    "resolve_exit",
]

#: How long a registrant must be silent before cessation is even a candidate.
#: Eight quarters is deliberately conservative: delinquency of a year is common
#: and routinely reverses, and a resumption retroactively cancels the candidate.
CESSATION_QUIET_QUARTERS = 8

_CONFIRMING = frozenset(
    {
        EvidenceType.CONFIRMED_EXCHANGE_DELISTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceType.CONFIRMED_ACQUISITION,
        EvidenceType.CONFIRMED_BANKRUPTCY,
    }
)


@dataclass(frozen=True, slots=True)
class ExitResolution:
    """What we can defend saying about one registrant's exit.

    ``evidence_date`` is the date of the filing the conclusion rests on, and is
    ``None`` for cessation-only and unresolved cases -- there is no filing to
    point at. ``effective_date`` is the date the event actually took effect and
    is ``None`` unless a parsed document stated it. **Counting by year uses
    ``evidence_date`` and the reports say so.**
    """

    cik: int
    company_name: str
    evidence_type: EvidenceType
    strength: EvidenceStrength
    scopes: frozenset[LifecycleScope]
    evidence_date: dt.date | None
    effective_date: dt.date | None
    supporting: tuple[LifecycleEvidence, ...]
    #: For cessation candidates: the last periodic report seen. Context, not a date of death.
    last_periodic: dt.date | None = None
    note: str = ""

    @property
    def is_confirmed(self) -> bool:
        return self.evidence_type in _CONFIRMING or (
            self.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
        )

    def summary(self) -> dict[str, object]:
        return {
            "cik": self.cik,
            "evidence_type": str(self.evidence_type),
            "strength": str(self.strength),
            "scopes": sorted(str(s) for s in self.scopes),
            "evidence_date": self.evidence_date.isoformat() if self.evidence_date else None,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "last_periodic": self.last_periodic.isoformat() if self.last_periodic else None,
            "accessions": [e.accession for e in self.supporting],
            "note": self.note,
        }


@dataclass(slots=True)
class IssuerTimeline:
    """Every lifecycle-relevant filing for one CIK, in order."""

    cik: int
    company_name: str
    evidence: list[LifecycleEvidence] = field(default_factory=list)

    @property
    def births(self) -> list[LifecycleEvidence]:
        return [e for e in self.evidence if e.role is FormRole.BIRTH]

    @property
    def periodic_dates(self) -> list[dt.date]:
        return sorted(e.evidence_date for e in self.evidence if e.role is FormRole.PERIODIC)

    @property
    def first_seen(self) -> dt.date:
        return min(e.evidence_date for e in self.evidence)

    @property
    def last_seen(self) -> dt.date:
        return max(e.evidence_date for e in self.evidence)

    @property
    def listing_start(self) -> dt.date | None:
        """First 12(b) registration, if any. The exchange-listing birth."""
        listings = [e for e in self.births if e.scope is LifecycleScope.EXCHANGE_LISTING]
        return min((e.evidence_date for e in listings), default=None)


def build_timelines(evidence: Iterable[LifecycleEvidence]) -> dict[int, IssuerTimeline]:
    """Group evidence by CIK. The company name kept is the most recent one seen."""
    grouped: dict[int, list[LifecycleEvidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.cik].append(item)
    timelines: dict[int, IssuerTimeline] = {}
    for cik, items in grouped.items():
        items.sort(key=lambda e: (e.evidence_date, e.accession))
        timelines[cik] = IssuerTimeline(
            cik=cik, company_name=items[-1].company_name, evidence=items
        )
    return timelines


def _quarters_between(earlier: dt.date, later: dt.date) -> float:
    return (later - earlier).days / 91.3125


def resolve_exit(
    timeline: IssuerTimeline,
    *,
    as_of: dt.date,
    quiet_quarters: int = CESSATION_QUIET_QUARTERS,
) -> ExitResolution:
    """Decide what, if anything, this registrant's filings prove about an exit.

    Order matters. Direct evidence wins; corroborated direct evidence across two
    scopes upgrades to ``CONFIRMED_SECURITY_EXTINGUISHED``; cessation is only
    considered when nothing direct exists, and produces a dated *context* rather
    than a dated conclusion.
    """
    confirming = [
        e
        for e in timeline.evidence
        if e.evidence_type is not None and e.evidence_type in _CONFIRMING
    ]
    periodic = timeline.periodic_dates

    if confirming:
        confirming.sort(key=lambda e: e.evidence_date)
        primary = confirming[0]
        scopes = frozenset(e.scope for e in confirming if e.scope is not None)
        distinct_types = {e.evidence_type for e in confirming}
        # Extinguishment is a derived claim: it needs the listing to have ended
        # AND the registration to have been terminated. One alone does not do it,
        # because a delisted security can keep trading and a deregistered issuer
        # can keep existing.
        if {
            EvidenceType.CONFIRMED_EXCHANGE_DELISTING,
            EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        } <= distinct_types:
            return ExitResolution(
                cik=timeline.cik,
                company_name=timeline.company_name,
                evidence_type=EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED,
                strength=EvidenceStrength.FORM_INFERRED,
                scopes=scopes,
                evidence_date=confirming[-1].evidence_date,
                effective_date=None,
                supporting=tuple(confirming),
                last_periodic=max(periodic) if periodic else None,
                note=(
                    "derived from delisting AND registration termination; "
                    "no single filing asserts extinguishment"
                ),
            )
        assert primary.evidence_type is not None
        return ExitResolution(
            cik=timeline.cik,
            company_name=timeline.company_name,
            evidence_type=primary.evidence_type,
            strength=EvidenceStrength.FORM_DIRECT,
            scopes=scopes,
            evidence_date=primary.evidence_date,
            effective_date=primary.effective_date,
            supporting=tuple(confirming),
            last_periodic=max(periodic) if periodic else None,
            note=primary.note,
        )

    if periodic:
        last = max(periodic)
        if _quarters_between(last, as_of) >= quiet_quarters:
            return ExitResolution(
                cik=timeline.cik,
                company_name=timeline.company_name,
                evidence_type=EvidenceType.POSSIBLE_EXIT_FILING_CESSATION,
                strength=EvidenceStrength.CESSATION_ONLY,
                scopes=frozenset({LifecycleScope.SEC_REPORTING}),
                # No date. The registrant stopped reporting; nothing says the
                # security died, so nothing here may be used as a death date.
                evidence_date=None,
                effective_date=None,
                supporting=(),
                last_periodic=last,
                note=(
                    f"no periodic report for >= {quiet_quarters} quarters after "
                    f"{last.isoformat()}; candidate only -- corroboration required"
                ),
            )

    return ExitResolution(
        cik=timeline.cik,
        company_name=timeline.company_name,
        evidence_type=EvidenceType.UNRESOLVED_EXIT,
        strength=EvidenceStrength.NONE,
        scopes=frozenset(),
        evidence_date=None,
        effective_date=None,
        supporting=(),
        last_periodic=max(periodic) if periodic else None,
        note="still filing, or no evidence either way",
    )


def assert_cessation_undated(resolutions: Sequence[ExitResolution]) -> None:
    """Guard: a cessation-only case must never carry a lifecycle date.

    Raises :class:`~tradeit.errors.DataError` naming every offender. Called by
    the denominator before it publishes anything, so the rule cannot be
    weakened by a later edit without a test failing.
    """
    offenders = [
        r.cik
        for r in resolutions
        if r.evidence_type is EvidenceType.POSSIBLE_EXIT_FILING_CESSATION
        and (r.evidence_date is not None or r.effective_date is not None)
    ]
    if offenders:
        raise DataError(
            "filing cessation assigned a lifecycle date for CIKs "
            f"{sorted(offenders)[:10]}; cessation is a candidate signal, not a death date"
        )
