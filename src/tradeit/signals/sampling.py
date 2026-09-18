"""A common calendar grid for cross-sectional signal studies.

**Why this exists.** Every signal study before this module sampled each security
on **its own** grid: every 21st bar counted from that security's first print.
Two securities listed a week apart were therefore never sampled on the same day,
and the "cross-section" on any given date was whoever happened to land there.
Measured on the §32 screen: 2,006 distinct sample dates, of which only 208
carried 20 or more securities, with a **median of 50** on those. §33 then showed
what that does to a per-date estimator: 1,592 thin dates of recent listings
outvoted 119 broad ones and more than doubled the headline IC.

A cross-sectional question -- *which of today's securities* -- needs every
security asked on the **same** day. This module puts them there.

**Two rules, both about what may not be invented.**

*No drift.* A security that did not print on a grid date is **not sampled on
that date**. It is never moved to its nearest print, because a print from the
following day uses information the grid date did not have, and one from the
previous day measures a different window from everyone else's.

*Every outcome spans the same calendar window.* The outcome is measured to the
exchange session exactly ``horizon`` sessions after the grid date -- the same
session for every security on that date -- and only if the security printed
there. Counting ``horizon`` of the security's *own* bars instead, as the old
samplers did, stretches the window for any security with a gap, so securities
on one date would be measured over different market moves. That is the very
confound a cross-sectional IC exists to remove.

Gaps *between* the endpoints are allowed. A quiet name that skipped a session
mid-window still has a return from T to T+h; refusing it would bias the sample
toward the most liquid names, which is a different claim from the one being
tested.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from tradeit.core.calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class GridPoint:
    """One security sampled on one grid date."""

    #: The grid date: the day the signal is read.
    session_date: dt.date
    #: Index of that session in the security's own bar list.
    signal_index: int
    #: Index, in the same list, of the grid date's outcome session.
    outcome_index: int


@dataclass(frozen=True, slots=True)
class CommonGrid:
    """Sample dates and the outcome session each one is measured to.

    The outcome sessions are computed **once**, from the exchange calendar,
    because they are the same for every security on a date. Carrying them here
    rather than recomputing per security is also what makes the shared window
    structural: no caller can measure one security on a date to a different
    session from another.
    """

    dates: tuple[dt.date, ...]
    outcome_dates: tuple[dt.date, ...]
    horizon: int
    stride: int


def common_grid(
    calendar: TradingCalendar,
    start: dt.date,
    end: dt.date,
    stride: int,
    horizon: int,
) -> CommonGrid:
    """Every ``stride``-th exchange session from ``start``, with its outcome.

    Only grid dates whose outcome session falls **on or before** ``end`` are
    kept. An outcome measured past the end of a registered window reads data
    the registration did not name -- for a 2010-2019 study, the first quarter
    of 2020 -- so the last ``horizon`` sessions contribute no sample dates.

    The grid is a property of the exchange calendar, not of any security, which
    is the whole point: two studies with the same arguments sample the same days.
    """
    if stride < 1:
        raise ValueError("stride must be >= 1")
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    sessions = calendar.sessions_between(start, end)
    dates: list[dt.date] = []
    outcomes: list[dt.date] = []
    for index in range(0, len(sessions), stride):
        target = index + horizon
        if target >= len(sessions):
            break
        dates.append(sessions[index])
        outcomes.append(sessions[target])
    return CommonGrid(tuple(dates), tuple(outcomes), horizon, stride)


def grid_points(
    bar_dates: Sequence[dt.date],
    grid: CommonGrid,
    *,
    min_history: int = 0,
) -> list[GridPoint]:
    """Where one security can be sampled on the grid, and its outcome bar.

    ``bar_dates`` must be the security's session dates in ascending order --
    the dates :func:`tradeit.research01.series.price_series` returns.

    ``min_history`` is the number of the security's own bars that must precede
    and include the signal bar, so an indicator is never computed on fewer bars
    than it needs.
    """
    position = {date: index for index, date in enumerate(bar_dates)}
    points: list[GridPoint] = []
    for date, target in zip(grid.dates, grid.outcome_dates, strict=True):
        signal_index = position.get(date)
        if signal_index is None or signal_index + 1 < min_history:
            continue
        outcome_index = position.get(target)
        if outcome_index is None:
            continue
        points.append(GridPoint(date, signal_index, outcome_index))
    return points
