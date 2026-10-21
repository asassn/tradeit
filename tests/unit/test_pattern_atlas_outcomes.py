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

import subprocess
import sys
import tempfile
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


def test_an_empty_result_is_an_error_not_a_success() -> None:
    """The failure mode that has now cost this project two overnight runs.

    Item 1's atlas compared a date string to a date-keyed dict, skipped every
    trade and wrote a header with no rows. The Stage B replay loaded the design
    window's bars for a hold-out run and did the same. **Both exited 0**, and
    both were only caught by a human reading the output.

    There is no question whose honest answer is a file containing a header and
    nothing else, so the script raises. This test drives the CLI rather than a
    function, because the exit code is the thing that was wrong.
    """
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # Events whose entry dates no price window will contain.
        (work / "events.csv").write_text(
            "security_id,direction,pattern,pattern_key,attempt,quality,scan_date,"
            "closed_above_date,retest_confirmed_date,confirmed_date,closed_above_entry,"
            "retest_confirmed_entry,confirmed_entry,stop,final_state\n"
            "1,long,volatility_contraction,k,1,50.0,1890-01-02,1890-01-03,,,"
            "10.0,,,9.0,confirmed\n"
        )
        (work / "elig.csv").write_text("security_id,session_date\n1,1890-01-03\n")
        (work / "index.csv").write_text(
            "session_date,level,securities\n"
            + "".join(f"1890-01-{d:02d},1.0,500\n" for d in range(1, 29))
        )
        done = subprocess.run(
            [
                # `sys.executable`, not `repo/.venv/bin/python`: the hardcoded
                # path does not exist on a CI runner, where the package is
                # pip-installed rather than run from a local venv, and this
                # test failed there with FileNotFoundError the first time CI
                # reached the Test step. It also has to be the interpreter
                # running pytest -- any other one may not have the package.
                sys.executable,
                str(repo / "scripts/pattern_atlas.py"),
                "--events",
                str(work / "events.csv"),
                "--eligible",
                str(work / "elig.csv"),
                "--index",
                str(work / "index.csv"),
                "--out",
                str(work / "out.csv"),
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            check=False,
        )
    assert done.returncode != 0, "an empty result must not exit 0"
    assert "wrote NO pairs" in done.stderr + done.stdout
    # NOTE: this guard is also the only practical defence against the window
    # being hard-coded again. A test that distinguished a parameterised window
    # from a hard-coded one would need the corpus, which a unit test must not
    # touch; instead, getting the window wrong now ends in this error rather
    # than in an empty file reported as a success.


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
