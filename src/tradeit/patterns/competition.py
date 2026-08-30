"""Which families fire on the same structure, and what separates them.

**Exclusivity is not the goal.** A window that is a tight consolidation at 78
and a bull flag at 74 is one piece of chart with two names, and forcing a single
label early discards information a later scoring stage is better placed to use.
The brief is explicit about this and the taxonomy has a ``RELATED_TO`` edge for
exactly the case.

So this module does not measure "confusion" in the classifier sense. It measures
**coexistence**: for a given structure, which detectors qualified, what each of
them scored, how much of their geometry overlapped, and — the part that matters
most — *which component readings differ*. Two families agreeing on a window is
uninteresting; two families agreeing on a window while disagreeing about
roundness, or about whether a prior trend exists, is the whole story.

**What a high competitor score does and does not mean.** It does not mean a
detector is wrong. The cup and the V-bottom share every dimension except
roundness, so a V-bottom scoring well as a cup is arithmetically correct and the
component breakdown is where the difference lives. What *would* be a defect is a
competitor scoring highly with **no** component separating it, because then the
two definitions are the same definition under two names.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.base import PatternInstance
from tradeit.patterns.registry import DetectorRegistry
from tradeit.patterns.relationships import span_overlap
from tradeit.patterns.synthetic import GeneratedSeries, PatternGenerator


@dataclass(frozen=True, slots=True)
class FamilyReading:
    """What one detector made of one structure."""

    detector: str
    quality: float
    coverage: float
    state: str
    #: The reason the detector gave for qualifying, from its own components.
    strongest: tuple[str, float]
    weakest: tuple[str, float]
    start_date: str
    end_date: str

    def to_payload(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "quality": round(self.quality, 1),
            "coverage": round(self.coverage, 1),
            "state": self.state,
            "strongest": [self.strongest[0], round(self.strongest[1], 1)],
            "weakest": [self.weakest[0], round(self.weakest[1], 1)],
            "span": [self.start_date, self.end_date],
        }


@dataclass(slots=True)
class CompetitionCase:
    """One test structure, every family that qualified, and what separated them."""

    case: str
    intent: str
    #: The family the structure was drawn as. Not a ground truth label for
    #: anything except the generator's intent.
    drawn_as: str
    readings: list[FamilyReading] = field(default_factory=list)
    #: Pairwise geometry overlap between the qualifying families.
    overlaps: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def families(self) -> list[str]:
        return [r.detector for r in self.readings]

    @property
    def leader(self) -> FamilyReading | None:
        return max(self.readings, key=lambda r: r.quality, default=None)

    def margin(self) -> float:
        """How far the top reading sits above the next one.

        A small margin is not a failure -- it is the honest report of a genuinely
        ambiguous structure -- but it is what a later scoring stage most needs to
        know, because it says the family label is the least reliable part of the
        reading.
        """
        ordered = sorted((r.quality for r in self.readings), reverse=True)
        return ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0] if ordered else 0.0

    def to_payload(self) -> dict[str, object]:
        return {
            "case": self.case,
            "intent": self.intent,
            "drawn_as": self.drawn_as,
            "families": self.families,
            "margin": round(self.margin(), 1),
            "readings": [r.to_payload() for r in self.readings],
            "overlaps": {f"{a}|{b}": round(v, 2) for (a, b), v in self.overlaps.items()},
        }

    def format_block(self) -> str:
        lines = [f"{self.case}  (drawn as {self.drawn_as}) -- {self.intent}"]
        for reading in sorted(self.readings, key=lambda r: -r.quality):
            lines.append(
                f"    {reading.detector:<24} q={reading.quality:>5.1f} "
                f"cov={reading.coverage:>5.1f} {reading.state:<24} "
                f"best={reading.strongest[0]}({reading.strongest[1]:.0f}) "
                f"worst={reading.weakest[0]}({reading.weakest[1]:.0f})"
            )
        if not self.readings:
            lines.append("    (no family qualified)")
        return "\n".join(lines)


def _reading(instance: PatternInstance) -> FamilyReading:
    available = [c for c in instance.components if not c.unavailable]
    strongest = max(available, key=lambda c: c.score) if available else None
    weakest = min(available, key=lambda c: c.score) if available else None
    return FamilyReading(
        detector=instance.detector_name,
        quality=instance.quality,
        coverage=instance.evidence_coverage,
        state=str(instance.state),
        strongest=(strongest.name, strongest.score) if strongest else ("none", 0.0),
        weakest=(weakest.name, weakest.score) if weakest else ("none", 0.0),
        start_date=instance.geometry.start_date.isoformat(),
        end_date=instance.geometry.end_date.isoformat(),
    )


def run_case(
    registry: DetectorRegistry,
    case: str,
    intent: str,
    drawn_as: str,
    series: GeneratedSeries,
) -> CompetitionCase:
    """Run every enabled daily detector over one structure."""
    result = CompetitionCase(case=case, intent=intent, drawn_as=drawn_as)
    best: dict[str, PatternInstance] = {}

    for detector in registry.enabled_for(Bartimeframe.D1):
        found = detector.detect(series.bars, series.last_session)
        if not found:
            continue
        top = max(found, key=lambda p: p.quality)
        best[detector.name] = top
        result.readings.append(_reading(top))

    names = sorted(best)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            a_share, b_share = span_overlap(best[first], best[second])
            result.overlaps[(first, second)] = min(a_share, b_share)
    return result


#: The comparisons the completion gate names, plus the ones the construction
#: work turned up. Each is a (name, intent, drawn_as, builder) row.
def competition_cases(
    generator: PatternGenerator,
) -> Sequence[tuple[str, str, str, Callable[[], GeneratedSeries]]]:
    return (
        (
            "bull_flag_vs_pennant",
            "same impulse; converging boundaries rather than parallel ones",
            "pennant",
            lambda: generator.pennant(),
        ),
        (
            "bull_flag_vs_tight_consolidation",
            "a pause after an impulse against a pause with no impulse",
            "tight_consolidation",
            lambda: generator.tight_consolidation(),
        ),
        (
            "vcp_vs_tight_consolidation",
            "a trajectory of narrowing legs against one quiet window",
            "vcp",
            lambda: generator.vcp(),
        ),
        (
            "vcp_vs_flat_base",
            "progressive tightening against a level held flat",
            "flat_base",
            lambda: generator.flat_base(),
        ),
        (
            "flat_base_vs_tight_consolidation",
            "weeks at a level against a fortnight of quiet",
            "flat_base",
            lambda: generator.flat_base(sessions=30),
        ),
        (
            "ascending_triangle_vs_pennant",
            "converging around a flat ceiling against converging on a midpoint",
            "ascending_triangle",
            lambda: generator.ascending_triangle(),
        ),
        (
            "ascending_triangle_vs_tight_consolidation",
            "rising lows under a ceiling against a quiet range",
            "ascending_triangle",
            lambda: generator.ascending_triangle(depth=0.07),
        ),
        (
            "pennant_vs_bull_flag",
            "the same comparison drawn the other way round",
            "bull_flag",
            lambda: generator.bull_flag(),
        ),
        (
            "cup_vs_double_bottom",
            "one rounded low against two lows at a level",
            "double_bottom",
            lambda: generator.double_bottom(),
        ),
        (
            "cup_vs_v_bottom",
            "the cup's defining negative: same rims, no time at the low",
            "v_bottom",
            lambda: generator.v_bottom(),
        ),
        (
            "double_bottom_vs_inverse_head_shoulders",
            "two shoulders at a level with a head beneath them",
            "inverse_head_shoulders",
            lambda: generator.inverse_head_shoulders(),
        ),
        (
            "high_tight_flag_vs_ordinary_bull_flag",
            "the definition that must not be diluted: a 25% advance",
            "bull_flag",
            lambda: generator.ordinary_bull_flag_for_htf(),
        ),
        (
            "base_on_base_vs_two_independent_bases",
            "two bases at a level against two separated by a real advance",
            "stair_step",
            lambda: generator.stair_step_bases(),
        ),
        (
            "base_on_base_vs_single_long_base",
            "one continuous range of the same total length",
            "flat_base",
            lambda: generator.single_long_base(),
        ),
        (
            "breakout_retest_vs_ordinary_support_test",
            "a level revisited after being crossed against one merely held",
            "flat_base",
            lambda: generator.flat_base(sessions=45),
        ),
        (
            "triple_bottom_contains_double_bottoms",
            "three lows at a level genuinely contain pairs of lows at that level",
            "triple_bottom",
            lambda: generator.triple_bottom(),
        ),
    )


def run_matrix(
    registry: DetectorRegistry | None = None, generator: PatternGenerator | None = None
) -> list[CompetitionCase]:
    """Every competing-pattern case, run against every enabled detector."""
    reg = registry or DetectorRegistry.from_config()
    gen = generator or PatternGenerator()
    return [
        run_case(reg, name, intent, drawn_as, builder())
        for name, intent, drawn_as, builder in competition_cases(gen)
    ]


def coexistence_counts(cases: Sequence[CompetitionCase]) -> Mapping[tuple[str, str], int]:
    """How often each pair of families qualified on the same structure."""
    counts: dict[tuple[str, str], int] = {}
    for case in cases:
        names = sorted(case.families)
        for index, first in enumerate(names):
            for second in names[index + 1 :]:
                counts[(first, second)] = counts.get((first, second), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


__all__ = [
    "CompetitionCase",
    "FamilyReading",
    "coexistence_counts",
    "competition_cases",
    "run_case",
    "run_matrix",
]
