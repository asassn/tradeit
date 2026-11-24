"""The loop a backtest structurally cannot have.

In a backtest the book and the venue are the same object, so our view of what
we hold is true by construction. In live the broker owns the book and we own a
model that can be wrong, and *that* is the failure mode paper trading exists to
rehearse.

The property under test throughout: **our state is an independent model updated
only from reported fills, and a drift between it and the broker is caught.** A
session driver that read the book back from the broker would reconcile the
broker against itself and agree forever -- which is the failure
`BrokerExecutionService` refuses to perform, and the easiest mistake to make
here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import (
    Bartimeframe,
    KnowledgeTimeSource,
    OrderSide,
    PositionStatus,
    Side,
    SignalDirection,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import Fill
from tradeit.execution.live_shape import LadderState, LiveShapedSession, Lot
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

DAY1 = dt.date(2015, 6, 1)
DAY2 = dt.date(2015, 6, 2)
DAY3 = dt.date(2015, 6, 3)


def _bar(
    instrument_id: int = 1,
    session_date: dt.date = DAY1,
    open_: int | str = 100,
    close: int | str = 101,
    volume: int = 5_000_000,
) -> OhlcvBar:
    body = (Decimal(open_), Decimal(close))
    stamp = dt.datetime.combine(session_date, dt.time(20), tzinfo=dt.UTC)
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=session_date,
        open=Decimal(open_),
        high=max(body) + 1,
        low=min(body) - 1,
        close=Decimal(close),
        volume=Decimal(volume),
        event_time=stamp,
        knowledge_time=stamp,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _candidate(instrument_id: int = 1, entry: str = "101", stop: str = "93") -> EntryCandidate:
    return EntryCandidate(
        score=OpportunityScore(
            instrument_id=instrument_id,
            session_date=DAY1,
            direction=SignalDirection.LONG,
            total=0.5,
            components=(ScoreComponent(name="s", raw_value=0.5, normalised=0.5, weight=1.0),),
            feature_set_digest="t",
            strategy_config_digest="t",
        ),
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        sector="Manufacturing",
    )


def _driver(db_session: Session, cash: int = 100_000) -> LiveShapedSession:
    config = StrategyConfig(name="ma_cross_baseline")
    broker = PaperBroker(
        account_id="t",
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        starting_cash=Decimal(cash),
        settings=Settings(trading_mode=TradingMode.PAPER),
    )
    service = BrokerExecutionService(
        broker=broker,
        session=db_session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("1.00"),
    )
    return LiveShapedSession(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02")
            ),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=config.risk),
                    MaxPositionsRule(config=config.risk),
                    GrossExposureRule(config=config.risk),
                ),
                sizing=config.sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        service=service,
        broker=broker,
        initial_capital=Decimal(cash),
    )


def test_a_quiet_session_still_produces_an_outcome(db_session: Session) -> None:
    driver = _driver(db_session)
    outcome = driver.run_session(DAY1, {1: _bar()}, [])
    assert outcome.fills == ()
    assert outcome.orders_placed == 0
    assert outcome.discrepancies == 0
    assert outcome.equity == Decimal(100_000)


def test_an_order_is_placed_then_filled_on_the_next_session(db_session: Session) -> None:
    """The temporal shape: decide on one session, fill on the next."""
    driver = _driver(db_session)
    first = driver.run_session(DAY1, {1: _bar()}, [_candidate()])
    assert first.orders_placed == 1
    assert first.fills == ()

    second = driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])
    assert len(second.fills) == 1
    assert second.positions == 1
    assert second.discrepancies == 0


def test_our_model_and_the_broker_agree_after_a_fill(db_session: Session) -> None:
    driver = _driver(db_session)
    driver.run_session(DAY1, {1: _bar()}, [_candidate()])
    driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])

    ours = driver.state()
    theirs = driver.broker.list_positions()  # type: ignore[attr-defined]
    assert len(theirs) == 1
    assert ours.open_positions[0].quantity == theirs[0].quantity
    assert driver.equity == pytest.approx(driver.broker.equity)  # type: ignore[attr-defined]


def test_a_drift_in_our_model_is_caught_and_halts(db_session: Session) -> None:
    """The whole point. If this passes trivially, the driver is reading the
    book back from the broker and reconciling it against itself."""
    driver = _driver(db_session)
    driver.run_session(DAY1, {1: _bar()}, [_candidate()])
    driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])
    assert not driver.service.is_halted(driver.portfolio_id)

    # Corrupt OUR view only, as a missed partial fill would.
    driver._lots[1].quantity += Decimal(7)

    outcome = driver.run_session(DAY3, {1: _bar(session_date=DAY3)}, [])

    assert outcome.discrepancies >= 1
    assert outcome.halted
    assert outcome.orders_placed == 0, "decided while holding a book known to be wrong"


def test_a_position_the_broker_does_not_have_is_caught(db_session: Session) -> None:
    driver = _driver(db_session)
    driver._lots[9] = Lot(
        instrument_id=9,
        quantity=Decimal(10),
        entry_price=Decimal(50),
        initial_stop=Decimal(46),
        current_stop=Decimal(46),
        highest=Decimal(50),
        opened_on=DAY1,
    )
    outcome = driver.run_session(DAY1, {9: _bar(instrument_id=9)}, [])
    assert outcome.halted


def test_an_entry_that_gaps_through_its_stop_is_kept_not_refused(db_session: Session) -> None:
    """The asymmetry with the backtester, which is the finding.

    `engine._settle` declines to open a position whose fill gapped through the
    intended stop, counting `abandoned_entries`. **A live system cannot**: the
    shares are already bought, and refusing to record the lot would leave us
    holding a position our own model does not know about -- exactly the drift
    reconciliation exists to catch, caused by us.
    """
    driver = _driver(db_session)
    # A stop just below the decision price, then a gap down through it.
    driver.run_session(DAY1, {1: _bar()}, [_candidate(stop="100")])
    outcome = driver.run_session(DAY2, {1: _bar(session_date=DAY2, open_=80, close=81)}, [])

    assert len(outcome.fills) == 1
    assert driver.premise_failed == 1
    lot = driver._lots[1]
    assert lot.current_stop < lot.entry_price, "R would be undefined"
    assert outcome.discrepancies == 0, "the lot was not recorded, so our book drifted"


def test_a_split_restates_both_books_identically(db_session: Session) -> None:
    driver = _driver(db_session)
    driver.run_session(DAY1, {1: _bar()}, [_candidate()])
    driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])
    before = driver._lots[1].quantity

    outcome = driver.run_session(DAY3, {1: _bar(session_date=DAY3)}, [], {1: Decimal("1.5")})

    assert driver._lots[1].quantity == before * Decimal("1.5")
    assert outcome.discrepancies == 0, "the split moved the two books apart"


def test_a_split_applied_to_only_one_book_is_caught(db_session: Session) -> None:
    """Pins that the split test above is not passing because splits are ignored."""
    driver = _driver(db_session)
    driver.run_session(DAY1, {1: _bar()}, [_candidate()])
    driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])

    # Only the broker is told, as a real corporate action would reach it.
    driver.broker.apply_splits({1: Decimal(2)})  # type: ignore[attr-defined]

    outcome = driver.run_session(DAY3, {1: _bar(session_date=DAY3)}, [])
    assert outcome.halted, "a split the broker applied and we did not went unnoticed"


def test_the_service_reconciles_our_state_not_the_brokers(db_session: Session) -> None:
    """Wired in `__post_init__` so the two cannot be pointed at different books."""
    driver = _driver(db_session)
    # Bound methods are not identity-equal, so compare what the binding is
    # actually asserting: the service reads OUR book, through the driver.
    assert getattr(driver.service.ours, "__self__", None) is driver
    assert driver.service.ours() is not None  # type: ignore[operator]
    assert driver.service.ours().portfolio_id == driver.portfolio_id  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Resuming a session
#
# `Lot` has said since Phase 11 why this is hard: "A broker reports quantity and
# average price. It does not report the high since entry, how many sessions we
# have held, or whether a partial profit was taken — those are ours, and losing
# them would reset the stop ladder on every restart."
#
# Until 2026-11-23 there was nowhere to put those three and no loader, so every
# paper session started flat and the question never arose. Phase 11 is closed on
# *equivalence* — 1,457 of 1,457 fills agreed — and these are about *operation*.
# ---------------------------------------------------------------------------


def _resume_state(**overrides: object) -> PortfolioState:
    position = PositionState(
        position_id=7,
        portfolio_id=1,
        instrument_id=7,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(100),
        average_entry_price=Decimal(100),
        stop_price=Decimal(95),
        opened_on=dt.date(2026, 7, 1),
        initial_stop_price=Decimal(92),
    )
    payload: dict[str, object] = {
        "portfolio_id": 1,
        "as_of": dt.datetime(2026, 8, 14, 21, tzinfo=dt.UTC),
        "cash": Decimal(40_000),
        "equity": Decimal(100_000),
        "positions": (position,),
        "last_prices": {7: Decimal(110)},
    }
    payload.update(overrides)
    return PortfolioState(**payload)  # type: ignore[arg-type]


def test_a_session_resumes_the_book_it_was_given(db_session: Session) -> None:
    """**The feature**: tomorrow continues yesterday instead of starting over."""
    driver = _driver(db_session)
    resumed = LiveShapedSession(
        cycle=driver.cycle,
        service=driver.service,
        broker=driver.broker,
        initial_capital=Decimal(100_000),
        resume_from=_resume_state(),
        ladder_state={
            7: LadderState(highest=Decimal(130), sessions_held=31, partial_profit_taken=True)
        },
    )

    state = resumed.state()

    assert state.cash == Decimal(40_000)
    assert state.position_count == 1
    (held,) = state.open_positions
    assert held.instrument_id == 7
    assert held.stop_price == Decimal(95), "the stop in force, not the initial one"
    assert held.initial_stop_price == Decimal(92)


def test_the_ladder_state_survives_the_restart(db_session: Session) -> None:
    """The three fields a broker cannot report, carried across the boundary.

    A trailing stop resumed from a reset high is looser than the one that was in
    force; a time stop resumed from zero never fires; and a partial profit
    forgotten is one that can be taken twice.
    """
    driver = _driver(db_session)
    resumed = LiveShapedSession(
        cycle=driver.cycle,
        service=driver.service,
        broker=driver.broker,
        initial_capital=Decimal(100_000),
        resume_from=_resume_state(),
        ladder_state={
            7: LadderState(highest=Decimal(130), sessions_held=31, partial_profit_taken=True)
        },
    )

    lot = resumed._lots[7]
    assert lot.highest == Decimal(130), "not reset to the entry price"
    assert lot.sessions_held == 31, "not reset to zero"
    assert lot.partial_profit_taken is True, "not forgotten and taken twice"


def test_a_position_with_no_recorded_high_is_refused_not_defaulted(
    db_session: Session,
) -> None:
    """**Unknown ladder state is refused, not substituted.**

    `positions.highest_since_entry` is nullable so that "nobody recorded this"
    stays distinguishable from a number. Quietly using the entry price would
    resume a trailing stop from a high that never happened, which is looser
    than the stop that was actually in force — a silent loosening of risk on
    every restart.
    """
    driver = _driver(db_session)

    with pytest.raises(ValueError, match="no high-since-entry"):
        LiveShapedSession(
            cycle=driver.cycle,
            service=driver.service,
            broker=driver.broker,
            initial_capital=Decimal(100_000),
            resume_from=_resume_state(),
            ladder_state={},
        )


def test_a_position_with_no_stop_cannot_be_resumed(db_session: Session) -> None:
    """Its risk is undefined, so the ladder has nothing to advance from."""
    driver = _driver(db_session)
    stopless = PositionState(
        position_id=7,
        portfolio_id=1,
        instrument_id=7,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(100),
        average_entry_price=Decimal(100),
        stop_price=None,
        opened_on=dt.date(2026, 7, 1),
        initial_stop_price=None,
    )

    with pytest.raises(ValueError, match="no stop recorded"):
        LiveShapedSession(
            cycle=driver.cycle,
            service=driver.service,
            broker=driver.broker,
            initial_capital=Decimal(100_000),
            resume_from=_resume_state(positions=(stopless,)),
            ladder_state={
                7: LadderState(highest=Decimal(130), sessions_held=1, partial_profit_taken=False)
            },
        )


def test_resuming_a_flat_book_is_not_the_same_as_starting_fresh(
    db_session: Session,
) -> None:
    """``None`` is "start fresh"; an empty portfolio is "resume, holding nothing".

    The difference is the cash: a fresh session starts at `initial_capital`, and
    a resumed flat one starts at whatever the book was left with.
    """
    driver = _driver(db_session)
    resumed = LiveShapedSession(
        cycle=driver.cycle,
        service=driver.service,
        broker=driver.broker,
        initial_capital=Decimal(100_000),
        resume_from=_resume_state(positions=(), cash=Decimal(73_500), last_prices={}),
    )

    assert resumed.state().cash == Decimal(73_500)
    assert resumed.state().open_positions == ()
    assert driver.state().cash == Decimal(100_000), "the fresh driver is untouched"


def test_every_existing_caller_still_starts_flat(db_session: Session) -> None:
    """`resume_from` defaults to None, so nothing that worked before changed."""
    driver = _driver(db_session)

    assert driver.state().cash == Decimal(100_000)
    assert driver.state().open_positions == ()


# -- the stop belongs to one decision ---------------------------------------

DAY4 = dt.date(2015, 6, 4)
DAY5 = dt.date(2015, 6, 5)
DAY6 = dt.date(2015, 6, 8)


def test_a_re_entry_gets_its_own_stop_not_the_first_entrys(db_session: Session) -> None:
    """Found on 2026-11-24 by comparing a paper run against the same run
    stopped and resumed, and it is the reason to run that comparison at all.

    The pending stop was keyed by client order id and looked up by prefix
    match -- first hit, never removed. So the second entry in an instrument
    inherited the first entry's stop, and so did every later one. Over three
    years instrument 1695 was entered four times at prices from 75.60 to 78.91
    and all four got the stop decided for the first: 70.104, between 7.3% and
    11.2% away where 8% was declared.

    The initial stop defines R. A position whose R is a year-old number has a
    risk figure that is wrong in every direction it is used.

    754 of 754 sessions had reconciled clean and both runs agreed on final
    equity. The expected answer arrived; it answered a different question.

    What this pins is the **key**: one pending stop per instrument, overwritten
    by the latest decision. Consuming it on use is a separate invariant and is
    held separately below, because a mutant that only stopped consuming it
    survived this test.
    """
    driver = _driver(db_session)
    driver.run_session(DAY1, {1: _bar()}, [_candidate(stop="93")])
    driver.run_session(DAY2, {1: _bar(session_date=DAY2)}, [])
    assert driver._lots[1].initial_stop == Decimal(93)

    # A close through the stop, so the ladder exits and the lot goes.
    driver.run_session(DAY3, {1: _bar(session_date=DAY3, open_=90, close=90)}, [])
    driver.run_session(DAY4, {1: _bar(session_date=DAY4, open_=90, close=90)}, [])
    assert 1 not in driver._lots, "no exit, so the re-entry under test never happens"

    # Re-entered at a different price, with a different stop.
    driver.run_session(
        DAY5, {1: _bar(session_date=DAY5, open_=90, close=90)}, [_candidate(entry="90", stop="82")]
    )
    driver.run_session(DAY6, {1: _bar(session_date=DAY6, open_=90, close=90)}, [])

    assert 1 in driver._lots, "the second entry never filled, so nothing is under test"
    assert driver._lots[1].initial_stop == Decimal(82), (
        "the re-entry inherited the first entry's stop"
    )


def test_an_unfilled_entrys_stop_is_not_handed_to_a_later_one(db_session: Session) -> None:
    """Two decisions pending in one instrument: the later one is in force.

    An entry is only submitted for an instrument we do not hold, so a second
    submission means the first did not fill -- and a stop decided against a
    price we have since moved away from is stale, not a spare.
    """
    driver = _driver(db_session)
    driver._submit(DAY1, 1, OrderSide.BUY, Decimal(10), candidate=_candidate(stop="93"))
    driver._submit(DAY2, 1, OrderSide.BUY, Decimal(10), candidate=_candidate(stop="81"))

    assert driver._pending_stops == {1: Decimal(81)}


def test_a_stop_is_used_once_and_not_left_for_a_later_fill(db_session: Session) -> None:
    """A stop belongs to one decision, and is consumed by the fill that uses it.

    Keying by instrument and overwriting is what fixes the re-entry above, and
    it is **not** what this holds. This holds the second half: a fill that
    arrives with no decision behind it must reach the fallback stop rather than
    inherit the last one recorded. With the stop merely read instead of popped,
    the fallback becomes unreachable for any instrument ever entered -- and the
    fallback is the only thing standing between a forgotten order and a
    position with no defined risk.
    """
    driver = _driver(db_session)
    driver._submit(DAY1, 1, OrderSide.BUY, Decimal(10), candidate=_candidate(stop="93"))
    first = Fill(
        order_id=1,
        instrument_id=1,
        side=OrderSide.BUY,
        quantity=Decimal(10),
        price=Decimal(100),
        filled_at=dt.datetime.combine(DAY2, dt.time(20), tzinfo=dt.UTC),
    )
    driver._apply_fill(first, DAY2)
    assert driver._lots[1].initial_stop == Decimal(93)
    assert driver._pending_stops == {}, "the stop outlived the decision that made it"

    # The lot goes, as a sell fill would take it, and a buy fill arrives for
    # an order no decision of ours is holding a stop for.
    driver._lots.clear()
    driver._apply_fill(replace(first, order_id=2, price=Decimal(200)), DAY3)

    assert driver._lots[1].initial_stop == Decimal(200) * Decimal("0.92")
    assert driver._lots[1].initial_stop != Decimal(93)
