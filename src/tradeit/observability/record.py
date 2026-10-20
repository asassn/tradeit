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

import dataclasses
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.backtesting.base import BacktestResult, BacktestSpec, MonteCarloResult
from tradeit.backtesting.walkforward import WalkForwardReport
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
from tradeit.storage.tables import (
    MonteCarloRun as MonteCarloRunRow,
)

__all__ = [
    "DatabaseRecorder",
    "DecisionChain",
    "reconstruct",
    "record_backtest_result",
    "record_execution",
    "record_monte_carlo",
    "record_order",
    "record_score",
    "record_trade",
    "record_walk_forward",
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
    mandate: str,
    timeframe: str,
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
    if not 0.0 <= total <= 1.0:
        # **The schema says so**: `opportunity_scores` carries
        # `CheckConstraint("total >= 0 AND total <= 1", name="ck_score_range")`.
        # Refused here with the value and the reason rather than left to SQLite,
        # which raises an opaque "CHECK constraint failed: ck_score_range" from
        # inside a bulk insert and names neither the instrument nor the number.
        #
        # This is ADR-0027's scale problem from the other side. That decision
        # made `OpportunityFitRanker` refuse an out-of-range total because
        # blending one makes `standalone_weight` describe a mixture that is not
        # happening; the schema had independently declared the same range. A
        # caller whose scorer is unbounded -- `MovingAverageCross` puts a raw
        # spread ratio here, which exceeds 1.0 when a 50-day mean doubles a
        # 200-day one, as happens in a 2009-style recovery -- normalises against
        # a **declared** range and says what range it used.
        raise ValueError(
            f"score {total} for instrument {instrument_id} on {session_date} is "
            "outside [0, 1], which ck_score_range forbids. Normalise it against a "
            "declared range first and record what range was used; a scorer whose "
            "output has no stated scale has not produced a score"
        )
    row = OpportunityScore(
        instrument_id=instrument_id,
        session_date=session_date,
        direction=direction,
        total=total,
        components=dict(components),
        features=dict(features) if features else None,
        rank=rank,
        mandate=mandate,
        timeframe=timeframe,
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
    #: The range this run's standalone scorer produces, when it is not already
    #: 0-1. Scores are mapped into [0, 1] against it and clamped at the ends.
    #:
    #: ``None`` means "the scorer already produces a score", and an
    #: out-of-range value is then **refused** rather than silently rescaled --
    #: because rescaling against a range nobody declared is inventing the
    #: scale, which is what ADR-0027 refused for the ranker.
    score_range: tuple[float, float] | None = None
    #: The horizon this run's scores are conclusions about.
    #:
    #: Defaults to Swing/1d because that is what every run this project has
    #: performed actually is -- and it is a **default on the recorder, set once
    #: per run by the caller**, not a default on `record_score`, which requires
    #: it. A per-score default would let a caller forget the argument and get
    #: Swing silently; a per-run one is a statement the caller makes about the
    #: whole run and can be read back off the recorder.
    mandate: str = "swing"
    timeframe: str = "1d"

    def _scaled(
        self, total: float, components: Mapping[str, object]
    ) -> tuple[float, dict[str, object]]:
        """The total on the 0-1 scale the schema requires, **and its components.**

        Both, together, and that is the whole point. A first version scaled the
        total alone and left the components untouched, so a persisted score read
        `total 0.0003` beside a single component of `0.000149` — the explanation
        no longer summing to the number it explains, which is exactly the
        failure `OpportunityScore.reconciles` exists to catch.
        `ranking.normalise_total` had already been careful about this for the
        same reason; this had not.

        Components are scaled by the same factor rather than recomputed, so the
        shares stay in proportion. A clamped total scales its components by the
        clamped factor, so they still sum to what is stored.
        """
        if self.score_range is None:
            return total, dict(components)
        lower, upper = self.score_range
        if upper <= lower:
            raise ValueError(
                f"score_range upper {upper} must exceed lower {lower}; an inverted "
                "range maps every score to the same value"
            )
        scaled = max(0.0, min(1.0, (total - lower) / (upper - lower)))
        if total == 0:
            # Nothing to scale the parts by, and a zero total is explained by
            # zero-weight parts rather than by dividing by zero.
            return scaled, {name: 0.0 for name in components}
        factor = scaled / total
        rescaled: dict[str, object] = {}
        for name, value in components.items():
            rescaled[name] = value * factor if isinstance(value, (int, float)) else value
        return scaled, rescaled

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
            scaled_total, scaled_components = self._scaled(total, components)
            record_score(
                self.session,
                instrument_id=instrument_id,
                session_date=decision.session_date,
                direction=direction,
                total=scaled_total,
                components=scaled_components,
                run_manifest_id=self.run_manifest_id,
                mandate=self.mandate,
                timeframe=self.timeframe,
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


def record_monte_carlo(
    session: Session,
    result: MonteCarloResult,
    *,
    backtest_run_id: int,
) -> MonteCarloRunRow:
    """A distributional study, with the seed that reproduces it.

    `monte_carlo_runs` existed with no writer, like the four links before it.
    Keyed on ``backtest_run_id`` because a Monte Carlo study is **about** a
    backtest rather than a thing in its own right: the trades it resamples are
    that run's, and a study whose subject could not be identified would be a
    distribution of nothing.

    ``seed`` and ``iterations`` come from the spec and are not defaulted here.
    A resampling study is only reproducible if the seed is recorded, and a
    recorded distribution without one is a number nobody can check — which this
    project treats as worse than no number.

    ``iterations_completed`` is stored **separately from** ``iterations``,
    because they differ when a study is cut short, and a report that showed only
    the requested count would describe a study that did not happen.
    """
    if result.iterations_completed > result.spec.iterations:
        raise ValueError(
            f"{result.iterations_completed} iterations completed against "
            f"{result.spec.iterations} requested; a study cannot overrun its own spec "
            "and the discrepancy would make the seed useless for reproducing it"
        )
    row = MonteCarloRunRow(
        backtest_run_id=backtest_run_id,
        method=result.spec.method,
        iterations=result.spec.iterations,
        seed=result.spec.seed,
        parameters={
            "block_size": result.spec.block_size,
            "perturbation_pct": result.spec.perturbation_pct,
        },
        # Keys stringified explicitly, and this is **cosmetic rather than
        # load-bearing** -- stated because the first version of this comment
        # claimed otherwise and a mutant removing the conversion passed every
        # test, correctly. JSON object keys are strings, so the serialisation
        # layer converts them whichever way they are handed over; a reader
        # comparing a loaded `{"5": ...}` against `{5: ...}` finds nothing
        # either way. The conversion is here so the write matches the read at
        # the call site, not because it prevents a defect.
        return_percentiles={str(k): v for k, v in result.return_percentiles.items()},
        drawdown_percentiles={str(k): v for k, v in result.drawdown_percentiles.items()},
        ruin_probability=result.ruin_probability,
        iterations_completed=result.iterations_completed,
    )
    session.add(row)
    return row


def record_walk_forward(
    session: Session,
    report: WalkForwardReport,
    spec: BacktestSpec,
    *,
    run_manifest_id: int,
) -> tuple[BacktestRunRow, ...]:
    """Every fold of a walk-forward, as what the schema already says it is.

    **No new table, and that is a finding rather than a shortcut.**
    `backtest_runs` already carries ``walk_forward_window`` and
    ``is_out_of_sample``, which is the schema stating that a walk-forward *is* a
    series of backtest runs distinguished by their window and which side of the
    split they sit on. Adding a `walk_forward_runs` table would have duplicated
    that and given the same fact two homes.

    Two rows per fold -- the in-sample side and the out-of-sample side -- with
    ``is_out_of_sample`` the only thing distinguishing them. A single row per
    fold holding both would have had to pick which metrics to store, and the
    degradation between them is the entire point of the exercise.

    ``name`` carries the mode and the window index, so folds are
    distinguishable without joining: an anchored and a rolling run of the same
    strategy are different studies and must not look alike in a listing.
    """
    written: list[BacktestRunRow] = []
    for index, window in enumerate(report.results):
        for side, result, out_of_sample in (
            ("is", window.in_sample, False),
            ("oos", window.out_of_sample, True),
        ):
            fold_spec = dataclasses.replace(
                spec,
                name=f"{spec.name}/wf-{report.mode}-{index}-{side}",
                start=window.window.train_start if not out_of_sample else window.window.test_start,
                end=window.window.train_end if not out_of_sample else window.window.test_end,
            )
            row = record_backtest_result(
                session,
                fold_spec,
                result,
                run_manifest_id=run_manifest_id,
                is_out_of_sample=out_of_sample,
            )
            row.walk_forward_window = index
            written.append(row)
    session.flush()
    return tuple(written)
