"""Base on Base, Tight Consolidation and Breakout Retest.

The last three families, and the three the brief warns about most specifically.

* **Base on Base** measures a relationship rather than a shape, and its
  defining parameter rewards the *absence* of progress. Its negative is the
  stair-step: two bases separated by a real advance, which is both more common
  and a weaker claim.
* **Tight Consolidation** is the family most easily degraded into "this stock is
  not moving". Its negative is the chronically quiet instrument, and every
  measurement in the detector is relative to the instrument's own past so that
  the negative scores nothing.
* **Breakout Retest** is where the Phase 4 boundary is most tempting to cross.
  Its tests check that the detector reports geometry and stops.

**A measured limitation, stated rather than tuned away.** The retest family
fires on roughly 70% of 250-session random walks with a mean quality near 74.
That is a property of the pattern rather than a defect in the code: every
ingredient — a level, a close above it, a return to it — is a single event that
noise supplies readily, where a cup or a high tight flag requires a sustained
shape that noise does not. The rate is asserted below as a recorded fact so a
regression in either direction is visible, and the brief's instruction not to
tune thresholds to obtain a desired false-positive rate is why it has not been
reduced by moving numbers.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import PatternType
from tradeit.patterns.base import ComponentRequirement, PatternState
from tradeit.patterns.detectors import (
    BaseOnBaseDetector,
    BreakoutRetestDetector,
    TightConsolidationDetector,
)
from tradeit.patterns.lifecycle import FAMILY_CONSTRAINTS
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
BOB = BaseOnBaseDetector()
TIGHT = TightConsolidationDetector()
RETEST = BreakoutRetestDetector()


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


def noise_rate(detector, *, trials: int = 100, sessions: int = 250) -> float:
    fired = sum(
        1
        for seed in range(trials)
        if detector.detect(*_args(GENERATOR.random_walk(sessions, seed=seed)))
    )
    return fired / trials


class TestBaseOnBase:
    def test_two_bases_at_a_level_after_an_advance_are_found(self):
        found = BOB.detect(*_args(GENERATOR.base_on_base()))
        assert found
        assert found[0].pattern_type is PatternType.BASE_ON_BASE
        assert found[0].quality > 80

    def test_a_stair_step_is_not_base_on_base(self):
        """The defining negative. Two bases separated by a 30% advance are two
        bases in a rising sequence, which is a different and weaker claim."""
        assert not BOB.detect(*_args(GENERATOR.stair_step_bases()))

    def test_the_absence_of_progress_is_what_is_being_scored(self):
        """Quality falls monotonically as the ceiling advances.

        Every other detector in the package rewards more movement. This one
        rewards less, because the claim is that supply was absorbed twice at one
        price.
        """
        scores = [best(BOB, GENERATOR.base_on_base(ceiling_advance=a)) for a in (0.03, 0.05, 0.08)]
        assert scores == sorted(scores, reverse=True)
        assert all(score > 0 for score in scores)

    def test_identical_ceilings_describe_one_base_not_two(self):
        """A consequence of the touch tolerance, and the right answer.

        When the second ceiling sits inside the tolerance that defines "the same
        level", the earliest-at-level walk-back reaches through both and finds a
        single long base. Two bases at literally the same price with no
        transition between them *are* one base.
        """
        for advance in (0.0, 0.01, 0.02):
            assert not BOB.detect(*_args(GENERATOR.base_on_base(ceiling_advance=advance)))

    def test_a_long_single_base_does_not_become_two(self):
        assert not BOB.detect(*_args(GENERATOR.single_long_base()))
        assert not BOB.detect(*_args(GENERATOR.flat_base()))

    def test_a_lower_second_base_is_the_opposite_claim(self):
        assert not BOB.detect(*_args(GENERATOR.descending_bases()))

    def test_tightening_is_scored_and_optional(self):
        """A second base no tighter than the first is still base-on-base."""
        scores = [
            component(BOB, GENERATOR.base_on_base(second_depth=depth), "tightening")
            for depth in (0.09, 0.14, 0.20)
        ]
        assert scores == sorted(scores, reverse=True)
        found = BOB.detect(*_args(GENERATOR.base_on_base(second_depth=0.20)))
        assert found, "a second base that did not tighten is still the pattern"
        assert found[0].component("tightening").requirement is ComponentRequirement.OPTIONAL

    def test_the_relationship_components_are_required(self):
        found = BOB.detect(*_args(GENERATOR.base_on_base()))[0]
        for name in ("prior_trend", "first_base", "second_base", "ceiling_progression"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_noise_rate_is_recorded(self):
        assert noise_rate(BOB) < 0.25


class TestTightConsolidation:
    def test_a_window_that_tightened_is_found(self):
        found = TIGHT.detect(*_args(GENERATOR.tight_consolidation()))
        assert found
        assert found[0].pattern_type is PatternType.TIGHT_CONSOLIDATION
        assert found[0].quality > 60

    def test_a_chronically_quiet_instrument_is_not_consolidating(self):
        """The requirement the brief states in so many words.

        The same absolute range with nothing to have contracted from. Under an
        absolute threshold this scores identically to the real thing, which is
        exactly how the family becomes a dumping ground for every sideways
        stock. Every measurement is relative, so it scores nothing.
        """
        assert not TIGHT.detect(*_args(GENERATOR.chronically_quiet()))
        assert not TIGHT.detect(*_args(GENERATOR.quiet_drift()))
        assert not TIGHT.detect(*_args(GENERATOR.low_volume_noise()))

    def test_a_tight_window_after_a_decline_is_a_different_structure(self):
        assert not TIGHT.detect(*_args(GENERATOR.tight_after_decline()))

    def test_quality_tracks_how_much_the_range_contracted(self):
        scores = [
            best(TIGHT, GENERATOR.tight_consolidation(depth=depth))
            for depth in (0.02, 0.035, 0.05, 0.07)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_contraction_is_measured_against_the_instrument_not_a_constant(self):
        """The same tight window against a wider prior range scores higher."""
        scores = [
            component(
                TIGHT,
                GENERATOR.tight_consolidation(reference_depth=reference),
                "range_contraction",
            )
            for reference in (0.05, 0.09, 0.14)
        ]
        assert scores == sorted(scores)

    def test_volatility_is_measured_on_each_window_own_bars(self):
        """Not from the smoothed ATR series.

        ATR carries a fourteen-session memory and these windows are five to
        twenty-five sessions long, so ATR *inside* a tight window is mostly the
        volatility that preceded it. Measured that way the component reported no
        contraction for a window whose bars had genuinely halved in range.
        """
        found = TIGHT.detect(*_args(GENERATOR.tight_consolidation()))[0]
        measured = found.component("volatility_contraction").measurements
        assert measured["range_ratio"] < 1.0
        assert measured["window_mean_range_pct"] < measured["reference_mean_range_pct"]

    def test_volume_dryup_is_scored(self):
        scores = [
            component(TIGHT, GENERATOR.tight_consolidation(volume_ratio=v), "volume_dryup")
            for v in (0.3, 0.6, 1.0)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_the_relative_measurements_are_required(self):
        found = TIGHT.detect(*_args(GENERATOR.tight_consolidation()))[0]
        for name in ("range_contraction", "volatility_contraction", "prior_trend"):
            assert found.component(name).requirement is ComponentRequirement.REQUIRED

    def test_the_claim_is_labelled_as_the_weaker_one(self):
        found = TIGHT.detect(*_args(GENERATOR.tight_consolidation()))[0]
        assert any("short-horizon" in note for note in found.notes)

    def test_noise_rate_is_recorded(self):
        assert noise_rate(TIGHT) < 0.30


class TestBreakoutRetest:
    def test_a_level_crossed_and_revisited_is_found(self):
        found = RETEST.detect(*_args(GENERATOR.breakout_retest()))
        assert found
        assert found[0].pattern_type is PatternType.BREAKOUT_RETEST
        assert found[0].quality > 80

    def test_a_breakout_that_ran_away_is_not_this_pattern(self):
        assert not RETEST.detect(*_args(GENERATOR.breakout_no_retest()))

    def test_a_single_poke_above_the_line_is_not_a_breakout(self):
        """You retest a breakout, and one close above a line is not one."""
        assert not RETEST.detect(*_args(GENERATOR.breakout_retest(break_strength=0.012)))

    def test_a_failed_retest_is_found_and_marked_invalidated(self):
        """Found, not hidden. The structure was real and then failed, and the
        state machine is what says so -- suppressing it would mean every
        measurement of how often these fail came from a population that had
        erased its own failures."""
        found = RETEST.detect(*_args(GENERATOR.failed_retest()))
        assert found
        assert all(instance.state is PatternState.INVALIDATED for instance in found)
        # The division of labour, and the provisional tail is what makes it
        # visible. `hold_behaviour` is measured on confirmed structure only, so
        # it reports the sessions that did hold -- in the sixties, and honestly
        # so, with one instance still showing a close above its line. The state
        # reads the last bar, tail included, and says the structure has failed.
        # Measurement describes the window; state describes now.
        last_close = float(GENERATOR.failed_retest().bars[-1].close)
        for instance in found:
            level = instance.geometry.resistance
            assert level is not None
            assert last_close < level.level * (1.0 - RETEST.config.fail_tolerance)

    def test_proximity_is_the_distinguishing_measurement(self):
        scores = [
            component(RETEST, GENERATOR.breakout_retest(retest_overshoot=o), "retest_proximity")
            for o in (0.0, 0.02, 0.04)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_the_level_is_built_only_from_data_before_the_break(self):
        """Otherwise the retest helps define the level it is retesting.

        The boundary's touches must all precede the break, and a line fitted
        through the pullback would necessarily pass near it -- flattering the
        one measurement that carries the family.
        """
        found = RETEST.detect(*_args(GENERATOR.breakout_retest()))[0]
        level = found.geometry.resistance
        assert level is not None
        break_date = found.geometry.key_points["break"].session_date
        assert all(point.session_date < break_date for point in level.touches)

    def test_no_verdict_about_the_breakout_is_expressed(self):
        """The Phase 4 boundary, checked on the family most likely to cross it."""
        for series in (GENERATOR.breakout_retest(), GENERATOR.failed_retest()):
            for instance in RETEST.detect(*_args(series)):
                label = str(instance.state)
                assert "confirmed" not in label or label.endswith("unconfirmed")
                assert any("Phase 5" in note for note in instance.notes)
                assert "confirmed breakout is\nnot" not in instance.explain()

    def test_the_family_never_occupies_near_breakout(self):
        """It begins life already resolved. The lifecycle carries the constraint
        and the detector's own state classification honours it."""
        permitted = FAMILY_CONSTRAINTS[PatternType.BREAKOUT_RETEST]
        assert PatternState.NEAR_BREAKOUT not in permitted
        for series in (
            GENERATOR.breakout_retest(),
            GENERATOR.failed_retest(),
            GENERATOR.breakout_retest(retest_overshoot=0.02),
        ):
            for instance in RETEST.detect(*_args(series)):
                assert instance.state in permitted

    def test_the_noise_rate_is_high_and_that_is_reported_not_tuned(self):
        """The measured limitation of this family, asserted as a fact.

        Roughly 70% of 250-session random walks contain something this detector
        calls a breakout retest, because every ingredient is a single event that
        noise supplies readily. The bound is wide on both sides deliberately: it
        exists to make a regression visible, not to encode a target, and the
        brief forbids moving thresholds to obtain a nicer number.
        """
        rate = noise_rate(RETEST)
        assert 0.55 < rate < 0.85, (
            f"random-walk detection rate {rate:.0%} has moved; this family's rate is a "
            "known limitation to be reported, and a change here needs explaining rather "
            "than tuning away"
        )

    def test_it_is_the_noisiest_family_by_a_wide_margin(self):
        """The comparison that makes the limitation legible."""
        assert noise_rate(RETEST) > noise_rate(BOB) + 0.3
        assert noise_rate(RETEST) > noise_rate(TIGHT) + 0.3


class TestSharedInvariants:
    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_bars_past_the_boundary_are_refused(self, detector):
        series = GENERATOR.base_on_base()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            detector.detect(series.bars, series.bars[-5].session_date)

    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_detection_is_reproducible(self, detector):
        for series in (
            GENERATOR.base_on_base(),
            GENERATOR.tight_consolidation(),
            GENERATOR.breakout_retest(),
        ):
            first = detector.detect(*_args(series))
            second = detector.detect(list(series.bars), series.last_session)
            assert [p.to_payload() for p in first] == [p.to_payload() for p in second]

    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_geometry_never_extends_past_the_boundary(self, detector):
        for series in (
            GENERATOR.base_on_base(),
            GENERATOR.tight_consolidation(),
            GENERATOR.breakout_retest(),
        ):
            for instance in detector.detect(*_args(series)):
                assert instance.geometry.end_date <= instance.as_of_session

    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_every_instance_reconciles_and_is_structurally_complete(self, detector):
        for series in (
            GENERATOR.base_on_base(),
            GENERATOR.tight_consolidation(),
            GENERATOR.breakout_retest(),
            GENERATOR.failed_retest(),
        ):
            for instance in detector.detect(*_args(series)):
                assert instance.reconciles
                assert instance.is_structurally_complete
                assert 0.0 <= instance.quality <= 100.0
                assert 0.0 < instance.evidence_coverage <= 100.0

    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_every_unavailable_component_states_a_reason(self, detector):
        for series in (GENERATOR.base_on_base(), GENERATOR.breakout_retest()):
            for instance in detector.detect(*_args(series)):
                for name, reason in instance.coverage_gaps().items():
                    assert reason, f"{detector.name}.{name} unavailable without a reason"

    @pytest.mark.parametrize("detector", (BOB, TIGHT, RETEST), ids=lambda d: d.name)
    def test_the_contract_declares_required_and_optional_components(self, detector):
        contract = detector.contract
        assert contract.required_components
        assert contract.warmup_bars == detector.minimum_bars
        assert 0.0 <= contract.minimum_evidence_coverage <= 100.0
