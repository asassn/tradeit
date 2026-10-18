"""Score 3 in the seat that decides, rather than beside it.

Authorised by the owner on 2026-10-18, as the scoped proposition the standing
rule requires: *"wire it before paper trading"*, so that paper trading
exercises the decision path intended for live rather than a different one.

**What this changes.** Until now `tradeit.opportunity` was measured next to the
decision and never inside it — every portfolio result this project has
published was ranked by :class:`DiversityAwareRanker` with
``correlation_weight=0.0``, and the three-score separation played no part in
choosing a trade. This class puts score 3 in the ranking seat.

**Why a ranker and not a change to the cycle.** ``AllocationRanker`` already
*is* the portfolio's seat for "see the whole basket and decide" — its docstring
says so, and it already returns candidates in allocation order with permission
to return fewer. So wiring score 3 needs no change to ``PortfolioCycle`` at
all, and a run reproduces its old result exactly by constructing the old
ranker. The rejected alternative was to teach ``_plan_entries`` about fit
directly, which would have put a portfolio policy inside the mechanism that
applies it and made the old behaviour unreachable.

**What the vetoes do and do not add.** ``score_fit``'s refusals overlap the risk
engine deliberately: ``MostRestrictiveEngine`` already refuses on position
count, heat and sector exposure, and it is the authority. A fit veto here
refuses *earlier*, before the authoritative sizing — so the measurable change
from wiring this is mostly the **order**, not the admissions. Said plainly
because "we turned on portfolio fit and admissions barely moved" is the expected
result and would otherwise read as a defect.

Scale, which is the trap
------------------------

``combine`` blends score 1 and the fit **linearly**, and ``OpportunityScore.total``
declares no range. The moving-average baseline puts a raw spread fraction there
— around 0.02 — while a fit score is 0 to 1. Blending those at a declared 0.6
weight produces a number the fit dominates completely, and the
``standalone_weight`` parameter would then be describing a mixture that is not
happening.

**So this refuses.** A standalone total outside [0, 1] raises
:class:`StandaloneScoreOutOfRange` rather than being normalised on the
candidate's behalf. The rejected alternative was within-slate min-max
normalisation, which is worse than it looks: on a two-candidate slate it forces
the better name to exactly 1.0 and the worse to exactly 0.0 whatever the real
gap between them, so the blend weight would vary with slate size. A caller
whose scorer is unbounded normalises it where the scale is understood and says
what it did — :func:`normalise_total` is provided for exactly that and is
deliberately not applied automatically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.opportunity.final import DEFAULT_STANDALONE_WEIGHT, combine
from tradeit.opportunity.fit import PortfolioContext, score_fit
from tradeit.portfolio.base import AllocationCandidate, PortfolioState
from tradeit.risk.contextual import UNCLASSIFIED
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import RiskConfig, SizingConfig

__all__ = [
    "OpportunityFitRanker",
    "StandaloneScoreOutOfRange",
    "normalise_total",
]


class StandaloneScoreOutOfRange(ValueError):
    """A standalone total that cannot be blended with a fit score honestly."""


def normalise_total(score: OpportunityScore, *, lower: float, upper: float) -> OpportunityScore:
    """Rescale a standalone total into [0, 1] against a **declared** range.

    The bounds are the caller's to state, because only the caller knows what
    its scorer can produce. Values outside them clamp, which is a deliberate
    choice over raising: a declared range that a live feed occasionally exceeds
    should not halt a session, and a clamped extreme is still ranked at the top.

    The components are rescaled with the total so :attr:`OpportunityScore.reconciles`
    keeps holding — an explanation that no longer sums to the number it explains
    is the specific failure that property exists to catch.
    """
    if upper <= lower:
        raise ValueError(f"upper must exceed lower, got lower={lower}, upper={upper}")
    span = upper - lower
    scaled = max(0.0, min(1.0, (score.total - lower) / span))
    if score.total == 0.0 or not score.components:
        components: tuple[ScoreComponent, ...] = tuple(
            ScoreComponent(
                name=component.name,
                raw_value=component.raw_value,
                normalised=component.normalised,
                weight=0.0,
            )
            for component in score.components
        )
        return _replace_score(score, scaled, components)
    factor = scaled / score.total
    return _replace_score(
        score,
        scaled,
        tuple(
            ScoreComponent(
                name=component.name,
                raw_value=component.raw_value,
                normalised=component.normalised,
                weight=component.weight * factor,
            )
            for component in score.components
        ),
    )


def _replace_score(
    score: OpportunityScore, total: float, components: tuple[ScoreComponent, ...]
) -> OpportunityScore:
    return OpportunityScore(
        instrument_id=score.instrument_id,
        session_date=score.session_date,
        direction=score.direction,
        total=total,
        components=components,
        feature_set_digest=score.feature_set_digest,
        strategy_config_digest=score.strategy_config_digest,
    )


@dataclass(frozen=True, slots=True)
class OpportunityFitRanker:
    """Ranks by score 3, and refuses what the fit vetoes.

    ``sectors`` is a flat point-in-time map, matching ``SectorExposureRule``.
    Unclassified instruments pool into ``UNCLASSIFIED`` exactly as they do
    there, so a name with no classification is constrained collectively rather
    than treated as its own sector — conservative, and the same compromise
    recorded for the sector limit.
    """

    risk: RiskConfig
    sizing: SizingConfig
    sectors: Mapping[int, str]
    standalone_weight: float = DEFAULT_STANDALONE_WEIGHT
    #: Whether a fit veto removes a candidate or only ranks it last.
    #:
    #: Default True. False exists for measurement — it isolates the ranking
    #: effect from the refusal effect, which is the only way to answer "did
    #: wiring this change the order, or only the admissions?"
    refuse_vetoed: bool = True

    @property
    def name(self) -> str:
        return "opportunity_fit"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "standalone_weight": self.standalone_weight,
            "refuse_vetoed": self.refuse_vetoed,
            "classified_instruments": len(self.sectors),
            "sector_limit_pct": self.risk.max_sector_exposure_pct,
            "heat_limit_pct": self.risk.max_portfolio_heat_pct,
        }

    def rank(
        self,
        candidates: Sequence[AllocationCandidate],
        portfolio: PortfolioState,
        correlation: dict[tuple[int, int], float] | None = None,
    ) -> Sequence[AllocationCandidate]:
        if not candidates:
            return ()
        exposures = _sector_exposures(portfolio, self.sectors)
        held = {position.instrument_id: position for position in portfolio.open_positions}
        held_ids = list(held)
        equity = portfolio.equity

        scored: list[tuple[float, AllocationCandidate]] = []
        for candidate in candidates:
            standalone = candidate.score.total
            if not 0.0 <= standalone <= 1.0:
                raise StandaloneScoreOutOfRange(
                    f"standalone total {standalone} for instrument "
                    f"{candidate.score.instrument_id} is outside [0, 1]; blending it with a "
                    "fit score would make standalone_weight describe a mixture that is not "
                    "happening. Normalise it against a declared range first "
                    "(normalise_total) and say what range was used."
                )
            instrument_id = candidate.score.instrument_id
            sector = candidate.sector or self.sectors.get(instrument_id, UNCLASSIFIED)
            position = held.get(instrument_id)
            existing_weight = 0.0
            if position is not None and equity > 0:
                price = portfolio.last_prices.get(instrument_id, position.average_entry_price)
                existing_weight = float(position.market_value(price) / equity)
            context = PortfolioContext(
                equity=equity,
                cash=portfolio.cash,
                open_positions=portfolio.position_count,
                position_limit=self.risk.max_positions,
                sector_exposure=exposures.get(sector, 0.0),
                sector_limit=self.risk.max_sector_exposure_pct,
                heat=float(portfolio.heat()),
                heat_limit=self.risk.max_portfolio_heat_pct,
                existing_weight=existing_weight,
                max_weight=self.sizing.max_position_pct_of_equity,
                overlap=_overlap(instrument_id, held_ids, correlation),
                required_cash=candidate.sizing.notional,
            )
            fit = score_fit(context)
            final = combine(standalone, fit, standalone_weight=self.standalone_weight)
            if self.refuse_vetoed and not final.admissible:
                continue
            # A vetoed candidate kept for measurement sorts below every
            # admissible one regardless of its number, so `refuse_vetoed=False`
            # changes what is reported and never what is preferred.
            scored.append((final.total if final.admissible else final.total - 1.0, candidate))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return tuple(candidate for _, candidate in scored)


def _sector_exposures(portfolio: PortfolioState, sectors: Mapping[int, str]) -> dict[str, float]:
    """Each sector's share of equity, unclassified holdings pooled.

    Duplicates ``risk.snapshot.sector_exposures`` rather than importing it, so
    that the opportunity package does not depend on the risk package to rank.
    The duplication is four lines and the coupling it avoids is a cycle waiting
    to happen; a test pins the two against each other.
    """
    if portfolio.equity <= 0:
        return {}
    totals: dict[str, Decimal] = {}
    for position in portfolio.open_positions:
        sector = sectors.get(position.instrument_id, UNCLASSIFIED)
        price = portfolio.last_prices.get(position.instrument_id, position.average_entry_price)
        totals[sector] = totals.get(sector, Decimal(0)) + position.market_value(price)
    return {sector: float(value / portfolio.equity) for sector, value in sorted(totals.items())}


def _overlap(
    instrument_id: int,
    held_ids: Sequence[int],
    correlation: Mapping[tuple[int, int], float] | None,
) -> float:
    """Strongest positive correlation with anything held; 0.0 when unknown.

    0.0 for an unmeasured pair understates overlap, and that is the right
    direction for a *ranking* input while being the wrong one for a gate —
    which is exactly why ``CorrelationRule`` fails closed and this does not. A
    ranker that refused to rank an unmeasured pair would decline to choose at
    all, and the caller has no such option.
    """
    if correlation is None:
        return 0.0
    best = 0.0
    for other in held_ids:
        if other == instrument_id:
            continue
        value = correlation.get((min(instrument_id, other), max(instrument_id, other)))
        if value is not None:
            best = max(best, value)
    return max(0.0, best)
