"""Writers for the four portfolio tables that had none, and the reader that pays for them.

**`portfolios`, `positions`, `portfolio_snapshots` and `risk_snapshots` were
declared in `storage/tables.py` and constructed nowhere in `src/tradeit`.**
Every run hardcoded ``portfolio_id=1`` pointing at a row that did not exist, so
every `orders` and `executions` row in all three decision records carries a
dangling foreign key. Measured 2026-11-19.

That absence is the root of three separate things, which is why it is worth
closing rather than working around:

* **`tradeit plan` reads its holdings from a JSON file the operator edits**,
  because there is nowhere else to read them from
  ([ADR-0039](../../../docs/adr/0039-a-refusal-must-name-the-limit-that-caused-it.md)).
* **A paper session cannot resume.** `LiveShapedSession.__post_init__` sets cash
  to `initial_capital` and lots to empty, and no loader exists — so Phase 11's
  closure is real for *equivalence* (1,457 of 1,457 fills agreed) and not for
  *operation*: every session starts flat, in memory, and nothing rebuilds it
  tomorrow.
* **The governed research loop Phase 12 still owes** needs a portfolio whose
  state survives the process that produced it.

**The reader is the point, not the writers.** Four `session.add` calls are
trivial; what they are *for* is :func:`load_portfolio_state`, which rebuilds a
:class:`PortfolioState` from the rows so the next session continues from the
last one instead of starting over.

Two rules this module keeps
---------------------------

**A position without a stop is not written.** `PositionState` says a position
without one "has undefined risk, cannot be sized, cannot be aggregated into
portfolio heat, and is therefore not a position this system will hold", and the
column is nullable only because a *closed* position no longer needs one. Writing
an open position with a null stop would put a row in the database that the
domain refuses to construct.

**A snapshot is a fact about a session, not a running total.** `session_date`
plus `portfolio_id` identifies one, and writing a second for the same pair is a
contradiction rather than an update — so it raises instead of silently making
the later row win.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.enums import PositionStatus, Side, TradingMode
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.risk.base import RiskSnapshot as RiskSnapshotValue
from tradeit.storage.tables import Portfolio, PortfolioSnapshot, Position, RiskSnapshot

__all__ = [
    "SnapshotExists",
    "load_portfolio_state",
    "record_portfolio",
    "record_portfolio_snapshot",
    "record_position",
    "record_risk_snapshot",
]


class SnapshotExists(ValueError):
    """A second snapshot for one portfolio and session, which is a contradiction."""


def record_portfolio(
    session: Session,
    *,
    name: str,
    mode: TradingMode,
    initial_capital: Decimal,
    inception_date: dt.date,
    base_currency: str = "USD",
    strategy_id: int | None = None,
    broker_account_ref: str | None = None,
) -> Portfolio:
    """Create the row every other table has been pointing at.

    ``mode`` is required and has no default. A portfolio that does not say
    whether it is backtest, paper or live is the one ambiguity this system can
    least afford, and :class:`TradingMode` is the enum the interlock reads.
    """
    if initial_capital <= 0:
        raise ValueError(f"initial capital must be positive, got {initial_capital}")
    row = Portfolio(
        name=name,
        mode=str(mode),
        strategy_id=strategy_id,
        base_currency=base_currency,
        initial_capital=initial_capital,
        inception_date=inception_date,
        broker_account_ref=broker_account_ref,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def record_position(
    session: Session,
    position: PositionState,
    *,
    highest_since_entry: Decimal | None = None,
    sessions_held: int | None = None,
    partial_profit_taken: bool | None = None,
) -> Position:
    """Persist one position exactly as the domain holds it.

    Takes the :class:`PositionState` rather than loose fields so the row cannot
    disagree with the object the cycle actually decided on — the same reason
    :func:`~tradeit.observability.record.record_order` takes an
    :class:`OrderRequest`.

    **The three ladder fields are separate arguments, and default to None.**
    They are not on ``PositionState`` because that value is shared with the
    backtester and the risk layer and must not grow execution-only fields; they
    live on ``execution.live_shape.Lot``, whose docstring says losing them
    "would reset the stop ladder on every restart". A ``None`` means *nobody
    recorded this*, and `LiveShapedSession` refuses to resume such a position
    rather than resuming it from a default — see migration
    ``0022_position_ladder_state``.
    """
    if position.status is PositionStatus.OPEN and position.stop_price is None:
        raise ValueError(
            f"open position on instrument {position.instrument_id} has no stop; "
            "a position without one has undefined risk and cannot be aggregated "
            "into portfolio heat, so it is not written"
        )
    row = Position(
        portfolio_id=position.portfolio_id,
        instrument_id=position.instrument_id,
        side=str(position.side),
        status=str(position.status),
        quantity=position.quantity,
        average_entry_price=position.average_entry_price,
        stop_price=position.stop_price,
        initial_stop_price=position.initial_stop_price,
        target_price=position.target_price,
        opened_on=position.opened_on,
        closed_on=position.closed_on,
        realised_pnl=position.realised_pnl,
        pyramid_entries=position.pyramid_entries,
        highest_since_entry=highest_since_entry,
        sessions_held=sessions_held,
        partial_profit_taken=partial_profit_taken,
    )
    session.add(row)
    return row


def _refuse_duplicate(session: Session, table: type, portfolio_id: int, day: dt.date) -> None:
    existing = session.execute(
        select(table.id).where(  # type: ignore[attr-defined]
            table.portfolio_id == portfolio_id,  # type: ignore[attr-defined]
            table.session_date == day,  # type: ignore[attr-defined]
        )
    ).first()
    if existing is not None:
        raise SnapshotExists(
            f"{table.__tablename__} already holds portfolio {portfolio_id} for {day}; "  # type: ignore[attr-defined]
            "a second row for one session is a contradiction, not an update"
        )


def record_portfolio_snapshot(
    session: Session,
    state: PortfolioState,
    *,
    session_date: dt.date,
    peak_equity: Decimal | None = None,
    realised_pnl_to_date: Decimal = Decimal(0),
) -> PortfolioSnapshot:
    """What the portfolio was worth at the close of one session.

    ``CyclePlan.projected`` is exactly this shape, which is why this is the
    cheapest of the four writers to make honest: the cycle already reports "the
    portfolio as it would stand" rather than leaving the caller to reconstruct
    it and get subtly wrong.

    ``drawdown_pct`` is derived here rather than stored by the caller, so it
    cannot disagree with the equity and peak beside it. With no peak supplied it
    is **null, not zero** — unknown drawdown and no drawdown are different
    facts.
    """
    _refuse_duplicate(session, PortfolioSnapshot, state.portfolio_id, session_date)
    long_value = sum(
        (
            position.market_value(
                state.last_prices.get(position.instrument_id, position.average_entry_price)
            )
            for position in state.open_positions
            if position.side is Side.LONG
        ),
        Decimal(0),
    )
    short_value = sum(
        (
            position.market_value(
                state.last_prices.get(position.instrument_id, position.average_entry_price)
            )
            for position in state.open_positions
            if position.side is not Side.LONG
        ),
        Decimal(0),
    )
    drawdown = None
    if peak_equity is not None and peak_equity > 0:
        drawdown = float(max((peak_equity - state.equity) / peak_equity, Decimal(0)))
    row = PortfolioSnapshot(
        portfolio_id=state.portfolio_id,
        session_date=session_date,
        as_of=state.as_of,
        cash=state.cash,
        equity=state.equity,
        long_market_value=long_value,
        short_market_value=short_value,
        position_count=state.position_count,
        realised_pnl_to_date=realised_pnl_to_date,
        # Equity less cash less what was put in is not derivable from a single
        # snapshot, so the unrealised figure is the marked value against cost.
        unrealised_pnl=sum(
            (
                position.market_value(
                    state.last_prices.get(position.instrument_id, position.average_entry_price)
                )
                - position.quantity * position.average_entry_price
                for position in state.open_positions
            ),
            Decimal(0),
        ),
        peak_equity=peak_equity,
        drawdown_pct=drawdown,
    )
    session.add(row)
    return row


def record_risk_snapshot(session: Session, snapshot: RiskSnapshotValue) -> RiskSnapshot:
    """Persist what `risk.snapshot.build_snapshot` already computes.

    The domain value and this table have carried the same fields since Phase 8
    and nothing connected them, so `build_snapshot`'s only caller was a script
    that printed the result and dropped it.
    """
    _refuse_duplicate(session, RiskSnapshot, snapshot.portfolio_id, snapshot.session_date)
    row = RiskSnapshot(
        portfolio_id=snapshot.portfolio_id,
        session_date=snapshot.session_date,
        as_of=snapshot.as_of,
        equity=snapshot.equity,
        open_risk=snapshot.open_risk,
        heat_pct=snapshot.heat,
        position_count=snapshot.position_count,
        largest_position_pct=snapshot.largest_position_pct,
        gross_exposure_pct=snapshot.gross_exposure_pct,
        max_pairwise_correlation=snapshot.max_pairwise_correlation,
        drawdown_from_peak_pct=snapshot.drawdown_from_peak,
        sector_exposures=dict(snapshot.sector_exposures),
        # Stored as a list rather than a count: "which limits" and "how many"
        # are different questions and only the first is actionable.
        limit_breaches=list(snapshot.limit_breaches),
        strategy_config_digest=snapshot.strategy_config_digest,
    )
    session.add(row)
    return row


def load_portfolio_state(
    session: Session,
    portfolio_id: int,
    *,
    as_of: dt.datetime,
    last_prices: dict[int, Decimal] | None = None,
) -> PortfolioState | None:
    """Rebuild a portfolio from its rows, so the next session continues the last.

    **This is what the writers above are for.** Returns ``None`` when the
    portfolio has no snapshot — which is a different answer from an empty
    portfolio, and the caller must not conflate them: "we have never recorded
    this portfolio" and "this portfolio holds nothing" lead to opposite
    decisions, and the second is a legitimate state to resume from.

    Cash and equity come from the **most recent snapshot at or before**
    ``as_of``; positions are the rows still open. Equity is taken as recorded
    rather than recomputed from `last_prices`, because a snapshot is a fact
    about the session that produced it and recomputing it here would silently
    re-mark a closed book at today's prices.
    """
    snapshot = session.execute(
        select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.as_of <= as_of,
        )
        .order_by(PortfolioSnapshot.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snapshot is None:
        return None

    rows = session.execute(
        select(Position).where(
            Position.portfolio_id == portfolio_id,
            Position.status == str(PositionStatus.OPEN),
        )
    ).scalars()
    positions = tuple(
        PositionState(
            position_id=int(row.id),
            portfolio_id=int(row.portfolio_id),
            instrument_id=int(row.instrument_id),
            side=Side(row.side),
            status=PositionStatus(row.status),
            quantity=row.quantity,
            average_entry_price=row.average_entry_price,
            stop_price=row.stop_price,
            opened_on=row.opened_on,
            closed_on=row.closed_on,
            realised_pnl=row.realised_pnl,
            initial_stop_price=row.initial_stop_price,
            target_price=row.target_price,
            pyramid_entries=row.pyramid_entries,
        )
        for row in rows
    )
    return PortfolioState(
        portfolio_id=portfolio_id,
        as_of=snapshot.as_of,
        cash=snapshot.cash,
        equity=snapshot.equity,
        positions=positions,
        last_prices=dict(last_prices or {}),
    )
