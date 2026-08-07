"""High Tight Flag.

**Structural definition.**

A near-vertical advance followed by a shallow, brief pause. The classic
statement is a 90-100% gain in eight weeks with a correction of no more than
25%, and the defaults here sit close to that.

**Precision over recall, deliberately.** The brief is explicit that this
definition must not be relaxed to generate more examples, and the reason is
worth stating: a high tight flag detector tuned until it fires on ordinary bull
flags is not a rarer pattern, it is a duplicate of the bull flag detector under
a name that implies more than it delivers. Every threshold here is set where the
pattern's own literature sets it, and the correct behaviour on a 25% advance is
to find nothing.

The dimensions, and why each is separate:

* **Magnitude** — how far. The headline requirement.
* **Speed** — how fast. A 90% gain over a year is a trend, not a thrust.
* **Consistency** — whether the advance was distributed or delivered in one or
  two sessions. A gap-driven double is a repricing.
* **Tightness** — the consolidation. Beyond ~25% it is an ordinary flag.
* **Liquidity** — these structures appear disproportionately in thin securities
  where the advance is a quote artefact rather than accumulation. Scored, and
  low liquidity is contradicting evidence rather than a filter, because the
  detector reports structure and the screen decides tradability.
* **Extreme-move risk** — a scored acknowledgement that a security which has
  doubled in eight weeks can halve in two.

**Ordinary bull flags are the primary negative.** If this detector fires on
them, its definition has been diluted, and the test suite generates them
specifically to check.
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
from tradeit.patterns.config import HighTightFlagConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_impulse, measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import single_extreme_resistance


class HighTightFlagDetector(BaseDetector):
    """Finds high tight flags. Rare by design."""

    name = "high_tight_flag"
    pattern_type = PatternType.HIGH_TIGHT_FLAG

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        required_components=(
            "advance_magnitude",
            "advance_speed",
            "consolidation_tightness",
        ),
        optional_components=(
            "advance_consistency",
            "consolidation_duration",
            "liquidity",
            "extreme_move_risk",
        ),
        minimum_evidence_coverage=65.0,
    )

    @property
    def config(self) -> HighTightFlagConfig:
        return self.engine_config.high_tight_flag

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
            + self.config.min_advance_sessions
            + self.config.min_consolidation_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """Advance termination determines the split, as in the flag and pennant.

        The advance runs from a confirmed swing low to its highest confirmed
        high; the consolidation is everything after. The magnitude filter is
        applied here rather than in scoring, because a 30% advance is not a weak
        high tight flag -- it is not one.
        """
        cfg = self.config
        end = inputs.structure_end
        highs = [float(b.high) for b in inputs.bars]
        out: list[Structure] = []
        seen: set[int] = set()

        for origin in sorted((s.index for s in inputs.swing_lows if s.index >= 20), reverse=True):
            span = highs[origin : end + 1]
            if not span:
                continue
            peak = origin + int(np.argmax(span))
            advance_sessions = peak - origin
            pause_sessions = end - peak

            if not cfg.min_advance_sessions <= advance_sessions <= cfg.max_advance_sessions:
                continue
            if not (
                cfg.min_consolidation_sessions <= pause_sessions <= cfg.max_consolidation_sessions
            ):
                continue
            if peak in seen:
                continue

            impulse = measure_impulse(
                inputs.bars, start_index=origin, end_index=peak, atr=inputs.atr
            )
            # The magnitude gate. Deliberately structural rather than scored.
            if impulse is None or impulse.gain_pct < cfg.min_advance:
                continue

            window = inputs.bars[peak + 1 : end + 1]
            if not window:
                continue
            low = min(float(b.low) for b in window)
            depth = (impulse.high - low) / impulse.high if impulse.high > 0 else 1.0
            if depth > cfg.max_consolidation_depth:
                continue

            seen.add(peak)
            out.append(
                Structure(
                    start_index=origin,
                    end_index=inputs.last_index,
                    parts={
                        "impulse": impulse,
                        "peak": peak,
                        "pause_start": peak + 1,
                        "structure_end": end,
                        "depth": depth,
                    },
                    reason=f"advance {impulse.gain_pct:.0%}, pause {depth:.0%}",
                )
            )
        return out

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        impulse = structure.parts["impulse"]
        start = int(structure.parts["pause_start"])
        end = int(structure.parts["structure_end"])
        depth = float(structure.parts["depth"])
        components: list[ComponentScore] = []

        components.append(
            ComponentScore(
                name="advance_magnitude",
                requirement=ComponentRequirement.REQUIRED,
                score=ramp_score(
                    impulse.gain_pct, zero_at=cfg.min_advance, full_at=cfg.ideal_advance
                ),
                weight=weights["advance_magnitude"],
                measurements={"gain_pct": impulse.gain_pct, "gain_atr": impulse.gain_atr},
                evidence=(
                    Evidence(
                        f"advance of {impulse.gain_pct:+.0%} over {impulse.sessions} sessions",
                        measured=impulse.gain_pct,
                    ),
                ),
            )
        )

        components.append(
            ComponentScore(
                name="advance_speed",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    float(impulse.sessions),
                    ideal_low=float(cfg.min_advance_sessions),
                    ideal_high=30.0,
                    tolerance_low=float(cfg.min_advance_sessions) - 2,
                    tolerance_high=float(cfg.max_advance_sessions),
                    floor=15.0,
                ),
                weight=weights["advance_speed"],
                measurements={
                    "sessions": float(impulse.sessions),
                    "rate_per_session": impulse.rate_per_session,
                },
            )
        )

        components.append(
            ComponentScore(
                name="consolidation_tightness",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    depth,
                    ideal_low=0.03,
                    ideal_high=cfg.ideal_consolidation_depth,
                    tolerance_low=0.0,
                    tolerance_high=cfg.max_consolidation_depth,
                ),
                weight=weights["consolidation_tightness"],
                measurements={"depth_pct": depth},
                evidence=(
                    (
                        Evidence(
                            f"consolidation held to {depth:.0%} after a "
                            f"{impulse.gain_pct:.0%} advance",
                            measured=depth,
                        ),
                    )
                    if depth <= cfg.ideal_consolidation_depth
                    else ()
                ),
            )
        )

        # Distributed advance versus one enormous session.
        consistency = 1.0 - impulse.largest_session_share
        components.append(
            ComponentScore(
                name="advance_consistency",
                score=float(
                    np.clip(
                        0.6 * ramp_score(consistency, zero_at=0.2, full_at=0.8)
                        + 0.4 * ramp_score(impulse.persistence, zero_at=0.4, full_at=0.75),
                        0,
                        100,
                    )
                ),
                weight=weights["advance_consistency"],
                measurements={
                    "largest_session_share": impulse.largest_session_share,
                    "largest_gap_share": impulse.largest_gap_share,
                    "persistence": impulse.persistence,
                },
                contradicting=(
                    (
                        Evidence(
                            f"a single gap delivered {impulse.largest_gap_share:.0%} of the "
                            "advance: a repricing rather than sustained accumulation",
                            measured=impulse.largest_gap_share,
                        ),
                    )
                    if impulse.largest_gap_share > 0.4
                    else ()
                ),
            )
        )

        sessions = end - start + 1
        components.append(
            ComponentScore(
                name="consolidation_duration",
                score=band_score(
                    float(sessions),
                    ideal_low=float(cfg.min_consolidation_sessions),
                    ideal_high=15.0,
                    tolerance_low=float(cfg.min_consolidation_sessions) - 1,
                    tolerance_high=float(cfg.max_consolidation_sessions),
                    floor=25.0,
                ),
                weight=weights["consolidation_duration"],
                measurements={"sessions": float(sessions)},
            )
        )

        # Liquidity: scored, not filtered. The detector reports structure; the
        # screen decides tradability.
        volume = measure_volume(inputs.bars, start_index=start, end_index=end)
        if volume is None:
            components.append(
                unavailable("liquidity", weights["liquidity"], "pause window too short")
            )
        else:
            price = float(inputs.bars[end].close)
            dollar_volume = volume.mean_volume * price
            components.append(
                ComponentScore(
                    name="liquidity",
                    score=ramp_score(
                        dollar_volume,
                        zero_at=cfg.min_dollar_volume * 0.2,
                        full_at=cfg.min_dollar_volume * 4,
                    ),
                    weight=weights["liquidity"],
                    measurements={"dollar_volume": dollar_volume},
                    contradicting=(
                        (
                            Evidence(
                                f"average dollar volume {dollar_volume:,.0f} is thin; an "
                                "advance this size in a thin name is often a quote artefact "
                                "rather than accumulation",
                                EvidenceKind.CONTEXTUAL,
                                measured=dollar_volume,
                            ),
                        )
                        if dollar_volume < cfg.min_dollar_volume
                        else ()
                    ),
                )
            )

        # An acknowledgement rather than a judgement: what doubled in eight
        # weeks can halve in two, and the pattern's own magnitude is the risk.
        components.append(
            ComponentScore(
                name="extreme_move_risk",
                score=decay_score(impulse.gain_pct, full_at=cfg.min_advance, zero_at=3.0),
                weight=weights["extreme_move_risk"],
                measurements={"gain_pct": impulse.gain_pct},
                contradicting=(
                    (
                        Evidence(
                            f"a {impulse.gain_pct:.0%} advance carries extreme reversal risk; "
                            "position sizing is a later phase's problem and this is the "
                            "input it will need",
                            EvidenceKind.CONTEXTUAL,
                            measured=impulse.gain_pct,
                        ),
                    )
                    if impulse.gain_pct > 1.5
                    else ()
                ),
            )
        )
        return components

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        impulse = structure.parts["impulse"]
        start = int(structure.parts["pause_start"])
        end = int(structure.parts["structure_end"])
        window = inputs.bars[start : end + 1]
        low = min(window, key=lambda b: b.low) if window else inputs.bars[end]

        return PatternGeometry(
            start_date=impulse.start_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={
                "advance": (impulse.start_date, impulse.end_date),
                "consolidation": (
                    inputs.bars[start].session_date,
                    inputs.bars[inputs.last_index].session_date,
                ),
            },
            resistance=single_extreme_resistance(
                inputs.bars, start_index=impulse.end_index, end_index=end
            ),
            support=Boundary(
                kind="support",
                method="single_extreme",
                level=float(low.low),
                anchor_date=low.session_date,
                confidence=30.0,
            ),
            key_points={
                "advance_low": PricePoint(impulse.start_date, impulse.low),
                "advance_high": PricePoint(impulse.end_date, impulse.high),
                "pause_low": PricePoint(low.session_date, float(low.low)),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the pause low the structure stops being tight."""
        start = int(structure.parts["pause_start"])
        end = int(structure.parts["structure_end"])
        window = inputs.bars[start : end + 1]
        if not window:
            return 0.0
        low = min(float(b.low) for b in window)
        return float(low * (1.0 - self.engine_config.states.invalidation_buffer_pct))

    def notes(self, structure: Structure) -> tuple[str, ...]:
        impulse = structure.parts["impulse"]
        return (
            f"advance {impulse.gain_pct:.0%} in {impulse.sessions} sessions, pause "
            f"{float(structure.parts['depth']):.0%}; an ordinary bull flag would not "
            "clear the magnitude gate",
        )
