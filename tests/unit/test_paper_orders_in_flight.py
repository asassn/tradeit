"""An order in flight at a restart is restored, not dropped.

Found 2026-11-26 (`docs/PLAN_FROM_BOOK_2026-11-26.md`): a book run one process
per session placed 379 orders over 144 sessions and filled none, where one
process placed 20 and filled 20. Every order is decided at a close and fills at
the next session's bars, so every one is in flight when its process ends --
and the record held positions, not orders.

`test_paper_session_resume.py` cut its run on a session with nothing in
flight, which is why it passed. **This file cuts on the one session that
matters**: the close at which an entry has been decided and not yet filled.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.unit.test_paper_session_resume import (
    CAPITAL,
    DAYS,
    _bar,
    _candidate,
    _driver,
    _portfolio,
    _venue,
    _write,
)
from tradeit.core.enums import OrderSide, OrderStatus, OrderType
from tradeit.execution.base import OrderRequest
from tradeit.execution.live_shape import LiveShapedSession
from tradeit.execution.paper import OrderInFlight
from tradeit.observability.portfolio_record import (
    last_recorded_session,
    load_ladder_state,
    load_open_orders,
    load_portfolio_state,
    sync_orders,
)
from tradeit.storage.tables import Order

#: The decided stop, deliberately NOT the driver's 0.92 fallback, so a restored
#: entry that lost its stop is visible as a different number.
DECIDED_STOP = Decimal(90)


def _entry(day: dt.date) -> object:
    candidate = _candidate(day, 100)
    return type(candidate)(
        score=candidate.score,
        entry_price=candidate.entry_price,
        stop_price=DECIDED_STOP,
        sector=candidate.sector,
    )


def _close(db: Session, pid: int, driver: LiveShapedSession, broker: object, day: dt.date) -> None:
    """What the script does at each close, orders included."""
    sync_orders(
        db,
        pid,
        broker.order_history(),  # type: ignore[attr-defined]
        driver.intended_stops(),
        decided_at=dt.datetime.combine(day, dt.time(20), tzinfo=dt.UTC),
    )
    _write(db, pid, driver, day, None)


def _restart(
    db: Session, pid: int, bars: dict[dt.date, object]
) -> tuple[LiveShapedSession, object]:
    """`--resume`, orders included: same account, decided bar re-derived."""
    last = last_recorded_session(db, pid)
    assert last is not None
    state = load_portfolio_state(
        db, pid, as_of=dt.datetime.combine(last, dt.time(20), tzinfo=dt.UTC)
    )
    assert state is not None
    orders = load_open_orders(db, pid)
    broker = _venue()
    broker.restore(
        cash=state.cash,
        holdings={
            p.instrument_id: (p.quantity, p.average_entry_price) for p in state.open_positions
        },
        orders=[
            OrderInFlight(
                request=o.request,
                broker_order_id=o.broker_order_id,
                reference_bar=bars[o.decided_on],  # type: ignore[arg-type]
            )
            for o in orders
        ],
    )
    driver = _driver(
        db,
        broker,
        portfolio_id=pid,
        resume_from=state,
        ladder=load_ladder_state(db, pid),
        pending_stops={
            o.request.instrument_id: o.intended_stop for o in orders if o.intended_stop is not None
        },
    )
    return driver, broker


def test_an_entry_in_flight_at_a_restart_fills_after_it(db_session: Session) -> None:
    """**The feature.** Cut at the close the entry was decided on.

    Uninterrupted, the entry fills on the next session. Restarted, it must fill
    on the same session at the same price, with the stop it was decided with.
    """
    bars = {day: _bar(day, 100 + i) for i, day in enumerate(DAYS[:2])}

    whole = _driver(db_session, _venue(), portfolio_id=99)
    whole.run_session(DAYS[0], {1: bars[DAYS[0]]}, [_entry(DAYS[0])])  # type: ignore[list-item]
    whole.run_session(DAYS[1], {1: bars[DAYS[1]]}, [])
    (uninterrupted,) = whole.state().open_positions

    pid = _portfolio(db_session)
    broker = _venue()
    first = _driver(db_session, broker, portfolio_id=pid)
    first.run_session(DAYS[0], {1: bars[DAYS[0]]}, [_entry(DAYS[0])])  # type: ignore[list-item]
    assert first.state().position_count == 0, "decided, not yet filled: that is the cut"
    _close(db_session, pid, first, broker, DAYS[0])

    second, _ = _restart(db_session, pid, bars)
    outcome = second.run_session(DAYS[1], {1: bars[DAYS[1]]}, [])

    assert outcome.discrepancies == 0
    assert len(outcome.fills) == 1, "the order in flight was dropped at the restart"
    (resumed,) = second.state().open_positions
    assert resumed.quantity == uninterrupted.quantity
    assert resumed.average_entry_price == uninterrupted.average_entry_price
    assert resumed.initial_stop_price == uninterrupted.initial_stop_price == DECIDED_STOP
    assert second.state().cash == whole.state().cash


def test_the_restored_order_is_priced_against_the_bar_it_was_decided_on(
    db_session: Session,
) -> None:
    """The reference bar sets the fill price, so it is not optional."""
    bars = {DAYS[0]: _bar(DAYS[0], 100), DAYS[1]: _bar(DAYS[1], 130)}
    pid = _portfolio(db_session)
    broker = _venue()
    first = _driver(db_session, broker, portfolio_id=pid)
    first.run_session(DAYS[0], {1: bars[DAYS[0]]}, [_entry(DAYS[0])])  # type: ignore[list-item]
    _close(db_session, pid, first, broker, DAYS[0])

    (order,) = load_open_orders(db_session, pid)
    fills = {}
    for name, reference in (("decided", bars[DAYS[0]]), ("filling", bars[DAYS[1]])):
        venue = _venue()
        venue.restore(
            cash=CAPITAL,
            holdings={},
            orders=[OrderInFlight(order.request, order.broker_order_id, reference)],  # type: ignore[arg-type]
        )
        (fills[name],) = venue.advance(DAYS[1], {1: bars[DAYS[1]]})
    assert fills["decided"].price != fills["filling"].price


def test_each_order_is_written_once_and_its_status_kept_current(db_session: Session) -> None:
    pid = _portfolio(db_session)
    broker = _venue()
    driver = _driver(db_session, broker, portfolio_id=pid)
    driver.run_session(DAYS[0], {1: _bar(DAYS[0], 100)}, [_entry(DAYS[0])])  # type: ignore[list-item]
    _close(db_session, pid, driver, broker, DAYS[0])
    driver.run_session(DAYS[1], {1: _bar(DAYS[1], 100)}, [])
    sync_orders(
        db_session,
        pid,
        broker.order_history(),
        driver.intended_stops(),
        decided_at=dt.datetime.combine(DAYS[1], dt.time(20), tzinfo=dt.UTC),
    )

    (row,) = db_session.execute(select(Order).where(Order.portfolio_id == pid)).scalars()
    assert row.status == str(OrderStatus.FILLED)
    assert row.intended_stop_price == DECIDED_STOP
    assert row.submitted_at is not None and row.submitted_at.date() == DAYS[0]
    assert load_open_orders(db_session, pid) == ()


def test_an_entry_with_no_intended_stop_is_refused_rather_than_written(
    db_session: Session,
) -> None:
    pid = _portfolio(db_session)
    broker = _venue()
    driver = _driver(db_session, broker, portfolio_id=pid)
    driver.run_session(DAYS[0], {1: _bar(DAYS[0], 100)}, [_entry(DAYS[0])])  # type: ignore[list-item]

    with pytest.raises(ValueError, match="no intended stop"):
        sync_orders(
            db_session,
            pid,
            broker.order_history(),
            {},
            decided_at=dt.datetime.combine(DAYS[0], dt.time(20), tzinfo=dt.UTC),
        )


def test_a_resting_order_already_part_filled_is_refused(db_session: Session) -> None:
    pid = _portfolio(db_session)
    db_session.add(
        Order(
            portfolio_id=pid,
            instrument_id=1,
            client_order_id="e-1-x",
            broker_order_id="t-000001",
            side="buy",
            order_type="market",
            time_in_force="day",
            status="new",
            quantity=Decimal(10),
            filled_quantity=Decimal(4),
            submitted_at=dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC),
        )
    )
    db_session.flush()

    with pytest.raises(ValueError, match="buy the filled part twice"):
        load_open_orders(db_session, pid)


def _request(n: int) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"x-{n}",
        portfolio_id=1,
        instrument_id=n,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal(1),
    )


def test_restored_orders_keep_their_ids_and_new_ones_continue_after_them() -> None:
    venue = _venue()
    venue.restore(
        cash=CAPITAL,
        holdings={1: (Decimal(5), Decimal(100)), 2: (Decimal(5), Decimal(100))},
        orders=[
            OrderInFlight(_request(2), "t-000007", None),
            OrderInFlight(_request(1), "t-000003", None),
        ],
    )
    new = venue.submit_order(_request(9), "x-9")

    ids = [status.broker_order_id for _, status in venue.order_history()]
    assert ids == ["t-000003", "t-000007", "t-000008"]
    assert new.broker_order_id == "t-000008"


def test_another_accounts_order_is_not_restored() -> None:
    with pytest.raises(ValueError, match="not issued by account"):
        _venue().restore(
            cash=CAPITAL, holdings={}, orders=[OrderInFlight(_request(1), "other-000001", None)]
        )


def test_pending_stops_without_a_book_to_resume_are_refused(db_session: Session) -> None:
    driver = _driver(db_session, _venue())
    with pytest.raises(ValueError, match="no restart without a book"):
        type(driver)(
            cycle=driver.cycle,
            service=driver.service,
            broker=driver.broker,
            pending_stops={1: Decimal(90)},
        )
