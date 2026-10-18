"""Phase 7 — the three scores, kept apart.

    1. **Standalone Opportunity Score** — how good is this setup, in isolation?
    2. **Portfolio Fit Score** — how well does it fit *this* portfolio, now?
    3. **Final Portfolio Opportunity Score** — the combination that ranks capital.

**The separation is the deliverable, not an implementation detail.** A great
stock the portfolio already effectively owns three times over is not a great
trade, and a system that cannot say so is a stock picker wearing a portfolio
manager's clothes. Only the third score may authorise a trade, and it can be
refused by the second no matter what the first says.

Score 1 already existed as
:class:`~tradeit.strategy.base.OpportunityScore`. This package adds the two that
turn it into a portfolio decision.

``ranking`` puts score 3 in the allocation seat, authorised 2026-10-18. Until
then this package was measured beside the decision and never inside it.
"""

from tradeit.opportunity.final import FinalOpportunityScore, combine
from tradeit.opportunity.fit import (
    FitComponent,
    PortfolioContext,
    PortfolioFit,
    VetoReason,
    score_fit,
)
from tradeit.opportunity.gates import GateOutcome, GateResult, breakout_confirmation_gate
from tradeit.opportunity.ranking import (
    OpportunityFitRanker,
    StandaloneScoreOutOfRange,
    normalise_total,
)
from tradeit.opportunity.slate import Contender, Slate, build_slate

__all__ = [
    "Contender",
    "FinalOpportunityScore",
    "FitComponent",
    "GateOutcome",
    "GateResult",
    "OpportunityFitRanker",
    "PortfolioContext",
    "PortfolioFit",
    "Slate",
    "StandaloneScoreOutOfRange",
    "VetoReason",
    "breakout_confirmation_gate",
    "build_slate",
    "combine",
    "normalise_total",
    "score_fit",
]
