"""A slate: the candidates competing for the same capital, at one mandate.

Phase 7 was built before the multi-timeframe gate, and the roadmap records the
consequence plainly — *a score built without them cannot be a mandate-scoped
score*. :mod:`tradeit.opportunity.final` produces a number that ranks; nothing
until now stopped that number being ranked against one produced for a different
horizon.

**A day-trade setup and a retirement position are not competing for the same
capital**, and a list that sorts them together says they are. They have
different holding horizons, different risk models and different corpora; the
only thing they share is the units their score happens to be expressed in.
Ranking on that is the shape the mandate separation exists to prevent, and it
would arrive looking like a perfectly ordinary sorted list.

So a slate carries the mandate and timeframe it was drawn from, and refuses to
be built from a mixture — via the same
:class:`~tradeit.portfolio.mandate.UnitOfStudy` the separation rule already
uses, because a slate *is* a scoring population and inventing a second, weaker
check for it would be how the two come to disagree.

**A declared mixture is still permitted and still labelled.** A swing mandate
that finds setups on both `1d` and `1h` may rank them together when it says so
in advance; :attr:`Slate.combined` then stays true, so a report of the slate
cannot be read as single-timeframe. That is the rule's own escape clause, not
a loosening of it.

Two things kept apart, following :mod:`tradeit.opportunity.final`:

* :attr:`Slate.ranked` orders **everything**, including candidates the fit
  vetoed. "Forbidden, and would have ranked first" is worth seeing, and
  discarding it hides what a limit cost.
* :attr:`Slate.for_capital` is the only sequence a caller may allocate from,
  and a vetoed candidate never appears in it however high its total.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tradeit.core.enums import Bartimeframe
from tradeit.opportunity.final import FinalOpportunityScore
from tradeit.portfolio.mandate import (
    Mandate,
    Observation,
    PopulationReport,
    UnitOfStudy,
    require_population,
)

__all__ = ["Contender", "Slate", "build_slate"]


@dataclass(frozen=True, slots=True)
class Contender:
    """One candidate for capital, carrying where it was found.

    ``mandate`` and ``timeframe`` are recorded on the contender rather than
    assumed by the caller, because the whole failure this module prevents is a
    caller that knows the answer and does not write it down.
    """

    reference: str
    mandate: Mandate
    timeframe: Bartimeframe
    score: FinalOpportunityScore

    def observation(self) -> Observation:
        return Observation(mandate=self.mandate, timeframe=self.timeframe)


@dataclass(frozen=True, slots=True)
class Slate:
    """Candidates competing for one mandate's capital, and their order."""

    unit: UnitOfStudy
    contenders: tuple[Contender, ...]
    population: PopulationReport

    @property
    def mandate(self) -> Mandate:
        """The single mandate this slate allocates for.

        A slate spanning mandates cannot be built, so this is unambiguous by
        construction rather than by convention.
        """
        return next(iter(sorted(self.unit.mandates)))

    @property
    def combined(self) -> bool:
        """True when the declared unit spans more than one timeframe."""
        return self.unit.combined

    @property
    def ranked(self) -> tuple[Contender, ...]:
        """Everything, best total first — vetoed candidates included."""
        return tuple(sorted(self.contenders, key=lambda c: c.score.total, reverse=True))

    @property
    def for_capital(self) -> tuple[Contender, ...]:
        """The only sequence a caller may allocate from.

        Admissibility comes from the fit's vetoes alone, so a high total never
        promotes a refused candidate into this list.
        """
        return tuple(c for c in self.ranked if c.score.may_authorise)

    @property
    def refused(self) -> tuple[Contender, ...]:
        """What a limit cost, in rank order. Reported, never discarded."""
        return tuple(c for c in self.ranked if not c.score.may_authorise)

    def describe(self) -> str:
        shape = "combined" if self.combined else "single"
        return (
            f"{self.mandate.value} slate ({shape}): {len(self.for_capital)} allocatable, "
            f"{len(self.refused)} refused, of {len(self.contenders)}"
        )


def build_slate(contenders: Iterable[Contender], unit: UnitOfStudy) -> Slate:
    """Assemble a slate, refusing a mixture the unit of study did not declare.

    Raises :class:`~tradeit.portfolio.mandate.PopulationNotAdmissible` when a
    contender falls outside the declared unit, when the declaration names a
    timeframe its mandate does not admit, or when the slate is empty — the last
    because an empty slate that ranked cleanly would be a result nobody could
    act on, reported as though it were one.
    """
    held: tuple[Contender, ...] = tuple(contenders)
    if len(unit.mandates) > 1:
        # Stated separately from the population check so the message names the
        # actual error. A mixed-mandate declaration is not a population that
        # failed a test; it is a slate that should never have been proposed.
        raise ValueError(
            "a slate allocates one mandate's capital; "
            f"{sorted(m.value for m in unit.mandates)} cannot share one"
        )
    report = require_population([c.observation() for c in held], unit)
    return Slate(unit=unit, contenders=held, population=report)
