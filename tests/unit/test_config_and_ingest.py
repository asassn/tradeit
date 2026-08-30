from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.config import (
    LIVE_AUTHORIZATION_PHRASE,
    Settings,
    get_settings,
)
from tradeit.core.enums import Bartimeframe, FiscalPeriod, KnowledgeTimeSource, TradingMode
from tradeit.core.models import FundamentalFact, OhlcvBar
from tradeit.data.registry import available, get_provider, register
from tradeit.errors import ConfigError, ProviderError
from tradeit.ingest.pipeline import Ingestor
from tradeit.storage import tables

UTC = dt.UTC


class TestLiveTradingInterlock:
    def test_paper_is_the_default(self):
        assert Settings().trading_mode is TradingMode.PAPER

    def test_live_is_refused_without_the_authorization_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tradeit.config.LIVE_AUTHORIZATION_PATH", tmp_path / "nope")
        with pytest.raises(ConfigError, match="does not exist"):
            Settings(trading_mode=TradingMode.LIVE)

    def test_live_is_refused_when_the_phrase_is_wrong(self, monkeypatch, tmp_path):
        path = tmp_path / "auth"
        path.write_text("yes please")
        monkeypatch.setattr("tradeit.config.LIVE_AUTHORIZATION_PATH", path)
        with pytest.raises(ConfigError, match="authorization phrase"):
            Settings(trading_mode=TradingMode.LIVE)

    def test_live_is_allowed_only_with_the_exact_phrase(self, monkeypatch, tmp_path):
        path = tmp_path / "auth"
        path.write_text(LIVE_AUTHORIZATION_PHRASE + "\n")
        monkeypatch.setattr("tradeit.config.LIVE_AUTHORIZATION_PATH", path)
        settings = Settings(trading_mode=TradingMode.LIVE)
        assert settings.is_live and not settings.orders_are_simulated

    def test_backtest_and_paper_both_simulate_orders(self):
        assert Settings(trading_mode=TradingMode.BACKTEST).orders_are_simulated
        assert Settings(trading_mode=TradingMode.PAPER).orders_are_simulated

    def test_settings_are_cached(self):
        assert get_settings() is get_settings()


class TestProviderRegistry:
    def test_synthetic_is_registered(self):
        assert "synthetic" in available()
        assert get_provider("synthetic").name == "synthetic"

    def test_unknown_provider_lists_what_is_available(self):
        with pytest.raises(ProviderError, match="registered: "):
            get_provider("bloomberg")

    def test_duplicate_registration_is_refused(self):
        with pytest.raises(ProviderError, match="already registered"):
            register("synthetic", object)


def _instrument(session) -> None:
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


def _bar(session_date: dt.date, close: str, knowledge_min: int = 20) -> OhlcvBar:
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


class TestIngestion:
    def test_bars_are_written_and_audited(self, db_session):
        _instrument(db_session)
        result = Ingestor(db_session, "test").ingest_bars(
            [_bar(dt.date(2024, 3, d), "100") for d in (4, 5, 6)]
        )
        assert result.rows_written == 3 and result.ok

        run = db_session.query(tables.IngestionRun).one()
        assert run.status == "completed" and run.rows_written == 3
        assert run.finished_at is not None

    def test_replaying_the_same_window_is_idempotent(self, db_session):
        _instrument(db_session)
        ingestor = Ingestor(db_session, "test")
        bars = [_bar(dt.date(2024, 3, d), "100") for d in (4, 5, 6)]
        ingestor.ingest_bars(bars)
        second = ingestor.ingest_bars(bars)

        assert second.rows_written == 0
        assert db_session.query(tables.OhlcvBar).count() == 3

    def test_a_revision_is_stored_alongside_the_original(self, db_session):
        _instrument(db_session)
        ingestor = Ingestor(db_session, "test")
        ingestor.ingest_bars([_bar(dt.date(2024, 3, 4), "100", knowledge_min=20)])
        ingestor.ingest_bars([_bar(dt.date(2024, 3, 4), "104", knowledge_min=4000)])
        assert db_session.query(tables.OhlcvBar).count() == 2

    def test_a_mismatched_timeframe_is_quarantined_not_dropped(self, db_session):
        _instrument(db_session)
        result = Ingestor(db_session, "test").ingest_bars(
            [_bar(dt.date(2024, 3, 4), "100")], timeframe=Bartimeframe.H1
        )
        assert result.rows_written == 0 and result.rows_rejected == 1

        quarantined = db_session.query(tables.QuarantinedRow).one()
        assert "timeframe" in quarantined.reason
        assert quarantined.payload  # the raw row is preserved for diagnosis

        run = db_session.query(tables.IngestionRun).one()
        assert run.status == "completed_with_rejections"

    def test_a_filing_stamped_with_its_period_end_is_quarantined(self, db_session):
        """The highest-value guard in the pipeline.

        A vendor that reports ``knowledge_time == period_end`` is telling us it
        has no filing dates. Accepting those rows would let a screen act on
        results weeks before they existed.
        """
        _instrument(db_session)
        period_end = dt.date(2023, 12, 31)
        stamp = dt.datetime.combine(period_end, dt.time(21, 0), tzinfo=UTC)
        fact = FundamentalFact(
            instrument_id=1,
            metric="revenue",
            fiscal_period=FiscalPeriod.Q4,
            fiscal_year=2023,
            period_end=period_end,
            event_time=stamp,
            knowledge_time=stamp,
            knowledge_source=KnowledgeTimeSource.ESTIMATED,
            value=Decimal("1000"),
        )
        result = Ingestor(db_session, "test").ingest_fundamentals([fact])

        assert result.rows_written == 0 and result.rows_rejected == 1
        assert db_session.query(tables.FundamentalFact).count() == 0
        assert "period-end-as-filing-date" in db_session.query(tables.QuarantinedRow).one().reason
