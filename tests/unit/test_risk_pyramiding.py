"""Tests for the pyramid rule.

Each of the three conditions in ``SizingConfig``'s stated policy -- winners
only, stop already raised, entry count capped -- gets a test that fails if that
condition alone is dropped.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import (
    PortfolioState,
    PositionState,
    PositionStatus,
    Side,
    SizingDecision,
)
from tradeit.risk.rules import PyramidRule
from tradeit.strategy.config import SizingConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _proposal(instrument_id: int = 7) -> SizingDecision:
    return SizingDecision(
        instrument_id=instrument_id,
        quantity=Decimal(50),
        entry_price=Decimal(110),
        stop_price=Decimal(104),
        risk_per_share=Decimal(6),
        risk_amount=Decimal(300),
        risk_fraction=Decimal("0.003"),
        notional=Decimal(5500),
        binding_constraint="risk_per_trade",
    )


def _position(
    *,
    entries: int = 1,
    stop: Decimal | None = Decimal(104),
    initial_stop: Decimal | None = Decimal(94),
    quantity: Decimal = Decimal(100),
) -> PositionState:
    return PositionState(
        position_id=1,
        portfolio_id=1,
        instrument_id=7,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=quantity,
        average_entry_price=Decimal(100),
        stop_price=stop,
        opened_on=dt.date(2024, 5, 1),
        initial_stop_price=initial_stop,
        pyramid_entries=entries,
    )


def _portfolio(
    *positions: PositionState,
    last: str | None = "110",
) -> PortfolioState:
    prices = {} if last is None else {7: Decimal(last)}
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(50000),
        equity=Decimal(100000),
        positions=positions,
        last_prices=prices,
    )


RULE = PyramidRule(config=SizingConfig())


def test_a_first_entry_is_not_a_pyramid() -> None:
    assessment = RULE.evaluate(_proposal(), _portfolio())
    assert assessment.decision is RiskDecision.ALLOW
    assert assessment.limit_type is RiskLimitType.PYRAMID_ENTRIES


def test_a_winner_with_a_raised_stop_may_be_added_to() -> None:
    assessment = RULE.evaluate(_proposal(), _portfolio(_position()))
    assert assessment.decision is RiskDecision.ALLOW
    assert "10.00% gain" in assessment.reason


def test_adding_to_a_loser_is_refused() -> None:
    assessment = RULE.evaluate(_proposal(), _portfolio(_position(), last="97"))
    assert assessment.decision is RiskDecision.REJECT
    assert "averaging down" in assessment.reason


def test_a_gain_below_the_threshold_is_refused() -> None:
    assessment = RULE.evaluate(_proposal(), _portfolio(_position(), last="102"))
    assert assessment.decision is RiskDecision.REJECT
    assert assessment.measured == 0.02


def test_an_unraised_stop_is_refused_however_large_the_gain() -> None:
    position = _position(stop=Decimal(94), initial_stop=Decimal(94))
    assessment = RULE.evaluate(_proposal(), _portfolio(position, last="150"))
    assert assessment.decision is RiskDecision.REJECT
    assert "has not been raised" in assessment.reason


def test_the_entry_cap_counts_the_initial_entry() -> None:
    """max_pyramid_entries of 2 permits one add, not two."""
    assessment = RULE.evaluate(_proposal(), _portfolio(_position(entries=2)))
    assert assessment.decision is RiskDecision.REJECT
    assert assessment.measured == 2.0


def test_a_missing_last_price_is_refused_not_treated_as_flat() -> None:
    """Falling back to entry would disable pyramiding while appearing to enforce it."""
    assessment = RULE.evaluate(_proposal(), _portfolio(_position(), last=None))
    assert assessment.decision is RiskDecision.REJECT
    assert "UNRESOLVED" in assessment.reason


def test_disabling_pyramiding_refuses_every_add() -> None:
    rule = PyramidRule(config=SizingConfig(allow_pyramiding=False))
    assessment = rule.evaluate(_proposal(), _portfolio(_position(), last="200"))
    assert assessment.decision is RiskDecision.REJECT
    assert "disabled" in assessment.reason


def test_disabling_pyramiding_does_not_block_a_first_entry() -> None:
    rule = PyramidRule(config=SizingConfig(allow_pyramiding=False))
    assert rule.evaluate(_proposal(), _portfolio()).decision is RiskDecision.ALLOW


def test_the_gain_is_measured_against_the_weighted_average_entry() -> None:
    """Not against the first entry, which a second add would leave behind."""
    cheap = _position(quantity=Decimal(100))
    dear = PositionState(
        position_id=2,
        portfolio_id=1,
        instrument_id=7,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(300),
        average_entry_price=Decimal(108),
        stop_price=Decimal(104),
        opened_on=dt.date(2024, 5, 20),
        initial_stop_price=Decimal(94),
        pyramid_entries=0,
    )
    # Weighted average entry is 106, so 110 is a 3.77% gain -- above the 3%
    # threshold. Measured against the first entry of 100 it would be 10%.
    assessment = RULE.evaluate(_proposal(), _portfolio(cheap, dear))
    assert assessment.decision is RiskDecision.ALLOW
    assert "3.77% gain" in assessment.reason
