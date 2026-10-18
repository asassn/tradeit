"""Phase 11's paper venue: the behaviours a simulated broker gets wrong.

The design constraint is that paper and live share one code path, so a paper
venue that takes a shortcut produces results that say nothing about live
behaviour. Each test below pins a shortcut this class refuses to take.

The central one is **temporal**: a venue does not know the future, so
``submit_order`` must fill nothing. A broker that filled on submission would be
answering with information it does not have, and every paper result would carry
a look-ahead the backtester is structured to prevent.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import (
    Bartimeframe,
    KnowledgeTimeSource,
    OrderSide,
    OrderStatus,
    OrderType,
)
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.execution.base import OrderRequest
from tradeit.execution.paper import PaperBroker, PaperTradingRefused
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.strategy.config import StrategyConfig

SESSION = dt.date(2015, 6, 1)
NEXT = dt.date(2015, 6, 2)
KNOWN = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


def _bar(
    instrument_id: int = 1,
    open_: int | str = 100,
    close: int | str = 101,
    volume: int = 1_000_000,
    session_date: dt.date = SESSION,
    high: int | str | None = None,
    low: int | str | None = None,
) -> OhlcvBar:
    """A valid bar for any open/close asked for.

    `high` and `low` DERIVE from open and close unless given. A first version
    hard-coded 102/99 and raised `DataError` the moment a test wanted a close
    of 130 -- the model validator catching a malformed fixture, which is the
    validator doing its job and not something to work around.
    """
    body = (Decimal(open_), Decimal(close))
    top = Decimal(high) if high is not None else max(body) + 1
    bottom = Decimal(low) if low is not None else min(body) - 1
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=session_date,
        open=Decimal(open_),
        high=top,
        low=bottom,
        close=Decimal(close),
        volume=Decimal(volume),
        event_time=KNOWN,
        knowledge_time=KNOWN,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _broker(cash: int = 100_000, **overrides: object) -> PaperBroker:
    config = StrategyConfig(name="ma_cross_baseline")
    kwargs: dict[str, object] = {
        "account_id": "paper-1",
        "costs": ParticipationCostModel(config=config.costs),
        "fills": BarFillModel(max_participation=Decimal("0.02")),
        "starting_cash": Decimal(cash),
        "settings": Settings(trading_mode=TradingMode.PAPER),
    }
    kwargs.update(overrides)
    return PaperBroker(**kwargs)  # type: ignore[arg-type]


def _order(
    client_order_id: str = "c-1",
    side: OrderSide = OrderSide.BUY,
    quantity: int | str = 100,
    order_type: OrderType = OrderType.MARKET,
    **overrides: object,
) -> OrderRequest:
    kwargs: dict[str, object] = {
        "client_order_id": client_order_id,
        "portfolio_id": 1,
        "instrument_id": 1,
        "side": side,
        "order_type": order_type,
        "quantity": Decimal(quantity),
    }
    kwargs.update(overrides)
    return OrderRequest(**kwargs)  # type: ignore[arg-type]


def test_real_live_settings_cannot_even_be_constructed_here() -> None:
    """The outer interlock, recorded because it changes what the next test can test.

    `Settings(trading_mode=LIVE)` raises unless a machine-local authorisation
    file exists, so a test cannot build live settings to hand the broker. That
    is the interlock in `config.py` working, and it means the broker's own
    check must be tested against a stand-in rather than the real thing.
    """
    with pytest.raises(ConfigError, match="authorization file"):
        Settings(trading_mode=TradingMode.LIVE)


class _NotSimulated:
    """A settings stand-in that reports a mode where real orders could go out.

    Needed because real live `Settings` cannot be constructed (above). The
    point under test is that `PaperBroker` consults `orders_are_simulated`
    ITSELF rather than trusting its caller -- ADR-0004 -- so a stand-in that
    answers False is exactly the input that matters.
    """

    trading_mode = "live"
    orders_are_simulated = False


def test_a_paper_broker_refuses_to_exist_where_orders_are_not_simulated() -> None:
    """ADR-0004, enforced by the adapter rather than trusted to the caller."""
    with pytest.raises(PaperTradingRefused, match="simulated venue"):
        _broker(settings=_NotSimulated())


def test_submitting_an_order_fills_nothing() -> None:
    """The temporal rule. A venue does not know the future.

    If this ever returns FILLED, every paper result carries a look-ahead: the
    order would have executed against a bar that had not printed when it was
    placed.
    """
    broker = _broker()
    status = broker.submit_order(_order(), "c-1")
    assert status.status is OrderStatus.NEW
    assert status.filled_quantity == 0
    assert status.average_fill_price is None
    assert broker.list_positions() == ()
    assert broker.get_account().cash == Decimal(100_000)


def test_a_resting_order_fills_on_the_next_session() -> None:
    broker = _broker()
    submitted = broker.submit_order(_order(), "c-1")
    fills = broker.advance(NEXT, {1: _bar()})

    assert len(fills) == 1
    assert fills[0].quantity == Decimal(100)
    assert broker.get_order(submitted.broker_order_id).status is OrderStatus.FILLED
    positions = broker.list_positions()
    assert len(positions) == 1
    assert positions[0].quantity == Decimal(100)
    assert broker.get_account().cash < Decimal(100_000)


def test_resubmitting_the_same_client_order_id_is_a_no_op() -> None:
    """Idempotency is what makes a retry after a timeout safe."""
    broker = _broker()
    first = broker.submit_order(_order(), "c-1")
    second = broker.submit_order(_order(), "c-1")

    assert first.broker_order_id == second.broker_order_id
    broker.advance(NEXT, {1: _bar()})
    assert broker.list_positions()[0].quantity == Decimal(100), (
        "a retried submission must not open a second position"
    )


def test_the_same_client_order_id_for_a_different_order_raises() -> None:
    """The dangerous case: returning the old status would drop the new order."""
    broker = _broker()
    broker.submit_order(_order(quantity=100), "c-1")
    with pytest.raises(ValueError, match="two intentions"):
        broker.submit_order(_order(quantity=200), "c-1")


def test_a_mismatched_idempotency_key_raises() -> None:
    broker = _broker()
    with pytest.raises(ValueError, match="idempotency key"):
        broker.submit_order(_order(client_order_id="c-1"), "c-2")


def test_a_buy_is_capped_by_cash_and_never_overdraws() -> None:
    """A book that cannot exist is worse than a rejected order.

    A negative cash balance reconciles against nothing, so the venue fills what
    the cash affords and no more.
    """
    broker = _broker(cash=5_000)
    broker.submit_order(_order(quantity=1_000), "c-1")
    broker.advance(NEXT, {1: _bar()})

    account = broker.get_account()
    assert account.cash >= 0, "the venue overdrew the account"
    held = broker.list_positions()[0].quantity
    assert held < Decimal(1_000)
    assert held > 0


def test_a_sell_cannot_exceed_what_is_held() -> None:
    """The FILL must be capped, not merely the visible position.

    A first version asserted only `list_positions() == ()`, and a mutant that
    removed the cap passed it: the holding went to -400 shares and
    `list_positions` hides non-positive quantities, so an impossible short read
    as a clean exit. The quantity actually sold and the cash received are what
    distinguish the two.
    """
    broker = _broker()
    broker.submit_order(_order(client_order_id="buy", quantity=100), "buy")
    broker.advance(NEXT, {1: _bar()})
    cash_after_buy = broker.get_account().cash

    broker.submit_order(_order(client_order_id="sell", side=OrderSide.SELL, quantity=500), "sell")
    fills = broker.advance(dt.date(2015, 6, 3), {1: _bar(session_date=dt.date(2015, 6, 3))})

    assert len(fills) == 1
    assert fills[0].quantity == Decimal(100), "sold more than was held"
    proceeds = broker.get_account().cash - cash_after_buy
    assert proceeds < Decimal(100) * Decimal(102), "received proceeds for shares never held"
    assert broker.list_positions() == ()


def test_short_selling_is_refused_rather_than_modelled() -> None:
    """Borrow availability is not something this project has evidence about."""
    broker = _broker()
    with pytest.raises(ValueError, match="borrow"):
        broker.submit_order(_order(side=OrderSide.SELL_SHORT), "c-1")


def test_a_fractional_quantity_is_refused() -> None:
    broker = _broker()
    with pytest.raises(ValueError, match="fractional"):
        broker.submit_order(_order(quantity="10.4"), "c-1")


def test_an_unfilled_day_order_is_cancelled_not_carried() -> None:
    """TimeInForce.DAY means the day. A survivor would be a GTC nobody asked for."""
    broker = _broker()
    submitted = broker.submit_order(
        _order(order_type=OrderType.LIMIT, limit_price=Decimal(50)), "c-1"
    )
    broker.advance(NEXT, {1: _bar()})

    assert broker.get_order(submitted.broker_order_id).status is OrderStatus.CANCELLED
    assert broker.list_positions() == ()


def test_an_instrument_that_did_not_print_neither_fills_nor_expires_early() -> None:
    """Silence is not an event. A venue does not cancel on a missing symbol.

    The order is still cancelled at end of session as a DAY order -- what must
    not happen is it being treated as *rejected* or filled because another
    instrument traded.
    """
    broker = _broker()
    submitted = broker.submit_order(_order(), "c-1")
    fills = broker.advance(NEXT, {99: _bar(instrument_id=99)})

    assert fills == ()
    assert broker.get_order(submitted.broker_order_id).status is not OrderStatus.REJECTED
    assert broker.list_positions() == ()


def test_a_partial_sale_leaves_the_average_price_unchanged() -> None:
    """Reconciliation compares average_price, so how the basis reduces matters.

    Reducing the basis by the proceeds instead of proportionally would make a
    profitable sale look like it cut the basis of the remainder -- and the
    broker's average_price is exactly what a reconciliation checks.
    """
    broker = _broker()
    broker.submit_order(_order(client_order_id="buy", quantity=100), "buy")
    broker.advance(NEXT, {1: _bar()})
    before = broker.list_positions()[0].average_price

    broker.submit_order(_order(client_order_id="sell", side=OrderSide.SELL, quantity=40), "sell")
    broker.advance(dt.date(2015, 6, 3), {1: _bar(session_date=dt.date(2015, 6, 3), close=130)})

    position = broker.list_positions()[0]
    assert position.quantity == Decimal(60)
    assert position.average_price == pytest.approx(before), (
        "a partial sale must not move the average entry price of the remainder"
    )


def test_cancelling_a_filled_order_is_a_no_op_not_an_error() -> None:
    """A caller retrying a cancel after a timeout must not be punished."""
    broker = _broker()
    submitted = broker.submit_order(_order(), "c-1")
    broker.advance(NEXT, {1: _bar()})
    status = broker.cancel_order(submitted.broker_order_id)
    assert status.status is OrderStatus.FILLED


def test_buying_power_equals_cash_because_no_margin_is_modelled() -> None:
    """Stated rather than inflated: a credit line would be invented."""
    broker = _broker()
    account = broker.get_account()
    assert account.buying_power == account.cash
    assert account.maintenance_margin == 0


def test_the_market_is_never_reported_open() -> None:
    """This venue has no intraday clock, and saying otherwise invites a caller
    to expect an immediate fill."""
    assert _broker().is_market_open() is False


def test_equity_tracks_the_last_print_not_the_entry_price() -> None:
    broker = _broker()
    broker.submit_order(_order(quantity=100), "c-1")
    broker.advance(NEXT, {1: _bar()})
    at_entry = broker.equity
    broker.advance(dt.date(2015, 6, 3), {1: _bar(session_date=dt.date(2015, 6, 3), close=150)})
    assert broker.equity > at_entry


def test_a_non_order_object_is_refused() -> None:
    broker = _broker()
    with pytest.raises(TypeError, match="OrderRequest"):
        broker.submit_order({"quantity": 100}, "c-1")


def test_starting_cash_must_be_positive() -> None:
    with pytest.raises(ValueError, match="starting_cash"):
        _broker(cash=0)


def test_an_unknown_order_id_raises_rather_than_returning_nothing() -> None:
    broker = _broker()
    with pytest.raises(KeyError):
        broker.get_order("nope")
    with pytest.raises(KeyError):
        broker.cancel_order("nope")
