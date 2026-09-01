"""Score 3: the number that ranks capital, and the only one that may authorise.

**Combination is not multiplication.** The obvious form —
``standalone * fit`` — is what ``AllocationCandidate.adjusted_score`` already
does, and it has a property that looks harmless and is not: a good enough
standalone score can dominate any fit short of zero. That is a stock picker's
arithmetic. A portfolio manager's answer to "we already own three of these" is
not *"score it slightly lower"*.

So the combination has two parts that never merge:

* ``admissible`` comes from the fit's **vetoes alone**. No standalone score,
  however high, makes a vetoed trade permissible. It is not a term in a sum.
* ``total`` is a weighted blend for **ranking the admissible ones**, and it is
  computed for inadmissible candidates too — because "forbidden but would have
  ranked first" is worth seeing, and discarding it hides what a limit cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradeit.opportunity.fit import PortfolioFit

__all__ = ["FinalOpportunityScore", "combine"]

#: How much of the ranking number is the setup, and how much is the fit.
#: Deliberately not 1.0/0.0 in either direction: at 1.0 the portfolio is
#: ignored, and at 0.0 the setup is. Both are failures of the thing this phase
#: exists to build.
DEFAULT_STANDALONE_WEIGHT = 0.6


@dataclass(frozen=True, slots=True)
class FinalOpportunityScore:
    """The ranking number, its two inputs, and whether it may be acted on."""

    standalone: float
    fit: PortfolioFit
    total: float
    standalone_weight: float

    @property
    def admissible(self) -> bool:
        """**Decided by the fit's vetoes and nothing else.**"""
        return self.fit.admissible

    @property
    def may_authorise(self) -> bool:
        """The only property a caller should act on.

        Named to be awkward to misuse. ``total`` is a sort key; a high total on
        an inadmissible candidate is a real and useful fact, and acting on one
        is the failure this phase exists to prevent.
        """
        return self.admissible

    def explain(self) -> dict[str, object]:
        return {
            "standalone": self.standalone,
            "fit": self.fit.score,
            "total": self.total,
            "admissible": self.admissible,
            "vetoes": [str(v) for v in self.fit.vetoes],
            "fit_components": self.fit.explain(),
        }


def combine(
    standalone: float,
    fit: PortfolioFit,
    *,
    standalone_weight: float = DEFAULT_STANDALONE_WEIGHT,
) -> FinalOpportunityScore:
    """Blend the two scores for ranking; take admissibility from the fit alone."""
    if not 0.0 <= standalone_weight <= 1.0:
        raise ValueError(f"standalone_weight must be in [0, 1], got {standalone_weight}")
    total = standalone * standalone_weight + fit.score * (1.0 - standalone_weight)
    return FinalOpportunityScore(
        standalone=standalone, fit=fit, total=total, standalone_weight=standalone_weight
    )
