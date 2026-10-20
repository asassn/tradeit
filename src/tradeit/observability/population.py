"""Checking the *record's* purity, not a population somebody constructed.

Phase 8 built `portfolio/mandate.py` so Day, Swing and Retirement are three
separate statistical populations and no learning, backtesting or scoring
population may mix them. `check_population` makes that executable, and until
now it had only ever been handed observations assembled in memory — including
by the audit of 2026-10-17, which built a list of 50,000 identical values and
confirmed the expected answer.

**That proves the function works. It does not prove the record is pure**, and
the record is what a later study would actually be drawn from. Migration 0020
added the horizon columns that make the question askable at all; this asks it.

What a NULL horizon does, and why it does not simply get skipped
----------------------------------------------------------------

A score with no recorded mandate cannot be made into an
:class:`~tradeit.portfolio.mandate.Observation` — there is nothing to observe
it as. Two tempting answers are both wrong:

* **Skip it.** Then a record half of whose rows are unattributable reports a
  clean population, and the certification means nothing.
* **Assume Swing.** That is the default migration 0020 refused for exactly this
  reason: a score mislabelled as a horizon it did not come from is worse than
  one labelled NULL.

So they are **counted and reported, and their presence blocks certification.**
:attr:`RecordPopulation.certified` is False while any row is unattributable,
and the count says how many — which is the `UNRESOLVED`-is-first-class
discipline applied to a population rather than to a number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe
from tradeit.portfolio.mandate import (
    Mandate,
    Observation,
    PopulationReport,
    UnitOfStudy,
    check_population,
)
from tradeit.storage.tables import OpportunityScore

__all__ = ["RecordPopulation", "check_recorded_scores"]


@dataclass(frozen=True, slots=True)
class RecordPopulation:
    """What the recorded scores are, as a population, and whether that is sayable."""

    total_rows: int
    #: Rows whose mandate or timeframe is NULL, or names something the enums do
    #: not. Not skipped: they block certification and are counted.
    unattributable: int
    #: Rows whose horizon pair the mandate table does not admit — a Retirement
    #: conclusion on 5-minute bars, say. Separate from unattributable because
    #: the horizon *was* recorded; it is the pairing that is wrong, which is a
    #: different defect with a different fix.
    ineligible: int
    report: PopulationReport | None
    unit: UnitOfStudy | None

    @property
    def certified(self) -> bool:
        """Whether this record may be used as a single-horizon population.

        False while anything is unattributable, **even if every attributable
        row agrees.** A record that is 90% Swing and 10% unlabelled is not a
        Swing population; it is a Swing population plus an unknown, and the
        unknown is exactly what a later study would silently absorb.
        """
        if self.unattributable or self.ineligible:
            return False
        return self.report is not None and self.report.admissible

    def describe(self) -> str:
        lines = [f"{self.total_rows:,} recorded scores"]
        if self.unit is not None:
            lines.append(
                "  declared unit: "
                + "/".join(sorted(m.value for m in self.unit.mandates))
                + " at "
                + "/".join(sorted(t.value for t in self.unit.timeframes))
            )
        if self.report is not None:
            lines.append(f"  {self.report.describe()}")
        if self.unattributable:
            lines.append(
                f"  {self.unattributable:,} rows have no recorded horizon and are NOT "
                "skipped: they block certification, because a record that is mostly "
                "one horizon plus an unknown is not that horizon's population"
            )
        if self.ineligible:
            lines.append(
                f"  {self.ineligible:,} rows pair a mandate with a timeframe it does "
                "not admit, which the mandate table forbids"
            )
        lines.append(f"  certified: {self.certified}")
        return "\n".join(lines)


def check_recorded_scores(
    session: Session, *, mandate: Mandate, timeframe: Bartimeframe
) -> RecordPopulation:
    """Read the recorded scores and check them against a declared unit of study.

    The unit is **declared by the caller**, not inferred from the rows. Reading
    the majority horizon out of the data and then checking the data against it
    would always pass — the check would be comparing the record to itself,
    which is the same failure `BrokerExecutionService` refuses when it
    reconciles the broker against its own view.
    """
    rows: Sequence[OpportunityScore] = session.execute(select(OpportunityScore)).scalars().all()
    observations: list[Observation] = []
    unattributable = 0
    ineligible = 0
    for row in rows:
        if not row.mandate or not row.timeframe:
            unattributable += 1
            continue
        try:
            row_mandate = Mandate(row.mandate)
            row_timeframe = Bartimeframe(row.timeframe)
        except ValueError:
            # A stored string the enums do not recognise. Counted with the
            # NULLs rather than raising: a record written by an older or
            # unknown writer is a fact about the record, and refusing to report
            # at all would be less useful than reporting that it cannot be
            # certified.
            unattributable += 1
            continue
        observations.append(Observation(mandate=row_mandate, timeframe=row_timeframe))

    unit = UnitOfStudy.single(mandate, timeframe)
    report = check_population(observations, unit) if observations else None
    return RecordPopulation(
        total_rows=len(rows),
        unattributable=unattributable,
        ineligible=ineligible,
        report=report,
        unit=unit,
    )
