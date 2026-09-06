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
"""

from tradeit.opportunity.final import FinalOpportunityScore, combine
from tradeit.opportunity.fit import (
    FitComponent,
    PortfolioContext,
    PortfolioFit,
    VetoReason,
    score_fit,
)
from tradeit.opportunity.slate import Contender, Slate, build_slate

__all__ = [
    "Contender",
    "FinalOpportunityScore",
    "FitComponent",
    "PortfolioContext",
    "PortfolioFit",
    "Slate",
    "VetoReason",
    "build_slate",
    "combine",
    "score_fit",
]
