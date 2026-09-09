"""Portfolio-level risk rules that need nothing but the snapshot.

Each rule here is a pure function of the proposal, the ``PortfolioState`` and
its own frozen configuration. Rules needing evidence the snapshot does not
carry — sector classifications, correlations, a peak-equity mark — live
separately, because a rule that reaches outside the snapshot for context can
disagree with its neighbours about what the portfolio currently is.

**These rules do not assume the sizer ran.** In the intended composition the
sizer has already applied the heat and position caps, so
:class:`PortfolioHeatRule` will usually agree and allow. That redundancy is
deliberate: sizing and risk are separately composable, a proposal can arrive
from a manual override or a replayed order, and a limit enforced in exactly one
place is a limit that disappears the first time somebody bypasses that place.

**Reductions are floored to whole shares** using ``SizingConfig``, so the
engine cannot hand back a fractional quantity to a portfolio that does not
trade fractions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.risk.base import RiskAssessment
from tradeit.strategy.config import RiskConfig, SizingConfig

__all__ = [
    "GrossExposureRule",
    "MaxPositionsRule",
    "PortfolioHeatRule",
    "PositionSizeRule",
    "PyramidRule",
    "verdict_for_headroom",
]


def verdict_for_headroom(
    *,
    rule_name: str,
    limit_type: RiskLimitType,
    allowed: Decimal,
    proposal: SizingDecision,
    reason: str,
    measured: float,
    limit: float,
) -> RiskAssessment:
    """Shared tail: allow, reduce to ``allowed``, or reject if none is left.

    ``allowed`` is floored to whole shares before the comparison, so a headroom
    of 0.4 shares is a rejection rather than an ``ALLOW_REDUCED`` the caller
    cannot execute.
    """
    whole = allowed.to_integral_value(rounding=ROUND_DOWN)
    if whole >= proposal.quantity:
        return RiskAssessment(
            rule_name=rule_name,
            limit_type=limit_type,
            decision=RiskDecision.ALLOW,
            reason="within limit",
            measured=measured,
            limit=limit,
        )
    if whole <= 0:
        return RiskAssessment(
            rule_name=rule_name,
            limit_type=limit_type,
            decision=RiskDecision.REJECT,
            reason=reason,
            measured=measured,
            limit=limit,
        )
    return RiskAssessment(
        rule_name=rule_name,
        limit_type=limit_type,
        decision=RiskDecision.ALLOW_REDUCED,
        reason=reason,
        measured=measured,
        limit=limit,
        max_allowed_quantity=whole,
    )


@dataclass(frozen=True, slots=True)
class PortfolioHeatRule:
    """Total open risk, including this trade, against ``max_portfolio_heat_pct``.

    Heat answers the question that matters when several positions fail at once:
    *if every stop were hit tomorrow, how much of the account is gone?* A
    per-trade risk limit cannot answer it, because twelve positions each risking
    a permissible 0.5% risk 6% together.
    """

    config: RiskConfig
    name: str = "portfolio_heat"
    limit_type: RiskLimitType = RiskLimitType.PORTFOLIO_HEAT

    @property
    def parameters(self) -> dict[str, object]:
        return {"max_portfolio_heat_pct": self.config.max_portfolio_heat_pct}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.config.max_portfolio_heat_pct))
        if portfolio.equity <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="portfolio equity is not positive; heat is undefined",
                limit=float(limit),
            )
        open_risk = portfolio.total_open_risk()
        combined = open_risk + proposal.risk_amount
        headroom = limit * portfolio.equity - open_risk
        allowed = headroom / proposal.risk_per_share if proposal.risk_per_share > 0 else Decimal(0)
        return verdict_for_headroom(
            rule_name=self.name,
            limit_type=self.limit_type,
            allowed=max(allowed, Decimal(0)),
            proposal=proposal,
            reason=(
                f"portfolio heat would be {combined / portfolio.equity:.2%}, "
                f"above the {limit:.2%} limit"
            ),
            measured=float(combined / portfolio.equity),
            limit=float(limit),
        )


@dataclass(frozen=True, slots=True)
class MaxPositionsRule:
    """A cap on concurrent positions, which is a cap on attention.

    **Adding to a name already held does not consume a slot.** Pyramiding
    changes the size of an existing bet, not the number of them, and counting
    it as a new position would make the limit block exactly the adds it was
    never meant to govern.
    """

    config: RiskConfig
    name: str = "max_positions"
    limit_type: RiskLimitType = RiskLimitType.MAX_POSITIONS

    @property
    def parameters(self) -> dict[str, object]:
        return {"max_positions": self.config.max_positions}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        count = portfolio.position_count
        if portfolio.holds(proposal.instrument_id):
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.ALLOW,
                reason="already held; an add consumes no slot",
                measured=float(count),
                limit=float(self.config.max_positions),
            )
        if count >= self.config.max_positions:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason=f"{count} positions open, at the {self.config.max_positions} limit",
                measured=float(count),
                limit=float(self.config.max_positions),
            )
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="within limit",
            measured=float(count),
            limit=float(self.config.max_positions),
        )


@dataclass(frozen=True, slots=True)
class PositionSizeRule:
    """One name's notional against ``max_position_pct_of_equity``.

    Evaluated against the position that would exist *after* the trade, not
    against the trade alone: three permissible adds to the same name make one
    impermissible position, and a rule that looked only at the increment would
    let them through one at a time.
    """

    max_position_pct_of_equity: float
    name: str = "position_size"
    limit_type: RiskLimitType = RiskLimitType.POSITION_SIZE

    @property
    def parameters(self) -> dict[str, object]:
        return {"max_position_pct_of_equity": self.max_position_pct_of_equity}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.max_position_pct_of_equity))
        if portfolio.equity <= 0 or proposal.entry_price <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="equity or entry price is not positive; exposure is undefined",
                limit=float(limit),
            )
        held = sum(
            (
                p.market_value(portfolio.last_prices.get(p.instrument_id, p.average_entry_price))
                for p in portfolio.open_positions
                if p.instrument_id == proposal.instrument_id
            ),
            Decimal(0),
        )
        budget = limit * portfolio.equity
        combined = held + proposal.notional
        allowed = max(budget - held, Decimal(0)) / proposal.entry_price
        return verdict_for_headroom(
            rule_name=self.name,
            limit_type=self.limit_type,
            allowed=allowed,
            proposal=proposal,
            reason=(
                f"position would be {combined / portfolio.equity:.2%} of equity, "
                f"above the {limit:.2%} limit"
            ),
            measured=float(combined / portfolio.equity),
            limit=float(limit),
        )


@dataclass(frozen=True, slots=True)
class GrossExposureRule:
    """Total invested capital against ``max_gross_exposure_pct``.

    Separate from heat because they measure different failures. Heat is what
    stops protect against; gross exposure is what they do not — a gap through
    every stop at once is bounded by how much is invested, not by where the
    stops sit.
    """

    config: RiskConfig
    name: str = "gross_exposure"
    limit_type: RiskLimitType = RiskLimitType.GROSS_EXPOSURE

    @property
    def parameters(self) -> dict[str, object]:
        return {"max_gross_exposure_pct": self.config.max_gross_exposure_pct}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.config.max_gross_exposure_pct))
        if portfolio.equity <= 0 or proposal.entry_price <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="equity or entry price is not positive; exposure is undefined",
                limit=float(limit),
            )
        invested = portfolio.invested_fraction() * portfolio.equity
        combined = invested + proposal.notional
        allowed = max(limit * portfolio.equity - invested, Decimal(0)) / proposal.entry_price
        return verdict_for_headroom(
            rule_name=self.name,
            limit_type=self.limit_type,
            allowed=allowed,
            proposal=proposal,
            reason=(
                f"gross exposure would be {combined / portfolio.equity:.2%}, "
                f"above the {limit:.2%} limit"
            ),
            measured=float(combined / portfolio.equity),
            limit=float(limit),
        )


@dataclass(frozen=True, slots=True)
class PyramidRule:
    """Governs adding to a position that already exists.

    ``SizingConfig`` states the policy this enforces: *pyramiding adds to
    winners only, and only with the stop already raised; adding to a loser is
    averaging down wearing a technical name.* All three conditions are checked,
    and each has its own rejection reason so the log distinguishes "not enough
    gain yet" from "stop never moved".

    **A first entry is not a pyramid** and passes untouched — this rule governs
    adds, and a rule that rejected new positions because the account was flat
    would be the max-positions limit written twice.

    **A missing last price is a rejection.** Whether the position is a winner
    cannot be established without one, and the whole rule turns on that fact.
    Falling back to the entry price would score every position as flat, which
    reads as "not enough gain" and quietly disables pyramiding altogether while
    appearing to enforce it.
    """

    config: SizingConfig
    name: str = "pyramid_entries"
    limit_type: RiskLimitType = RiskLimitType.PYRAMID_ENTRIES

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "allow_pyramiding": self.config.allow_pyramiding,
            "max_pyramid_entries": self.config.max_pyramid_entries,
            "pyramid_min_gain_pct": self.config.pyramid_min_gain_pct,
        }

    def _allow(self, reason: str) -> RiskAssessment:
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason=reason,
        )

    def _reject(self, reason: str, *, measured: float | None = None) -> RiskAssessment:
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.REJECT,
            reason=reason,
            measured=measured,
            limit=float(self.config.max_pyramid_entries),
        )

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        held = [p for p in portfolio.open_positions if p.instrument_id == proposal.instrument_id]
        if not held:
            return self._allow("first entry; not a pyramid")
        if not self.config.allow_pyramiding:
            return self._reject("pyramiding is disabled")

        entries = sum(p.pyramid_entries for p in held)
        if entries >= self.config.max_pyramid_entries:
            return self._reject(
                f"{entries} entries already made, at the {self.config.max_pyramid_entries} limit",
                measured=float(entries),
            )

        last_price = portfolio.last_prices.get(proposal.instrument_id)
        if last_price is None:
            return self._reject(
                f"no last price for instrument {proposal.instrument_id}; whether the "
                "position is a winner is UNRESOLVED"
            )
        quantity = sum((p.quantity for p in held), Decimal(0))
        if quantity <= 0:
            return self._reject("the held position has no quantity to add to")
        cost = sum((p.quantity * p.average_entry_price for p in held), Decimal(0))
        average_entry = cost / quantity
        gain = (last_price - average_entry) / average_entry
        if gain < Decimal(str(self.config.pyramid_min_gain_pct)):
            return self._reject(
                f"position is {gain:.2%} from its average entry, below the "
                f"{self.config.pyramid_min_gain_pct:.2%} required to add; adding to a "
                "loser is averaging down wearing a technical name",
                measured=float(gain),
            )

        unraised = [
            p
            for p in held
            if p.stop_price is None
            or p.initial_stop_price is None
            or p.stop_price <= p.initial_stop_price
        ]
        if unraised:
            return self._reject(
                "the stop has not been raised above where the position opened; the add "
                "would increase risk rather than redeploy it"
            )
        return self._allow(f"{gain:.2%} gain with the stop raised; add permitted")
