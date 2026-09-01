"""Score 2: how well does this candidate fit *this* portfolio, right now?

**A discount is not a refusal, and conflating them is the bug this module
exists to prevent.** ``AllocationCandidate.adjusted_score`` multiplies a
standalone score by ``(1 - correlation_penalty)``, which can only ever move a
number toward zero. It cannot express *"this trade is not permitted however good
the setup is"* — and a strong enough setup times a 0.9 penalty still ranks near
the top. Every hard portfolio limit has that shape: a sector cap is not a
preference to be outbid.

So a fit carries two independent things: a **score** in ``[0, 1]`` for ranking,
and a set of **vetoes** that are not scores at all. A veto is absolute. It is
not weighted, it cannot be outvoted by a high standalone score, and there is no
argument the strategy layer can make against it — which is what *risk controls
live outside a strategy's reach* means in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

__all__ = ["FitComponent", "PortfolioContext", "PortfolioFit", "VetoReason", "score_fit"]


class VetoReason(StrEnum):
    """Absolute refusals. Not weights, not penalties, not tie-breakers."""

    ALREADY_AT_FULL_SIZE = "already_at_full_size"
    POSITION_LIMIT_REACHED = "position_limit_reached"
    SECTOR_LIMIT_REACHED = "sector_limit_reached"
    HEAT_LIMIT_EXCEEDED = "heat_limit_exceeded"
    INSUFFICIENT_CASH = "insufficient_cash"


@dataclass(frozen=True, slots=True)
class FitComponent:
    """One reason the fit is what it is. Recorded, never reconstructed."""

    name: str
    value: float
    weight: float
    note: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    """What the portfolio looks like, as a value rather than a query.

    Explicit inputs rather than a ``PortfolioState``, deliberately: that class
    keys on ``instrument_id`` and belongs to ``full-01``. Fit is arithmetic
    about exposure and has no business knowing which corpus it is scoring.
    """

    equity: Decimal
    cash: Decimal
    open_positions: int
    position_limit: int
    #: Fraction of equity already in this candidate's sector, 0-1.
    sector_exposure: float
    sector_limit: float
    #: Portfolio heat: open risk over equity, 0-1.
    heat: float
    heat_limit: float
    #: Fraction of equity already held in this exact security, 0-1.
    existing_weight: float = 0.0
    max_weight: float = 1.0
    #: Estimated overlap with what is already held, 0-1. A proxy for "another
    #: way to own what we own", not a statistical correlation.
    overlap: float = 0.0
    required_cash: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class PortfolioFit:
    """A fit score and, separately, the vetoes. The two never merge."""

    score: float
    components: tuple[FitComponent, ...]
    vetoes: tuple[VetoReason, ...] = ()

    @property
    def admissible(self) -> bool:
        """Whether this trade is permitted at all. **Independent of the score.**"""
        return not self.vetoes

    def explain(self) -> list[tuple[str, float]]:
        ranked = sorted(self.components, key=lambda c: abs(c.contribution), reverse=True)
        return [(c.name, c.contribution) for c in ranked]

    @property
    def reconciles(self) -> bool:
        """Components must sum to the score, as they must for score 1.

        Guards the same failure: a late adjustment applied without being
        recorded, leaving an explanation that does not add up to the number it
        explains.
        """
        return abs(sum(c.contribution for c in self.components) - self.score) < 1e-9


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_fit(context: PortfolioContext) -> PortfolioFit:
    """Score the fit and collect any vetoes.

    Vetoes are gathered **before** the score and do not affect it. A vetoed
    candidate still gets an honest fit number, because "forbidden" and "poor
    fit" are different findings and a reader deserves both — a trade blocked
    only by a sector cap is worth revisiting when that cap frees up, and one
    that also fits badly is not.
    """
    vetoes: list[VetoReason] = []
    if context.existing_weight >= context.max_weight:
        vetoes.append(VetoReason.ALREADY_AT_FULL_SIZE)
    if context.open_positions >= context.position_limit:
        vetoes.append(VetoReason.POSITION_LIMIT_REACHED)
    if context.sector_exposure >= context.sector_limit:
        vetoes.append(VetoReason.SECTOR_LIMIT_REACHED)
    if context.heat >= context.heat_limit:
        vetoes.append(VetoReason.HEAT_LIMIT_EXCEEDED)
    if context.required_cash > context.cash:
        vetoes.append(VetoReason.INSUFFICIENT_CASH)

    # Headroom, not absence: a sector at 90% of its cap fits worse than one at
    # 10%, and the difference matters for ranking even when neither is vetoed.
    sector_headroom = (
        1.0 - _clamp(context.sector_exposure / context.sector_limit)
        if context.sector_limit > 0
        else 0.0
    )
    heat_headroom = (
        1.0 - _clamp(context.heat / context.heat_limit) if context.heat_limit > 0 else 0.0
    )
    slot_headroom = (
        1.0 - _clamp(context.open_positions / context.position_limit)
        if context.position_limit > 0
        else 0.0
    )
    distinctness = 1.0 - _clamp(context.overlap)
    freshness = 1.0 - _clamp(
        context.existing_weight / context.max_weight if context.max_weight > 0 else 1.0
    )

    components = (
        FitComponent(
            "sector_headroom",
            sector_headroom,
            0.25,
            "five names from one sector are one bet wearing five tickers",
        ),
        FitComponent(
            "heat_headroom", heat_headroom, 0.25, "room left if every stop were hit tomorrow"
        ),
        FitComponent("slot_headroom", slot_headroom, 0.15, "unused position slots"),
        FitComponent(
            "distinctness",
            distinctness,
            0.20,
            "how much this is NOT another way to own what we own",
        ),
        FitComponent("freshness", freshness, 0.15, "room left in this security itself"),
    )
    return PortfolioFit(
        score=sum(c.contribution for c in components),
        components=components,
        vetoes=tuple(vetoes),
    )
