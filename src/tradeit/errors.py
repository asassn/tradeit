"""Exception hierarchy.

Every error the platform raises deliberately descends from :class:`TradeitError`
so that a job runner can distinguish "our invariant was violated" from "the
process is broken".
"""

from __future__ import annotations


class TradeitError(Exception):
    """Base class for all platform errors."""


class ConfigError(TradeitError):
    """Invalid or unsafe configuration."""


class ClockError(TradeitError):
    """Misuse of the as-of clock: naive datetimes, rewinds, staleness."""


class LookaheadError(TradeitError):
    """A read was attempted for data the clock is not allowed to see.

    This is raised rather than silently filtered when the caller asked for a
    specific fact by identity, because returning ``None`` would let a bug hide
    as a missing-data branch.
    """


class DataError(TradeitError):
    """Malformed, contradictory, or unusable market/fundamental data."""


class ProviderError(TradeitError):
    """A data provider failed or returned something unusable."""


class UniverseError(TradeitError):
    """Instrument identity or universe-membership resolution failed."""


class SafetyInterlockError(TradeitError):
    """An operation was blocked by a safety interlock (e.g. live trading)."""
