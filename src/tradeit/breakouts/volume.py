"""Volume confirmation, including the partial-session problem.

Volume is the largest single component of the breakout quality score, and it is
also where the most confident wrong numbers get produced. Two separate hazards
live here and they need separate defences.

**Hazard one: one threshold for every setup.** "Breakouts need 1.5x volume" is a
statement about one family's behaviour promoted to a law. A flat base is a quiet
structure by construction; demanding flag-like volume of it is demanding that it
stop being a flat base. So the expansion figure that scores 100 is per family,
configurable, and the families that do not differ are simply absent from the
override map — absence meaning "no justified difference", not "not considered".

**Hazard two: comparing a partial day to a full day.** 800,000 shares at 10:15
against a 2,000,000-share average is not relative volume 0.4. It is 40% of a
typical day done in the first 45 minutes, which is heavy. Getting the number
right needs a time-of-day curve; getting it *honest* needs four quantities kept
distinct all the way to the consumer:

``CURRENT_OBSERVED_VOLUME``
    What has actually traded. A fact.
``TIME_NORMALIZED_VOLUME``
    Observed volume divided by the fraction of the session's typical volume that
    has historically traded by this time. Still derived from facts, but from a
    curve that could be wrong.
``PROJECTED_VOLUME``
    What the full session would print if the rest of the day behaved typically.
    A projection. Never a fact.
``COMPLETED_BAR_RELATIVE_VOLUME``
    Only defined once the bar has closed.

:class:`VolumeReading` carries all four and refuses to let a projection be read
as a completed figure: ``completed_relative_volume`` is ``None`` on a partial
bar, full stop. A caller that wants a number for an in-progress session has to
ask for the projected one by name, and the name says what it is.

**The curve is built from prior sessions only.** Using the rest of today's
volume to normalise this morning's would be a leak so direct it barely needs
stating — and it is exactly what "volume so far vs. average volume by this time"
looks like when implemented carelessly against a dataframe that contains the
whole day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from tradeit.breakouts.config import IntradayVolumeConfig, VolumeConfirmationConfig
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.patterns.scoring import ramp_score


@dataclass(frozen=True, slots=True)
class VolumeReading:
    """Volume at the breakout, with the partial-bar distinction preserved.

    ``is_partial`` is the field everything else keys off. When it is true the
    bar has not closed, and ``completed_relative_volume`` is ``None`` — there is
    no completed bar to take a ratio of. Consumers that treat ``None`` as zero
    will produce a wrong answer loudly rather than a plausible one quietly,
    which is the intended failure mode.
    """

    current_observed_volume: float
    average_volume: float | None
    median_volume: float | None
    is_partial: bool

    #: Only on a completed bar.
    completed_relative_volume: float | None = None
    #: Only on a partial bar, and only above the elapsed-fraction floor.
    time_normalized_volume: float | None = None
    projected_volume: float | None = None
    projected_relative_volume: float | None = None
    elapsed_fraction: float | None = None
    expected_fraction: float | None = None
    #: Why a projection was withheld, when it was.
    projection_unavailable_reason: str = ""

    #: Volume relative to the consolidation the pattern formed in.
    consolidation_ratio: float | None = None
    #: Volume relative to the impulse leg that created the pattern.
    impulse_ratio: float | None = None
    #: Percentile of this volume within the averaging window, 0-100.
    percentile: float | None = None
    #: Up-volume as a fraction of total volume over the recent window.
    up_volume_fraction: float | None = None

    def to_measurements(self) -> dict[str, float]:
        out: dict[str, float] = {"current_observed_volume": self.current_observed_volume}
        for name, value in (
            ("average_volume", self.average_volume),
            ("median_volume", self.median_volume),
            ("relative_volume", self.completed_relative_volume),
            ("time_normalized_volume", self.time_normalized_volume),
            ("projected_volume", self.projected_volume),
            ("projected_relative_volume", self.projected_relative_volume),
            ("elapsed_fraction", self.elapsed_fraction),
            ("expected_volume_fraction", self.expected_fraction),
            ("volume_consolidation_ratio", self.consolidation_ratio),
            ("volume_impulse_ratio", self.impulse_ratio),
            ("volume_percentile", self.percentile),
            ("up_volume_fraction", self.up_volume_fraction),
        ):
            if value is not None:
                out[name] = value
        return out

    @property
    def usable_relative_volume(self) -> float | None:
        """The relative-volume figure a score may use, or ``None``.

        Prefers the completed figure. Falls back to the projected one *only*
        because an intraday consumer genuinely has nothing else — and every
        score built on it records that it used a projection, so the fact travels
        with the number.
        """
        if self.completed_relative_volume is not None:
            return self.completed_relative_volume
        return self.projected_relative_volume

    @property
    def used_projection(self) -> bool:
        return self.completed_relative_volume is None and self.projected_relative_volume is not None


@dataclass(frozen=True, slots=True)
class IntradayVolumeCurve:
    """Cumulative fraction of a session's volume traded by each bucket.

    Built from prior complete sessions, which is the only way it can be built
    without reading the current session's future. U-shaped in practice — heavy
    at the open and into the close, quiet at lunch — which is exactly why a
    flat "fraction of the day elapsed" normalisation understates morning volume
    and overstates midday volume.
    """

    #: ``fractions[k]`` is the mean cumulative share of session volume traded by
    #: the end of bucket ``k``. Monotone non-decreasing, ending at 1.0.
    fractions: tuple[float, ...]
    sessions_used: int
    session_start: dt.time
    session_end: dt.time

    def __post_init__(self) -> None:
        if not self.fractions:
            raise ConfigError("an intraday volume curve needs at least one bucket")
        if any(b < a for a, b in zip(self.fractions, self.fractions[1:], strict=False)):
            raise ConfigError("cumulative volume fractions must be non-decreasing")
        if abs(self.fractions[-1] - 1.0) > 1e-6:
            raise ConfigError(f"curve ends at {self.fractions[-1]}, not 1.0")

    def elapsed_fraction(self, at: dt.time) -> float:
        """Fraction of the session's *clock* elapsed. Linear, unlike volume."""
        start = _minutes(self.session_start)
        end = _minutes(self.session_end)
        now = _minutes(at)
        if end <= start:
            raise ConfigError("session end must follow session start")
        return float(min(1.0, max(0.0, (now - start) / (end - start))))

    def expected_fraction(self, at: dt.time) -> float:
        """Fraction of the session's *volume* typically done by this time.

        Interpolated between bucket boundaries rather than snapped to them, so
        the number moves smoothly through the session instead of stepping.
        """
        elapsed = self.elapsed_fraction(at)
        if elapsed <= 0.0:
            return 0.0
        n = len(self.fractions)
        position = elapsed * n
        index = int(position)
        if index >= n:
            return 1.0
        lower = self.fractions[index - 1] if index > 0 else 0.0
        upper = self.fractions[index]
        return float(lower + (upper - lower) * (position - index))


def _minutes(value: dt.time) -> float:
    return value.hour * 60.0 + value.minute + value.second / 60.0


def build_intraday_curve(
    sessions: Sequence[Sequence[OhlcvBar]],
    *,
    config: IntradayVolumeConfig,
    session_start: dt.time = dt.time(9, 30),
    session_end: dt.time = dt.time(16, 0),
) -> IntradayVolumeCurve | None:
    """Build a curve from complete prior sessions.

    ``sessions`` must contain only sessions that have finished — the caller
    slices them under the same clock that gates everything else. Returns
    ``None`` when there are too few to average, rather than a curve fitted to
    two days, because a two-day curve is a description of two days.
    """
    usable = [bars for bars in sessions if bars and sum(float(b.volume) for b in bars) > 0]
    if len(usable) < 3:
        return None
    usable = usable[-config.curve_lookback_sessions :]

    buckets = config.buckets
    accumulated = np.zeros(buckets, dtype=float)
    for bars in usable:
        total = sum(float(b.volume) for b in bars)
        per_bucket = np.zeros(buckets, dtype=float)
        for position, bar in enumerate(bars):
            index = min(buckets - 1, int(position * buckets / len(bars)))
            per_bucket[index] += float(bar.volume)
        accumulated += np.cumsum(per_bucket) / total

    mean = accumulated / len(usable)
    mean[-1] = 1.0
    mean = np.maximum.accumulate(mean)
    return IntradayVolumeCurve(
        fractions=tuple(float(x) for x in mean),
        sessions_used=len(usable),
        session_start=session_start,
        session_end=session_end,
    )


def measure_volume(
    bars: Sequence[OhlcvBar],
    *,
    config: VolumeConfirmationConfig,
    intraday_config: IntradayVolumeConfig | None = None,
    is_partial: bool = False,
    observed_at: dt.time | None = None,
    curve: IntradayVolumeCurve | None = None,
    consolidation_bars: Sequence[OhlcvBar] = (),
    impulse_bars: Sequence[OhlcvBar] = (),
) -> VolumeReading:
    """Volume at the last bar of ``bars``, against history strictly before it.

    The averaging window deliberately excludes the bar being measured. Including
    a 5x volume day in the average it is being compared against dilutes exactly
    the signal the comparison exists to detect, and the effect grows as the
    window shrinks.
    """
    if not bars:
        raise ConfigError("volume needs at least one bar")

    last = bars[-1]
    observed = float(last.volume)
    history = bars[:-1]

    average = None
    median = None
    percentile = None
    if len(history) >= 5:
        window = [float(b.volume) for b in history[-config.average_period :]]
        average = float(np.mean(window))
        median_window = [float(b.volume) for b in history[-config.median_period :]]
        median = float(np.median(median_window))
        percentile = float((np.sum(np.asarray(window) <= observed) / len(window)) * 100.0)

    up_fraction = None
    if len(history) >= 5:
        recent = history[-config.average_period :]
        total = sum(float(b.volume) for b in recent)
        if total > 0:
            up = sum(float(b.volume) for b in recent if float(b.close) >= float(b.open))
            up_fraction = up / total

    consolidation_ratio = _ratio_against(observed, consolidation_bars)
    impulse_ratio = _ratio_against(observed, impulse_bars)

    if not is_partial:
        completed = None if not average else observed / average
        return VolumeReading(
            current_observed_volume=observed,
            average_volume=average,
            median_volume=median,
            is_partial=False,
            completed_relative_volume=completed,
            consolidation_ratio=consolidation_ratio,
            impulse_ratio=impulse_ratio,
            percentile=percentile,
            up_volume_fraction=up_fraction,
        )

    # Partial bar: no completed relative volume exists, whatever the caller
    # would like. Everything below is explicitly a projection.
    reason = ""
    elapsed = expected = normalized = projected = projected_relative = None
    if intraday_config is None or not intraday_config.enabled:
        reason = "intraday volume projection is disabled in configuration"
    elif curve is None:
        reason = "no time-of-day volume curve is available for this instrument"
    elif observed_at is None:
        reason = "the observation time within the session was not supplied"
    else:
        elapsed = curve.elapsed_fraction(observed_at)
        expected = curve.expected_fraction(observed_at)
        if elapsed < intraday_config.min_elapsed_fraction:
            reason = (
                f"only {elapsed:.1%} of the session has elapsed, below the "
                f"{intraday_config.min_elapsed_fraction:.0%} floor: dividing by a "
                "sliver of the session multiplies its noise rather than removing it"
            )
        elif expected <= 0:
            reason = "the volume curve expects no volume by this time"
        else:
            normalized = observed / expected
            projected = observed / expected
            projected_relative = None if not average else projected / average

    return VolumeReading(
        current_observed_volume=observed,
        average_volume=average,
        median_volume=median,
        is_partial=True,
        completed_relative_volume=None,
        time_normalized_volume=normalized,
        projected_volume=projected,
        projected_relative_volume=projected_relative,
        elapsed_fraction=elapsed,
        expected_fraction=expected,
        projection_unavailable_reason=reason,
        consolidation_ratio=consolidation_ratio,
        impulse_ratio=impulse_ratio,
        percentile=percentile,
        up_volume_fraction=up_fraction,
    )


def _ratio_against(observed: float, reference: Sequence[OhlcvBar]) -> float | None:
    if not reference:
        return None
    mean = float(np.mean([float(b.volume) for b in reference]))
    return None if mean <= 0 else observed / mean


@dataclass(frozen=True, slots=True)
class VolumeScores:
    """The three volume outputs the brief names, kept separate.

    ``confirmation`` is the composite the breakout score consumes;
    ``relative`` and ``expansion`` remain visible because they can disagree. A
    stock whose whole volume profile stepped up last month shows a weak
    ``relative`` figure and a strong ``expansion`` one, and only reporting the
    composite would hide which was which.
    """

    relative: float | None
    expansion: float | None
    confirmation: float | None
    unavailable_reason: str = ""
    measurements: Mapping[str, float] = field(default_factory=dict)


def score_volume(
    reading: VolumeReading,
    *,
    config: VolumeConfirmationConfig,
    family: str | None,
) -> VolumeScores:
    """Turn a volume reading into the three scores, or say why it cannot.

    Returns ``None`` scores with a reason rather than zeros. Zero means measured
    and bad; an instrument with five bars of history has not been measured at
    all, and the difference is what evidence coverage exists to record.
    """
    relative_volume = reading.usable_relative_volume
    if relative_volume is None:
        reason = (
            reading.projection_unavailable_reason
            or "insufficient volume history to form an average"
        )
        return VolumeScores(None, None, None, reason, reading.to_measurements())

    full = config.full_for(family)
    relative = ramp_score(relative_volume, zero_at=config.relative_volume_zero, full_at=full)

    expansion_terms: list[float] = []
    if reading.consolidation_ratio is not None:
        expansion_terms.append(
            ramp_score(
                reading.consolidation_ratio,
                zero_at=0.0,
                full_at=config.consolidation_expansion_full,
            )
        )
    if reading.impulse_ratio is not None:
        expansion_terms.append(
            ramp_score(reading.impulse_ratio, zero_at=0.0, full_at=config.impulse_comparison_full)
        )
    expansion = float(np.mean(expansion_terms)) if expansion_terms else None

    # When no structural reference exists the relative figure carries the whole
    # component rather than being averaged against a stand-in — an invented
    # neutral would move the composite while adding nothing.
    confirmation = relative if expansion is None else relative * 0.6 + expansion * 0.4

    measurements = dict(reading.to_measurements())
    measurements["relative_volume_score"] = relative
    if expansion is not None:
        measurements["volume_expansion_score"] = expansion
    measurements["volume_confirmation_score"] = confirmation
    measurements["relative_volume_target"] = full
    if reading.used_projection:
        measurements["volume_from_projection"] = 1.0
    return VolumeScores(relative, expansion, confirmation, "", measurements)


__all__ = [
    "IntradayVolumeCurve",
    "VolumeReading",
    "VolumeScores",
    "build_intraday_curve",
    "measure_volume",
    "score_volume",
]
