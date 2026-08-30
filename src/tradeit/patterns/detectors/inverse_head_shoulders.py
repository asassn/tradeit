"""Inverse Head and Shoulders.

**Not a double bottom with a third low.** The two families share a context — a
prior decline being reversed — and nothing else. A double bottom asserts that
*the same level held twice*, so its defining measurement is how close the two
lows are and its failure mode is a second low that undercuts. This asserts that
*selling exhausted in stages*: a low, a deeper low, then a shallower low, with
each rally reaching a common line. Its defining measurement is the head's
prominence below both shoulders, and a structure whose three lows are all at the
same level is a triangle bottom, not this. The `low_symmetry` component that
carries the double bottom would actively mis-score this pattern.

**Structural definition.**

1. **Prior decline.** The same requirement as any reversal, and the same
   reasoning: without a fall there is nothing to reverse.
2. **Left shoulder.** A confirmed swing low.
3. **Neckline point.** The confirmed swing high after it.
4. **Head.** A confirmed swing low materially *below* both shoulders. Materially
   is the point: `min_head_prominence` is what stops three lows in a range being
   read as a head between shoulders.
5. **Second neckline point.**
6. **Right shoulder**, at a comparable depth to the left.
7. **Neckline** — the line through the two intervening highs. Sloped, because
   real necklines are, and a gently *rising* neckline is the constructive case
   while a falling one says each rally is weaker than the last.

**Asymmetry is normal.** Textbook illustrations are symmetric; real structures
are not, and a detector that demands visual symmetry rejects almost all of them.
`max_shoulder_asymmetry` defaults to 0.40 and `max_timing_asymmetry` to 2.5 —
both deliberately loose — and the tightness of the match is *scored* rather than
required, so a lopsided but real structure appears with a lower number instead
of disappearing.

**Discovery is structural.** Anchored on the most recent confirmed low that
could be a right shoulder; the head is the deepest confirmed low before it
inside the window; the left shoulder is the lowest confirmed low before the
head. Each neckline point is the highest confirmed high in its gap. Nothing is
searched over and scored.
"""

from __future__ import annotations

from collections.abc import Mapping

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
from tradeit.patterns.config import InverseHeadShouldersConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_prior_trend, measure_volume
from tradeit.patterns.scoring import band_score, decay_score
from tradeit.patterns.swings import Swing


class InverseHeadShouldersDetector(BaseDetector):
    """Finds inverse head-and-shoulders bottoms."""

    name = "inverse_head_shoulders"
    pattern_type = PatternType.INVERSE_HEAD_AND_SHOULDERS

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=(
            "head_prominence",
            "shoulder_symmetry",
            "neckline_quality",
            "prior_decline",
        ),
        optional_components=("timing_symmetry", "volume_profile"),
        minimum_evidence_coverage=60.0,
    )

    @property
    def config(self) -> InverseHeadShouldersConfig:
        return self.engine_config.inverse_head_shoulders

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
            + self.config.prior_trend_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        cfg = self.config
        end = inputs.structure_end
        lows = [s for s in inputs.swing_lows if s.index <= end]
        highs = [s for s in inputs.swing_highs if s.index <= end]
        if len(lows) < 3 or len(highs) < 2:
            return []

        out: list[Structure] = []
        seen: set[tuple[int, int, int]] = set()
        for right in sorted(lows, key=lambda s: s.index, reverse=True):
            window_start = max(0, right.index - cfg.max_sessions)
            before = [s for s in lows if window_start <= s.index < right.index]
            if len(before) < 2:
                continue

            head = min(before, key=lambda s: s.price)
            left_candidates = [s for s in before if s.index < head.index]
            if not left_candidates:
                continue
            left = min(left_candidates, key=lambda s: s.price)

            if right.index - left.index < cfg.min_sessions:
                continue

            # The head must be materially below *both* shoulders. Without this,
            # three lows in a range read as a head between shoulders.
            shallower = min(left.price, right.price)
            prominence = (shallower - head.price) / shallower if shallower > 0 else 0.0
            if prominence < cfg.min_head_prominence:
                continue

            first_gap = [s for s in highs if left.index < s.index < head.index]
            second_gap = [s for s in highs if head.index < s.index < right.index]
            if not first_gap or not second_gap:
                continue
            neck_left = max(first_gap, key=lambda s: s.price)
            neck_right = max(second_gap, key=lambda s: s.price)

            window_lookback = max(0, left.index - cfg.prior_trend_sessions)
            if left.index - window_lookback < 10:
                continue
            prior_high = max(float(b.high) for b in inputs.bars[window_lookback : left.index + 1])
            decline = (prior_high - left.price) / prior_high if prior_high > 0 else 0.0
            if decline < cfg.min_prior_decline:
                continue
            # Peak-to-low says a 20% range fell 20%. Net change says it did not,
            # and a range with three lows in it is a range.
            trend = measure_prior_trend(
                inputs.bars, end_index=left.index, lookback=cfg.prior_trend_sessions
            )
            if trend is None or trend.gain_pct > -cfg.min_net_decline:
                continue

            key = (left.index, head.index, right.index)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Structure(
                    start_index=left.index,
                    end_index=inputs.last_index,
                    parts={
                        "left": left,
                        "head": head,
                        "right": right,
                        "neck_left": neck_left,
                        "neck_right": neck_right,
                        "structure_end": end,
                        "prominence": prominence,
                        "decline": decline,
                        "net_change": trend.gain_pct,
                    },
                    reason=f"head {prominence:.0%} below the shoulders",
                )
            )
        return out

    # -- neckline ------------------------------------------------------------

    def _neckline(self, structure: Structure) -> tuple[float, float]:
        """Slope per session and the level at index zero.

        Two points, so no regression: a least-squares fit through exactly two
        points reports the same line with a fabricated goodness-of-fit, and
        :func:`fit_line` deliberately returns r²=0 for that case. Computing the
        line directly makes it obvious that its support is two touches, which is
        what ``touches`` on the boundary records.
        """
        left: Swing = structure.parts["neck_left"]
        right: Swing = structure.parts["neck_right"]
        span = right.index - left.index
        slope = (right.price - left.price) / span if span > 0 else 0.0
        return slope, left.price - slope * left.index

    def _neckline_at(self, structure: Structure, index: int) -> float:
        slope, intercept = self._neckline(structure)
        return slope * index + intercept

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        left: Swing = structure.parts["left"]
        head: Swing = structure.parts["head"]
        right: Swing = structure.parts["right"]
        neck_left: Swing = structure.parts["neck_left"]
        end = int(structure.parts["structure_end"])
        prominence = float(structure.parts["prominence"])
        decline = float(structure.parts["decline"])
        components: list[ComponentScore] = []

        # -- head prominence (required)
        components.append(
            ComponentScore(
                name="head_prominence",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    prominence,
                    ideal_low=0.07,
                    ideal_high=0.30,
                    tolerance_low=cfg.min_head_prominence,
                    tolerance_high=0.55,
                    floor=20.0,
                ),
                weight=weights["head_prominence"],
                measurements={
                    "prominence": prominence,
                    "head": head.price,
                    "left_shoulder": left.price,
                    "right_shoulder": right.price,
                },
                evidence=(
                    Evidence(
                        f"the head sits {prominence:.0%} below the shallower shoulder",
                        measured=prominence,
                    ),
                ),
                contradicting=(
                    (
                        Evidence(
                            f"the head is {prominence:.0%} below the shoulders; that is a "
                            "range with a dip in it rather than a head",
                            measured=prominence,
                        ),
                    )
                    if prominence < 0.07
                    else ()
                ),
            )
        )

        # -- shoulder symmetry (required), measured below the neckline
        left_depth = self._depth_below_neckline(structure, left)
        right_depth = self._depth_below_neckline(structure, right)
        deeper = max(left_depth, right_depth)
        asymmetry = abs(left_depth - right_depth) / deeper if deeper > 0 else 1.0
        components.append(
            ComponentScore(
                name="shoulder_symmetry",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(asymmetry, full_at=0.08, zero_at=cfg.max_shoulder_asymmetry),
                weight=weights["shoulder_symmetry"],
                measurements={
                    "asymmetry": asymmetry,
                    "left_depth": left_depth,
                    "right_depth": right_depth,
                },
                contradicting=(
                    (
                        Evidence(
                            f"the shoulders differ in depth by {asymmetry:.0%}; real "
                            "structures are asymmetric but this one is lopsided",
                            measured=asymmetry,
                        ),
                    )
                    if asymmetry > cfg.max_shoulder_asymmetry
                    else ()
                ),
            )
        )

        # -- neckline quality (required)
        slope, _ = self._neckline(structure)
        reference = max(neck_left.price, 1e-9)
        slope_pct = slope / reference
        # A gently rising neckline is constructive: each rally reached further
        # than the last. A falling one says the opposite, and is scored down
        # rather than excluded -- the structure is still there.
        conformity = self._neckline_conformity(inputs, structure)
        levelness = band_score(
            slope_pct,
            ideal_low=0.0,
            ideal_high=0.004,
            tolerance_low=-0.004,
            tolerance_high=0.010,
            floor=15.0,
        )
        components.append(
            ComponentScore(
                name="neckline_quality",
                requirement=ComponentRequirement.REQUIRED,
                score=round(0.6 * levelness + 0.4 * conformity, 6),
                weight=weights["neckline_quality"],
                measurements={
                    "slope_pct_per_session": slope_pct,
                    "conformity": conformity / 100.0,
                    "neckline_now": self._neckline_at(structure, end),
                    "touches": 2.0,
                },
                contradicting=(
                    (
                        Evidence(
                            f"the neckline falls {abs(slope_pct):.2%} a session: each rally "
                            "is reaching less far than the last",
                            measured=slope_pct,
                        ),
                    )
                    if slope_pct < -0.001
                    else ()
                ),
            )
        )

        # -- prior decline (required): the thing being reversed
        components.append(
            ComponentScore(
                name="prior_decline",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    decline,
                    ideal_low=0.15,
                    ideal_high=0.40,
                    tolerance_low=cfg.min_prior_decline,
                    tolerance_high=0.70,
                    floor=20.0,
                ),
                weight=weights["prior_decline"],
                measurements={
                    "decline_pct": decline,
                    "net_change_pct": float(structure.parts["net_change"]),
                },
                evidence=(
                    Evidence(
                        f"a {decline:.0%} decline preceded the left shoulder",
                        EvidenceKind.CONTEXTUAL,
                        measured=decline,
                    ),
                ),
            )
        )

        # -- timing symmetry
        first_half = max(1, head.index - left.index)
        second_half = max(1, right.index - head.index)
        ratio = max(first_half, second_half) / min(first_half, second_half)
        components.append(
            ComponentScore(
                name="timing_symmetry",
                score=decay_score(ratio, full_at=1.0, zero_at=cfg.max_timing_asymmetry),
                weight=weights["timing_symmetry"],
                measurements={
                    "ratio": ratio,
                    "left_half_sessions": float(first_half),
                    "right_half_sessions": float(second_half),
                },
            )
        )

        # -- volume: heaviest into the head, lightest on the right shoulder
        head_volume = measure_volume(
            inputs.bars, start_index=max(0, head.index - 3), end_index=head.index + 3
        )
        right_volume = measure_volume(
            inputs.bars, start_index=max(0, right.index - 3), end_index=min(end, right.index + 3)
        )
        if head_volume is None or right_volume is None or head_volume.mean_volume <= 0:
            components.append(
                unavailable(
                    "volume_profile",
                    weights["volume_profile"],
                    "not enough sessions around the head or right shoulder",
                )
            )
        else:
            ratio_volume = right_volume.mean_volume / head_volume.mean_volume
            components.append(
                ComponentScore(
                    name="volume_profile",
                    score=decay_score(ratio_volume, full_at=0.7, zero_at=1.6),
                    weight=weights["volume_profile"],
                    measurements={"right_over_head": ratio_volume},
                    evidence=(
                        (
                            Evidence(
                                f"the right shoulder formed on {1 - ratio_volume:.0%} less "
                                "volume than the head: less supply at a higher price",
                                EvidenceKind.STRUCTURAL,
                                measured=ratio_volume,
                            ),
                        )
                        if ratio_volume < 0.9
                        else ()
                    ),
                )
            )

        return components

    def _depth_below_neckline(self, structure: Structure, swing: Swing) -> float:
        line = self._neckline_at(structure, swing.index)
        if line <= 0:
            return 0.0
        return max(0.0, (line - swing.price) / line)

    def _neckline_conformity(self, inputs: DetectionInputs, structure: Structure) -> float:
        """How well the rallies inside the structure reach the line.

        Two points define the line, so the line itself is not evidence. What is
        evidence is whether the *other* confirmed highs in the structure sit
        near it rather than scattered, and that is what this measures.
        """
        left: Swing = structure.parts["left"]
        right: Swing = structure.parts["right"]
        interior = [s for s in inputs.swing_highs if left.index <= s.index <= right.index]
        if len(interior) <= 2:
            # Nothing beyond the two defining points. Reported as a middling
            # number rather than as a perfect fit, which two points never are.
            return 50.0
        errors = []
        for swing in interior:
            line = self._neckline_at(structure, swing.index)
            if line > 0:
                errors.append(abs(swing.price - line) / line)
        if not errors:
            return 50.0
        mean_error = sum(errors) / len(errors)
        return decay_score(mean_error, full_at=0.01, zero_at=0.12)

    # -- geometry ------------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        left: Swing = structure.parts["left"]
        head: Swing = structure.parts["head"]
        right: Swing = structure.parts["right"]
        neck_left: Swing = structure.parts["neck_left"]
        neck_right: Swing = structure.parts["neck_right"]
        last = inputs.last_index
        slope, _ = self._neckline(structure)

        return PatternGeometry(
            start_date=left.session_date,
            end_date=inputs.bars[last].session_date,
            segments={
                "left_shoulder": (left.session_date, neck_left.session_date),
                "head": (neck_left.session_date, neck_right.session_date),
                "right_shoulder": (neck_right.session_date, right.session_date),
            },
            #: Anchored on the last session with the line *projected* to it, so
            #: the level a state machine compares against is the neckline as it
            #: stands today rather than where it stood when it was drawn.
            resistance=Boundary(
                kind="resistance",
                method="neckline",
                level=self._neckline_at(structure, last),
                anchor_date=inputs.bars[last].session_date,
                slope_per_session=slope,
                touches=(
                    PricePoint(neck_left.session_date, neck_left.price),
                    PricePoint(neck_right.session_date, neck_right.price),
                ),
                start_date=neck_left.session_date,
                end_date=neck_right.session_date,
                confidence=45.0,
            ),
            support=Boundary(
                kind="support",
                method="single_extreme",
                level=head.price,
                anchor_date=head.session_date,
                confidence=60.0,
            ),
            key_points={
                "left_shoulder": PricePoint(left.session_date, left.price),
                "head": PricePoint(head.session_date, head.price),
                "right_shoulder": PricePoint(right.session_date, right.price),
                "neckline_left": PricePoint(neck_left.session_date, neck_left.price),
                "neckline_right": PricePoint(neck_right.session_date, neck_right.price),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the head the staged-exhaustion claim has failed."""
        head: Swing = structure.parts["head"]
        return float(head.price * 0.99)

    def notes(self, structure: Structure) -> tuple[str, ...]:
        slope, _ = self._neckline(structure)
        direction = "rising" if slope > 0 else "falling" if slope < 0 else "flat"
        return (
            f"{direction} neckline drawn through two confirmed highs; the line is where the "
            "structure would resolve, not a statement that it will",
        )


__all__ = ["InverseHeadShouldersDetector"]
