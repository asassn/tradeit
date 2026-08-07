"""Bull flag detection.

The architectural template for every other detector. A bull flag is
conceptually simple and structurally subtle, which makes it the right pattern to
establish the shape of the code on.

**The structure.** A directional advance (the flagpole), then a controlled pause
(the consolidation) that gives back part of the advance without destroying it,
usually on declining volume and contracting range, bounded above by a level
price must clear.

**What this detector does not do.** It does not decide whether a break of that
level is tradeable. It reports BROKEN_OUT_UNCONFIRMED when price has closed
above resistance and stops there. Volume confirmation, follow-through, and
extension from the pivot are Phase 5's questions.

**Where the look-ahead would be.** Three places, all handled explicitly:

1. *Resistance from unconfirmed highs.* Every swing comes from
   :func:`~tradeit.patterns.swings.confirmed_swings`, so a high that is not yet
   knowable as a pivot cannot define the level.
2. *Choosing the flagpole that worked.* The candidate search is bounded and the
   selection rule is stated below — earliest valid pole, not best-scoring one.
3. *Retroactive start dates.* A pattern's start is pinned to a confirmed swing
   low, so it cannot slide backwards as new bars arrive.

**Candidate selection, stated explicitly.** Among the surviving (flagpole,
consolidation) splits, the detector keeps the one whose flagpole *starts
earliest*, then longest pole, then narrowest consolidation. It deliberately does
**not** keep the highest-scoring candidate. On historical data "highest-scoring"
selects the window that happened to resolve well, which is pattern-selection
look-ahead bias — the output looks better and is not reproducible forward. The
earliest-start rule is arbitrary in the way a tie-break should be: it depends
only on data available at the time.
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
    PatternCandidate,
    PatternContext,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)
from tradeit.patterns.config import BullFlagConfig, PatternEngineConfig
from tradeit.patterns.scoring import (
    band_score,
    combine,
    confidence_from,
    decay_score,
    ramp_score,
)
from tradeit.patterns.structure import (
    ConsolidationShape,
    horizontal_resistance,
    measure_consolidation,
    single_extreme_resistance,
    structural_support,
)
from tradeit.patterns.swings import (
    Swing,
    SwingKind,
    confirmed_swings,
    swings_within,
)


class BullFlagDetector:
    """Finds bull flags. Implements :class:`~tradeit.patterns.base.Detector`."""

    name = "bull_flag"
    pattern_type = PatternType.BULL_FLAG

    #: What the detector needs, declared rather than left implicit in the order
    #: of its early returns. `flagpole`, `consolidation`, `retracement` and
    #: `duration` are the pattern's *definition* -- without any of them there is
    #: no flag to classify. The rest is corroboration.
    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=("flagpole", "consolidation", "retracement", "duration"),
        optional_components=(
            "volume_structure",
            "volatility_contraction",
            "relative_strength",
            "resistance_quality",
        ),
        warmup_bars=0,  # replaced below by minimum_bars, which reads config
        # Below 60% coverage the composite is computed from fewer than three of
        # its eight dimensions and stops describing the pattern. Not a trading
        # threshold -- Phase 4 sets none -- but the point at which the detector
        # should stop calling a structure MATURE.
        minimum_evidence_coverage=60.0,
    )

    def __init__(
        self,
        config: PatternEngineConfig | None = None,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> None:
        self.engine_config = config or PatternEngineConfig()
        self.config: BullFlagConfig = self.engine_config.bull_flag
        self.timeframe = timeframe
        self.version = self.config.version

    @property
    def contract(self) -> DetectorContract:
        from dataclasses import replace as _replace

        return _replace(self.CONTRACT, warmup_bars=self.minimum_bars)

    @property
    def minimum_bars(self) -> int:
        """Enough for a baseline, a pole, a consolidation and confirmation lag."""
        return (
            self.config.volume_baseline_sessions
            + self.config.flagpole.min_sessions
            + self.config.consolidation.min_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

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
        """Find bull flags visible as of ``as_of_session``.

        Returns a list because a security can legitimately host more than one
        interpretation — a short flag inside a longer one is two real
        structures, and collapsing them to the "best" would discard the
        information that both exist.
        """
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

        candidates = self._enumerate_candidates(bars, atr, swing_lows)
        if not candidates:
            return []

        instances: list[PatternInstance] = []
        seen_starts: set[int] = set()
        for candidate in candidates:
            if candidate.start_index in seen_starts:
                continue
            instance = self._assess(
                bars, candidate, atr, swing_highs, swing_lows, as_of_session, context
            )
            if instance is None:
                continue
            if not instance.is_structurally_complete:
                # A required component could not be measured. That is not a
                # low-coverage flag; there is no flag. Emitting one anyway would
                # be constructing a pattern from insufficient structural
                # evidence, which the contract exists to forbid.
                continue
            if instance.quality < self.engine_config.min_quality_to_report:
                continue
            seen_starts.add(candidate.start_index)
            instances.append(instance)

        # Highest quality first, for a reader. The *selection* among candidates
        # already happened causally above; this is presentation order only.
        return sorted(instances, key=lambda p: -p.quality)

    # -- candidate enumeration ----------------------------------------------

    def _enumerate_candidates(
        self,
        bars: Sequence[OhlcvBar],
        atr: np.ndarray,
        swing_lows: Sequence[Swing],
    ) -> list[PatternCandidate]:
        """Propose (flagpole, consolidation) splits worth assessing.

        The consolidation always ends at the last bar: a bull flag is a
        structure that exists *now*, and a completed flag from three months ago
        is history rather than a pattern. Historical instances are produced by
        replaying the detector at earlier dates, which is how a backtest gets
        them honestly.

        Pole starts come from confirmed swing lows only. Allowing an arbitrary
        bar to start the pole would let the search slide the origin to wherever
        the gain looks best, which is the selection bias this method exists to
        avoid.

        **There is no window search.** Given a pole start, the pole ends at the
        highest high between that start and the present, and the consolidation
        is everything after it. Both are determined by the data; neither is a
        free parameter.

        That is the important design decision here, and it was arrived at the
        hard way. The first implementation enumerated every (pole length, flag
        length) split and picked among them by a tie-break rule. Two things went
        wrong. Structurally, the tie-break rather than the data ended up
        choosing where the pole ended -- on a generated 12-session pole followed
        by a 9-session flag it reported a 20-session "consolidation" that
        swallowed the pole itself, then complained the consolidation was rising.
        Causally, any rule for choosing among overlapping windows is a rule
        about which structure *looks* best, and on historical data the honest
        version of that question is unanswerable without the future.

        Fixing the peak position removes the search entirely: one candidate per
        confirmed swing low, each fully determined. The remaining ordering
        (latest start first) only decides which candidates survive the cap, and
        it prefers the most recent low because a flagpole is by definition the
        leg from the last significant low -- short and sharp is the pattern's
        defining characteristic, not an incidental property.
        """
        pole_cfg = self.config.flagpole
        flag_cfg = self.config.consolidation
        last = len(bars) - 1
        highs = [float(b.high) for b in bars]

        low_indices = sorted({int(s.index) for s in swing_lows})
        if not low_indices:
            return []

        out: list[PatternCandidate] = []
        seen_peaks: set[tuple[int, int]] = set()

        for pole_start in low_indices:
            if pole_start < self.config.volume_baseline_sessions:
                continue  # no baseline to measure volume expansion against

            # The pole ends where the advance topped out. Ties resolve to the
            # earliest peak, so a double top starts the consolidation at the
            # first touch rather than the second.
            #
            # The search stops short of the provisional tail, for the same
            # reason resistance does: structure is defined only from confirmed
            # data. Without that, a breakout moves the peak onto the breakout
            # bar, the consolidation shrinks to nothing, and the flag vanishes
            # at the exact moment it resolves -- so no pattern ever reached
            # BROKEN_OUT_UNCONFIRMED. The tail still drives *state*; it just
            # does not get to redraw the structure.
            structure_end = max(pole_start, last - self.engine_config.swings.right_bars)
            span = highs[pole_start : structure_end + 1]
            if not span:
                continue
            pole_end = pole_start + int(np.argmax(span))

            pole_sessions = pole_end - pole_start
            flag_sessions = last - pole_end
            if not pole_cfg.min_sessions <= pole_sessions <= pole_cfg.max_sessions:
                continue
            if not flag_cfg.min_sessions <= flag_sessions <= flag_cfg.max_sessions:
                continue

            # The consolidation must be *observed*, not merely inferred from the
            # provisional tail. Without this, a pole running to within three
            # bars of the present produces a three-session "consolidation" that
            # is entirely unconfirmed data -- which is how a parabolic run, a
            # noisy drift and a broad trading range all came back as flags
            # scoring 52-70 on nothing but their last three bars.
            confirmed_flag_sessions = flag_sessions - self.engine_config.swings.right_bars
            if confirmed_flag_sessions < flag_cfg.min_sessions:
                continue

            gain = self._pole_gain(bars, pole_start, pole_end)
            if gain is None or gain < pole_cfg.min_gain_pct:
                continue
            if self._largest_session_share(bars, pole_start, pole_end) > (
                pole_cfg.max_single_session_share
            ):
                continue

            key = (pole_start, pole_end)
            if key in seen_peaks:
                continue
            seen_peaks.add(key)

            out.append(
                PatternCandidate(
                    start_index=pole_start,
                    end_index=last,
                    anchors={"pole_end": pole_end, "flag_start": pole_end + 1},
                    reason=f"pole {gain:.1%} over {pole_sessions} sessions",
                )
            )

        # Latest pole start first. This decides only which candidates survive
        # the cap, never how a candidate is shaped, and depends on nothing but
        # data available at the time.
        out.sort(key=lambda c: -c.start_index)
        return out[: self.engine_config.max_candidates_per_pattern]

    @staticmethod
    def _pole_gain(bars: Sequence[OhlcvBar], start: int, end: int) -> float | None:
        """Advance from the pole's low to its high, as a fraction.

        Low-to-high rather than close-to-close: the flagpole is the move, and
        measuring it on closes understates an advance that ran intraday.
        """
        base = float(bars[start].low)
        if base <= 0 or end <= start:
            return None
        peak = max(float(b.high) for b in bars[start : end + 1])
        return peak / base - 1.0

    # -- assessment ----------------------------------------------------------

    def _assess(
        self,
        bars: Sequence[OhlcvBar],
        candidate: PatternCandidate,
        atr: np.ndarray,
        swing_highs: Sequence[Swing],
        swing_lows: Sequence[Swing],
        as_of_session: dt.date,
        context: PatternContext | None,
    ) -> PatternInstance | None:
        pole_start = candidate.start_index
        pole_end = int(candidate.anchors["pole_end"])
        flag_start = int(candidate.anchors["flag_start"])
        flag_end = candidate.end_index

        shape = measure_consolidation(bars, start_index=flag_start, end_index=flag_end)
        if shape is None:
            return None

        pole_low = float(bars[pole_start].low)
        pole_high = max(float(b.high) for b in bars[pole_start : pole_end + 1])
        pole_gain = pole_high / pole_low - 1.0 if pole_low > 0 else 0.0
        pole_sessions = pole_end - pole_start
        if pole_height := (pole_high - pole_low):
            retracement = (pole_high - shape.low) / pole_height
        else:
            return None

        components: list[ComponentScore] = []
        supporting: list[Evidence] = []
        contradicting: list[Evidence] = []

        components.append(
            self._score_flagpole(bars, atr, pole_start, pole_end, pole_gain, pole_sessions)
        )
        components.append(self._score_consolidation(shape))
        components.append(self._score_retracement(retracement))
        components.append(self._score_duration(shape.sessions, pole_sessions))
        components.append(self._score_volume(bars, pole_start, pole_end, flag_start, flag_end))
        components.append(self._score_volatility(atr, flag_start, flag_end, shape))
        components.append(self._score_relative_strength(context, flag_start, flag_end, bars))

        resistance = self._resistance(bars, swing_highs, flag_start, flag_end, pole_high)
        support = structural_support(
            bars,
            swing_lows,
            start_index=flag_start,
            end_index=flag_end,
            tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
        )
        components.append(self._score_resistance(resistance))

        for component in components:
            supporting.extend(component.evidence)
            contradicting.extend(component.contradicting)

        weights = self.config.weights.as_mapping()
        composite = combine(
            {c.name: None if c.unavailable else c.score for c in components}, weights
        )

        invalidation = self._invalidation_price(shape, support, pole_low, pole_high)
        state = self._classify_state(
            bars, shape, resistance, invalidation, composite.value, pole_sessions
        )

        coverage = (
            sum(c.weight for c in components if not c.unavailable)
            / sum(c.weight for c in components)
            * 100.0
            if sum(c.weight for c in components) > 0
            else 0.0
        )
        if coverage < self.contract.minimum_evidence_coverage and state.is_established:
            # The structure may be perfectly sound; the *evidence* is too thin
            # for the composite to describe it. Reported as FORMING rather than
            # suppressed, with the coverage on its face, so a consumer can see
            # both the structure and the gap.
            state = PatternState.FORMING
            contradicting.append(
                Evidence(
                    f"evidence coverage {coverage:.0f}% is below the detector's "
                    f"{self.contract.minimum_evidence_coverage:.0f}% floor; the quality "
                    "score is computed from too little of its intended evidence to "
                    "call this mature",
                    EvidenceKind.CONTEXTUAL,
                    measured=coverage,
                )
            )

        if state is PatternState.INVALIDATED:
            contradicting.append(
                Evidence(
                    "structure broken: price closed below the invalidation level",
                    EvidenceKind.STRUCTURAL,
                    measured=float(bars[-1].close),
                )
            )

        geometry = PatternGeometry(
            start_date=bars[pole_start].session_date,
            end_date=bars[flag_end].session_date,
            segments={
                "flagpole": (bars[pole_start].session_date, bars[pole_end].session_date),
                "consolidation": (bars[flag_start].session_date, bars[flag_end].session_date),
            },
            resistance=resistance,
            support=support,
            key_points={
                "pole_low": PricePoint(bars[pole_start].session_date, pole_low),
                "pole_high": PricePoint(
                    max(bars[pole_start : pole_end + 1], key=lambda b: b.high).session_date,
                    pole_high,
                ),
                "flag_low": PricePoint(shape.start_date, shape.low),
            },
            swing_highs=tuple(
                PricePoint(bars[int(s.index)].session_date, float(s.price))
                for s in swings_within(swing_highs, pole_start, flag_end)
            ),
            swing_lows=tuple(
                PricePoint(bars[int(s.index)].session_date, float(s.price))
                for s in swings_within(swing_lows, pole_start, flag_end)
            ),
        )

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
                    session_count=shape.sessions,
                    minimum_sessions=self.config.consolidation.min_sessions,
                ),
                6,
            ),
            invalidation_price=invalidation,
            session_count=flag_end - pole_start + 1,
            supporting_evidence=tuple(supporting),
            contradicting_evidence=tuple(contradicting),
            detector_name=self.name,
            detector_version=self.version,
            data_snapshot_digest=context.data_snapshot_digest if context else "",
        )

    # -- components ----------------------------------------------------------

    def _score_flagpole(
        self,
        bars: Sequence[OhlcvBar],
        atr: np.ndarray,
        start: int,
        end: int,
        gain: float,
        sessions: int,
    ) -> ComponentScore:
        """Was the advance strong, orderly and participated in?

        Magnitude is scored twice — once as a percentage, once in ATRs — and the
        ATR measure carries more weight. A 12% advance means something entirely
        different in a utility and a biotech, and only the volatility-relative
        version compares them.
        """
        cfg = self.config.flagpole
        evidence: list[Evidence] = []
        against: list[Evidence] = []

        pct_score = ramp_score(gain, zero_at=cfg.min_gain_pct * 0.5, full_at=cfg.strong_gain_pct)

        atr_at_start = atr[start] if start < len(atr) and np.isfinite(atr[start]) else np.nan
        pole_low = float(bars[start].low)
        if np.isfinite(atr_at_start) and atr_at_start > 0:
            gain_in_atr = (pole_low * gain) / float(atr_at_start)
            atr_score = ramp_score(
                gain_in_atr, zero_at=cfg.min_gain_atr * 0.5, full_at=cfg.strong_gain_atr
            )
        else:
            gain_in_atr = float("nan")
            atr_score = pct_score

        duration_score = band_score(
            float(sessions),
            ideal_low=float(cfg.ideal_sessions_low),
            ideal_high=float(cfg.ideal_sessions_high),
            tolerance_low=float(cfg.min_sessions),
            tolerance_high=float(cfg.max_sessions),
            floor=20.0,
        )

        window = bars[start : end + 1]
        up_days = sum(1 for b in window if b.close > b.open)
        up_fraction = up_days / len(window) if window else 0.0
        up_score = ramp_score(up_fraction, zero_at=0.3, full_at=cfg.ideal_up_day_fraction)

        closing_strength = float(
            np.mean(
                [
                    (float(b.close) - float(b.low)) / (float(b.high) - float(b.low))
                    for b in window
                    if b.high > b.low
                ]
                or [0.5]
            )
        )
        close_score = ramp_score(closing_strength, zero_at=0.35, full_at=0.7)

        volume_ratio = self._volume_expansion(bars, start, end)
        volume_score = ramp_score(
            volume_ratio, zero_at=cfg.min_volume_ratio * 0.6, full_at=cfg.strong_volume_ratio
        )

        gap_share = self._largest_gap_share(bars, start, end, gain)
        gap_driven = gap_share >= cfg.gap_dominance_threshold
        # Not a rejection. The brief is explicit that gap-driven moves are
        # recorded rather than dismissed -- a gap is a repricing rather than an
        # accumulation, which is a real difference in character but not a
        # disqualification.
        gap_penalty = 12.0 if gap_driven else 0.0

        score = float(
            np.clip(
                0.20 * pct_score
                + 0.30 * atr_score
                + 0.15 * duration_score
                + 0.13 * up_score
                + 0.10 * close_score
                + 0.12 * volume_score
                - gap_penalty,
                0.0,
                100.0,
            )
        )

        if gain >= self.config.flagpole.strong_gain_pct * 0.8:
            evidence.append(
                Evidence(
                    f"strong directional flagpole: +{gain:.1%} over {sessions} sessions",
                    measured=gain,
                )
            )
        if volume_ratio >= self.config.flagpole.strong_volume_ratio:
            evidence.append(
                Evidence(
                    f"volume expanded {volume_ratio:.1f}x its baseline during the advance",
                    measured=volume_ratio,
                )
            )
        elif volume_ratio < self.config.flagpole.min_volume_ratio:
            against.append(
                Evidence(
                    f"advance ran on {volume_ratio:.1f}x baseline volume: little "
                    "evidence of participation",
                    measured=volume_ratio,
                )
            )
        if gap_driven:
            against.append(
                Evidence(
                    f"a single gap accounts for {gap_share:.0%} of the advance: a "
                    "repricing rather than sustained accumulation",
                    measured=gap_share,
                )
            )
        if up_fraction < 0.5:
            against.append(
                Evidence(
                    f"only {up_fraction:.0%} of flagpole sessions closed up",
                    measured=up_fraction,
                )
            )

        return ComponentScore(
            name="flagpole",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.flagpole,
            measurements={
                "gain_pct": gain,
                "gain_atr": gain_in_atr if np.isfinite(gain_in_atr) else 0.0,
                "sessions": float(sessions),
                "up_day_fraction": up_fraction,
                "closing_strength": closing_strength,
                "volume_ratio": volume_ratio,
                "largest_gap_share": gap_share,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_consolidation(self, shape: ConsolidationShape) -> ComponentScore:
        """Is the pause orderly, contained and appropriately shaped?

        The slope band permits sideways and mildly-upward drift, not only the
        textbook downward channel. Demanding the textbook shape finds almost
        nothing on real data.
        """
        cfg = self.config.consolidation
        evidence: list[Evidence] = []
        against: list[Evidence] = []

        slope_score = band_score(
            shape.slope_pct_per_session,
            ideal_low=cfg.ideal_slope_low,
            ideal_high=cfg.ideal_slope_high,
            tolerance_low=cfg.tolerance_slope_low,
            tolerance_high=cfg.tolerance_slope_high,
        )
        width_score = decay_score(
            shape.depth_pct, full_at=cfg.ideal_channel_width_pct, zero_at=cfg.max_channel_width_pct
        )
        tightness_score = decay_score(shape.close_dispersion, full_at=0.015, zero_at=0.08)
        contraction_score = decay_score(shape.range_contraction, full_at=0.6, zero_at=1.2)

        score = float(
            np.clip(
                0.34 * slope_score
                + 0.28 * width_score
                + 0.20 * tightness_score
                + 0.18 * contraction_score,
                0.0,
                100.0,
            )
        )

        if cfg.ideal_slope_low <= shape.slope_pct_per_session <= cfg.ideal_slope_high:
            evidence.append(
                Evidence(
                    f"consolidation drifts {shape.slope_pct_per_session * 100:+.2f}%/session, "
                    "within the preferred range",
                    measured=shape.slope_pct_per_session,
                )
            )
        elif shape.slope_pct_per_session < cfg.ideal_slope_low:
            against.append(
                Evidence(
                    f"consolidation slope {shape.slope_pct_per_session * 100:+.2f}%/session "
                    "is steeper than preferred",
                    measured=shape.slope_pct_per_session,
                )
            )
        else:
            against.append(
                Evidence(
                    f"consolidation is rising at {shape.slope_pct_per_session * 100:+.2f}%"
                    "/session, which is trend continuation rather than a pause",
                    measured=shape.slope_pct_per_session,
                )
            )

        if shape.depth_pct > cfg.max_channel_width_pct:
            against.append(
                Evidence(
                    f"channel is {shape.depth_pct:.1%} wide: a trading range rather than a flag",
                    measured=shape.depth_pct,
                )
            )
        if shape.is_contracting:
            evidence.append(
                Evidence(
                    f"range contracted to {shape.range_contraction:.2f}x through the consolidation",
                    measured=shape.range_contraction,
                )
            )

        return ComponentScore(
            name="consolidation",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.consolidation,
            measurements={
                "slope_pct_per_session": shape.slope_pct_per_session,
                "slope_r_squared": shape.slope_r_squared,
                "depth_pct": shape.depth_pct,
                "close_dispersion": shape.close_dispersion,
                "range_contraction": shape.range_contraction,
                "sessions": float(shape.sessions),
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_retracement(self, retracement: float) -> ComponentScore:
        """How much of the advance was given back.

        Continuous, and deliberately not anchored to one Fibonacci ratio. The
        band is configurable; the one genuine discontinuity is a retracement
        through the flagpole origin, which is not a worse flag but a destroyed
        one, and is handled by the invalidation logic rather than by the curve.
        """
        cfg = self.config.consolidation
        score = band_score(
            retracement,
            ideal_low=cfg.ideal_retracement_low,
            ideal_high=cfg.ideal_retracement_high,
            tolerance_low=cfg.tolerance_retracement_low,
            tolerance_high=cfg.tolerance_retracement_high,
        )

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if cfg.ideal_retracement_low <= retracement <= cfg.ideal_retracement_high:
            evidence.append(
                Evidence(
                    f"retraced {retracement:.1%} of the flagpole: a controlled pause",
                    measured=retracement,
                )
            )
        elif retracement > cfg.ideal_retracement_high:
            against.append(
                Evidence(
                    f"retraced {retracement:.1%} of the flagpole: the advance is being "
                    "given back rather than digested",
                    measured=retracement,
                )
            )
        else:
            evidence.append(
                Evidence(
                    f"retraced only {retracement:.1%}; very shallow, which may mean the "
                    "consolidation has barely begun",
                    measured=retracement,
                )
            )

        return ComponentScore(
            name="retracement",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.retracement,
            measurements={"retracement_of_flagpole": retracement},
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_duration(self, flag_sessions: int, pole_sessions: int) -> ComponentScore:
        """Length in absolute terms and relative to the pole.

        Relative matters more. A 10-session pause after a 30-session advance is
        a flag; the same 10 sessions after a 4-session pole is a base that
        happens to follow a spike.
        """
        cfg = self.config.consolidation
        absolute = band_score(
            float(flag_sessions),
            ideal_low=float(cfg.ideal_sessions_low),
            ideal_high=float(cfg.ideal_sessions_high),
            tolerance_low=float(cfg.min_sessions),
            tolerance_high=float(cfg.max_sessions),
            floor=15.0,
        )
        ratio = flag_sessions / pole_sessions if pole_sessions > 0 else float("inf")
        relative = decay_score(ratio, full_at=1.0, zero_at=cfg.max_duration_ratio)
        score = float(np.clip(0.55 * absolute + 0.45 * relative, 0.0, 100.0))

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        too_short = flag_sessions < cfg.ideal_sessions_low
        too_extended = flag_sessions > cfg.ideal_sessions_high or ratio > cfg.max_duration_ratio
        if too_short:
            against.append(
                Evidence(
                    f"consolidation is only {flag_sessions} sessions: too short to have "
                    "established structure",
                    measured=float(flag_sessions),
                )
            )
        if too_extended:
            against.append(
                Evidence(
                    f"consolidation has run {flag_sessions} sessions "
                    f"({ratio:.1f}x the pole): closer to a base than a flag",
                    measured=ratio,
                )
            )
        if not too_short and not too_extended:
            evidence.append(
                Evidence(
                    f"consolidation of {flag_sessions} sessions is proportionate to a "
                    f"{pole_sessions}-session pole",
                    measured=ratio,
                )
            )

        return ComponentScore(
            name="duration",
            requirement=ComponentRequirement.REQUIRED,
            score=score,
            weight=self.config.weights.duration,
            measurements={
                "flag_sessions": float(flag_sessions),
                "pole_sessions": float(pole_sessions),
                "duration_ratio": ratio if np.isfinite(ratio) else 0.0,
                "too_short": 1.0 if too_short else 0.0,
                "too_extended": 1.0 if too_extended else 0.0,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_volume(
        self,
        bars: Sequence[OhlcvBar],
        pole_start: int,
        pole_end: int,
        flag_start: int,
        flag_end: int,
    ) -> ComponentScore:
        """Participation on the advance, dry-up on the pause, return near the top.

        Volume influences quality and never gates detection. Requiring dry-up
        would select for one correlated property rather than for structure, and
        plenty of sound flags form on flat volume.
        """
        cfg = self.config.volume
        pole_volume = float(np.mean([float(b.volume) for b in bars[pole_start : pole_end + 1]]))
        flag_volume = float(np.mean([float(b.volume) for b in bars[flag_start : flag_end + 1]]))
        contraction = flag_volume / pole_volume if pole_volume > 0 else 1.0
        contraction_score = decay_score(
            contraction, full_at=cfg.ideal_contraction, zero_at=cfg.poor_contraction
        )

        window = bars[flag_start : flag_end + 1]
        up_volume = sum(float(b.volume) for b in window if b.close >= b.open)
        down_volume = sum(float(b.volume) for b in window if b.close < b.open)
        up_down = up_volume / down_volume if down_volume > 0 else 2.0
        up_down_score = ramp_score(up_down, zero_at=0.5, full_at=cfg.ideal_up_down_ratio)

        approach = window[-cfg.approach_sessions :]
        approach_volume = float(np.mean([float(b.volume) for b in approach])) if approach else 0.0
        approach_ratio = approach_volume / flag_volume if flag_volume > 0 else 1.0
        # Returning participation near resistance is constructive but mild:
        # scored generously above 1.0 and not punished below it, because a
        # quiet approach is the normal case rather than a defect.
        approach_score = ramp_score(approach_ratio, zero_at=0.5, full_at=1.4, floor=45.0)

        obv = kernels.on_balance_volume(
            np.array([float(b.close) for b in bars]),
            np.array([float(b.volume) for b in bars]),
        )
        obv_change = float(obv[flag_end] - obv[flag_start]) if flag_end < len(obv) else 0.0
        obv_holding = obv_change >= 0
        obv_score = 100.0 if obv_holding else 45.0

        score = float(
            np.clip(
                0.40 * contraction_score
                + 0.22 * up_down_score
                + 0.16 * approach_score
                + 0.22 * obv_score,
                0.0,
                100.0,
            )
        )

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if contraction <= cfg.ideal_contraction:
            evidence.append(
                Evidence(
                    f"volume declined to {contraction:.2f}x the flagpole average during "
                    "the consolidation",
                    measured=contraction,
                )
            )
        elif contraction >= cfg.poor_contraction:
            against.append(
                Evidence(
                    f"volume rose to {contraction:.2f}x the flagpole average during the "
                    "consolidation: distribution rather than digestion",
                    measured=contraction,
                )
            )
        if obv_holding:
            evidence.append(
                Evidence("on-balance volume held through the consolidation", measured=obv_change)
            )
        else:
            against.append(
                Evidence(
                    "on-balance volume declined through the consolidation",
                    measured=obv_change,
                )
            )
        if approach_ratio > 1.2:
            evidence.append(
                Evidence(
                    f"participation returning near resistance ({approach_ratio:.2f}x the "
                    "consolidation average)",
                    measured=approach_ratio,
                )
            )

        return ComponentScore(
            name="volume_structure",
            requirement=ComponentRequirement.OPTIONAL,
            score=score,
            weight=self.config.weights.volume_structure,
            measurements={
                "contraction": contraction,
                "up_down_ratio": up_down,
                "approach_ratio": approach_ratio,
                "obv_change": obv_change,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_volatility(
        self, atr: np.ndarray, flag_start: int, flag_end: int, shape: ConsolidationShape
    ) -> ComponentScore:
        """Is range compressing through the pause?

        Also the foundation the VCP detector builds on, which is why it is
        measured here rather than folded into the consolidation score.
        """
        cfg = self.config.volatility
        sessions = flag_end - flag_start + 1
        if sessions < cfg.min_sessions:
            return ComponentScore(
                name="volatility_contraction",
                score=0.0,
                weight=self.config.weights.volatility_contraction,
                measurements={"sessions": float(sessions)},
                unavailable=True,
                unavailable_reason=(
                    f"consolidation is {sessions} sessions; contraction needs "
                    f"{cfg.min_sessions} for the two halves to be comparable"
                ),
            )

        half = sessions // 2
        early = atr[flag_start : flag_start + half]
        late = atr[flag_start + half : flag_end + 1]
        early_mean = float(np.nanmean(early)) if early.size else float("nan")
        late_mean = float(np.nanmean(late)) if late.size else float("nan")
        atr_ratio = (
            late_mean / early_mean
            if np.isfinite(early_mean) and np.isfinite(late_mean) and early_mean > 0
            else 1.0
        )

        atr_score = decay_score(atr_ratio, full_at=cfg.ideal_atr_ratio, zero_at=cfg.poor_atr_ratio)
        range_score = decay_score(
            shape.range_contraction, full_at=cfg.ideal_range_ratio, zero_at=cfg.poor_range_ratio
        )
        score = float(np.clip(0.55 * atr_score + 0.45 * range_score, 0.0, 100.0))

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if atr_ratio <= cfg.ideal_atr_ratio:
            evidence.append(
                Evidence(
                    f"ATR contracted to {atr_ratio:.2f}x through the consolidation",
                    measured=atr_ratio,
                )
            )
        elif atr_ratio >= cfg.poor_atr_ratio:
            against.append(
                Evidence(
                    f"ATR expanded to {atr_ratio:.2f}x through the consolidation",
                    measured=atr_ratio,
                )
            )

        return ComponentScore(
            name="volatility_contraction",
            requirement=ComponentRequirement.OPTIONAL,
            score=score,
            weight=self.config.weights.volatility_contraction,
            measurements={
                "atr_ratio": atr_ratio,
                "range_contraction": shape.range_contraction,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_relative_strength(
        self,
        context: PatternContext | None,
        flag_start: int,
        flag_end: int,
        bars: Sequence[OhlcvBar],
    ) -> ComponentScore:
        """Did the security hold up against its benchmark while pausing?

        **Evidence, not geometry.** The brief is explicit, and it matters: a
        detector that refuses to find a flag because the benchmark series is
        missing has confused corroboration with definition. So this component
        is marked ``unavailable`` when there is no benchmark, and the composite
        renormalises rather than scoring it zero.
        """
        weight = self.config.weights.relative_strength
        if context is None or context.benchmark_closes is None:
            return ComponentScore(
                name="relative_strength",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason="no benchmark series supplied in the pattern context",
            )

        benchmark = np.asarray(context.benchmark_closes, dtype=np.float64)
        closes = np.array([float(b.close) for b in bars])
        start, end = flag_start, flag_end
        if benchmark[start] <= 0 or closes[start] <= 0:
            return ComponentScore(
                name="relative_strength",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason=(
                    "benchmark or security price is non-positive at the window start"
                ),
            )

        security_return = closes[end] / closes[start] - 1.0
        benchmark_return = benchmark[end] / benchmark[start] - 1.0
        excess = security_return - benchmark_return

        ratio = closes[start : end + 1] / benchmark[start : end + 1]
        rs_rising = bool(ratio[-1] >= ratio.max() * 0.995)
        rs_score = ramp_score(excess, zero_at=-0.08, full_at=0.05)
        if rs_rising:
            rs_score = min(100.0, rs_score + 12.0)

        percentile_score: float | None = None
        if context.rs_percentile is not None:
            percentile_score = ramp_score(context.rs_percentile, zero_at=0.4, full_at=0.9)
            rs_score = 0.6 * rs_score + 0.4 * percentile_score

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if excess >= 0:
            evidence.append(
                Evidence(
                    f"relative strength held: {excess:+.1%} against the benchmark through "
                    "the consolidation",
                    EvidenceKind.ANALYTIC,
                    measured=excess,
                )
            )
        else:
            against.append(
                Evidence(
                    f"underperformed the benchmark by {abs(excess):.1%} through the consolidation",
                    EvidenceKind.ANALYTIC,
                    measured=excess,
                )
            )
        if rs_rising:
            evidence.append(
                Evidence(
                    "relative-strength line at its consolidation high while price consolidates",
                    EvidenceKind.ANALYTIC,
                )
            )

        measurements = {"excess_return": excess, "rs_line_at_high": 1.0 if rs_rising else 0.0}
        if percentile_score is not None and context.rs_percentile is not None:
            measurements["rs_percentile"] = context.rs_percentile

        return ComponentScore(
            name="relative_strength",
            requirement=ComponentRequirement.OPTIONAL,
            score=float(np.clip(rs_score, 0.0, 100.0)),
            weight=weight,
            measurements=measurements,
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    def _score_resistance(self, resistance: Boundary | None) -> ComponentScore:
        """How well established is the level price must clear?

        A level defined by four separate tests is a market fact; the highest bar
        so far is arithmetic. Both are usable, and the score is what keeps them
        distinguishable downstream.
        """
        weight = self.config.weights.resistance_quality
        if resistance is None:
            return ComponentScore(
                name="resistance_quality",
                score=0.0,
                weight=weight,
                unavailable=True,
                unavailable_reason=(
                    "no resistance level could be established from confirmed pivots "
                    "or the consolidation high"
                ),
            )

        evidence: list[Evidence] = []
        against: list[Evidence] = []
        if resistance.touch_count >= 3:
            evidence.append(
                Evidence(
                    f"resistance at {resistance.level:.2f} tested {resistance.touch_count} times",
                    measured=float(resistance.touch_count),
                )
            )
        elif resistance.method == "single_extreme":
            against.append(
                Evidence(
                    "resistance is the single highest bar so far, not a level the "
                    "market has repeatedly respected",
                    measured=1.0,
                )
            )

        return ComponentScore(
            name="resistance_quality",
            requirement=ComponentRequirement.OPTIONAL,
            score=resistance.confidence,
            weight=weight,
            measurements={
                "touches": float(resistance.touch_count),
                "level": resistance.level,
                "confidence": resistance.confidence,
            },
            evidence=tuple(evidence),
            contradicting=tuple(against),
        )

    # -- structure -----------------------------------------------------------

    def _resistance(
        self,
        bars: Sequence[OhlcvBar],
        swing_highs: Sequence[Swing],
        flag_start: int,
        flag_end: int,
        pole_high: float,
    ) -> Boundary | None:
        """The level a breakout must clear.

        Prefers a cluster of confirmed swing highs inside the consolidation.
        Falls back to the highest bar, labelled ``single_extreme`` — a young
        flag has no repeated tests, and refusing to name a level would leave it
        without the number that defines it.

        The search window starts a few sessions *before* the consolidation, and
        that is structural rather than a fudge. For a bull flag the initial
        resistance is the pole's own peak — that is the level the advance
        stalled at and the level a breakout must reclaim. Searching only inside
        the consolidation excludes it, which on a typical 9-session flag leaves
        too few confirmed pivots to cluster at all: with a 3-bar confirmation
        window either side, a 9-bar window can hold at most one. Every flag then
        falls back to ``single_extreme`` and scores 25 for resistance quality
        regardless of how well established the level actually is.

        The window also **stops short of the provisional tail**. This is the
        rule stated in :mod:`tradeit.patterns.swings`: the last ``right_bars``
        sessions may inform *state* but must not *define* structure. Violating
        it here was not a stylistic slip — including the final bars in the
        resistance search makes resistance at least as high as the latest high,
        so a breakout can never be observed. The first version of this method
        did exactly that, and no series in the test suite ever reached
        ``BROKEN_OUT_UNCONFIRMED``.
        """
        swing_cfg = self.engine_config.swings
        lookback = swing_cfg.left_bars + swing_cfg.right_bars
        search_start = max(0, flag_start - lookback)
        search_end = flag_end - swing_cfg.right_bars
        if search_end <= search_start:
            search_end = flag_end

        cluster = horizontal_resistance(
            bars,
            swing_highs,
            start_index=search_start,
            end_index=search_end,
            tolerance_pct=swing_cfg.touch_tolerance_pct,
            min_touches=2,
        )
        if cluster is not None and cluster.level <= pole_high * 1.01:
            return cluster
        # Capped at the pole high: a flag's resistance cannot legitimately sit
        # above the peak the flag is consolidating below.
        return single_extreme_resistance(bars, start_index=search_start, end_index=search_end)

    def _invalidation_price(
        self,
        shape: ConsolidationShape,
        support: Boundary | None,
        pole_low: float,
        pole_high: float,
    ) -> float:
        """Where this *pattern* has failed as a pattern.

        Emphatically not a stop-loss. A stop belongs to a position and depends
        on portfolio risk, volatility budget and sizing, none of which exist at
        this stage. Conflating the two produces stops placed by a chart-drawing
        routine, which is how a system ends up risking a fixed fraction of
        equity on a number chosen for geometric convenience.

        The level is the higher of consolidation support and the maximum
        tolerable retracement of the pole — whichever fails first is what
        destroys the structure.
        """
        cfg = self.config.consolidation
        support_level = support.level if support is not None else shape.low
        retracement_floor = pole_high - (pole_high - pole_low) * cfg.invalidation_retracement
        buffer = 1.0 - self.engine_config.states.invalidation_buffer_pct
        return float(max(support_level, retracement_floor) * buffer)

    def _classify_state(
        self,
        bars: Sequence[OhlcvBar],
        shape: ConsolidationShape,
        resistance: Boundary | None,
        invalidation: float,
        quality: float,
        pole_sessions: int,
    ) -> PatternState:
        """Place the pattern in its lifecycle.

        Uses the *current bar* for state, which is legitimate and is why
        :func:`provisional_extremes` exists: "has price closed above
        resistance?" is a statement about today, not a retroactive refinement of
        the structure.

        Order matters. Invalidation is checked first because a broken structure
        is broken regardless of how good it looked, and expiry before maturity
        because a stale pattern should not be surfaced as fresh.
        """
        states = self.engine_config.states
        last_close = float(bars[-1].close)

        if last_close < invalidation:
            return PatternState.INVALIDATED

        max_sessions = self.config.consolidation.max_sessions * states.expiry_multiple
        if shape.sessions > max_sessions:
            return PatternState.EXPIRED

        if quality < states.maturity_quality_floor:
            return PatternState.FORMING
        if shape.sessions < self.config.consolidation.min_sessions:
            return PatternState.FORMING
        if resistance is None:
            return PatternState.FORMING

        level = resistance.level
        if last_close > level * (1.0 + states.breakout_buffer_pct):
            # A geometric observation only. Whether this breakout is valid,
            # tradeable, or likely to hold is Phase 5's question.
            return PatternState.BROKEN_OUT_UNCONFIRMED
        if last_close >= level * (1.0 - states.near_breakout_pct):
            return PatternState.NEAR_BREAKOUT
        return PatternState.MATURE

    # -- measurement helpers -------------------------------------------------

    def _volume_expansion(self, bars: Sequence[OhlcvBar], start: int, end: int) -> float:
        """Flagpole volume against the baseline immediately preceding it."""
        baseline_start = max(0, start - self.config.volume_baseline_sessions)
        baseline = [float(b.volume) for b in bars[baseline_start:start]]
        pole = [float(b.volume) for b in bars[start : end + 1]]
        if not baseline or not pole:
            return 1.0
        baseline_mean = float(np.mean(baseline))
        return float(np.mean(pole)) / baseline_mean if baseline_mean > 0 else 1.0

    @staticmethod
    def _largest_session_share(bars: Sequence[OhlcvBar], start: int, end: int) -> float:
        """Fraction of the advance delivered by its single largest session.

        A structural control, not a quality one. A flagpole is a sustained
        advance; a single bar carrying the whole move has no duration to
        sustain, and the flat stretch after it is a security digesting one
        event rather than an uptrend pausing. Distinct from
        ``_largest_gap_share``, which characterises an *overnight* move and is
        recorded rather than acted on.
        """
        if end <= start:
            return 1.0
        total = float(bars[end].close) - float(bars[start].low)
        if total <= 0:
            return 1.0
        largest = max(
            float(bars[i].close) - float(bars[i - 1].close) for i in range(start + 1, end + 1)
        )
        return float(np.clip(largest / total, 0.0, 1.0))

    @staticmethod
    def _largest_gap_share(bars: Sequence[OhlcvBar], start: int, end: int, gain: float) -> float:
        """What fraction of the advance came from the single largest gap.

        Recorded rather than used to reject. A gap-driven advance is a
        repricing rather than an accumulation -- a real difference in character
        that the evidence should carry, and not a disqualification.
        """
        if gain <= 0 or end <= start:
            return 0.0
        base = float(bars[start].low)
        if base <= 0:
            return 0.0
        largest = 0.0
        for i in range(start + 1, end + 1):
            gap = float(bars[i].open) - float(bars[i - 1].close)
            largest = max(largest, gap)
        return float(np.clip(largest / (base * gain), 0.0, 1.0))
