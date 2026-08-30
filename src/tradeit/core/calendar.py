"""Exchange session calendar.

Trading-day arithmetic looks trivial until a half-day before Thanksgiving
silently turns a 20-day lookback into 20 rows spanning 28 calendar days, or a
knowledge timestamp lands during a session that closed at 13:00. Everything
date-shaped in the platform routes through here.

Backed by ``pandas_market_calendars`` when installed. A small hardcoded fallback
keeps unit tests and fresh checkouts working offline; the fallback knows
weekends and the fixed US market holidays but not one-off closures, so it is
never used when the real calendar is importable.
"""

from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from tradeit.errors import DataError

NY = ZoneInfo("America/New_York")
UTC = dt.UTC

REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)


@dataclass(frozen=True, slots=True)
class Session:
    """One trading session, with UTC instants for its boundaries."""

    session_date: dt.date
    open_utc: dt.datetime
    close_utc: dt.datetime
    is_early_close: bool

    @property
    def duration(self) -> dt.timedelta:
        return self.close_utc - self.open_utc


class TradingCalendar:
    """Session calendar for a single exchange group (default: US equities)."""

    def __init__(self, name: str = "XNYS") -> None:
        self.name = name
        self._schedule = self._load_schedule(name)

    @staticmethod
    def _load_schedule(name: str) -> dict[dt.date, tuple[dt.datetime, dt.datetime]] | None:
        """Load a real exchange schedule, or ``None`` to use the fallback."""
        try:
            import pandas_market_calendars as mcal
        except ImportError:
            return None
        cal = mcal.get_calendar(name)
        frame = cal.schedule(start_date="1990-01-01", end_date="2035-12-31")
        return {
            idx.date(): (
                row.market_open.to_pydatetime().astimezone(UTC),
                row.market_close.to_pydatetime().astimezone(UTC),
            )
            for idx, row in frame.iterrows()
        }

    @property
    def uses_fallback(self) -> bool:
        """True when running on the approximate built-in holiday list.

        Surfaced deliberately: a backtest run against the fallback calendar has
        wrong session counts around one-off closures and should say so.
        """
        return self._schedule is None

    # -- membership ----------------------------------------------------------

    def is_session(self, day: dt.date) -> bool:
        if self._schedule is not None:
            return day in self._schedule
        return day.weekday() < 5 and day not in _fallback_holidays(day.year)

    def session(self, day: dt.date) -> Session:
        if not self.is_session(day):
            raise DataError(f"{day.isoformat()} is not a trading session on {self.name}")
        if self._schedule is not None:
            open_utc, close_utc = self._schedule[day]
            close_local = close_utc.astimezone(NY).time()
            return Session(day, open_utc, close_utc, is_early_close=close_local < REGULAR_CLOSE)
        early = day in _fallback_early_closes(day.year)
        close_time = EARLY_CLOSE if early else REGULAR_CLOSE
        return Session(
            session_date=day,
            open_utc=dt.datetime.combine(day, REGULAR_OPEN, tzinfo=NY).astimezone(UTC),
            close_utc=dt.datetime.combine(day, close_time, tzinfo=NY).astimezone(UTC),
            is_early_close=early,
        )

    # -- navigation ----------------------------------------------------------

    def next_session(self, day: dt.date) -> dt.date:
        cursor = day + dt.timedelta(days=1)
        for _ in range(30):
            if self.is_session(cursor):
                return cursor
            cursor += dt.timedelta(days=1)
        raise DataError(f"no trading session within 30 days after {day.isoformat()}")

    def previous_session(self, day: dt.date) -> dt.date:
        cursor = day - dt.timedelta(days=1)
        for _ in range(30):
            if self.is_session(cursor):
                return cursor
            cursor -= dt.timedelta(days=1)
        raise DataError(f"no trading session within 30 days before {day.isoformat()}")

    def sessions_between(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Sessions in ``[start, end]``, inclusive on both ends."""
        if end < start:
            raise DataError(f"sessions_between called with end {end} before start {start}")
        out: list[dt.date] = []
        cursor = start
        while cursor <= end:
            if self.is_session(cursor):
                out.append(cursor)
            cursor += dt.timedelta(days=1)
        return out

    def shift_sessions(self, day: dt.date, n: int) -> dt.date:
        """Move ``n`` sessions forward (positive) or back (negative)."""
        cursor = day
        step = self.next_session if n > 0 else self.previous_session
        for _ in range(abs(n)):
            cursor = step(cursor)
        return cursor

    def session_count(self, start: dt.date, end: dt.date) -> int:
        return len(self.sessions_between(start, end))

    # -- instants ------------------------------------------------------------

    def session_for_instant(self, moment: dt.datetime) -> dt.date | None:
        """The session an instant falls inside, or ``None`` outside RTH."""
        if moment.tzinfo is None:
            raise DataError("session_for_instant requires a timezone-aware datetime")
        local_day = moment.astimezone(NY).date()
        if not self.is_session(local_day):
            return None
        sess = self.session(local_day)
        return local_day if sess.open_utc <= moment.astimezone(UTC) <= sess.close_utc else None

    def close_instant(self, day: dt.date) -> dt.datetime:
        return self.session(day).close_utc


@functools.lru_cache(maxsize=64)
def _fallback_holidays(year: int) -> frozenset[dt.date]:
    """Approximate US market holidays for a year. Weekend-observed rules only.

    Deliberately omits one-off closures (funerals, hurricanes, 9/11). Used only
    when ``pandas_market_calendars`` is unavailable.
    """

    def observed(day: dt.date) -> dt.date:
        if day.weekday() == 5:
            return day - dt.timedelta(days=1)
        if day.weekday() == 6:
            return day + dt.timedelta(days=1)
        return day

    def nth_weekday(month: int, weekday: int, n: int) -> dt.date:
        first = dt.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + dt.timedelta(days=offset + 7 * (n - 1))

    def last_weekday(month: int, weekday: int) -> dt.date:
        nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
        last = nxt - dt.timedelta(days=1)
        return last - dt.timedelta(days=(last.weekday() - weekday) % 7)

    good_friday = _easter(year) - dt.timedelta(days=2)
    days = {
        observed(dt.date(year, 1, 1)),  # New Year's Day
        nth_weekday(1, 0, 3),  # MLK Jr Day
        nth_weekday(2, 0, 3),  # Washington's Birthday
        good_friday,
        last_weekday(5, 0),  # Memorial Day
        nth_weekday(9, 0, 1),  # Labor Day
        nth_weekday(11, 3, 4),  # Thanksgiving
        observed(dt.date(year, 12, 25)),  # Christmas
        observed(dt.date(year, 7, 4)),  # Independence Day
    }
    if year >= 2022:  # Juneteenth became a market holiday in 2022
        days.add(observed(dt.date(year, 6, 19)))
    return frozenset(days)


@functools.lru_cache(maxsize=64)
def _fallback_early_closes(year: int) -> frozenset[dt.date]:
    """Approximate 13:00 ET closes: day after Thanksgiving, Christmas Eve."""
    thanksgiving = _fallback_holidays(year)
    day_after = {d + dt.timedelta(days=1) for d in thanksgiving if d.month == 11}
    eve = dt.date(year, 12, 24)
    out = day_after
    if eve.weekday() < 5:
        out = out | {eve}
    return frozenset(out)


def _easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm -- needed for Good Friday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


@functools.lru_cache(maxsize=8)
def get_calendar(name: str = "XNYS") -> TradingCalendar:
    """Cached calendar accessor -- loading a full schedule is not cheap."""
    return TradingCalendar(name)
