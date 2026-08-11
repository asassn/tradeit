"""Vectorised indicator kernels.

Every function here takes NumPy arrays of a bar series in chronological order
and returns an array of the same length, with ``NaN`` for the warm-up region.
Nothing takes a date, a clock, or a database handle: these are pure numeric
transforms, and the point-in-time guarantee is enforced by whoever assembles
the input series.

**Two rules govern every kernel, and both are enforced by tests rather than
convention.**

*Causality.* Output *i* depends only on inputs ``0..i``. Computing a kernel over
``x[:k]`` must reproduce exactly the first *k* outputs of computing it over all
of ``x``. This is asserted for every kernel in
``tests/unit/test_causality.py``; it is the property that rules out centred
windows, full-sample normalisation, and the accidental ``shift(-1)``.

*Explicit warm-up.* A kernel that needs *n* observations emits ``NaN`` for the
first *n-1* (or however many its recursion requires) rather than a number
computed from a shorter window. A 200-day average of 40 bars is a different
indicator wearing the same name, and silently returning it makes a live system
and a backtest disagree in exactly the region where instruments enter the
universe.

**Why these are written out rather than imported.** Third-party indicator
libraries differ in seeding (SMA seed vs first-value seed for EMA), in how they
handle gaps, and in whether their rolling windows are centred. Those choices
change the numbers and, in the centred case, break causality outright. The
brief requires indicator behaviour to be explicitly tested, and testing a
dependency's initialisation semantics is harder than writing the recursion.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]


#: Multiplier on machine epsilon for deciding that two price-derived quantities
#: are *equal* rather than merely close.
#:
#: This is not a fudge factor for output noise. It exists because several
#: indicators make a **discrete** decision -- which directional move wins, where
#: a value ranks -- by comparing two floats, and a difference smaller than the
#: representation error of the inputs is not a real difference. Deciding such a
#: case with `>` does not produce a slightly wrong answer; it produces a
#: categorically different one, chosen by whichever way the binary
#: approximation of a decimal price happened to fall.
#:
#: A worked case from real-shaped data: high goes 3.01 -> 3.06 and low goes 3.01
#: -> 2.96, so up-move and down-move are both exactly 0.05 and Wilder's rule
#: says *both* directional movements are zero. In IEEE-754 they come out as
#: 0.050000000000000266 and 0.049999999999999822, so `up > down` is True and the
#: bar is recorded as +DM = 0.05. Quote the same bars in a different unit and
#: the comparison flips.
#:
#: 32 is chosen with margin: the error in `a - b` is bounded by
#: `eps * max(|a|, |b|)`, and these quantities pass through a multiplication and
#: two subtractions before they are compared.
TIE_EPSILON_FACTOR = 32.0

_FLOAT_EPS = float(np.finfo(np.float64).eps)


def tie_tolerance(reference: Floats) -> Floats:
    """Absolute tolerance below which two values derived from ``reference`` are tied.

    Proportional to the magnitude of the *inputs*, not of their difference,
    because that is what bounds cancellation error -- and because a tolerance
    proportional to the inputs scales with them, which is what makes the
    comparisons built on it invariant under a change of units.
    """
    return TIE_EPSILON_FACTOR * _FLOAT_EPS * np.abs(_as_float(reference))


def _empty_like(values: Floats) -> Floats:
    return np.full(values.shape[0], np.nan, dtype=np.float64)


def _as_float(values: NDArray[np.generic]) -> Floats:
    return np.asarray(values, dtype=np.float64)


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma(values: Floats, period: int) -> Floats:
    """Simple moving average. Warm-up ``period - 1``.

    Uses a cumulative-sum sliding window, which is O(n) rather than O(n·period).
    That matters: a full-universe scan computes five SMAs over 4,000 names.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    values = _as_float(values)
    out = _empty_like(values)
    if values.shape[0] < period:
        return out
    cumulative = np.concatenate(([0.0], np.nancumsum(values)))
    windows = cumulative[period:] - cumulative[:-period]
    out[period - 1 :] = windows / period
    return out


def ema(values: Floats, period: int) -> Floats:
    """Exponential moving average, seeded with the SMA of the first ``period``.

    The seeding choice is the one place EMA implementations diverge, and it
    changes every subsequent value. Seeding from the first observation makes the
    early series depend heavily on a single bar; seeding from the SMA is the
    conventional choice and is what is done here. Either way it must be *stated*,
    because a backtest seeded one way and a live system seeded the other produce
    different signals from identical data.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n < period:
        return out

    alpha = 2.0 / (period + 1.0)
    current = float(np.mean(values[:period]))
    out[period - 1] = current
    for i in range(period, n):
        current = alpha * values[i] + (1.0 - alpha) * current
        out[i] = current
    return out


def wilder_smooth(values: Floats, period: int) -> Floats:
    """Wilder's smoothing, seeded with the mean of the first ``period``.

    Distinct from :func:`ema` with the same period: Wilder uses ``1/period``
    where a standard EMA uses ``2/(period+1)``. RSI, ATR and ADX are defined in
    terms of this, and substituting a standard EMA -- which several libraries
    silently do -- shifts every value.
    """
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n < period:
        return out

    current = float(np.mean(values[:period]))
    out[period - 1] = current
    for i in range(period, n):
        current = (current * (period - 1) + values[i]) / period
        out[i] = current
    return out


# ---------------------------------------------------------------------------
# Momentum and oscillators
# ---------------------------------------------------------------------------


def rsi(close: Floats, period: int = 14) -> Floats:
    """Wilder's RSI. Warm-up ``period`` bars (one is consumed by differencing).

    Returns 100 when average loss is zero rather than dividing by it -- an
    unbroken run of gains is a legitimate state, not an error.
    """
    close = _as_float(close)
    out = _empty_like(close)
    n = close.shape[0]
    if n <= period:
        return out

    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = wilder_smooth(gains, period)
    avg_loss = wilder_smooth(losses, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.inf), where=avg_loss > 0)
        values = 100.0 - (100.0 / (1.0 + rs))
    values = np.where(avg_loss == 0, 100.0, values)
    values = np.where((avg_gain == 0) & (avg_loss == 0), 50.0, values)
    values[np.isnan(avg_gain)] = np.nan

    out[1:] = values
    return out


def macd(
    close: Floats, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Floats, Floats, Floats]:
    """MACD line, signal line, histogram.

    The signal EMA is seeded from the SMA of the first ``signal`` *defined* MACD
    values, so the signal's warm-up stacks on the MACD line's rather than
    starting from NaNs. Implementations that ignore this emit a signal line
    several bars too early, and the histogram crossing it produces is fictional.
    """
    close = _as_float(close)
    macd_line = ema(close, fast) - ema(close, slow)

    signal_line = _empty_like(close)
    defined = ~np.isnan(macd_line)
    if defined.any():
        start = int(np.argmax(defined))
        tail = ema(macd_line[start:], signal)
        signal_line[start:] = tail

    return macd_line, signal_line, macd_line - signal_line


def rate_of_change(values: Floats, period: int) -> Floats:
    """Fractional change over ``period`` bars. Warm-up ``period``."""
    values = _as_float(values)
    out = _empty_like(values)
    if values.shape[0] <= period:
        return out
    previous = values[:-period]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period:] = np.where(previous != 0, values[period:] / previous - 1.0, np.nan)
    return out


def momentum_score(close: Floats, period: int) -> Floats:
    """Alias of :func:`rate_of_change` on closes, named for how it is used."""
    return rate_of_change(close, period)


# ---------------------------------------------------------------------------
# Volatility and range
# ---------------------------------------------------------------------------


def true_range(high: Floats, low: Floats, close: Floats) -> Floats:
    """True range. The first bar has no previous close, so it is NaN.

    Several libraries use ``high - low`` for the first bar instead. That is a
    different quantity, and it biases the first ATR value low.
    """
    high, low, close = _as_float(high), _as_float(low), _as_float(close)
    out = _empty_like(close)
    if close.shape[0] < 2:
        return out
    previous_close = close[:-1]
    out[1:] = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - previous_close),
            np.abs(low[1:] - previous_close),
        ]
    )
    return out


def atr(high: Floats, low: Floats, close: Floats, period: int = 14) -> Floats:
    """Average true range, Wilder-smoothed."""
    tr = true_range(high, low, close)
    out = _empty_like(tr)
    if tr.shape[0] < period + 1:
        return out
    out[1:] = wilder_smooth(tr[1:], period)
    return out


def atr_percent(high: Floats, low: Floats, close: Floats, period: int = 14) -> Floats:
    """ATR as a fraction of price -- comparable across instruments.

    Raw ATR is not: a $2 ATR is enormous on a $20 stock and trivial on a $2,000
    one, so any cross-sectional use of volatility needs this form.
    """
    values = atr(high, low, close, period)
    close = _as_float(close)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(close > 0, values / close, np.nan)


def realized_volatility(close: Floats, period: int, annualisation: int = 252) -> Floats:
    """Annualised standard deviation of log returns. Warm-up ``period``.

    Log returns rather than simple returns: they are additive across time, which
    is what makes the square-root-of-time annualisation valid.
    """
    close = _as_float(close)
    out = _empty_like(close)
    n = close.shape[0]
    if n <= period:
        return out

    with np.errstate(divide="ignore", invalid="ignore"):
        # log(p1 / p0), not log(p1) - log(p0). Algebraically the same and
        # numerically not: differencing two logs of nearby prices cancels most
        # of the significand, so the result carries far less precision than the
        # ratio does. It also leaves the value dependent on the units -- the
        # cancellation error changes with the price level -- which showed up
        # downstream as a percentile whose rank moved when prices were rescaled.
        positive = np.where(close > 0, close, np.nan)
        log_returns = np.log(positive[1:] / positive[:-1])

    scale = float(np.sqrt(annualisation))
    if log_returns.shape[0] < period:
        return out
    # Strided windows rather than a Python loop. The window for output index i
    # is log_returns[i - period : i], i.e. returns strictly before bar i, which
    # is what keeps this causal.
    windows = np.lib.stride_tricks.sliding_window_view(log_returns, period)
    valid = ~np.isnan(windows).any(axis=1)
    stdev = np.full(windows.shape[0], np.nan, dtype=np.float64)
    if valid.any():
        stdev[valid] = np.std(windows[valid], axis=1, ddof=1)
    out[period : period + stdev.shape[0]] = stdev * scale
    return out


def bollinger_bands(
    close: Floats, period: int = 20, num_stdev: float = 2.0
) -> tuple[Floats, Floats, Floats, Floats]:
    """Middle, upper, lower bands and bandwidth.

    Population standard deviation (``ddof=0``), matching Bollinger's original
    definition. Bandwidth ``(upper - lower) / middle`` is the value that
    actually gets used downstream, because it is the scale-free measure of
    whether a range is contracting.
    """
    close = _as_float(close)
    middle = sma(close, period)
    deviation = _empty_like(close)
    n = close.shape[0]
    if n >= period:
        windows = np.lib.stride_tricks.sliding_window_view(close, period)
        deviation[period - 1 :] = np.std(windows, axis=1, ddof=0)

    upper = middle + num_stdev * deviation
    lower = middle - num_stdev * deviation
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = np.where(middle > 0, (upper - lower) / middle, np.nan)
    return middle, upper, lower, bandwidth


def adx(
    high: Floats, low: Floats, close: Floats, period: int = 14
) -> tuple[Floats, Floats, Floats]:
    """Wilder's ADX with +DI and -DI. Warm-up roughly ``2 * period``.

    ADX is a double smoothing -- DI from smoothed directional movement, then ADX
    from smoothed DX -- so its warm-up is about twice the nominal period. Using
    ``period`` as the warm-up, as some implementations do, exposes values that
    are still converging.
    """
    high, low, close = _as_float(high), _as_float(low), _as_float(close)
    n = close.shape[0]
    plus_di = _empty_like(close)
    minus_di = _empty_like(close)
    adx_out = _empty_like(close)
    if n < 2 * period + 1:
        return plus_di, minus_di, adx_out

    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]

    # Wilder's rule: the larger move wins, and a tie gives *both* directional
    # movements zero. Implementing that with a bare `>` on two floats delegates
    # the tie to representation error -- 3.01 -> 3.06 against 3.01 -> 2.96 is a
    # genuine tie that IEEE-754 renders as 0.050000000000000266 against
    # 0.049999999999999822 -- so the bar's direction is decided by the binary
    # approximation of a decimal price rather than by the market. Quoting the
    # same bars in cents flips it.
    #
    # The tolerance is proportional to the price level, which is what bounds the
    # cancellation error in these differences, and which makes the whole
    # comparison invariant to the units the prices are quoted in.
    reference = np.maximum(
        np.maximum(np.abs(high[1:]), np.abs(high[:-1])),
        np.maximum(np.abs(low[1:]), np.abs(low[:-1])),
    )
    tolerance = tie_tolerance(reference)
    decisive = np.abs(up_move - down_move) > tolerance
    plus_dm = np.where(decisive & (up_move > down_move) & (up_move > tolerance), up_move, 0.0)
    minus_dm = np.where(decisive & (down_move > up_move) & (down_move > tolerance), down_move, 0.0)

    tr = true_range(high, low, close)[1:]
    smoothed_tr = wilder_smooth(tr, period)
    smoothed_plus = wilder_smooth(plus_dm, period)
    smoothed_minus = wilder_smooth(minus_dm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * np.divide(
            smoothed_plus, smoothed_tr, out=np.full_like(smoothed_tr, np.nan), where=smoothed_tr > 0
        )
        mdi = 100.0 * np.divide(
            smoothed_minus,
            smoothed_tr,
            out=np.full_like(smoothed_tr, np.nan),
            where=smoothed_tr > 0,
        )
        denominator = pdi + mdi
        dx = 100.0 * np.divide(
            np.abs(pdi - mdi),
            denominator,
            out=np.full_like(denominator, np.nan),
            where=denominator > 0,
        )

    plus_di[1:] = pdi
    minus_di[1:] = mdi

    defined = ~np.isnan(dx)
    if defined.any():
        start = int(np.argmax(defined))
        adx_out[1 + start :] = wilder_smooth(dx[start:], period)
    return plus_di, minus_di, adx_out


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def typical_price(high: Floats, low: Floats, close: Floats) -> Floats:
    return (_as_float(high) + _as_float(low) + _as_float(close)) / 3.0


def on_balance_volume(close: Floats, volume: Floats) -> Floats:
    """Cumulative signed volume.

    The *level* of OBV is meaningless -- it depends entirely on where the series
    happens to start -- so only its slope and its divergence from price carry
    information. Downstream features must use changes, never the raw level, and
    the level is emitted only so those changes can be computed.
    """
    close, volume = _as_float(close), _as_float(volume)
    out = _empty_like(close)
    if close.shape[0] == 0:
        return out
    direction = np.sign(np.diff(close))
    out[0] = 0.0
    out[1:] = np.cumsum(direction * volume[1:])
    return out


def relative_volume(volume: Floats, period: int = 20) -> Floats:
    """Today's volume against the average of the ``period`` bars *before* it.

    The current bar is excluded from its own baseline. Including it damps
    exactly the signal being measured -- on a 5x volume day, self-inclusion
    pulls the ratio toward 4.2x for a 20-day window -- and the whole point of
    the measure is to detect that day.
    """
    volume = _as_float(volume)
    out = _empty_like(volume)
    baseline = sma(volume, period)
    if volume.shape[0] <= period:
        return out
    previous = baseline[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.where(previous > 0, volume[1:] / previous, np.nan)
    return out


def average_dollar_volume(
    high: Floats, low: Floats, close: Floats, volume: Floats, period: int = 20
) -> Floats:
    """Rolling mean turnover, using typical price rather than close.

    Close-times-volume systematically misstates turnover on trend days, when
    much of the volume traded well away from the close.
    """
    return sma(typical_price(high, low, close) * _as_float(volume), period)


def rolling_vwap(high: Floats, low: Floats, close: Floats, volume: Floats, period: int) -> Floats:
    """Volume-weighted average price over a rolling window of bars.

    A note on meaningfulness, since the brief asks for VWAP "where
    mathematically and temporally meaningful": true VWAP is an intraday,
    session-anchored quantity computed from every trade. On daily bars the
    honest approximation is this rolling volume-weighted typical price. It is a
    useful reference level; it is *not* the VWAP a trader sees on an intraday
    chart, and it must not be presented as one. Genuine session VWAP requires
    intraday data and is computed by :func:`session_vwap`.
    """
    weights = _as_float(volume)
    prices = typical_price(high, low, close)
    numerator = sma(prices * weights, period)
    denominator = sma(weights, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator > 0, numerator / denominator, np.nan)


def session_vwap(
    high: Floats, low: Floats, close: Floats, volume: Floats, session_index: NDArray[np.int64]
) -> Floats:
    """Anchored VWAP that resets at each session boundary.

    ``session_index`` labels each bar with its trading session, so the
    accumulator resets at the open rather than running across the overnight gap.
    Meaningful only on intraday bars; on daily bars every session contains one
    bar and the result degenerates to the typical price.
    """
    prices = typical_price(high, low, close)
    weights = _as_float(volume)
    out = _empty_like(prices)

    cumulative_pv = 0.0
    cumulative_v = 0.0
    previous_session = None
    for i in range(prices.shape[0]):
        if session_index[i] != previous_session:
            cumulative_pv = 0.0
            cumulative_v = 0.0
            previous_session = session_index[i]
        cumulative_pv += prices[i] * weights[i]
        cumulative_v += weights[i]
        out[i] = cumulative_pv / cumulative_v if cumulative_v > 0 else np.nan
    return out


# ---------------------------------------------------------------------------
# Structure: extremes, distance, slope, contraction
# ---------------------------------------------------------------------------


def rolling_max(values: Floats, period: int) -> Floats:
    """Highest value over the trailing ``period`` bars, inclusive of the current."""
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, period)
    out[period - 1 :] = np.nanmax(windows, axis=1)
    return out


def rolling_min(values: Floats, period: int) -> Floats:
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, period)
    out[period - 1 :] = np.nanmin(windows, axis=1)
    return out


def distance_from(values: Floats, reference: Floats) -> Floats:
    """Fractional distance from a reference series: ``(v - r) / r``.

    Serves distance-from-moving-average and distance-from-52-week-high alike.
    Negative means below.
    """
    values, reference = _as_float(values), _as_float(reference)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(reference > 0, values / reference - 1.0, np.nan)


def slope(values: Floats, lookback: int) -> Floats:
    """Per-bar fractional slope over ``lookback`` bars.

    Normalised by the starting level and by the lookback, so slopes are
    comparable across instruments and across lookback settings. A rising 50-day
    average on a $20 stock and on a $2,000 stock produce the same number when
    they are rising at the same rate.
    """
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n <= lookback:
        return out
    previous = values[:-lookback]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[lookback:] = np.where(
            previous > 0, (values[lookback:] / previous - 1.0) / lookback, np.nan
        )
    return out


def contraction_ratio(values: Floats, short_window: int, long_window: int) -> Floats:
    """Recent activity against a longer baseline. Below 1 means quieter.

    The scale-free signature of a consolidating base, and the input Phase 4's
    pattern detectors will use. Defined for range, ATR and volume alike, which
    is why it takes a generic series.
    """
    if long_window <= short_window:
        raise ValueError("long_window must exceed short_window")
    short = sma(values, short_window)
    long = sma(values, long_window)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(long > 0, short / long, np.nan)


def percent_rank(values: Floats, period: int) -> Floats:
    """Percentile of the current value within its own trailing window, in [0, 1].

    A *time-series* percentile -- where does today sit in this instrument's own
    recent history -- as distinct from the cross-sectional percentile computed
    across instruments in the relative-strength engine. The two are different
    features and are deliberately named differently.
    """
    values = _as_float(values)
    out = _empty_like(values)
    n = values.shape[0]
    if n < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, period)
    subjects = values[period - 1 :][:, None]
    finite = ~np.isnan(windows)
    counts = finite.sum(axis=1)

    with np.errstate(invalid="ignore"):
        # `<=` with a tie tolerance, because this is a discrete count and two
        # values equal to within representation error are the same value. A bare
        # `<=` moves the rank by a whole step -- 1/(counts - 1), percent points
        # rather than rounding -- on a difference of one ulp, which is how a
        # percentile of an invariant series stopped being invariant.
        tolerance = tie_tolerance(np.maximum(np.abs(windows), np.abs(subjects)))
        at_or_below = (finite & (windows <= subjects + tolerance)).sum(axis=1)

    usable = (counts >= 2) & ~np.isnan(values[period - 1 :])
    ranks = np.full(windows.shape[0], np.nan, dtype=np.float64)
    # Subtract one from both to exclude the subject from its own comparison,
    # matching the closed form the scalar version used.
    ranks[usable] = (at_or_below[usable] - 1) / (counts[usable] - 1)
    out[period - 1 :] = ranks
    return out


def gap_frequency(open_: Floats, close: Floats, period: int, threshold: float) -> Floats:
    """Fraction of the last ``period`` bars that opened beyond ``threshold``.

    An input to the volatility regime: markets that gap frequently behave
    differently from markets with the same realised volatility that do not, and
    stops behave very differently in the two.
    """
    open_, close = _as_float(open_), _as_float(close)
    out = _empty_like(close)
    n = close.shape[0]
    if n <= period:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        gaps = np.abs(np.where(close[:-1] > 0, open_[1:] / close[:-1] - 1.0, np.nan))
    exceeded = (gaps > threshold).astype(np.float64)
    exceeded[np.isnan(gaps)] = np.nan
    out[period:] = sma(exceeded, period)[period - 1 :]
    return out
