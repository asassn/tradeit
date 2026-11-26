"""Planning from a recorded book rather than a hand-edited declaration.

The record is written by ``scripts/paper_live_shape.py --record`` at every
close. These tests write one the same way — with the same writers — into a
separate SQLite file, then plan the next session from it.

The property that carries the file is **the plan advances the book by exactly
one session, the way the driver does**: `LiveShapedSession.run_session`
increments ``sessions_held`` and raises the high to the close before deciding.
The two time-stop tests pin the increment from both sides, because a mutant
that dropped the ``+ 1`` would pass either one alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.unit.test_decide_runner import AS_OF, WARM, _bars, _declared, _listed, _weekdays
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.cli_decide import _from_flags
from tradeit.core.enums import PositionStatus, Side, TradingMode
from tradeit.decide import (
    IN_FLIGHT_CAVEAT,
    BookRef,
    DeclarationError,
    load_declaration,
    plan_declared,
)
from tradeit.decide.book import read_book
from tradeit.decide.runner import _from_book
from tradeit.execution.live_shape import LadderState
from tradeit.observability.portfolio_record import (
    record_portfolio,
    record_portfolio_snapshot,
    record_position,
)
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.storage.tables import Base, Order

LAST = dt.date(2024, 2, 29)  # the weekday before AS_OF
NAME = "paper-swing"


@pytest.fixture
def loud(db_session: Session) -> int:
    """One name printing 100 on every weekday through AS_OF."""
    sid = _listed(db_session, "LOUD")
    _bars(db_session, sid, _weekdays(WARM, AS_OF), close="100")
    return sid


def _record(
    path: Path,
    instrument_id: int | None,
    *,
    entry: str = "100",
    stop: str = "92",
    sessions_held: int = 10,
    highest: str | None = "104",
    partial: bool = False,
    cash: str = "90000",
    last: dt.date = LAST,
    snapshot: bool = True,
) -> int:
    """Write a book exactly as the paper session's writers would."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as record:
        row = record_portfolio(
            record,
            name=NAME,
            mode=TradingMode.PAPER,
            initial_capital=Decimal(100_000),
            inception_date=WARM,
        )
        positions: tuple[PositionState, ...] = ()
        if instrument_id is not None:
            position = PositionState(
                position_id=0,
                portfolio_id=int(row.id),
                instrument_id=instrument_id,
                side=Side.LONG,
                status=PositionStatus.OPEN,
                quantity=Decimal(100),
                average_entry_price=Decimal(entry),
                stop_price=Decimal(stop),
                opened_on=WARM,
                initial_stop_price=Decimal(stop),
            )
            record_position(
                record,
                position,
                highest_since_entry=None if highest is None else Decimal(highest),
                sessions_held=None if highest is None else sessions_held,
                partial_profit_taken=None if highest is None else partial,
            )
            positions = (position,)
        if snapshot:
            record_portfolio_snapshot(
                record,
                PortfolioState(
                    portfolio_id=int(row.id),
                    as_of=dt.datetime.combine(last, dt.time(20), tzinfo=dt.UTC),
                    cash=Decimal(cash),
                    equity=Decimal(cash) + Decimal(100) * Decimal(entry),
                    positions=positions,
                ),
                session_date=last,
                peak_equity=Decimal(101_000),
            )
        record.commit()
        return int(row.id)


def _book(path: Path, **overrides: object) -> object:
    payload: dict[str, object] = {
        "equity": None,
        "cash": None,
        "book": BookRef(record=path, portfolio=NAME),
        "universe": ("LOUD",),
    }
    payload.update(overrides)
    return _declared(**payload)


# -- the book is what the plan holds ----------------------------------------


def test_the_plan_holds_what_the_record_holds(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    portfolio_id = _record(record, loud)

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    (held,) = result.plan.holdings
    assert held.reference == "LOUD"
    assert held.quantity == Decimal(100)
    assert held.stop_price == Decimal(92)
    assert result.book is not None
    assert result.book.portfolio_id == portfolio_id
    assert result.book.last_session == LAST
    assert result.book.peak_equity == Decimal(101_000)


def test_equity_is_marked_at_the_planned_close_as_the_driver_marks_it(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """Recorded at entry 100; marked at today's 100. Distinguished by a 90 entry."""
    record = tmp_path / "book.sqlite"
    _record(record, loud, entry="90", stop="80")

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert result.book is not None
    assert result.book.recorded_equity == Decimal(90_000) + Decimal(9_000)
    assert result.book.marked_equity == Decimal(90_000) + Decimal(10_000)
    assert result.plan.equity == result.book.marked_equity


def test_the_time_stop_fires_on_the_session_the_driver_would_fire_it(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """Recorded 39 at the last close; the planned session is the 40th."""
    record = tmp_path / "book.sqlite"
    _record(record, loud, sessions_held=39)

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert [e.reason for e in result.plan.exits] == ["time_stop"]


def test_the_time_stop_does_not_fire_a_session_early(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud, sessions_held=38)

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert result.plan.exits == ()


def test_a_partial_profit_already_taken_is_not_taken_again(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """At 6R the ladder takes a partial — unless the record says it already did."""
    taken, fresh = tmp_path / "taken.sqlite", tmp_path / "fresh.sqlite"
    _record(taken, loud, entry="40", stop="30", partial=True)
    _record(fresh, loud, entry="40", stop="30", partial=False)

    assert plan_declared(db_session, _book(taken)).plan.exits == ()  # type: ignore[arg-type]
    exits = plan_declared(db_session, _book(fresh)).plan.exits  # type: ignore[arg-type]
    assert [e.reason for e in exits] == ["partial_profit"]


def test_the_high_is_raised_to_the_close_not_reset_to_it(tmp_path: Path) -> None:
    """The recorded high wins when higher; the close wins when it is above it."""
    position = PositionState(
        position_id=1,
        portfolio_id=7,
        instrument_id=5,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(1),
        average_entry_price=Decimal(100),
        stop_price=Decimal(92),
        opened_on=WARM,
        initial_stop_price=Decimal(92),
    )
    state = PortfolioState(
        portfolio_id=7,
        as_of=dt.datetime(2024, 2, 29, 20, tzinfo=dt.UTC),
        cash=Decimal(0),
        equity=Decimal(100),
        positions=(position,),
    )

    def high(recorded: str, close: str) -> Decimal:
        book = type(
            "B",
            (),
            {
                "portfolio_id": 7,
                "state": state,
                "ladder": {5: LadderState(Decimal(recorded), 3, False)},
            },
        )()
        _, contexts = _from_book(_declared(), book, {5: Decimal(close)})  # type: ignore[arg-type]
        return contexts[5].high_since_entry

    assert high("120", "110") == Decimal(120)
    assert high("105", "110") == Decimal(110)


def test_every_plan_from_a_book_says_what_the_record_cannot_see(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud)

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert IN_FLIGHT_CAVEAT in result.plan.caveats


def test_a_declared_plan_does_not_carry_the_book_caveat(db_session: Session, loud: int) -> None:
    result = plan_declared(db_session, _declared(universe=("LOUD",)))

    assert result.book is None
    assert IN_FLIGHT_CAVEAT not in result.plan.caveats


# -- refusals: each would otherwise plan a book that does not exist ---------


def test_a_missing_record_is_refused_and_not_created(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "typo.sqlite"

    with pytest.raises(DeclarationError, match="no record at"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]
    assert not record.exists(), "SQLite creates on connect; the check must come first"


def test_the_record_is_opened_read_only(db_session: Session, loud: int, tmp_path: Path) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud)
    before = record.read_bytes()

    plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert record.read_bytes() == before


def test_an_unknown_portfolio_name_is_refused(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud)

    with pytest.raises(DeclarationError, match="no portfolio named"):
        plan_declared(
            db_session,
            _book(record, book=BookRef(record=record, portfolio="paper-swimg")),  # type: ignore[arg-type]
        )


def test_a_book_never_recorded_at_a_close_is_not_an_empty_book(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, None, snapshot=False)

    with pytest.raises(DeclarationError, match="never been recorded"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_an_empty_book_that_was_recorded_plans_as_empty(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """The counterpart: holding nothing is a legitimate state to plan from."""
    record = tmp_path / "book.sqlite"
    _record(record, None)

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert result.plan.holdings == ()
    assert result.book is not None and result.book.positions == 0


def test_a_position_with_no_recorded_ladder_is_refused(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud, highest=None)

    with pytest.raises(DeclarationError, match="no high since entry"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_a_session_already_decided_is_not_planned_again(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud, last=AS_OF)

    with pytest.raises(DeclarationError, match="already been decided"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_skipped_sessions_are_refused_and_the_right_one_is_named(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    record = tmp_path / "book.sqlite"
    _record(record, loud, last=dt.date(2024, 2, 27))

    with pytest.raises(DeclarationError, match=r"2 session\(s\).*Plan 2024-02-28"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_a_warmup_that_starts_after_the_book_is_refused(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """Otherwise the skipped sessions would fall outside the window and go unseen."""
    record = tmp_path / "book.sqlite"
    _record(record, loud, last=dt.date(2023, 12, 29))

    with pytest.raises(DeclarationError, match="starts after the book"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_a_holding_outside_the_universe_is_refused(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    other = _listed(db_session, "OTHER")
    _bars(db_session, other, _weekdays(WARM, AS_OF), close="50")
    record = tmp_path / "book.sqlite"
    _record(record, other)

    with pytest.raises(DeclarationError, match=f"instrument {other}"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_a_split_on_the_planned_session_is_refused(
    db_session: Session, loud: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-split units marked at a post-split close would read as a collapse."""
    record = tmp_path / "book.sqlite"
    _record(record, loud)
    real = CorpusSessionData.splits_on
    monkeypatch.setattr(
        CorpusSessionData,
        "splits_on",
        lambda self, day: {loud: Decimal(2)} if day == AS_OF else real(self, day),
    )

    with pytest.raises(DeclarationError, match="split on"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_read_book_refuses_an_open_position_with_no_initial_stop(tmp_path: Path) -> None:
    """The initial stop defines R, and the schema does not require it.

    ``ck_position_open_has_stop`` already forbids an open row with no *current*
    stop, so that half is the database's. The initial stop is nullable, and a
    position without one has no R: a partial-profit trigger at 2R would be
    measured against nothing.
    """
    record = tmp_path / "book.sqlite"
    _record(record, 5)
    engine = create_engine(f"sqlite:///{record}")
    with engine.begin() as connection:
        connection.exec_driver_sql("update positions set initial_stop_price = null")

    with sessionmaker(bind=engine)() as session, pytest.raises(DeclarationError, match="no stop"):
        read_book(session, NAME)


# -- one source per portfolio, in both forms --------------------------------


def _write(tmp_path: Path, entry: dict[str, object]) -> Path:
    base: dict[str, object] = {
        "mandate": "swing",
        "universe": ["LOUD"],
        "warmup_from": "2024-01-01",
        "as_of": "2024-03-01",
        "score_range": {"lower": 0, "upper": 0.5},
    }
    base.update(entry)
    path = tmp_path / "decl" / "portfolios.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"portfolios": [base]}))
    return path


def test_a_relative_record_is_read_beside_the_declaration(tmp_path: Path) -> None:
    """The CLI and the service run from different directories; the file must not."""
    path = _write(tmp_path, {"book": {"record": "book.sqlite", "portfolio": NAME}})

    (declared,) = load_declaration(path)

    assert declared.book == BookRef(record=path.parent / "book.sqlite", portfolio=NAME)
    assert declared.equity is None and declared.cash is None


@pytest.mark.parametrize("stated", ["equity", "cash", "holdings"])
def test_a_book_beside_stated_holdings_is_refused(tmp_path: Path, stated: str) -> None:
    value: object = [] if stated == "holdings" else 1000
    path = _write(tmp_path, {"book": {"record": "b.sqlite", "portfolio": NAME}, stated: value})

    with pytest.raises(DeclarationError, match=f"{stated} stated beside a recorded book"):
        load_declaration(path)


def test_a_book_needs_a_portfolio_name(tmp_path: Path) -> None:
    path = _write(tmp_path, {"book": {"record": "b.sqlite"}})

    with pytest.raises(DeclarationError, match="needs both record"):
        load_declaration(path)


def test_without_a_book_equity_is_still_required(tmp_path: Path) -> None:
    path = _write(tmp_path, {"cash": 1000})

    with pytest.raises(DeclarationError, match="equity is required"):
        load_declaration(path)


def _args(**overrides: object) -> argparse.Namespace:
    payload: dict[str, object] = {
        "tickers": "LOUD",
        "mandate": "swing",
        "start": "2024-01-01",
        "as_of": "2024-03-01",
        "equity": None,
        "cash": None,
        "hold": None,
        "book": None,
        "portfolio": None,
        "score_lower": 0.0,
        "score_upper": 0.5,
        "fast": 50,
        "slow": 200,
        "stop_pct": "0.08",
    }
    payload.update(overrides)
    return argparse.Namespace(**payload)


def test_the_flags_name_a_book() -> None:
    declared = _from_flags(_args(book="b.sqlite", portfolio=NAME))

    assert declared.book == BookRef(record=Path("b.sqlite"), portfolio=NAME)
    assert declared.equity is None


def test_the_flags_refuse_a_book_without_its_portfolio() -> None:
    with pytest.raises(DeclarationError, match="go together"):
        _from_flags(_args(book="b.sqlite"))


def test_the_flags_refuse_stated_cash_beside_a_book() -> None:
    with pytest.raises(DeclarationError, match="--cash stated beside --book"):
        _from_flags(_args(book="b.sqlite", portfolio=NAME, cash="5"))


def test_the_flags_keep_their_old_defaults_without_a_book() -> None:
    declared = _from_flags(_args())

    assert declared.equity == Decimal(100_000) and declared.cash == Decimal(100_000)


# -- orders in flight, now that the record holds them -----------------------


def _order(path: Path, portfolio_id: int, instrument_id: int, *, status: str) -> None:
    engine = create_engine(f"sqlite:///{path}")
    with sessionmaker(bind=engine)() as record:
        record.add(
            Order(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                client_order_id=f"x-{instrument_id}-{LAST}",
                broker_order_id="live-shape-000001",
                side="sell",
                order_type="market",
                time_in_force="day",
                status=status,
                quantity=Decimal(100),
                submitted_at=dt.datetime.combine(LAST, dt.time(20), tzinfo=dt.UTC),
            )
        )
        record.commit()


def test_an_order_in_flight_refuses_the_plan_and_is_named(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """It fills before the session decides; planning over it would sell twice."""
    record = tmp_path / "book.sqlite"
    pid = _record(record, loud)
    _order(record, pid, loud, status="new")

    with pytest.raises(DeclarationError, match=r"1 order\(s\) in flight.*sell 100"):
        plan_declared(db_session, _book(record))  # type: ignore[arg-type]


def test_a_record_that_holds_orders_needs_no_blanket_caveat(
    db_session: Session, loud: int, tmp_path: Path
) -> None:
    """Every order finished, so "nothing in flight" is now a fact, not a hope."""
    record = tmp_path / "book.sqlite"
    pid = _record(record, loud)
    _order(record, pid, loud, status="filled")

    result = plan_declared(db_session, _book(record))  # type: ignore[arg-type]

    assert IN_FLIGHT_CAVEAT not in result.plan.caveats
