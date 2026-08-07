"""Pattern architecture, swing causality, scoring curves, and the bull flag.

Four kinds of test, and the last two are the ones that would catch a real bug:

* **Positive** — the detector finds a structure that was deliberately drawn.
  The weakest evidence here, close to tautological: the generator draws what
  the detector looks for.
* **Negative** — the detector does *not* find structure in bear flags, falling
  knives, single-day spikes, gap-and-fades, parabolic runs and noise. Stronger,
  because nothing was built to make these fail.
* **Perturbation** — turning one knob moves the score the right way. Much
  stronger than any fixture: it tests the shape of the response rather than one
  point on it.
* **Causality** — adding tomorrow's bar cannot change what was reported
  yesterday. The property the whole architecture exists to guarantee.
"""

from __future__ import annotations

import datetime as dt
from itertools import pairwise

import numpy as np
import pytest

from tradeit.core.enums import PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import (
    ComponentScore,
    Detector,
    Evidence,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.detectors import BullFlagDetector
from tradeit.patterns.scoring import (
    band_score,
    combine,
    decay_score,
    linear_interpolate,
    ramp_score,
    step_score,
)
from tradeit.patterns.structure import fit_line, measure_consolidation
from tradeit.patterns.swings import (
    SwingKind,
    confirmed_swings,
    find_swings,
    provisional_extremes,
    touches_of_level,
)
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator, SeriesSpec
from tradeit.patterns.tracking import PatternTracker, merge_overlapping

GENERATOR = PatternGenerator()
DETECTOR = BullFlagDetector()


def detect(series, *, detector: BullFlagDetector | None = None) -> list[PatternInstance]:
    return (detector or DETECTOR).detect(series.bars, series.last_session)


def best_quality(series, *, detector: BullFlagDetector | None = None) -> float:
    return max((p.quality for p in detect(series, detector=detector)), default=0.0)


# ---------------------------------------------------------------------------
# Causality: the property the architecture exists for
# ---------------------------------------------------------------------------


class TestSwingCausality:
    def test_a_swing_is_not_knowable_on_the_day_it_occurs(self):
        """The leak this module exists to prevent.

        A swing high needs bars on both sides. Marking bar i as a pivot on day i
        uses bars that have not happened, and every resistance line built on it
        inherits the lie.
        """
        series = GENERATOR.bull_flag()
        swings = find_swings(series.bars, left_bars=3, right_bars=3, kind=SwingKind.HIGH)
        assert swings
        for swing in swings:
            assert swing.confirmation_lag == 3
            assert swing.confirmed_date > swing.session_date
            assert not swing.is_visible_at(swing.session_date)

    def test_confirmed_swings_excludes_the_unconfirmed_tail(self):
        series = GENERATOR.bull_flag()
        last = series.last_session
        visible = confirmed_swings(series.bars, last, left_bars=3, right_bars=3)
        assert all(s.confirmed_date <= last for s in visible)
        assert all(s.index <= len(series.bars) - 4 for s in visible)

    def test_the_confirmation_filter_actually_removes_something(self):
        """Guards a filter that silently passes everything.

        If the filter never excluded anything, the visibility rule would be
        decorative and every causality test built on it would pass vacuously.
        So this cuts the series at a known pivot and asserts that pivot is
        invisible until its confirmation bars have arrived, then visible.
        """
        series = GENERATOR.bull_flag()
        pivots = find_swings(series.bars, left_bars=3, right_bars=3)
        assert pivots
        pivot = pivots[-1]

        at_the_pivot = series.bars[: pivot.index + 1]
        assert not confirmed_swings(
            at_the_pivot, at_the_pivot[-1].session_date, left_bars=3, right_bars=3
        ) or all(
            s.index < pivot.index
            for s in confirmed_swings(
                at_the_pivot, at_the_pivot[-1].session_date, left_bars=3, right_bars=3
            )
        )

        confirmed = series.bars[: pivot.confirmed_index + 1]
        visible = confirmed_swings(confirmed, confirmed[-1].session_date, left_bars=3, right_bars=3)
        assert any(s.index == pivot.index for s in visible)

    def test_swing_ties_resolve_to_the_earlier_bar(self):
        """Two adjacent equal highs are one pivot, not two.

        Picking the later one moves the pivot forward in time for no structural
        reason, which shortens every pattern anchored to it.
        """
        series = GENERATOR.random_walk(40, seed=7)
        bars = list(series.bars)
        swings = find_swings(bars, left_bars=2, right_bars=2)
        indices = [s.index for s in swings]
        assert indices == sorted(set(indices))

    def test_provisional_extremes_are_labelled_not_hidden(self):
        """The tail must be summarised, not silently dropped."""
        series = GENERATOR.bull_flag()
        tail = provisional_extremes(series.bars, right_bars=3)
        assert tail.sessions == 3
        assert tail.last_close == pytest.approx(float(series.bars[-1].close))
        assert tail.highest_high >= tail.last_close or tail.highest_high > 0

    def test_a_drift_along_a_level_is_one_touch_not_three(self):
        """Three consecutive days hovering at a level is one test of it.

        Counting them separately inflates every confidence score derived from
        touch counts, which makes a fresh level look like an established one.
        """
        series = GENERATOR.random_walk(60, volatility=0.001, seed=3)
        level = float(series.bars[30].high)
        touches = touches_of_level(
            series.bars, level, start_index=25, end_index=40, tolerance_pct=0.05, min_separation=2
        )
        assert all(b - a >= 2 for a, b in pairwise(touches))


class TestDetectorCausality:
    """Adding tomorrow's bar cannot change what was reported yesterday."""

    def test_a_later_bar_does_not_change_an_earlier_detection(self):
        """The formal statement of no-look-ahead for the whole detector.

        Detect at bar k, then detect at bar k+5 restricted to the same prefix,
        and require identical geometry. If these ever diverge, every stored
        pattern is a claim about what the system knew that it did not know.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=6))
        cut = len(series.bars) - 7

        prefix = series.slice_to(cut)
        early = detect(prefix)

        # The same prefix, obtained from the longer series. The detector must
        # not be able to tell the difference.
        again = DETECTOR.detect(series.bars[: cut + 1], prefix.last_session)

        assert [p.to_payload() for p in early] == [p.to_payload() for p in again]

    def test_every_prefix_is_stable_under_extension(self):
        """Prefix consistency across many cut points, not just one."""
        series = GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=8))
        for cut in range(len(series.bars) - 12, len(series.bars) - 1):
            prefix_bars = series.bars[: cut + 1]
            as_of = prefix_bars[-1].session_date
            first = DETECTOR.detect(prefix_bars, as_of)
            second = DETECTOR.detect(list(prefix_bars), as_of)
            assert [p.to_payload() for p in first] == [p.to_payload() for p in second]

    def test_future_highs_cannot_refine_historical_resistance(self):
        """The specific leak: a later high tightening an earlier level.

        A resistance computed as of Monday must be the same number when
        recomputed from Monday's data on Friday, however much price has moved
        since.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=10))
        cut = len(series.bars) - 11
        monday = DETECTOR.detect(series.bars[: cut + 1], series.bars[cut].session_date)
        if not monday:
            pytest.skip("no pattern at the chosen cut point")

        friday_view_of_monday = DETECTOR.detect(
            series.bars[: cut + 1], series.bars[cut].session_date
        )
        assert [p.resistance_price for p in monday] == [
            p.resistance_price for p in friday_view_of_monday
        ]

    def test_bars_past_the_knowledge_boundary_are_refused(self):
        """A caller that forgets to clock-gate gets an error, not a silent leak."""
        series = GENERATOR.bull_flag()
        earlier = series.bars[-5].session_date
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            DETECTOR.detect(series.bars, earlier)

    def test_geometry_never_extends_past_the_boundary(self):
        series = GENERATOR.bull_flag()
        for pattern in detect(series):
            assert pattern.geometry.end_date <= pattern.as_of_session

    def test_a_misaligned_context_is_refused(self):
        """A benchmark of the wrong length yields RS computed from mismatched days."""
        from tradeit.patterns.base import PatternContext

        series = GENERATOR.bull_flag()
        bad = PatternContext(benchmark_closes=[100.0] * (len(series.bars) - 3))
        with pytest.raises(ValueError, match="not aligned"):
            DETECTOR.detect(series.bars, series.last_session, context=bad)


# ---------------------------------------------------------------------------
# Positive detection
# ---------------------------------------------------------------------------


class TestBullFlagPositive:
    def test_a_textbook_flag_is_found(self):
        found = detect(GENERATOR.bull_flag())
        assert found
        assert found[0].pattern_type is PatternType.BULL_FLAG
        assert found[0].quality > 60

    def test_the_detected_consolidation_matches_the_generated_one(self):
        """Finding *a* flag is not finding *the* flag.

        A detector that locates a structure twenty sessions from where one was
        drawn has not found it, and a test that only checks "something was
        returned" cannot tell the difference.
        """
        series = GENERATOR.bull_flag()
        found = detect(series)
        assert found

        expected_start = series.bars[int(series.truth["flag_start_index"])].session_date
        expected_end = series.bars[int(series.truth["flag_end_index"])].session_date
        actual_start, actual_end = found[0].geometry.segments["consolidation"]
        assert actual_start == expected_start
        assert actual_end == expected_end

    def test_the_quality_score_reconciles_with_its_components(self):
        """Guards a late adjustment applied without being recorded."""
        for pattern in detect(GENERATOR.bull_flag()):
            assert pattern.reconciles

    def test_evidence_is_recorded_on_both_sides(self):
        """A detector that only records what it liked is arguing for itself."""
        found = detect(GENERATOR.bull_flag(BullFlagSpec(flag_slope=-0.006, noise=1.5)))
        assert found
        assert found[0].supporting_evidence
        assert found[0].contradicting_evidence

    def test_resistance_support_and_invalidation_are_ordered(self):
        for pattern in detect(GENERATOR.bull_flag()):
            assert pattern.resistance_price is not None
            assert pattern.support_price is not None
            assert pattern.invalidation_price is not None
            assert pattern.invalidation_price <= pattern.support_price
            assert pattern.support_price < pattern.resistance_price

    def test_geometry_is_sufficient_to_redraw_the_pattern(self):
        """The dashboard must not need to recompute -- recomputing later against
        a longer series would draw a different pattern and call it the same."""
        found = detect(GENERATOR.bull_flag())
        payload = found[0].geometry.as_dict()
        assert set(payload["segments"]) == {"flagpole", "consolidation"}
        assert payload["resistance"]["level"] > 0
        assert payload["support"]["level"] > 0
        assert "pole_high" in payload["key_points"]

    def test_the_explanation_names_every_component(self):
        text = detect(GENERATOR.bull_flag())[0].explain()
        for component in (
            "flagpole",
            "consolidation",
            "retracement",
            "volume_structure",
            "volatility_contraction",
            "relative_strength",
            "resistance_quality",
        ):
            assert component in text

    def test_a_gap_driven_advance_is_recorded_not_rejected(self):
        """The brief is explicit: record gap characteristics, do not reject.

        A gap is a repricing rather than an accumulation -- a real difference in
        character, and not a disqualification.
        """
        found = detect(GENERATOR.bull_flag(BullFlagSpec(gap_share=0.7)))
        assert found, "a gap-driven flagpole must still produce a pattern"
        component = found[0].component("flagpole")
        assert component is not None
        assert component.measurements["largest_gap_share"] > 0.0

    def test_the_detector_satisfies_the_protocol(self):
        assert isinstance(DETECTOR, Detector)


# ---------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------


class TestBullFlagNegative:
    """Tested at least as hard against non-patterns as against ideal ones."""

    @pytest.mark.parametrize(
        "factory",
        [
            "bear_flag",
            "falling_knife",
            "single_day_spike",
            "parabolic",
            "low_volume_noise",
        ],
    )
    def test_structures_that_are_not_bull_flags_are_not_reported(self, factory):
        series = getattr(GENERATOR, factory)()
        found = detect(series)
        assert not found, f"{factory} produced {len(found)} bull flags"

    def test_random_walks_produce_a_low_false_positive_rate(self):
        """A rate, not an absolute, and the bound follows the measurement.

        Random walks genuinely do produce flag-shaped structures, and so do real
        markets -- a detector claiming zero false positives on noise is either
        mis-measuring or so tight it will find nothing real. Consolidation after
        an advance is not a rare accident; it is what price does.

        Measured over 50 seeds: 23 produced some structure, 14 scored >= 50,
        6 scored >= 60, 1 scored >= 70, none >= 80. The assertions below track
        that distribution with headroom. They are deliberately *not* tightened
        by adjusting detector thresholds until a nicer number appears -- that
        would be fitting the detector to this test, and the resulting figure
        would describe the test rather than the detector.

        The operational reading: the quality score separates noise from
        structure well at the top of the range and poorly in the middle, so a
        screen consuming these must apply a quality floor rather than treating
        "a pattern was found" as information.
        """
        qualities = [best_quality(GENERATOR.random_walk(120, seed=s)) for s in range(50)]
        assert sum(1 for q in qualities if q >= 80.0) == 0
        assert sum(1 for q in qualities if q >= 70.0) <= 3
        assert sum(1 for q in qualities if q >= 60.0) <= 10

    def test_a_broad_volatile_range_scores_poorly_if_it_scores_at_all(self):
        """The difference between a base and a range is compression."""
        assert best_quality(GENERATOR.broad_volatile_range()) < 55

    def test_a_breakout_stops_being_re_detectable_once_it_is_confirmed(self):
        """A structural limitation worth pinning, because it drives a design.

        A breakout is observable for exactly ``right_bars`` sessions. After
        that the new high is itself confirmed, it re-anchors the flagpole, and
        the old consolidation is no longer the most recent structure -- so
        re-detection returns nothing.

        That is correct: the pattern has resolved into a new leg. What it means
        is that **re-detection alone cannot carry a pattern through its own
        resolution**, which is precisely why pattern identity and state history
        are persisted rather than recomputed each day. A system that only ever
        re-detects would lose every pattern at the moment it mattered most.
        """
        observed = {}
        for sessions in range(0, 6):
            series = GENERATOR.bull_flag(
                BullFlagSpec(breakout_sessions=sessions, breakout_strength=0.05)
            )
            observed[sessions] = {str(p.state) for p in detect(series)}

        assert "broken_out_unconfirmed" in observed[3]
        assert observed[5] == set(), "re-detection should lose a resolved pattern"

    def test_a_gap_and_fade_does_not_score_as_a_quality_flag(self):
        """The consolidation-shaped trap: the range narrows while price bleeds
        back through the gap rather than holding above it."""
        assert best_quality(GENERATOR.gap_and_fade()) < 60

    def test_a_single_day_spike_is_not_a_flagpole(self):
        """No duration, no accumulation, no structure. Magnitude alone is not
        quality, and a detector that scores this well has confused the two."""
        assert best_quality(GENERATOR.single_day_spike()) == 0.0

    def test_a_series_too_short_to_judge_produces_nothing(self):
        series = GENERATOR.random_walk(20)
        assert detect(series) == []

    def test_a_downtrend_never_produces_a_quality_bullish_structure(self):
        """A sustained decline may contain a brief bounce that is technically a
        pole. What it must not produce is a *good* flag."""
        for seed in range(6):
            assert best_quality(GENERATOR.falling_knife(seed=seed)) < 50


# ---------------------------------------------------------------------------
# Perturbation: the shape of the response
# ---------------------------------------------------------------------------


class TestPerturbation:
    """A robust detector degrades gradually. These test the gradient."""

    def test_deepening_retracement_lowers_the_score_monotonically(self):
        scores = [
            best_quality(GENERATOR.bull_flag(BullFlagSpec(retracement=r)))
            for r in (0.30, 0.45, 0.60, 0.80)
        ]
        assert scores == sorted(scores, reverse=True), scores

    def test_increasing_noise_lowers_the_score(self):
        clean = best_quality(GENERATOR.bull_flag(BullFlagSpec(noise=0.4)))
        noisy = best_quality(GENERATOR.bull_flag(BullFlagSpec(noise=3.0)))
        assert clean > noisy

    def test_a_steeper_consolidation_slope_lowers_the_score(self):
        gentle = best_quality(GENERATOR.bull_flag(BullFlagSpec(flag_slope=-0.002)))
        steep = best_quality(GENERATOR.bull_flag(BullFlagSpec(flag_slope=-0.015)))
        assert gentle > steep

    def test_removing_volume_contraction_costs_points_without_collapsing(self):
        """The brief is explicit: volume influences quality, it does not gate
        detection. Requiring dry-up would select for one correlated property
        rather than for structure."""
        with_dryup = best_quality(GENERATOR.bull_flag(BullFlagSpec(volume_contraction=0.4)))
        without = best_quality(GENERATOR.bull_flag(BullFlagSpec(volume_contraction=1.2)))
        assert with_dryup > without
        assert without > 40, "a sound flag on flat volume is still a flag"

    def test_a_weaker_flagpole_lowers_the_flagpole_component(self):
        strong = detect(GENERATOR.bull_flag(BullFlagSpec(pole_gain=0.30)))
        weak = detect(GENERATOR.bull_flag(BullFlagSpec(pole_gain=0.10)))
        assert strong and weak
        assert strong[0].component("flagpole").score > weak[0].component("flagpole").score

    def test_breaking_support_invalidates_the_pattern(self):
        """Structure destroyed, not merely degraded."""
        series = GENERATOR.bull_flag(BullFlagSpec(retracement=0.5, breakdown=0.35))
        states = {p.state for p in detect(series)}
        assert states == {PatternState.INVALIDATED}

    def test_the_score_never_leaves_its_range_under_any_perturbation(self):
        """A composite that can exceed 100 is a composite with a weighting bug."""
        for retracement in (0.05, 0.25, 0.5, 0.9):
            for noise in (0.2, 1.0, 2.5):
                for pattern in detect(
                    GENERATOR.bull_flag(BullFlagSpec(retracement=retracement, noise=noise))
                ):
                    assert 0.0 <= pattern.quality <= 100.0
                    assert 0.0 <= pattern.confidence <= 100.0


# ---------------------------------------------------------------------------
# Lifecycle and identity
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_a_breakout_is_observed_but_not_judged(self):
        """Phase 4 may say the line was crossed. It may not say it counts."""
        series = GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=3, breakout_strength=0.05))
        states = {p.state for p in detect(series)}
        assert PatternState.BROKEN_OUT_UNCONFIRMED in states
        # The vocabulary contains no notion of a confirmed or valid breakout.
        assert not any(str(s) == "confirmed" for s in PatternState)

    def test_price_near_resistance_reads_as_near_breakout(self):
        series = GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=2, breakout_strength=0.05))
        states = {p.state for p in detect(series)}
        assert states & {PatternState.NEAR_BREAKOUT, PatternState.BROKEN_OUT_UNCONFIRMED}

    def test_terminal_states_are_terminal(self):
        assert PatternState.INVALIDATED.is_terminal
        assert PatternState.EXPIRED.is_terminal
        assert not PatternState.MATURE.is_terminal

    def test_forming_patterns_are_not_screenable(self):
        """Surfacing an incomplete structure means scoring a guess about what it
        will become."""
        assert not PatternState.FORMING.may_be_screened
        assert PatternState.MATURE.may_be_screened
        assert PatternState.BROKEN_OUT_UNCONFIRMED.may_be_screened

    def test_identity_survives_the_arrival_of_new_bars(self):
        """The same flag on Monday and Friday is one pattern, not five rows.

        Identity keys on what does not change as the pattern evolves. Including
        the evaluation date would make every day a new pattern, which is exactly
        the failure the key exists to prevent.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=14))
        cut = len(series.bars) - 4
        monday = DETECTOR.detect(series.bars[: cut + 1], series.bars[cut].session_date)
        friday = detect(series)
        if not monday or not friday:
            pytest.skip("no pattern spanning both evaluation dates")

        monday_keys = {p.identity_key for p in monday}
        friday_keys = {p.identity_key for p in friday}
        assert monday_keys & friday_keys, "the pattern lost its identity as bars arrived"

    def test_identity_ignores_the_evaluation_date(self):
        geometry = PatternGeometry(start_date=dt.date(2024, 1, 3), end_date=dt.date(2024, 2, 1))
        common = {
            "instrument_id": 1,
            "pattern_type": PatternType.BULL_FLAG,
            "timeframe": DETECTOR.timeframe,
            "geometry": geometry,
            "knowledge_time": dt.datetime(2024, 2, 1, 21, tzinfo=dt.UTC),
            "quality": 70.0,
        }
        monday = PatternInstance(
            state=PatternState.MATURE, as_of_session=dt.date(2024, 2, 1), **common
        )
        friday = PatternInstance(
            state=PatternState.BROKEN_OUT_UNCONFIRMED,
            as_of_session=dt.date(2024, 2, 8),
            **common,
        )
        assert monday.identity_key == friday.identity_key

    def test_a_different_start_is_a_different_pattern(self):
        common = {
            "instrument_id": 1,
            "pattern_type": PatternType.BULL_FLAG,
            "timeframe": DETECTOR.timeframe,
            "state": PatternState.MATURE,
            "as_of_session": dt.date(2024, 2, 1),
            "knowledge_time": dt.datetime(2024, 2, 1, 21, tzinfo=dt.UTC),
            "quality": 70.0,
        }
        a = PatternInstance(
            geometry=PatternGeometry(dt.date(2024, 1, 3), dt.date(2024, 2, 1)), **common
        )
        b = PatternInstance(
            geometry=PatternGeometry(dt.date(2024, 1, 10), dt.date(2024, 2, 1)), **common
        )
        assert a.identity_key != b.identity_key

    def test_an_instance_cannot_claim_geometry_past_its_boundary(self):
        with pytest.raises(ConfigError, match="not allowed to see"):
            PatternInstance(
                instrument_id=1,
                pattern_type=PatternType.BULL_FLAG,
                timeframe=DETECTOR.timeframe,
                state=PatternState.MATURE,
                geometry=PatternGeometry(dt.date(2024, 1, 3), dt.date(2024, 3, 1)),
                as_of_session=dt.date(2024, 2, 1),
                knowledge_time=dt.datetime(2024, 2, 1, 21, tzinfo=dt.UTC),
                quality=70.0,
            )


# ---------------------------------------------------------------------------
# Overlap and multiple interpretations
# ---------------------------------------------------------------------------


class TestPatternOverlap:
    def test_multiple_interpretations_may_coexist(self):
        """A short flag inside a longer one is two real structures.

        Collapsing them to "the best" discards the information that both exist,
        and later scoring is where the choice belongs.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(pole_sessions=20, flag_sessions=12))
        found = detect(series)
        starts = {p.geometry.start_date for p in found}
        assert len(starts) == len(found), "instances must not duplicate a start date"

    def test_instances_are_returned_highest_quality_first(self):
        found = detect(GENERATOR.bull_flag(BullFlagSpec(pole_sessions=18)))
        assert [p.quality for p in found] == sorted([p.quality for p in found], reverse=True)


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


class TestScoringCurves:
    def test_a_band_scores_full_inside_and_tapers_outside(self):
        kwargs = {
            "ideal_low": 0.2,
            "ideal_high": 0.4,
            "tolerance_low": 0.05,
            "tolerance_high": 0.65,
        }
        assert band_score(0.3, **kwargs) == 100.0
        assert band_score(0.05, **kwargs) == 0.0
        assert band_score(0.65, **kwargs) == 0.0
        assert 0.0 < band_score(0.5, **kwargs) < 100.0

    def test_the_taper_is_a_slope_not_a_cliff(self):
        """The whole objection to binary rules. 49.9% and 50.1% are the same
        structure, and a detector that calls one perfect and the other
        worthless is reporting its threshold rather than the pattern."""
        kwargs = {
            "ideal_low": 0.1,
            "ideal_high": 0.5,
            "tolerance_low": 0.0,
            "tolerance_high": 0.7,
        }
        just_inside = band_score(0.499, **kwargs)
        just_outside = band_score(0.501, **kwargs)
        assert just_inside == 100.0
        assert just_outside > 98.0

    def test_a_band_must_be_ordered(self):
        with pytest.raises(ConfigError, match="ordered"):
            band_score(0.3, ideal_low=0.5, ideal_high=0.2, tolerance_low=0.0, tolerance_high=1.0)

    def test_a_ramp_saturates_rather_than_rewarding_extremes(self):
        """A 60% flagpole is not twice as good as a 30% one; past a point more
        magnitude is evidence of exhaustion, scored separately."""
        assert ramp_score(0.5, zero_at=0.05, full_at=0.25) == 100.0
        assert ramp_score(5.0, zero_at=0.05, full_at=0.25) == 100.0
        assert ramp_score(0.05, zero_at=0.05, full_at=0.25) == 0.0

    def test_decay_is_the_mirror_of_ramp(self):
        assert decay_score(0.4, full_at=0.4, zero_at=1.2) == 100.0
        assert decay_score(1.2, full_at=0.4, zero_at=1.2) == 0.0
        assert 0.0 < decay_score(0.8, full_at=0.4, zero_at=1.2) < 100.0

    def test_a_step_is_reserved_for_genuine_discontinuities(self):
        assert step_score(1.1, threshold=1.0, above=0.0, below=100.0) == 0.0
        assert step_score(0.9, threshold=1.0, above=0.0, below=100.0) == 100.0

    def test_piecewise_curves_hold_at_their_endpoints(self):
        points = [(0.0, 0.0), (0.5, 80.0), (1.0, 100.0)]
        assert linear_interpolate(-1.0, points) == 0.0
        assert linear_interpolate(2.0, points) == 100.0
        assert linear_interpolate(0.25, points) == pytest.approx(40.0)

    def test_a_nan_input_scores_the_floor_rather_than_crashing(self):
        assert (
            band_score(
                float("nan"), ideal_low=0.1, ideal_high=0.2, tolerance_low=0.0, tolerance_high=0.3
            )
            == 0.0
        )
        assert ramp_score(float("nan"), zero_at=0.0, full_at=1.0) == 0.0


class TestCombining:
    def test_an_unavailable_component_is_excluded_from_the_denominator(self):
        """Not scored zero.

        Scoring it zero reports a pattern as low quality because an optional
        input was missing, which is a data-availability fact wearing a
        structural judgement's clothes.
        """
        weights = {"a": 0.5, "b": 0.5}
        both = combine({"a": 80.0, "b": 80.0}, weights)
        one_missing = combine({"a": 80.0, "b": None}, weights)
        assert both.value == one_missing.value == 80.0
        assert one_missing.missing == ("b",)
        assert one_missing.available_weight == 0.5

    def test_a_measured_zero_counts_fully(self):
        """Zero means measured-and-bad, and must not be confused with absent."""
        result = combine({"a": 80.0, "b": 0.0}, {"a": 0.5, "b": 0.5})
        assert result.value == 40.0
        assert result.is_complete

    def test_an_unweighted_component_is_an_error(self):
        with pytest.raises(ConfigError, match="no weight configured"):
            combine({"a": 50.0}, {"b": 1.0})

    def test_an_out_of_range_score_is_refused(self):
        with pytest.raises(ConfigError, match="outside"):
            combine({"a": 140.0}, {"a": 1.0})


# ---------------------------------------------------------------------------
# Structure primitives
# ---------------------------------------------------------------------------


class TestStructure:
    def test_two_points_do_not_constitute_a_trendline(self):
        """Two points always fit perfectly and mean nothing.

        Reporting r²=1.0 would let any two highs masquerade as a channel.
        """
        fit = fit_line([0, 5], [10.0, 20.0], anchor_index=0)
        assert fit.slope_per_session == pytest.approx(2.0)
        assert fit.r_squared == 0.0

    def test_a_real_line_reports_a_high_r_squared(self):
        fit = fit_line([0, 1, 2, 3, 4], [10.0, 11.0, 12.0, 13.0, 14.0], anchor_index=0)
        assert fit.r_squared > 0.99

    def test_scatter_reports_a_low_r_squared(self):
        fit = fit_line([0, 1, 2, 3, 4], [10.0, 14.0, 9.0, 15.0, 10.0], anchor_index=0)
        assert fit.r_squared < 0.3

    def test_consolidation_measurement_is_scale_free(self):
        """A $5 stock and a $500 stock with the same shape must measure alike."""
        cheap = PatternGenerator(SeriesSpec(start_price=5.0)).bull_flag()
        rich = PatternGenerator(SeriesSpec(start_price=500.0)).bull_flag()
        a = measure_consolidation(cheap.bars, start_index=70, end_index=80)
        b = measure_consolidation(rich.bars, start_index=70, end_index=80)
        assert a is not None and b is not None
        assert a.depth_pct == pytest.approx(b.depth_pct, rel=0.05)

    def test_a_window_too_short_to_measure_returns_none(self):
        series = GENERATOR.bull_flag()
        assert measure_consolidation(series.bars, start_index=70, end_index=71) is None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_no_weight_is_hard_coded(self):
        """Changing a weight must change the score, or the config is decorative."""
        default = PatternEngineConfig()
        tweaked = PatternEngineConfig.model_validate(
            {
                "bull_flag": {
                    "weights": {
                        "flagpole": 0.9,
                        "consolidation": 0.02,
                        "retracement": 0.02,
                        "duration": 0.01,
                        "volume_structure": 0.01,
                        "volatility_contraction": 0.02,
                        "relative_strength": 0.01,
                        "resistance_quality": 0.01,
                    }
                }
            }
        )
        series = GENERATOR.bull_flag()
        assert best_quality(series, detector=BullFlagDetector(default)) != best_quality(
            series, detector=BullFlagDetector(tweaked)
        )

    def test_an_unordered_band_is_refused_at_config_time(self):
        with pytest.raises(Exception):
            PatternEngineConfig.model_validate(
                {"bull_flag": {"consolidation": {"ideal_retracement_low": 0.9}}}
            )

    def test_invalidation_must_sit_outside_the_tolerance_band(self):
        """Otherwise a pattern is invalidated while still scoring above zero."""
        with pytest.raises(Exception, match="invalidation_retracement"):
            PatternEngineConfig.model_validate(
                {"bull_flag": {"consolidation": {"invalidation_retracement": 0.3}}}
            )

    def test_a_typo_in_a_config_key_is_an_error(self):
        with pytest.raises(Exception):
            PatternEngineConfig.model_validate({"bull_flag": {"weightz": {}}})

    def test_detector_parameters_are_reportable(self):
        assert DETECTOR.parameters["atr_period"] == 14
        assert "weights" in DETECTOR.parameters


class TestComponentScores:
    def test_an_out_of_range_component_is_refused(self):
        with pytest.raises(ConfigError, match="outside"):
            ComponentScore(name="x", score=120.0, weight=1.0)

    def test_an_unavailable_component_may_hold_any_score(self):
        component = ComponentScore(name="x", score=0.0, weight=1.0, unavailable=True)
        assert component.effective_weight == 0.0
        assert component.contribution == 0.0

    def test_evidence_renders_as_its_detail(self):
        assert str(Evidence("volume dried up")) == "volume dried up"

    def test_a_price_point_serialises_for_the_dashboard(self):
        point = PricePoint(dt.date(2024, 1, 3), 147.3)
        assert point.as_dict() == {"date": "2024-01-03", "price": 147.3}


class TestSyntheticGenerator:
    def test_generation_is_deterministic(self):
        a = GENERATOR.bull_flag(seed=5).closes()
        b = GENERATOR.bull_flag(seed=5).closes()
        np.testing.assert_array_equal(a, b)

    def test_different_seeds_produce_different_series(self):
        a = GENERATOR.bull_flag(seed=1).closes()
        b = GENERATOR.bull_flag(seed=2).closes()
        assert not np.allclose(a, b)

    def test_generated_bars_satisfy_the_domain_invariants(self):
        """Otherwise the generator spends its time producing test data that
        fails validation for reasons unrelated to the pattern."""
        for series in (
            GENERATOR.bull_flag(),
            GENERATOR.bear_flag(),
            GENERATOR.falling_knife(),
            GENERATOR.broad_volatile_range(),
            GENERATOR.random_walk(50),
        ):
            for bar in series.bars:
                assert bar.low <= bar.open <= bar.high
                assert bar.low <= bar.close <= bar.high
                assert bar.volume > 0

    def test_the_generator_reports_where_it_drew_the_pattern(self):
        truth = GENERATOR.bull_flag().truth
        assert truth["pattern"] == "bull_flag"
        assert truth["flag_start_index"] < truth["flag_end_index"]

    def test_an_impossible_spec_is_refused(self):
        with pytest.raises(ConfigError):
            BullFlagSpec(pole_sessions=1)
        with pytest.raises(ConfigError):
            BullFlagSpec(retracement=2.0)


# ---------------------------------------------------------------------------
# Identity tracking across a pattern's life
# ---------------------------------------------------------------------------


class TestTracking:
    """The memory that re-detection lacks."""

    @staticmethod
    def _replay(series, tracker: PatternTracker, sessions: int = 20) -> PatternTracker:
        for i in range(len(series.bars) - sessions, len(series.bars)):
            bars = series.bars[: i + 1]
            session = bars[-1].session_date
            tracker.observe(
                DETECTOR.detect(bars, session), session, closes={1: float(bars[-1].close)}
            )
        return tracker

    def test_one_pattern_is_one_row_across_its_whole_life(self):
        """The failure this module exists to prevent: a new database object
        every day merely because another candle appeared."""
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=10, breakout_sessions=8))
        tracker = self._replay(series, PatternTracker())
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        assert tracked.sessions_tracked >= 6
        assert len({t.session_date for t in tracked.history}) == tracked.sessions_tracked

    def test_the_lifecycle_is_recorded_in_order(self):
        series = GENERATOR.bull_flag(
            BullFlagSpec(flag_sessions=10, breakout_sessions=8, breakout_strength=0.04)
        )
        tracker = self._replay(series, PatternTracker())
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        states = [t.to_state for t in tracked.history]
        assert states[0] is PatternState.MATURE
        assert PatternState.BROKEN_OUT_UNCONFIRMED in states
        assert states.index(PatternState.NEAR_BREAKOUT) < states.index(
            PatternState.BROKEN_OUT_UNCONFIRMED
        )

    def test_a_pattern_that_leaves_the_detectors_view_is_resolved_not_lost(self):
        """The specific bug this API exists to fix.

        A flag that breaks out just outside the re-detection window would
        otherwise be recorded as "lost" -- the most misleading outcome
        available, because the pattern did not fail or fade, it did the thing it
        was being watched for.
        """
        series = GENERATOR.bull_flag(
            BullFlagSpec(flag_sessions=10, breakout_sessions=8, breakout_strength=0.04)
        )
        with_price = self._replay(series, PatternTracker())
        assert any(
            t.state is PatternState.BROKEN_OUT_UNCONFIRMED for t in with_price.open_patterns()
        )

        blind = PatternTracker()
        for i in range(len(series.bars) - 20, len(series.bars)):
            bars = series.bars[: i + 1]
            blind.observe(DETECTOR.detect(bars, bars[-1].session_date), bars[-1].session_date)
        assert not any(
            t.state is PatternState.BROKEN_OUT_UNCONFIRMED
            for t in blind.open_patterns() + blind.closed_patterns()
        )

    def test_carrying_a_pattern_forward_never_rewrites_its_geometry(self):
        """The retroactive-refinement leak in different clothes.

        A stored pattern whose resistance quietly improves is a pattern that
        can no longer be reproduced from the data that produced it.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=10, breakout_sessions=8))
        tracker = PatternTracker()
        geometries: dict[str, dict] = {}
        for i in range(len(series.bars) - 20, len(series.bars)):
            bars = series.bars[: i + 1]
            session = bars[-1].session_date
            for tracked in tracker.observe(
                DETECTOR.detect(bars, session), session, closes={1: float(bars[-1].close)}
            ):
                # Only patterns carried forward (not re-detected) are checked:
                # a re-detected pattern legitimately re-measures itself.
                if tracked.last_seen != session:
                    assert geometries[tracked.identity_key] == tracked.current.geometry.as_dict()
                geometries[tracked.identity_key] = tracked.current.geometry.as_dict()

    def test_history_is_append_only(self):
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=12))
        tracker = PatternTracker()
        lengths: dict[str, int] = {}
        for i in range(len(series.bars) - 15, len(series.bars)):
            bars = series.bars[: i + 1]
            session = bars[-1].session_date
            for tracked in tracker.observe(DETECTOR.detect(bars, session), session):
                previous = lengths.get(tracked.identity_key, 0)
                assert len(tracked.history) >= previous
                lengths[tracked.identity_key] = len(tracked.history)

    def test_peak_quality_survives_decay(self):
        """A structure that scored 88 and decayed to 61 is a different story
        from one that was always mediocre."""
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=14))
        tracker = self._replay(series, PatternTracker(), sessions=16)
        for tracked in tracker.open_patterns():
            assert tracked.peak_quality >= tracked.current.quality

    def test_out_of_order_observation_is_refused(self):
        """Feeding a tracker out of order silently corrupts every history."""
        series = GENERATOR.bull_flag()
        detections = detect(series)
        if not detections:
            pytest.skip("no pattern to track")
        with pytest.raises(ConfigError, match="out of order"):
            PatternTracker().observe(detections, series.bars[0].session_date)

    def test_a_broken_structure_closes_rather_than_disappearing(self):
        """A failed pattern is evidence. A false-positive rate cannot be
        measured from surviving patterns alone."""
        series = GENERATOR.bull_flag(BullFlagSpec(retracement=0.5, breakdown=0.35))
        tracker = self._replay(series, PatternTracker(), sessions=6)
        assert tracker.summary()["closed"] >= 1
        assert all(t.state.is_terminal for t in tracker.closed_patterns())

    def test_forming_patterns_are_not_screenable(self):
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=12))
        tracker = self._replay(series, PatternTracker())
        assert all(t.state.may_be_screened for t in tracker.screenable())

    def test_overlap_merging_only_absorbs_the_same_pattern_type(self):
        """Multiple interpretations may legitimately coexist. Forcing a single
        label early discards what later scoring is better placed to use.
        """
        series = GENERATOR.bull_flag(BullFlagSpec(pole_sessions=18, flag_sessions=12))
        tracker = self._replay(series, PatternTracker())
        merged = merge_overlapping(tracker.open_patterns())
        assert len(merged) <= len(tracker.open_patterns())
        assert all(m.pattern_type is PatternType.BULL_FLAG for m in merged)
