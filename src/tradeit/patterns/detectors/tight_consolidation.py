"""Tight Consolidation.

**The family most easily degraded into nothing.** The brief is explicit that
this must not become a dumping ground for every sideways stock, and the reason
is worth stating plainly: under an absolute threshold — "range under 5%" — a
utility that trades in a 2% weekly range qualifies every week of its life. That
is not a pattern. It is the instrument's ordinary behaviour, and reporting it
says nothing about supply, demand, or anything else.

So **every measurement here is relative to the instrument's own recent past**:

* Range against its own prior range.
* True range against its own prior true range.
* Volume against its own baseline.

A quiet stock that has always been quiet contracts against nothing and scores
close to nothing, which is the correct answer. A stock that normally swings 12%
a week and has just spent eight sessions inside 3% has done something, and that
is what this family is for.

Two further guards. A **required prior advance**, because a tight range after a
decline is a different structure with different odds — the same requirement the
flat base carries and for the same reason. And an **absolute depth ceiling** as a
backstop, because a window can halve its range and still be 30% wide if what
preceded it was chaos; contraction from chaos is not tightness.

**What this is not.** It is not a small flat base and it is not a small VCP. A
flat base asserts a *level* held over weeks; a VCP asserts a *trajectory* of
narrowing legs. This asserts neither — only that the last week or two is quiet
against this instrument's own normal, which is a weaker and shorter-horizon
claim, and the score should be read that way. Where the same window supports a
base or a VCP reading, those detectors will produce them independently; nothing
here suppresses them.

**Discovery is structural.** The window is anchored on the most recent confirmed
swing high or low, and runs from there to the structure boundary. There is no
search over window lengths — searching lengths and keeping the tightest is
exactly how this family becomes a machine for finding the quietest fortnight in
any series, which is a thing that always exists.
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
from tradeit.patterns.config import TightConsolidationConfig
from tradeit.patterns.detectors._base import (
    BaseDetector,
    DetectionInputs,
    Structure,
    unavailable,
)
from tradeit.patterns.primitives import measure_prior_trend, measure_volume
from tradeit.patterns.scoring import band_score, decay_score, ramp_score
from tradeit.patterns.structure import ConsolidationShape, measure_consolidation


class TightConsolidationDetector(BaseDetector):
    """Finds windows that are tight relative to the instrument's own past."""

    name = "tight_consolidation"
    pattern_type = PatternType.TIGHT_CONSOLIDATION

    CONTRACT = DetectorContract(
        required_inputs=("ohlcv_bars",),
        #: `range_contraction` is required rather than optional because without
        #: it the pattern has no content: absolute tightness alone is a
        #: statement about the instrument, not about this window.
        required_components=(
            "range_contraction",
            "absolute_tightness",
            "volatility_contraction",
            "prior_trend",
        ),
        optional_components=("close_clustering", "volume_dryup", "duration"),
        minimum_evidence_coverage=70.0,
    )

    @property
    def config(self) -> TightConsolidationConfig:
        return self.engine_config.tight_consolidation

    @property
    def version(self) -> int:
        return self.config.version

    @property
    def atr_period(self) -> int:
        return self.config.atr_period

    @property
    def minimum_bars(self) -> int:
        return (
            self.config.max_sessions
            + self.config.reference_sessions
            + self.config.prior_trend_sessions
            + self.engine_config.swings.right_bars
            + self.config.atr_period
        )

    def weights(self) -> Mapping[str, float]:
        return self.config.weights

    # -- discovery -----------------------------------------------------------

    def discover(self, inputs: DetectionInputs) -> list[Structure]:
        """Anchored on the most recent confirmed pivot, not searched.

        The quietest fortnight in a series always exists. A detector that
        searches window lengths and keeps the tightest one finds it every time,
        on every instrument, and reports it as a pattern -- which is the
        dumping-ground failure in its purest form and precisely what ADR-0014
        prohibits. The window here starts at a confirmed pivot: a real turning
        point that the market produced, whose position owes nothing to this
        detector's opinion of the result.
        """
        cfg = self.config
        end = inputs.structure_end
        pivots = sorted(
            (s for s in (*inputs.swing_highs, *inputs.swing_lows) if s.index <= end),
            key=lambda s: s.index,
            reverse=True,
        )
        if not pivots:
            return []

        out: list[Structure] = []
        seen: set[int] = set()
        for pivot in pivots:
            start = pivot.index
            sessions = end - start + 1
            if not cfg.min_sessions <= sessions <= cfg.max_sessions:
                continue
            if start in seen:
                continue

            reference_start = max(0, start - cfg.reference_sessions)
            if start - reference_start < cfg.min_sessions:
                continue

            window = measure_consolidation(inputs.bars, start_index=start, end_index=end)
            reference = measure_consolidation(
                inputs.bars, start_index=reference_start, end_index=start - 1
            )
            if window is None or reference is None:
                continue

            if window.depth_pct > cfg.max_depth:
                continue
            ratio = window.depth_pct / reference.depth_pct if reference.depth_pct > 0 else 1.0
            if ratio > cfg.max_range_ratio:
                continue

            trend = measure_prior_trend(
                inputs.bars, end_index=start, lookback=cfg.prior_trend_sessions
            )
            if trend is None or trend.gain_pct < cfg.min_prior_gain:
                continue

            seen.add(start)
            out.append(
                Structure(
                    start_index=start,
                    end_index=inputs.last_index,
                    parts={
                        "window": window,
                        "reference": reference,
                        "trend": trend,
                        "ratio": ratio,
                        "reference_start": reference_start,
                        "structure_end": end,
                    },
                    reason=(
                        f"{sessions} sessions inside {window.depth_pct:.1%}, "
                        f"{ratio:.0%} of the prior range"
                    ),
                )
            )
        return out

    # -- scoring -------------------------------------------------------------

    def score(self, inputs: DetectionInputs, structure: Structure) -> list[ComponentScore]:
        cfg = self.config
        weights = cfg.weights
        window: ConsolidationShape = structure.parts["window"]
        reference: ConsolidationShape = structure.parts["reference"]
        trend = structure.parts["trend"]
        ratio = float(structure.parts["ratio"])
        reference_start = int(structure.parts["reference_start"])
        end = int(structure.parts["structure_end"])
        components: list[ComponentScore] = []

        # -- the component that gives the family its meaning
        components.append(
            ComponentScore(
                name="range_contraction",
                requirement=ComponentRequirement.REQUIRED,
                score=decay_score(ratio, full_at=0.20, zero_at=cfg.max_range_ratio),
                weight=weights["range_contraction"],
                measurements={
                    "range_ratio": ratio,
                    "window_depth_pct": window.depth_pct,
                    "reference_depth_pct": reference.depth_pct,
                },
                evidence=(
                    (
                        Evidence(
                            f"{window.sessions} sessions inside {window.depth_pct:.1%}, which "
                            f"is {ratio:.0%} of this instrument's own prior "
                            f"{reference.depth_pct:.1%} range",
                            measured=ratio,
                        ),
                    )
                    if ratio <= cfg.ideal_range_ratio
                    else ()
                ),
                contradicting=(
                    (
                        Evidence(
                            f"the range is {ratio:.0%} of the prior window's: quiet, but not "
                            "quieter than this instrument's normal",
                            measured=ratio,
                        ),
                    )
                    if ratio > cfg.ideal_range_ratio
                    else ()
                ),
            )
        )

        # -- the backstop: contraction from chaos is not tightness
        components.append(
            ComponentScore(
                name="absolute_tightness",
                requirement=ComponentRequirement.REQUIRED,
                score=band_score(
                    window.depth_pct,
                    ideal_low=0.005,
                    ideal_high=cfg.ideal_depth,
                    tolerance_low=0.0,
                    tolerance_high=cfg.max_depth,
                ),
                weight=weights["absolute_tightness"],
                measurements={"depth_pct": window.depth_pct},
            )
        )

        # -- true range against the instrument's own prior true range
        #
        # Measured on each window's own bars rather than from the smoothed ATR
        # series. ATR carries a fourteen-session memory, and these windows are
        # five to twenty-five sessions long, so ATR *inside* a tight window is
        # still mostly the volatility that preceded it -- the ratio came out
        # near one for a window whose bars had genuinely halved in range, and
        # the component reported no contraction where there plainly was one.
        if reference.mean_range_pct <= 0:
            components.append(
                unavailable(
                    "volatility_contraction",
                    weights["volatility_contraction"],
                    "the reference window has no measurable range to contract from",
                )
            )
        else:
            atr_ratio = window.mean_range_pct / reference.mean_range_pct
            components.append(
                ComponentScore(
                    name="volatility_contraction",
                    requirement=ComponentRequirement.REQUIRED,
                    score=decay_score(atr_ratio, full_at=0.45, zero_at=cfg.max_atr_ratio),
                    weight=weights["volatility_contraction"],
                    measurements={
                        "range_ratio": atr_ratio,
                        "window_mean_range_pct": window.mean_range_pct,
                        "reference_mean_range_pct": reference.mean_range_pct,
                    },
                )
            )

        components.append(
            ComponentScore(
                name="prior_trend",
                requirement=ComponentRequirement.REQUIRED,
                score=ramp_score(trend.gain_pct, zero_at=0.0, full_at=0.30),
                weight=weights["prior_trend"],
                measurements={
                    "gain_pct": trend.gain_pct,
                    "distance_from_high": trend.distance_from_high,
                },
                contradicting=(
                    (
                        Evidence(
                            "a quiet window after a decline is a different structure with "
                            "different odds from a pause inside an advance",
                            EvidenceKind.CONTEXTUAL,
                            measured=trend.gain_pct,
                        ),
                    )
                    if trend.gain_pct < cfg.min_prior_gain
                    else ()
                ),
            )
        )

        components.append(
            ComponentScore(
                name="close_clustering",
                score=decay_score(window.close_dispersion, full_at=0.004, zero_at=0.05),
                weight=weights["close_clustering"],
                measurements={"close_dispersion": window.close_dispersion},
            )
        )

        volume = measure_volume(
            inputs.bars,
            start_index=window.start_index,
            end_index=end,
            baseline_start=reference_start,
            baseline_end=window.start_index - 1,
        )
        if volume is None:
            components.append(
                unavailable("volume_dryup", weights["volume_dryup"], "window too short for volume")
            )
        else:
            components.append(
                ComponentScore(
                    name="volume_dryup",
                    score=round(
                        0.6 * decay_score(volume.ratio, full_at=0.55, zero_at=1.2)
                        + 0.4 * ramp_score(volume.dry_session_fraction, zero_at=0.2, full_at=0.8),
                        6,
                    ),
                    weight=weights["volume_dryup"],
                    measurements={
                        "volume_ratio": volume.ratio,
                        "dry_session_fraction": volume.dry_session_fraction,
                    },
                )
            )

        components.append(
            ComponentScore(
                name="duration",
                score=band_score(
                    float(window.sessions),
                    ideal_low=float(cfg.ideal_sessions_low),
                    ideal_high=float(cfg.ideal_sessions_high),
                    tolerance_low=float(cfg.min_sessions),
                    tolerance_high=float(cfg.max_sessions),
                    floor=30.0,
                ),
                weight=weights["duration"],
                measurements={"sessions": float(window.sessions)},
            )
        )
        return components

    def notes(self, structure: Structure) -> tuple[str, ...]:
        return (
            "a short-horizon claim: this window is quiet against this instrument's own "
            "normal, which is weaker than a base's claim that a level held for weeks",
        )

    # -- geometry ------------------------------------------------------------

    def geometry(self, inputs: DetectionInputs, structure: Structure) -> PatternGeometry:
        window: ConsolidationShape = structure.parts["window"]
        return PatternGeometry(
            start_date=window.start_date,
            end_date=inputs.bars[inputs.last_index].session_date,
            segments={"consolidation": (window.start_date, window.end_date)},
            resistance=Boundary(
                kind="resistance",
                method="single_extreme",
                level=window.high,
                anchor_date=window.end_date,
                confidence=30.0,
            ),
            support=Boundary(
                kind="support",
                method="single_extreme",
                level=window.low,
                anchor_date=window.end_date,
                confidence=30.0,
            ),
            key_points={
                "window_high": PricePoint(window.end_date, window.high),
                "window_low": PricePoint(window.end_date, window.low),
            },
        )

    def invalidation(self, inputs: DetectionInputs, structure: Structure) -> float:
        """Below the window's low it is no longer this window."""
        window: ConsolidationShape = structure.parts["window"]
        return float(window.low * 0.995)


__all__ = ["TightConsolidationDetector"]
