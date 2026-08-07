"""Cup and Handle.

**Structural definition.**

A multi-stage structure, and modelling it as one shape is why most detectors get
it wrong. The stages, each measured independently:

1. **Prior trend.** A cup forms after an advance. Without one it is a bottoming
   attempt, which is a different structure with different odds.
2. **Left rim.** The high the decline begins from.
3. **Cup decline.** Down into the base.
4. **Cup bottom.** The part that matters most and is easiest to fake. A cup is
   *rounded*: price spends time near the low. Measured as the fraction of cup
   sessions inside a band above the low — a V-shaped reversal spends almost
   none there, and that difference is accumulation versus a bounce.
5. **Right-side recovery** back toward the rim.
6. **Right rim**, level with the left within a tolerance. A right rim far below
   the left is an incomplete recovery.
7. **Handle** — its own substructure, with its own depth, duration and
   boundaries. A handle deeper than a third of the cup is a second decline
   rather than a shakeout.

**No curve fitting.** The detector does not fit a semicircle or a parabola.
Roundness is measured as *time spent near the low*, which is a statement about
price behaviour rather than about the resemblance of a chart to a geometric
figure. Fitting a curve would reward charts that look like a picture of a cup;
this rewards charts where supply was actually absorbed.

**Not a double bottom.** A double bottom has two distinct lows with a rally
between; a cup has one rounded low. Where the geometry genuinely overlaps —
a rounded bottom with a small bump in the middle — both may fire, and the
`bottom_roundness` and `low_symmetry` measurements are what distinguish the
readings.

**Discovery is structural.** The left rim is the highest confirmed swing high
before the deepest confirmed low; the handle begins at the right rim.
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
from tradeit.patterns.config import CupHandleConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import (
    measure_prior_trend,
    measure_relative_strength,
    measure_volume,
)
from tradeit.patterns.scoring import band_score, decay_score, ramp_score


class CupHandleDetector(BaseDetector):
    """Finds cup-and-handle structures."""

    name = "cup_handle"
    pattern_type = PatternType.CUP_WITH_HANDLE

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: The cup and the handle are both the pattern. A cup with no handle is
        #: a rounded base; a handle with no cup is a flag.
        required_components=(
            "cup_depth",
            "cup_duration",
            "bottom_roundness",
            "rim_symmetry",
            "handle_structure",
        ),
        optional_components=("prior_trend", "volume_profile", "relative_strength"),
        minimum_evidence_coverage=60.0,
    )

    @property
    def config(self) -> CupHandleConfig:
        return self.engine_config.cup_handle

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.min_cup_sessions
            + self.config.min_handle_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
            + 10
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """A pair of rims at the same level, and the decline between them.

        **The rims define the cup, not the low.** An earlier draft anchored on
        the deepest confirmed low in range and took the highest high on either
        side. On a real series that finds the pre-advance level rather than a
        cup bottom: a stock that advances 35% and then bases has its deepest low
        of the last two hundred sessions *before* the advance, so the "cup" it
        described spanned the advance itself. The structure a cup actually
        asserts is *price returned to a level it had already reached*, which
        makes the rim pair the anchor and the low a consequence.

        For each confirmed swing high whose remaining sessions form a plausible
        handle, discovery takes the highest confirmed high in the cup window
        before it and then walks back to the **earliest** high at that same
        level -- the earliest-at-level rule ADR-0014 forced on the VCP base, and
        for the same reason: taking the later of two equal highs truncates the
        structure, and taking whichever produced the best score is the selection
        bias the ADR prohibits. The level match is deliberately tight; widening
        it lets the left rim slide back into the advance that preceded the cup,
        which describes a "cup" spanning the advance itself. The bottom is then
        simply the lowest confirmed low between the rims. Nothing is chosen by
        score.
        """
        cfg = self.config
        end = inputs.structure_end
        highs = [s for s in inputs.swing_highs if s.index <= end]
        lows = [s for s in inputs.swing_lows if s.index <= end]
        if len(highs) < 2 or not lows:
            return []

        # Deliberately wider than the scoring tolerance: an incomplete recovery
        # should be found and marked down, not discarded as though it never
        # formed.
        tolerance = cfg.max_rim_asymmetry * cfg.rim_search_multiple

        out: list[Structure] = []
        seen: set[tuple[int, int]] = set()
        for right_rim in sorted(highs, key=lambda s: s.index, reverse=True):
            handle_sessions = end - right_rim.index
            if not cfg.min_handle_sessions <= handle_sessions <= cfg.max_handle_sessions:
                continue

            window_start = max(0, right_rim.index - cfg.max_cup_sessions)
            prior = [s for s in highs if window_start <= s.index < right_rim.index]
            if not prior:
                continue
            level = max(s.price for s in prior)
            left_rim = next(s for s in prior if s.price >= level * (1.0 - cfg.rim_level_tolerance))

            # A right rim far below the left is an incomplete recovery, which is
            # found and marked down; far above it is a breakout to new highs,
            # which is not a cup at all.
            if abs(right_rim.price - left_rim.price) / left_rim.price > tolerance:
                continue

            interior = [s for s in lows if left_rim.index < s.index < right_rim.index]
            if not interior:
                continue
            bottom = min(interior, key=lambda s: s.price)

            cup_sessions = right_rim.index - left_rim.index
            if not cfg.min_cup_sessions <= cup_sessions <= cfg.max_cup_sessions:
                continue
            depth = (left_rim.price - bottom.price) / left_rim.price if left_rim.price > 0 else 0.0
            if not cfg.min_cup_depth <= depth <= cfg.max_cup_depth:
                continue

            key = (left_rim.index, right_rim.index)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Structure(
                    start_index=left_rim.index,
                    end_index=inputs.last_index,
                    parts={
                        "left_rim": left_rim,
                        "bottom": bottom,
                        "right_rim": right_rim,
                        "handle_start": right_rim.index + 1,
                        "structure_end": end,
                        "cup_depth": depth,
                    },
                    reason=f"cup {depth:.1%} deep over {cup_sessions} sessions",
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        left, bottom, right = (
            structure.parts["left_rim"],
            structure.parts["bottom"],
            structure.parts["right_rim"],
        )
        end = int(structure.parts["structure_end"])
        handle_start = int(structure.parts["handle_start"])
        depth = float(structure.parts["cup_depth"])
        components: list[ComponentScore] = []

        # -- cup depth (required)
        components.append(
            ComponentScore(
                name="cup_depth",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    depth,
                    ideal_low=cfg.ideal_cup_depth_low,
                    ideal_high=cfg.ideal_cup_depth_high,
                    tolerance_low=cfg.min_cup_depth,
                    tolerance_high=cfg.max_cup_depth,
                ),
                weight=weights["cup_depth"],
                measurements={"depth_pct": depth},
                evidence=(Evidence(f"cup {depth:.1%} deep", measured=depth),),
            )
        )

        # -- cup duration (required)
        cup_sessions = right.index - left.index
        components.append(
            ComponentScore(
                name="cup_duration",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    float(cup_sessions),
                    ideal_low=float(cfg.ideal_cup_sessions_low),
                    ideal_high=float(cfg.ideal_cup_sessions_high),
                    tolerance_low=float(cfg.min_cup_sessions),
                    tolerance_high=float(cfg.max_cup_sessions),
                    floor=20.0,
                ),
                weight=weights["cup_duration"],
                measurements={"sessions": float(cup_sessions)},
            )
        )

        # -- bottom roundness (required): the component that rejects a V
        window = inputs.bars[left.index : right.index + 1]
        # The band is a fraction of the cup's depth, and membership is judged on
        # the close. A bar whose *low* ticked into the band on the way past did
        # not spend time there, and counting it would score a fast transit as
        # absorption -- which is precisely the V-shaped reversal this component
        # exists to reject.
        band_top = bottom.price + (left.price - bottom.price) * cfg.bottom_band
        near_low = sum(1 for b in window if float(b.close) <= band_top)
        roundness = near_low / len(window) if window else 0.0
        components.append(
            ComponentScore(
                name="bottom_roundness",
                requirement=ComponentRequirement.REQUIRED,
                score=ramp_score(
                    roundness,
                    zero_at=cfg.min_bottom_fraction * 0.4,
                    full_at=cfg.min_bottom_fraction * 2.2,
                ),
                weight=weights["bottom_roundness"],
                measurements={"bottom_fraction": roundness, "band": cfg.bottom_band},
                evidence=(
                    (
                        Evidence(
                            f"price spent {roundness:.0%} of the cup within "
                            f"{cfg.bottom_band:.0%} of the low: a rounded bottom",
                            measured=roundness,
                        ),
                    )
                    if roundness >= cfg.min_bottom_fraction
                    else ()
                ),
                contradicting=(
                    (
                        Evidence(
                            f"price spent only {roundness:.0%} near the low; a V-shaped "
                            "reversal is a bounce rather than absorption",
                            measured=roundness,
                        ),
                    )
                    if roundness < cfg.min_bottom_fraction
                    else ()
                ),
            )
        )

        # -- rim symmetry (required)
        asymmetry = abs(right.price - left.price) / left.price if left.price > 0 else 1.0
        components.append(
            ComponentScore(
                name="rim_symmetry",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(asymmetry, full_at=0.01, zero_at=cfg.max_rim_asymmetry),
                weight=weights["rim_symmetry"],
                measurements={
                    "asymmetry": asymmetry,
                    "left_rim": left.price,
                    "right_rim": right.price,
                },
                contradicting=(
                    (
                        Evidence(
                            f"right rim sits {asymmetry:.1%} from the left; the recovery is "
                            "incomplete",
                            measured=asymmetry,
                        ),
                    )
                    if asymmetry > cfg.max_rim_asymmetry
                    else ()
                ),
            )
        )

        # -- handle (required): its own substructure
        handle = inputs.bars[handle_start : end + 1]
        if len(handle) < cfg.min_handle_sessions:
            components.append(
                unavailable(
                    "handle_structure",
                    weights["handle_structure"],
                    f"handle is {len(handle)} sessions; {cfg.min_handle_sessions} needed",
                )
            )
        else:
            handle_high = max(float(b.high) for b in handle)
            handle_low = min(float(b.low) for b in handle)
            handle_depth = (handle_high - handle_low) / handle_high if handle_high > 0 else 0.0
            cup_height = left.price - bottom.price
            depth_ratio = (right.price - handle_low) / cup_height if cup_height > 0 else 1.0
            duration_score = band_score(
                float(len(handle)),
                ideal_low=float(cfg.min_handle_sessions + 2),
                ideal_high=float(cfg.max_handle_sessions * 0.6),
                tolerance_low=float(cfg.min_handle_sessions),
                tolerance_high=float(cfg.max_handle_sessions),
                floor=25.0,
            )
            depth_score = band_score(
                depth_ratio,
                ideal_low=0.05,
                ideal_high=cfg.ideal_handle_depth_ratio,
                tolerance_low=0.0,
                tolerance_high=cfg.max_handle_depth_ratio,
            )
            components.append(
                ComponentScore(
                    name="handle_structure",
                    requirement=ComponentRequirement.REQUIRED,
                    score=float(np.clip(0.6 * depth_score + 0.4 * duration_score, 0, 100)),
                    weight=weights["handle_structure"],
                    measurements={
                        "handle_depth_pct": handle_depth,
                        "depth_ratio_of_cup": depth_ratio,
                        "sessions": float(len(handle)),
                    },
                    evidence=(
                        (
                            Evidence(
                                f"handle retraced {depth_ratio:.0%} of the cup over "
                                f"{len(handle)} sessions",
                                measured=depth_ratio,
                            ),
                        )
                        if depth_ratio <= cfg.max_handle_depth_ratio
                        else ()
                    ),
                    contradicting=(
                        (
                            Evidence(
                                f"handle retraced {depth_ratio:.0%} of the cup; a handle "
                                "deeper than a third is a second decline, not a shakeout",
                                measured=depth_ratio,
                            ),
                        )
                        if depth_ratio > cfg.max_handle_depth_ratio
                        else ()
                    ),
                )
            )

        # -- prior trend (optional here: a cup can form after a modest advance)
        trend = measure_prior_trend(
            inputs.bars, end_index=left.index, lookback=cfg.prior_trend_sessions
        )
        if trend is None:
            components.append(
                unavailable(
                    "prior_trend", weights["prior_trend"], "insufficient history before the cup"
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="prior_trend",
                    score=ramp_score(trend.gain_pct, zero_at=-0.05, full_at=0.25),
                    weight=weights["prior_trend"],
                    measurements={"gain_pct": trend.gain_pct},
                )
            )

        # -- volume: dry through the base, returning on the right side
        volume = measure_volume(
            inputs.bars,
            start_index=left.index,
            end_index=end,
            baseline_start=max(0, left.index - cfg.prior_trend_sessions),
            baseline_end=left.index - 1,
        )
        if volume is None:
            components.append(
                unavailable("volume_profile", weights["volume_profile"], "cup too short")
            )
        else:
            components.append(
                ComponentScore(
                    name="volume_profile",
                    score=decay_score(volume.ratio, full_at=0.7, zero_at=1.4),
                    weight=weights["volume_profile"],
                    measurements={"ratio": volume.ratio, "late_ratio": volume.late_ratio},
                )
            )

        components.append(self._score_rs(inputs, left.index, inputs.last_index))
        return components

    def _score_rs(self, inputs: DetectionInputs, start: int, end: int) -> ComponentScore:
        weight = self.config.weights["relative_strength"]
        if inputs.context is None or inputs.context.benchmark_closes is None:
            return unavailable("relative_strength", weight, "no benchmark series supplied")
        profile = measure_relative_strength(
            [float(b.close) for b in inputs.bars],
            list(inputs.context.benchmark_closes),
            start_index=start,
            end_index=end,
        )
        if profile is None:
            return unavailable(
                "relative_strength", weight, "benchmark contains non-positive prices"
            )
        return ComponentScore(
            name="relative_strength",
            score=ramp_score(profile.excess_return, zero_at=-0.15, full_at=0.05),
            weight=weight,
            measurements={"excess_return": profile.excess_return},
            evidence=(
                (
                    Evidence(
                        f"outperformed by {profile.excess_return:+.1%} through the cup",
                        EvidenceKind.ANALYTIC,
                        measured=profile.excess_return,
                    ),
                )
                if profile.excess_return > 0
                else ()
            ),
        )

    # -- structure -----------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        left = structure.parts["left_rim"]
        bottom = structure.parts["bottom"]
        right = structure.parts["right_rim"]
        handle_start = int(structure.parts["handle_start"])
        end = int(structure.parts["structure_end"])
        handle = inputs.bars[handle_start : end + 1]
        handle_low = min((float(b.low) for b in handle), default=right.price)

        return PatternGeometry(
            start_date=inputs.bars[left.index].session_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                # Each stage independently addressable, which is what makes the
                # structure drawable and its parts separately reviewable.
                "cup_decline": (left.session_date, bottom.session_date),
                "cup_recovery": (bottom.session_date, right.session_date),
                "handle": (
                    inputs.bars[handle_start].session_date,
                    inputs.bars[inputs.last_index].session_date,
                ),
            },
            resistance=Boundary(
                kind="resistance",
                method="cup_rim",
                level=max(left.price, right.price),
                anchor_date=left.session_date,
                touches=(
                    PricePoint(left.session_date, left.price),
                    PricePoint(right.session_date, right.price),
                ),
                start_date=left.session_date,
                end_date=right.session_date,
                confidence=55.0,
            ),
            support=Boundary(
                kind="support",
                method="handle_low",
                level=handle_low,
                anchor_date=inputs.bars[handle_start].session_date,
                confidence=35.0,
            ),
            key_points={
                "left_rim": PricePoint(left.session_date, left.price),
                "cup_bottom": PricePoint(bottom.session_date, bottom.price),
                "right_rim": PricePoint(right.session_date, right.price),
                "handle_low": PricePoint(
                    min(handle, key=lambda b: b.low).session_date if handle else right.session_date,
                    handle_low,
                ),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the handle's low the handle has become a decline.

        Not the cup's low: a break of the handle falsifies the pattern long
        before price revisits the base, and waiting for the cup low would keep a
        failed structure alive through a 30% drop.
        """
        handle_start = int(structure.parts["handle_start"])
        end = int(structure.parts["structure_end"])
        handle = inputs.bars[handle_start : end + 1]
        if not handle:
            return float(structure.parts["bottom"].price)
        low = min(float(b.low) for b in handle)
        return float(low * (1.0 - self.engine_config.states.invalidation_buffer_pct))
