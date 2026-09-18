"""FactorTilt: the candidate source for the registered low-volatility backtest.

Each test names a way a factor strategy fails quietly -- nominating off its
schedule, taking the wrong end of the ranking, a "random" control that is not
reproducible, or a split read as a crash.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pytest

from tradeit.backtesting.factor import FactorTilt, Selection
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar

START = dt.date(2020, 1, 2)


def _day(n: int) -> dt.date:
    return START + dt.timedelta(days=n)


def _bar(
    iid: int, day: dt.date, close: float, volume: float = 1e6, spread: float = 0.005
) -> OhlcvBar:
    moment = dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC)
    c = Decimal(str(round(close, 6)))
    return OhlcvBar(
        instrument_id=iid,
        timeframe=Bartimeframe.D1,
        session_date=day,
        open=c,
        high=c * Decimal(str(1 + spread)),
        low=c * Decimal(str(1 - spread)),
        close=c,
        volume=Decimal(str(volume)),
        event_time=moment,
        knowledge_time=moment,
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
    )


def _tilt(
    selection: Selection,
    rebalance: set[dt.date],
    splits: Mapping[dt.date, Mapping[int, Decimal]] | None = None,
    **overrides: object,
) -> FactorTilt:
    table = splits or {}
    params: dict[str, object] = {
        "selection": selection,
        "rebalance_dates": frozenset(rebalance),
        "splits_on": lambda day: table.get(day, {}),
        "volatility_lookback": 60,
        "fraction": 0.2,
        "min_history": 100,
        "min_dollar_volume": Decimal(1_000_000),
        "dollar_volume_lookback": 20,
        "max_atr_percent": 1.0,
        "stop_pct": Decimal("0.08"),
        "seed": 20260918,
    }
    params.update(overrides)
    return FactorTilt(**params)  # type: ignore[arg-type]


def _panel(names: int, sessions: int, seed: int = 0) -> dict[int, list[float]]:
    """Security i has daily volatility proportional to i+1: 0 is the calmest."""
    rng = np.random.default_rng(seed)
    return {
        i: list(100.0 * np.exp(np.cumsum(rng.normal(0, 0.002 * (i + 1), sessions))))
        for i in range(names)
    }


def _run(tilt: FactorTilt, panel: dict[int, list[float]], sessions: int) -> dict:
    out = {}
    for n in range(sessions):
        day = _day(n)
        out[day] = tilt(day, {i: _bar(i, day, closes[n]) for i, closes in panel.items()})
    return out


class TestSchedule:
    def test_nominates_only_on_rebalance_dates(self) -> None:
        panel = _panel(20, 130)
        rebalance = {_day(110), _day(125)}
        results = _run(_tilt(Selection.CALM, rebalance), panel, 130)
        assert {d for d, c in results.items() if c} == rebalance

    def test_nothing_before_the_history_it_needs(self) -> None:
        panel = _panel(20, 130)
        results = _run(_tilt(Selection.CALM, {_day(50)}), panel, 130)
        assert results[_day(50)] == []


class TestCalm:
    def test_the_calmest_fifth_best_first(self) -> None:
        panel = _panel(20, 130)
        results = _run(_tilt(Selection.CALM, {_day(120)}), panel, 130)
        chosen = [c.instrument_id for c in results[_day(120)]]
        assert chosen == [0, 1, 2, 3]
        scores = [c.score.total for c in results[_day(120)]]
        assert scores == sorted(scores, reverse=True)

    def test_stop_is_a_fixed_percentage_for_equal_dollar_sizing(self) -> None:
        panel = _panel(20, 130)
        candidate = _run(_tilt(Selection.CALM, {_day(120)}), panel, 130)[_day(120)][0]
        assert candidate.stop_price == candidate.entry_price * Decimal("0.92")


class TestRandomControl:
    def test_same_count_different_choice_same_every_time(self) -> None:
        panel = _panel(40, 130)
        calm = _run(_tilt(Selection.CALM, {_day(120)}), panel, 130)[_day(120)]
        first = _run(_tilt(Selection.RANDOM, {_day(120)}), panel, 130)[_day(120)]
        second = _run(_tilt(Selection.RANDOM, {_day(120)}), panel, 130)[_day(120)]
        assert len(first) == len(calm) == 8
        assert [c.instrument_id for c in first] == [c.instrument_id for c in second]
        assert {c.instrument_id for c in first} != {c.instrument_id for c in calm}

    def test_the_random_draw_does_not_know_volatility(self) -> None:
        """Over many dates the random arm's picks should average mid-ranking."""
        panel = _panel(40, 400, seed=3)
        rebalance = {_day(n) for n in range(120, 400, 7)}
        tilt = _tilt(Selection.RANDOM, rebalance)
        _run(tilt, panel, 400)
        picks = [i for chosen in tilt.nominated.values() for i in chosen]
        assert 12 < float(np.mean(picks)) < 27


class TestEligibility:
    def test_below_the_dollar_floor_is_not_nominated(self) -> None:
        panel = _panel(20, 130)
        tilt = _tilt(Selection.CALM, {_day(120)})
        for n in range(130):
            day = _day(n)
            bars = {
                i: _bar(i, day, closes[n], volume=1.0 if i == 0 else 1e6)
                for i, closes in panel.items()
            }
            last = tilt(day, bars)
        assert 0 not in [c.instrument_id for c in last]

    def test_a_bad_print_is_not_nominated(self) -> None:
        """DATA_DICTIONARY §0.8: ATR above the whole price is a bad print."""
        panel = _panel(20, 130)
        tilt = _tilt(Selection.CALM, {_day(120)})
        for n in range(130):
            day = _day(n)
            bars = {
                i: _bar(i, day, closes[n], spread=0.9 if i == 0 and n > 110 else 0.005)
                for i, closes in panel.items()
            }
            last = tilt(day, bars)
        assert 0 not in [c.instrument_id for c in last]


class TestSplits:
    def test_a_split_is_not_read_as_a_crash(self) -> None:
        """A steady security splits 2-for-1 mid-window. Raw bars halve overnight.

        Restated, it stays the calmest name. Unrestated, the halving is a 69%
        log move inside its 60-session window and it becomes the most volatile.
        """
        panel = _panel(20, 130)
        ex = _day(100)
        raw = {i: list(v) for i, v in panel.items()}
        raw[0] = [p / 2 if n >= 100 else p for n, p in enumerate(raw[0])]
        # Volume doubles with the share count, so dollar turnover is unchanged.
        splits = {ex: {0: Decimal(2)}}
        tilt = _tilt(Selection.CALM, {_day(120)}, splits)
        blind = _tilt(Selection.CALM, {_day(120)})
        for n in range(130):
            day = _day(n)
            bars = {
                i: _bar(i, day, closes[n], volume=2e6 if i == 0 and n >= 100 else 1e6)
                for i, closes in raw.items()
            }
            calls = (tilt(day, bars), blind(day, bars))
            if day == _day(120):
                restated, unrestated = calls
        assert restated[0].instrument_id == 0
        assert 0 not in [c.instrument_id for c in unrestated]


class TestArguments:
    @pytest.mark.parametrize(
        "overrides",
        [{"fraction": 0.0}, {"stop_pct": Decimal(1)}, {"min_history": 30}],
    )
    def test_bad_parameters_are_refused(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            _tilt(Selection.CALM, set(), **overrides)
