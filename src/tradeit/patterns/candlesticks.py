"""The twenty candlestick shapes, as entry filters rather than signals.

``PATTERN_PROGRAM.md`` items 21-40, from the owner's reference chart. They are
one-, two- and three-bar shapes, and the program is explicit that the atlas
measures them **as filters on the structural patterns**, not standalone: a
hammer on its own is a bar with a long lower wick, and there are hundreds of
thousands of those.

**Why these live outside the detector registry.** The twelve structural
detectors fit geometry -- swings, boundaries, retracements, a quality model --
and ``bull_flag.py`` is 1,387 lines for one of them. A candlestick has no
geometry to fit: it is an arithmetic relation between four numbers on up to
three consecutive bars. Putting them through the registry would buy a scanner
pass, a lifecycle and a quality score that none of them has any use for, and
would cost a re-scan of the corpus. Evaluated here, they can be applied at the
**signal bar of an event that has already been scanned**, which is why items
21-40 cost no scan at all.

Thresholds, and where they come from
------------------------------------

Every constant below is a **published convention**, taken from the owner's
reference chart and the standard literature, written down before any of them
was measured. None was tuned, and none may be tuned to improve a result
without the scoped proposition ``CLAUDE.md`` requires for a detector threshold.

The one judgement call worth naming: a shape is defined on **proportions of
the bar's own range**, never on absolute prices or on a fixed number of ticks.
A $5 security and a $500 one must be able to print the same hammer, and this
corpus spans both.

**Context is not shape.** A hammer and a hanging man are the *same* shape and
differ only in what preceded them; likewise an inverted hammer and a shooting
star. This module returns the shape and a separate prior-trend reading, and
refuses to collapse them -- so the atlas can ask whether "hammer" pays because
of the wick or because of the downtrend it follows, which is a question a
combined flag cannot answer.

The two tweezers are **not** such a pair, though they are often described as
one: here they differ in the order of the two bars' colours, which is shape,
and each is defined in its own right.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A body at or under this share of the bar's range is "small" -- the doji
#: family. The conventional figure; 5% is sometimes used for a strict doji and
#: is deliberately not adopted here, because the strict reading finds almost
#: nothing on daily bars.
DOJI_BODY = 0.10
#: A shadow must be at least this multiple of the body to count as "long".
LONG_SHADOW = 2.0
#: The opposite shadow of a hammer or shooting star may be no more than this
#: share of the range, or the bar is a spinning top instead.
SHORT_SHADOW = 0.25
#: A spinning top's body sits between the doji threshold and this share.
SPINNING_BODY = 0.35
#: Two bars are a tweezer when their extremes agree within this share of the
#: first bar's range. Exact equality is a convention of drawn charts, not of
#: real prints.
TWEEZER_TOLERANCE = 0.05
#: A star's body must gap clear of the prior body by this share of that body.
STAR_GAP = 0.0
#: Sessions of prior closes a shape's context is judged against.
CONTEXT_SESSIONS = 5

#: Every shape this module reports, in the program's numbering.
SHAPES = (
    "hammer",
    "inverted_hammer",
    "spinning_top",
    "doji",
    "dragonfly_doji",
    "gravestone_doji",
    "bullish_engulfing",
    "bearish_engulfing",
    "tweezer_top",
    "tweezer_bottom",
    "morning_star",
    "morning_doji_star",
    "evening_star",
    "evening_doji_star",
    "three_white_soldiers",
    "three_black_crows",
    "rising_three",
    "falling_three",
)
#: The two shapes that are other shapes plus a context reading, reported
#: separately so the atlas can separate the wick from the trend that preceded
#: it. See the module docstring.
CONTEXTUAL = {"hanging_man": "hammer", "shooting_star": "inverted_hammer"}


@dataclass(frozen=True, slots=True)
class Bar:
    """One bar reduced to the six quantities every shape is defined on."""

    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def upper(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    def body_share(self) -> float:
        """The body as a share of the range; 1.0 for a zero-range bar.

        A zero-range bar is a real print in this corpus (§0.7 records
        placeholder bars) and dividing by it would give a NaN that reads as
        False everywhere downstream -- quietly, and for the wrong reason. It is
        called a full body instead, which excludes it from every doji test and
        is the honest reading of a bar that did not move.
        """
        return self.body / self.span if self.span > 0 else 1.0


def _bars(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, index: int, count: int
) -> list[Bar] | None:
    """The ``count`` bars ending at ``index``, oldest first, or None."""
    if index - count + 1 < 0 or index >= close.shape[0]:
        return None
    out = [
        Bar(float(open_[i]), float(high[i]), float(low[i]), float(close[i]))
        for i in range(index - count + 1, index + 1)
    ]
    return out if all(b.high >= b.low and b.low > 0 for b in out) else None


def _is_doji(bar: Bar) -> bool:
    return bar.body_share() <= DOJI_BODY


def _long_lower(bar: Bar) -> bool:
    return bar.lower >= LONG_SHADOW * bar.body and bar.upper <= SHORT_SHADOW * bar.span


def _long_upper(bar: Bar) -> bool:
    return bar.upper >= LONG_SHADOW * bar.body and bar.lower <= SHORT_SHADOW * bar.span


def prior_trend(close: np.ndarray, index: int, sessions: int = CONTEXT_SESSIONS) -> str:
    """What the bars before ``index`` were doing. Context, never shape.

    Returned separately from every shape so that "hammer" and "hanging man" --
    which are the same bar -- can be told apart by the atlas rather than by a
    definition that has already decided the answer.
    """
    if index - sessions < 0:
        return "unknown"
    before, at = float(close[index - sessions]), float(close[index - 1])
    if before <= 0:
        return "unknown"
    change = at / before - 1.0
    return "rising" if change > 0 else ("falling" if change < 0 else "flat")


def shapes_at(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, index: int
) -> dict[str, bool]:
    """Every shape that is true of the bar at ``index`` and those before it.

    Returns a flag per name in :data:`SHAPES` plus the two in
    :data:`CONTEXTUAL`. A bar can satisfy several -- a dragonfly doji is also a
    doji -- and they are reported as they fall out rather than forced into one
    winner, because the atlas conditions on each independently and a
    precedence rule here would silently decide which of two setups got
    measured.
    """
    found = dict.fromkeys((*SHAPES, *CONTEXTUAL), False)
    one = _bars(open_, high, low, close, index, 1)
    if one is None:
        return found
    (bar,) = one
    doji = _is_doji(bar)
    found["doji"] = doji
    found["dragonfly_doji"] = doji and _long_lower(bar) and bar.upper <= SHORT_SHADOW * bar.span
    found["gravestone_doji"] = doji and _long_upper(bar) and bar.lower <= SHORT_SHADOW * bar.span
    found["spinning_top"] = (
        DOJI_BODY < bar.body_share() <= SPINNING_BODY
        and bar.upper > SHORT_SHADOW * bar.span
        and bar.lower > SHORT_SHADOW * bar.span
    )
    # A hammer needs a body to hang beneath; a doji with a long lower shadow is
    # a dragonfly, and calling it both would double-count the same bar under
    # two of the program's numbered setups.
    found["hammer"] = not doji and _long_lower(bar)
    found["inverted_hammer"] = not doji and _long_upper(bar)
    trend = prior_trend(close, index)
    found["hanging_man"] = found["hammer"] and trend == "rising"
    found["shooting_star"] = found["inverted_hammer"] and trend == "rising"

    two = _bars(open_, high, low, close, index, 2)
    if two is not None:
        first, second = two
        found["bullish_engulfing"] = (
            first.bearish
            and second.bullish
            and second.close >= first.open
            and second.open <= first.close
            and second.body > first.body
        )
        found["bearish_engulfing"] = (
            first.bullish
            and second.bearish
            and second.open >= first.close
            and second.close <= first.open
            and second.body > first.body
        )
        tolerance = TWEEZER_TOLERANCE * first.span if first.span > 0 else 0.0
        found["tweezer_top"] = (
            abs(second.high - first.high) <= tolerance and first.bullish and second.bearish
        )
        found["tweezer_bottom"] = (
            abs(second.low - first.low) <= tolerance and first.bearish and second.bullish
        )

    three = _bars(open_, high, low, close, index, 3)
    if three is not None:
        first, middle, last = three
        star_down = (
            first.bearish
            and max(middle.open, middle.close) <= first.close + STAR_GAP * first.body
            and last.bullish
            and last.close > first.open - first.body / 2
        )
        star_up = (
            first.bullish
            and min(middle.open, middle.close) >= first.close - STAR_GAP * first.body
            and last.bearish
            and last.close < first.open + first.body / 2
        )
        small = middle.body <= first.body / 2
        found["morning_star"] = star_down and small and not _is_doji(middle)
        found["morning_doji_star"] = star_down and _is_doji(middle)
        found["evening_star"] = star_up and small and not _is_doji(middle)
        found["evening_doji_star"] = star_up and _is_doji(middle)
        found["three_white_soldiers"] = all(b.bullish for b in three) and (
            first.close < middle.close < last.close
            and middle.open > min(first.open, first.close)
            and last.open > min(middle.open, middle.close)
        )
        found["three_black_crows"] = all(b.bearish for b in three) and (
            first.close > middle.close > last.close
            and middle.open < max(first.open, first.close)
            and last.open < max(middle.open, middle.close)
        )

    five = _bars(open_, high, low, close, index, 5)
    if five is not None:
        first, a, b, c, last = five
        # The three middle bars must stay within the long first bar's range:
        # that containment IS the pattern, and a bar that escapes it has
        # broken the consolidation rather than continued it.
        inside = all(first.low <= x.low and x.high <= first.high for x in (a, b, c))
        found["rising_three"] = (
            first.bullish
            and last.bullish
            and inside
            and all(not x.bullish or x.body < first.body for x in (a, b, c))
            and last.close > first.close
        )
        found["falling_three"] = (
            first.bearish
            and last.bearish
            and inside
            and all(not x.bearish or x.body < first.body for x in (a, b, c))
            and last.close < first.close
        )
    return found
