"""The as-of clock: the platform's single defence against look-ahead bias.

Every read of historical data goes through a :class:`AsOfClock`. The clock
carries one thing that matters -- ``as_of``, the instant a decision is being
made -- and repositories refuse to return rows whose ``knowledge_time`` is
after it.

The design goal is that leakage should require *deliberate* effort. There is no
"just give me the latest data" repository method; the only way to read is with a
clock, and the only way to move a backtest clock forward is
:meth:`AsOfClock.advance_to`, which refuses to go backwards.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

from tradeit.core.enums import TradingMode
from tradeit.errors import ClockError

UTC = dt.UTC


def utcnow() -> dt.datetime:
    """Timezone-aware current UTC time.

    Centralised so tests can monkeypatch a single symbol rather than chasing
    ``datetime.now`` calls across the codebase.
    """
    return dt.datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class AsOfClock:
    """An immutable point in time that bounds what may be known.

    Parameters
    ----------
    as_of:
        Timezone-aware UTC instant. Data with ``knowledge_time > as_of`` is
        invisible through any repository handed this clock.
    mode:
        Distinguishes a replayed historical instant from wall-clock trading.
        In :attr:`TradingMode.PAPER` and :attr:`TradingMode.LIVE` the clock is
        expected to track real time; a clock whose ``as_of`` is far in the past
        while in live mode is a bug, and :meth:`assert_fresh` catches it.
    """

    as_of: dt.datetime
    mode: TradingMode

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ClockError("AsOfClock.as_of must be timezone-aware (UTC expected)")
        if self.as_of.utcoffset() != dt.timedelta(0):
            raise ClockError(f"AsOfClock.as_of must be UTC, got offset {self.as_of.utcoffset()}")

    @classmethod
    def live(cls, mode: TradingMode = TradingMode.PAPER) -> AsOfClock:
        """A clock pinned to the current wall-clock instant."""
        if mode is TradingMode.BACKTEST:
            raise ClockError("use AsOfClock.at() for backtest clocks")
        return cls(as_of=utcnow(), mode=mode)

    @classmethod
    def at(cls, moment: dt.datetime, mode: TradingMode = TradingMode.BACKTEST) -> AsOfClock:
        """A clock pinned to an arbitrary instant, normalised to UTC."""
        if moment.tzinfo is None:
            raise ClockError("refusing to build a clock from a naive datetime")
        return cls(as_of=moment.astimezone(UTC), mode=mode)

    def advance_to(self, moment: dt.datetime) -> AsOfClock:
        """Return a new clock later in time. Never earlier.

        Rewinding a backtest clock is how a walk-forward loop silently starts
        re-reading the future, so it is an error rather than a warning.
        """
        if moment.tzinfo is None:
            raise ClockError("refusing to advance to a naive datetime")
        target = moment.astimezone(UTC)
        if target < self.as_of:
            raise ClockError(
                f"cannot rewind clock from {self.as_of.isoformat()} to {target.isoformat()}"
            )
        return replace(self, as_of=target)

    def knows(self, knowledge_time: dt.datetime) -> bool:
        """Whether a fact published at ``knowledge_time`` is visible."""
        if knowledge_time.tzinfo is None:
            raise ClockError("knowledge_time must be timezone-aware")
        return knowledge_time.astimezone(UTC) <= self.as_of

    def assert_fresh(self, tolerance: dt.timedelta = dt.timedelta(minutes=15)) -> None:
        """Guard against a stale clock driving real (paper or live) orders."""
        if self.mode is TradingMode.BACKTEST:
            return
        drift = utcnow() - self.as_of
        if drift > tolerance:
            raise ClockError(
                f"{self.mode} clock is {drift} behind wall time (tolerance {tolerance}); "
                "refusing to act on stale state"
            )

    @property
    def date(self) -> dt.date:
        """The UTC calendar date. Session-local dates come from the calendar."""
        return self.as_of.date()

    def __str__(self) -> str:
        return f"AsOfClock({self.as_of.isoformat()}, {self.mode})"
