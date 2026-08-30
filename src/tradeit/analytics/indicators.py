"""The configurable indicator engine.

Binds the pure kernels to a strategy configuration and a feature registry. The
engine's job is bookkeeping, not arithmetic: decide which indicators to compute
from config, run the kernels once over a bar series, and return a named,
registry-described result whose warm-up is honest.

**No period is defaulted here.** Every window comes from
``StrategyConfig.indicators``. A ``period: int = 50`` in a signature would be a
strategy constant hiding in a type annotation (ADR-0008), and the whole point of
hashing configurations is defeated if half the parameters live in code.

**One pass over the data.** A naive implementation recomputes SMA(50) three
times — once for the value, once for distance-from, once for slope. At 4,000
instruments that is the difference between a scan that finishes and one that
does not, so intermediate series are computed once and reused.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tradeit.analytics import kernels as k
from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.errors import DataError
from tradeit.strategy.config import IndicatorConfig

Floats = NDArray[np.float64]

#: Datasets every price indicator reads. Recorded on each spec so the pipeline
#: knows which ingestion runs gate it and which quality issues invalidate it.
PRICE_INPUTS = ("ohlcv_bars", "corporate_actions")


@dataclass(frozen=True, slots=True)
class IndicatorSeries:
    """Named feature series aligned one-to-one with the input bars.

    ``sessions`` and every array in ``values`` have the same length and the same
    ordering, which is what keeps a bar and its features aligned. A shorter
    array would be where off-by-one leakage starts, so
    :meth:`__post_init__` refuses one.
    """

    instrument_id: int
    timeframe: Bartimeframe
    sessions: tuple[dt.date, ...]
    values: dict[str, Floats]
    feature_set_digest: str

    def __post_init__(self) -> None:
        n = len(self.sessions)
        wrong = {name: arr.shape[0] for name, arr in self.values.items() if arr.shape[0] != n}
        if wrong:
            raise DataError(
                f"indicator series misaligned with {n} sessions: {wrong}; "
                "a feature array shorter than its bar series silently shifts every value"
            )

    def latest(self, name: str) -> float | None:
        """Most recent value, or ``None`` if undefined (warm-up or missing)."""
        series = self.values.get(name)
        if series is None or series.shape[0] == 0:
            return None
        value = series[-1]
        return None if np.isnan(value) else float(value)

    def at(self, session: dt.date, name: str) -> float | None:
        try:
            index = self.sessions.index(session)
        except ValueError:
            return None
        value = self.values[name][index]
        return None if np.isnan(value) else float(value)

    def as_of_last(self) -> dict[str, float | None]:
        """Every feature's latest value -- the row a screen consumes."""
        return {name: self.latest(name) for name in sorted(self.values)}

    @property
    def names(self) -> list[str]:
        return sorted(self.values)


class IndicatorEngine:
    """Computes the configured indicator set over a bar series."""

    def __init__(self, config: IndicatorConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    # -- registry ------------------------------------------------------------

    @staticmethod
    def _build_registry(config: IndicatorConfig) -> FeatureRegistry:
        """Declare every feature this engine can produce.

        Warm-up numbers here are the contract the causality tests check. They
        are stated per feature rather than derived, because a derived warm-up
        that is wrong is invisible.
        """
        registry = FeatureRegistry()

        def spec(
            name: str,
            description: str,
            warmup: int,
            output: OutputType,
            parameters: dict[str, object],
            *,
            null: NullBehaviour = NullBehaviour.WARMUP,
            lookback: int | None = None,
        ) -> None:
            registry.register(
                FeatureSpec(
                    name=name,
                    version=1,
                    description=description,
                    kind=FeatureKind.TIME_SERIES,
                    timeframe=Bartimeframe.D1,
                    output_type=output,
                    null_behaviour=null,
                    input_datasets=PRICE_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                    lookback_sessions=lookback,
                )
            )

        for period in config.sma_periods:
            spec(
                f"sma_{period}",
                f"{period}-session simple moving average of close.",
                period,
                OutputType.PRICE,
                {"period": period},
            )
            spec(
                f"distance_from_sma_{period}",
                f"Fractional distance of close from its {period}-session average. "
                "Negative means below.",
                period,
                OutputType.RATIO,
                {"period": period},
            )
        for period in config.ema_periods:
            spec(
                f"ema_{period}",
                f"{period}-session exponential moving average, SMA-seeded.",
                period,
                OutputType.PRICE,
                {"period": period, "seed": "sma"},
            )

        spec(
            f"rsi_{config.rsi_period}",
            "Wilder's relative strength index -- a momentum oscillator on one "
            "series. Unrelated to the relative-strength engine.",
            config.rsi_period + 1,
            OutputType.SCORE_0_100,
            {"period": config.rsi_period, "smoothing": "wilder"},
        )
        macd_warmup = config.macd_slow + config.macd_signal - 1
        for suffix, description in (
            ("line", "MACD line: fast EMA minus slow EMA."),
            ("signal", "Signal line: EMA of the MACD line."),
            ("histogram", "MACD line minus signal line."),
        ):
            spec(
                f"macd_{suffix}",
                description,
                macd_warmup,
                OutputType.PRICE,
                {
                    "fast": config.macd_fast,
                    "slow": config.macd_slow,
                    "signal": config.macd_signal,
                },
            )
        spec(
            f"adx_{config.adx_period}",
            "Wilder's ADX -- trend strength regardless of direction.",
            2 * config.adx_period + 1,
            OutputType.SCORE_0_100,
            {"period": config.adx_period},
        )
        spec(
            "plus_di",
            "Wilder's +DI -- upward directional movement.",
            config.adx_period + 1,
            OutputType.SCORE_0_100,
            {"period": config.adx_period},
        )
        spec(
            "minus_di",
            "Wilder's -DI -- downward directional movement.",
            config.adx_period + 1,
            OutputType.SCORE_0_100,
            {"period": config.adx_period},
        )
        spec(
            f"atr_{config.atr_period}",
            "Average true range in price units.",
            config.atr_period + 1,
            OutputType.PRICE,
            {"period": config.atr_period, "smoothing": "wilder"},
        )
        spec(
            "atr_percent",
            "ATR as a fraction of price -- comparable across instruments, which raw ATR is not.",
            config.atr_period + 1,
            OutputType.RATIO,
            {"period": config.atr_period},
        )
        for suffix, description, output in (
            ("middle", "Bollinger middle band (SMA).", OutputType.PRICE),
            ("upper", "Bollinger upper band.", OutputType.PRICE),
            ("lower", "Bollinger lower band.", OutputType.PRICE),
            (
                "bandwidth",
                "Band width relative to the middle -- the scale-free measure of range contraction.",
                OutputType.RATIO,
            ),
        ):
            spec(
                f"bollinger_{suffix}",
                description,
                config.bollinger_period,
                output,
                {"period": config.bollinger_period, "stdev": config.bollinger_stdev},
            )
        spec(
            f"vwap_{config.vwap_period}",
            "Rolling volume-weighted typical price. NOT intraday session VWAP -- "
            "that requires intraday data and is computed separately.",
            config.vwap_period,
            OutputType.PRICE,
            {"period": config.vwap_period, "anchor": "rolling"},
        )
        spec(
            "obv",
            "On-balance volume. The LEVEL is arbitrary (it depends on where the "
            "series starts); only its slope and divergence carry information.",
            1,
            OutputType.COUNT,
            {},
        )
        spec(
            "obv_slope",
            "Slope of on-balance volume -- the usable form of OBV.",
            1 + config.ma_slope_lookback,
            OutputType.RATIO,
            {"lookback": config.ma_slope_lookback},
        )
        spec(
            "relative_volume",
            "Volume against the average of the PRIOR N sessions, excluding today.",
            config.relative_volume_period + 1,
            OutputType.RATIO,
            {"period": config.relative_volume_period, "self_inclusive": False},
        )
        spec(
            f"avg_dollar_volume_{config.dollar_volume_period}",
            "Rolling mean turnover using typical price.",
            config.dollar_volume_period,
            OutputType.CURRENCY,
            {"period": config.dollar_volume_period},
        )
        for period in config.volatility_periods:
            spec(
                f"realized_volatility_{period}",
                f"Annualised stdev of log returns over {period} sessions.",
                period + 1,
                OutputType.RATIO,
                {"period": period, "annualisation": config.annualisation_factor},
            )
        for period in config.momentum_periods:
            spec(
                f"momentum_{period}",
                f"Price change over {period} sessions.",
                period,
                OutputType.RATIO,
                {"period": period},
                lookback=period,
            )
        for period in config.roc_periods:
            spec(
                f"roc_{period}",
                f"Rate of change over {period} sessions.",
                period,
                OutputType.RATIO,
                {"period": period},
                lookback=period,
            )
        spec(
            "volume_momentum",
            "Change in average volume -- accumulation building or fading.",
            config.relative_volume_period * 2,
            OutputType.RATIO,
            {"period": config.relative_volume_period},
        )
        spec(
            f"sma_{config.ma_slope_period}_slope",
            "Per-session fractional slope of the moving average, normalised so "
            "it is comparable across price levels.",
            config.ma_slope_period + config.ma_slope_lookback,
            OutputType.RATIO,
            {
                "ma_period": config.ma_slope_period,
                "lookback": config.ma_slope_lookback,
            },
        )
        for period in config.rolling_extreme_periods:
            spec(
                f"rolling_high_{period}",
                f"Highest high over {period} sessions.",
                period,
                OutputType.PRICE,
                {"period": period},
            )
            spec(
                f"rolling_low_{period}",
                f"Lowest low over {period} sessions.",
                period,
                OutputType.PRICE,
                {"period": period},
            )
            spec(
                f"distance_from_high_{period}",
                f"Fractional distance below the {period}-session high. Zero means at a new high.",
                period,
                OutputType.RATIO,
                {"period": period},
            )
            spec(
                f"distance_from_low_{period}",
                f"Fractional distance above the {period}-session low.",
                period,
                OutputType.RATIO,
                {"period": period},
            )
        short, long = config.contraction_short_window, config.contraction_long_window
        spec(
            "range_contraction",
            "Recent bar range against a longer baseline. Below 1 means quieter -- "
            "the signature of a consolidating base.",
            long,
            OutputType.RATIO,
            {"short": short, "long": long},
        )
        spec(
            "atr_contraction",
            "Recent ATR against a longer baseline.",
            long + config.atr_period,
            OutputType.RATIO,
            {"short": short, "long": long, "atr_period": config.atr_period},
        )
        spec(
            "volume_contraction",
            "Recent volume against a longer baseline. Dry-up during a base.",
            long,
            OutputType.RATIO,
            {"short": short, "long": long},
        )
        spec(
            "volatility_percentile",
            "Where current realised volatility sits in this instrument's own "
            "recent history. A time-series percentile, not cross-sectional.",
            config.volatility_periods[0] + config.annualisation_factor,
            OutputType.PERCENTILE_0_1,
            {
                "vol_period": config.volatility_periods[0],
                "window": config.annualisation_factor,
            },
        )
        registry.validate()
        return registry

    # -- computation ---------------------------------------------------------

    def compute(
        self,
        bars: Sequence[OhlcvBar],
        *,
        instrument_id: int | None = None,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> IndicatorSeries:
        """Compute every configured indicator over a chronological bar series.

        The caller is responsible for the series being clock-gated; this
        function has no access to a clock and computes only what it is given,
        which is exactly why the causality property holds.
        """
        if not bars:
            raise DataError("cannot compute indicators over an empty bar series")
        sessions = [b.session_date for b in bars]
        if sessions != sorted(sessions):
            raise DataError("bars must be in chronological order")

        c = self.config
        high = np.array([float(b.high) for b in bars], dtype=np.float64)
        low = np.array([float(b.low) for b in bars], dtype=np.float64)
        close = np.array([float(b.close) for b in bars], dtype=np.float64)
        volume = np.array([float(b.volume) for b in bars], dtype=np.float64)

        values: dict[str, Floats] = {}

        # Moving averages, computed once and reused by distance and slope.
        moving_averages: dict[int, Floats] = {}
        for period in c.sma_periods:
            series = k.sma(close, period)
            moving_averages[period] = series
            values[f"sma_{period}"] = series
            values[f"distance_from_sma_{period}"] = k.distance_from(close, series)
        for period in c.ema_periods:
            values[f"ema_{period}"] = k.ema(close, period)

        # NOTE: an `or` fallback here is a bug -- a NumPy array has no truth
        # value. The membership check is deliberate, not stylistic.
        if c.ma_slope_period in moving_averages:
            slope_ma = moving_averages[c.ma_slope_period]
        else:
            slope_ma = k.sma(close, c.ma_slope_period)
        values[f"sma_{c.ma_slope_period}_slope"] = k.slope(slope_ma, c.ma_slope_lookback)

        # Oscillators.
        values[f"rsi_{c.rsi_period}"] = k.rsi(close, c.rsi_period)
        line, signal, histogram = k.macd(close, c.macd_fast, c.macd_slow, c.macd_signal)
        values["macd_line"] = line
        values["macd_signal"] = signal
        values["macd_histogram"] = histogram

        plus_di, minus_di, adx = k.adx(high, low, close, c.adx_period)
        values["plus_di"] = plus_di
        values["minus_di"] = minus_di
        values[f"adx_{c.adx_period}"] = adx

        # Volatility and range.
        atr = k.atr(high, low, close, c.atr_period)
        values[f"atr_{c.atr_period}"] = atr
        values["atr_percent"] = k.atr_percent(high, low, close, c.atr_period)

        middle, upper, lower, bandwidth = k.bollinger_bands(
            close, c.bollinger_period, c.bollinger_stdev
        )
        values["bollinger_middle"] = middle
        values["bollinger_upper"] = upper
        values["bollinger_lower"] = lower
        values["bollinger_bandwidth"] = bandwidth

        for period in c.volatility_periods:
            values[f"realized_volatility_{period}"] = k.realized_volatility(
                close, period, c.annualisation_factor
            )
        values["volatility_percentile"] = k.percent_rank(
            values[f"realized_volatility_{c.volatility_periods[0]}"], c.annualisation_factor
        )

        # Volume.
        values[f"vwap_{c.vwap_period}"] = k.rolling_vwap(high, low, close, volume, c.vwap_period)
        obv = k.on_balance_volume(close, volume)
        values["obv"] = obv
        values["obv_slope"] = k.slope(np.abs(obv) + 1.0, c.ma_slope_lookback)
        values["relative_volume"] = k.relative_volume(volume, c.relative_volume_period)
        values[f"avg_dollar_volume_{c.dollar_volume_period}"] = k.average_dollar_volume(
            high, low, close, volume, c.dollar_volume_period
        )
        values["volume_momentum"] = k.rate_of_change(
            k.sma(volume, c.relative_volume_period), c.relative_volume_period
        )

        # Momentum.
        for period in c.momentum_periods:
            values[f"momentum_{period}"] = k.momentum_score(close, period)
        for period in c.roc_periods:
            values[f"roc_{period}"] = k.rate_of_change(close, period)

        # Structure.
        for period in c.rolling_extreme_periods:
            highs = k.rolling_max(high, period)
            lows = k.rolling_min(low, period)
            values[f"rolling_high_{period}"] = highs
            values[f"rolling_low_{period}"] = lows
            values[f"distance_from_high_{period}"] = k.distance_from(close, highs)
            values[f"distance_from_low_{period}"] = k.distance_from(close, lows)

        values["range_contraction"] = k.contraction_ratio(
            high - low, c.contraction_short_window, c.contraction_long_window
        )
        values["atr_contraction"] = k.contraction_ratio(
            atr, c.contraction_short_window, c.contraction_long_window
        )
        values["volume_contraction"] = k.contraction_ratio(
            volume, c.contraction_short_window, c.contraction_long_window
        )

        # Guard: silently emitting an undeclared feature would let a value reach
        # storage with no registry entry describing how to interpret it.
        undeclared = set(values) - set(self.registry.names())
        if undeclared:
            raise DataError(f"computed features not present in the registry: {sorted(undeclared)}")

        return IndicatorSeries(
            instrument_id=instrument_id if instrument_id is not None else bars[0].instrument_id,
            timeframe=timeframe,
            sessions=tuple(sessions),
            values=values,
            feature_set_digest=self.registry.digest,
        )

    @property
    def warmup_periods(self) -> int:
        """Sessions before every configured indicator is defined."""
        return self.registry.max_warmup
