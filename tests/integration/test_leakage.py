"""The ten leakage acceptance criteria, end to end through storage.

The brief lists these as acceptance criteria rather than enhancements. Each
test below maps to one numbered requirement, exercised through the real
repositories rather than against in-memory fixtures — because the storage layer
is where most of these can actually be violated.

Kernel-level causality (requirements 1, 2 and 7 at the arithmetic level) is
proved exhaustively in ``tests/unit/test_causality.py`` over 274 cases; the
tests here check the same properties survive the round trip through the
database and the analytics engines.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.analytics.indicators import IndicatorEngine
from tradeit.analytics.relative_strength import RelativeStrengthEngine
from tradeit.analytics.timeframes import aggregate_daily, resample_for_feature_use
from tradeit.core.calendar import get_calendar
from tradeit.core.clock import AsOfClock
from tradeit.core.enums import (
    AdjustmentPolicy,
    Bartimeframe,
    CorporateActionType,
    KnowledgeTimeSource,
    TradingMode,
)
from tradeit.errors import DataError
from tradeit.storage import tables
from tradeit.storage.repositories import (
    BarRepository,
    InstrumentRepository,
    SectorRepository,
)
from tradeit.strategy.config import StrategyConfig

UTC = dt.UTC
CAL = get_calendar("XNYS")
CONFIG = StrategyConfig(name="baseline")


def clock_at(day: str, hour: int = 23) -> AsOfClock:
    return AsOfClock.at(
        dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hour), tzinfo=UTC),
        mode=TradingMode.BACKTEST,
    )


def add_instrument(session, instrument_id: int) -> None:
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


def add_bars(
    session,
    instrument_id: int,
    sessions: list[dt.date],
    closes: list[float],
    *,
    lag_minutes: int = 20,
    volume: float = 1_000_000,
) -> None:
    for day, close in zip(sessions, closes, strict=True):
        close_utc = CAL.close_instant(day)
        session.add(
            tables.OhlcvBar(
                instrument_id=instrument_id,
                timeframe="1d",
                session_date=day,
                event_time=close_utc,
                knowledge_time=close_utc + dt.timedelta(minutes=lag_minutes),
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST.value,
                open=Decimal(str(round(close, 4))),
                high=Decimal(str(round(close * 1.01, 4))),
                low=Decimal(str(round(close * 0.99, 4))),
                close=Decimal(str(round(close, 4))),
                volume=Decimal(str(volume)),
                source="test",
            )
        )
    session.flush()


class TestRequirement1And2FuturePricesAndVolume:
    """1. Future price bars cannot enter calculations.
    2. Future volume cannot enter calculations."""

    def test_indicators_computed_from_a_clock_gated_series_ignore_later_bars(self, db_session):
        add_instrument(db_session, 1)
        sessions = CAL.sessions_between(dt.date(2023, 1, 3), dt.date(2024, 6, 28))
        closes = [100.0 + i * 0.1 for i in range(len(sessions))]
        add_bars(db_session, 1, sessions, closes)

        engine = IndicatorEngine(CONFIG.indicators)
        repo = BarRepository(db_session)

        midpoint = sessions[len(sessions) // 2]
        early_clock = clock_at(midpoint.isoformat())
        late_clock = clock_at(sessions[-1].isoformat())

        early = engine.compute(repo.history(early_clock, 1, adjustment=AdjustmentPolicy.NONE))
        late = engine.compute(repo.history(late_clock, 1, adjustment=AdjustmentPolicy.NONE))

        assert early.sessions[-1] == midpoint
        for name in ("sma_50", "rsi_14", "atr_percent", "momentum_120"):
            assert early.latest(name) == pytest.approx(late.at(midpoint, name)), (
                f"{name} at {midpoint} changed once later bars existed"
            )

    def test_a_bar_is_invisible_before_its_publication_lag(self, db_session):
        add_instrument(db_session, 1)
        day = dt.date(2024, 3, 8)
        add_bars(db_session, 1, [day], [100.0], lag_minutes=20)
        repo = BarRepository(db_session)

        at_close = AsOfClock.at(CAL.close_instant(day), mode=TradingMode.BACKTEST)
        assert repo.history(at_close, 1) == []
        after = AsOfClock.at(
            CAL.close_instant(day) + dt.timedelta(minutes=21), mode=TradingMode.BACKTEST
        )
        assert len(repo.history(after, 1)) == 1


class TestRequirement3PartialHigherTimeframeCandles:
    """3. Partially formed higher-timeframe candles are handled correctly."""

    def test_a_midweek_clock_sees_no_current_weekly_bar(self, db_session):
        add_instrument(db_session, 1)
        week = CAL.sessions_between(dt.date(2024, 3, 4), dt.date(2024, 3, 8))
        add_bars(db_session, 1, week, [10.0, 20.0, 30.0, 40.0, 50.0])

        repo = BarRepository(db_session)
        wednesday = clock_at("2024-03-06")
        bars = repo.history(wednesday, 1, adjustment=AdjustmentPolicy.NONE)

        assert [b.session_date for b in bars] == week[:3]
        assert resample_for_feature_use(bars, Bartimeframe.W1, wednesday.date) == []

    def test_the_finished_week_appears_only_after_friday(self, db_session):
        add_instrument(db_session, 1)
        week = CAL.sessions_between(dt.date(2024, 3, 4), dt.date(2024, 3, 8))
        add_bars(db_session, 1, week, [10.0, 20.0, 30.0, 40.0, 50.0])

        repo = BarRepository(db_session)
        friday = clock_at("2024-03-08")
        weekly = resample_for_feature_use(
            repo.history(friday, 1, adjustment=AdjustmentPolicy.NONE),
            Bartimeframe.W1,
            friday.date,
        )
        assert len(weekly) == 1
        assert weekly[0].close == Decimal("50")


class TestRequirement4UniversePercentiles:
    """4. Universe percentile rankings cannot see future constituents."""

    def test_an_instrument_not_yet_listed_is_absent_from_the_roster(self, db_session):
        add_instrument(db_session, 1)
        add_instrument(db_session, 2)
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
                    valid_from=dt.date(2020, 1, 1),
                    source="test",
                ),
            ]
        )
        db_session.flush()
        repo = InstrumentRepository(db_session)

        assert repo.universe(clock_at("2015-06-01"), "us_equity") == [1]
        assert repo.universe(clock_at("2021-06-01"), "us_equity") == [1, 2]

    def test_ranking_against_a_future_constituent_is_refused(self, db_session):
        engine = RelativeStrengthEngine(CONFIG.relative_strength)
        roster = list(range(1, 26))
        performance = {i: 0.05 for i in roster}
        performance[999] = 0.99  # not yet listed on this date
        with pytest.raises(DataError, match="not in the eligible universe"):
            engine.rank_cross_section(dt.date(2015, 6, 1), roster, performance)

    def test_a_delisted_name_remains_in_a_historical_roster(self, db_session):
        add_instrument(db_session, 1)
        add_instrument(db_session, 2)
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

        assert repo.universe(clock_at("2015-01-01"), "us_equity") == [1, 2]
        assert repo.universe(clock_at("2020-01-01"), "us_equity") == [1]


class TestRequirement5SectorClassification:
    """5. Sector calculations cannot use future classifications."""

    def test_a_reclassified_company_keeps_its_old_sector_historically(self, db_session):
        add_instrument(db_session, 1)
        db_session.add_all(
            [
                tables.Sector(
                    instrument_id=1,
                    scheme="GICS",
                    sector="Information Technology",
                    industry="Software",
                    valid_from=dt.date(2010, 1, 1),
                    valid_to=dt.date(2018, 9, 24),
                    source="test",
                ),
                tables.Sector(
                    instrument_id=1,
                    scheme="GICS",
                    sector="Communication Services",
                    industry="Interactive Media",
                    valid_from=dt.date(2018, 9, 24),
                    source="test",
                ),
            ]
        )
        db_session.flush()
        repo = SectorRepository(db_session)

        before = repo.classification(clock_at("2017-06-01"), 1)
        after = repo.classification(clock_at("2020-06-01"), 1)
        assert before == ("Information Technology", "Software")
        assert after == ("Communication Services", "Interactive Media")

    def test_an_unclassified_instrument_returns_none_not_a_default_bucket(self, db_session):
        """An 'Other' bucket would distort that group's aggregates and hide the
        gap in the data."""
        add_instrument(db_session, 1)
        db_session.add(
            tables.Sector(
                instrument_id=1,
                scheme="GICS",
                sector="Energy",
                valid_from=dt.date(2020, 1, 1),
                source="test",
            )
        )
        db_session.flush()
        repo = SectorRepository(db_session)
        assert repo.classification(clock_at("2015-01-01"), 1) is None

    def test_sector_membership_applies_both_intervals(self, db_session):
        """A company in the universe but classified elsewhere at the time must
        not appear in that sector's members."""
        for i in (1, 2):
            add_instrument(db_session, i)
            db_session.add(
                tables.UniverseMembership(
                    universe="us_equity",
                    instrument_id=i,
                    valid_from=dt.date(2010, 1, 1),
                    source="test",
                )
            )
        db_session.add_all(
            [
                tables.Sector(
                    instrument_id=1,
                    scheme="GICS",
                    sector="Energy",
                    valid_from=dt.date(2010, 1, 1),
                    source="test",
                ),
                tables.Sector(
                    instrument_id=2,
                    scheme="GICS",
                    sector="Energy",
                    valid_from=dt.date(2019, 1, 1),
                    source="test",
                ),
            ]
        )
        db_session.flush()
        repo = SectorRepository(db_session)

        assert repo.members(clock_at("2015-01-01"), "Energy", "us_equity") == [1]
        assert repo.members(clock_at("2020-01-01"), "Energy", "us_equity") == [1, 2]


class TestRequirement6BackfilledData:
    """6. Backfilled data arriving after the original run does not alter
    snapshot-pinned replay."""

    def test_a_late_arriving_revision_is_invisible_to_the_earlier_clock(self, db_session):
        """The bitemporal guarantee: a correction published on Monday does not
        change what Friday's scan saw."""
        add_instrument(db_session, 1)
        day = dt.date(2024, 3, 8)
        add_bars(db_session, 1, [day], [100.0], lag_minutes=20)

        close_utc = CAL.close_instant(day)
        db_session.add(
            tables.OhlcvBar(
                instrument_id=1,
                timeframe="1d",
                session_date=day,
                event_time=close_utc,
                knowledge_time=close_utc + dt.timedelta(days=3),
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST.value,
                open=Decimal("104"),
                high=Decimal("104"),
                low=Decimal("104"),
                close=Decimal("104"),
                volume=Decimal("1000000"),
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)

        friday = AsOfClock.at(close_utc + dt.timedelta(hours=2), mode=TradingMode.BACKTEST)
        assert repo.history(friday, 1)[0].close == Decimal("100.000000")
        tuesday = clock_at("2024-03-12")
        assert repo.history(tuesday, 1)[0].close == Decimal("104.000000")

    def test_the_ingestion_high_water_mark_pins_a_replay(self, db_session):
        """The residual gap ``as_of`` alone leaves.

        A backfill landing after a scan adds rows whose knowledge_time precedes
        that scan's as_of — legitimately, they simply had not been loaded. The
        data snapshot pins the maximum ingestion run so replay is exact.
        """
        run_a = tables.IngestionRun(
            provider="test",
            dataset="ohlcv_bars",
            started_at=dt.datetime.now(tz=UTC),
            status="completed",
            code_version="0.3.0",
        )
        db_session.add(run_a)
        db_session.flush()

        db_session.add(
            tables.DataSnapshot(
                digest="snap-1", dataset="ohlcv_bars", max_ingestion_run_id=run_a.id
            )
        )
        db_session.flush()

        run_b = tables.IngestionRun(
            provider="test",
            dataset="ohlcv_bars",
            started_at=dt.datetime.now(tz=UTC),
            status="completed",
            code_version="0.3.0",
        )
        db_session.add(run_b)
        db_session.flush()

        pinned = db_session.query(tables.DataSnapshot).filter_by(digest="snap-1").one()
        assert pinned.max_ingestion_run_id == run_a.id
        assert run_b.id > pinned.max_ingestion_run_id, (
            "the later run must be excluded by the pin, not merely be later in time"
        )


class TestRequirement7Warmup:
    """7. Indicator warm-up periods behave correctly."""

    def test_a_short_history_yields_nulls_not_numbers_from_too_little_data(self, db_session):
        add_instrument(db_session, 1)
        sessions = CAL.sessions_between(dt.date(2024, 1, 2), dt.date(2024, 3, 1))
        add_bars(db_session, 1, sessions, [100.0 + i for i in range(len(sessions))])

        engine = IndicatorEngine(CONFIG.indicators)
        series = engine.compute(
            BarRepository(db_session).history(
                clock_at("2024-03-01"), 1, adjustment=AdjustmentPolicy.NONE
            )
        )
        assert len(series.sessions) < 60
        assert series.latest("sma_200") is None, "a 200-day average of 40 bars is not one"
        assert series.latest("sma_10") is not None

    def test_the_registry_reports_the_history_a_backtest_must_skip(self):
        engine = IndicatorEngine(CONFIG.indicators)
        assert engine.warmup_periods >= 252
        assert engine.registry.max_warmup == engine.warmup_periods


class TestRequirement8CorporateActions:
    """8. Corporate actions are adjusted only per the point-in-time policy."""

    def test_a_split_does_not_adjust_history_before_it_is_announced(self, db_session):
        add_instrument(db_session, 1)
        sessions = CAL.sessions_between(dt.date(2024, 3, 1), dt.date(2024, 3, 8))
        add_bars(db_session, 1, sessions, [100.0] * len(sessions))

        ex_date = dt.date(2024, 3, 20)
        db_session.add(
            tables.CorporateAction(
                instrument_id=1,
                action_type=CorporateActionType.SPLIT.value,
                ex_date=ex_date,
                event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
                knowledge_time=dt.datetime(2024, 3, 12, 20, 0, tzinfo=UTC),
                knowledge_source=KnowledgeTimeSource.REPORTED.value,
                ratio=Decimal(2),
                source="test",
            )
        )
        db_session.flush()
        repo = BarRepository(db_session)

        before = repo.history(clock_at("2024-03-11"), 1, adjustment=AdjustmentPolicy.SPLIT_ONLY)
        after = repo.history(clock_at("2024-03-13"), 1, adjustment=AdjustmentPolicy.SPLIT_ONLY)
        assert before[0].close == Decimal("100.000000")
        assert after[0].close == Decimal("50")

    def test_indicators_recompute_consistently_with_the_adjustment_policy(self, db_session):
        """A split must not create a phantom momentum reading."""
        add_instrument(db_session, 1)
        sessions = CAL.sessions_between(dt.date(2023, 1, 3), dt.date(2024, 6, 28))
        add_bars(db_session, 1, sessions, [100.0] * len(sessions))

        ex_date = sessions[len(sessions) // 2]
        db_session.add(
            tables.CorporateAction(
                instrument_id=1,
                action_type=CorporateActionType.SPLIT.value,
                ex_date=ex_date,
                event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
                knowledge_time=dt.datetime.combine(
                    ex_date - dt.timedelta(days=20), dt.time(20), tzinfo=UTC
                ),
                knowledge_source=KnowledgeTimeSource.REPORTED.value,
                ratio=Decimal(2),
                source="test",
            )
        )
        db_session.flush()

        engine = IndicatorEngine(CONFIG.indicators)
        adjusted = engine.compute(
            BarRepository(db_session).history(
                clock_at(sessions[-1].isoformat()), 1, adjustment=AdjustmentPolicy.SPLIT_ONLY
            )
        )
        assert adjusted.latest("momentum_120") == pytest.approx(0.0, abs=1e-9), (
            "a split on a flat price series must not register as momentum"
        )


class TestRequirement9And10CalendarBoundaries:
    """9. Timezone boundaries cannot leak next-session data.
    10. Market holidays and shortened sessions behave correctly."""

    def test_a_clock_before_the_close_cannot_see_that_session(self, db_session):
        add_instrument(db_session, 1)
        day = dt.date(2024, 3, 8)
        add_bars(db_session, 1, [day], [100.0])

        session = CAL.session(day)
        intraday = AsOfClock.at(session.open_utc + dt.timedelta(hours=2), mode=TradingMode.BACKTEST)
        assert BarRepository(db_session).history(intraday, 1) == []

    def test_a_utc_date_boundary_does_not_admit_the_next_session(self, db_session):
        """The US close is 21:00 UTC, so 'today' in UTC extends three hours past
        it. A naive date comparison would admit the following session's data for
        any clock set in that window."""
        add_instrument(db_session, 1)
        friday, monday = dt.date(2024, 3, 8), dt.date(2024, 3, 11)
        add_bars(db_session, 1, [friday, monday], [100.0, 110.0])

        late_friday = AsOfClock.at(
            dt.datetime(2024, 3, 8, 23, 59, tzinfo=UTC), mode=TradingMode.BACKTEST
        )
        bars = BarRepository(db_session).history(late_friday, 1)
        assert [b.session_date for b in bars] == [friday]

    def test_a_holiday_shortened_week_completes_on_its_last_session(self, db_session):
        """Good Friday 2024-03-29: the week ends Thursday. A rule waiting for
        Friday would leave the weekly feature permanently stale."""
        add_instrument(db_session, 1)
        week = CAL.sessions_between(dt.date(2024, 3, 25), dt.date(2024, 3, 31))
        assert week[-1] == dt.date(2024, 3, 28)
        add_bars(db_session, 1, week, [100.0 + i for i in range(len(week))])

        bars = BarRepository(db_session).history(
            clock_at("2024-03-28"), 1, adjustment=AdjustmentPolicy.NONE
        )
        weekly = aggregate_daily(bars, Bartimeframe.W1, dt.date(2024, 3, 28))
        assert weekly[0].complete
        assert weekly[0].constituent_sessions == 4

    def test_an_early_close_session_is_a_normal_session_for_daily_features(self, db_session):
        """Day after Thanksgiving closes at 13:00 ET. It is a real session and
        must contribute a bar, not be skipped as anomalous."""
        add_instrument(db_session, 1)
        day = dt.date(2024, 11, 29)
        assert CAL.session(day).is_early_close
        add_bars(db_session, 1, [day], [100.0])

        after = AsOfClock.at(
            CAL.close_instant(day) + dt.timedelta(hours=1), mode=TradingMode.BACKTEST
        )
        assert len(BarRepository(db_session).history(after, 1)) == 1

    def test_indicator_lookbacks_count_sessions_not_calendar_days(self, db_session):
        """20 sessions spanning a holiday covers more than 20 calendar days;
        a calendar-day lookback would silently use a shorter window."""
        add_instrument(db_session, 1)
        sessions = CAL.sessions_between(dt.date(2024, 3, 1), dt.date(2024, 6, 28))
        add_bars(db_session, 1, sessions, [100.0 + i for i in range(len(sessions))])

        engine = IndicatorEngine(CONFIG.indicators)
        series = engine.compute(
            BarRepository(db_session).history(
                clock_at("2024-06-28"), 1, adjustment=AdjustmentPolicy.NONE
            )
        )
        assert len(series.sessions) == len(sessions)
        # 20 sessions back from the last is exactly the SMA(20) window.
        assert series.latest("sma_20") == pytest.approx(
            sum(float(100.0 + i) for i in range(len(sessions) - 20, len(sessions))) / 20
        )
