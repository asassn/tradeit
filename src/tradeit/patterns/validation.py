"""Per-detector behavioural characterisation.

:mod:`tradeit.patterns.corpus` measures one detector against one family of
series. This module measures **all twelve against a structured set of cohorts**,
and reports the statistics the Phase 4 completion gate asks for.

**Why cohorts rather than positives and negatives.** A single "negative rate"
averages together things that are not alike. A detector's behaviour on quiet
noise, on violent noise, on its nearest structural neighbour, and on a
deliberately broken version of its own pattern are four different facts, and a
figure that pools them describes none of them. So each family declares seven
kinds of cohort:

``POSITIVE``
    The pattern as its definition describes it.
``MODERATE``
    Real but unremarkable. Most of what a scan actually finds.
``BORDERLINE``
    At the edge of the definition. Where a reader most needs to know what the
    detector does.
``INVALID``
    A deliberately broken version of the same structure.
``COMPETING``
    The nearest structural neighbour — the family most likely to be confused
    with this one.
``NOISE``
    Random walks and market-like noise with no structure drawn into them.
``ADVERSARIAL``
    Pattern-specific traps: the thing that fools *this* detector in particular.

**Candidate frequency is not quality frequency**, and the gate is right to
insist on the distinction. A detector may legitimately find many candidate
structures and rate almost none of them highly; a detector that finds few but
rates them all highly is a different animal with a different failure mode.
:class:`CohortStatistics` reports them separately and never combines them into a
single "accuracy".

**Nothing here is tuned against.** The distributions are measured, published and
argued with. Moving a threshold because a number looked unflattering would fit
the detector to its own corpus, and the number would then describe the corpus.

**Nothing here uses future returns.** No cohort is selected, weighted or scored
by what price did afterwards. Phase 4 characterises pattern recognition; outcome
research is a later phase's work and mixing the two would let the detector be
"validated" by the thing it is supposed to be an input to.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from tradeit.patterns.base import Detector, PatternInstance, PatternState
from tradeit.patterns.synthetic import BullFlagSpec, GeneratedSeries, PatternGenerator, VcpSpec

Builder = Callable[[int], GeneratedSeries]

#: The quality thresholds the gate asks to be reported at. Cumulative ("at or
#: above"), and deliberately not named -- a band called "tradeable" would be an
#: operational threshold smuggled in as a reporting convention.
QUALITY_THRESHOLDS: tuple[float, ...] = (50.0, 60.0, 70.0, 80.0, 90.0)


class CohortKind(StrEnum):
    """What a cohort is for. Drives how its statistics should be read."""

    POSITIVE = "positive"
    MODERATE = "moderate"
    BORDERLINE = "borderline"
    INVALID = "invalid"
    COMPETING = "competing"
    NOISE = "noise"
    ADVERSARIAL = "adversarial"

    @property
    def is_negative(self) -> bool:
        """Whether a high score here is evidence against the detector.

        ``INVALID`` and ``COMPETING`` are deliberately excluded. A broken
        version of a pattern legitimately produces a low-scoring instance rather
        than silence, and a competing structure may legitimately qualify under
        two definitions at once — treating either as a straight false positive
        would push the detectors toward forced exclusivity, which the brief
        rejects.
        """
        return self in {CohortKind.NOISE, CohortKind.ADVERSARIAL}


@dataclass(frozen=True, slots=True)
class Cohort:
    """A named, seeded family of series with a stated purpose."""

    name: str
    kind: CohortKind
    builder: Builder
    #: One line on what this cohort is testing. Reproduced in the report so a
    #: reader does not have to open the source to know what a row means.
    intent: str = ""


@dataclass(frozen=True, slots=True)
class CohortStatistics:
    """Everything measured for one detector against one cohort."""

    detector: str
    cohort: str
    kind: CohortKind
    intent: str
    tested: int
    #: Best quality per series, zero where nothing was detected. Includes the
    #: zeros, so percentiles describe the cohort rather than the subset that
    #: happened to produce something.
    scores: tuple[float, ...]
    #: Coverage of the best instance, only where one existed.
    coverages: tuple[float, ...]
    states: tuple[str, ...]
    #: Series producing at least one candidate structure.
    candidates: int
    #: Total instances across all series -- a detector emitting six readings per
    #: series is doing something different from one emitting a single reading.
    instances: int

    # -- frequencies, kept separate on purpose -------------------------------

    @property
    def candidate_rate(self) -> float:
        return self.candidates / self.tested if self.tested else 0.0

    @property
    def mature_rate(self) -> float:
        """Series whose best instance reached an established state."""
        established = {
            str(PatternState.MATURE),
            str(PatternState.NEAR_BREAKOUT),
            str(PatternState.BROKEN_OUT_UNCONFIRMED),
        }
        return sum(1 for s in self.states if s in established) / self.tested if self.tested else 0.0

    def high_quality_rate(self, threshold: float = 70.0) -> float:
        return sum(1 for s in self.scores if s >= threshold) / self.tested if self.tested else 0.0

    @property
    def invalidation_rate(self) -> float:
        terminal = {str(PatternState.INVALIDATED), str(PatternState.EXPIRED)}
        return sum(1 for s in self.states if s in terminal) / self.tested if self.tested else 0.0

    @property
    def instances_per_series(self) -> float:
        return self.instances / self.tested if self.tested else 0.0

    # -- distribution --------------------------------------------------------

    def percentile(self, q: float) -> float:
        return float(np.percentile(self.scores, q)) if self.scores else 0.0

    @property
    def median(self) -> float:
        return self.percentile(50)

    @property
    def mean(self) -> float:
        return float(statistics.fmean(self.scores)) if self.scores else 0.0

    @property
    def maximum(self) -> float:
        return max(self.scores, default=0.0)

    @property
    def detected_median(self) -> float:
        """Median among series that produced something.

        The counterpart to :attr:`median`: with a low candidate rate the
        all-series median is zero and says nothing about how the detector rates
        what it does find.
        """
        found = [s for s in self.scores if s > 0]
        return float(np.percentile(found, 50)) if found else 0.0

    def coverage_percentile(self, q: float) -> float:
        return float(np.percentile(self.coverages, q)) if self.coverages else 0.0

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for state in self.states:
            counts[state] = counts.get(state, 0) + 1
        return dict(sorted(counts.items()))

    def threshold_rates(self) -> dict[str, float]:
        return {f">={int(t)}": self.high_quality_rate(t) for t in QUALITY_THRESHOLDS}

    def summary(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "cohort": self.cohort,
            "kind": str(self.kind),
            "intent": self.intent,
            "tested": self.tested,
            "candidate_rate": round(self.candidate_rate, 4),
            "mature_rate": round(self.mature_rate, 4),
            "invalidation_rate": round(self.invalidation_rate, 4),
            "instances_per_series": round(self.instances_per_series, 3),
            "mean": round(self.mean, 2),
            "median": round(self.median, 2),
            "detected_median": round(self.detected_median, 2),
            "p50": round(self.percentile(50), 2),
            "p75": round(self.percentile(75), 2),
            "p90": round(self.percentile(90), 2),
            "p95": round(self.percentile(95), 2),
            "p99": round(self.percentile(99), 2),
            "max": round(self.maximum, 2),
            "coverage_p10": round(self.coverage_percentile(10), 1),
            "coverage_p50": round(self.coverage_percentile(50), 1),
            "coverage_p90": round(self.coverage_percentile(90), 1),
            "thresholds": {k: round(v, 4) for k, v in self.threshold_rates().items()},
            "states": self.state_counts(),
        }


@dataclass(slots=True)
class FamilyReport:
    """Every cohort's statistics for one detector."""

    detector: str
    version: int
    cohorts: list[CohortStatistics] = field(default_factory=list)

    def get(self, cohort: str) -> CohortStatistics | None:
        return next((c for c in self.cohorts if c.cohort == cohort), None)

    def of_kind(self, kind: CohortKind) -> list[CohortStatistics]:
        return [c for c in self.cohorts if c.kind is kind]

    @property
    def total_tested(self) -> int:
        return sum(c.tested for c in self.cohorts)

    def worst_negative(self) -> CohortStatistics | None:
        """The negative cohort producing the highest 99th percentile.

        Usually the single most informative number in a run: it names the
        structure this detector is most likely to be fooled by, which is where
        real-data validation should look first.
        """
        negatives = [c for c in self.cohorts if c.kind.is_negative]
        return max(negatives, key=lambda c: c.percentile(99), default=None)

    def separation(self, threshold: float = 70.0) -> float:
        """Positive high-quality rate minus the worst negative's.

        A blunt single number, reported alongside the distributions rather than
        instead of them. It is not an accuracy: no cost is attached to either
        error, because attaching one is a later phase's decision.
        """
        positives = self.of_kind(CohortKind.POSITIVE)
        if not positives:
            return 0.0
        best_positive = max(c.high_quality_rate(threshold) for c in positives)
        negatives = [c for c in self.cohorts if c.kind.is_negative]
        worst = max((c.high_quality_rate(threshold) for c in negatives), default=0.0)
        return best_positive - worst

    def summary(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "version": self.version,
            "total_tested": self.total_tested,
            "separation_at_70": round(self.separation(70.0), 4),
            "cohorts": [c.summary() for c in self.cohorts],
        }

    def format_table(self) -> str:
        header = (
            f"{'cohort':<28}{'kind':<12}{'n':>6}{'cand':>8}{'mature':>8}"
            f"{'>=70':>8}{'>=90':>8}{'med':>7}{'p90':>7}{'max':>7}"
        )
        lines = [header, "-" * len(header)]
        for c in self.cohorts:
            lines.append(
                f"{c.cohort:<28}{c.kind!s:<12}{c.tested:>6}"
                f"{c.candidate_rate:>7.1%} {c.mature_rate:>7.1%} "
                f"{c.high_quality_rate(70):>7.1%} {c.high_quality_rate(90):>7.1%} "
                f"{c.median:>6.1f} {c.percentile(90):>6.1f} {c.maximum:>6.1f}"
            )
        return "\n".join(lines)


def characterise(
    detector: Detector,
    cohorts: Sequence[Cohort],
    *,
    trials: int,
    seed_offset: int = 0,
) -> FamilyReport:
    """Run one detector across its cohorts.

    ``seed_offset`` shifts every cohort's seeds together, so a second run with a
    different offset is an independent sample rather than the same series again.
    Runs are otherwise a pure function of the seeds, which is what makes a
    surprising number reproducible instead of re-argued.
    """
    report = FamilyReport(detector=detector.name, version=detector.version)
    for cohort in cohorts:
        report.cohorts.append(_run_cohort(detector, cohort, trials=trials, seed_offset=seed_offset))
    return report


def _run_cohort(
    detector: Detector, cohort: Cohort, *, trials: int, seed_offset: int
) -> CohortStatistics:
    scores: list[float] = []
    coverages: list[float] = []
    states: list[str] = []
    candidates = 0
    instances = 0

    for index in range(trials):
        series = cohort.builder(index + seed_offset)
        found = detector.detect(series.bars, series.last_session)
        instances += len(found)
        best = _best(found)
        if best is None:
            scores.append(0.0)
            continue
        candidates += 1
        scores.append(best.quality)
        coverages.append(best.evidence_coverage)
        states.append(str(best.state))

    return CohortStatistics(
        detector=detector.name,
        cohort=cohort.name,
        kind=cohort.kind,
        intent=cohort.intent,
        tested=trials,
        scores=tuple(scores),
        coverages=tuple(coverages),
        states=tuple(states),
        candidates=candidates,
        instances=instances,
    )


def _best(instances: Sequence[PatternInstance]) -> PatternInstance | None:
    return max(instances, key=lambda p: p.quality, default=None)


# ---------------------------------------------------------------------------
# Cohort definitions
# ---------------------------------------------------------------------------
#
# One block per family. Each names its own competing structure and its own
# adversarial case, because "the thing most likely to fool this detector" is a
# different thing for each of them and a shared negative set would miss all
# twelve of them.
#
# The shared noise cohorts *are* shared: random walks are not family-specific,
# and running twelve private copies of the same walk would multiply the cost
# without adding a fact.


def shared_noise(generator: PatternGenerator) -> list[Cohort]:
    """Market-like noise with nothing drawn into it.

    The volatility variants are separate cohorts rather than one pooled family:
    a detector's behaviour on quiet noise and on violent noise are different
    numbers, and their average describes neither.
    """
    return [
        Cohort(
            "walk_low_vol",
            CohortKind.NOISE,
            lambda s: generator.random_walk(220, volatility=0.006, seed=s),
            "quiet random walk",
        ),
        Cohort(
            "walk_mid_vol",
            CohortKind.NOISE,
            lambda s: generator.random_walk(220, volatility=0.014, seed=s + 10_000),
            "ordinary random walk",
        ),
        Cohort(
            "walk_high_vol",
            CohortKind.NOISE,
            lambda s: generator.random_walk(220, volatility=0.030, seed=s + 20_000),
            "violent random walk",
        ),
        Cohort(
            "trending_walk",
            CohortKind.NOISE,
            lambda s: generator.trending_walk(seed=s),
            "drifting noise; the continuation families' hardest noise case",
        ),
        Cohort(
            "mean_reverting",
            CohortKind.NOISE,
            lambda s: generator.mean_reverting(seed=s),
            "oscillation with no structure",
        ),
        Cohort(
            "regime_switching",
            CohortKind.NOISE,
            lambda s: generator.regime_switching(seed=s),
            "volatility regimes without pattern",
        ),
    ]


def bull_flag_cohorts(generator: PatternGenerator) -> list[Cohort]:
    def flag(spec: BullFlagSpec) -> Builder:
        return lambda s: generator.bull_flag(spec, seed=s)

    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            flag(
                BullFlagSpec(
                    pole_gain=0.28,
                    pole_sessions=11,
                    retracement=0.30,
                    flag_sessions=9,
                    flag_slope=-0.002,
                    volume_contraction=0.45,
                    range_contraction=0.45,
                    noise=0.4,
                )
            ),
            "the pattern as its definition describes it",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            flag(
                BullFlagSpec(
                    pole_gain=0.18,
                    pole_sessions=14,
                    retracement=0.42,
                    flag_sessions=12,
                    flag_slope=-0.005,
                    volume_contraction=0.75,
                    range_contraction=0.75,
                    noise=1.1,
                )
            ),
            "real but unremarkable; most of what a scan finds",
        ),
        Cohort(
            "marginal",
            CohortKind.BORDERLINE,
            flag(
                BullFlagSpec(
                    pole_gain=0.11,
                    pole_sessions=20,
                    retracement=0.58,
                    flag_sessions=18,
                    flag_slope=-0.009,
                    volume_contraction=1.05,
                    range_contraction=1.0,
                    noise=2.0,
                )
            ),
            "at the edge of the definition",
        ),
        Cohort(
            "broken",
            CohortKind.INVALID,
            flag(
                BullFlagSpec(
                    pole_gain=0.09,
                    pole_sessions=28,
                    retracement=0.95,
                    flag_sessions=28,
                    flag_slope=-0.014,
                    volume_contraction=1.4,
                    range_contraction=1.3,
                    noise=3.0,
                )
            ),
            "a flag that gave back nearly the whole pole",
        ),
        Cohort(
            "pennant",
            CohortKind.COMPETING,
            lambda s: generator.pennant(seed=s),
            "same impulse, converging rather than parallel boundaries",
        ),
        Cohort(
            "tight_consolidation",
            CohortKind.COMPETING,
            lambda s: generator.tight_consolidation(seed=s),
            "same pause, no impulse to pause from",
        ),
        Cohort(
            "bear_flag",
            CohortKind.ADVERSARIAL,
            lambda s: generator.bear_flag(seed=s),
            "the mirror image; every volume and range relation is right",
        ),
        Cohort(
            "single_day_spike",
            CohortKind.ADVERSARIAL,
            lambda s: generator.single_day_spike(seed=s),
            "a pole with no duration to sustain",
        ),
        Cohort(
            "gap_and_fade",
            CohortKind.ADVERSARIAL,
            lambda s: generator.gap_and_fade(seed=s),
            "a repricing followed by a drift that looks like digestion",
        ),
        Cohort(
            "deep_pullback",
            CohortKind.ADVERSARIAL,
            lambda s: generator.deep_pullback(seed=s),
            "an advance being given back rather than digested",
        ),
        *shared_noise(generator),
    ]


def vcp_cohorts(generator: PatternGenerator) -> list[Cohort]:
    def vcp(spec: VcpSpec) -> Builder:
        return lambda s: generator.vcp(spec, seed=s)

    return [
        Cohort(
            "textbook_forming",
            CohortKind.POSITIVE,
            vcp(VcpSpec(depths=(0.18, 0.10, 0.05), leg_sessions=(14, 9, 6), noise=0.4)),
            "three legs drawn, two confirmable: the ordinary case while a base "
            "is still forming, and the one that shows the progression lag",
        ),
        Cohort(
            "textbook_confirmed",
            CohortKind.POSITIVE,
            vcp(
                VcpSpec(
                    depths=(0.18, 0.10, 0.05),
                    leg_sessions=(14, 9, 6),
                    noise=0.4,
                    breakout_sessions=6,
                )
            ),
            "the same base with the final leg confirmed; the comparison that "
            "isolates the causal progression lag",
        ),
        Cohort(
            "two_leg",
            CohortKind.MODERATE,
            vcp(
                VcpSpec(
                    depths=(0.16, 0.09),
                    leg_sessions=(15, 10),
                    volume_ratios=(0.9, 0.6),
                    noise=1.0,
                )
            ),
            "the minimum contraction sequence",
        ),
        Cohort(
            "shallow_progression",
            CohortKind.BORDERLINE,
            vcp(VcpSpec(depths=(0.13, 0.11, 0.10), leg_sessions=(14, 12, 10), noise=1.6)),
            "barely tightening; the edge of a progression",
        ),
        Cohort(
            "non_monotone",
            CohortKind.INVALID,
            vcp(
                VcpSpec(
                    depths=(0.18, 0.05, 0.12),
                    leg_sessions=(14, 9, 8),
                    noise=1.0,
                    breakout_sessions=6,
                )
            ),
            "a base that widens again before ending; separates measuring "
            "progression from measuring net change. Trailing bars are required "
            "or the widening leg sits in the provisional tail and the cohort "
            "asserts something the detector may not causally see",
        ),
        Cohort(
            "flat_base",
            CohortKind.COMPETING,
            lambda s: generator.flat_base(seed=s),
            "consistently shallow rather than progressively tightening",
        ),
        Cohort(
            "tight_consolidation",
            CohortKind.COMPETING,
            lambda s: generator.tight_consolidation(seed=s),
            "one tight window rather than a trajectory of them",
        ),
        Cohort(
            "broadening",
            CohortKind.ADVERSARIAL,
            lambda s: generator.broadening_formation(seed=s),
            "the exact inverse: legs that widen",
        ),
        Cohort(
            "failed_base",
            CohortKind.ADVERSARIAL,
            lambda s: generator.failed_base(seed=s),
            "a base that broke down",
        ),
        *shared_noise(generator),
    ]


def flat_base_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.flat_base(seed=s, depth=0.08, sessions=35),
            "a shallow horizontal range after an advance",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.flat_base(seed=s, depth=0.12, sessions=28, prior_gain=0.20),
            "deeper and shorter",
        ),
        Cohort(
            "sloping",
            CohortKind.BORDERLINE,
            lambda s: generator.flat_base(seed=s, depth=0.13, sessions=25, slope=0.003),
            "drifting rather than flat",
        ),
        Cohort(
            "too_deep",
            CohortKind.INVALID,
            lambda s: generator.flat_base(seed=s, depth=0.30, sessions=30),
            "a correction someone did not want to call one",
        ),
        Cohort(
            "no_prior_trend",
            CohortKind.COMPETING,
            lambda s: generator.base_without_prior_trend(seed=s),
            "identical geometry, missing context",
        ),
        Cohort(
            "vcp",
            CohortKind.COMPETING,
            lambda s: generator.vcp(seed=s),
            "a tightening base rather than a level one",
        ),
        Cohort(
            "rectangle",
            CohortKind.ADVERSARIAL,
            lambda s: generator.rectangle(seed=s),
            "a flat range with no advance into it",
        ),
        Cohort(
            "quiet_drift",
            CohortKind.ADVERSARIAL,
            lambda s: generator.quiet_drift(seed=s),
            "an instrument that is simply not moving",
        ),
        *shared_noise(generator),
    ]


def ascending_triangle_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.ascending_triangle(seed=s, touches=4, depth=0.12),
            "a flat ceiling with lows climbing into it",
        ),
        Cohort(
            "three_touch",
            CohortKind.MODERATE,
            lambda s: generator.ascending_triangle(seed=s, touches=3, depth=0.10),
            "the minimum number of tests",
        ),
        Cohort(
            "shallow_rise",
            CohortKind.BORDERLINE,
            lambda s: generator.ascending_triangle(seed=s, touches=3, depth=0.06),
            "lows barely rising",
        ),
        Cohort(
            "rectangle",
            CohortKind.INVALID,
            lambda s: generator.rectangle(seed=s),
            "same ceiling, flat lows; the definition's other half missing",
        ),
        Cohort(
            "pennant",
            CohortKind.COMPETING,
            lambda s: generator.pennant(seed=s),
            "converging boundaries, but the upper one falls",
        ),
        Cohort(
            "tight_consolidation",
            CohortKind.COMPETING,
            lambda s: generator.tight_consolidation(seed=s),
            "a quiet range under a level",
        ),
        Cohort(
            "broadening",
            CohortKind.ADVERSARIAL,
            lambda s: generator.broadening_formation(seed=s),
            "diverging rather than converging",
        ),
        Cohort(
            "parabolic",
            CohortKind.ADVERSARIAL,
            lambda s: generator.parabolic(seed=s),
            "an accelerating run whose top briefly shows a ceiling and rising lows",
        ),
        *shared_noise(generator),
    ]


def pennant_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.pennant(seed=s, convergence=0.30),
            "a sharp impulse and a symmetric converging wedge",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.pennant(seed=s, impulse_gain=0.15, convergence=0.50),
            "a smaller impulse and a looser wedge",
        ),
        Cohort(
            "weak_convergence",
            CohortKind.BORDERLINE,
            lambda s: generator.pennant(seed=s, convergence=0.75),
            "boundaries barely converging",
        ),
        Cohort(
            "descending_wedge",
            CohortKind.INVALID,
            lambda s: generator.pennant(seed=s, symmetric=False),
            "only the highs come down; asymmetric",
        ),
        Cohort(
            "parallel_channel",
            CohortKind.COMPETING,
            lambda s: generator.parallel_flag_channel(seed=s),
            "same impulse, boundaries that do not converge: a flag",
        ),
        Cohort(
            "ascending_triangle",
            CohortKind.COMPETING,
            lambda s: generator.ascending_triangle(seed=s),
            "converging, but around a flat ceiling",
        ),
        Cohort(
            "falling_knife",
            CohortKind.ADVERSARIAL,
            lambda s: generator.falling_knife(seed=s),
            "a decline whose deceleration resembles convergence",
        ),
        Cohort(
            "single_day_spike",
            CohortKind.ADVERSARIAL,
            lambda s: generator.single_day_spike(seed=s),
            "an impulse with no duration",
        ),
        *shared_noise(generator),
    ]


def cup_handle_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.cup_handle(seed=s, roundness=0.5, depth=0.22),
            "a rounded decline and recovery with a shallow handle",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.cup_handle(seed=s, roundness=1.0, depth=0.28, handle_sessions=14),
            "a less rounded bottom and a longer handle",
        ),
        Cohort(
            "shallow_or_narrow",
            CohortKind.BORDERLINE,
            lambda s: generator.cup_handle(seed=s, roundness=2.0, depth=0.13, cup_sessions=32),
            "narrow bottom, shallow cup, short duration",
        ),
        Cohort(
            "deep_handle",
            CohortKind.INVALID,
            lambda s: generator.cup_handle(seed=s, handle_depth_ratio=0.70),
            "a handle deeper than a third of the cup: a second decline",
        ),
        Cohort(
            "v_bottom",
            CohortKind.COMPETING,
            lambda s: generator.v_bottom(seed=s),
            "same rims, depth, duration and handle; no time spent at the low",
        ),
        Cohort(
            "double_bottom",
            CohortKind.COMPETING,
            lambda s: generator.double_bottom(seed=s),
            "two lows rather than one rounded one",
        ),
        Cohort(
            "incomplete_recovery",
            CohortKind.ADVERSARIAL,
            lambda s: generator.incomplete_recovery(seed=s),
            "a right rim well below the left",
        ),
        Cohort(
            "cup_without_handle",
            CohortKind.ADVERSARIAL,
            lambda s: generator.cup_without_handle(seed=s),
            "a rounded base that ran straight back to the rim",
        ),
        *shared_noise(generator),
    ]


def high_tight_flag_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.high_tight_flag(seed=s, advance=1.10, pause_depth=0.12),
            "a near-vertical advance and a shallow pause",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.high_tight_flag(seed=s, advance=0.85, pause_depth=0.17),
            "above the floor with a deeper pause",
        ),
        Cohort(
            "at_the_floor",
            CohortKind.BORDERLINE,
            lambda s: generator.high_tight_flag(
                seed=s, advance=0.72, pause_depth=0.22, advance_sessions=45
            ),
            "just inside every threshold at once",
        ),
        Cohort(
            "deep_pause",
            CohortKind.INVALID,
            lambda s: generator.deep_pause_after_thrust(seed=s),
            "a genuine thrust followed by a 40% correction",
        ),
        Cohort(
            "ordinary_bull_flag",
            CohortKind.COMPETING,
            lambda s: generator.ordinary_bull_flag_for_htf(seed=s),
            "the primary negative: a 25% advance with a 10% pause",
        ),
        Cohort(
            "slow_double",
            CohortKind.COMPETING,
            lambda s: generator.slow_double(seed=s),
            "the magnitude without the thrust: a trend",
        ),
        Cohort(
            "parabolic",
            CohortKind.ADVERSARIAL,
            lambda s: generator.parabolic(seed=s),
            "an extreme advance with no pause",
        ),
        Cohort(
            "gap_and_fade",
            CohortKind.ADVERSARIAL,
            lambda s: generator.gap_and_fade(seed=s),
            "a repricing rather than accumulation",
        ),
        *shared_noise(generator),
    ]


def double_bottom_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.double_bottom(seed=s, rally=0.16, undercut=0.02),
            "two lows at a level after a decline, split by a real rally",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.double_bottom(seed=s, rally=0.11, undercut=0.0, prior_decline=0.20),
            "a shallower rally and an exact double",
        ),
        Cohort(
            "shallow_rally",
            CohortKind.BORDERLINE,
            lambda s: generator.double_bottom(seed=s, rally=0.075, separation=14),
            "close to being one low with noise in it",
        ),
        Cohort(
            "descending",
            CohortKind.INVALID,
            lambda s: generator.descending_double_low(seed=s),
            "a second low well below the first",
        ),
        Cohort(
            "inverse_head_shoulders",
            CohortKind.COMPETING,
            lambda s: generator.inverse_head_shoulders(seed=s),
            "two shoulders at a level with a head beneath them",
        ),
        Cohort(
            "triple_bottom",
            CohortKind.COMPETING,
            lambda s: generator.triple_bottom(seed=s),
            "three lows at a level; genuinely contains double bottoms",
        ),
        Cohort(
            "range_double_low",
            CohortKind.ADVERSARIAL,
            lambda s: generator.range_double_low(seed=s),
            "identical geometry with nothing to reverse",
        ),
        Cohort(
            "head_and_shoulders_top",
            CohortKind.ADVERSARIAL,
            lambda s: generator.head_and_shoulders_top(seed=s),
            "the bearish mirror; two troughs at a level with a rally between",
        ),
        Cohort(
            "choppy_range",
            CohortKind.ADVERSARIAL,
            lambda s: generator.choppy_range(seed=s),
            "a range that manufactures same-level lows by the dozen",
        ),
        *shared_noise(generator),
    ]


def inverse_head_shoulders_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.inverse_head_shoulders(seed=s, prominence=0.12),
            "three lows, the middle materially deepest, on a level neckline",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.inverse_head_shoulders(
                seed=s, prominence=0.09, shoulder_asymmetry=0.18
            ),
            "asymmetric, as real ones are",
        ),
        Cohort(
            "lopsided",
            CohortKind.BORDERLINE,
            lambda s: generator.inverse_head_shoulders(
                seed=s, prominence=0.06, shoulder_asymmetry=0.32, timing_skew=1.8
            ),
            "at the edge of both asymmetry tolerances",
        ),
        Cohort(
            "no_head",
            CohortKind.INVALID,
            lambda s: generator.triple_bottom(seed=s),
            "three lows at one level: a range, not a head between shoulders",
        ),
        Cohort(
            "double_bottom",
            CohortKind.COMPETING,
            lambda s: generator.double_bottom(seed=s),
            "two lows rather than three",
        ),
        Cohort(
            "falling_neckline",
            CohortKind.COMPETING,
            lambda s: generator.inverse_head_shoulders(seed=s, neckline_slope=-0.05),
            "each rally reaching less far than the last",
        ),
        Cohort(
            "head_and_shoulders_top",
            CohortKind.ADVERSARIAL,
            lambda s: generator.head_and_shoulders_top(seed=s),
            "the bearish mirror",
        ),
        Cohort(
            "choppy_range",
            CohortKind.ADVERSARIAL,
            lambda s: generator.choppy_range(seed=s),
            "three lows of unequal depth arise readily in a range",
        ),
        *shared_noise(generator),
    ]


def base_on_base_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.base_on_base(seed=s, ceiling_advance=0.05, second_depth=0.08),
            "two bases at a level, the second tighter and no lower",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.base_on_base(seed=s, ceiling_advance=0.07, second_depth=0.11),
            "a little more progress and no tightening",
        ),
        Cohort(
            "at_level_identity_boundary",
            CohortKind.BORDERLINE,
            lambda s: generator.base_on_base(seed=s, ceiling_advance=0.03, second_depth=0.08),
            "a second ceiling close enough to the first that noise sometimes "
            "merges them into one base; the boundary is the touch tolerance and "
            "the intermittent candidate rate is the point",
        ),
        Cohort(
            "loose",
            CohortKind.BORDERLINE,
            lambda s: generator.base_on_base(
                seed=s, ceiling_advance=0.08, second_depth=0.16, first_depth=0.17
            ),
            "at the edge of both the advance and depth tolerances",
        ),
        Cohort(
            "descending",
            CohortKind.INVALID,
            lambda s: generator.descending_bases(seed=s),
            "a second base built below the first",
        ),
        Cohort(
            "stair_step",
            CohortKind.COMPETING,
            lambda s: generator.stair_step_bases(seed=s),
            "two bases separated by a real advance",
        ),
        Cohort(
            "single_long_base",
            CohortKind.COMPETING,
            lambda s: generator.single_long_base(seed=s),
            "one continuous range of the same total length",
        ),
        Cohort(
            "choppy_range",
            CohortKind.ADVERSARIAL,
            lambda s: generator.choppy_range(seed=s),
            "a range that can be divided into two ranges anywhere",
        ),
        Cohort(
            "quiet_drift",
            CohortKind.ADVERSARIAL,
            lambda s: generator.quiet_drift(seed=s),
            "an instrument doing nothing for a long time",
        ),
        *shared_noise(generator),
    ]


def tight_consolidation_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.tight_consolidation(seed=s, depth=0.025, reference_depth=0.14),
            "a genuinely tight window against a much wider prior range",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.tight_consolidation(seed=s, depth=0.04, reference_depth=0.11),
            "moderate contraction",
        ),
        Cohort(
            "weak_contraction",
            CohortKind.BORDERLINE,
            lambda s: generator.tight_consolidation(seed=s, depth=0.06, reference_depth=0.09),
            "barely tighter than what preceded it",
        ),
        Cohort(
            "wide_window",
            CohortKind.INVALID,
            lambda s: generator.tight_consolidation(seed=s, depth=0.11, reference_depth=0.20),
            "contraction from chaos; halved but still wide",
        ),
        Cohort(
            "flat_base",
            CohortKind.COMPETING,
            lambda s: generator.flat_base(seed=s),
            "a level held for weeks rather than a quiet fortnight",
        ),
        Cohort(
            "bull_flag",
            CohortKind.COMPETING,
            lambda s: generator.bull_flag(seed=s),
            "a pause after an impulse",
        ),
        Cohort(
            "chronically_quiet",
            CohortKind.ADVERSARIAL,
            lambda s: generator.chronically_quiet(seed=s),
            "the defining negative: an instrument that is always this quiet",
        ),
        Cohort(
            "tight_after_decline",
            CohortKind.ADVERSARIAL,
            lambda s: generator.tight_after_decline(seed=s),
            "the same tight window with a decline behind it",
        ),
        Cohort(
            "low_volume_noise",
            CohortKind.ADVERSARIAL,
            lambda s: generator.low_volume_noise(seed=s),
            "quiet by absence of interest rather than by absorption",
        ),
        *shared_noise(generator),
    ]


def breakout_retest_cohorts(generator: PatternGenerator) -> list[Cohort]:
    return [
        Cohort(
            "textbook",
            CohortKind.POSITIVE,
            lambda s: generator.breakout_retest(seed=s, break_strength=0.07),
            "a well-tested level, a decisive break, a pullback to the line",
        ),
        Cohort(
            "ordinary",
            CohortKind.MODERATE,
            lambda s: generator.breakout_retest(
                seed=s, break_strength=0.05, retest_overshoot=0.015
            ),
            "a smaller break and a pullback slightly through the line",
        ),
        Cohort(
            "marginal",
            CohortKind.BORDERLINE,
            lambda s: generator.breakout_retest(
                seed=s, break_strength=0.03, retest_overshoot=0.025, base_sessions=40
            ),
            "a shallow break off a shorter base",
        ),
        Cohort(
            "failed",
            CohortKind.INVALID,
            lambda s: generator.failed_retest(seed=s),
            "a genuine retest that then broke down",
        ),
        Cohort(
            "no_retest",
            CohortKind.COMPETING,
            lambda s: generator.breakout_no_retest(seed=s),
            "a break that ran away and never came back",
        ),
        Cohort(
            "flat_base_breakout",
            CohortKind.COMPETING,
            lambda s: generator.flat_base(seed=s, breakout_sessions=8),
            "an ordinary base breakout with no pullback",
        ),
        Cohort(
            "choppy_range",
            CohortKind.ADVERSARIAL,
            lambda s: generator.choppy_range(seed=s),
            "levels crossed and revisited constantly",
        ),
        Cohort(
            "broad_volatile_range",
            CohortKind.ADVERSARIAL,
            lambda s: generator.broad_volatile_range(seed=s),
            "wide swings through any level drawn in them",
        ),
        *shared_noise(generator),
    ]


#: Every family's cohorts, keyed by detector name.
COHORT_BUILDERS: Mapping[str, Callable[[PatternGenerator], list[Cohort]]] = {
    "bull_flag": bull_flag_cohorts,
    "vcp": vcp_cohorts,
    "flat_base": flat_base_cohorts,
    "ascending_triangle": ascending_triangle_cohorts,
    "pennant": pennant_cohorts,
    "cup_handle": cup_handle_cohorts,
    "high_tight_flag": high_tight_flag_cohorts,
    "double_bottom": double_bottom_cohorts,
    "inverse_head_shoulders": inverse_head_shoulders_cohorts,
    "base_on_base": base_on_base_cohorts,
    "tight_consolidation": tight_consolidation_cohorts,
    "breakout_retest": breakout_retest_cohorts,
}


def cohorts_for(name: str, generator: PatternGenerator | None = None) -> list[Cohort]:
    builder = COHORT_BUILDERS.get(name)
    if builder is None:
        raise KeyError(f"no cohorts defined for detector {name!r}")
    return builder(generator or PatternGenerator())


__all__ = [
    "COHORT_BUILDERS",
    "QUALITY_THRESHOLDS",
    "Cohort",
    "CohortKind",
    "CohortStatistics",
    "FamilyReport",
    "characterise",
    "cohorts_for",
    "shared_noise",
]
