"""Causality: the acceptance criterion the whole analytics layer rests on.

The property tested here is **prefix consistency**: computing a feature over
``x[:k]`` must reproduce exactly the first *k* outputs of computing it over all
of ``x``, for every *k*. If that holds, output *i* cannot depend on input *j > i*
— which is the formal statement of "no look-ahead".

This is worth more than any number of hand-written examples because it catches
the leaks that look respectable:

* a centred rolling window
* a z-score or min-max normalisation whose statistics come from the full sample
* an ``interpolate()`` that fills backwards
* an off-by-one that reads ``close[i + 1]``
* a percentile rank computed once over all history

Each of those produces plausible values and a backtest that cannot be repeated
live. Only a prefix test finds them reliably.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import numpy as np
import pytest

from tradeit.analytics import kernels as k

UTC = dt.UTC
RTOL = 1e-12


@pytest.fixture(scope="module")
def bars() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(20260101)
    n = 320
    close = 80.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.017, n)))
    spread = close * rng.uniform(0.004, 0.025, n)
    high = close + spread * rng.uniform(0.2, 1.0, n)
    low = close - spread * rng.uniform(0.2, 1.0, n)
    open_ = low + (high - low) * rng.uniform(0, 1, n)
    volume = rng.lognormal(np.log(2e6), 0.5, n)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _kernels() -> dict[str, Callable[[dict[str, np.ndarray]], np.ndarray]]:
    """Every kernel, wrapped so it takes the bar dict and returns one series."""
    return {
        "sma_50": lambda b: k.sma(b["close"], 50),
        "sma_200": lambda b: k.sma(b["close"], 200),
        "ema_21": lambda b: k.ema(b["close"], 21),
        "wilder_14": lambda b: k.wilder_smooth(b["close"], 14),
        "rsi_14": lambda b: k.rsi(b["close"], 14),
        "macd_line": lambda b: k.macd(b["close"])[0],
        "macd_signal": lambda b: k.macd(b["close"])[1],
        "macd_hist": lambda b: k.macd(b["close"])[2],
        "roc_20": lambda b: k.rate_of_change(b["close"], 20),
        "momentum_120": lambda b: k.momentum_score(b["close"], 120),
        "true_range": lambda b: k.true_range(b["high"], b["low"], b["close"]),
        "atr_14": lambda b: k.atr(b["high"], b["low"], b["close"], 14),
        "atr_pct_14": lambda b: k.atr_percent(b["high"], b["low"], b["close"], 14),
        "realized_vol_20": lambda b: k.realized_volatility(b["close"], 20),
        "bollinger_mid": lambda b: k.bollinger_bands(b["close"])[0],
        "bollinger_upper": lambda b: k.bollinger_bands(b["close"])[1],
        "bollinger_bandwidth": lambda b: k.bollinger_bands(b["close"])[3],
        "plus_di": lambda b: k.adx(b["high"], b["low"], b["close"])[0],
        "minus_di": lambda b: k.adx(b["high"], b["low"], b["close"])[1],
        "adx_14": lambda b: k.adx(b["high"], b["low"], b["close"])[2],
        "obv": lambda b: k.on_balance_volume(b["close"], b["volume"]),
        "relative_volume_20": lambda b: k.relative_volume(b["volume"], 20),
        "avg_dollar_volume_20": lambda b: k.average_dollar_volume(
            b["high"], b["low"], b["close"], b["volume"], 20
        ),
        "rolling_vwap_20": lambda b: k.rolling_vwap(
            b["high"], b["low"], b["close"], b["volume"], 20
        ),
        "rolling_max_252": lambda b: k.rolling_max(b["high"], 252),
        "rolling_min_252": lambda b: k.rolling_min(b["low"], 252),
        "distance_from_sma_50": lambda b: k.distance_from(b["close"], k.sma(b["close"], 50)),
        "slope_20": lambda b: k.slope(k.sma(b["close"], 50), 20),
        "range_contraction": lambda b: k.contraction_ratio(b["high"] - b["low"], 10, 50),
        "atr_contraction": lambda b: k.contraction_ratio(
            k.atr(b["high"], b["low"], b["close"], 14), 10, 50
        ),
        "volume_contraction": lambda b: k.contraction_ratio(b["volume"], 10, 50),
        "percent_rank_60": lambda b: k.percent_rank(b["close"], 60),
        "gap_frequency_20": lambda b: k.gap_frequency(b["open"], b["close"], 20, 0.02),
        # Added for the 24-indicator screening pass. A kernel absent from this
        # dict is not merely untested for causality, it is exempt from every
        # check below -- which is the same blindness the ORM drift guard had
        # when a table was missing from its list.
        "tema_20": lambda b: k.tema(b["close"], 20),
        "aroon_oscillator_25": lambda b: k.aroon_oscillator(b["high"], b["low"], 25),
        "cci_20": lambda b: k.commodity_channel_index(b["high"], b["low"], b["close"], 20),
        "ulcer_index_14": lambda b: k.ulcer_index(b["close"], 14),
        "money_flow_index_14": lambda b: k.money_flow_index(
            b["high"], b["low"], b["close"], b["volume"], 14
        ),
        "chaikin_money_flow_20": lambda b: k.chaikin_money_flow(
            b["high"], b["low"], b["close"], b["volume"], 20
        ),
        "td_setup_count": lambda b: k.td_setup_count(b["close"]),
    }


KERNEL_NAMES = sorted(_kernels())


@pytest.mark.parametrize("name", KERNEL_NAMES)
@pytest.mark.parametrize("cutoff", [60, 140, 219, 300])
def test_prefix_consistency(bars, name, cutoff):
    """The core guarantee: truncating the input truncates the output, nothing more.

    A failure here means the kernel at that index used data from beyond it.
    """
    kernel = _kernels()[name]
    full = kernel(bars)
    prefix = kernel({key: values[:cutoff] for key, values in bars.items()})

    assert prefix.shape[0] == cutoff
    np.testing.assert_allclose(
        prefix,
        full[:cutoff],
        rtol=RTOL,
        atol=0,
        equal_nan=True,
        err_msg=(
            f"{name} is not causal: computing it over the first {cutoff} bars gives "
            f"different values than computing it over all bars and truncating. "
            f"Some output before index {cutoff} depends on a later bar."
        ),
    )


@pytest.mark.parametrize("name", KERNEL_NAMES)
def test_appending_a_future_bar_never_changes_the_past(bars, name):
    """The same property stated the way it actually bites in production.

    Tomorrow's bar arrives; today's feature values must not move. If they do,
    a signal recorded yesterday cannot be reproduced today, and every stored
    decision becomes unverifiable.
    """
    kernel = _kernels()[name]
    original = kernel(bars)

    extended = {key: np.append(values, values[-1] * 1.35) for key, values in bars.items()}
    # Keep the appended bar internally consistent.
    extended["high"][-1] = max(extended["high"][-1], extended["close"][-1])
    extended["low"][-1] = min(extended["low"][-1], extended["close"][-1])
    after = kernel(extended)

    np.testing.assert_allclose(
        after[: original.shape[0]],
        original,
        rtol=RTOL,
        equal_nan=True,
        err_msg=f"{name} rewrote history when a new bar arrived",
    )


@pytest.mark.parametrize("name", KERNEL_NAMES)
def test_future_volume_cannot_enter_a_calculation(bars, name):
    """Volume-specific version of the same check.

    Volume is the easiest series to leak, because relative-volume style
    measures are naturally written as "today against the average", and the
    average is naturally written to include today.
    """
    kernel = _kernels()[name]
    original = kernel(bars)

    tampered = {key: values.copy() for key, values in bars.items()}
    tampered["volume"][-5:] *= 1000.0
    after = kernel(tampered)

    np.testing.assert_allclose(
        after[:-5],
        original[:-5],
        rtol=RTOL,
        equal_nan=True,
        err_msg=f"{name} changed historical values when future volume changed",
    )


@pytest.mark.parametrize("name", KERNEL_NAMES)
def test_future_prices_cannot_enter_a_calculation(bars, name):
    kernel = _kernels()[name]
    original = kernel(bars)

    tampered = {key: values.copy() for key, values in bars.items()}
    for field in ("open", "high", "low", "close"):
        tampered[field][-3:] *= 3.0
    after = kernel(tampered)

    np.testing.assert_allclose(
        after[:-3],
        original[:-3],
        rtol=RTOL,
        equal_nan=True,
        err_msg=f"{name} changed historical values when future prices changed",
    )


class TestWarmup:
    """Warm-up must be honest: NaN, not a number from too little data."""

    @pytest.mark.parametrize("name", KERNEL_NAMES)
    def test_a_short_series_never_produces_a_number(self, bars, name):
        """Ten bars cannot support a 50-day average, and must not pretend to."""
        kernel = _kernels()[name]
        short = kernel({key: values[:10] for key, values in bars.items()})
        assert short.shape[0] == 10

    @pytest.mark.parametrize(
        ("kernel", "period"),
        [(k.sma, 50), (k.ema, 21), (k.wilder_smooth, 14), (k.rolling_max, 20), (k.rolling_min, 20)],
    )
    def test_warmup_boundary_is_exactly_period_minus_one(self, bars, kernel, period):
        result = kernel(bars["close"], period)
        assert np.isnan(result[period - 2]), "a value appeared one bar too early"
        assert not np.isnan(result[period - 1]), "the first defined value is one bar too late"

    def test_a_series_shorter_than_the_window_is_entirely_nan(self):
        assert np.isnan(k.sma(np.arange(5.0), 20)).all()
        assert np.isnan(k.rsi(np.arange(5.0), 14)).all()
        assert np.isnan(k.realized_volatility(np.arange(5.0), 20)).all()

    def test_no_kernel_emits_a_value_before_its_first_valid_index(self, bars):
        """A blanket check that nothing quietly back-fills the warm-up region."""
        for name, kernel in _kernels().items():
            result = kernel(bars)
            defined = ~np.isnan(result)
            if not defined.any():
                continue
            first = int(np.argmax(defined))
            assert not defined[:first].any(), f"{name} has a value before its warm-up ends"


class TestNumericalStability:
    def test_kernels_do_not_raise_on_a_flat_series(self, bars):
        """Zero variance is a real market state (a halted or pegged instrument),
        and it must produce NaN or a defined value, never an exception."""
        flat = {key: np.full(200, 10.0) for key in bars}
        flat["volume"] = np.full(200, 1e6)
        for name, kernel in _kernels().items():
            result = kernel(flat)
            assert result.shape[0] == 200, f"{name} changed length on a flat series"

    def test_kernels_do_not_raise_on_zero_volume(self, bars):
        """Halted sessions print zero volume; division must be guarded."""
        zeroed = {key: values.copy() for key, values in bars.items()}
        zeroed["volume"][:] = 0.0
        for kernel in _kernels().values():
            kernel(zeroed)  # must not raise

    def test_kernels_do_not_emit_infinities(self, bars):
        for name, kernel in _kernels().items():
            result = kernel(bars)
            assert not np.isinf(result).any(), f"{name} emitted an infinity"
