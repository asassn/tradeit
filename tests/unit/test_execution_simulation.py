"""Tests for simulated costs and fills.

The gap tests are the point of the file. A backtester that fills stops at their
trigger price understates drawdown precisely in the sessions that decide whether
a strategy survives, and it does so while every other test still passes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import (
    Bartimeframe,
    KnowledgeTimeSource,
    OrderSide,
    OrderType,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import CostEstimate, OrderRequest
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.strategy.config import CostConfig

SESSION = dt.date(2024, 6, 3)
KNOWN = dt.datetime(2024, 6, 3, 21, tzinfo=dt.UTC)

COSTS = ParticipationCostModel(config=CostConfig())
FILLS = BarFillModel(max_participation=Decimal("0.10"))


def _bar(
    *,
    open_: str = "100",
    high: str = "105",
    low: str = "95",
    close: str = "102",
    volume: str = "1000000",
    instrument_id: int = 7,
) -> OhlcvBar:
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=SESSION,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        event_time=KNOWN,
        knowledge_time=KNOWN,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _order(
    *,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: str = "100",
    limit: str | None = None,
    stop: str | None = None,
    instrument_id: int = 7,
) -> OrderRequest:
    return OrderRequest(
        client_order_id="c1",
        portfolio_id=1,
        instrument_id=instrument_id,
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit) if limit else None,
        stop_price=Decimal(stop) if stop else None,
    )


def _free() -> CostEstimate:
    """A cost estimate with no friction, to isolate fill logic from pricing."""
    return CostEstimate(
        commission=Decimal(0),
        spread_cost=Decimal(0),
        market_impact=Decimal(0),
        expected_fill_price=Decimal(100),
        participation_rate=0.0,
    )


class TestCostModel:
    def test_slippage_is_adverse_in_both_directions(self) -> None:
        """Never symmetric: a zero-mean draw returns the frictionless result."""
        bar = _bar()
        buy = COSTS.estimate(_order(side=OrderSide.BUY), bar)
        sell = COSTS.estimate(_order(side=OrderSide.SELL), bar)
        assert buy.expected_fill_price > bar.close
        assert sell.expected_fill_price < bar.close

    def test_cost_rises_with_participation(self) -> None:
        """A flat bps model buys a million illiquid shares at the close."""
        bar = _bar()
        liquid = COSTS.estimate(_order(quantity="100"), bar, Decimal(100_000_000))
        thin = COSTS.estimate(_order(quantity="100"), bar, Decimal(50_000))
        assert thin.market_impact > liquid.market_impact
        assert thin.total > liquid.total

    def test_no_volume_information_means_no_impact_term(self) -> None:
        estimate = COSTS.estimate(_order(), _bar(), None)
        assert estimate.market_impact == Decimal(0)
        assert estimate.participation_rate == 0.0
        assert estimate.spread_cost > 0  # spread and slippage still apply

    def test_the_commission_minimum_applies_to_small_orders(self) -> None:
        estimate = COSTS.estimate(_order(quantity="10"), _bar())
        assert estimate.commission == Decimal("1.0")  # not 10 x 0.005

    def test_commission_scales_beyond_the_minimum(self) -> None:
        estimate = COSTS.estimate(_order(quantity="1000"), _bar())
        assert estimate.commission == Decimal("5.000")

    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
    @pytest.mark.parametrize("adv", [None, Decimal(50_000)])
    def test_pricing_from_a_price_is_pricing_from_the_bar(
        self, side: OrderSide, adv: Decimal | None
    ) -> None:
        """``estimate_at`` is what ``estimate`` does, so a caller holding only a
        price -- the transaction-cost rule -- prices exactly what will be charged."""
        bar = _bar()
        assert COSTS.estimate_at(
            side=side, quantity=Decimal(300), reference=bar.close, average_dollar_volume=adv
        ) == COSTS.estimate(_order(side=side, quantity="300"), bar, adv)


class TestGapsAreRespected:
    def test_a_sell_stop_that_gaps_through_fills_at_the_open(self) -> None:
        """The single most consequential rule in the module."""
        bar = _bar(open_="44", high="46", low="43", close="45")
        order = _order(side=OrderSide.SELL, order_type=OrderType.STOP, stop="50")
        fill = FILLS.simulate(order, bar, _free())
        assert fill is not None
        assert fill.price == Decimal(44)  # not 50

    def test_a_buy_stop_that_gaps_through_pays_the_open(self) -> None:
        bar = _bar(open_="60", high="62", low="59", close="61")
        order = _order(side=OrderSide.BUY, order_type=OrderType.STOP, stop="55")
        fill = FILLS.simulate(order, bar, _free())
        assert fill is not None
        assert fill.price == Decimal(60)  # not 55

    def test_a_stop_touched_intraday_fills_at_the_stop(self) -> None:
        bar = _bar(open_="100", high="105", low="49", close="52")
        order = _order(side=OrderSide.SELL, order_type=OrderType.STOP, stop="50")
        fill = FILLS.simulate(order, bar, _free())
        assert fill is not None
        assert fill.price == Decimal(50)

    def test_an_untouched_stop_does_not_fill(self) -> None:
        bar = _bar(open_="100", high="105", low="95", close="102")
        order = _order(side=OrderSide.SELL, order_type=OrderType.STOP, stop="90")
        assert FILLS.simulate(order, bar, _free()) is None

    def test_a_stop_limit_can_gap_past_its_limit_and_not_fill(self) -> None:
        """The protection worked and the exit did not happen. That is real."""
        bar = _bar(open_="30", high="32", low="29", close="31")
        order = _order(side=OrderSide.SELL, order_type=OrderType.STOP_LIMIT, stop="50", limit="45")
        assert FILLS.simulate(order, bar, _free()) is None


class TestNothingFillsOutsideTheBar:
    def test_a_limit_below_the_session_low_does_not_fill(self) -> None:
        bar = _bar(low="100.50", open_="101", high="105", close="102")
        order = _order(order_type=OrderType.LIMIT, limit="100")
        assert FILLS.simulate(order, bar, _free()) is None

    def test_a_buy_limit_takes_a_better_open_when_the_gap_favours_it(self) -> None:
        bar = _bar(open_="98", high="103", low="97", close="102")
        order = _order(order_type=OrderType.LIMIT, limit="100")
        fill = FILLS.simulate(order, bar, _free())
        assert fill is not None
        assert fill.price == Decimal(98)

    def test_a_buy_limit_touched_intraday_fills_at_the_limit(self) -> None:
        bar = _bar(open_="103", high="105", low="99", close="102")
        order = _order(order_type=OrderType.LIMIT, limit="100")
        fill = FILLS.simulate(order, bar, _free())
        assert fill is not None
        assert fill.price == Decimal(100)

    def test_a_sell_limit_above_the_session_high_does_not_fill(self) -> None:
        bar = _bar(high="104")
        order = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit="110")
        assert FILLS.simulate(order, bar, _free()) is None

    def test_slippage_cannot_push_a_fill_outside_the_range(self) -> None:
        """The exchange printed no price there."""
        bar = _bar(open_="105", high="105", low="105", close="105")
        cost = CostEstimate(
            commission=Decimal(0),
            spread_cost=Decimal(10000),
            market_impact=Decimal(0),
            expected_fill_price=Decimal(105),
            participation_rate=0.0,
        )
        fill = FILLS.simulate(_order(), bar, cost)
        assert fill is not None
        assert fill.price == Decimal(105)


class TestVolumeCapsTheFill:
    def test_an_oversized_order_fills_partially(self) -> None:
        bar = _bar(volume="1000")
        fill = FILLS.simulate(_order(quantity="500"), bar, _free())
        assert fill is not None
        assert fill.quantity == Decimal(100)  # 10% of 1,000

    def test_a_session_with_no_volume_fills_nothing(self) -> None:
        """A halted day is not a chance to trade at yesterday's price."""
        bar = _bar(volume="0")
        assert FILLS.simulate(_order(), bar, _free()) is None

    def test_commission_is_prorated_on_a_partial_fill(self) -> None:
        bar = _bar(volume="1000")
        cost = CostEstimate(
            commission=Decimal(10),
            spread_cost=Decimal(0),
            market_impact=Decimal(0),
            expected_fill_price=Decimal(100),
            participation_rate=0.0,
        )
        fill = FILLS.simulate(_order(quantity="500"), bar, cost)
        assert fill is not None
        assert fill.commission == Decimal(2)  # 100 of 500 shares

    def test_an_order_inside_the_cap_fills_in_full(self) -> None:
        fill = FILLS.simulate(_order(quantity="100"), _bar(volume="1000000"), _free())
        assert fill is not None
        assert fill.quantity == Decimal(100)


class TestSafety:
    def test_filling_an_order_against_another_instruments_bar_is_refused(self) -> None:
        with pytest.raises(ValueError, match="would invent a price"):
            FILLS.simulate(_order(instrument_id=7), _bar(instrument_id=9), _free())

    def test_market_on_close_uses_the_close(self) -> None:
        fill = FILLS.simulate(
            _order(order_type=OrderType.MARKET_ON_CLOSE), _bar(close="102"), _free()
        )
        assert fill is not None
        assert fill.price == Decimal(102)

    def test_a_market_order_uses_the_open(self) -> None:
        """The bar it is handed. Which bar that is, is the engine's job."""
        fill = FILLS.simulate(_order(), _bar(open_="100"), _free())
        assert fill is not None
        assert fill.price == Decimal(100)

    def test_slippage_is_recorded_separately_from_the_price(self) -> None:
        cost = CostEstimate(
            commission=Decimal(0),
            spread_cost=Decimal(50),
            market_impact=Decimal(0),
            expected_fill_price=Decimal(100),
            participation_rate=0.0,
        )
        fill = FILLS.simulate(_order(quantity="100"), _bar(open_="100"), cost)
        assert fill is not None
        assert fill.price == Decimal("100.5")
        assert fill.slippage == Decimal("50.0")
