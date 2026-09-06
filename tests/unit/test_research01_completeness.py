"""Holding a price bar is not the same as holding a complete series.

The gate counts a dead registrant as covered on one bar. A company that failed
in 2005 whose prices stop in 2001 passes that test while hiding the four years
that killed it, which is a subtler version of not holding it at all. These
tests pin the two ways a series fails, and pin that they fail independently.
"""

from __future__ import annotations

import datetime as dt

from tradeit.core.calendar import TradingCalendar
from tradeit.research01.completeness import (
    Reach,
    assess_series,
    summarise,
)

CAL = TradingCalendar()


def _sessions(start: dt.date, end: dt.date, *, keep: int = 1) -> list[dt.date]:
    """Every nth session in a range, so density can be dialled."""
    return CAL.sessions_between(start, end)[::keep]


def test_an_empty_series_is_not_a_series() -> None:
    assert assess_series([], exit_date=dt.date(2005, 1, 1)) is None


def test_a_full_series_is_fully_dense() -> None:
    days = _sessions(dt.date(2001, 1, 2), dt.date(2004, 12, 31))
    got = assess_series(days, exit_date=dt.date(2005, 1, 10))
    assert got is not None
    assert got.density == 1.0
    assert got.dense_enough


def test_density_counts_sessions_not_calendar_days() -> None:
    """A 252/365 approximation reads a holiday-heavy span as holed. The real
    schedule is what the corpus is measured against."""
    days = _sessions(dt.date(2003, 11, 20), dt.date(2004, 1, 8))
    got = assess_series(days, exit_date=None)
    assert got is not None
    assert got.density == 1.0
    assert got.sessions_expected < (got.last - got.first).days


def test_a_holed_series_is_caught_even_though_it_spans_the_whole_life() -> None:
    days = _sessions(dt.date(2001, 1, 2), dt.date(2004, 12, 31), keep=3)
    got = assess_series(days, exit_date=dt.date(2005, 1, 10))
    assert got is not None
    assert got.density is not None and got.density < 0.4
    assert not got.dense_enough
    # ...and it still reaches the exit. The two failures are independent.
    assert got.reach is Reach.REACHES_EXIT


def test_a_dense_series_that_stops_years_early_is_caught() -> None:
    """The failure that matters most: dense, clean, and missing the death."""
    days = _sessions(dt.date(1998, 1, 2), dt.date(2001, 6, 29))
    got = assess_series(days, exit_date=dt.date(2005, 3, 1))
    assert got is not None
    assert got.dense_enough
    assert got.reach is Reach.STOPS_YEARS_EARLY
    assert not got.records_the_death


def test_stopping_a_few_months_early_is_its_own_bucket() -> None:
    days = _sessions(dt.date(2003, 1, 2), dt.date(2004, 6, 30))
    got = assess_series(days, exit_date=dt.date(2005, 3, 1))
    assert got is not None
    assert got.reach is Reach.STOPS_MONTHS_EARLY


def test_a_filing_lag_is_not_reported_as_a_data_gap() -> None:
    """An exit is dated at a filing, and the last trade routinely precedes the
    paperwork by weeks. A tighter bound would report that lag as a hole."""
    days = _sessions(dt.date(2003, 1, 2), dt.date(2004, 11, 1))
    got = assess_series(days, exit_date=dt.date(2004, 12, 20))
    assert got is not None
    assert got.reach is Reach.REACHES_EXIT
    assert got.records_the_death


def test_a_series_outliving_its_exit_is_a_separate_answer() -> None:
    """Not a gap. Usually a filing dating the exit before trading stopped, and
    worth seeing on its own rather than folded into "reaches"."""
    days = _sessions(dt.date(2003, 1, 2), dt.date(2006, 6, 30))
    got = assess_series(days, exit_date=dt.date(2004, 1, 5))
    assert got is not None
    assert got.reach is Reach.OUTLIVES_EXIT
    assert got.records_the_death


def test_a_single_bar_claims_no_density() -> None:
    """Reporting 1.0 from one observation would claim completeness from
    nothing."""
    got = assess_series([dt.date(2004, 3, 1)], exit_date=dt.date(2004, 3, 2))
    assert got is not None
    assert got.sessions_held == 1
    assert got.density is None
    assert not got.dense_enough


def test_duplicate_session_dates_do_not_inflate_density() -> None:
    """The corpus stores a raw and a vendor-adjusted fact per session, so
    counting rows would report every series as twice as dense as it is."""
    days = _sessions(dt.date(2004, 1, 2), dt.date(2004, 6, 30))
    doubled = [*days, *days]
    plain = assess_series(days, exit_date=None)
    twice = assess_series(doubled, exit_date=None)
    assert plain is not None and twice is not None
    assert twice.sessions_held == plain.sessions_held
    assert twice.density == plain.density == 1.0


def test_reach_is_none_without_an_exit_date() -> None:
    """A living registrant has nothing to reach, and inventing a verdict would
    put a live name in a survivorship count."""
    got = assess_series(_sessions(dt.date(2020, 1, 2), dt.date(2020, 6, 30)), exit_date=None)
    assert got is not None
    assert got.reach is None
    assert not got.records_the_death


def test_summarise_reports_both_failures_without_merging_them() -> None:
    holed = assess_series(
        _sessions(dt.date(2001, 1, 2), dt.date(2004, 12, 31), keep=4),
        exit_date=dt.date(2005, 1, 10),
    )
    early = assess_series(
        _sessions(dt.date(1998, 1, 2), dt.date(2001, 6, 29)), exit_date=dt.date(2005, 3, 1)
    )
    good = assess_series(
        _sessions(dt.date(2001, 1, 2), dt.date(2004, 12, 31)), exit_date=dt.date(2005, 1, 10)
    )
    assert holed and early and good
    got = summarise([holed, early, good])
    assert got["series"] == 3
    assert got["records_the_death"] == 2
    assert got["below_dense_enough"] == 1
    assert got["by_reach"] == {"reaches_exit": 2, "stops_years_early": 1}
