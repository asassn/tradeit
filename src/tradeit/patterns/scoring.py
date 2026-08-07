"""Continuous scoring curves.

The brief's central objection to naive pattern detection is the binary rule:
``IF retracement < 50% THEN valid``. The problem is not the number 50 — it is
the cliff. A 49.9% retracement and a 50.1% retracement are the same structure,
and a detector that calls one perfect and the other worthless is reporting the
threshold, not the pattern.

So every dimension is scored on a continuous curve, and every curve's shape is
configuration rather than code (ADR-0008). Four shapes cover everything the
detectors need:

* :func:`band_score` — a preferred *range*, tapering outside it. The workhorse:
  retracement depth, consolidation duration, channel slope. Most pattern
  qualities are "somewhere in this window is good, and further away is
  progressively worse".
* :func:`ramp_score` — more is better, up to a point. Flagpole magnitude,
  volume expansion, touch counts.
* :func:`decay_score` — less is better. ATR contraction ratios, volume dry-up.
* :func:`step_score` — a genuine discontinuity, used *only* where the underlying
  fact is discontinuous. A retracement through the flagpole origin is not "a bad
  flag", it is not a flag, and pretending otherwise to keep the curve smooth
  would be dishonest in the other direction.

**Taper, not cliff.** Outside the preferred band the score falls linearly to
zero over a stated tolerance rather than dropping to zero at the edge. The
tolerance is what turns "50% is the rule" into "we prefer under 50%, we tolerate
up to 62%, and beyond that we do not". That is a defensible statement about
markets; a hard 50% is not.

**No curve here was fitted to returns.** Phase 4 explicitly must not optimise
against trading outcomes, and these shapes come from the structural reasoning
recorded alongside each default, not from a search. Phase 9 is where fitting
belongs, with an out-of-sample harness to make it meaningful.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from tradeit.errors import ConfigError


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return float(max(low, min(high, value)))


def band_score(
    value: float,
    *,
    ideal_low: float,
    ideal_high: float,
    tolerance_low: float,
    tolerance_high: float,
    floor: float = 0.0,
) -> float:
    """100 inside the ideal band, tapering to ``floor`` at the tolerances.

    ``tolerance_low <= ideal_low <= ideal_high <= tolerance_high``. Beyond the
    tolerances the score is ``floor`` — which defaults to zero but is
    configurable, because for several dimensions "outside the preferred range"
    means poor rather than disqualifying.
    """
    if not tolerance_low <= ideal_low <= ideal_high <= tolerance_high:
        raise ConfigError(
            f"band must be ordered: tolerance_low({tolerance_low}) <= "
            f"ideal_low({ideal_low}) <= ideal_high({ideal_high}) <= "
            f"tolerance_high({tolerance_high})"
        )
    if not np.isfinite(value):
        return floor

    if ideal_low <= value <= ideal_high:
        return 100.0
    if value < ideal_low:
        span = ideal_low - tolerance_low
        if span <= 0:
            return floor
        fraction = (value - tolerance_low) / span
    else:
        span = tolerance_high - ideal_high
        if span <= 0:
            return floor
        fraction = (tolerance_high - value) / span

    return _clamp(floor + (100.0 - floor) * fraction, floor, 100.0)


def ramp_score(
    value: float, *, zero_at: float, full_at: float, floor: float = 0.0, cap: float = 100.0
) -> float:
    """Rises from ``floor`` at ``zero_at`` to ``cap`` at ``full_at``, then flat.

    Flat beyond ``full_at`` on purpose. A 60% flagpole is not twice as good as a
    30% one; past a point, more magnitude stops being evidence of quality and
    starts being evidence of exhaustion. Detectors that care about the
    exhaustion side score it as a separate penalty rather than by bending this
    curve downward, so the two effects stay separable.
    """
    if full_at == zero_at:
        raise ConfigError("ramp needs distinct zero_at and full_at")
    if not np.isfinite(value):
        return floor
    fraction = (value - zero_at) / (full_at - zero_at)
    return _clamp(floor + (cap - floor) * fraction, min(floor, cap), max(floor, cap))


def decay_score(value: float, *, full_at: float, zero_at: float, floor: float = 0.0) -> float:
    """Falls from 100 at ``full_at`` to ``floor`` at ``zero_at``.

    For dimensions where smaller is better: an ATR contraction ratio of 0.4 is
    better than 0.9. Just :func:`ramp_score` with the endpoints reversed, named
    separately because reading ``ramp_score(x, zero_at=0.9, full_at=0.4)`` at a
    call site is a reliable way to introduce a sign error.
    """
    return ramp_score(value, zero_at=zero_at, full_at=full_at, floor=floor)


def step_score(value: float, *, threshold: float, above: float, below: float) -> float:
    """A genuine discontinuity.

    Reserved for facts that really are discontinuous. Retracing through the
    flagpole origin is the canonical case: the structure the pattern was
    defined against no longer exists, and smoothing that boundary would report
    a broken pattern as a slightly worse one.

    Every use of this function should be able to answer "what changes
    qualitatively at the threshold?" If the answer is "nothing, it is just
    where we drew the line", the dimension wants :func:`band_score`.
    """
    if not np.isfinite(value):
        return below
    return above if value >= threshold else below


def linear_interpolate(value: float, points: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear curve through explicit (input, score) points.

    The escape hatch for shapes the four primitives do not cover, and the form
    a config file can express directly. Points must be sorted by input; outside
    the range the endpoints hold.
    """
    if len(points) < 2:
        raise ConfigError("a piecewise curve needs at least two points")
    xs = [p[0] for p in points]
    if xs != sorted(xs):
        raise ConfigError("curve points must be sorted by input value")
    if not np.isfinite(value):
        return float(points[0][1])

    if value <= xs[0]:
        return float(points[0][1])
    if value >= xs[-1]:
        return float(points[-1][1])
    for (x0, y0), (x1, y1) in pairwise(points):
        if x0 <= value <= x1:
            if x1 == x0:
                return float(y1)
            return float(y0 + (y1 - y0) * (value - x0) / (x1 - x0))
    return float(points[-1][1])


@dataclass(frozen=True, slots=True)
class WeightedScore:
    """The composite of several component scores, renormalised for gaps.

    The renormalisation is the important part. When a component is unavailable
    -- no benchmark series, so relative strength cannot be computed -- it is
    excluded from *both* the numerator and the denominator. Scoring it zero
    instead would report a pattern as low quality for the sole reason that an
    optional input was missing, which is a data-availability fact masquerading
    as a structural judgement.
    """

    value: float
    available_weight: float
    missing: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.missing


def combine(scores: Mapping[str, float | None], weights: Mapping[str, float]) -> WeightedScore:
    """Weighted mean over available components.

    A ``None`` score means unavailable and is excluded from the denominator.
    Zero means measured-and-bad and counts fully.
    """
    unknown = sorted(set(scores) - set(weights))
    if unknown:
        raise ConfigError(f"no weight configured for component(s): {unknown}")

    total = 0.0
    weighted = 0.0
    missing: list[str] = []
    for name, score in scores.items():
        weight = weights[name]
        if score is None:
            missing.append(name)
            continue
        if not 0.0 <= score <= 100.0:
            raise ConfigError(f"component {name!r} scored {score}, outside [0, 100]")
        total += weight
        weighted += score * weight

    if total <= 0:
        return WeightedScore(0.0, 0.0, tuple(sorted(missing)))
    return WeightedScore(weighted / total, total, tuple(sorted(missing)))


def confidence_from(
    *,
    available_weight: float,
    total_weight: float,
    component_scores: Sequence[float],
    session_count: int,
    minimum_sessions: int,
) -> float:
    """How much to believe a quality score.

    Distinct from quality, and the distinction matters: a 90 computed from three
    of eight components on twelve sessions of data is not the same claim as a 90
    computed from all eight on sixty sessions. Consumers that cannot tell them
    apart will treat them identically, which is the outcome this number exists
    to prevent.

    Three terms:

    * **Coverage** -- how much of the configured weight was actually computable.
    * **Agreement** -- how tightly the components cluster. Components spread
      from 20 to 95 average to something respectable while describing a
      structure nobody would recognise, and the mean alone hides that.
    * **Maturity** -- how much history the pattern has. A structure at its
      minimum length has had less opportunity to prove itself.
    """
    coverage = available_weight / total_weight if total_weight > 0 else 0.0

    if len(component_scores) >= 2:
        spread = float(np.std(component_scores))
        # 30 points of standard deviation is genuine disagreement; the
        # components are describing different structures.
        agreement = _clamp(1.0 - spread / 30.0, 0.0, 1.0)
    else:
        agreement = 0.5

    maturity = (
        _clamp(session_count / (minimum_sessions * 2.0), 0.0, 1.0) if minimum_sessions > 0 else 1.0
    )

    return _clamp((0.45 * coverage + 0.35 * agreement + 0.20 * maturity) * 100.0)
