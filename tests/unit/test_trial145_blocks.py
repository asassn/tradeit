"""The two helpers that can make trial 145 report a wrong verdict silently.

`docs/prereg/BREADTH_DRAWDOWN_2026-11-25.md` fixes a within-block maximum
drawdown with the running peak **reset at each block start**, measured over
twenty six-month calendar blocks. Both of those are easy to get wrong in a way
that still produces plausible percentages, so both are pinned here rather than
trusted. The alternative -- discovering the block boundaries were off by a day
after grading -- would mean a spent trial graded on a statistic nobody
registered.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    """Import the verdict module by path; `scripts/` is not a package."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    sys.path.insert(0, str(_ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "trial145_verdict", _ROOT / "scripts" / "trial_breadth_drawdown_verdict.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because the module defines a dataclass, and
    # `dataclasses` resolves annotations through `sys.modules[cls.__module__]`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERDICT = _load()


class TestBlocks:
    """Twenty blocks, contiguous, covering exactly the registered decade."""

    def test_there_are_twenty_and_they_are_six_month_halves(self) -> None:
        got = VERDICT.blocks()
        assert len(got) == 20, "the hurdle is quoted at twenty blocks; a 19th or 21st moves it"
        assert got[0] == ("2010-H1", dt.date(2010, 1, 1), dt.date(2010, 6, 30))
        assert got[-1] == ("2019-H2", dt.date(2019, 7, 1), dt.date(2019, 12, 31))

    def test_no_block_overlaps_its_neighbour_and_none_is_skipped(self) -> None:
        """Non-overlapping is what makes the block t-statistic admissible."""
        got = VERDICT.blocks()
        for (_a, _lo_a, hi_a), (_b, lo_b, _hi_b) in itertools.pairwise(got):
            assert lo_b == hi_a + dt.timedelta(days=1)


class TestWithinBlockDrawdown:
    """The peak must reset at the block start, and the worst fall must win."""

    def test_a_fall_before_the_block_does_not_count(self) -> None:
        """The registered reset, stated as a case.

        Equity peaks at 200 in 2009 and the block opens at 100. Carrying the
        running peak in would report a 50% drawdown for a block in which equity
        only rose -- which is the correlated-path failure the registration
        rejects by name.
        """
        curve = [
            (dt.date(2009, 12, 1), 200.0),
            (dt.date(2010, 1, 5), 100.0),
            (dt.date(2010, 2, 5), 110.0),
            (dt.date(2010, 3, 5), 120.0),
        ]
        got = VERDICT.within_block_drawdown(curve, dt.date(2010, 1, 1), dt.date(2010, 6, 30))
        assert got == pytest.approx(0.0)

    def test_the_deepest_fall_inside_the_block_is_the_answer(self) -> None:
        """Two falls, and the larger is not the last one."""
        curve = [
            (dt.date(2010, 1, 5), 100.0),
            (dt.date(2010, 2, 5), 60.0),  # -40% from 100
            (dt.date(2010, 3, 5), 120.0),
            (dt.date(2010, 4, 5), 96.0),  # -20% from 120
        ]
        got = VERDICT.within_block_drawdown(curve, dt.date(2010, 1, 1), dt.date(2010, 6, 30))
        assert got == pytest.approx(0.40)

    def test_a_later_peak_resets_the_reference(self) -> None:
        """A fall is measured from the highest point *before* it, not the first."""
        curve = [
            (dt.date(2010, 1, 5), 100.0),
            (dt.date(2010, 2, 5), 200.0),
            (dt.date(2010, 3, 5), 150.0),
        ]
        got = VERDICT.within_block_drawdown(curve, dt.date(2010, 1, 1), dt.date(2010, 6, 30))
        assert got == pytest.approx(0.25)

    def test_a_block_with_fewer_than_two_points_is_zero_not_an_error(self) -> None:
        """A drawdown needs two observations; one is no evidence of a fall.

        Returning 0.0 rather than raising matters because the difference
        narrow-minus-wide is taken per block: a raise would abort the grading,
        and a sentinel like -1.0 would enter the mean as a 100-point swing.
        """
        curve = [(dt.date(2010, 1, 5), 100.0)]
        assert (
            VERDICT.within_block_drawdown(curve, dt.date(2010, 1, 1), dt.date(2010, 6, 30)) == 0.0
        )
        assert VERDICT.within_block_drawdown([], dt.date(2010, 1, 1), dt.date(2010, 6, 30)) == 0.0

    def test_points_outside_the_block_are_excluded_at_both_ends(self) -> None:
        """A crash the week after the block closes belongs to the next block."""
        curve = [
            (dt.date(2010, 6, 1), 100.0),
            (dt.date(2010, 6, 30), 100.0),
            (dt.date(2010, 7, 1), 10.0),
        ]
        got = VERDICT.within_block_drawdown(curve, dt.date(2010, 1, 1), dt.date(2010, 6, 30))
        assert got == pytest.approx(0.0)


class TestPairedStatistic:
    """The t the verdict quotes, and the cases where it must decline to quote one."""

    def test_t_is_the_mean_over_the_standard_error(self) -> None:
        got = VERDICT.Paired(label="x", differences=[0.02, 0.04, 0.06, 0.08])
        assert got.mean == pytest.approx(0.05)
        # sd = 0.0258199, se = sd/2, t = 0.05 / 0.01291 = 3.873
        assert got.t == pytest.approx(3.8730, rel=1e-3)

    def test_a_constant_difference_yields_no_t_rather_than_infinity(self) -> None:
        """Zero variance is not infinite confidence.

        A float-identical set of twenty blocks would mean the two arms produced
        the same curve, which is a defect to investigate -- not a pass.
        """
        assert VERDICT.Paired(label="x", differences=[0.01] * 20).t is None
        assert VERDICT.Paired(label="x", differences=[0.01]).t is None
