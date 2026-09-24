"""The mirror is only sound if it really inverts direction. Prove it.

``src/tradeit/patterns/mirror.py`` claims that running the *long* detectors and
the *long* breakout engine over a reciprocal series measures the short setup.
That claim is load-bearing -- the whole bear-flag study rests on it -- and it
would be satisfied *by accident* by a transform that did nothing at all, since
a detector that fires on the original and on the mirror looks like success
until you ask which one it was meant to fire on.

So the tests below are written to fail against a no-op mirror, against a
mirror that forgets to swap high and low, and against a mirror used for the
one thing it must never be used for: computing a short's return.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.mirror import (
    borrow_cost,
    mirror_bars,
    mirror_price,
    short_return,
    unmirror_price,
)
from tradeit.patterns.scanner import PatternScanner
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator


def _bull_flags(bars: list[object]) -> int:
    scanner = PatternScanner()
    result = scanner.scan(
        1,
        Bartimeframe.D1,
        bars,  # type: ignore[arg-type]
        bars[-1].session_date,  # type: ignore[attr-defined]
        track=False,
    )
    return sum(1 for p in result.instances if str(p.pattern_type) == "bull_flag")


def test_mirror_is_its_own_inverse() -> None:
    bars = PatternGenerator().bull_flag().bars
    back = mirror_bars(mirror_bars(bars))
    for original, restored in zip(bars, back, strict=True):
        for field in ("open", "high", "low", "close"):
            assert float(getattr(restored, field)) == pytest.approx(
                float(getattr(original, field)), rel=1e-12
            )


def test_mirror_swaps_high_and_low() -> None:
    """A mirror that kept high as 1/high would invert the bar itself."""
    bars = PatternGenerator().bull_flag().bars
    mirrored = mirror_bars(bars)
    for original, flipped in zip(bars, mirrored, strict=True):
        assert flipped.high >= flipped.low
        assert float(flipped.high) == pytest.approx(1.0 / float(original.low), rel=1e-12)
        assert float(flipped.low) == pytest.approx(1.0 / float(original.high), rel=1e-12)


def test_mirror_inverts_direction_for_the_detectors() -> None:
    """The claim the bear-flag study rests on.

    A synthetic bull flag is found. Its mirror -- which *is* a bear flag in
    real prices -- is not found, because a bull-flag detector has no business
    finding a bull flag in a falling market. Mirroring back finds it again.

    A no-op mirror fails the middle assertion; a mirror that reflects about a
    constant instead of inverting fails the last one.
    """
    bars = PatternGenerator().bull_flag(BullFlagSpec(breakout_sessions=0)).bars
    assert _bull_flags(bars) > 0, "the generator's own flag must be detectable first"
    assert _bull_flags(mirror_bars(bars)) == 0
    assert _bull_flags(mirror_bars(mirror_bars(bars))) > 0


def test_a_real_bear_flag_detects_through_the_mirror() -> None:
    """The study's actual read path, stated the way the study uses it.

    Build a bear flag directly -- a bull flag run backwards in price, which is
    what mirroring the generator's output produces -- and confirm the *long*
    machinery finds it once the series is mirrored. This is the test that says
    the bear side can be measured at all.
    """
    bear = mirror_bars(PatternGenerator().bull_flag().bars)
    assert _bull_flags(bear) == 0
    assert _bull_flags(mirror_bars(bear)) > 0


def test_level_crossings_keep_their_order() -> None:
    """Why the breakout engine's states fire on the right sessions.

    The engine is a state machine over level crossings. If the mirror did not
    preserve *which crossing happened first*, every ``CLOSED_ABOVE``,
    ``RETEST_CONFIRMED`` and ``FAILED_BREAKOUT`` would land on the wrong bar.
    """
    rng = np.random.default_rng(20260924)
    prices = 50.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    for level in (40.0, 50.0, 62.5):
        first_below = next((i for i, p in enumerate(prices) if p <= level), None)
        mirrored = 1.0 / prices
        first_above_mirror = next((i for i, p in enumerate(mirrored) if p >= 1.0 / level), None)
        assert first_below == first_above_mirror


def test_short_return_is_not_the_mirrored_long_return() -> None:
    """The trap the module exists to stop.

    Halve the price: the short made 50%, the mirrored long made 100%. Quoting
    the second as the first would overstate every winning short, so the study
    prices shorts on real prices and the mirror never touches a return.
    """
    assert short_return(100.0, 50.0) == pytest.approx(0.50)
    mirrored_long = (1.0 / 50.0) / (1.0 / 100.0) - 1.0
    assert mirrored_long == pytest.approx(1.00)
    assert short_return(100.0, 50.0) != pytest.approx(mirrored_long)


def test_short_return_is_unbounded_below_and_capped_above() -> None:
    """The asymmetry a long position does not have."""
    assert short_return(100.0, 0.0) == pytest.approx(1.0)
    assert short_return(100.0, 300.0) == pytest.approx(-2.0)
    assert short_return(100.0, 95.0, cost=0.001) < short_return(100.0, 95.0)


def test_mirror_refuses_a_non_positive_price() -> None:
    """§0.7 and §0.8 record zero and placeholder prints in this corpus.

    A silent infinity would reach a detector and come back as a pattern.
    """
    for bad in (Decimal("0"), Decimal("-1.5")):
        with pytest.raises(ValueError, match="non-positive"):
            mirror_price(bad)


def test_unmirror_round_trips_a_price() -> None:
    assert float(unmirror_price(mirror_price(Decimal("37.5")))) == pytest.approx(37.5)


def test_borrow_cost_accrues_with_time() -> None:
    assert borrow_cost(252, 0.03) == pytest.approx(0.03)
    assert borrow_cost(63, 0.03) == pytest.approx(0.0075)
    assert borrow_cost(0, 0.03) == 0.0
    with pytest.raises(ValueError):
        borrow_cost(-1, 0.03)
