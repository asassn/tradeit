"""The denominator: how many securities disappeared, and how well we know it.

Every count published here carries its **evidence strength**, because a 2001
termination count that is mostly ``CESSATION_ONLY`` is a materially weaker claim
than one that is mostly ``FORM_DIRECT``, and a single number throws that away.

Coverage against a vendor roster is reported as **two bounds, always together**:

``matched_coverage``
    over identity-``RESOLVED`` denominator entries. The optimistic bound -- it
    implicitly assumes unresolved entries would have matched.
``bounded_coverage``
    over ``RESOLVED + AMBIGUOUS + UNRESOLVED``. The pessimistic bound -- it
    assumes none of them would.

The truth is between them. Publishing only the first is the standard way this
measurement is made to look better than it is, so :class:`CoverageBounds` has no
single-number accessor to reach for.

**The survivorship classification deliberately cannot reach its top grade.**
:data:`RESEARCH_GRADE_THRESHOLD` is ``None`` and stays ``None`` until the
denominator has actually been built and its distribution examined. Thirty
controls passing is a stress test, not proof of global completeness, and picking
a number before seeing the data would be choosing the answer first.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tradeit.edgar.evidence import EvidenceStrength, EvidenceType, LifecycleScope
from tradeit.edgar.identity import MappingStatus, SecurityMapping
from tradeit.edgar.lifecycle import (
    ExitResolution,
    IssuerTimeline,
    assert_cessation_undated,
    assert_exit_not_contradicted,
)

__all__ = [
    "LIFESPAN_BUCKETS",
    "RESEARCH_GRADE_THRESHOLD",
    "Classification",
    "CoverageBounds",
    "Denominator",
    "SurvivorshipClass",
    "classify_corpus",
]

#: Deliberately unset. See the module docstring and :func:`classify_corpus`.
RESEARCH_GRADE_THRESHOLD: float | None = None

#: Upper bounds in years; the last bucket is open-ended.
LIFESPAN_BUCKETS: tuple[tuple[str, float], ...] = (
    ("<1y", 1.0),
    ("1-3y", 3.0),
    ("3-10y", 10.0),
    (">10y", float("inf")),
)


class SurvivorshipClass(StrEnum):
    SURVIVORSHIP_SAFE_RESEARCH_GRADE = "survivorship_safe_research_grade"
    MATERIALLY_SURVIVORSHIP_CORRECTED = "materially_survivorship_corrected"
    PARTIALLY_SURVIVORSHIP_CORRECTED = "partially_survivorship_corrected"
    SURVIVOR_BIASED = "survivor_biased"


@dataclass(frozen=True, slots=True)
class CoverageBounds:
    """Two bounds and the gap between them. There is no third number."""

    matched_numerator: int
    resolved_denominator: int
    full_denominator: int

    @property
    def matched_coverage(self) -> float | None:
        if self.resolved_denominator == 0:
            return None
        return self.matched_numerator / self.resolved_denominator

    @property
    def bounded_coverage(self) -> float | None:
        if self.full_denominator == 0:
            return None
        return self.matched_numerator / self.full_denominator

    @property
    def uncertainty(self) -> float | None:
        """How much of the answer is identity mapping rather than vendor coverage."""
        upper, lower = self.matched_coverage, self.bounded_coverage
        if upper is None or lower is None:
            return None
        return upper - lower

    def summary(self) -> dict[str, object]:
        return {
            "matched_numerator": self.matched_numerator,
            "resolved_denominator": self.resolved_denominator,
            "full_denominator": self.full_denominator,
            "matched_coverage": self.matched_coverage,
            "bounded_coverage": self.bounded_coverage,
            "uncertainty": self.uncertainty,
        }


@dataclass(slots=True)
class Denominator:
    """Aggregated lifecycle conclusions, sliced the ways the probe needs.

    Construction runs :func:`assert_cessation_undated` **and**
    :func:`assert_exit_not_contradicted`, so neither a denominator that dated a
    cessation nor one that dated an exit before the registrant's own last
    periodic report can be built at all.
    """

    resolutions: list[ExitResolution]
    timelines: dict[int, IssuerTimeline] = field(default_factory=dict)
    mappings: dict[int, SecurityMapping] = field(default_factory=dict)
    missing_quarters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        assert_cessation_undated(self.resolutions)
        assert_exit_not_contradicted(self.resolutions)

    # -- counts ------------------------------------------------------------

    def counts_by_evidence_type(self) -> dict[str, int]:
        counts = {str(t): 0 for t in EvidenceType}
        for r in self.resolutions:
            counts[str(r.evidence_type)] += 1
        return counts

    def counts_by_strength(self) -> dict[str, int]:
        counts = {str(s): 0 for s in EvidenceStrength}
        for r in self.resolutions:
            counts[str(r.strength)] += 1
        return counts

    def counts_by_scope(self) -> dict[str, int]:
        counts = {str(s): 0 for s in LifecycleScope}
        for r in self.resolutions:
            for scope in r.scopes:
                counts[str(scope)] += 1
        return counts

    def counts_by_year(self, *, confirmed_only: bool = True) -> dict[int, int]:
        """Terminations per year, keyed on ``evidence_date``.

        Cessation candidates are absent by construction: they have no date, and
        inventing one is the error this whole module exists to prevent. When
        ``confirmed_only`` is False the total is unchanged -- undated entries
        still cannot be placed in a year -- and the difference is visible in
        :meth:`counts_by_evidence_type`.
        """
        counts: Counter[int] = Counter()
        for r in self.resolutions:
            if r.evidence_date is None:
                continue
            if confirmed_only and not r.is_confirmed:
                continue
            counts[r.evidence_date.year] += 1
        return dict(sorted(counts.items()))

    def undated_exits(self) -> int:
        """Exits we believe happened but cannot place in time.

        ``NON_EXIT_REGISTRANT_STILL_REPORTING`` is deliberately **not** counted
        here. Folding it in would have been the easy way to make the totals
        reconcile after the supersession fix, and it would have preserved the
        original error in a quieter form: these are registrants we believe did
        *not* exit, so counting them among exits-we-cannot-date would still be
        claiming an exit. :meth:`non_exits` reports them separately.
        """
        return sum(
            1
            for r in self.resolutions
            if r.evidence_type
            in {EvidenceType.POSSIBLE_EXIT_FILING_CESSATION, EvidenceType.UNRESOLVED_EXIT}
        )

    def non_exits(self) -> int:
        """Registrants with confirming filings that went on reporting anyway."""
        return sum(
            1
            for r in self.resolutions
            if r.evidence_type is EvidenceType.NON_EXIT_REGISTRANT_STILL_REPORTING
        )

    def superseded_evidence_counts(self) -> dict[str, int]:
        """How far supersession reached, so the fix is measurable rather than asserted.

        ``resolutions_with_superseded_evidence`` includes the confirmed exits
        that kept a date from a *standing* filing, which :meth:`non_exits` does
        not -- the two answer different questions and are reported together.
        """
        touched = [r for r in self.resolutions if r.superseded]
        return {
            "resolutions_with_superseded_evidence": len(touched),
            "superseded_filings": sum(len(r.superseded) for r in touched),
            "still_dated_from_a_standing_filing": sum(
                1 for r in touched if r.evidence_date is not None
            ),
        }

    def mapping_counts(self) -> dict[str, int]:
        counts = {str(status): 0 for status in MappingStatus}
        for cik in (r.cik for r in self.resolutions):
            mapping = self.mappings.get(cik)
            if mapping is None:
                counts[str(MappingStatus.UNRESOLVED)] += 1
            else:
                counts[str(mapping.status)] += 1
        return counts

    def supplied_mapping_reach(self) -> dict[str, int]:
        """How far the supplied identity evidence actually reaches into *this* corpus.

        **Supplying a mapping is not the same as identifying a registrant**, and
        the difference is invisible in :meth:`mapping_counts`, which iterates
        resolutions rather than mappings. A mapping whose CIK never appears in
        the index range being built contributes to no count above and no
        coverage bound below; it is simply inert. Without this, a reader who
        knows thirty controls were supplied has no way to see that some of them
        landed nowhere, and would reasonably assume otherwise.

        ``matched`` is therefore the only one of these three numbers that has
        affected anything the denominator publishes.
        """
        present = {r.cik for r in self.resolutions}
        matched = sum(1 for cik in self.mappings if cik in present)
        return {
            "supplied": len(self.mappings),
            "matched": matched,
            "unmatched": len(self.mappings) - matched,
        }

    def lifespan_buckets(self) -> dict[str, int]:
        """Registrant lifespans, first filing to last, for dated exits only."""
        counts = {label: 0 for label, _ in LIFESPAN_BUCKETS}
        for r in self.resolutions:
            timeline = self.timelines.get(r.cik)
            if timeline is None or r.evidence_date is None:
                continue
            years = (r.evidence_date - timeline.first_seen).days / 365.25
            for label, upper in LIFESPAN_BUCKETS:
                if years < upper:
                    counts[label] += 1
                    break
        return counts

    def cohort_survival(self, horizons: Sequence[int] = (3, 5, 10)) -> dict[int, dict[int, float]]:
        """Fraction of each listing cohort still alive after *n* years.

        Cohort membership uses the exchange-listing birth where one exists and
        the first filing otherwise. "Still alive" means no *dated* confirmed
        exit within the horizon -- undated candidates count as alive, which
        biases these curves toward survival and is stated rather than hidden.
        """
        by_cohort: dict[int, list[tuple[dt.date, dt.date | None]]] = defaultdict(list)
        exits = {r.cik: r.evidence_date for r in self.resolutions if r.is_confirmed}
        for cik, timeline in self.timelines.items():
            birth = timeline.listing_start or timeline.first_seen
            by_cohort[birth.year].append((birth, exits.get(cik)))

        out: dict[int, dict[int, float]] = {}
        for year, members in sorted(by_cohort.items()):
            if not members:
                continue
            row: dict[int, float] = {}
            for horizon in horizons:
                alive = sum(
                    1
                    for birth, exit_date in members
                    if exit_date is None or (exit_date - birth).days / 365.25 > horizon
                )
                row[horizon] = alive / len(members)
            out[year] = row
        return out

    # -- coverage ----------------------------------------------------------

    def coverage(self, vendor_tickers: Sequence[str]) -> CoverageBounds:
        """Match a vendor roster against the denominator. Both bounds, always.

        **Known and unaddressed: an identity break flatters this measurement.**
        Matching is by ticker, and both registrants behind a broken identity
        carry the same one -- ``GM`` names two CIKs, ``ENE`` names two, ``AOL``
        and ``BBBY`` likewise. One roster entry therefore matches both, and
        ``matched_numerator`` counts two registrants covered where the vendor
        can hold at most one series. The overstatement is exactly the size of
        the identity breaks in the corpus, which is the population these
        controls were selected to expose.

        Left as it is deliberately: correcting it means deciding *which* issuer
        a roster ticker refers to, which is a validity-window question against
        vendor metadata this code has not been given, and guessing would
        manufacture the attribution rather than measure it. Anyone reading a
        coverage figure computed over identity-break controls should read it as
        an upper bound on both ends.
        """
        roster = {t.strip().upper() for t in vendor_tickers}
        resolved = 0
        matched = 0
        for r in self.resolutions:
            mapping = self.mappings.get(r.cik)
            if mapping is not None and mapping.counts_in_numerator:
                resolved += 1
                if mapping.ticker and mapping.ticker.upper() in roster:
                    matched += 1
        return CoverageBounds(
            matched_numerator=matched,
            resolved_denominator=resolved,
            full_denominator=len(self.resolutions),
        )

    def report(self) -> dict[str, object]:
        return {
            "registrants": len(self.resolutions),
            "counts_by_evidence_type": self.counts_by_evidence_type(),
            "counts_by_strength": self.counts_by_strength(),
            "counts_by_scope": self.counts_by_scope(),
            "counts_by_year_confirmed": self.counts_by_year(),
            "undated_exits": self.undated_exits(),
            "non_exits_registrant_still_reporting": self.non_exits(),
            "superseded_evidence": self.superseded_evidence_counts(),
            "mapping_counts": self.mapping_counts(),
            "supplied_mappings": self.supplied_mapping_reach(),
            "lifespan_buckets": self.lifespan_buckets(),
            "missing_quarters": list(self.missing_quarters),
        }


@dataclass(frozen=True, slots=True)
class Classification:
    """A corpus classification, or a refusal to give one."""

    assigned: SurvivorshipClass | None
    reason: str
    controls_passed: int
    controls_total: int
    bounds: CoverageBounds | None = None

    def summary(self) -> dict[str, object]:
        return {
            "assigned": str(self.assigned) if self.assigned else None,
            "reason": self.reason,
            "controls": f"{self.controls_passed}/{self.controls_total}",
            "bounds": self.bounds.summary() if self.bounds else None,
        }


def classify_corpus(
    bounds: CoverageBounds,
    *,
    controls_passed: int,
    controls_total: int,
    research_grade_threshold: float | None = RESEARCH_GRADE_THRESHOLD,
) -> Classification:
    """Assign a survivorship class from the measurement.

    **The top grade is unreachable until a threshold is set deliberately.**
    Passing every control is *necessary* for research grade and is explicitly
    not *sufficient*: thirty securities are a stress test against known failure
    modes, not evidence about the other fifteen thousand. Until the denominator
    exists and its distribution has been examined, this function returns
    ``assigned=None`` rather than guessing, and the caller must treat that as
    "not yet classified" rather than as a pass.
    """
    lower = bounds.bounded_coverage
    if lower is None:
        return Classification(
            assigned=None,
            reason="empty denominator; nothing to classify",
            controls_passed=controls_passed,
            controls_total=controls_total,
            bounds=bounds,
        )

    if controls_passed < controls_total:
        # Failing a control caps the classification but does not by itself
        # decide it; the coverage bound still discriminates the lower classes.
        pass

    if lower >= 0.45 and controls_passed == controls_total:
        if research_grade_threshold is None:
            return Classification(
                assigned=SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED,
                reason=(
                    "coverage and controls are consistent with a higher grade, but the "
                    "research-grade threshold is deliberately unset until the denominator "
                    "has been built and its distribution examined; 30 controls are a "
                    "stress test, not proof of global completeness"
                ),
                controls_passed=controls_passed,
                controls_total=controls_total,
                bounds=bounds,
            )
        if lower >= research_grade_threshold:
            return Classification(
                assigned=SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE,
                reason=f"bounded_coverage {lower:.3f} >= threshold {research_grade_threshold:.3f} "
                "and all controls passed",
                controls_passed=controls_passed,
                controls_total=controls_total,
                bounds=bounds,
            )

    if lower >= 0.45:
        return Classification(
            assigned=SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED,
            reason=f"bounded_coverage {lower:.3f}; controls {controls_passed}/{controls_total}",
            controls_passed=controls_passed,
            controls_total=controls_total,
            bounds=bounds,
        )
    if lower >= 0.25:
        return Classification(
            assigned=SurvivorshipClass.PARTIALLY_SURVIVORSHIP_CORRECTED,
            reason=f"bounded_coverage {lower:.3f}; controls {controls_passed}/{controls_total}",
            controls_passed=controls_passed,
            controls_total=controls_total,
            bounds=bounds,
        )
    return Classification(
        assigned=SurvivorshipClass.SURVIVOR_BIASED,
        reason=f"bounded_coverage {lower:.3f} below 0.25",
        controls_passed=controls_passed,
        controls_total=controls_total,
        bounds=bounds,
    )
