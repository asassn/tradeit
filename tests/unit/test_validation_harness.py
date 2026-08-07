"""The characterisation harness itself.

Everything in `docs/PATTERN_VALIDATION.md` is produced by this module, so a bug
here would corrupt every number in the report while leaving it looking
plausible. These tests run the harness at small trial counts and assert the
properties the report's readers rely on.

Trial counts are deliberately tiny — the point is that the machinery is correct,
not to characterise anything. The real characterisation runs at n=1000 via
`scripts/phase4_report.py`, which is far too slow for a unit suite.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.registry import DETECTOR_ORDER, DetectorRegistry
from tradeit.patterns.synthetic import PatternGenerator
from tradeit.patterns.validation import (
    QUALITY_THRESHOLDS,
    CohortKind,
    characterise,
    cohorts_for,
    shared_noise,
)

GENERATOR = PatternGenerator()
REGISTRY = DetectorRegistry.from_config()
TRIALS = 6


class TestCohortDefinitions:
    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_every_family_declares_the_full_set_of_cohort_kinds(self, name):
        """A family missing a cohort kind has a blind spot in its report.

        The four that must always be present are the ones a reader draws
        conclusions from: what the pattern looks like, what a broken one looks
        like, what its nearest neighbour looks like, and what noise looks like.
        """
        kinds = {cohort.kind for cohort in cohorts_for(name, GENERATOR)}
        for required in (
            CohortKind.POSITIVE,
            CohortKind.INVALID,
            CohortKind.COMPETING,
            CohortKind.NOISE,
        ):
            assert required in kinds, f"{name} has no {required} cohort"

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_every_cohort_states_its_intent(self, name):
        """The report reproduces these. A row whose meaning is only recoverable
        from the source is a row nobody will check."""
        for cohort in cohorts_for(name, GENERATOR):
            assert cohort.intent, f"{name}/{cohort.name} has no stated intent"

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_cohort_names_are_unique_within_a_family(self, name):
        names = [cohort.name for cohort in cohorts_for(name, GENERATOR)]
        assert len(names) == len(set(names))

    def test_the_noise_cohorts_are_shared_rather_than_duplicated(self):
        """Random walks are not family-specific. Twelve private copies would
        multiply the cost without adding a fact."""
        shared = {cohort.name for cohort in shared_noise(GENERATOR)}
        for name in DETECTOR_ORDER:
            family = {c.name for c in cohorts_for(name, GENERATOR)}
            assert shared <= family

    def test_only_noise_and_adversarial_count_against_a_detector(self):
        """A broken pattern legitimately produces a low-scoring instance, and a
        competing structure may legitimately qualify under two definitions at
        once. Counting either as a false positive would push the detectors
        toward the forced exclusivity ADR-0019 rejects."""
        assert CohortKind.NOISE.is_negative
        assert CohortKind.ADVERSARIAL.is_negative
        assert not CohortKind.INVALID.is_negative
        assert not CohortKind.COMPETING.is_negative
        assert not CohortKind.BORDERLINE.is_negative


@pytest.fixture(scope="module")
def flat_base_report():
    detector = REGISTRY.detector("flat_base", Bartimeframe.D1)
    return characterise(detector, cohorts_for("flat_base", GENERATOR), trials=TRIALS)


class TestStatistics:
    def test_frequencies_are_reported_separately(self, flat_base_report):
        """Candidate, mature, high-quality and terminal are four different facts
        and the harness must never collapse them into an accuracy."""
        for cohort in flat_base_report.cohorts:
            assert 0.0 <= cohort.candidate_rate <= 1.0
            assert 0.0 <= cohort.mature_rate <= 1.0
            assert 0.0 <= cohort.invalidation_rate <= 1.0
            assert cohort.mature_rate <= cohort.candidate_rate + 1e-9

    def test_thresholds_are_cumulative_and_monotone(self, flat_base_report):
        for cohort in flat_base_report.cohorts:
            rates = [cohort.high_quality_rate(t) for t in QUALITY_THRESHOLDS]
            assert rates == sorted(rates, reverse=True)

    def test_percentiles_include_the_zeros(self, flat_base_report):
        """A cohort with a low candidate rate has a low median *because* most
        series produced nothing. Excluding the zeros would describe the subset
        that happened to fire, which is a different and flattering question."""
        for cohort in flat_base_report.cohorts:
            assert len(cohort.scores) == cohort.tested
            if cohort.candidate_rate < 0.5:
                assert cohort.median <= cohort.detected_median + 1e-9

    def test_coverage_is_recorded_only_where_an_instance_existed(self, flat_base_report):
        for cohort in flat_base_report.cohorts:
            assert len(cohort.coverages) == cohort.candidates
            assert len(cohort.states) == cohort.candidates

    def test_a_summary_is_serialisable(self, flat_base_report):
        summary = flat_base_report.summary()
        assert summary["detector"] == "flat_base"
        assert summary["total_tested"] == TRIALS * len(flat_base_report.cohorts)
        assert isinstance(summary["cohorts"], list)

    def test_the_worst_negative_is_identified(self, flat_base_report):
        worst = flat_base_report.worst_negative()
        assert worst is not None
        assert worst.kind.is_negative
        negatives = [c for c in flat_base_report.cohorts if c.kind.is_negative]
        assert worst.percentile(99) == max(c.percentile(99) for c in negatives)


class TestReproducibility:
    def test_the_same_seeds_produce_the_same_numbers(self):
        """A surprising result must be reproducible rather than re-argued."""
        detector = REGISTRY.detector("pennant", Bartimeframe.D1)
        cohorts = cohorts_for("pennant", GENERATOR)
        first = characterise(detector, cohorts, trials=TRIALS)
        second = characterise(detector, cohorts, trials=TRIALS)
        assert [c.summary() for c in first.cohorts] == [c.summary() for c in second.cohorts]

    def test_a_seed_offset_produces_an_independent_sample(self):
        """Otherwise a second run would re-measure the same series and look like
        confirmation."""
        detector = REGISTRY.detector("pennant", Bartimeframe.D1)
        cohorts = cohorts_for("pennant", GENERATOR)
        first = characterise(detector, cohorts, trials=TRIALS)
        shifted = characterise(detector, cohorts, trials=TRIALS, seed_offset=500)
        assert first.cohorts[0].scores != shifted.cohorts[0].scores


class TestOrdering:
    @pytest.mark.parametrize(
        "name",
        ["flat_base", "cup_handle", "high_tight_flag", "double_bottom"],
    )
    def test_positive_outscores_invalid(self, name):
        """The weakest claim a synthetic corpus can support, and the one it must.

        Not asserted across every family: several have invalid cohorts the
        detector correctly refuses to score at all, where the comparison is
        between a number and silence.
        """
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        report = characterise(detector, cohorts_for(name, GENERATOR), trials=TRIALS)
        positive = max(c.median for c in report.of_kind(CohortKind.POSITIVE))
        invalid = max((c.median for c in report.of_kind(CohortKind.INVALID)), default=0.0)
        assert positive > invalid

    def test_separation_is_reported_without_being_called_accuracy(self):
        """A blunt single number, published alongside the distributions rather
        than instead of them. No cost is attached to either error, because
        attaching one is a later phase's decision."""
        detector = REGISTRY.detector("high_tight_flag", Bartimeframe.D1)
        report = characterise(detector, cohorts_for("high_tight_flag", GENERATOR), trials=TRIALS)
        assert -1.0 <= report.separation(70.0) <= 1.0


class TestUnknownFamily:
    def test_an_undefined_family_is_refused(self):
        with pytest.raises(KeyError, match="no cohorts defined"):
            cohorts_for("moon_phase", GENERATOR)
