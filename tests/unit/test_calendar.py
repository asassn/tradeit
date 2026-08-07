from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.calendar import TradingCalendar, _easter, get_calendar
from tradeit.errors import DataError


@pytest.fixture
def cal() -> TradingCalendar:
    return get_calendar("XNYS")


def test_weekends_are_not_sessions(cal):
    assert not cal.is_session(dt.date(2024, 3, 9))  # Saturday
    assert not cal.is_session(dt.date(2024, 3, 10))  # Sunday
    assert cal.is_session(dt.date(2024, 3, 8))  # Friday


@pytest.mark.parametrize(
    "holiday",
    [
        dt.date(2024, 1, 1),  # New Year's Day
        dt.date(2024, 1, 15),  # MLK Jr
        dt.date(2024, 3, 29),  # Good Friday
        dt.date(2024, 5, 27),  # Memorial Day
        dt.date(2024, 6, 19),  # Juneteenth
        dt.date(2024, 7, 4),  # Independence Day
        dt.date(2024, 9, 2),  # Labor Day
        dt.date(2024, 11, 28),  # Thanksgiving
        dt.date(2024, 12, 25),  # Christmas
    ],
)
def test_market_holidays_are_closed(cal, holiday):
    assert not cal.is_session(holiday)


def test_navigation_skips_the_holiday_weekend(cal):
    # Good Friday 2024-03-29 falls before a weekend: Thursday's next is Monday.
    assert cal.next_session(dt.date(2024, 3, 28)) == dt.date(2024, 4, 1)
    assert cal.previous_session(dt.date(2024, 4, 1)) == dt.date(2024, 3, 28)


def test_shift_sessions_round_trips(cal):
    start = dt.date(2024, 5, 1)
    assert cal.shift_sessions(cal.shift_sessions(start, 20), -20) == start


def test_session_count_is_inclusive(cal):
    assert cal.session_count(dt.date(2024, 3, 4), dt.date(2024, 3, 8)) == 5


def test_backwards_range_is_an_error(cal):
    with pytest.raises(DataError, match="before start"):
        cal.sessions_between(dt.date(2024, 3, 8), dt.date(2024, 3, 1))


def test_early_close_is_flagged(cal):
    """Day after Thanksgiving 2024 closes at 13:00 ET."""
    session = cal.session(dt.date(2024, 11, 29))
    assert session.is_early_close
    assert session.duration < dt.timedelta(hours=5)


def test_regular_session_is_six_and_a_half_hours(cal):
    assert cal.session(dt.date(2024, 3, 8)).duration == dt.timedelta(hours=6, minutes=30)


def test_instant_outside_regular_hours_maps_to_no_session(cal):
    utc = dt.UTC
    midday = dt.datetime(2024, 3, 8, 18, 0, tzinfo=utc)
    assert cal.session_for_instant(midday) == dt.date(2024, 3, 8)
    assert cal.session_for_instant(dt.datetime(2024, 3, 8, 8, 0, tzinfo=utc)) is None
    assert cal.session_for_instant(dt.datetime(2024, 3, 9, 18, 0, tzinfo=utc)) is None


def test_requesting_a_non_session_raises(cal):
    with pytest.raises(DataError, match="not a trading session"):
        cal.session(dt.date(2024, 12, 25))


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2024, dt.date(2024, 3, 31)), (2025, dt.date(2025, 4, 20)), (2021, dt.date(2021, 4, 4))],
)
def test_easter_computation(year, expected):
    assert _easter(year) == expected
