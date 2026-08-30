"""Interfaces for portfolio state, sizing, and capital allocation.

This is where the platform's central distinction becomes code: an opportunity
score says a stock is attractive; portfolio construction decides whether it is
an attractive *use of this portfolio's capital right now*. The two answers
diverge whenever the portfolio already holds something correlated, whenever
open risk is near its limit, and whenever a better use of the same capital
exists.

Sizing is expressed in **risk**, not dollars. The size of a position is whatever
number of shares puts a fixed fraction of equity at risk between entry and stop.
This is what makes percentage returns compound rather than dollar profits
accumulate, which the brief requires: a position that risks 0.5% of equity risks
0.5% whether equity is $50,000 or $500,000.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.core.enums import PositionStatus, Side
from tradeit.strategy.base import OpportunityScore


@dataclass(frozen=True, slots=True)
class PositionState:
    """An open or closed position as the platform understands it.

    ``stop_price`` is required while a position is open. A position without a
    stop has undefined risk, cannot be sized, cannot be aggregated into
    portfolio heat, and is therefore not a position this system will hold.
    """

    position_id: int
    portfolio_id: int
    instrument_id: int
    side: Side
    status: PositionStatus
    quantity: Decimal
    average_entry_price: Decimal
    stop_price: Decimal | None
    opened_on: dt.date
    closed_on: dt.date | None = None
    realised_pnl: Decimal = Decimal(0)

    def open_risk(self, last_price: Decimal) -> Decimal:
        """Currency at risk between the current price and the stop.

        Zero once the stop is at or beyond the current price — a position whose
        stop has moved above entry has no downside risk under the stop
        assumption, and counting it toward portfolio heat would wrongly block
        new entries. The assumption itself is optimistic: gaps do not respect
        stops, which is why gap risk is modelled separately in backtesting.
        """
        if self.stop_price is None or self.status is not PositionStatus.OPEN:
            return Decimal(0)
        per_share = (
            last_price - self.stop_price if self.side is Side.LONG else self.stop_price - last_price
        )
        return max(per_share, Decimal(0)) * self.quantity

    def market_value(self, last_price: Decimal) -> Decimal:
        return last_price * self.quantity


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """A complete, self-consistent snapshot of the portfolio at one instant.

    Passed to sizers and risk rules as a value rather than queried from the
    database by each of them. Two reasons: every rule sees the *same* state
    rather than a slightly different one depending on when it ran, and a
    backtest can construct a state directly without a database at all.
    """

    portfolio_id: int
    as_of: dt.datetime
    cash: Decimal
    equity: Decimal
    positions: tuple[PositionState, ...]
    last_prices: dict[int, Decimal] = field(default_factory=dict)

    @property
    def open_positions(self) -> tuple[PositionState, ...]:
        return tuple(p for p in self.positions if p.status is PositionStatus.OPEN)

    @property
    def position_count(self) -> int:
        return len(self.open_positions)

    def total_open_risk(self) -> Decimal:
        """Sum of per-position risk. The numerator of portfolio heat."""
        return sum(
            (
                p.open_risk(self.last_prices.get(p.instrument_id, p.average_entry_price))
                for p in self.open_positions
            ),
            Decimal(0),
        )

    def heat(self) -> Decimal:
        """Open risk as a fraction of equity.

        The single most important portfolio-level number: it answers "if every
        stop were hit tomorrow, how much of the account is gone?"
        """
        if self.equity <= 0:
            return Decimal(0)
        return self.total_open_risk() / self.equity

    def holds(self, instrument_id: int) -> bool:
        return any(p.instrument_id == instrument_id for p in self.open_positions)

    def invested_fraction(self) -> Decimal:
        if self.equity <= 0:
            return Decimal(0)
        invested = sum(
            (
                p.market_value(self.last_prices.get(p.instrument_id, p.average_entry_price))
                for p in self.open_positions
            ),
            Decimal(0),
        )
        return invested / self.equity


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """The result of sizing one candidate, with its arithmetic exposed.

    Every intermediate is kept because position size is the number most worth
    auditing after a loss, and "the sizer said 340 shares" is not an answer.
    ``binding_constraint`` names which limit actually determined the size, which
    is what tells you whether the portfolio is risk-constrained,
    liquidity-constrained, or cash-constrained.
    """

    instrument_id: int
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    risk_per_share: Decimal
    risk_amount: Decimal
    risk_fraction: Decimal
    notional: Decimal
    binding_constraint: str
    rejected: bool = False
    rejection_reason: str | None = None

    @property
    def is_tradable(self) -> bool:
        return not self.rejected and self.quantity > 0


@runtime_checkable
class PositionSizer(Protocol):
    """Turns a candidate and a portfolio state into a share count.

    Implementations must be **monotone in equity**: doubling equity with
    everything else held constant doubles the size. That is what makes returns
    compound in percentage terms, and it is asserted as a property test rather
    than trusted.
    """

    name: str

    @property
    def parameters(self) -> dict[str, object]: ...

    def size(
        self,
        candidate: OpportunityScore,
        entry_price: Decimal,
        stop_price: Decimal,
        portfolio: PortfolioState,
        average_dollar_volume: Decimal | None = None,
    ) -> SizingDecision: ...


@dataclass(frozen=True, slots=True)
class AllocationCandidate:
    """A candidate paired with its proposed size, ready to be ranked."""

    score: OpportunityScore
    sizing: SizingDecision
    correlation_penalty: float = 0.0
    sector: str | None = None

    @property
    def adjusted_score(self) -> float:
        return self.score.total * (1.0 - self.correlation_penalty)


@runtime_checkable
class AllocationRanker(Protocol):
    """Chooses which candidates get the available capital.

    This is the stage that makes the system a portfolio manager. It sees all
    candidates at once, together with the current portfolio, and must consider
    what the set looks like *as a group*: five names from one sector are one bet
    wearing five tickers, and ranking them independently cannot see that.

    Returns candidates in allocation order — the caller takes them until capital
    or risk budget is exhausted.
    """

    name: str

    @property
    def parameters(self) -> dict[str, object]: ...

    def rank(
        self,
        candidates: Sequence[AllocationCandidate],
        portfolio: PortfolioState,
        correlation: dict[tuple[int, int], float] | None = None,
    ) -> list[AllocationCandidate]: ...
