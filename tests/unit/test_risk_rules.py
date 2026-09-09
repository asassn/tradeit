"""Tests for the snapshot-only risk rules and the aggregating engine.

The rules are tested against proposals built by hand rather than by the sizer,
which is the point: a rule that only ever sees sized proposals is a rule whose
limit is really enforced by the sizer.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import (
    PortfolioState,
    PositionState,
    PositionStatus,
    Side,
    SizingDecision,
)
from tradeit.risk.base import RiskAssessment, RiskEngine, RiskRule
from tradeit.risk.engine import SIZER_RULE_NAME, MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.strategy.config import RiskConfig, SizingConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _proposal(
    *,
    instrument_id: int = 7,
    quantity: str = "100",
    entry: str = "100",
    stop: str = "95",
    rejected: bool = False,
    rejection_reason: str | None = None,
) -> SizingDecision:
    entry_price = Decimal(entry)
    stop_price = Decimal(stop)
    per_share = entry_price - stop_price
    qty = Decimal(quantity)
    return SizingDecision(
        instrument_id=instrument_id,
        quantity=qty,
        entry_price=entry_price,
        stop_price=stop_price,
        risk_per_share=per_share,
        risk_amount=qty * per_share,
        risk_fraction=Decimal(0),
        notional=qty * entry_price,
        binding_constraint="risk_per_trade",
        rejected=rejected,
        rejection_reason=rejection_reason,
    )


def _held(
    instrument_id: int,
    *,
    quantity: str = "100",
    entry: str = "50",
    stop: str = "45",
) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        opened_on=dt.date(2024, 5, 1),
    )


def _portfolio(
    *,
    equity: str = "100000",
    positions: tuple[PositionState, ...] = (),
    last_prices: dict[int, Decimal] | None = None,
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(equity),
        equity=Decimal(equity),
        positions=positions,
        last_prices=last_prices or {},
    )


class TestPortfolioHeatRule:
    def test_allows_a_trade_inside_the_budget(self) -> None:
        rule = PortfolioHeatRule(config=RiskConfig())
        assessment = rule.evaluate(_proposal(), _portfolio())
        assert assessment.decision is RiskDecision.ALLOW
        assert assessment.limit_type is RiskLimitType.PORTFOLIO_HEAT

    def test_reduces_to_the_remaining_budget(self) -> None:
        # $5,500 of the $6,000 budget is already open, leaving $500 -- 100
        # shares at $5 of risk each, against a proposal of 200.
        held = _held(9, quantity="1000", entry="50", stop="44.50")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(50)})
        assessment = PortfolioHeatRule(config=RiskConfig()).evaluate(
            _proposal(quantity="200"), portfolio
        )
        assert assessment.decision is RiskDecision.ALLOW_REDUCED
        assert assessment.max_allowed_quantity == Decimal(100)

    def test_rejects_when_the_budget_is_spent(self) -> None:
        held = _held(9, quantity="1000", entry="50", stop="44")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(50)})
        assessment = PortfolioHeatRule(config=RiskConfig()).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.REJECT
        assert assessment.measured is not None and assessment.measured > 0.06

    def test_a_rejection_carries_the_numbers_that_produced_it(self) -> None:
        held = _held(9, quantity="1000", entry="50", stop="44")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(50)})
        assessment = PortfolioHeatRule(config=RiskConfig()).evaluate(_proposal(), portfolio)
        assert "6.50%" in assessment.reason
        assert "6.00% limit" in assessment.reason

    def test_headroom_of_less_than_one_share_is_a_rejection(self) -> None:
        """Not an ALLOW_REDUCED the caller cannot execute."""
        held = _held(9, quantity="1000", entry="50", stop="44.001")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(50)})
        assessment = PortfolioHeatRule(config=RiskConfig()).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.REJECT

    def test_zero_equity_is_a_rejection_not_a_division(self) -> None:
        assessment = PortfolioHeatRule(config=RiskConfig()).evaluate(
            _proposal(), _portfolio(equity="0")
        )
        assert assessment.decision is RiskDecision.REJECT


class TestMaxPositionsRule:
    def test_rejects_a_new_name_at_the_limit(self) -> None:
        positions = tuple(_held(i) for i in range(100, 112))
        portfolio = _portfolio(positions=positions)
        assert portfolio.position_count == 12
        assessment = MaxPositionsRule(config=RiskConfig()).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.REJECT

    def test_an_add_to_a_held_name_consumes_no_slot(self) -> None:
        positions = tuple(_held(i) for i in range(100, 112))
        portfolio = _portfolio(positions=positions)
        assessment = MaxPositionsRule(config=RiskConfig()).evaluate(
            _proposal(instrument_id=100), portfolio
        )
        assert assessment.decision is RiskDecision.ALLOW

    def test_closed_positions_do_not_occupy_slots(self) -> None:
        closed = tuple(
            PositionState(
                position_id=i,
                portfolio_id=1,
                instrument_id=i,
                side=Side.LONG,
                status=PositionStatus.CLOSED,
                quantity=Decimal(0),
                average_entry_price=Decimal(50),
                stop_price=None,
                opened_on=dt.date(2024, 1, 2),
                closed_on=dt.date(2024, 3, 1),
            )
            for i in range(100, 120)
        )
        assessment = MaxPositionsRule(config=RiskConfig()).evaluate(
            _proposal(), _portfolio(positions=closed)
        )
        assert assessment.decision is RiskDecision.ALLOW


class TestPositionSizeRule:
    def test_measures_the_position_that_would_exist_not_the_increment(self) -> None:
        """Three permissible adds make one impermissible position."""
        held = _held(7, quantity="180", entry="100", stop="95")
        portfolio = _portfolio(positions=(held,), last_prices={7: Decimal(100)})
        rule = PositionSizeRule(max_position_pct_of_equity=0.20)
        assessment = rule.evaluate(_proposal(instrument_id=7, quantity="100"), portfolio)
        assert assessment.decision is RiskDecision.ALLOW_REDUCED
        assert assessment.max_allowed_quantity == Decimal(20)

    def test_allows_a_first_entry_inside_the_cap(self) -> None:
        rule = PositionSizeRule(max_position_pct_of_equity=0.20)
        assessment = rule.evaluate(_proposal(quantity="100"), _portfolio())
        assert assessment.decision is RiskDecision.ALLOW

    def test_a_full_position_admits_no_addition(self) -> None:
        held = _held(7, quantity="200", entry="100", stop="95")
        portfolio = _portfolio(positions=(held,), last_prices={7: Decimal(100)})
        rule = PositionSizeRule(max_position_pct_of_equity=0.20)
        assessment = rule.evaluate(_proposal(instrument_id=7, quantity="1"), portfolio)
        assert assessment.decision is RiskDecision.REJECT


class TestGrossExposureRule:
    def test_reduces_to_the_uninvested_remainder(self) -> None:
        held = _held(9, quantity="950", entry="100", stop="95")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(100)})
        # $95,000 invested of a $100,000 limit; $5,000 remains, or 50 shares.
        assessment = GrossExposureRule(config=RiskConfig()).evaluate(
            _proposal(quantity="100"), portfolio
        )
        assert assessment.decision is RiskDecision.ALLOW_REDUCED
        assert assessment.max_allowed_quantity == Decimal(50)

    def test_uses_the_last_price_not_the_entry_price(self) -> None:
        """A position that has doubled occupies twice the exposure it opened with."""
        held = _held(9, quantity="500", entry="100", stop="95")
        portfolio = _portfolio(positions=(held,), last_prices={9: Decimal(200)})
        assessment = GrossExposureRule(config=RiskConfig()).evaluate(
            _proposal(quantity="100"), portfolio
        )
        assert assessment.decision is RiskDecision.REJECT


class _AlwaysAllows:
    name = "always_allows"
    limit_type = RiskLimitType.DAILY_LOSS

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="fine",
        )


class TestMostRestrictiveEngine:
    def _engine(self, *rules: RiskRule) -> MostRestrictiveEngine:
        return MostRestrictiveEngine(rule_set=rules, sizing=SizingConfig())

    def test_satisfies_the_risk_engine_protocol(self) -> None:
        engine: RiskEngine = self._engine(PortfolioHeatRule(config=RiskConfig()))
        assert len(engine.rules) == 1

    def test_allows_when_every_rule_allows(self) -> None:
        engine = self._engine(
            PortfolioHeatRule(config=RiskConfig()),
            MaxPositionsRule(config=RiskConfig()),
            GrossExposureRule(config=RiskConfig()),
        )
        verdict = engine.evaluate(_proposal(), _portfolio())
        assert verdict.decision is RiskDecision.ALLOW
        assert verdict.final_quantity == Decimal(100)
        assert verdict.binding_rule is None
        assert verdict.explain() == "within all limits"

    def test_one_rejection_outweighs_every_allowance(self) -> None:
        positions = tuple(_held(i) for i in range(100, 112))
        engine = self._engine(
            _AlwaysAllows(),
            MaxPositionsRule(config=RiskConfig()),
        )
        verdict = engine.evaluate(_proposal(), _portfolio(positions=positions))
        assert verdict.decision is RiskDecision.REJECT
        assert verdict.final_quantity == Decimal(0)
        assert verdict.binding_rule == "max_positions"

    def test_takes_the_tightest_of_several_reductions(self) -> None:
        held = _held(7, quantity="180", entry="100", stop="95")
        portfolio = _portfolio(positions=(held,), last_prices={7: Decimal(100)})
        engine = self._engine(
            PortfolioHeatRule(config=RiskConfig()),
            PositionSizeRule(max_position_pct_of_equity=0.20),
        )
        verdict = engine.evaluate(_proposal(instrument_id=7, quantity="100"), portfolio)
        assert verdict.decision is RiskDecision.ALLOW_REDUCED
        # Heat allows 120 shares; the position cap allows 20.
        assert verdict.final_quantity == Decimal(20)
        assert verdict.binding_rule == "position_size"

    def test_every_rule_is_evaluated_even_after_a_rejection(self) -> None:
        """Recording one breach where there were two hides a calibration problem."""
        positions = tuple(_held(i, quantity="1000", entry="50", stop="44") for i in range(100, 112))
        last_prices = {i: Decimal(50) for i in range(100, 112)}
        engine = self._engine(
            MaxPositionsRule(config=RiskConfig()),
            PortfolioHeatRule(config=RiskConfig()),
            GrossExposureRule(config=RiskConfig()),
        )
        verdict = engine.evaluate(
            _proposal(), _portfolio(positions=positions, last_prices=last_prices)
        )
        assert len(verdict.assessments) == 3
        assert sorted(verdict.blocking_rules) == [
            "gross_exposure",
            "max_positions",
            "portfolio_heat",
        ]

    def test_a_sizer_refusal_is_reported_not_re_adjudicated(self) -> None:
        """A proposal of zero shares passes every limit trivially."""
        engine = self._engine(PortfolioHeatRule(config=RiskConfig()))
        verdict = engine.evaluate(
            _proposal(quantity="0", rejected=True, rejection_reason="no stop"),
            _portfolio(),
        )
        assert verdict.decision is RiskDecision.REJECT
        assert verdict.binding_rule == SIZER_RULE_NAME
        assert "no stop" in verdict.explain()

    def test_an_empty_rule_set_allows_and_says_nothing(self) -> None:
        """No rules means no limits -- which is a fact the caller should see."""
        verdict = self._engine().evaluate(_proposal(), _portfolio())
        assert verdict.decision is RiskDecision.ALLOW
        assert verdict.assessments == ()

    def test_duplicate_rule_names_are_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="duplicate risk rule names: portfolio_heat"):
            self._engine(
                PortfolioHeatRule(config=RiskConfig()),
                PortfolioHeatRule(config=RiskConfig()),
            )

    def test_a_reduction_is_floored_for_a_whole_share_portfolio(self) -> None:
        engine = MostRestrictiveEngine(
            rule_set=(PortfolioHeatRule(config=RiskConfig()),),
            sizing=SizingConfig(allow_fractional_shares=False),
        )
        verdict = engine.evaluate(_proposal(), _portfolio())
        assert verdict.final_quantity == verdict.final_quantity.to_integral_value()
