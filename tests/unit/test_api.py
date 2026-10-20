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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api import create_app
from tradeit.core.enums import OrderSide, OrderType
from tradeit.execution.base import OrderRequest
from tradeit.execution.journal import JournalEntryKind, record_entry
from tradeit.observability.record import record_order, record_score
from tradeit.storage.tables import ArtifactVersion, BacktestRun, Base, RunManifest
from tradeit.storage.tables import BacktestTrade as BacktestTradeRow

SESSION = dt.date(2015, 6, 1)
STAMP = dt.datetime(2015, 6, 1, 20, tzinfo=dt.UTC)


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
