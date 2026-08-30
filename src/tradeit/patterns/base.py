"""Pattern architecture: what a detector returns and what that means.

Phase 4 answers one question: *does this price/volume structure resemble a valid
bullish chart pattern, and how good is that pattern?* It does not answer whether
to buy anything. A high-quality pattern is one input to a decision made several
stages later, and the separation is structural rather than a matter of
discipline — nothing in this package can see a portfolio, a position size, or a
fill.

Three commitments shape everything here.

**Patterns are continuous, not binary.** ``IF price rose 20% AND fell 10% THEN
bull_flag`` is not pattern recognition; it is a tripwire that fires on
coincidence and misses everything one parameter away from the template. Every
detector evaluates several structural dimensions independently, scores each
0-100, and combines them under configurable weights. The component scores
survive into the output, because "quality 72" is unusable and "quality 72:
flagpole 91, volume structure 38" is a diagnosis.

**Uncertainty is reported, not hidden.** Every instance carries supporting *and*
contradicting evidence. A 90-quality flag with three contradicting observations
is a materially different object from a 90 with none, and a detector that only
records what it liked is arguing for its own conclusion.

**Nothing is decided from the future.** A detector sees a bar series that has
already been clock-gated by the caller, and everything it derives -- swing
points, resistance, support -- must be derivable from bars at or before the
evaluation session. This is not merely tested after the fact; the primitives in
:mod:`tradeit.patterns.swings` are built so the leak is difficult to write.

The one subtlety worth stating plainly: **a pattern may legitimately change as
new bars arrive.** A flag detected on Monday may be MATURE on Wednesday and
BROKEN_OUT_UNCONFIRMED on Friday, and its resistance may firm up as more
touches accumulate. That is evolution, and it is fine. What is forbidden is
*silently rewriting what the system claims it knew on Monday* -- so state
history is preserved and every instance records the knowledge boundary it was
computed under.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import content_hash


class PatternState(StrEnum):
    """Where a pattern is in its life.

    Ordered by progression, though not every pattern visits every state. The
    terminal states (``INVALIDATED``, ``EXPIRED``) are absorbing: a structure
    that broke its support does not become MATURE again, it becomes a different
    structure with its own identity.
    """

    #: Structure is present but incomplete -- too short, too few touches, or
    #: geometry still resolving. Recorded, not actionable.
    FORMING = "forming"

    #: The structure satisfies its definition. Resistance and support are
    #: established. This is the state a screen would surface.
    MATURE = "mature"

    #: Mature, and price is within striking distance of the resistance level.
    #: A scheduling hint -- these are the names worth monitoring intraday --
    #: not a trading signal.
    NEAR_BREAKOUT = "near_breakout"

    #: Price has closed above structural resistance by more than noise.
    #:
    #: **This is a geometric observation, not a verdict.** Whether the breakout
    #: is valid, tradeable, or likely to hold is Phase 5's question, and Phase 4
    #: must not answer it. The name says "unconfirmed" because that is the
    #: permanent condition of anything Phase 4 can say about a breakout.
    BROKEN_OUT_UNCONFIRMED = "broken_out_unconfirmed"

    #: Structure failed: support broken, retracement excessive, or geometry
    #: destroyed. Terminal.
    INVALIDATED = "invalidated"

    #: Ran out of time without resolving. Terminal, and distinct from
    #: invalidation -- a base that drifts sideways for a year did not fail, it
    #: stopped being the thing that was detected.
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (PatternState.INVALIDATED, PatternState.EXPIRED)

    @property
    def is_established(self) -> bool:
        """Whether the structure met its definition at some point."""
        return self in (
            PatternState.MATURE,
            PatternState.NEAR_BREAKOUT,
            PatternState.BROKEN_OUT_UNCONFIRMED,
        )

    @property
    def may_be_screened(self) -> bool:
        """Whether a later stage should consider this instance at all.

        FORMING is deliberately excluded. Surfacing incomplete structures to a
        scorer means scoring a guess about what a structure will become.
        """
        return self.is_established


class ComponentRequirement(StrEnum):
    """Whether a component is part of the pattern's definition or its context.

    The distinction is the difference between "we could not measure this" and
    "there is nothing here to classify".

    A bull flag with no benchmark series cannot be scored for relative
    strength. That is a gap in *evidence* — the flag still exists, it is still a
    flag, and refusing to report it would confuse corroboration with definition.

    A bull flag with too little history to establish a flagpole is not a flag
    with a gap. There is no flagpole, so there is no flag, and emitting one
    anyway would be constructing a pattern from insufficient structural
    evidence.
    """

    #: The pattern cannot be classified without it. Its absence suppresses the
    #: instance entirely rather than lowering a score.
    REQUIRED = "required"
    #: Contributes to quality and coverage. Its absence is reported, not fatal.
    OPTIONAL = "optional"


class EvidenceKind(StrEnum):
    """Why an observation was recorded, so a reader can weigh it."""

    #: Measured from price/volume geometry. The strongest kind.
    STRUCTURAL = "structural"
    #: Derived from an analytics feature (RS, ATR percentile, breadth).
    ANALYTIC = "analytic"
    #: A property of the instrument or session, not the pattern (thin
    #: liquidity, an earnings date inside the window).
    CONTEXTUAL = "contextual"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One observation about a pattern, for or against.

    ``detail`` is prose for a human; ``measured`` and ``expected`` are the
    numbers behind it, kept separate so a report can aggregate on them rather
    than parsing sentences.
    """

    detail: str
    kind: EvidenceKind = EvidenceKind.STRUCTURAL
    measured: float | None = None
    expected: str | None = None

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class ComponentScore:
    """One scored dimension of pattern quality, with its reasoning attached.

    Components are scored independently and combined by configurable weights.
    Keeping them separate through to the output is what makes a quality score
    debuggable: two patterns can both score 70 for entirely different reasons,
    and a screen that cannot tell them apart is throwing away the distinction.
    """

    name: str
    score: float
    weight: float
    #: What was measured, keyed by sub-dimension. Present in the output so a
    #: dashboard can show the workings without re-deriving them.
    measurements: Mapping[str, float] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    contradicting: tuple[Evidence, ...] = ()
    #: True when the component could not be computed (missing benchmark,
    #: insufficient history). Distinct from a score of zero, which is a
    #: measured failure.
    unavailable: bool = False
    #: Why it could not be computed. Mandatory when ``unavailable`` -- "not
    #: available" without a reason is an unfalsifiable claim, and an operator
    #: staring at 58% coverage needs to know whether the fix is a benchmark
    #: series or more history.
    unavailable_reason: str = ""
    #: Whether the pattern's *definition* depends on this component. A required
    #: component that cannot be computed means the structure cannot be
    #: classified at all; an optional one only reduces evidence coverage.
    requirement: ComponentRequirement = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.requirement is None:  # dataclass default resolved lazily
            object.__setattr__(self, "requirement", ComponentRequirement.OPTIONAL)
        if not self.unavailable and not 0.0 <= self.score <= 100.0:
            raise ConfigError(f"component {self.name!r} scored {self.score}, outside [0, 100]")
        if self.weight < 0:
            raise ConfigError(f"component {self.name!r} has negative weight {self.weight}")
        if self.unavailable and not self.unavailable_reason:
            raise ConfigError(
                f"component {self.name!r} is unavailable without a reason; "
                "an unexplained gap in the evidence cannot be acted on or fixed"
            )

    @property
    def contribution(self) -> float:
        return 0.0 if self.unavailable else self.score * self.weight

    @property
    def effective_weight(self) -> float:
        """Zero when unavailable, so the composite renormalises rather than
        treating a missing component as a zero score."""
        return 0.0 if self.unavailable else self.weight

    @property
    def blocks_classification(self) -> bool:
        """Whether this gap prevents the pattern from being classified at all."""
        return self.unavailable and self.requirement is ComponentRequirement.REQUIRED


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A dated price, for drawing the pattern on a chart later."""

    session_date: dt.date
    price: float

    def as_dict(self) -> dict[str, Any]:
        return {"date": self.session_date.isoformat(), "price": self.price}


@dataclass(frozen=True, slots=True)
class Boundary:
    """A support or resistance line, with how much to believe it.

    A boundary defined by five touches over three months is a different object
    from one defined by the single highest bar in the window, and the two must
    not be reported identically. ``touches`` and ``confidence`` carry that.

    Sloped boundaries are supported because real flags and triangles have them;
    ``price_at`` evaluates the line on a given session so a horizontal boundary
    is just the degenerate case with zero slope.
    """

    kind: str  # "resistance" | "support"
    #: How the level was derived: "swing_highs", "horizontal_cluster",
    #: "regression_channel", "single_extreme".
    method: str
    #: Price at ``anchor_date``.
    level: float
    anchor_date: dt.date
    #: Price change per session. Zero for a horizontal level.
    slope_per_session: float = 0.0
    touches: tuple[PricePoint, ...] = ()
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    confidence: float = 0.0

    @property
    def touch_count(self) -> int:
        return len(self.touches)

    @property
    def is_horizontal(self) -> bool:
        return self.slope_per_session == 0.0

    def price_at(self, session: dt.date, sessions_from_anchor: int | None = None) -> float:
        """The boundary's level on a given session.

        ``sessions_from_anchor`` is *trading* sessions, which the caller knows
        and this class does not. When omitted the calculation falls back to
        calendar days, which is wrong across weekends and holidays -- so a
        sloped boundary without it raises rather than quietly drifting.
        """
        if self.slope_per_session == 0.0:
            return self.level
        if sessions_from_anchor is None:
            raise ConfigError(
                "a sloped boundary needs sessions_from_anchor: calendar-day "
                "arithmetic drifts across weekends and holidays"
            )
        return self.level + self.slope_per_session * sessions_from_anchor

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "method": self.method,
            "level": self.level,
            "anchor_date": self.anchor_date.isoformat(),
            "slope_per_session": self.slope_per_session,
            "touches": [t.as_dict() for t in self.touches],
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class PatternGeometry:
    """Enough structure to redraw the pattern on a chart.

    Stored rather than recomputed because the detector had information the
    dashboard will not: the bar series as it stood at the knowledge boundary.
    Recomputing later against a longer series would draw a *different* pattern
    and call it the same one.

    ``segments`` names the sub-structures by pattern-specific keys --
    ``flagpole``, ``consolidation``, ``cup``, ``handle``, ``left_shoulder`` --
    so one geometry type serves every detector without a union of twelve
    dataclasses.
    """

    start_date: dt.date
    end_date: dt.date
    #: Named (start, end) date spans. Keys are the detector's own vocabulary.
    segments: Mapping[str, tuple[dt.date, dt.date]] = field(default_factory=dict)
    resistance: Boundary | None = None
    support: Boundary | None = None
    #: Named single points: ``pivot``, ``cup_bottom``, ``head``, ``first_low``.
    key_points: Mapping[str, PricePoint] = field(default_factory=dict)
    swing_highs: tuple[PricePoint, ...] = ()
    swing_lows: tuple[PricePoint, ...] = ()

    @property
    def length_sessions(self) -> int:
        """Calendar span in days. Session count lives in the instance."""
        return (self.end_date - self.start_date).days

    @property
    def boundaries_are_ordered(self) -> bool:
        """Whether support sits strictly below resistance, as a range must.

        False means the detector has described something that is not a
        consolidation. Each boundary can be locally defensible and the pair
        still incoherent: `structural_support` clusters swing lows and
        `structural_resistance` clusters swing highs, and in a base that drifts
        upward the late lows can sit above the early highs. Every measurement
        drawn from such a pair — depth, width, penetration, position within the
        range — is then a difference between two lines in the wrong order.

        The database asserts the same invariant in
        ``ck_pattern_support_below_resistance``, where it can only abort a run.
        Detectors consult this first so the structure is *rejected* with a
        reason instead. Real multi-year series produce these; the synthetic
        corpora, whose bases bracket their lows by construction, never did.
        """
        if self.resistance is None or self.support is None:
            return True
        return self.support.level < self.resistance.level

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "segments": {
                name: [a.isoformat(), b.isoformat()]
                for name, (a, b) in sorted(self.segments.items())
            },
            "resistance": self.resistance.as_dict() if self.resistance else None,
            "support": self.support.as_dict() if self.support else None,
            "key_points": {k: v.as_dict() for k, v in sorted(self.key_points.items())},
            "swing_highs": [p.as_dict() for p in self.swing_highs],
            "swing_lows": [p.as_dict() for p in self.swing_lows],
        }


@dataclass(frozen=True, slots=True)
class PatternInstance:
    """One detected structure, as of one knowledge boundary.

    The fields fall into four groups, and mixing them up is how a pattern
    dataset becomes uninterpretable:

    * **Identity** -- ``identity_key`` is stable across days, so Monday's
      FORMING flag and Friday's BROKEN_OUT_UNCONFIRMED flag are the same
      pattern rather than five unrelated rows.
    * **Knowledge** -- ``as_of_session`` and ``knowledge_time`` say what the
      detector could see. A row without them cannot be audited.
    * **Structure** -- geometry, boundaries, invalidation.
    * **Assessment** -- component scores, quality, evidence.

    ``invalidation_price`` is structural: the level at which this *pattern* has
    failed as a pattern. It is emphatically **not** a stop-loss. A stop belongs
    to a position and depends on portfolio risk, volatility and sizing, none of
    which exist at this stage. Conflating them produces stops placed by a
    chart-drawing routine.
    """

    instrument_id: int
    pattern_type: PatternType
    timeframe: Bartimeframe
    state: PatternState
    geometry: PatternGeometry

    #: The last session the detector was allowed to see.
    as_of_session: dt.date
    #: The clock instant that gated the input. Recorded so a stored pattern can
    #: be reproduced against the same visible data.
    knowledge_time: dt.datetime

    quality: float
    components: tuple[ComponentScore, ...] = ()
    confidence: float = 0.0

    invalidation_price: float | None = None
    #: Sessions from pattern start to ``as_of_session``, counted on the trading
    #: calendar. The honest length; ``geometry.length_sessions`` is calendar days.
    session_count: int = 0

    supporting_evidence: tuple[Evidence, ...] = ()
    contradicting_evidence: tuple[Evidence, ...] = ()
    notes: tuple[str, ...] = ()

    detector_name: str = ""
    detector_version: int = 1
    config_digest: str = ""
    data_snapshot_digest: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.quality <= 100.0:
            raise ConfigError(f"pattern quality {self.quality} outside [0, 100]")
        if self.geometry.end_date > self.as_of_session:
            raise ConfigError(
                f"pattern geometry ends {self.geometry.end_date}, after the "
                f"knowledge boundary {self.as_of_session}: the detector used a bar "
                "it was not allowed to see"
            )

    @property
    def identity_key(self) -> str:
        """Stable identity across the pattern's life.

        Keyed on what does *not* change as the pattern evolves: the instrument,
        the timeframe, the pattern type, and where the structure began. The end
        date, the state, the scores and the resistance level all move as bars
        arrive; the origin does not.

        Deliberately excludes the detector version, so that a detector bug fix
        does not fork every open pattern into a new identity. It also excludes
        ``as_of_session``, which is the whole point -- including it would make
        every day a new pattern, which is precisely the failure this key exists
        to prevent.
        """
        return content_hash(
            {
                "instrument_id": self.instrument_id,
                "pattern_type": str(self.pattern_type),
                "timeframe": str(self.timeframe),
                "start_date": self.geometry.start_date.isoformat(),
            }
        )[:24]

    @property
    def resistance_price(self) -> float | None:
        return self.geometry.resistance.level if self.geometry.resistance else None

    @property
    def support_price(self) -> float | None:
        return self.geometry.support.level if self.geometry.support else None

    def component(self, name: str) -> ComponentScore | None:
        for item in self.components:
            if item.name == name:
                return item
        return None

    @property
    def reconciles(self) -> bool:
        """Whether the component scores actually produce the quality score.

        Guards a specific failure: a late adjustment applied to ``quality``
        without being recorded as a component, producing an explanation that
        does not add up to the number it explains.
        """
        total = sum(c.effective_weight for c in self.components)
        if total <= 0:
            return self.quality == 0.0
        expected = sum(c.contribution for c in self.components) / total
        return abs(expected - self.quality) < 1e-6

    # -- evidence coverage ---------------------------------------------------

    @property
    def evidence_coverage(self) -> float:
        """How much of the intended evidence was actually available, 0-100.

        Deliberately a *separate* number from quality, and deliberately not
        multiplied into it. They answer different questions:

        * **Quality**: how good does the available evidence look?
        * **Coverage**: how much of the intended evidence was available?

        Quality 92 / coverage 100 and quality 92 / coverage 58 are materially
        different results, and collapsing them into one number destroys the
        distinction irrecoverably. The second is a pattern that looks excellent
        on the four dimensions that could be measured while four others are
        simply unknown — which a consumer may reasonably treat as a strong
        candidate or as unusable, but cannot decide if the system has already
        decided for it.

        Whether coverage should gate trade eligibility is a later phase's
        decision. Phase 4's job is to preserve both values honestly.
        """
        total = sum(c.weight for c in self.components)
        if total <= 0:
            return 0.0
        available = sum(c.weight for c in self.components if not c.unavailable)
        return round(available / total * 100.0, 6)

    @property
    def available_components(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.components if not c.unavailable)

    @property
    def unavailable_components(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.components if c.unavailable)

    def coverage_gaps(self) -> dict[str, str]:
        """Unavailable components mapped to why, so a gap can be acted on."""
        return {c.name: c.unavailable_reason for c in self.components if c.unavailable}

    @property
    def is_structurally_complete(self) -> bool:
        """Whether every component the *definition* requires was measurable.

        False means the instance should not have been emitted at all: a
        structure missing a required component is not a low-coverage pattern,
        it is not that pattern.
        """
        return not any(c.blocks_classification for c in self.components)

    def explain(self) -> str:
        """The human-readable output the brief specifies."""
        lines = [
            f"Pattern:          {self.pattern_type}",
            f"State:            {self.state}",
            f"Pattern Quality:  {self.quality:.0f}",
            f"Evidence Coverage:{self.evidence_coverage:>4.0f}",
            f"Confidence:       {self.confidence:.0f}",
            f"Detector:         {self.detector_name} v{self.detector_version}",
            "",
        ]
        for item in self.components:
            label = "n/a" if item.unavailable else f"{item.score:.0f}"
            marker = "*" if item.requirement is ComponentRequirement.REQUIRED else " "
            lines.append(f" {marker}{item.name:<28} {label:>4}  (weight {item.weight:.2f})")
        gaps = self.coverage_gaps()
        if gaps:
            lines.append("")
            lines.append("Unavailable:")
            lines.extend(f"  {name}: {reason}" for name, reason in sorted(gaps.items()))
        lines.append("")
        if self.geometry.resistance:
            lines.append(f"Resistance:       {self.geometry.resistance.level:.2f}")
        if self.geometry.support:
            lines.append(f"Support:          {self.geometry.support.level:.2f}")
        if self.invalidation_price is not None:
            lines.append(f"Invalidation:     {self.invalidation_price:.2f}")
        lines.append("")
        lines.append("Supporting Evidence:")
        lines.extend(f"  * {e}" for e in self.supporting_evidence or ())
        if not self.supporting_evidence:
            lines.append("  (none)")
        lines.append("Contradicting Evidence:")
        lines.extend(f"  * {e}" for e in self.contradicting_evidence or ())
        if not self.contradicting_evidence:
            lines.append("  (none)")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        """Canonical form for storage and hashing."""
        return {
            "instrument_id": self.instrument_id,
            "pattern_type": str(self.pattern_type),
            "timeframe": str(self.timeframe),
            "state": str(self.state),
            "as_of_session": self.as_of_session.isoformat(),
            "quality": round(self.quality, 6),
            "confidence": round(self.confidence, 6),
            "invalidation_price": self.invalidation_price,
            "session_count": self.session_count,
            "geometry": self.geometry.as_dict(),
            "components": [
                {
                    "name": c.name,
                    "score": None if c.unavailable else round(c.score, 6),
                    "weight": c.weight,
                    "measurements": {k: round(v, 6) for k, v in sorted(c.measurements.items())},
                }
                for c in self.components
            ],
            "supporting_evidence": [e.detail for e in self.supporting_evidence],
            "contradicting_evidence": [e.detail for e in self.contradicting_evidence],
            "detector": {"name": self.detector_name, "version": self.detector_version},
            "config_digest": self.config_digest,
        }


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """A structure under consideration, before it is judged valid.

    The distinction between candidate and instance is a look-ahead control, not
    bookkeeping. Pattern discovery necessarily searches over windows -- where
    did the flagpole start? how long is the consolidation? -- and the tempting
    implementation scores every possible window and keeps the best. On
    historical data that silently selects the window that *turned out* to look
    good, which is pattern-selection look-ahead bias and is invisible in the
    output.

    So candidate enumeration is constrained to structures a detector could have
    identified at the time, from causally-confirmed swing points, and the
    selection rule among survivors is stated by each detector rather than being
    "highest score wins".
    """

    start_index: int
    end_index: int
    #: Detector-specific split points inside the window (e.g. flagpole end).
    anchors: Mapping[str, int] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.end_index < self.start_index:
            raise ConfigError(
                f"candidate ends at {self.end_index} before it starts at {self.start_index}"
            )

    @property
    def length(self) -> int:
        return self.end_index - self.start_index + 1


@dataclass(frozen=True, slots=True)
class DetectorContract:
    """What a detector needs, declared rather than discovered.

    Written down because the alternative is that each detector's requirements
    live implicitly in the order of its early-return statements, where nobody
    can review them and a later refactor can quietly relax one.

    ``minimum_evidence_coverage`` deserves a note. It is **not** a trading
    threshold and Phase 4 sets no trading thresholds. It is the point below
    which the detector's own quality score stops meaning what it says: a
    composite computed from a fifth of its intended evidence is describing
    something other than the pattern. A detector may still emit such an
    instance -- with the low coverage on its face -- but it should not claim it
    is MATURE.
    """

    #: Datasets that must be present. ``ohlcv_bars`` is universal; a detector
    #: needing corporate actions or fundamentals says so here.
    required_inputs: tuple[str, ...] = ("ohlcv_bars",)
    #: Components without which the pattern cannot be classified at all.
    required_components: tuple[str, ...] = ()
    #: Components that contribute to quality and coverage but never gate.
    optional_components: tuple[str, ...] = ()
    #: Bars needed before the detector can produce anything.
    warmup_bars: int = 0
    #: Below this, the instance stays FORMING regardless of its score.
    minimum_evidence_coverage: float = 0.0

    def __post_init__(self) -> None:
        overlap = set(self.required_components) & set(self.optional_components)
        if overlap:
            raise ConfigError(f"components declared both required and optional: {sorted(overlap)}")
        if not 0.0 <= self.minimum_evidence_coverage <= 100.0:
            raise ConfigError("minimum_evidence_coverage must be a percentage")

    @property
    def all_components(self) -> tuple[str, ...]:
        return (*self.required_components, *self.optional_components)

    def requirement_of(self, name: str) -> ComponentRequirement:
        return (
            ComponentRequirement.REQUIRED
            if name in self.required_components
            else ComponentRequirement.OPTIONAL
        )

    def describe(self) -> dict[str, Any]:
        return {
            "required_inputs": list(self.required_inputs),
            "required_components": list(self.required_components),
            "optional_components": list(self.optional_components),
            "warmup_bars": self.warmup_bars,
            "minimum_evidence_coverage": self.minimum_evidence_coverage,
        }


@runtime_checkable
class Detector(Protocol):
    """Finds one family of structures in a clock-gated bar series.

    One detector per pattern type, deliberately. They have genuinely different
    parameters, they are validated separately, and a single class that finds
    twelve patterns is a class nobody will ever refactor. Renaming one
    detector's output to serve another pattern is the specific anti-pattern
    this interface exists to prevent.

    ``bars`` is guaranteed by the caller to contain nothing after
    ``as_of_session``. Detectors must not reach past it, and the causality
    tests assert they do not.
    """

    name: str
    pattern_type: PatternType
    #: Bumped whenever a scoring rule or structural definition changes, so a
    #: stored pattern says which definition produced it. A backtest pins this;
    #: silently recomputing history under a newer detector and presenting the
    #: results as unchanged is the specific dishonesty it prevents.
    version: int

    @property
    def contract(self) -> DetectorContract: ...

    @property
    def minimum_bars(self) -> int: ...

    #: The ATR period the detector computes with. Part of the interface rather
    #: than a private detail because callers that bound a rescan window need it:
    #: Wilder smoothing has unbounded memory, so how much history a detector
    #: needs re-examined depends on this number.
    @property
    def atr_period(self) -> int: ...

    @property
    def parameters(self) -> Mapping[str, object]: ...

    def detect(
        self,
        bars: Sequence[OhlcvBar],
        as_of_session: dt.date,
        *,
        context: PatternContext | None = None,
    ) -> list[PatternInstance]: ...


@dataclass(frozen=True, slots=True)
class PatternContext:
    """Optional analytics a detector may use but must work without.

    Relative strength is the motivating case. High-quality patterns often hold
    up against the benchmark while price consolidates, and that is genuine
    quality evidence -- but it is **not pattern geometry**, and a detector that
    refuses to find a flag because the RS series is missing has confused
    evidence with definition. So every field here is optional, and a detector
    marks the corresponding component ``unavailable`` rather than scoring it
    zero.
    """

    #: Benchmark closes aligned one-to-one with the bar series.
    benchmark_closes: Sequence[float] | None = None
    #: Sector proxy closes, aligned the same way.
    sector_closes: Sequence[float] | None = None
    #: The instrument's cross-sectional RS percentile on ``as_of_session``.
    rs_percentile: float | None = None
    #: Average dollar volume, for liquidity-sensitive patterns.
    average_dollar_volume: float | None = None
    #: Sessions where an earnings event is known to fall inside the window.
    earnings_sessions: Sequence[dt.date] = ()
    data_snapshot_digest: str = ""

    def aligned_with(self, bars: Sequence[OhlcvBar]) -> bool:
        """Whether the supplied series line up with the bars.

        A misaligned benchmark produces a plausible relative-strength number
        computed from mismatched days, which is worse than no number at all.
        """
        n = len(bars)
        for series in (self.benchmark_closes, self.sector_closes):
            if series is not None and len(series) != n:
                return False
        return True
