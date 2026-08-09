"""Measurements taken at and around the boundary.

Pure functions over a bar window and a frozen boundary. They measure and they
score; they do not decide states, and nothing here can see a bar the caller did
not hand it. That is the whole causality story for this module: a function that
takes ``bars[:i+1]`` cannot read ``bars[i+1]``, and every call site slices
before it calls.

The distinctions this module exists to preserve, in the order the brief states
them:

* **touching** a level, **penetrating** it intraday, **closing** through it, and
  **being accepted** above it are four different events. A high 0.3% above
  resistance that closes below is not a close above, and a system that
  collapses them will report the first as the third for roughly the reason a
  bad chart pattern library reports every touch as a breakout.
* **penetration** and **extension** are separate scores, not two ends of one
  curve. A close 3 ATR above the level is *strong* and *extended*; those are
  both true, they point in opposite directions for different consumers, and
  averaging them into a single hump would let the engine claim it had weighed a
  trade-off it has no authority to weigh.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from tradeit.breakouts.base import GapClass
from tradeit.breakouts.boundary import BreakoutBoundary
from tradeit.breakouts.config import (
    ApproachConfig,
    CandleQualityConfig,
    FollowThroughConfig,
    PenetrationConfig,
)
from tradeit.core.models import OhlcvBar
from tradeit.patterns.scoring import band_score, decay_score, ramp_score

#: ATR bands for the gap classification. Deliberately coarse: a finer scale
#: would imply a precision the underlying distinction does not have.
GAP_BANDS: tuple[tuple[float, GapClass], ...] = (
    (0.25, GapClass.NONE),
    (1.0, GapClass.SMALL),
    (2.0, GapClass.MODERATE),
    (4.0, GapClass.LARGE),
)


def true_range(bar: OhlcvBar, previous_close: float | None) -> float:
    """Wilder's true range. Falls back to the bar range on the first bar."""
    high, low = float(bar.high), float(bar.low)
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def average_true_range(bars: Sequence[OhlcvBar], period: int) -> float | None:
    """Wilder-smoothed ATR over the supplied bars, or ``None`` if too short.

    ``None`` rather than a shorter-window fallback. A caller that receives a
    number cannot tell it was computed from four bars, and every ATR-relative
    threshold downstream would silently change meaning.
    """
    if period < 2 or len(bars) < period + 1:
        return None
    ranges = [
        true_range(bar, float(bars[i - 1].close) if i else None) for i, bar in enumerate(bars)
    ]
    atr = float(np.mean(ranges[1 : period + 1]))
    for value in ranges[period + 1 :]:
        atr = (atr * (period - 1) + value) / period
    return atr


# ---------------------------------------------------------------------------
# Approach
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApproachReading:
    """How close price is to the boundary, and how it got there.

    ``score`` is APPROACH_DISTANCE_SCORE. It is informational: it says a name is
    worth watching, and explicitly does not say a breakout has happened. The
    state machine treats this reading as a classifier input, not a signal.
    """

    close_distance_pct: float
    high_distance_pct: float
    close_distance_atr: float | None
    #: Distance travelled toward the boundary over the lookback, in ATR.
    approach_rate_atr: float | None
    #: Recent range over the baseline range. Below 1 means compression.
    compression_ratio: float | None
    #: Volume over the baseline average during the approach.
    approach_volume_ratio: float | None
    is_approaching: bool
    is_testing: bool
    score: float

    def to_measurements(self) -> dict[str, float]:
        out = {
            "close_distance_pct": self.close_distance_pct,
            "high_distance_pct": self.high_distance_pct,
            "approach_distance_score": self.score,
        }
        for name, value in (
            ("close_distance_atr", self.close_distance_atr),
            ("approach_rate_atr", self.approach_rate_atr),
            ("compression_ratio", self.compression_ratio),
            ("approach_volume_ratio", self.approach_volume_ratio),
        ):
            if value is not None:
                out[name] = value
        return out


def measure_approach(
    bars: Sequence[OhlcvBar],
    boundary: BreakoutBoundary,
    *,
    config: ApproachConfig,
    sessions_from_anchor: int = 0,
) -> ApproachReading:
    """Where price stands relative to the boundary, using bars up to the last.

    ``bars`` must end on the session being evaluated. The compression and rate
    terms look backward from there; nothing looks forward, and the function has
    no way to.
    """
    last = bars[-1]
    close, high = float(last.close), float(last.high)
    level = boundary.level_on(sessions_from_anchor)
    atr = boundary.atr_at_open

    close_distance_pct = (level - close) / level
    high_distance_pct = (level - high) / level
    close_distance_atr = None if not atr else (level - close) / atr

    lookback = min(config.approach_lookback, len(bars))
    approach_rate_atr = None
    if atr and lookback >= 2:
        was = float(bars[-lookback].close)
        approach_rate_atr = ((level - was) - (level - close)) / atr

    compression_ratio = None
    baseline_n = min(config.compression_baseline, len(bars))
    if baseline_n >= lookback * 2:
        recent = _mean_range(bars[-lookback:])
        baseline = _mean_range(bars[-baseline_n:])
        if baseline > 0:
            compression_ratio = recent / baseline

    approach_volume_ratio = None
    if baseline_n >= lookback * 2:
        recent_v = float(np.mean([float(b.volume) for b in bars[-lookback:]]))
        baseline_v = float(np.mean([float(b.volume) for b in bars[-baseline_n:]]))
        if baseline_v > 0:
            approach_volume_ratio = recent_v / baseline_v

    within_atr = close_distance_atr is not None and close_distance_atr <= config.approach_atr
    within_pct = close_distance_pct <= config.approach_max_pct
    # Above the level counts as approaching too: an event that opens with price
    # already through the zone still needs the approach state to exist.
    is_approaching = (within_atr or close_distance_atr is None) and within_pct
    is_testing = (
        close_distance_atr is not None and abs(close_distance_atr) <= config.testing_atr
    ) or boundary.contains(high, sessions_from_anchor)

    if close_distance_atr is None:
        score = ramp_score(
            close_distance_pct,
            zero_at=config.approach_max_pct,
            full_at=0.0,
        )
    else:
        score = ramp_score(
            close_distance_atr,
            zero_at=config.approach_atr,
            full_at=0.0,
        )

    return ApproachReading(
        close_distance_pct=close_distance_pct,
        high_distance_pct=high_distance_pct,
        close_distance_atr=close_distance_atr,
        approach_rate_atr=approach_rate_atr,
        compression_ratio=compression_ratio,
        approach_volume_ratio=approach_volume_ratio,
        is_approaching=is_approaching,
        is_testing=is_testing,
        score=score,
    )


def _mean_range(bars: Sequence[OhlcvBar]) -> float:
    return float(np.mean([float(b.high) - float(b.low) for b in bars])) if bars else 0.0


# ---------------------------------------------------------------------------
# Penetration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PenetrationReading:
    """How far through the boundary this bar went, and by what.

    Four booleans rather than one enum because they are not mutually exclusive:
    a bar can touch, penetrate intraday and close above all at once, and a
    single "what happened" field would have to pick.
    """

    touched: bool
    penetrated_intraday: bool
    closed_above: bool
    gapped_above: bool

    close_above_pct: float
    high_above_pct: float
    close_above_atr: float | None
    high_above_atr: float | None
    #: Above the *threshold*, so clearing the ambiguity zone scores zero.
    penetration_atr: float | None
    #: How far above the level the close sits beyond the free allowance.
    extension_atr: float | None
    open_gap_pct: float
    open_gap_atr: float | None
    gap_class: GapClass

    penetration_score: float
    extension_score: float

    def to_measurements(self) -> dict[str, float]:
        out = {
            "close_above_pct": self.close_above_pct,
            "high_above_pct": self.high_above_pct,
            "open_gap_pct": self.open_gap_pct,
            "penetration_score": self.penetration_score,
            "extension_score": self.extension_score,
        }
        for name, value in (
            ("close_above_atr", self.close_above_atr),
            ("high_above_atr", self.high_above_atr),
            ("penetration_atr", self.penetration_atr),
            ("extension_atr", self.extension_atr),
            ("open_gap_atr", self.open_gap_atr),
        ):
            if value is not None:
                out[name] = value
        return out


def classify_gap(gap_atr: float | None) -> GapClass:
    """Band an opening gap by ATR. ``None`` ATR yields NONE, not a guess."""
    if gap_atr is None or gap_atr <= 0:
        return GapClass.NONE
    for upper, label in GAP_BANDS:
        if gap_atr < upper:
            return label
    return GapClass.EXTREME


def measure_penetration(
    bar: OhlcvBar,
    previous_close: float | None,
    boundary: BreakoutBoundary,
    *,
    config: PenetrationConfig,
    sessions_from_anchor: int = 0,
) -> PenetrationReading:
    """What this single bar did to the boundary.

    Note what is *not* an argument: any later bar. The reading is a property of
    one bar and a level frozen before it, which is what makes the breakout
    quality score immune to hindsight by construction.
    """
    level = boundary.level_on(sessions_from_anchor)
    threshold = boundary.threshold(sessions_from_anchor)
    atr = boundary.atr_at_open
    close, high, low = float(bar.close), float(bar.high), float(bar.low)
    open_ = float(bar.open)

    touched = boundary.contains(high, sessions_from_anchor) or (low <= level <= high)
    penetrated_intraday = high > threshold
    closed_above = close > threshold
    gapped_above = open_ > threshold and (previous_close is None or previous_close <= threshold)

    close_above_pct = (close - level) / level
    high_above_pct = (high - level) / level
    open_gap_pct = 0.0 if previous_close is None else (open_ - previous_close) / previous_close

    close_above_atr = None if not atr else (close - level) / atr
    high_above_atr = None if not atr else (high - level) / atr
    open_gap_atr = None if not atr or previous_close is None else (open_ - previous_close) / atr
    penetration_atr = None if not atr else (close - threshold) / atr
    extension_atr = (
        None if close_above_atr is None else max(0.0, close_above_atr - config.extension_free_atr)
    )

    if penetration_atr is None:
        # Without ATR the only honest penetration measure is the percentage
        # beyond the zone, expressed against the zone's own width.
        beyond = (close - threshold) / level
        penetration_score = ramp_score(
            beyond, zero_at=0.0, full_at=max(boundary.tolerance_pct, 1e-6) * 4.0
        )
    else:
        penetration_score = ramp_score(
            penetration_atr,
            zero_at=config.zero_penetration_atr,
            full_at=config.full_penetration_atr,
        )

    if extension_atr is None:
        extension_score = 100.0
    else:
        extension_score = decay_score(
            extension_atr,
            full_at=0.0,
            zero_at=config.extension_max_atr - config.extension_free_atr,
        )

    return PenetrationReading(
        touched=touched,
        penetrated_intraday=penetrated_intraday,
        closed_above=closed_above,
        gapped_above=gapped_above,
        close_above_pct=close_above_pct,
        high_above_pct=high_above_pct,
        close_above_atr=close_above_atr,
        high_above_atr=high_above_atr,
        penetration_atr=penetration_atr,
        extension_atr=extension_atr,
        open_gap_pct=open_gap_pct,
        open_gap_atr=open_gap_atr,
        gap_class=classify_gap(open_gap_atr),
        penetration_score=penetration_score,
        extension_score=extension_score,
    )


# ---------------------------------------------------------------------------
# Candle quality
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandleReading:
    """The breakout bar's own anatomy.

    ``close_location`` carries most of the information: it is where the close
    sits in the bar's range, 0 at the low and 1 at the high. A bar that traded
    through a level and closed at its low leaves every buyer above the level
    underwater at the close, which is a materially different market from the
    same penetration closing on the high.
    """

    close_location: float
    body_fraction: float
    upper_wick_fraction: float
    lower_wick_fraction: float
    range_over_atr: float | None
    close_strength_score: float
    score: float

    def to_measurements(self) -> dict[str, float]:
        out = {
            "close_location": self.close_location,
            "body_fraction": self.body_fraction,
            "upper_wick_fraction": self.upper_wick_fraction,
            "lower_wick_fraction": self.lower_wick_fraction,
            "close_strength_score": self.close_strength_score,
            "breakout_candle_score": self.score,
        }
        if self.range_over_atr is not None:
            out["range_over_atr"] = self.range_over_atr
        return out


def measure_candle(
    bar: OhlcvBar,
    previous_close: float | None,
    *,
    atr: float | None,
    config: CandleQualityConfig,
) -> CandleReading:
    """Score the bar's shape.

    A zero-range bar — a limit-up print, or a thin name that traded once — has
    no shape to read. Rather than dividing by zero or inventing a location, the
    close is placed at the top of a degenerate range, which is what a bar that
    opened, high'd, low'd and closed at one price actually did.
    """
    high, low, close, open_ = (
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.open),
    )
    span = high - low
    if span <= 0:
        close_location = 1.0
        body_fraction = 1.0
        upper_wick = lower_wick = 0.0
    else:
        close_location = (close - low) / span
        body_fraction = abs(close - open_) / span
        upper_wick = (high - max(open_, close)) / span
        lower_wick = (min(open_, close) - low) / span

    tr = true_range(bar, previous_close)
    range_over_atr = None if not atr else tr / atr

    close_strength = ramp_score(
        close_location,
        zero_at=config.close_location_zero,
        full_at=config.close_location_full,
    )
    body = ramp_score(body_fraction, zero_at=0.0, full_at=config.body_fraction_full)
    wick = decay_score(upper_wick, full_at=0.0, zero_at=config.upper_wick_zero)
    expansion = (
        50.0
        if range_over_atr is None
        else ramp_score(range_over_atr, zero_at=0.0, full_at=config.range_expansion_full)
    )

    score = (
        close_strength * config.weight_close_location
        + body * config.weight_body
        + wick * config.weight_upper_wick
        + expansion * config.weight_range_expansion
    )
    return CandleReading(
        close_location=close_location,
        body_fraction=body_fraction,
        upper_wick_fraction=upper_wick,
        lower_wick_fraction=lower_wick,
        range_over_atr=range_over_atr,
        close_strength_score=close_strength,
        score=score,
    )


# ---------------------------------------------------------------------------
# Close acceptance and follow-through
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptanceReading:
    """How settled price is above the level.

    ``consecutive_closes`` resets on a close back inside the zone;
    ``closes_above`` does not. The pair distinguishes "five of the last eight
    sessions closed above" from "the last five did", and only the second is
    acceptance.
    """

    closes_above: int
    consecutive_closes: int
    mean_close_above_atr: float | None
    #: Mean true range since the breakout over the ATR at breakout. Below 1
    #: means volatility is contracting above the level.
    range_ratio: float | None
    score: float

    def to_measurements(self) -> dict[str, float]:
        out = {
            "closes_above_level": float(self.closes_above),
            "consecutive_closes_above": float(self.consecutive_closes),
            "close_acceptance_score": self.score,
        }
        for name, value in (
            ("mean_close_above_atr", self.mean_close_above_atr),
            ("acceptance_range_ratio", self.range_ratio),
        ):
            if value is not None:
                out[name] = value
        return out


def measure_acceptance(
    bars: Sequence[OhlcvBar],
    boundary: BreakoutBoundary,
    *,
    target_closes: int,
) -> AcceptanceReading:
    """Acceptance over the post-breakout bars supplied.

    ``bars`` is the window from the breakout bar to the session being evaluated,
    inclusive. The caller slices it; this function cannot see past its end.
    """
    if not bars:
        return AcceptanceReading(0, 0, None, None, 0.0)

    threshold = boundary.threshold()
    level = boundary.nominal
    atr = boundary.atr_at_open

    closes_above = 0
    consecutive = 0
    best_consecutive = 0
    for bar in bars:
        if float(bar.close) > threshold:
            closes_above += 1
            consecutive += 1
            best_consecutive = max(best_consecutive, consecutive)
        else:
            consecutive = 0

    mean_above = None
    if atr:
        mean_above = float(np.mean([(float(b.close) - level) / atr for b in bars]))

    range_ratio = None
    if atr:
        range_ratio = _mean_range(bars) / atr

    score = ramp_score(float(consecutive), zero_at=0.0, full_at=float(max(target_closes, 1)))
    return AcceptanceReading(
        closes_above=closes_above,
        consecutive_closes=consecutive,
        mean_close_above_atr=mean_above,
        range_ratio=range_ratio,
        score=score,
    )


@dataclass(frozen=True, slots=True)
class FollowThroughReading:
    """Progress after the breakout bar, scored separately from it.

    ``sessions`` is here so a consumer can tell "no follow-through yet" from
    "no follow-through, and there has been time". A score of 30 after one
    session and a score of 30 after five are different claims.
    """

    sessions: int
    progress_atr: float | None
    adverse_atr: float | None
    higher_closes: int
    range_expansion: float | None
    volume_hold: float | None
    score: float
    available: bool = True

    def to_measurements(self) -> dict[str, float]:
        out = {
            "follow_through_sessions": float(self.sessions),
            "follow_through_higher_closes": float(self.higher_closes),
            "follow_through_score": self.score,
        }
        for name, value in (
            ("follow_through_progress_atr", self.progress_atr),
            ("follow_through_adverse_atr", self.adverse_atr),
            ("follow_through_range_expansion", self.range_expansion),
            ("follow_through_volume_hold", self.volume_hold),
        ):
            if value is not None:
                out[name] = value
        return out


def measure_follow_through(
    after: Sequence[OhlcvBar],
    *,
    breakout_close: float,
    breakout_volume: float,
    atr: float | None,
    config: FollowThroughConfig,
) -> FollowThroughReading:
    """Follow-through over the bars strictly after the breakout bar.

    Returns ``available=False`` on an empty window. That is not a score of zero:
    a breakout on its first day has produced no follow-through evidence either
    way, and scoring it zero would penalise an event for the passage of time
    rather than for anything it did. The absence propagates as an unavailable
    component and shows up in coverage.
    """
    if not after:
        return FollowThroughReading(0, None, None, 0, None, None, 0.0, available=False)

    highest_close = max(float(b.close) for b in after)
    lowest_low = min(float(b.low) for b in after)
    higher_closes = sum(1 for b in after if float(b.close) > breakout_close)

    progress_atr = None if not atr else (highest_close - breakout_close) / atr
    adverse_atr = None if not atr else max(0.0, (breakout_close - lowest_low) / atr)
    range_expansion = None if not atr else _mean_range(after) / atr
    volume_hold = (
        None
        if breakout_volume <= 0
        else float(np.mean([float(b.volume) for b in after])) / breakout_volume
    )

    progress_term = (
        50.0
        if progress_atr is None
        else ramp_score(progress_atr, zero_at=0.0, full_at=config.progress_full_atr)
    )
    adverse_term = (
        50.0
        if adverse_atr is None
        else decay_score(adverse_atr, full_at=0.0, zero_at=config.adverse_zero_atr)
    )
    expansion_term = (
        50.0
        if range_expansion is None
        else ramp_score(range_expansion, zero_at=0.0, full_at=config.expansion_full)
    )
    volume_term = (
        50.0
        if volume_hold is None
        else ramp_score(volume_hold, zero_at=0.0, full_at=config.volume_hold_full)
    )

    score = (
        progress_term * config.weight_progress
        + adverse_term * config.weight_adverse
        + expansion_term * config.weight_expansion
        + volume_term * config.weight_volume
    )
    return FollowThroughReading(
        sessions=len(after),
        progress_atr=progress_atr,
        adverse_atr=adverse_atr,
        higher_closes=higher_closes,
        range_expansion=range_expansion,
        volume_hold=volume_hold,
        score=score,
    )


# ---------------------------------------------------------------------------
# Boundary quality
# ---------------------------------------------------------------------------


def boundary_quality_score(boundary: BreakoutBoundary) -> tuple[float, dict[str, float]]:
    """How much the level itself deserves to be believed.

    Three terms: the pattern layer's own confidence in the boundary, how many
    times it was tested, and the quality of the structure it belongs to. A level
    drawn through one extreme bar in a marginal pattern is a weaker object than
    one drawn through five touches in a strong base, and a breakout of the first
    should not read the same as a breakout of the second.

    The touch term saturates at five. Beyond that, more touches stop being
    evidence of a real level and start being evidence that price cannot get
    through it — a distinction Phase 5 has no way to adjudicate and therefore
    declines to score in either direction.
    """
    confidence_term = boundary.confidence
    touch_term = ramp_score(float(boundary.touch_count), zero_at=0.0, full_at=5.0)
    pattern_term = boundary.pattern_quality if boundary.is_attached else 40.0

    score = confidence_term * 0.4 + touch_term * 0.3 + pattern_term * 0.3
    return score, {
        "boundary_confidence": confidence_term,
        "boundary_touches": float(boundary.touch_count),
        "boundary_pattern_quality": pattern_term,
        "boundary_quality_score": score,
    }


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectionReading:
    """Whether this bar pushed through the level and was pushed back.

    A rejection is a statement about one bar. It is not a failed breakout, which
    is a statement about a structure over several sessions, and keeping them
    apart is what stops a single wicky day from terminating an event the next
    day resolves cleanly.
    """

    rejected: bool
    upper_wick_fraction: float
    close_location: float
    closed_below_level: bool
    relative_volume: float | None
    score: float

    def to_measurements(self) -> dict[str, float]:
        out = {
            "rejection_score": self.score,
            "rejection_wick_fraction": self.upper_wick_fraction,
            "rejection_close_location": self.close_location,
        }
        if self.relative_volume is not None:
            out["rejection_relative_volume"] = self.relative_volume
        return out


def measure_rejection(
    bar: OhlcvBar,
    boundary: BreakoutBoundary,
    *,
    relative_volume: float | None,
    config_wick: float,
    config_close_ceiling: float,
    heavy_volume: float,
    sessions_from_anchor: int = 0,
) -> RejectionReading:
    """Score an attempted push through the level that did not stick.

    Heavier volume makes a rejection *more* significant, not less: being turned
    back on heavy volume means size was willing to sell there.
    """
    high, low, close, open_ = (
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.open),
    )
    span = high - low
    upper_wick = 0.0 if span <= 0 else (high - max(open_, close)) / span
    close_location = 1.0 if span <= 0 else (close - low) / span

    threshold = boundary.threshold(sessions_from_anchor)
    level = boundary.level_on(sessions_from_anchor)
    penetrated = high > threshold
    closed_below = close < level

    rejected = (
        penetrated
        and closed_below
        and (upper_wick >= config_wick or close_location <= config_close_ceiling)
    )

    if not rejected:
        return RejectionReading(
            False, upper_wick, close_location, closed_below, relative_volume, 0.0
        )

    wick_term = ramp_score(upper_wick, zero_at=0.0, full_at=1.0)
    location_term = decay_score(close_location, full_at=0.0, zero_at=1.0)
    volume_term = (
        50.0
        if relative_volume is None
        else ramp_score(relative_volume, zero_at=0.5, full_at=heavy_volume)
    )
    score = wick_term * 0.4 + location_term * 0.35 + volume_term * 0.25
    return RejectionReading(True, upper_wick, close_location, closed_below, relative_volume, score)


def compression_score(ratio: float | None) -> float:
    """Score a compression ratio, where below 1.0 is tightening.

    A band rather than a ramp: extreme compression right at a boundary is as
    likely to be a thin, untradeable stretch as a coiled spring, and Phase 5 has
    no basis for preferring one reading.
    """
    if ratio is None:
        return 50.0
    return band_score(
        ratio,
        ideal_low=0.45,
        ideal_high=0.8,
        tolerance_low=0.15,
        tolerance_high=1.4,
        floor=20.0,
    )


__all__ = [
    "GAP_BANDS",
    "AcceptanceReading",
    "ApproachReading",
    "CandleReading",
    "FollowThroughReading",
    "PenetrationReading",
    "RejectionReading",
    "average_true_range",
    "boundary_quality_score",
    "classify_gap",
    "compression_score",
    "measure_acceptance",
    "measure_approach",
    "measure_candle",
    "measure_follow_through",
    "measure_penetration",
    "measure_rejection",
    "true_range",
]
