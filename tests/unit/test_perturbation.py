"""Score stability, and monotonicity where the definitions imply it.

Two related questions, deliberately kept apart.

**Stability** asks how much a score moves when the input moves a little. The
answer should usually be "a little", and where it is not, the jump should have a
structural cause — a newly confirmed pivot, a change of lifecycle state, a
structure appearing or vanishing. A large jump with none of those is a threshold
being crossed where reality is continuous, and that is a defect.

**Monotonicity** asks whether a score moves the *right way* when a dimension is
pushed. It is imposed only where the definition implies it. Deeper retracement
must not improve retracement quality; less-ascending lows must not improve
rising-lows quality. But most dimensions are deliberately non-linear — a
band-scored component peaks in the middle and falls away on both sides, and
demanding monotonicity there would be demanding the detector abandon the reason
the band exists.

The tolerance in the monotonicity helpers is not slack for its own sake. The
generators are stochastic, so adjacent settings differ by a little noise as well
as by the parameter; a strict comparison would be asserting the generator is
deterministic in a dimension where it is not.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.perturbation import (
    add_price_noise,
    add_volume_noise,
    is_non_increasing,
    perturb,
    shift_timing,
    sweep,
)
from tradeit.patterns.registry import DETECTOR_ORDER, DetectorRegistry
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator, VcpSpec

GENERATOR = PatternGenerator()
REGISTRY = DetectorRegistry.from_config()

#: The series each detector is perturbed around: its own structure, with
#: trailing room so a small nudge does not simply run the pattern off the end.
BASELINE = {
    "bull_flag": lambda s: GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=3), seed=s),
    "vcp": lambda s: GENERATOR.vcp(VcpSpec(breakout_sessions=8), seed=s),
    "flat_base": lambda s: GENERATOR.flat_base(seed=s, breakout_sessions=8),
    "ascending_triangle": lambda s: GENERATOR.ascending_triangle(seed=s, touches=6),
    "pennant": lambda s: GENERATOR.pennant(seed=s, sessions=18),
    "cup_handle": lambda s: GENERATOR.cup_handle(seed=s, handle_sessions=22),
    "high_tight_flag": lambda s: GENERATOR.high_tight_flag(seed=s, pause_sessions=20),
    "double_bottom": lambda s: GENERATOR.double_bottom(seed=s, recovery_sessions=22),
    "inverse_head_shoulders": lambda s: GENERATOR.inverse_head_shoulders(
        seed=s, recovery_sessions=22
    ),
    "base_on_base": lambda s: GENERATOR.base_on_base(seed=s, second_sessions=28),
    "tight_consolidation": lambda s: GENERATOR.tight_consolidation(seed=s, sessions=20),
    "breakout_retest": lambda s: GENERATOR.breakout_retest(seed=s, hold_sessions=20),
}

TRIALS = 12


def detector(name: str):
    return REGISTRY.detector(name, Bartimeframe.D1)


class TestSmallPerturbationsMoveScoresSmally:
    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_tiny_price_noise_is_not_amplified(self, name):
        """A tenth of a percent of jitter must not swing a score wildly.

        The bound is on the *median*, not the maximum. A single sample where
        noise happened to flip a pivot is a legitimate structural transition,
        and the median is what says whether the detector is measuring structure
        or measuring noise.
        """
        report = perturb(
            detector(name),
            BASELINE[name],
            lambda series, seed: add_price_noise(series, 0.001, seed=seed + 7000),
            dimension="price_noise_0.1pct",
            intent="every bar scaled by ~0.1%",
            trials=TRIALS,
        )
        assert report.median_delta < 8.0, (
            f"{name} moved a median of {report.median_delta:.1f} points under 0.1% price "
            f"noise: {report.format_row()}"
        )

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_large_jumps_have_structural_causes(self, name):
        """A jump is allowed; a jump with no explanation is not.

        "Unexplained" means the structure started in the same place, the state
        did not change, and the pattern neither appeared nor vanished — yet the
        score moved by more than three times the median. That combination is a
        threshold being crossed where the underlying reality is continuous.
        """
        report = perturb(
            detector(name),
            BASELINE[name],
            lambda series, seed: add_price_noise(series, 0.003, seed=seed + 8000),
            dimension="price_noise_0.3pct",
            intent="every bar scaled by ~0.3%",
            trials=TRIALS,
        )
        unexplained = report.unexplained_jumps
        assert len(unexplained) <= 1, (
            f"{name} produced {len(unexplained)} score jumps with no structural cause; "
            f"largest delta {max(u.delta for u in unexplained):.1f}"
        )

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_volume_noise_alone_moves_scores_less_than_price_noise(self, name):
        """Volume is evidence, geometry is definition.

        Every detector here weights its volume components well below its
        structural ones, so a volume-only perturbation should move a score less
        than an equivalent price one. A detector where the reverse held would be
        one whose pattern was really a volume screen.
        """
        volume = perturb(
            detector(name),
            BASELINE[name],
            lambda series, seed: add_volume_noise(series, 0.15, seed=seed + 9000),
            dimension="volume_noise",
            intent="volume jittered by ~15%, prices untouched",
            trials=TRIALS,
        )
        price = perturb(
            detector(name),
            BASELINE[name],
            lambda series, seed: add_price_noise(series, 0.003, seed=seed + 8000),
            dimension="price_noise_0.3pct",
            intent="every bar scaled by ~0.3%",
            trials=TRIALS,
        )
        assert volume.median_delta <= price.median_delta + 6.0

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_a_score_does_not_depend_on_where_the_window_sits(self, name):
        """Dropping leading bars must not move a score much.

        The structure and the knowledge boundary are untouched; only the array
        offset changes. A score that moved here would be reading an index rather
        than a shape. Some movement is legitimate — the indicator warm-up and
        the volume baseline both shorten — so the bound is loose and on the
        median.
        """
        report = perturb(
            detector(name),
            BASELINE[name],
            lambda series, _seed: shift_timing(series, 3),
            dimension="timing_shift",
            intent="three leading bars dropped",
            trials=TRIALS,
        )
        assert report.median_delta < 12.0, report.format_row()


class TestMonotonicityWhereTheDefinitionImpliesIt:
    """Only where a definition actually implies a direction."""

    def test_deeper_retracement_never_improves_retracement_quality(self):
        scores = sweep(
            detector("bull_flag"),
            lambda v: GENERATOR.bull_flag(BullFlagSpec(retracement=v)),
            (0.30, 0.45, 0.60, 0.75, 0.90),
            component="retracement",
        )
        assert is_non_increasing(scores), scores

    def test_less_ascending_lows_never_improve_rising_lows_quality(self):
        rising = sweep(
            detector("ascending_triangle"),
            lambda v: GENERATOR.ascending_triangle(touches=4, depth=v),
            (0.12,),
            component="rising_lows",
        )
        flat = sweep(
            detector("ascending_triangle"),
            lambda _v: GENERATOR.rectangle(),
            (0.0,),
            component="rising_lows",
        )
        assert rising[0] > flat[0], (rising, flat)

    def test_disordered_contractions_never_improve_progression_quality(self):
        """A staircase, a shallow progression, and a base that widens again.

        All three carry trailing bars so the final leg is *confirmed*. Without
        them the widening leg sits in the provisional tail, the detector
        correctly cannot see it, and the test would assert something the
        detector is forbidden to know.
        """
        ordered = sweep(
            detector("vcp"),
            lambda _v: GENERATOR.vcp(
                VcpSpec(depths=(0.18, 0.10, 0.05), leg_sessions=(14, 9, 6), breakout_sessions=6)
            ),
            (0.0,),
            component="contraction_progression",
        )
        disordered = sweep(
            detector("vcp"),
            lambda _v: GENERATOR.vcp(
                VcpSpec(depths=(0.18, 0.05, 0.12), leg_sessions=(14, 9, 8), breakout_sessions=6)
            ),
            (0.0,),
            component="contraction_progression",
        )
        assert ordered[0] > disordered[0], (ordered, disordered)

    def test_depth_beyond_the_preferred_zone_never_improves_flat_base_depth(self):
        scores = sweep(
            detector("flat_base"),
            lambda v: GENERATOR.flat_base(depth=v),
            (0.08, 0.13, 0.18, 0.24, 0.30),
            component="depth",
        )
        assert is_non_increasing(scores), scores

    def test_a_deeper_pause_never_improves_high_tight_flag_tightness(self):
        scores = sweep(
            detector("high_tight_flag"),
            lambda v: GENERATOR.high_tight_flag(pause_depth=v),
            (0.10, 0.14, 0.18, 0.22),
            component="consolidation_tightness",
        )
        assert is_non_increasing(scores), scores

    def test_destroying_the_handle_never_improves_handle_quality(self):
        scores = sweep(
            detector("cup_handle"),
            lambda v: GENERATOR.cup_handle(handle_depth_ratio=v),
            (0.15, 0.25, 0.40, 0.60, 0.80),
            component="handle_structure",
        )
        assert is_non_increasing(scores), scores

    def test_wider_rim_asymmetry_never_improves_rim_symmetry(self):
        scores = sweep(
            detector("cup_handle"),
            lambda v: GENERATOR.cup_handle(right_rim_shortfall=v),
            (0.0, 0.06, 0.12, 0.18),
            component="rim_symmetry",
        )
        assert is_non_increasing(scores), scores

    def test_a_lower_second_low_never_improves_double_bottom_symmetry(self):
        scores = sweep(
            detector("double_bottom"),
            lambda v: GENERATOR.double_bottom(undercut=v),
            (0.0, 0.015, 0.03, 0.045),
            component="low_symmetry",
        )
        assert is_non_increasing(scores), scores

    def test_more_shoulder_asymmetry_never_improves_shoulder_symmetry(self):
        scores = sweep(
            detector("inverse_head_shoulders"),
            lambda v: GENERATOR.inverse_head_shoulders(shoulder_asymmetry=v),
            (0.0, 0.12, 0.24, 0.36),
            component="shoulder_symmetry",
        )
        assert is_non_increasing(scores), scores

    def test_more_ceiling_progress_never_improves_base_on_base_progression(self):
        scores = sweep(
            detector("base_on_base"),
            lambda v: GENERATOR.base_on_base(ceiling_advance=v),
            (0.04, 0.05, 0.06, 0.08),
            component="ceiling_progression",
        )
        assert is_non_increasing(scores), scores

    def test_weaker_contraction_never_improves_tight_consolidation(self):
        scores = sweep(
            detector("tight_consolidation"),
            lambda v: GENERATOR.tight_consolidation(depth=v),
            (0.02, 0.035, 0.05, 0.07),
            component="range_contraction",
        )
        assert is_non_increasing(scores), scores

    def test_a_pullback_further_from_the_line_never_improves_proximity(self):
        scores = sweep(
            detector("breakout_retest"),
            lambda v: GENERATOR.breakout_retest(retest_overshoot=v),
            (0.0, 0.01, 0.02, 0.03),
            component="retest_proximity",
        )
        assert is_non_increasing(scores), scores


class TestNonLinearityIsDeliberate:
    """Where monotonicity is *not* imposed, and why.

    These record design decisions that a future reader might otherwise mistake
    for oversights.
    """

    def test_prior_decline_peaks_in_the_middle(self):
        """More damage is not more evidence.

        A first bounce is rarely the end of a 60% fall, so the component is a
        band rather than a ramp. A monotone-increasing assertion here would be
        asserting that the worse the collapse, the better the bottom.
        """
        scores = sweep(
            detector("double_bottom"),
            lambda v: GENERATOR.double_bottom(prior_decline=v),
            (0.14, 0.25, 0.40, 0.60),
            component="prior_decline",
        )
        assert scores[-1] < max(scores)

    def test_head_prominence_peaks_in_the_middle(self):
        """A head 45% below its shoulders is a crash with two bounces around it."""
        scores = sweep(
            detector("inverse_head_shoulders"),
            lambda v: GENERATOR.inverse_head_shoulders(prominence=v),
            (0.05, 0.12, 0.25, 0.45),
            component="head_prominence",
        )
        assert scores[-1] < max(scores)
        assert scores[0] < max(scores)

    def test_a_small_undercut_scores_above_an_exact_double(self):
        """Deliberately non-monotone in the constructive direction: taking out
        the obvious stops before turning is evidence, not damage."""
        scores = sweep(
            detector("double_bottom"),
            lambda v: GENERATOR.double_bottom(undercut=v),
            (0.0, 0.02),
            component="undercut_behaviour",
        )
        assert scores[1] >= scores[0]
