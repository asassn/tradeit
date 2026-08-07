"""Pattern-detection parameters.

Every number a detector uses lives here, not in code (ADR-0008). That includes
the *shapes* of the scoring curves, not merely their thresholds — a curve whose
ideal band is configurable but whose taper is hard-coded is still a hard-coded
opinion about how quality falls away.

**The defaults are defensible starting points, not fitted values.** Phase 4 is
explicitly forbidden from optimising against trading returns, and nothing here
was searched. Each default carries the structural reasoning for its value, so
that Phase 9 — which *is* allowed to fit, with an out-of-sample harness — knows
what it is arguing against.

Sections are Pydantic models rather than plain dataclasses so they inherit the
strategy config's validation, its ``extra="forbid"`` typo guard, and its
content hash: change any number here and every pattern computed under it gets a
different config digest.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeit.errors import ConfigError


class PatternSection(BaseModel):
    """Base for detector configuration.

    Deliberately a separate base from ``strategy.config.Section`` so this module
    does not import from the module that imports it. Same semantics: frozen,
    and a typo'd key is an error rather than a silent fallback to the default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class SwingConfig(PatternSection):
    """Pivot-detection parameters, shared by every detector.

    ``right_bars`` is the confirmation lag and therefore the causality control:
    a swing high is not knowable until this many sessions have passed. Raising
    it makes pivots more reliable and the detector slower to see them; lowering
    it makes the detector twitchier. It is not a free parameter — it is the
    price of not looking at the future.
    """

    left_bars: int = Field(default=3, ge=1, le=20)
    right_bars: int = Field(default=3, ge=1, le=20)
    #: How close a bar must come to a level to count as testing it.
    touch_tolerance_pct: float = Field(default=0.015, gt=0, lt=0.2)
    #: Minimum sessions between two touches for them to count separately. A
    #: three-day drift along a line is one test, not three.
    min_touch_separation: int = Field(default=2, ge=1, le=10)


class FlagpoleConfig(PatternSection):
    """What counts as a directional advance worth flagging.

    Magnitude is measured two ways because neither alone is right. A 20% move
    is enormous for a utility and ordinary for a biotech, so the ATR-relative
    measure carries most of the weight; but a pure ATR measure would accept a
    trivial advance in a dead-quiet stock, so the percentage floor stays.
    """

    min_gain_pct: float = Field(default=0.08, gt=0)
    #: Where the percentage score saturates. Beyond this, more magnitude stops
    #: being evidence of quality; exhaustion is scored separately.
    strong_gain_pct: float = Field(default=0.25, gt=0)
    #: Advance measured in ATRs. The scale-free version, and the one that lets
    #: a 9% move in a quiet stock outscore a 15% move in a volatile one.
    min_gain_atr: float = Field(default=3.0, gt=0)
    strong_gain_atr: float = Field(default=8.0, gt=0)
    min_sessions: int = Field(default=3, ge=1)
    max_sessions: int = Field(default=40, ge=2)
    #: Preferred duration band. Short and sharp is the classic flagpole; a
    #: 30-session grind is a trend, and the flag that follows it is a different
    #: structure with different odds.
    ideal_sessions_low: int = Field(default=5, ge=1)
    ideal_sessions_high: int = Field(default=18, ge=2)
    #: Volume on the advance against its prior baseline.
    min_volume_ratio: float = Field(default=1.0, gt=0)
    strong_volume_ratio: float = Field(default=1.8, gt=0)
    #: Fraction of flagpole sessions that closed up.
    ideal_up_day_fraction: float = Field(default=0.65, gt=0, le=1)
    #: A single gap accounting for more than this fraction of the whole advance
    #: makes the move gap-driven. **Not disqualifying** — the brief is explicit
    #: that gap moves are recorded, not rejected — but recorded as evidence and
    #: scored slightly lower, because a gap is a repricing rather than an
    #: accumulation.
    gap_dominance_threshold: float = Field(default=0.5, gt=0, le=1)
    #: A pole whose entire advance arrives in one session is not a pole. Unlike
    #: `gap_dominance_threshold`, which characterises *how* a move happened and
    #: is never disqualifying, this is a structural minimum: a flagpole is a
    #: sustained advance, and a single bar has no duration to sustain. Set high
    #: enough that a genuinely sharp two- or three-day thrust still qualifies.
    max_single_session_share: float = Field(default=0.75, gt=0, le=1)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.strong_gain_pct <= self.min_gain_pct:
            raise ConfigError("strong_gain_pct must exceed min_gain_pct")
        if self.strong_gain_atr <= self.min_gain_atr:
            raise ConfigError("strong_gain_atr must exceed min_gain_atr")
        if self.max_sessions <= self.min_sessions:
            raise ConfigError("max_sessions must exceed min_sessions")
        if not (
            self.min_sessions
            <= self.ideal_sessions_low
            <= self.ideal_sessions_high
            <= self.max_sessions
        ):
            raise ConfigError(
                "flagpole ideal duration band must sit inside [min_sessions, max_sessions]"
            )
        return self


class FlagConsolidationConfig(PatternSection):
    """The consolidation half of a bull flag.

    The slope band is the parameter most often got wrong. A textbook flag drifts
    gently *down*; but real flags go sideways constantly and occasionally drift
    mildly up, and a detector demanding a downward parallel channel finds almost
    nothing. So the ideal band spans slightly-negative to flat, tolerance
    extends into mildly-positive, and a steep decline in either direction scores
    poorly — a sharply rising "flag" is just the trend continuing, and a sharply
    falling one is a reversal.
    """

    min_sessions: int = Field(default=3, ge=2)
    max_sessions: int = Field(default=30, ge=3)
    ideal_sessions_low: int = Field(default=5, ge=2)
    ideal_sessions_high: int = Field(default=15, ge=3)
    #: Consolidation length as a multiple of flagpole length. A flag longer than
    #: its own pole has stopped being a pause and become a base.
    max_duration_ratio: float = Field(default=2.5, gt=0)

    #: Slope in fraction-of-price per session.
    ideal_slope_low: float = Field(default=-0.004)
    ideal_slope_high: float = Field(default=0.0005)
    tolerance_slope_low: float = Field(default=-0.012)
    tolerance_slope_high: float = Field(default=0.006)

    #: Retracement of the flagpole. Shallow-to-moderate scores best; deep
    #: retracement means the advance is being given back rather than digested.
    ideal_retracement_low: float = Field(default=0.15, ge=0)
    ideal_retracement_high: float = Field(default=0.40, gt=0)
    tolerance_retracement_low: float = Field(default=0.02, ge=0)
    tolerance_retracement_high: float = Field(default=0.62, gt=0)
    #: Retracing past this fraction of the pole destroys the structure. This is
    #: a genuine discontinuity, not a threshold of taste: below it the pattern
    #: is not a worse flag, it is not a flag.
    invalidation_retracement: float = Field(default=1.0, gt=0)

    #: Channel width as a fraction of price. A flag that is 25% wide is a
    #: trading range wearing a flag's name.
    max_channel_width_pct: float = Field(default=0.20, gt=0)
    ideal_channel_width_pct: float = Field(default=0.08, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_sessions <= self.min_sessions:
            raise ConfigError("max_sessions must exceed min_sessions")
        if not (
            self.min_sessions
            <= self.ideal_sessions_low
            <= self.ideal_sessions_high
            <= self.max_sessions
        ):
            raise ConfigError("consolidation ideal duration band must sit inside the limits")
        if not (
            self.tolerance_slope_low
            <= self.ideal_slope_low
            <= self.ideal_slope_high
            <= self.tolerance_slope_high
        ):
            raise ConfigError("slope band must be ordered")
        if not (
            self.tolerance_retracement_low
            <= self.ideal_retracement_low
            <= self.ideal_retracement_high
            <= self.tolerance_retracement_high
        ):
            raise ConfigError("retracement band must be ordered")
        if self.invalidation_retracement <= self.tolerance_retracement_high:
            raise ConfigError(
                "invalidation_retracement must exceed the tolerance band; otherwise a "
                "pattern is invalidated while still scoring above zero"
            )
        if self.ideal_channel_width_pct >= self.max_channel_width_pct:
            raise ConfigError("ideal channel width must be below the maximum")
        return self


class VolumeStructureConfig(PatternSection):
    """Volume behaviour through a pattern.

    Every threshold here influences quality and none gates detection. The brief
    is explicit that volume contraction must not be an absolute condition, and
    the reasoning is sound: plenty of good flags form on flat volume, and a
    detector that requires dry-up finds a subset of patterns selected for one
    correlated property rather than for structure.
    """

    #: Consolidation volume against flagpole volume. Below 1 is drying up.
    ideal_contraction: float = Field(default=0.6, gt=0)
    #: Where the contraction score bottoms out. Above this, volume is expanding
    #: through the consolidation, which is distribution rather than digestion.
    poor_contraction: float = Field(default=1.3, gt=0)
    #: Sessions at the end of the consolidation to watch for returning
    #: participation. Rising volume near resistance is constructive.
    approach_sessions: int = Field(default=3, ge=1, le=10)
    #: Up-volume over down-volume through the consolidation. Above 1 means
    #: buyers showed up on the up days, which is accumulation.
    ideal_up_down_ratio: float = Field(default=1.2, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.poor_contraction <= self.ideal_contraction:
            raise ConfigError("poor_contraction must exceed ideal_contraction")
        return self


class VolatilityContractionConfig(PatternSection):
    """Range and ATR compression. Shared by the flag and the VCP detector."""

    #: Late-window ATR over early-window ATR. Below 1 is contracting.
    ideal_atr_ratio: float = Field(default=0.6, gt=0)
    poor_atr_ratio: float = Field(default=1.15, gt=0)
    ideal_range_ratio: float = Field(default=0.6, gt=0)
    poor_range_ratio: float = Field(default=1.15, gt=0)
    #: Minimum sessions before contraction can be measured at all. Below this
    #: the halves are too short for the ratio to mean anything.
    min_sessions: int = Field(default=6, ge=4)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.poor_atr_ratio <= self.ideal_atr_ratio:
            raise ConfigError("poor_atr_ratio must exceed ideal_atr_ratio")
        if self.poor_range_ratio <= self.ideal_range_ratio:
            raise ConfigError("poor_range_ratio must exceed ideal_range_ratio")
        return self


class PatternStateConfig(PatternSection):
    """Thresholds that move a pattern between lifecycle states.

    ``breakout_buffer_pct`` deserves a note. Phase 4 declares
    BROKEN_OUT_UNCONFIRMED when price closes above resistance by more than
    noise, and the buffer is what "more than noise" means. It is deliberately
    small: Phase 4's job is to observe that the line was crossed, not to decide
    whether the crossing counts. Setting it large would smuggle breakout
    *confirmation* into Phase 4 under a different name.
    """

    #: Distance to resistance below which a mature pattern is NEAR_BREAKOUT.
    near_breakout_pct: float = Field(default=0.03, gt=0, lt=0.5)
    #: How far above resistance a close must sit to register as a break.
    breakout_buffer_pct: float = Field(default=0.002, ge=0, lt=0.05)
    #: How far below support a close must sit to invalidate.
    invalidation_buffer_pct: float = Field(default=0.005, ge=0, lt=0.1)
    #: Multiple of the maximum consolidation length after which an unresolved
    #: pattern expires rather than being carried indefinitely.
    expiry_multiple: float = Field(default=1.5, gt=1.0)
    #: Quality below which a structure stays FORMING even when its geometry is
    #: complete. Prevents a technically-valid but poor structure being surfaced
    #: as MATURE.
    maturity_quality_floor: float = Field(default=45.0, ge=0, le=100)


class BullFlagWeights(PatternSection):
    """Component weights for the bull-flag quality score.

    Not fitted. The ordering encodes a structural argument: geometry outranks
    context, because a flag with poor geometry is not a flag with a caveat, it
    is a different structure. Flagpole and consolidation carry the most weight
    because they are the pattern's definition; volume and volatility are
    corroboration; relative strength is context and is weighted least because
    the brief is explicit that it is quality evidence, not pattern geometry.
    """

    flagpole: float = Field(default=0.22, ge=0)
    consolidation: float = Field(default=0.20, ge=0)
    retracement: float = Field(default=0.16, ge=0)
    duration: float = Field(default=0.08, ge=0)
    volume_structure: float = Field(default=0.12, ge=0)
    volatility_contraction: float = Field(default=0.10, ge=0)
    relative_strength: float = Field(default=0.06, ge=0)
    resistance_quality: float = Field(default=0.06, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.as_mapping_total <= 0:
            raise ConfigError("at least one component must carry weight")
        return self

    @property
    def as_mapping_total(self) -> float:
        return sum(self.as_mapping().values())

    def as_mapping(self) -> dict[str, float]:
        return {
            "flagpole": self.flagpole,
            "consolidation": self.consolidation,
            "retracement": self.retracement,
            "duration": self.duration,
            "volume_structure": self.volume_structure,
            "volatility_contraction": self.volatility_contraction,
            "relative_strength": self.relative_strength,
            "resistance_quality": self.resistance_quality,
        }


class BullFlagConfig(PatternSection):
    """Everything the bull-flag detector reads."""

    version: int = Field(default=1, ge=1)
    flagpole: FlagpoleConfig = Field(default_factory=FlagpoleConfig)
    consolidation: FlagConsolidationConfig = Field(default_factory=FlagConsolidationConfig)
    volume: VolumeStructureConfig = Field(default_factory=VolumeStructureConfig)
    volatility: VolatilityContractionConfig = Field(default_factory=VolatilityContractionConfig)
    weights: BullFlagWeights = Field(default_factory=BullFlagWeights)
    #: ATR window used for every ATR-relative measurement in this detector.
    atr_period: int = Field(default=14, ge=2)
    #: How many bars before the flagpole to use as the volume baseline.
    volume_baseline_sessions: int = Field(default=20, ge=5)


class VcpWeights(PatternSection):
    """Component weights for the VCP quality score.

    Progression carries the most weight because it *is* the pattern. A base with
    perfect volume dry-up and no tightening is not a mediocre VCP; it is a quiet
    consolidation, and the weighting says so.
    """

    contraction_progression: float = Field(default=0.30, ge=0)
    base_structure: float = Field(default=0.16, ge=0)
    prior_trend: float = Field(default=0.14, ge=0)
    volume_dryup: float = Field(default=0.14, ge=0)
    volatility_profile: float = Field(default=0.12, ge=0)
    pivot_quality: float = Field(default=0.08, ge=0)
    relative_strength: float = Field(default=0.06, ge=0)

    def as_mapping(self) -> dict[str, float]:
        return {
            "contraction_progression": self.contraction_progression,
            "base_structure": self.base_structure,
            "prior_trend": self.prior_trend,
            "volume_dryup": self.volume_dryup,
            "volatility_profile": self.volatility_profile,
            "pivot_quality": self.pivot_quality,
            "relative_strength": self.relative_strength,
        }


class VcpConfig(PatternSection):
    """Volatility Contraction Pattern parameters.

    ``min_contractions`` is 2, not 3. Three is the textbook count and requiring
    it finds a subset of VCPs selected for tidiness rather than for structure --
    a two-leg base that has tightened from 14% to 5% is a VCP in progress, and a
    detector blind to it is blind until the pattern is nearly over.
    """

    version: int = Field(default=1, ge=1)

    min_contractions: int = Field(default=2, ge=2, le=8)
    max_contractions: int = Field(default=6, ge=2, le=12)
    ideal_contractions_low: int = Field(default=3, ge=2)
    ideal_contractions_high: int = Field(default=4, ge=2)

    #: Final depth over first depth. Below 1 means the base tightened.
    ideal_tightening_ratio: float = Field(default=0.35, gt=0)
    poor_tightening_ratio: float = Field(default=0.95, gt=0)
    #: Depth of the last contraction. The tightness that makes a pivot.
    ideal_final_depth: float = Field(default=0.05, gt=0)
    poor_final_depth: float = Field(default=0.15, gt=0)

    min_base_sessions: int = Field(default=15, ge=6)
    max_base_sessions: int = Field(default=140, ge=20)
    ideal_base_sessions_low: int = Field(default=25, ge=6)
    ideal_base_sessions_high: int = Field(default=80, ge=10)

    ideal_base_depth_low: float = Field(default=0.08, gt=0)
    ideal_base_depth_high: float = Field(default=0.28, gt=0)
    #: Beyond this the structure is a correction with tightening legs, not a
    #: base. A 50% decline that narrows as it goes is a security finding a
    #: floor, which is a different bet.
    max_base_depth: float = Field(default=0.40, gt=0, lt=1)

    prior_trend_sessions: int = Field(default=60, ge=20)
    ideal_prior_gain: float = Field(default=0.25, gt=0)
    ideal_volume_ratio: float = Field(default=0.65, gt=0)
    atr_period: int = Field(default=14, ge=2)
    #: A later high within this fraction of the base peak starts a tighter
    #: sub-base of the same structure, reported as a second interpretation.
    sub_base_tolerance: float = Field(default=0.02, gt=0, lt=0.2)

    weights: VcpWeights = Field(default_factory=VcpWeights)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_contractions <= self.min_contractions:
            raise ConfigError("max_contractions must exceed min_contractions")
        if not (
            self.min_contractions
            <= self.ideal_contractions_low
            <= self.ideal_contractions_high
            <= self.max_contractions
        ):
            raise ConfigError("VCP ideal contraction count must sit inside the limits")
        if self.poor_tightening_ratio <= self.ideal_tightening_ratio:
            raise ConfigError("poor_tightening_ratio must exceed ideal_tightening_ratio")
        if self.poor_final_depth <= self.ideal_final_depth:
            raise ConfigError("poor_final_depth must exceed ideal_final_depth")
        if self.max_base_sessions <= self.min_base_sessions:
            raise ConfigError("max_base_sessions must exceed min_base_sessions")
        if self.ideal_base_depth_high >= self.max_base_depth:
            raise ConfigError("ideal base depth must sit below max_base_depth")
        return self


class FlatBaseConfig(PatternSection):
    """Flat base: a shallow, horizontal pause in an advance.

    ``max_depth`` is the parameter that separates this from every other base. A
    flat base is *flat* -- 15% is the classic ceiling, and a 25% "flat base" is
    a correction someone did not want to call one.
    """

    version: int = Field(default=1, ge=1)
    min_sessions: int = Field(default=20, ge=8)
    max_sessions: int = Field(default=120, ge=20)
    ideal_sessions_low: int = Field(default=25, ge=8)
    ideal_sessions_high: int = Field(default=60, ge=15)
    max_depth: float = Field(default=0.15, gt=0, lt=0.5)
    ideal_depth_low: float = Field(default=0.04, gt=0)
    ideal_depth_high: float = Field(default=0.11, gt=0)
    #: Slope of the base, fraction of price per session. A flat base is flat in
    #: both directions -- a rising one is a channel, a falling one is a decline.
    max_abs_slope: float = Field(default=0.0025, gt=0)
    min_resistance_touches: int = Field(default=2, ge=2)
    prior_trend_sessions: int = Field(default=60, ge=20)
    ideal_prior_gain: float = Field(default=0.25, gt=0)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "prior_trend": 0.18,
            "horizontal_structure": 0.20,
            "depth": 0.16,
            "duration": 0.10,
            "resistance_consistency": 0.14,
            "compression": 0.12,
            "volume_character": 0.06,
            "relative_strength": 0.04,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_sessions <= self.min_sessions:
            raise ConfigError("max_sessions must exceed min_sessions")
        if self.ideal_depth_high >= self.max_depth:
            raise ConfigError("ideal depth must sit below max_depth")
        return self


class AscendingTriangleConfig(PatternSection):
    """Horizontal resistance with rising lows converging into it."""

    version: int = Field(default=1, ge=1)
    min_sessions: int = Field(default=18, ge=10)
    max_sessions: int = Field(default=120, ge=20)
    ideal_sessions_low: int = Field(default=25, ge=10)
    ideal_sessions_high: int = Field(default=70, ge=15)
    #: Resistance is "flat" within this scatter. Real triangles are not drawn
    #: with a ruler; demanding a perfect line finds none of them.
    max_resistance_scatter: float = Field(default=0.025, gt=0, lt=0.15)
    min_resistance_touches: int = Field(default=2, ge=2)
    min_rising_lows: int = Field(default=2, ge=2)
    #: Maximum sessions between consecutive resistance touches. Touches farther
    #: apart than this belong to different structures that happen to share a
    #: price, not to one triangle.
    max_touch_gap: int = Field(default=25, ge=5, le=120)
    #: Lower boundary slope, fraction of price per session. Must be positive --
    #: flat lows make it a rectangle, falling lows a descending triangle.
    min_lower_slope: float = Field(default=0.0008, gt=0)
    #: How much of the initial height the apex must have closed.
    ideal_convergence: float = Field(default=0.55, gt=0, lt=1)
    #: Fraction of pivots allowed to sit outside the boundaries. Real triangles
    #: have overshoots; zero tolerance rejects every genuine one.
    outlier_tolerance: float = Field(default=0.25, ge=0, lt=0.6)
    prior_trend_sessions: int = Field(default=60, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "resistance_flatness": 0.24,
            "rising_lows": 0.24,
            "convergence": 0.16,
            "touch_quality": 0.12,
            "duration": 0.08,
            "compression": 0.10,
            "prior_context": 0.06,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_sessions <= self.min_sessions:
            raise ConfigError("max_sessions must exceed min_sessions")
        return self


class PennantConfig(PatternSection):
    """A sharp impulse followed by a symmetrically converging pause.

    The distinction from a bull flag is not cosmetic. A flag's boundaries are
    roughly *parallel* -- it is a channel. A pennant's boundaries *converge* from
    both sides. `min_convergence` is what enforces that, and without it every
    pennant is also a flag and the family is redundant.
    """

    version: int = Field(default=1, ge=1)
    min_impulse_gain: float = Field(default=0.10, gt=0)
    min_impulse_sessions: int = Field(default=3, ge=2)
    max_impulse_sessions: int = Field(default=25, ge=3)
    min_sessions: int = Field(default=5, ge=3)
    max_sessions: int = Field(default=25, ge=5)
    #: Pennant duration as a multiple of the impulse. A pennant is brief; one
    #: lasting longer than its own pole has become a triangle.
    max_duration_ratio: float = Field(default=1.2, gt=0)
    #: Late height over early height. Must be well below 1 -- that is the
    #: pattern. A ratio near 1 is a flag, not a pennant.
    min_convergence: float = Field(default=0.35, gt=0, lt=1)
    ideal_convergence: float = Field(default=0.45, gt=0, lt=1)
    #: How symmetric the two boundaries must be. Upper falling and lower rising
    #: at wildly different rates is a wedge or a triangle.
    max_slope_asymmetry: float = Field(default=3.0, gt=1)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "impulse": 0.24,
            "convergence": 0.26,
            "symmetry": 0.14,
            "duration": 0.12,
            "compression": 0.14,
            "volume_contraction": 0.10,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_sessions <= self.min_sessions:
            raise ConfigError("max_sessions must exceed min_sessions")
        if self.ideal_convergence <= self.min_convergence:
            raise ConfigError("ideal_convergence must exceed min_convergence")
        return self


class CupHandleConfig(PatternSection):
    """Cup and Handle: a rounded decline and recovery, then a shallow drift.

    ``max_bottom_sharpness`` is what stops a V-shaped decline qualifying. A cup
    is *rounded* -- price spends time near the low rather than reversing at a
    point -- and the difference is what separates accumulation from a bounce.
    """

    version: int = Field(default=1, ge=1)
    min_cup_sessions: int = Field(default=25, ge=12)
    max_cup_sessions: int = Field(default=200, ge=30)
    ideal_cup_sessions_low: int = Field(default=35, ge=12)
    ideal_cup_sessions_high: int = Field(default=120, ge=25)
    min_cup_depth: float = Field(default=0.10, gt=0)
    max_cup_depth: float = Field(default=0.45, gt=0, lt=1)
    ideal_cup_depth_low: float = Field(default=0.14, gt=0)
    ideal_cup_depth_high: float = Field(default=0.32, gt=0)
    #: How level the two rims must be. A right rim far below the left is an
    #: incomplete recovery, not a cup.
    max_rim_asymmetry: float = Field(default=0.10, gt=0, lt=0.5)
    #: Discovery admits rims within ``max_rim_asymmetry * rim_search_multiple``
    #: and *scores* symmetry within that band. Searching at exactly the scoring
    #: tolerance would mean every discovered cup scored well on symmetry by
    #: construction, and an incomplete recovery would be silently discarded
    #: rather than found and marked down.
    rim_search_multiple: float = Field(default=2.0, ge=1.0, le=5.0)
    #: How close to the window's highest high a swing must sit to count as the
    #: *same* rim. Tight, because this decides where the cup starts: loosening
    #: it lets the left rim slide back into the advance that preceded the cup.
    rim_level_tolerance: float = Field(default=0.03, gt=0, lt=0.2)
    #: The "near the low" band, expressed as a fraction of the **cup's own
    #: depth** rather than of price. A fixed 10%-of-price band is a narrow
    #: sliver inside a 45% cup and the entire range of a 12% one, so a
    #: price-relative band measures cup depth twice and roundness not at all.
    bottom_band: float = Field(default=0.33, gt=0, le=1.0)
    min_bottom_fraction: float = Field(default=0.20, gt=0, lt=1)
    min_handle_sessions: int = Field(default=4, ge=2)
    max_handle_sessions: int = Field(default=35, ge=5)
    #: Handle depth as a fraction of cup depth. A handle deeper than a third of
    #: the cup is a second decline, not a shakeout.
    max_handle_depth_ratio: float = Field(default=0.40, gt=0, lt=1)
    ideal_handle_depth_ratio: float = Field(default=0.18, gt=0, lt=1)
    prior_trend_sessions: int = Field(default=60, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "prior_trend": 0.10,
            "cup_depth": 0.14,
            "cup_duration": 0.10,
            "bottom_roundness": 0.18,
            "rim_symmetry": 0.14,
            "handle_structure": 0.20,
            "volume_profile": 0.08,
            "relative_strength": 0.06,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_cup_sessions <= self.min_cup_sessions:
            raise ConfigError("max_cup_sessions must exceed min_cup_sessions")
        if self.ideal_cup_depth_high >= self.max_cup_depth:
            raise ConfigError("ideal cup depth must sit below max_cup_depth")
        if self.ideal_handle_depth_ratio >= self.max_handle_depth_ratio:
            raise ConfigError("ideal handle depth must sit below the maximum")
        return self


class HighTightFlagConfig(PatternSection):
    """High Tight Flag: a rare extreme-momentum structure.

    **Precision over recall, deliberately.** The classic definition is a 90-100%
    advance in eight weeks followed by a correction of no more than 25%. These
    defaults are close to that, and the brief is explicit that they must not be
    relaxed to generate more examples -- a high tight flag that fires on
    ordinary bull flags is not a rarer pattern, it is a duplicate detector.
    """

    version: int = Field(default=1, ge=1)
    #: The advance. Deliberately extreme.
    min_advance: float = Field(default=0.70, gt=0)
    ideal_advance: float = Field(default=1.00, gt=0)
    min_advance_sessions: int = Field(default=8, ge=3)
    max_advance_sessions: int = Field(default=50, ge=10)
    #: The consolidation must be *tight*. Beyond this it is an ordinary flag.
    max_consolidation_depth: float = Field(default=0.25, gt=0, lt=0.6)
    ideal_consolidation_depth: float = Field(default=0.12, gt=0)
    min_consolidation_sessions: int = Field(default=4, ge=2)
    max_consolidation_sessions: int = Field(default=25, ge=5)
    #: Liquidity floor in dollar volume. These structures appear
    #: disproportionately in thin securities where the advance is a quote
    #: artefact rather than accumulation.
    min_dollar_volume: float = Field(default=5_000_000, ge=0)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "advance_magnitude": 0.26,
            "advance_speed": 0.16,
            "advance_consistency": 0.14,
            "consolidation_tightness": 0.22,
            "consolidation_duration": 0.08,
            "liquidity": 0.08,
            "extreme_move_risk": 0.06,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.ideal_advance <= self.min_advance:
            raise ConfigError("ideal_advance must exceed min_advance")
        if self.ideal_consolidation_depth >= self.max_consolidation_depth:
            raise ConfigError("ideal consolidation depth must sit below the maximum")
        return self


class DoubleBottomConfig(PatternSection):
    """Two lows at a similar level separated by a rally."""

    version: int = Field(default=1, ge=1)
    #: How close the two lows must be, as a fraction of the first.
    max_low_divergence: float = Field(default=0.06, gt=0, lt=0.3)
    #: The rally between them, as a fraction of the first low. Too shallow and
    #: it is one low with noise, not two.
    min_intervening_rally: float = Field(default=0.06, gt=0)
    ideal_intervening_rally: float = Field(default=0.14, gt=0)
    min_separation_sessions: int = Field(default=8, ge=3)
    max_separation_sessions: int = Field(default=90, ge=15)
    #: A second low slightly *below* the first is constructive -- it shakes out
    #: stops before the reversal. Beyond this it is a continued decline.
    max_undercut: float = Field(default=0.04, ge=0, lt=0.2)
    #: The decline the pattern is reversing. Without it there is nothing to
    #: double-bottom out of. Measured peak-to-low.
    min_prior_decline: float = Field(default=0.12, gt=0)
    #: **Net** change across the lookback, which peak-to-low does not capture.
    #: A choppy range contains a high and a low 20% apart and has declined by
    #: nothing; without this a range yields a double bottom on every pair of
    #: lows it happens to put at the same level.
    min_net_decline: float = Field(default=0.06, ge=0)
    prior_trend_sessions: int = Field(default=60, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "low_symmetry": 0.24,
            "intervening_rally": 0.20,
            "prior_decline": 0.16,
            "timing": 0.12,
            "undercut_behaviour": 0.12,
            "volume_profile": 0.10,
            "relative_strength": 0.06,
        }
    )


class InverseHeadShouldersConfig(PatternSection):
    """Three lows, the middle one deepest, with a neckline above."""

    version: int = Field(default=1, ge=1)
    #: How much deeper the head must be than the shoulders.
    min_head_prominence: float = Field(default=0.04, gt=0)
    #: Shoulder-to-shoulder depth difference. Real patterns are asymmetric;
    #: demanding visual perfection rejects almost all of them.
    max_shoulder_asymmetry: float = Field(default=0.40, gt=0, lt=1)
    #: Timing asymmetry between the two halves, as a ratio.
    max_timing_asymmetry: float = Field(default=2.5, gt=1)
    min_sessions: int = Field(default=20, ge=10)
    max_sessions: int = Field(default=160, ge=25)
    min_prior_decline: float = Field(default=0.10, gt=0)
    #: As for the double bottom: peak-to-low says a range fell 20%, and net
    #: change is what says it did not.
    min_net_decline: float = Field(default=0.05, ge=0)
    prior_trend_sessions: int = Field(default=60, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "head_prominence": 0.22,
            "shoulder_symmetry": 0.20,
            "neckline_quality": 0.18,
            "timing_symmetry": 0.14,
            "prior_decline": 0.14,
            "volume_profile": 0.12,
        }
    )


class BaseOnBaseConfig(PatternSection):
    """Two consecutive bases at a similar level, the second no lower.

    The parameter that carries the family is ``max_ceiling_advance``. What
    distinguishes base-on-base from an ordinary sequence of bases is the
    *absence* of progress between them: price built a base, failed to advance
    meaningfully, and built another at the same level without giving ground. A
    pair separated by a 30% advance is a stair-step, which is a different and
    more common thing.
    """

    version: int = Field(default=1, ge=1)
    min_base_sessions: int = Field(default=12, ge=6)
    max_base_sessions: int = Field(default=80, ge=15)
    #: Each base must be shallow enough to be a base rather than a correction.
    max_base_depth: float = Field(default=0.22, gt=0, lt=0.6)
    ideal_base_depth: float = Field(default=0.11, gt=0)
    #: Advance of the second base's ceiling over the first's. Small is the
    #: point; large means these are two bases in a rising sequence.
    max_ceiling_advance: float = Field(default=0.15, gt=0, lt=0.6)
    ideal_ceiling_advance: float = Field(default=0.05, ge=0)
    #: The second base may not undercut the first's low by more than this. A
    #: lower base is a descending sequence, which is the opposite claim.
    max_low_undercut: float = Field(default=0.03, ge=0, lt=0.2)
    #: Sessions permitted between the end of the first base and the start of
    #: the second. They are consecutive; a long gap means an intervening move.
    max_gap_sessions: int = Field(default=12, ge=0)
    min_prior_gain: float = Field(default=0.15, gt=0)
    prior_trend_sessions: int = Field(default=60, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "prior_trend": 0.12,
            "first_base": 0.16,
            "second_base": 0.18,
            "ceiling_progression": 0.20,
            "low_progression": 0.16,
            "tightening": 0.10,
            "volume_character": 0.08,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.max_base_sessions <= self.min_base_sessions:
            raise ConfigError("max_base_sessions must exceed min_base_sessions")
        if self.ideal_ceiling_advance >= self.max_ceiling_advance:
            raise ConfigError("ideal ceiling advance must sit below the maximum")
        return self


class TightConsolidationConfig(PatternSection):
    """A short window that is tight **for this instrument**.

    **The dumping-ground problem.** Tight consolidation is the pattern most
    easily degraded into "this stock is not moving", and every sideways stock in
    the market qualifies under an absolute threshold. A utility that trades in a
    2% weekly range is not consolidating tightly; that is its ordinary state,
    and reporting it as a pattern says nothing about supply and demand.

    So every measurement here is **relative to the instrument's own recent
    past**: range against its own prior range, ATR against its own prior ATR,
    volume against its own baseline. A quiet stock that has always been quiet
    contracts against nothing and scores nothing. Combined with a required prior
    advance, the family describes what it is supposed to describe -- a pause
    that tightened -- rather than an absence of movement.
    """

    version: int = Field(default=1, ge=1)
    min_sessions: int = Field(default=5, ge=3)
    max_sessions: int = Field(default=25, ge=6)
    ideal_sessions_low: int = Field(default=6, ge=3)
    ideal_sessions_high: int = Field(default=15, ge=5)
    #: Window range as a fraction of the *prior* window's range. This is the
    #: measurement that makes the family mean anything.
    max_range_ratio: float = Field(default=0.70, gt=0, lt=1.5)
    ideal_range_ratio: float = Field(default=0.40, gt=0)
    #: Absolute depth ceiling, as a backstop. A window can contract by half and
    #: still be 30% wide if what preceded it was chaos.
    max_depth: float = Field(default=0.12, gt=0, lt=0.5)
    ideal_depth: float = Field(default=0.05, gt=0)
    #: ATR across the window over ATR before it.
    max_atr_ratio: float = Field(default=0.85, gt=0, lt=1.5)
    #: The lookback the window is compared against. Longer is a more stable
    #: reference and slower to notice a genuine regime change.
    reference_sessions: int = Field(default=30, ge=10)
    min_prior_gain: float = Field(default=0.10, gt=0)
    #: Must comfortably exceed ``reference_sessions``: the reference window sits
    #: between the advance and the tight window by construction, so a lookback
    #: barely longer than it measures the reference and reports no prior trend.
    prior_trend_sessions: int = Field(default=80, ge=20)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "range_contraction": 0.26,
            "absolute_tightness": 0.14,
            "volatility_contraction": 0.18,
            "close_clustering": 0.12,
            "volume_dryup": 0.12,
            "prior_trend": 0.12,
            "duration": 0.06,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.ideal_range_ratio >= self.max_range_ratio:
            raise ConfigError("ideal range ratio must sit below the maximum")
        if self.ideal_depth >= self.max_depth:
            raise ConfigError("ideal depth must sit below the maximum")
        if not (
            self.min_sessions
            <= self.ideal_sessions_low
            <= self.ideal_sessions_high
            <= self.max_sessions
        ):
            raise ConfigError("ideal duration band must sit inside [min_sessions, max_sessions]")
        if self.prior_trend_sessions <= self.reference_sessions + self.max_sessions:
            raise ConfigError(
                "prior_trend_sessions must exceed reference_sessions + max_sessions, or the "
                "prior-trend lookback measures the reference window rather than the advance"
            )
        return self


class BreakoutRetestConfig(PatternSection):
    """A level that was crossed, returned to, and so far held.

    **Phase 4 boundary.** This family is defined by a breakout having already
    happened, which makes it the one place where the temptation to say something
    about breakout *validity* is strongest. It does not. Every measurement here
    is geometric: a level existed, price closed above it, price came back to it,
    price is currently above or below it. Whether any of that constitutes a
    confirmed breakout is Phase 5's question, and this module has no vocabulary
    for answering it.
    """

    version: int = Field(default=1, ge=1)
    #: How far above the level a close must sit to count as having crossed it.
    #: A geometric observation, not a confirmation rule.
    break_buffer: float = Field(default=0.005, gt=0, lt=0.1)
    #: How close the pullback must come to the level to count as a retest. Wider
    #: and every shallow dip after a breakout is a retest.
    retest_tolerance: float = Field(default=0.04, gt=0, lt=0.2)
    ideal_retest_tolerance: float = Field(default=0.015, gt=0)
    #: Below the level by more than this and the retest failed.
    fail_tolerance: float = Field(default=0.03, gt=0, lt=0.2)
    #: Closes above the level between the break and the excursion peak. You
    #: retest a *breakout*, and a single close above a line is not one -- it is
    #: a poke. Definitional rather than a tuning knob.
    min_sessions_above: int = Field(default=3, ge=1)
    min_sessions_to_retest: int = Field(default=2, ge=1)
    max_sessions_to_retest: int = Field(default=30, ge=5)
    #: Sessions after the retest low. Zero means the retest is the last bar and
    #: nothing is yet known about whether it held.
    min_hold_sessions: int = Field(default=2, ge=0)
    #: The level itself must be worth retesting. Two touches inside a fortnight
    #: is a coincidence; a random walk supplies those by the dozen, and a
    #: detector built on them finds textbook retests in pure noise.
    min_level_touches: int = Field(default=3, ge=2)
    #: Sessions between the level's first and last touch. This is what makes it
    #: *resistance* rather than two highs that happened to land together.
    min_level_span: int = Field(default=20, ge=5)
    #: Fraction of sessions before the break that closed below the level. A
    #: level price spent half its time above was never resistance.
    min_below_fraction: float = Field(default=0.85, gt=0, le=1)
    #: The break must be a real excursion, measured in ATRs so a quiet stock and
    #: a volatile one are held to the same scale-free standard.
    min_break_atr: float = Field(default=1.0, ge=0)
    level_lookback: int = Field(default=90, ge=30)
    atr_period: int = Field(default=14, ge=2)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "level_quality": 0.22,
            "break_observation": 0.16,
            "retest_proximity": 0.24,
            "hold_behaviour": 0.20,
            "timing": 0.08,
            "volume_character": 0.10,
        }
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.ideal_retest_tolerance >= self.retest_tolerance:
            raise ConfigError("ideal retest tolerance must sit below the maximum")
        if self.max_sessions_to_retest <= self.min_sessions_to_retest:
            raise ConfigError("max_sessions_to_retest must exceed the minimum")
        return self


class PatternEngineConfig(PatternSection):
    """Top-level pattern configuration, shared plus per-detector.

    ``max_candidates_per_pattern`` is a look-ahead control as much as a
    performance one. An unbounded search over every (flagpole, consolidation)
    split and a "keep the best" rule is how pattern-selection bias enters: on
    historical data the best-scoring window is the one that happens to have
    worked out. Bounding the search and stating the selection rule keeps the
    choice explicit.
    """

    swings: SwingConfig = Field(default_factory=SwingConfig)
    states: PatternStateConfig = Field(default_factory=PatternStateConfig)
    bull_flag: BullFlagConfig = Field(default_factory=BullFlagConfig)
    vcp: VcpConfig = Field(default_factory=VcpConfig)
    flat_base: FlatBaseConfig = Field(default_factory=FlatBaseConfig)
    ascending_triangle: AscendingTriangleConfig = Field(default_factory=AscendingTriangleConfig)
    pennant: PennantConfig = Field(default_factory=PennantConfig)
    cup_handle: CupHandleConfig = Field(default_factory=CupHandleConfig)
    high_tight_flag: HighTightFlagConfig = Field(default_factory=HighTightFlagConfig)
    double_bottom: DoubleBottomConfig = Field(default_factory=DoubleBottomConfig)
    inverse_head_shoulders: InverseHeadShouldersConfig = Field(
        default_factory=InverseHeadShouldersConfig
    )
    base_on_base: BaseOnBaseConfig = Field(default_factory=BaseOnBaseConfig)
    tight_consolidation: TightConsolidationConfig = Field(default_factory=TightConsolidationConfig)
    breakout_retest: BreakoutRetestConfig = Field(default_factory=BreakoutRetestConfig)

    #: Detectors to run. A detector absent from this list is not merely skipped
    #: -- its features never enter the dataset, which keeps the stored pattern
    #: set reproducible from the config digest alone.
    enabled_detectors: tuple[str, ...] = (
        "bull_flag",
        "vcp",
        "flat_base",
        "ascending_triangle",
        "pennant",
        "cup_handle",
        "high_tight_flag",
        "double_bottom",
        "inverse_head_shoulders",
        "base_on_base",
        "tight_consolidation",
        "breakout_retest",
    )
    max_candidates_per_pattern: int = Field(default=24, ge=1, le=200)
    #: Instances below this quality are discarded rather than stored. Low, so
    #: that near-misses remain visible for false-positive analysis; the screen
    #: applies its own, higher floor.
    min_quality_to_report: float = Field(default=25.0, ge=0, le=100)
