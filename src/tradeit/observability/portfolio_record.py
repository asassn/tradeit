"""Writers for the four portfolio tables that had none, and the reader that pays for them.

**`portfolios`, `positions`, `portfolio_snapshots` and `risk_snapshots` were
declared in `storage/tables.py` and constructed nowhere in `src/tradeit`.**
Every run hardcoded ``portfolio_id=1`` pointing at a row that did not exist, so
every `orders` and `executions` row in all three decision records carries a
dangling foreign key. Measured 2026-11-19.

That absence is the root of three separate things, which is why it is worth
closing rather than working around:

* **`tradeit plan` read its holdings from a JSON file the operator edits**,
  because there was nowhere else to read them from
  ([ADR-0039](../../../docs/adr/0039-a-refusal-must-name-the-limit-that-caused-it.md)).
  Since 2026-11-26 it can read them from a book recorded here instead
  (`tradeit.decide.book`).
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.enums import PositionStatus, Side, TradingMode

# **Downhill, and deliberately this way round.** `observability` already
# imports `execution` (`base.py` takes `Fill`, `record.py` takes the journal)
# and nothing in `execution` imports `observability` -- the rule that keeps
# SQLAlchemy out of the backtest engine's import graph. `LadderState` is
# imported rather than re-declared so the thing written and the thing resumed
# cannot drift apart.
from tradeit.execution.live_shape import LadderState
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.risk.base import RiskSnapshot as RiskSnapshotValue
from tradeit.storage.tables import Portfolio, PortfolioSnapshot, Position, RiskSnapshot

__all__ = [
    "PositionSync",
    "SnapshotExists",
    "last_recorded_session",
    "load_ladder_state",
    "load_portfolio_state",
    "portfolio_named",
    "record_portfolio",
    "record_portfolio_snapshot",
    "record_position",
    "record_risk_snapshot",
    "recorded_peak_equity",
    "sync_positions",
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


# -- the book, kept current rather than logged ------------------------------


@dataclass(frozen=True, slots=True)
class PositionSync:
    """What one session's write did to the book."""

    opened: int
    updated: int
    closed: int

    def describe(self) -> str:
        return f"{self.opened} opened, {self.updated} updated, {self.closed} closed"


def sync_positions(
    session: Session,
    portfolio_id: int,
    positions: Sequence[PositionState],
    ladder: Mapping[int, LadderState],
    *,
    closed_on: dt.date,
) -> PositionSync:
    """Make ``positions`` describe the book as it now stands.

    **`positions` is the running total, not a log.** The table's own docstring
    says so -- "the immutable record is `executions`; this table is the running
    total" -- so one holding gets one row for its lifetime, updated in place,
    and a holding that has gone is *closed* rather than deleted.
    :func:`record_position` inserts unconditionally and is the right primitive
    for a first write; this is the one a session loop needs, because calling
    the other every session would write a new open row per session and the
    reader would restore the same holding several times over.

    **A holding whose ladder state is missing is refused, not written.**
    `LiveShapedSession._adopt` refuses to resume such a position -- correctly,
    because a trailing stop resumed from a reset high is looser than the one in
    force. Writing it incomplete here would move that failure to tomorrow,
    where it is a crash in a session that was meant to continue rather than an
    error in the session that caused it.

    ``initial_stop_price`` is written on the insert and never on an update. It
    is the premise the entry was decided on and defines R; a later write that
    could move it would make the recorded risk of a trade depend on when it was
    last saved.
    """
    rows = {
        int(row.instrument_id): row
        for row in session.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.status == str(PositionStatus.OPEN),
            )
        ).scalars()
    }
    opened = updated = 0
    for position in positions:
        if position.status is not PositionStatus.OPEN:
            continue
        state = ladder.get(position.instrument_id)
        if state is None:
            raise ValueError(
                f"refusing to write instrument {position.instrument_id} with no ladder "
                "state: the high since entry, sessions held and partial-profit flag are "
                "ours and cannot be recovered from a broker, so a row without them is "
                "one that cannot be resumed"
            )
        row = rows.pop(position.instrument_id, None)
        if row is None:
            record_position(
                session,
                position,
                highest_since_entry=state.highest,
                sessions_held=state.sessions_held,
                partial_profit_taken=state.partial_profit_taken,
            )
            opened += 1
            continue
        row.quantity = position.quantity
        row.average_entry_price = position.average_entry_price
        row.stop_price = position.stop_price
        row.target_price = position.target_price
        row.realised_pnl = position.realised_pnl
        row.pyramid_entries = position.pyramid_entries
        row.highest_since_entry = state.highest
        row.sessions_held = state.sessions_held
        row.partial_profit_taken = state.partial_profit_taken
        updated += 1

    for row in rows.values():
        # Closed, not deleted, and the quantity is left as it was: what the
        # position held is a fact about it, and the realised P&L belongs to
        # `executions`, which this writer does not read.
        row.status = str(PositionStatus.CLOSED)
        row.closed_on = closed_on
    session.flush()
    return PositionSync(opened=opened, updated=updated, closed=len(rows))


# -- what resume needs to ask the record ------------------------------------


def portfolio_named(session: Session, name: str) -> Portfolio | None:
    """The portfolio to continue, or ``None`` to create one.

    Looked up by name because that is what an operator has: a run is resumed
    by saying which portfolio it is, not by remembering a row id.
    """
    return session.execute(select(Portfolio).where(Portfolio.name == name)).scalar_one_or_none()


def last_recorded_session(session: Session, portfolio_id: int) -> dt.date | None:
    """The last session this portfolio has a snapshot for.

    A resumed run must start *after* it. Re-running a recorded session would
    hit :class:`SnapshotExists`, which is the right refusal and the wrong place
    to discover the mistake -- the caller can skip the sessions already done.
    """
    return session.execute(
        select(func.max(PortfolioSnapshot.session_date)).where(
            PortfolioSnapshot.portfolio_id == portfolio_id
        )
    ).scalar_one_or_none()


def recorded_peak_equity(
    session: Session, portfolio_id: int, *, as_of: dt.datetime | None = None
) -> Decimal | None:
    """The highest equity this portfolio has ever been recorded at.

    Derived from the record rather than held in memory, so it survives a
    restart. A peak that reset on every restart would make every recorded
    drawdown an understatement -- and understating drawdown is the direction
    that gets a strategy promoted.

    Takes the larger of each snapshot's ``equity`` and its own ``peak_equity``,
    because the first snapshot of a run has no prior peak to carry and is
    itself the peak so far.
    """
    query = select(
        func.max(PortfolioSnapshot.equity), func.max(PortfolioSnapshot.peak_equity)
    ).where(PortfolioSnapshot.portfolio_id == portfolio_id)
    if as_of is not None:
        query = query.where(PortfolioSnapshot.as_of <= as_of)
    equity, peak = session.execute(query).one()
    candidates = [value for value in (equity, peak) if value is not None]
    return max(candidates) if candidates else None


def load_ladder_state(session: Session, portfolio_id: int) -> dict[int, LadderState]:
    """The three fields a broker cannot report, for the positions still open.

    **A position missing any of them is left out rather than defaulted**, so
    `LiveShapedSession` refuses it by name instead of silently resuming a
    reset ladder. Rows written before migration ``0022_position_ladder_state``
    have all three null, and that is the case this absence is for.
    """
    rows = session.execute(
        select(
            Position.instrument_id,
            Position.highest_since_entry,
            Position.sessions_held,
            Position.partial_profit_taken,
        ).where(
            Position.portfolio_id == portfolio_id,
            Position.status == str(PositionStatus.OPEN),
        )
    ).all()
    return {
        int(instrument_id): LadderState(
            highest=highest,
            sessions_held=int(held),
            partial_profit_taken=bool(taken),
        )
        for instrument_id, highest, held, taken in rows
        if highest is not None and held is not None and taken is not None
    }
