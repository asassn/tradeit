"""Base on Base.

**A statement about what did *not* happen.** Every other continuation family
here measures a structure. This one measures a relationship between two of them:
price built a base, failed to advance meaningfully out of it, and built another
at the same level without giving ground. The claim is that supply was absorbed
twice at one price while the second base gave up nothing — which is a different
and more specific thing than "there are two bases in this chart".

That is why `ceiling_progression` scores **small** advances highest. Every other
detector in this package rewards more movement; here a large advance between the
two bases means they are two bases in a rising sequence — a stair-step, which is
both more common and a weaker claim, and which the detector must not absorb.

**Structural definition.**

1. **Prior advance.** The sequence is a pause in an uptrend, so it needs one.
2. **First base.** A shallow range under a ceiling.
3. **Little or no progress.** The second ceiling sits close to the first.
4. **Second base**, adjacent to the first, whose low does not undercut the
   first's. A lower second base is a descending sequence and the opposite claim.
5. **Tightening**, optionally: a second base tighter than the first is the
   constructive case and is scored, not required.

**Not a long base with a bump in it.** The two bases must be separated by an
identifiable transition — the first base's ceiling being cleared — and each must
independently satisfy the duration and depth requirements. A single sixty-session
range does not become base-on-base because a line can be drawn through its
middle.

**Discovery is structural.** Each base runs from the earliest confirmed high at
its ceiling level to the last session before the next base begins, which is the
same earliest-at-level rule ADR-0014 forced on the VCP and the cup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
from tradeit.patterns.config import BaseOnBaseConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_prior_trend, measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import (
    ConsolidationShape,
    horizontal_resistance,
    measure_consolidation,
)
from tradeit.patterns.swings import Swing


class BaseOnBaseDetector(BaseDetector):
    """Finds two consecutive bases at a similar level."""

    name = "base_on_base"
    pattern_type = PatternType.BASE_ON_BASE

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=(
            "prior_trend",
            "first_base",
            "second_base",
            "ceiling_progression",
            "low_progression",
        ),
        optional_components=("tightening", "volume_character"),
        minimum_evidence_coverage=65.0,
    )

    @property
    def config(self) -> BaseOnBaseConfig:
        return self.engine_config.base_on_base

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            2 * self.config.min_base_sessions
            + self.config.prior_trend_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def _base_start(self, highs: Sequence[Swing], ceiling_index: int, tolerance: float) -> int:
        """Earliest confirmed high at the ceiling's level, walking back.

        The rule ADR-0014 records. Taking the *latest* high at a level truncates
        the base to its final leg, which then measures as far shorter and far
        tighter than the structure that actually formed.
        """
        level = next(s.price for s in highs if s.index == ceiling_index)
        candidates = [
            s for s in highs if s.index <= ceiling_index and s.price >= level * (1.0 - tolerance)
        ]
        return candidates[0].index if candidates else ceiling_index

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        cfg = self.config
        end = inputs.structure_end
        highs = [s for s in inputs.swing_highs if s.index <= end]
        if len(highs) < 2:
            return []

        tolerance = self.engine_config.swings.touch_tolerance_pct
        out: list[Structure] = []
        seen: set[tuple[int, int]] = set()

        # The second base is the one that is still open: it ends at the
        # structure boundary, and its ceiling is the highest confirmed high
        # inside it. Anchoring on the open base rather than searching pairs is
        # what keeps the choice causal.
        for second_ceiling in sorted(highs, key=lambda s: s.index, reverse=True):
            second_start = self._base_start(highs, second_ceiling.index, tolerance)
            second_sessions = end - second_start + 1
            if not cfg.min_base_sessions <= second_sessions <= cfg.max_base_sessions:
                continue

            earlier = [s for s in highs if s.index < second_start]
            if not earlier:
                continue
            first_ceiling = max(earlier, key=lambda s: s.price)
            first_start = self._base_start(earlier, first_ceiling.index, tolerance)
            first_end = second_start - 1
            first_sessions = first_end - first_start + 1
            if not cfg.min_base_sessions <= first_sessions <= cfg.max_base_sessions:
                continue
            if second_start - first_end - 1 > cfg.max_gap_sessions:
                continue

            first = measure_consolidation(inputs.bars, start_index=first_start, end_index=first_end)
            second = measure_consolidation(inputs.bars, start_index=second_start, end_index=end)
            if first is None or second is None:
                continue
            if first.depth_pct > cfg.max_base_depth or second.depth_pct > cfg.max_base_depth:
                continue

            if first.high <= 0:
                continue
            advance = (second.high - first.high) / first.high
            if not -cfg.max_low_undercut <= advance <= cfg.max_ceiling_advance:
                continue

            undercut = (first.low - second.low) / first.low if first.low > 0 else 1.0
            if undercut > cfg.max_low_undercut:
                continue

            trend = measure_prior_trend(
                inputs.bars, end_index=first_start, lookback=cfg.prior_trend_sessions
            )
            if trend is None or trend.gain_pct < cfg.min_prior_gain:
                continue

            key = (first_start, second_start)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Structure(
                    start_index=first_start,
                    end_index=inputs.last_index,
                    parts={
                        "first": first,
                        "second": second,
                        "trend": trend,
                        "advance": advance,
                        "undercut": undercut,
                        "structure_end": end,
                    },
                    reason=(
                        f"two bases of {first_sessions} and {second_sessions} sessions, "
                        f"ceiling advancing {advance:+.1%}"
                    ),
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        first: ConsolidationShape = structure.parts["first"]
        second: ConsolidationShape = structure.parts["second"]
        trend = structure.parts["trend"]
        advance = float(structure.parts["advance"])
        undercut = float(structure.parts["undercut"])
        components: list[ComponentScore] = []

        components.append(
            ComponentScore(
                name="prior_trend",
                requirement=ComponentRequirement.REQUIRED,
                score=ramp_score(trend.gain_pct, zero_at=0.0, full_at=0.35),
                weight=weights["prior_trend"],
                measurements={
                    "gain_pct": trend.gain_pct,
                    "time_above_mean": trend.time_above_mean,
                },
            )
        )

        for name, shape in (("first_base", first), ("second_base", second)):
            components.append(
                ComponentScore(
                    name=name,
                    requirement=ComponentRequirement.REQUIRED,
                    score=round(
                        0.6
                        * band_score(
                            shape.depth_pct,
                            ideal_low=0.03,
                            ideal_high=cfg.ideal_base_depth,
                            tolerance_low=0.0,
                            tolerance_high=cfg.max_base_depth,
                        )
                        + 0.4
                        * band_score(
                            float(shape.sessions),
                            ideal_low=float(cfg.min_base_sessions + 3),
                            ideal_high=float(cfg.max_base_sessions * 0.5),
                            tolerance_low=float(cfg.min_base_sessions),
                            tolerance_high=float(cfg.max_base_sessions),
                            floor=25.0,
                        ),
                        6,
                    ),
                    weight=weights[name],
                    measurements={
                        "depth_pct": shape.depth_pct,
                        "sessions": float(shape.sessions),
                        "slope_pct_per_session": shape.slope_pct_per_session,
                    },
                )
            )

        # The component that carries the family. Small is best, and the
        # contradicting evidence names the alternative reading explicitly.
        components.append(
            ComponentScore(
                name="ceiling_progression",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    advance,
                    ideal_low=0.0,
                    ideal_high=cfg.ideal_ceiling_advance,
                    tolerance_low=-cfg.max_low_undercut,
                    tolerance_high=cfg.max_ceiling_advance,
                ),
                weight=weights["ceiling_progression"],
                measurements={
                    "ceiling_advance": advance,
                    "first_ceiling": first.high,
                    "second_ceiling": second.high,
                },
                evidence=(
                    (
                        Evidence(
                            f"the second base formed {advance:+.1%} from the first's ceiling: "
                            "supply absorbed twice at one price",
                            measured=advance,
                        ),
                    )
                    if abs(advance) <= cfg.ideal_ceiling_advance
                    else ()
                ),
                contradicting=(
                    (
                        Evidence(
                            f"the ceiling advanced {advance:.1%} between the bases; that is a "
                            "rising sequence of bases rather than one built on another",
                            measured=advance,
                        ),
                    )
                    if advance > cfg.ideal_ceiling_advance
                    else ()
                ),
            )
        )

        components.append(
            ComponentScore(
                name="low_progression",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    -undercut,
                    ideal_low=0.0,
                    ideal_high=cfg.max_ceiling_advance,
                    tolerance_low=-cfg.max_low_undercut,
                    tolerance_high=cfg.max_ceiling_advance * 1.5,
                    floor=30.0,
                ),
                weight=weights["low_progression"],
                measurements={
                    "low_advance": -undercut,
                    "first_low": first.low,
                    "second_low": second.low,
                },
                contradicting=(
                    (
                        Evidence(
                            f"the second base undercut the first's low by {undercut:.1%}: "
                            "ground was given rather than held",
                            measured=undercut,
                        ),
                    )
                    if undercut > 0
                    else ()
                ),
            )
        )

        # Optional and genuinely optional: a second base no tighter than the
        # first is still base-on-base, just a less compelling one.
        ratio = second.depth_pct / first.depth_pct if first.depth_pct > 0 else 1.0
        components.append(
            ComponentScore(
                name="tightening",
                score=decay_score(ratio, full_at=0.5, zero_at=1.6),
                weight=weights["tightening"],
                measurements={"depth_ratio": ratio},
                evidence=(
                    (
                        Evidence(
                            f"the second base is {1 - ratio:.0%} tighter than the first",
                            measured=ratio,
                        ),
                    )
                    if ratio < 0.8
                    else ()
                ),
            )
        )

        volume = measure_volume(
            inputs.bars,
            start_index=second.start_index,
            end_index=second.end_index,
            baseline_start=first.start_index,
            baseline_end=first.end_index,
        )
        if volume is None:
            components.append(
                unavailable(
                    "volume_character",
                    weights["volume_character"],
                    "second base too short to measure volume against the first",
                )
            )
        else:
            components.append(
                ComponentScore(
                    name="volume_character",
                    score=decay_score(volume.ratio, full_at=0.75, zero_at=1.5),
                    weight=weights["volume_character"],
                    measurements={
                        "second_over_first": volume.ratio,
                        "dry_session_fraction": volume.dry_session_fraction,
                    },
                    evidence=(
                        (
                            Evidence(
                                f"the second base traded on {1 - volume.ratio:.0%} less volume "
                                "than the first",
                                EvidenceKind.STRUCTURAL,
                                measured=volume.ratio,
                            ),
                        )
                        if volume.ratio < 0.85
                        else ()
                    ),
                )
            )

        return components

    # -- geometry ------------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        first: ConsolidationShape = structure.parts["first"]
        second: ConsolidationShape = structure.parts["second"]
        end = int(structure.parts["structure_end"])

        resistance = horizontal_resistance(
            inputs.bars,
            inputs.swing_highs,
            start_index=second.start_index,
            end_index=end,
            tolerance_pct=self.engine_config.swings.touch_tolerance_pct,
            min_touches=2,
        ) or Boundary(
            kind="resistance",
            method="single_extreme",
            level=second.high,
            anchor_date=second.end_date,
            confidence=25.0,
        )

        return PatternGeometry(
            start_date=first.start_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "first_base": (first.start_date, first.end_date),
                "second_base": (second.start_date, second.end_date),
            },
            resistance=resistance,
            support=Boundary(
                kind="support",
                method="single_extreme",
                level=second.low,
                anchor_date=second.end_date,
                confidence=40.0,
            ),
            key_points={
                "first_ceiling": PricePoint(first.end_date, first.high),
                "first_low": PricePoint(first.start_date, first.low),
                "second_ceiling": PricePoint(second.end_date, second.high),
                "second_low": PricePoint(second.start_date, second.low),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the first base's low the sequence has stopped holding ground."""
        first: ConsolidationShape = structure.parts["first"]
        return float(first.low * (1.0 - self.config.max_low_undercut))


__all__ = ["BaseOnBaseDetector"]
