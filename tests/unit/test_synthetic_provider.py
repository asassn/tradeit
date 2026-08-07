"""Contract tests for the synthetic provider.

These double as the conformance suite any future vendor adapter must pass:
swap the fixture and the assertions should still hold.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.data.provider import FundamentalProvider, PriceProvider, ReferenceDataProvider
from tradeit.data.providers.synthetic import SyntheticProvider

START = dt.date(2022, 1, 3)
END = dt.date(2023, 12, 29)


@pytest.fixture
def provider() -> SyntheticProvider:
    return SyntheticProvider(seed=42)


def test_satisfies_all_three_protocols(provider):
    assert isinstance(provider, ReferenceDataProvider)
    assert isinstance(provider, PriceProvider)
    assert isinstance(provider, FundamentalProvider)


def test_bars_are_deterministic_for_a_seed():
    a = list(SyntheticProvider(seed=7).fetch_bars(1, START, END))
    b = list(SyntheticProvider(seed=7).fetch_bars(1, START, END))
    assert [x.close for x in a] == [x.close for x in b]


def test_different_seeds_produce_different_series():
    a = list(SyntheticProvider(seed=7).fetch_bars(1, START, END))
    b = list(SyntheticProvider(seed=8).fetch_bars(1, START, END))
    assert [x.close for x in a] != [x.close for x in b]


def test_bars_land_only_on_trading_sessions(provider):
    bars = list(provider.fetch_bars(1, START, END))
    assert bars
    assert all(b.session_date.weekday() < 5 for b in bars)
    assert dt.date(2022, 12, 26) not in {b.session_date for b in bars}  # Christmas observed


def test_bars_are_knowable_only_after_the_close(provider):
    for bar in provider.fetch_bars(1, START, END):
        assert bar.knowledge_time > bar.event_time


def test_bars_are_chronological_and_unique(provider):
    dates = [b.session_date for b in provider.fetch_bars(1, START, END)]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


def test_ohlc_invariants_hold_across_the_whole_series(provider):
    """Construction validates each bar, so reaching the end is the assertion."""
    bars = list(provider.fetch_bars(1, START, END))
    assert len(bars) > 400
    assert all(b.low <= b.open <= b.high and b.low <= b.close <= b.high for b in bars)


def test_intraday_timeframes_are_refused_rather_than_faked(provider):
    with pytest.raises(NotImplementedError):
        list(provider.fetch_bars(1, START, END, timeframe=Bartimeframe.M5))


def test_a_breakout_is_planted_in_the_series(provider):
    """Later phases need a series with a known answer to test detectors against."""
    bars = list(provider.fetch_bars(1, START, END))
    closes = [float(b.close) for b in bars]
    biggest_move = max((closes[i] / closes[i - 1] - 1, i) for i in range(1, len(closes)))
    assert biggest_move[0] > 0.05, "expected a planted expansion day of at least 5%"


def test_fundamentals_carry_a_realistic_filing_lag(provider):
    facts = list(provider.fetch_fundamentals(1, ["revenue"], START, END))
    assert facts
    for fact in facts:
        lag = fact.knowledge_time.date() - fact.period_end
        assert dt.timedelta(days=20) <= lag <= dt.timedelta(days=90)


def test_earnings_dates_are_announced_before_they_occur(provider):
    events = list(provider.fetch_earnings(1, START, END))
    assert events
    assert all(e.knowledge_time.date() < e.scheduled_date for e in events)


def test_corporate_actions_are_announced_before_the_ex_date(provider):
    found = False
    for instrument_id in range(1, 9):
        for action in provider.fetch_corporate_actions(instrument_id, START, END):
            found = True
            assert action.knowledge_time.date() < action.ex_date
    assert found, "expected at least one instrument to have a split"


def test_universe_is_stable_and_uniquely_tickered(provider):
    instruments = list(provider.list_instruments(END))
    assert len({i.instrument_id for i, _ in instruments}) == len(instruments)
    assert len({m.ticker for _, m in instruments}) == len(instruments)
