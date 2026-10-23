"""Which session a nightly capture is actually about.

**The capture was off by one day on every scheduled run**, and the reason is
worth stating because nothing in the output looked wrong. `end` defaulted to
``dt.datetime.now(dt.UTC).date()``, the job fires at 22:10 America/New_York,
and at that hour the UTC date is already tomorrow. So a run on Thursday evening
asked Tiingo for a window ending Friday, received **Thursday's** bar, and wrote
the observation as ``session_date = Friday``.

Measured 2026-10-22 at 22:13 EDT: bars landed on 2026-10-22, observations on
2026-10-23. The bars were right -- `_write_bars` takes each row's own
``session_date`` from the vendor -- and only the observation carried the
request's `end`, which is the one field a delisting is made of.

Two consequences, the second worse than the first:

1. **Every scheduled observation is dated one day late.** A future exit would
   be recorded against the wrong session.
2. **A Friday-night run stamps Saturday.** The vendor still returns Friday's
   bar for a window ending Saturday, so the record would assert a Saturday
   session that had a price. A weekend run on a universe with a genuine
   delisting would assert a non-existent session in which a company was
   silent.

The fix is not a timezone correction alone. The honest session is **the latest
trading session at or before the market-local date**, and that question has an
answer only from a calendar: `research-01`'s `trading_sessions` is a
materialised table spanning 1990-01-02 to 2030-12-31 precisely because, as
`RESEARCH_01_DATA_DICTIONARY.md` §3a puts it, an exchange calendar cannot be
expressed in SQL.

**Fails closed.** If the calendar cannot answer -- no session at or before the
date asked about, which means the table has not been extended far enough back
or the date predates the corpus -- this raises rather than guessing. A capture
that writes a session date nobody can confirm is worse than one that does not
run, because the row it writes outlives the operator's memory of the gap.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from tradeit.errors import DataError

__all__ = ["MARKET_TZ", "latest_session_at_or_before", "market_today"]

#: The exchange's own clock. The capture runs at 22:10 local, which is after
#: the close and before midnight -- but **after** midnight UTC, which is what
#: made the naive `now(UTC).date()` wrong by exactly one day every night.
MARKET_TZ = ZoneInfo("America/New_York")


def market_today(now: dt.datetime | None = None) -> dt.date:
    """Today as the exchange reckons it, not as UTC does.

    Takes `now` so the boundary is testable: the interesting instants are the
    ones where the two calendars disagree, and they cannot be reached by
    waiting for them.
    """
    moment = now or dt.datetime.now(dt.UTC)
    if moment.tzinfo is None:
        raise DataError("refusing to reckon a market date from a naive datetime")
    return moment.astimezone(MARKET_TZ).date()


def latest_session_at_or_before(sessions: Iterable[dt.date], day: dt.date) -> dt.date:
    """The most recent trading session on or before ``day``.

    Returns ``day`` itself when it is a session. On a weekend or holiday it
    walks back, which is the right behaviour for a job that runs nightly: the
    last thing the market did is still the last thing the market did, and
    re-asking about it is idempotent on prices.

    Raises when the calendar has nothing to offer, rather than returning
    ``day`` and letting a non-session be recorded as one.
    """
    candidates = [s for s in sessions if s <= day]
    if not candidates:
        raise DataError(
            f"no trading session at or before {day} in the calendar; refusing to "
            "stamp an observation with a session date nothing can confirm"
        )
    return max(candidates)
