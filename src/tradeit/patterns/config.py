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

    #: Detectors to run. A detector absent from this list is not merely skipped
    #: -- its features never enter the dataset, which keeps the stored pattern
    #: set reproducible from the config digest alone.
    enabled_detectors: tuple[str, ...] = ("bull_flag", "vcp")
    max_candidates_per_pattern: int = Field(default=24, ge=1, le=200)
    #: Instances below this quality are discarded rather than stored. Low, so
    #: that near-misses remain visible for false-positive analysis; the screen
    #: applies its own, higher floor.
    min_quality_to_report: float = Field(default=25.0, ge=0, le=100)
