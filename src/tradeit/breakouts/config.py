"""Breakout-detection and confirmation parameters.

Same rule as the pattern engine (ADR-0008): every number the engine uses lives
here, and every curve shape with it. Same warning, too, and it is worth
restating because Phase 5 is where the temptation is strongest:

**Nothing here was fitted.** Phase 5 is explicitly forbidden from searching
historical outcomes for the best relative-volume threshold, the best breakout
percentage, the best confirmation window or the best retest tolerance. Every
default below is a transparent structural choice with its reasoning attached,
and Phase 9 — which has a walk-forward harness and is allowed to fit — will
argue against these, not inherit them as though they had been measured.

The one thing that *is* deliberately open-ended is the confirmation policy.
Three profiles ship (CONSERVATIVE, BALANCED, AGGRESSIVE); none is privileged,
none is the default in the sense of being recommended, and the names describe
how much evidence they demand, not how much money to put behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeit.errors import ConfigError


class BreakoutSection(BaseModel):
    """Base for breakout configuration: frozen, and typos are errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ToleranceConfig(BreakoutSection):
    """How wide the zone around a nominal resistance level is.

    Penny-exact resistance is a fiction. A level drawn through three swing highs
    that printed at 147.28, 147.33 and 147.30 is "about 147.30", and an engine
    that treats 147.31 as a breakout and 147.29 as a test is measuring its own
    rounding rather than the market's behaviour.

    Three terms, combined by taking the largest:

    * a **percentage floor**, so a level on a $4 stock is not resolved to the
      tick;
    * an **ATR multiple**, so a name that moves 6% a day is not judged against
      the same band as one that moves 0.6%;
    * a widening for **low-confidence boundaries**. A level defined by a single
      extreme bar deserves a wider zone than one defined by five touches over
      three months, because the uncertainty is genuinely in the level itself.

    The widening is stated as a multiplier applied at confidence zero and
    interpolated to 1.0 at confidence 100.
    """

    min_tolerance_pct: float = Field(default=0.0015, gt=0, lt=0.05)
    atr_multiple: float = Field(default=0.10, ge=0, le=1.0)
    max_tolerance_pct: float = Field(default=0.02, gt=0, lt=0.2)
    #: Multiplier on the tolerance at boundary confidence 0.
    low_confidence_widening: float = Field(default=1.6, ge=1.0, le=4.0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.min_tolerance_pct >= self.max_tolerance_pct:
            raise ConfigError("min_tolerance_pct must be below max_tolerance_pct")
        return self


class ApproachConfig(BreakoutSection):
    """When price counts as approaching, and when as testing, a boundary.

    Both distances are expressed in ATR rather than percent so the same numbers
    mean the same thing across instruments. The percentage cap exists for the
    pathological case: a name whose ATR has collapsed to nothing would otherwise
    report "approaching" from 40% away.
    """

    #: Within this many ATR of the boundary: APPROACHING.
    approach_atr: float = Field(default=2.5, gt=0, le=20)
    #: Hard cap on the approach zone, whatever ATR says.
    approach_max_pct: float = Field(default=0.12, gt=0, lt=1.0)
    #: Within this many ATR: TESTING_RESISTANCE. Distinct from a penetration.
    testing_atr: float = Field(default=0.4, gt=0, le=5)
    #: Sessions used to measure the rate of approach and near-boundary
    #: compression. Short, because both are statements about recent behaviour.
    approach_lookback: int = Field(default=10, ge=3, le=60)
    #: Sessions of range history the compression measure compares against.
    compression_baseline: int = Field(default=30, ge=5, le=250)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.testing_atr >= self.approach_atr:
            raise ConfigError("testing_atr must be inside the approach zone")
        return self


class PenetrationConfig(BreakoutSection):
    """How far through the boundary counts as what.

    The asymmetry here is the point. A close 0.05 ATR above the level has barely
    cleared the tolerance zone; a close 3 ATR above it has cleared it so
    convincingly that the *entry* is now poor. Both facts are real, and the
    engine reports them as two numbers rather than one curve that peaks in the
    middle — because "strong penetration" and "extended" are different claims
    and a consumer may weigh them differently.
    """

    #: Penetration in ATR at which PENETRATION_SCORE reaches 100.
    full_penetration_atr: float = Field(default=0.75, gt=0, le=10)
    #: Penetration at which it is zero. Above the tolerance zone by definition.
    zero_penetration_atr: float = Field(default=0.0, ge=0, le=5)
    #: Extension is scored separately, and starts counting from here.
    extension_free_atr: float = Field(default=1.0, ge=0, le=10)
    #: Extension at which EXTENSION_SCORE reaches zero: as far above the level
    #: as this, a breakout is a completed move rather than a beginning one.
    extension_max_atr: float = Field(default=4.0, gt=0, le=20)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.zero_penetration_atr >= self.full_penetration_atr:
            raise ConfigError("zero_penetration_atr must be below full_penetration_atr")
        if self.extension_free_atr >= self.extension_max_atr:
            raise ConfigError("extension_free_atr must be below extension_max_atr")
        return self


class CandleQualityConfig(BreakoutSection):
    """What a convincing breakout bar looks like.

    Close location within the bar's range is the single most informative number
    here and the reasoning is mechanical rather than folkloric: a bar that
    traded up through a level and closed at its low means every buyer above the
    level is losing money at the close. That is a different market than a bar
    that closed on its high, whatever the two bars' penetration figures say.
    """

    #: Close location (0 = low, 1 = high) at which CLOSE_STRENGTH_SCORE is 100.
    close_location_full: float = Field(default=0.85, gt=0, le=1.0)
    #: Close location at which it is zero.
    close_location_zero: float = Field(default=0.25, ge=0, lt=1.0)
    #: Body as a fraction of true range at which the body term is 100.
    body_fraction_full: float = Field(default=0.6, gt=0, le=1.0)
    #: Upper wick as a fraction of true range at which the wick term is zero.
    upper_wick_zero: float = Field(default=0.5, gt=0, le=1.0)
    #: True range over ATR at which the expansion term is 100. A breakout bar
    #: that is quieter than average is not evidence of anything.
    range_expansion_full: float = Field(default=1.5, gt=0, le=10)
    weight_close_location: float = Field(default=0.45, ge=0, le=1)
    weight_body: float = Field(default=0.25, ge=0, le=1)
    weight_upper_wick: float = Field(default=0.15, ge=0, le=1)
    weight_range_expansion: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.close_location_zero >= self.close_location_full:
            raise ConfigError("close_location_zero must be below close_location_full")
        total = (
            self.weight_close_location
            + self.weight_body
            + self.weight_upper_wick
            + self.weight_range_expansion
        )
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"candle-quality weights sum to {total}, not 1.0")
        return self


class VolumeConfirmationConfig(BreakoutSection):
    """Volume expectations, per family where the difference is justified.

    **There is deliberately no single universal threshold.** A high tight flag
    breaking out of a two-week consolidation after a 100% advance is a different
    volume proposition from a flat base whose whole character is quiet drift,
    and applying one number to both would encode a preference for one family's
    behaviour as though it were a law.

    ``per_family_full`` overrides ``relative_volume_full`` by detector name.
    Families absent from the mapping use the shared default — absence means "no
    justified difference", not "not thought about".
    """

    #: Sessions in the average that relative volume is measured against.
    average_period: int = Field(default=20, ge=5, le=250)
    #: A longer median, reported alongside the mean because a single 10x day
    #: inside the averaging window drags the mean and not the median.
    median_period: int = Field(default=50, ge=5, le=250)
    #: Relative volume at which RELATIVE_VOLUME_SCORE reaches 100.
    relative_volume_full: float = Field(default=1.8, gt=0, le=20)
    #: Relative volume at which it is zero. Below average volume on a breakout
    #: is a real negative, not merely a neutral.
    relative_volume_zero: float = Field(default=0.7, ge=0, le=5)
    #: Per-detector overrides of ``relative_volume_full``.
    per_family_full: Mapping[str, float] = Field(
        default_factory=lambda: {
            # An explosive continuation pattern; the volume signature that
            # matters is expansion off an already-elevated base.
            "high_tight_flag": 2.2,
            # A quiet structure by construction. Demanding flag-like volume
            # would mean demanding that it stop being a flat base.
            "flat_base": 1.5,
            # The whole VCP thesis is dry-up then expansion, so the expansion
            # leg is where the evidence lives.
            "vcp": 2.0,
            "tight_consolidation": 1.5,
            # A retest structure breaks out from a level it has already cleared
            # once; the second clearance is not usually the volume event.
            "breakout_retest": 1.4,
        }
    )
    #: Breakout volume against the consolidation's own average: the comparison
    #: that survives a stock whose whole volume profile shifted.
    consolidation_expansion_full: float = Field(default=2.0, gt=0, le=20)
    #: Breakout volume against the impulse/flagpole leg's average. Reaching the
    #: volume of the move that created the pattern is strong evidence.
    impulse_comparison_full: float = Field(default=1.0, gt=0, le=10)
    #: Post-breakout volume against the *pre-breakout* average, scoring 100.
    #:
    #: Measured against the pre-breakout baseline rather than against the
    #: breakout bar itself, which is the obvious implementation and is perverse:
    #: dividing by a small breakout volume rewards a low-volume breakout for
    #: having had little volume to fall from. The baseline does not move with
    #: the thing being judged.
    persistence_full: float = Field(default=1.0, gt=0, le=10)
    #: The ratio at which post-breakout volume counts as having collapsed.
    persistence_zero: float = Field(default=0.4, ge=0, le=5)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.relative_volume_zero >= self.relative_volume_full:
            raise ConfigError("relative_volume_zero must be below relative_volume_full")
        if self.persistence_zero >= self.persistence_full:
            raise ConfigError("persistence_zero must be below persistence_full")
        for name, value in self.per_family_full.items():
            if value <= self.relative_volume_zero:
                raise ConfigError(
                    f"per-family relative volume for {name!r} ({value}) is at or below "
                    f"the zero point {self.relative_volume_zero}"
                )
        return self

    def full_for(self, family: str | None) -> float:
        """The relative-volume figure that scores 100 for this family."""
        if family is None:
            return self.relative_volume_full
        return self.per_family_full.get(family, self.relative_volume_full)


class IntradayVolumeConfig(BreakoutSection):
    """Partial-session volume, and the honesty controls around it.

    800,000 shares at 10:15 against a 2,000,000-share daily average is not
    "relative volume 0.4". It is 40% of a full day's volume in the first 45
    minutes, which is heavy. Getting this right needs a time-of-day curve, and
    getting it *honest* needs the projection to be labelled as a projection
    everywhere it travels.

    ``min_elapsed_fraction`` is the guard that matters. Dividing by 2% of a
    session multiplies the noise in the first two minutes by fifty, and a
    projection built on that is a number with no information in it. Below the
    threshold the engine reports the observed volume and marks the projection
    unavailable rather than emitting a figure it does not believe.
    """

    enabled: bool = True
    #: Prior sessions the cumulative time-of-day curve is built from. Prior
    #: only: using the current session's later volume to normalise its earlier
    #: volume is the exact leak this whole section exists to prevent.
    curve_lookback_sessions: int = Field(default=20, ge=5, le=250)
    #: Below this fraction of the session elapsed, no projection is reported.
    min_elapsed_fraction: float = Field(default=0.08, gt=0, lt=1.0)
    #: Buckets the session is divided into for the curve.
    buckets: int = Field(default=13, ge=2, le=390)


class CloseConfirmationConfig(BreakoutSection):
    """What a qualifying close is, and how many are wanted.

    A "qualifying close" is a completed bar closing above the breakout
    threshold — the top of the tolerance zone, not the nominal level. The
    distinction is the difference between a close that cleared the level and one
    that landed inside the ambiguity around it.
    """

    #: Closes above the threshold needed before CLOSED_ABOVE is reached. One,
    #: because "closed above" is an observation; how many are *required* is the
    #: confirmation profile's business, not this state's.
    min_qualifying_closes: int = Field(default=1, ge=1, le=10)
    #: Confirmation windows the engine can be asked for. No single value is
    #: privileged; profiles pick from these and Phase 9 may find the choice
    #: matters more than any threshold here.
    windows: tuple[int, ...] = (1, 2, 3, 5)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not self.windows:
            raise ConfigError("at least one confirmation window is required")
        if sorted(self.windows) != list(self.windows):
            raise ConfigError("confirmation windows must be sorted")
        if self.windows[0] < 1:
            raise ConfigError("confirmation windows must be at least one bar")
        return self


class FollowThroughConfig(BreakoutSection):
    """Progress after the breakout bar, measured separately from it.

    The separation is deliberate and the docstring says so because the merge is
    tempting: a strong breakout bar and strong follow-through are both good, so
    why not one number? Because a breakout that has not yet had time to follow
    through is not the same as one that has had time and failed to, and a single
    score cannot distinguish "no evidence yet" from "evidence against".
    """

    #: Progress above the breakout close, in ATR, scoring 100.
    progress_full_atr: float = Field(default=1.0, gt=0, le=10)
    #: Adverse excursion below the breakout close, in ATR, scoring zero.
    adverse_zero_atr: float = Field(default=1.0, gt=0, le=10)
    #: Range expansion (mean true range after / ATR at breakout) scoring 100.
    expansion_full: float = Field(default=1.2, gt=0, le=10)
    #: Volume in the follow-through window over the breakout bar's volume at
    #: which the volume term is 100. Below 1.0 on purpose: follow-through does
    #: not require repeating the breakout day's volume, only not collapsing.
    volume_hold_full: float = Field(default=0.7, gt=0, le=5)
    weight_progress: float = Field(default=0.4, ge=0, le=1)
    weight_adverse: float = Field(default=0.25, ge=0, le=1)
    weight_expansion: float = Field(default=0.15, ge=0, le=1)
    weight_volume: float = Field(default=0.2, ge=0, le=1)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        total = (
            self.weight_progress + self.weight_adverse + self.weight_expansion + self.weight_volume
        )
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"follow-through weights sum to {total}, not 1.0")
        return self


class RetestConfig(BreakoutSection):
    """Revisiting the former resistance, judged against the original level.

    ``max_undercut_atr`` is the number most likely to be argued about, so the
    reasoning is explicit: an undercut is not a failure, it is a test. Price
    that dips 0.3 ATR below a level it broke three days ago and closes back
    above it has demonstrated the level, not broken it. Volatility-aware,
    because a fixed percentage would call the same behaviour a hold on a quiet
    name and a failure on a fast one.
    """

    #: Sessions after the breakout within which a pullback counts as a retest
    #: of *this* breakout rather than as unrelated later weakness.
    max_sessions_to_retest: int = Field(default=20, ge=1, le=120)
    #: Distance below the breakout close, in ATR, at which price is considered
    #: to be retesting rather than advancing.
    retest_trigger_atr: float = Field(default=0.5, gt=0, le=10)
    #: Undercut of the nominal level, in ATR, still consistent with a hold.
    max_undercut_atr: float = Field(default=0.6, gt=0, le=10)
    #: Sessions price may spend below the level during a retest before the hold
    #: is no longer credible.
    max_sessions_below: int = Field(default=3, ge=1, le=20)
    #: Volume during the retest over the breakout bar's volume at which the
    #: contraction term scores 100. Lower is better: a pullback on heavy volume
    #: is distribution, on light volume it is disinterest.
    volume_contraction_full: float = Field(default=0.45, gt=0, le=5)
    #: Range during the retest over the ATR at breakout, scoring 100.
    range_contraction_full: float = Field(default=0.6, gt=0, le=5)
    #: Closes back above the level needed for RETEST_CONFIRMED.
    recovery_closes: int = Field(default=1, ge=1, le=10)
    weight_undercut: float = Field(default=0.3, ge=0, le=1)
    weight_volume: float = Field(default=0.25, ge=0, le=1)
    weight_range: float = Field(default=0.15, ge=0, le=1)
    weight_recovery: float = Field(default=0.3, ge=0, le=1)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        total = self.weight_undercut + self.weight_volume + self.weight_range + self.weight_recovery
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"retest weights sum to {total}, not 1.0")
        return self


class RejectionConfig(BreakoutSection):
    """Immediate rejection at the boundary.

    Rejection is about *this bar*: price went through and came back. It is not a
    failed breakout, which is a claim about a structure over several sessions,
    and conflating them would let a single wicky bar terminate an event that the
    next bar resolves cleanly.
    """

    #: Upper wick as a fraction of true range above which rejection is scored.
    wick_fraction: float = Field(default=0.5, gt=0, le=1.0)
    #: Close location below which a penetrating bar counts as rejected.
    close_location_ceiling: float = Field(default=0.35, gt=0, lt=1.0)
    #: Relative volume above which a rejection is scored as heavier.
    heavy_volume: float = Field(default=1.5, gt=0, le=20)
    #: Consecutive rejections at the same boundary before the event is closed
    #: rather than left open for another test.
    max_rejections: int = Field(default=3, ge=1, le=10)


class FailureConfig(BreakoutSection):
    """What ends a breakout as failed rather than merely disappointing."""

    #: Closes back below the *lower* edge of the tolerance zone that constitute
    #: a decisive return inside the pattern.
    closes_back_inside: int = Field(default=2, ge=1, le=10)
    #: A single close this far below the nominal level, in ATR, is decisive on
    #: its own. Set above ``RetestConfig.max_undercut_atr`` so an ordinary
    #: retest cannot trip it.
    decisive_close_atr: float = Field(default=1.2, gt=0, le=10)
    #: Breaking the breakout bar's low is a distinct, weaker condition: it can
    #: happen inside a legitimate retest, so on its own it does not fail the
    #: event unless it is also below the tolerance zone.
    require_below_zone_with_bar_low: bool = True
    #: Adverse excursion from the breakout close, in ATR, that fails the event
    #: however price closes.
    max_adverse_atr: float = Field(default=2.5, gt=0, le=20)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        return self


class ExpirationConfig(BreakoutSection):
    """Running out of time, which is not the same as failing.

    An event that approached a level for forty sessions and never took it did
    not fail; nothing happened. Recording that as a failure would inflate every
    failure rate with non-events.
    """

    #: Sessions in APPROACHING/TESTING without a penetration.
    max_sessions_approaching: int = Field(default=40, ge=1, le=250)
    #: Sessions in CONFIRMATION_PENDING without resolving either way.
    max_sessions_pending: int = Field(default=15, ge=1, le=120)
    #: Sessions a confirmed event stays monitored before it is closed as
    #: resolved. Not a failure and not an outcome — the engine simply stops
    #: having anything to say about it.
    max_sessions_confirmed: int = Field(default=30, ge=1, le=250)
    #: Sessions in a retest before the retest itself is out of time.
    max_sessions_retesting: int = Field(default=25, ge=1, le=120)
    #: Cap expressed as a multiple of the underlying pattern's own length, so a
    #: three-week flag and a nine-month cup are not given the same clock.
    pattern_length_multiple: float = Field(default=1.0, gt=0, le=10)


class BreakoutQualityWeights(BreakoutSection):
    """Weights over the breakout-quality components.

    Transparent, configurable and versioned — and *not* fitted. Bump
    ``BREAKOUT_SCORER_VERSION`` when these change, so a stored score says which
    weighting produced it.
    """

    boundary_quality: float = Field(default=0.16, ge=0, le=1)
    penetration: float = Field(default=0.16, ge=0, le=1)
    candle_quality: float = Field(default=0.22, ge=0, le=1)
    volume_confirmation: float = Field(default=0.24, ge=0, le=1)
    relative_strength: float = Field(default=0.10, ge=0, le=1)
    market_context: float = Field(default=0.06, ge=0, le=1)
    sector_context: float = Field(default=0.06, ge=0, le=1)

    def as_mapping(self) -> dict[str, float]:
        return {
            "boundary_quality": self.boundary_quality,
            "penetration": self.penetration,
            "candle_quality": self.candle_quality,
            "volume_confirmation": self.volume_confirmation,
            "relative_strength": self.relative_strength,
            "market_context": self.market_context,
            "sector_context": self.sector_context,
        }

    @model_validator(mode="after")
    def _validate(self) -> Self:
        total = sum(self.as_mapping().values())
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"breakout-quality weights sum to {total}, not 1.0")
        return self


class ConfirmationWeights(BreakoutSection):
    """Weights over the confirmation components.

    Note what is *absent*: nothing about the breakout bar itself. Confirmation
    is evidence accumulated after the event, and folding the event's own quality
    back in would make the two scores correlated by construction and destroy the
    distinction item 18 of the brief exists to protect.
    """

    close_acceptance: float = Field(default=0.30, ge=0, le=1)
    follow_through: float = Field(default=0.30, ge=0, le=1)
    volume_persistence: float = Field(default=0.15, ge=0, le=1)
    retest_quality: float = Field(default=0.15, ge=0, le=1)
    relative_strength_after: float = Field(default=0.10, ge=0, le=1)

    def as_mapping(self) -> dict[str, float]:
        return {
            "close_acceptance": self.close_acceptance,
            "follow_through": self.follow_through,
            "volume_persistence": self.volume_persistence,
            "retest_quality": self.retest_quality,
            "relative_strength_after": self.relative_strength_after,
        }

    @model_validator(mode="after")
    def _validate(self) -> Self:
        total = sum(self.as_mapping().values())
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"confirmation weights sum to {total}, not 1.0")
        return self


class ProfileConfig(BreakoutSection):
    """One confirmation policy: how much evidence before CONFIRMED.

    **These names describe evidence requirements, not risk appetite.**
    AGGRESSIVE does not mean "take a bigger position"; it means "accept a first
    strong close as sufficient". Position sizing is Phase 8's and cannot be
    inferred from anything here.
    """

    name: str = Field(min_length=1, max_length=32)
    #: Qualifying closes above the threshold required.
    required_closes: int = Field(default=1, ge=1, le=20)
    #: Bars of follow-through examined before the momentum path can confirm.
    follow_through_window: int = Field(default=1, ge=0, le=20)
    #: Minimum FOLLOW_THROUGH_SCORE for the momentum path.
    min_follow_through: float = Field(default=50.0, ge=0, le=100)
    #: Minimum relative volume on the breakout bar for the momentum path.
    min_relative_volume: float = Field(default=1.3, ge=0, le=20)
    #: Minimum BREAKOUT_QUALITY_SCORE for the momentum path.
    min_breakout_quality: float = Field(default=60.0, ge=0, le=100)
    #: Closes above the level for the acceptance path.
    acceptance_closes: int = Field(default=4, ge=1, le=30)
    #: Maximum range (as a multiple of ATR at breakout) during those closes for
    #: the acceptance path — the "volatility contracts above resistance" leg.
    acceptance_max_range: float = Field(default=1.0, gt=0, le=10)
    #: Minimum RETEST_QUALITY_SCORE for the retest path.
    min_retest_quality: float = Field(default=55.0, ge=0, le=100)
    #: Paths this profile will accept, in the order it tries them.
    paths: tuple[str, ...] = ("momentum", "retest", "acceptance")
    #: Minimum evidence coverage below which the profile declines to confirm at
    #: all. Not a trading threshold: it is the point below which the
    #: confirmation score is computed from too little to mean what it says.
    min_evidence_coverage: float = Field(default=50.0, ge=0, le=100)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        allowed = {"momentum", "retest", "acceptance"}
        unknown = sorted(set(self.paths) - allowed)
        if unknown:
            raise ConfigError(f"unknown confirmation path(s): {unknown}")
        if not self.paths:
            raise ConfigError("a profile with no confirmation paths can never confirm")
        return self


def _default_profiles() -> dict[str, ProfileConfig]:
    return {
        "conservative": ProfileConfig(
            name="conservative",
            required_closes=2,
            follow_through_window=3,
            min_follow_through=65.0,
            min_relative_volume=1.5,
            min_breakout_quality=70.0,
            acceptance_closes=6,
            acceptance_max_range=0.8,
            min_retest_quality=65.0,
            min_evidence_coverage=65.0,
        ),
        "balanced": ProfileConfig(
            name="balanced",
            required_closes=1,
            follow_through_window=2,
            min_follow_through=55.0,
            min_relative_volume=1.3,
            min_breakout_quality=60.0,
            acceptance_closes=4,
            acceptance_max_range=1.0,
            min_retest_quality=55.0,
            min_evidence_coverage=50.0,
        ),
        "aggressive": ProfileConfig(
            name="aggressive",
            required_closes=1,
            follow_through_window=0,
            min_follow_through=0.0,
            min_relative_volume=1.2,
            min_breakout_quality=55.0,
            acceptance_closes=3,
            acceptance_max_range=1.3,
            min_retest_quality=45.0,
            min_evidence_coverage=40.0,
        ),
    }


class BreakoutEngineConfig(BreakoutSection):
    """Everything the breakout engine reads.

    ``monitored_states`` is the active-pattern filter. Evaluating every pattern
    ever detected against today's bar is waste, and the waste is not the only
    cost — a long-expired structure's resistance is not a level anyone is
    trading against, so scoring a cross of it manufactures events.
    """

    tolerance: ToleranceConfig = Field(default_factory=ToleranceConfig)
    approach: ApproachConfig = Field(default_factory=ApproachConfig)
    penetration: PenetrationConfig = Field(default_factory=PenetrationConfig)
    candle: CandleQualityConfig = Field(default_factory=CandleQualityConfig)
    volume: VolumeConfirmationConfig = Field(default_factory=VolumeConfirmationConfig)
    intraday: IntradayVolumeConfig = Field(default_factory=IntradayVolumeConfig)
    closes: CloseConfirmationConfig = Field(default_factory=CloseConfirmationConfig)
    follow_through: FollowThroughConfig = Field(default_factory=FollowThroughConfig)
    retest: RetestConfig = Field(default_factory=RetestConfig)
    rejection: RejectionConfig = Field(default_factory=RejectionConfig)
    failure: FailureConfig = Field(default_factory=FailureConfig)
    expiration: ExpirationConfig = Field(default_factory=ExpirationConfig)
    quality_weights: BreakoutQualityWeights = Field(default_factory=BreakoutQualityWeights)
    confirmation_weights: ConfirmationWeights = Field(default_factory=ConfirmationWeights)
    profiles: Mapping[str, ProfileConfig] = Field(default_factory=_default_profiles)
    #: Which profile the engine uses when the caller does not name one. Not a
    #: recommendation — it is the middle of three, and Phase 9 decides.
    active_profile: str = "balanced"

    #: ATR period used for every ATR-relative measurement here. Matches the
    #: pattern engine's so the two layers speak the same units.
    atr_period: int = Field(default=14, ge=2, le=100)

    #: Pattern states whose resistance the engine will monitor. FORMING is
    #: excluded: its boundary is still resolving, so a "breakout" of it is a
    #: breakout of a guess.
    monitored_states: tuple[str, ...] = (
        "mature",
        "near_breakout",
        "broken_out_unconfirmed",
    )
    #: Sessions after a pattern's last observation that its boundary is still
    #: considered live.
    max_pattern_staleness: int = Field(default=25, ge=1, le=250)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.active_profile not in self.profiles:
            raise ConfigError(
                f"active_profile {self.active_profile!r} is not among {sorted(self.profiles)}"
            )
        for key, profile in self.profiles.items():
            if key != profile.name:
                raise ConfigError(f"profile keyed {key!r} is named {profile.name!r}")
        if self.retest.max_undercut_atr >= self.failure.decisive_close_atr:
            raise ConfigError(
                "max_undercut_atr must stay below decisive_close_atr, otherwise an "
                "ordinary retest fails the event it is testing"
            )
        return self

    def profile(self, name: str | None = None) -> ProfileConfig:
        key = name or self.active_profile
        if key not in self.profiles:
            raise ConfigError(f"no confirmation profile named {key!r}")
        return self.profiles[key]


__all__ = [
    "ApproachConfig",
    "BreakoutEngineConfig",
    "BreakoutQualityWeights",
    "BreakoutSection",
    "CandleQualityConfig",
    "CloseConfirmationConfig",
    "ConfirmationWeights",
    "ExpirationConfig",
    "FailureConfig",
    "FollowThroughConfig",
    "IntradayVolumeConfig",
    "PenetrationConfig",
    "ProfileConfig",
    "RejectionConfig",
    "RetestConfig",
    "ToleranceConfig",
    "VolumeConfirmationConfig",
]
