"""Pennant.

**Structural definition.**

A sharp impulse followed by a brief, *symmetrically converging* pause. Both
boundaries close on each other: highs falling, lows rising, into a point.

The whole family rests on one distinction, and without it the pennant is
redundant:

**A flag is a channel. A pennant is a wedge.** A bull flag's boundaries run
roughly parallel — the consolidation drifts, keeping its width. A pennant's
boundaries *converge* from both sides. If a detector does not enforce
convergence, every pennant it finds is also a flag and the two families report
the same structures under two names.

The structure:

1. **A sharp impulse.** Same primitive as the flag's flagpole, and required for
   the same reason: a converging range with no impulse before it is a
   symmetrical triangle, which is a neutral continuation structure rather than a
   bullish one.
2. **Converging boundaries.** Late height materially below early height. This is
   the pattern.
3. **Rough symmetry.** Both boundaries move, at comparable rates. A falling
   upper boundary with a flat lower one is a descending wedge; a flat upper with
   rising lows is an ascending triangle. Asymmetry beyond a configured ratio
   means the structure belongs to another family.
4. **Brevity.** A pennant is a pause, not a base. One lasting longer than its
   own impulse has become a triangle.

**Competing interpretations, explicitly.** A structure may be both a pennant and
a flag; the detectors say why each qualifies and the relationship is recorded
rather than resolved. What distinguishes them is measured and reported —
``convergence`` and ``slope_asymmetry`` are on every instance.
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
    PatternGeometry,
    PricePoint,
)
from tradeit.patterns.config import PennantConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_impulse, measure_volatility, measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import fit_line, structural_support


class PennantDetector(BaseDetector):
    """Finds pennants: impulse, then a converging wedge."""

    name = "pennant"
    pattern_type = PatternType.PENNANT

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: Convergence is the pattern; the impulse is what makes it bullish
        #: rather than a neutral symmetrical triangle. Neither is optional.
        required_components=("impulse", "convergence", "symmetry"),
        optional_components=("duration", "compression", "volume_contraction"),
        minimum_evidence_coverage=60.0,
    )

    @property
    def config(self) -> PennantConfig:
        return self.engine_config.pennant

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            20
            + self.config.min_impulse_sessions
            + self.config.min_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """Impulse termination determines the split, exactly as in the flag.

        The impulse ends at its highest confirmed high; the pennant is
        everything after. One structure per confirmed swing low origin, with the
        latest origin preferred — no window search, no score-driven choice.
        """
        cfg = self.config
        end = inputs.structure_end
        highs = [float(b.high) for b in inputs.bars]

        origins = sorted((s.index for s in inputs.swing_lows if s.index >= 20), reverse=True)
        out: list[Structure] = []
        seen: set[int] = set()

        for origin in origins:
            span = highs[origin : end + 1]
            if not span:
                continue
            impulse_end = origin + int(np.argmax(span))
            impulse_sessions = impulse_end - origin
            pennant_sessions = end - impulse_end

            if not cfg.min_impulse_sessions <= impulse_sessions <= cfg.max_impulse_sessions:
                continue
            if not cfg.min_sessions <= pennant_sessions <= cfg.max_sessions:
                continue
            if impulse_end in seen:
                continue

            impulse = measure_impulse(
                inputs.bars, start_index=origin, end_index=impulse_end, atr=inputs.atr
            )
            if impulse is None or impulse.gain_pct < cfg.min_impulse_gain:
                continue

            seen.add(impulse_end)
            out.append(
                Structure(
                    start_index=origin,
                    end_index=inputs.last_index,
                    parts={
                        "impulse": impulse,
                        "impulse_end": impulse_end,
                        "pennant_start": impulse_end + 1,
                        "structure_end": end,
                    },
                    reason=f"impulse {impulse.gain_pct:.1%} then {pennant_sessions} sessions",
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        impulse = structure.parts["impulse"]
        start = int(structure.parts["pennant_start"])
        end = int(structure.parts["structure_end"])

        _, _, convergence, asymmetry = self._boundaries(inputs, start, end)
        components: list[ComponentScore] = []

        # -- impulse (required)
        components.append(
            ComponentScore(
                name="impulse",
                requirement=ComponentRequirement.REQUIRED,
                score=float(
                    np.clip(
                        0.45
                        * ramp_score(
                            impulse.gain_pct, zero_at=cfg.min_impulse_gain * 0.6, full_at=0.25
                        )
                        + 0.30 * ramp_score(impulse.gain_atr, zero_at=2.0, full_at=8.0)
                        + 0.25 * ramp_score(impulse.rate_per_session, zero_at=0.004, full_at=0.025),
                        0,
                        100,
                    )
                ),
                weight=weights["impulse"],
                measurements={
                    "gain_pct": impulse.gain_pct,
                    "gain_atr": impulse.gain_atr,
                    "rate_per_session": impulse.rate_per_session,
                    "sessions": float(impulse.sessions),
                },
                evidence=(
                    Evidence(
                        f"impulse of {impulse.gain_pct:+.1%} over {impulse.sessions} sessions "
                        f"({impulse.rate_per_session:.1%}/session)",
                        measured=impulse.gain_pct,
                    ),
                ),
            )
        )

        # -- convergence (required): the defining property
        components.append(
            ComponentScore(
                name="convergence",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(convergence, full_at=cfg.ideal_convergence * 0.6, zero_at=1.0),
                weight=weights["convergence"],
                measurements={"height_ratio": convergence},
                evidence=(
                    (
                        Evidence(
                            f"range converged to {convergence:.2f}x its opening height",
                            measured=convergence,
                        ),
                    )
                    if convergence <= cfg.ideal_convergence
                    else ()
                ),
                contradicting=(
                    (
                        Evidence(
                            f"boundaries stayed {convergence:.2f}x apart; roughly parallel "
                            "boundaries are a flag's channel, not a pennant's wedge",
                            measured=convergence,
                        ),
                    )
                    if convergence > cfg.min_convergence + 0.4
                    else ()
                ),
            )
        )

        # -- symmetry (required): distinguishes pennant from wedge/triangle
        components.append(
            ComponentScore(
                name="symmetry",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(asymmetry, full_at=1.0, zero_at=cfg.max_slope_asymmetry * 1.5),
                weight=weights["symmetry"],
                measurements={"slope_asymmetry": asymmetry},
                contradicting=(
                    (
                        Evidence(
                            f"boundaries converge {asymmetry:.1f}x asymmetrically; a flat "
                            "boundary with one moving side is a triangle or a wedge",
                            measured=asymmetry,
                        ),
                    )
                    if asymmetry > cfg.max_slope_asymmetry
                    else ()
                ),
            )
        )

        # -- duration
        sessions = end - start + 1
        ratio = sessions / impulse.sessions if impulse.sessions > 0 else float("inf")
        components.append(
            ComponentScore(
                name="duration",
                score=float(
                    np.clip(
                        0.5
                        * band_score(
                            float(sessions),
                            ideal_low=float(cfg.min_sessions + 1),
                            ideal_high=float(cfg.max_sessions * 0.6),
                            tolerance_low=float(cfg.min_sessions),
                            tolerance_high=float(cfg.max_sessions),
                            floor=20.0,
                        )
                        + 0.5 * decay_score(ratio, full_at=0.5, zero_at=cfg.max_duration_ratio),
                        0,
                        100,
                    )
                ),
                weight=weights["duration"],
                measurements={"sessions": float(sessions), "duration_ratio": ratio},
                contradicting=(
                    (
                        Evidence(
                            f"pennant has run {ratio:.1f}x its own impulse; a pause that long "
                            "has become a triangle",
                            measured=ratio,
                        ),
                    )
                    if ratio > cfg.max_duration_ratio
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
                    "compression",
                    weights["compression"],
                    f"pennant is {sessions} sessions; too short to compare halves",
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="compression",
                    score=float(
                        np.clip(
                            0.6 * decay_score(volatility.atr_ratio, full_at=0.5, zero_at=1.1)
                            + 0.4
                            * ramp_score(volatility.narrowing_fraction, zero_at=0.35, full_at=0.65),
                            0,
                            100,
                        )
                    ),
                    weight=weights["compression"],
                    measurements={
                        "atr_ratio": volatility.atr_ratio,
                        "narrowing_fraction": volatility.narrowing_fraction,
                    },
                )
            )

        # -- volume
        volume = measure_volume(
            inputs.bars,
            start_index=start,
            end_index=end,
            baseline_start=impulse.start_index,
            baseline_end=impulse.end_index,
        )
        if volume is None:
            components.append(
                unavailable(
                    "volume_contraction",
                    weights["volume_contraction"],
                    "pennant window too short to measure volume",
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="volume_contraction",
                    score=decay_score(volume.ratio, full_at=0.5, zero_at=1.2),
                    weight=weights["volume_contraction"],
                    measurements={"ratio": volume.ratio},
                )
            )
        return components

    # -- structure -----------------------------------------------------------

    def _boundaries(
        self, inputs: DetectionInputs, start: int, end: int
    ) -> tuple[Boundary | None, Boundary | None, float, float]:
        """Fit both converging boundaries and measure how they close.

        Convergence is measured on the *fitted* boundaries rather than on raw
        extremes, because two noisy bars at opposite ends of the window would
        otherwise decide the answer.
        """
        highs = [(i, float(inputs.bars[i].high)) for i in range(start, end + 1)]
        lows = [(i, float(inputs.bars[i].low)) for i in range(start, end + 1)]
        if len(highs) < 4:
            return None, None, 1.0, 1.0

        upper_fit = fit_line([i for i, _ in highs], [p for _, p in highs], anchor_index=start)
        lower_fit = fit_line([i for i, _ in lows], [p for _, p in lows], anchor_index=start)

        opening = upper_fit.value_at(start) - lower_fit.value_at(start)
        closing = upper_fit.value_at(end) - lower_fit.value_at(end)
        convergence = closing / opening if opening > 0 else 1.0

        upper_move = abs(upper_fit.slope_per_session)
        lower_move = abs(lower_fit.slope_per_session)
        larger, smaller = max(upper_move, lower_move), min(upper_move, lower_move)
        asymmetry = (
            larger / smaller if smaller > 1e-9 else float(self.config.max_slope_asymmetry * 2)
        )

        upper = Boundary(
            kind="resistance",
            method="regression_channel",
            level=upper_fit.value_at(start),
            anchor_date=inputs.bars[start].session_date,
            slope_per_session=upper_fit.slope_per_session,
            start_date=inputs.bars[start].session_date,
            end_date=inputs.bars[end].session_date,
            confidence=min(100.0, upper_fit.r_squared * 100.0),
        )
        lower = Boundary(
            kind="support",
            method="regression_channel",
            level=lower_fit.value_at(start),
            anchor_date=inputs.bars[start].session_date,
            slope_per_session=lower_fit.slope_per_session,
            start_date=inputs.bars[start].session_date,
            end_date=inputs.bars[end].session_date,
            confidence=min(100.0, lower_fit.r_squared * 100.0),
        )
        return upper, lower, float(np.clip(convergence, 0.0, 3.0)), float(asymmetry)

    def resistance_of(self, geometry: PatternGeometry) -> Boundary | None:
        """The breakout level is the impulse high, not the sloping upper line.

        A pennant's upper boundary falls, so using it would declare a breakout
        at a level below where the impulse stalled -- progressively lower the
        longer the pennant lasts, which is precisely backwards.
        """
        point = geometry.key_points.get("impulse_high")
        if point is None:
            return geometry.resistance
        return Boundary(
            kind="resistance",
            method="impulse_high",
            level=point.price,
            anchor_date=point.session_date,
            confidence=40.0,
        )

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        impulse = structure.parts["impulse"]
        start = int(structure.parts["pennant_start"])
        end = int(structure.parts["structure_end"])
        upper, lower, _, _ = self._boundaries(inputs, start, end)

        return PatternGeometry(
            start_date=inputs.bars[structure.start_index].session_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "impulse": (impulse.start_date, impulse.end_date),
                "pennant": (
                    inputs.bars[start].session_date,
                    inputs.bars[inputs.last_index].session_date,
                ),
            },
            resistance=upper,
            support=lower,
            key_points={
                "impulse_low": PricePoint(impulse.start_date, impulse.low),
                "impulse_high": PricePoint(impulse.end_date, impulse.high),
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
        """Below the pennant's low the wedge has broken downward."""
        start = int(structure.parts["pennant_start"])
        end = int(structure.parts["structure_end"])
        low = min(float(b.low) for b in inputs.bars[start : end + 1])
        support = structural_support(
            inputs.bars,
            inputs.swing_lows,
            start_index=start,
            end_index=end,
            tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
        )
        level = min(low, support.level) if support is not None else low
        return float(level * (1.0 - self.engine_config.states.invalidation_buffer_pct))

    def notes(self, structure: Structure) -> tuple[str, ...]:
        impulse = structure.parts["impulse"]
        return (
            f"impulse {impulse.gain_pct:+.1%} over {impulse.sessions} sessions; "
            "classified pennant rather than bull flag because the boundaries converge "
            "rather than running parallel",
        )
