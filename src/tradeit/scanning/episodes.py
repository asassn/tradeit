"""Where one analytical history ends and another begins.

The first real snapshot contains a series that is not one security. Under the
ticker ``BBBY`` there are 3,638 bars running 2010-01-04 to 2026-08-07 with a
536-session hole from 2023-07-05 to 2025-08-21. Bed Bath & Beyond stopped
trading in May 2023. The bars after the hole belong to whatever later took the
symbol, and the vendor handed both to us under one instrument id.

Nothing downstream can survive that being treated as one history:

* a 200-day moving average on the first session after the gap averages prices
  from two companies;
* ATR and every volatility measure inherit a range no participant experienced;
* confirmed pivots reach back across the boundary, so a "swing low" can be a
  different issuer's low;
* a pattern detected before the gap can be carried forward, re-detected, or
  resolved against a level from the other company;
* a breakout monitor watching a pre-gap boundary opens an attempt the moment
  the new listing's price crosses a number that meant something to a security
  that no longer exists;
* a retest measures a pullback to a level from a different order book.

So a long discontinuity **ends the analytical episode**. The next session
starts a new one with no inherited state and no inherited bars.

**Why a reset rather than a bridge.** The architecture has no mechanism that
could prove continuity here, and the one it does have argues the other way:
identity is the surrogate ``instrument_id``, and
:class:`~tradeit.storage.tables.SymbolMapping` exists precisely because a
ticker string is not an identity. A vendor's decision to reuse a symbol is not
evidence that two price series belong to one economic entity, and *no amount of
point-in-time discipline* makes a 26-month hole into a tradeable history. If a
future data source supplies a listing-level identifier that genuinely
establishes continuity across a gap, this is the seam to relax — with that
evidence, not by widening a threshold.

**No bars are manufactured.** The gap stays a gap. An episode is a view over
the sessions that exist, not an interpolation over the ones that do not.

**Scattered absences are not breaks.** See :data:`BREAK_SESSIONS`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from tradeit.core.calendar import TradingCalendar, get_calendar
from tradeit.validation.continuity import STRUCTURAL_RUN_SESSIONS

__all__ = ["BREAK_SESSIONS", "AnalyticalEpisode", "segment_sessions"]

#: Consecutive missing trading sessions that end an episode.
#:
#: The same constant the continuity check calls a structural break, imported
#: rather than restated so the two can never drift: a gap the gate reports as
#: structural is exactly a gap the scanner resets on. Roughly a trading month —
#: long enough that no holiday, no ordinary halt and no thin-liquidity run
#: reaches it, short enough to catch a quarter-long suspension.
#:
#: Below it, state is **preserved**. NKLA in the same snapshot is missing 31
#: sessions across 11 separate runs over five years: absent observations inside
#: a continuous listing, not a discontinuity. Resetting on those would destroy
#: identity continuity for every thinly traded name and would report each
#: missing print as a new security — which is the opposite error and a worse
#: one, because it inflates the count of distinct patterns and makes any rate
#: computed over them meaningless. The pattern tracker's own grace period
#: already handles brief absences, and that is the right layer for them.
BREAK_SESSIONS = STRUCTURAL_RUN_SESSIONS


@dataclass(frozen=True, slots=True)
class AnalyticalEpisode:
    """A maximal run of sessions with no structural break inside it.

    ``start`` is a floor, not a suggestion: no bar before it may reach a
    detector, an indicator, a pivot search, or a breakout boundary during this
    episode.
    """

    index: int
    start: dt.date
    end: dt.date
    sessions: tuple[dt.date, ...]
    #: Missing trading sessions between the previous episode and this one.
    #: Zero for the first episode.
    break_sessions: int = 0

    @property
    def is_continuation(self) -> bool:
        return self.index > 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "sessions": len(self.sessions),
            "break_sessions_before": self.break_sessions,
        }

    def __str__(self) -> str:
        prefix = f"after a {self.break_sessions}-session break, " if self.break_sessions else ""
        return (
            f"episode {self.index}: {prefix}{self.start.isoformat()}..{self.end.isoformat()} "
            f"({len(self.sessions):,} sessions)"
        )


def segment_sessions(
    sessions: Sequence[dt.date],
    *,
    break_sessions: int = BREAK_SESSIONS,
    calendar: TradingCalendar | None = None,
) -> list[AnalyticalEpisode]:
    """Split observed sessions into episodes at structural breaks.

    The gap is measured in **trading sessions the exchange held**, not calendar
    days: a three-day weekend is not a break and a summer of suspension is,
    and only the calendar can tell them apart.

    An empty input gives no episodes. A series with no break gives exactly one,
    which is the ordinary case and costs one calendar lookup per session pair.
    """
    if not sessions:
        return []
    calendar = calendar or get_calendar()

    episodes: list[AnalyticalEpisode] = []
    current: list[dt.date] = [sessions[0]]
    pending_break = 0
    for previous, day in pairwise(sessions):
        # Sessions the exchange held strictly between the two observed dates.
        missing = max(0, calendar.session_count(previous, day) - 2)
        if missing >= break_sessions:
            episodes.append(
                AnalyticalEpisode(
                    index=len(episodes),
                    start=current[0],
                    end=current[-1],
                    sessions=tuple(current),
                    break_sessions=pending_break,
                )
            )
            pending_break = missing
            current = [day]
            continue
        current.append(day)

    episodes.append(
        AnalyticalEpisode(
            index=len(episodes),
            start=current[0],
            end=current[-1],
            sessions=tuple(current),
            break_sessions=pending_break,
        )
    )
    return episodes
