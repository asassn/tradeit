"""The controls a strategy cannot reach.

The roadmap puts these above the strategy, *"enforced above the strategy, not
configured by it"*, and says their values *"need approval and are not
inferred"*. Both shape what is tested here.

They are not `RiskRule`s, and could not be: that protocol is pure functions of
(proposal, state, config) that do not read the clock or the database, which is
what makes the risk layer backtestable. A kill switch is operator state,
stale-data protection is a fact about when data arrived, and duplicate
protection is a fact about what was already sent. Forcing them in would have
cost the sentence that makes the rest testable.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tradeit.config import Settings, TradingMode
from tradeit.core.enums import OrderSide, OrderType
from tradeit.execution.base import OrderRequest
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import (
    BrokerExecutionService,
    OrderRefusedByControl,
)
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.risk.controls import (
    ControlOutcome,
    DuplicateOrderGuard,
    KillSwitch,
    PreTradeControls,
    StaleDataGuard,
)
from tradeit.storage.tables import JournalEntry
from tradeit.strategy.config import StrategyConfig

SESSION = dt.date(2015, 6, 1)
NOW = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


def _order(client_order_id: str = "c-1", instrument_id: int = 1) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(100),
    )


# -- the kill switch --------------------------------------------------------


def test_a_fresh_kill_switch_permits() -> None:
    assert KillSwitch().check() is None


def test_engaging_requires_a_reason() -> None:
    switch = KillSwitch()
    with pytest.raises(ValueError, match="requires a reason"):
        switch.engage("   ")


def test_an_engaged_switch_refuses_and_carries_the_reason() -> None:
    switch = KillSwitch()
    switch.engage("feed desynchronised")
    refusal = switch.check()
    assert refusal is not None
    assert refusal.outcome is ControlOutcome.KILL_SWITCH
    assert "feed desynchronised" in refusal.detail


def test_a_switch_file_throws_it_without_touching_the_process(tmp_path: Path) -> None:
    """An operator must be able to stop a running system without waiting for it
    to ask."""
    marker = tmp_path / "STOP"
    switch = KillSwitch(path=marker)
    assert switch.check() is None

    marker.write_text("operator stopped trading")
    refusal = switch.check()
    assert refusal is not None
    assert "operator stopped trading" in refusal.detail


def test_the_file_is_read_every_time_not_cached(tmp_path: Path) -> None:
    """A switch that was read at startup is not a switch."""
    marker = tmp_path / "STOP"
    switch = KillSwitch(path=marker)
    switch.check()
    marker.write_text("thrown later")
    assert switch.check() is not None


def test_an_empty_switch_file_still_throws_it(tmp_path: Path) -> None:
    """`touch STOP` is the fastest thing an operator under pressure will do."""
    marker = tmp_path / "STOP"
    marker.write_text("")
    refusal = KillSwitch(path=marker).check()
    assert refusal is not None


def test_an_unreadable_switch_file_is_treated_as_thrown(tmp_path: Path) -> None:
    """Fail closed. The alternative is a control that stops working exactly
    when the filesystem is in trouble."""
    marker = tmp_path / "STOP"
    marker.mkdir()  # a directory cannot be read as text
    refusal = KillSwitch(path=marker).check()
    assert refusal is not None
    assert "could not be read" in refusal.detail


def test_clearing_requires_who_and_why() -> None:
    switch = KillSwitch()
    switch.engage("x")
    with pytest.raises(ValueError, match="who cleared it"):
        switch.disengage(cleared_by="", note="fine now")
    assert switch.check() is not None


def test_a_process_cannot_clear_a_switch_thrown_by_file(tmp_path: Path) -> None:
    """That one is the operator's, and clearing it would overrule them."""
    marker = tmp_path / "STOP"
    marker.write_text("operator")
    switch = KillSwitch(path=marker)
    switch.disengage(cleared_by="system", note="looks fine")
    assert switch.check() is not None


# -- stale data -------------------------------------------------------------


def test_a_stale_tolerance_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        StaleDataGuard(max_age=dt.timedelta(0))


def test_fresh_data_passes() -> None:
    guard = StaleDataGuard(max_age=dt.timedelta(days=2))
    assert guard.check(1, {1: NOW - dt.timedelta(hours=1)}, NOW) is None


def test_stale_data_refuses_and_reports_the_age() -> None:
    guard = StaleDataGuard(max_age=dt.timedelta(days=2))
    refusal = guard.check(1, {1: NOW - dt.timedelta(days=5)}, NOW)
    assert refusal is not None
    assert refusal.outcome is ControlOutcome.STALE_DATA
    assert "5 days" in refusal.reason


def test_never_seen_data_refuses() -> None:
    guard = StaleDataGuard(max_age=dt.timedelta(days=2))
    refusal = guard.check(99, {1: NOW}, NOW)
    assert refusal is not None
    assert "ever been seen" in refusal.reason


def test_staleness_is_judged_against_the_given_as_of_not_the_clock() -> None:
    """A guard that read the clock could not be replayed, and a guard nobody
    can replay is a guard nobody can test."""
    guard = StaleDataGuard(max_age=dt.timedelta(days=2))
    stamp = dt.datetime(1999, 3, 1, tzinfo=dt.UTC)
    assert guard.check(1, {1: stamp}, stamp + dt.timedelta(hours=1)) is None
    assert guard.check(1, {1: stamp}, stamp + dt.timedelta(days=9)) is not None


# -- duplicate orders -------------------------------------------------------


def test_a_resent_client_order_id_is_refused() -> None:
    guard = DuplicateOrderGuard()
    guard.record(_order(), SESSION)
    refusal = guard.check(_order(), SESSION)
    assert refusal is not None
    assert "already been sent" in refusal.reason


def test_the_same_trade_under_a_different_id_is_refused() -> None:
    """The dangerous case a venue's idempotency key cannot catch, because the
    ids differ: a system that decides twice doubles a position while every
    individual order looks correct."""
    guard = DuplicateOrderGuard()
    guard.record(_order(client_order_id="first"), SESSION)
    refusal = guard.check(_order(client_order_id="second"), SESSION)
    assert refusal is not None
    assert refusal.outcome is ControlOutcome.DUPLICATE_ORDER
    assert "under a different id" in refusal.detail


def test_the_same_trade_on_a_different_session_is_permitted() -> None:
    """Otherwise a strategy could never re-enter a name it had traded once."""
    guard = DuplicateOrderGuard()
    guard.record(_order(client_order_id="first"), SESSION)
    assert guard.check(_order(client_order_id="second"), SESSION + dt.timedelta(days=1)) is None


def test_a_different_instrument_is_permitted() -> None:
    guard = DuplicateOrderGuard()
    guard.record(_order(instrument_id=1), SESSION)
    assert guard.check(_order(client_order_id="other", instrument_id=2), SESSION) is None


# -- all together -----------------------------------------------------------


def test_every_control_runs_even_after_the_first_refusal() -> None:
    """A caller stopping at the first learns the switch is thrown and not that
    the data is also three days old -- and the second explains the first."""
    switch = KillSwitch()
    switch.engage("stopped")
    controls = PreTradeControls(
        kill_switch=switch,
        stale_data=StaleDataGuard(max_age=dt.timedelta(days=2)),
        duplicates=DuplicateOrderGuard(),
    )
    refusals = controls.check(
        _order(), session_date=SESSION, as_of=NOW, last_seen={1: NOW - dt.timedelta(days=5)}
    )
    outcomes = {refusal.outcome for refusal in refusals}
    assert ControlOutcome.KILL_SWITCH in outcomes
    assert ControlOutcome.STALE_DATA in outcomes
    assert len(refusals) == 2


def test_configured_reports_what_is_actually_installed() -> None:
    """A run with no controls must be visible as such, rather than letting a
    reader assume protection that was never wired."""
    assert PreTradeControls().configured == ()
    assert PreTradeControls(kill_switch=KillSwitch()).configured == ("kill_switch",)


# -- at the execution boundary ----------------------------------------------


def _service(db_session: Session, controls: PreTradeControls | None) -> BrokerExecutionService:
    config = StrategyConfig(name="ma_cross_baseline")
    broker = PaperBroker(
        account_id="t",
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        starting_cash=Decimal(100_000),
        settings=Settings(trading_mode=TradingMode.PAPER),
    )
    return BrokerExecutionService(
        broker=broker,
        session=db_session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("0.01"),
        controls=controls,
    )


def test_a_thrown_switch_stops_order_placement(db_session: Session) -> None:
    """Checked at the boundary where orders LEAVE. A switch that only stopped
    sizing would let a resting order fill after it was thrown."""
    switch = KillSwitch()
    switch.engage("stopped")
    service = _service(db_session, PreTradeControls(kill_switch=switch))
    with pytest.raises(OrderRefusedByControl, match="kill_switch"):
        service.place(_order(), session_date=SESSION, as_of=NOW)


def test_a_control_refusal_is_journalled(db_session: Session) -> None:
    switch = KillSwitch()
    switch.engage("stopped")
    service = _service(db_session, PreTradeControls(kill_switch=switch))
    with pytest.raises(OrderRefusedByControl):
        service.place(_order(), session_date=SESSION, as_of=NOW)
    db_session.flush()
    entries = db_session.query(JournalEntry).all()
    assert len(entries) == 1
    assert entries[0].entry_type == "declined"
    assert "kill_switch" in (entries[0].rationale or "")


def test_installed_controls_make_the_session_date_required(db_session: Session) -> None:
    """Defaulting it to now would make a stale-data guard unreplayable."""
    service = _service(db_session, PreTradeControls(kill_switch=KillSwitch()))
    with pytest.raises(ValueError, match="session_date and as_of"):
        service.place(_order())


def test_without_controls_place_still_works(db_session: Session) -> None:
    """Opt-in: every existing caller keeps working, visibly unprotected."""
    service = _service(db_session, None)
    assert service.place(_order()) == 1


def test_a_duplicate_is_only_remembered_after_a_successful_submission(
    db_session: Session,
) -> None:
    """An order the venue refused was not sent, so the legitimate retry must
    not be blocked."""
    service = _service(db_session, PreTradeControls(duplicates=DuplicateOrderGuard()))
    bad = OrderRequest(
        client_order_id="frac",
        portfolio_id=1,
        instrument_id=1,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10.5"),
    )
    with pytest.raises(ValueError, match="fractional"):
        service.place(bad, session_date=SESSION, as_of=NOW)

    # The retry, corrected, must get through.
    assert service.place(_order(client_order_id="frac2"), session_date=SESSION, as_of=NOW) == 1


def test_a_genuine_duplicate_is_refused_at_the_boundary(db_session: Session) -> None:
    service = _service(db_session, PreTradeControls(duplicates=DuplicateOrderGuard()))
    service.place(_order(client_order_id="first"), session_date=SESSION, as_of=NOW)
    with pytest.raises(OrderRefusedByControl, match="duplicate_order"):
        service.place(_order(client_order_id="second"), session_date=SESSION, as_of=NOW)
