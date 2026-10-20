"""The read side: answering *why*, from the record rather than from inference.

Phase 10 asks for a service over produced results, and its assistant
*"operates over the platform's own structured data and provenance and explains
decisions the system actually made -- it never invents a reason. If the system
cannot reconstruct why something happened, the correct answer is that it cannot,
and the assistant says so."*

This is the layer that makes that possible, and three of the roadmap's
constraints are enforced here rather than left to whoever builds the UI.

**Read-only, structurally.** There are no write routes, and
:func:`create_app` opens its session factory for reads. The roadmap says of the
news surface that it "has no trading authority"; the same applies to the whole
service. A dashboard that can place an order is a trading system with a chart
attached.

**No global buy/sell score, and none can be added here.** The per-ticker panel
must present Day, Swing and Retirement as *separate conclusions* --
*"a stock may be attractive long-term and unattractive as a swing; that is
information, not a contradiction"*. So nothing in this module aggregates across
horizons, and :func:`session_detail` returns each horizon's record separately
when more than one exists.

**It reports what it cannot explain.** Every session response carries
``missing``, straight from
:func:`~tradeit.observability.record.reconstruct`. An endpoint that returned an
empty list for an unlogged session would be the single most dangerous answer
this service can give: it reads as "nothing happened".

What it deliberately does not do
--------------------------------

**No valuation callout and no news.** Both are Phase 10 capabilities and both
need something that does not exist yet -- a fundamentals-backed valuation
history, and a news source with honest publication timestamps. A callout is
*"never shipped without the reasoning that produced it"*, so shipping the route
before the reasoning exists would invert that.

**No assistant.** The assistant is only as honest as this record, and the record
is one backtest old. The endpoints below are what it would read; it is not
written against a substrate this thin.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.observability.record import reconstruct
from tradeit.storage.tables import (
    BacktestRun,
    BacktestTrade,
    JournalEntry,
    OpportunityScore,
)

__all__ = ["create_app"]


def _get_session(request: Request) -> Iterator[Session]:
    """A session for one request, taken from the app rather than a closure.

    **Module-level on purpose, and this is not style.** This file uses
    ``from __future__ import annotations``, so every annotation is a string
    that FastAPI resolves against the *module* globals. A dependency alias
    defined inside ``create_app`` is invisible to that lookup, and the failure
    is silent in the worst way: FastAPI cannot see the ``Depends`` and
    reinterprets the parameter as a **required query parameter**, so every
    route answers `{"detail": [{"type": "missing", "loc": ["query",
    "session"]}]}`. Found by calling `/health` and getting a validation error
    instead of a status.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        # Rolled back rather than committed on the way out. Nothing here
        # writes, and a rollback makes that structural instead of trusted: a
        # future route that accidentally added a row could not persist it.
        session.rollback()
        session.close()


SessionDep = Annotated[Session, Depends(_get_session)]


def create_app(dsn: str) -> FastAPI:
    """Build the service over one decision record.

    ``dsn`` is required and has no default. The record and the corpus are
    different databases, and a service that defaulted to the configured DSN
    would read whichever one happened to be configured — the confusion
    `tradeit status` had to be fixed for, where every row read UNKNOWN because
    it was pointed at the wrong database without saying so.
    """
    engine = create_engine(dsn, future=True)
    factory = sessionmaker(bind=engine, future=True)

    app = FastAPI(
        title="TradeIt research interface",
        description=(
            "Read-only. Reports what the system decided and what it cannot "
            "explain. No trading authority."
        ),
        version="0.1.0",
    )
    app.state.session_factory = factory

    @app.get("/health")
    def health(session: SessionDep) -> dict[str, Any]:
        """Whether the record is reachable, and whether it holds anything.

        Two separate facts, reported separately. A reachable but empty record is
        not healthy for this service's purpose, and one number covering both
        would hide which it was.
        """
        scores = session.execute(select(OpportunityScore)).first() is not None
        runs = session.execute(select(BacktestRun)).first() is not None
        return {
            "reachable": True,
            "has_scores": scores,
            "has_runs": runs,
            "note": (
                None
                if (scores or runs)
                else "the record is reachable and empty; nothing has been recorded yet"
            ),
        }

    @app.get("/runs")
    def runs(session: SessionDep) -> list[dict[str, Any]]:
        """Every recorded run, with its metrics and whether it was out of sample."""
        rows = session.execute(select(BacktestRun).order_by(BacktestRun.id)).scalars().all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "start": row.start_date.isoformat(),
                "end": row.end_date.isoformat(),
                "universe": row.universe,
                "is_out_of_sample": row.is_out_of_sample,
                "run_manifest_id": row.run_manifest_id,
                "metrics": {
                    "total_return_pct": row.total_return_pct,
                    "cagr": row.cagr,
                    "max_drawdown_pct": row.max_drawdown_pct,
                    "sharpe": row.sharpe,
                    "win_rate": row.win_rate,
                    "profit_factor": row.profit_factor,
                },
            }
            for row in rows
        ]

    @app.get("/runs/{run_id}/trades")
    def run_trades(
        run_id: int,
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        """The trades of one run, with their P&L and the score each came from."""
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        rows = (
            session.execute(
                select(BacktestTrade)
                .where(BacktestTrade.backtest_run_id == run_id)
                .order_by(BacktestTrade.entry_date)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        total = session.execute(
            select(BacktestTrade).where(BacktestTrade.backtest_run_id == run_id)
        ).all()
        return {
            "run_id": run_id,
            "returned": len(rows),
            # Reported so a truncated page cannot be mistaken for the whole run.
            "total": len(total),
            "trades": [
                {
                    "instrument_id": row.instrument_id,
                    "side": row.side,
                    "entry_date": row.entry_date.isoformat(),
                    "exit_date": row.exit_date.isoformat(),
                    "exit_reason": row.exit_reason,
                    "net_pnl": str(row.net_pnl),
                    "return_pct": row.return_pct,
                    "r_multiple": row.r_multiple,
                    "holding_sessions": row.holding_sessions,
                    "score_at_entry": row.score_at_entry,
                }
                for row in rows
            ],
        }

    @app.get("/sessions/{session_date}")
    def session_detail(
        session_date: dt.date,
        session: SessionDep,
        portfolio_id: Annotated[int, Query(ge=1)] = 1,
    ) -> dict[str, Any]:
        """What was decided on one session, and what cannot be explained about it.

        ``missing`` is the field that matters. A response with empty lists and
        no ``missing`` would mean a quiet session; a response with empty lists
        *and* ``missing`` means nobody wrote the record. Those are different
        operational facts and this endpoint refuses to conflate them.
        """
        chain = reconstruct(session, portfolio_id=portfolio_id, session_date=session_date)
        return {
            "session_date": session_date.isoformat(),
            "portfolio_id": portfolio_id,
            "walkable_end_to_end": chain.complete,
            "missing": list(chain.missing),
            "scores": [
                {
                    "instrument_id": row.instrument_id,
                    "direction": row.direction,
                    "total": row.total,
                    # The breakdown, always. A score without it is the
                    # rationalisation `OpportunityScore.reconciles` exists to
                    # prevent, and a UI given only the total would invent one.
                    "components": row.components,
                    "rank": row.rank,
                }
                for row in chain.scores
            ],
            "orders": [
                {
                    "client_order_id": row.client_order_id,
                    "instrument_id": row.instrument_id,
                    "side": row.side,
                    "quantity": str(row.quantity),
                    "status": row.status,
                    "intent": row.intent,
                    "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                }
                for row in chain.orders
            ],
            "executions": [
                {
                    "order_id": row.order_id,
                    "instrument_id": row.instrument_id,
                    "quantity": str(row.quantity),
                    "price": str(row.price),
                    "commission": str(row.commission),
                    "slippage": str(row.slippage),
                    "venue": row.venue,
                }
                for row in chain.executions
            ],
            "journal": [
                {
                    "entry_type": entry.entry_type,
                    "summary": entry.summary,
                    "rationale": entry.rationale,
                    "context": entry.context,
                }
                for entry in chain.journal
                if isinstance(entry, JournalEntry)
            ],
        }

    @app.get("/instruments/{instrument_id}/scores")
    def instrument_scores(
        instrument_id: int,
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        """One instrument's recorded scores, newest first.

        **Not a verdict, and deliberately not aggregated.** The roadmap forbids
        a global buy/sell number, so this returns the scores as recorded and
        computes nothing across them. A mean here would be exactly the single
        score the architecture exists to prevent.
        """
        rows = (
            session.execute(
                select(OpportunityScore)
                .where(OpportunityScore.instrument_id == instrument_id)
                .order_by(OpportunityScore.session_date.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return {
            "instrument_id": instrument_id,
            "returned": len(rows),
            "scores": [
                {
                    "session_date": row.session_date.isoformat(),
                    "direction": row.direction,
                    "total": row.total,
                    "components": row.components,
                }
                for row in rows
            ],
            "note": (
                "scores as recorded, per session. No aggregate is computed: a "
                "single number across horizons is the thing the three-horizon "
                "architecture exists to prevent."
            ),
        }

    return app
