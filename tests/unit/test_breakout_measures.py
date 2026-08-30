"""Measurement primitives, and the four distinctions they exist to preserve.

Item 4 of the brief lists four things that must not be collapsed: touching a
level, penetrating it intraday, closing through it, and being accepted above it.
Item 8 adds a fifth distinction that is easier to get wrong and harder to
notice: observed volume, time-normalised volume, projected volume and completed
relative volume are four quantities, and a partial session has no value for the
fourth.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.breakouts.base import GapClass
from tradeit.breakouts.boundary import BreakoutBoundary
from tradeit.breakouts.config import (
    ApproachConfig,
    CandleQualityConfig,
    FollowThroughConfig,
    IntradayVolumeConfig,
    PenetrationConfig,
    VolumeConfirmationConfig,
)
from tradeit.breakouts.measures import (
    average_true_range,
    boundary_quality_score,
    classify_gap,
    measure_acceptance,
    measure_approach,
    measure_candle,
    measure_follow_through,
    measure_penetration,
    measure_rejection,
)
from tradeit.breakouts.volume import (
    build_intraday_curve,
    measure_volume,
    score_volume,
)
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError

UTC = dt.UTC
START = dt.date(2023, 1, 2)


def sessions(count: int) -> list[dt.date]:
    out: list[dt.date] = []
    day = START
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def bar(
    session: dt.date,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1_000_000.0,
) -> OhlcvBar:
    return OhlcvBar(
        instrument_id=1,
        timeframe=Bartimeframe.D1,
        session_date=session,
        open=Decimal(f"{open_:.4f}"),
        high=Decimal(f"{max(high, open_, close):.4f}"),
        low=Decimal(f"{min(low, open_, close):.4f}"),
        close=Decimal(f"{close:.4f}"),
        volume=Decimal(f"{volume:.0f}"),
        event_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_time=dt.datetime.combine(session, dt.time(21, 30), tzinfo=UTC),
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def flat_series(n: int, price: float = 98.0, volume: float = 1_000_000.0) -> list[OhlcvBar]:
    return [bar(day, price, price * 1.005, price * 0.995, price, volume) for day in sessions(n)]


BOUNDARY = BreakoutBoundary(
    nominal=100.0,
    anchor_date=START,
    tolerance_pct=0.005,
    confidence=70.0,
    method="swing_highs",
    touch_count=4,
    atr_at_open=2.0,
    pattern_key="p",
    pattern_type="flat_base",
    pattern_quality=80.0,
)


class TestAtr:
    def test_a_short_series_yields_none_rather_than_a_shorter_window(self):
        """A caller handed a number cannot tell it came from four bars, and
        every ATR-relative threshold downstream would change meaning."""
        assert average_true_range(flat_series(5), 14) is None

    def test_atr_is_positive_on_a_real_series(self):
        value = average_true_range(flat_series(40), 14)
        assert value is not None and value > 0


class TestPenetration:
    def test_the_four_events_are_separately_reported(self):
        """Item 4: touching, penetrating, closing through and being accepted
        are four different facts and a bar can be several at once."""
        b = bar(START, 99.0, 100.9, 98.9, 99.2)
        reading = measure_penetration(b, 99.0, BOUNDARY, config=PenetrationConfig())
        assert reading.touched
        assert reading.penetrated_intraday
        assert not reading.closed_above

    def test_a_high_above_the_level_that_closes_below_is_not_a_close_above(self):
        """The brief's own example: high 0.3% above resistance, close below."""
        b = bar(START, 99.5, 100.3, 99.0, 99.4)
        reading = measure_penetration(b, 99.5, BOUNDARY, config=PenetrationConfig())
        assert not reading.closed_above

    def test_penetration_and_extension_move_in_opposite_directions(self):
        """Both facts are true of a big move and they are reported separately.

        Averaging them into one hump would let the engine claim it had weighed a
        trade-off it has no authority to weigh.
        """
        modest = measure_penetration(
            bar(START, 100.0, 101.6, 100.0, 101.5), 100.0, BOUNDARY, config=PenetrationConfig()
        )
        far = measure_penetration(
            bar(START, 100.0, 108.5, 100.0, 108.0), 100.0, BOUNDARY, config=PenetrationConfig()
        )
        assert far.penetration_score >= modest.penetration_score
        assert far.extension_score < modest.extension_score

    def test_gaps_are_banded_by_atr(self):
        assert classify_gap(None) is GapClass.NONE
        assert classify_gap(0.1) is GapClass.NONE
        assert classify_gap(0.5) is GapClass.SMALL
        assert classify_gap(1.5) is GapClass.MODERATE
        assert classify_gap(3.0) is GapClass.LARGE
        assert classify_gap(6.0) is GapClass.EXTREME

    def test_a_gap_through_the_level_is_recorded(self):
        reading = measure_penetration(
            bar(START, 104.0, 105.0, 103.5, 104.5), 99.0, BOUNDARY, config=PenetrationConfig()
        )
        assert reading.gapped_above
        assert reading.gap_class is not GapClass.NONE

    def test_without_atr_the_penetration_score_is_still_defined(self):
        b = BreakoutBoundary(nominal=100.0, anchor_date=START, tolerance_pct=0.005, confidence=50.0)
        reading = measure_penetration(
            bar(START, 100.0, 103.0, 100.0, 102.5), 100.0, b, config=PenetrationConfig()
        )
        assert reading.penetration_atr is None
        assert reading.penetration_score > 0


class TestCandle:
    def test_closing_near_the_high_beats_closing_near_the_low(self):
        """The mechanical reason: a bar that traded up through a level and
        closed at its low leaves every buyer above the level underwater."""
        config = CandleQualityConfig()
        strong = measure_candle(
            bar(START, 100.0, 103.0, 99.8, 102.9), 100.0, atr=2.0, config=config
        )
        weak = measure_candle(bar(START, 100.0, 103.0, 99.8, 100.1), 100.0, atr=2.0, config=config)
        assert strong.close_location > weak.close_location
        assert strong.close_strength_score > weak.close_strength_score
        assert strong.score > weak.score

    def test_a_large_upper_wick_lowers_the_score(self):
        config = CandleQualityConfig()
        clean = measure_candle(bar(START, 100.0, 102.2, 99.9, 102.1), 100.0, atr=2.0, config=config)
        wicky = measure_candle(bar(START, 100.0, 105.0, 99.9, 102.1), 100.0, atr=2.0, config=config)
        assert wicky.upper_wick_fraction > clean.upper_wick_fraction
        assert wicky.score < clean.score

    def test_a_zero_range_bar_does_not_divide_by_zero(self):
        """A limit-up print has no shape to read, and the close is placed where
        a bar that opened, high'd, low'd and closed at one price actually did."""
        flat = bar(START, 100.0, 100.0, 100.0, 100.0)
        reading = measure_candle(flat, 99.0, atr=2.0, config=CandleQualityConfig())
        assert reading.close_location == 1.0
        assert 0.0 <= reading.score <= 100.0


class TestApproach:
    def test_distance_falls_as_price_nears_the_level(self):
        config = ApproachConfig()
        far = measure_approach(flat_series(40, price=88.0), BOUNDARY, config=config)
        near = measure_approach(flat_series(40, price=99.4), BOUNDARY, config=config)
        assert near.score > far.score
        assert near.close_distance_pct < far.close_distance_pct

    def test_testing_is_a_narrower_condition_than_approaching(self):
        config = ApproachConfig()
        near = measure_approach(flat_series(40, price=99.9), BOUNDARY, config=config)
        approaching = measure_approach(flat_series(40, price=96.5), BOUNDARY, config=config)
        assert near.is_testing
        assert approaching.is_approaching
        assert not approaching.is_testing

    def test_the_approach_reading_is_informational_only(self):
        """It says a name is worth watching; it does not say a breakout
        happened, and nothing in the reading claims otherwise."""
        reading = measure_approach(flat_series(40, price=99.5), BOUNDARY, config=ApproachConfig())
        assert not hasattr(reading, "closed_above")
        assert set(reading.to_measurements()) & {"approach_distance_score"}


class TestAcceptance:
    def test_consecutive_closes_reset_on_a_close_back_inside(self):
        """'Five of the last eight closed above' and 'the last five did' are
        different facts, and only the second is acceptance."""
        days = sessions(6)
        window = [
            bar(days[0], 101.0, 101.5, 100.8, 101.2),
            bar(days[1], 101.2, 101.6, 100.9, 101.4),
            bar(days[2], 101.4, 101.5, 99.0, 99.5),
            bar(days[3], 99.5, 101.3, 99.4, 101.2),
            bar(days[4], 101.2, 101.7, 101.0, 101.5),
        ]
        reading = measure_acceptance(window, BOUNDARY, target_closes=4)
        assert reading.closes_above == 4
        assert reading.consecutive_closes == 2

    def test_an_empty_window_is_not_an_error(self):
        reading = measure_acceptance([], BOUNDARY, target_closes=4)
        assert reading.closes_above == 0
        assert reading.score == 0.0


class TestFollowThrough:
    def test_no_elapsed_sessions_means_unavailable_not_zero(self):
        """A breakout on its first day has produced no follow-through evidence
        either way. Scoring it zero would penalise the passage of time."""
        reading = measure_follow_through(
            [],
            breakout_close=102.0,
            breakout_volume=2_000_000.0,
            atr=2.0,
            config=FollowThroughConfig(),
        )
        assert not reading.available
        assert reading.sessions == 0

    def test_progress_scores_above_stagnation(self):
        days = sessions(4)
        advancing = [
            bar(days[0], 102.0, 103.5, 101.9, 103.4),
            bar(days[1], 103.4, 105.0, 103.2, 104.8),
        ]
        stalling = [
            bar(days[0], 102.0, 102.2, 100.5, 100.8),
            bar(days[1], 100.8, 101.0, 99.8, 100.0),
        ]
        config = FollowThroughConfig()
        good = measure_follow_through(
            advancing,
            breakout_close=102.0,
            breakout_volume=2_000_000.0,
            atr=2.0,
            config=config,
        )
        bad = measure_follow_through(
            stalling,
            breakout_close=102.0,
            breakout_volume=2_000_000.0,
            atr=2.0,
            config=config,
        )
        assert good.score > bad.score
        assert good.higher_closes > bad.higher_closes


class TestRejection:
    def test_a_penetrating_bar_that_closes_at_its_low_is_rejected(self):
        reading = measure_rejection(
            bar(START, 100.2, 102.0, 99.0, 99.2),
            BOUNDARY,
            relative_volume=1.8,
            config_wick=0.5,
            config_close_ceiling=0.35,
            heavy_volume=1.5,
        )
        assert reading.rejected
        assert reading.score > 0

    def test_a_bar_that_never_cleared_the_zone_is_not_a_rejection(self):
        reading = measure_rejection(
            bar(START, 99.0, 100.2, 98.9, 99.0),
            BOUNDARY,
            relative_volume=1.0,
            config_wick=0.5,
            config_close_ceiling=0.35,
            heavy_volume=1.5,
        )
        assert not reading.rejected

    def test_heavier_volume_makes_a_rejection_more_significant(self):
        """Being turned back on heavy volume means size was willing to sell."""
        light = measure_rejection(
            bar(START, 100.2, 102.0, 99.0, 99.2),
            BOUNDARY,
            relative_volume=0.6,
            config_wick=0.5,
            config_close_ceiling=0.35,
            heavy_volume=1.5,
        )
        heavy = measure_rejection(
            bar(START, 100.2, 102.0, 99.0, 99.2),
            BOUNDARY,
            relative_volume=2.5,
            config_wick=0.5,
            config_close_ceiling=0.35,
            heavy_volume=1.5,
        )
        assert heavy.score > light.score


class TestBoundaryQuality:
    def test_more_touches_and_a_better_pattern_score_higher(self):
        weak, _ = boundary_quality_score(
            BreakoutBoundary(
                nominal=100.0,
                anchor_date=START,
                tolerance_pct=0.005,
                confidence=20.0,
                method="single_extreme",
                touch_count=1,
            )
        )
        strong, _ = boundary_quality_score(BOUNDARY)
        assert strong > weak

    def test_an_unattached_level_is_neither_credited_nor_zeroed(self):
        """A level from prior highs is a real structure; it just is not one a
        detector claimed."""
        _score, measurements = boundary_quality_score(
            BreakoutBoundary(
                nominal=100.0,
                anchor_date=START,
                tolerance_pct=0.005,
                confidence=50.0,
                touch_count=3,
            )
        )
        assert 0 < measurements["boundary_pattern_quality"] < 100


class TestVolume:
    def test_relative_volume_excludes_the_bar_being_measured(self):
        """Including a 5x day in the average it is compared against dilutes the
        very signal the comparison exists to detect."""
        series = flat_series(30, volume=1_000_000.0)
        series[-1] = bar(series[-1].session_date, 98.0, 98.5, 97.5, 98.0, 5_000_000.0)
        reading = measure_volume(series, config=VolumeConfirmationConfig())
        assert reading.completed_relative_volume == pytest.approx(5.0, rel=1e-6)

    def test_a_partial_bar_has_no_completed_relative_volume(self):
        """Item 8. A caller wanting an in-progress figure must ask for the
        projection by name, and the name says what it is."""
        series = flat_series(30)
        reading = measure_volume(
            series,
            config=VolumeConfirmationConfig(),
            intraday_config=IntradayVolumeConfig(),
            is_partial=True,
            observed_at=dt.time(10, 15),
            curve=None,
        )
        assert reading.is_partial
        assert reading.completed_relative_volume is None
        assert reading.projection_unavailable_reason

    def test_the_four_quantities_stay_distinct(self):
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        series = flat_series(30)
        series[-1] = bar(series[-1].session_date, 98.0, 98.2, 97.9, 98.0, 800_000.0)
        reading = measure_volume(
            series,
            config=VolumeConfirmationConfig(),
            intraday_config=IntradayVolumeConfig(buckets=13),
            is_partial=True,
            observed_at=dt.time(10, 15),
            curve=curve,
        )
        assert reading.current_observed_volume == 800_000.0
        assert reading.completed_relative_volume is None
        assert reading.projected_volume is not None
        assert reading.projected_volume > reading.current_observed_volume
        assert reading.used_projection

    def test_the_brief_s_own_example_is_not_relative_volume_zero_point_four(self):
        """800k at 10:15 against a 2m average is heavy, not 0.4x."""
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        series = flat_series(30, volume=2_000_000.0)
        series[-1] = bar(series[-1].session_date, 98.0, 98.2, 97.9, 98.0, 800_000.0)
        reading = measure_volume(
            series,
            config=VolumeConfirmationConfig(),
            intraday_config=IntradayVolumeConfig(buckets=13),
            is_partial=True,
            observed_at=dt.time(10, 15),
            curve=curve,
        )
        naive = 800_000.0 / 2_000_000.0
        assert reading.projected_relative_volume is not None
        assert reading.projected_relative_volume > naive * 2

    def test_a_projection_is_withheld_at_the_very_open(self):
        """Dividing by 2% of a session multiplies its noise by fifty."""
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        reading = measure_volume(
            flat_series(30),
            config=VolumeConfirmationConfig(),
            intraday_config=IntradayVolumeConfig(buckets=13, min_elapsed_fraction=0.5),
            is_partial=True,
            observed_at=dt.time(9, 35),
            curve=curve,
        )
        assert reading.projected_volume is None
        assert "elapsed" in reading.projection_unavailable_reason

    def test_the_curve_is_built_only_from_complete_prior_sessions(self):
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        assert curve.sessions_used == 20
        assert curve.fractions[-1] == pytest.approx(1.0)
        assert list(curve.fractions) == sorted(curve.fractions)

    def test_the_curve_is_not_the_clock(self):
        """Volume is front- and back-loaded; a flat 'fraction of the day
        elapsed' normalisation understates the morning."""
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        at = dt.time(10, 15)
        assert curve.expected_fraction(at) > curve.elapsed_fraction(at)

    def test_too_few_sessions_yields_no_curve(self):
        assert build_intraday_curve(_intraday_sessions(2), config=IntradayVolumeConfig()) is None

    def test_a_non_monotone_curve_is_refused(self):
        from tradeit.breakouts.volume import IntradayVolumeCurve

        with pytest.raises(ConfigError, match="non-decreasing"):
            IntradayVolumeCurve(
                fractions=(0.5, 0.3, 1.0),
                sessions_used=5,
                session_start=dt.time(9, 30),
                session_end=dt.time(16, 0),
            )

    def test_scores_are_none_with_a_reason_rather_than_zero(self):
        """Zero means measured and bad; an instrument with five bars has not
        been measured at all."""
        scores = score_volume(
            measure_volume(flat_series(3), config=VolumeConfirmationConfig()),
            config=VolumeConfirmationConfig(),
            family="flat_base",
        )
        assert scores.confirmation is None
        assert scores.unavailable_reason

    def test_the_family_target_changes_the_score(self):
        series = flat_series(30, volume=1_000_000.0)
        series[-1] = bar(series[-1].session_date, 98.0, 98.5, 97.5, 98.0, 1_600_000.0)
        reading = measure_volume(series, config=VolumeConfirmationConfig())
        config = VolumeConfirmationConfig()
        quiet_family = score_volume(reading, config=config, family="flat_base")
        loud_family = score_volume(reading, config=config, family="high_tight_flag")
        assert quiet_family.relative is not None and loud_family.relative is not None
        assert quiet_family.relative > loud_family.relative

    def test_using_a_projection_is_recorded_in_the_measurements(self):
        curve = build_intraday_curve(
            _intraday_sessions(20), config=IntradayVolumeConfig(buckets=13)
        )
        assert curve is not None
        reading = measure_volume(
            flat_series(30),
            config=VolumeConfirmationConfig(),
            intraday_config=IntradayVolumeConfig(buckets=13),
            is_partial=True,
            observed_at=dt.time(14, 0),
            curve=curve,
        )
        scores = score_volume(reading, config=VolumeConfirmationConfig(), family=None)
        assert scores.measurements.get("volume_from_projection") == 1.0


def _intraday_sessions(count: int) -> list[list[OhlcvBar]]:
    """Sessions with a U-shaped intraday volume profile.

    Heavy at the open and into the close, quiet at lunch — the shape that makes
    the difference between the clock and the curve visible.
    """
    shape = [3.0, 2.0, 1.4, 1.0, 0.8, 0.7, 0.7, 0.7, 0.8, 1.0, 1.4, 2.0, 3.0]
    out: list[list[OhlcvBar]] = []
    for index, day in enumerate(sessions(count)):
        out.append(
            [
                bar(day, 98.0, 98.3, 97.8, 98.0, 100_000.0 * weight * (1 + index * 0.01))
                for weight in shape
            ]
        )
    return out
