"""The tests that justify the whole storage design.

If any of these regress, every backtest the platform produces is fiction.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import (
    AdjustmentPolicy,
    CorporateActionType,
    KnowledgeTimeSource,
)
from tradeit.errors import UniverseError
from tradeit.storage import tables
from tradeit.storage.repositories import (
    BarRepository,
    FundamentalRepository,
    InstrumentRepository,
)

UTC = dt.UTC


def _instrument(session, instrument_id: int = 1) -> None:
    session.add(
        tables.Instrument(
            instrument_id=instrument_id,
            primary_exchange="XNYS",
            asset_class="common_stock",
            name=f"Test {instrument_id}",
            source="test",
        )
    )
    session.flush()


def _bar(
    session,
    session_date: dt.date,
    close: str,
    *,
    instrument_id: int = 1,
    knowledge_offset_min: int = 20,
    volume: str = "1000000",
) -> None:
    close_utc = dt.datetime.combine(session_date, dt.time(21, 0), tzinfo=UTC)
    session.add(
        tables.OhlcvBar(
            instrument_id=instrument_id,
            timeframe="1d",
            session_date=session_date,
            event_time=close_utc,
            knowledge_time=close_utc + dt.timedelta(minutes=knowledge_offset_min),
            knowledge_source=KnowledgeTimeSource.VENDOR_INGEST.value,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(volume),
            source="test",
        )
    )
    session.flush()


class TestBarVisibility:
    def test_a_bar_is_invisible_before_its_publication_lag_elapses(self, db_session, clock_factory):
        _instrument(db_session)
        _bar(db_session, dt.date(2024, 3, 8), "100", knowledge_offset_min=20)
        repo = BarRepository(db_session)

        at_close = clock_factory("2024-03-08", hour=21)
        assert repo.history(at_close, 1) == []

        after_lag = clock_factory("2024-03-09", hour=0)
        assert len(repo.history(after_lag, 1)) == 1

    def test_future_bars_are_never_returned(self, db_session, clock_factory):
        _instrument(db_session)
        for day, price in [(4, "100"), (5, "101"), (6, "102"), (7, "103")]:
            _bar(db_session, dt.date(2024, 3, day), price)
        repo = BarRepository(db_session)

        history = repo.history(clock_factory("2024-03-06", hour=22), 1)
        assert [b.session_date.day for b in history] == [4, 5, 6]
        assert all(b.close <= Decimal("102") for b in history)

    def test_history_is_chronological(self, db_session, clock_factory):
        _instrument(db_session)
        for day in (4, 5, 6, 7):
            _bar(db_session, dt.date(2024, 3, day), str(100 + day))
        history = BarRepository(db_session).history(clock_factory("2024-03-08"), 1)
        assert [b.session_date for b in history] == sorted(b.session_date for b in history)

    def test_limit_returns_the_most_recent_bars_not_the_oldest(self, db_session, clock_factory):
        _instrument(db_session)
        for day in (4, 5, 6, 7):
            _bar(db_session, dt.date(2024, 3, day), str(100 + day))
        history = BarRepository(db_session).history(clock_factory("2024-03-08"), 1, limit=2)
        assert [b.session_date.day for b in history] == [6, 7]

    def test_latest_respects_the_clock(self, db_session, clock_factory):
        _instrument(db_session)
        _bar(db_session, dt.date(2024, 3, 6), "102")
        _bar(db_session, dt.date(2024, 3, 7), "103")
        repo = BarRepository(db_session)
        assert repo.latest(clock_factory("2024-03-06", hour=23), 1).close == Decimal("102.000000")


class TestRevisions:
    def test_the_original_value_is_returned_before_the_correction_was_published(
        self, db_session, clock_factory
    ):
        """A vendor corrects Friday's close on Monday.

        A screen run on Friday evening must see the wrong-but-then-current
        value; a screen run on Tuesday must see the correction. Anything else
        is hindsight leaking into history.
        """
        _instrument(db_session)
        session_date = dt.date(2024, 3, 8)
        close_utc = dt.datetime.combine(session_date, dt.time(21, 0), tzinfo=UTC)

        _bar(db_session, session_date, "100", knowledge_offset_min=20)
        db_session.add(
            tables.OhlcvBar(
                instrument_id=1,
                timeframe="1d",
                session_date=session_date,
                event_time=close_utc,
                knowledge_time=close_utc + dt.timedelta(days=3),
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST.value,
                open=Decimal("104"),
                high=Decimal("104"),
                low=Decimal("104"),
                close=Decimal("104"),
                volume=Decimal("1000000"),
                quality="vendor_revision",
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)

        friday_evening = clock_factory("2024-03-08", hour=22)
        assert repo.history(friday_evening, 1)[0].close == Decimal("100.000000")

        tuesday = clock_factory("2024-03-12", hour=12)
        revised = repo.history(tuesday, 1, include_suspect=True)
        assert len(revised) == 1, "a revision must replace, not duplicate, the original session"
        assert revised[0].close == Decimal("104.000000")

    def test_fundamental_series_uses_as_filed_values(self, db_session, clock_factory):
        _instrument(db_session)
        period_end = dt.date(2023, 12, 31)
        original_filing = dt.datetime(2024, 2, 15, 21, 0, tzinfo=UTC)
        restatement = dt.datetime(2024, 8, 1, 21, 0, tzinfo=UTC)

        for filed, value in [(original_filing, "1000"), (restatement, "850")]:
            db_session.add(
                tables.FundamentalFact(
                    instrument_id=1,
                    metric="revenue",
                    fiscal_period="Q4",
                    fiscal_year=2023,
                    period_end=period_end,
                    event_time=dt.datetime.combine(period_end, dt.time(21, 0), tzinfo=UTC),
                    knowledge_time=filed,
                    knowledge_source=KnowledgeTimeSource.REPORTED.value,
                    value=Decimal(value),
                    source="test",
                )
            )
        db_session.flush()
        repo = FundamentalRepository(db_session)

        march = clock_factory("2024-03-01")
        assert repo.latest_metric(march, 1, "revenue").value == Decimal("1000.000000")

        september = clock_factory("2024-09-01")
        assert repo.latest_metric(september, 1, "revenue").value == Decimal("850.000000")

        series = repo.series(march, 1, "revenue")
        assert len(series) == 1 and series[0].value == Decimal("1000.000000")


class TestSurvivorshipAndIdentity:
    def test_a_delisted_company_is_still_in_the_universe_on_a_past_date(
        self, db_session, clock_factory
    ):
        _instrument(db_session, 1)
        _instrument(db_session, 2)
        db_session.add_all(
            [
                tables.UniverseMembership(
                    universe="us_equity",
                    instrument_id=1,
                    valid_from=dt.date(2010, 1, 1),
                    source="test",
                ),
                tables.UniverseMembership(
                    universe="us_equity",
                    instrument_id=2,
                    valid_from=dt.date(2010, 1, 1),
                    valid_to=dt.date(2018, 6, 1),
                    exit_reason="bankrupt",
                    source="test",
                ),
            ]
        )
        db_session.flush()
        repo = InstrumentRepository(db_session)

        assert repo.universe(clock_factory("2015-01-01"), "us_equity") == [1, 2]
        assert repo.universe(clock_factory("2020-01-01"), "us_equity") == [1]

    def test_a_recycled_ticker_resolves_to_the_right_company(self, db_session, clock_factory):
        _instrument(db_session, 1)
        _instrument(db_session, 2)
        db_session.add_all(
            [
                tables.SymbolMapping(
                    instrument_id=1,
                    ticker="XYZ",
                    valid_from=dt.date(2005, 1, 1),
                    valid_to=dt.date(2015, 1, 1),
                    source="test",
                ),
                tables.SymbolMapping(
                    instrument_id=2, ticker="XYZ", valid_from=dt.date(2016, 1, 1), source="test"
                ),
            ]
        )
        db_session.flush()
        repo = InstrumentRepository(db_session)

        assert repo.resolve_ticker(clock_factory("2010-06-01"), "XYZ") == 1
        assert repo.resolve_ticker(clock_factory("2020-06-01"), "XYZ") == 2

    def test_a_ticker_in_its_dormant_gap_does_not_resolve(self, db_session, clock_factory):
        _instrument(db_session, 1)
        db_session.add(
            tables.SymbolMapping(
                instrument_id=1,
                ticker="XYZ",
                valid_from=dt.date(2005, 1, 1),
                valid_to=dt.date(2015, 1, 1),
                source="test",
            )
        )
        db_session.flush()
        repo = InstrumentRepository(db_session)
        with pytest.raises(UniverseError, match="does not resolve"):
            repo.resolve_ticker(clock_factory("2015-06-01"), "XYZ")


class TestAdjustmentIsPointInTime:
    def test_prices_are_unadjusted_before_a_split_is_announced(self, db_session, clock_factory):
        _instrument(db_session)
        for day in range(1, 6):
            _bar(db_session, dt.date(2024, 3, day), "100", volume="1000000")
        ex_date = dt.date(2024, 3, 20)
        db_session.add(
            tables.CorporateAction(
                instrument_id=1,
                action_type=CorporateActionType.SPLIT.value,
                ex_date=ex_date,
                event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
                knowledge_time=dt.datetime(2024, 3, 10, 20, 0, tzinfo=UTC),
                knowledge_source=KnowledgeTimeSource.REPORTED.value,
                ratio=Decimal(2),
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)

        before = repo.history(
            clock_factory("2024-03-08"), 1, adjustment=AdjustmentPolicy.SPLIT_ONLY
        )
        assert before[0].close == Decimal("100.000000")

        after = repo.history(clock_factory("2024-03-11"), 1, adjustment=AdjustmentPolicy.SPLIT_ONLY)
        assert after[0].close == Decimal("50")
        assert after[0].volume == Decimal("2000000")

    def test_dollar_volume_survives_a_split_adjustment(self, db_session, clock_factory):
        _instrument(db_session)
        _bar(db_session, dt.date(2024, 3, 1), "100", volume="1000000")
        ex_date = dt.date(2024, 3, 20)
        db_session.add(
            tables.CorporateAction(
                instrument_id=1,
                action_type=CorporateActionType.SPLIT.value,
                ex_date=ex_date,
                event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
                knowledge_time=dt.datetime(2024, 3, 10, 20, 0, tzinfo=UTC),
                knowledge_source=KnowledgeTimeSource.REPORTED.value,
                ratio=Decimal(2),
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)

        raw = repo.history(clock_factory("2024-03-05"), 1, adjustment=AdjustmentPolicy.NONE)[0]
        adjusted = repo.history(clock_factory("2024-03-11"), 1)[0]
        assert adjusted.close * adjusted.volume == raw.close * raw.volume

    def test_dividends_are_left_alone_under_split_only(self, db_session, clock_factory):
        _instrument(db_session)
        _bar(db_session, dt.date(2024, 3, 1), "100")
        ex_date = dt.date(2024, 3, 20)
        db_session.add(
            tables.CorporateAction(
                instrument_id=1,
                action_type=CorporateActionType.CASH_DIVIDEND.value,
                ex_date=ex_date,
                event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
                knowledge_time=dt.datetime(2024, 3, 5, 20, 0, tzinfo=UTC),
                knowledge_source=KnowledgeTimeSource.REPORTED.value,
                ratio=Decimal(1),
                cash_amount=Decimal("2.50"),
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)
        clock = clock_factory("2024-03-11")

        assert repo.history(clock, 1, adjustment=AdjustmentPolicy.SPLIT_ONLY)[0].close == Decimal(
            "100.000000"
        )
        total_return = repo.history(clock, 1, adjustment=AdjustmentPolicy.TOTAL_RETURN)[0]
        assert total_return.close < Decimal("100")


class TestTimezoneIntegrity:
    def test_timestamps_survive_a_round_trip_as_aware_utc(self, db_session, clock_factory):
        _instrument(db_session)
        _bar(db_session, dt.date(2024, 3, 8), "100")
        bar = BarRepository(db_session).history(clock_factory("2024-03-09"), 1)[0]
        assert bar.knowledge_time.tzinfo is not None
        assert bar.knowledge_time.utcoffset() == dt.timedelta(0)

    def test_naive_timestamps_cannot_be_persisted(self, db_session):
        _instrument(db_session)
        db_session.add(
            tables.OhlcvBar(
                instrument_id=1,
                timeframe="1d",
                session_date=dt.date(2024, 3, 8),
                event_time=dt.datetime(2024, 3, 8, 21, 0),
                knowledge_time=dt.datetime(2024, 3, 8, 21, 20),
                knowledge_source="vendor_ingest",
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
                source="test",
            )
        )
        with pytest.raises(Exception, match="naive"):
            db_session.flush()
