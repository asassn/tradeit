"""The moving-average baseline restates its history at splits.

Until 2026-09-18 it kept raw closes and never restated them, so a 2-for-1 read
as a 50% fall inside every window spanning the ex-date -- distorting both its
crossover and the ATR stop that sizes §28's equal-risk arm.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.backtesting.baselines import MovingAverageCross
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar

START = dt.date(2020, 1, 1)


def _bar(day: dt.date, close: float) -> OhlcvBar:
    c = Decimal(str(close))
    moment = dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC)
    return OhlcvBar(
        instrument_id=7,
        timeframe=Bartimeframe.D1,
        session_date=day,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(1000),
        event_time=moment,
        knowledge_time=moment,
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _signals(rule: MovingAverageCross, raw: list[float]) -> list[dt.date]:
    fired = []
    for n, close in enumerate(raw):
        day = START + dt.timedelta(days=n)
        if rule(day, {7: _bar(day, close)}):
            fired.append(day)
    return fired


# 25 sessions at 100, a 2-for-1 on session 25, then a genuine rally from 50.
EX = START + dt.timedelta(days=25)
RAW = [100.0] * 25 + [50.0 + 0.5 * n for n in range(15)]


def test_a_rally_just_after_a_split_is_seen() -> None:
    """Restated, the history is flat at 50 and the rally crosses the fast mean
    above the slow one. Unrestated, the slow mean is still averaging the 100s
    and the rally is invisible."""
    rule = MovingAverageCross(fast=5, slow=20, stop_pct=Decimal("0.08"))
    rule.splits_on = lambda day: {7: Decimal(2)} if day == EX else {}
    fired = _signals(rule, RAW)
    assert fired, "the post-split rally produced no crossover"
    assert all(day > EX for day in fired)


def test_history_is_restated_in_place() -> None:
    rule = MovingAverageCross(fast=5, slow=20, stop_pct=Decimal("0.08"))
    rule.splits_on = lambda day: {7: Decimal(2)} if day == EX else {}
    _signals(rule, RAW[:26])
    assert set(rule.closes[7]) == {Decimal(50)}
    # Shares double with the split, so dollar turnover is unchanged.
    assert set(rule._volumes[7]) == {Decimal(2000), Decimal(1000)}


def test_running_without_a_split_source_is_refused() -> None:
    """Fail closed: a run that silently skipped restatement would reproduce the
    defect with nothing to say so."""
    rule = MovingAverageCross(fast=5, slow=20, stop_pct=Decimal("0.08"))
    with pytest.raises(ValueError, match="splits_on"):
        rule(START, {7: _bar(START, 100.0)})
