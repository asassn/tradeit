"""The off-by-one that dated every scheduled observation one day late.

Each case is an instant where the UTC calendar and the exchange calendar
disagree. None of them can be reached by waiting, which is why `market_today`
takes `now`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.errors import DataError
from tradeit.forward.sessions import latest_session_at_or_before, market_today

# 22:10 America/New_York on Thursday 2026-10-22 is 02:10 UTC on Friday the
# 23rd. This is the exact instant the job fires, and the exact bug: the run
# that produced Thursday's bars wrote them up as Friday's session.
FIRES_AT = dt.datetime(2026, 10, 23, 2, 10, tzinfo=dt.UTC)

WEEK = [
    dt.date(2026, 10, 19),  # Mon
    dt.date(2026, 10, 20),
    dt.date(2026, 10, 21),
    dt.date(2026, 10, 22),
    dt.date(2026, 10, 23),  # Fri
    dt.date(2026, 10, 26),  # Mon -- no 24th or 25th; the weekend is absent
]


def test_the_nightly_run_is_dated_by_the_exchanges_calendar() -> None:
    """**The regression.** UTC says the 23rd; the exchange says the 22nd.

    A `market_today` that returned `now.date()` passes nothing here, which is
    the point: the old code's answer was 2026-10-23 and the bars it wrote were
    2026-10-22.
    """
    assert market_today(FIRES_AT) == dt.date(2026, 10, 22)


def test_a_midday_run_is_unaffected() -> None:
    """Why this hid: the first run was manual, at midday, and was correct.

    16:39 UTC is 12:39 EDT, same date either way. The bug only appears once
    the job runs on its schedule, which is also when nobody is watching.
    """
    midday = dt.datetime(2026, 10, 21, 16, 39, tzinfo=dt.UTC)
    assert market_today(midday) == dt.date(2026, 10, 21)


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(DataError, match="naive"):
        market_today(dt.datetime(2026, 10, 23, 2, 10))


def test_a_session_resolves_to_itself() -> None:
    assert latest_session_at_or_before(WEEK, dt.date(2026, 10, 22)) == dt.date(2026, 10, 22)


def test_a_saturday_walks_back_to_friday() -> None:
    """The weekend case, which would otherwise assert a session that never was.

    A Friday-night run stamps Saturday under the old code, and the vendor
    still returns Friday's bar for a window ending Saturday -- so the record
    would claim a Saturday session that had a price. Walking back is correct
    rather than merely safe: the last thing the market did is still the last
    thing it did, and re-asking is idempotent on prices.
    """
    assert latest_session_at_or_before(WEEK, dt.date(2026, 10, 24)) == dt.date(2026, 10, 23)
    assert latest_session_at_or_before(WEEK, dt.date(2026, 10, 25)) == dt.date(2026, 10, 23)


def test_a_holiday_walks_back_too() -> None:
    """A weekday absent from the calendar is a holiday, and behaves the same."""
    thanksgiving_week = [dt.date(2026, 11, 25), dt.date(2026, 11, 27)]
    assert latest_session_at_or_before(thanksgiving_week, dt.date(2026, 11, 26)) == dt.date(
        2026, 11, 25
    )


def test_a_calendar_with_no_answer_raises_rather_than_guessing() -> None:
    """Fails closed. Returning `day` would record a non-session as a session."""
    with pytest.raises(DataError, match="no trading session at or before"):
        latest_session_at_or_before(WEEK, dt.date(1989, 1, 3))
