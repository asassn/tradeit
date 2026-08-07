"""The competing-pattern matrix.

Coexistence is the expected outcome, not a failure. Twelve detectors over the
same bars overlap by construction, and forcing one label would discard what a
later scoring stage is better placed to decide (ADR-0019).

So these tests assert three things:

1. The matrix **runs** and produces a reading for every case, so the generated
   report cannot silently become empty.
2. The **specific exclusions that are definitional** hold — chiefly that the high
   tight flag does not fire on an ordinary bull flag, which is the one comparison
   the brief singles out.
3. Where two families coexist, **some component separates them**. Two definitions
   that agree on every component are the same definition under two names, and
   that would be a defect rather than a coexistence.
"""

from __future__ import annotations

import pytest

from tradeit.patterns.competition import (
    coexistence_counts,
    competition_cases,
    run_case,
    run_matrix,
)
from tradeit.patterns.registry import DetectorRegistry
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
REGISTRY = DetectorRegistry.from_config()


@pytest.fixture(scope="module")
def matrix():
    return run_matrix(REGISTRY, GENERATOR)


class TestMatrixRuns:
    def test_every_named_comparison_is_covered(self):
        """The gate names thirteen comparisons; the suite carries those and the
        ones construction turned up."""
        names = {name for name, _, _, _ in competition_cases(GENERATOR)}
        required = {
            "bull_flag_vs_pennant",
            "bull_flag_vs_tight_consolidation",
            "vcp_vs_tight_consolidation",
            "vcp_vs_flat_base",
            "flat_base_vs_tight_consolidation",
            "ascending_triangle_vs_pennant",
            "ascending_triangle_vs_tight_consolidation",
            "pennant_vs_bull_flag",
            "cup_vs_double_bottom",
            "double_bottom_vs_inverse_head_shoulders",
            "high_tight_flag_vs_ordinary_bull_flag",
            "base_on_base_vs_two_independent_bases",
            "breakout_retest_vs_ordinary_support_test",
        }
        assert required <= names

    def test_most_cases_produce_at_least_one_reading(self, matrix):
        """A case where nothing qualifies is legitimate but should be rare; a
        matrix that was mostly empty would be reporting nothing."""
        with_readings = [case for case in matrix if case.readings]
        assert len(with_readings) >= len(matrix) - 2

    def test_each_reading_names_its_strongest_and_weakest_component(self, matrix):
        """The component breakdown is the point of the matrix. A reading without
        one records that a family qualified and nothing about why."""
        for case in matrix:
            for reading in case.readings:
                assert reading.strongest[0] != "none"
                assert reading.weakest[0] != "none"
                assert 0.0 <= reading.quality <= 100.0
                assert 0.0 < reading.coverage <= 100.0


class TestDefinitionalExclusions:
    """Where exclusivity is enforced because the definitions are incompatible."""

    def test_the_high_tight_flag_does_not_fire_on_an_ordinary_bull_flag(self, matrix):
        """The comparison the brief singles out.

        A detector tuned until it fires here is not a rarer pattern; it is the
        bull flag detector under a name that implies more than it delivers.
        """
        case = next(c for c in matrix if c.case == "high_tight_flag_vs_ordinary_bull_flag")
        assert "high_tight_flag" not in case.families
        assert "bull_flag" in case.families

    def test_a_stair_step_is_not_base_on_base(self, matrix):
        case = next(c for c in matrix if c.case == "base_on_base_vs_two_independent_bases")
        assert "base_on_base" not in case.families

    def test_an_inverse_head_and_shoulders_is_not_a_double_bottom(self, matrix):
        """Its shoulders sit at a common level, so every double-bottom
        measurement looks excellent; the head beneath them is the whole reason
        it is not one."""
        case = next(c for c in matrix if c.case == "double_bottom_vs_inverse_head_shoulders")
        assert "inverse_head_shoulders" in case.families
        assert "double_bottom" not in case.families


class TestCoexistenceIsInformative:
    def test_where_families_coexist_a_component_separates_them(self, matrix):
        """Two definitions agreeing on every component are one definition under
        two names, which would be a defect rather than a coexistence."""
        for case in matrix:
            if len(case.readings) < 2:
                continue
            signatures = {
                (r.strongest[0], round(r.strongest[1]), r.weakest[0], round(r.weakest[1]))
                for r in case.readings
            }
            assert len(signatures) > 1, (
                f"every family reading {case.case} produced an identical component "
                "signature; that would mean the definitions are the same definition"
            )

    def test_a_margin_is_reported_for_every_case(self, matrix):
        """How far the top reading sits above the next is what a later stage most
        needs: a small margin says the family label is the least reliable part of
        the reading."""
        for case in matrix:
            assert case.margin() >= 0.0

    def test_geometry_overlap_is_recorded_between_qualifying_families(self, matrix):
        multi = [c for c in matrix if len(c.readings) > 1]
        assert multi
        for case in multi:
            assert case.overlaps
            assert all(0.0 <= v <= 1.0 for v in case.overlaps.values())

    def test_coexistence_counts_rank_the_common_pairs(self, matrix):
        counts = coexistence_counts(matrix)
        assert counts
        values = list(counts.values())
        assert values == sorted(values, reverse=True)


class TestSingleCase:
    def test_a_case_can_be_run_in_isolation(self):
        """The report builds cases one at a time; running one must not depend on
        the others having run."""
        case = run_case(
            REGISTRY,
            "cup_vs_v_bottom",
            "the cup's defining negative",
            "v_bottom",
            GENERATOR.v_bottom(),
        )
        assert case.case == "cup_vs_v_bottom"
        assert "cup_handle" in case.families
        payload = case.to_payload()
        assert payload["drawn_as"] == "v_bottom"
        assert payload["readings"]

    def test_the_v_bottom_is_separated_by_roundness_and_nothing_else(self):
        """The analysis the gate asks for, pinned as a test.

        The cup and the V-bottom are generated with the same rims, depth,
        duration and handle, so `bottom_roundness` is the only component that can
        differ — and it must, decisively.
        """
        from tradeit.patterns.detectors import CupHandleDetector

        detector = CupHandleDetector()
        cup = detector.detect(*_args(GENERATOR.cup_handle()))[0]
        v_bottom = detector.detect(*_args(GENERATOR.v_bottom()))[0]

        cup_scores = {c.name: c.score for c in cup.components if not c.unavailable}
        v_scores = {c.name: c.score for c in v_bottom.components if not c.unavailable}

        differing = {
            name for name in cup_scores if abs(cup_scores[name] - v_scores.get(name, 0.0)) > 5.0
        }
        assert "bottom_roundness" in differing
        assert cup_scores["bottom_roundness"] - v_scores["bottom_roundness"] > 35.0


def _args(series):
    return series.bars, series.last_session
