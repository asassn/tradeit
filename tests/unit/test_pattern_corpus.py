"""Score distributions across adversarial and positive corpora.

These tests characterise the detector rather than gate it. The distinction
matters: a pass/fail assertion on one series says almost nothing, while the
shape of a score distribution across a thousand says whether quality separates
structure from noise and where it stops doing so.

**Nothing here was tuned against.** Every bound below was written after reading
the measured distribution, with headroom, and no detector threshold was adjusted
to make a number look better. Doing that would fit the detector to this corpus,
and the resulting figure would describe the corpus.

The full corpus (21 adversarial families) is marked ``performance`` and
excluded from the default run — it takes about six seconds and its value is in
the printed distributions rather than in a boolean. ``make bench`` prints them.
A fast subset runs by default so a regression that collapses the detector is
still caught in CI.
"""

from __future__ import annotations

import pytest

from tradeit.patterns.corpus import (
    POSITIVE_GRADES,
    QUALITY_BANDS,
    ScoreDistribution,
    adversarial_families,
    grade_ordering_holds,
    positive_families,
    run_corpus,
    run_family,
    state_breakdown,
)
from tradeit.patterns.detectors import BullFlagDetector
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()
DETECTOR = BullFlagDetector()


class TestPositiveDistribution:
    """A detector should rank grades without needing binary perfection."""

    def test_grades_are_ordered_by_median_quality(self):
        """clean > moderate > borderline > flawed.

        Asserted on medians, deliberately not on maxima or individual series.
        A borderline flag that happens to outscore a clean one is ordinary
        noise; demanding otherwise would demand the detector be right about
        every case rather than right on average.
        """
        report = run_corpus(DETECTOR, positive_families(GENERATOR), trials=20)
        assert grade_ordering_holds(report, ["clean", "moderate", "borderline", "flawed"])

    def test_clean_examples_are_separated_from_flawed_ones(self):
        report = run_corpus(DETECTOR, positive_families(GENERATOR), trials=20)
        clean = report.get("clean")
        flawed = report.get("flawed")
        assert clean is not None and flawed is not None
        # Measured gap is ~43 points; a much smaller one would mean the score
        # is not discriminating between a textbook flag and a broken one.
        assert clean.median - flawed.median > 25.0

    def test_a_clean_flag_is_always_detected(self):
        report = run_corpus(DETECTOR, positive_families(GENERATOR), trials=20)
        assert report.get("clean").structure_rate == 1.0

    def test_every_grade_varies_several_dimensions_at_once(self):
        """A corpus varying one knob shows much less than one varying several.

        Guards against the grades quietly collapsing into a single-parameter
        sweep, which would make the ordering trivial.
        """
        clean, flawed = POSITIVE_GRADES["clean"], POSITIVE_GRADES["flawed"]
        differing = sum(
            1
            for field in ("pole_gain", "retracement", "flag_slope", "noise", "volume_contraction")
            if getattr(clean, field) != getattr(flawed, field)
        )
        assert differing >= 4


class TestAdversarialDistribution:
    """Fast subset. The full corpus runs under `make bench`."""

    FAMILIES = (
        "random_walk_mid_vol",
        "bear_flag",
        "parabolic",
        "quiet_drift",
        "broadening_formation",
        "single_day_spike",
    )

    def test_no_negative_family_reaches_the_top_quality_band(self):
        """The strongest single claim the corpus supports.

        Measured across 1,050 adversarial series, the highest score any
        negative family produced was 85.4 (single_day_spike) and 85.1
        (earnings_gap). Nothing reached 90.
        """
        families = adversarial_families(GENERATOR)
        for name in self.FAMILIES:
            distribution = run_family(DETECTOR, name, families[name], trials=25)
            assert distribution.maximum < 90.0, distribution.format_row()

    def test_structurally_impossible_families_produce_nothing(self):
        """A parabolic run has no pause; a quiet drift has no advance. These
        are not low-quality flags, they are absences of the structure."""
        families = adversarial_families(GENERATOR)
        for name in ("parabolic", "quiet_drift"):
            distribution = run_family(DETECTOR, name, families[name], trials=25)
            assert distribution.structure_rate == 0.0, distribution.format_row()

    def test_a_broadening_formation_scores_poorly(self):
        """The inverse of every contraction pattern. A detector measuring net
        range change rather than its direction would score this as compression.
        """
        families = adversarial_families(GENERATOR)
        distribution = run_family(
            DETECTOR, "broadening_formation", families["broadening_formation"], trials=25
        )
        assert distribution.percentile(95) < 50.0

    def test_the_bear_flag_mirror_image_is_rejected(self):
        """Every volume and volatility characteristic of a good bull flag is
        present; only the direction differs."""
        families = adversarial_families(GENERATOR)
        distribution = run_family(DETECTOR, "bear_flag", families["bear_flag"], trials=25)
        assert distribution.percentile(95) < 55.0

    def test_the_measured_false_positive_rate_is_reported_not_asserted_to_zero(self):
        """Random walks genuinely produce flag-shaped structures, and so do
        real markets. A detector claiming zero false positives on noise is
        either mis-measuring or so tight it will find nothing real."""
        families = adversarial_families(GENERATOR)
        distribution = run_family(
            DETECTOR, "random_walk_mid_vol", families["random_walk_mid_vol"], trials=40
        )
        assert distribution.structure_rate > 0.0, "zero would mean the detector is inert"
        assert distribution.percentile(95) < 75.0


class TestReporting:
    def test_a_distribution_reports_percentiles_not_a_verdict(self):
        families = adversarial_families(GENERATOR)
        distribution = run_family(DETECTOR, "choppy_range", families["choppy_range"], trials=20)
        summary = distribution.summary()
        for key in ("tested", "structure_rate", "median", "p90", "p95", "p99", "max", "bands"):
            assert key in summary

    def test_band_rates_sum_to_the_structure_rate(self):
        families = adversarial_families(GENERATOR)
        distribution = run_family(
            DETECTOR, "random_walk_mid_vol", families["random_walk_mid_vol"], trials=30
        )
        assert sum(distribution.band_rates().values()) == pytest.approx(
            distribution.structure_rate, abs=1e-9
        )

    def test_the_bands_are_not_named_as_trading_thresholds(self):
        """Naming a band "tradeable" would set an operational threshold under
        cover of a reporting convention. Phase 4 sets none."""
        names = {name for name, _, _ in QUALITY_BANDS}
        assert not names & {"tradeable", "marginal", "reject", "buy"}

    def test_the_worst_offender_is_identifiable(self):
        """The most informative single number in a corpus run: it names the
        structure a real-data validation should look at first."""
        report = run_corpus(
            DETECTOR,
            {
                k: v
                for k, v in adversarial_families(GENERATOR).items()
                if k in ("bear_flag", "choppy_range")
            },
            trials=15,
        )
        assert report.worst_offender() is not None

    def test_state_breakdown_reports_only_states_that_occurred(self):
        families = adversarial_families(GENERATOR)
        distribution = run_family(DETECTOR, "deep_pullback", families["deep_pullback"], trials=10)
        breakdown = state_breakdown(distribution)
        assert all(count > 0 for count in breakdown.values())

    def test_an_empty_distribution_does_not_divide_by_zero(self):
        empty = ScoreDistribution(family="none", tested=0, scores=(), with_structure=0)
        assert empty.structure_rate == 0.0
        assert empty.median == 0.0
        assert empty.maximum == 0.0


@pytest.mark.performance
class TestFullCorpus:
    """The whole corpus. Value is in the printed distributions.

    Run with ``pytest -m performance -s`` or ``make bench``.
    """

    def test_full_adversarial_corpus(self):
        report = run_corpus(DETECTOR, adversarial_families(GENERATOR), trials=50)
        print(f"\n  ADVERSARIAL CORPUS  ({report.total_tested} series)")
        print("  " + report.format_table().replace("\n", "\n  "))
        worst = report.worst_offender()
        print(
            f"\n  worst offender: {worst.family} "
            f"(p99 {worst.percentile(99):.1f}, max {worst.maximum:.1f})"
        )
        assert report.total_tested >= 1000
        # No negative family should reach the top band. This is a genuine
        # property of the detector, not a tuned one -- the measured maximum
        # across every family is 85.4.
        assert all(d.maximum < 90.0 for d in report.distributions)

    def test_full_positive_corpus(self):
        report = run_corpus(DETECTOR, positive_families(GENERATOR), trials=100)
        print(f"\n  POSITIVE CORPUS  ({report.total_tested} series)")
        print("  " + report.format_table().replace("\n", "\n  "))
        assert grade_ordering_holds(report, ["clean", "moderate", "borderline", "flawed"])

    def test_separation_between_clean_positives_and_worst_negatives(self):
        """The number that matters most, and the one that is least flattering.

        Clean synthetic flags median ~75. The worst adversarial families reach
        the low 80s at p99. **The distributions overlap**, which means quality
        alone does not separate a clean flag from a well-shaped false positive,
        and no threshold on this score would.

        Reported rather than fixed. The overlap is dominated by earnings-gap
        and single-session-spike structures, where whether the detection is
        even wrong is genuinely arguable -- a tight range holding a gap is a
        setup many practitioners trade. Resolving it needs real labelled data,
        not a tighter synthetic threshold.
        """
        negatives = run_corpus(DETECTOR, adversarial_families(GENERATOR), trials=50)
        positives = run_corpus(DETECTOR, positive_families(GENERATOR), trials=100)

        clean = positives.get("clean")
        worst = max(negatives.distributions, key=lambda d: d.maximum)
        print(
            f"\n  clean median {clean.median:.1f} / max {clean.maximum:.1f}"
            f"  vs worst negative {worst.family} max {worst.maximum:.1f}"
        )
        assert worst.maximum > clean.median, (
            "if this ever passes cleanly, re-read it: it would mean the "
            "synthetic corpus has stopped containing hard cases"
        )
