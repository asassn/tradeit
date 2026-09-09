"""Feeding the backtester from ``research-01``.

The engine has run only on scripted bars until now. This is the adapter that
points it at 71 million real ones, and every decision in it is about not
quietly inventing an advantage the strategy never had.

Raw prices, never adjusted ones
--------------------------------

**The backtester trades the print, not the restatement.** Today's
split-adjusted history for a stock that split last year is not the history
anybody could have traded: the adjustment factor is derived from a split that
had not happened, so an adjusted series carries future information in every bar
before it. ADR-0005 is why this corpus stores raw prices and adjusts at read
time; this module is where that decision earns its keep.

The consequence is that a position held through a split must have its share
count changed, exactly as a real holder's would be. :meth:`splits_on` reports
the ratios and the engine applies them. Ignore that and every backtest crossing
a 2-for-1 shows a 50% loss on the ex-date.

Point-in-time, which the views deliberately are not
----------------------------------------------------

The data dictionary says plainly that ``v_prices`` returns *the current
belief* -- the latest revision, whenever it was learned -- and that anything
asking what was knowable on a past date must bound by ``knowledge_time``. A
backtester is the definitive such caller, so this reads the base table with the
bound applied, session by session.

**Measured, so the bound is not oversold:** across 116,262 raw bars in a
fourteen-security sample, every one carries ``knowledge_time == event_time ==
the session close``, and none was learned late. The bound therefore drops
nothing today. It is here because the corpus is still growing and a backfilled
correction to a 2015 bar must not become tradeable in 2015 the moment it
lands -- and because the corpus does hold 327,924 multi-revision keys, none of
which happened to fall in that sample.

A declared universe, because the alternative scans
---------------------------------------------------

``ix_security_price_pit`` leads with ``security_id``. A cross-sectional read --
one session, every security, which is exactly the shape a backtester wants --
cannot use it, and ``EXPLAIN QUERY PLAN`` reports ``SCAN`` over 71 million
rows. So the universe is declared up front and loaded per security, which uses
the index: **62,900 bars for 50 securities over five years in 0.52 seconds**,
measured.

That is a constraint worth stating rather than hiding, because it also matches
how a backtest should be specified: a universe chosen before the run, not
whatever the data happened to contain.

Two exclusions carried over from ``price_series``
--------------------------------------------------

Bars on days the market was closed, and bars outside the security's adjudicated
ticker interval. The second is the splice guard: a reused ticker has two
neighbours, and trading through the boundary is trading one company's prices as
another's.

**No result produced through this adapter is evidence of profitability.** The
survivorship gate reads ``SURVIVOR_BIASED`` against this corpus, so the
companies that failed are substantially absent. The machinery is real; the
numbers are not yet evidence.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.research01.series import adjudicated_window, known_splits
from tradeit.storage.tables import SecurityPriceFact

__all__ = ["CandidateSource", "CorpusSessionData", "SessionBars"]

#: What the engine asks a strategy for, given one session's prices. Injected
#: rather than implemented here: choosing what to buy is the strategy's job,
#: and a data adapter that also picked candidates would make the two
#: impossible to vary independently.
CandidateSource = Callable[[dt.date, Mapping[int, OhlcvBar]], Sequence[EntryCandidate]]

#: One session's raw prints, keyed by security.
SessionBars = dict[int, OhlcvBar]

_CLOSE = dt.time(21, 0)


def _session_close(day: dt.date) -> dt.datetime:
    """The instant a session's data becomes knowable.

    21:00 UTC, comfortably after a US close in either daylight regime. The
    bound only has to separate "today" from "tomorrow", and a bound that moved
    with the clock would make a backtest's results depend on when it was run.
    """
    return dt.datetime.combine(day, _CLOSE, tzinfo=dt.UTC)


@dataclass(slots=True)
class CorpusSessionData:
    """``SessionData`` over ``research-01``, loaded once for a declared universe.

    Bars are held as tuples and turned into :class:`OhlcvBar` per session on
    demand. Materialising a million validated models up front costs about a
    gigabyte for no benefit; a session's worth is a few hundred objects.
    """

    session: Session
    universe: tuple[int, ...]
    start: dt.date
    end: dt.date
    candidate_source: CandidateSource
    _bars: dict[dt.date, list[tuple[int, Decimal, Decimal, Decimal, Decimal, Decimal]]] = field(
        default_factory=dict, init=False
    )
    _splits: dict[dt.date, dict[int, Decimal]] = field(default_factory=dict, init=False)
    _sessions: list[dt.date] = field(default_factory=list, init=False)
    excluded_out_of_window: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.universe:
            raise ValueError(
                "a backtest needs a declared universe; loading every security would "
                "scan 71 million rows because the price index leads with security_id"
            )
        if self.end <= self.start:
            raise ValueError("end must be after start")
        self._load()

    # -- SessionData ---------------------------------------------------------

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]:
        return [day for day in self._sessions if start <= day <= end]

    def bars(self, session_date: dt.date) -> SessionBars:
        rows = self._bars.get(session_date, ())
        return {
            security_id: OhlcvBar(
                instrument_id=security_id,
                timeframe=Bartimeframe.D1,
                session_date=session_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                event_time=_session_close(session_date),
                knowledge_time=_session_close(session_date),
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
            )
            for security_id, open_, high, low, close, volume in rows
        }

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]:
        return self.candidate_source(session_date, self.bars(session_date))

    # -- what was loaded -----------------------------------------------------

    @property
    def bar_count(self) -> int:
        """Raw prints held, after both exclusions."""
        return sum(len(rows) for rows in self._bars.values())

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def split_count(self) -> int:
        return sum(len(ratios) for ratios in self._splits.values())

    # -- corporate actions ---------------------------------------------------

    def splits_on(self, session_date: dt.date) -> Mapping[int, Decimal]:
        """Split ratios with this ex-date, knowable on it.

        A 2-for-1 returns 2: the holder ends with twice the shares at half the
        price. Reported rather than applied, because only the engine knows what
        is held.
        """
        return self._splits.get(session_date, {})

    # -- loading -------------------------------------------------------------

    def _load(self) -> None:
        as_of = _session_close(self.end)
        found: set[dt.date] = set()
        for security_id in self.universe:
            window_start, window_end = adjudicated_window(self.session, security_id)
            for row in self._rows(security_id):
                day, open_, high, low, close, volume = row
                if window_start is not None and day < window_start:
                    self.excluded_out_of_window += 1
                    continue
                if window_end is not None and day >= window_end:
                    # valid_to is exclusive: the last owned day is the one before.
                    self.excluded_out_of_window += 1
                    continue
                self._bars.setdefault(day, []).append(
                    (security_id, open_, high, low, close, volume)
                )
                found.add(day)
            for split in known_splits(self.session, security_id, as_of=as_of):
                if self.start <= split.ex_date <= self.end and split.knowledge_time <= (
                    _session_close(split.ex_date)
                ):
                    self._splits.setdefault(split.ex_date, {})[security_id] = split.ratio
        self._sessions = sorted(found)

    def _rows(
        self, security_id: int
    ) -> list[tuple[dt.date, Decimal, Decimal, Decimal, Decimal, Decimal]]:
        """One security's raw prints, latest revision knowable at each session.

        The revision bound is per session rather than global: a correction
        published in 2024 to a 2015 bar did not exist in 2015, and a backtest
        that used it would be trading on a fact nobody had.
        """
        fact = SecurityPriceFact
        rows = self.session.execute(
            select(
                fact.session_date,
                fact.open,
                fact.high,
                fact.low,
                fact.close,
                fact.volume,
                fact.knowledge_time,
            )
            .where(
                fact.security_id == security_id,
                fact.adjustment_basis == "raw",
                fact.session_date >= self.start,
                fact.session_date <= self.end,
            )
            .order_by(fact.session_date, fact.knowledge_time)
        ).all()
        latest: dict[dt.date, tuple[dt.date, Decimal, Decimal, Decimal, Decimal, Decimal]] = {}
        for day, open_, high, low, close, volume, knowledge_time in rows:
            if knowledge_time > _session_close(day):
                # Learned after the session it describes. Real, and not
                # knowable then -- this is the point-in-time bound.
                continue
            latest[day] = (day, open_, high, low, close, volume)
        return list(latest.values())
