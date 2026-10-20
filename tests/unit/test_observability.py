"""The system explaining what it did.

> A live trading system that cannot explain what it did is not acceptable.

The audit of 2026-10-19 found four of the chain's seven links with no writer at
all: `Order`, `Execution`, `Trade` and `OpportunityScore` were declared in the
schema and constructed nowhere else in `src/`. These tests cover the writers and
the one property that makes a reconstruction worth having — **that it names what
was not recorded, rather than letting an unlogged session look like a quiet
one.**
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tradeit.core.enums import OrderSide, OrderStatus, OrderType
from tradeit.execution.base import Fill, OrderRequest
from tradeit.execution.journal import JournalEntryKind, record_entry
from tradeit.observability.record import (
    reconstruct,
    record_execution,
    record_order,
    record_score,
    record_trade,
)
from tradeit.storage.tables import Execution, OpportunityScore, Order, Trade

SESSION = dt.date(2015, 6, 1)
FILLED_AT = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


def _request(client_order_id: str = "e-1-2015-06-01") -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        portfolio_id=1,
        instrument_id=7,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(100),
        intent="ma cross up",
    )


def _fill(quantity: int = 100, price: str = "101.25") -> Fill:
    return Fill(
        order_id=1,
        instrument_id=7,
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_at=FILLED_AT,
        commission=Decimal("1.00"),
        slippage=Decimal("0.05"),
        venue="paper",
    )


def test_a_score_is_recorded_with_the_breakdown_that_explains_it(
    db_session: Session,
) -> None:
    record_score(
        db_session,
        instrument_id=7,
        session_date=SESSION,
        direction="long",
        total=0.42,
        components={"ma_spread": 0.42},
        run_manifest_id=1,
        rank=3,
    )
    db_session.flush()
    row = db_session.query(OpportunityScore).one()
    assert row.total == 0.42
    assert row.components == {"ma_spread": 0.42}
    assert row.rank == 3


def test_a_score_with_no_components_is_refused(db_session: Session) -> None:
    """`OpportunityScore.reconciles` exists because an explanation reconstructed
    from a total is a rationalisation. Persisting a componentless score would
    put that failure beyond the property's reach."""
    with pytest.raises(ValueError, match="needs its components"):
        record_score(
            db_session,
            instrument_id=7,
            session_date=SESSION,
            direction="long",
            total=0.42,
            components={},
            run_manifest_id=1,
        )


def test_an_order_row_cannot_disagree_with_the_request_that_was_sent(
    db_session: Session,
) -> None:
    """Takes the OrderRequest rather than loose fields, so the two cannot drift."""
    request = _request()
    record_order(
        db_session, request, submitted_at=FILLED_AT, status=OrderStatus.NEW, broker_order_id="b-1"
    )
    db_session.flush()
    row = db_session.query(Order).one()
    assert row.client_order_id == request.client_order_id
    assert row.quantity == request.quantity
    assert row.intent == "ma cross up"
    assert row.status == "new"


def test_an_empty_intent_is_stored_as_null_not_as_an_empty_string(
    db_session: Session,
) -> None:
    """Two spellings of "no intent" cannot be grouped in a query."""
    request = OrderRequest(
        client_order_id="x-1",
        portfolio_id=1,
        instrument_id=7,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal(10),
    )
    record_order(db_session, request, submitted_at=FILLED_AT)
    db_session.flush()
    assert db_session.query(Order).one().intent is None


def test_the_unique_client_order_id_is_enforced_by_the_database(
    db_session: Session,
) -> None:
    """The column is unique=True, which is why the engine's ids had to carry
    their session -- see PHASE_11_AUDIT_2026-10-18."""
    record_order(db_session, _request(), submitted_at=FILLED_AT)
    db_session.flush()
    record_order(db_session, _request(), submitted_at=FILLED_AT)
    with pytest.raises(Exception):
        db_session.flush()


def test_an_execution_is_tied_to_the_order_by_database_id(db_session: Session) -> None:
    """Not the broker's string: a chain tied by a vendor identifier breaks the
    moment a second venue uses the same format."""
    order = record_order(db_session, _request(), submitted_at=FILLED_AT)
    db_session.flush()
    record_execution(
        db_session,
        _fill(),
        order_id=order.id,
        portfolio_id=1,
        session_date=SESSION,
    )
    db_session.flush()
    row = db_session.query(Execution).one()
    assert row.order_id == order.id
    assert row.price == Decimal("101.25")
    assert row.commission == Decimal("1.00")
    assert row.venue == "paper"


def test_partial_fills_are_separate_rows(db_session: Session) -> None:
    """`Fill`'s own requirement: the average price of a position built across
    three fills at three prices is only reconstructible if all three were kept."""
    order = record_order(db_session, _request(), submitted_at=FILLED_AT)
    db_session.flush()
    for quantity, price in ((40, "101.00"), (35, "101.50"), (25, "102.00")):
        record_execution(
            db_session,
            _fill(quantity=quantity, price=price),
            order_id=order.id,
            portfolio_id=1,
            session_date=SESSION,
        )
    db_session.flush()
    rows = db_session.query(Execution).all()
    assert len(rows) == 3
    assert sum(row.quantity for row in rows) == Decimal(100)


def _trade_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "portfolio_id": 1,
        "position_id": 1,
        "instrument_id": 7,
        "side": "long",
        "quantity": Decimal(100),
        "entry_date": SESSION,
        "entry_price": Decimal(100),
        "exit_date": dt.date(2015, 7, 1),
        "exit_price": Decimal(110),
        "exit_reason": "time_stop",
        "gross_pnl": Decimal(1000),
        "costs": Decimal(10),
        "net_pnl": Decimal(990),
        "return_pct": 0.099,
        "holding_sessions": 21,
    }
    base.update(overrides)
    return base


def test_a_trade_records_the_pnl_and_the_score_it_came_from(db_session: Session) -> None:
    record_trade(db_session, **_trade_kwargs(score_at_entry=0.42))  # type: ignore[arg-type]
    db_session.flush()
    row = db_session.query(Trade).one()
    assert row.net_pnl == Decimal(990)
    assert row.score_at_entry == 0.42


def test_a_pnl_row_whose_parts_do_not_add_up_is_refused(db_session: Session) -> None:
    """The arithmetic is the one thing a reader trusts without looking."""
    with pytest.raises(ValueError, match="do not add up"):
        record_trade(db_session, **_trade_kwargs(net_pnl=Decimal(500)))  # type: ignore[arg-type]


def test_a_negative_holding_period_is_refused(db_session: Session) -> None:
    """It reaches a report as a plausible-looking outlier."""
    with pytest.raises(ValueError, match="precedes entry"):
        record_trade(
            db_session,
            **_trade_kwargs(exit_date=dt.date(2015, 5, 1)),  # type: ignore[arg-type]
        )


def test_nothing_commits_so_the_record_cannot_outlive_a_rollback(
    db_session: Session,
) -> None:
    """A chain that survived a rollback would describe orders never placed --
    worse than none, because it is confidently wrong."""
    record_order(db_session, _request(), submitted_at=FILLED_AT)
    record_score(
        db_session,
        instrument_id=7,
        session_date=SESSION,
        direction="long",
        total=0.1,
        components={"a": 0.1},
        run_manifest_id=1,
    )
    db_session.rollback()
    assert db_session.query(Order).count() == 0
    assert db_session.query(OpportunityScore).count() == 0


def test_reconstruct_names_the_links_that_hold_nothing(db_session: Session) -> None:
    """The property that makes the whole module worth having.

    A reconstruction that cannot distinguish "nothing happened" from "nothing
    was recorded" would let an unlogged live session look like a quiet one,
    which is the most dangerous confusion available here.
    """
    chain = reconstruct(db_session, portfolio_id=1, session_date=SESSION)
    assert not chain.complete
    assert set(chain.missing) == {"scores", "orders", "executions", "trades", "journal"}
    assert "NOT RECORDED" in chain.describe()
    assert "different fact from nothing having happened" in chain.describe()


def test_a_fully_recorded_session_walks_end_to_end(db_session: Session) -> None:
    order = record_order(db_session, _request(), submitted_at=FILLED_AT)
    db_session.flush()
    record_score(
        db_session,
        instrument_id=7,
        session_date=SESSION,
        direction="long",
        total=0.42,
        components={"ma_spread": 0.42},
        run_manifest_id=1,
    )
    record_execution(db_session, _fill(), order_id=order.id, portfolio_id=1, session_date=SESSION)
    record_trade(
        db_session,
        **_trade_kwargs(exit_date=SESSION),  # type: ignore[arg-type]
    )
    record_entry(
        db_session,
        kind=JournalEntryKind.ENTRY,
        session_date=SESSION,
        portfolio_id=1,
        summary="bought 100 of instrument 7",
    )
    db_session.flush()

    chain = reconstruct(db_session, portfolio_id=1, session_date=SESSION)
    assert chain.complete, chain.describe()
    assert len(chain.scores) == 1
    assert len(chain.orders) == 1
    assert len(chain.executions) == 1
    assert len(chain.trades) == 1
    assert len(chain.journal) == 1


def test_a_partial_chain_reports_exactly_which_link_is_absent(
    db_session: Session,
) -> None:
    """The informative case: orders exist, executions do not. That is a real
    operational state -- orders placed and nothing filled -- and it must read
    differently from orders never having been written."""
    record_order(db_session, _request(), submitted_at=FILLED_AT)
    db_session.flush()
    chain = reconstruct(db_session, portfolio_id=1, session_date=SESSION)
    assert "orders" not in chain.missing
    assert "executions" in chain.missing


def test_only_this_module_writes_the_four_tables() -> None:
    """The single-writer discipline, as the journal has.

    Four tables had no writer at all until today. If a second one appears, that
    is a decision somebody should make deliberately -- the fix is to route
    through these functions, not to extend the allow-list.
    """
    root = Path(__file__).resolve().parents[2] / "src" / "tradeit"
    allowed = {root / "observability" / "record.py", root / "storage" / "tables.py"}
    offenders: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if path in allowed:
            continue
        text = path.read_text()
        if "from tradeit.storage.tables import" not in text:
            continue
        for name in ("Order(", "Execution(", "Trade(", "OpportunityScore("):
            if name in text and name.rstrip("(") in text.split("import", 1)[1][:4000]:
                offenders.append((str(path.relative_to(root)), name.rstrip("(")))
    assert offenders == [], f"written outside the observability writer: {offenders}"


def test_executions_are_scoped_to_this_portfolios_orders(db_session: Session) -> None:
    """Another portfolio's fills must not appear in this one's chain.

    Found by a mutant that broadened the execution filter and passed every
    other test in this file: they only ever hold executions belonging to the
    order under test, so the scoping was never exercised.
    """
    mine = record_order(db_session, _request("mine-1"), submitted_at=FILLED_AT)
    theirs = Order(
        portfolio_id=99,
        instrument_id=7,
        client_order_id="theirs-1",
        side="buy",
        order_type="market",
        time_in_force="day",
        status="new",
        quantity=Decimal(50),
    )
    db_session.add(theirs)
    db_session.flush()

    record_execution(db_session, _fill(), order_id=mine.id, portfolio_id=1, session_date=SESSION)
    record_execution(
        db_session,
        _fill(quantity=50),
        order_id=theirs.id,
        portfolio_id=99,
        session_date=SESSION,
    )
    db_session.flush()

    chain = reconstruct(db_session, portfolio_id=1, session_date=SESSION)
    assert len(chain.orders) == 1
    assert len(chain.executions) == 1, (
        "another portfolio's execution appeared in this portfolio's chain"
    )
    assert chain.executions[0].order_id == mine.id


def test_importing_the_engine_pulls_in_no_database_machinery() -> None:
    """The seam's whole purpose, asserted rather than trusted.

    `EventDrivenEngine` takes a `DecisionRecorder` so a run's decisions can be
    persisted without the engine knowing a database exists. That property is
    invisible at the call site that breaks it: `base` was split out of `record`
    to protect it, and the split achieved **nothing** at first, because a
    submodule import executes the package `__init__` and that file re-exported
    `record`. Importing `observability.base` therefore still dragged in 150
    SQLAlchemy modules.

    Measured in a subprocess because `sys.modules` is process-wide: by the time
    this file's other tests have run, SQLAlchemy is loaded regardless of who
    asked for it, and an in-process check would pass whatever the engine
    imports.
    """
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    probe = (
        f"import sys; sys.path.insert(0, {str(src)!r});"
        "import tradeit.backtesting.engine;"
        "print(len([m for m in sys.modules if m.startswith('sqlalchemy')]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    count = int(result.stdout.strip())
    assert count == 0, (
        f"importing the engine pulled in {count} sqlalchemy modules; the recorder seam "
        "has been re-coupled to storage, most likely by a convenience re-export in "
        "tradeit/observability/__init__.py"
    )


def test_a_fill_with_no_recorded_order_is_refused(db_session: Session) -> None:
    """An execution that cannot be traced to its order defeats the chain."""
    from tradeit.observability.base import RecordedDecision
    from tradeit.observability.record import DatabaseRecorder

    recorder = DatabaseRecorder(session=db_session, run_manifest_id=1)
    with pytest.raises(ValueError, match="defeats the chain"):
        recorder.record(
            RecordedDecision(
                session_date=SESSION,
                portfolio_id=1,
                fills=(("never-placed", _fill()),),
            )
        )


def test_the_database_recorder_writes_the_whole_session(db_session: Session) -> None:
    from tradeit.observability.base import RecordedDecision
    from tradeit.observability.record import DatabaseRecorder

    recorder = DatabaseRecorder(session=db_session, run_manifest_id=1)
    request = _request()
    recorder.record(
        RecordedDecision(
            session_date=SESSION,
            portfolio_id=1,
            scores=((7, "long", 0.42, {"ma_spread": 0.42}),),
            orders=(request,),
            fills=((request.client_order_id, _fill()),),
            refused=((9, "portfolio_heat"),),
        )
    )
    db_session.flush()

    chain = reconstruct(db_session, portfolio_id=1, session_date=SESSION)
    assert len(chain.scores) == 1
    assert len(chain.orders) == 1
    assert len(chain.executions) == 1
    assert len(chain.journal) == 1, "the refusal was not journalled"
    assert chain.journal[0].entry_type == "declined"


def test_the_null_recorder_is_the_engines_default() -> None:
    """A backtest over scripted bars should write nothing, and most runs are that."""
    from tradeit.backtesting.engine import EventDrivenEngine
    from tradeit.observability.base import NullRecorder

    field = next(f for f in EventDrivenEngine.__dataclass_fields__.values() if f.name == "recorder")
    assert field.default_factory is NullRecorder  # type: ignore[misc]


def test_orders_are_scoped_to_the_session_they_were_placed_on(
    db_session: Session,
) -> None:
    """The defect that made a first real run report an impossible number.

    `orders` has no `session_date` column, so `reconstruct` filtered on
    portfolio alone and returned every order the portfolio had ever placed. On
    504 real sessions it reported **189 orders on a single session** and "504 of
    504 sessions can be walked end to end" -- the second being true only because
    the first was wrong. The fix is two parts: the recorder stamps
    `submitted_at` from the session, and the query bounds on it.
    """
    from tradeit.observability.base import RecordedDecision
    from tradeit.observability.record import DatabaseRecorder

    recorder = DatabaseRecorder(session=db_session, run_manifest_id=1)
    monday, tuesday = dt.date(2015, 6, 1), dt.date(2015, 6, 2)
    recorder.record(
        RecordedDecision(session_date=monday, portfolio_id=1, orders=(_request("mon-1"),))
    )
    recorder.record(
        RecordedDecision(
            session_date=tuesday,
            portfolio_id=1,
            orders=(_request("tue-1"), _request("tue-2")),
        )
    )
    db_session.flush()

    assert db_session.query(Order).count() == 3
    assert [o.client_order_id for o in reconstruct(db_session, 1, monday).orders] == ["mon-1"]
    assert sorted(o.client_order_id for o in reconstruct(db_session, 1, tuesday).orders) == [
        "tue-1",
        "tue-2",
    ]


def test_submitted_at_is_never_left_null_by_the_recorder(db_session: Session) -> None:
    """Because it is the only thing tying an order to its session."""
    from tradeit.observability.base import RecordedDecision
    from tradeit.observability.record import DatabaseRecorder

    DatabaseRecorder(session=db_session, run_manifest_id=1).record(
        RecordedDecision(session_date=SESSION, portfolio_id=1, orders=(_request(),))
    )
    db_session.flush()
    assert db_session.query(Order).one().submitted_at is not None


def test_a_fill_settling_a_later_session_finds_its_earlier_order(
    db_session: Session,
) -> None:
    """The ordinary case, and the one a single-call map cannot serve.

    The engine settles yesterday's decisions against today's prices, so by the
    time an execution exists its order was written by a previous `record` call.
    A first version looked only at the current call's map and would have refused
    every real execution while passing its own unit test, where order and fill
    are handed over together.
    """
    from tradeit.observability.base import RecordedDecision
    from tradeit.observability.record import DatabaseRecorder

    recorder = DatabaseRecorder(session=db_session, run_manifest_id=1)
    request = _request("placed-monday")
    recorder.record(
        RecordedDecision(session_date=dt.date(2015, 6, 1), portfolio_id=1, orders=(request,))
    )
    db_session.flush()
    recorder.record(
        RecordedDecision(
            session_date=dt.date(2015, 6, 2),
            portfolio_id=1,
            fills=((request.client_order_id, _fill()),),
        )
    )
    db_session.flush()

    row = db_session.query(Execution).one()
    assert row.order_id == db_session.query(Order).one().id
