"""Double Bottom and Inverse Head and Shoulders — the reversal families.

Every detector before these two measures a pause inside an advance and requires
a prior uptrend. These require a prior *decline*, and that inversion is the
reason neither can be a wrapper around an earlier detector: the context
component is not the flag's with the sign flipped, it is a different
measurement of a different thing.

The negatives are chosen for that inversion:

* **The range.** Two lows at the same level with nothing to reverse. This is
  the failure mode that matters most in production, because ranges are common
  and they manufacture same-level lows by the dozen.
* **Each other.** An inverse head and shoulders contains two shoulders at a
  common level, which reads as a textbook double bottom unless the detector
  checks that the level actually held. A triple bottom contains no head, which
  is the mirror requirement.
* **The bearish mirror.** A head-and-shoulders *top* has two troughs at a
  common level with a rally between them.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import PatternType
from tradeit.patterns.base import ComponentRequirement, PatternState
from tradeit.patterns.detectors import (
    DoubleBottomDetector,
    InverseHeadShouldersDetector,
)
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
DOUBLE = DoubleBottomDetector()
IHS = InverseHeadShouldersDetector()


def _args(series):
    return series.bars, series.last_session


def best(detector, series) -> float:
    return max((p.quality for p in detector.detect(*_args(series))), default=0.0)


def component(detector, series, name) -> float:
    found = detector.detect(*_args(series))
    assert found, "expected a detection to read a component from"
    scored = found[0].component(name)
    assert scored is not None
    return scored.score


class TestDoubleBottom:
    def test_two_lows_at_a_level_after_a_decline_are_found(self):
        found = DOUBLE.detect(*_args(GENERATOR.double_bottom()))
        assert found
        assert found[0].pattern_type is PatternType.DOUBLE_BOTTOM
        assert found[0].quality > 80

    def test_a_range_is_not_a_bottom(self):
        """The defining negative, and the one that matters in production.

        The same two lows, the same intervening rally, and nothing to reverse.
        A reversal pattern without a prior decline is a claim about nothing.
        """
        assert not DOUBLE.detect(*_args(GENERATOR.range_double_low()))

    def test_net_change_is_what_separates_a_decline_from_a_range(self):
        """Peak-to-low alone reports a 20% decline for a range that oscillated
        20% and finished where it started, which is how a choppy series
        manufactures double bottoms on every pair of lows it lands together."""
        assert not DOUBLE.detect(*_args(GENERATOR.choppy_range()))
        assert not DOUBLE.detect(*_args(GENERATOR.mean_reverting()))
        assert not DOUBLE.detect(*_args(GENERATOR.random_walk(220)))

    def test_a_lower_low_between_the_two_means_the_level_did_not_hold(self):
        """An inverse head and shoulders is the case that exposes this.

        Its two shoulders sit at a common level with a rally between them, so
        every measurement a double bottom makes looks excellent -- and the head
        beneath them is the whole reason it is not one. Without the interior
        check, the shoulders scored *higher* as a double bottom than a real
        double bottom does.
        """
        assert IHS.detect(*_args(GENERATOR.inverse_head_shoulders()))
        assert not DOUBLE.detect(*_args(GENERATOR.inverse_head_shoulders()))

    def test_a_second_low_well_below_the_first_is_a_continued_decline(self):
        assert not DOUBLE.detect(*_args(GENERATOR.descending_double_low()))

    def test_a_shallow_wobble_is_one_low_not_two(self):
        assert not DOUBLE.detect(*_args(GENERATOR.single_low_with_noise()))

    def test_the_intervening_rally_scores_by_depth(self):
        scores = [
            component(DOUBLE, GENERATOR.double_bottom(rally=rally), "intervening_rally")
            for rally in (0.07, 0.12, 0.16)
        ]
        assert scores == sorted(scores)

    def test_a_small_undercut_is_constructive(self):
        """The stop-run before the turn scores above an exact double match, and
        a large undercut scores below both."""
        exact = component(DOUBLE, GENERATOR.double_bottom(undercut=0.0), "undercut_behaviour")
        small = component(DOUBLE, GENERATOR.double_bottom(undercut=0.02), "undercut_behaviour")
        large = component(DOUBLE, GENERATOR.double_bottom(undercut=0.035), "undercut_behaviour")
        assert small >= exact > large

    def test_a_collapse_is_scored_down_rather_than_celebrated(self):
        """A first bounce is rarely the end of a 60% fall. The component is a
        band, not a ramp, because more damage is not more evidence."""
        moderate = component(DOUBLE, GENERATOR.double_bottom(prior_decline=0.30), "prior_decline")
        collapse = component(DOUBLE, GENERATOR.double_bottom(prior_decline=0.60), "prior_decline")
        assert moderate > collapse
        found = DOUBLE.detect(*_args(GENERATOR.double_bottom(prior_decline=0.60)))
        assert any("first bounce" in e.detail for e in found[0].contradicting_evidence)

    def test_a_quieter_second_low_is_less_supply_at_the_same_price(self):
        quiet = component(DOUBLE, GENERATOR.double_bottom(second_low_volume=0.4), "volume_profile")
        heavy = component(DOUBLE, GENERATOR.double_bottom(second_low_volume=2.4), "volume_profile")
        assert quiet > heavy

    def test_the_defining_components_are_required(self):
        found = DOUBLE.detect(*_args(GENERATOR.double_bottom()))[0]
        for name in ("low_symmetry", "intervening_rally", "prior_decline"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_the_neckline_is_geometry_not_a_verdict(self):
        """Phase 4 records where the structure would resolve and stops."""
        found = DOUBLE.detect(*_args(GENERATOR.double_bottom()))[0]
        assert found.geometry.resistance is not None
        assert found.geometry.resistance.method == "neckline"
        assert not found.state.is_terminal or found.state is PatternState.INVALIDATED

    def test_invalidation_sits_below_both_lows(self):
        found = DOUBLE.detect(*_args(GENERATOR.double_bottom()))[0]
        lows = found.geometry.key_points
        assert found.invalidation_price < min(lows["first_low"].price, lows["second_low"].price)


class TestInverseHeadAndShoulders:
    def test_three_lows_with_the_middle_deepest_are_found(self):
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders()))
        assert found
        assert found[0].pattern_type is PatternType.INVERSE_HEAD_AND_SHOULDERS
        assert found[0].quality > 80

    def test_three_lows_at_the_same_level_have_no_head(self):
        """A triple bottom. The mirror of the double bottom's interior check:
        this family needs a head, and that one needs there not to be one."""
        assert not IHS.detect(*_args(GENERATOR.triple_bottom()))

    def test_head_prominence_is_a_band_not_a_ramp(self):
        """A head 45% below its shoulders is a crash with two bounces around
        it, not a staged exhaustion of selling."""
        shallow = component(
            IHS, GENERATOR.inverse_head_shoulders(prominence=0.05), "head_prominence"
        )
        ideal = component(IHS, GENERATOR.inverse_head_shoulders(prominence=0.18), "head_prominence")
        extreme = component(
            IHS, GENERATOR.inverse_head_shoulders(prominence=0.45), "head_prominence"
        )
        assert ideal > shallow
        assert ideal > extreme

    def test_asymmetric_shoulders_are_marked_down_not_rejected(self):
        """Textbook illustrations are symmetric and real structures are not.

        A detector that demands visual symmetry finds almost nothing, so the
        match is scored across a deliberately loose tolerance and a lopsided
        structure appears with a lower number rather than disappearing.
        """
        scores = [
            component(
                IHS,
                GENERATOR.inverse_head_shoulders(shoulder_asymmetry=asymmetry),
                "shoulder_symmetry",
            )
            for asymmetry in (0.0, 0.15, 0.30)
        ]
        assert scores == sorted(scores, reverse=True)
        assert IHS.detect(*_args(GENERATOR.inverse_head_shoulders(shoulder_asymmetry=0.35)))

    def test_a_falling_neckline_says_each_rally_is_weaker(self):
        falling = component(
            IHS, GENERATOR.inverse_head_shoulders(neckline_slope=-0.06), "neckline_quality"
        )
        level = component(
            IHS, GENERATOR.inverse_head_shoulders(neckline_slope=0.0), "neckline_quality"
        )
        assert level > falling
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders(neckline_slope=-0.06)))
        assert any("reaching less far" in e.detail for e in found[0].contradicting_evidence)

    def test_two_points_do_not_make_a_well_supported_line(self):
        """The neckline is defined by two highs, so the line itself is not
        evidence for the line. Conformity is measured on the *other* confirmed
        highs inside the structure, and reports a middling number when there
        are none rather than a perfect fit."""
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders()))[0]
        neckline = found.component("neckline_quality")
        assert neckline.measurements["touches"] == 2.0
        assert neckline.score < 100.0

    def test_timing_asymmetry_is_scored(self):
        scores = [
            component(IHS, GENERATOR.inverse_head_shoulders(timing_skew=skew), "timing_symmetry")
            for skew in (1.0, 1.5, 2.2)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_the_prior_decline_is_required(self):
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders()))[0]
        for name in ("head_prominence", "shoulder_symmetry", "neckline_quality", "prior_decline"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_a_range_produces_no_bottom(self):
        assert not IHS.detect(*_args(GENERATOR.choppy_range()))
        assert not IHS.detect(*_args(GENERATOR.random_walk(220)))

    def test_invalidation_sits_below_the_head(self):
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders()))[0]
        assert found.invalidation_price < found.geometry.key_points["head"].price

    def test_the_neckline_level_is_projected_to_today(self):
        """A sloped line's level moves. Reporting where it stood when it was
        drawn would have the state machine compare today's close against a
        stale number."""
        found = IHS.detect(*_args(GENERATOR.inverse_head_shoulders(neckline_slope=0.05)))[0]
        resistance = found.geometry.resistance
        assert resistance is not None
        assert resistance.anchor_date == found.as_of_session
        assert resistance.slope_per_session > 0


class TestReversalFamiliesAreDistinct:
    """Neither family is the other with a component renamed."""

    def test_the_two_detectors_do_not_absorb_each_other(self):
        double = GENERATOR.double_bottom()
        ihs = GENERATOR.inverse_head_shoulders()
        assert DOUBLE.detect(*_args(double)) and not IHS.detect(*_args(double))
        assert IHS.detect(*_args(ihs)) and not DOUBLE.detect(*_args(ihs))

    def test_a_bearish_top_is_not_a_bullish_bottom(self):
        """A head-and-shoulders top has two troughs at a common level with a
        rally between, which is a double bottom's geometry exactly. What it
        does not have is a decline into the first trough."""
        top = GENERATOR.head_and_shoulders_top()
        assert not DOUBLE.detect(*_args(top))
        assert not IHS.detect(*_args(top))

    def test_a_triple_bottom_contains_double_bottoms_and_that_is_correct(self):
        """Recorded rather than suppressed. Three lows at one level contain
        pairs of lows at that level, and reading them as double bottoms is what
        the structure supports -- the absent head is what rules out the other
        family."""
        triple = GENERATOR.triple_bottom()
        assert DOUBLE.detect(*_args(triple))
        assert not IHS.detect(*_args(triple))

    def test_continuation_detectors_read_reversal_series_weakly(self):
        """Not silently, and not strongly.

        The right half of an inverse head and shoulders -- head, rally,
        shallower low -- genuinely is a flag's shape, so the flag detector
        firing there is a real reading rather than a bug, and suppressing it
        would mean rejecting flags for the company they keep. What must hold is
        that the reading is weak. Measured at 65 on the inverse head and
        shoulders and 46 on the double bottom, both below the flag detector's
        own reading of its own generator, and the flag's defining
        `retracement` component sits at zero on the double bottom because the
        advance was entirely given back.

        The margin on the inverse head and shoulders is thin -- five points --
        and the honest statement of that is the assertion below rather than a
        wider bound the numbers do not support.
        """
        from tradeit.patterns.detectors import BullFlagDetector, FlatBaseDetector

        flag = BullFlagDetector()
        own = best(flag, GENERATOR.bull_flag())
        for detector in (flag, FlatBaseDetector()):
            for series in (GENERATOR.double_bottom(), GENERATOR.inverse_head_shoulders()):
                assert best(detector, series) < min(70.0, own)

        retracement = flag.detect(*_args(GENERATOR.double_bottom()))[0].component("retracement")
        assert retracement.score < 5.0


class TestSharedInvariants:
    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_bars_past_the_boundary_are_refused(self, detector):
        series = GENERATOR.double_bottom()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            detector.detect(series.bars, series.bars[-5].session_date)

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_detection_is_reproducible(self, detector):
        for series in (GENERATOR.double_bottom(), GENERATOR.inverse_head_shoulders()):
            first = detector.detect(*_args(series))
            second = detector.detect(list(series.bars), series.last_session)
            assert [p.to_payload() for p in first] == [p.to_payload() for p in second]

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_geometry_never_extends_past_the_boundary(self, detector):
        for series in (GENERATOR.double_bottom(), GENERATOR.inverse_head_shoulders()):
            for instance in detector.detect(*_args(series)):
                assert instance.geometry.end_date <= instance.as_of_session

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_every_instance_reconciles_and_is_structurally_complete(self, detector):
        for series in (
            GENERATOR.double_bottom(),
            GENERATOR.inverse_head_shoulders(),
            GENERATOR.triple_bottom(),
        ):
            for instance in detector.detect(*_args(series)):
                assert instance.reconciles
                assert instance.is_structurally_complete
                assert 0.0 <= instance.quality <= 100.0
                assert 0.0 < instance.evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_every_unavailable_component_states_a_reason(self, detector):
        for series in (GENERATOR.double_bottom(), GENERATOR.inverse_head_shoulders()):
            for instance in detector.detect(*_args(series)):
                for name, reason in instance.coverage_gaps().items():
                    assert reason, f"{detector.name}.{name} unavailable without a reason"

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_the_contract_declares_required_and_optional_components(self, detector):
        contract = detector.contract
        assert contract.required_components
        assert contract.warmup_bars == detector.minimum_bars
        assert 0.0 <= contract.minimum_evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", (DOUBLE, IHS), ids=lambda d: d.name)
    def test_no_detector_expresses_breakout_validity(self, detector):
        for series in (GENERATOR.double_bottom(), GENERATOR.inverse_head_shoulders()):
            for instance in detector.detect(*_args(series)):
                label = str(instance.state)
                assert "confirmed" not in label or label.endswith("unconfirmed")
