"""Building the causal bar series a scanner is given, one per timeframe.

**Why this is not inside the scanner.** A scanner that resampled internally
would be deciding for itself which bars form this week's candle, and the answer
depends on a trading calendar and a clock that the pattern layer has no business
owning. Worse, the failure mode is silent: aggregate Monday-to-Wednesday into a
"weekly" bar and every weekly detector reads a close that will change on Friday,
with nothing in the output saying so.

So resampling happens here, on the way in, using the Phase 3 aggregation that
already knows the rule: **a period is complete when its last scheduled trading
session has passed**, decided by the calendar rather than by whether data
happens to be present. :func:`causal_series` then hands the scanner finished
bars only.

**What a mid-week weekly scan sees.** Last week's completed bar, not this week's
partial one. That is the value a live system would have had on Wednesday, and it
is the whole point: a weekly pattern detected mid-week must be detectable from
the weeks that have actually finished. The alternative — letting the current
partial week in — produces a weekly high that grows through the week and a
pattern whose geometry changes without any new *weekly* information.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from tradeit.analytics.timeframes import (
    aggregate_intraday,
    completed_only,
    resample_for_feature_use,
    to_ohlcv_bars,
)
from tradeit.core.calendar import TradingCalendar
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.errors import DataError

#: Timeframes built by rolling up daily bars.
FROM_DAILY: frozenset[Bartimeframe] = frozenset({Bartimeframe.W1, Bartimeframe.MN1})

#: Timeframes built by rolling up intraday bars.
FROM_INTRADAY: frozenset[Bartimeframe] = frozenset(
    {Bartimeframe.M5, Bartimeframe.M15, Bartimeframe.M30, Bartimeframe.H1, Bartimeframe.H4}
)


def causal_series(
    daily_bars: Sequence[OhlcvBar],
    timeframes: Sequence[Bartimeframe],
    as_of_session: dt.date,
    *,
    instrument_id: int | None = None,
    calendar: TradingCalendar | None = None,
) -> dict[Bartimeframe, list[OhlcvBar]]:
    """Finished bars for each requested timeframe, from a daily series.

    Only completed periods are returned. Partial ones are dropped rather than
    marked, because a scanner receiving a mixed list would have to remember to
    filter and the one time it forgets is the one time it matters.
    """
    if not daily_bars:
        return {}
    if daily_bars[-1].session_date > as_of_session:
        raise DataError(
            f"daily series extends to {daily_bars[-1].session_date}, past the "
            f"{as_of_session} boundary; the caller must clock-gate before resampling"
        )

    instrument = instrument_id if instrument_id is not None else daily_bars[0].instrument_id
    out: dict[Bartimeframe, list[OhlcvBar]] = {}
    for timeframe in timeframes:
        if timeframe is Bartimeframe.D1:
            out[timeframe] = list(daily_bars)
        elif timeframe in FROM_DAILY:
            aggregated = resample_for_feature_use(daily_bars, timeframe, as_of_session, calendar)
            out[timeframe] = to_ohlcv_bars(aggregated, instrument)
        else:
            raise DataError(
                f"{timeframe} cannot be built from daily bars; supply intraday bars "
                "and use intraday_series"
            )
    return out


def intraday_series(
    minute_bars: Sequence[OhlcvBar],
    timeframes: Sequence[Bartimeframe],
    as_of: dt.datetime,
    *,
    instrument_id: int | None = None,
    calendar: TradingCalendar | None = None,
) -> dict[Bartimeframe, list[OhlcvBar]]:
    """Finished intraday bars for each requested timeframe.

    Buckets are anchored to each session open rather than the wall clock, which
    matters more than it sounds: clock-aligned four-hour buckets on a 09:30 open
    would put half an hour of nothing at the front of the first bar, and that
    bar is then not comparable with any other.
    """
    if not minute_bars:
        return {}

    instrument = instrument_id if instrument_id is not None else minute_bars[0].instrument_id
    out: dict[Bartimeframe, list[OhlcvBar]] = {}
    for timeframe in timeframes:
        if timeframe not in FROM_INTRADAY:
            raise DataError(f"{timeframe} is not an intraday timeframe")
        aggregated = completed_only(aggregate_intraday(minute_bars, timeframe, as_of, calendar))
        out[timeframe] = to_ohlcv_bars(aggregated, instrument)
    return out


def contains_no_future_bars(higher: Sequence[OhlcvBar], as_of_session: dt.date) -> bool:
    """Whether every aggregated bar finished on or before the boundary.

    The invariant the multi-timeframe requirement turns on, expressed as a
    predicate so a test reads as the requirement rather than as a date
    comparison.
    """
    return all(bar.session_date <= as_of_session for bar in higher)


__all__ = [
    "FROM_DAILY",
    "FROM_INTRADAY",
    "causal_series",
    "contains_no_future_bars",
    "intraday_series",
]
