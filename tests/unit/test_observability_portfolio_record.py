"""Writing a portfolio down, and reading it back.

`portfolios`, `positions`, `portfolio_snapshots` and `risk_snapshots` were
declared in Phase 8 and constructed nowhere in `src/tradeit`, so every run
hardcoded `portfolio_id=1` against a row that did not exist. The consequence
that matters is not the dangling key: it is that **a paper session cannot
resume**, because `LiveShapedSession` starts flat in memory and nothing rebuilt
it.

So the test that carries this module is
`test_a_portfolio_survives_the_process_that_produced_it` — the writers exist to
make the reader possible, and a reader that cannot reproduce the state is the
whole feature failing quietly.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.core.enums import PositionStatus, Side, TradingMode
from tradeit.observability.portfolio_record import (
    SnapshotExists,
    load_portfolio_state,
    record_portfolio,
    record_portfolio_snapshot,
    record_position,
    record_risk_snapshot,
)
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.risk.base import RiskSnapshot as RiskSnapshotValue

SESSION = dt.date(2026, 8, 14)
AS_OF = dt.datetime.combine(SESSION, dt.time(21), tzinfo=dt.UTC)


def _position(
    portfolio_id: int,
    instrument_id: int = 7,
    *,
    status: PositionStatus = PositionStatus.OPEN,
    stop: str | None = "90",
    quantity: str = "100",
) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=portfolio_id,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=status,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(100),
        stop_price=None if stop is None else Decimal(stop),
        opened_on=dt.date(2026, 7, 1),
        initial_stop_price=None if stop is None else Decimal(stop),
        pyramid_entries=1,
    )


def _state(portfolio_id: int, *positions: PositionState, cash: str = "40000") -> PortfolioState:
    return PortfolioState(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        cash=Decimal(cash),
        equity=Decimal(100_000),
        positions=positions,
        last_prices={p.instrument_id: Decimal(110) for p in positions},
    )


def _portfolio(db_session: Session, name: str = "swing") -> int:
    row = record_portfolio(
        db_session,
        name=name,
        mode=TradingMode.PAPER,
        initial_capital=Decimal(100_000),
        inception_date=dt.date(2026, 1, 2),
    )
    return int(row.id)


# -- the reader is the point -------------------------------------------------


def test_a_portfolio_survives_the_process_that_produced_it(db_session: Session) -> None:
    """**The feature.** Write a portfolio, read it back, continue from it.

    Phase 11 demonstrated paper/backtest equivalence order by order and is
    closed on that basis. It is not closed on *operation*: every session started
    flat because nothing could rebuild one. This is the test that changes that,
    and if it ever passes vacuously the rest of this module is decoration.
    """
    portfolio_id = _portfolio(db_session)
    held = _position(portfolio_id, 7)
    record_position(db_session, held)
    record_portfolio_snapshot(db_session, _state(portfolio_id, held), session_date=SESSION)
    db_session.flush()

    restored = load_portfolio_state(db_session, portfolio_id, as_of=AS_OF + dt.timedelta(days=1))

    assert restored is not None
    assert restored.cash == Decimal(40_000)
    assert restored.equity == Decimal(100_000)
    assert restored.position_count == 1
    (back,) = restored.open_positions
    assert back.instrument_id == 7
    assert back.quantity == Decimal(100)
    assert back.stop_price == Decimal(90)
    assert back.initial_stop_price == Decimal(90)
    assert back.side is Side.LONG
    # The stop survived, so heat is computable — which is the property that
    # makes the restored state usable by the risk layer at all. With no prices
    # supplied it marks at the entry: 100 shares losing 100 - 90.
    assert restored.total_open_risk() == Decimal(100) * Decimal(10)


def test_a_restored_portfolio_carries_no_prices_until_it_is_given_some(
    db_session: Session,
) -> None:
    """**A snapshot records what a session was worth, not what today's prices are.**

    So a restored state has an empty `last_prices` unless the caller supplies
    one, and `open_risk` then marks against the entry price. That is the honest
    default — re-marking a closed book at prices nobody has fetched would invent
    a valuation — but it means a caller resuming a session must pass today's
    closes before reading heat, or it will read yesterday's risk.
    """
    portfolio_id = _portfolio(db_session)
    held = _position(portfolio_id, 7)
    record_position(db_session, held)
    record_portfolio_snapshot(db_session, _state(portfolio_id, held), session_date=SESSION)
    db_session.flush()

    bare = load_portfolio_state(db_session, portfolio_id, as_of=AS_OF)
    marked = load_portfolio_state(
        db_session, portfolio_id, as_of=AS_OF, last_prices={7: Decimal(130)}
    )

    assert bare is not None and marked is not None
    assert bare.last_prices == {}
    # Marked at entry: 100 - 90. Marked at 130: 130 - 90.
    assert bare.total_open_risk() == Decimal(100) * Decimal(10)
    assert marked.total_open_risk() == Decimal(100) * Decimal(40)


def test_an_unrecorded_portfolio_reads_as_none_not_as_empty(db_session: Session) -> None:
    """**Two different answers, and conflating them is the failure.**

    "We have never recorded this portfolio" and "this portfolio holds nothing"
    lead to opposite decisions, and the second is a legitimate state to resume
    from. Returning an empty `PortfolioState` for the first would make a
    missing record look like a flat book.
    """
    portfolio_id = _portfolio(db_session)

    assert load_portfolio_state(db_session, portfolio_id, as_of=AS_OF) is None


def test_a_flat_portfolio_reads_back_as_flat_not_as_missing(db_session: Session) -> None:
    portfolio_id = _portfolio(db_session)
    record_portfolio_snapshot(db_session, _state(portfolio_id, cash="100000"), session_date=SESSION)
    db_session.flush()

    restored = load_portfolio_state(db_session, portfolio_id, as_of=AS_OF)

    assert restored is not None, "a recorded flat portfolio is not a missing one"
    assert restored.open_positions == ()
    assert restored.cash == Decimal(100_000)


def test_a_closed_position_is_not_restored_at_all(db_session: Session) -> None:
    """**Asserted on `positions`, not on `open_positions`.**

    `PortfolioState.open_positions` filters by status, so a closed row loaded
    into `positions` is invisible to that property — and a test checking only
    it passes while the query is wrong. It matters because `PortfolioCycle`'s
    `_apply_exits` iterates `portfolio.positions` directly, so a closed row
    carried in would be considered for an exit it has already had.
    """
    portfolio_id = _portfolio(db_session)
    record_position(db_session, _position(portfolio_id, 7))
    record_position(
        db_session,
        _position(portfolio_id, 8, status=PositionStatus.CLOSED, stop=None),
    )
    record_portfolio_snapshot(db_session, _state(portfolio_id), session_date=SESSION)
    db_session.flush()

    restored = load_portfolio_state(db_session, portfolio_id, as_of=AS_OF)

    assert restored is not None
    assert {p.instrument_id for p in restored.open_positions} == {7}
    assert {p.instrument_id for p in restored.positions} == {7}, (
        "the closed row must not be loaded at all, not merely filtered later"
    )


def test_another_portfolios_positions_are_never_restored(db_session: Session) -> None:
    """Two portfolios, and the reader must not mix them."""
    mine = _portfolio(db_session, "swing")
    theirs = _portfolio(db_session, "retirement")
    record_position(db_session, _position(mine, 7))
    record_position(db_session, _position(theirs, 9))
    record_portfolio_snapshot(db_session, _state(mine), session_date=SESSION)
    db_session.flush()

    restored = load_portfolio_state(db_session, mine, as_of=AS_OF)

    assert restored is not None
    assert {p.instrument_id for p in restored.positions} == {7}


def test_the_most_recent_snapshot_at_or_before_the_date_wins(db_session: Session) -> None:
    """Resuming on Wednesday must not read Thursday's book."""
    portfolio_id = _portfolio(db_session)
    for day, cash in ((dt.date(2026, 8, 12), "10000"), (SESSION, "40000")):
        record_portfolio_snapshot(
            db_session,
            PortfolioState(
                portfolio_id=portfolio_id,
                as_of=dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC),
                cash=Decimal(cash),
                equity=Decimal(100_000),
                positions=(),
            ),
            session_date=day,
        )
    db_session.flush()

    earlier = load_portfolio_state(
        db_session, portfolio_id, as_of=dt.datetime(2026, 8, 13, tzinfo=dt.UTC)
    )
    later = load_portfolio_state(db_session, portfolio_id, as_of=AS_OF)

    assert earlier is not None and earlier.cash == Decimal(10_000)
    assert later is not None and later.cash == Decimal(40_000)


# -- what the writers refuse -------------------------------------------------


def test_an_open_position_without_a_stop_is_refused(db_session: Session) -> None:
    """`PositionState`'s own rule, enforced at the boundary.

    A position with no stop has undefined risk and cannot be summed into heat,
    so writing one would put a row in the database that the domain refuses to
    construct. The column is nullable only because a *closed* position no
    longer needs one.
    """
    portfolio_id = _portfolio(db_session)

    with pytest.raises(ValueError, match="no stop"):
        record_position(db_session, _position(portfolio_id, 7, stop=None))


def test_a_closed_position_may_have_no_stop(db_session: Session) -> None:
    portfolio_id = _portfolio(db_session)

    row = record_position(
        db_session, _position(portfolio_id, 7, status=PositionStatus.CLOSED, stop=None)
    )
    db_session.flush()

    assert row.stop_price is None


def test_a_second_snapshot_for_one_session_is_refused(db_session: Session) -> None:
    """A snapshot is a fact about a session, not a running total."""
    portfolio_id = _portfolio(db_session)
    record_portfolio_snapshot(db_session, _state(portfolio_id), session_date=SESSION)
    db_session.flush()

    with pytest.raises(SnapshotExists, match="contradiction"):
        record_portfolio_snapshot(db_session, _state(portfolio_id), session_date=SESSION)


def test_a_non_positive_initial_capital_is_refused(db_session: Session) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        record_portfolio(
            db_session,
            name="broken",
            mode=TradingMode.PAPER,
            initial_capital=Decimal(0),
            inception_date=dt.date(2026, 1, 2),
        )


# -- the figures the snapshot derives ---------------------------------------


def test_drawdown_is_derived_so_it_cannot_disagree_with_the_equity(
    db_session: Session,
) -> None:
    portfolio_id = _portfolio(db_session)

    row = record_portfolio_snapshot(
        db_session,
        _state(portfolio_id),
        session_date=SESSION,
        peak_equity=Decimal(125_000),
    )
    db_session.flush()

    assert row.peak_equity == Decimal(125_000)
    assert row.drawdown_pct == pytest.approx(0.2)


def test_without_a_peak_the_drawdown_is_null_not_zero(db_session: Session) -> None:
    """**Unknown drawdown and no drawdown are different facts.**

    Zero says the portfolio is at its high. Null says nobody told us where the
    high was, and a risk view that read the first for the second would report a
    book at its peak while it sat 40% below one.
    """
    portfolio_id = _portfolio(db_session)

    row = record_portfolio_snapshot(db_session, _state(portfolio_id), session_date=SESSION)
    db_session.flush()

    assert row.drawdown_pct is None


def test_market_value_is_split_long_from_short(db_session: Session) -> None:
    portfolio_id = _portfolio(db_session)
    held = _position(portfolio_id, 7)

    row = record_portfolio_snapshot(db_session, _state(portfolio_id, held), session_date=SESSION)
    db_session.flush()

    # Marked at 110, not at the 100 entry.
    assert row.long_market_value == Decimal(100) * Decimal(110)
    assert row.short_market_value == Decimal(0)
    assert row.unrealised_pnl == Decimal(100) * Decimal(10)


# -- the risk snapshot ------------------------------------------------------


def test_a_risk_snapshot_keeps_which_limits_broke_not_how_many(
    db_session: Session,
) -> None:
    """ "Which limits" and "how many" are different questions.

    Only the first is actionable, so the breaches are stored as a list.
    """
    portfolio_id = _portfolio(db_session)
    value = RiskSnapshotValue(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        session_date=SESSION,
        equity=Decimal(100_000),
        cash=Decimal(40_000),
        open_risk=Decimal(6_280),
        heat=0.0628,
        position_count=6,
        largest_position_pct=0.0999,
        gross_exposure_pct=0.514,
        sector_exposures={"Manufacturing": 0.32, "Services": 0.10},
        max_pairwise_correlation=0.83,
        drawdown_from_peak=0.04,
        limit_breaches=("portfolio_heat", "sector_exposure"),
        strategy_config_digest="abc123",
    )

    row = record_risk_snapshot(db_session, value)
    db_session.flush()

    assert row.heat_pct == pytest.approx(0.0628)
    assert row.limit_breaches == ["portfolio_heat", "sector_exposure"]
    assert row.sector_exposures == {"Manufacturing": 0.32, "Services": 0.10}
    assert row.drawdown_from_peak_pct == pytest.approx(0.04)


def test_a_second_risk_snapshot_for_one_session_is_refused(db_session: Session) -> None:
    portfolio_id = _portfolio(db_session)
    value = RiskSnapshotValue(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        session_date=SESSION,
        equity=Decimal(100_000),
        cash=Decimal(40_000),
        open_risk=Decimal(0),
        heat=0.0,
        position_count=0,
        largest_position_pct=None,
        gross_exposure_pct=None,
        sector_exposures={},
        max_pairwise_correlation=None,
        drawdown_from_peak=None,
        limit_breaches=(),
        strategy_config_digest=None,
    )
    record_risk_snapshot(db_session, value)
    db_session.flush()

    with pytest.raises(SnapshotExists):
        record_risk_snapshot(db_session, value)


def test_the_portfolio_row_the_runs_have_been_pointing_at_now_exists(
    db_session: Session,
) -> None:
    """Every run hardcodes `portfolio_id=1` against a row that did not exist."""
    row = record_portfolio(
        db_session,
        name="swing",
        mode=TradingMode.PAPER,
        initial_capital=Decimal(100_000),
        inception_date=dt.date(2026, 1, 2),
    )
    db_session.flush()

    assert row.id is not None
    assert row.mode == "paper"
    assert row.is_active is True
