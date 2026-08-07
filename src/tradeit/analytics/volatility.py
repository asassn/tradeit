"""Volatility regime.

Separate from the market regime because it answers a different question and
because the two disagree in the situations that matter most. A market can be in
a strong uptrend with elevated volatility (2020 H2) or a mild downtrend with low
volatility (2015), and collapsing both into one state loses exactly the
information that would drive position sizing.

**Thresholds are percentiles, not absolute levels.** 20% annualised realised
volatility meant something very different in 2017 than in 2020. A fixed
threshold silently reclassifies the entire market when the volatility level
shifts, and a model calibrated on one era misfires in the next. Percentiles of
the instrument's own trailing history adapt without needing recalibration.

Phase 3 computes and explains the regime. It deliberately does **not** act on
it: position sizing, breakout-quality adjustment and exposure limits are Phase 5
and Phase 8, and wiring them here would put trading behaviour in the analytics
layer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.core.enums import Bartimeframe
from tradeit.strategy.config import VolatilityRegimeConfig

VOLATILITY_INPUTS = ("ohlcv_bars", "indicator_values")


class VolatilityRegime(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


#: The states that count as elevated, used in several places. Defined once so
#: the three-way membership test cannot drift apart between them.
_ELEVATED_REGIMES: frozenset[str] = frozenset()  # populated below


@dataclass(frozen=True, slots=True)
class VolatilityRegimeState:
    """A classification with the evidence that produced it."""

    session_date: dt.date
    regime: VolatilityRegime
    confidence: int
    realized_volatility: float | None
    volatility_percentile: float | None
    atr_percent: float | None
    gap_frequency: float | None
    cross_sectional_volatility: float | None
    expansion: float | None
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    inputs_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_elevated(self) -> bool:
        return self.regime in _ELEVATED_REGIMES

    def explain(self) -> str:
        lines = [
            f"Volatility Regime: {self.regime}",
            f"Confidence: {self.confidence}",
            "Supporting Evidence:",
            *(f"  {item}" for item in self.supporting_evidence or ("  (none)",)),
        ]
        if self.contradicting_evidence:
            lines.append("Contradicting Evidence:")
            lines.extend(f"  {item}" for item in self.contradicting_evidence)
        return "\n".join(lines)


_ELEVATED_REGIMES = frozenset(
    {VolatilityRegime.ELEVATED, VolatilityRegime.HIGH, VolatilityRegime.EXTREME}
)


class VolatilityRegimeEngine:
    """Classifies volatility from realised volatility, ATR and gap behaviour."""

    def __init__(self, config: VolatilityRegimeConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    @staticmethod
    def _build_registry(config: VolatilityRegimeConfig) -> FeatureRegistry:
        registry = FeatureRegistry()
        warmup = config.percentile_lookback + config.realized_vol_period

        def spec(
            name: str,
            description: str,
            output: OutputType,
            parameters: dict[str, object],
            null: NullBehaviour = NullBehaviour.WARMUP,
        ) -> None:
            registry.register(
                FeatureSpec(
                    name=name,
                    version=1,
                    description=description,
                    kind=FeatureKind.MARKET_LEVEL,
                    timeframe=Bartimeframe.D1,
                    output_type=output,
                    null_behaviour=null,
                    input_datasets=VOLATILITY_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                )
            )

        spec(
            "volatility_regime",
            "LOW / NORMAL / ELEVATED / HIGH / EXTREME, from percentiles of the "
            "market's own trailing volatility rather than absolute thresholds.",
            OutputType.CATEGORICAL,
            {
                "vol_period": config.realized_vol_period,
                "percentile_lookback": config.percentile_lookback,
                "thresholds": dict(config.thresholds),
            },
        )
        spec(
            "volatility_regime_confidence",
            "0-100. How far the reading sits from its band edges; a value at a "
            "boundary is genuinely ambiguous and says so.",
            OutputType.SCORE_0_100,
            {},
        )
        spec(
            "volatility_expansion",
            "Recent realised volatility against its longer baseline. Above 1 means expanding.",
            OutputType.RATIO,
            {"period": config.realized_vol_period},
        )
        spec(
            "cross_sectional_volatility",
            "Dispersion of returns across the universe. High dispersion with low "
            "index volatility is a distinct state: the index is calm because "
            "constituents are cancelling out, not because nothing is moving.",
            OutputType.RATIO,
            {},
            NullBehaviour.MISSING_INPUT,
        )
        registry.validate()
        return registry

    def classify(
        self,
        session_date: dt.date,
        *,
        realized_volatility: float | None,
        volatility_percentile: float | None,
        atr_percent: float | None = None,
        gap_frequency: float | None = None,
        cross_sectional_volatility: float | None = None,
        volatility_expansion: float | None = None,
    ) -> VolatilityRegimeState:
        """Classify one date.

        The percentile is the primary input; everything else refines the
        confidence and the evidence. When the percentile is unavailable the
        result is ``UNKNOWN`` with confidence 0 rather than a guess from ATR
        alone — an unreliable regime label is worse than an absent one, because
        downstream code will act on it.
        """
        supporting: list[str] = []
        contradicting: list[str] = []
        inputs_used: list[str] = []
        notes: list[str] = []

        if volatility_percentile is None or not np.isfinite(volatility_percentile):
            return VolatilityRegimeState(
                session_date=session_date,
                regime=VolatilityRegime.UNKNOWN,
                confidence=0,
                realized_volatility=realized_volatility,
                volatility_percentile=None,
                atr_percent=atr_percent,
                gap_frequency=gap_frequency,
                cross_sectional_volatility=cross_sectional_volatility,
                expansion=volatility_expansion,
                notes=(
                    "insufficient history for a volatility percentile; refusing to "
                    "guess a regime from absolute levels",
                ),
            )

        inputs_used.append("volatility_percentile")
        thresholds = self.config.thresholds
        percentile = float(volatility_percentile)

        if percentile < thresholds["LOW"]:
            regime = VolatilityRegime.LOW
            band = (0.0, thresholds["LOW"])
        elif percentile < thresholds["NORMAL"]:
            regime = VolatilityRegime.NORMAL
            band = (thresholds["LOW"], thresholds["NORMAL"])
        elif percentile < thresholds["ELEVATED"]:
            regime = VolatilityRegime.ELEVATED
            band = (thresholds["NORMAL"], thresholds["ELEVATED"])
        elif percentile < thresholds["HIGH"]:
            regime = VolatilityRegime.HIGH
            band = (thresholds["ELEVATED"], thresholds["HIGH"])
        else:
            regime = VolatilityRegime.EXTREME
            band = (thresholds["HIGH"], 1.0)

        supporting.append(
            f"Realised volatility at the {percentile * 100:.0f}th percentile of its "
            f"trailing {self.config.percentile_lookback} sessions"
        )
        if realized_volatility is not None and np.isfinite(realized_volatility):
            supporting.append(f"Annualised realised volatility {realized_volatility:.1%}")

        if atr_percent is not None and np.isfinite(atr_percent):
            inputs_used.append("atr_percent")
            supporting.append(f"ATR at {atr_percent:.2%} of price")

        if volatility_expansion is not None and np.isfinite(volatility_expansion):
            inputs_used.append("volatility_expansion")
            if volatility_expansion > 1.15:
                supporting.append(
                    f"Volatility expanding ({volatility_expansion:.2f}x its baseline)"
                )
            elif volatility_expansion < 0.85:
                if regime in _ELEVATED_REGIMES:
                    contradicting.append(
                        f"Volatility contracting ({volatility_expansion:.2f}x baseline) "
                        "despite an elevated percentile"
                    )
                else:
                    supporting.append(
                        f"Volatility contracting ({volatility_expansion:.2f}x baseline)"
                    )

        if gap_frequency is not None and np.isfinite(gap_frequency):
            inputs_used.append("gap_frequency")
            if gap_frequency > 0.25:
                supporting.append(
                    f"{gap_frequency:.0%} of recent sessions gapped beyond "
                    f"{self.config.gap_threshold_pct:.0%}"
                )
            elif gap_frequency < 0.05 and regime in (
                VolatilityRegime.HIGH,
                VolatilityRegime.EXTREME,
            ):
                contradicting.append(
                    "Gaps are rare despite a high volatility percentile; the move is "
                    "continuous rather than discontinuous"
                )

        if cross_sectional_volatility is not None and np.isfinite(cross_sectional_volatility):
            inputs_used.append("cross_sectional_volatility")
            if cross_sectional_volatility > 0.30 and regime in (
                VolatilityRegime.LOW,
                VolatilityRegime.NORMAL,
            ):
                contradicting.append(
                    f"Cross-sectional dispersion is high ({cross_sectional_volatility:.1%}) "
                    "while index volatility is not: constituents are moving and "
                    "cancelling out"
                )
            elif cross_sectional_volatility > 0.30:
                supporting.append(
                    f"Cross-sectional dispersion elevated ({cross_sectional_volatility:.1%})"
                )

        confidence = _band_confidence(percentile, band, contradicting_count=len(contradicting))
        if len(inputs_used) == 1:
            notes.append(
                "classified from the volatility percentile alone; ATR, gap frequency "
                "and dispersion were unavailable"
            )

        return VolatilityRegimeState(
            session_date=session_date,
            regime=regime,
            confidence=confidence,
            realized_volatility=realized_volatility,
            volatility_percentile=percentile,
            atr_percent=atr_percent,
            gap_frequency=gap_frequency,
            cross_sectional_volatility=cross_sectional_volatility,
            expansion=volatility_expansion,
            supporting_evidence=tuple(supporting),
            contradicting_evidence=tuple(contradicting),
            inputs_used=tuple(inputs_used),
            notes=tuple(notes),
        )

    @staticmethod
    def cross_sectional_dispersion(returns: Sequence[float | None]) -> float | None:
        """Standard deviation of one date's returns across the universe.

        Computed over the point-in-time roster supplied by the caller. High
        dispersion with a calm index is a genuinely different market from low
        dispersion with a calm index, and only this measure distinguishes them.
        """
        finite = [r for r in returns if r is not None and np.isfinite(r)]
        if len(finite) < 20:
            return None
        return float(np.std(finite, ddof=1))


def _band_confidence(value: float, band: tuple[float, float], *, contradicting_count: int) -> int:
    """How firmly a value sits inside its band, 0-100.

    A reading at a band edge is genuinely ambiguous, and reporting 95%
    confidence for it would be a lie the downstream consumer cannot detect.
    Contradicting evidence reduces it further.
    """
    low, high = band
    width = high - low
    if width <= 0:
        return 50
    position = (value - low) / width
    # 1.0 at the band centre, 0 at either edge.
    centrality = 1.0 - 2.0 * abs(position - 0.5)
    base = 50.0 + 50.0 * max(centrality, 0.0)
    penalty = 12.0 * contradicting_count
    return round(max(0.0, min(100.0, base - penalty)))
