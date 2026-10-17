"""The moving-average baseline restates its history at splits.

Until 2026-09-18 it kept raw closes and never restated them, so a 2-for-1 read
as a 50% fall inside every window spanning the ex-date -- distorting both its
crossover and the ATR stop that sizes §28's equal-risk arm.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.backtesting.baselines import (
    BuyAndHold,
    EqualWeightBenchmark,
    MovingAverageCross,
    NaiveMomentumClassifier,
)
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


class TestTheOtherThreeBaselines:
    """The roadmap names four baselines; this module shipped one until 2026-10-17.

    The module docstring has always promised *"buy and hold, the benchmark, a
    moving-average rule, a naive classifier"* and stated why they must be
    runnable strategies rather than quoted numbers. Only the moving-average
    rule existed, which means **§44 through §53 never compared themselves
    against doing nothing** — their control was a random security, which
    answers "did this beat a coin toss" and not "did this beat inaction".

    These tests pin the behaviour that distinguishes the three, because the
    easy mistakes make them collapse into each other: a buy-and-hold that
    re-buys is a benchmark, a benchmark that never re-offers is buy-and-hold,
    and a naive classifier reading net movement instead of each step is a
    momentum strategy with a lookback.
    """

    @staticmethod
    def _bars(day: dt.date, prices: dict[int, float]) -> dict[int, OhlcvBar]:
        out = {}
        for instrument_id, close in prices.items():
            bar = _bar(day, close)
            out[instrument_id] = bar.model_copy(update={"instrument_id": instrument_id})
        return out

    def test_buy_and_hold_buys_each_name_exactly_once(self) -> None:
        """The floor. If it re-bought, it would be the benchmark."""
        rule = BuyAndHold()
        first = rule(START, self._bars(START, {1: 100.0, 2: 50.0}))
        assert {c.instrument_id for c in first} == {1, 2}
        later = rule(START + dt.timedelta(days=1), self._bars(START, {1: 101.0, 2: 51.0}))
        assert later == () or len(later) == 0
        # A name that appears for the first time later is still bought once.
        third = rule(START + dt.timedelta(days=2), self._bars(START, {1: 102.0, 3: 20.0}))
        assert {c.instrument_id for c in third} == {3}

    def test_the_benchmark_re_offers_on_its_cadence(self) -> None:
        """What makes it a benchmark rather than buy-and-hold.

        Buy-and-hold lets winners grow into the portfolio and is therefore an
        undeclared momentum bet; re-offering lets the sizer pull weights back.
        """
        rule = EqualWeightBenchmark(cadence_sessions=3)
        offered = [
            len(rule(START + dt.timedelta(days=n), self._bars(START, {1: 100.0, 2: 50.0})))
            for n in range(7)
        ]
        assert offered == [2, 0, 0, 2, 0, 0, 2]

    def test_the_benchmark_refuses_a_cadence_below_one(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            EqualWeightBenchmark(cadence_sessions=0)

    def test_naive_momentum_needs_every_step_up_not_a_net_gain(self) -> None:
        """The mistake that turns the crudest rule into a momentum strategy.

        Two sessions of +5% then -4% is a net gain and is **not** "it went up
        yesterday". Reading the net move would make this a two-day lookback
        momentum rule, which is not the baseline the roadmap asks for.
        """
        rule = NaiveMomentumClassifier(lookback=2)
        prices = [100.0, 105.0, 100.8]  # net up over the window, last step down
        fired = []
        for n, close in enumerate(prices):
            day = START + dt.timedelta(days=n)
            fired.append(len(rule(day, self._bars(day, {1: close}))))
        assert fired == [0, 0, 0]
        rising = NaiveMomentumClassifier(lookback=2)
        fired = []
        for n, close in enumerate([100.0, 101.0, 102.0]):
            day = START + dt.timedelta(days=n)
            fired.append(len(rising(day, self._bars(day, {1: close}))))
        assert fired == [0, 0, 1]

    def test_naive_momentum_refuses_a_lookback_below_one(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            NaiveMomentumClassifier(lookback=0)

    def test_all_three_share_one_stop_rule(self) -> None:
        """A difference between baselines must be selection, not risk.

        If one of them managed risk more cleverly than the others, comparing a
        candidate against it would conflate two questions.
        """
        bars = self._bars(START, {1: 100.0})
        stop_pct = Decimal("0.10")
        held = BuyAndHold(stop_pct=stop_pct)(START, bars)
        bench = EqualWeightBenchmark(stop_pct=stop_pct)(START, bars)
        naive_rule = NaiveMomentumClassifier(stop_pct=stop_pct)
        naive_rule(START, bars)
        naive = naive_rule(START + dt.timedelta(days=1), self._bars(START, {1: 101.0}))
        assert held[0].stop_price == Decimal(100) * Decimal("0.90")
        assert bench[0].stop_price == held[0].stop_price
        assert naive[0].stop_price == Decimal("101") * Decimal("0.90")

    def test_a_stop_at_or_above_the_close_is_refused(self) -> None:
        """A stop that cannot size a position is dropped, not widened."""
        assert BuyAndHold(stop_pct=Decimal(1))(START, self._bars(START, {1: 100.0})) == []
