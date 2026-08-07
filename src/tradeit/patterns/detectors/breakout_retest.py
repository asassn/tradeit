"""Breakout Retest Structure.

**The Phase 4 boundary lives here.** This family is defined by a breakout having
already happened, which makes it the one detector where the temptation to say
something about breakout *validity* is strongest and the one where saying it
would be most wrong. It does not. Every measurement is geometric:

* A level existed, supported by repeated confirmed touches.
* Price closed above it.
* Price came back to within a tolerance of it.
* Price is currently above or below it.

Whether that sequence constitutes a *confirmed* breakout — whether the volume
was sufficient, whether the follow-through was real, whether it is tradeable —
is Phase 5's question. This module has no vocabulary for answering it, and the
component named `hold_behaviour` is deliberately named for what it measures
(price is above the line) rather than for what a reader might want it to mean
(the breakout worked).

**Lifecycle.** A retest structure begins life already resolved, so it never
occupies NEAR_BREAKOUT; :mod:`tradeit.patterns.lifecycle` enforces that as a
family constraint and :meth:`classify_state` here honours it. The states it can
occupy are FORMING (the retest is still in progress), MATURE (a retest occurred
and price is holding above the level), BROKEN_OUT_UNCONFIRMED (price has moved
back up and away from the level), INVALIDATED (price closed back below it), and
EXPIRED.

**Not a flag and not a base.** A flag is a pause *before* a level is crossed; a
base is a level being built. This is a level being revisited *after* it was
crossed, and the distinguishing measurement -- `retest_proximity`, how close the
pullback came to the line -- has no counterpart in either.

**Discovery is structural.** Each candidate level comes from a horizontal
cluster of confirmed swing highs at or before an anchoring pivot; the break is
the first close above it after the cluster's last touch; the retest is the first
confirmed swing low after the excursion peak that reaches the line. Every index
is determined by the data.

**A measured limitation, reported rather than tuned away.** Across the full
validation corpus (n=1000 per cohort) this family produces a candidate on 30.7%
of noise series and rates 19.8% of them at 70 or above. That is a property of
the pattern, not a defect in the code: every ingredient — a level, a close above
it, a return to it — is a single event that noise supplies readily, where a cup
or a high tight flag requires a sustained shape that noise does not.

Two details of the shape are worth knowing. It fires **most on quiet noise**
(73% candidate on low-volatility walks, 27.8% on high-volatility ones), because
a level needs price to sit below it for 85% of its span and a violent series
does not oblige. And it produces **nothing at all** on choppy or broad volatile
ranges, where no level survives the below-fraction test.

Several structural requirements are in place because they are definitionally
right — three touches spanning at least twenty sessions, price below the level
for 85% of them, a break of at least one ATR sustained over three closes — and
each cut the rate. None was chosen to hit a number, and no threshold has moved
since the rate was measured.

**It is not the noisiest family.** An earlier estimate, taken from a narrow probe
before the full corpus existed, said it was. At n=1000 the flat base is
materially noisier (46.6% of noise series at 70 or above, against 19.8% here).
The consequence for later phases stands regardless: a retest reading is weak
evidence on its own and real labelled data is what will say by how much.
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
    PatternState,
    PricePoint,
)
from tradeit.patterns.config import BreakoutRetestConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import horizontal_resistance


class BreakoutRetestDetector(BaseDetector):
    """Finds levels that were crossed and then revisited."""

    name = "breakout_retest"
    pattern_type = PatternType.BREAKOUT_RETEST

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=("level_quality", "break_observation", "retest_proximity"),
        optional_components=("hold_behaviour", "timing", "volume_character"),
        minimum_evidence_coverage=60.0,
    )

    @property
    def config(self) -> BreakoutRetestConfig:
        return self.engine_config.breakout_retest

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        # The retest window sits *inside* the level lookback rather than after
        # it, so adding the two would demand history the pattern does not need.
        return (
            self.config.level_lookback
            + self.config.min_hold_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """The level is built from data that precedes the break.

        **The subtle failure this avoids.** Clustering every confirmed high in
        the lookback -- including the ones the breakout and the pullback
        themselves produced -- lets the retest help define the level it is
        supposedly retesting. On a textbook series that pushed the level's last
        touch past the retest and left no sequence at all; on a messier one it
        would flatter the proximity measurement, because a line fitted through
        the pullback necessarily passes near it. So each candidate level is
        anchored on a confirmed swing high and built only from highs at or
        before that anchor, and the break must occur after it.

        Anchors are walked from the most recent backwards and every valid
        sequence is emitted. Selection among them is by recency, which the
        market decides, rather than by score, which ADR-0014 prohibits.
        """
        cfg = self.config
        end = inputs.structure_end
        swing_cfg = self.engine_config.swings
        highs = [s for s in inputs.swing_highs if s.index <= end]
        if len(highs) < cfg.min_level_touches:
            return []

        out: list[Structure] = []
        seen: set[tuple[int, int]] = set()
        for anchor in sorted(highs, key=lambda s: s.index, reverse=True):
            if anchor.index >= end - cfg.min_sessions_to_retest:
                continue
            lookback_start = max(0, anchor.index - cfg.level_lookback)
            level = horizontal_resistance(
                inputs.bars,
                [s for s in highs if s.index <= anchor.index],
                start_index=lookback_start,
                end_index=anchor.index,
                tolerance_pct=swing_cfg.touch_tolerance_pct,
                min_touches=cfg.min_level_touches,
            )
            if level is None or level.touch_count < cfg.min_level_touches:
                continue

            # The last session that *defined* the level. Everything after it is
            # the breakout attempt. Taken from the touch list rather than from
            # ``end_date``, which describes the cluster's anchoring span and can
            # precede its final touch.
            touch_dates = {point.session_date for point in level.touches}
            touch_indices = [i for i, b in enumerate(inputs.bars) if b.session_date in touch_dates]
            if not touch_indices:
                continue
            first_touch, last_touch = touch_indices[0], touch_indices[-1]
            if last_touch >= end:
                continue

            # What makes a level *resistance* rather than two highs that landed
            # together. Both gates exist because without them this detector
            # found textbook retests in a random walk -- noise supplies clustered
            # highs, a close above one of them, and a dip back, and every
            # measurement downstream then looks excellent.
            if last_touch - first_touch < cfg.min_level_span:
                continue
            before = inputs.bars[first_touch : last_touch + 1]
            below = sum(1 for b in before if float(b.close) < level.level) / len(before)
            if below < cfg.min_below_fraction:
                continue

            threshold = level.level * (1.0 + cfg.break_buffer)
            break_index = next(
                (
                    i
                    for i in range(last_touch + 1, end + 1)
                    if float(inputs.bars[i].close) > threshold
                ),
                None,
            )
            if break_index is None:
                continue

            # The excursion has to happen before the return, so the retest is
            # measured from the high of the move rather than from the break
            # itself. Without this the session two bars after the break -- still
            # inside the thrust and still near the line -- reads as a retest,
            # and a breakout that ran away scores as a textbook one.
            excursion_end = min(end, break_index + cfg.max_sessions_to_retest)
            peak_index = max(
                range(break_index, excursion_end + 1), key=lambda i: float(inputs.bars[i].high)
            )

            # The move above the line has to be worth calling a break. Measured
            # in ATRs so a quiet instrument and a volatile one face the same
            # scale-free standard, rather than a percentage that a volatile
            # stock clears every other session.
            atr = float(inputs.atr[break_index])
            excursion = float(inputs.bars[peak_index].high) - level.level
            if not np.isfinite(atr) or atr <= 0 or excursion < cfg.min_break_atr * atr:
                continue

            # You retest a breakout, and a single close above a line is not one.
            above = sum(
                1 for b in inputs.bars[break_index : peak_index + 1] if float(b.close) > level.level
            )
            if above < cfg.min_sessions_above:
                continue

            # The retest is the **first confirmed swing low** after that peak
            # that reaches the line, and it is a swing low for a reason. Taking
            # the first *bar* within tolerance measures the pullback at the
            # moment it entered the band rather than at its extreme, which
            # reported a textbook retest as a four-percent near-miss. Taking the
            # lowest bar would let a later collapse redefine where the retest
            # happened, and taking the closest would pick the approach that
            # flatters the score -- the bias ADR-0014 exists to prevent. A swing
            # low is the pullback's own turning point: the market decides where
            # it is, and it is already causally confirmed.
            threshold_low = level.level * (1.0 + cfg.retest_tolerance)
            floor_price = level.level * (1.0 - cfg.fail_tolerance - cfg.retest_tolerance)
            retest = next(
                (
                    s
                    for s in inputs.swing_lows
                    if peak_index + cfg.min_sessions_to_retest <= s.index <= end
                    and s.index <= peak_index + cfg.max_sessions_to_retest
                    and floor_price <= s.price <= threshold_low
                ),
                None,
            )
            if retest is None:
                # Either price never came back to the line, or it came back and
                # kept going. Both are real things and neither is this pattern.
                continue

            retest_index = retest.index
            key = (break_index, retest_index)
            if key in seen:
                continue
            seen.add(key)

            retest_low = retest.price
            proximity = (retest_low - level.level) / level.level if level.level > 0 else 1.0
            out.append(
                Structure(
                    # The level's first touch, not the lookback window's start.
                    # The window start is an artefact of how far back the search
                    # reached; identity keys on the structural start, and a start
                    # that moves with the search depth is not a structural fact.
                    start_index=first_touch,
                    end_index=inputs.last_index,
                    parts={
                        "level": level,
                        "last_touch": last_touch,
                        "break_index": break_index,
                        "retest_index": retest_index,
                        "retest_low": retest_low,
                        "proximity": proximity,
                        "structure_end": end,
                    },
                    reason=(
                        f"level {level.level:.2f} crossed, revisited to within {abs(proximity):.1%}"
                    ),
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        level: Boundary = structure.parts["level"]
        break_index = int(structure.parts["break_index"])
        retest_index = int(structure.parts["retest_index"])
        proximity = float(structure.parts["proximity"])
        end = int(structure.parts["structure_end"])
        components: list[ComponentScore] = []

        components.append(
            ComponentScore(
                name="level_quality",
                requirement=ComponentRequirement.REQUIRED,
                score=round(
                    0.6 * ramp_score(float(level.touch_count), zero_at=1.0, full_at=5.0)
                    + 0.4 * level.confidence,
                    6,
                ),
                weight=weights["level_quality"],
                measurements={
                    "touches": float(level.touch_count),
                    "level": level.level,
                    "boundary_confidence": level.confidence,
                },
                evidence=(
                    Evidence(
                        f"level at {level.level:.2f} defined by {level.touch_count} confirmed "
                        "touches",
                        measured=float(level.touch_count),
                    ),
                ),
            )
        )

        # Purely geometric, and named so. "A close occurred above the line" is
        # the whole claim; the size of the excursion is reported because Phase 5
        # will want it, not because this detector is judging it.
        excursion_high = max(float(b.high) for b in inputs.bars[break_index : retest_index + 1])
        excursion = (excursion_high - level.level) / level.level if level.level > 0 else 0.0
        atr = float(inputs.atr[break_index]) if np.isfinite(inputs.atr[break_index]) else 0.0
        components.append(
            ComponentScore(
                name="break_observation",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    excursion,
                    ideal_low=0.02,
                    ideal_high=0.15,
                    tolerance_low=cfg.break_buffer,
                    tolerance_high=0.45,
                    floor=25.0,
                ),
                weight=weights["break_observation"],
                measurements={
                    "excursion_pct": excursion,
                    "excursion_atr": (excursion_high - level.level) / atr if atr > 0 else 0.0,
                    "sessions_above": float(retest_index - break_index),
                },
                evidence=(
                    Evidence(
                        f"price closed above the level and reached {excursion:.1%} beyond it "
                        "before returning; whether that constitutes a confirmed breakout is "
                        "not a question this phase answers",
                        measured=excursion,
                    ),
                ),
            )
        )

        # The distinguishing measurement. Zero is a perfect retest; positive
        # means it stopped short, negative means it dipped through.
        components.append(
            ComponentScore(
                name="retest_proximity",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(abs(proximity), full_at=0.002, zero_at=cfg.retest_tolerance),
                weight=weights["retest_proximity"],
                measurements={
                    "proximity": proximity,
                    "retest_low": float(structure.parts["retest_low"]),
                },
                evidence=(
                    (
                        Evidence(
                            f"the pullback came within {abs(proximity):.1%} of the level",
                            measured=proximity,
                        ),
                    )
                    if abs(proximity) <= cfg.ideal_retest_tolerance
                    else ()
                ),
            )
        )

        # Named for what it measures. "Price is above the line" -- not "the
        # breakout worked", which is a different sentence in a different phase.
        held = end - retest_index
        if held < cfg.min_hold_sessions:
            components.append(
                unavailable(
                    "hold_behaviour",
                    weights["hold_behaviour"],
                    f"{held} sessions since the retest low; nothing is yet observable about "
                    "whether the level is holding",
                )
            )
        else:
            after = inputs.bars[retest_index + 1 : end + 1]
            floor_level = level.level * (1.0 - cfg.fail_tolerance)
            above = sum(1 for b in after if float(b.close) >= floor_level) / max(1, len(after))
            last_close = float(inputs.bars[end].close)
            components.append(
                ComponentScore(
                    name="hold_behaviour",
                    score=ramp_score(above, zero_at=0.3, full_at=1.0),
                    weight=weights["hold_behaviour"],
                    measurements={
                        "sessions_above_fraction": above,
                        "sessions_since_retest": float(held),
                        "close_over_level": last_close / level.level if level.level > 0 else 0.0,
                    },
                    contradicting=(
                        (
                            Evidence(
                                f"price has closed back below the level in {1 - above:.0%} of "
                                "the sessions since the retest",
                                measured=above,
                            ),
                        )
                        if above < 0.7
                        else ()
                    ),
                )
            )

        components.append(
            ComponentScore(
                name="timing",
                score=band_score(
                    float(retest_index - break_index),
                    ideal_low=3.0,
                    ideal_high=12.0,
                    tolerance_low=float(cfg.min_sessions_to_retest),
                    tolerance_high=float(cfg.max_sessions_to_retest),
                    floor=30.0,
                ),
                weight=weights["timing"],
                measurements={"sessions_to_retest": float(retest_index - break_index)},
            )
        )

        break_volume = measure_volume(
            inputs.bars, start_index=break_index, end_index=min(end, break_index + 2)
        )
        retest_volume = measure_volume(
            inputs.bars, start_index=max(0, retest_index - 2), end_index=min(end, retest_index + 1)
        )
        if break_volume is None or retest_volume is None or break_volume.mean_volume <= 0:
            components.append(
                unavailable(
                    "volume_character",
                    weights["volume_character"],
                    "too few sessions around the break or the retest to compare volume",
                )
            )
        else:
            ratio = retest_volume.mean_volume / break_volume.mean_volume
            components.append(
                ComponentScore(
                    name="volume_character",
                    score=decay_score(ratio, full_at=0.6, zero_at=1.5),
                    weight=weights["volume_character"],
                    measurements={"retest_over_break": ratio},
                    evidence=(
                        (
                            Evidence(
                                f"the retest came on {1 - ratio:.0%} less volume than the "
                                "break itself",
                                EvidenceKind.STRUCTURAL,
                                measured=ratio,
                            ),
                        )
                        if ratio < 0.85
                        else ()
                    ),
                )
            )

        return components

    # -- lifecycle -----------------------------------------------------------

    def classify_state(
        self,
        inputs: DetectionInputs,
        structure: Structure,
        geometry: PatternGeometry,
        invalidation: float,
        quality: float,
    ) -> PatternState:
        """No NEAR_BREAKOUT: the level has already been crossed.

        The default flow would put a retest structure sitting just under its own
        level into NEAR_BREAKOUT, which describes a pattern approaching a
        breakout it has not had. This one has had it, and what is in question is
        whether the level is holding — a different sentence, and the reason
        :mod:`tradeit.patterns.lifecycle` carries a family constraint for it.
        """
        states = self.engine_config.states
        cfg = self.config
        level: Boundary = structure.parts["level"]
        retest_index = int(structure.parts["retest_index"])
        end = int(structure.parts["structure_end"])
        last_close = float(inputs.bars[-1].close)

        if last_close < invalidation:
            return PatternState.INVALIDATED
        if quality < states.maturity_quality_floor:
            return PatternState.FORMING
        if end - retest_index < cfg.min_hold_sessions:
            return PatternState.FORMING
        if last_close > level.level * (1.0 + states.breakout_buffer_pct * 4):
            # Moved back up and away. A geometric observation about distance,
            # not a verdict about the breakout.
            return PatternState.BROKEN_OUT_UNCONFIRMED
        return PatternState.MATURE

    # -- geometry ------------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        level: Boundary = structure.parts["level"]
        break_index = int(structure.parts["break_index"])
        retest_index = int(structure.parts["retest_index"])
        bars = inputs.bars

        return PatternGeometry(
            start_date=bars[structure.start_index].session_date,
            end_date=bars[inputs.last_index].session_date,
            segments={
                "level": (
                    level.start_date or bars[0].session_date,
                    level.end_date or level.anchor_date,
                ),
                "break": (bars[break_index].session_date, bars[retest_index].session_date),
                "retest": (bars[retest_index].session_date, bars[inputs.last_index].session_date),
            },
            resistance=level,
            support=Boundary(
                kind="support",
                method="retest_low",
                level=float(structure.parts["retest_low"]),
                anchor_date=bars[retest_index].session_date,
                confidence=50.0,
            ),
            key_points={
                "break": PricePoint(bars[break_index].session_date, float(bars[break_index].close)),
                "retest_low": PricePoint(
                    bars[retest_index].session_date, float(structure.parts["retest_low"])
                ),
                "level": PricePoint(level.anchor_date, level.level),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the level by the failure tolerance, the retest did not hold."""
        level: Boundary = structure.parts["level"]
        return float(level.level * (1.0 - self.config.fail_tolerance))

    def notes(self, structure: Structure) -> tuple[str, ...]:
        return (
            "geometric only: a level was crossed and revisited. Whether the breakout is "
            "confirmed, and whether any of this is actionable, are Phase 5 questions",
        )


__all__ = ["BreakoutRetestDetector"]
