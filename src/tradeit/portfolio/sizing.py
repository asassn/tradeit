"""Risk-based position sizing: the first thing Phase 8 needs and the last one
anybody audits.

**Size is expressed in risk, not dollars.** The number of shares is whatever
puts a fixed *fraction of equity* between entry and stop. That is what makes
returns compound in percentage terms rather than dollar profits accumulate: a
position risking 0.5% of equity risks 0.5% whether the account holds $50,000 or
$500,000. :class:`~tradeit.portfolio.base.PositionSizer` states the property
this implies — **monotone in equity** — and it is asserted as a property test
rather than trusted.

**A position without a stop has undefined risk and is refused.** Not sized
small, not sized on a default stop: refused. ``PositionState`` already says a
position without a stop cannot be sized, cannot be aggregated into portfolio
heat and is therefore not a position this system will hold. A sizer that
invented a stop to keep going would defeat that.

Five caps, and the one that binds is named
--------------------------------------------

The size is the **smallest** of five, and ``binding_constraint`` records which.
That field is the point of this module. *"The sizer said 340 shares"* is not an
answer after a loss; *"340 shares, bound by portfolio heat"* tells you the
account was already carrying its limit and the next trade would have been
refused anyway.

* **risk** — the per-trade budget, ``risk_per_trade_pct`` of equity
* **position** — ``max_position_pct_of_equity``, so no single name dominates
  however tight its stop is. A very tight stop otherwise buys an enormous
  position at the same nominal risk, and the risk that is not in the stop is
  the gap through it
* **heat** — the portfolio's *remaining* risk budget, ``max_portfolio_heat_pct``
  less what is already open. Per-trade risk alone cannot see the other eleven
  positions
* **cash** — what is actually available to spend
* **liquidity** — a share of average dollar volume, because a position that is
  a large fraction of a day's trading moves the price it is trying to get

**Participation has no default and must be supplied.** It is not in
``SizingConfig``, and a default here would be a strategy parameter hiding in a
signature, which ADR-0008 exists to prevent. The caller states it or there is
no sizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.strategy.base import OpportunityScore
from tradeit.strategy.config import RiskConfig, SizingConfig

__all__ = ["BindingConstraint", "RiskBasedSizer"]


class BindingConstraint:
    """The names ``SizingDecision.binding_constraint`` can take.

    Strings rather than an enum because the field is typed ``str`` on a
    dataclass that predates this module, and widening its type would be a
    change to a stored shape for a cosmetic gain.
    """

    RISK = "risk_per_trade"
    POSITION = "max_position_fraction"
    HEAT = "portfolio_heat_budget"
    CASH = "available_cash"
    LIQUIDITY = "liquidity_participation"


def _floor(quantity: Decimal, *, fractional: bool) -> Decimal:
    if fractional:
        return quantity
    return quantity.to_integral_value(rounding=ROUND_DOWN)


@dataclass(frozen=True, slots=True)
class RiskBasedSizer:
    """Sizes by risk, capped by position, heat, cash and liquidity.

    ``max_participation`` is the share of average dollar volume a single
    position may take. Required, per the module docstring.
    """

    sizing: SizingConfig
    risk: RiskConfig
    max_participation: Decimal
    name: str = "risk_based"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "risk_per_trade_pct": self.sizing.risk_per_trade_pct,
            "max_position_pct_of_equity": self.sizing.max_position_pct_of_equity,
            "min_position_notional": self.sizing.min_position_notional,
            "allow_fractional_shares": self.sizing.allow_fractional_shares,
            "max_portfolio_heat_pct": self.risk.max_portfolio_heat_pct,
            "max_participation": str(self.max_participation),
        }

    def size(
        self,
        candidate: OpportunityScore,
        entry_price: Decimal,
        stop_price: Decimal,
        portfolio: PortfolioState,
        average_dollar_volume: Decimal | None = None,
    ) -> SizingDecision:
        """Shares to buy, the arithmetic that produced them, and what bound it."""
        instrument_id = candidate.instrument_id
        risk_per_share = entry_price - stop_price

        def refuse(reason: str, constraint: str) -> SizingDecision:
            return SizingDecision(
                instrument_id=instrument_id,
                quantity=Decimal(0),
                entry_price=entry_price,
                stop_price=stop_price,
                risk_per_share=risk_per_share,
                risk_amount=Decimal(0),
                risk_fraction=Decimal(0),
                notional=Decimal(0),
                binding_constraint=constraint,
                rejected=True,
                rejection_reason=reason,
            )

        if entry_price <= 0:
            return refuse(f"entry price {entry_price} is not positive", BindingConstraint.RISK)
        if risk_per_share <= 0:
            # A stop at or above entry is not a stop. Sizing it would divide by
            # zero or produce a short position from a long candidate.
            return refuse(
                f"stop {stop_price} is not below entry {entry_price}; risk per share "
                "would be zero or negative and the position would have undefined risk",
                BindingConstraint.RISK,
            )
        if portfolio.equity <= 0:
            return refuse("portfolio equity is not positive", BindingConstraint.RISK)

        equity = portfolio.equity

        # Heat is the portfolio's *remaining* risk budget, not this trade's. A
        # per-trade limit cannot see the other open positions.
        remaining_heat = (
            equity * Decimal(str(self.risk.max_portfolio_heat_pct)) - portfolio.total_open_risk()
        )

        # Insertion order is the order the module docstring lists, because
        # ``min`` reports the first key on a tie and a tie should be attributed
        # to the more fundamental limit.
        caps: dict[str, Decimal] = {
            BindingConstraint.RISK: (
                equity * Decimal(str(self.sizing.risk_per_trade_pct)) / risk_per_share
            ),
            BindingConstraint.POSITION: (
                equity * Decimal(str(self.sizing.max_position_pct_of_equity)) / entry_price
            ),
            BindingConstraint.HEAT: max(remaining_heat, Decimal(0)) / risk_per_share,
            BindingConstraint.CASH: max(portfolio.cash, Decimal(0)) / entry_price,
        }

        if average_dollar_volume is not None and average_dollar_volume > 0:
            caps[BindingConstraint.LIQUIDITY] = (
                average_dollar_volume * self.max_participation / entry_price
            )

        binding = min(caps, key=lambda name: caps[name])
        quantity = _floor(caps[binding], fractional=self.sizing.allow_fractional_shares)

        if quantity <= 0:
            return refuse(
                f"{binding} allows {caps[binding]:.4f} shares, which rounds to none",
                binding,
            )

        notional = quantity * entry_price
        if notional < Decimal(str(self.sizing.min_position_notional)):
            # Refused rather than rounded up: a position below the minimum is
            # one whose costs outweigh it, and rounding up would breach the cap
            # that produced it.
            return refuse(
                f"notional {notional} is below the {self.sizing.min_position_notional} minimum",
                binding,
            )

        risk_amount = quantity * risk_per_share
        return SizingDecision(
            instrument_id=instrument_id,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount,
            risk_fraction=risk_amount / equity,
            notional=notional,
            binding_constraint=binding,
        )
