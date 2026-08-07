"""Market regime classification.

A **transparent, rule-based, first-generation** classifier. Deliberately not a
machine-learning model, for three reasons that all point the same way:

1. There are only a few dozen genuine regime changes in the whole modern record.
   That is not enough to fit anything with meaningful degrees of freedom without
   memorising the sample.
2. A regime label drives position sizing and exposure. When it is wrong, the
   first question is "why did it say BULL?" — and a gradient-boosted answer is
   not an answer.
3. A model fitted on 2000-2024 has seen every crash in the test set.

So the classifier is a weighted sum of named, individually interpretable
signals, each contributing a score in [-1, +1]. Every signal that fired is
recorded, **both for and against**, because a BULL reading with three
contradicting signals is a materially different statement from one with none,
and only recording the supporting evidence hides exactly that.

**Thresholds were not fitted to historical returns.** The brief is explicit on
this and it matters: tuning them in Phase 3 would produce a regime model that
looks excellent on the data it was tuned on and says nothing about the future.
They are documented starting points for Phase 9 to test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.analytics.volatility import VolatilityRegime
from tradeit.core.enums import Bartimeframe, MarketRegime
from tradeit.errors import DataError
from tradeit.strategy.config import RegimeConfig

REGIME_INPUTS = ("ohlcv_bars", "indicator_values", "universe_memberships")

#: Regimes ordered from strongest to weakest. Used for threshold bucketing.
_ORDERED_REGIMES = (
    MarketRegime.BULL_TRENDING,
    MarketRegime.BULL_CHOPPY,
    MarketRegime.NEUTRAL,
    MarketRegime.BEAR_CHOPPY,
    MarketRegime.BEAR_TRENDING,
)

#: The six states the brief requires, mapped onto the persisted vocabulary from
#: Phase 2. The persisted enum is deliberately not renamed -- it is already in
#: the schema -- so the mapping is stated once, here.
REGIME_LABELS: dict[MarketRegime, str] = {
    MarketRegime.BULL_TRENDING: "STRONG_BULL",
    MarketRegime.BULL_CHOPPY: "BULL",
    MarketRegime.NEUTRAL: "NEUTRAL",
    MarketRegime.BEAR_CHOPPY: "WEAK",
    MarketRegime.BEAR_TRENDING: "BEAR",
    MarketRegime.HIGH_VOLATILITY: "SEVERE_RISK_OFF",
    MarketRegime.UNKNOWN: "UNKNOWN",
}

_THRESHOLD_KEYS = {
    MarketRegime.BULL_TRENDING: "STRONG_BULL",
    MarketRegime.BULL_CHOPPY: "BULL",
    MarketRegime.NEUTRAL: "NEUTRAL",
    MarketRegime.BEAR_CHOPPY: "WEAK",
    MarketRegime.BEAR_TRENDING: "BEAR",
}


@dataclass(frozen=True, slots=True)
class BenchmarkTrendInput:
    """One benchmark's trend structure on the evaluation date."""

    symbol: str
    close: float | None = None
    ma_fast: float | None = None
    ma_slow: float | None = None
    ma_fast_slope: float | None = None
    ma_slow_slope: float | None = None

    @property
    def above_fast(self) -> bool | None:
        if self.close is None or self.ma_fast is None:
            return None
        return self.close > self.ma_fast

    @property
    def above_slow(self) -> bool | None:
        if self.close is None or self.ma_slow is None:
            return None
        return self.close > self.ma_slow

    @property
    def fast_above_slow(self) -> bool | None:
        if self.ma_fast is None or self.ma_slow is None:
            return None
        return self.ma_fast > self.ma_slow


@dataclass(frozen=True, slots=True)
class RegimeSignal:
    """One named, interpretable contribution to the composite score."""

    name: str
    score: float
    weight: float
    evidence: str
    #: Whether this signal reads *bullish*. Note carefully: this is the sign of
    #: the signal, not "supports the classification". In a bear market every
    #: signal is bearish and every one of them supports the BEAR conclusion.
    #: Conflating the two put all seven signals under "Contradicting Evidence"
    #: on a maximum-conviction risk-off reading -- the evidence lists were
    #: exactly inverted for every negative regime. Use :meth:`agrees_with`.
    supports: bool

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    def agrees_with(self, direction: int) -> bool:
        """Whether this signal points the same way as the composite.

        ``direction`` is the sign of the composite score. A zero-score signal
        agrees with nothing: it is the absence of evidence, and filing it as
        support would overstate the case.
        """
        if self.score == 0 or direction == 0:
            return False
        return (self.score > 0) == (direction > 0)


@dataclass(frozen=True, slots=True)
class MarketRegimeState:
    """A regime classification with full, symmetric evidence."""

    session_date: dt.date
    regime: MarketRegime
    label: str
    composite_score: float
    confidence: int
    signals: tuple[RegimeSignal, ...]
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    volatility_regime: VolatilityRegime | None = None
    universe_size: int | None = None
    strategy_config_digest: str = ""
    notes: tuple[str, ...] = ()

    @property
    def is_risk_on(self) -> bool:
        return self.regime in (MarketRegime.BULL_TRENDING, MarketRegime.BULL_CHOPPY)

    @property
    def reconciles(self) -> bool:
        """Whether the recorded signals actually sum to the composite score.

        Guards a specific failure: a late adjustment applied without being
        recorded as a signal, producing an explanation that does not add up to
        the number it explains.
        """
        total_weight = sum(s.weight for s in self.signals)
        if total_weight <= 0:
            return self.composite_score == 0.0
        expected = sum(s.contribution for s in self.signals) / total_weight
        # composite_score is stored rounded to 6dp, so the tolerance must exceed
        # half a unit in the last place rather than being nominally exact.
        return abs(expected - self.composite_score) < 1e-6

    def explain(self) -> str:
        """The human-readable output the brief specifies."""
        lines = [
            f"Market Regime: {self.label}",
            f"Confidence: {self.confidence}",
            "Supporting Evidence:",
        ]
        lines.extend(f"  {item}" for item in self.supporting_evidence or ("(none)",))
        lines.append("Contradicting Evidence:")
        lines.extend(f"  {item}" for item in self.contradicting_evidence or ("(none)",))
        return "\n".join(lines)


class MarketRegimeEngine:
    """Rule-based regime classification from trend, breadth and participation."""

    def __init__(self, config: RegimeConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    @staticmethod
    def _build_registry(config: RegimeConfig) -> FeatureRegistry:
        registry = FeatureRegistry()
        warmup = config.trend_ma_slow + config.slope_lookback

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
                    input_datasets=REGIME_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                )
            )

        spec(
            "market_regime",
            "STRONG_BULL / BULL / NEUTRAL / WEAK / BEAR / SEVERE_RISK_OFF from a "
            "weighted sum of named, individually interpretable signals.",
            OutputType.CATEGORICAL,
            {
                "weights": config.normalised_signal_weights(),
                "thresholds": dict(config.thresholds),
                "benchmarks": list(config.benchmarks),
                "ma_fast": config.trend_ma_fast,
                "ma_slow": config.trend_ma_slow,
            },
        )
        spec(
            "market_regime_score",
            "Composite score in [-1, +1]. The number the regime bands partition.",
            OutputType.RATIO,
            {"weights": config.normalised_signal_weights()},
        )
        spec(
            "market_regime_confidence",
            "0-100. Reduced by contradicting signals and by proximity to a band "
            "edge, so a borderline reading says it is borderline.",
            OutputType.SCORE_0_100,
            {},
        )
        registry.validate()
        return registry

    # -- signals -------------------------------------------------------------

    def _primary_trend(self, benchmark: BenchmarkTrendInput) -> RegimeSignal | None:
        """Price against the 50 and 200-day averages, and their ordering."""
        above_fast, above_slow = benchmark.above_fast, benchmark.above_slow
        if above_fast is None or above_slow is None:
            return None

        stacked = benchmark.fast_above_slow
        score = 0.0
        parts: list[str] = []
        if above_slow:
            score += 0.5
            parts.append(f"{benchmark.symbol} above {self.config.trend_ma_slow}DMA")
        else:
            score -= 0.5
            parts.append(f"{benchmark.symbol} below {self.config.trend_ma_slow}DMA")
        if above_fast:
            score += 0.3
            parts.append(f"above {self.config.trend_ma_fast}DMA")
        else:
            score -= 0.3
            parts.append(f"below {self.config.trend_ma_fast}DMA")
        if stacked is True:
            score += 0.2
            parts.append("averages stacked bullishly")
        elif stacked is False:
            score -= 0.2
            parts.append("averages stacked bearishly")

        return RegimeSignal(
            name="primary_trend",
            score=_clip(score),
            weight=self.config.signal_weights.get("primary_trend", 0.0),
            evidence=", ".join(parts),
            supports=score > 0,
        )

    def _benchmark_agreement(
        self, benchmarks: Mapping[str, BenchmarkTrendInput]
    ) -> RegimeSignal | None:
        """Do SPY, QQQ and IWM agree?

        Disagreement is informative in its own right. A market where large-cap
        growth leads while small caps lag is a narrower market than one where
        all three participate, and the classifier should not average that away.
        """
        states = [b.above_slow for b in benchmarks.values() if b.above_slow is not None]
        if not states:
            return None

        agreeing = sum(states)
        total = len(states)
        score = (2.0 * agreeing / total) - 1.0
        names_above = sorted(s for s, b in benchmarks.items() if b.above_slow)
        names_below = sorted(s for s, b in benchmarks.items() if b.above_slow is False)

        if names_below and names_above:
            evidence = (
                f"{', '.join(names_above)} above {self.config.trend_ma_slow}DMA but "
                f"{', '.join(names_below)} below"
            )
        elif names_above:
            evidence = f"all benchmarks above {self.config.trend_ma_slow}DMA"
        else:
            evidence = f"all benchmarks below {self.config.trend_ma_slow}DMA"

        return RegimeSignal(
            name="benchmark_agreement",
            score=_clip(score),
            weight=self.config.signal_weights.get("benchmark_agreement", 0.0),
            evidence=evidence,
            supports=score > 0,
        )

    def _ma_slope(self, benchmark: BenchmarkTrendInput) -> RegimeSignal | None:
        """Is the trend improving or rolling over?

        A market above a *falling* 200-day average is a different state from one
        above a rising average, and price-versus-average alone cannot see it.
        """
        slope = benchmark.ma_fast_slope
        if slope is None or not np.isfinite(slope):
            return None
        # A per-session slope of 0.001 (0.1% per day) is a firm trend.
        score = _clip(slope / 0.001)
        direction = "rising" if slope > 0 else "falling" if slope < 0 else "flat"
        return RegimeSignal(
            name="ma_slope",
            score=score,
            weight=self.config.signal_weights.get("ma_slope", 0.0),
            evidence=(
                f"{benchmark.symbol} {self.config.trend_ma_fast}DMA {direction} "
                f"({slope * 100:.3f}%/session)"
            ),
            supports=score > 0,
        )

    def _breadth(
        self, pct_above_200: float | None, universe_size: int | None
    ) -> RegimeSignal | None:
        if pct_above_200 is None or not np.isfinite(pct_above_200):
            return None
        if not 0.0 <= pct_above_200 <= 1.0:
            # A *fraction*, matching what BreadthEngine emits. The parameter name
            # says "pct" and invites a caller to pass 3.0 for 3%, which without
            # this guard scores +1.0 -- maximally bullish -- for the single most
            # bearish breadth reading possible. Found by the Phase 3 validation
            # gate doing exactly that. Raising is the only safe response: there
            # is no way to tell 0.03 from a genuine 3% at the boundary, so
            # guessing would trade a loud failure for a silent one.
            raise DataError(
                f"pct_above_200dma must be a fraction in [0, 1]; got {pct_above_200}. "
                "Breadth is supplied as a fraction (0.03 for 3%), not a percentage."
            )
        # 50% above the 200DMA is neutral; 80% is firmly bullish.
        score = _clip((pct_above_200 - 0.5) / 0.3)
        size = f" of {universe_size} eligible" if universe_size else ""
        return RegimeSignal(
            name="breadth_above_200dma",
            score=score,
            weight=self.config.signal_weights.get("breadth_above_200dma", 0.0),
            evidence=f"{pct_above_200:.0%}{size} above their 200DMA",
            supports=score > 0,
        )

    def _new_high_low(self, new_highs: int | None, new_lows: int | None) -> RegimeSignal | None:
        if new_highs is None or new_lows is None:
            return None
        total = new_highs + new_lows
        if total == 0:
            return RegimeSignal(
                name="new_high_low",
                score=0.0,
                weight=self.config.signal_weights.get("new_high_low", 0.0),
                evidence="no new 52-week highs or lows",
                supports=False,
            )
        score = _clip((new_highs - new_lows) / total)
        comparison = "exceed" if new_highs > new_lows else "trail"
        return RegimeSignal(
            name="new_high_low",
            score=score,
            weight=self.config.signal_weights.get("new_high_low", 0.0),
            evidence=f"new highs ({new_highs}) {comparison} new lows ({new_lows})",
            supports=score > 0,
        )

    def _sector_participation(
        self, sectors_above_fast: int | None, sectors_total: int | None
    ) -> RegimeSignal | None:
        # `is None`, not falsiness. Zero sectors above their moving average is
        # the most bearish participation reading available, and treating it as
        # a missing input discarded the signal precisely when it mattered most
        # -- in the March 2020 scenario it silently removed the sector evidence
        # from a maximum-risk-off classification. Found by the Phase 3
        # validation gate.
        if sectors_above_fast is None or not sectors_total:
            return None
        if sectors_above_fast < 0 or sectors_above_fast > sectors_total:
            raise DataError(
                f"sectors_above_fast_ma ({sectors_above_fast}) is outside [0, {sectors_total}]"
            )
        fraction = sectors_above_fast / sectors_total
        score = _clip((fraction - 0.5) / 0.35)
        return RegimeSignal(
            name="sector_participation",
            score=score,
            weight=self.config.signal_weights.get("sector_participation", 0.0),
            evidence=(
                f"{sectors_above_fast} of {sectors_total} sectors above their "
                f"{self.config.trend_ma_fast}DMA"
            ),
            supports=score > 0,
        )

    def _observations(self, benchmarks: Mapping[str, BenchmarkTrendInput]) -> list[tuple[str, int]]:
        """Per-benchmark trend facts, each tagged with the direction it implies.

        The aggregate agreement signal deliberately scores the *balance* across
        benchmarks, which means a single lagging index can be outvoted and
        vanish from the output. That lag is exactly the kind of thing a reader
        needs to see: a bull market where small caps are below their 50DMA is
        narrower than one where they are not.

        Both directions are emitted. A benchmark *holding above* its 200DMA
        while the composite is bearish is the mirror-image observation and just
        as informative -- it is what an early turn looks like -- and only
        recording the bearish half made it invisible.

        The direction tag is what lets the caller decide whether an observation
        corroborates the classification or cuts against it. Getting that
        backwards is not cosmetic: before this was tagged, every bearish
        observation was filed as *contradicting* regardless of the composite,
        so the March 2020 scenario -- with all seven signals in agreement and
        nine benchmark observations all confirming -- scored 64/100 confidence
        and would have scored lower still the more the benchmarks agreed.

        These strings never contribute to the composite, so ``reconciles``
        still holds.
        """
        out: list[tuple[str, int]] = []
        fast, slow = self.config.trend_ma_fast, self.config.trend_ma_slow
        for symbol in sorted(benchmarks):
            benchmark = benchmarks[symbol]
            if benchmark.above_fast is False:
                out.append((f"{symbol} below {fast}DMA", -1))
            elif benchmark.above_fast is True:
                out.append((f"{symbol} above {fast}DMA", 1))
            if benchmark.above_slow is False:
                out.append((f"{symbol} below {slow}DMA", -1))
            elif benchmark.above_slow is True:
                out.append((f"{symbol} above {slow}DMA", 1))
            if benchmark.ma_fast_slope is not None:
                if benchmark.ma_fast_slope < 0:
                    out.append((f"{symbol} {fast}DMA falling", -1))
                elif benchmark.ma_fast_slope > 0:
                    out.append((f"{symbol} {fast}DMA rising", 1))
        return out

    def _volatility(self, volatility_regime: VolatilityRegime | None) -> RegimeSignal | None:
        if volatility_regime is None or volatility_regime is VolatilityRegime.UNKNOWN:
            return None
        scores = {
            VolatilityRegime.LOW: 0.5,
            VolatilityRegime.NORMAL: 0.25,
            VolatilityRegime.ELEVATED: -0.25,
            VolatilityRegime.HIGH: -0.7,
            VolatilityRegime.EXTREME: -1.0,
        }
        score = scores.get(volatility_regime, 0.0)
        return RegimeSignal(
            name="volatility",
            score=score,
            weight=self.config.signal_weights.get("volatility", 0.0),
            evidence=f"volatility regime {volatility_regime}",
            supports=score > 0,
        )

    # -- classification ------------------------------------------------------

    def classify(
        self,
        session_date: dt.date,
        benchmarks: Mapping[str, BenchmarkTrendInput],
        *,
        pct_above_200dma: float | None = None,
        new_highs: int | None = None,
        new_lows: int | None = None,
        sectors_above_fast_ma: int | None = None,
        sectors_total: int | None = None,
        volatility_regime: VolatilityRegime | None = None,
        universe_size: int | None = None,
        strategy_config_digest: str = "",
    ) -> MarketRegimeState:
        """Classify one date from the evidence supplied.

        Deterministic: the same inputs always produce the same regime, score and
        evidence. Nothing here reads a clock, a database or a global.
        """
        primary_symbol = self.config.benchmarks[0] if self.config.benchmarks else "SPY"
        primary = benchmarks.get(primary_symbol)

        signals: list[RegimeSignal] = []
        for signal in (
            self._primary_trend(primary) if primary else None,
            self._benchmark_agreement(benchmarks),
            self._ma_slope(primary) if primary else None,
            self._breadth(pct_above_200dma, universe_size),
            self._new_high_low(new_highs, new_lows),
            self._sector_participation(sectors_above_fast_ma, sectors_total),
            self._volatility(volatility_regime),
        ):
            if signal is not None and signal.weight > 0:
                signals.append(signal)

        notes: list[str] = []
        if not signals:
            return MarketRegimeState(
                session_date=session_date,
                regime=MarketRegime.UNKNOWN,
                label=REGIME_LABELS[MarketRegime.UNKNOWN],
                composite_score=0.0,
                confidence=0,
                signals=(),
                supporting_evidence=(),
                contradicting_evidence=(),
                volatility_regime=volatility_regime,
                universe_size=universe_size,
                strategy_config_digest=strategy_config_digest,
                notes=("no regime signals could be computed from the supplied inputs",),
            )

        total_weight = sum(s.weight for s in signals)
        composite = sum(s.contribution for s in signals) / total_weight

        regime = self._bucket(composite)

        # SEVERE_RISK_OFF is an override, not a band. A market can be mildly
        # negative on trend while volatility is genuinely extreme, and that is a
        # different operating environment from an ordinary bear -- one where
        # position sizing should shrink regardless of what the trend says.
        if (
            volatility_regime is not None
            and str(volatility_regime) in self.config.severe_risk_off_volatility_states
            and composite < self.config.thresholds["NEUTRAL"]
        ):
            regime = MarketRegime.HIGH_VOLATILITY
            notes.append(
                f"volatility regime {volatility_regime} with a negative composite "
                "score overrides the trend-based band"
            )

        # Per-benchmark observations are unscored; they enrich the explanation
        # without touching the composite. Each is filed by whether it points the
        # same way as the composite, so a confirming observation strengthens the
        # picture instead of being logged as a contradiction.
        direction = 1 if composite > 0 else -1 if composite < 0 else 0
        corroborating = [t for t, d in self._observations(benchmarks) if d == direction]
        opposing = [t for t, d in self._observations(benchmarks) if d != direction]

        # Evidence is filed relative to the *classification*, not relative to
        # bullishness. In a BEAR reading, "0 of 11 sectors above their 50DMA" is
        # supporting evidence.
        supporting = tuple(
            dict.fromkeys([s.evidence for s in signals if s.agrees_with(direction)] + corroborating)
        )
        contradicting = tuple(
            dict.fromkeys(
                [s.evidence for s in signals if not s.agrees_with(direction)]
                + [t for t in opposing if t not in supporting]
            )
        )
        # Only *opposing* observations reduce confidence. Unanimity is not
        # ambiguity.
        confidence = self._confidence(composite, regime, signals, len(opposing))

        if len(signals) < 4:
            notes.append(
                f"only {len(signals)} of 7 signals were computable; confidence is "
                "reduced accordingly"
            )

        return MarketRegimeState(
            session_date=session_date,
            regime=regime,
            label=REGIME_LABELS[regime],
            composite_score=round(composite, 6),
            confidence=confidence,
            signals=tuple(signals),
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            volatility_regime=volatility_regime,
            universe_size=universe_size,
            strategy_config_digest=strategy_config_digest,
            notes=tuple(notes),
        )

    def _bucket(self, composite: float) -> MarketRegime:
        for regime in _ORDERED_REGIMES:
            if composite >= self.config.thresholds[_THRESHOLD_KEYS[regime]]:
                return regime
        return MarketRegime.BEAR_TRENDING

    def _confidence(
        self,
        composite: float,
        regime: MarketRegime,
        signals: Sequence[RegimeSignal],
        divergence_count: int = 0,
    ) -> int:
        """Confidence falls near band edges and when signals disagree.

        Both terms matter. A score of 0.251 with the BULL threshold at 0.25 is a
        coin flip dressed as a classification, and a score comfortably inside a
        band but with half the signals pointing the other way is a market in
        transition. Reporting high confidence for either would be a lie the
        consumer cannot detect.
        """
        thresholds = sorted(self.config.thresholds.values(), reverse=True)
        distances = [abs(composite - t) for t in thresholds]
        edge_distance = min(distances) if distances else 1.0
        # 0.15 away from any boundary is comfortably inside a band.
        edge_term = min(edge_distance / 0.15, 1.0)

        weight_total = sum(s.weight for s in signals)
        agreeing = sum(
            s.weight for s in signals if (s.score > 0) == (composite > 0) and s.score != 0
        )
        agreement_term = agreeing / weight_total if weight_total > 0 else 0.0

        coverage_term = min(len(signals) / 6.0, 1.0)

        blended = 0.45 * edge_term + 0.35 * agreement_term + 0.20 * coverage_term
        # Each unscored divergence shaves a little confidence: the classification
        # stands, but it is less clean than the headline suggests.
        penalty = 0.04 * divergence_count
        return round(max(0.0, min(1.0, blended - penalty)) * 100)


def _clip(value: float) -> float:
    """Constrain a signal score to [-1, +1]."""
    if not np.isfinite(value):
        return 0.0
    return float(max(-1.0, min(1.0, value)))
