"""Handing a detector the series it could have seen, and nothing more.

A scan replays sixteen years one session at a time. What a detector may see on
session *d* is defined by the point-in-time repository: the newest revision of
each bar whose ``knowledge_time`` is at or before *d*'s close. That is one query
per session, and 4,000 sessions by 78 instruments is 312,000 queries -- slow
enough that somebody will eventually replace it with "read the series once and
slice prefixes", which is **not the same thing**. A bar revised in 2019 appears
in a 2015 prefix, so the 2015 detector sees a number nobody had.

So this module does both, and *proves* which one it may use:

* :attr:`FeedMode.PER_SESSION_READ` — one repository call per session. Always
  correct.
* :attr:`FeedMode.VERIFIED_PREFIX` — one repository call, then prefixes. Used
  only when the snapshot is checked and found to contain, for this instrument,
  no revised bars and no session knowable at or before the session preceding
  it. Under exactly those conditions the two are identical, because the
  per-session query would return the same rows every time.

The precondition is measured against the database, not assumed from the
importer's behaviour. That is the whole point: an optimisation whose
justification is a belief about upstream code is an optimisation that will be
wrong the first time upstream changes.

Neither mode ever hands a detector a bar dated after the session under
evaluation, and both go through :class:`~tradeit.storage.repositories.BarRepository`
rather than reimplementing its revision logic.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from tradeit.core.calendar import TradingCalendar, get_calendar
from tradeit.core.clock import AsOfClock
from tradeit.core.enums import AdjustmentPolicy, Bartimeframe, TradingMode
from tradeit.core.models import OhlcvBar
from tradeit.storage import tables as t
from tradeit.storage.repositories import BarRepository

__all__ = ["CausalFeed", "FeedMode", "SessionView"]


class FeedMode(StrEnum):
    """How one instrument's causal series is being produced."""

    #: One point-in-time read per session. Always correct, always slow.
    PER_SESSION_READ = "per_session_read"
    #: One read, then prefixes — permitted only after the precondition below
    #: was verified against this snapshot.
    VERIFIED_PREFIX = "verified_prefix"

    @property
    def is_optimised(self) -> bool:
        return self is FeedMode.VERIFIED_PREFIX


@dataclass(frozen=True, slots=True)
class SessionView:
    """What a detector may see on one session."""

    session_date: dt.date
    knowledge_time: dt.datetime
    bars: Sequence[OhlcvBar]


@dataclass(frozen=True, slots=True)
class _Precondition:
    revised_sessions: int
    out_of_order_bars: int

    @property
    def prefix_is_equivalent(self) -> bool:
        return self.revised_sessions == 0 and self.out_of_order_bars == 0

    @property
    def reason(self) -> str:
        if self.prefix_is_equivalent:
            return "no revised bars, and every session knowable strictly after the one before"
        parts = []
        if self.revised_sessions:
            parts.append(f"{self.revised_sessions} session(s) carry more than one revision")
        if self.out_of_order_bars:
            parts.append(
                f"{self.out_of_order_bars} session(s) became knowable at or before the "
                "session preceding them"
            )
        return "; ".join(parts)


class CausalFeed:
    """Yields, session by session, exactly what a detector could have seen."""

    def __init__(
        self,
        session: Session,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
        adjustment: AdjustmentPolicy = AdjustmentPolicy.NONE,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self.session = session
        self.timeframe = timeframe
        # NONE by default: the imported package already declares its own
        # adjustment policy, and applying a second one on read would adjust an
        # adjusted series. Whether the prices are raw or split-adjusted is a
        # property of the snapshot, recorded there, and the scan reports it
        # rather than silently changing it.
        self.adjustment = adjustment
        self.calendar = calendar or get_calendar()
        self.bars = BarRepository(session)

    # -- the knowledge boundary ---------------------------------------------

    def calendar_close(self, session_date: dt.date) -> dt.datetime:
        """The exchange's own close for a session, from the project's calendar."""
        if self.calendar.is_session(session_date):
            return self.calendar.session(session_date).close_utc
        return dt.datetime.combine(session_date, dt.time(23, 59), tzinfo=dt.UTC)

    def clock_for(self, session_date: dt.date, knowledge_time: dt.datetime) -> AsOfClock:
        """The instant at which session ``session_date`` may be evaluated.

        Taken from the data — the ``knowledge_time`` the snapshot gives that
        session's bar — rather than from the calendar alone. The first version
        of this used the exchange close and it was wrong in a way worth
        recording: the importer places every daily bar at a fixed 16:00 New
        York, which on an early-close session is three hours *after* the real
        close, so the bar for 2018-07-03 was invisible on 2018-07-03 and the
        scan crashed rather than evaluating it.

        The stamped instant is the honest answer to "when did this bar exist?",
        which is exactly the question a causal read has to ask. Bounded below by
        the exchange close so a snapshot that stamps a bar *before* its session
        ended cannot pull the evaluation forward into the session itself.
        """
        return AsOfClock(
            as_of=max(knowledge_time, self.calendar_close(session_date)),
            mode=TradingMode.BACKTEST,
        )

    # -- precondition --------------------------------------------------------

    def knowledge_times(self, instrument_id: int, as_of: dt.datetime) -> dict[dt.date, dt.datetime]:
        """When each session's bar became knowable, newest revision per session.

        Read once per instrument and reused for both the precondition and the
        walk, because both need the same answer and asking twice invites them
        to disagree.
        """
        bar = t.OhlcvBar
        rows = self.session.execute(
            select(bar.session_date, bar.knowledge_time)
            .where(
                and_(
                    bar.instrument_id == instrument_id,
                    bar.timeframe == self.timeframe.value,
                    bar.knowledge_time <= as_of,
                )
            )
            .order_by(bar.session_date, bar.knowledge_time)
        ).all()
        latest: dict[dt.date, dt.datetime] = {}
        for session_date, knowledge_time in rows:
            latest[session_date] = knowledge_time
        return latest

    def _precondition(self, instrument_id: int, as_of: dt.datetime) -> _Precondition:
        """The two conditions under which prefixing a single read is equivalent.

        Evaluated in Python rather than in SQL: the comparison needs the
        *exchange's* close for each date, which lives in the trading calendar
        and not in the database, and the portable SQL approximations are either
        wrong or so loose they would pass a snapshot that genuinely leaks. One
        instrument's bars are a few thousand rows, so the exact answer costs
        nothing worth saving.
        """
        bar = t.OhlcvBar
        rows = self.session.execute(
            select(bar.session_date, bar.knowledge_time)
            .where(
                and_(
                    bar.instrument_id == instrument_id,
                    bar.timeframe == self.timeframe.value,
                    bar.knowledge_time <= as_of,
                )
            )
            .order_by(bar.session_date)
        ).all()

        seen: set[dt.date] = set()
        revised: set[dt.date] = set()
        latest: dict[dt.date, dt.datetime] = {}
        for session_date, knowledge_time in rows:
            if session_date in seen:
                revised.add(session_date)
            seen.add(session_date)
            latest[session_date] = max(latest.get(session_date, knowledge_time), knowledge_time)

        # A later session knowable at or before an earlier session's evaluation
        # instant is a genuine leak: the earlier session's read would return a
        # bar from the future. That is the condition prefixing has to rule out,
        # and it is the one a backfill stamping every row with the same
        # timestamp would violate.
        ordered = sorted(latest)
        out_of_order = 0
        for earlier, later in pairwise(ordered):
            if latest[later] <= self.clock_for(earlier, latest[earlier]).as_of:
                out_of_order += 1
        return _Precondition(revised_sessions=len(revised), out_of_order_bars=out_of_order)

    def mode_for(self, instrument_id: int, as_of: dt.datetime) -> tuple[FeedMode, str]:
        """Decide how this instrument's series may be produced, and say why."""
        precondition = self._precondition(instrument_id, as_of)
        if precondition.prefix_is_equivalent:
            return FeedMode.VERIFIED_PREFIX, precondition.reason
        return FeedMode.PER_SESSION_READ, precondition.reason

    # -- the walk ------------------------------------------------------------

    def sessions(
        self,
        instrument_id: int,
        *,
        as_of: dt.datetime,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> list[dt.date]:
        """Sessions this instrument has a bar for, oldest first.

        Driven by the bars rather than by the calendar, so a scan never
        evaluates a session on which the security did not trade — pre-IPO,
        post-delisting, or inside a suspension.
        """
        bar = t.OhlcvBar
        conditions = [
            bar.instrument_id == instrument_id,
            bar.timeframe == self.timeframe.value,
            bar.knowledge_time <= as_of,
        ]
        if start is not None:
            conditions.append(bar.session_date >= start)
        if end is not None:
            conditions.append(bar.session_date <= end)
        rows = self.session.scalars(
            select(bar.session_date).where(and_(*conditions)).distinct().order_by(bar.session_date)
        ).all()
        return list(rows)

    def walk(
        self,
        instrument_id: int,
        session_dates: Sequence[dt.date],
        *,
        mode: FeedMode,
        as_of: dt.datetime,
        floor: dt.date | None = None,
    ) -> Iterator[SessionView]:
        """Yield one view per session, in chronological order.

        Chronological is not a convenience: the pattern tracker and the breakout
        monitor both carry state across sessions, and feeding them out of order
        would mint identities against a history that never happened.

        ``floor`` is the analytical episode's first session. Bars before it are
        removed from every view, so a rolling average, an ATR, a pivot search
        and a pattern boundary all begin at the episode rather than reaching
        back across a structural break into a different listing's prices. It is
        a hard floor and not a hint: see :mod:`tradeit.scanning.episodes`.
        """
        if not session_dates:
            return
        known = self.knowledge_times(instrument_id, as_of)

        if mode is FeedMode.VERIFIED_PREFIX:
            clock = AsOfClock(as_of=as_of, mode=TradingMode.BACKTEST)
            everything = self.bars.history(
                clock,
                instrument_id,
                timeframe=self.timeframe,
                adjustment=self.adjustment,
            )
            index = {bar.session_date: position for position, bar in enumerate(everything)}
            for session_date in session_dates:
                cut = index.get(session_date)
                if cut is None:
                    # In our session list but not in the repository's read — a
                    # quality flag the repository excludes. Skipped rather than
                    # evaluated against a series that stops short of it.
                    continue
                window = everything[: cut + 1]
                if floor is not None:
                    window = [bar for bar in window if bar.session_date >= floor]
                if not window:
                    continue
                yield SessionView(
                    session_date=session_date,
                    knowledge_time=self.clock_for(
                        session_date, known.get(session_date, as_of)
                    ).as_of,
                    bars=window,
                )
            return

        for session_date in session_dates:
            stamped = known.get(session_date)
            if stamped is None:
                continue
            clock = self.clock_for(session_date, stamped)
            bars = self.bars.history(
                clock,
                instrument_id,
                timeframe=self.timeframe,
                adjustment=self.adjustment,
            )
            # The repository bounds by knowledge_time, which is the right rule.
            # This second bound is the belt to its braces: a snapshot whose
            # knowledge times are estimated rather than reported can stamp two
            # sessions identically, and a bar from the future must not reach a
            # detector because two rows happen to share a timestamp.
            bars = [
                bar
                for bar in bars
                if bar.session_date <= session_date and (floor is None or bar.session_date >= floor)
            ]
            if not bars or bars[-1].session_date != session_date:
                # The session's own bar is not visible at its own evaluation
                # instant, so nothing causal can be said about that session. It
                # is skipped rather than evaluated against a stale series.
                continue
            yield SessionView(session_date=session_date, knowledge_time=clock.as_of, bars=bars)
