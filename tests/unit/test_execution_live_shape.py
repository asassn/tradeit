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
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import (
    Bartimeframe,
    KnowledgeTimeSource,
    SignalDirection,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.live_shape import LiveShapedSession, Lot
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
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
