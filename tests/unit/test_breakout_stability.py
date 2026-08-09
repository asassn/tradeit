"""Perturbation, monotonicity and the characterisation harness itself.

Item 35 asks that small irrelevant changes produce modest score changes and that
larger moves be explained. The distinction this file keeps is between the two
kinds of input:

* **noise** — price, volume and timing. Structurally irrelevant, so a big move
  is a defect;
* **definition** — the level, the gap, the close location, the retest depth.
  Moving these is *meant* to move the score, and a stability threshold applied
  to them would be asserting that the engine ignores its own definition.

The monotonicity tests apply only where the direction is definitional. A
detector's score need not rise with every input, and asserting that it does is
how a test ends up enforcing an accident.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tradeit.breakouts.perturbation import (
    LARGE_JUMP,
    PerturbationKind,
    add_price_noise,
    add_volume_noise,
    is_monotone_in,
    shift_timing,
    sweep,
)
from tradeit.breakouts.synthetic import (
    ADVERSARIAL_SCENARIOS,
    CONTROLLED_SCENARIOS,
    BreakoutGenerator,
    BreakoutSpec,
    all_scenarios,
)
from tradeit.breakouts.validation import (
    ScenarioOutcome,
    characterise,
    run_scenario,
    separation,
)

GENERATOR = BreakoutGenerator()

#: Kept small: these tests establish that the machinery is correct, not what the
#: distributions are. The real characterisation runs at higher trial counts via
#: ``scripts/phase5_report.py``.
TRIALS = 3

STABILITY_CASES = ("clean_breakout", "weak_breakout", "gap_breakout", "successful_retest")


@pytest.fixture(scope="module")
def scenarios():
    return {name: getattr(GENERATOR, name)(seed=0) for name in STABILITY_CASES}


class TestNoisePerturbations:
    @pytest.mark.parametrize("name", STABILITY_CASES)
    def test_price_noise_moves_the_score_modestly(self, name, scenarios):
        report = sweep(
            scenarios[name],
            PerturbationKind.PRICE_NOISE,
            [0.0005, 0.001, 0.002],
            trials=3,
            builder=GENERATOR.build,
        )
        assert report.median_delta < 5.0
        assert not report.unexplained_jumps

    @pytest.mark.parametrize("name", STABILITY_CASES)
    def test_timing_shifts_move_the_score_modestly(self, name, scenarios):
        """Changes where every window falls without changing the structure.
        Anything depending on window alignment rather than geometry shows here.
        """
        report = sweep(
            scenarios[name],
            PerturbationKind.TIMING,
            [1, 2, 3],
            trials=2,
            builder=GENERATOR.build,
        )
        assert report.median_delta < 5.0

    def test_volume_noise_response_is_a_slope_rather_than_a_cliff(self, scenarios):
        """The weak breakout sits on the steep part of the relative-volume ramp
        by construction, so it is the scenario where volume noise matters most.
        What is asserted is that the response grows with the perturbation rather
        than leaping at one magnitude.
        """
        report = sweep(
            scenarios["weak_breakout"],
            PerturbationKind.VOLUME_NOISE,
            [0.01, 0.02, 0.05, 0.1],
            trials=4,
            builder=GENERATOR.build,
        )
        assert report.is_proportional
        assert report.median_delta < LARGE_JUMP

    def test_a_saturated_volume_component_is_immune_to_volume_noise(self, scenarios):
        """A breakout at 3x average is far above the ramp's full point, so
        noise on it cannot move the component at all."""
        report = sweep(
            scenarios["clean_breakout"],
            PerturbationKind.VOLUME_NOISE,
            [0.05, 0.1],
            trials=3,
            builder=GENERATOR.build,
        )
        assert report.median_delta == pytest.approx(0.0)


class TestDefinitionalPerturbations:
    @pytest.mark.parametrize(
        "kind",
        [
            PerturbationKind.LEVEL_SHIFT,
            PerturbationKind.ATR_SHIFT,
            PerturbationKind.GAP_SIZE,
            PerturbationKind.CLOSE_LOCATION,
            PerturbationKind.RETEST_DEPTH,
        ],
    )
    def test_every_large_move_is_structurally_explained(self, kind, scenarios):
        magnitudes = {
            PerturbationKind.LEVEL_SHIFT: [-0.002, -0.001, 0.001, 0.002],
            PerturbationKind.ATR_SHIFT: [-2, -1, 1, 2],
            PerturbationKind.GAP_SIZE: [0.005, 0.01],
            PerturbationKind.CLOSE_LOCATION: [-0.1, 0.05],
            PerturbationKind.RETEST_DEPTH: [0.002, 0.005],
        }[kind]
        report = sweep(
            scenarios["clean_breakout"],
            kind,
            magnitudes,
            trials=2,
            builder=GENERATOR.build,
        )
        assert not report.unexplained_jumps, [s.cause() for s in report.unexplained_jumps]

    def test_the_atr_period_is_not_what_the_engine_measures(self, scenarios):
        """An engine whose conclusions swing on 14 versus 16 sessions of
        smoothing is measuring its own smoothing."""
        report = sweep(
            scenarios["clean_breakout"],
            PerturbationKind.ATR_SHIFT,
            [-2, -1, 1, 2],
            trials=2,
            builder=GENERATOR.build,
        )
        assert report.median_delta < 3.0

    def test_a_spec_perturbation_without_a_builder_is_refused(self, scenarios):
        """Editing bars instead would produce candles no market could print,
        which is a poor thing to measure stability against."""
        with pytest.raises(ValueError, match="build method"):
            sweep(
                scenarios["clean_breakout"],
                PerturbationKind.GAP_SIZE,
                [0.01],
                trials=1,
                builder=None,
            )


class TestPerturbationHelpers:
    def test_price_noise_preserves_the_candle_shape(self, scenarios):
        """One factor per bar rather than per price. Perturbing the four prices
        independently would change close location and wick fractions, which is
        a structural change dressed up as noise."""
        original = scenarios["clean_breakout"]
        noisy = add_price_noise(original, 0.002, seed=1)
        for before, after in zip(original.bars, noisy.bars, strict=True):
            span_before = float(before.high) - float(before.low)
            span_after = float(after.high) - float(after.low)
            if span_before <= 0:
                continue
            location_before = (float(before.close) - float(before.low)) / span_before
            location_after = (float(after.close) - float(after.low)) / span_after
            # Tolerance covers price quantisation to four decimals, which is a
            # property of the bar type rather than of the perturbation.
            assert location_after == pytest.approx(location_before, abs=1e-3)

    def test_volume_noise_leaves_prices_alone(self, scenarios):
        original = scenarios["clean_breakout"]
        noisy = add_volume_noise(original, 0.1, seed=1)
        assert [b.close for b in original.bars] == [b.close for b in noisy.bars]

    def test_shifting_by_too_much_is_a_no_op(self, scenarios):
        original = scenarios["clean_breakout"]
        assert shift_timing(original, 10_000) is original

    def test_a_perturbation_records_both_sides(self, scenarios):
        report = sweep(
            scenarios["clean_breakout"],
            PerturbationKind.PRICE_NOISE,
            [0.001],
            trials=1,
            builder=GENERATOR.build,
        )
        sample = report.samples[0]
        assert sample.baseline_state and sample.perturbed_state
        assert sample.cause() in ("continuous", "terminal state changed", "breakout bar moved")


class TestMonotonicity:
    """Only where the direction is definitional.

    Each of these asserts a relationship the *definition* requires, not one the
    numbers happen to show. Asserting monotonicity where the definition is
    silent is how a test ends up enforcing an accident.
    """

    def test_quality_rises_with_breakout_volume(self):
        scores = [
            run_scenario(
                GENERATOR.build(
                    BreakoutSpec(breakout_volume_multiple=multiple), name="volume-sweep"
                )
            ).breakout_quality
            for multiple in (0.6, 1.0, 1.5, 2.2, 3.0)
        ]
        assert is_monotone_in(scores, increasing=True)

    def test_quality_rises_with_close_location(self):
        scores = [
            run_scenario(
                GENERATOR.build(
                    BreakoutSpec(breakout_close_location=location), name="location-sweep"
                )
            ).breakout_quality
            for location in (0.3, 0.5, 0.7, 0.9)
        ]
        assert is_monotone_in(scores, increasing=True)

    def test_the_extension_term_falls_as_the_close_moves_further_above(self):
        extensions = []
        for above in (0.01, 0.03, 0.06, 0.10):
            event = run_scenario(
                GENERATOR.build(BreakoutSpec(breakout_close_above=above), name="extension-sweep")
            )
            component = event.component("penetration")
            assert component is not None
            extensions.append(component.measurements["extension_score"])
        assert is_monotone_in(extensions, increasing=False)

    def test_confidence_rises_with_boundary_touches(self):
        from tradeit.breakouts.validation import BoundarySpec

        scenario = GENERATOR.clean_breakout(seed=0)
        scores = [
            run_scenario(
                scenario, spec=BoundarySpec(touches=touches, confidence=float(touches) * 20.0)
            ).confidence
            for touches in (1, 2, 3, 4, 5)
        ]
        assert is_monotone_in(scores, increasing=True)

    def test_monotonicity_is_not_asserted_for_gap_size(self):
        """Deliberately absent as an assertion. A larger gap raises penetration
        and lowers extension at once; the composite has no direction the
        definition commits to, and a test claiming one would be inventing a
        preference Phase 5 is not entitled to hold.
        """
        qualities = [
            run_scenario(GENERATOR.gap_breakout(seed=0, gap=gap)).breakout_quality
            for gap in (0.01, 0.04, 0.09)
        ]
        assert all(0.0 <= q <= 100.0 for q in qualities)

    def test_the_helper_rejects_a_genuine_inversion(self):
        assert not is_monotone_in([10.0, 40.0, 5.0], increasing=True, tolerance=2.0)
        assert is_monotone_in([10.0, 9.0, 8.5], increasing=False)


class TestCharacterisationHarness:
    def test_the_corpus_has_the_scenarios_the_brief_lists(self):
        assert len(CONTROLLED_SCENARIOS) == 17
        assert len(ADVERSARIAL_SCENARIOS) == 13
        names = {s.name for s in all_scenarios()}
        assert names == set(CONTROLLED_SCENARIOS) | set(ADVERSARIAL_SCENARIOS)

    def test_every_scenario_states_its_intent(self):
        """A row whose meaning is only recoverable from the source is a row
        nobody will check."""
        for scenario in all_scenarios():
            assert scenario.intent

    def test_the_adversarial_corpus_is_marked_as_such(self):
        adversarial = {s.name for s in all_scenarios() if s.adversarial}
        assert adversarial == set(ADVERSARIAL_SCENARIOS)

    def test_the_same_seeds_produce_the_same_numbers(self):
        """A surprising result must be reproducible rather than re-argued."""
        scenario = next(s for s in all_scenarios() if s.name == "clean_breakout")
        first = characterise(scenario, trials=TRIALS)
        second = characterise(scenario, trials=TRIALS)
        assert first.summary() == second.summary()

    def test_a_seed_offset_produces_an_independent_sample(self):
        scenario = next(s for s in all_scenarios() if s.name == "noise_around_level")
        first = characterise(scenario, trials=TRIALS)
        shifted = characterise(scenario, trials=TRIALS, seed_offset=500)
        assert first.outcomes != shifted.outcomes

    def test_the_rates_are_reported_separately(self):
        """Candidate, confirmed, rejected, failed and expired are five outcomes
        and the harness must never collapse them into an accuracy."""
        scenario = next(s for s in all_scenarios() if s.name == "clean_breakout")
        stats = characterise(scenario, trials=TRIALS)
        for rate in (
            stats.breakout_rate,
            stats.confirmed_rate,
            stats.rejected_rate,
            stats.failed_rate,
            stats.expired_rate,
        ):
            assert 0.0 <= rate <= 1.0
        assert stats.confirmed_rate <= stats.breakout_rate + 1e-9

    def test_quality_thresholds_are_cumulative(self):
        scenario = next(s for s in all_scenarios() if s.name == "clean_breakout")
        stats = characterise(scenario, trials=TRIALS)
        rates = [stats.high_quality_rate(t) for t in (50.0, 60.0, 70.0, 80.0, 90.0)]
        assert rates == sorted(rates, reverse=True)

    def test_series_that_never_broke_out_count_as_zero_rather_than_being_excluded(self):
        """Excluding them would describe the subset that happened to fire, which
        is a different and flattering question."""
        scenario = next(s for s in all_scenarios() if s.name == "no_breakout")
        stats = characterise(scenario, trials=TRIALS)
        assert stats.breakout_rate == 0.0
        assert stats.median_quality == 0.0

    def test_separation_is_reported_without_being_called_accuracy(self):
        results = [
            characterise(s, trials=TRIALS)
            for s in all_scenarios()
            if s.name in ("clean_breakout", "noise_around_level")
        ]
        assert -1.0 <= separation(results) <= 1.0

    def test_an_outcome_aggregates_across_attempts(self):
        """'Did this series produce a breakout?' is a question about the series,
        not about whichever attempt happened to be last."""
        from tradeit.breakouts.validation import replay_scenario

        scenario = next(s for s in all_scenarios() if s.name == "multiple_attempts")
        attempts = replay_scenario(scenario.build(0))
        outcome = ScenarioOutcome.of(scenario, 0, attempts)
        assert outcome.attempts == len(attempts)
        assert outcome.reached_close_above == any(e.has_broken_out for e in attempts)


class TestScenarioParameterisation:
    def test_a_scenario_differs_from_the_clean_case_in_one_field(self):
        """The property that makes a difference in output attributable.

        A hand-written series per scenario produces corpora whose differences
        are accidental; this one is a parameterisation of a single builder.
        """
        clean = GENERATOR.clean_breakout(seed=0).spec
        quiet = GENERATOR.low_volume_breakout(seed=0).spec
        differing = [
            field for field in clean.__slots__ if getattr(clean, field) != getattr(quiet, field)
        ]
        assert differing == ["breakout_volume_multiple"]

    def test_a_prefix_of_a_scenario_forgets_a_later_breakout(self):
        scenario = GENERATOR.clean_breakout(seed=0)
        index = scenario.breakout_index
        assert index is not None
        assert scenario.prefix(index - 1).breakout_index is None
        assert scenario.prefix(index + 1).breakout_index == index

    def test_a_rejection_scenario_does_not_quietly_recover(self):
        """A scenario that rejects and then drifts back above the level within
        two sessions is testing recovery, not rejection, and would silently stop
        being the negative it is named for.
        """
        scenario = GENERATOR.immediate_rejection(seed=0)
        index = scenario.spec.advance_sessions + scenario.spec.base_sessions
        after = [float(b.close) for b in scenario.bars[index + 1 :]]
        assert all(close < scenario.level for close in after)

    def test_the_builder_is_deterministic(self):
        spec = replace(BreakoutSpec(), seed=7)
        first = GENERATOR.build(spec, name="x")
        second = GENERATOR.build(spec, name="x")
        assert [b.model_dump() for b in first.bars] == [b.model_dump() for b in second.bars]
