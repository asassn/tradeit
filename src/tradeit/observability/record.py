"""Writing down what the system decided, so it can be asked later.

Phase 12's observability requirement is specific:

> The system must be able to reconstruct its own decisions: what data arrived,
> which signal fired, which strategy version acted, which risk gate approved or
> rejected, what order was submitted, what execution occurred, what P&L
> resulted. **A live trading system that cannot explain what it did is not
> acceptable.**

The audit of 2026-10-19 measured the state of that chain and found the same
shape Phase 11 found in the journal: **the tables exist and nothing writes
them.** `Order`, `Execution`, `Trade` and `OpportunityScore` are declared in
`storage/tables.py` and constructed nowhere else in `src/`. Four of the seven
links had no writer, so the chain could not be walked at all.

This module is those four writers. `RiskSnapshot` already had one
(`risk/snapshot.py`, Phase 8) and `JournalEntry` has one
(`execution/journal.py`, Phase 11).

The gate decision has no table, and does not get one
----------------------------------------------------

There is no `risk_verdicts` table, and adding one was rejected. `RiskVerdict`
already carries `binding_rule` — *"the one piece of information that turns 'the
system isn't buying anything' into an actionable answer"* — and the **journal**
is the append-only record built for exactly this, with `rejected` candidates in
its context. A second table holding the same fact would be a second place for
it to be wrong. Gate decisions are recorded through
:func:`~tradeit.execution.journal.record_entry`.

Nothing commits
---------------

Every function here `add`s and returns the row. A decision record belongs in
the same transaction as the decision, exactly as the journal does: a chain that
survived a rollback would describe orders that were never placed, which is a
worse record than none because it is confidently wrong.

Reconstruction names what is missing
------------------------------------

:func:`reconstruct` returns a :class:`DecisionChain` whose `missing` lists the
links with no rows. **A reconstruction that cannot distinguish "nothing
happened" from "nothing was recorded" does not satisfy the requirement** — it
would let an unlogged live session look like a quiet one, which is the single
most dangerous confusion available here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.core.enums import OrderStatus
from tradeit.execution.base import Fill, OrderRequest
from tradeit.execution.journal import JournalEntryKind, RejectedCandidate, record_entry
from tradeit.observability.base import RecordedDecision
from tradeit.storage.tables import (
    BacktestRun as BacktestRunRow,
)
from tradeit.storage.tables import (
    BacktestTrade as BacktestTradeRow,
)
from tradeit.storage.tables import Execution, OpportunityScore, Order, Trade

__all__ = [
    "DatabaseRecorder",
    "DecisionChain",
    "reconstruct",
    "record_backtest_result",
    "record_execution",
    "record_order",
    "record_score",
    "record_trade",
]


def record_score(
    session: Session,
    *,
    instrument_id: int,
    session_date: dt.date,
    direction: str,
    total: float,
    components: Mapping[str, object],
    run_manifest_id: int,
    features: Mapping[str, object] | None = None,
    rank: int | None = None,
) -> OpportunityScore:
    """Which signal fired, and the breakdown that explains the number.

    ``components`` is required and must be non-empty. A score with no
    components is the exact failure `OpportunityScore.reconciles` exists to
    catch in the domain object — *"an explanation reconstructed later from a
    total is a rationalisation"* — and persisting one would put that failure
    beyond the reach of the property.
    """
    if not components:
        raise ValueError(
            f"a persisted score for instrument {instrument_id} needs its components; "
            "a total with no breakdown cannot be explained later and the explanation "
            "is the deliverable"
        )
    row = OpportunityScore(
        instrument_id=instrument_id,
        session_date=session_date,
        direction=direction,
        total=total,
        components=dict(components),
        features=dict(features) if features else None,
        rank=rank,
        run_manifest_id=run_manifest_id,
    )
    session.add(row)
    return row


def record_order(
    session: Session,
    request: OrderRequest,
    *,
    submitted_at: dt.datetime,
    status: OrderStatus = OrderStatus.PENDING_NEW,
    broker_order_id: str | None = None,
    run_manifest_id: int | None = None,
    rejection_reason: str | None = None,
) -> Order:
    """What order was submitted, keyed on the id we generated for it.

    Takes the :class:`OrderRequest` rather than loose fields, so the row cannot
    disagree with the object that was actually sent. ``client_order_id`` is the
    column's unique key, which is why the engine's ids had to carry their
    session — see `PHASE_11_AUDIT_2026-10-18.md`.
    """
    row = Order(
        portfolio_id=request.portfolio_id,
        instrument_id=request.instrument_id,
        client_order_id=request.client_order_id,
        broker_order_id=broker_order_id,
        side=str(request.side),
        order_type=str(request.order_type),
        time_in_force=str(request.time_in_force),
        status=str(status),
        quantity=request.quantity,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        intent=request.intent or None,
        submitted_at=submitted_at,
        rejection_reason=rejection_reason,
        run_manifest_id=run_manifest_id,
        position_id=request.parent_position_id,
    )
    session.add(row)
    return row


def record_execution(
    session: Session,
    fill: Fill,
    *,
    order_id: int,
    portfolio_id: int,
    session_date: dt.date,
    broker_execution_id: str | None = None,
) -> Execution:
    """What execution occurred, against the order that produced it.

    ``order_id`` is the database id, not the broker's string: `executions.order_id`
    is a foreign key, and a chain tied by a vendor's identifier would break the
    moment a second venue used the same format.

    Partial fills are separate rows, as `Fill`'s own docstring requires — *"the
    average price of a position built across three fills at three prices is only
    reconstructible if all three were kept"*.
    """
    row = Execution(
        order_id=order_id,
        portfolio_id=portfolio_id,
        instrument_id=fill.instrument_id,
        side=str(fill.side),
        quantity=fill.quantity,
        price=fill.price,
        commission=fill.commission,
        slippage=fill.slippage,
        filled_at=fill.filled_at,
        session_date=session_date,
        venue=fill.venue or None,
        broker_execution_id=broker_execution_id,
    )
    session.add(row)
    return row


def record_trade(
    session: Session,
    *,
    portfolio_id: int,
    position_id: int,
    instrument_id: int,
    side: str,
    quantity: Decimal,
    entry_date: dt.date,
    entry_price: Decimal,
    exit_date: dt.date,
    exit_price: Decimal,
    exit_reason: str,
    gross_pnl: Decimal,
    costs: Decimal,
    net_pnl: Decimal,
    return_pct: float,
    holding_sessions: int,
    r_multiple: float | None = None,
    mae: Decimal | None = None,
    mfe: Decimal | None = None,
    score_at_entry: float | None = None,
) -> Trade:
    """What P&L resulted, and against which score.

    ``gross_pnl``, ``costs`` and ``net_pnl`` are all taken rather than two being
    derived, and then **checked**: the arithmetic is the one thing a reader will
    trust without looking, so a row whose parts do not add up is refused at the
    point of writing rather than discovered in a report.

    ``exit_date`` before ``entry_date`` is refused for the same reason — a
    negative holding period is not a number anybody can act on, and it reaches
    reports as a plausible-looking outlier.
    """
    if exit_date < entry_date:
        raise ValueError(
            f"exit {exit_date} precedes entry {entry_date} for instrument {instrument_id}; "
            "a negative holding period reaches a report as a plausible outlier"
        )
    if abs((gross_pnl - costs) - net_pnl) > Decimal("0.01"):
        raise ValueError(
            f"gross {gross_pnl} - costs {costs} != net {net_pnl} for instrument "
            f"{instrument_id}; a P&L row whose parts do not add up is the number a "
            "reader trusts without checking"
        )
    row = Trade(
        portfolio_id=portfolio_id,
        position_id=position_id,
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=net_pnl,
        return_pct=return_pct,
        r_multiple=r_multiple,
        holding_sessions=holding_sessions,
        mae=mae,
        mfe=mfe,
        score_at_entry=score_at_entry,
    )
    session.add(row)
    return row


@dataclass(frozen=True, slots=True)
class DecisionChain:
    """Everything recorded for one portfolio on one session, and what was not."""

    portfolio_id: int
    session_date: dt.date
    scores: tuple[OpportunityScore, ...] = ()
    orders: tuple[Order, ...] = ()
    executions: tuple[Execution, ...] = ()
    trades: tuple[Trade, ...] = ()
    journal: tuple[object, ...] = ()
    #: Links with no rows at all. **Not the same as a quiet session.**
    missing: tuple[str, ...] = field(default=())

    @property
    def complete(self) -> bool:
        """Whether every link has at least one row.

        A quiet session is legitimately incomplete, so this is **not** a health
        check — it is the answer to "can this session's decisions be walked
        end to end", and `missing` says which links cannot.
        """
        return not self.missing

    def describe(self) -> str:
        lines = [
            f"portfolio {self.portfolio_id} on {self.session_date}:",
            f"  scores     {len(self.scores)}",
            f"  orders     {len(self.orders)}",
            f"  executions {len(self.executions)}",
            f"  trades     {len(self.trades)}",
            f"  journal    {len(self.journal)}",
        ]
        if self.missing:
            lines.append(
                f"  NOT RECORDED: {', '.join(self.missing)} — which is a different "
                "fact from nothing having happened"
            )
        return "\n".join(lines)


def reconstruct(session: Session, portfolio_id: int, session_date: dt.date) -> DecisionChain:
    """Walk one session's decisions, and name the links that hold nothing.

    Journal entries are read through the same query rather than assumed: the
    journal is where gate decisions live, so a chain that omitted it would be
    missing the "which risk gate approved or rejected" link entirely.
    """
    from tradeit.storage.tables import JournalEntry

    scores = tuple(
        session.execute(
            select(OpportunityScore).where(OpportunityScore.session_date == session_date)
        )
        .scalars()
        .all()
    )
    # Scoped to the session via `submitted_at`, because `orders` carries no
    # session_date column. Filtering on portfolio alone returned every order the
    # portfolio had ever placed: a first run reported 189 orders on a single
    # session, which is impossible, and reported '504 of 504 sessions can be
    # walked end to end' -- true only because every session returned every order.
    day_start = dt.datetime.combine(session_date, dt.time(0, 0), tzinfo=dt.UTC)
    day_end = day_start + dt.timedelta(days=1)
    orders = tuple(
        session.execute(
            select(Order).where(
                Order.portfolio_id == portfolio_id,
                Order.submitted_at >= day_start,
                Order.submitted_at < day_end,
            )
        )
        .scalars()
        .all()
    )
    order_ids = [order.id for order in orders]
    executions: tuple[Execution, ...] = ()
    if order_ids:
        executions = tuple(
            session.execute(
                select(Execution).where(
                    Execution.session_date == session_date,
                    Execution.order_id.in_(order_ids),
                )
            )
            .scalars()
            .all()
        )
    trades = tuple(
        session.execute(
            select(Trade).where(Trade.portfolio_id == portfolio_id, Trade.exit_date == session_date)
        )
        .scalars()
        .all()
    )
    journal = tuple(
        session.execute(
            select(JournalEntry).where(
                JournalEntry.session_date == session_date,
                JournalEntry.portfolio_id == portfolio_id,
            )
        )
        .scalars()
        .all()
    )
    missing = tuple(
        name
        for name, rows in (
            ("scores", scores),
            ("orders", orders),
            ("executions", executions),
            ("trades", trades),
            ("journal", journal),
        )
        if not rows
    )
    return DecisionChain(
        portfolio_id=portfolio_id,
        session_date=session_date,
        scores=scores,
        orders=orders,
        executions=executions,
        trades=trades,
        journal=journal,
        missing=missing,
    )


@dataclass
class DatabaseRecorder:
    """Writes a session's decisions through this module's writers.

    ``run_manifest_id`` is required and has no default. A score row's manifest
    is what makes the result reproducible, and a recorder that invented one
    would produce exactly the "fabricated provenance wearing the shape of the
    real thing" the engine's own manifest docstring refuses.

    **Does not commit**, for the reason every writer here does not: the record
    belongs in the same transaction as whatever else the caller is doing.
    """

    session: Session
    run_manifest_id: int

    def _order_by_client_id(self, client_order_id: str) -> Order | None:
        """The order row for an id placed on an earlier session.

        **Needed because fills arrive a session after the order was placed** —
        the engine settles yesterday's decisions against today's prices, so by
        the time an execution exists its order was written by a previous
        `record` call and is not in this one's map. A first version looked only
        at the current call and would have refused every real execution while
        passing its own unit test, where order and fill are handed over
        together.

        Keyed on `client_order_id` because that column is `unique=True`, which
        is what makes the lookup single-valued — and why the engine's ids had
        to start carrying their session.
        """
        return self.session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        ).scalar_one_or_none()

    def record(self, decision: RecordedDecision) -> None:
        for instrument_id, direction, total, components in decision.scores:
            record_score(
                self.session,
                instrument_id=instrument_id,
                session_date=decision.session_date,
                direction=direction,
                total=total,
                components=components,
                run_manifest_id=self.run_manifest_id,
            )
        by_client: dict[str, Order] = {}
        # `submitted_at` is stamped from the session rather than left NULL,
        # because `orders` has no session_date column and this is the only
        # thing tying an order to the day it was placed. Left NULL, a first
        # run reported 189 orders on a single session -- impossible -- and
        # '504 of 504 sessions can be walked end to end', true only because
        # every session returned every order.
        submitted = dt.datetime.combine(decision.session_date, dt.time(20, 0), tzinfo=dt.UTC)
        for request in decision.orders:
            by_client[request.client_order_id] = record_order(
                self.session,
                request,
                status=OrderStatus.NEW,
                run_manifest_id=self.run_manifest_id,
                submitted_at=submitted,
            )
        if by_client:
            # Flushed so the order rows have ids, which `executions.order_id`
            # needs as a foreign key. Flush is not commit: a rollback still
            # discards everything.
            self.session.flush()
        for client_order_id, fill in decision.fills:
            order = by_client.get(client_order_id) or self._order_by_client_id(client_order_id)
            if order is None:
                # A fill for an order that was never recorded anywhere. Refused
                # rather than written with a null order: an execution that
                # cannot be traced to its order is the link the chain exists
                # to provide.
                raise ValueError(
                    f"fill for {client_order_id!r} has no recorded order; an "
                    "execution that cannot be traced to its order defeats the chain"
                )
            record_execution(
                self.session,
                fill,
                order_id=order.id,
                portfolio_id=decision.portfolio_id,
                session_date=decision.session_date,
            )
        if decision.refused:
            record_entry(
                self.session,
                kind=JournalEntryKind.DECLINED,
                session_date=decision.session_date,
                portfolio_id=decision.portfolio_id,
                summary=f"{len(decision.refused)} candidate(s) refused by risk",
                rejected=tuple(
                    RejectedCandidate(instrument_id=iid, score=0.0, reason=rule)
                    for iid, rule in decision.refused
                ),
            )


def record_backtest_result(
    session: Session,
    spec: BacktestSpec,
    result: BacktestResult,
    *,
    run_manifest_id: int,
    is_out_of_sample: bool = False,
) -> BacktestRunRow:
    """The last link for a backtest: the run, its metrics, and every trade.

    **Not :func:`record_trade`, and the distinction is the schema's rather than
    a convenience.** ``trades`` carries a non-nullable foreign key into
    ``positions``, because a trade there is the closing of a position somebody
    actually held — that is the live and paper path, and `record_trade` serves
    it. ``backtest_trades`` keys on ``backtest_run_id`` instead and has no
    position at all, because a backtested trade never had one. Forcing backtest
    output into ``trades`` would have required inventing a position row per
    trade, which is fabricated provenance for the sake of a single foreign key.

    ``is_out_of_sample`` defaults to **False** and is the caller's to set. A
    default of True would quietly mark design-window runs as confirmations, and
    the ledger discipline this project runs on depends on that flag meaning what
    it says.

    Does not commit, like every writer here.
    """
    metrics = result.metrics
    run = BacktestRunRow(
        name=spec.name,
        run_manifest_id=run_manifest_id,
        status=str(result.status),
        start_date=spec.start,
        end_date=spec.end,
        universe=spec.universe[:64],
        initial_capital=spec.initial_capital,
        cost_model=spec.cost_model,
        fill_model=spec.fill_model,
        is_out_of_sample=is_out_of_sample,
        total_return_pct=metrics.total_return_pct if metrics else None,
        cagr=metrics.cagr if metrics else None,
        max_drawdown_pct=metrics.max_drawdown_pct if metrics else None,
        sharpe=metrics.sharpe if metrics else None,
        sortino=metrics.sortino if metrics else None,
        calmar=metrics.calmar if metrics else None,
        win_rate=metrics.win_rate if metrics else None,
        profit_factor=metrics.profit_factor if metrics else None,
        expectancy_r=metrics.expectancy_r if metrics else None,
    )
    session.add(run)
    session.flush()
    for trade in result.trades:
        if trade.exit_date < trade.entry_date:
            raise ValueError(
                f"trade on instrument {trade.instrument_id} exits {trade.exit_date} "
                f"before entering {trade.entry_date}; a negative holding period reaches "
                "a report as a plausible outlier"
            )
        session.add(
            BacktestTradeRow(
                backtest_run_id=run.id,
                instrument_id=trade.instrument_id,
                side=trade.side,
                quantity=trade.quantity,
                entry_date=trade.entry_date,
                entry_price=trade.entry_price,
                exit_date=trade.exit_date,
                exit_price=trade.exit_price,
                exit_reason=trade.exit_reason,
                gross_pnl=trade.gross_pnl,
                costs=trade.costs,
                net_pnl=trade.net_pnl,
                return_pct=trade.return_pct,
                r_multiple=trade.r_multiple,
                holding_sessions=trade.holding_sessions,
                mae=trade.mae,
                mfe=trade.mfe,
                score_at_entry=trade.score_at_entry,
                regime_at_entry=trade.regime_at_entry,
                sector_at_entry=trade.sector,
            )
        )
    return run
