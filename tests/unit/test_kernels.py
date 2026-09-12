"""Numerical correctness and warm-up behaviour for every indicator kernel.

Causality is tested separately and exhaustively in ``test_causality.py``. This
module checks that the numbers are right — against hand-computed values where
a closed form exists, and against defining properties where one does not.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradeit.analytics import kernels as k


@pytest.fixture
def ramp() -> np.ndarray:
    """1.0, 2.0, … 20.0 — trivially predictable."""
    return np.arange(1.0, 21.0)


@pytest.fixture
def series() -> dict[str, np.ndarray]:
    """A seeded OHLCV series with realistic structure."""
    rng = np.random.default_rng(12345)
    n = 400
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    spread = close * rng.uniform(0.005, 0.02, n)
    high = close + spread * rng.uniform(0.2, 1.0, n)
    low = close - spread * rng.uniform(0.2, 1.0, n)
    open_ = low + (high - low) * rng.uniform(0, 1, n)
    volume = rng.lognormal(np.log(1e6), 0.4, n)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class TestMovingAverages:
    def test_sma_matches_the_definition(self, ramp):
        result = k.sma(ramp, 5)
        assert np.isnan(result[:4]).all()
        assert result[4] == pytest.approx(3.0)  # mean(1..5)
        assert result[19] == pytest.approx(18.0)  # mean(16..20)

    def test_sma_warmup_is_exactly_period_minus_one(self, ramp):
        for period in (2, 5, 10, 20):
            result = k.sma(ramp, period)
            assert np.isnan(result[: period - 1]).all()
            assert not np.isnan(result[period - 1])

    def test_sma_of_a_constant_is_the_constant(self):
        result = k.sma(np.full(30, 7.0), 10)
        assert result[9:] == pytest.approx(7.0)

    def test_sma_shorter_than_period_is_all_nan(self):
        assert np.isnan(k.sma(np.arange(3.0), 10)).all()

    def test_ema_is_seeded_from_the_sma(self, ramp):
        """The seeding choice is the one place implementations diverge."""
        result = k.ema(ramp, 5)
        assert np.isnan(result[:4]).all()
        assert result[4] == pytest.approx(3.0)  # SMA seed
        alpha = 2 / 6
        assert result[5] == pytest.approx(alpha * 6.0 + (1 - alpha) * 3.0)

    def test_ema_converges_toward_a_constant(self):
        values = np.concatenate([np.full(50, 10.0), np.full(200, 20.0)])
        result = k.ema(values, 10)
        assert result[-1] == pytest.approx(20.0, abs=1e-6)

    def test_wilder_smoothing_differs_from_ema_of_the_same_period(self, ramp):
        """Wilder uses 1/n where EMA uses 2/(n+1). Substituting one for the
        other shifts every RSI, ATR and ADX value."""
        assert k.wilder_smooth(ramp, 5)[10] != pytest.approx(k.ema(ramp, 5)[10])

    def test_zero_period_is_refused(self, ramp):
        with pytest.raises(ValueError):
            k.sma(ramp, 0)


class TestOscillators:
    def test_rsi_is_100_when_every_bar_gains(self, ramp):
        assert k.rsi(ramp, 14)[-1] == pytest.approx(100.0)

    def test_rsi_is_0_when_every_bar_loses(self, ramp):
        assert k.rsi(ramp[::-1], 14)[-1] == pytest.approx(0.0)

    def test_rsi_stays_within_bounds(self, series):
        result = k.rsi(series["close"], 14)
        defined = result[~np.isnan(result)]
        assert defined.size > 300
        assert defined.min() >= 0.0 and defined.max() <= 100.0

    def test_rsi_warmup_consumes_one_bar_for_differencing(self, series):
        result = k.rsi(series["close"], 14)
        assert np.isnan(result[:14]).all()
        assert not np.isnan(result[14])

    def test_rsi_of_a_flat_series_is_neutral(self):
        result = k.rsi(np.full(40, 50.0), 14)
        assert result[-1] == pytest.approx(50.0)

    def test_macd_histogram_is_line_minus_signal(self, series):
        line, signal, histogram = k.macd(series["close"], 12, 26, 9)
        defined = ~np.isnan(histogram)
        assert np.allclose(histogram[defined], (line - signal)[defined])

    def test_macd_signal_warmup_stacks_on_the_line(self, series):
        """A signal line emitted before the MACD line has 9 defined values is
        computed from NaNs or from too little data; either way the crossings it
        produces are fictional."""
        line, signal, _ = k.macd(series["close"], 12, 26, 9)
        first_line = int(np.argmax(~np.isnan(line)))
        first_signal = int(np.argmax(~np.isnan(signal)))
        assert first_signal == first_line + 8

    def test_rate_of_change_matches_the_definition(self, ramp):
        result = k.rate_of_change(ramp, 5)
        assert np.isnan(result[:5]).all()
        assert result[5] == pytest.approx(6.0 / 1.0 - 1.0)


class TestVolatility:
    def test_true_range_first_bar_is_nan(self, series):
        """There is no previous close, so true range is undefined — not
        high-minus-low, which biases the first ATR low."""
        assert np.isnan(k.true_range(series["high"], series["low"], series["close"])[0])

    def test_true_range_captures_a_gap(self):
        high = np.array([10.0, 20.0])
        low = np.array([9.0, 19.0])
        close = np.array([9.5, 19.5])
        assert k.true_range(high, low, close)[1] == pytest.approx(20.0 - 9.5)

    def test_atr_is_positive_and_warms_after_period_plus_one(self, series):
        result = k.atr(series["high"], series["low"], series["close"], 14)
        assert np.isnan(result[:14]).all()
        assert not np.isnan(result[14])
        assert (result[~np.isnan(result)] > 0).all()

    def test_atr_percent_is_scale_free(self, series):
        """The property that makes volatility comparable across instruments."""
        base = k.atr_percent(series["high"], series["low"], series["close"], 14)
        scaled = k.atr_percent(series["high"] * 10, series["low"] * 10, series["close"] * 10, 14)
        defined = ~np.isnan(base)
        assert np.allclose(base[defined], scaled[defined])

    def test_realized_volatility_of_a_constant_series_is_zero(self):
        result = k.realized_volatility(np.full(60, 100.0), 20)
        assert result[-1] == pytest.approx(0.0)

    def test_realized_volatility_scales_with_annualisation(self):
        rng = np.random.default_rng(7)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
        daily = k.realized_volatility(close, 20, annualisation=1)
        annual = k.realized_volatility(close, 20, annualisation=252)
        assert annual[-1] == pytest.approx(daily[-1] * np.sqrt(252))

    def test_bollinger_bands_bracket_the_middle(self, series):
        middle, upper, lower, bandwidth = k.bollinger_bands(series["close"], 20, 2.0)
        defined = ~np.isnan(middle)
        assert (upper[defined] >= middle[defined]).all()
        assert (lower[defined] <= middle[defined]).all()
        assert (bandwidth[defined] >= 0).all()

    def test_bollinger_bandwidth_collapses_on_a_flat_series(self):
        _, _, _, bandwidth = k.bollinger_bands(np.full(40, 100.0), 20, 2.0)
        assert bandwidth[-1] == pytest.approx(0.0)

    def test_adx_warmup_is_roughly_double_the_period(self, series):
        """ADX is a double smoothing; treating its warm-up as one period
        exposes values that are still converging."""
        _, _, adx = k.adx(series["high"], series["low"], series["close"], 14)
        first = int(np.argmax(~np.isnan(adx)))
        assert 27 <= first <= 30

    def test_adx_stays_within_bounds(self, series):
        plus, minus, adx = k.adx(series["high"], series["low"], series["close"], 14)
        for arr in (plus, minus, adx):
            defined = arr[~np.isnan(arr)]
            assert defined.min() >= 0.0 and defined.max() <= 100.0

    def test_adx_is_high_in_a_clean_trend(self):
        n = 120
        close = np.arange(100.0, 100.0 + n)
        high, low = close + 0.5, close - 0.5
        _, _, adx = k.adx(high, low, close, 14)
        assert adx[-1] > 60, "a monotone trend should register as strongly directional"


class TestVolume:
    def test_relative_volume_excludes_the_current_bar_from_its_baseline(self):
        """Self-inclusion damps exactly the spike being measured."""
        volume = np.concatenate([np.full(20, 1_000_000.0), [5_000_000.0]])
        assert k.relative_volume(volume, 20)[-1] == pytest.approx(5.0)

    def test_relative_volume_of_a_flat_series_is_one(self):
        assert k.relative_volume(np.full(50, 1e6), 20)[-1] == pytest.approx(1.0)

    def test_obv_accumulates_in_the_direction_of_price(self):
        close = np.array([10.0, 11.0, 12.0, 11.0])
        volume = np.array([100.0, 200.0, 300.0, 400.0])
        result = k.on_balance_volume(close, volume)
        assert result[0] == 0.0
        assert result[1] == 200.0
        assert result[2] == 500.0
        assert result[3] == 100.0

    def test_average_dollar_volume_uses_typical_price(self):
        high = np.full(30, 12.0)
        low = np.full(30, 8.0)
        close = np.full(30, 11.0)
        volume = np.full(30, 1000.0)
        expected = ((12 + 8 + 11) / 3) * 1000
        assert k.average_dollar_volume(high, low, close, volume, 20)[-1] == pytest.approx(expected)

    def test_rolling_vwap_weights_toward_high_volume_bars(self):
        high = np.array([10.0] * 5 + [20.0] * 5)
        low = high.copy()
        close = high.copy()
        volume = np.array([1.0] * 5 + [9.0] * 5)
        result = k.rolling_vwap(high, low, close, volume, 10)
        assert result[-1] == pytest.approx((5 * 10 * 1 + 5 * 20 * 9) / (5 * 1 + 5 * 9))

    def test_session_vwap_resets_at_each_session(self):
        prices = np.array([10.0, 20.0, 100.0, 200.0])
        volume = np.ones(4)
        sessions = np.array([0, 0, 1, 1], dtype=np.int64)
        result = k.session_vwap(prices, prices, prices, volume, sessions)
        assert result[1] == pytest.approx(15.0)
        assert result[2] == pytest.approx(100.0), "a new session must not inherit the prior one"
        assert result[3] == pytest.approx(150.0)


class TestStructure:
    def test_rolling_max_and_min(self, ramp):
        assert k.rolling_max(ramp, 5)[9] == pytest.approx(10.0)
        assert k.rolling_min(ramp, 5)[9] == pytest.approx(6.0)

    def test_distance_from_is_signed_and_fractional(self):
        values = np.array([110.0, 90.0])
        reference = np.array([100.0, 100.0])
        result = k.distance_from(values, reference)
        assert result[0] == pytest.approx(0.10)
        assert result[1] == pytest.approx(-0.10)

    def test_slope_is_normalised_by_level_so_it_is_comparable(self):
        """A $20 stock and a $2,000 stock rising at the same rate must produce
        the same slope."""
        cheap = np.arange(20.0, 40.0)
        rich = cheap * 100
        assert k.slope(cheap, 10)[-1] == pytest.approx(k.slope(rich, 10)[-1])

    def test_slope_is_zero_for_a_flat_series(self):
        assert k.slope(np.full(30, 5.0), 10)[-1] == pytest.approx(0.0)

    def test_contraction_ratio_is_below_one_when_recent_activity_is_quieter(self):
        values = np.concatenate([np.full(40, 10.0), np.full(10, 2.0)])
        assert k.contraction_ratio(values, 10, 50)[-1] < 1.0

    def test_contraction_ratio_is_one_for_a_constant_series(self):
        assert k.contraction_ratio(np.full(60, 3.0), 10, 50)[-1] == pytest.approx(1.0)

    def test_contraction_requires_the_long_window_to_be_longer(self):
        with pytest.raises(ValueError):
            k.contraction_ratio(np.arange(60.0), 50, 10)

    def test_percent_rank_is_one_at_a_new_high(self, ramp):
        assert k.percent_rank(ramp, 10)[-1] == pytest.approx(1.0)

    def test_percent_rank_is_zero_at_a_new_low(self, ramp):
        assert k.percent_rank(ramp[::-1], 10)[-1] == pytest.approx(0.0)

    def test_gap_frequency_counts_only_gaps_beyond_the_threshold(self):
        close = np.full(30, 100.0)
        open_ = np.full(30, 100.0)
        open_[25] = 105.0  # a 5% gap
        result = k.gap_frequency(open_, close, 20, 0.02)
        assert result[-1] == pytest.approx(1.0 / 20.0)


class TestNoLibraryDependence:
    def test_every_kernel_returns_the_input_length(self, series):
        """Length-preserving output is what keeps a bar series and its feature
        series aligned; a shorter return is where off-by-one leakage starts."""
        n = series["close"].shape[0]
        outputs = [
            k.sma(series["close"], 50),
            k.ema(series["close"], 21),
            k.rsi(series["close"], 14),
            k.atr(series["high"], series["low"], series["close"], 14),
            k.realized_volatility(series["close"], 20),
            k.relative_volume(series["volume"], 20),
            k.on_balance_volume(series["close"], series["volume"]),
            k.rolling_max(series["high"], 252),
            k.percent_rank(series["close"], 60),
            *k.macd(series["close"]),
            *k.bollinger_bands(series["close"]),
            *k.adx(series["high"], series["low"], series["close"]),
        ]
        assert all(out.shape[0] == n for out in outputs)


class TestObvTrend:
    """The signed, comparable form of OBV.

    Written against a real defect: the registry used to publish
    ``slope(abs(obv) + 1, lookback)``, and a security closing down every session
    for forty sessions returned the identical ``+0.052632`` as one closing up
    every session. These tests fail on that implementation.
    """

    def _run(self, closes: list[float], volumes: list[float], lookback: int = 20):
        return k.obv_trend(np.array(closes), np.array(volumes), lookback)

    def test_accumulation_and_distribution_have_opposite_signs(self) -> None:
        up = [10.0 + 0.1 * i for i in range(40)]
        down = [10.0 - 0.1 * i for i in range(40)]
        volume = [1_000_000.0] * 40
        assert self._run(up, volume)[-1] > 0
        assert self._run(down, volume)[-1] < 0
        assert self._run(up, volume)[-1] == pytest.approx(-self._run(down, volume)[-1])

    def test_every_session_up_on_full_volume_is_one(self) -> None:
        up = [10.0 + 0.1 * i for i in range(40)]
        assert self._run(up, [1_000_000.0] * 40)[-1] == pytest.approx(1.0)

    def test_every_session_down_on_full_volume_is_minus_one(self) -> None:
        down = [10.0 - 0.1 * i for i in range(40)]
        assert self._run(down, [1_000_000.0] * 40)[-1] == pytest.approx(-1.0)

    def test_alternating_sessions_cancel(self) -> None:
        closes = [10.0 + (0.1 if i % 2 else -0.1) for i in range(41)]
        value = self._run(closes, [1_000_000.0] * 41, lookback=20)[-1]
        assert abs(value) < 0.1

    def test_it_is_unchanged_by_a_split_adjustment(self) -> None:
        """A split divides price and multiplies volume; the ratio does not care."""
        closes = [10.0 + 0.1 * i for i in range(40)]
        volume = [1_000_000.0 + 1_000.0 * i for i in range(40)]
        plain = self._run(closes, volume)[-1]
        adjusted = self._run([c / 2 for c in closes], [v * 2 for v in volume])[-1]
        assert adjusted == pytest.approx(plain)

    def test_it_is_bounded(self) -> None:
        rng = np.random.default_rng(7)
        closes = list(100 + np.cumsum(rng.normal(0, 1, 200)))
        volume = list(rng.uniform(1e5, 5e6, 200))
        values = self._run(closes, volume, lookback=20)
        finite = values[np.isfinite(values)]
        assert finite.size > 0
        assert np.all(finite <= 1.0) and np.all(finite >= -1.0)

    def test_the_warm_up_is_not_a_number(self) -> None:
        closes = [10.0 + 0.1 * i for i in range(40)]
        values = self._run(closes, [1_000_000.0] * 40, lookback=20)
        assert np.all(np.isnan(values[:20]))
        assert np.isfinite(values[20:]).all()

    def test_a_window_with_no_volume_is_not_a_number(self) -> None:
        closes = [10.0 + 0.1 * i for i in range(40)]
        values = self._run(closes, [0.0] * 40, lookback=20)
        assert np.all(np.isnan(values[20:]))
