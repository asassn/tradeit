"""Phase 10's read side, and the constraints the roadmap puts on it.

Three of them are enforced in code rather than left to whoever builds a UI, and
each has a test here:

* **No trading authority.** There are no write routes, and the session is
  rolled back on the way out so a future one could not persist anything.
* **No global buy/sell score.** The per-ticker surface must present horizons as
  separate conclusions — *"a stock may be attractive long-term and unattractive
  as a swing; that is information, not a contradiction"* — so nothing here
  aggregates.
* **It reports what it cannot explain.** *"If the system cannot reconstruct why
  something happened, the correct answer is that it cannot."*
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api import create_app
from tradeit.core.enums import OrderSide, OrderType
from tradeit.execution.base import OrderRequest
from tradeit.execution.journal import JournalEntryKind, record_entry
from tradeit.observability.record import record_order, record_score
from tradeit.storage.tables import (
    ArtifactVersion,
    BacktestRun,
    Base,
    RunManifest,
    SymbolMapping,
)
from tradeit.storage.tables import BacktestTrade as BacktestTradeRow

SESSION = dt.date(2015, 6, 1)
STAMP = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


def test_the_research_path_imports_no_web_framework() -> None:
    """`pyproject.toml` says the research path runs without a web framework.

    That claim had nothing holding it up. The `api` extra is optional on
    purpose -- the comment above it in `pyproject.toml` says the research and
    backtesting path must stay installable and runnable without FastAPI -- and
    the only thing that happened to enforce it was CI installing `[dev]` and
    not `[dev,api]`. That is not enforcement, it is a coincidence, and it cost
    more than it protected: `mypy` could not resolve `fastapi`, so the Type
    check step failed on every commit of this branch, and since it runs before
    Test, **the suite never ran in CI at all.**

    CI now installs the extra and this test holds the invariant instead. The
    asymmetry is the point: an install set can only assert an absence, and the
    moment anything legitimately needs the dependency the assertion
    disappears silently. A test keeps asserting it afterwards.

    Measured in a subprocess because `sys.modules` is process-wide -- this
    very file imports `fastapi` at module scope, so an in-process check would
    pass no matter what the research path imports.
    """
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    probe = (
        f"import sys; sys.path.insert(0, {str(src)!r});"
        "import tradeit.backtesting.engine;"
        "import tradeit.research01.importer;"
        "print(len([m for m in sys.modules if m.split('.')[0] in "
        "{'fastapi', 'starlette', 'uvicorn'}]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    count = int(result.stdout.strip())
    assert count == 0, (
        f"importing the research path pulled in {count} web-framework modules; "
        "tradeit.api is meant to be the only thing that imports FastAPI, so the "
        "research and backtesting path no longer installs without the api extra"
    )


@pytest.fixture
def record(tmp_path: object) -> str:
    """An empty record on disk, so `create_app` opens a real engine."""
    path = tmp_path / "record.sqlite"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
    return f"sqlite:///{path}"


def _populate(dsn: str) -> None:
    engine = create_engine(dsn, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        for digest, kind in (("sc", "strategy_config"), ("ds", "data_snapshot")):
            session.add(
                ArtifactVersion(digest=digest, kind=kind, name="t", payload={}, created_at=STAMP)
            )
        session.flush()
        manifest = RunManifest(
            run_id="r",
            run_kind="backtest",
            as_of=STAMP,
            manifest_digest="m",
            strategy_config_digest="sc",
            data_snapshot_digest="ds",
            code_version="t",
            created_at=STAMP,
        )
        session.add(manifest)
        session.flush()
        session.add(
            BacktestRun(
                name="ma_cross",
                run_manifest_id=manifest.id,
                status="completed",
                start_date=SESSION,
                end_date=dt.date(2015, 12, 31),
                universe="AAPL",
                initial_capital=Decimal(100_000),
                cost_model="participation",
                fill_model="bar",
                is_out_of_sample=False,
                total_return_pct=0.05,
                sharpe=0.4,
            )
        )
        record_score(
            session,
            instrument_id=7,
            session_date=SESSION,
            direction="long",
            total=0.42,
            components={"ma_spread": 0.42},
            run_manifest_id=manifest.id,
            mandate="swing",
            timeframe="1d",
        )
        record_order(
            session,
            OrderRequest(
                client_order_id="e-7",
                portfolio_id=1,
                instrument_id=7,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal(100),
                intent="ma cross up",
            ),
            submitted_at=STAMP,
        )
        # THREE trades, so a `limit=1` page is genuinely truncated. With one
        # trade, `total` and `returned` are both 1 and a mutant reporting
        # `len(rows)` as the total passes -- which it did.
        for index in range(3):
            session.add(
                BacktestTradeRow(
                    backtest_run_id=1,
                    instrument_id=7 + index,
                    side="long",
                    quantity=Decimal(100),
                    entry_date=SESSION + dt.timedelta(days=index),
                    entry_price=Decimal(100),
                    exit_date=SESSION + dt.timedelta(days=index + 10),
                    exit_price=Decimal(110),
                    exit_reason="time_stop",
                    gross_pnl=Decimal(1000),
                    costs=Decimal(10),
                    net_pnl=Decimal(990),
                    return_pct=0.099,
                    holding_sessions=10,
                )
            )
        record_entry(
            session,
            kind=JournalEntryKind.DECLINED,
            session_date=SESSION,
            portfolio_id=1,
            summary="1 candidate refused by risk",
            rationale="portfolio_heat",
        )
        session.commit()


def test_an_empty_record_says_so_rather_than_reporting_health(record: str) -> None:
    """A reachable-but-empty record is not healthy for this service's purpose,
    and one number covering both would hide which it was."""
    body = TestClient(create_app(record)).get("/health").json()
    assert body["reachable"] is True
    assert body["has_scores"] is False
    assert body["has_runs"] is False
    assert "reachable and empty" in body["note"]


def test_a_populated_record_reports_both_facts(record: str) -> None:
    _populate(record)
    body = TestClient(create_app(record)).get("/health").json()
    assert body["has_scores"] and body["has_runs"]
    assert body["note"] is None


def test_runs_carry_the_out_of_sample_flag(record: str) -> None:
    """The flag the ledger discipline depends on must reach the reader."""
    _populate(record)
    runs = TestClient(create_app(record)).get("/runs").json()
    assert len(runs) == 1
    assert runs[0]["is_out_of_sample"] is False
    assert runs[0]["run_manifest_id"] is not None, "a result without provenance"


def test_a_session_reports_what_it_cannot_explain(record: str) -> None:
    """The field that matters. Empty lists with no `missing` would mean a quiet
    session; empty lists *with* `missing` mean nobody wrote the record."""
    _populate(record)
    body = TestClient(create_app(record)).get(f"/sessions/{SESSION}").json()
    assert body["walkable_end_to_end"] is False
    assert "executions" in body["missing"]
    assert len(body["orders"]) == 1
    assert len(body["scores"]) == 1


def test_a_quiet_session_is_distinguishable_from_an_unlogged_one(record: str) -> None:
    _populate(record)
    body = TestClient(create_app(record)).get("/sessions/2015-07-01").json()
    assert body["scores"] == []
    assert set(body["missing"]) == {
        "scores",
        "orders",
        "executions",
        "trades",
        "journal",
    }


def test_every_score_carries_its_components(record: str) -> None:
    """A UI given only the total would invent the reasoning.

    That is the rationalisation `OpportunityScore.reconciles` exists to prevent,
    and the API is where it would be dropped.
    """
    _populate(record)
    body = TestClient(create_app(record)).get(f"/sessions/{SESSION}").json()
    assert body["scores"][0]["components"] == {"ma_spread": 0.42}


def test_the_refusal_and_its_rule_reach_the_reader(record: str) -> None:
    """ "which risk gate approved or rejected" is a named observability link."""
    _populate(record)
    body = TestClient(create_app(record)).get(f"/sessions/{SESSION}").json()
    assert len(body["journal"]) == 1
    assert body["journal"][0]["entry_type"] == "declined"
    assert body["journal"][0]["rationale"] == "portfolio_heat"


def test_instrument_scores_are_not_aggregated(record: str) -> None:
    """No global buy/sell score, and none can be added here.

    A mean across sessions would be exactly the single number the
    three-horizon architecture exists to prevent.
    """
    _populate(record)
    body = TestClient(create_app(record)).get("/instruments/7/scores").json()
    assert body["returned"] == 1
    assert "No aggregate is computed" in body["note"]
    assert not any(key in body for key in ("average", "mean", "verdict", "rating", "signal"))


def test_a_missing_run_is_a_404_not_an_empty_list(record: str) -> None:
    """An empty trade list for a run that does not exist reads as a run that
    did nothing."""
    _populate(record)
    assert TestClient(create_app(record)).get("/runs/999/trades").status_code == 404


def test_a_truncated_page_reports_the_total(record: str) -> None:
    """Otherwise a limit silently becomes the answer."""
    _populate(record)
    body = TestClient(create_app(record)).get("/runs/1/trades?limit=1").json()
    assert body["returned"] == 1
    assert body["total"] == 3, "the page's length was reported as the run's total"


def test_the_service_has_no_write_routes() -> None:
    """No trading authority. A dashboard that can place an order is a trading
    system with a chart attached."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.sqlite"
        Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
        app = create_app(f"sqlite:///{path}")
    methods = {method for route in app.routes for method in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}, f"the read side exposes {methods - {'GET', 'HEAD'}}"


def test_the_session_is_rolled_back_so_a_route_could_not_persist(record: str) -> None:
    """Structural rather than trusted: nothing here writes, and the teardown
    makes an accidental write impossible to keep."""
    _populate(record)
    app = create_app(record)
    with TestClient(app) as client:
        client.get(f"/sessions/{SESSION}")
    factory: sessionmaker[Session] = app.state.session_factory
    with factory() as session:
        # The populate() rows are committed; nothing the request touched added more.
        assert session.query(BacktestRun).count() == 1


def test_create_app_requires_a_dsn() -> None:
    """No default. The record and the corpus are different databases, and a
    service defaulting to the configured DSN would read whichever happened to
    be configured -- the confusion `tradeit status` had to be fixed for."""
    with pytest.raises(TypeError):
        create_app()  # type: ignore[call-arg]


def test_the_panel_keeps_horizons_separate(record: str) -> None:
    """One security, independent conclusions per horizon.

    *"A stock may be attractive long-term and unattractive as a swing; that is
    information, not a contradiction."* So the horizons are separate keys and
    nothing is computed across them.
    """
    _populate(record)
    body = TestClient(create_app(record)).get("/instruments/7/panel").json()
    assert "swing" in body["horizons"]
    assert len(body["horizons"]["swing"]) == 1
    assert body["horizons"]["swing"][0]["timeframe"] == "1d"
    assert "no score is computed across horizons" in body["note"]


def test_an_absent_horizon_carries_its_reason_not_an_empty_list(record: str) -> None:
    """An empty list reads as "looked and found nothing". Day was never looked
    at and cannot be: there is no intraday corpus, and scoring Day on Daily
    bars would collapse it into Swing.
    """
    _populate(record)
    body = TestClient(create_app(record)).get("/instruments/7/panel").json()
    assert "day" in body["absent"]
    assert "intraday" in body["absent"]["day"]
    assert "retirement" in body["absent"]
    assert "day" not in body["horizons"], "an absent horizon must not appear as empty"


def test_the_panel_computes_nothing_across_horizons(record: str) -> None:
    """The global buy/sell score the architecture exists to prevent."""
    _populate(record)
    body = TestClient(create_app(record)).get("/instruments/7/panel").json()
    assert not any(
        key in body for key in ("overall", "verdict", "rating", "score", "signal", "consensus")
    )


def test_a_score_with_no_recorded_horizon_is_its_own_bucket(record: str) -> None:
    """Rows written before migration 0020 say NULL. Folding them into Swing
    would claim a horizon nobody recorded."""
    engine = create_engine(record, future=True)
    _populate(record)
    with sessionmaker(bind=engine, future=True)() as session:
        from tradeit.storage.tables import OpportunityScore

        session.add(
            OpportunityScore(
                instrument_id=7,
                session_date=dt.date(2015, 5, 1),
                direction="long",
                total=0.3,
                components={"legacy": 0.3},
                run_manifest_id=1,
                mandate=None,
                timeframe=None,
            )
        )
        session.commit()

    body = TestClient(create_app(record)).get("/instruments/7/panel").json()
    assert "unrecorded" in body["horizons"]
    assert body["unrecorded_horizon_rows"] == 1
    assert len(body["horizons"]["swing"]) == 1, "the NULL row leaked into swing"


# ---------------------------------------------------------------------------
# The screen, and the routes that make the service navigable.
#
# Added 2026-11-16 with Phase 10's first milestone. Everything above this line
# tested a service nothing could open: `create_app` required a DSN, the runbook
# documented a start command that raised `TypeError` for want of a wrapper, and
# every per-security route keyed on an `instrument_id` that no route returned.
# ---------------------------------------------------------------------------


def test_the_page_is_served_and_needs_no_network(record: str) -> None:
    """The screen is served from this process, with no CDN and no build step.

    The assertion is about *self-containment*, not about markup: a page that
    pulled a script or a stylesheet from elsewhere would be a page that stops
    working on a machine with no network, and this one is meant to open over a
    local record with the wifi off.
    """
    client = TestClient(create_app(record))
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # Not the title, which is incidental and changed when the page was
    # rebuilt: that it is a complete document, and that every asset it
    # needs is inside it.
    assert body.lstrip().startswith("<!doctype html>")
    assert 'src="http' not in body
    assert 'href="http' not in body
    assert "cdn" not in body.lower()


def test_the_page_is_not_in_the_api_schema(record: str) -> None:
    """It is a view over the routes, not a route anything should call."""
    client = TestClient(create_app(record))
    assert "/" not in client.get("/openapi.json").json()["paths"]


def test_health_names_which_record_it_is_reading(record: str) -> None:
    """There are several records on this machine and they hold different runs.

    A screen that did not say which one it was reading would let a reader
    attribute one record's results to another, which is the confusion
    `tradeit status` had to be fixed for.
    """
    assert record in TestClient(create_app(record)).get("/health").json()["record"]


def test_the_record_label_never_carries_a_password() -> None:
    """The rule that no credential is printed or logged does not get a weaker
    implementation because this one goes to a local browser."""
    dsn = "postgresql+psycopg://someone:hunter2@localhost:5432/record"
    app = create_app(dsn)
    client = TestClient(app)
    # The engine is never connected -- only the rendered label is under test,
    # and `/health` would need a live Postgres.
    label = app.state.session_factory.kw["bind"].url.render_as_string(hide_password=True)
    assert "hunter2" not in label
    assert "***" in label
    del client


def test_instruments_lists_what_the_record_can_speak_about(record: str) -> None:
    """Without this the service could not be navigated: every per-security
    route keys on an id and nothing returned one, so the only way in was a
    direct SQL query against the record the service stands in front of."""
    _populate(record)
    body = TestClient(create_app(record)).get("/instruments").json()
    assert body["returned"] == 1
    row = body["instruments"][0]
    assert row["instrument_id"] == 7
    assert row["scores"] == 1
    assert row["horizons"] == ["swing"]
    assert row["first_session"] == SESSION.isoformat()


def test_an_unresolvable_ticker_is_named_as_unrecorded_not_left_blank(record: str) -> None:
    """A blank reads as "this security has no ticker"; the truth is "nobody
    wrote one down". Measured 2026-11-16: all three decision records on this
    machine hold zero `symbol_mappings` rows, so this is the normal answer."""
    _populate(record)
    row = TestClient(create_app(record)).get("/instruments").json()["instruments"][0]
    assert row["ticker"] is None
    assert "unrecorded, not absent" in row["ticker_note"]


def test_a_ticker_resolves_when_the_record_holds_exactly_one(record: str) -> None:
    """The companion to the test above, so neither can go vacuous: without it,
    a helper that resolved nothing at all would still pass."""
    _populate(record)
    engine = create_engine(record, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        session.add(
            SymbolMapping(
                instrument_id=7, ticker="AAPL", valid_from=dt.date(2000, 1, 1), source="t"
            )
        )
        session.commit()
    row = TestClient(create_app(record)).get("/instruments").json()["instruments"][0]
    assert row["ticker"] == "AAPL"
    assert row["ticker_note"] is None


def test_two_tickers_for_one_security_resolve_to_neither(record: str) -> None:
    """Fail closed on identity, applied to a display field.

    A security whose ticker changed, or whose ticker was reused, has several
    rows here. Picking the newest would label a 2004 score with a ticker the
    security only carried in 2019 -- the splice the identity architecture
    exists to prevent, shown on a chart where somebody would believe it.
    """
    _populate(record)
    engine = create_engine(record, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        session.add(
            SymbolMapping(
                instrument_id=7,
                ticker="OLD",
                valid_from=dt.date(2000, 1, 1),
                valid_to=dt.date(2010, 1, 1),
                source="t",
            )
        )
        session.add(
            SymbolMapping(instrument_id=7, ticker="NEW", valid_from=dt.date(2010, 1, 1), source="t")
        )
        session.commit()
    row = TestClient(create_app(record)).get("/instruments").json()["instruments"][0]
    assert row["ticker"] is None, "an ambiguous identity must not be resolved to one of them"
    assert row["ticker_note"] is not None


def test_sessions_lists_dates_newest_first(record: str) -> None:
    """`GET /sessions/{date}` needed a date that nothing would tell you."""
    _populate(record)
    engine = create_engine(record, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        manifest_id = session.execute(select(RunManifest.id)).scalar_one()
        record_score(
            session,
            instrument_id=7,
            session_date=SESSION + dt.timedelta(days=5),
            direction="long",
            total=0.1,
            components={"ma_spread": 0.1},
            run_manifest_id=manifest_id,
            mandate="swing",
            timeframe="1d",
        )
        session.commit()
    body = TestClient(create_app(record)).get("/sessions").json()
    dates = [row["session_date"] for row in body["sessions"]]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == (SESSION + dt.timedelta(days=5)).isoformat()


def test_listing_a_session_is_not_a_claim_that_it_can_be_walked(record: str) -> None:
    """Presence in the menu says only that something was recorded.

    Folding the end-to-end check in would mean reconstructing every date to
    build a menu, and would let an incomplete session vanish from the list
    rather than be found and shown as incomplete -- which is the one thing
    this service exists to make visible.
    """
    _populate(record)
    client = TestClient(create_app(record))
    listed = client.get("/sessions").json()["sessions"][0]["session_date"]
    assert client.get(f"/sessions/{listed}").json()["missing"], (
        "fixture changed; this session is meant to be incomplete"
    )


def test_a_run_reports_the_trades_that_reference_it(record: str) -> None:
    """`trade_count` is `NOT NULL DEFAULT 0` and was never written, so every
    run recorded before 2026-11-16 claims zero trades beside real rows."""
    _populate(record)
    row = TestClient(create_app(record)).get("/runs").json()[0]
    assert row["trades_recorded"] == 3
    assert row["trade_count_column"] == 0


def test_the_disagreement_is_named_rather_than_silently_corrected(record: str) -> None:
    """Substituting the counted value would repair the display of a record
    that is still wrong on disk, and the next reader to query the table
    directly would get the old answer with nothing to warn them."""
    _populate(record)
    row = TestClient(create_app(record)).get("/runs").json()[0]
    assert row["trade_count_disagrees"] is not None
    assert "0" in row["trade_count_disagrees"] and "3" in row["trade_count_disagrees"]


def test_an_agreeing_run_raises_no_flag(record: str) -> None:
    """Without this the flag could be unconditional and every test above would
    still pass."""
    _populate(record)
    engine = create_engine(record, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        session.execute(select(BacktestRun)).scalar_one().trade_count = 3
        session.commit()
    assert TestClient(create_app(record)).get("/runs").json()[0]["trade_count_disagrees"] is None


def test_sample_size_is_judged_on_the_rows_not_on_the_column(record: str) -> None:
    """`statistically_meaningful` is `trade_count >= 30`. Read from the stale
    column, a repaired record would still read "not enough trades to
    interpret" -- so it is computed from what the record actually holds."""
    _populate(record)
    engine = create_engine(record, future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        run_id = session.execute(select(BacktestRun.id)).scalar_one()
        for index in range(40):
            session.add(
                BacktestTradeRow(
                    backtest_run_id=run_id,
                    instrument_id=100 + index,
                    side="long",
                    quantity=Decimal(1),
                    entry_date=SESSION,
                    entry_price=Decimal(100),
                    exit_date=SESSION + dt.timedelta(days=1),
                    exit_price=Decimal(101),
                    exit_reason="time_stop",
                    gross_pnl=Decimal(1),
                    costs=Decimal(0),
                    net_pnl=Decimal(1),
                    return_pct=0.01,
                    holding_sessions=1,
                )
            )
        session.commit()
    row = TestClient(create_app(record)).get("/runs").json()[0]
    assert row["trade_count_column"] == 0
    assert row["statistically_meaningful"] is True


# ---------------------------------------------------------------------------
# Valuation, which reads a different database from everything above.
# ---------------------------------------------------------------------------


def test_valuation_without_a_corpus_is_501_not_an_empty_panel(record: str) -> None:
    """The distinction this route exists to keep.

    An empty panel reads as *this company has no valuation* --- a claim about
    the company. The truth is a claim about this process: it was started
    without the corpus. Measured 2026-11-17, the decision record holds **zero**
    rows in `securities`, `security_price_facts` and
    `security_fundamental_facts`, so a route pointed at the record would answer
    empty forever and look like a gap in the corpus.

    `501` rather than `404`: the request is well formed and the resource could
    exist; the service is not configured to serve it.
    """
    response = TestClient(create_app(record)).get("/securities/1/valuation")
    assert response.status_code == 501
    assert "different database" in response.json()["detail"]


def test_health_says_whether_a_corpus_is_attached(record: str) -> None:
    """So a reader seeing no valuation can tell which of the two reasons it
    is without trying the route."""
    assert TestClient(create_app(record)).get("/health").json()["corpus_attached"] is False
    attached = TestClient(create_app(record, record)).get("/health").json()
    assert attached["corpus_attached"] is True


def test_a_corpus_without_the_corpus_views_says_which_mistake_it_is(record: str) -> None:
    """Pointing `--corpus` at the wrong database is the likely operator error.

    `v_prices_raw` is a **view**, created by the corpus build and not by
    `Base.metadata.create_all`, so a database with every table and no views is
    exactly what you get by passing the decision record twice. Without this it
    surfaced as a 500 naming a view nobody has heard of; it now names the
    mistake.
    """
    _populate(record)
    body = TestClient(create_app(record, record)).get("/securities/1/valuation").json()
    assert body["available"] is False
    assert "not a prepared research-01 corpus" in body["note"]


def test_the_valuation_route_promises_no_verdict(record: str) -> None:
    """ADR-0036, asserted at the boundary as well as in the panel.

    The panel's own test checks the object has no verdict attribute; this
    checks the payload does not grow one on the way out, which is where a
    convenience field would most plausibly be added.
    """
    _populate(record)
    body = TestClient(create_app(record, record)).get("/securities/1/valuation").json()
    # True of the unavailable payload as well as the full one: a convenience
    # field would most plausibly be added to whichever shape a caller hits
    # first, and both are checked by the same rule.
    for forbidden in ("verdict", "label", "rating", "callout"):
        assert forbidden not in body, f"the payload grew a {forbidden}"


def test_search_without_a_corpus_is_501(record: str) -> None:
    """Search reads the corpus. Without one it says so rather than returning
    an empty result list, which would read as "no such company"."""
    assert TestClient(create_app(record)).get("/securities/search?q=AAPL").status_code == 501


def test_the_search_route_is_not_swallowed_by_the_valuation_route(record: str) -> None:
    """`/securities/search` and `/securities/{security_id}` collide.

    FastAPI matches in declaration order, so if `search` is declared second it
    is tried as a `security_id`, fails integer validation and answers 422 --
    the search box would simply never work. Declaration order is load-bearing
    and nothing else would catch a reordering.
    """
    response = TestClient(create_app(record)).get("/securities/search?q=AAPL")
    assert response.status_code != 422, "search is being parsed as a security id"


def test_search_requires_something_to_search_for(record: str) -> None:
    """An empty query would `LIKE '%'` the whole ticker table."""
    assert TestClient(create_app(record, record)).get("/securities/search?q=").status_code == 422


def test_the_page_offers_search_rather_than_asking_for_an_id() -> None:
    """The rewrite's premise, asserted so it cannot quietly regress.

    The first version of this page had a numeric `security_id` input: the
    screen asking the reader for the answer it was supposed to give. Nobody
    knows a security id, and the corpus holds 6,876 current tickers that
    people do know.
    """
    from tradeit.api.page import PAGE

    assert 'id="search"' in PAGE
    assert "securities/search" in PAGE
    assert 'type="number"' not in PAGE, "an id input is back on the page"
