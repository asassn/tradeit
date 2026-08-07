"""Causal multi-timeframe bar construction.

Higher-timeframe bars are **aggregated from a base timeframe**, never fetched
separately. Two reasons, and the second is the important one:

1. A weekly bar built from the daily bars is consistent with them by
   construction, rather than by hoping the vendor agrees.
2. Completeness becomes something the *exchange calendar* determines rather
   than something a vendor asserts.

Point (2) is the whole problem this module exists to solve.

**The partial-candle trap.** At Wednesday noon, the current week's bar exists —
it has an open, a running high, a running low, and a last price. It is also
*wrong* to use as a feature, because on Friday it will be a different bar. A
system that treats it as complete has, at Wednesday noon, a "weekly close" that
will change twice more before the week ends. Backtest that and you are reading
Thursday's and Friday's prices on Wednesday.

So the rule here is absolute: **a higher-timeframe bar is emitted only once the
calendar says its final constituent session has closed.** ``complete=False``
bars are constructed (a live dashboard legitimately wants to see the week in
progress) but the feature pipeline filters them out, and
``TimeframeConfig.emit_incomplete_bars`` defaults to ``False``.

The same logic governs intraday resampling, where the additional hazard is that
a session is 6.5 hours long and does not divide evenly into 4-hour buckets, and
that early-close days produce a short final bucket.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.core.calendar import TradingCalendar, get_calendar
from tradeit.core.enums import Bartimeframe, DataQualityFlag, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.errors import DataError

UTC = dt.UTC

#: Higher timeframes this module can build, and the base each is built from.
AGGREGATION_SOURCES: dict[Bartimeframe, Bartimeframe] = {
    Bartimeframe.W1: Bartimeframe.D1,
    Bartimeframe.MN1: Bartimeframe.D1,
    Bartimeframe.H4: Bartimeframe.M1,
    Bartimeframe.H1: Bartimeframe.M1,
    Bartimeframe.M30: Bartimeframe.M1,
    Bartimeframe.M15: Bartimeframe.M1,
    Bartimeframe.M5: Bartimeframe.M1,
}

_INTRADAY_MINUTES: dict[Bartimeframe, int] = {
    Bartimeframe.M5: 5,
    Bartimeframe.M15: 15,
    Bartimeframe.M30: 30,
    Bartimeframe.H1: 60,
    Bartimeframe.H4: 240,
}


@dataclass(frozen=True, slots=True)
class AggregatedBar:
    """A higher-timeframe bar and, critically, whether it is finished.

    ``complete`` is the field that matters. An incomplete bar is a legitimate
    object -- a dashboard shows the week in progress -- but it is not a feature
    input, and :func:`completed_only` is the gate that enforces that.

    ``knowledge_time`` is inherited from the *last* constituent bar, so a weekly
    bar becomes knowable when its Friday close becomes knowable, not when its
    Monday open did.
    """

    timeframe: Bartimeframe
    period_start: dt.date
    period_end: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    event_time: dt.datetime
    knowledge_time: dt.datetime
    complete: bool
    constituent_sessions: int
    quality: DataQualityFlag = DataQualityFlag.OK

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / 3


def completed_only(bars: Sequence[AggregatedBar]) -> list[AggregatedBar]:
    """Drop unfinished bars. The gate between construction and feature use."""
    return [bar for bar in bars if bar.complete]


# ---------------------------------------------------------------------------
# Daily -> weekly / monthly
# ---------------------------------------------------------------------------


def _week_key(day: dt.date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def _month_key(day: dt.date) -> tuple[int, int]:
    return day.year, day.month


def aggregate_daily(
    bars: Sequence[OhlcvBar],
    timeframe: Bartimeframe,
    as_of_session: dt.date,
    calendar: TradingCalendar | None = None,
) -> list[AggregatedBar]:
    """Roll daily bars into weekly or monthly bars.

    Completeness is decided by the calendar, not by the data: a period is
    complete when its last *scheduled* trading session is at or before
    ``as_of_session``. That distinction matters because the data alone cannot
    tell a finished week from a week with a missing Friday -- and treating the
    second as finished silently produces a weekly close that later changes.
    """
    if timeframe not in (Bartimeframe.W1, Bartimeframe.MN1):
        raise DataError(f"aggregate_daily cannot build {timeframe}")
    if not bars:
        return []

    calendar = calendar or get_calendar()
    key_fn = _week_key if timeframe is Bartimeframe.W1 else _month_key

    ordered = sorted(bars, key=lambda b: b.session_date)
    if ordered[-1].session_date > as_of_session:
        raise DataError(
            f"aggregate_daily received a bar dated {ordered[-1].session_date} which is "
            f"after as_of_session {as_of_session}; the caller must supply a clock-gated series"
        )

    groups: dict[tuple[int, int], list[OhlcvBar]] = {}
    for bar in ordered:
        if bar.timeframe is not Bartimeframe.D1:
            raise DataError(f"expected daily bars, got {bar.timeframe}")
        groups.setdefault(key_fn(bar.session_date), []).append(bar)

    out: list[AggregatedBar] = []
    for key in sorted(groups):
        members = groups[key]
        final_session = _final_session_of_period(members[0].session_date, timeframe, calendar)
        complete = final_session is not None and final_session <= as_of_session
        out.append(_fold(members, timeframe, complete))
    return out


def _final_session_of_period(
    any_day_in_period: dt.date, timeframe: Bartimeframe, calendar: TradingCalendar
) -> dt.date | None:
    """The last scheduled trading session of the period containing ``any_day``.

    Asks the calendar rather than assuming Friday or month-end: Good Friday
    means the week ends on Thursday, and a month can end on a weekend.
    """
    if timeframe is Bartimeframe.W1:
        monday = any_day_in_period - dt.timedelta(days=any_day_in_period.weekday())
        sessions = calendar.sessions_between(monday, monday + dt.timedelta(days=6))
    else:
        first = any_day_in_period.replace(day=1)
        if first.month == 12:
            following = first.replace(year=first.year + 1, month=1)
        else:
            following = first.replace(month=first.month + 1)
        sessions = calendar.sessions_between(first, following - dt.timedelta(days=1))
    return sessions[-1] if sessions else None


def _fold(members: Sequence[OhlcvBar], timeframe: Bartimeframe, complete: bool) -> AggregatedBar:
    """Combine constituent bars. Open from the first, close from the last."""
    first, last = members[0], members[-1]
    return AggregatedBar(
        timeframe=timeframe,
        period_start=first.session_date,
        period_end=last.session_date,
        open=first.open,
        high=max(b.high for b in members),
        low=min(b.low for b in members),
        close=last.close,
        volume=sum((b.volume for b in members), Decimal(0)),
        event_time=last.event_time,
        # The period becomes knowable when its LAST constituent does. Using the
        # first would make a whole week readable from its Monday.
        knowledge_time=max(b.knowledge_time for b in members),
        complete=complete,
        constituent_sessions=len(members),
        quality=(
            DataQualityFlag.OK
            if all(b.quality is DataQualityFlag.OK for b in members)
            else DataQualityFlag.SUSPECT_PRICE_SPIKE
        ),
    )


# ---------------------------------------------------------------------------
# Intraday
# ---------------------------------------------------------------------------


def aggregate_intraday(
    bars: Sequence[OhlcvBar],
    timeframe: Bartimeframe,
    as_of: dt.datetime,
    calendar: TradingCalendar | None = None,
) -> list[AggregatedBar]:
    """Roll minute bars into 5/15/30-minute, hourly or 4-hourly bars.

    Buckets are anchored to each **session open**, not to the wall clock. A
    market opening at 09:30 produces hourly bars at 09:30, 10:30, 11:30 …; naive
    clock-aligned resampling would produce a 09:00-10:00 bucket containing half
    an hour of nothing, and a first bar that is not comparable to the others.

    The final bucket of a session is usually short -- 6.5 hours does not divide
    into four-hour buckets, and an early close makes it shorter still. Such a
    bucket is still *complete* once the session has closed: it is a real bar
    covering the time the market was actually open. Only a bucket whose session
    is still in progress is incomplete.
    """
    if timeframe not in _INTRADAY_MINUTES:
        raise DataError(f"aggregate_intraday cannot build {timeframe}")
    if not bars:
        return []

    calendar = calendar or get_calendar()
    width = dt.timedelta(minutes=_INTRADAY_MINUTES[timeframe])
    ordered = sorted(bars, key=lambda b: b.event_time)

    by_session: dict[dt.date, list[OhlcvBar]] = {}
    for bar in ordered:
        if bar.event_time > as_of:
            raise DataError(
                f"aggregate_intraday received a bar at {bar.event_time.isoformat()} after "
                f"as_of {as_of.isoformat()}; the caller must supply a clock-gated series"
            )
        by_session.setdefault(bar.session_date, []).append(bar)

    out: list[AggregatedBar] = []
    for session_date in sorted(by_session):
        session = calendar.session(session_date)
        session_closed = session.close_utc <= as_of

        buckets: dict[int, list[OhlcvBar]] = {}
        for bar in by_session[session_date]:
            # A minute bar stamped at its close belongs to the bucket containing
            # the minute it covers, so subtract an epsilon before bucketing.
            elapsed = (bar.event_time - dt.timedelta(microseconds=1)) - session.open_utc
            index = max(int(elapsed // width), 0)
            buckets.setdefault(index, []).append(bar)

        for index in sorted(buckets):
            members = buckets[index]
            bucket_end = session.open_utc + width * (index + 1)
            # Complete when the bucket's own window has elapsed, or when the
            # session has closed (which truncates the final bucket legitimately).
            complete = bucket_end <= as_of or session_closed
            folded = _fold(members, timeframe, complete)
            out.append(folded)
    return out


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def align_to_daily(
    daily_sessions: Sequence[dt.date], higher: Sequence[AggregatedBar]
) -> list[AggregatedBar | None]:
    """Map each daily session to the most recent *completed* higher-timeframe bar.

    This is how a daily-frequency feature vector carries weekly context. The
    rule is strictly backward-looking: Wednesday sees last week's completed bar,
    not this week's partial one. On Monday of a new week, the answer is still
    the previous week -- which is correct, and is exactly the value a live
    system would have had.
    """
    completed = sorted(completed_only(higher), key=lambda b: b.period_end)
    out: list[AggregatedBar | None] = []
    cursor = 0
    current: AggregatedBar | None = None

    for session in daily_sessions:
        while cursor < len(completed) and completed[cursor].period_end <= session:
            current = completed[cursor]
            cursor += 1
        out.append(current)
    return out


def resample_for_feature_use(
    bars: Sequence[OhlcvBar],
    timeframe: Bartimeframe,
    as_of_session: dt.date,
    calendar: TradingCalendar | None = None,
) -> list[AggregatedBar]:
    """The function feature code should call: aggregate, then drop partials.

    Exists so that using an incomplete bar requires deliberately calling
    :func:`aggregate_daily` and ignoring ``complete`` -- rather than being the
    default outcome of forgetting to filter.
    """
    return completed_only(aggregate_daily(bars, timeframe, as_of_session, calendar))


def to_ohlcv_bars(aggregated: Sequence[AggregatedBar], instrument_id: int) -> list[OhlcvBar]:
    """Convert completed aggregates back into validated domain bars.

    Lets the indicator engine run unchanged on any timeframe. Incomplete bars
    are refused rather than silently dropped: converting one would produce a
    validated-looking bar with no record that it was provisional.
    """
    out: list[OhlcvBar] = []
    for bar in aggregated:
        if not bar.complete:
            raise DataError(
                f"refusing to convert an incomplete {bar.timeframe} bar "
                f"({bar.period_start}..{bar.period_end}) into an OhlcvBar; "
                "it would look indistinguishable from a finished one"
            )
        out.append(
            OhlcvBar(
                instrument_id=instrument_id,
                timeframe=bar.timeframe,
                session_date=bar.period_end,
                event_time=bar.event_time,
                knowledge_time=bar.knowledge_time,
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                quality=bar.quality,
            )
        )
    return out
