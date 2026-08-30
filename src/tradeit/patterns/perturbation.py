"""Score stability under perturbation.

A quality score that swings twenty points when a single bar moves by a tenth of
a percent is not measuring structure — it is measuring noise, and every
downstream use of it inherits that. So the question this module answers is: *how
much does a score move when the input moves a little?*

**Smooth is not the goal; the right kind of discontinuity is.** Two things are
being separated:

* **Continuous dimensions.** Adding a little price noise, or nudging a
  retracement by a percent, should move a score a little. A large jump here is a
  defect: it means a threshold is being crossed where the underlying reality is
  continuous.
* **Structural transitions.** A newly confirmed pivot changes where a structure
  *starts*; price closing below an invalidation level changes what the structure
  *is*. Those are genuine discontinuities and forcing them to be smooth would be
  forcing the detector to lie about a real change.

So the report separates *magnitude* from *cause*. A large jump accompanied by a
change of structural start or of state is classified and kept; a large jump with
neither is the finding worth acting on.

**What is perturbed.** Price noise, volume noise, and the structural knobs each
generator exposes — retracement depth, boundary slope, duration, volatility,
contraction depth. Pivot placement is perturbed indirectly, by adding noise
large enough to move which bars qualify as swings; that is the honest way to
test it, because pivots are not a free parameter.

Everything is seeded, so a surprising jump is reproducible rather than
re-argued.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise

import numpy as np

from tradeit.core.models import OhlcvBar
from tradeit.patterns.base import Detector, PatternInstance
from tradeit.patterns.synthetic import GeneratedSeries

Builder = Callable[[int], GeneratedSeries]


@dataclass(frozen=True, slots=True)
class Perturbation:
    """One before/after pair and everything that changed between them."""

    seed: int
    baseline_quality: float
    perturbed_quality: float
    #: Whether the structure's start date moved. A legitimate reason for a jump.
    start_moved: bool
    #: Whether any *other* key point moved -- a head, a right shoulder, a rim.
    #:
    #: Added after the first stability run reported three "unexplained" jumps of
    #: ~28 points in the inverse head and shoulders under 0.1% price noise.
    #: Diagnosing one showed the right shoulder had been re-identified nineteen
    #: sessions earlier, with `shoulder_symmetry` moving 39.5 -> 100 accordingly.
    #: That is a genuine structural re-identification and the score was right to
    #: follow it; the *classifier* was blind, because it only compared the start.
    #: A measurement instrument that mislabels real structure as noise is worse
    #: than useless -- it points investigation at the wrong thing.
    geometry_moved: bool
    #: Whether the lifecycle state changed. Also legitimate.
    state_changed: bool
    #: Whether the pattern vanished or appeared. The largest legitimate jump.
    presence_changed: bool

    @property
    def delta(self) -> float:
        return abs(self.perturbed_quality - self.baseline_quality)

    @property
    def structurally_explained(self) -> bool:
        """Whether a jump has a structural cause rather than being noise."""
        return (
            self.start_moved or self.geometry_moved or self.state_changed or self.presence_changed
        )


@dataclass(slots=True)
class StabilityReport:
    """Perturbation results for one detector on one dimension."""

    detector: str
    dimension: str
    #: What was varied, in one line, so a row reads without the source.
    intent: str
    samples: list[Perturbation] = field(default_factory=list)

    @property
    def tested(self) -> int:
        return len(self.samples)

    @property
    def deltas(self) -> list[float]:
        return [s.delta for s in self.samples]

    @property
    def median_delta(self) -> float:
        return float(statistics.median(self.deltas)) if self.samples else 0.0

    def percentile_delta(self, q: float) -> float:
        return float(np.percentile(self.deltas, q)) if self.samples else 0.0

    @property
    def largest(self) -> Perturbation | None:
        return max(self.samples, key=lambda s: s.delta, default=None)

    @property
    def unexplained_jumps(self) -> list[Perturbation]:
        """Large moves with no structural cause. The findings that matter."""
        threshold = max(10.0, 3.0 * self.median_delta)
        return [s for s in self.samples if s.delta > threshold and not s.structurally_explained]

    def summary(self) -> dict[str, object]:
        largest = self.largest
        return {
            "detector": self.detector,
            "dimension": self.dimension,
            "intent": self.intent,
            "tested": self.tested,
            "median_delta": round(self.median_delta, 2),
            "p95_delta": round(self.percentile_delta(95), 2),
            "max_delta": round(largest.delta, 2) if largest else 0.0,
            "max_delta_cause": _cause(largest),
            "unexplained_jumps": len(self.unexplained_jumps),
        }

    def format_row(self) -> str:
        largest = self.largest
        return (
            f"{self.detector:<24}{self.dimension:<22}n={self.tested:>4}  "
            f"med={self.median_delta:>5.1f}  p95={self.percentile_delta(95):>5.1f}  "
            f"max={largest.delta if largest else 0.0:>5.1f}  "
            f"cause={_cause(largest):<22}  unexplained={len(self.unexplained_jumps)}"
        )


def _cause(sample: Perturbation | None) -> str:
    if sample is None:
        return "none"
    if sample.presence_changed:
        return "appeared/vanished"
    if sample.start_moved:
        return "structural start moved"
    if sample.geometry_moved:
        return "key point re-identified"
    if sample.state_changed:
        return "lifecycle state changed"
    return "continuous"


def add_price_noise(series: GeneratedSeries, magnitude: float, *, seed: int) -> GeneratedSeries:
    """Jitter every bar by a small multiplicative factor.

    The whole bar moves together, so the OHLC relationships survive: jittering
    each field independently would produce bars whose high is below their close,
    and the detector would then be being tested on invalid data rather than on
    noisy data.
    """
    rng = np.random.default_rng(seed)
    factors = 1.0 + rng.normal(0.0, magnitude, len(series.bars))
    return GeneratedSeries(
        [_scaled(bar, float(factor)) for bar, factor in zip(series.bars, factors, strict=True)],
        dict(series.truth),
    )


def add_volume_noise(series: GeneratedSeries, magnitude: float, *, seed: int) -> GeneratedSeries:
    """Jitter volume only, leaving every price untouched.

    Isolates the volume components: a score that moves here has moved for a
    volume reason, which is the point of testing the dimension separately.
    """
    rng = np.random.default_rng(seed)
    factors = np.exp(rng.normal(0.0, magnitude, len(series.bars)))
    return GeneratedSeries(
        [
            bar.model_copy(
                update={"volume": Decimal(str(int(max(1.0, float(bar.volume) * factor))))}
            )
            for bar, factor in zip(series.bars, factors, strict=True)
        ],
        dict(series.truth),
    )


def shift_timing(series: GeneratedSeries, sessions: int) -> GeneratedSeries:
    """Drop the first ``sessions`` bars, moving every structure earlier.

    Tests that a score depends on the shape of a window rather than on where it
    happens to sit in the array. Dropping from the *front* leaves the structure
    and the knowledge boundary untouched, which is what makes this a timing test
    rather than a truncation test.
    """
    return GeneratedSeries(list(series.bars[sessions:]), dict(series.truth))


def _scaled(bar: OhlcvBar, factor: float) -> OhlcvBar:
    return bar.model_copy(
        update={
            "open": _price(bar.open, factor),
            "high": _price(bar.high, factor),
            "low": _price(bar.low, factor),
            "close": _price(bar.close, factor),
        }
    )


def _price(value: Decimal, factor: float) -> Decimal:
    return Decimal(str(round(float(value) * factor, 4)))


def _best(instances: Sequence[PatternInstance]) -> PatternInstance | None:
    return max(instances, key=lambda p: p.quality, default=None)


def _key_points_moved(before: PatternInstance | None, after: PatternInstance | None) -> bool:
    """Whether any named structural point landed on a different session.

    Compared by *date* rather than by price: a rim that moved by a cent under
    price jitter is the same rim, and a rim that moved to a different session is
    a different rim. Only the second is a structural re-identification, and only
    the second can justify a large score change.
    """
    if before is None or after is None:
        return False
    shared = set(before.geometry.key_points) & set(after.geometry.key_points)
    return any(
        before.geometry.key_points[name].session_date
        != after.geometry.key_points[name].session_date
        for name in shared
    )


def perturb(
    detector: Detector,
    baseline: Builder,
    perturbed: Callable[[GeneratedSeries, int], GeneratedSeries],
    *,
    dimension: str,
    intent: str,
    trials: int,
) -> StabilityReport:
    """Compare a detector's reading of a series with its reading of a nudged one."""
    report = StabilityReport(detector=detector.name, dimension=dimension, intent=intent)

    for seed in range(trials):
        original = baseline(seed)
        modified = perturbed(original, seed)

        before = _best(detector.detect(original.bars, original.last_session))
        after = _best(detector.detect(modified.bars, modified.last_session))

        report.samples.append(
            Perturbation(
                seed=seed,
                baseline_quality=before.quality if before else 0.0,
                perturbed_quality=after.quality if after else 0.0,
                start_moved=(
                    before is not None
                    and after is not None
                    and before.geometry.start_date != after.geometry.start_date
                ),
                geometry_moved=_key_points_moved(before, after),
                state_changed=(
                    before is not None and after is not None and before.state is not after.state
                ),
                presence_changed=(before is None) != (after is None),
            )
        )
    return report


def sweep(
    detector: Detector,
    builder: Callable[[float], GeneratedSeries],
    values: Sequence[float],
    component: str | None = None,
) -> list[float]:
    """One score per parameter value, for monotonicity checks.

    Returns the component score when ``component`` is named and the composite
    otherwise. Missing detections score zero, which is the honest reading: a
    parameter pushed far enough that the structure ceases to exist has not
    produced a low score, it has produced no pattern.
    """
    out: list[float] = []
    for value in values:
        series = builder(value)
        found = _best(detector.detect(series.bars, series.last_session))
        if found is None:
            out.append(0.0)
        elif component is None:
            out.append(found.quality)
        else:
            scored = found.component(component)
            out.append(scored.score if scored is not None else 0.0)
    return out


def is_non_increasing(values: Sequence[float], *, tolerance: float = 1.0) -> bool:
    """Whether a sequence never rises by more than ``tolerance``.

    A tolerance rather than a strict comparison because the generators are
    stochastic: a one-point rise between adjacent settings is noise, and
    demanding strict monotonicity would be demanding the generator be
    deterministic in a dimension it is not.
    """
    return all(later <= earlier + tolerance for earlier, later in pairwise(values))


def is_non_decreasing(values: Sequence[float], *, tolerance: float = 1.0) -> bool:
    return all(later >= earlier - tolerance for earlier, later in pairwise(values))


__all__ = [
    "Perturbation",
    "StabilityReport",
    "add_price_noise",
    "add_volume_noise",
    "is_non_decreasing",
    "is_non_increasing",
    "perturb",
    "shift_timing",
    "sweep",
]
