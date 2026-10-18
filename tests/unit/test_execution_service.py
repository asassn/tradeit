"""Reconciliation, and refusing to trade against a fictional portfolio.

The design claim under test: **the broker is authoritative and our state is a
model of it.** When they disagree, every decision taken afterwards is computed
from a book that does not exist, so the platform halts. A service that logs a
discrepancy and keeps trading has chosen the worst of both -- it knows it is
wrong and acts anyway.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import (
    Bartimeframe,
    KnowledgeTimeSource,
    OrderSide,
    OrderType,
    PositionStatus,
    Side,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import DiscrepancyKind, OrderRequest
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService, TradingHalted
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.storage.tables import JournalEntry
from tradeit.strategy.config import StrategyConfig

SESSION = dt.date(2015, 6, 1)
NEXT = dt.date(2015, 6, 2)
KNOWN = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


def _bar(instrument_id: int = 1, close: int = 101) -> OhlcvBar:
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=NEXT,
        open=Decimal(100),
        high=Decimal(close + 1),
        low=Decimal(99),
        close=Decimal(close),
        volume=Decimal(1_000_000),
        event_time=KNOWN,
        knowledge_time=KNOWN,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _broker(cash: int = 100_000) -> PaperBroker:
    config = StrategyConfig(name="ma_cross_baseline")
    return PaperBroker(
        account_id="paper-1",
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        starting_cash=Decimal(cash),
        settings=Settings(trading_mode=TradingMode.PAPER),
    )


def _position(instrument_id: int, quantity: Decimal, price: Decimal) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=quantity,
        average_entry_price=price,
        stop_price=price * Decimal("0.92"),
        opened_on=SESSION,
    )


def _state(
    positions: tuple[PositionState, ...] = (), cash: Decimal = Decimal(100_000)
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(SESSION, dt.time(), tzinfo=dt.UTC),
        cash=cash,
        equity=cash,
        positions=positions,
    )


def _service(
    db_session: Session, broker: PaperBroker, state: PortfolioState
) -> BrokerExecutionService:
    return BrokerExecutionService(
        broker=broker,
        session=db_session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("0.01"),
        ours=lambda _portfolio_id: state,
    )


def _order(client_order_id: str = "c-1", quantity: int = 100) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        portfolio_id=1,
        instrument_id=1,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(quantity),
    )


def test_agreement_produces_no_discrepancies(db_session: Session) -> None:
    broker = _broker()
    service = _service(db_session, broker, _state())
    assert service.reconcile(1, session_date=SESSION) == ()
    assert not service.is_halted(1)


def test_a_position_the_broker_does_not_report_halts_trading(db_session: Session) -> None:
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)

    found = service.reconcile(1, session_date=SESSION)

    assert [item.kind for item in found] == [DiscrepancyKind.MISSING_POSITION]
    assert service.is_halted(1)
    with pytest.raises(TradingHalted, match="halted"):
        service.place(_order())


def test_a_position_we_have_no_record_of_halts_trading(db_session: Session) -> None:
    broker = _broker()
    broker.submit_order(_order(), "c-1")
    broker.advance(NEXT, {1: _bar()})
    service = _service(db_session, broker, _state())

    found = service.reconcile(1, session_date=SESSION)

    kinds = {item.kind for item in found}
    assert DiscrepancyKind.UNKNOWN_POSITION in kinds
    assert service.is_halted(1)


def test_a_quantity_disagreement_is_exact_with_no_tolerance(db_session: Session) -> None:
    """A share count is an integer, and a tolerance on it would hide the
    commonest real cause -- a missed partial fill."""
    broker = _broker()
    broker.submit_order(_order(quantity=100), "c-1")
    broker.advance(NEXT, {1: _bar()})
    held = broker.list_positions()[0]
    state = _state(
        positions=(_position(1, held.quantity + 1, held.average_price),),
        cash=broker.get_account().cash,
    )
    service = _service(db_session, broker, state)

    found = service.reconcile(1, session_date=SESSION)

    assert [item.kind for item in found] == [DiscrepancyKind.QUANTITY]
    assert found[0].magnitude == 1


def test_an_average_price_within_tolerance_is_not_a_discrepancy(db_session: Session) -> None:
    """Decimal quantities accumulate rounding through commission splits.
    Halting on a half-cent would mean halting every day."""
    broker = _broker()
    broker.submit_order(_order(), "c-1")
    broker.advance(NEXT, {1: _bar()})
    held = broker.list_positions()[0]
    state = _state(
        positions=(_position(1, held.quantity, held.average_price + Decimal("0.005")),),
        cash=broker.get_account().cash,
    )
    service = _service(db_session, broker, state)
    assert service.reconcile(1, session_date=SESSION) == ()


def test_an_average_price_outside_tolerance_is_a_discrepancy(db_session: Session) -> None:
    broker = _broker()
    broker.submit_order(_order(), "c-1")
    broker.advance(NEXT, {1: _bar()})
    held = broker.list_positions()[0]
    state = _state(
        positions=(_position(1, held.quantity, held.average_price + Decimal("5")),),
        cash=broker.get_account().cash,
    )
    service = _service(db_session, broker, state)
    found = service.reconcile(1, session_date=SESSION)
    assert [item.kind for item in found] == [DiscrepancyKind.AVERAGE_PRICE]


def test_a_cash_disagreement_is_reported_against_the_portfolio_not_an_instrument(
    db_session: Session,
) -> None:
    broker = _broker()
    service = _service(db_session, broker, _state(cash=Decimal(90_000)))
    found = service.reconcile(1, session_date=SESSION)
    assert [item.kind for item in found] == [DiscrepancyKind.CASH]
    assert found[0].instrument_id is None
    assert found[0].magnitude == Decimal(10_000)


def test_magnitude_is_none_when_one_side_has_no_value(db_session: Session) -> None:
    """A 0 would read as agreement. The distance from a number to an absence
    is not a number."""
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)
    found = service.reconcile(1, session_date=SESSION)
    assert found[0].magnitude is None


def test_a_halt_is_written_to_the_journal(db_session: Session) -> None:
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)
    service.reconcile(1, session_date=SESSION)
    db_session.flush()

    entries = db_session.query(JournalEntry).all()
    assert len(entries) == 1
    assert entries[0].entry_type == "discrepancy"
    assert entries[0].session_date == SESSION
    assert "halted" in entries[0].summary


def test_resuming_requires_an_author_and_a_reason(db_session: Session) -> None:
    """A halt cleared by nobody for no reason is indistinguishable from a halt
    that never happened."""
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)
    service.reconcile(1, session_date=SESSION)

    with pytest.raises(ValueError, match="who resolved it"):
        service.resume(1, session_date=SESSION, resolved_by="", note="fixed")
    with pytest.raises(ValueError, match="who resolved it"):
        service.resume(1, session_date=SESSION, resolved_by="eric", note="  ")
    assert service.is_halted(1)


def test_resuming_clears_the_halt_and_records_who(db_session: Session) -> None:
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)
    service.reconcile(1, session_date=SESSION)

    service.resume(1, session_date=SESSION, resolved_by="eric", note="broker restated")
    db_session.flush()

    assert not service.is_halted(1)
    authors = {entry.author for entry in db_session.query(JournalEntry).all()}
    assert "eric" in authors


def test_a_second_reconcile_that_agrees_does_not_clear_a_halt(db_session: Session) -> None:
    """A transient disagreement that resolves itself is where silence is most
    dangerous, so clearing is an act somebody performs."""
    broker = _broker()
    state = _state(positions=(_position(1, Decimal(100), Decimal(50)),))
    service = _service(db_session, broker, state)
    service.reconcile(1, session_date=SESSION)
    assert service.is_halted(1)

    service.ours = lambda _portfolio_id: _state()
    assert service.reconcile(1, session_date=SESSION) == ()
    assert service.is_halted(1), "a halt must not clear itself"


def test_reconciling_without_our_own_state_is_refused(db_session: Session) -> None:
    """Comparing the broker to itself always agrees, which is the failure."""
    service = BrokerExecutionService(
        broker=_broker(),
        session=db_session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("0.01"),
    )
    with pytest.raises(ValueError, match="compares the broker to itself"):
        service.reconcile(1, session_date=SESSION)


def test_placing_an_order_returns_a_cancellable_handle(db_session: Session) -> None:
    broker = _broker()
    service = _service(db_session, broker, _state())
    order_id = service.place(_order())
    service.cancel(order_id)
    with pytest.raises(KeyError):
        service.cancel(order_id + 999)
