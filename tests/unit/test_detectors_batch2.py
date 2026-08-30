"""Cup and Handle, and High Tight Flag.

Each is tested against the negative that most specifically threatens *it*:

* Cup and handle — the **V-bottom**. Same rims, same depth, same duration, same
  handle. It differs in exactly one dimension: whether price spent time at the
  low. That is the whole distinction between absorption and a bounce, and a
  detector that cannot see it is detecting "went down and came back".
* High tight flag — the **ordinary bull flag**. The brief is explicit that this
  definition must not be relaxed to generate examples. A high tight flag
  detector that fires on a 25% advance is not a rarer pattern; it is the bull
  flag detector under a name that implies more than it delivers.

Where a composite gap is narrower than the component gap, the test says so
rather than being written around: when two series differ in one dimension, the
composite can only separate them by that dimension's weight, and forcing a
wider gap would mean weighting a component for the convenience of a test.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import PatternType
from tradeit.patterns.base import ComponentRequirement, PatternState
from tradeit.patterns.detectors import (
    BullFlagDetector,
    CupHandleDetector,
    HighTightFlagDetector,
)
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
CUP = CupHandleDetector()
HTF = HighTightFlagDetector()
FLAG = BullFlagDetector()


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


class TestCupAndHandle:
    def test_a_rounded_base_with_a_handle_is_found(self):
        found = CUP.detect(*_args(GENERATOR.cup_handle()))
        assert found
        assert found[0].pattern_type is PatternType.CUP_WITH_HANDLE
        assert found[0].quality > 80

    def test_roundness_is_what_separates_a_cup_from_a_v(self):
        """The component separates decisively; the composite separates by its weight.

        The V-bottom is generated with the same rims, depth, duration and
        handle, so ``bottom_roundness`` is the only dimension that can differ.
        It scores about 100 against about 53, and the composite gap is the
        roughly nine points that difference is worth. Widening the composite gap
        would mean re-weighting the component because a test wanted a rounder
        number, which is the tuning the brief forbids.
        """
        cup_round = component(CUP, GENERATOR.cup_handle(), "bottom_roundness")
        v_round = component(CUP, GENERATOR.v_bottom(), "bottom_roundness")
        assert cup_round - v_round > 35

        assert best(CUP, GENERATOR.cup_handle()) > best(CUP, GENERATOR.v_bottom())

    def test_roundness_responds_monotonically_to_the_bottom_shape(self):
        """A broader trough must never measure as less round than a narrower one."""
        fractions = [
            component(
                CUP,
                GENERATOR.cup_handle(roundness=exponent),
                "bottom_roundness",
            )
            for exponent in (0.5, 2.0, 4.0)
        ]
        assert fractions == sorted(fractions, reverse=True)

    def test_roundness_is_measured_on_where_price_settled(self):
        """Not on where it ticked in passing.

        A bar whose low dipped into the band on the way past did not spend time
        there. The measurement uses the close for exactly that reason.
        """
        found = CUP.detect(*_args(GENERATOR.cup_handle()))[0]
        measured = found.component("bottom_roundness").measurements
        assert 0.0 < measured["bottom_fraction"] <= 1.0

    def test_the_near_low_band_scales_with_the_cup(self):
        """A band fixed as a fraction of price measures depth, not roundness.

        Two cups with the same shape and different depths must report similar
        roundness; under a price-relative band the deeper one would report far
        less time near its low purely because its low is further away.
        """
        shallow = component(CUP, GENERATOR.cup_handle(depth=0.13), "bottom_roundness")
        deep = component(CUP, GENERATOR.cup_handle(depth=0.33), "bottom_roundness")
        assert abs(shallow - deep) < 20

    def test_an_incomplete_recovery_is_found_and_marked_down(self):
        """Not discarded. A right rim well below the left is a real structure
        that is not a cup, and saying so is more useful than silence."""
        found = CUP.detect(*_args(GENERATOR.incomplete_recovery()))
        assert found, "an incomplete recovery must be visible, not filtered away"
        assert found[0].component("rim_symmetry").score < 20
        assert best(CUP, GENERATOR.cup_handle()) > best(CUP, GENERATOR.incomplete_recovery())

    def test_a_cup_with_no_handle_is_not_a_cup_and_handle(self):
        assert not CUP.detect(*_args(GENERATOR.cup_without_handle()))

    def test_a_handle_deeper_than_a_third_of_the_cup_is_a_second_decline(self):
        shallow = component(CUP, GENERATOR.cup_handle(handle_depth_ratio=0.15), "handle_structure")
        deep = component(CUP, GENERATOR.cup_handle(handle_depth_ratio=0.70), "handle_structure")
        assert shallow > deep

    def test_the_defining_components_are_required(self):
        found = CUP.detect(*_args(GENERATOR.cup_handle()))[0]
        for name in ("cup_depth", "cup_duration", "bottom_roundness", "rim_symmetry"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_the_left_rim_does_not_slide_back_into_the_advance(self):
        """The regression that motivated the discovery rewrite.

        A cup forms after an advance, so the deepest low of the preceding two
        hundred sessions sits *before* the advance. Anchoring on that low, or
        pairing rims at a loose enough tolerance to reach it, describes a cup
        that spans the advance itself. The cup must begin at the rim.
        """
        series = GENERATOR.cup_handle()
        expected = int(series.truth["cup_start_index"])
        found = CUP.detect(*_args(series))[0]
        actual = next(
            i for i, b in enumerate(series.bars) if b.session_date == found.geometry.start_date
        )
        assert abs(actual - expected) < 10, "cup begins at the rim, not before the advance"

    def test_no_cup_in_a_random_walk(self):
        assert not CUP.detect(*_args(GENERATOR.random_walk(220)))

    def test_a_flat_base_reads_as_a_shallow_cup_and_scores_far_below_one(self):
        """A competing interpretation, recorded rather than suppressed.

        A shallow horizontal range with a drift at the end is geometrically a
        very shallow cup, and the depth component says so at close to zero. The
        reading is legitimate and the score is what separates it.
        """
        assert best(CUP, GENERATOR.cup_handle()) - best(CUP, GENERATOR.flat_base()) > 25


class TestHighTightFlag:
    def test_a_doubling_followed_by_a_shallow_pause_is_found(self):
        found = HTF.detect(*_args(GENERATOR.high_tight_flag()))
        assert found
        assert found[0].pattern_type is PatternType.HIGH_TIGHT_FLAG
        assert found[0].quality > 80

    def test_an_ordinary_bull_flag_is_not_a_high_tight_flag(self):
        """The requirement the brief states in so many words.

        The negative is a real bull flag -- the bull flag detector finds it --
        and the high tight flag detector must find nothing. Not a low score:
        nothing. A 25% advance is not a weak high tight flag.
        """
        series = GENERATOR.ordinary_bull_flag_for_htf()
        assert FLAG.detect(*_args(series)), "the negative must be a genuine bull flag"
        assert not HTF.detect(*_args(series))

    def test_magnitude_without_thrust_is_a_trend(self):
        """A 100% gain taken over a year is not a flagpole."""
        assert not HTF.detect(*_args(GENERATOR.slow_double()))

    def test_a_deep_correction_after_a_thrust_is_an_ordinary_flag(self):
        assert not HTF.detect(*_args(GENERATOR.deep_pause_after_thrust()))

    def test_the_magnitude_floor_is_structural_rather_than_scored(self):
        """Below the floor there is no instance at all.

        Scoring a 40% advance as a poor high tight flag would let a screen with
        a low quality threshold recover the ordinary flag universe by the back
        door. The gate belongs in discovery.

        The gate applies to the leg the detector *measures* -- swing low to
        peak -- not to the generator's parameter, which is measured from the end
        of the lead-in. A series drawn with a 60% advance whose lead-in dipped
        first contains a leg longer than 60%, and the detector is right to
        measure the leg. The invariant is therefore stated on the measurement.
        """
        floor = HTF.config.min_advance
        for advance in (0.20, 0.30, 0.45):
            assert not HTF.detect(*_args(GENERATOR.high_tight_flag(advance=advance)))
        for advance in (0.60, 0.90, 1.40):
            for instance in HTF.detect(*_args(GENERATOR.high_tight_flag(advance=advance))):
                measured = instance.component("advance_magnitude").measurements["gain_pct"]
                assert measured >= floor
        assert HTF.detect(*_args(GENERATOR.high_tight_flag(advance=0.90)))

    def test_a_gap_driven_advance_is_a_repricing(self):
        """Recorded on consistency, not on magnitude.

        The advance genuinely happened, so the headline magnitude is unchanged.
        What differs is how it was delivered, and that belongs in its own
        component with its own contradicting evidence.
        """
        clean = HTF.detect(*_args(GENERATOR.high_tight_flag()))[0]
        gappy = HTF.detect(*_args(GENERATOR.high_tight_flag(gap_share=0.6)))[0]
        assert clean.component("advance_magnitude").score == pytest.approx(
            gappy.component("advance_magnitude").score, abs=1.0
        )
        assert clean.component("advance_consistency").score > (
            gappy.component("advance_consistency").score + 20
        )
        assert gappy.component("advance_consistency").contradicting

    def test_extreme_move_risk_is_reported_not_acted_on(self):
        """Phase 4 hands the risk forward; sizing is a later phase's problem."""
        found = HTF.detect(*_args(GENERATOR.high_tight_flag(advance=1.6)))
        assert found
        risk = found[0].component("extreme_move_risk")
        assert risk.score < 60
        assert risk.contradicting

    def test_thin_liquidity_contradicts_rather_than_filters(self):
        """The detector reports structure. Tradability is the screen's call."""
        found = HTF.detect(*_args(GENERATOR.high_tight_flag()))[0]
        liquidity = found.component("liquidity")
        assert liquidity is not None
        assert liquidity.requirement is ComponentRequirement.OPTIONAL

    def test_the_defining_components_are_required(self):
        found = HTF.detect(*_args(GENERATOR.high_tight_flag()))[0]
        for name in ("advance_magnitude", "advance_speed", "consolidation_tightness"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_no_high_tight_flag_in_a_random_walk(self):
        assert not HTF.detect(*_args(GENERATOR.random_walk(220)))

    def test_a_parabolic_run_offers_no_pause(self):
        assert not HTF.detect(*_args(GENERATOR.parabolic()))


class TestCompetingInterpretations:
    def test_a_high_tight_flag_is_also_a_bull_flag_and_both_may_say_so(self):
        """Exclusivity is not forced. The structures genuinely nest: every high
        tight flag is a bull flag, and the reverse is what must not hold."""
        series = GENERATOR.high_tight_flag()
        assert HTF.detect(*_args(series))
        assert FLAG.detect(*_args(series)), "an extreme flag is still a flag"

    def test_the_containment_runs_one_way(self):
        """The asymmetry that keeps the family distinct."""
        htf_series = GENERATOR.high_tight_flag()
        flag_series = GENERATOR.ordinary_bull_flag_for_htf()
        assert HTF.detect(*_args(htf_series)) and FLAG.detect(*_args(htf_series))
        assert FLAG.detect(*_args(flag_series)) and not HTF.detect(*_args(flag_series))


class TestSharedInvariants:
    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_bars_past_the_boundary_are_refused(self, detector):
        series = GENERATOR.cup_handle()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            detector.detect(series.bars, series.bars[-5].session_date)

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_detection_is_reproducible(self, detector):
        for series in (GENERATOR.cup_handle(), GENERATOR.high_tight_flag()):
            first = detector.detect(*_args(series))
            second = detector.detect(list(series.bars), series.last_session)
            assert [p.to_payload() for p in first] == [p.to_payload() for p in second]

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_geometry_never_extends_past_the_boundary(self, detector):
        for series in (GENERATOR.cup_handle(), GENERATOR.high_tight_flag()):
            for instance in detector.detect(*_args(series)):
                assert instance.geometry.end_date <= instance.as_of_session

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_every_instance_reconciles_and_is_structurally_complete(self, detector):
        for series in (GENERATOR.cup_handle(), GENERATOR.high_tight_flag(), GENERATOR.flat_base()):
            for instance in detector.detect(*_args(series)):
                assert instance.reconciles
                assert instance.is_structurally_complete
                assert 0.0 <= instance.quality <= 100.0
                assert 0.0 < instance.evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_every_unavailable_component_states_a_reason(self, detector):
        for series in (GENERATOR.cup_handle(), GENERATOR.high_tight_flag()):
            for instance in detector.detect(*_args(series)):
                for name, reason in instance.coverage_gaps().items():
                    assert reason, f"{detector.name}.{name} unavailable without a reason"

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_the_contract_declares_required_and_optional_components(self, detector):
        contract = detector.contract
        assert contract.required_components
        assert contract.warmup_bars == detector.minimum_bars
        assert 0.0 <= contract.minimum_evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", (CUP, HTF), ids=lambda d: d.name)
    def test_no_detector_expresses_breakout_validity(self, detector):
        """Phase 4 observes that a line was crossed and stops there."""
        for series in (GENERATOR.cup_handle(), GENERATOR.high_tight_flag()):
            for instance in detector.detect(*_args(series)):
                assert instance.state is not PatternState.INVALIDATED or instance.state.is_terminal
                label = str(instance.state)
                assert "confirmed" not in label or label.endswith("unconfirmed")
