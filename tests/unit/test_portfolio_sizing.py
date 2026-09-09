"""Tests for risk-based position sizing.

The first test is the one the ``PositionSizer`` protocol asks for by name:
sizing must be **monotone in equity**, because that is what makes returns
compound in percentage terms. The rest assert that each of the five caps can
bind, and that when one does it says so.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.portfolio.base import (
    PortfolioState,
    PositionSizer,
    PositionState,
    PositionStatus,
    Side,
)
from tradeit.portfolio.sizing import BindingConstraint, RiskBasedSizer
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection
from tradeit.strategy.config import RiskConfig, SizingConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _candidate(instrument_id: int = 7) -> OpportunityScore:
    return OpportunityScore(
        instrument_id=instrument_id,
        session_date=dt.date(2024, 6, 3),
        direction=SignalDirection.LONG,
        total=1.0,
        components=(ScoreComponent(name="trend", raw_value=1.0, normalised=1.0, weight=1.0),),
        feature_set_digest="fs",
        strategy_config_digest="sc",
    )


def _portfolio(
    *,
    equity: Decimal,
    cash: Decimal | None = None,
    positions: tuple[PositionState, ...] = (),
    last_prices: dict[int, Decimal] | None = None,
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=equity if cash is None else cash,
        equity=equity,
        positions=positions,
        last_prices=last_prices or {},
    )


def _sizer(
    *,
    sizing: SizingConfig | None = None,
    risk: RiskConfig | None = None,
    participation: str = "0.02",
) -> RiskBasedSizer:
    return RiskBasedSizer(
        sizing=sizing or SizingConfig(),
        risk=risk or RiskConfig(),
        max_participation=Decimal(participation),
    )


def _open(
    instrument_id: int,
    *,
    quantity: Decimal,
    entry: Decimal,
    stop: Decimal,
) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=quantity,
        average_entry_price=entry,
        stop_price=stop,
        opened_on=dt.date(2024, 5, 1),
    )


def test_satisfies_the_position_sizer_protocol() -> None:
    sizer: PositionSizer = _sizer()
    assert sizer.name == "risk_based"


class TestMonotoneInEquity:
    """The property the protocol docstring requires be asserted, not trusted."""

    @pytest.mark.parametrize("multiple", [2, 3, 5, 10, 100])
    def test_scaling_equity_scales_size_by_the_same_factor(self, multiple: int) -> None:
        # Fractional shares so the property is tested rather than the rounding.
        sizer = _sizer(sizing=SizingConfig(allow_fractional_shares=True))
        base = sizer.size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000")),
        )
        scaled = sizer.size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000") * multiple),
        )
        assert scaled.quantity == base.quantity * multiple

    def test_risk_fraction_is_invariant_to_equity(self) -> None:
        """The same fraction of the account is at risk at every account size.

        This is compounding stated as an invariant: dollar risk grows with the
        account, percentage risk does not.
        """
        sizer = _sizer(sizing=SizingConfig(allow_fractional_shares=True))
        fractions = {
            sizer.size(
                _candidate(),
                Decimal("100"),
                Decimal("95"),
                _portfolio(equity=Decimal(equity)),
            ).risk_fraction
            for equity in ("50000", "100000", "1000000", "10000000")
        }
        assert fractions == {Decimal("0.005")}

    def test_whole_share_rounding_never_exceeds_the_budget(self) -> None:
        """Rounding is downward, so the invariant is an upper bound, not equality."""
        sizer = _sizer()
        for equity in range(10_000, 200_001, 7_919):
            decision = sizer.size(
                _candidate(),
                Decimal("100"),
                Decimal("95"),
                _portfolio(equity=Decimal(equity)),
            )
            if decision.rejected:
                continue
            assert decision.risk_fraction <= Decimal("0.005")
            assert decision.quantity == decision.quantity.to_integral_value()


class TestTheBindingConstraintIsNamed:
    def test_risk_binds_in_the_ordinary_case(self) -> None:
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000")),
        )
        assert decision.binding_constraint == BindingConstraint.RISK
        assert decision.quantity == Decimal(100)  # $500 risk / $5 per share
        assert decision.risk_amount == Decimal(500)
        assert decision.notional == Decimal(10000)

    def test_a_very_tight_stop_is_capped_by_position_size(self) -> None:
        """The risk a tight stop does not measure is the gap through it."""
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal("99.90"),
            _portfolio(equity=Decimal("100000")),
        )
        # Risk alone would allow 5,000 shares ($500 / $0.10); the position cap
        # allows 200.
        assert decision.binding_constraint == BindingConstraint.POSITION
        assert decision.quantity == Decimal(200)

    def test_existing_open_risk_consumes_the_heat_budget(self) -> None:
        held = _open(9, quantity=Decimal(1000), entry=Decimal(50), stop=Decimal("44.20"))
        portfolio = _portfolio(
            equity=Decimal("100000"),
            positions=(held,),
            last_prices={9: Decimal(50)},
        )
        # $5,800 of $6,000 heat is already open; $200 remains.
        assert portfolio.total_open_risk() == Decimal("5800.00")
        decision = _sizer().size(_candidate(), Decimal("100"), Decimal("95"), portfolio)
        assert decision.binding_constraint == BindingConstraint.HEAT
        assert decision.quantity == Decimal(40)  # $200 / $5

    def test_a_full_heat_budget_refuses_the_trade(self) -> None:
        held = _open(9, quantity=Decimal(1000), entry=Decimal(50), stop=Decimal(44))
        portfolio = _portfolio(
            equity=Decimal("100000"),
            positions=(held,),
            last_prices={9: Decimal(50)},
        )
        decision = _sizer().size(_candidate(), Decimal("100"), Decimal("95"), portfolio)
        assert decision.rejected
        assert decision.binding_constraint == BindingConstraint.HEAT
        assert decision.quantity == Decimal(0)

    def test_cash_binds_when_equity_is_mostly_invested(self) -> None:
        portfolio = _portfolio(equity=Decimal("100000"), cash=Decimal("4000"))
        decision = _sizer().size(_candidate(), Decimal("100"), Decimal("95"), portfolio)
        assert decision.binding_constraint == BindingConstraint.CASH
        assert decision.quantity == Decimal(40)

    def test_liquidity_binds_on_a_thin_name(self) -> None:
        decision = _sizer(participation="0.02").size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000")),
            average_dollar_volume=Decimal("200000"),
        )
        # 2% of $200,000 is $4,000, or 40 shares at $100.
        assert decision.binding_constraint == BindingConstraint.LIQUIDITY
        assert decision.quantity == Decimal(40)

    def test_liquidity_is_ignored_when_volume_is_unknown(self) -> None:
        """Absent liquidity data is not a cap of zero, and not a cap of infinity.

        It is simply not applied — but the caller then has no liquidity
        protection, which is why the field is on the signature at all.
        """
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000")),
            average_dollar_volume=None,
        )
        assert decision.binding_constraint == BindingConstraint.RISK


class TestRefusals:
    @pytest.mark.parametrize("stop", ["100", "105"])
    def test_a_stop_at_or_above_entry_is_refused(self, stop: str) -> None:
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal(stop),
            _portfolio(equity=Decimal("100000")),
        )
        assert decision.rejected
        assert decision.quantity == Decimal(0)
        assert decision.rejection_reason is not None
        assert "undefined risk" in decision.rejection_reason

    def test_a_sub_minimum_position_is_refused_rather_than_rounded_up(self) -> None:
        """Rounding up would breach the very cap that produced the size."""
        portfolio = _portfolio(equity=Decimal("100000"), cash=Decimal("400"))
        decision = _sizer().size(_candidate(), Decimal("100"), Decimal("95"), portfolio)
        assert decision.rejected
        assert decision.binding_constraint == BindingConstraint.CASH
        assert decision.rejection_reason is not None
        assert "below the 500.0 minimum" in decision.rejection_reason

    def test_a_position_smaller_than_one_share_is_refused(self) -> None:
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal("100000"), cash=Decimal("50")),
        )
        assert decision.rejected
        assert decision.rejection_reason is not None
        assert "rounds to none" in decision.rejection_reason

    def test_zero_equity_is_refused_rather_than_dividing_by_it(self) -> None:
        decision = _sizer().size(
            _candidate(),
            Decimal("100"),
            Decimal("95"),
            _portfolio(equity=Decimal(0)),
        )
        assert decision.rejected

    def test_a_non_positive_entry_price_is_refused(self) -> None:
        decision = _sizer().size(
            _candidate(),
            Decimal(0),
            Decimal("-5"),
            _portfolio(equity=Decimal("100000")),
        )
        assert decision.rejected


def test_parameters_are_reported_for_the_audit_trail() -> None:
    parameters = _sizer().parameters
    assert parameters["risk_per_trade_pct"] == 0.005
    assert parameters["max_portfolio_heat_pct"] == 0.06
    assert parameters["max_participation"] == "0.02"
