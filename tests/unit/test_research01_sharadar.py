"""The Sharadar reconstruction must return the print, not the adjusted series.

Fixtures are THQ's rows around its 1-for-10 reverse split of 2012-07-09 as
Sharadar served them on 2026-09-14.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tradeit.research01.sharadar_client import parse_sep_bars, parse_splits

THQ = [
    {"date": "2012-07-06", "open": 5.6, "high": 5.6, "low": 5.2, "close": 5.2,
     "volume": 46670, "closeadj": 5.2, "closeunadj": 0.52},
    {"date": "2012-07-09", "open": 5.09, "high": 6.08, "low": 4.76, "close": 5.79,
     "volume": 197000, "closeadj": 5.79, "closeunadj": 5.79},
]  # fmt: skip


def _raw(bars, day):
    return next(b for b in bars if b.session_date == day and b.adjustment_basis == "raw")


def test_the_raw_bar_before_a_reverse_split_is_the_printed_price() -> None:
    bars = parse_sep_bars("THQI", THQ)
    before = _raw(bars, dt.date(2012, 7, 6))
    assert before.close == Decimal("0.52")
    assert before.open == Decimal("0.560000")
    assert before.low == Decimal("0.520000")
    assert before.volume == Decimal("466700")


def test_the_raw_series_steps_on_the_ex_date_and_the_adjusted_one_does_not() -> None:
    bars = parse_sep_bars("THQI", THQ)
    raw_jump = _raw(bars, dt.date(2012, 7, 9)).close / _raw(bars, dt.date(2012, 7, 6)).close
    assert raw_jump > 10
    totals = [b for b in bars if b.adjustment_basis == "total"]
    assert totals[1].close / totals[0].close < Decimal("1.2")


def test_bars_carry_the_label_the_caller_chose_not_the_vendor_symbol() -> None:
    assert {b.ticker for b in parse_sep_bars("THQI", THQ)} == {"THQI"}


def test_a_row_without_an_unadjusted_close_is_dropped_not_repaired() -> None:
    row = dict(THQ[0], closeunadj=None)
    assert parse_sep_bars("X", [row]) == []


def test_a_non_positive_close_is_dropped() -> None:
    assert parse_sep_bars("X", [dict(THQ[0], close=0)]) == []


def test_raw_bars_stay_coherent_after_reconstruction() -> None:
    for bar in parse_sep_bars("THQI", THQ):
        assert bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high


def test_splits_keep_new_over_old_and_drop_nothing_splits() -> None:
    rows = [
        {"date": "2012-07-09", "action": "split", "value": 0.1},
        {"date": "2005-09-07", "action": "split", "value": 1.5},
        {"date": "2001-01-01", "action": "split", "value": 1},
        {"date": "2001-01-02", "action": "split", "value": 0},
        {"date": "2012-12-21", "action": "delisted", "value": None},
    ]
    splits = parse_splits("THQI", rows)
    assert [(s.ex_date, s.ratio) for s in splits] == [
        (dt.date(2012, 7, 9), Decimal("0.1")),
        (dt.date(2005, 9, 7), Decimal("1.5")),
    ]
