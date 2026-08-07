"""Ascending Triangle.

**Structural definition.**

A horizontal ceiling that price repeatedly fails to clear, with each pullback
stopping higher than the last. Supply sits at one level while demand rises to
meet it, and the two boundaries converge.

Four properties, and the second is what makes it *ascending* rather than a
rectangle:

1. **Flat resistance.** Two or more confirmed swing highs clustered at one
   level. "Flat" within a tolerance — real triangles are not drawn with a
   ruler, and demanding a perfect line finds none of them.
2. **Rising lows.** Two or more confirmed swing lows in ascending sequence, with
   a positive fitted slope. Flat lows make it a rectangle; falling lows make it
   a descending triangle, which is a bearish structure this system does not
   trade.
3. **Convergence.** The vertical distance between the boundaries closes
   materially. Two parallel boundaries are a channel.
4. **Outlier tolerance.** A configurable fraction of pivots may sit outside the
   boundaries. Real triangles overshoot; zero tolerance rejects every genuine
   one.

**Not a pennant.** A pennant's *upper* boundary falls; an ascending triangle's
is flat. Both converge, and that shared property is why they are so often
confused — the difference is entirely in whether supply is retreating or
holding a line.

**Discovery is structural.** The triangle begins at the earliest confirmed swing
high in the resistance cluster: the first time price was turned away at the
level that defines it.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise

import numpy as np

from tradeit.core.enums import PatternType
from tradeit.patterns.base import (
    ComponentRequirement,
    ComponentScore,
    DetectorContract,
    Evidence,
    PatternGeometry,
    PricePoint,
)
from tradeit.patterns.config import AscendingTriangleConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_prior_trend, measure_volatility, measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import fit_line, horizontal_resistance, sloped_boundary


class AscendingTriangleDetector(BaseDetector):
    """Finds ascending triangles."""

    name = "ascending_triangle"
    pattern_type = PatternType.ASCENDING_TRIANGLE

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: Both boundaries are the pattern. Without a flat ceiling or without
        #: rising lows there is no triangle -- there is a rectangle, a wedge, or
        #: nothing.
        required_components=("resistance_flatness", "rising_lows", "convergence"),
        optional_components=("touch_quality", "duration", "compression", "prior_context"),
        minimum_evidence_coverage=55.0,
    )

    @property
    def config(self) -> AscendingTriangleConfig:
        return self.engine_config.ascending_triangle

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.min_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
            + 10
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """The triangle begins at the first rejection from its ceiling.

        The ceiling is discovered first, as a cluster of confirmed swing highs.
        The structure then starts at the *earliest* member of that cluster --
        the first time price was turned away at the level that defines the
        pattern. Nothing here scores anything.
        """
        cfg = self.config
        end = inputs.structure_end
        earliest = max(0, end - cfg.max_sessions)
        latest = end - cfg.min_sessions
        if latest <= earliest:
            return []

        ceiling = horizontal_resistance(
            inputs.bars,
            inputs.swing_highs,
            start_index=earliest,
            end_index=end,
            tolerance_pct=cfg.max_resistance_scatter,
            min_touches=cfg.min_resistance_touches,
        )
        if ceiling is None or not ceiling.touches:
            return []

        cluster = [
            s
            for s in inputs.swing_highs
            if earliest <= s.index <= end
            and abs(s.price - ceiling.level) <= ceiling.level * cfg.max_resistance_scatter
        ]
        if len(cluster) < cfg.min_resistance_touches:
            return []

        # The touches must be *contiguous in time*. Without this the cluster
        # reaches back to any historical high that happens to sit at the same
        # price -- on a generated 40-session triangle it produced an 83-session
        # structure whose "rising lows" were lead-in noise and whose measured
        # slope came out negative. A high eighty sessions before the next touch
        # is not part of the same structure; it is a coincidence of price.
        cluster.sort(key=lambda s: s.index)
        contiguous = [cluster[-1]]
        for earlier in reversed(cluster[:-1]):
            if contiguous[-1].index - earlier.index > cfg.max_touch_gap:
                break
            contiguous.append(earlier)
        cluster = sorted(contiguous, key=lambda s: s.index)
        if len(cluster) < cfg.min_resistance_touches:
            return []

        start = min(s.index for s in cluster)
        if end - start < cfg.min_sessions:
            return []

        lows = [s for s in inputs.swing_lows if start <= s.index <= end]
        if len(lows) < cfg.min_rising_lows:
            return []

        return [
            Structure(
                start_index=start,
                end_index=inputs.last_index,
                parts={
                    "structure_end": end,
                    "ceiling": ceiling,
                    "cluster_size": len(cluster),
                    "lows": lows,
                },
                reason=f"ceiling {ceiling.level:.2f} tested {len(cluster)} times",
            )
        ]

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        start = structure.start_index
        end = int(structure.parts["structure_end"])
        ceiling = structure.parts["ceiling"]
        lows = structure.parts["lows"]

        components: list[ComponentScore] = []

        # -- resistance flatness (required)
        prices = np.array([t.price for t in ceiling.touches])
        scatter = float(prices.std() / ceiling.level) if ceiling.level > 0 else 1.0
        components.append(
            ComponentScore(
                name="resistance_flatness",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(scatter, full_at=0.004, zero_at=cfg.max_resistance_scatter),
                weight=weights["resistance_flatness"],
                measurements={
                    "scatter": scatter,
                    "level": ceiling.level,
                    "touches": float(ceiling.touch_count),
                },
                evidence=(
                    (
                        Evidence(
                            f"ceiling at {ceiling.level:.2f} held across "
                            f"{ceiling.touch_count} tests within {scatter:.2%}",
                            measured=scatter,
                        ),
                    )
                    if scatter <= cfg.max_resistance_scatter * 0.5
                    else ()
                ),
            )
        )

        # -- rising lows (required): what makes it ascending
        fit = fit_line([s.index for s in lows], [s.price for s in lows], anchor_index=lows[0].index)
        mean_price = float(np.mean([s.price for s in lows]))
        slope_pct = fit.slope_per_session / mean_price if mean_price > 0 else 0.0
        ascending = sum(1 for a, b in pairwise(lows) if b.price > a.price)
        ascending_fraction = ascending / max(1, len(lows) - 1)

        slope_score = ramp_score(slope_pct, zero_at=0.0, full_at=cfg.min_lower_slope * 4)
        sequence_score = ramp_score(ascending_fraction, zero_at=0.4, full_at=1.0)
        fit_score = ramp_score(fit.r_squared, zero_at=0.1, full_at=0.7)
        components.append(
            ComponentScore(
                name="rising_lows",
                requirement=ComponentRequirement.REQUIRED,
                score=float(
                    np.clip(0.45 * slope_score + 0.35 * sequence_score + 0.20 * fit_score, 0, 100)
                ),
                weight=weights["rising_lows"],
                measurements={
                    "slope_pct_per_session": slope_pct,
                    "ascending_fraction": ascending_fraction,
                    "r_squared": fit.r_squared,
                    "low_count": float(len(lows)),
                },
                evidence=(
                    (
                        Evidence(
                            f"{len(lows)} lows rising at "
                            f"{slope_pct * 100:+.2f}%/session into the ceiling",
                            measured=slope_pct,
                        ),
                    )
                    if slope_pct >= cfg.min_lower_slope
                    else ()
                ),
                contradicting=(
                    ()
                    if slope_pct >= cfg.min_lower_slope
                    else (
                        Evidence(
                            f"lows are not rising ({slope_pct * 100:+.2f}%/session); flat lows "
                            "make this a rectangle, falling lows a descending triangle",
                            measured=slope_pct,
                        ),
                    )
                ),
            )
        )

        # -- convergence (required)
        first_gap = ceiling.level - lows[0].price
        last_gap = ceiling.level - lows[-1].price
        convergence = 1.0 - (last_gap / first_gap) if first_gap > 0 else 0.0
        components.append(
            ComponentScore(
                name="convergence",
                requirement=ComponentRequirement.REQUIRED,
                score=ramp_score(convergence, zero_at=0.05, full_at=cfg.ideal_convergence),
                weight=weights["convergence"],
                measurements={
                    "convergence": convergence,
                    "first_gap": first_gap,
                    "last_gap": last_gap,
                },
                evidence=(
                    (
                        Evidence(
                            f"boundaries converged {convergence:.0%} toward the apex",
                            measured=convergence,
                        ),
                    )
                    if convergence >= cfg.ideal_convergence * 0.6
                    else ()
                ),
                contradicting=(
                    (
                        Evidence(
                            f"boundaries barely converge ({convergence:.0%}); two roughly "
                            "parallel boundaries are a channel",
                            measured=convergence,
                        ),
                    )
                    if convergence < 0.15
                    else ()
                ),
            )
        )

        # -- touch quality: outliers tolerated, counted
        window_highs = [s for s in inputs.swing_highs if start <= s.index <= end]
        outside = sum(
            1
            for s in window_highs
            if s.price > ceiling.level * (1 + cfg.max_resistance_scatter * 1.5)
        )
        outlier_fraction = outside / max(1, len(window_highs))
        components.append(
            ComponentScore(
                name="touch_quality",
                score=float(
                    np.clip(
                        0.6 * ramp_score(float(ceiling.touch_count), zero_at=1.0, full_at=4.0)
                        + 0.4
                        * decay_score(outlier_fraction, full_at=0.0, zero_at=cfg.outlier_tolerance),
                        0,
                        100,
                    )
                ),
                weight=weights["touch_quality"],
                measurements={
                    "touches": float(ceiling.touch_count),
                    "outlier_fraction": outlier_fraction,
                },
                contradicting=(
                    (
                        Evidence(
                            f"{outlier_fraction:.0%} of highs pierced the ceiling; the level "
                            "is not holding cleanly",
                            measured=outlier_fraction,
                        ),
                    )
                    if outlier_fraction > cfg.outlier_tolerance
                    else ()
                ),
            )
        )

        # -- duration
        sessions = end - start + 1
        components.append(
            ComponentScore(
                name="duration",
                score=band_score(
                    float(sessions),
                    ideal_low=float(cfg.ideal_sessions_low),
                    ideal_high=float(cfg.ideal_sessions_high),
                    tolerance_low=float(cfg.min_sessions),
                    tolerance_high=float(cfg.max_sessions),
                    floor=25.0,
                ),
                weight=weights["duration"],
                measurements={"sessions": float(sessions)},
            )
        )

        # -- compression
        volatility = measure_volatility(
            inputs.bars, start_index=start, end_index=end, atr=inputs.atr
        )
        if volatility is None:
            components.append(
                unavailable("compression", weights["compression"], "window too short for ATR")
            )
        else:
            components.append(
                ComponentScore(
                    name="compression",
                    score=decay_score(volatility.atr_ratio, full_at=0.65, zero_at=1.2),
                    weight=weights["compression"],
                    measurements={"atr_ratio": volatility.atr_ratio},
                )
            )

        # -- prior context: weakly required, because a triangle can form
        # anywhere. Recorded so a triangle after a decline is distinguishable
        # from one after an advance without being rejected.
        trend = measure_prior_trend(inputs.bars, end_index=start, lookback=cfg.prior_trend_sessions)
        if trend is None:
            components.append(
                unavailable(
                    "prior_context",
                    weights["prior_context"],
                    "insufficient history before the triangle",
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="prior_context",
                    score=ramp_score(trend.gain_pct, zero_at=-0.15, full_at=0.20),
                    weight=weights["prior_context"],
                    measurements={"gain_pct": trend.gain_pct},
                )
            )

        volume = measure_volume(inputs.bars, start_index=start, end_index=end)
        if volume is not None:
            components[-1] = ComponentScore(
                name=components[-1].name,
                score=components[-1].score,
                weight=components[-1].weight,
                measurements={**components[-1].measurements, "volume_ratio": volume.ratio},
                evidence=components[-1].evidence,
                contradicting=components[-1].contradicting,
                unavailable=components[-1].unavailable,
                unavailable_reason=components[-1].unavailable_reason,
            )
        return components

    # -- structure -----------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        start = structure.start_index
        end = int(structure.parts["structure_end"])
        ceiling = structure.parts["ceiling"]
        lows = structure.parts["lows"]

        return PatternGeometry(
            start_date=inputs.bars[start].session_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "triangle": (
                    inputs.bars[start].session_date,
                    inputs.bars[inputs.last_index].session_date,
                )
            },
            resistance=ceiling,
            # The rising lower boundary, stored with its coordinates so the
            # dashboard can draw the actual line rather than re-fitting one.
            support=sloped_boundary(
                inputs.bars,
                inputs.swing_lows,
                start_index=start,
                end_index=end,
                kind="support",
                min_points=self.config.min_rising_lows,
            ),
            key_points={
                "first_low": PricePoint(lows[0].session_date, lows[0].price),
                "last_low": PricePoint(lows[-1].session_date, lows[-1].price),
                "apex_level": PricePoint(inputs.bars[end].session_date, ceiling.level),
            },
            swing_highs=tuple(
                PricePoint(inputs.bars[s.index].session_date, s.price)
                for s in inputs.swing_highs
                if start <= s.index <= end
            ),
            swing_lows=tuple(PricePoint(s.session_date, s.price) for s in lows),
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the most recent rising low the ascending sequence is broken.

        Not the triangle's overall low: the pattern's claim is that each
        pullback stops higher, and a break of the *latest* low falsifies that
        while the earlier lows are still intact.
        """
        lows = structure.parts["lows"]
        return float(lows[-1].price * (1.0 - self.engine_config.states.invalidation_buffer_pct))
