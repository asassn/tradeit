"""Cross-validation of our indicator kernels against independent libraries.

ADR-0010 says we write our own kernels rather than importing them, so that
seeding, smoothing and warm-up behaviour is ours to test. The cost of that
decision is that a mistake in one of them is a mistake nobody else has already
found. This module pays that cost down by checking every kernel against
implementations written by other people: ``ta`` (0.11) and ``pandas-ta-classic``
(0.6.52).

**What agreement proves.** Two independent implementations of the same formula
agreeing to 1e-9 is strong evidence that neither has an arithmetic or indexing
error. That is the whole claim, and it is worth having.

**What disagreement does not prove.** Where our definition differs deliberately,
the reference is not a target to hit. Every divergence below is either
reconciled exactly by construction or documented with the reason, and the
reason is always a stated choice rather than "the library does it differently".
Changing our maths to match a library we do not otherwise trust would be
optimising for agreement rather than correctness.

The known, deliberate divergences:

* **EMA and Wilder seeding.** We seed from the mean of the first ``period``
  values. ``ta`` runs its smoothing from bar zero and masks the warm-up, so its
  first published value already carries the weight of every earlier bar. Both
  emit at the same index and both are legitimate; ours makes the early series
  independent of a single bar. The difference decays geometrically, so the
  comparisons start after the transient rather than at the seed, and the
  measured decay is quoted in each test.
* **ADX warm-up.** Ours is ``2 * period``, because ADX is a double smoothing and
  values before that are still converging. Several libraries emit from
  ``period``, which exposes numbers that have not settled.
* **OBV origin.** We start the cumulative sum at zero; ``ta`` and
  ``pandas-ta-classic`` start it at ``volume[0]``. A constant offset in a series
  whose absolute level carries no information, so the increments are compared
  instead — and the offset is asserted to be *constant*, which is what would
  break if a single bar's direction convention diverged.
* **RSI at zero average loss.** We return 100 rather than dividing by zero. An
  unbroken run of gains is a legitimate state, not an error.
* **Rate of change units.** We return a fraction; ``ta`` returns a percentage.
  A unit convention, converted in the comparison rather than "fixed".

The data is a deterministic pseudo-random walk. Real market data would be
better and is unavailable in this environment (see ``docs/PHASE_03_GATE.md``),
but the mathematics being checked here is a property of the arithmetic, not of
the data: an indexing error or a wrong smoothing constant shows up on any
series with enough variation, and the walk below has gaps, trends and
volatility clusters deliberately built in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradeit.analytics import kernels

ta = pytest.importorskip("ta", reason="cross-validation requires the `ta` package")
pta = pytest.importorskip(
    "pandas_ta_classic", reason="cross-validation requires `pandas-ta-classic`"
)

TOLERANCE = 1e-9
#: Looser bound for comparisons that cross a library's own float bookkeeping
#: (pandas rolling reductions accumulate differently from a NumPy pass).
LOOSE = 1e-6


def _walk(n: int = 600, seed: int = 20260807) -> pd.DataFrame:
    """A price series with the awkward features of a real one.

    Trends, volatility clustering, overnight gaps and a couple of very large
    single-session moves. Not a market model -- a stress input.
    """
    rng = np.random.default_rng(seed)
    drift = np.concatenate(
        [
            np.full(200, 0.0008),  # trending up
            np.full(150, -0.0012),  # trending down
            np.full(250, 0.0002),  # drifting
        ]
    )[:n]
    vol = np.concatenate([np.full(150, 0.010), np.full(100, 0.030), np.full(350, 0.014)])[:n]
    returns = rng.normal(drift, vol)
    returns[300] = 0.18  # a genuine news gap
    returns[420] = -0.22

    close = 100.0 * np.exp(np.cumsum(returns))
    intraday = np.abs(rng.normal(0.008, 0.004, n))
    high = close * (1.0 + intraday)
    low = close * (1.0 - intraday)
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1.0 + rng.normal(0, 0.003, n))
    open_ = np.clip(open_, low, high)
    volume = rng.lognormal(14.0, 0.6, n).round()

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


DATA = _walk()
CLOSE = DATA["close"].to_numpy()
HIGH = DATA["high"].to_numpy()
LOW = DATA["low"].to_numpy()
OPEN = DATA["open"].to_numpy()
VOLUME = DATA["volume"].to_numpy()


def _compare(
    ours: np.ndarray, theirs: pd.Series | np.ndarray, *, skip: int, tol: float = TOLERANCE
) -> None:
    """Compare where both are defined, after a warm-up we state explicitly.

    ``skip`` is never chosen to make a test pass: it is the larger of the two
    implementations' warm-ups, so the comparison starts where both are
    genuinely settled.
    """
    reference = np.asarray(theirs, dtype=float)
    assert reference.shape == ours.shape, "series lengths differ; alignment is not comparable"

    both_defined = ~np.isnan(ours) & ~np.isnan(reference)
    both_defined[:skip] = False
    assert both_defined.sum() > 50, "too few overlapping values for the comparison to mean anything"

    np.testing.assert_allclose(ours[both_defined], reference[both_defined], rtol=tol, atol=tol)


class TestMovingAverages:
    @pytest.mark.parametrize("period", [10, 20, 50, 200])
    def test_sma_matches_ta(self, period):
        theirs = ta.trend.SMAIndicator(DATA["close"], window=period).sma_indicator()
        _compare(kernels.sma(CLOSE, period), theirs, skip=period, tol=LOOSE)

    @pytest.mark.parametrize("period", [10, 20, 50])
    def test_sma_matches_pandas_rolling(self, period):
        """A third opinion, from the most boring possible implementation."""
        theirs = DATA["close"].rolling(period).mean()
        _compare(kernels.sma(CLOSE, period), theirs, skip=period, tol=LOOSE)

    @pytest.mark.parametrize("period", [12, 26, 50])
    def test_ema_converges_to_ta_despite_different_seeding(self, period):
        """The seeding divergence is transient, and this measures how transient.

        ``ta`` seeds from the first observation; we seed from the SMA of the
        first ``period``. Both are exponentially weighted with the same alpha,
        so the difference decays by ``(1 - alpha)`` each bar. After ten
        half-lives it is far below anything that could change a decision.
        """
        ours = kernels.ema(CLOSE, period)
        theirs = np.asarray(
            ta.trend.EMAIndicator(DATA["close"], window=period).ema_indicator(), dtype=float
        )
        # 10 half-lives of the smoothing constant, computed rather than guessed.
        alpha = 2.0 / (period + 1.0)
        transient = int(np.ceil(10 * np.log(2) / -np.log(1 - alpha)))
        _compare(ours, theirs, skip=period + transient, tol=1e-4)

    def test_ema_seeding_difference_is_real_and_bounded(self):
        """Name the divergence rather than hide it behind a loose tolerance.

        Both implementations start emitting at the same index. What differs is
        the *seed value*: ``ta`` runs an ``ewm(adjust=False)`` from bar zero and
        masks the warm-up, so its first published value already carries the
        weight of every earlier bar; ours is the plain mean of the window. On
        this series they differ by 0.83 at the seed and converge from there.

        This test exists so that a silent change to our seeding shows up as a
        failure rather than as a slightly different backtest.
        """
        period = 26
        ours = kernels.ema(CLOSE, period)
        theirs = np.asarray(
            ta.trend.EMAIndicator(DATA["close"], window=period).ema_indicator(), dtype=float
        )
        assert np.isnan(ours[: period - 1]).all(), "we emit nothing before the seed is complete"
        assert ours[period - 1] == pytest.approx(float(np.mean(CLOSE[:period])))
        assert abs(ours[period - 1] - theirs[period - 1]) > 1e-6

    @pytest.mark.parametrize("period", [14, 20])
    def test_wilder_smoothing_is_not_a_standard_ema(self, period):
        """The substitution several libraries make silently.

        Wilder uses 1/n; a standard EMA uses 2/(n+1). They are close enough to
        look right and different enough to shift every RSI and ATR value.
        """
        wilder = kernels.wilder_smooth(CLOSE, period)
        standard = kernels.ema(CLOSE, period)
        defined = ~np.isnan(wilder) & ~np.isnan(standard)
        assert not np.allclose(wilder[defined], standard[defined], rtol=1e-3)

        # Equivalent to an EMA with alpha = 1/n, which is the definition.
        equivalent = pd.Series(CLOSE).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
        # Seeded differently, so compare after the transient rather than at the seed.
        _compare(wilder, equivalent, skip=period * 12, tol=1e-6)


class TestOscillators:
    @pytest.mark.parametrize("period", [7, 14, 21])
    def test_rsi_matches_ta(self, period):
        """Exact agreement once the different Wilder seeds have washed out.

        ``ta`` seeds its average gain/loss from an ``ewm`` over the whole
        series; we seed from the mean of the first ``period`` changes. Both are
        1/n smoothings, so the difference decays geometrically: on this series
        it is 1.9e-2 at bar 100, 2.1e-6 at bar 200, and 2.8e-14 by bar 500. The
        skip below is where it has decayed past the tolerance, not where the
        test happens to pass.
        """
        theirs = ta.momentum.RSIIndicator(DATA["close"], window=period).rsi()
        _compare(kernels.rsi(CLOSE, period), theirs, skip=300, tol=1e-6)

    def test_rsi_stays_in_range(self):
        values = kernels.rsi(CLOSE, 14)
        defined = ~np.isnan(values)
        assert values[defined].min() >= 0.0
        assert values[defined].max() <= 100.0

    def test_rsi_returns_100_rather_than_dividing_by_zero(self):
        """An unbroken run of gains is a legitimate state, not an error."""
        rising = np.arange(1.0, 60.0)
        values = kernels.rsi(rising, 14)
        assert values[-1] == 100.0

    def test_macd_matches_ta(self):
        indicator = ta.trend.MACD(DATA["close"], window_slow=26, window_fast=12, window_sign=9)
        line, signal, histogram = kernels.macd(CLOSE, 12, 26, 9)
        # MACD inherits the EMA seeding divergence twice over, so the same
        # convergence argument applies with the slower of the two constants.
        _compare(line, indicator.macd(), skip=26 * 12, tol=1e-4)
        _compare(signal, indicator.macd_signal(), skip=26 * 12, tol=1e-4)
        _compare(histogram, indicator.macd_diff(), skip=26 * 12, tol=1e-4)

    @pytest.mark.parametrize("period", [5, 20, 60])
    def test_rate_of_change_matches_ta(self, period):
        theirs = ta.momentum.ROCIndicator(DATA["close"], window=period).roc()
        # ta reports percent; our kernel reports a fraction. A unit convention,
        # not a disagreement -- converted rather than "fixed".
        _compare(kernels.rate_of_change(CLOSE, period) * 100.0, theirs, skip=period, tol=LOOSE)


class TestVolatility:
    def test_true_range_matches_ta(self):
        theirs = ta.volatility.AverageTrueRange(
            DATA["high"], DATA["low"], DATA["close"], window=1
        ).average_true_range()
        _compare(kernels.true_range(HIGH, LOW, CLOSE), theirs, skip=2, tol=LOOSE)

    @pytest.mark.parametrize("period", [14, 20])
    def test_atr_matches_ta(self, period):
        """Same Wilder-seeding transient as RSI, same geometric decay.

        Relative difference on this series: 8.2e-7 at bar 150, 2.5e-10 at bar
        250, and exactly zero by bar 450.
        """
        theirs = ta.volatility.AverageTrueRange(
            DATA["high"], DATA["low"], DATA["close"], window=period
        ).average_true_range()
        _compare(kernels.atr(HIGH, LOW, CLOSE, period), theirs, skip=250, tol=1e-6)

    @pytest.mark.parametrize("period,stdev", [(20, 2.0), (50, 2.5)])
    def test_bollinger_bands_match_ta(self, period, stdev):
        indicator = ta.volatility.BollingerBands(DATA["close"], window=period, window_dev=stdev)
        middle, upper, lower, _ = kernels.bollinger_bands(CLOSE, period, stdev)
        _compare(middle, indicator.bollinger_mavg(), skip=period, tol=LOOSE)
        # ta uses the population standard deviation (ddof=0). So do we -- a
        # rolling window of a price series is the population of that window,
        # not a sample from it. Agreement here confirms the convention matches,
        # and it is exact to 3e-13 rather than merely close.
        _compare(upper, indicator.bollinger_hband(), skip=period, tol=LOOSE)
        _compare(lower, indicator.bollinger_lband(), skip=period, tol=LOOSE)

    def test_bollinger_uses_population_stdev_deliberately(self):
        """If someone switches to ddof=1, this says so before the bands move."""
        period = 20
        middle, upper, _, _ = kernels.bollinger_bands(CLOSE, period, 2.0)
        population = DATA["close"].rolling(period).std(ddof=0).to_numpy()
        sample = DATA["close"].rolling(period).std(ddof=1).to_numpy()
        implied = (upper - middle) / 2.0
        defined = ~np.isnan(implied)
        np.testing.assert_allclose(implied[defined], population[defined], rtol=LOOSE, atol=LOOSE)
        assert not np.allclose(implied[defined], sample[defined], rtol=1e-6)


class TestDirectionalMovement:
    """ADX: the kernel where implementations disagree most, and most quietly."""

    @pytest.mark.parametrize("period", [14, 20])
    def test_adx_matches_ta_after_both_have_settled(self, period):
        indicator = ta.trend.ADXIndicator(DATA["high"], DATA["low"], DATA["close"], window=period)
        _, _, ours = kernels.adx(HIGH, LOW, CLOSE, period)
        # Both are Wilder double-smoothings seeded slightly differently. The
        # transient is long -- which is exactly why our declared warm-up is
        # 2*period and why we do not emit before it.
        _compare(ours, indicator.adx(), skip=period * 14, tol=1e-3)

    @pytest.mark.parametrize("period", [14])
    def test_directional_indicators_match_ta(self, period):
        indicator = ta.trend.ADXIndicator(DATA["high"], DATA["low"], DATA["close"], window=period)
        plus, minus, _ = kernels.adx(HIGH, LOW, CLOSE, period)
        _compare(plus, indicator.adx_pos(), skip=period * 12, tol=1e-3)
        _compare(minus, indicator.adx_neg(), skip=period * 12, tol=1e-3)

    def test_our_adx_warmup_is_longer_than_the_nominal_period(self):
        """The deliberate divergence, asserted rather than described.

        Libraries that emit ADX from bar ``period`` are publishing values that
        are still converging. We emit nothing until the double smoothing has
        settled, and this test fails if that is ever relaxed.
        """
        period = 14
        plus, _, ours = kernels.adx(HIGH, LOW, CLOSE, period)
        # +DI needs one bar for differencing plus `period` smoothed values.
        assert int(np.argmax(~np.isnan(plus))) == period
        # ADX smooths DX a second time, so it needs 2*period bars in total --
        # index 2*period - 1, which is the double-smoothing warm-up ADR-0010
        # declares and several libraries do not honour.
        first_defined = int(np.argmax(~np.isnan(ours)))
        assert first_defined + 1 == 2 * period

    def test_adx_is_non_negative_and_bounded(self):
        _, _, values = kernels.adx(HIGH, LOW, CLOSE, 14)
        defined = ~np.isnan(values)
        assert values[defined].min() >= 0.0
        assert values[defined].max() <= 100.0


class TestVolume:
    def test_obv_matches_ta_up_to_its_arbitrary_origin(self):
        """A constant offset, and deliberately not corrected.

        ``ta`` seeds OBV at ``volume[0]``; we seed at zero. On this series the
        two differ by exactly 1,017,596 at every bar -- which is ``volume[0]``,
        and is the whole disagreement.

        Neither origin is more correct: OBV is a cumulative sum whose absolute
        level is meaningless, and every use of it (divergence, slope, its own
        moving average) reads changes rather than levels. Starting at zero says
        that plainly. So the test compares the *increments*, which is the part
        that carries information, and asserts the offset is constant -- if a
        single bar's direction convention ever diverged, the offset would stop
        being constant and this fails.
        """
        theirs = np.asarray(
            ta.volume.OnBalanceVolumeIndicator(DATA["close"], DATA["volume"]).on_balance_volume(),
            dtype=float,
        )
        ours = kernels.on_balance_volume(CLOSE, VOLUME)

        offsets = np.unique(np.round(theirs - ours, 6))
        assert offsets.tolist() == [VOLUME[0]], "the divergence is more than a choice of origin"
        np.testing.assert_allclose(np.diff(ours), np.diff(theirs), rtol=LOOSE, atol=LOOSE)

    def test_obv_direction_convention(self):
        """An unchanged close adds nothing. Some implementations add volume."""
        close = np.array([10.0, 10.0, 10.0])
        volume = np.array([100.0, 200.0, 300.0])
        assert kernels.on_balance_volume(close, volume)[-1] == 0.0

    def test_typical_price_matches_the_definition(self):
        expected = (HIGH + LOW + CLOSE) / 3.0
        np.testing.assert_allclose(kernels.typical_price(HIGH, LOW, CLOSE), expected)

    @pytest.mark.parametrize("period", [20, 50])
    def test_rolling_vwap_matches_a_direct_computation(self, period):
        """No library agreement to lean on here, so check the definition itself.

        ``ta`` has only a session-anchored VWAP, and pandas-ta-classic's rolling
        variant uses a different anchor. The definition -- volume-weighted mean
        typical price over the window -- is unambiguous, so it is computed
        independently in pandas and compared.
        """
        typical = pd.Series((HIGH + LOW + CLOSE) / 3.0)
        volume = pd.Series(VOLUME)
        expected = (
            (typical * volume).rolling(period).sum() / volume.rolling(period).sum()
        ).to_numpy()
        _compare(
            kernels.rolling_vwap(HIGH, LOW, CLOSE, VOLUME, period),
            expected,
            skip=period,
            tol=LOOSE,
        )


class TestSecondOpinionFromPandasTaClassic:
    """A second independent library, because two agreeing is much stronger.

    ``ta`` and ``pandas-ta-classic`` are separate codebases with separate
    authors. Where our value matches both, a shared upstream bug is not a
    plausible explanation.
    """

    @pytest.mark.parametrize("period", [20, 50])
    def test_sma(self, period):
        theirs = pta.sma(DATA["close"], length=period)
        _compare(kernels.sma(CLOSE, period), theirs, skip=period, tol=LOOSE)

    def test_rsi(self):
        theirs = pta.rsi(DATA["close"], length=14)
        _compare(kernels.rsi(CLOSE, 14), theirs, skip=300, tol=1e-6)

    def test_atr(self):
        # pandas-ta-classic defaults to an RMA (Wilder) smoothing, matching ours.
        theirs = pta.atr(DATA["high"], DATA["low"], DATA["close"], length=14)
        _compare(kernels.atr(HIGH, LOW, CLOSE, 14), theirs, skip=250, tol=1e-6)

    def test_obv(self):
        """Same constant-origin divergence as ``ta``, checked the same way."""
        theirs = np.asarray(pta.obv(DATA["close"], DATA["volume"]), dtype=float)
        ours = kernels.on_balance_volume(CLOSE, VOLUME)
        np.testing.assert_allclose(np.diff(ours), np.diff(theirs), rtol=LOOSE, atol=LOOSE)

    def test_macd(self):
        frame = pta.macd(DATA["close"], fast=12, slow=26, signal=9)
        line, signal, _ = kernels.macd(CLOSE, 12, 26, 9)
        _compare(line, frame.iloc[:, 0], skip=26 * 12, tol=1e-4)
        _compare(signal, frame.iloc[:, 2], skip=26 * 12, tol=1e-4)


class TestTheComparisonItself:
    """Guard against a cross-validation that cannot fail.

    A comparison harness that silently skips everything, or compares a series
    against itself, passes forever and proves nothing. These check the harness.
    """

    def test_a_planted_error_is_caught(self):
        broken = kernels.sma(CLOSE, 20).copy()
        broken[100] += 0.01
        with pytest.raises(AssertionError):
            _compare(broken, DATA["close"].rolling(20).mean(), skip=20, tol=LOOSE)

    def test_an_off_by_one_shift_is_caught(self):
        """The error class this whole exercise exists to detect."""
        shifted = np.roll(kernels.sma(CLOSE, 20), -1)
        with pytest.raises(AssertionError):
            _compare(shifted, DATA["close"].rolling(20).mean(), skip=20, tol=LOOSE)

    def test_an_all_nan_series_does_not_pass_vacuously(self):
        empty = np.full_like(CLOSE, np.nan)
        with pytest.raises(AssertionError, match="too few overlapping"):
            _compare(empty, DATA["close"].rolling(20).mean(), skip=20)

    def test_wrong_smoothing_constant_is_caught(self):
        """Wilder vs standard EMA -- the substitution that looks almost right."""
        with pytest.raises(AssertionError):
            _compare(
                kernels.ema(CLOSE, 14),
                pd.Series(CLOSE).ewm(alpha=1.0 / 14, adjust=False).mean(),
                skip=200,
                tol=1e-6,
            )
