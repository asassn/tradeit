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
        "min_price": Decimal(5),
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


class TestMinimumPrice:
    def test_a_sub_penny_placeholder_is_not_nominated(self) -> None:
        """The case that sank two registered backtests: a security collapsing to
        $0.0001 keeps its last normal weeks of turnover in the 20-session window
        and, flat, reads as the calmest name there is."""
        panel = _panel(20, 130)
        tilt = _tilt(Selection.CALM, {_day(120)})
        for n in range(130):
            day = _day(n)
            bars = {
                i: _bar(i, day, 0.0001 if i == 0 and n > 110 else closes[n])
                for i, closes in panel.items()
            }
            last = tilt(day, bars)
        assert 0 not in [c.instrument_id for c in last]

    def test_a_name_just_above_the_floor_is_eligible(self) -> None:
        panel = {i: [5.5 * c / 100 for c in closes] for i, closes in _panel(20, 130).items()}
        chosen = _run(_tilt(Selection.CALM, {_day(120)}), panel, 130)[_day(120)]
        assert chosen


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
        [
            {"fraction": 0.0},
            {"stop_pct": Decimal(1)},
            {"min_history": 30},
            {"min_price": Decimal(0)},
        ],
    )
    def test_bad_parameters_are_refused(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            _tilt(Selection.CALM, set(), **overrides)


class TestSurpriseAndCombined:
    """The arms registered in ``COMBINATION_PORTFOLIO_2026-09-22``.

    The combination's whole point is that it is neither of its components, so
    the tests are the three ways it collapses into one of them or into noise.
    """

    @staticmethod
    def _external(names: int, day: dt.date, best_first: list[int]) -> dict:
        """Highest score to ``best_first[0]``, descending."""
        return {(i, day): float(names - rank) for rank, i in enumerate(best_first)}

    def test_surprise_nominates_the_highest_external_score(self) -> None:
        sessions, day = 140, _day(139)
        panel = _panel(10, sessions)
        # Security 9 is the MOST volatile, so a calm arm would never take it.
        tilt = _tilt(
            Selection.SURPRISE,
            {day},
            fraction=0.2,
            external=self._external(10, day, [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]),
        )
        chosen = [c.instrument_id for c in _run(tilt, panel, sessions)[day]]
        assert chosen == [9, 8]

    def test_a_security_without_a_score_is_not_nominable(self) -> None:
        """Fundamentals are absent for most securities on most dates."""
        sessions, day = 140, _day(139)
        panel = _panel(10, sessions)
        tilt = _tilt(
            Selection.SURPRISE, {day}, fraction=0.5, external=self._external(10, day, [7, 3])
        )
        chosen = [c.instrument_id for c in _run(tilt, panel, sessions)[day]]
        assert chosen == [7]  # half of the two that carry a score

    def test_the_combination_is_neither_component(self) -> None:
        """The calmest has the worst surprise and vice versa: the mean picks the middle."""
        sessions, day = 140, _day(139)
        panel = _panel(10, sessions)
        # _panel makes 0 calmest and 9 most volatile; score them the opposite way.
        external = self._external(10, day, [9, 8, 7, 6, 5, 4, 3, 2, 1, 0])
        combined = _tilt(Selection.COMBINED, {day}, fraction=0.3, external=external)
        calm = _tilt(Selection.CALM, {day}, fraction=0.3)
        surprise = _tilt(Selection.SURPRISE, {day}, fraction=0.3, external=external)
        picked = {
            name: [c.instrument_id for c in _run(t, panel, sessions)[day]]
            for name, t in (("combined", combined), ("calm", calm), ("surprise", surprise))
        }
        assert picked["calm"] == [0, 1, 2]
        assert picked["surprise"] == [9, 8, 7]
        # Ranks sum to a constant here, so ties break by id -- the point is only
        # that it is not either end of the two orderings.
        assert picked["combined"] != picked["calm"]
        assert picked["combined"] != picked["surprise"]

    def test_the_combination_follows_both_ranks_when_they_agree(self) -> None:
        """Calm AND high-surprise: security 0 must come first on both readings."""
        sessions, day = 140, _day(139)
        panel = _panel(10, sessions)
        external = self._external(10, day, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        tilt = _tilt(Selection.COMBINED, {day}, fraction=0.2, external=external)
        chosen = [c.instrument_id for c in _run(tilt, panel, sessions)[day]]
        assert chosen == [0, 1]

    def test_the_control_is_untouched_by_a_missing_score(self) -> None:
        """RANDOM must not shrink when fundamentals are sparse, or it is not a control."""
        sessions, day = 140, _day(139)
        panel = _panel(10, sessions)
        with_scores = _tilt(
            Selection.RANDOM, {day}, fraction=0.5, external=self._external(10, day, [1])
        )
        without = _tilt(Selection.RANDOM, {day}, fraction=0.5)
        a = [c.instrument_id for c in _run(with_scores, panel, sessions)[day]]
        b = [c.instrument_id for c in _run(without, panel, sessions)[day]]
        assert a == b and len(a) == 5
