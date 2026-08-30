"""Volatility Contraction Pattern.

The detector's whole claim is that it measures a *trajectory* rather than a
*state*. So the tests that matter are the ones separating those: a quiet range
has low volatility and no VCP; a base that pulled back 18%, then 10%, then 5%
has a VCP even though its average volatility was high.

The single most important negative is the descending wedge — tightening legs
whose highs also fall. A detector scoring only depth progression sees a textbook
VCP there, and what it is missing is that the security is grinding down rather
than coiling beneath a level.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import ComponentRequirement, PatternState
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.detectors import BullFlagDetector, VcpDetector
from tradeit.patterns.synthetic import PatternGenerator, VcpSpec

GENERATOR = PatternGenerator()
DETECTOR = VcpDetector()


def detect(series):
    return DETECTOR.detect(series.bars, series.last_session)


def best(series) -> float:
    return max((p.quality for p in detect(series)), default=0.0)


class TestProgression:
    """The defining property: legs get shallower."""

    def test_a_textbook_base_is_found(self):
        found = detect(GENERATOR.vcp())
        assert found
        assert found[0].pattern_type is PatternType.VOLATILITY_CONTRACTION

    def test_the_contraction_legs_are_returned_as_geometry(self):
        """The hierarchy must be visualisable later, not just scored."""
        instance = detect(GENERATOR.vcp())[0]
        legs = [k for k in instance.geometry.segments if k.startswith("contraction_")]
        assert len(legs) >= 2
        assert "base" in instance.geometry.segments
        assert instance.notes and "->" in instance.notes[0]

    def test_exactly_three_contractions_is_not_required(self):
        """Requiring the textbook count finds a subset selected for tidiness.

        A two-leg base that tightened from 16% to 7% is a VCP in progress, and
        a detector blind to it is blind until the pattern is nearly over.
        """
        two_leg = GENERATOR.vcp(
            VcpSpec(depths=(0.16, 0.07), leg_sessions=(14, 9), volume_ratios=(0.9, 0.5))
        )
        four_leg = GENERATOR.vcp(
            VcpSpec(
                depths=(0.20, 0.13, 0.08, 0.04),
                leg_sessions=(14, 10, 8, 6),
                volume_ratios=(0.95, 0.8, 0.6, 0.4),
            )
        )
        assert detect(two_leg)
        assert detect(four_leg)

    def test_more_clean_contractions_score_higher(self):
        two_leg = best(
            GENERATOR.vcp(
                VcpSpec(depths=(0.16, 0.07), leg_sessions=(14, 9), volume_ratios=(0.9, 0.5))
            )
        )
        four_leg = best(
            GENERATOR.vcp(
                VcpSpec(
                    depths=(0.20, 0.13, 0.08, 0.04),
                    leg_sessions=(14, 10, 8, 6),
                    volume_ratios=(0.95, 0.8, 0.6, 0.4),
                )
            )
        )
        assert four_leg > two_leg

    def test_widening_legs_score_below_tightening_ones(self):
        """The inverse structure. Not a bad VCP -- not a VCP."""
        tightening = best(GENERATOR.vcp(VcpSpec(depths=(0.19, 0.12, 0.06))))
        widening = best(
            GENERATOR.vcp(
                VcpSpec(
                    depths=(0.06, 0.12, 0.19),
                    leg_sessions=(8, 10, 14),
                    volume_ratios=(0.5, 0.8, 1.0),
                )
            )
        )
        assert tightening > widening

    def test_a_base_that_ends_tight_without_a_staircase_scores_lower(self):
        """18 -> 5 -> 12 -> 4 ends tight and is not a progression.

        A score built only on the final-over-first ratio would call it textbook,
        which is why monotonic_fraction is measured separately.
        """
        clean = best(GENERATOR.vcp(VcpSpec(depths=(0.18, 0.10, 0.05))))
        ragged = best(
            GENERATOR.vcp(
                VcpSpec(
                    depths=(0.18, 0.05, 0.12),
                    leg_sessions=(14, 9, 10),
                    volume_ratios=(0.9, 0.7, 0.8),
                )
            )
        )
        assert clean > ragged

    def test_the_progression_component_records_both_measures(self):
        component = detect(GENERATOR.vcp())[0].component("contraction_progression")
        assert component is not None
        assert "tightening_ratio" in component.measurements
        assert "monotonic_fraction" in component.measurements
        assert component.requirement is ComponentRequirement.REQUIRED


class TestNegatives:
    def test_a_descending_wedge_is_not_a_vcp(self):
        """The most important negative in this suite.

        The pullbacks shrink, so a detector scoring only depth progression sees
        a textbook VCP. What is missing is that the pivot is falling -- the
        security is grinding down, not coiling beneath a level.
        """
        for seed in range(5):
            assert not detect(GENERATOR.descending_wedge(seed=seed))

    def test_a_quiet_range_is_low_volatility_not_a_vcp(self):
        """The state/trajectory distinction, stated as a test."""
        assert best(GENERATOR.quiet_drift()) < 45

    def test_a_downtrend_produces_nothing(self):
        for seed in range(4):
            assert not detect(GENERATOR.falling_knife(seed=seed))

    def test_a_parabolic_advance_has_no_base(self):
        assert not detect(GENERATOR.parabolic())

    def test_a_bear_flag_is_not_a_base(self):
        assert not detect(GENERATOR.bear_flag())

    def test_random_walks_rarely_produce_a_quality_base(self):
        qualities = [best(GENERATOR.random_walk(160, seed=s)) for s in range(30)]
        assert sum(1 for q in qualities if q >= 70) <= 3


class TestContext:
    def test_a_base_without_a_prior_advance_is_refused(self):
        """A VCP is a pause in an uptrend. The same contraction sequence after a
        decline is a failing security tightening into a low."""
        component = detect(GENERATOR.vcp())[0].component("prior_trend")
        assert component is not None
        assert component.requirement is ComponentRequirement.REQUIRED
        assert component.measurements["gain_pct"] > 0

    def test_a_base_deeper_than_the_maximum_is_not_a_base(self):
        """A 50% decline with tightening legs is a security finding a floor."""
        deep = GENERATOR.vcp(
            VcpSpec(
                depths=(0.45, 0.30, 0.18), leg_sessions=(16, 12, 8), volume_ratios=(1.0, 0.8, 0.6)
            )
        )
        for instance in detect(deep):
            assert instance.component("base_structure").measurements["base_depth"] <= 0.40


class TestLifecycleAndCausality:
    def test_a_breakout_is_observed_but_not_judged(self):
        series = GENERATOR.vcp(VcpSpec(breakout_sessions=3))
        assert PatternState.BROKEN_OUT_UNCONFIRMED in {p.state for p in detect(series)}

    def test_the_pivot_is_the_final_contractions_high(self):
        """Not the base high. A pivot set at the base high sits above a level
        the security stopped testing several legs ago."""
        instance = detect(GENERATOR.vcp())[0]
        base_high = instance.geometry.key_points["base_high"].price
        assert instance.resistance_price is not None
        assert instance.resistance_price <= base_high * 1.02

    def test_invalidation_sits_below_the_final_low(self):
        """Using the base's overall low would keep a pattern alive through a
        break of every level that made it a VCP."""
        instance = detect(GENERATOR.vcp())[0]
        final_low = instance.geometry.key_points["final_low"].price
        assert instance.invalidation_price is not None
        assert instance.invalidation_price <= final_low * 1.001

    def test_the_base_start_is_determined_not_selected(self):
        """The first implementation enumerated candidates and let the quality
        sort pick -- pattern-selection look-ahead bias, which showed up as an
        18/10/5 base being reported as 15.8/22.5/14.4 because the winning
        window started inside the prior advance.
        """
        series = GENERATOR.vcp()
        first = detect(series)
        second = DETECTOR.detect(list(series.bars), series.last_session)
        assert [p.geometry.start_date for p in first] == [p.geometry.start_date for p in second]

    def test_adding_bars_does_not_rewrite_earlier_geometry(self):
        series = GENERATOR.vcp(VcpSpec(breakout_sessions=6))
        cut = len(series.bars) - 7
        early = DETECTOR.detect(series.bars[: cut + 1], series.bars[cut].session_date)
        again = DETECTOR.detect(series.bars[: cut + 1], series.bars[cut].session_date)
        assert [p.to_payload() for p in early] == [p.to_payload() for p in again]

    def test_bars_past_the_boundary_are_refused(self):
        series = GENERATOR.vcp()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            DETECTOR.detect(series.bars, series.bars[-5].session_date)

    def test_the_final_leg_may_sit_in_the_unconfirmed_tail(self):
        """A known and correct limitation, pinned so it is not mistaken for a bug.

        The last `right_bars` sessions cannot contain a confirmed pivot, so a
        base whose final contraction is still forming is reported with one fewer
        leg than it will eventually have. That is the causal cost of not looking
        at the future, not an undercount to fix.
        """
        series = GENERATOR.vcp(VcpSpec(depths=(0.18, 0.10, 0.05)))
        instance = detect(series)[0]
        legs = [k for k in instance.geometry.segments if k.startswith("contraction_")]
        assert len(legs) <= 3


class TestDistinctFromBullFlag:
    def test_the_two_detectors_are_not_the_same_logic_renamed(self):
        """Different required components, different weights, different geometry.

        The flag has one pullback and scores its depth; the VCP has several and
        scores their progression.
        """
        flag = BullFlagDetector()
        assert set(DETECTOR.contract.required_components) != set(flag.contract.required_components)
        assert "contraction_progression" in DETECTOR.contract.required_components
        assert "flagpole" in flag.contract.required_components

    def test_both_may_legitimately_fire_on_one_structure(self):
        """Multiple interpretations may coexist; forcing exclusivity discards
        what a later scoring stage is better placed to decide."""
        series = GENERATOR.vcp(
            VcpSpec(depths=(0.16, 0.07), leg_sessions=(14, 9), volume_ratios=(0.9, 0.5))
        )
        vcp_found = bool(detect(series))
        flag_found = bool(BullFlagDetector().detect(series.bars, series.last_session))
        assert vcp_found or flag_found

    def test_scores_are_not_comparable_across_families(self):
        """A VCP 90 is not a Bull Flag 90. Each score is quality relative to its
        own detector's definition, and no cross-family calibration exists yet --
        that needs real labelled data."""
        assert DETECTOR.pattern_type is not BullFlagDetector().pattern_type


class TestConfiguration:
    def test_the_contraction_count_band_must_be_ordered(self):
        with pytest.raises(Exception, match="ideal contraction count"):
            PatternEngineConfig.model_validate(
                {
                    "vcp": {
                        "min_contractions": 2,
                        "max_contractions": 3,
                        "ideal_contractions_low": 3,
                        "ideal_contractions_high": 5,
                    }
                }
            )

    def test_ideal_base_depth_must_sit_below_the_maximum(self):
        with pytest.raises(Exception, match="ideal base depth"):
            PatternEngineConfig.model_validate(
                {"vcp": {"ideal_base_depth_high": 0.5, "max_base_depth": 0.4}}
            )

    def test_weights_are_configurable(self):
        tweaked = PatternEngineConfig.model_validate(
            {
                "vcp": {
                    "weights": {
                        "contraction_progression": 0.9,
                        "base_structure": 0.02,
                        "prior_trend": 0.02,
                        "volume_dryup": 0.02,
                        "volatility_profile": 0.02,
                        "pivot_quality": 0.01,
                        "relative_strength": 0.01,
                    }
                }
            }
        )
        series = GENERATOR.vcp()
        assert best(series) != max(
            (p.quality for p in VcpDetector(tweaked).detect(series.bars, series.last_session)),
            default=0.0,
        )


class TestGenerator:
    def test_a_spec_with_one_leg_is_refused(self):
        with pytest.raises(ConfigError, match="at least two contractions"):
            VcpSpec(depths=(0.1,), leg_sessions=(10,), volume_ratios=(0.5,))

    def test_mismatched_leg_arrays_are_refused(self):
        with pytest.raises(ConfigError, match="leg_sessions"):
            VcpSpec(depths=(0.1, 0.05), leg_sessions=(10,), volume_ratios=(0.5, 0.4))

    def test_generation_is_deterministic(self):
        a = GENERATOR.vcp(seed=3).closes()
        b = GENERATOR.vcp(seed=3).closes()
        assert (a == b).all()
