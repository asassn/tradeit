"""Shared causal pattern primitives.

Twelve detectors that each re-derive "how far did price advance", "how much did
it give back", "is range contracting" would be twelve chances to define those
slightly differently — and the divergence would be invisible, showing up only as
two detectors disagreeing about the same chart for no stateable reason.

So the measurements are extracted here. What is *not* extracted is the judgement:
each family scores these differently, and that is where the genuine structural
difference between a flag and a triangle lives. A bull flag wants a mild
downward drift, a flat base wants none, an ascending triangle wants rising lows.
One measurement, three opinions.

**Every primitive is a measurement, not a verdict.** Nothing here returns a
score, a boolean "is valid", or a pattern. They return numbers with names, and
the detectors decide what the numbers mean.

**Every primitive is causal.** Where a primitive needs pivots it takes them as
an argument rather than computing them, so the caller has already passed them
through :func:`~tradeit.patterns.swings.confirmed_swings`. A primitive that
found its own pivots would be a primitive that could quietly use unconfirmed
ones, and the leak would be reintroduced twelve times over.

Only abstractions that actually recur are here. A `SymmetryProfile` used by one
detector belongs in that detector; extracting it would be indirection without a
second caller to justify it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.patterns.structure import fit_line
from tradeit.patterns.swings import Swing

Floats = NDArray[np.float64]


# ---------------------------------------------------------------------------
# Directional legs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImpulseLeg:
    """A directional advance, measured every way a detector might need.

    Used by the bull flag (flagpole), the pennant (impulse), the high tight flag
    (the extreme advance), base-on-base (the intermediate advance), and as prior
    trend context by every base pattern.

    Magnitude is reported both as a percentage and in ATRs because neither alone
    compares across securities: 12% is enormous for a utility and ordinary for a
    biotech, while a pure ATR measure accepts a trivial advance in a dead-quiet
    stock.
    """

    start_index: int
    end_index: int
    start_date: dt.date
    end_date: dt.date
    sessions: int
    low: float
    high: float
    gain_pct: float
    gain_atr: float
    #: Compound gain per session. Distinguishes a sharp thrust from a grind of
    #: the same total magnitude.
    rate_per_session: float
    up_day_fraction: float
    #: Mean position of the close within each bar's range. High values mean
    #: buyers held into the close rather than fading.
    closing_strength: float
    volume_ratio: float
    #: Fraction of the advance delivered by its single largest session. A
    #: structural measure -- a "leg" carried entirely by one bar has no
    #: duration to sustain.
    largest_session_share: float
    #: Fraction delivered by the single largest overnight gap. Characterises
    #: *how* the move happened; never disqualifying on its own.
    largest_gap_share: float
    #: Fraction of sessions closing above the previous close. Persistence of the
    #: move rather than its size.
    persistence: float

    @property
    def is_single_session(self) -> bool:
        return self.sessions <= 1

    @property
    def height(self) -> float:
        return self.high - self.low


def measure_impulse(
    bars: Sequence[OhlcvBar],
    *,
    start_index: int,
    end_index: int,
    atr: Floats | None = None,
    volume_baseline_sessions: int = 20,
) -> ImpulseLeg | None:
    """Measure a directional leg. Returns ``None`` if the window is degenerate."""
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi <= lo:
        return None

    window = bars[lo : hi + 1]
    lows = np.array([float(b.low) for b in window])
    highs = np.array([float(b.high) for b in window])
    closes = np.array([float(b.close) for b in window])
    opens = np.array([float(b.open) for b in window])
    volumes = np.array([float(b.volume) for b in window])

    low = float(lows[0])
    high = float(highs.max())
    if low <= 0:
        return None
    gain = high / low - 1.0
    sessions = hi - lo

    gain_atr = 0.0
    if atr is not None and lo < len(atr) and np.isfinite(atr[lo]) and atr[lo] > 0:
        gain_atr = (low * gain) / float(atr[lo])

    rate = (1.0 + gain) ** (1.0 / sessions) - 1.0 if sessions > 0 else 0.0
    up_days = float(np.mean(closes > opens))
    spans = highs - lows
    closing_strength = float(
        np.mean(np.where(spans > 0, (closes - lows) / np.where(spans > 0, spans, 1.0), 0.5))
    )

    baseline_start = max(0, lo - volume_baseline_sessions)
    baseline = [float(b.volume) for b in bars[baseline_start:lo]]
    baseline_mean = float(np.mean(baseline)) if baseline else 0.0
    volume_ratio = float(volumes.mean()) / baseline_mean if baseline_mean > 0 else 1.0

    total = float(closes[-1]) - low
    if total > 0 and len(closes) > 1:
        steps = np.diff(closes)
        largest_session = float(np.clip(steps.max() / total, 0.0, 1.0))
        gaps = opens[1:] - closes[:-1]
        largest_gap = float(np.clip(gaps.max() / total, 0.0, 1.0)) if gaps.size else 0.0
        persistence = float(np.mean(steps > 0))
    else:
        largest_session, largest_gap, persistence = 1.0, 0.0, 0.0

    return ImpulseLeg(
        start_index=lo,
        end_index=hi,
        start_date=window[0].session_date,
        end_date=window[-1].session_date,
        sessions=sessions,
        low=low,
        high=high,
        gain_pct=gain,
        gain_atr=gain_atr,
        rate_per_session=rate,
        up_day_fraction=up_days,
        closing_strength=closing_strength,
        volume_ratio=volume_ratio,
        largest_session_share=largest_session,
        largest_gap_share=largest_gap,
        persistence=persistence,
    )


@dataclass(frozen=True, slots=True)
class PullbackLeg:
    """A decline measured against the advance it is giving back.

    ``retracement_of_impulse`` is the number every continuation pattern turns
    on. ``depth_atr`` is the same decline in volatility units, which is what
    lets a 6% pullback in a quiet name and a 15% pullback in a volatile one be
    recognised as the same event.
    """

    start_index: int
    end_index: int
    start_date: dt.date
    end_date: dt.date
    sessions: int
    high: float
    low: float
    depth_pct: float
    depth_atr: float
    retracement_of_impulse: float
    #: Worst close-to-low excursion inside the pullback, as a fraction. Distinct
    #: from depth, which is measured from the impulse high.
    max_adverse_excursion: float
    #: True when price traded below the impulse's own origin. Not a score --
    #: a genuine structural break, since the leg the pattern was defined
    #: against no longer exists.
    undercut_impulse_low: bool


def measure_pullback(
    bars: Sequence[OhlcvBar],
    impulse: ImpulseLeg,
    *,
    start_index: int,
    end_index: int,
    atr: Floats | None = None,
) -> PullbackLeg | None:
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi < lo:
        return None

    window = bars[lo : hi + 1]
    lows = np.array([float(b.low) for b in window])
    highs = np.array([float(b.high) for b in window])
    closes = np.array([float(b.close) for b in window])

    high = float(max(highs.max(), impulse.high))
    low = float(lows.min())
    depth = (high - low) / high if high > 0 else 0.0

    depth_atr = 0.0
    if atr is not None and lo < len(atr) and np.isfinite(atr[lo]) and atr[lo] > 0:
        depth_atr = (high - low) / float(atr[lo])

    retracement = (impulse.high - low) / impulse.height if impulse.height > 0 else 0.0
    running_peak = np.maximum.accumulate(np.maximum(closes, impulse.high))
    excursion = float(np.max((running_peak - lows) / np.where(running_peak > 0, running_peak, 1.0)))

    return PullbackLeg(
        start_index=lo,
        end_index=hi,
        start_date=window[0].session_date,
        end_date=window[-1].session_date,
        sessions=hi - lo + 1,
        high=high,
        low=low,
        depth_pct=depth,
        depth_atr=depth_atr,
        retracement_of_impulse=retracement,
        max_adverse_excursion=excursion,
        undercut_impulse_low=bool(low < impulse.low),
    )


# ---------------------------------------------------------------------------
# Contraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contraction:
    """One leg of a contracting base: a high, a low, and what lies between.

    The unit the VCP is built from. Kept general because a base-on-base parent
    and an ascending-triangle pullback are the same shape measured differently.
    """

    index: int  # position in the sequence, 0-based
    high_index: int
    low_index: int
    high_date: dt.date
    low_date: dt.date
    high: float
    low: float
    sessions: int

    @property
    def depth_pct(self) -> float:
        return (self.high - self.low) / self.high if self.high > 0 else 0.0


@dataclass(frozen=True, slots=True)
class ContractionSequence:
    """Successive contractions, with the progression measured.

    The VCP's defining property is that each contraction is *shallower* than the
    last. ``depth_progression`` measures that directly: negative means
    tightening. ``monotonic_fraction`` says how consistently, because one deep
    leg in the middle of four shallow ones is a different structure from a clean
    staircase.

    Deliberately does not require exactly three legs. Two is a beginning and
    five is a long base; the count is reported and the detector judges it.
    """

    legs: tuple[Contraction, ...]

    @property
    def count(self) -> int:
        return len(self.legs)

    @property
    def depths(self) -> tuple[float, ...]:
        return tuple(leg.depth_pct for leg in self.legs)

    @property
    def durations(self) -> tuple[int, ...]:
        return tuple(leg.sessions for leg in self.legs)

    @property
    def depth_progression(self) -> float:
        """Mean change in depth from one leg to the next. Negative tightens."""
        depths = self.depths
        if len(depths) < 2:
            return 0.0
        return float(np.mean(np.diff(depths)))

    @property
    def monotonic_fraction(self) -> float:
        """Fraction of successive pairs that tighten.

        1.0 is a clean staircase. 0.5 is a base that happens to end tighter than
        it started, which is a materially weaker statement.
        """
        depths = self.depths
        if len(depths) < 2:
            return 0.0
        steps = np.diff(depths)
        return float(np.mean(steps < 0))

    @property
    def tightening_ratio(self) -> float:
        """Final depth over first depth. Below 1 means the base tightened."""
        depths = self.depths
        if len(depths) < 2 or depths[0] <= 0:
            return 1.0
        return depths[-1] / depths[0]

    @property
    def final_depth(self) -> float:
        return self.depths[-1] if self.legs else 0.0

    @property
    def base_depth(self) -> float:
        """Deepest point of the whole base, from its highest high."""
        if not self.legs:
            return 0.0
        high = max(leg.high for leg in self.legs)
        low = min(leg.low for leg in self.legs)
        return (high - low) / high if high > 0 else 0.0

    def as_dict(self) -> list[dict[str, object]]:
        """Geometry a dashboard can draw the contraction hierarchy from."""
        return [
            {
                "index": leg.index,
                "high_date": leg.high_date.isoformat(),
                "low_date": leg.low_date.isoformat(),
                "high": leg.high,
                "low": leg.low,
                "depth_pct": round(leg.depth_pct, 6),
                "sessions": leg.sessions,
            }
            for leg in self.legs
        ]


def build_contractions(
    swing_highs: Sequence[Swing], swing_lows: Sequence[Swing], *, start_index: int, end_index: int
) -> ContractionSequence:
    """Pair confirmed highs with the lows that follow them.

    A contraction is a high followed by a low. Pairing that way rather than
    low-to-high is what makes the sequence describe *pullbacks within a base*
    rather than rallies — the VCP's legs are its declines, and measuring the
    rallies instead would report a rising base as a tightening one.

    Both inputs must already be confirmed pivots; this function does no pivot
    detection precisely so it cannot bypass that.
    """
    highs = sorted(
        (s for s in swing_highs if start_index <= s.index <= end_index), key=lambda s: s.index
    )
    lows = sorted(
        (s for s in swing_lows if start_index <= s.index <= end_index), key=lambda s: s.index
    )
    if not highs or not lows:
        return ContractionSequence(())

    legs: list[Contraction] = []
    for high in highs:
        following = next((low for low in lows if low.index > high.index), None)
        if following is None:
            break
        legs.append(
            Contraction(
                index=len(legs),
                high_index=high.index,
                low_index=following.index,
                high_date=high.session_date,
                low_date=following.session_date,
                high=high.price,
                low=following.price,
                sessions=following.index - high.index,
            )
        )
    return ContractionSequence(tuple(legs))


# ---------------------------------------------------------------------------
# Profiles: volume, volatility, compression
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """How volume behaved through a window, relative to a baseline."""

    mean_volume: float
    baseline_volume: float
    #: Window mean over baseline mean. Below 1 means drying up.
    ratio: float
    #: Volume on up sessions over volume on down sessions.
    up_down_ratio: float
    #: Regression slope of volume through the window, normalised by its mean.
    slope_per_session: float
    #: Net change in on-balance volume across the window.
    obv_change: float
    #: Mean volume over the last few sessions, relative to the window mean.
    late_ratio: float
    #: Fraction of sessions with volume below the baseline. Dry-up measured by
    #: breadth rather than by average, which one heavy session can dominate.
    dry_session_fraction: float


def measure_volume(
    bars: Sequence[OhlcvBar],
    *,
    start_index: int,
    end_index: int,
    baseline_start: int | None = None,
    baseline_end: int | None = None,
    late_sessions: int = 3,
) -> VolumeProfile | None:
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi < lo:
        return None

    window = bars[lo : hi + 1]
    volumes = np.array([float(b.volume) for b in window])
    closes = np.array([float(b.close) for b in window])
    opens = np.array([float(b.open) for b in window])

    b_lo = 0 if baseline_start is None else max(0, baseline_start)
    b_hi = lo - 1 if baseline_end is None else min(len(bars) - 1, baseline_end)
    baseline = (
        np.array([float(b.volume) for b in bars[b_lo : b_hi + 1]]) if b_hi >= b_lo else np.array([])
    )
    baseline_mean = float(baseline.mean()) if baseline.size else 0.0
    mean_volume = float(volumes.mean())

    up_volume = float(volumes[closes >= opens].sum())
    down_volume = float(volumes[closes < opens].sum())

    slope = 0.0
    if len(volumes) >= 3 and mean_volume > 0:
        slope = (
            fit_line(list(range(len(volumes))), volumes.tolist(), anchor_index=0).slope_per_session
            / mean_volume
        )

    direction = np.sign(np.diff(closes, prepend=closes[0]))
    obv_change = float(np.sum(direction * volumes))

    late = volumes[-late_sessions:] if late_sessions > 0 else volumes
    late_ratio = float(late.mean()) / mean_volume if mean_volume > 0 else 1.0

    dry = float(np.mean(volumes < baseline_mean)) if baseline_mean > 0 else 0.0

    return VolumeProfile(
        mean_volume=mean_volume,
        baseline_volume=baseline_mean,
        ratio=mean_volume / baseline_mean if baseline_mean > 0 else 1.0,
        up_down_ratio=up_volume / down_volume if down_volume > 0 else 2.0,
        slope_per_session=slope,
        obv_change=obv_change,
        late_ratio=late_ratio,
        dry_session_fraction=dry,
    )


@dataclass(frozen=True, slots=True)
class VolatilityProfile:
    """How range and ATR behaved through a window.

    Shared by the flag (contraction component), the VCP (its defining property),
    the flat base, the pennant and tight consolidation. All five ask the same
    question and would otherwise ask it five slightly different ways.
    """

    mean_range_pct: float
    #: Late-half ATR over early-half ATR. Below 1 contracts.
    atr_ratio: float
    #: Same for raw bar ranges.
    range_ratio: float
    #: Standard deviation of closes over their mean.
    close_dispersion: float
    #: Widest bar in the window as a fraction of price.
    max_range_pct: float
    #: Fraction of successive session-range comparisons that narrowed. A
    #: measure of *consistent* compression rather than net compression.
    narrowing_fraction: float


def measure_volatility(
    bars: Sequence[OhlcvBar], *, start_index: int, end_index: int, atr: Floats | None = None
) -> VolatilityProfile | None:
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi - lo + 1 < 4:
        return None

    window = bars[lo : hi + 1]
    highs = np.array([float(b.high) for b in window])
    lows = np.array([float(b.low) for b in window])
    closes = np.array([float(b.close) for b in window])

    ranges = highs - lows
    safe_closes = np.where(closes > 0, closes, np.nan)
    range_pct = ranges / safe_closes

    half = len(window) // 2
    early_range = float(np.nanmean(ranges[:half])) if half else 0.0
    late_range = float(np.nanmean(ranges[half:])) if half else 0.0
    range_ratio = late_range / early_range if early_range > 0 else 1.0

    atr_ratio = range_ratio
    if atr is not None and hi < len(atr):
        early_atr = float(np.nanmean(atr[lo : lo + half])) if half else float("nan")
        late_atr = float(np.nanmean(atr[lo + half : hi + 1])) if half else float("nan")
        if np.isfinite(early_atr) and np.isfinite(late_atr) and early_atr > 0:
            atr_ratio = late_atr / early_atr

    mean_close = float(closes.mean())
    narrowing = float(np.mean(np.diff(ranges) < 0)) if len(ranges) > 1 else 0.0

    return VolatilityProfile(
        mean_range_pct=float(np.nanmean(range_pct)),
        atr_ratio=atr_ratio,
        range_ratio=range_ratio,
        close_dispersion=float(closes.std() / mean_close) if mean_close > 0 else 0.0,
        max_range_pct=float(np.nanmax(range_pct)),
        narrowing_fraction=narrowing,
    )


@dataclass(frozen=True, slots=True)
class RelativeStrengthProfile:
    """Performance against a benchmark through a window.

    Optional context for every detector, and never part of any pattern's
    definition. A structure that holds up against its benchmark while
    consolidating is better evidence than one that does not, but a detector that
    refuses to find a flag because the benchmark series is missing has confused
    corroboration with definition.
    """

    security_return: float
    benchmark_return: float
    excess_return: float
    #: True when the security/benchmark ratio ends at its window high.
    ratio_at_window_high: bool
    #: Slope of that ratio, normalised. Rising means outperforming.
    ratio_slope: float


def measure_relative_strength(
    closes: Sequence[float],
    benchmark_closes: Sequence[float],
    *,
    start_index: int,
    end_index: int,
) -> RelativeStrengthProfile | None:
    if len(closes) != len(benchmark_closes):
        raise ConfigError(
            "security and benchmark series differ in length; a misaligned "
            "benchmark produces relative strength computed from mismatched days"
        )
    lo, hi = max(0, start_index), min(len(closes) - 1, end_index)
    if hi <= lo:
        return None

    security = np.asarray(closes[lo : hi + 1], dtype=np.float64)
    benchmark = np.asarray(benchmark_closes[lo : hi + 1], dtype=np.float64)
    if security[0] <= 0 or benchmark[0] <= 0 or np.any(benchmark <= 0):
        return None

    security_return = float(security[-1] / security[0] - 1.0)
    benchmark_return = float(benchmark[-1] / benchmark[0] - 1.0)
    ratio = security / benchmark
    mean_ratio = float(ratio.mean())

    slope = 0.0
    if len(ratio) >= 3 and mean_ratio > 0:
        slope = (
            fit_line(list(range(len(ratio))), ratio.tolist(), anchor_index=0).slope_per_session
            / mean_ratio
        )

    return RelativeStrengthProfile(
        security_return=security_return,
        benchmark_return=benchmark_return,
        excess_return=security_return - benchmark_return,
        ratio_at_window_high=bool(ratio[-1] >= ratio.max() * 0.995),
        ratio_slope=slope,
    )


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriorTrendContext:
    """What price was doing before the structure began.

    The primitive that stops arbitrary sideways movement being classified as a
    base. A flat base is a *pause in an advance*; the same geometry after a
    decline is a bear-market rest, and the only thing distinguishing them is
    what came before.
    """

    sessions: int
    gain_pct: float
    #: Fraction of the lookback spent above the window's own mean close. High
    #: values mean a sustained uptrend rather than a late spike.
    time_above_mean: float
    #: Close at the end of the lookback relative to its highest high.
    distance_from_high: float
    #: Regression slope of closes, normalised by mean price.
    slope_per_session: float
    slope_r_squared: float

    @property
    def is_uptrend(self) -> bool:
        """A deliberately loose reading: positive slope with a real fit.

        Loose because a base can form after a modest advance as easily as after
        a dramatic one, and requiring a dramatic one would find only the bases
        that follow the moves everyone already noticed.
        """
        return self.slope_per_session > 0 and self.slope_r_squared > 0.15


def measure_prior_trend(
    bars: Sequence[OhlcvBar], *, end_index: int, lookback: int = 60
) -> PriorTrendContext | None:
    hi = min(len(bars) - 1, end_index)
    lo = max(0, hi - lookback + 1)
    if hi - lo + 1 < 10:
        return None

    window = bars[lo : hi + 1]
    closes = np.array([float(b.close) for b in window])
    highs = np.array([float(b.high) for b in window])
    if closes[0] <= 0:
        return None

    fit = fit_line(list(range(len(closes))), closes.tolist(), anchor_index=0)
    mean_close = float(closes.mean())
    peak = float(highs.max())

    return PriorTrendContext(
        sessions=len(window),
        gain_pct=float(closes[-1] / closes[0] - 1.0),
        time_above_mean=float(np.mean(closes > mean_close)),
        distance_from_high=float((peak - closes[-1]) / peak) if peak > 0 else 0.0,
        slope_per_session=fit.slope_per_session / mean_close if mean_close > 0 else 0.0,
        slope_r_squared=fit.r_squared,
    )


@dataclass(frozen=True, slots=True)
class DurationProfile:
    """A window's length, absolute and relative to what preceded it.

    Relative usually matters more. Ten sessions of pause after a thirty-session
    advance is a flag; the same ten after a four-session spike is a base that
    happens to follow a jump.
    """

    sessions: int
    reference_sessions: int
    ratio: float
    too_short: bool
    too_extended: bool


def measure_duration(
    sessions: int,
    reference_sessions: int,
    *,
    minimum: int,
    maximum: int,
    max_ratio: float | None = None,
) -> DurationProfile:
    ratio = sessions / reference_sessions if reference_sessions > 0 else float("inf")
    return DurationProfile(
        sessions=sessions,
        reference_sessions=reference_sessions,
        ratio=ratio if np.isfinite(ratio) else 0.0,
        too_short=sessions < minimum,
        too_extended=sessions > maximum or (max_ratio is not None and ratio > max_ratio),
    )
