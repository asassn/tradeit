"""A paper session stopped and continued against a real record.

Phase 11 closed on *equivalence*: 1,457 of 1,457 replayed fills agreed. That
is a claim about arithmetic, and it says nothing about operation -- until
2026-11-24 a paper session started flat in memory every time, so a ten-year run
was one process or it was nothing.

`test_execution_live_shape.py` holds the resume path against a hand-built
`PortfolioState`. **This file holds the round trip**: a driver runs, its book
is written to the database, a *different* driver and a *different* venue are
built from those rows, and the run continues. The distinction matters because
the two halves were written a day apart and each passed its own tests while
nothing connected them.

The property that carries the file is
`test_a_run_stopped_and_restarted_is_one_run`. Everything else pins a way the
round trip can look right and be wrong.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource, PositionStatus, SignalDirection
from tradeit.core.models import OhlcvBar
from tradeit.execution.live_shape import LadderState, LiveShapedSession
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.observability.portfolio_record import (
    last_recorded_session,
    load_ladder_state,
    load_portfolio_state,
    portfolio_named,
    record_portfolio,
    record_portfolio_snapshot,
    record_risk_snapshot,
    recorded_peak_equity,
    sync_positions,
)
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.risk.snapshot import build_snapshot
from tradeit.storage.tables import Position
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

CAPITAL = Decimal(100_000)
DAYS = [dt.date(2015, 6, day) for day in range(1, 9)]


def _bar(session_date: dt.date, close: int, instrument_id: int = 1) -> OhlcvBar:
    stamp = dt.datetime.combine(session_date, dt.time(20), tzinfo=dt.UTC)
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=session_date,
        open=Decimal(close),
        high=Decimal(close) + 1,
        low=Decimal(close) - 1,
        close=Decimal(close),
        volume=Decimal(5_000_000),
        event_time=stamp,
        knowledge_time=stamp,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _candidate(session_date: dt.date, close: int, instrument_id: int = 1) -> EntryCandidate:
    return EntryCandidate(
        score=OpportunityScore(
            instrument_id=instrument_id,
            session_date=session_date,
            direction=SignalDirection.LONG,
            total=0.5,
            components=(ScoreComponent(name="s", raw_value=0.5, normalised=0.5, weight=1.0),),
            feature_set_digest="t",
            strategy_config_digest="t",
        ),
        entry_price=Decimal(close),
        stop_price=Decimal(close) * Decimal("0.92"),
        sector="Manufacturing",
    )


CONFIG = StrategyConfig(name="ma_cross_baseline")


def _venue(cash: Decimal = CAPITAL, account: str = "t") -> PaperBroker:
    return PaperBroker(
        account_id=account,
        costs=ParticipationCostModel(config=CONFIG.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        starting_cash=cash,
        settings=Settings(trading_mode=TradingMode.PAPER),
    )


def _driver(
    db_session: Session,
    broker: PaperBroker,
    *,
    portfolio_id: int = 1,
    resume_from: object = None,
    ladder: dict[int, LadderState] | None = None,
) -> LiveShapedSession:
    """A fresh process's driver, against a venue the caller supplies.

    The venue is a parameter so a test can build a *second* driver on a
    *second* venue from the same rows, which is what a restart is.
    """
    service = BrokerExecutionService(
        broker=broker,
        session=db_session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("1.00"),
    )
    return LiveShapedSession(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=CONFIG.sizing, risk=CONFIG.risk, max_participation=Decimal("0.02")
            ),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=CONFIG.risk),
                    MaxPositionsRule(config=CONFIG.risk),
                    GrossExposureRule(config=CONFIG.risk),
                ),
                sizing=CONFIG.sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=CONFIG.exits),
        ),
        service=service,
        broker=broker,
        portfolio_id=portfolio_id,
        initial_capital=CAPITAL,
        resume_from=resume_from,  # type: ignore[arg-type]
        ladder_state=ladder or {},
    )


def _portfolio(db_session: Session) -> int:
    row = record_portfolio(
        db_session,
        name="live-shape-paper",
        mode=TradingMode.PAPER,
        initial_capital=CAPITAL,
        inception_date=DAYS[0],
    )
    db_session.commit()
    return int(row.id)


def _write(
    db_session: Session,
    portfolio_id: int,
    driver: LiveShapedSession,
    session_date: dt.date,
    peak: Decimal | None,
) -> Decimal:
    """Exactly what `scripts/paper_live_shape.py` does at each close."""
    state = driver.state()
    peak = state.equity if peak is None else max(peak, state.equity)
    sync_positions(
        db_session, portfolio_id, state.open_positions, driver.ladder(), closed_on=session_date
    )
    record_portfolio_snapshot(db_session, state, session_date=session_date, peak_equity=peak)
    record_risk_snapshot(
        db_session,
        build_snapshot(state, CONFIG.risk, session_date=session_date, peak_equity=peak),
    )
    db_session.commit()
    return peak


def _restart(
    db_session: Session, portfolio_id: int, *, restore_venue: bool = True
) -> LiveShapedSession:
    """Everything the script does on `--resume`, and nothing it does not."""
    last = last_recorded_session(db_session, portfolio_id)
    assert last is not None, "nothing recorded, so there is nothing to restart from"
    state = load_portfolio_state(
        db_session,
        portfolio_id,
        as_of=dt.datetime.combine(last, dt.time(20), tzinfo=dt.UTC),
    )
    assert state is not None
    ladder = load_ladder_state(db_session, portfolio_id)
    broker = _venue(cash=CAPITAL, account="restarted")
    if restore_venue:
        broker.restore(
            cash=state.cash,
            holdings={
                position.instrument_id: (position.quantity, position.average_entry_price)
                for position in state.open_positions
            },
        )
    return _driver(db_session, broker, portfolio_id=portfolio_id, resume_from=state, ladder=ladder)


def _run_until_held(db_session: Session, portfolio_id: int) -> tuple[LiveShapedSession, Decimal]:
    """Three sessions: decide, fill, then a higher close that lifts the high.

    Each session is written **after** it ran and under its own date, which is
    the order `scripts/paper_live_shape.py` uses. Writing before the session
    would file one session's book under the next one's date, and the resumed
    run would then continue from a book one session stale -- a fault no
    assertion about the ladder could see, because the ladder would be
    internally consistent and simply belong to the wrong day.
    """
    driver = _driver(db_session, _venue(), portfolio_id=portfolio_id)
    peak: Decimal | None = None
    schedule: tuple[tuple[dt.date, int, list[EntryCandidate]], ...] = (
        (DAYS[0], 100, [_candidate(DAYS[0], 100)]),
        (DAYS[1], 100, []),
        (DAYS[2], 120, []),
    )
    for day, close, candidates in schedule:
        driver.run_session(day, {1: _bar(day, close)}, candidates)
        peak = _write(db_session, portfolio_id, driver, day, peak)
    assert driver.state().position_count == 1, "no holding, so the round trip proves nothing"
    assert last_recorded_session(db_session, portfolio_id) == DAYS[2]
    return driver, peak


# -- the feature -------------------------------------------------------------


def test_a_run_stopped_and_restarted_is_one_run(db_session: Session) -> None:
    """**The feature.** A second process continues the first one's book.

    Both halves of this existed and passed a day before it did: the writers
    could write and the driver could resume a state handed to it in memory, and
    nothing read one into the other.
    """
    portfolio_id = _portfolio(db_session)
    first, _ = _run_until_held(db_session, portfolio_id)
    before = first.state()
    high_before = first.ladder()[1].highest
    held_before = first.ladder()[1].sessions_held

    second = _restart(db_session, portfolio_id)
    after = second.state()

    assert after.position_count == before.position_count == 1
    assert after.cash == before.cash
    assert after.open_positions[0].quantity == before.open_positions[0].quantity
    assert after.open_positions[0].stop_price == before.open_positions[0].stop_price
    assert second.ladder()[1].highest == high_before == Decimal(120)
    assert second.ladder()[1].sessions_held == held_before

    # And it keeps going: the next session reconciles and decides.
    outcome = second.run_session(DAYS[3], {1: _bar(DAYS[3], 121)}, [])
    assert outcome.discrepancies == 0, "the restored book disagreed with the restored venue"
    assert second.ladder()[1].sessions_held == held_before + 1


def test_the_restarted_high_is_the_one_in_force_not_the_entry_price(
    db_session: Session,
) -> None:
    """The failure the stored high exists to prevent, stated as a number.

    The entry filled near 100 and the high reached 120. A restart that reset
    the high to the entry would hand the ladder a trailing stop 20% looser than
    the one that was in force, and nothing downstream would report it -- the
    stop would simply never have moved.
    """
    portfolio_id = _portfolio(db_session)
    first, _ = _run_until_held(db_session, portfolio_id)
    entry = first.state().open_positions[0].average_entry_price

    second = _restart(db_session, portfolio_id)

    assert second.ladder()[1].highest == Decimal(120)
    assert second.ladder()[1].highest > entry


def test_the_peak_equity_survives_the_restart(db_session: Session) -> None:
    """A peak that reset would understate every later drawdown.

    Understating drawdown is the direction that gets a strategy promoted, which
    is why this is read back from the record rather than held in memory.
    """
    portfolio_id = _portfolio(db_session)
    _, peak = _run_until_held(db_session, portfolio_id)

    assert recorded_peak_equity(db_session, portfolio_id) == peak
    assert peak >= CAPITAL


# -- the ways the round trip can look right and be wrong ---------------------


def test_one_holding_keeps_one_row_however_many_sessions_it_is_held(
    db_session: Session,
) -> None:
    """`positions` is the running total, not a log.

    `record_position` inserts unconditionally, so a session loop calling it
    directly would write one open row per session -- and the reader restores
    *every* open row, so a holding held 200 sessions would come back as 200
    positions and the heat calculation would be 200 times too large.
    """
    portfolio_id = _portfolio(db_session)
    _run_until_held(db_session, portfolio_id)

    rows = (
        db_session.execute(select(Position).where(Position.portfolio_id == portfolio_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == str(PositionStatus.OPEN)


def test_a_holding_that_has_gone_is_closed_not_deleted(db_session: Session) -> None:
    """The row is history; deleting it would lose the trade."""
    portfolio_id = _portfolio(db_session)
    driver, peak = _run_until_held(db_session, portfolio_id)

    driver._lots.clear()
    _write(db_session, portfolio_id, driver, DAYS[3], peak)

    (row,) = (
        db_session.execute(select(Position).where(Position.portfolio_id == portfolio_id))
        .scalars()
        .all()
    )
    assert row.status == str(PositionStatus.CLOSED)
    assert row.closed_on == DAYS[3]
    assert row.quantity > 0, "what it held is a fact about it"
    restored = load_portfolio_state(
        db_session, portfolio_id, as_of=dt.datetime(2015, 6, 5, 20, tzinfo=dt.UTC)
    )
    assert restored is not None
    assert restored.position_count == 0


def test_an_update_never_moves_the_initial_stop(db_session: Session) -> None:
    """It is the premise the entry was decided on, and it defines R.

    If a later write could move it, the recorded risk of a trade would depend
    on when it was last saved -- and every R multiple computed from it would be
    a different number on a different restart.
    """
    portfolio_id = _portfolio(db_session)
    driver, peak = _run_until_held(db_session, portfolio_id)
    (row,) = (
        db_session.execute(select(Position).where(Position.portfolio_id == portfolio_id))
        .scalars()
        .all()
    )
    initial = row.initial_stop_price

    driver._lots[1].current_stop += Decimal(5)
    driver._lots[1].initial_stop += Decimal(5)
    _write(db_session, portfolio_id, driver, DAYS[3], peak)
    db_session.refresh(row)

    assert row.initial_stop_price == initial
    assert row.stop_price == driver._lots[1].current_stop, "the stop in force does move"


def test_a_holding_with_no_ladder_state_is_refused_rather_than_written(
    db_session: Session,
) -> None:
    """Refused where it happens, not where it is noticed.

    A row written without the high can be read back, and `_adopt` then refuses
    it -- tomorrow, in the session that was meant to continue. Refusing the
    write puts the error in the session that caused it.
    """
    portfolio_id = _portfolio(db_session)
    driver, _ = _run_until_held(db_session, portfolio_id)
    state = driver.state()

    with pytest.raises(ValueError, match="no ladder state"):
        sync_positions(db_session, portfolio_id, state.open_positions, {}, closed_on=DAYS[3])


def test_a_row_written_before_the_ladder_columns_is_left_out_not_defaulted(
    db_session: Session,
) -> None:
    """Migration 0022 added the three columns nullable and backfilled nothing.

    A null is "nobody recorded this", and the reader must not turn it into a
    number. Leaving it out makes the driver refuse that position by name.
    """
    portfolio_id = _portfolio(db_session)
    _run_until_held(db_session, portfolio_id)
    (row,) = (
        db_session.execute(select(Position).where(Position.portfolio_id == portfolio_id))
        .scalars()
        .all()
    )
    row.highest_since_entry = None
    db_session.commit()

    assert load_ladder_state(db_session, portfolio_id) == {}
    with pytest.raises(ValueError, match="no high-since-entry"):
        _restart(db_session, portfolio_id)


# -- the venue is a second authority, and it also has to come back ----------


def test_without_restoring_the_venue_the_first_session_back_halts(
    db_session: Session,
) -> None:
    """Why `PaperBroker.restore` exists, pinned.

    A real broker's book survives our restart -- that is the whole reason
    reconciliation is worth running. A simulated venue has no memory, so a
    restored book reconciled against a forgetful venue halts immediately, and
    the halt would read as a drift when it is the simulation having forgotten.
    """
    portfolio_id = _portfolio(db_session)
    _run_until_held(db_session, portfolio_id)

    exposed = _restart(db_session, portfolio_id, restore_venue=False)
    outcome = exposed.run_session(DAYS[3], {1: _bar(DAYS[3], 121)}, [])

    assert outcome.halted
    assert outcome.discrepancies >= 1


def test_a_venue_that_has_already_traded_refuses_a_restore(db_session: Session) -> None:
    """Restoring over a book in use would discard fills already reported."""
    portfolio_id = _portfolio(db_session)
    driver = _driver(db_session, _venue(), portfolio_id=portfolio_id)
    driver.run_session(DAYS[0], {1: _bar(DAYS[0], 100)}, [_candidate(DAYS[0], 100)])
    driver.run_session(DAYS[1], {1: _bar(DAYS[1], 100)}, [])

    with pytest.raises(ValueError, match="already traded"):
        driver.broker.restore(  # type: ignore[attr-defined]
            cash=Decimal(50_000), holdings={1: (Decimal(10), Decimal(100))}
        )


def test_a_holding_of_nothing_is_not_restored(db_session: Session) -> None:
    """An absence is not a holding, and restoring one as a holding diverges.

    `list_positions` filters out a zero quantity, so a venue restored with one
    would report no position while our book reported one -- a halt on the first
    session back, blamed on drift.
    """
    broker = _venue(account="empty-holding")
    with pytest.raises(ValueError, match="holding of nothing"):
        broker.restore(cash=CAPITAL, holdings={1: (Decimal(0), Decimal(100))})


def test_a_restored_venue_holds_what_our_book_holds(db_session: Session) -> None:
    """The point of the restore: the two agree on quantity and average price."""
    portfolio_id = _portfolio(db_session)
    _run_until_held(db_session, portfolio_id)

    second = _restart(db_session, portfolio_id)
    ours = second.state().open_positions[0]
    (theirs,) = second.broker.list_positions()  # type: ignore[attr-defined]

    assert theirs.quantity == ours.quantity
    assert theirs.average_price == ours.average_entry_price


# -- resuming a portfolio that was never recorded ---------------------------


def test_an_unrecorded_portfolio_has_no_session_to_resume_after(db_session: Session) -> None:
    """The script prints "starting flat" on this, rather than resuming nothing."""
    portfolio_id = _portfolio(db_session)
    assert last_recorded_session(db_session, portfolio_id) is None
    assert portfolio_named(db_session, "live-shape-paper") is not None
    assert portfolio_named(db_session, "no-such-book") is None
