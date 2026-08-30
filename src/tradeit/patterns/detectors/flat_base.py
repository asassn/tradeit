"""Flat Base.

**Structural definition.**

A shallow, horizontal pause in an established advance. Three properties, and the
third is the one detectors usually omit:

1. **Shallow.** Under ~15% from high to low. A 25% "flat base" is a correction
   that someone did not want to call one.
2. **Horizontal.** The base neither rises nor falls materially. A rising base is
   a channel; a falling one is a decline that has not finished.
3. **Preceded by an advance.** This is the requirement that stops arbitrary
   sideways movement being classified. The same shallow horizontal range after a
   decline is a bear-market rest, and it is the *context* — not the geometry —
   that distinguishes them. The brief is explicit about this and it is why
   `prior_trend` is a required component rather than a scored nicety.

**Not a VCP.** A VCP is a trajectory: successive pullbacks tightening. A flat
base is a *state*: consistently shallow throughout. A base that started 14% deep
and tightened to 4% is a VCP; one that stayed 6% deep the whole time is a flat
base. Both are legitimate and they are not the same structure, so both may fire
and the relationship is recorded rather than resolved.

**Discovery is structural** (ADR-0014). The base begins at the earliest confirmed
swing high at the peak level — the first time the advance stopped — and runs to
the last confirmed session. No window search.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from tradeit.core.enums import PatternType
from tradeit.patterns.base import (
    Boundary,
    ComponentRequirement,
    ComponentScore,
    DetectorContract,
    Evidence,
    EvidenceKind,
    PatternGeometry,
    PricePoint,
)
from tradeit.patterns.config import FlatBaseConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import (
    measure_prior_trend,
    measure_relative_strength,
    measure_volatility,
    measure_volume,
)
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import (
    horizontal_resistance,
    measure_consolidation,
    single_extreme_resistance,
    structural_support,
)


class FlatBaseDetector(BaseDetector):
    """Finds flat bases."""

    name = "flat_base"
    pattern_type = PatternType.FLAT_BASE

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: Prior trend is required, not optional. A shallow horizontal range
        #: without an advance behind it is a different structure that happens
        #: to share a shape, and classifying it as a flat base would make the
        #: label mean nothing.
        required_components=("prior_trend", "horizontal_structure", "depth", "duration"),
        optional_components=(
            "resistance_consistency",
            "compression",
            "volume_character",
            "relative_strength",
        ),
        minimum_evidence_coverage=55.0,
    )

    @property
    def config(self) -> FlatBaseConfig:
        return self.engine_config.flat_base

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.prior_trend_sessions
            + self.config.min_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """The base begins where the advance stopped.

        The earliest confirmed swing high at the peak level, exactly as the VCP
        does — for the same reason, and after the same bug. Taking a later equal
        high truncates the base and hides its earliest structure.
        """
        cfg = self.config
        end = inputs.structure_end
        earliest = max(cfg.prior_trend_sessions, end - cfg.max_sessions)
        latest = end - cfg.min_sessions
        if latest <= earliest:
            return []

        eligible = [s for s in inputs.swing_highs if earliest <= s.index <= latest]
        if not eligible:
            return []

        highest = max(s.price for s in eligible)
        at_peak = [s for s in eligible if s.price >= highest * 0.98]
        origin = min(at_peak, key=lambda s: s.index)

        return [
            Structure(
                start_index=origin.index,
                end_index=inputs.last_index,
                parts={"structure_end": end, "peak": highest},
                reason=f"base from {inputs.bars[origin.index].session_date}",
            )
        ]

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        start = structure.start_index
        end = int(structure.parts["structure_end"])
        shape = measure_consolidation(inputs.bars, start_index=start, end_index=end)
        if shape is None:
            return []

        weights = cfg.weights
        components: list[ComponentScore] = []

        # -- prior trend (required)
        trend = measure_prior_trend(inputs.bars, end_index=start, lookback=cfg.prior_trend_sessions)
        if trend is None:
            components.append(
                unavailable(
                    "prior_trend",
                    weights["prior_trend"],
                    "insufficient history before the base to establish context",
                )
            )
        else:
            gain_score = ramp_score(trend.gain_pct, zero_at=0.0, full_at=cfg.ideal_prior_gain)
            fit_score = ramp_score(trend.slope_r_squared, zero_at=0.05, full_at=0.5)
            components.append(
                ComponentScore(
                    name="prior_trend",
                    requirement=ComponentRequirement.REQUIRED,
                    score=float(np.clip(0.7 * gain_score + 0.3 * fit_score, 0, 100)),
                    weight=weights["prior_trend"],
                    measurements={
                        "gain_pct": trend.gain_pct,
                        "slope_r_squared": trend.slope_r_squared,
                    },
                    evidence=(
                        (
                            Evidence(
                                f"base follows a {trend.gain_pct:+.1%} advance over "
                                f"{trend.sessions} sessions",
                                measured=trend.gain_pct,
                            ),
                        )
                        if trend.is_uptrend
                        else ()
                    ),
                    contradicting=(
                        ()
                        if trend.is_uptrend
                        else (
                            Evidence(
                                f"no clear prior advance ({trend.gain_pct:+.1%}); a shallow "
                                "horizontal range without one is not a base in context",
                                measured=trend.gain_pct,
                            ),
                        )
                    ),
                )
            )

        # -- horizontal structure (required): the defining property
        slope_score = band_score(
            shape.slope_pct_per_session,
            ideal_low=-cfg.max_abs_slope * 0.4,
            ideal_high=cfg.max_abs_slope * 0.4,
            tolerance_low=-cfg.max_abs_slope * 2.5,
            tolerance_high=cfg.max_abs_slope * 2.5,
        )
        dispersion_score = decay_score(shape.close_dispersion, full_at=0.02, zero_at=0.09)
        components.append(
            ComponentScore(
                name="horizontal_structure",
                requirement=ComponentRequirement.REQUIRED,
                score=float(np.clip(0.65 * slope_score + 0.35 * dispersion_score, 0, 100)),
                weight=weights["horizontal_structure"],
                measurements={
                    "slope_pct_per_session": shape.slope_pct_per_session,
                    "close_dispersion": shape.close_dispersion,
                },
                evidence=(
                    (
                        Evidence(
                            f"base drifts {shape.slope_pct_per_session * 100:+.2f}%/session: "
                            "genuinely horizontal",
                            measured=shape.slope_pct_per_session,
                        ),
                    )
                    if abs(shape.slope_pct_per_session) <= cfg.max_abs_slope
                    else ()
                ),
                contradicting=(
                    ()
                    if abs(shape.slope_pct_per_session) <= cfg.max_abs_slope
                    else (
                        Evidence(
                            f"base slopes {shape.slope_pct_per_session * 100:+.2f}%/session; "
                            "a rising base is a channel and a falling one is a decline",
                            measured=shape.slope_pct_per_session,
                        ),
                    )
                ),
            )
        )

        # -- depth (required): flat means flat
        components.append(
            ComponentScore(
                name="depth",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    shape.depth_pct,
                    ideal_low=cfg.ideal_depth_low,
                    ideal_high=cfg.ideal_depth_high,
                    tolerance_low=0.01,
                    tolerance_high=cfg.max_depth,
                ),
                weight=weights["depth"],
                measurements={"depth_pct": shape.depth_pct},
                contradicting=(
                    (
                        Evidence(
                            f"base is {shape.depth_pct:.1%} deep, beyond what a flat base "
                            "means; this is a correction",
                            measured=shape.depth_pct,
                        ),
                    )
                    if shape.depth_pct > cfg.max_depth
                    else ()
                ),
            )
        )

        # -- duration (required)
        components.append(
            ComponentScore(
                name="duration",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    float(shape.sessions),
                    ideal_low=float(cfg.ideal_sessions_low),
                    ideal_high=float(cfg.ideal_sessions_high),
                    tolerance_low=float(cfg.min_sessions),
                    tolerance_high=float(cfg.max_sessions),
                    floor=20.0,
                ),
                weight=weights["duration"],
                measurements={"sessions": float(shape.sessions)},
            )
        )

        # -- resistance consistency
        resistance = self._resistance(inputs, structure)
        if resistance is None:
            components.append(
                unavailable(
                    "resistance_consistency",
                    weights["resistance_consistency"],
                    "no resistance level could be established",
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="resistance_consistency",
                    score=resistance.confidence,
                    weight=weights["resistance_consistency"],
                    measurements={
                        "touches": float(resistance.touch_count),
                        "level": resistance.level,
                    },
                    evidence=(
                        (
                            Evidence(
                                f"ceiling at {resistance.level:.2f} tested "
                                f"{resistance.touch_count} times",
                                measured=float(resistance.touch_count),
                            ),
                        )
                        if resistance.touch_count >= cfg.min_resistance_touches
                        else ()
                    ),
                )
            )

        # -- compression
        volatility = measure_volatility(
            inputs.bars, start_index=start, end_index=end, atr=inputs.atr
        )
        if volatility is None:
            components.append(
                unavailable(
                    "compression", weights["compression"], "base too short to measure ATR change"
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="compression",
                    score=float(
                        np.clip(
                            0.6 * decay_score(volatility.atr_ratio, full_at=0.7, zero_at=1.2)
                            + 0.4
                            * decay_score(volatility.mean_range_pct, full_at=0.01, zero_at=0.05),
                            0,
                            100,
                        )
                    ),
                    weight=weights["compression"],
                    measurements={
                        "atr_ratio": volatility.atr_ratio,
                        "mean_range_pct": volatility.mean_range_pct,
                    },
                )
            )

        # -- volume
        volume = measure_volume(
            inputs.bars,
            start_index=start,
            end_index=end,
            baseline_start=max(0, start - cfg.prior_trend_sessions),
            baseline_end=start - 1,
        )
        if volume is None:
            components.append(
                unavailable(
                    "volume_character", weights["volume_character"], "base too short for volume"
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="volume_character",
                    score=decay_score(volume.ratio, full_at=0.7, zero_at=1.35),
                    weight=weights["volume_character"],
                    measurements={"ratio": volume.ratio, "late_ratio": volume.late_ratio},
                )
            )

        # -- relative strength
        components.append(self._score_rs(inputs, start, inputs.last_index))
        return components

    def _score_rs(self, inputs: DetectionInputs, start: int, end: int) -> ComponentScore:
        weight = self.config.weights["relative_strength"]
        if inputs.context is None or inputs.context.benchmark_closes is None:
            return unavailable(
                "relative_strength", weight, "no benchmark series supplied in the context"
            )
        profile = measure_relative_strength(
            [float(b.close) for b in inputs.bars],
            list(inputs.context.benchmark_closes),
            start_index=start,
            end_index=end,
        )
        if profile is None:
            return unavailable(
                "relative_strength", weight, "benchmark series contains non-positive prices"
            )
        return ComponentScore(
            name="relative_strength",
            score=ramp_score(profile.excess_return, zero_at=-0.10, full_at=0.04),
            weight=weight,
            measurements={"excess_return": profile.excess_return},
            evidence=(
                (
                    Evidence(
                        f"held {profile.excess_return:+.1%} against the benchmark while basing",
                        EvidenceKind.ANALYTIC,
                        measured=profile.excess_return,
                    ),
                )
                if profile.excess_return >= 0
                else ()
            ),
        )

    # -- structure -----------------------------------------------------------

    def _resistance(self, inputs: DetectionInputs, structure: Structure) -> Boundary | None:
        end = int(structure.parts["structure_end"])
        return horizontal_resistance(
            inputs.bars,
            inputs.swing_highs,
            start_index=structure.start_index,
            end_index=end,
            tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
            min_touches=self.config.min_resistance_touches,
        ) or single_extreme_resistance(
            inputs.bars, start_index=structure.start_index, end_index=end
        )

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        start = structure.start_index
        end = int(structure.parts["structure_end"])
        window = inputs.bars[start : end + 1]
        peak = max(window, key=lambda b: b.high)
        trough = min(window, key=lambda b: b.low)
        return PatternGeometry(
            start_date=inputs.bars[start].session_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "base": (
                    inputs.bars[start].session_date,
                    inputs.bars[inputs.last_index].session_date,
                )
            },
            resistance=self._resistance(inputs, structure),
            support=structural_support(
                inputs.bars,
                inputs.swing_lows,
                start_index=start,
                end_index=end,
                tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
            ),
            key_points={
                "base_high": PricePoint(peak.session_date, float(peak.high)),
                "base_low": PricePoint(trough.session_date, float(trough.low)),
            },
            swing_highs=tuple(
                PricePoint(inputs.bars[s.index].session_date, s.price)
                for s in inputs.swing_highs
                if start <= s.index <= end
            ),
            swing_lows=tuple(
                PricePoint(inputs.bars[s.index].session_date, s.price)
                for s in inputs.swing_lows
                if start <= s.index <= end
            ),
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the base's low the flat base is not shallow any more.

        Measured from the base low rather than a support cluster, because a flat
        base's defining property is its depth: breaking the low does not merely
        break a level, it makes the structure a different depth than the one
        that was classified.
        """
        end = int(structure.parts["structure_end"])
        low = min(float(b.low) for b in inputs.bars[structure.start_index : end + 1])
        return float(low * (1.0 - self.engine_config.states.invalidation_buffer_pct))
