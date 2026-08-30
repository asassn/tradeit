"""PostgreSQL-specific behaviour that SQLite cannot exercise.

Skipped unless ``TRADEIT_TEST_PG_DSN`` points at a throwaway database. The
schema is created by Alembic, not ``create_all``, so these tests also prove the
migration and the ORM metadata have not drifted apart.
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from tradeit.core.clock import AsOfClock
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource, TradingMode
from tradeit.core.models import OhlcvBar
from tradeit.ingest.pipeline import Ingestor
from tradeit.storage import tables
from tradeit.storage.repositories import BarRepository

pytestmark = pytest.mark.integration

DSN = os.environ.get("TRADEIT_TEST_PG_DSN")
UTC = dt.UTC

if not DSN:
    pytest.skip("set TRADEIT_TEST_PG_DSN to run PostgreSQL tests", allow_module_level=True)


@pytest.fixture
def pg_session():
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DSN)
    command.upgrade(cfg, "head")

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    session.add(
        tables.Instrument(
            instrument_id=1,
            primary_exchange="XNYS",
            asset_class="common_stock",
            name="Test",
            source="test",
        )
    )
    session.flush()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _bar(day: int, close: str, knowledge_min: int = 20) -> OhlcvBar:
    session_date = dt.date(2024, 3, day)
    close_utc = dt.datetime.combine(session_date, dt.time(21, 0), tzinfo=UTC)
    return OhlcvBar(
        instrument_id=1,
        timeframe=Bartimeframe.D1,
        session_date=session_date,
        event_time=close_utc,
        knowledge_time=close_utc + dt.timedelta(minutes=knowledge_min),
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1000"),
    )


def test_migration_produces_a_schema_the_orm_can_use(pg_session):
    result = Ingestor(pg_session, "test").ingest_bars([_bar(4, "100"), _bar(5, "101")])
    assert result.rows_written == 2


def test_on_conflict_do_nothing_makes_replays_idempotent(pg_session):
    ingestor = Ingestor(pg_session, "test")
    bars = [_bar(4, "100"), _bar(5, "101")]
    assert ingestor.ingest_bars(bars).rows_written == 2
    assert ingestor.ingest_bars(bars).rows_written == 0
    assert pg_session.query(tables.OhlcvBar).count() == 2


def test_check_constraints_reject_an_impossible_bar(pg_session):
    """The database is the last line of defence when a writer bypasses the model."""
    with pytest.raises(IntegrityError, match="ck_bar_high"):
        pg_session.execute(
            text(
                "INSERT INTO ohlcv_bars (instrument_id, timeframe, session_date, event_time, "
                "knowledge_time, knowledge_source, open, high, low, close, volume, quality, "
                "source) VALUES (1, '1d', '2024-03-04', '2024-03-04T21:00:00Z', "
                "'2024-03-04T21:20:00Z', 'vendor_ingest', 100, 99, 98, 100, 1000, 'ok', 'test')"
            )
        )
    pg_session.rollback()


def test_knowledge_time_before_event_time_is_rejected_at_the_database(pg_session):
    with pytest.raises(IntegrityError, match="ck_bar_knowledge"):
        pg_session.execute(
            text(
                "INSERT INTO ohlcv_bars (instrument_id, timeframe, session_date, event_time, "
                "knowledge_time, knowledge_source, open, high, low, close, volume, quality, "
                "source) VALUES (1, '1d', '2024-03-04', '2024-03-04T21:00:00Z', "
                "'2024-03-04T20:00:00Z', 'vendor_ingest', 100, 100, 100, 100, 1000, 'ok', 'test')"
            )
        )
    pg_session.rollback()


def test_timestamptz_round_trips_as_aware_utc(pg_session):
    Ingestor(pg_session, "test").ingest_bars([_bar(4, "100")])
    clock = AsOfClock.at(dt.datetime(2024, 3, 5, tzinfo=UTC), mode=TradingMode.BACKTEST)
    bar = BarRepository(pg_session).history(clock, 1)[0]
    assert bar.knowledge_time.utcoffset() == dt.timedelta(0)
    assert bar.knowledge_time == dt.datetime(2024, 3, 4, 21, 20, tzinfo=UTC)


def test_numeric_columns_preserve_exact_decimals(pg_session):
    """Floats would silently make a cash ledger un-reconcilable."""
    Ingestor(pg_session, "test").ingest_bars([_bar(4, "123.456789")])
    clock = AsOfClock.at(dt.datetime(2024, 3, 5, tzinfo=UTC), mode=TradingMode.BACKTEST)
    bar = BarRepository(pg_session).history(clock, 1)[0]
    assert bar.close == Decimal("123.456789")


def test_the_window_function_query_picks_the_latest_revision(pg_session):
    ingestor = Ingestor(pg_session, "test")
    ingestor.ingest_bars([_bar(4, "100", knowledge_min=20)])
    ingestor.ingest_bars([_bar(4, "104", knowledge_min=5000)])
    repo = BarRepository(pg_session)

    early = AsOfClock.at(dt.datetime(2024, 3, 5, tzinfo=UTC), mode=TradingMode.BACKTEST)
    late = AsOfClock.at(dt.datetime(2024, 3, 12, tzinfo=UTC), mode=TradingMode.BACKTEST)
    assert repo.history(early, 1)[0].close == Decimal("100.000000")
    revised = repo.history(late, 1)
    assert len(revised) == 1 and revised[0].close == Decimal("104.000000")
