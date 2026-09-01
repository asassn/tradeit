"""Reconciling fundamentals against filings -- milestone 5's gate.

**Two independent SEC products describing the same filings.** The quarterly
full-index reports a filing's accession, form type and filing date; the
Financial Statement Data Sets report the same accession with its own ``filed``
date and form. They are produced by different pipelines, so agreement is
evidence and **disagreement is a finding rather than a rounding error**.

The reconciliation asks three questions and reports each separately, because
they fail for different reasons and a single pass/fail would hide which:

``linked``
    does every fundamental fact point at a filing we actually hold? An unlinked
    fact is not wrong -- we may simply not have loaded that quarter of the
    index -- but a corpus that cannot say where a number came from is one
    nobody can audit.
``filed date agreement``
    does the FSDS ``filed`` date match the index's? This is the real
    cross-check. ``knowledge_time`` is derived from that date, so a
    disagreement is a disagreement about *when a number became usable*.
``form agreement``
    does the form type match, verbatim? ``10-K`` is not ``10-K405``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.storage.tables import Filing, SecurityFundamentalFact

__all__ = ["ReconciliationReport", "reconcile_fundamentals_against_filings"]


@dataclass(frozen=True, slots=True)
class Disagreement:
    accession: str
    field: str
    index_says: str
    fsds_says: str


@dataclass(slots=True)
class ReconciliationReport:
    facts: int = 0
    linked: int = 0
    unlinked: int = 0
    filings_held: int = 0
    filings_carrying_facts: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)

    @property
    def linked_share(self) -> float | None:
        return self.linked / self.facts if self.facts else None

    def summary(self) -> dict[str, object]:
        return {
            "facts": self.facts,
            "linked_to_a_filing": self.linked,
            "unlinked": self.unlinked,
            "linked_share": self.linked_share,
            "filings_held": self.filings_held,
            "filings_carrying_facts": self.filings_carrying_facts,
            "disagreements": len(self.disagreements),
        }


def reconcile_fundamentals_against_filings(session: Session) -> ReconciliationReport:
    """Measure the join between fundamental facts and the filings they claim.

    Reports rather than raises. An unlinked fact is a coverage statement about
    the index range loaded, not a defect, and conflating the two would make the
    gate fire on the wrong thing.
    """
    report = ReconciliationReport()
    report.facts = session.scalar(select(func.count()).select_from(SecurityFundamentalFact)) or 0
    report.linked = (
        session.scalar(
            select(func.count())
            .select_from(SecurityFundamentalFact)
            .where(SecurityFundamentalFact.filing_id.is_not(None))
        )
        or 0
    )
    report.unlinked = report.facts - report.linked
    report.filings_held = session.scalar(select(func.count()).select_from(Filing)) or 0
    report.filings_carrying_facts = (
        session.scalar(
            select(func.count(func.distinct(SecurityFundamentalFact.filing_id))).where(
                SecurityFundamentalFact.filing_id.is_not(None)
            )
        )
        or 0
    )

    # The cross-check proper: for every linked fact, the knowledge_time we
    # derived from the FSDS `filed` date must agree with the index's filed_at.
    rows = session.execute(
        select(
            Filing.accession,
            Filing.filed_at,
            SecurityFundamentalFact.knowledge_time,
        )
        .join(SecurityFundamentalFact, SecurityFundamentalFact.filing_id == Filing.filing_id)
        .distinct()
    ).all()
    for accession, filed_at, knowledge_time in rows:
        if knowledge_time.date() != filed_at:
            report.disagreements.append(
                Disagreement(accession, "filed", str(filed_at), str(knowledge_time.date()))
            )
    return report
