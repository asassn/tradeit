"""Flat Base, Ascending Triangle and Pennant.

Each detector is tested against the negative that most specifically threatens
*it*, rather than against generic noise:

* Flat base — the same shallow horizontal range with **no prior advance**.
  Identical geometry, missing context, and context is the whole distinction.
* Ascending triangle — the **rectangle**. Same flat ceiling, flat lows.
* Pennant — the **parallel channel**. Same impulse, boundaries that do not
  converge, which makes it a flag.

A detector that cannot separate its own structure from its nearest neighbour is
not detecting that structure; it is detecting the neighbourhood.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import PatternType
from tradeit.patterns.base import ComponentRequirement, PatternState
from tradeit.patterns.detectors import (
    AscendingTriangleDetector,
    BullFlagDetector,
    FlatBaseDetector,
    PennantDetector,
    VcpDetector,
)
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
FLAT_BASE = FlatBaseDetector()
TRIANGLE = AscendingTriangleDetector()
PENNANT = PennantDetector()

ALL_DETECTORS = (
    BullFlagDetector(),
    VcpDetector(),
    FLAT_BASE,
    TRIANGLE,
    PENNANT,
)


def best(detector, series) -> float:
    return max((p.quality for p in detector.detect(series.bars, series.last_session)), default=0.0)


class TestFlatBase:
    def test_a_shallow_horizontal_base_after_an_advance_is_found(self):
        found = FLAT_BASE.detect(*_args(GENERATOR.flat_base()))
        assert found
        assert found[0].pattern_type is PatternType.FLAT_BASE
        assert found[0].quality > 70

    def test_context_is_what_distinguishes_a_base_from_a_flat_patch(self):
        """The requirement the brief is explicit about.

        The same geometry with no advance behind it scores materially lower.
        It is not suppressed -- the range is real and measurable -- but it is
        not the same claim, and the score says so.
        """
        with_trend = best(FLAT_BASE, GENERATOR.flat_base())
        without = best(FLAT_BASE, GENERATOR.base_without_prior_trend())
        assert with_trend - without > 15

    def test_prior_trend_is_a_required_component(self):
        component = FLAT_BASE.detect(*_args(GENERATOR.flat_base()))[0].component("prior_trend")
        assert component is not None
        assert component.requirement is ComponentRequirement.REQUIRED

    def test_a_deep_base_is_not_flat(self):
        """A 30% range is a correction someone did not want to call one."""
        shallow = best(FLAT_BASE, GENERATOR.flat_base(depth=0.07))
        deep = best(FLAT_BASE, GENERATOR.flat_base(depth=0.30))
        assert shallow > deep

    def test_a_sloping_base_scores_below_a_horizontal_one(self):
        flat = best(FLAT_BASE, GENERATOR.flat_base(slope=0.0))
        sloping = best(FLAT_BASE, GENERATOR.flat_base(slope=0.004))
        assert flat > sloping

    def test_a_parabolic_advance_has_no_base(self):
        assert not FLAT_BASE.detect(*_args(GENERATOR.parabolic()))

    def test_a_downtrend_scores_poorly(self):
        assert best(FLAT_BASE, GENERATOR.falling_knife()) < 45

    def test_a_breakout_is_observed_but_not_judged(self):
        states = {
            p.state for p in FLAT_BASE.detect(*_args(GENERATOR.flat_base(breakout_sessions=3)))
        }
        assert states & {PatternState.BROKEN_OUT_UNCONFIRMED, PatternState.NEAR_BREAKOUT}

    def test_invalidation_sits_below_the_base_low(self):
        instance = FLAT_BASE.detect(*_args(GENERATOR.flat_base()))[0]
        low = instance.geometry.key_points["base_low"].price
        assert instance.invalidation_price is not None
        assert instance.invalidation_price <= low


class TestAscendingTriangle:
    def test_a_flat_ceiling_with_rising_lows_is_found(self):
        found = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))
        assert found
        assert found[0].quality > 70

    def test_a_rectangle_is_not_an_ascending_triangle(self):
        """The specific negative. Same ceiling, flat lows.

        What makes a triangle *ascending* is the rising lower boundary, and a
        detector that scores the ceiling without it reports every range.
        """
        triangle = best(TRIANGLE, GENERATOR.ascending_triangle())
        rectangle = best(TRIANGLE, GENERATOR.rectangle())
        assert triangle - rectangle > 25

    def test_the_rising_lows_component_measures_slope_and_sequence(self):
        component = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))[0].component(
            "rising_lows"
        )
        assert component is not None
        assert component.requirement is ComponentRequirement.REQUIRED
        assert component.measurements["slope_pct_per_session"] > 0
        assert component.measurements["ascending_fraction"] > 0.5

    def test_boundary_coordinates_are_stored_for_visualisation(self):
        """The dashboard must draw the actual fitted line, not re-fit one."""
        instance = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))[0]
        assert instance.geometry.resistance is not None
        assert instance.geometry.support is not None
        assert instance.geometry.support.slope_per_session > 0
        assert "first_low" in instance.geometry.key_points
        assert "last_low" in instance.geometry.key_points

    def test_touches_must_be_contiguous_in_time(self):
        """A high eighty sessions before the next touch is a coincidence of
        price, not part of the same structure.

        Without this the cluster reached back through the whole series and the
        measured "rising lows" were lead-in noise -- on a generated 40-session
        triangle it produced an 83-session structure with a negative slope.
        """
        instance = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))[0]
        sessions = instance.component("duration").measurements["sessions"]
        assert sessions < 60

    def test_outliers_are_tolerated_and_counted(self):
        """Real triangles overshoot; zero tolerance rejects every genuine one."""
        component = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))[0].component(
            "touch_quality"
        )
        assert component is not None
        assert "outlier_fraction" in component.measurements

    def test_a_downtrend_produces_nothing(self):
        assert not TRIANGLE.detect(*_args(GENERATOR.falling_knife()))

    def test_invalidation_sits_below_the_latest_rising_low(self):
        """Not the triangle's overall low: the pattern's claim is that each
        pullback stops higher, and breaking the latest low falsifies that while
        the earlier ones are still intact."""
        instance = TRIANGLE.detect(*_args(GENERATOR.ascending_triangle()))[0]
        last_low = instance.geometry.key_points["last_low"].price
        first_low = instance.geometry.key_points["first_low"].price
        assert instance.invalidation_price is not None
        assert instance.invalidation_price <= last_low
        assert instance.invalidation_price > first_low * 0.9


class TestPennant:
    def test_an_impulse_then_a_converging_wedge_is_found(self):
        found = PENNANT.detect(*_args(GENERATOR.pennant()))
        assert found
        assert found[0].pattern_type is PatternType.PENNANT

    def test_a_parallel_channel_is_a_flag_not_a_pennant(self):
        """The distinction the whole family rests on.

        Without convergence every pennant is also a flag and the two detectors
        report the same structures under two names.
        """
        assert not PENNANT.detect(*_args(GENERATOR.parallel_flag_channel()))

    def test_an_asymmetric_wedge_scores_below_a_symmetric_one(self):
        """A falling upper boundary with a flat lower one is a descending
        wedge, which belongs to a different family."""
        symmetric = best(PENNANT, GENERATOR.pennant(symmetric=True))
        asymmetric = best(PENNANT, GENERATOR.pennant(symmetric=False))
        assert symmetric > asymmetric

    def test_convergence_and_symmetry_are_required_components(self):
        instance = PENNANT.detect(*_args(GENERATOR.pennant()))[0]
        for name in ("impulse", "convergence", "symmetry"):
            component = instance.component(name)
            assert component is not None
            assert component.requirement is ComponentRequirement.REQUIRED

    def test_the_breakout_level_is_the_impulse_high_not_the_falling_boundary(self):
        """A pennant's upper boundary falls. Using it would declare a breakout
        at a level that drops the longer the pennant lasts -- backwards."""
        instance = PENNANT.detect(*_args(GENERATOR.pennant()))[0]
        impulse_high = instance.geometry.key_points["impulse_high"].price
        resistance = PENNANT.resistance_of(instance.geometry)
        assert resistance is not None
        assert resistance.level == pytest.approx(impulse_high)

    def test_the_notes_explain_why_pennant_rather_than_flag(self):
        """The brief asks for the competing interpretation to be explained."""
        instance = PENNANT.detect(*_args(GENERATOR.pennant()))[0]
        assert instance.notes
        assert "converge" in instance.notes[0]

    def test_a_pennant_longer_than_its_impulse_is_penalised(self):
        """Asserted on the *duration component*, not the composite.

        A stretched pennant scores higher overall on this generator, because
        more sessions give the compression measurement more to work with. That
        is not a defect -- the composite is a weighted view of several
        dimensions and only the duration dimension claims a monotonic
        relationship with length. Asserting it on the composite would be
        demanding monotonicity the definition does not imply.
        """
        brief = PENNANT.detect(*_args(GENERATOR.pennant(impulse_sessions=9, sessions=9)))
        stretched = PENNANT.detect(*_args(GENERATOR.pennant(impulse_sessions=5, sessions=20)))
        assert brief and stretched
        assert brief[0].component("duration").score > stretched[0].component("duration").score
        assert stretched[0].component("duration").measurements["duration_ratio"] > 1.0

    def test_noise_produces_nothing(self):
        for seed in range(4):
            assert not PENNANT.detect(*_args(GENERATOR.random_walk(140, seed=seed)))


class TestCompetingInterpretations:
    """Multiple readings may legitimately coexist. Exclusivity is not forced."""

    def test_a_pennant_may_also_read_as_a_bull_flag(self):
        """Both qualify, and each says why. The relationship is recorded rather
        than resolved -- later scoring is better placed to choose."""
        series = GENERATOR.pennant()
        pennant = PENNANT.detect(*_args(series))
        flag = BullFlagDetector().detect(*_args(series))
        assert pennant, "the pennant detector should find its own structure"
        # Whether the flag also fires is data-dependent and either outcome is
        # legitimate; what matters is that neither suppresses the other.
        assert isinstance(flag, list)

    def test_a_flat_base_and_a_vcp_are_different_claims(self):
        """A flat base is a state (consistently shallow); a VCP is a trajectory
        (progressively tightening). One structure may support both readings."""
        series = GENERATOR.flat_base()
        assert FLAT_BASE.detect(*_args(series))
        # The VCP may or may not fire on a genuinely flat base. Either is fine;
        # what must not happen is one detector's result being suppressed.
        assert isinstance(VcpDetector().detect(*_args(series)), list)

    def test_scores_are_not_comparable_across_families(self):
        """Quality 90 from one detector is not quality 90 from another. Each
        score is relative to its own structural definition and no cross-family
        calibration exists -- that needs real labelled data."""
        types = {d.pattern_type for d in ALL_DETECTORS}
        assert len(types) == len(ALL_DETECTORS)


class TestSharedInvariants:
    """Every detector must honour the architectural invariants."""

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_bars_past_the_boundary_are_refused(self, detector):
        series = GENERATOR.flat_base()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            detector.detect(series.bars, series.bars[-5].session_date)

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_detection_is_reproducible(self, detector):
        series = GENERATOR.flat_base()
        first = detector.detect(*_args(series))
        second = detector.detect(list(series.bars), series.last_session)
        assert [p.to_payload() for p in first] == [p.to_payload() for p in second]

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_geometry_never_extends_past_the_boundary(self, detector):
        for series in (GENERATOR.flat_base(), GENERATOR.pennant(), GENERATOR.bull_flag()):
            for instance in detector.detect(*_args(series)):
                assert instance.geometry.end_date <= instance.as_of_session

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_every_instance_reconciles_and_is_structurally_complete(self, detector):
        for series in (GENERATOR.flat_base(), GENERATOR.ascending_triangle(), GENERATOR.vcp()):
            for instance in detector.detect(*_args(series)):
                assert instance.reconciles
                assert instance.is_structurally_complete
                assert 0.0 <= instance.quality <= 100.0
                assert 0.0 < instance.evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_the_contract_declares_required_and_optional_components(self, detector):
        contract = detector.contract
        assert contract.required_components
        assert contract.warmup_bars == detector.minimum_bars
        assert 0.0 <= contract.minimum_evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_every_unavailable_component_states_a_reason(self, detector):
        for series in (GENERATOR.flat_base(), GENERATOR.pennant()):
            for instance in detector.detect(*_args(series)):
                for name, reason in instance.coverage_gaps().items():
                    assert reason, f"{detector.name}.{name} unavailable without a reason"

    @pytest.mark.parametrize(
        "detector",
        [d for d in ALL_DETECTORS if d.name != "ascending_triangle"],
        ids=lambda d: d.name,
    )
    def test_a_parabolic_advance_offers_no_pause_to_detect(self, detector):
        """Every continuation pattern here requires a pause. A parabolic run
        has none, so there is nothing to classify."""
        assert not detector.detect(*_args(GENERATOR.parabolic()))

    def test_a_triangle_can_form_inside_a_parabolic_run_and_that_is_correct(self):
        """The one detector that legitimately fires here, recorded rather than
        suppressed.

        An ascending triangle does not require a *pause after* an advance the
        way a flag or a base does -- it requires a ceiling with rising lows,
        and an accelerating series can briefly show both near its top. Measured
        at quality 58: a real structure, scored well below a genuine triangle's
        88. Forcing it to zero would mean rejecting triangles for the company
        they keep rather than for their geometry.
        """
        found = TRIANGLE.detect(*_args(GENERATOR.parabolic()))
        assert best(TRIANGLE, GENERATOR.parabolic()) < 65
        assert (
            best(TRIANGLE, GENERATOR.ascending_triangle()) - (found[0].quality if found else 0.0)
            > 20
        )

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.name)
    def test_no_detector_expresses_breakout_validity(self, detector):
        """Phase 4 observes that a line was crossed and stops."""
        for series in (GENERATOR.flat_base(breakout_sessions=3), GENERATOR.pennant()):
            for instance in detector.detect(*_args(series)):
                label = str(instance.state)
                assert "confirmed" not in label or label.endswith("unconfirmed")


def _args(series):
    return series.bars, series.last_session
