"""The stop must be honoured for the whole holding period, not half of it.

``scripts/pattern_atlas.py`` reports net returns at 5, 10, 21, 63 and 126
sessions. Its stop search ran over 63 sessions only, so a position stopped out
on session 80 was scored at its **session-126 close** as though it had been
held the whole way. Every 126-session mean in the atlas of 2026-09-25 was
overstated by that, and it was found while preparing to stake a ledger trial
on one of those means.

The two windows are deliberately different and both are pinned here:

* the **probability** columns are a 63-session race between the stop and +1R,
  which is §44's and §51's definition and is kept so those studies stay
  comparable;
* the **stop** is searched over the longest horizon reported, because a return
  that ignores the stop describes a trade nobody took.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from pattern_atlas import BORROW_ANNUAL, HORIZONS, outcomes

from tradeit.patterns.mirror import borrow_cost, short_return

#: The atlas writes its returns at six decimals, so comparisons are made at
#: the precision the file actually carries rather than at float precision.
TOLERANCE = 1e-6
COST = 0.0010


def test_a_stop_after_session_63_still_ends_the_trade() -> None:
    """The defect, reduced to one trade.

    Entry 100, stop 90. Price sits at 100 until session 80, drops to 85 (through
    the stop), then recovers to 150 by session 126. The six-month return must be
    the stop, not +50%.
    """
    close = np.full(200, 100.0)
    close[80] = 85.0
    close[81:] = 150.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 90.0, COST)
    assert found is not None
    net_126 = float(found["net_126"])
    assert net_126 < 0, "a stopped trade cannot return the later recovery"
    assert net_126 == pytest.approx((90.0 * (1 - COST)) / (100.0 * (1 + COST)) - 1.0, abs=1e-6)


def test_the_unstopped_trade_keeps_its_full_return() -> None:
    """The control: the fix must not stop trades that never hit their stop."""
    close = np.full(200, 100.0)
    close[1:] = 150.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 90.0, COST)
    assert found is not None
    assert float(found["net_126"]) == pytest.approx(
        (150.0 * (1 - COST)) / (100.0 * (1 + COST)) - 1.0, abs=1e-6
    )


def test_a_stop_inside_63_is_unchanged() -> None:
    """The old behaviour was already right here, and must stay right."""
    close = np.full(200, 100.0)
    close[40] = 85.0
    close[41:] = 150.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 90.0, COST)
    assert found is not None
    expected = (90.0 * (1 - COST)) / (100.0 * (1 + COST)) - 1.0
    for horizon in HORIZONS:
        if horizon >= 40:
            assert float(found[f"net_{horizon}"]) == pytest.approx(expected, abs=1e-6)


def test_the_probability_columns_stay_on_their_63_session_window() -> None:
    """A stop on session 80 is NOT ``stopped_within_63``.

    The race and the stop search are different windows on purpose. If this ever
    starts reporting 1, the fix has leaked into §44's and §51's definition and
    the three studies are no longer comparable.
    """
    close = np.full(200, 100.0)
    close[80] = 85.0
    close[81:] = 150.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 90.0, COST)
    assert found is not None
    assert found["stopped_within_63"] == "0"
    assert found["reached_1r_first"] == "0"
    # But the six-month net still reflects that stop.
    assert float(found["net_126"]) < 0


def test_a_short_stop_after_63_fires_on_the_right_session() -> None:
    """The bear side, where the stop sits ABOVE entry and a HIGH triggers it.

    Asserting only the sign is not enough: a short stopped at the wrong session
    still exits at the same stop price, so the return alone cannot tell a
    correct inequality from the long one. What it does change is **how long the
    borrow was paid** -- 80 sessions against 1 -- and that is the only visible
    difference, so it is what this pins. A mutation using the long side's
    ``low[j] <= stop`` stops this trade on session 1 and survives every other
    assertion here.
    """
    close = np.full(200, 100.0)
    close[80:] = 115.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 110.0, COST, short=True)
    assert found is not None
    expected = short_return(100.0, 110.0, COST) - borrow_cost(80, BORROW_ANNUAL)
    assert float(found["net_126"]) == pytest.approx(expected, abs=TOLERANCE)
    # And the long side's inequality would have charged one session of borrow.
    wrong = short_return(100.0, 110.0, COST) - borrow_cost(1, BORROW_ANNUAL)
    assert abs(expected - wrong) > 10 * TOLERANCE


def test_the_raw_return_column_ignores_the_stop_by_design() -> None:
    """``ret_*`` is the unstopped path and must NOT change.

    The atlas reports both because "did it continue" and "did it continue
    without taking me out first" are different questions.
    """
    close = np.full(200, 100.0)
    close[80] = 85.0
    close[81:] = 150.0
    found = outcomes(close.copy(), close.copy(), close, 0, 100.0, 90.0, COST)
    assert found is not None
    assert float(found["ret_126"]) == pytest.approx(0.5, abs=1e-6)
