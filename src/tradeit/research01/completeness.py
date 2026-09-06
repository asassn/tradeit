"""Does a price series actually cover the company, and does it reach the death?

The survivorship gate reports coverage as *"this dead registrant has at least
one price bar"*, and states in its own limitations that **holding a price bar
is not the same as holding a complete series**. That limitation is the one that
matters most for the question the corpus exists to answer: a company that
failed in 2005 whose prices stop in 2001 contributes a name to the denominator
and hides the four years that killed it. Counting it as covered is a subtler
version of not holding it at all.

This module turns that acknowledged unknown into two measured facts, kept
apart because they fail independently:

* **Density** — how many of the sessions inside a series' own span it actually
  holds. Catches a series that is present but full of holes.
* **Reach** — how close the last bar gets to the registrant's exit date.
  Catches a series that is dense and simply stops too early.

A series can be perfect on one and useless on the other, so there is no single
completeness number here, for the same reason ``CoverageBounds`` publishes two
bounds and no third.

**Sessions come from the exchange calendar, not from a 252/365 approximation.**
The approximation is wrong in exactly the cases that matter — a short span
around a suspension, or a series ending in a holiday-heavy month — and a
density that reads 0.94 because December has fewer sessions is a number
somebody will investigate for nothing. :class:`~tradeit.core.calendar.TradingCalendar`
already knows the real schedule.

*Not measured here:* whether the prices are **correct**. A dense series that
reaches the exit can still be another company's data spliced in, which is what
``adjudicate.py`` and the alias intervals exist for. Completeness and
correctness are different questions and this answers only the first.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.core.calendar import TradingCalendar

__all__ = [
    "Reach",
    "SeriesCompleteness",
    "assess_series",
    "assess_span",
    "summarise",
]

#: How close the last bar must come to the exit for the series to be treated as
#: reaching it. Three months is deliberately generous: an exit is dated at a
#: *filing*, and a company's last trade routinely precedes the paperwork that
#: records its death by weeks. A tighter bound would report a filing lag as a
#: data gap.
REACHES_WITHIN = dt.timedelta(days=92)

#: Below this share of its own span's sessions, a series is holed rather than
#: merely short. Not a quality threshold anything refuses on — a reported cut,
#: because no threshold here has been justified against outcomes.
DENSE_ENOUGH = 0.8


class Reach(StrEnum):
    """How close a series gets to the death it is supposed to record."""

    REACHES_EXIT = "reaches_exit"
    STOPS_MONTHS_EARLY = "stops_months_early"
    STOPS_YEARS_EARLY = "stops_years_early"
    #: The series continues past the exit date. Not a gap — usually a filing
    #: that dates the exit before trading actually stopped, and worth seeing
    #: separately rather than folded into "reaches".
    OUTLIVES_EXIT = "outlives_exit"


@dataclass(frozen=True, slots=True)
class SeriesCompleteness:
    """One security's series, measured against its own span and its exit."""

    sessions_held: int
    first: dt.date
    last: dt.date
    sessions_expected: int
    exit_date: dt.date | None

    @property
    def density(self) -> float | None:
        """Share of its own span's sessions the series holds.

        ``None`` when the span holds fewer than two sessions. A single bar
        spans exactly one session, so the ratio is 1.0 and means nothing —
        reporting it would claim completeness from one observation, which is
        the vacuous pass this codebase has produced before. Two is the smallest
        span in which a hole can exist.
        """
        if self.sessions_expected < 2:
            return None
        return self.sessions_held / self.sessions_expected

    @property
    def dense_enough(self) -> bool:
        density = self.density
        return density is not None and density >= DENSE_ENOUGH

    @property
    def gap_to_exit(self) -> dt.timedelta | None:
        """How long before the exit the series stops. Negative if it outlives."""
        if self.exit_date is None:
            return None
        return self.exit_date - self.last

    @property
    def reach(self) -> Reach | None:
        gap = self.gap_to_exit
        if gap is None:
            return None
        if gap < -REACHES_WITHIN:
            return Reach.OUTLIVES_EXIT
        if gap <= REACHES_WITHIN:
            return Reach.REACHES_EXIT
        if gap <= dt.timedelta(days=365):
            return Reach.STOPS_MONTHS_EARLY
        return Reach.STOPS_YEARS_EARLY

    @property
    def records_the_death(self) -> bool:
        """The question the corpus exists to answer, for one name.

        A series that outlives its exit date records it too: the trading is
        there, and the exit date is the thing in doubt.
        """
        return self.reach in (Reach.REACHES_EXIT, Reach.OUTLIVES_EXIT)


def assess_series(
    sessions: Sequence[dt.date],
    *,
    exit_date: dt.date | None,
    calendar: TradingCalendar | None = None,
) -> SeriesCompleteness | None:
    """Measure one series. ``None`` for an empty one, which is not a series.

    ``sessions`` is the set of *distinct session dates* held, not the row
    count: the corpus stores a raw and a vendor-adjusted fact per session, so
    counting rows reports every series as exactly twice as dense as it is.
    """
    if not sessions:
        return None
    ordered = sorted(set(sessions))
    first, last = ordered[0], ordered[-1]
    cal = calendar or TradingCalendar()
    expected = len(cal.sessions_between(first, last))
    return SeriesCompleteness(
        sessions_held=len(ordered),
        first=first,
        last=last,
        sessions_expected=expected,
        exit_date=exit_date,
    )


def assess_span(
    sessions_held: int,
    first: dt.date,
    last: dt.date,
    *,
    exit_date: dt.date | None,
    calendar: TradingCalendar | None = None,
) -> SeriesCompleteness | None:
    """Measure a series from its shape rather than from its dates.

    :func:`assess_series` needs every session date in memory, which is fine for
    one series and fatal for a corpus: 23 million price facts materialised as
    Python dates killed the gate outright. The count and the two endpoints are
    all the measurement uses, and a database can produce those with
    ``count(distinct session_date), min(...), max(...)`` without loading a row.
    """
    if sessions_held <= 0 or last < first:
        return None
    cal = calendar or TradingCalendar()
    return SeriesCompleteness(
        sessions_held=sessions_held,
        first=first,
        last=last,
        sessions_expected=len(cal.sessions_between(first, last)),
        exit_date=exit_date,
    )


def summarise(assessments: Sequence[SeriesCompleteness]) -> dict[str, object]:
    """Counts by reach and density. Reported beside coverage, never merged."""
    by_reach: dict[str, int] = {}
    for entry in assessments:
        reach = entry.reach
        if reach is not None:
            by_reach[reach.value] = by_reach.get(reach.value, 0) + 1
    measurable = [a for a in assessments if a.density is not None]
    holed = [a for a in measurable if not a.dense_enough]
    records = [a for a in assessments if a.records_the_death]
    return {
        "series": len(assessments),
        "by_reach": dict(sorted(by_reach.items())),
        "records_the_death": len(records),
        "density_measurable": len(measurable),
        "below_dense_enough": len(holed),
    }
