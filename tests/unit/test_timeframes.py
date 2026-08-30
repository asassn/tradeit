"""Multi-timeframe construction, with the partial-candle trap as the focus.

The brief names this explicitly: at Wednesday noon a weekly candle must not
contain Thursday or Friday information. These tests encode that.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.analytics.timeframes import (
    aggregate_daily,
    aggregate_intraday,
    align_to_daily,
    completed_only,
    resample_for_feature_use,
    to_ohlcv_bars,
)
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.errors import DataError

UTC = dt.UTC
CAL = get_calendar("XNYS")


def daily_bar(
    day: dt.date,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000,
) -> OhlcvBar:
    close_utc = CAL.close_instant(day)
    return OhlcvBar(
        instrument_id=1,
        timeframe=Bartimeframe.D1,
        session_date=day,
        event_time=close_utc,
        knowledge_time=close_utc + dt.timedelta(minutes=20),
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        open=Decimal(str(close)),
        high=Decimal(str(high if high is not None else close + 1)),
        low=Decimal(str(low if low is not None else close - 1)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


def week_of(monday: dt.date) -> list[OhlcvBar]:
    """Mon-Fri bars with closes 10, 20, 30, 40, 50."""
    sessions = CAL.sessions_between(monday, monday + dt.timedelta(days=4))
    return [daily_bar(day, 10.0 * (i + 1)) for i, day in enumerate(sessions)]


class TestWeeklyCompleteness:
    """The core requirement, stated four ways."""

    def test_a_week_in_progress_is_not_complete(self):
        """At Wednesday, this week's bar is provisional."""
        monday = dt.date(2024, 3, 4)
        wednesday = dt.date(2024, 3, 6)
        partial = [b for b in week_of(monday) if b.session_date <= wednesday]

        weekly = aggregate_daily(partial, Bartimeframe.W1, as_of_session=wednesday)
        assert len(weekly) == 1
        assert not weekly[0].complete

    def test_the_partial_week_never_reaches_a_feature(self):
        """The gate that makes the trap impossible to fall into by accident."""
        monday = dt.date(2024, 3, 4)
        wednesday = dt.date(2024, 3, 6)
        partial = [b for b in week_of(monday) if b.session_date <= wednesday]

        assert resample_for_feature_use(partial, Bartimeframe.W1, wednesday) == []

    def test_a_finished_week_is_complete(self):
        monday = dt.date(2024, 3, 4)
        friday = dt.date(2024, 3, 8)
        weekly = aggregate_daily(week_of(monday), Bartimeframe.W1, as_of_session=friday)
        assert len(weekly) == 1 and weekly[0].complete

    def test_wednesdays_weekly_bar_contains_no_thursday_or_friday_information(self):
        """The brief's example, tested literally.

        The Wednesday view and the Friday view of the same week must differ,
        and the Wednesday view must reflect only Mon-Wed.
        """
        monday = dt.date(2024, 3, 4)
        full_week = week_of(monday)
        through_wednesday = full_week[:3]

        midweek = aggregate_daily(
            through_wednesday, Bartimeframe.W1, as_of_session=dt.date(2024, 3, 6)
        )[0]
        finished = aggregate_daily(full_week, Bartimeframe.W1, as_of_session=dt.date(2024, 3, 8))[0]

        assert midweek.close == Decimal("30")  # Wednesday's close
        assert finished.close == Decimal("50")  # Friday's close
        assert midweek.high < finished.high, "Thursday/Friday highs leaked into Wednesday"
        assert midweek.constituent_sessions == 3
        assert finished.constituent_sessions == 5


class TestWeeklyAggregation:
    def test_ohlc_is_folded_correctly(self):
        monday = dt.date(2024, 3, 4)
        bars = week_of(monday)
        weekly = aggregate_daily(bars, Bartimeframe.W1, dt.date(2024, 3, 8))[0]

        assert weekly.open == bars[0].open, "open comes from the first session"
        assert weekly.close == bars[-1].close, "close comes from the last session"
        assert weekly.high == max(b.high for b in bars)
        assert weekly.low == min(b.low for b in bars)
        assert weekly.volume == sum(b.volume for b in bars)

    def test_knowledge_time_comes_from_the_last_constituent(self):
        """Otherwise a whole week becomes readable from its Monday."""
        monday = dt.date(2024, 3, 4)
        bars = week_of(monday)
        weekly = aggregate_daily(bars, Bartimeframe.W1, dt.date(2024, 3, 8))[0]
        assert weekly.knowledge_time == max(b.knowledge_time for b in bars)
        assert weekly.knowledge_time > bars[0].knowledge_time

    def test_a_holiday_shortened_week_still_completes(self):
        """Good Friday 2024-03-29: the week ends on Thursday and is complete then.

        A rule that waits for Friday would leave this week permanently
        provisional, and the weekly feature would silently go stale.
        """
        monday = dt.date(2024, 3, 25)
        sessions = CAL.sessions_between(monday, monday + dt.timedelta(days=6))
        assert sessions[-1] == dt.date(2024, 3, 28), "expected a Thursday close that week"

        bars = [daily_bar(day, 100.0 + i) for i, day in enumerate(sessions)]
        weekly = aggregate_daily(bars, Bartimeframe.W1, as_of_session=dt.date(2024, 3, 28))
        assert weekly[0].complete
        assert weekly[0].constituent_sessions == 4

    def test_multiple_weeks_are_grouped_independently(self):
        bars = week_of(dt.date(2024, 3, 4)) + week_of(dt.date(2024, 3, 11))
        weekly = aggregate_daily(bars, Bartimeframe.W1, dt.date(2024, 3, 15))
        assert len(weekly) == 2
        assert all(b.complete for b in weekly)

    def test_a_year_boundary_uses_iso_weeks(self):
        """2024-12-30 and 2025-01-02 are the same ISO week."""
        sessions = CAL.sessions_between(dt.date(2024, 12, 30), dt.date(2025, 1, 3))
        bars = [daily_bar(day, 100.0 + i) for i, day in enumerate(sessions)]
        weekly = aggregate_daily(bars, Bartimeframe.W1, dt.date(2025, 1, 3))
        assert len(weekly) == 1, "an ISO week spanning New Year must not split"

    def test_a_month_is_incomplete_until_its_last_session(self):
        partial = CAL.sessions_between(dt.date(2024, 4, 1), dt.date(2024, 4, 25))
        bars = [daily_bar(day, 100.0 + i) for i, day in enumerate(partial)]
        monthly = aggregate_daily(bars, Bartimeframe.MN1, dt.date(2024, 4, 25))
        assert len(monthly) == 1
        assert not monthly[0].complete, "April trades past the 25th"

        full = CAL.sessions_between(dt.date(2024, 4, 1), dt.date(2024, 4, 30))
        bars = [daily_bar(day, 100.0 + i) for i, day in enumerate(full)]
        monthly = aggregate_daily(bars, Bartimeframe.MN1, dt.date(2024, 4, 30))
        assert monthly[0].complete

    def test_month_end_completeness_follows_the_calendar_not_the_date(self):
        """March 2024 ends on the 28th: the 29th is Good Friday and the 30th-31st
        are the weekend. A rule keyed on the calendar-month last day would leave
        March permanently provisional and the monthly feature silently stale."""
        sessions = CAL.sessions_between(dt.date(2024, 3, 1), dt.date(2024, 3, 31))
        assert sessions[-1] == dt.date(2024, 3, 28)

        bars = [daily_bar(day, 100.0 + i) for i, day in enumerate(sessions)]
        monthly = aggregate_daily(bars, Bartimeframe.MN1, dt.date(2024, 3, 28))
        assert monthly[0].complete


class TestGuards:
    def test_a_bar_after_the_as_of_session_is_refused(self):
        """Defence in depth: the caller should pass a clock-gated series, and
        this catches it when they do not."""
        bars = week_of(dt.date(2024, 3, 4))
        with pytest.raises(DataError, match="after as_of_session"):
            aggregate_daily(bars, Bartimeframe.W1, as_of_session=dt.date(2024, 3, 6))

    def test_non_daily_input_is_refused(self):
        bar = daily_bar(dt.date(2024, 3, 4), 100.0)
        weekly = bar.model_copy(update={"timeframe": Bartimeframe.W1})
        with pytest.raises(DataError, match="expected daily bars"):
            aggregate_daily([weekly], Bartimeframe.W1, dt.date(2024, 3, 8))

    def test_an_unsupported_timeframe_is_refused(self):
        with pytest.raises(DataError, match="cannot build"):
            aggregate_daily(week_of(dt.date(2024, 3, 4)), Bartimeframe.H1, dt.date(2024, 3, 8))

    def test_converting_an_incomplete_bar_is_refused(self):
        """It would produce something indistinguishable from a finished bar."""
        partial = week_of(dt.date(2024, 3, 4))[:3]
        weekly = aggregate_daily(partial, Bartimeframe.W1, dt.date(2024, 3, 6))
        with pytest.raises(DataError, match="incomplete"):
            to_ohlcv_bars(weekly, instrument_id=1)

    def test_completed_bars_convert_to_validated_domain_bars(self):
        weekly = aggregate_daily(week_of(dt.date(2024, 3, 4)), Bartimeframe.W1, dt.date(2024, 3, 8))
        converted = to_ohlcv_bars(weekly, instrument_id=1)
        assert len(converted) == 1
        assert converted[0].timeframe is Bartimeframe.W1
        assert converted[0].session_date == dt.date(2024, 3, 8)


class TestAlignment:
    def test_a_daily_session_sees_only_the_last_completed_week(self):
        """The mechanism that carries weekly context into a daily feature vector."""
        bars = week_of(dt.date(2024, 3, 4)) + week_of(dt.date(2024, 3, 11))
        sessions = [b.session_date for b in bars]
        weekly = aggregate_daily(bars, Bartimeframe.W1, sessions[-1])

        aligned = align_to_daily(sessions, weekly)

        # Days within week 1 have no completed weekly bar yet.
        assert all(a is None for a in aligned[:4])
        # Friday of week 1 completes it.
        assert aligned[4] is not None and aligned[4].period_end == dt.date(2024, 3, 8)
        # Every day of week 2 still sees week 1 -- not its own partial week.
        assert all(a.period_end == dt.date(2024, 3, 8) for a in aligned[5:9])
        assert aligned[9].period_end == dt.date(2024, 3, 15)

    def test_alignment_never_returns_a_future_bar(self):
        bars = week_of(dt.date(2024, 3, 4)) + week_of(dt.date(2024, 3, 11))
        sessions = [b.session_date for b in bars]
        weekly = aggregate_daily(bars, Bartimeframe.W1, sessions[-1])
        for session, aligned in zip(sessions, align_to_daily(sessions, weekly), strict=True):
            if aligned is not None:
                assert aligned.period_end <= session

    def test_alignment_ignores_incomplete_bars_entirely(self):
        bars = week_of(dt.date(2024, 3, 4))[:3]
        sessions = [b.session_date for b in bars]
        weekly = aggregate_daily(bars, Bartimeframe.W1, sessions[-1])
        assert completed_only(weekly) == []
        assert align_to_daily(sessions, weekly) == [None, None, None]


class TestIntraday:
    """Intraday resampling anchored to the session open."""

    def minute_bars(self, day: dt.date, count: int, start_minute: int = 0) -> list[OhlcvBar]:
        session = CAL.session(day)
        out = []
        for i in range(count):
            close_at = session.open_utc + dt.timedelta(minutes=start_minute + i + 1)
            price = 100.0 + i
            out.append(
                OhlcvBar(
                    instrument_id=1,
                    timeframe=Bartimeframe.M1,
                    session_date=day,
                    event_time=close_at,
                    knowledge_time=close_at,
                    knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                    open=Decimal(str(price)),
                    high=Decimal(str(price + 0.5)),
                    low=Decimal(str(price - 0.5)),
                    close=Decimal(str(price)),
                    volume=Decimal("1000"),
                )
            )
        return out

    def test_buckets_are_anchored_to_the_session_open_not_the_wall_clock(self):
        """A 09:30 open produces 09:30/10:30/11:30 hourly bars, not 09:00/10:00.

        Clock-aligned buckets would make the first bar of every session cover
        30 minutes while the rest cover 60 -- not comparable to each other.
        """
        day = dt.date(2024, 3, 5)
        session = CAL.session(day)
        bars = self.minute_bars(day, 120)
        hourly = aggregate_intraday(bars, Bartimeframe.H1, as_of=session.close_utc)

        assert len(hourly) == 2
        assert hourly[0].constituent_sessions == 60
        assert hourly[1].constituent_sessions == 60

    def test_a_bucket_still_open_is_incomplete(self):
        day = dt.date(2024, 3, 5)
        session = CAL.session(day)
        bars = self.minute_bars(day, 90)
        midway = session.open_utc + dt.timedelta(minutes=90)

        hourly = aggregate_intraday(bars, Bartimeframe.H1, as_of=midway)
        assert hourly[0].complete, "the first full hour has elapsed"
        assert not hourly[1].complete, "the second hour is still running"

    def test_the_short_final_bucket_of_a_session_is_complete(self):
        """6.5 hours does not divide into 4-hour buckets. The 2.5-hour remainder
        is a real bar covering the time the market was open, and treating it as
        provisional would leave the last bar of every session unusable."""
        day = dt.date(2024, 3, 5)
        session = CAL.session(day)
        bars = self.minute_bars(day, 390)
        four_hour = aggregate_intraday(bars, Bartimeframe.H4, as_of=session.close_utc)

        assert len(four_hour) == 2
        assert four_hour[0].constituent_sessions == 240
        assert four_hour[1].constituent_sessions == 150
        assert all(b.complete for b in four_hour)

    def test_an_early_close_truncates_the_final_bucket_legitimately(self):
        """Day after Thanksgiving 2024 closes at 13:00 ET -- a 3.5-hour session."""
        day = dt.date(2024, 11, 29)
        session = CAL.session(day)
        assert session.is_early_close

        minutes = int((session.close_utc - session.open_utc).total_seconds() // 60)
        bars = self.minute_bars(day, minutes)
        hourly = aggregate_intraday(bars, Bartimeframe.H1, as_of=session.close_utc)

        assert sum(b.constituent_sessions for b in hourly) == minutes
        assert all(b.complete for b in hourly)
        assert hourly[-1].constituent_sessions < 60

    def test_sessions_do_not_bleed_into_each_other(self):
        """The overnight gap must reset bucketing; otherwise a 4-hour bar spans
        two days and its 'open' is yesterday's."""
        bars = self.minute_bars(dt.date(2024, 3, 5), 60) + self.minute_bars(dt.date(2024, 3, 6), 60)
        hourly = aggregate_intraday(
            bars, Bartimeframe.H1, as_of=CAL.close_instant(dt.date(2024, 3, 6))
        )
        assert len(hourly) == 2
        assert hourly[0].period_end == dt.date(2024, 3, 5)
        assert hourly[1].period_end == dt.date(2024, 3, 6)

    def test_a_bar_after_as_of_is_refused(self):
        day = dt.date(2024, 3, 5)
        session = CAL.session(day)
        bars = self.minute_bars(day, 60)
        with pytest.raises(DataError, match="after as_of"):
            aggregate_intraday(
                bars, Bartimeframe.H1, as_of=session.open_utc + dt.timedelta(minutes=30)
            )

    def test_fifteen_minute_buckets(self):
        day = dt.date(2024, 3, 5)
        session = CAL.session(day)
        bars = self.minute_bars(day, 60)
        result = aggregate_intraday(bars, Bartimeframe.M15, as_of=session.close_utc)
        assert len(result) == 4
        assert all(b.constituent_sessions == 15 for b in result)
