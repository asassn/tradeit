"""Volatility Contraction Pattern.

**Structural definition, written before implementation.**

A VCP is a base whose successive pullbacks get *progressively shallower*. It is
not "a quiet consolidation" and it is not "a base with low ATR" — those are
states, and a VCP is a *trajectory*. The distinction is the whole detector: a
security that traded in a 4% range for thirty sessions has low volatility and no
VCP, while one that pulled back 18%, then 10%, then 5% has a VCP even though its
average volatility over the base was high.

The structure, in order:

1. **A prior advance.** A VCP is a pause in an uptrend. The same contraction
   sequence after a decline is a failing security tightening into a low, which
   is a different thing with different odds.
2. **Two or more contraction legs**, each a confirmed swing high followed by a
   confirmed swing low. Three is the textbook count and the detector does not
   require it: two is a beginning and five is a long base, and demanding
   exactly three finds a subset of VCPs selected for tidiness.
3. **Progressive tightening.** Each leg shallower than the last. Measured two
   ways because they say different things: ``tightening_ratio`` (final over
   first depth) says how far it got, ``monotonic_fraction`` says how cleanly.
   A base that went 18 → 5 → 12 → 4 ends tight but is not a staircase, and
   reporting only the ratio would hide that.
4. **Volume drying up** through the contractions. Corroboration, never a gate.
5. **A final pivot** — the tightest leg's high — that price must clear.

**Why this is not the bull flag with different numbers.** The flag has one
pullback and scores its *depth*; the VCP has several and scores their
*progression*. A flag's consolidation is measured as a channel with a slope; a
VCP's base is measured as a sequence of legs whose relationship to each other is
the pattern. Sharing `ContractionSequence` would be reuse; sharing the scoring
would be renaming.

**What disqualifies.** Fewer than two legs (there is no progression to measure),
a base deeper than a configured maximum (a 50% decline with tightening legs is a
falling security, not a base), and legs that widen rather than narrow.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import numpy as np

from tradeit.analytics import kernels
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.core.models import OhlcvBar
from tradeit.patterns.base import (
    Boundary,
    ComponentRequirement,
    ComponentScore,
    DetectorContract,
    Evidence,
    EvidenceKind,
    PatternContext,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)
from tradeit.patterns.config import PatternEngineConfig, VcpConfig
from tradeit.patterns.invariants import check_geometry
from tradeit.patterns.primitives import (
    ContractionSequence,
    build_contractions,
    measure_prior_trend,
    measure_relative_strength,
    measure_volatility,
    measure_volume,
)
from tradeit.patterns.scoring import band_score, combine, confidence_from, decay_score, ramp_score
from tradeit.patterns.structure import single_extreme_resistance, structural_support
from tradeit.patterns.swings import Swing, SwingKind, confirmed_swings


class VcpDetector:
    """Finds volatility contraction patterns."""

    name = "vcp"
    pattern_type = PatternType.VOLATILITY_CONTRACTION

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: The progression *is* the pattern; without it there is nothing to
        #: classify. Prior trend is required too, because a contraction
        #: sequence without an advance behind it is a different structure that
        #: happens to share a shape.
        required_components=("contraction_progression", "base_structure", "prior_trend"),
        optional_components=(
            "volume_dryup",
            "volatility_profile",
            "pivot_quality",
            "relative_strength",
        ),
        warmup_bars=0,
        minimum_evidence_coverage=55.0,
    )

    def __init__(
        self,
        config: PatternEngineConfig | None = None,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> None:
        self.engine_config = config or PatternEngineConfig()
        self.config: VcpConfig = self.engine_config.vcp
        self.timeframe = timeframe
        self.version = self.config.version

    @property
    def contract(self) -> DetectorContract:
        from dataclasses import replace

        return replace(self.CONTRACT, warmup_bars=self.minimum_bars)

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.prior_trend_sessions
            + self.config.min_base_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.config.model_dump(mode="json")

    # -- entry point ---------------------------------------------------------

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
                f"{as_of_session}"
            )
        if context is not None and not context.aligned_with(bars):
            raise ValueError("pattern context series are not aligned with the bar series")

        highs = np.array([float(b.high) for b in bars])
        lows = np.array([float(b.low) for b in bars])
        closes = np.array([float(b.close) for b in bars])
        atr = kernels.atr(highs, lows, closes, self.config.atr_period)

        swing_cfg = self.engine_config.swings
        swing_highs = confirmed_swings(
            bars,
            as_of_session,
            left_bars=swing_cfg.left_bars,
            right_bars=swing_cfg.right_bars,
            kind=SwingKind.HIGH,
            atr=atr,
        )
        swing_lows = confirmed_swings(
            bars,
            as_of_session,
            left_bars=swing_cfg.left_bars,
            right_bars=swing_cfg.right_bars,
            kind=SwingKind.LOW,
            atr=atr,
        )

        instances: list[PatternInstance] = []
        for base_start in self._base_starts(bars, swing_highs):
            instance = self._assess(
                bars, atr, base_start, swing_highs, swing_lows, as_of_session, context
            )
            if instance is None or not instance.is_structurally_complete:
                continue
            if instance.quality < self.engine_config.min_quality_to_report:
                continue
            instances.append(instance)

        return sorted(instances, key=lambda p: -p.quality)

    def _base_starts(self, bars: Sequence[OhlcvBar], swing_highs: Sequence[Swing]) -> list[int]:
        """Where the base began: the high that started the first contraction.

        **Determined by the data, not chosen among candidates.** The first
        implementation here enumerated every confirmed swing low in range and
        let the quality sort pick the winner -- which is the pattern-selection
        look-ahead bias the bull flag work already ran into once. It showed up
        the same way: on a generated 18/10/5 base it reported contractions of
        15.8/22.5/14.4, because the highest-scoring window started inside the
        prior advance and measured part of the rally as a contraction leg.

        A VCP's base starts where the advance stopped, so the rule is the
        highest confirmed swing high in the eligible window. One candidate,
        fully determined. The secondary starts are kept only so a long base
        containing a tighter sub-base can still be reported as two
        interpretations, which the brief explicitly permits.
        """
        last = len(bars) - 1
        earliest = max(self.config.prior_trend_sessions, last - self.config.max_base_sessions)
        latest = last - self.config.min_base_sessions
        if latest <= earliest:
            return []

        eligible = [s for s in swing_highs if earliest <= s.index <= latest]
        if not eligible:
            return []

        # The base began the *first* time price reached the level it then
        # stopped advancing past. Taking the later of two equal highs truncates
        # the base and hides its earliest contractions -- on a generated
        # three-leg VCP it cut the base to one leg and the pattern vanished.
        highest = max(s.price for s in eligible)
        at_the_peak = [
            s for s in eligible if s.price >= highest * (1.0 - self.config.sub_base_tolerance)
        ]
        peak = min(at_the_peak, key=lambda s: s.index)
        starts = [peak.index]

        # A later high that is within a whisker of the peak begins a tighter
        # sub-base of the same structure. Reported as a second interpretation
        # rather than replacing the first.
        for candidate in at_the_peak:
            if candidate.index > peak.index:
                starts.append(candidate.index)

        return starts[: self.engine_config.max_candidates_per_pattern]

    # -- assessment ----------------------------------------------------------

    def _assess(
        self,
        bars: Sequence[OhlcvBar],
        atr: np.ndarray,
        base_start: int,
        swing_highs: Sequence[Swing],
        swing_lows: Sequence[Swing],
        as_of_session: dt.date,
        context: PatternContext | None,
    ) -> PatternInstance | None:
        last = len(bars) - 1
        structure_end = last - self.engine_config.swings.right_bars
        if structure_end - base_start < self.config.min_base_sessions:
            return None

        sequence = build_contractions(
            swing_highs, swing_lows, start_index=base_start, end_index=structure_end
        )
        if sequence.count < self.config.min_contractions:
            return None
        if sequence.base_depth > self.config.max_base_depth:
            return None

        components: list[ComponentScore] = []
        supporting: list[Evidence] = []
        contradicting: list[Evidence] = []

        components.append(self._score_progression(sequence))
        components.append(self._score_base(bars, sequence, base_start, structure_end))
        components.append(self._score_prior_trend(bars, base_start))
        components.append(self._score_volume(bars, sequence, base_start, structure_end))
        components.append(self._score_volatility(bars, atr, base_start, structure_end))

        pivot = self._pivot(bars, sequence, base_start, structure_end)
        components.append(self._score_pivot(pivot, sequence))
        components.append(self._score_relative_strength(context, bars, base_start, last))

        for component in components:
            supporting.extend(component.evidence)
            contradicting.extend(component.contradicting)

        weights = self.config.weights.as_mapping()
        composite = combine(
            {c.name: None if c.unavailable else c.score for c in components}, weights
        )

        support = structural_support(
            bars,
            swing_lows,
            start_index=base_start,
            end_index=structure_end,
            tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
        )
        invalidation = self._invalidation(sequence, support)
        coverage = _coverage(components)
        state = self._classify_state(bars, sequence, pivot, invalidation, composite.value)

        if coverage < self.contract.minimum_evidence_coverage and state.is_established:
            state = PatternState.FORMING
            contradicting.append(
                Evidence(
                    f"evidence coverage {coverage:.0f}% is below the detector's floor",
                    EvidenceKind.CONTEXTUAL,
                    measured=coverage,
                )
            )

        geometry = PatternGeometry(
            start_date=bars[base_start].session_date,
            end_date=bars[last].session_date,
            segments={
                "base": (bars[base_start].session_date, bars[last].session_date),
                **{
                    f"contraction_{leg.index + 1}": (leg.high_date, leg.low_date)
                    for leg in sequence.legs
                },
            },
            resistance=pivot,
            support=support,
            key_points={
                "base_high": PricePoint(
                    sequence.legs[0].high_date, max(leg.high for leg in sequence.legs)
                ),
                "base_low": PricePoint(
                    min(sequence.legs, key=lambda leg: leg.low).low_date,
                    min(leg.low for leg in sequence.legs),
                ),
                "final_low": PricePoint(sequence.legs[-1].low_date, sequence.legs[-1].low),
            },
            swing_highs=tuple(PricePoint(leg.high_date, leg.high) for leg in sequence.legs),
            swing_lows=tuple(PricePoint(leg.low_date, leg.low) for leg in sequence.legs),
        )

        # This detector assembles its own instance rather than going through
        # `BaseDetector._build`, so the shared validator has to be called here
        # too. See `tradeit.patterns.invariants`: the rules are checked against
        # the *persisted* representation, because two prices a nanocent apart
        # become one price at Numeric(18, 6).
        if check_geometry(geometry, invalidation=invalidation, detector=self.name):
            return None

        return PatternInstance(
            instrument_id=bars[0].instrument_id,
            pattern_type=self.pattern_type,
            timeframe=self.timeframe,
            state=state,
            geometry=geometry,
            as_of_session=as_of_session,
            knowledge_time=bars[-1].knowledge_time,
            quality=round(composite.value, 6),
            components=tuple(components),
            confidence=round(
                confidence_from(
                    available_weight=composite.available_weight,
                    total_weight=sum(weights.values()),
                    component_scores=[c.score for c in components if not c.unavailable],
                    session_count=last - base_start + 1,
                    minimum_sessions=self.config.min_base_sessions,
                ),
                6,
            ),
            invalidation_price=invalidation,
            session_count=last - base_start + 1,
            supporting_evidence=tuple(supporting),
            contradicting_evidence=tuple(contradicting),
            notes=("contractions: " + " -> ".join(f"{d:.1%}" for d in sequence.depths),),
            detector_name=self.name,
            detector_version=self.version,
            data_snapshot_digest=context.data_snapshot_digest if context else "",
        )

    # -- components ----------------------------------------------------------

    def _score_progression(self, sequence: ContractionSequence) -> ComponentScore:
        """The defining component: are the legs getting shallower?

        Scored on how far it tightened *and* how cleanly. A base that went
        18 → 5 → 12 → 4 ends tight and is not a staircase, and a score built
        only on the ratio would call it textbook.
        """
        cfg = self.config
        ratio_score = decay_score(
            sequence.tightening_ratio,
            full_at=cfg.ideal_tightening_ratio,
            zero_at=cfg.poor_tightening_ratio,
        )
        monotonic_score = ramp_score(sequence.monotonic_fraction, zero_at=0.3, full_at=1.0)
        count_score = band_score(
            float(sequence.count),
            ideal_low=float(cfg.ideal_contractions_low),
            ideal_high=float(cfg.ideal_contractions_high),
            tolerance_low=float(cfg.min_contractions),
            tolerance_high=float(cfg.max_contractions),
            floor=30.0,
        )
        final_score = decay_score(
            sequence.final_depth, full_at=cfg.ideal_final_depth, zero_at=cfg.poor_final_depth
        )

        score = float(
            np.clip(
                0.34 * ratio_score
                + 0.28 * monotonic_score
                + 0.16 * count_score
                + 0.22 * final_score,
                0.0,
                100.0,
            )
        )

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        legs = " -> ".join(f"{d:.1%}" for d in sequence.depths)
        if sequence.monotonic_fraction == 1.0:
            evidence.append(
                Evidence(
                    f"{sequence.count} progressively tighter contractions ({legs})",
                    measured=sequence.tightening_ratio,
                )
            )
        elif sequence.monotonic_fraction >= 0.5:
            evidence.append(
                Evidence(
                    f"base tightened overall ({legs}) but not monotonically",
                    measured=sequence.monotonic_fraction,
                )
            )
        else:
            against.append(
                Evidence(
                    f"contractions do not progressively tighten ({legs})",
                    measured=sequence.monotonic_fraction,
                )
            )
        if sequence.final_depth <= cfg.ideal_final_depth:
            evidence.append(
                Evidence(
                    f"final contraction is {sequence.final_depth:.1%} deep",
                    measured=sequence.final_depth,
                )
            )
        elif sequence.final_depth >= cfg.poor_final_depth:
            against.append(
                Evidence(
                    f"final contraction is still {sequence.final_depth:.1%} deep; the base "
                    "has not tightened into a pivot",
                    measured=sequence.final_depth,
                )
            )

        return ComponentScore(
            name="contraction_progression",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.contraction_progression,
            measurements={
                "count": float(sequence.count),
                "tightening_ratio": sequence.tightening_ratio,
                "monotonic_fraction": sequence.monotonic_fraction,
                "final_depth": sequence.final_depth,
                "depth_progression": sequence.depth_progression,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_base(
        self,
        bars: Sequence[OhlcvBar],
        sequence: ContractionSequence,
        start: int,
        end: int,
    ) -> ComponentScore:
        """Is the base the right depth and duration to be a base at all?"""
        cfg = self.config
        sessions = end - start + 1
        depth_score = band_score(
            sequence.base_depth,
            ideal_low=cfg.ideal_base_depth_low,
            ideal_high=cfg.ideal_base_depth_high,
            tolerance_low=0.02,
            tolerance_high=cfg.max_base_depth,
        )
        duration_score = band_score(
            float(sessions),
            ideal_low=float(cfg.ideal_base_sessions_low),
            ideal_high=float(cfg.ideal_base_sessions_high),
            tolerance_low=float(cfg.min_base_sessions),
            tolerance_high=float(cfg.max_base_sessions),
            floor=20.0,
        )
        score = float(np.clip(0.55 * depth_score + 0.45 * duration_score, 0.0, 100.0))

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if sequence.base_depth > cfg.ideal_base_depth_high:
            against.append(
                Evidence(
                    f"base is {sequence.base_depth:.1%} deep, closer to a correction "
                    "than a consolidation",
                    measured=sequence.base_depth,
                )
            )
        else:
            evidence.append(
                Evidence(
                    f"base depth {sequence.base_depth:.1%} over {sessions} sessions",
                    measured=sequence.base_depth,
                )
            )

        return ComponentScore(
            name="base_structure",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.base_structure,
            measurements={"base_depth": sequence.base_depth, "sessions": float(sessions)},
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_prior_trend(self, bars: Sequence[OhlcvBar], base_start: int) -> ComponentScore:
        """A VCP is a pause in an advance.

        Required, not optional. The same contraction sequence after a decline is
        a failing security tightening into a low, and calling both a VCP would
        make the label meaningless.
        """
        weight = self.config.weights.prior_trend
        trend = measure_prior_trend(
            bars, end_index=base_start, lookback=self.config.prior_trend_sessions
        )
        if trend is None:
            return ComponentScore(
                name="prior_trend",
                requirement=ComponentRequirement.REQUIRED,
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason=(
                    f"fewer than 10 sessions of history before the base; "
                    f"{self.config.prior_trend_sessions} wanted"
                ),
            )

        gain_score = ramp_score(trend.gain_pct, zero_at=-0.02, full_at=self.config.ideal_prior_gain)
        quality_score = ramp_score(trend.slope_r_squared, zero_at=0.05, full_at=0.55)
        score = float(np.clip(0.65 * gain_score + 0.35 * quality_score, 0.0, 100.0))

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if trend.is_uptrend:
            evidence.append(
                Evidence(
                    f"base follows a {trend.gain_pct:+.1%} advance over {trend.sessions} sessions",
                    measured=trend.gain_pct,
                )
            )
        else:
            against.append(
                Evidence(
                    f"no clear prior uptrend ({trend.gain_pct:+.1%} over "
                    f"{trend.sessions} sessions); contraction without an advance behind "
                    "it is a different structure",
                    measured=trend.gain_pct,
                )
            )

        return ComponentScore(
            name="prior_trend",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=weight,
            measurements={
                "gain_pct": trend.gain_pct,
                "slope_r_squared": trend.slope_r_squared,
                "distance_from_high": trend.distance_from_high,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_volume(
        self,
        bars: Sequence[OhlcvBar],
        sequence: ContractionSequence,
        start: int,
        end: int,
    ) -> ComponentScore:
        """Volume drying up through the base. Corroboration, never a gate."""
        weight = self.config.weights.volume_dryup
        profile = measure_volume(
            bars,
            start_index=start,
            end_index=end,
            baseline_start=max(0, start - self.config.prior_trend_sessions),
            baseline_end=start - 1,
        )
        if profile is None:
            return ComponentScore(
                name="volume_dryup",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="base window too short to measure volume",
            )

        ratio_score = decay_score(
            profile.ratio, full_at=self.config.ideal_volume_ratio, zero_at=1.25
        )
        slope_score = decay_score(profile.slope_per_session, full_at=-0.02, zero_at=0.02)
        late_score = decay_score(profile.late_ratio, full_at=0.6, zero_at=1.3)
        score = float(
            np.clip(0.45 * ratio_score + 0.30 * slope_score + 0.25 * late_score, 0.0, 100.0)
        )

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if profile.ratio <= self.config.ideal_volume_ratio:
            evidence.append(
                Evidence(
                    f"volume dried to {profile.ratio:.2f}x the pre-base baseline",
                    measured=profile.ratio,
                )
            )
        elif profile.ratio > 1.2:
            against.append(
                Evidence(
                    f"volume rose to {profile.ratio:.2f}x through the base",
                    measured=profile.ratio,
                )
            )

        return ComponentScore(
            name="volume_dryup",
            score=score,
            weight=weight,
            measurements={
                "ratio": profile.ratio,
                "slope": profile.slope_per_session,
                "late_ratio": profile.late_ratio,
                "dry_session_fraction": profile.dry_session_fraction,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_volatility(
        self, bars: Sequence[OhlcvBar], atr: np.ndarray, start: int, end: int
    ) -> ComponentScore:
        weight = self.config.weights.volatility_profile
        profile = measure_volatility(bars, start_index=start, end_index=end, atr=atr)
        if profile is None:
            return ComponentScore(
                name="volatility_profile",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="base window too short to compare early and late volatility",
            )

        atr_score = decay_score(profile.atr_ratio, full_at=0.55, zero_at=1.15)
        narrowing_score = ramp_score(profile.narrowing_fraction, zero_at=0.35, full_at=0.6)
        score = float(np.clip(0.65 * atr_score + 0.35 * narrowing_score, 0.0, 100.0))

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if profile.atr_ratio <= 0.7:
            evidence.append(
                Evidence(
                    f"ATR contracted to {profile.atr_ratio:.2f}x across the base",
                    measured=profile.atr_ratio,
                )
            )
        elif profile.atr_ratio >= 1.1:
            against.append(
                Evidence(
                    f"ATR expanded to {profile.atr_ratio:.2f}x across the base",
                    measured=profile.atr_ratio,
                )
            )

        return ComponentScore(
            name="volatility_profile",
            score=score,
            weight=weight,
            measurements={
                "atr_ratio": profile.atr_ratio,
                "range_ratio": profile.range_ratio,
                "narrowing_fraction": profile.narrowing_fraction,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_pivot(self, pivot: Boundary | None, sequence: ContractionSequence) -> ComponentScore:
        """How tight is the level the base has resolved toward?

        A VCP's pivot is the high of its *final* contraction, not the base's
        overall high. Using the overall high would put the buy point above a
        level the security has already stopped testing.
        """
        weight = self.config.weights.pivot_quality
        if pivot is None:
            return ComponentScore(
                name="pivot_quality",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="no pivot could be established from the final contraction",
            )

        tightness = decay_score(sequence.final_depth, full_at=0.03, zero_at=0.12)
        score = float(np.clip(0.5 * pivot.confidence + 0.5 * tightness, 0.0, 100.0))

        evidence: list[Evidence] = []
        if sequence.final_depth <= 0.05:
            evidence.append(
                Evidence(
                    f"final contraction tightened to {sequence.final_depth:.1%} below the "
                    f"{pivot.level:.2f} pivot",
                    measured=sequence.final_depth,
                )
            )

        return ComponentScore(
            name="pivot_quality",
            score=score,
            weight=weight,
            measurements={
                "level": pivot.level,
                "touches": float(pivot.touch_count),
                "final_depth": sequence.final_depth,
            },
            evidence=tuple(evidence),
        )

    def _score_relative_strength(
        self,
        context: PatternContext | None,
        bars: Sequence[OhlcvBar],
        start: int,
        end: int,
    ) -> ComponentScore:
        weight = self.config.weights.relative_strength
        if context is None or context.benchmark_closes is None:
            return ComponentScore(
                name="relative_strength",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="no benchmark series supplied in the pattern context",
            )

        profile = measure_relative_strength(
            [float(b.close) for b in bars],
            list(context.benchmark_closes),
            start_index=start,
            end_index=end,
        )
        if profile is None:
            return ComponentScore(
                name="relative_strength",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="benchmark series contains non-positive prices",
            )

        score = ramp_score(profile.excess_return, zero_at=-0.10, full_at=0.05)
        if profile.ratio_at_window_high:
            score = min(100.0, score + 12.0)

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if profile.excess_return >= 0:
            evidence.append(
                Evidence(
                    f"outperformed the benchmark by {profile.excess_return:+.1%} while basing",
                    EvidenceKind.ANALYTIC,
                    measured=profile.excess_return,
                )
            )
        else:
            against.append(
                Evidence(
                    f"lagged the benchmark by {abs(profile.excess_return):.1%} while basing",
                    EvidenceKind.ANALYTIC,
                    measured=profile.excess_return,
                )
            )

        return ComponentScore(
            name="relative_strength",
            score=float(np.clip(score, 0.0, 100.0)),
            weight=weight,
            measurements={
                "excess_return": profile.excess_return,
                "ratio_slope": profile.ratio_slope,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    # -- structure -----------------------------------------------------------

    def _pivot(
        self,
        bars: Sequence[OhlcvBar],
        sequence: ContractionSequence,
        start: int,
        end: int,
    ) -> Boundary | None:
        """The high of the final contraction.

        Not the base's overall high: a VCP resolves toward the level its last
        leg tested, and a pivot set at the base high would sit above a level the
        security stopped testing several legs ago.
        """
        if not sequence.legs:
            return None
        final = sequence.legs[-1]
        return single_extreme_resistance(
            bars, start_index=final.high_index, end_index=min(end, final.low_index)
        ) or single_extreme_resistance(bars, start_index=start, end_index=end)

    def _invalidation(self, sequence: ContractionSequence, support: Boundary | None) -> float:
        """Below the final contraction's low, the tightening has failed.

        Using the base's overall low instead would keep a pattern alive through
        a break of every level that made it a VCP.
        """
        final_low = sequence.legs[-1].low if sequence.legs else 0.0
        level = min(final_low, support.level) if support is not None else final_low
        return float(level * (1.0 - self.engine_config.states.invalidation_buffer_pct))

    def _classify_state(
        self,
        bars: Sequence[OhlcvBar],
        sequence: ContractionSequence,
        pivot: Boundary | None,
        invalidation: float,
        quality: float,
    ) -> PatternState:
        states = self.engine_config.states
        last_close = float(bars[-1].close)

        if last_close < invalidation:
            return PatternState.INVALIDATED
        if quality < states.maturity_quality_floor:
            return PatternState.FORMING
        if pivot is None or sequence.count < self.config.min_contractions:
            return PatternState.FORMING

        if last_close > pivot.level * (1.0 + states.breakout_buffer_pct):
            return PatternState.BROKEN_OUT_UNCONFIRMED
        if last_close >= pivot.level * (1.0 - states.near_breakout_pct):
            return PatternState.NEAR_BREAKOUT
        return PatternState.MATURE


def _coverage(components: Sequence[ComponentScore]) -> float:
    total = sum(c.weight for c in components)
    if total <= 0:
        return 0.0
    return sum(c.weight for c in components if not c.unavailable) / total * 100.0
