"""Resolving a ticker to a security **as of a date**, which is the only way.

`RESEARCH_01_DATA_DICTIONARY.md` §0.2 states the rule this module exists to
keep: *"Resolution is per date, never per ticker. Tickers are reused: two
unrelated companies can hold `ACME` in different decades, and asking 'which
security is ACME' has no answer."*

The forward capture resolved tickers with `scripts/backtest_research01._universe`,
which asks `where alias_value in (...) order by valid_from` and keeps the first
row per ticker — **no date filter, and therefore the earliest holder**. Measured
2026-10-22: 1,471 of the corpus's 17,342 tickers are held by more than one
security, so for 8.5% of tickers that query answers a question nobody asked.

It did not corrupt the current record. Of the 48 names in the forward universe,
46 resolve identically either way, and the two that differ -- `ABAX` and `AUTO`
-- resolve to **no holder at all** today, which is the defect this module turns
into a refusal rather than a guess:

- `ABAX` last held its ticker to 2018-08-10 (Abaxis; `security_id` 3377)
- `AUTO` last held its ticker to 2022-09-12 (`security_id` 4535, and 1914 before
  it to 2013-05-01 -- a reused ticker, and the old resolver returned 1914)

Both had been asked about daily and recorded as `empty`. **That row is a
delisting claim.** The forward record's whole value is that a silence is an
*observation* -- we asked on a dated day and got nothing -- and a silence about
a ticker nobody has held since 2018 is a fact about neither the vendor nor the
market. Left alone it would have accumulated two false exits per day, dated
2026, in the one record built specifically so survivorship cannot go wrong.

## Why unresolved symbols are dropped from the ask, not recorded with a status

The alternative was to keep asking and write the observation under a distinct
status, which fits the capture's standing rule that *every* outcome is written
down including the boring ones. Rejected: that rule is about observations, and
a ticker we chose not to ask about is not an observation. Giving it a row makes
the reader's job harder for no gain -- they must now know which statuses mean
"the market was quiet" and which mean "we were asking a meaningless question" --
and the first reader to miss the distinction gets exactly the false exit this
was written to prevent. The exclusions are reported to the operator instead,
where a universe problem belongs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["TickerAlias", "UnresolvedReason", "resolve_as_of"]


class UnresolvedReason(StrEnum):
    """Why a ticker could not be resolved. **Never a default id.**"""

    #: No security held the ticker on the date asked about. The usual cause is
    #: a universe built from a historical window: a ticker that was rich in
    #: 2010-2019 data may have been gone for years.
    NO_HOLDER_AT_DATE = "no_holder_at_date"
    #: Two or more securities held it on that date. The corpus contradicts
    #: itself and `UNRESOLVED` is the honest answer; picking one would be a
    #: coin flip recorded as a fact.
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TickerAlias:
    """One `symbol_aliases` row. ``valid_to`` is **exclusive**, per §0.2.

    The last day a security owns a ticker is ``valid_to - 1 day``, and ``None``
    means open-ended -- the SEC publishes it as current -- **not** that the end
    is unknown. Getting this backwards is an off-by-one that silently hands you
    the wrong company for exactly one day per handover.
    """

    ticker: str
    security_id: int
    valid_from: dt.date
    valid_to: dt.date | None

    def holds_on(self, day: dt.date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or self.valid_to > day)


def resolve_as_of(
    aliases: Iterable[TickerAlias],
    tickers: Iterable[str],
    *,
    as_of: dt.date,
) -> tuple[dict[str, int], dict[str, UnresolvedReason]]:
    """Map each ticker to the security holding it on ``as_of``, or say why not.

    Pure: takes rows rather than a session, so the rule can be tested against a
    reused ticker without a corpus. Returns ``(resolved, unresolved)`` and
    **never puts a ticker in both**, so a caller cannot act on a guess by
    reading only the first return value.
    """
    by_ticker: dict[str, list[TickerAlias]] = {}
    for alias in aliases:
        by_ticker.setdefault(alias.ticker, []).append(alias)

    resolved: dict[str, int] = {}
    unresolved: dict[str, UnresolvedReason] = {}
    for ticker in tickers:
        holders = {a.security_id for a in by_ticker.get(ticker, ()) if a.holds_on(as_of)}
        if len(holders) == 1:
            resolved[ticker] = holders.pop()
        elif not holders:
            unresolved[ticker] = UnresolvedReason.NO_HOLDER_AT_DATE
        else:
            unresolved[ticker] = UnresolvedReason.AMBIGUOUS
    return resolved, unresolved
