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

The third discipline is **supersession**, and it was added after the first real
run rather than designed in. A confirming filing dates an exit only if the
registrant did not go on reporting afterwards. ``INTEL CORP`` filed a Form 15
in 1994 and files to this day; reading that form as Intel's exit date is not a
near-miss, it is a fabricated death. So a confirming filing that *precedes* the
registrant's last periodic report is **superseded**: it stays in the record as
evidence at its own scope, and it may not supply the exit date.

:func:`assert_exit_not_contradicted` exists so that rule, like the cessation
rule, is checkable rather than merely intended.
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
    ReportingRegime,
    reporting_regime,
)
from tradeit.errors import DataError

__all__ = [
    "CESSATION_QUIET_QUARTERS",
    "ExitResolution",
    "IssuerTimeline",
    "assert_cessation_undated",
    "assert_exit_not_contradicted",
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
    ``None`` for cessation-only, non-exit and unresolved cases -- there is no
    filing that dates an exit to point at. ``effective_date`` is the date the
    event actually took effect and is ``None`` unless a parsed document stated
    it. **Counting by year uses ``evidence_date`` and the reports say so.**

    ``supporting`` and ``superseded`` partition the confirming filings. A
    superseded filing is one the registrant went on reporting after; it is real
    evidence at its own scope and it is disqualified from supplying the exit
    date. Keeping both means a reader can see *why* a date was refused without
    going back to the index.
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
    #: Confirming filings the registrant kept reporting after. Never a date source.
    superseded: tuple[LifecycleEvidence, ...] = ()
    #: Which statute the registrant reported under, from its own filings.
    #:
    #: Carried here so a consumer can scope a coverage denominator without
    #: rebuilding timelines. **It never affects whether or how an exit is
    #: dated** — a fund's exit is resolved exactly as any other registrant's,
    #: which is what keeps these available for a fund corpus later.
    regime: ReportingRegime = ReportingRegime.NEITHER
    note: str = ""

    @property
    def is_exchange_act(self) -> bool:
        """Did this registrant ever report under the Exchange Act?

        The test a price-based coverage denominator wants: an entity that only
        ever filed Investment Company Act reports is overwhelmingly one that
        never traded, so it can contribute denominator and never numerator.
        ``BOTH`` counts as true — a registrant that reported under each did
        report under this one.
        """
        return self.regime in (ReportingRegime.EXCHANGE_ACT, ReportingRegime.BOTH)

    @property
    def is_confirmed(self) -> bool:
        return self.evidence_type in _CONFIRMING or (
            self.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
        )

    @property
    def contradicts_its_own_evidence(self) -> bool:
        """True when a date claimed here precedes the registrant's last periodic report.

        The shape :func:`assert_exit_not_contradicted` refuses to publish. It is
        a property rather than an inline check so that a diagnostic can count
        the offenders without duplicating the definition.
        """
        if self.last_periodic is None:
            return False
        return any(
            date is not None and date < self.last_periodic
            for date in (self.evidence_date, self.effective_date)
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
            "regime": str(self.regime),
            "accessions": [e.accession for e in self.supporting],
            "superseded_accessions": [e.accession for e in self.superseded],
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
    def reporting_regime(self) -> ReportingRegime:
        """Which statute this registrant actually reported under.

        Read from the periodic filings in the archive, so it is a fact about
        the record rather than a judgement about the entity.
        """
        return reporting_regime(e.form_type for e in self.evidence if e.role is FormRole.PERIODIC)

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


def _partition_superseded(
    confirming: Sequence[LifecycleEvidence], last_periodic: dt.date | None
) -> tuple[tuple[LifecycleEvidence, ...], tuple[LifecycleEvidence, ...]]:
    """Split confirming filings into (superseded, standing).

    A filing is **superseded** when the registrant filed a periodic report after
    it. That is the same principle as "a resumption retroactively cancels a
    cessation candidate", applied in the other direction: continued reporting
    falsifies the reading of an earlier filing as the registrant's exit.

    The boundary is strict. A confirming filing on the *same day* as the last
    periodic report is standing, because same-day is not "after" and a
    registrant filing its final report and its Form 25 together is an ordinary
    exit, not a contradiction. That boundary is deliberately identical to the
    one :func:`assert_exit_not_contradicted` tests, so the filter and the guard
    can never disagree about a marginal case.
    """
    if last_periodic is None:
        return (), tuple(confirming)
    superseded = tuple(e for e in confirming if e.evidence_date < last_periodic)
    standing = tuple(e for e in confirming if e.evidence_date >= last_periodic)
    return superseded, standing


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

    **A confirming filing only dates an exit if the registrant stopped reporting
    afterwards.** Filings the registrant kept reporting past are superseded and
    may not supply the date; if every one of them is superseded, the registrant
    has not exited on this evidence and no date is offered at all.
    """
    confirming = [
        e
        for e in timeline.evidence
        if e.evidence_type is not None and e.evidence_type in _CONFIRMING
    ]
    periodic = timeline.periodic_dates
    last_periodic = max(periodic) if periodic else None

    if confirming:
        confirming.sort(key=lambda e: e.evidence_date)
        superseded, standing = _partition_superseded(confirming, last_periodic)
        # Scopes come from *all* confirming filings, superseded included. A
        # superseded Form 25 still removed a listing; what it does not do is
        # date the registrant's exit. Dropping it from the scope set would
        # discard evidence to fix a dating bug.
        scopes = frozenset(e.scope for e in confirming if e.scope is not None)

        if not standing:
            # Every confirming filing precedes the registrant's own last
            # periodic report. This is INTEL CORP: a Form 15 in 1994 and 10-Ks
            # ever since. The filings are real and something ended -- the index
            # names no security class, so it cannot say what -- but the
            # registrant plainly did not exit, and picking a "better" date here
            # would still be picking a date for an event that did not happen.
            return ExitResolution(
                cik=timeline.cik,
                company_name=timeline.company_name,
                regime=timeline.reporting_regime,
                evidence_type=EvidenceType.NON_EXIT_REGISTRANT_STILL_REPORTING,
                # Derived by combining the confirming filings with the periodic
                # reports that outlive them; no single form says this.
                strength=EvidenceStrength.FORM_INFERRED,
                scopes=scopes,
                evidence_date=None,
                effective_date=None,
                supporting=(),
                last_periodic=last_periodic,
                superseded=superseded,
                note=(
                    f"{len(superseded)} confirming filing(s) through "
                    f"{superseded[-1].evidence_date.isoformat()}, all superseded by a "
                    f"periodic report on {last_periodic.isoformat() if last_periodic else '?'}; "
                    "a class-scope event, not a registrant exit -- no date is claimed"
                ),
            )

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
                regime=timeline.reporting_regime,
                evidence_type=EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED,
                strength=EvidenceStrength.FORM_INFERRED,
                scopes=scopes,
                # The *latest* standing filing, and here that is a decision
                # rather than the oversight it once looked like: the conjunction
                # this claim rests on does not exist until its later half is
                # filed, so an earlier date would assert the derived event
                # before its own evidence was complete.
                evidence_date=standing[-1].evidence_date,
                effective_date=None,
                supporting=tuple(confirming),
                last_periodic=last_periodic,
                superseded=superseded,
                note=(
                    "derived from delisting AND registration termination; "
                    "no single filing asserts extinguishment"
                    + (
                        f"; dated from the latest of {len(standing)} standing filing(s), "
                        f"{len(superseded)} superseded"
                        if superseded
                        else ""
                    )
                ),
            )
        # The *earliest standing* filing: the first direct evidence of an exit
        # that the registrant's own later reporting does not contradict.
        # Earliest rather than latest, so that every registrant whose filings
        # were already consistent keeps the date it already had -- a later
        # confirming filing is usually the administrative tail of the same exit,
        # and re-dating all of them would be a far wider change than this defect
        # calls for. The measured split is in EDGAR_DELISTING_DENOMINATOR.md
        # §7bc; it is not restated here, where it would rot.
        primary = standing[0]
        assert primary.evidence_type is not None
        return ExitResolution(
            cik=timeline.cik,
            company_name=timeline.company_name,
            regime=timeline.reporting_regime,
            evidence_type=primary.evidence_type,
            strength=EvidenceStrength.FORM_DIRECT,
            scopes=scopes,
            evidence_date=primary.evidence_date,
            effective_date=primary.effective_date,
            supporting=tuple(confirming),
            last_periodic=last_periodic,
            superseded=superseded,
            note=(
                primary.note
                + (
                    f"; dated from the earliest of {len(standing)} standing filing(s), "
                    f"{len(superseded)} superseded by later periodic reporting"
                    if superseded
                    else ""
                )
            ),
        )

    if periodic:
        last = max(periodic)
        if _quarters_between(last, as_of) >= quiet_quarters:
            return ExitResolution(
                cik=timeline.cik,
                company_name=timeline.company_name,
                regime=timeline.reporting_regime,
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
        regime=timeline.reporting_regime,
        evidence_type=EvidenceType.UNRESOLVED_EXIT,
        strength=EvidenceStrength.NONE,
        scopes=frozenset(),
        evidence_date=None,
        effective_date=None,
        supporting=(),
        last_periodic=last_periodic,
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


def assert_exit_not_contradicted(resolutions: Sequence[ExitResolution]) -> None:
    """Guard: no resolution may date an exit before its own last periodic report.

    The companion to :func:`assert_cessation_undated`, and it exists for the
    same reason: the rule had been stated in prose, nothing checked it, and the
    first run against the real archive produced 12,549 registrants -- 30.7% of
    all dated exits -- whose exit date preceded a periodic report they had
    themselves filed. ``INTEL CORP`` was dated 1994 while still filing in 2026.

    A registrant cannot report after it has exited. This is therefore not a
    tolerance to be tuned but a contradiction in the record, and the denominator
    refuses to publish one. Raises :class:`~tradeit.errors.DataError` naming
    every offender.
    """
    offenders = [r for r in resolutions if r.contradicts_its_own_evidence]
    if offenders:
        worst = max(
            offenders,
            key=lambda r: (
                r.last_periodic - r.evidence_date
                if r.last_periodic and r.evidence_date
                else dt.timedelta(0)
            ),
        )
        raise DataError(
            f"{len(offenders)} exit(s) dated before the registrant's own last periodic "
            f"report, e.g. CIK {worst.cik} ({worst.company_name}) dated "
            f"{worst.evidence_date} with a periodic report on {worst.last_periodic}; "
            "a registrant that reports after its exit date has not exited"
        )
