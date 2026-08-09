"""Breakout quality, confirmation, coverage and confidence.

Four numbers, and the architecture of this module is mostly about keeping them
from collapsing into one.

**BREAKOUT_QUALITY_SCORE** answers *how favourable were the observed breakout
characteristics?* It is computed once, from the breakout bar and information
that predates it, and then frozen. This is the single most important design
decision in Phase 5 and it does two jobs at once:

* it makes "future retests cannot retroactively change the original breakout
  score" true **by construction**, not by test — though the test exists;
* it preserves the item-18 distinction. Quality 94 / confirmation 42 on Monday
  and quality 94 / confirmation 88 on Wednesday describe the same breakout with
  more evidence behind it. If quality drifted, the pair would be two views of
  one blended number and the reader could not tell which had moved.

**CONFIRMATION_SCORE** answers *how much subsequent evidence corroborates it?*
It is recomputed each session from post-breakout evidence only. Nothing about
the breakout bar enters it; folding the bar's own quality back in would
correlate the two scores by construction and destroy exactly what they exist to
separate.

**EVIDENCE_COVERAGE** answers *how much of the intended evidence was available?*
Same rule as Phase 4 and ADR-0017: separate, never multiplied in. A missing
sector series lowers coverage; it does not lower quality, because "we could not
measure the sector" is not a bearish observation about the sector.

**CONFIDENCE** answers *how certain are we that this is the structural event we
think it is?* Not how good it looks — how sure we are we are looking at the
right thing. A crisp five-touch boundary on a 90-quality base under a clear
close is a confident identification; a single-extreme level on a marginal
pattern with the close sitting inside the tolerance zone is not, whatever the
quality score says.

**None of the weights below was fitted.** They are stated, versioned, and
configurable, and Phase 9 is the phase permitted to argue with them from
out-of-sample evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from tradeit.breakouts.config import BreakoutQualityWeights, ConfirmationWeights
from tradeit.patterns.base import (
    ComponentRequirement,
    ComponentScore,
    Evidence,
    EvidenceKind,
)
from tradeit.patterns.scoring import combine

#: Bumped whenever a component definition, curve or weight default changes here.
#: A stored event records it, so a score always says which scorer produced it —
#: and a backtest that silently recomputed history under a newer scorer would be
#: caught by the mismatch rather than presented as unchanged.
BREAKOUT_SCORER_VERSION = 1

#: Components without which there is no breakout to score. Both are geometric:
#: without a boundary there is nothing to break, and without a penetration
#: reading nothing has been broken. Everything else is corroboration.
REQUIRED_QUALITY_COMPONENTS: frozenset[str] = frozenset({"boundary_quality", "penetration"})


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """A composite plus the components that produced it."""

    score: float
    components: tuple[ComponentScore, ...]
    coverage: float

    @property
    def reconciles(self) -> bool:
        """Whether the components actually produce the composite.

        Guards the specific failure of a late adjustment applied to the score
        without being recorded as a component — an explanation that does not add
        up to the number it explains.
        """
        total = sum(c.effective_weight for c in self.components)
        if total <= 0:
            return self.score == 0.0
        expected = sum(c.contribution for c in self.components) / total
        return abs(expected - self.score) < 1e-6

    @property
    def is_classifiable(self) -> bool:
        return not any(c.blocks_classification for c in self.components)

    def component(self, name: str) -> ComponentScore | None:
        for item in self.components:
            if item.name == name:
                return item
        return None


def _component(
    name: str,
    score: float | None,
    weight: float,
    *,
    measurements: dict[str, float] | None = None,
    supporting: Sequence[Evidence] = (),
    contradicting: Sequence[Evidence] = (),
    unavailable_reason: str = "",
    required: bool = False,
) -> ComponentScore:
    requirement = ComponentRequirement.REQUIRED if required else ComponentRequirement.OPTIONAL
    if score is None:
        return ComponentScore(
            name=name,
            score=0.0,
            weight=weight,
            measurements=measurements or {},
            unavailable=True,
            unavailable_reason=unavailable_reason or "not measurable from the supplied inputs",
            requirement=requirement,
        )
    return ComponentScore(
        name=name,
        score=float(np.clip(score, 0.0, 100.0)),
        weight=weight,
        measurements=measurements or {},
        evidence=tuple(supporting),
        contradicting=tuple(contradicting),
        requirement=requirement,
    )


def _assemble(components: Sequence[ComponentScore]) -> ScoreResult:
    scores = {c.name: (None if c.unavailable else c.score) for c in components}
    weights = {c.name: c.weight for c in components}
    weighted = combine(scores, weights)
    total = sum(weights.values())
    coverage = 0.0 if total <= 0 else weighted.available_weight / total * 100.0
    return ScoreResult(
        score=round(weighted.value, 10),
        components=tuple(components),
        coverage=round(coverage, 6),
    )


# ---------------------------------------------------------------------------
# Breakout quality
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityInputs:
    """Everything the frozen breakout quality score is computed from.

    Deliberately a closed set, and deliberately containing nothing dated after
    the breakout bar. Adding a post-breakout field here would be the way the
    freezing guarantee gets broken, so the type is the place to look when
    reviewing whether it still holds.
    """

    boundary_quality: float
    boundary_measurements: dict[str, float]
    penetration_score: float
    extension_score: float
    penetration_measurements: dict[str, float]
    candle_score: float
    candle_measurements: dict[str, float]
    volume_score: float | None
    volume_measurements: dict[str, float]
    volume_unavailable_reason: str = ""
    rs_score: float | None = None
    rs_measurements: dict[str, float] | None = None
    rs_unavailable_reason: str = ""
    market_score: float | None = None
    market_measurements: dict[str, float] | None = None
    market_unavailable_reason: str = ""
    sector_score: float | None = None
    sector_measurements: dict[str, float] | None = None
    sector_unavailable_reason: str = ""
    #: True when the volume figure came from an intraday projection rather than
    #: a completed bar. Recorded as contradicting evidence, not as a penalty:
    #: the projection may well be right, and the reader should know it is one.
    volume_from_projection: bool = False


def score_breakout_quality(
    inputs: QualityInputs,
    *,
    weights: BreakoutQualityWeights,
) -> ScoreResult:
    """The frozen breakout quality composite.

    Extension is folded into the penetration component rather than carried as
    its own weighted term, and the reasoning matters: extension is not an
    independent dimension of quality, it is the other end of the same
    measurement. A close 3 ATR through the level scores high on penetration and
    low on extension *for the same underlying fact*, and giving them separate
    weights would count that fact twice. Both raw scores survive into the
    component's measurements, so a consumer that wants to weigh them differently
    has the numbers.
    """
    components: list[ComponentScore] = []

    components.append(
        _component(
            "boundary_quality",
            inputs.boundary_quality,
            weights.boundary_quality,
            measurements=inputs.boundary_measurements,
            supporting=(
                [
                    Evidence(
                        "breakout is against a well-established boundary",
                        EvidenceKind.STRUCTURAL,
                        measured=inputs.boundary_quality,
                    )
                ]
                if inputs.boundary_quality >= 70
                else []
            ),
            contradicting=(
                [
                    Evidence(
                        "boundary is weakly established",
                        EvidenceKind.STRUCTURAL,
                        measured=inputs.boundary_quality,
                        expected=">= 50",
                    )
                ]
                if inputs.boundary_quality < 50
                else []
            ),
            required=True,
        )
    )

    penetration_blend = inputs.penetration_score * 0.65 + inputs.extension_score * 0.35
    penetration_measurements = dict(inputs.penetration_measurements)
    penetration_measurements["penetration_blend"] = penetration_blend
    extended = inputs.extension_score < 50
    components.append(
        _component(
            "penetration",
            penetration_blend,
            weights.penetration,
            measurements=penetration_measurements,
            supporting=(
                [
                    Evidence(
                        "close cleared the tolerance zone decisively",
                        EvidenceKind.STRUCTURAL,
                        measured=inputs.penetration_score,
                    )
                ]
                if inputs.penetration_score >= 70
                else []
            ),
            contradicting=(
                [
                    Evidence(
                        "price is extended well above the boundary",
                        EvidenceKind.STRUCTURAL,
                        measured=inputs.penetration_measurements.get("extension_atr", 0.0),
                        expected="closer to the level",
                    )
                ]
                if extended
                else []
            ),
            required=True,
        )
    )

    candle_supporting = []
    candle_contradicting = []
    location = inputs.candle_measurements.get("close_location")
    if location is not None:
        if location >= 0.8:
            candle_supporting.append(
                Evidence(
                    "closed near the session high",
                    EvidenceKind.STRUCTURAL,
                    measured=location * 100.0,
                )
            )
        elif location <= 0.4:
            candle_contradicting.append(
                Evidence(
                    "closed in the lower part of the session range",
                    EvidenceKind.STRUCTURAL,
                    measured=location * 100.0,
                    expected=">= 40th percentile of the bar",
                )
            )
    components.append(
        _component(
            "candle_quality",
            inputs.candle_score,
            weights.candle_quality,
            measurements=inputs.candle_measurements,
            supporting=candle_supporting,
            contradicting=candle_contradicting,
        )
    )

    volume_supporting = []
    volume_contradicting = []
    relative = inputs.volume_measurements.get("relative_volume")
    if relative is not None:
        if relative >= 1.5:
            volume_supporting.append(
                Evidence(
                    f"{relative:.2f}x relative volume",
                    EvidenceKind.STRUCTURAL,
                    measured=relative,
                )
            )
        elif relative < 1.0:
            volume_contradicting.append(
                Evidence(
                    f"breakout on {relative:.2f}x relative volume",
                    EvidenceKind.STRUCTURAL,
                    measured=relative,
                    expected=">= 1.0x",
                )
            )
    if inputs.volume_from_projection:
        volume_contradicting.append(
            Evidence(
                "relative volume is projected from a partial session, not measured",
                EvidenceKind.CONTEXTUAL,
            )
        )
    components.append(
        _component(
            "volume_confirmation",
            inputs.volume_score,
            weights.volume_confirmation,
            measurements=inputs.volume_measurements,
            supporting=volume_supporting,
            contradicting=volume_contradicting,
            unavailable_reason=inputs.volume_unavailable_reason,
        )
    )

    components.append(
        _component(
            "relative_strength",
            inputs.rs_score,
            weights.relative_strength,
            measurements=inputs.rs_measurements or {},
            supporting=(
                [
                    Evidence(
                        "relative strength reached a new high",
                        EvidenceKind.ANALYTIC,
                        measured=inputs.rs_score,
                    )
                ]
                if inputs.rs_score is not None
                and (inputs.rs_measurements or {}).get("rs_new_high_before_price")
                else []
            ),
            contradicting=(
                [
                    Evidence(
                        "relative strength is deteriorating despite the price breakout",
                        EvidenceKind.ANALYTIC,
                    )
                ]
                if (inputs.rs_measurements or {}).get("rs_deteriorating")
                else []
            ),
            unavailable_reason=inputs.rs_unavailable_reason,
        )
    )
    components.append(
        _component(
            "market_context",
            inputs.market_score,
            weights.market_context,
            measurements=inputs.market_measurements or {},
            unavailable_reason=inputs.market_unavailable_reason,
        )
    )
    components.append(
        _component(
            "sector_context",
            inputs.sector_score,
            weights.sector_context,
            measurements=inputs.sector_measurements or {},
            unavailable_reason=inputs.sector_unavailable_reason,
        )
    )

    return _assemble(components)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmationInputs:
    """Post-breakout evidence only.

    Nothing about the breakout bar appears here, and that absence is load-
    bearing rather than an oversight — see the module docstring.
    """

    acceptance_score: float
    acceptance_measurements: dict[str, float]
    follow_through_score: float | None
    follow_through_measurements: dict[str, float]
    follow_through_unavailable_reason: str = ""
    volume_persistence: float | None = None
    volume_measurements: dict[str, float] | None = None
    volume_unavailable_reason: str = ""
    retest_score: float | None = None
    retest_measurements: dict[str, float] | None = None
    retest_unavailable_reason: str = ""
    rs_after_score: float | None = None
    rs_after_measurements: dict[str, float] | None = None
    rs_after_unavailable_reason: str = ""


def score_confirmation(
    inputs: ConfirmationInputs,
    *,
    weights: ConfirmationWeights,
) -> ScoreResult:
    """The evolving confirmation composite.

    Every component here is optional. A breakout on its first day has no
    follow-through evidence, has not retested, and may have no benchmark — so it
    scores on acceptance alone at low coverage, which is the honest description
    of its situation. Filling the gaps with neutral 50s would manufacture a
    middling confirmation score for an event about which almost nothing is yet
    known.
    """
    components = [
        _component(
            "close_acceptance",
            inputs.acceptance_score,
            weights.close_acceptance,
            measurements=inputs.acceptance_measurements,
        ),
        _component(
            "follow_through",
            inputs.follow_through_score,
            weights.follow_through,
            measurements=inputs.follow_through_measurements,
            unavailable_reason=inputs.follow_through_unavailable_reason
            or "no sessions have elapsed since the breakout bar",
            contradicting=(
                [Evidence("no follow-through bar yet", EvidenceKind.STRUCTURAL)]
                if inputs.follow_through_score is None
                else []
            ),
        ),
        _component(
            "volume_persistence",
            inputs.volume_persistence,
            weights.volume_persistence,
            measurements=inputs.volume_measurements or {},
            unavailable_reason=inputs.volume_unavailable_reason
            or "no post-breakout volume history yet",
        ),
        _component(
            "retest_quality",
            inputs.retest_score,
            weights.retest_quality,
            measurements=inputs.retest_measurements or {},
            unavailable_reason=inputs.retest_unavailable_reason
            or "no retest has occurred; this is not a defect in the breakout",
        ),
        _component(
            "relative_strength_after",
            inputs.rs_after_score,
            weights.relative_strength_after,
            measurements=inputs.rs_after_measurements or {},
            unavailable_reason=inputs.rs_after_unavailable_reason or "no benchmark series supplied",
        ),
    ]
    return _assemble(components)


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def breakout_confidence(
    *,
    boundary_confidence: float,
    boundary_touches: int,
    pattern_quality: float,
    is_attached: bool,
    quality_coverage: float,
    close_above_threshold: bool,
    penetration_atr: float | None,
    component_scores: Sequence[float],
) -> float:
    """How sure the engine is that it has identified the event correctly.

    Distinct from quality, and the distinction is the one item 41 asks for. A
    breakout can look excellent and still be an uncertain *identification*: an
    unattached level, one touch, a close sitting a hair above the tolerance
    zone, three of seven components measurable. Reporting 91 without that caveat
    invites a consumer to treat the two situations identically.

    Five terms, each bounded to [0, 100] and averaged under stated weights:

    * **boundary** — the pattern layer's confidence in the level;
    * **definition** — how many touches defined it, and whether it belongs to a
      detected pattern at all;
    * **coverage** — how much of the intended evidence was measurable;
    * **decisiveness** — how far clear of the ambiguity zone the close sits. A
      close 0.02 ATR above the threshold is a breakout *identification* that
      would flip under a slightly different tolerance;
    * **agreement** — how tightly the components cluster. Components spread from
      15 to 95 average to something respectable while describing an event
      nobody would recognise, and the mean alone hides that.
    """
    definition = min(100.0, boundary_touches * 25.0)
    if not is_attached:
        definition *= 0.6
    pattern_term = pattern_quality if is_attached else 40.0

    if penetration_atr is None:
        decisiveness = 60.0 if close_above_threshold else 30.0
    else:
        decisiveness = float(np.clip(penetration_atr / 0.5, 0.0, 1.0) * 100.0)

    if len(component_scores) >= 2:
        spread = float(np.std(component_scores))
        agreement = float(np.clip(100.0 - spread * 2.0, 0.0, 100.0))
    else:
        agreement = 50.0

    value = (
        boundary_confidence * 0.20
        + definition * 0.15
        + pattern_term * 0.15
        + quality_coverage * 0.20
        + decisiveness * 0.15
        + agreement * 0.15
    )
    return float(np.clip(value, 0.0, 100.0))


__all__ = [
    "BREAKOUT_SCORER_VERSION",
    "REQUIRED_QUALITY_COMPONENTS",
    "ConfirmationInputs",
    "QualityInputs",
    "ScoreResult",
    "breakout_confidence",
    "score_breakout_quality",
    "score_confirmation",
]
