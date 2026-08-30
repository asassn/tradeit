"""Score distributions over large synthetic corpora.

A pass/fail assertion on a detector tells you almost nothing. "No bull flag was
found in this bear flag" is one observation about one series; what matters is
the *shape* of the score distribution across thousands, because that is what
says whether a quality score separates structure from noise and where it stops
doing so.

So this module runs families of generated series and reports percentiles rather
than verdicts. Two rules govern how the results are used:

**Nothing here is tuned against.** The brief is explicit and the reasoning is
sound: adjusting detector thresholds until a false-positive figure looks better
fits the detector to its own corpus, and the resulting number then describes the
corpus rather than the detector. Distributions are measured, reported, and
argued with — not optimised.

**Nothing here sets a trading threshold.** Phase 4 sets none. The distributions
say where a quality floor *would* sit if someone wanted one; choosing it is a
later phase's decision, made with real data and out-of-sample evidence.

Everything is seeded, so a surprising number can be reproduced exactly rather
than re-argued.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from tradeit.patterns.base import Detector, PatternInstance, PatternState
from tradeit.patterns.synthetic import BullFlagSpec, GeneratedSeries, PatternGenerator

#: Quality bands used for reporting. Deliberately coarse, and deliberately not
#: named "tradeable"/"marginal"/etc -- naming them that way would be setting an
#: operational threshold under cover of a reporting convention.
QUALITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0-40", 0.0, 40.0),
    ("40-55", 40.0, 55.0),
    ("55-70", 55.0, 70.0),
    ("70-85", 70.0, 85.0),
    ("85-100", 85.0, 100.0),
)


@dataclass(frozen=True, slots=True)
class ScoreDistribution:
    """What a detector produced across one family of generated series."""

    family: str
    tested: int
    #: Best quality per series; zero where nothing was detected.
    scores: tuple[float, ...]
    #: Series that produced at least one candidate structure.
    with_structure: int
    #: Coverage of the best instance per series, where one existed.
    coverages: tuple[float, ...] = ()
    states: tuple[str, ...] = ()

    @property
    def structure_rate(self) -> float:
        return self.with_structure / self.tested if self.tested else 0.0

    def percentile(self, q: float) -> float:
        return float(np.percentile(self.scores, q)) if self.scores else 0.0

    @property
    def median(self) -> float:
        return self.percentile(50)

    @property
    def maximum(self) -> float:
        return max(self.scores, default=0.0)

    def band_counts(self) -> dict[str, int]:
        counts = {name: 0 for name, _, _ in QUALITY_BANDS}
        for score in self.scores:
            if score <= 0:
                continue
            for name, low, high in QUALITY_BANDS:
                if low <= score < high or (high == 100.0 and score == 100.0):
                    counts[name] += 1
                    break
        return counts

    def band_rates(self) -> dict[str, float]:
        if not self.tested:
            return {name: 0.0 for name, _, _ in QUALITY_BANDS}
        return {name: count / self.tested for name, count in self.band_counts().items()}

    def summary(self) -> dict[str, object]:
        return {
            "family": self.family,
            "tested": self.tested,
            "structure_rate": round(self.structure_rate, 4),
            "median": round(self.median, 2),
            "p90": round(self.percentile(90), 2),
            "p95": round(self.percentile(95), 2),
            "p99": round(self.percentile(99), 2),
            "max": round(self.maximum, 2),
            "bands": {k: round(v, 4) for k, v in self.band_rates().items()},
        }

    def format_row(self) -> str:
        return (
            f"{self.family:<24} n={self.tested:>5}  "
            f"struct={self.structure_rate:>6.1%}  "
            f"med={self.median:>5.1f}  p90={self.percentile(90):>5.1f}  "
            f"p95={self.percentile(95):>5.1f}  p99={self.percentile(99):>5.1f}  "
            f"max={self.maximum:>5.1f}"
        )


@dataclass(slots=True)
class CorpusReport:
    """Distributions across every family in a run."""

    distributions: list[ScoreDistribution] = field(default_factory=list)

    def add(self, distribution: ScoreDistribution) -> None:
        self.distributions.append(distribution)

    def get(self, family: str) -> ScoreDistribution | None:
        return next((d for d in self.distributions if d.family == family), None)

    @property
    def total_tested(self) -> int:
        return sum(d.tested for d in self.distributions)

    def worst_offender(self) -> ScoreDistribution | None:
        """The family producing the highest scores.

        Usually the most informative single number in a corpus run: it names
        the structure the detector is most likely to be fooled by, which is
        where a real-data validation should look first.
        """
        return max(self.distributions, key=lambda d: d.percentile(99), default=None)

    def format_table(self) -> str:
        lines = [d.format_row() for d in self.distributions]
        return "\n".join(lines)

    def summary(self) -> dict[str, object]:
        return {
            "total_tested": self.total_tested,
            "families": [d.summary() for d in self.distributions],
        }


def _best(instances: Sequence[PatternInstance]) -> PatternInstance | None:
    return max(instances, key=lambda p: p.quality, default=None)


def run_family(
    detector: Detector,
    family: str,
    builder: Callable[[int], GeneratedSeries],
    *,
    trials: int,
) -> ScoreDistribution:
    """Run one family of generated series through a detector."""
    scores: list[float] = []
    coverages: list[float] = []
    states: list[str] = []
    with_structure = 0

    for seed in range(trials):
        series = builder(seed)
        instances = detector.detect(series.bars, series.last_session)
        best = _best(instances)
        if best is None:
            scores.append(0.0)
            continue
        with_structure += 1
        scores.append(best.quality)
        coverages.append(best.evidence_coverage)
        states.append(str(best.state))

    return ScoreDistribution(
        family=family,
        tested=trials,
        scores=tuple(scores),
        with_structure=with_structure,
        coverages=tuple(coverages),
        states=tuple(states),
    )


def adversarial_families(
    generator: PatternGenerator,
) -> dict[str, Callable[[int], GeneratedSeries]]:
    """Every negative family, keyed by name.

    The volatility variants of the plain random walk are separate entries
    rather than one pooled family. A detector's false-positive rate on quiet
    noise and on violent noise are different numbers, and averaging them
    produces a figure that describes neither.
    """
    return {
        "random_walk_low_vol": lambda s: generator.random_walk(140, volatility=0.006, seed=s),
        "random_walk_mid_vol": lambda s: generator.random_walk(140, volatility=0.014, seed=s),
        "random_walk_high_vol": lambda s: generator.random_walk(140, volatility=0.030, seed=s),
        "trending_walk": lambda s: generator.trending_walk(seed=s),
        "declining_walk": lambda s: generator.random_walk(140, drift=-0.0015, seed=s + 500),
        "mean_reverting": lambda s: generator.mean_reverting(seed=s),
        "autocorrelated_noise": lambda s: generator.autocorrelated_noise(seed=s),
        "regime_switching": lambda s: generator.regime_switching(seed=s),
        "quiet_drift": lambda s: generator.quiet_drift(seed=s),
        "choppy_range": lambda s: generator.choppy_range(seed=s),
        "broad_volatile_range": lambda s: generator.broad_volatile_range(seed=s),
        "broadening_formation": lambda s: generator.broadening_formation(seed=s),
        "bear_flag": lambda s: generator.bear_flag(seed=s),
        "falling_knife": lambda s: generator.falling_knife(seed=s),
        "parabolic": lambda s: generator.parabolic(seed=s),
        "single_day_spike": lambda s: generator.single_day_spike(seed=s),
        "gap_and_fade": lambda s: generator.gap_and_fade(seed=s),
        "earnings_gap": lambda s: generator.earnings_gap(seed=s),
        "low_volume_noise": lambda s: generator.low_volume_noise(seed=s),
        "deep_pullback": lambda s: generator.deep_pullback(seed=s),
        "failed_base": lambda s: generator.failed_base(seed=s),
    }


#: Positive grades, from textbook to intentionally broken. Each varies several
#: dimensions at once, because a detector that ranks a corpus varying one knob
#: has been shown much less than one that ranks a corpus varying several.
POSITIVE_GRADES: dict[str, BullFlagSpec] = {
    "clean": BullFlagSpec(
        pole_gain=0.28,
        pole_sessions=11,
        retracement=0.30,
        flag_sessions=9,
        flag_slope=-0.002,
        volume_contraction=0.45,
        range_contraction=0.45,
        noise=0.4,
    ),
    "moderate": BullFlagSpec(
        pole_gain=0.18,
        pole_sessions=14,
        retracement=0.42,
        flag_sessions=12,
        flag_slope=-0.005,
        volume_contraction=0.75,
        range_contraction=0.75,
        noise=1.1,
    ),
    "borderline": BullFlagSpec(
        pole_gain=0.11,
        pole_sessions=20,
        retracement=0.58,
        flag_sessions=18,
        flag_slope=-0.009,
        volume_contraction=1.05,
        range_contraction=1.0,
        noise=2.0,
    ),
    "flawed": BullFlagSpec(
        pole_gain=0.09,
        pole_sessions=28,
        retracement=0.78,
        flag_sessions=26,
        flag_slope=-0.013,
        volume_contraction=1.3,
        range_contraction=1.2,
        noise=3.0,
    ),
}


def positive_families(
    generator: PatternGenerator,
) -> dict[str, Callable[[int], GeneratedSeries]]:
    def make(spec: BullFlagSpec) -> Callable[[int], GeneratedSeries]:
        return lambda seed: generator.bull_flag(spec, seed=seed)

    return {grade: make(spec) for grade, spec in POSITIVE_GRADES.items()}


def run_corpus(
    detector: Detector,
    families: dict[str, Callable[[int], GeneratedSeries]],
    *,
    trials: int,
) -> CorpusReport:
    report = CorpusReport()
    for name, builder in families.items():
        report.add(run_family(detector, name, builder, trials=trials))
    return report


def grade_ordering_holds(report: CorpusReport, grades: Iterable[str]) -> bool:
    """Whether median quality falls monotonically across an ordered grade list.

    The claim a positive corpus should support: clean > moderate > borderline >
    flawed, on medians. Deliberately *not* asserted on maxima or on individual
    series -- a borderline flag that happens to score above a clean one is
    ordinary noise, and demanding otherwise would be demanding a detector be
    right about every case rather than right on average.
    """
    medians = []
    for grade in grades:
        distribution = report.get(grade)
        if distribution is None:
            return False
        medians.append(distribution.median)
    return medians == sorted(medians, reverse=True)


def state_breakdown(distribution: ScoreDistribution) -> dict[str, int]:
    counts: dict[str, int] = {str(s): 0 for s in PatternState}
    for state in distribution.states:
        counts[state] = counts.get(state, 0) + 1
    return {k: v for k, v in counts.items() if v}
