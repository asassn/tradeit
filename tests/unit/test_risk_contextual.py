"""Tests for the risk rules that take outside evidence at construction.

Each rule's fail-closed behaviour gets a test of its own, because the
permissive alternative is the one that looks fine in every other test: a limit
fed no evidence reports comfortable numbers and constrains nothing.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tradeit.core.enums import RiskDecision
from tradeit.portfolio.base import (
    PortfolioState,
    PositionState,
    PositionStatus,
    Side,
    SizingDecision,
)
from tradeit.risk.contextual import (
    UNCLASSIFIED,
    CorrelationRule,
    DailyLossRule,
    DrawdownHaltRule,
    MatrixCorrelationSource,
    SectorExposureRule,
)
from tradeit.strategy.config import RiskConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _proposal(*, instrument_id: int = 7, quantity: str = "100") -> SizingDecision:
    qty = Decimal(quantity)
    return SizingDecision(
        instrument_id=instrument_id,
        quantity=qty,
        entry_price=Decimal(100),
        stop_price=Decimal(95),
        risk_per_share=Decimal(5),
        risk_amount=qty * 5,
        risk_fraction=Decimal(0),
        notional=qty * 100,
        binding_constraint="risk_per_trade",
    )


def _held(instrument_id: int, *, quantity: str = "100") -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(100),
        stop_price=Decimal(95),
        opened_on=dt.date(2024, 5, 1),
    )


def _portfolio(
    *,
    equity: str = "100000",
    positions: tuple[PositionState, ...] = (),
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(equity),
        equity=Decimal(equity),
        positions=positions,
        last_prices={p.instrument_id: Decimal(100) for p in positions},
    )


class TestSectorExposureRule:
    def test_reduces_to_the_sector_remainder(self) -> None:
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        rule = SectorExposureRule(
            config=RiskConfig(),
            sectors={7: "Financials", 8: "Financials", 9: "Financials"},
        )
        # $20,000 of a $30,000 sector budget is held; $10,000, or 100 shares, remains.
        assessment = rule.evaluate(_proposal(quantity="200"), portfolio)
        assert assessment.decision is RiskDecision.ALLOW_REDUCED
        assert assessment.max_allowed_quantity == Decimal(100)

    def test_a_different_sector_is_unaffected(self) -> None:
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        rule = SectorExposureRule(
            config=RiskConfig(),
            sectors={7: "Energy", 8: "Financials", 9: "Financials"},
        )
        assessment = rule.evaluate(_proposal(quantity="200"), portfolio)
        assert assessment.decision is RiskDecision.ALLOW

    def test_unclassified_names_are_pooled_not_treated_as_separate_sectors(self) -> None:
        """The permissive reading would allow twelve unclassified banks."""
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        rule = SectorExposureRule(config=RiskConfig(), sectors={})
        assessment = rule.evaluate(_proposal(quantity="200"), portfolio)
        assert assessment.decision is RiskDecision.ALLOW_REDUCED
        assert assessment.max_allowed_quantity == Decimal(100)
        assert UNCLASSIFIED in assessment.reason
        assert "pooled" in assessment.reason

    def test_a_classified_proposal_does_not_join_the_unclassified_pool(self) -> None:
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        rule = SectorExposureRule(config=RiskConfig(), sectors={7: "Energy"})
        assessment = rule.evaluate(_proposal(quantity="200"), portfolio)
        assert assessment.decision is RiskDecision.ALLOW


class TestMatrixCorrelationSource:
    def test_lookup_is_symmetric(self) -> None:
        source = MatrixCorrelationSource(values={(1, 2): 0.8})
        assert source.correlation(1, 2) == 0.8
        assert source.correlation(2, 1) == 0.8

    def test_a_missing_pair_is_none_not_zero(self) -> None:
        source = MatrixCorrelationSource(values={(1, 2): 0.8})
        assert source.correlation(1, 3) is None

    def test_an_instrument_is_perfectly_correlated_with_itself(self) -> None:
        assert MatrixCorrelationSource(values={}).correlation(1, 1) == 1.0


class TestCorrelationRule:
    def _rule(self, values: dict[tuple[int, int], float]) -> CorrelationRule:
        return CorrelationRule(config=RiskConfig(), source=MatrixCorrelationSource(values=values))

    def test_an_empty_portfolio_has_no_pair_to_measure(self) -> None:
        assessment = self._rule({}).evaluate(_proposal(), _portfolio())
        assert assessment.decision is RiskDecision.ALLOW

    def test_rejects_a_name_that_moves_with_a_holding(self) -> None:
        portfolio = _portfolio(positions=(_held(8),))
        assessment = self._rule({(7, 8): 0.91}).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.REJECT
        assert "0.91" in assessment.reason

    def test_allows_a_genuinely_separate_bet(self) -> None:
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        assessment = self._rule({(7, 8): 0.1, (7, 9): 0.42}).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.ALLOW
        assert assessment.measured == 0.42  # the worst pair, not the last

    def test_an_unknown_correlation_blocks(self) -> None:
        """Unmeasured is not uncorrelated."""
        portfolio = _portfolio(positions=(_held(8), _held(9)))
        assessment = self._rule({(7, 8): 0.1}).evaluate(_proposal(), portfolio)
        assert assessment.decision is RiskDecision.REJECT
        assert "UNRESOLVED" in assessment.reason

    def test_adding_to_a_held_name_is_not_blocked_by_self_correlation(self) -> None:
        portfolio = _portfolio(positions=(_held(7),))
        assessment = self._rule({}).evaluate(_proposal(instrument_id=7), portfolio)
        assert assessment.decision is RiskDecision.ALLOW


class TestDrawdownHaltRule:
    def test_halts_at_the_configured_drawdown(self) -> None:
        rule = DrawdownHaltRule(config=RiskConfig(), peak_equity=Decimal(120000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="102000"))
        assert assessment.decision is RiskDecision.REJECT
        assert assessment.measured == 0.15

    def test_allows_just_inside_the_halt(self) -> None:
        rule = DrawdownHaltRule(config=RiskConfig(), peak_equity=Decimal(120000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="103000"))
        assert assessment.decision is RiskDecision.ALLOW

    def test_a_new_high_is_not_a_negative_drawdown(self) -> None:
        rule = DrawdownHaltRule(config=RiskConfig(), peak_equity=Decimal(120000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="130000"))
        assert assessment.measured == 0.0

    def test_the_reason_records_that_open_positions_are_still_managed(self) -> None:
        rule = DrawdownHaltRule(config=RiskConfig(), peak_equity=Decimal(120000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="90000"))
        assert "continue to be managed" in assessment.reason


class TestDailyLossRule:
    def test_stops_new_positions_after_a_bad_day(self) -> None:
        rule = DailyLossRule(config=RiskConfig(), session_open_equity=Decimal(100000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="97000"))
        assert assessment.decision is RiskDecision.REJECT

    def test_allows_a_merely_poor_day(self) -> None:
        rule = DailyLossRule(config=RiskConfig(), session_open_equity=Decimal(100000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="98000"))
        assert assessment.decision is RiskDecision.ALLOW
        assert assessment.measured == 0.02

    def test_a_gain_is_not_a_negative_loss(self) -> None:
        rule = DailyLossRule(config=RiskConfig(), session_open_equity=Decimal(100000))
        assessment = rule.evaluate(_proposal(), _portfolio(equity="104000"))
        assert assessment.measured == 0.0

    def test_a_missing_session_open_is_refused_not_defaulted(self) -> None:
        rule = DailyLossRule(config=RiskConfig(), session_open_equity=Decimal(0))
        assessment = rule.evaluate(_proposal(), _portfolio())
        assert assessment.decision is RiskDecision.REJECT
        assert "undefined" in assessment.reason
