from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.clock import AsOfClock, utcnow
from tradeit.core.enums import TradingMode
from tradeit.errors import ClockError

UTC = dt.UTC


def test_naive_datetime_is_rejected():
    with pytest.raises(ClockError, match="naive"):
        AsOfClock.at(dt.datetime(2024, 3, 1, 12, 0))


def test_non_utc_input_is_normalised():
    eastern = dt.timezone(dt.timedelta(hours=-5))
    clock = AsOfClock.at(dt.datetime(2024, 3, 1, 12, 0, tzinfo=eastern))
    assert clock.as_of == dt.datetime(2024, 3, 1, 17, 0, tzinfo=UTC)


def test_clock_cannot_rewind():
    clock = AsOfClock.at(dt.datetime(2024, 3, 10, tzinfo=UTC))
    with pytest.raises(ClockError, match="cannot rewind"):
        clock.advance_to(dt.datetime(2024, 3, 9, tzinfo=UTC))


def test_advance_returns_new_clock_leaving_original_intact():
    clock = AsOfClock.at(dt.datetime(2024, 3, 10, tzinfo=UTC))
    later = clock.advance_to(dt.datetime(2024, 3, 11, tzinfo=UTC))
    assert clock.as_of.day == 10
    assert later.as_of.day == 11
    assert later.mode is clock.mode


@pytest.mark.parametrize(
    ("knowledge_offset_hours", "expected"),
    [(-1, True), (0, True), (1, False)],
)
def test_knows_is_inclusive_of_the_instant_itself(knowledge_offset_hours, expected):
    as_of = dt.datetime(2024, 3, 10, 12, 0, tzinfo=UTC)
    clock = AsOfClock.at(as_of)
    fact_time = as_of + dt.timedelta(hours=knowledge_offset_hours)
    assert clock.knows(fact_time) is expected


def test_stale_live_clock_is_rejected():
    stale = AsOfClock.at(utcnow() - dt.timedelta(hours=2), mode=TradingMode.PAPER)
    with pytest.raises(ClockError, match="behind wall time"):
        stale.assert_fresh()


def test_backtest_clock_is_never_stale():
    old = AsOfClock.at(dt.datetime(2010, 1, 1, tzinfo=UTC), mode=TradingMode.BACKTEST)
    old.assert_fresh()  # must not raise


def test_live_helper_refuses_backtest_mode():
    with pytest.raises(ClockError):
        AsOfClock.live(mode=TradingMode.BACKTEST)
