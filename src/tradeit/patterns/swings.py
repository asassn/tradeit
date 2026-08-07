"""Swing points, confirmed causally.

This module is where look-ahead bias would enter the pattern engine if it
entered anywhere, so it is built to make the leak hard to write rather than
merely tested for afterwards.

**The problem.** A swing high is a bar whose high exceeds its neighbours on both
sides. The definition is symmetric, and that symmetry is a time machine: to know
that bar *i* is a swing high with a 3-bar window, you must see bars *i+1*,
*i+2*, *i+3*. On a historical run, marking bar *i* as a swing high *on day i* is
using three bars that had not happened yet. The resulting resistance line is
drawn through highs the system could not have known were highs, and every
pattern built on it inherits the leak.

It is a comfortable bug to have. It makes patterns look cleaner, resistance
tighter, and backtests better, and nothing about the output announces it.

**The resolution.** Every swing carries two dates:

* ``session_date`` -- when the pivot *occurred*. What you draw on a chart.
* ``confirmed_date`` -- when it became *knowable*, which is ``right_bars``
  sessions later. What a detector is allowed to use.

:func:`confirmed_swings` returns only swings whose ``confirmed_date`` is at or
before the evaluation session. A detector that calls it can draw resistance
through a high from twenty sessions ago, because twenty sessions ago plus three
is still in the past — but it cannot use yesterday's high as a confirmed pivot,
because yesterday's high is not yet known to be one.

**The deliberate exception.** The most recent bars matter enormously for
patterns near a breakout, and refusing to look at them at all would make every
detector blind to the present. So :func:`provisional_extremes` exists, returns
the running high and low of the unconfirmed tail, and is explicitly marked
provisional. A detector may use it for *state* decisions -- is price near
resistance? has support broken? -- because those are statements about the
current bar. It must not use it to *define* the structure, because that is the
retroactive-refinement leak wearing different clothes.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError

Floats = NDArray[np.float64]


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed pivot, with both of its dates.

    The two dates are the whole point. ``session_date`` is where the pivot is
    drawn; ``confirmed_date`` is when a detector may first refer to it. Code
    that uses ``session_date`` for visibility decisions has reintroduced the
    leak this class exists to prevent, which is why
    :meth:`is_visible_at` exists and takes the confirmation date rather than
    leaving callers to compare fields themselves.
    """

    kind: SwingKind
    #: Index into the bar series the swing was found in.
    index: int
    session_date: dt.date
    price: float
    #: When this pivot became knowable: ``right_bars`` sessions after it
    #: occurred.
    confirmed_date: dt.date
    confirmed_index: int
    #: Bars required on each side. Part of the swing's identity, because a
    #: 2-bar swing and a 5-bar swing are different structures.
    left_bars: int
    right_bars: int
    #: How far the pivot stands out from the surrounding window, in ATR units
    #: where available. Used to rank pivots without a magic price threshold.
    prominence: float = 0.0

    def is_visible_at(self, session: dt.date) -> bool:
        """Whether a detector evaluating ``session`` may use this pivot."""
        return self.confirmed_date <= session

    @property
    def confirmation_lag(self) -> int:
        return self.confirmed_index - self.index


def find_swings(
    bars: Sequence[OhlcvBar],
    *,
    left_bars: int = 3,
    right_bars: int = 3,
    kind: SwingKind = SwingKind.HIGH,
    atr: Floats | None = None,
) -> list[Swing]:
    """Locate every pivot in the series, tagged with when it became knowable.

    Returns *all* swings including ones not yet confirmed as of the last bar --
    they carry a ``confirmed_index`` past the end of the series, and
    :func:`confirmed_swings` filters them. Returning them rather than dropping
    them keeps the confirmation lag inspectable, which is what makes the
    causality tests able to prove the filter is doing something.

    Ties go to the *earlier* bar. Two adjacent equal highs are one pivot, not
    two, and picking the later one would move the pivot forward in time for no
    structural reason.
    """
    if left_bars < 1 or right_bars < 1:
        raise ConfigError("swing detection needs at least one bar on each side")

    n = len(bars)
    if n < left_bars + right_bars + 1:
        return []

    prices = np.array(
        [float(b.high if kind is SwingKind.HIGH else b.low) for b in bars], dtype=np.float64
    )
    out: list[Swing] = []

    for i in range(left_bars, n - right_bars):
        window_left = prices[i - left_bars : i]
        window_right = prices[i + 1 : i + 1 + right_bars]
        centre = prices[i]

        if kind is SwingKind.HIGH:
            # Strict on the left, non-strict on the right: a later equal high
            # does not unseat an established pivot, but an earlier one does.
            # This is what makes ties resolve to the earlier bar.
            is_pivot = bool((window_left < centre).all() and (window_right <= centre).all())
            reference = float(min(window_left.min(), window_right.min()))
            prominence = centre - reference
        else:
            is_pivot = bool((window_left > centre).all() and (window_right >= centre).all())
            reference = float(max(window_left.max(), window_right.max()))
            prominence = reference - centre

        if not is_pivot:
            continue

        confirmed_index = i + right_bars
        scale = 1.0
        if atr is not None and confirmed_index < len(atr):
            value = atr[i]
            if np.isfinite(value) and value > 0:
                scale = float(value)

        out.append(
            Swing(
                kind=kind,
                index=i,
                session_date=bars[i].session_date,
                price=float(centre),
                confirmed_date=bars[confirmed_index].session_date,
                confirmed_index=confirmed_index,
                left_bars=left_bars,
                right_bars=right_bars,
                prominence=prominence / scale,
            )
        )
    return out


def confirmed_swings(
    bars: Sequence[OhlcvBar],
    as_of_session: dt.date,
    *,
    left_bars: int = 3,
    right_bars: int = 3,
    kind: SwingKind = SwingKind.HIGH,
    atr: Floats | None = None,
) -> list[Swing]:
    """Pivots a detector evaluating ``as_of_session`` may legitimately use.

    This is the function detectors should call. It is a thin filter over
    :func:`find_swings`, and the thinness is deliberate -- the visibility rule
    is one line, stated once, rather than reimplemented in twelve detectors
    with eleven chances to get it wrong.
    """
    return [
        s
        for s in find_swings(bars, left_bars=left_bars, right_bars=right_bars, kind=kind, atr=atr)
        if s.is_visible_at(as_of_session)
    ]


@dataclass(frozen=True, slots=True)
class ProvisionalExtremes:
    """The unconfirmed tail of the series, honestly labelled.

    The final ``right_bars`` sessions cannot contain a confirmed pivot -- there
    has not been time. Ignoring them entirely would blind a detector to the
    present, which is unacceptable for a pattern near a breakout. So they are
    summarised here and marked provisional.

    **Permitted use: state.** "Has price closed above resistance?" "Has support
    broken?" Those are statements about the current bar and are exactly what a
    lifecycle needs.

    **Forbidden use: structure.** Extending a resistance line to include a high
    from this window, or moving a pattern's start date into it, is retroactive
    refinement from unconfirmed data. The prohibition is not stylistic: a
    resistance level that incorporates an unconfirmed high will be *revised
    downward* two sessions later when the high turns out not to be a pivot, and
    every stored pattern that referenced it becomes irreproducible.
    """

    #: Number of trailing sessions with no possible confirmed pivot.
    sessions: int
    highest_high: float | None
    lowest_low: float | None
    last_close: float | None
    first_date: dt.date | None
    last_date: dt.date | None

    @property
    def is_empty(self) -> bool:
        return self.sessions == 0


def provisional_extremes(bars: Sequence[OhlcvBar], *, right_bars: int = 3) -> ProvisionalExtremes:
    """Summarise the tail that cannot yet contain a confirmed pivot."""
    tail = list(bars[-right_bars:]) if right_bars > 0 else []
    if not tail:
        return ProvisionalExtremes(0, None, None, None, None, None)
    return ProvisionalExtremes(
        sessions=len(tail),
        highest_high=max(float(b.high) for b in tail),
        lowest_low=min(float(b.low) for b in tail),
        last_close=float(tail[-1].close),
        first_date=tail[0].session_date,
        last_date=tail[-1].session_date,
    )


def swings_within(swings: Sequence[Swing], start_index: int, end_index: int) -> list[Swing]:
    """Pivots falling inside a window, by bar index."""
    return [s for s in swings if start_index <= s.index <= end_index]


def highest_swing(swings: Sequence[Swing]) -> Swing | None:
    """The highest pivot, ties resolved to the earlier one.

    Earlier-wins matters for resistance: when two highs are equal, the
    structure was established at the first, and the second is a *touch* of it.
    Picking the later one shortens every pattern by however long ago the level
    was first set.
    """
    best: Swing | None = None
    for swing in swings:
        if best is None or swing.price > best.price:
            best = swing
    return best


def lowest_swing(swings: Sequence[Swing]) -> Swing | None:
    best: Swing | None = None
    for swing in swings:
        if best is None or swing.price < best.price:
            best = swing
    return best


def touches_of_level(
    bars: Sequence[OhlcvBar],
    level: float,
    *,
    start_index: int,
    end_index: int,
    tolerance_pct: float,
    kind: SwingKind = SwingKind.HIGH,
    min_separation: int = 2,
) -> list[int]:
    """Bar indices where price came within ``tolerance_pct`` of a level.

    ``min_separation`` prevents a three-day drift along a resistance line from
    counting as three independent touches. A level tested on three separate
    occasions is meaningfully stronger than one price hovered against for three
    consecutive days, and counting them the same inflates every confidence
    score computed from touch counts.
    """
    if tolerance_pct < 0:
        raise ConfigError("tolerance_pct cannot be negative")
    if level <= 0:
        return []

    band = level * tolerance_pct
    out: list[int] = []
    for i in range(max(0, start_index), min(len(bars) - 1, end_index) + 1):
        price = float(bars[i].high if kind is SwingKind.HIGH else bars[i].low)
        if abs(price - level) > band:
            continue
        if out and i - out[-1] < min_separation:
            # Keep the closer of the two, so a drift reports its best test.
            if abs(price - level) < abs(
                float(bars[out[-1]].high if kind is SwingKind.HIGH else bars[out[-1]].low) - level
            ):
                out[-1] = i
            continue
        out.append(i)
    return out
