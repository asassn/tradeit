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
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session, load_only, sessionmaker

from tradeit.api.page import PAGE
from tradeit.api.valuation_route import search_securities, valuation_payload
from tradeit.observability.record import reconstruct
from tradeit.storage.tables import (
    BacktestRun,
    BacktestTrade,
    JournalEntry,
    OpportunityScore,
    SymbolMapping,
)

__all__ = ["STATIC_ROOT", "create_app"]

#: Where `vite build` puts the compiled front end. Inside the package so that
#: `pip install` carries it, which is why the bundle is committed: an operator
#: must not need Node to run the dashboard.
STATIC_ROOT = Path(__file__).resolve().parent / "static"

#: Why an instrument may have no ticker in a response. Spelled out rather than
#: returned as ``null``, because the two readings of a bare null -- "this
#: security has no ticker" and "nobody recorded one here" -- are different
#: facts and only the second is ever true. Measured 2026-11-16: all three
#: decision records on this machine hold **zero** ``symbol_mappings`` rows, so
#: this is the normal answer rather than an edge case.
NO_TICKER = "no symbol_mappings row in this record; the ticker is unrecorded, not absent"


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


def _tickers_for(session: Session, ids: list[int]) -> dict[int, str]:
    """Resolve instrument ids to tickers, and leave out what cannot be resolved.

    **Absence from the returned mapping is the signal**, which is why this
    returns only what it found rather than a dict of ``id -> str | None``. The
    caller attaches :data:`NO_TICKER` to the gaps, so a reader is told the
    ticker is *unrecorded* rather than shown a blank that reads as *untickered*.

    **An instrument with more than one mapping is left unresolved.** That is
    the fail-closed rule the rest of the system runs on applied to a display
    field: a security whose ticker changed, or whose ticker was reused, has
    several rows here and picking one --- the newest, say --- would put a
    confident wrong label on a chart. Labelling a 2004 score with a ticker the
    security only carried in 2019 is precisely the splice the identity
    architecture exists to prevent, and a dashboard is where somebody would
    believe it. Interval-correct resolution needs the session date of the row
    being labelled, which is a per-row question this per-instrument helper
    cannot answer; until a route needs it, ambiguity is reported instead.
    """
    if not ids:
        return {}
    rows = session.execute(
        select(SymbolMapping.instrument_id, SymbolMapping.ticker).where(
            SymbolMapping.instrument_id.in_(ids)
        )
    ).all()
    found: dict[int, str] = {}
    ambiguous: set[int] = set()
    for instrument_id, ticker in rows:
        key = int(instrument_id)
        if key in found and found[key] != ticker:
            ambiguous.add(key)
        found[key] = ticker
    for key in ambiguous:
        del found[key]
    return found


#: What a record that predates migration 0020 is missing, and why it matters.
#: `opportunity_scores` gained `mandate` and `timeframe` in that migration;
#: rows written before it carry no horizon at all. That is not a display
#: nicety -- `check_recorded_scores` refuses to certify a population
#: containing unattributable rows, because "90% Swing and 10% unlabelled" is
#: a Swing population plus an unknown, and the unknown is exactly what a later
#: study would silently absorb.
PRE_HORIZON_NOTE = (
    "this record predates migration 0020, so its scores carry no recorded "
    "horizon. They are reported as `unrecorded` rather than assumed to be "
    "Swing: a row with no horizon is unattributable, and a population "
    "containing unattributable rows cannot be certified as any horizon"
)


#: The score columns every schema generation has. Selecting the whole ORM
#: entity emits `SELECT` for `mandate` and `timeframe` as well, which a record
#: written before migration 0020 does not have -- so the read fails at the
#: driver and the route answers 500.
#:
#: **This is the fix for the cause rather than the instances.** The first
#: attempt patched two routes and left three others raising the same error
#: from the same line of SQL, which is the shape of defect that comes back.
#: Every read of a score now names its columns.
SCORE_COLUMNS_IN_EVERY_SCHEMA = (
    "id",
    "instrument_id",
    "session_date",
    "direction",
    "total",
    "components",
    "rank",
)


def _score_columns() -> tuple[InstrumentedAttribute[Any], ...]:
    """The score attributes safe to load against any schema generation."""
    return tuple(getattr(OpportunityScore, name) for name in SCORE_COLUMNS_IN_EVERY_SCHEMA)


def _records_horizons(session: Session) -> bool:
    """Whether this record's `opportunity_scores` carries a `mandate` column.

    **Asked rather than assumed.** Querying `mandate` against a record written
    before migration 0020 raises `no such column` and the route answers 500 --
    which reads to an operator as a broken service rather than as an older
    database, and sends them to the logs instead of to the answer.

    Inspected on the live connection rather than inferred from a migration
    version, because the question is what this file actually has.
    """
    from sqlalchemy import inspect as sa_inspect

    bind = session.get_bind()
    columns = sa_inspect(bind).get_columns(OpportunityScore.__tablename__)
    return any(column["name"] == "mandate" for column in columns)


def _horizons_for(session: Session, ids: list[int]) -> dict[int, list[str]]:
    """Which horizons each instrument has a recorded conclusion at.

    A ``NULL`` mandate becomes ``"unrecorded"`` rather than being dropped or
    folded into Swing: those rows predate migration 0020 and nobody wrote down
    what they were. Dropping them would under-report what the record holds;
    folding them into Swing would be an invention.
    """
    if not ids:
        return {}
    if not _records_horizons(session):
        # Every row is unattributable, which is the honest answer rather than
        # an error. See PRE_HORIZON_NOTE.
        return {key: ["unrecorded"] for key in ids}
    rows = session.execute(
        select(OpportunityScore.instrument_id, OpportunityScore.mandate)
        .where(OpportunityScore.instrument_id.in_(ids))
        .distinct()
    ).all()
    out: dict[int, list[str]] = {}
    for instrument_id, mandate in rows:
        out.setdefault(int(instrument_id), []).append(mandate or "unrecorded")
    for value in out.values():
        value.sort()
    return out


def create_app(dsn: str, corpus_dsn: str | None = None) -> FastAPI:
    """Build the service over one decision record.

    ``dsn`` is required and has no default. The record and the corpus are
    different databases, and a service that defaulted to the configured DSN
    would read whichever one happened to be configured — the confusion
    `tradeit status` had to be fixed for, where every row read UNKNOWN because
    it was pointed at the wrong database without saying so.
    """
    engine = create_engine(dsn, future=True)
    factory = sessionmaker(bind=engine, future=True)
    # The corpus is a **second, optional** connection, and separate from the
    # record on purpose. The record holds what the system decided; the corpus
    # holds what the world did. Measured 2026-11-17: the decision record has
    # zero rows in `securities`, `security_price_facts` and
    # `security_fundamental_facts`, so a valuation route pointed at it would
    # answer "no data" forever and read as a gap in the corpus rather than a
    # service wired to the wrong database.
    corpus_factory: sessionmaker[Session] | None = None
    if corpus_dsn is not None:
        corpus_factory = sessionmaker(bind=create_engine(corpus_dsn, future=True), future=True)
    # Rendered through SQLAlchemy's own URL rather than printed, because the
    # page displays it and a Postgres DSN carries a password. `hide_password`
    # is the library's redaction and is used instead of a hand-rolled one: the
    # rule that no credential is ever printed or logged does not get a weaker
    # implementation just because this one goes to a local browser.
    record_label = engine.url.render_as_string(hide_password=True)

    app = FastAPI(
        title="TradeIt research interface",
        description=(
            "Read-only. Reports what the system decided and what it cannot "
            "explain. No trading authority."
        ),
        version="0.1.0",
    )
    app.state.session_factory = factory
    app.state.corpus_factory = corpus_factory

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        """The screen, served from this process so it needs no network at all.

        ``include_in_schema=False`` keeps it out of the OpenAPI document: it
        is a view over the routes below, not a route anything should call.

        **`PAGE` remains as the fallback**, for a source checkout whose bundle
        has not been built. `pip install` carries the committed bundle, so an
        operator never needs Node; a developer who has just cloned and not yet
        run `npm run build` still gets a working dashboard rather than a 404.
        """
        built = STATIC_ROOT / "index.html"
        if built.is_file():
            # **`no-store`, and this is not belt-and-braces.** Vite hashes
            # every asset filename and `emptyOutDir` deletes the previous
            # build, so a browser holding a cached `index.html` after a
            # rebuild asks for a bundle that no longer exists and renders
            # nothing at all -- a white screen, with the server healthy and
            # every JSON route answering. Found by rebuilding and watching a
            # stale page serve the previous figures.
            #
            # The hashed assets themselves are immutable and cache normally;
            # it is only the entry document that must never be reused.
            return HTMLResponse(
                built.read_text(encoding="utf-8"),
                headers={"cache-control": "no-store, must-revalidate"},
            )
        return HTMLResponse(PAGE, headers={"cache-control": "no-store"})

    @app.get("/health")
    def health(session: SessionDep) -> dict[str, Any]:
        """Whether the record is reachable, and whether it holds anything.

        Two separate facts, reported separately. A reachable but empty record is
        not healthy for this service's purpose, and one number covering both
        would hide which it was.
        """
        # One narrow column, not the entity: `/health` must answer on any
        # schema generation, and a health check that 500s is the least useful
        # failure available.
        scores = session.execute(select(OpportunityScore.id).limit(1)).first() is not None
        runs = session.execute(select(BacktestRun)).first() is not None
        return {
            "reachable": True,
            # Which record this is. There are several on this machine and they
            # hold different runs; a screen that did not say which one it was
            # reading would let a reader attribute one record's results to
            # another. Password-redacted at construction.
            "record": record_label,
            "corpus_attached": corpus_factory is not None,
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
        """Every recorded run, with its metrics and whether it was out of sample.

        **Two trade counts, reported separately and never reconciled here.**
        ``trades_recorded`` is counted from ``backtest_trades``;
        ``trade_count_column`` is what the run row claims. They agree for
        anything written after 2026-11-16, when the writer was fixed to
        populate the column at all --- it is ``NOT NULL DEFAULT 0``, so for
        every run recorded before then it reads **0 beside real trade rows**
        (1,402, 106 and 17,392 across the three records on this machine).

        Substituting the counted value silently would have been the easier
        move and the wrong one: it would repair the display of a record that
        is still wrong on disk, and the next reader to query the table
        directly would get the old answer with nothing to warn them.
        ``trade_count_disagrees`` names it instead.
        """
        rows = session.execute(select(BacktestRun).order_by(BacktestRun.id)).scalars().all()
        counted: dict[int, int] = {
            int(run_id): int(n)
            for run_id, n in session.execute(
                select(BacktestTrade.backtest_run_id, func.count())
                .group_by(BacktestTrade.backtest_run_id)
                .where(BacktestTrade.backtest_run_id.in_([row.id for row in rows]))
            ).all()
        }
        out: list[dict[str, Any]] = []
        for row in rows:
            recorded = int(counted.get(row.id, 0))
            out.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "start": row.start_date.isoformat(),
                    "end": row.end_date.isoformat(),
                    "universe": row.universe,
                    "is_out_of_sample": row.is_out_of_sample,
                    "run_manifest_id": row.run_manifest_id,
                    "trades_recorded": recorded,
                    "trade_count_column": row.trade_count,
                    "trade_count_disagrees": (
                        None
                        if row.trade_count == recorded
                        else (
                            f"the run row claims {row.trade_count} trades and "
                            f"{recorded} backtest_trades rows reference it; the "
                            "column was not written before 2026-11-16"
                        )
                    ),
                    "metrics": {
                        "total_return_pct": row.total_return_pct,
                        "cagr": row.cagr,
                        "max_drawdown_pct": row.max_drawdown_pct,
                        "sharpe": row.sharpe,
                        "win_rate": row.win_rate,
                        "profit_factor": row.profit_factor,
                        "exposure_pct": row.exposure_pct,
                        "benchmark_return_pct": row.benchmark_return_pct,
                    },
                    # `statistically_meaningful` is `trade_count >= 30`. Applied
                    # to the counted rows, never to the column, or a repaired
                    # record would still read "not enough trades to interpret".
                    "statistically_meaningful": recorded >= 30,
                }
            )
        return out

    @app.get("/instruments")
    def instruments(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    ) -> dict[str, Any]:
        """Which securities this record has anything to say about.

        **Added because the service could not be navigated.** Every other
        per-security route keys on ``instrument_id``, and nothing returned one:
        a reader had to already know the id of the thing they wanted to look
        up, which means the only way in was a direct SQL query against the
        record the service exists to stand in front of.

        Ordered by how much is recorded, not alphabetically. A list sorted by
        id puts the emptiest rows first and buries the securities with
        something to show.
        """
        rows = session.execute(
            select(
                OpportunityScore.instrument_id,
                func.count().label("scores"),
                func.min(OpportunityScore.session_date),
                func.max(OpportunityScore.session_date),
            )
            .group_by(OpportunityScore.instrument_id)
            .order_by(func.count().desc(), OpportunityScore.instrument_id)
            .limit(limit)
        ).all()
        ids = [int(row[0]) for row in rows]
        tickers = _tickers_for(session, ids)
        horizons = _horizons_for(session, ids)
        return {
            "returned": len(rows),
            "instruments": [
                {
                    "instrument_id": int(row[0]),
                    "ticker": tickers.get(int(row[0])),
                    "ticker_note": None if int(row[0]) in tickers else NO_TICKER,
                    "scores": int(row[1]),
                    "first_session": row[2].isoformat(),
                    "last_session": row[3].isoformat(),
                    # Which horizons this security has a conclusion at, and no
                    # aggregate across them. The list is the answer; a count
                    # would invite "3 of 3" being read as agreement.
                    "horizons": horizons.get(int(row[0]), []),
                }
                for row in rows
            ],
            "note": (
                "securities with at least one recorded score, most-recorded "
                "first. No ranking is implied: this orders by how much the "
                "record holds, not by how attractive anything is."
            ),
        }

    @app.get("/sessions")
    def sessions(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    ) -> dict[str, Any]:
        """Which sessions this record has anything to say about, newest first.

        The companion to ``/instruments`` and added for the same reason:
        ``GET /sessions/{date}`` needed a date nothing would tell you.

        **This lists sessions that produced scores, which is not the same as
        sessions that can be walked end to end.** A date appearing here says
        only that something was recorded on it; whether the chain from score
        to execution is complete is what ``/sessions/{date}`` answers, through
        ``missing``. Folding that in would mean running the full
        reconstruction for every date to build a menu, and would let an
        incomplete session disappear from the list rather than be found and
        shown as incomplete.
        """
        rows = session.execute(
            select(OpportunityScore.session_date, func.count())
            .group_by(OpportunityScore.session_date)
            .order_by(OpportunityScore.session_date.desc())
            .limit(limit)
        ).all()
        return {
            "returned": len(rows),
            "sessions": [
                {"session_date": row[0].isoformat(), "scores": int(row[1])} for row in rows
            ],
            "note": (
                "sessions with at least one recorded score. Presence here is "
                "not a claim that the session can be walked end to end; read "
                "`missing` on the session itself for that."
            ),
        }

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

    @app.get("/instruments/{instrument_id}/panel")
    def instrument_panel(
        instrument_id: int,
        session: SessionDep,
        per_horizon: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> dict[str, Any]:
        """The per-ticker panel: one security, each horizon separately.

        The roadmap specifies *"one security, three independent conclusions --
        Day, Swing and Retirement -- each with its own supporting and
        contradicting factors"*, and that *"a stock may be attractive long-term
        and unattractive as a swing; that is information, not a
        contradiction"*. So the horizons are returned as **separate keys with
        separate score lists**, and nothing is computed across them.

        **Horizons with no record say so, and why.** Day cannot be scored at
        all: there is no `intraday-01` corpus, and scoring the Day mandate on
        Daily bars would collapse it into Swing — the one thing the
        three-horizon architecture exists to prevent. Retirement is simply
        unrun. Both are reported as absent with the reason, rather than as
        empty lists a reader would take for "nothing found".

        A `NULL` horizon is its own bucket, not folded into Swing: those are
        rows written before migration 0020 and nobody recorded what they were.
        **A record older than migration 0020 degrades rather than failing.**
        Selecting `mandate` against one raises `no such column` and the route
        answered 500, which reads to an operator as a broken service rather
        than as an older database. Every row is reported as `unrecorded`,
        which is what it is.
        """
        horizons_recorded = _records_horizons(session)
        columns = [
            OpportunityScore.session_date,
            OpportunityScore.direction,
            OpportunityScore.total,
            OpportunityScore.components,
        ]
        if horizons_recorded:
            columns += [OpportunityScore.mandate, OpportunityScore.timeframe]
        rows = session.execute(
            select(*columns)
            .where(OpportunityScore.instrument_id == instrument_id)
            .order_by(OpportunityScore.session_date.desc())
        ).all()
        by_horizon: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = (row.mandate or "unrecorded") if horizons_recorded else "unrecorded"
            bucket = by_horizon.setdefault(key, [])
            if len(bucket) < per_horizon:
                bucket.append(
                    {
                        "session_date": row.session_date.isoformat(),
                        "timeframe": row.timeframe if horizons_recorded else None,
                        "direction": row.direction,
                        "total": row.total,
                        "components": row.components,
                    }
                )
        absent = {
            name: reason
            for name, reason in (
                (
                    "day",
                    "no intraday corpus exists; scoring Day on Daily bars would "
                    "collapse it into Swing",
                ),
                ("retirement", "no run has produced Retirement-horizon scores"),
                ("swing", "no run has produced Swing-horizon scores for this security"),
            )
            if name not in by_horizon
        }
        return {
            "instrument_id": instrument_id,
            "horizons": by_horizon,
            # Reported as absent-with-a-reason rather than as empty lists. An
            # empty list reads as "looked and found nothing"; these were never
            # looked at, and for Day cannot be.
            "absent": absent,
            "note": (
                "each horizon is an independent conclusion. A security may be "
                "attractive at one and unattractive at another; that is "
                "information, not a contradiction, and no score is computed "
                "across horizons."
            ),
            "unrecorded_horizon_rows": len(by_horizon.get("unrecorded", [])),
            # Present only when it applies, so a current record carries no
            # explanation for a condition it does not have.
            "horizons_not_recorded": None if horizons_recorded else PRE_HORIZON_NOTE,
        }

    @app.get("/securities/search")
    def securities_search(
        q: Annotated[str, Query(min_length=1, max_length=64)],
        limit: Annotated[int, Query(ge=1, le=50)] = 12,
    ) -> dict[str, Any]:
        """Find a security by ticker or company name.

        **Declared before `/securities/{security_id}/valuation` on purpose.**
        FastAPI matches routes in order, and `search` would otherwise be tried
        as a `security_id` and rejected as an invalid integer.
        """
        if corpus_factory is None:
            raise HTTPException(
                status_code=501,
                detail="no corpus is attached; start with --corpus to enable search",
            )
        corpus = corpus_factory()
        try:
            return {"query": q, "results": search_securities(corpus, q, limit)}
        finally:
            corpus.rollback()
            corpus.close()

    @app.get("/securities/{security_id}/valuation")
    def security_valuation(security_id: int) -> dict[str, Any]:
        """One security's valuation panel, read from the corpus.

        **Keyed on `security_id`, not `instrument_id`**, and the difference is
        not pedantry: instruments live in the decision record and securities
        live in `research-01`. They are separate id spaces, and a route that
        silently accepted one for the other would return another company's
        valuation --- a wrong answer that looks entirely right.

        **No verdict is returned.** ADR-0036: a multiple has no absolute
        meaning without growth and risk, valuation is horizon-dependent so one
        label across three horizons would be the universal buy/sell number the
        architecture exists to prevent, and none of the four fundamental
        signals measured on this corpus has passed.
        """
        if corpus_factory is None:
            # 501 rather than 404 or an empty body: the request is well formed
            # and the service is not configured to answer it. An empty panel
            # would read as "this company has no valuation", which is a claim
            # about the company rather than about this process.
            raise HTTPException(
                status_code=501,
                detail=(
                    "no corpus is attached. Valuation reads research-01, which is "
                    "a different database from the decision record; start with "
                    "--corpus to enable it"
                ),
            )
        corpus = corpus_factory()
        try:
            return valuation_payload(corpus, security_id)
        finally:
            corpus.rollback()
            corpus.close()

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
                .options(load_only(*_score_columns()))
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

    # Mounted LAST, deliberately: a static mount matches greedily, and
    # mounting it before the routes above would have `/runs` serve a 404 from
    # the asset directory instead of JSON.
    assets = STATIC_ROOT / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app
