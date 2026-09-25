"""Every candlestick shape, built by hand from its definition.

``PATTERN_PROGRAM.md`` items 21-40. A shape detector is trivially easy to get
*nearly* right and the near-misses are the dangerous ones: a hammer test that
passes on any bar with a lower wick, an engulfing test that ignores which bar
is bigger, a star test that never checks the gap. So each shape here is built
from its published definition, and each is paired with a **negative** case that
differs in exactly the one property the definition turns on.

The shapes are measured on 2010-2019 as entry filters on already-scanned
events, so a false positive here does not merely add noise -- it silently
reassigns trades between two of the program's numbered setups.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradeit.patterns.candlesticks import (
    CONTEXTUAL,
    SHAPES,
    Bar,
    prior_trend,
    shapes_at,
)


def series(*bars: tuple[float, float, float, float]) -> tuple[np.ndarray, ...]:
    """OHLC arrays from (open, high, low, close) tuples."""
    array = np.array(bars, dtype=float)
    return array[:, 0], array[:, 1], array[:, 2], array[:, 3]


def flags(*bars: tuple[float, float, float, float]) -> dict[str, bool]:
    o, h, low, c = series(*bars)
    return shapes_at(o, h, low, c, len(bars) - 1)


#: Five quiet bars, so a shape under test has a context and a warm-up.
FLAT = [(100.0, 100.5, 99.5, 100.0)] * 5
RISE = [
    (96.0, 96.5, 95.5, 96.4),
    (97.0, 97.5, 96.5, 97.4),
    (98.0, 98.5, 97.5, 98.4),
    (99.0, 99.5, 98.5, 99.4),
    (100.0, 100.5, 99.5, 100.4),
]
FALL = [
    (104.0, 104.5, 103.5, 103.6),
    (103.0, 103.5, 102.5, 102.6),
    (102.0, 102.5, 101.5, 101.6),
    (101.0, 101.5, 100.5, 100.6),
    (100.0, 100.5, 99.5, 99.6),
]


def test_every_named_shape_is_reported() -> None:
    """A shape missing from the output is a setup silently dropped."""
    found = flags(*FLAT, (100.0, 101.0, 99.0, 100.5))
    assert set(found) == {*SHAPES, *CONTEXTUAL}


def test_hammer_needs_the_long_lower_shadow() -> None:
    """A real body, small but not a doji, hanging from a long lower shadow.

    The body must clear ``DOJI_BODY``: a bar with *no* body and a long lower
    shadow is a dragonfly doji, which is item 27 rather than item 21.
    """
    assert flags(*FALL, (100.0, 100.3, 96.0, 99.0))["hammer"]
    # Same body, same range, shadow on the wrong side.
    assert not flags(*FALL, (100.0, 104.0, 99.7, 101.0))["hammer"]
    # Long lower shadow but a body far too big to hang from it.
    assert not flags(*FALL, (100.0, 100.2, 96.0, 96.5))["hammer"]


def test_inverted_hammer_needs_the_long_upper_shadow() -> None:
    assert flags(*FALL, (100.0, 104.0, 99.7, 101.0))["inverted_hammer"]
    assert not flags(*FALL, (100.0, 100.3, 96.0, 99.0))["inverted_hammer"]


def test_hanging_man_is_a_hammer_after_a_rise_and_nothing_else() -> None:
    """The distinction the module refuses to collapse.

    Identical bar, opposite context. If these two ever agree, the atlas has
    lost the ability to ask whether the wick or the trend did the work.
    """
    bar = (100.0, 100.3, 96.0, 99.0)
    after_fall, after_rise = flags(*FALL, bar), flags(*RISE, bar)
    assert after_fall["hammer"] and not after_fall["hanging_man"]
    assert after_rise["hammer"] and after_rise["hanging_man"]


def test_shooting_star_is_an_inverted_hammer_after_a_rise() -> None:
    bar = (100.0, 104.0, 99.7, 101.0)
    assert not flags(*FALL, bar)["shooting_star"]
    assert flags(*RISE, bar)["shooting_star"]


def test_doji_family_separates_by_where_the_shadow_sits() -> None:
    assert flags(*FLAT, (100.0, 102.0, 98.0, 100.05))["doji"]
    dragonfly = flags(*FLAT, (100.0, 100.1, 96.0, 100.02))
    assert dragonfly["doji"] and dragonfly["dragonfly_doji"]
    assert not dragonfly["gravestone_doji"]
    gravestone = flags(*FLAT, (100.0, 104.0, 99.9, 100.02))
    assert gravestone["doji"] and gravestone["gravestone_doji"]
    assert not gravestone["dragonfly_doji"]


def test_a_doji_is_never_also_a_hammer() -> None:
    """Otherwise one bar is counted under two numbered setups at once."""
    found = flags(*FLAT, (100.0, 100.1, 96.0, 100.02))
    assert found["dragonfly_doji"]
    assert not found["hammer"]


def test_spinning_top_sits_between_doji_and_a_real_body() -> None:
    assert flags(*FLAT, (100.0, 102.0, 98.0, 100.6))["spinning_top"]
    # Body too small: that is a doji.
    assert not flags(*FLAT, (100.0, 102.0, 98.0, 100.05))["spinning_top"]
    # Body too big.
    assert not flags(*FLAT, (100.0, 102.0, 98.0, 101.8))["spinning_top"]


def test_engulfing_needs_the_second_body_to_be_larger() -> None:
    assert flags(*FLAT, (100.0, 100.2, 98.8, 99.0), (98.8, 100.6, 98.6, 100.4))["bullish_engulfing"]
    # Second body contained by the first: an inside bar, not an engulfing.
    assert not flags(*FLAT, (100.0, 100.2, 98.8, 99.0), (99.6, 99.8, 99.1, 99.2))[
        "bullish_engulfing"
    ]
    assert flags(*FLAT, (99.0, 100.2, 98.8, 100.0), (100.2, 100.4, 98.6, 98.8))["bearish_engulfing"]


def test_an_exactly_matching_body_is_not_engulfing() -> None:
    """The only case the body-size clause actually decides.

    Engulfing already implies a bigger body: if the second bar spans the
    first's open *and* its close, it cannot be smaller. The one case left over
    is the exact tie -- a second bar whose body matches the first's precisely,
    which the chart convention calls a matching bar and not an engulfing. A
    mutation dropping the size comparison survived every other test here.
    """
    matched = flags(*FLAT, (100.0, 100.5, 98.5, 99.0), (99.0, 100.5, 98.5, 100.0))
    assert not matched["bullish_engulfing"]
    # One cent wider, and it engulfs.
    assert flags(*FLAT, (100.0, 100.5, 98.5, 99.0), (98.99, 100.5, 98.5, 100.0))[
        "bullish_engulfing"
    ]


def test_engulfing_requires_opposite_colours() -> None:
    """Two rising bars are a trend, not a reversal."""
    assert not flags(*FLAT, (99.0, 100.0, 98.8, 99.8), (98.8, 101.0, 98.6, 100.8))[
        "bullish_engulfing"
    ]


def test_tweezers_need_matching_extremes_and_opposite_colours() -> None:
    assert flags(*FLAT, (99.0, 100.0, 98.8, 99.9), (99.9, 100.02, 99.0, 99.1))["tweezer_top"]
    # Highs disagree by far more than the tolerance.
    assert not flags(*FLAT, (99.0, 100.0, 98.8, 99.9), (99.9, 103.0, 99.0, 99.1))["tweezer_top"]
    assert flags(*FLAT, (100.0, 100.2, 99.0, 99.1), (99.1, 100.1, 99.02, 100.0))["tweezer_bottom"]


def test_morning_and_evening_stars_need_the_small_middle_bar() -> None:
    morning = flags(
        *FALL, (100.0, 100.2, 97.0, 97.2), (97.0, 97.2, 96.6, 96.8), (97.0, 99.4, 96.9, 99.2)
    )
    assert morning["morning_star"] and not morning["morning_doji_star"]
    # Middle bar as large as the first: no pause, so no star.
    assert not flags(
        *FALL, (100.0, 100.2, 97.0, 97.2), (97.2, 97.4, 94.0, 94.2), (94.2, 99.4, 94.0, 99.2)
    )["morning_star"]
    evening = flags(
        *RISE,
        (100.0, 103.2, 99.8, 103.0),
        (103.2, 103.6, 103.0, 103.4),
        (103.2, 103.4, 100.4, 100.6),
    )
    assert evening["evening_star"]


def test_a_doji_middle_bar_reports_the_doji_variant_only() -> None:
    """Items 33/34 and 35/36 are different rows in the program's table."""
    found = flags(
        *FALL, (100.0, 100.2, 97.0, 97.2), (97.0, 97.1, 96.9, 97.02), (97.0, 99.4, 96.9, 99.2)
    )
    assert found["morning_doji_star"]
    assert not found["morning_star"]


def test_three_soldiers_and_crows_need_progress_and_overlap() -> None:
    assert flags(
        *FLAT,
        (100.0, 101.2, 99.9, 101.0),
        (100.5, 102.2, 100.4, 102.0),
        (101.5, 103.2, 101.4, 103.0),
    )["three_white_soldiers"]
    # Third bar opens below the second's body: the advance broke.
    assert not flags(
        *FLAT, (100.0, 101.2, 99.9, 101.0), (100.5, 102.2, 100.4, 102.0), (99.0, 103.2, 98.9, 103.0)
    )["three_white_soldiers"]
    assert flags(
        *FLAT,
        (103.0, 103.1, 101.8, 102.0),
        (102.5, 102.6, 100.8, 101.0),
        (101.5, 101.6, 99.8, 100.0),
    )["three_black_crows"]


def test_rising_and_falling_three_need_containment() -> None:
    long_up = (100.0, 106.0, 99.8, 105.8)
    assert flags(
        *FLAT,
        long_up,
        (105.0, 105.2, 104.0, 104.2),
        (104.2, 104.4, 103.2, 103.4),
        (103.4, 103.6, 102.6, 102.8),
        (103.0, 107.0, 102.9, 106.5),
    )["rising_three"]
    # One middle bar escapes the first bar's range: the consolidation broke.
    assert not flags(
        *FLAT,
        long_up,
        (105.0, 105.2, 104.0, 104.2),
        (104.2, 108.0, 103.2, 103.4),
        (103.4, 103.6, 102.6, 102.8),
        (103.0, 107.0, 102.9, 106.5),
    )["rising_three"]


def test_prior_trend_reads_the_bars_before_not_the_bar_itself() -> None:
    """A context that included its own bar would be circular."""
    *_, closes = series(*RISE, (100.0, 100.2, 90.0, 90.5))
    assert prior_trend(closes, 5) == "rising"
    *_, closes = series(*FALL, (100.0, 110.0, 99.8, 109.5))
    assert prior_trend(closes, 5) == "falling"


def test_prior_trend_is_unknown_without_enough_history() -> None:
    *_, closes = series((100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 99.0, 100.0))
    assert prior_trend(closes, 1) == "unknown"


def test_a_zero_range_bar_is_a_full_body_not_a_doji() -> None:
    """§0.7 records placeholder bars. A NaN here would read as False quietly.

    A bar that did not move has no shadows to interpret, and calling it a doji
    would hand every placeholder print in the corpus a reversal signal.
    """
    assert Bar(100.0, 100.0, 100.0, 100.0).body_share() == 1.0
    found = flags(*FLAT, (100.0, 100.0, 100.0, 100.0))
    assert not found["doji"]
    assert not any(found[name] for name in ("dragonfly_doji", "gravestone_doji", "spinning_top"))


def test_no_shape_fires_without_enough_bars() -> None:
    o, h, low, c = series((100.0, 101.0, 99.0, 100.0))
    assert not any(shapes_at(o, h, low, c, 0)[name] for name in ("morning_star", "rising_three"))


def test_a_non_positive_price_reports_nothing() -> None:
    """The corpus records zero prints, and a shape built on one is fiction."""
    o, h, low, c = series(*FLAT, (0.0, 0.0, 0.0, 0.0))
    assert not any(shapes_at(o, h, low, c, 5).values())


@pytest.mark.parametrize("name", [*SHAPES, *CONTEXTUAL])
def test_every_shape_can_actually_fire_somewhere(name: str) -> None:
    """Guards against a definition so strict it is never true.

    A shape that cannot fire would sit in the atlas as an empty row and read
    as "measured, no edge" when it was never measured at all. This is the
    failure that made two earlier tests in this repository go vacuous.
    """
    rng = np.random.default_rng(20260924)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 40000)))
    highs = closes * (1 + np.abs(rng.normal(0, 0.012, 40000)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.012, 40000)))
    opens = np.concatenate(([100.0], closes[:-1])) * (1 + rng.normal(0, 0.004, 40000))
    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])
    assert any(shapes_at(opens, highs, lows, closes, i)[name] for i in range(6, 40000))
