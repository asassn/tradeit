"""Turn a short setup into a long one, so the long machinery can measure it.

The owner asked for **bear flag breakdown + confirmation + retest**, and the
platform has no bearish detector and no short position. Building both means a
second copy of twelve detectors and of an 11,000-line breakout engine whose
state machine, tolerance model, retest logic and confirmation profiles have
been audited once. A second copy would need auditing again, and the two copies
would drift.

**So the series is mirrored instead of the machinery.** Under the reciprocal
map

    open' = 1/open   high' = 1/low   low' = 1/high   close' = 1/close

a bear flag becomes a bull flag: the sharp decline that forms the pole becomes
a sharp rise, the drifting flag inverts, support becomes resistance, and a
close *below* support becomes a close *above* resistance. The existing
detectors and the existing engine then measure the short setup without a line
of them changing.

**Why the reciprocal and not a reflection about a constant.** ``C - price``
also swaps highs for lows, but it destroys the multiplicative structure every
threshold in this codebase is written in: a percentage gain, ``atr_percent``, a
volume ratio against a price-weighted mean. The reciprocal is the only map that
inverts direction while leaving *ratios* intact, and every detector threshold
here is a ratio.

What the mirror is exactly right about
--------------------------------------

**The order in which price levels are touched.** ``1/x`` is strictly decreasing
on positive prices, so a session whose low crosses below a level is exactly a
session whose mirrored high crosses above the mirrored level, and the sequence
of crossings is preserved bar for bar. Every state the breakout engine can
reach -- ``TESTING_RESISTANCE``, ``CLOSED_ABOVE``, ``RETEST_CONFIRMED``,
``FAILED_BREAKOUT`` -- therefore fires on exactly the sessions its bearish twin
would have fired on. That is the whole reason this is sound rather than
convenient.

What the mirror is **not** right about, and must not be used for
---------------------------------------------------------------

**Returns.** A short sold at ``E`` and covered at ``X`` returns ``1 - X/E``; the
mirrored long returns ``E/X - 1``. Those are different numbers -- halve the
price and the short makes 50% while the mirror makes 100% -- and they stay
different in units of risk. **So the mirror is used only to locate events and
price levels. Every return in a short study is computed on the original prices
with short arithmetic**, by :func:`unmirror_price` and the study's own walk.
Reporting a mirrored return as a short return would overstate every winner.

**Eligibility.** A $5 floor or a $1m turnover floor means nothing applied to a
reciprocal, where a $5 security prices at 0.2. Liquidity is judged on the real
series, always, and passed in.

**Percentage thresholds are log-symmetric, not arithmetically symmetric.** A
detector wanting a 20% pole accepts a mirrored move of 20%, which is a 16.7%
decline in real terms. That is a deliberate choice and the defensible one --
declines and rises are symmetric in log space and in no other -- but it means a
"bear flag" here is the log-mirror of a bull flag rather than a separately
specified structure, and the two would not agree on borderline cases.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any


def mirror_price(price: Decimal | float) -> Decimal:
    """One price through the reciprocal map.

    Raises on a non-positive price rather than returning an infinity: this
    corpus documents zero and placeholder prints (§0.7, §0.8), and a silent
    ``inf`` would travel into a detector and come back as a pattern.
    """
    value = Decimal(str(price))
    if value <= 0:
        raise ValueError(f"cannot mirror a non-positive price: {value}")
    return Decimal(1) / value


def unmirror_price(price: Decimal | float) -> Decimal:
    """Back to real prices. The map is its own inverse."""
    return mirror_price(price)


def _rebuilt[BarT](bar: BarT, **fields: Decimal) -> BarT:
    """One bar with four prices replaced, whatever kind of bar it is.

    The studies feed the detectors a ``DetectorBar`` dataclass and the tests
    feed them a pydantic ``OhlcvBar``; both are frozen and neither can be
    mutated. ``copy.replace`` would dispatch to both in one call but is 3.13+,
    and ``[tool.mypy]`` targets 3.12 -- so the two cases are written out rather
    than raising the floor of the whole package for one function.
    """
    if dataclasses.is_dataclass(bar) and not isinstance(bar, type):
        return dataclasses.replace(bar, **fields)
    copy_method = getattr(bar, "model_copy", None)
    if copy_method is None:
        raise TypeError(f"cannot rebuild a {type(bar).__name__} with new prices")
    copied: Any = copy_method(update=fields)
    return copied  # type: ignore[no-any-return]


def mirror_bars[BarT](bars: list[BarT]) -> list[BarT]:
    """A whole series through the mirror, highs and lows swapped.

    Volume passes through untouched, which is correct: a bear flag's volume
    signature -- heavy on the decline, drying up through the drift, heavy on
    the breakdown -- is the same shape the bull-flag detectors already look
    for, and a reciprocal would corrupt it into nonsense.
    """
    out: list[BarT] = []
    for bar in bars:
        out.append(
            _rebuilt(
                bar,
                open=mirror_price(bar.open),  # type: ignore[attr-defined]
                high=mirror_price(bar.low),  # type: ignore[attr-defined]
                low=mirror_price(bar.high),  # type: ignore[attr-defined]
                close=mirror_price(bar.close),  # type: ignore[attr-defined]
            )
        )
    return out


def short_return(entry: float, exit_price: float, cost: float = 0.0) -> float:
    """What a short actually earned, on real prices.

    Sold at ``entry``, bought back at ``exit_price``, costs charged on both
    sides. Bounded above by ``1 - cost`` terms because a short's best case is
    the security going to zero, and **unbounded below** -- the asymmetry the
    mirrored long cannot represent and the reason this function exists.
    """
    received = entry * (1.0 - cost)
    paid = exit_price * (1.0 + cost)
    return (received - paid) / entry


def borrow_cost(sessions: int, annual_rate: float, sessions_per_year: int = 252) -> float:
    """The borrow fee a short pays for holding, as a fraction of notional.

    A cost the long side does not have and this corpus cannot measure: no
    vendor here carries a borrow rate or a locate. It is therefore charged as
    an assumption at a stated rate and reported across a range, never as a
    single number -- and the study says plainly that a name nobody will lend is
    not expensive to short, it is impossible.
    """
    if sessions < 0 or annual_rate < 0:
        raise ValueError("sessions and annual_rate must be non-negative")
    return annual_rate * sessions / sessions_per_year
