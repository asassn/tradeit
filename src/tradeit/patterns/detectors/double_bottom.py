"""Double Bottom.

**The first reversal family, and the difference matters.** Every detector before
this one measures a *pause inside an advance*: the flag, the base, the triangle
and the pennant all require a prior uptrend and all fail without one. A double
bottom requires the opposite context. It is an attempt to end a decline, and
without the decline there is nothing to reverse — two lows at a similar level
inside a quiet sideways market are a range, not a bottom.

That single inversion is why this cannot be a wrapper around any earlier
detector. `prior_decline` is not the flag's `prior_trend` component with the
sign flipped: the flag asks *how strong was the advance we are pausing inside*,
and this asks *how much damage is there to repair*, which is measured from a
peak rather than across a lookback, and which scores badly for both too little
(nothing to reverse) and too much (a collapse, where a first bounce is rarely
the end).

**Structural definition.**

1. **Prior decline.** A fall from a prior high into the first low.
2. **First low.** A confirmed swing low.
3. **Intervening rally.** A confirmed swing high between the lows. This is the
   neckline, and it is the level the pattern must eventually clear. Too shallow
   and the "two lows" are one low with noise in it.
4. **Second low**, at a similar level. Slightly *below* the first is
   constructive: it takes out the obvious stops before the reversal, which is
   why `undercut_behaviour` scores a small undercut above an exact match rather
   than penalising it.
5. **Separation.** Two lows three sessions apart are one event.

**What this detector does not claim.** That the reversal will happen. The
neckline is where the structure would be resolved; whether crossing it means
anything is Phase 5's question. A double bottom whose second low is still
forming is FORMING, and the state machine is the only thing that changes as
price approaches the neckline.

**Discovery is structural.** For each confirmed swing low that could be the
second low, the first low is the *earliest* confirmed swing low at a comparable
level within the separation window, and the intervening high is the highest
confirmed high between them. No window is searched and scored.
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
from tradeit.patterns.config import DoubleBottomConfig
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
from tradeit.patterns.swings import Swing


class DoubleBottomDetector(BaseDetector):
    """Finds double bottoms: two lows at a level, separated by a rally."""

    name = "double_bottom"
    pattern_type = PatternType.DOUBLE_BOTTOM

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=("low_symmetry", "intervening_rally", "prior_decline"),
        optional_components=(
            "timing",
            "undercut_behaviour",
            "volume_profile",
            "relative_strength",
        ),
        minimum_evidence_coverage=60.0,
    )

    @property
    def config(self) -> DoubleBottomConfig:
        return self.engine_config.double_bottom

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.min_separation_sessions
            + self.config.prior_trend_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """Second low, first low at the same level, highest high between.

        The first low is the **earliest** confirmed low at a comparable level
        rather than the nearest, for the reason ADR-0014 records: taking the
        later of two equal lows truncates the structure, and taking whichever
        pairing scored best is selection by score.
        """
        cfg = self.config
        end = inputs.structure_end
        lows = [s for s in inputs.swing_lows if s.index <= end]
        highs = [s for s in inputs.swing_highs if s.index <= end]
        if len(lows) < 2:
            return []

        out: list[Structure] = []
        seen: set[tuple[int, int]] = set()
        for second in sorted(lows, key=lambda s: s.index, reverse=True):
            earliest = second.index - cfg.max_separation_sessions
            latest = second.index - cfg.min_separation_sessions
            peers = [
                s
                for s in lows
                if earliest <= s.index <= latest
                and abs(s.price - second.price) / max(s.price, second.price)
                <= cfg.max_low_divergence
            ]
            if not peers:
                continue
            first = peers[0]

            # The level has to have *held*. A confirmed low between the two that
            # sits materially below both means it did not, and the structure is
            # something else -- most obviously an inverse head and shoulders,
            # whose two shoulders sit at a common level with the head beneath
            # them. Without this check the shoulders read as a textbook double
            # bottom and the reading scores higher than a real one, because
            # nothing else about the pairing looks wrong.
            floor = min(first.price, second.price) * (1.0 - cfg.max_undercut)
            if any(s.price < floor for s in lows if first.index < s.index < second.index):
                continue

            between = [s for s in highs if first.index < s.index < second.index]
            if not between:
                continue
            middle = max(between, key=lambda s: s.price)

            rally = (middle.price - first.price) / first.price if first.price > 0 else 0.0
            if rally < cfg.min_intervening_rally:
                continue

            # The decline being reversed, measured two ways because either alone
            # is wrong. Peak-to-low says how much damage there is to repair; a
            # lookback mean would understate it for a series that fell hard and
            # then chopped, which is exactly this pattern's setup. But
            # peak-to-low alone reports a 20% decline for a range that oscillated
            # 20% and went nowhere, and a range with two lows at the same level
            # is a range. Net change is what separates them.
            window_start = max(0, first.index - cfg.prior_trend_sessions)
            if first.index - window_start < 10:
                continue
            prior_high = max(float(b.high) for b in inputs.bars[window_start : first.index + 1])
            decline = (prior_high - first.price) / prior_high if prior_high > 0 else 0.0
            if decline < cfg.min_prior_decline:
                continue
            trend = measure_prior_trend(
                inputs.bars, end_index=first.index, lookback=cfg.prior_trend_sessions
            )
            if trend is None or trend.gain_pct > -cfg.min_net_decline:
                continue

            undercut = (first.price - second.price) / first.price if first.price > 0 else 0.0
            if undercut > cfg.max_undercut:
                continue

            key = (first.index, second.index)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Structure(
                    start_index=first.index,
                    end_index=inputs.last_index,
                    parts={
                        "first": first,
                        "second": second,
                        "middle": middle,
                        "structure_end": end,
                        "rally": rally,
                        "decline": decline,
                        "undercut": undercut,
                        "prior_high": prior_high,
                        "prior_high_index": window_start,
                        "net_change": trend.gain_pct,
                    },
                    reason=(
                        f"lows {first.price:.2f}/{second.price:.2f} split by a {rally:.0%} rally"
                    ),
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        first: Swing = structure.parts["first"]
        second: Swing = structure.parts["second"]
        middle: Swing = structure.parts["middle"]
        end = int(structure.parts["structure_end"])
        rally = float(structure.parts["rally"])
        decline = float(structure.parts["decline"])
        undercut = float(structure.parts["undercut"])
        components: list[ComponentScore] = []

        # -- low symmetry (required)
        divergence = abs(second.price - first.price) / first.price if first.price > 0 else 1.0
        components.append(
            ComponentScore(
                name="low_symmetry",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(divergence, full_at=0.005, zero_at=cfg.max_low_divergence),
                weight=weights["low_symmetry"],
                measurements={
                    "divergence": divergence,
                    "first_low": first.price,
                    "second_low": second.price,
                },
                evidence=(
                    (
                        Evidence(
                            f"two lows {divergence:.1%} apart at "
                            f"{first.price:.2f} and {second.price:.2f}",
                            measured=divergence,
                        ),
                    )
                    if divergence <= cfg.max_low_divergence / 2
                    else ()
                ),
            )
        )

        # -- intervening rally (required): this is the neckline
        components.append(
            ComponentScore(
                name="intervening_rally",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    rally,
                    ideal_low=cfg.ideal_intervening_rally,
                    ideal_high=cfg.ideal_intervening_rally * 2.5,
                    tolerance_low=cfg.min_intervening_rally,
                    tolerance_high=cfg.ideal_intervening_rally * 5,
                    floor=20.0,
                ),
                weight=weights["intervening_rally"],
                measurements={"rally_pct": rally, "neckline": middle.price},
                contradicting=(
                    (
                        Evidence(
                            f"the rally between the lows was only {rally:.1%}; that is one "
                            "low with noise in it rather than two",
                            measured=rally,
                        ),
                    )
                    if rally < cfg.ideal_intervening_rally
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
                    ideal_low=0.18,
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
                        f"a {decline:.0%} decline preceded the first low",
                        EvidenceKind.CONTEXTUAL,
                        measured=decline,
                    ),
                ),
                contradicting=(
                    (
                        Evidence(
                            f"the prior decline was {decline:.0%}; a first bounce is rarely "
                            "the end of a fall that size",
                            EvidenceKind.CONTEXTUAL,
                            measured=decline,
                        ),
                    )
                    if decline > 0.55
                    else ()
                ),
            )
        )

        # -- timing
        separation = second.index - first.index
        components.append(
            ComponentScore(
                name="timing",
                score=band_score(
                    float(separation),
                    ideal_low=15.0,
                    ideal_high=55.0,
                    tolerance_low=float(cfg.min_separation_sessions),
                    tolerance_high=float(cfg.max_separation_sessions),
                    floor=25.0,
                ),
                weight=weights["timing"],
                measurements={"separation_sessions": float(separation)},
            )
        )

        # -- undercut behaviour: a small undercut is constructive
        components.append(
            ComponentScore(
                name="undercut_behaviour",
                score=band_score(
                    undercut,
                    ideal_low=0.002,
                    ideal_high=cfg.max_undercut * 0.6,
                    tolerance_low=-cfg.max_low_divergence,
                    tolerance_high=cfg.max_undercut,
                    floor=40.0,
                ),
                weight=weights["undercut_behaviour"],
                measurements={"undercut_pct": undercut},
                evidence=(
                    (
                        Evidence(
                            f"the second low undercut the first by {undercut:.1%}, taking "
                            "out the obvious stops before turning",
                            measured=undercut,
                        ),
                    )
                    if 0.0 < undercut <= cfg.max_undercut
                    else ()
                ),
            )
        )

        # -- volume: the second low should be quieter than the first
        first_volume = measure_volume(
            inputs.bars, start_index=max(0, first.index - 3), end_index=first.index + 3
        )
        second_volume = measure_volume(
            inputs.bars, start_index=max(0, second.index - 3), end_index=min(end, second.index + 3)
        )
        if first_volume is None or second_volume is None or first_volume.mean_volume <= 0:
            components.append(
                unavailable(
                    "volume_profile",
                    weights["volume_profile"],
                    "not enough sessions around one of the lows to compare volume",
                )
            )
        else:
            ratio = second_volume.mean_volume / first_volume.mean_volume
            components.append(
                ComponentScore(
                    name="volume_profile",
                    score=decay_score(ratio, full_at=0.75, zero_at=1.6),
                    weight=weights["volume_profile"],
                    measurements={
                        "second_over_first": ratio,
                        "first_low_volume": first_volume.mean_volume,
                        "second_low_volume": second_volume.mean_volume,
                    },
                    evidence=(
                        (
                            Evidence(
                                f"the second low came on {1 - ratio:.0%} less volume than the "
                                "first: less supply at the same price",
                                EvidenceKind.STRUCTURAL,
                                measured=ratio,
                            ),
                        )
                        if ratio < 0.9
                        else ()
                    ),
                    contradicting=(
                        (
                            Evidence(
                                f"the second low came on {ratio - 1:.0%} more volume than the "
                                "first: supply is still arriving at this level",
                                EvidenceKind.STRUCTURAL,
                                measured=ratio,
                            ),
                        )
                        if ratio > 1.2
                        else ()
                    ),
                )
            )

        components.append(self._score_rs(inputs, first.index, inputs.last_index))
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
        # A bottom that fell less than the market while both declined is the
        # constructive case, so the zero point sits below flat.
        return ComponentScore(
            name="relative_strength",
            score=ramp_score(profile.excess_return, zero_at=-0.20, full_at=0.05),
            weight=weight,
            measurements={"excess_return": profile.excess_return},
        )

    # -- geometry ------------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        first: Swing = structure.parts["first"]
        second: Swing = structure.parts["second"]
        middle: Swing = structure.parts["middle"]

        return PatternGeometry(
            start_date=first.session_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "first_low": (first.session_date, first.session_date),
                "rally": (first.session_date, middle.session_date),
                "second_low": (middle.session_date, second.session_date),
            },
            #: The neckline. Named `resistance` because that is the role it
            #: plays in the state machine, not because the detector has an
            #: opinion about crossing it.
            resistance=Boundary(
                kind="resistance",
                method="neckline",
                level=middle.price,
                anchor_date=middle.session_date,
                confidence=70.0,
                touches=(PricePoint(middle.session_date, middle.price),),
            ),
            support=Boundary(
                kind="support",
                method="paired_lows",
                level=min(first.price, second.price),
                anchor_date=second.session_date,
                confidence=70.0,
                touches=(
                    PricePoint(first.session_date, first.price),
                    PricePoint(second.session_date, second.price),
                ),
            ),
            key_points={
                "first_low": PricePoint(first.session_date, first.price),
                "neckline": PricePoint(middle.session_date, middle.price),
                "second_low": PricePoint(second.session_date, second.price),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the lower of the two lows, by the tolerated undercut.

        Not a stop-loss. The claim "price stopped falling here" is what fails.
        """
        first: Swing = structure.parts["first"]
        second: Swing = structure.parts["second"]
        return float(min(first.price, second.price) * (1.0 - self.config.max_undercut))

    def notes(self, structure: Structure) -> tuple[str, ...]:
        decline = float(structure.parts["decline"])
        return (
            f"reversal structure after a {decline:.0%} decline; the neckline is where it "
            "would be resolved, and whether that resolution counts is a later question",
        )
