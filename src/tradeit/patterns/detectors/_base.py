"""Shared detector machinery.

Every detector performs the same six steps around its own structural logic:
validate the knowledge boundary, compute ATR, find confirmed pivots, discover
structures, score them, and assemble instances with coverage and state applied.
Writing that twelve times would be twelve chances for one detector's coverage
floor to be applied differently, or one detector to forget the boundary check.

**What is shared is the machinery. What is not shared is the pattern.** Each
detector supplies its own :meth:`discover` (where does this structure begin and
end?) and :meth:`score` (what makes it good?), and those two methods are where
the genuine difference between a flag and a triangle lives. A detector that
inherited scoring would be a superficial wrapper, which is the specific outcome
this base is designed to make *harder* rather than easier: `discover` and
`score` are abstract, so a subclass cannot accidentally get a flag's opinion.

Discovery must obey ADR-0014: structures are found from causal market geometry,
never selected by maximising the detector's own quality score.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from tradeit.analytics import kernels
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.core.models import OhlcvBar
from tradeit.patterns.base import (
    Boundary,
    ComponentScore,
    DetectorContract,
    Evidence,
    EvidenceKind,
    PatternContext,
    PatternGeometry,
    PatternInstance,
    PatternState,
)
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.scoring import combine, confidence_from
from tradeit.patterns.swings import Swing, SwingKind, confirmed_swings

Floats = np.ndarray


@dataclass(slots=True)
class Structure:
    """A discovered structure, before it is judged.

    Carries whatever the detector's own discovery produced under ``parts`` --
    contraction legs, a cup and a handle, a neckline. The base never inspects
    it; it exists so the scoring and geometry methods receive one object rather
    than a widening parameter list.

    ``parts`` is deliberately untyped. Every detector's structure is different,
    and a union of twelve payload types would be a type that means "anything"
    with extra ceremony. The detector that wrote a part is the only code that
    reads it, so the type checker adds nothing here.
    """

    start_index: int
    end_index: int
    parts: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DetectionInputs:
    """Everything the base computed once, handed to the detector's own methods.

    Computed once per ``detect`` call and passed down, so a detector cannot
    accidentally recompute pivots with different parameters — which would give
    two structures in one instance different notions of what a pivot is.
    """

    bars: Sequence[OhlcvBar]
    atr: Floats
    swing_highs: tuple[Swing, ...]
    swing_lows: tuple[Swing, ...]
    as_of_session: dt.date
    context: PatternContext | None

    @property
    def last_index(self) -> int:
        return len(self.bars) - 1

    @property
    def structure_end(self) -> int:
        """Last index that may *define* structure.

        The provisional tail may inform state but never geometry. This is the
        rule from :mod:`tradeit.patterns.swings`, applied once here so no
        detector has to remember it.
        """
        return self.last_index - self._right_bars

    _right_bars: int = 3

    def closes(self) -> Floats:
        return np.array([float(b.close) for b in self.bars])


class BaseDetector(ABC):
    """Common detection flow. Subclasses supply structure and judgement."""

    name: str = ""
    pattern_type: PatternType
    CONTRACT: DetectorContract

    def __init__(
        self,
        config: PatternEngineConfig | None = None,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> None:
        self.engine_config = config or PatternEngineConfig()
        self.timeframe = timeframe

    # -- subclass hooks ------------------------------------------------------

    @abstractmethod
    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """Find structures from causal market geometry.

        Must obey ADR-0014: boundaries come from confirmed pivots, impulse
        termination, contraction transitions or prior structural events — never
        from an unbounded search scored by this detector's own quality
        function.
        """

    @abstractmethod
    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        """Judge a discovered structure on this pattern's own dimensions."""

    @abstractmethod
    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        """Enough detail to redraw the pattern later without recomputing it."""

    @abstractmethod
    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """The level at which this *pattern* has failed. Not a stop-loss."""

    @abstractmethod
    def weights(self) -> Mapping[str, float]:
        """Component weights, from configuration."""

    def resistance_of(self, geometry: PatternGeometry) -> Boundary | None:
        return geometry.resistance

    def notes(self, structure: Structure) -> tuple[str, ...]:
        return ()

    @property
    @abstractmethod
    def version(self) -> int: ...

    @property
    @abstractmethod
    def minimum_bars(self) -> int: ...

    @property
    @abstractmethod
    def atr_period(self) -> int: ...

    # -- shared flow ---------------------------------------------------------

    @property
    def contract(self) -> DetectorContract:
        return replace(self.CONTRACT, warmup_bars=self.minimum_bars)

    @property
    def parameters(self) -> Mapping[str, object]:
        return dict(self.weights())

    def detect(
        self,
        bars: Sequence[OhlcvBar],
        as_of_session: dt.date,
        *,
        context: PatternContext | None = None,
    ) -> list[PatternInstance]:
        if len(bars) < self.minimum_bars:
            return []
        if bars[-1].session_date > as_of_session:
            raise ValueError(
                f"bars extend to {bars[-1].session_date}, past the knowledge boundary "
                f"{as_of_session}; the caller must clock-gate the series"
            )
        if context is not None and not context.aligned_with(bars):
            raise ValueError(
                "pattern context series are not aligned with the bar series; a "
                "misaligned benchmark produces relative strength computed from "
                "mismatched days"
            )

        highs = np.array([float(b.high) for b in bars])
        lows = np.array([float(b.low) for b in bars])
        closes = np.array([float(b.close) for b in bars])
        atr = kernels.atr(highs, lows, closes, self.atr_period)

        swing_cfg = self.engine_config.swings
        inputs = DetectionInputs(
            bars=bars,
            atr=atr,
            swing_highs=tuple(
                confirmed_swings(
                    bars,
                    as_of_session,
                    left_bars=swing_cfg.left_bars,
                    right_bars=swing_cfg.right_bars,
                    kind=SwingKind.HIGH,
                    atr=atr,
                )
            ),
            swing_lows=tuple(
                confirmed_swings(
                    bars,
                    as_of_session,
                    left_bars=swing_cfg.left_bars,
                    right_bars=swing_cfg.right_bars,
                    kind=SwingKind.LOW,
                    atr=atr,
                )
            ),
            as_of_session=as_of_session,
            context=context,
            _right_bars=swing_cfg.right_bars,
        )

        discovered = _one_per_structural_start(self.discover(inputs))
        instances: list[PatternInstance] = []
        for structure in discovered[: self.engine_config.max_candidates_per_pattern]:
            instance = self._build(inputs, structure)
            if instance is None or not instance.is_structurally_complete:
                continue
            if instance.quality < self.engine_config.min_quality_to_report:
                continue
            instances.append(instance)

        # Presentation order only. Selection among structures already happened
        # causally in `discover`; sorting here cannot change what exists.
        return sorted(instances, key=lambda p: -p.quality)

    def _build(self, inputs: DetectionInputs, structure: Structure) -> PatternInstance | None:
        components = self.score(inputs, structure)
        if not components:
            return None

        weights = dict(self.weights())
        composite = combine(
            {c.name: None if c.unavailable else c.score for c in components}, weights
        )

        geometry = self.geometry(inputs, structure)
        if not geometry.boundaries_are_ordered:
            # Support above resistance is not a consolidation. Each boundary can
            # be locally defensible — `structural_support` clusters swing lows
            # and `structural_resistance` clusters swing highs, and in a base
            # that drifts upward the late lows can sit above the early highs —
            # but jointly they describe no structure the detector claims to
            # have found, and every measurement drawn from them (depth, width,
            # penetration) is a difference between two lines in the wrong order.
            #
            # Rejected here rather than at the database, which asserts the same
            # invariant in `ck_pattern_support_below_resistance` and could only
            # abort a whole run over it. Real multi-year series produce these;
            # the synthetic corpora, which build bases whose highs bracket their
            # lows by construction, never did.
            return None
        invalidation = self.invalidation(inputs, structure)
        coverage = _coverage(components)

        supporting: list[Evidence] = []
        contradicting: list[Evidence] = []
        for component in components:
            supporting.extend(component.evidence)
            contradicting.extend(component.contradicting)

        state = self.classify_state(inputs, structure, geometry, invalidation, composite.value)
        if coverage < self.contract.minimum_evidence_coverage and state.is_established:
            state = PatternState.FORMING
            contradicting.append(
                Evidence(
                    f"evidence coverage {coverage:.0f}% is below the detector's "
                    f"{self.contract.minimum_evidence_coverage:.0f}% floor; the quality "
                    "score rests on too little of its intended evidence to call this "
                    "mature",
                    EvidenceKind.CONTEXTUAL,
                    measured=coverage,
                )
            )

        return PatternInstance(
            instrument_id=inputs.bars[0].instrument_id,
            pattern_type=self.pattern_type,
            timeframe=self.timeframe,
            state=state,
            geometry=geometry,
            as_of_session=inputs.as_of_session,
            knowledge_time=inputs.bars[-1].knowledge_time,
            quality=round(composite.value, 6),
            components=tuple(components),
            confidence=round(
                confidence_from(
                    available_weight=composite.available_weight,
                    total_weight=sum(weights.values()),
                    component_scores=[c.score for c in components if not c.unavailable],
                    session_count=structure.end_index - structure.start_index + 1,
                    minimum_sessions=max(1, self.minimum_bars // 4),
                ),
                6,
            ),
            invalidation_price=invalidation,
            session_count=structure.end_index - structure.start_index + 1,
            supporting_evidence=tuple(supporting),
            contradicting_evidence=tuple(contradicting),
            notes=self.notes(structure),
            detector_name=self.name,
            detector_version=self.version,
            data_snapshot_digest=(inputs.context.data_snapshot_digest if inputs.context else ""),
        )

    def classify_state(
        self,
        inputs: DetectionInputs,
        structure: Structure,
        geometry: PatternGeometry,
        invalidation: float,
        quality: float,
    ) -> PatternState:
        """Default lifecycle: invalidate, then quality, then distance to pivot.

        Overridable, because not every family shares it — a breakout-retest
        structure begins life already resolved and has no NEAR_BREAKOUT.
        """
        states = self.engine_config.states
        last_close = float(inputs.bars[-1].close)

        if last_close < invalidation:
            return PatternState.INVALIDATED
        if quality < states.maturity_quality_floor:
            return PatternState.FORMING

        resistance = self.resistance_of(geometry)
        if resistance is None:
            return PatternState.FORMING

        level = resistance.level
        if last_close > level * (1.0 + states.breakout_buffer_pct):
            # A geometric observation. Whether the breakout counts is Phase 5's
            # question and this package has no vocabulary for answering it.
            return PatternState.BROKEN_OUT_UNCONFIRMED
        if last_close >= level * (1.0 - states.near_breakout_pct):
            return PatternState.NEAR_BREAKOUT
        return PatternState.MATURE


def _one_per_structural_start(structures: Sequence[Structure]) -> list[Structure]:
    """Keep one structure per structural start index.

    **The defect this closes.** Pattern identity is a content hash of the
    instrument, the family, the timeframe and the structural start -- everything
    that does *not* change as a pattern evolves. Several detectors deduplicated
    discovery on a composite key instead: the cup on (left rim, right rim), the
    double bottom on (first low, second low), the inverse head and shoulders on
    all three lows. Those keys permit two structures to share a start, and two
    structures sharing a start share an identity.

    The consequences were not cosmetic. The tracker matches on identity, so it
    would have folded two genuinely different structures into one history; the
    patterns table has a unique constraint on (identity_key, detector_version),
    so the second would have collided on insert. Measured before the fix, more
    than half of the cup detector's instances and three quarters of the inverse
    head and shoulders' collided.

    **Which one is kept, and why that is not a score decision.** Every detector
    walks its anchors from the most recent backwards, so the first structure
    seen at a given start is the one with the most recent completion -- the most
    recent right rim, second low, or right shoulder. That is the structure as it
    stands today, and recency is decided by the market rather than by this
    detector's opinion of the result, which is what ADR-0014 requires.
    """
    seen: set[int] = set()
    kept: list[Structure] = []
    for structure in structures:
        if structure.start_index in seen:
            continue
        seen.add(structure.start_index)
        kept.append(structure)
    return kept


def _coverage(components: Sequence[ComponentScore]) -> float:
    total = sum(c.weight for c in components)
    if total <= 0:
        return 0.0
    return sum(c.weight for c in components if not c.unavailable) / total * 100.0


def unavailable(name: str, weight: float, reason: str) -> ComponentScore:
    """Shorthand for a component that could not be computed.

    Exists so the mandatory reason cannot be forgotten at a call site — the
    constructor already refuses an unexplained gap, and this makes supplying one
    the path of least resistance.
    """
    return ComponentScore(
        name=name, score=0.0, weight=weight, unavailable=True, unavailable_reason=reason
    )
