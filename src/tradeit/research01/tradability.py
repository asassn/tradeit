"""Could this security actually have been bought on this day?

A corpus that holds every listed security holds a great many that nobody could
trade. Measured 2026-09-07 across 30.4 million adjusted bars: **12.8% close at
exactly the previous session's price and 5.8% carry zero volume**, evenly across
every era. That is not corruption — it is what a full universe including
microcaps and OTC names looks like — and it means a backtest run over the whole
corpus buys things that were never for sale.

**This is a precondition, not a refinement.** A strategy tested on untradeable
names does not produce an optimistic result, it produces a meaningless one: the
fills never existed, so neither did the returns. The screen therefore *refuses*
rather than scoring, in the same spirit as the portfolio veto — a discount can
be outvoted by a good enough signal and a refusal cannot.

Three independent reasons, kept apart
--------------------------------------

Each fails for its own cause and a name can fail one while passing the others,
so there is no single tradability number here:

* **No trading at all.** Zero volume means no shares changed hands. Whatever the
  close says, nobody bought at it.
* **Too thin to fill.** A position that is a large fraction of a day's dollar
  volume moves the price it is trying to get. The threshold belongs to a
  strategy, not to this module, so it is a parameter with no default that
  pretends to be neutral.
* **Below a price floor.** Sub-dollar quotes have a bid-ask spread that is a
  large fraction of the price, so a backtest filling at the close overstates
  every round trip. This is where survivorship and tradability meet: failing
  companies spend their last months here.

*Not decided here:* what the thresholds should be. They are strategy
parameters, and a module that chose them would be making a strategy decision
while looking like plumbing.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "Tradability",
    "TradabilityRule",
    "UntradableReason",
    "assess_tradability",
    "tradable_days",
]


class UntradableReason(StrEnum):
    """Why a day could not have been traded. Absolute, never weighted."""

    #: No shares changed hands. The close is a quote, not a transaction.
    NO_VOLUME = "no_volume"
    #: Dollar volume too small for the position this rule contemplates.
    TOO_THIN = "too_thin"
    #: Price below the floor where the spread stops being a rounding error.
    BELOW_PRICE_FLOOR = "below_price_floor"
    #: The bar is flat *and* unvolumed — a carried-forward quote rather than a
    #: session. Reported separately from NO_VOLUME because a flat bar that did
    #: trade is real and this one is not.
    STALE_QUOTE = "stale_quote"


@dataclass(frozen=True, slots=True)
class TradabilityRule:
    """The thresholds a strategy asserts, with no neutral defaults.

    Every field is required. A default here would be a strategy parameter
    hiding in a signature, which ADR-0008 exists to prevent.
    """

    min_price: Decimal
    min_dollar_volume: Decimal
    #: Reject a bar that neither moved nor traded. Almost always wanted; still
    #: stated, because "almost always" is not "always" and a caller studying
    #: illiquidity itself wants them.
    reject_stale_quotes: bool


@dataclass(frozen=True, slots=True)
class Tradability:
    """One day's verdict, with every reason it failed rather than the first."""

    session_date: dt.date
    reasons: tuple[UntradableReason, ...]

    @property
    def tradable(self) -> bool:
        return not self.reasons


def assess_tradability(
    *,
    session_date: dt.date,
    close: Decimal,
    volume: Decimal,
    previous_close: Decimal | None,
    rule: TradabilityRule,
) -> Tradability:
    """Every reason this day could not have been traded, not merely the first.

    Returning all of them matters for diagnosis: a universe that empties under
    a price floor is a different problem from one that empties under a volume
    floor, and stopping at the first reason hides which.
    """
    reasons: list[UntradableReason] = []
    if volume <= 0:
        reasons.append(UntradableReason.NO_VOLUME)
    if close < rule.min_price:
        reasons.append(UntradableReason.BELOW_PRICE_FLOOR)
    if close * volume < rule.min_dollar_volume:
        reasons.append(UntradableReason.TOO_THIN)
    if (
        rule.reject_stale_quotes
        and volume <= 0
        and previous_close is not None
        and close == previous_close
    ):
        reasons.append(UntradableReason.STALE_QUOTE)
    return Tradability(session_date=session_date, reasons=tuple(reasons))


def tradable_days(
    bars: Sequence[tuple[dt.date, Decimal, Decimal]], rule: TradabilityRule
) -> tuple[Tradability, ...]:
    """Assess a series in order, so each day sees its own predecessor.

    ``bars`` is ``(session_date, close, volume)`` in ascending date order. The
    first bar has no predecessor and therefore cannot be judged stale — an
    absent predecessor is not evidence of a repeated price, and treating it as
    one would refuse the first day of every series.
    """
    out: list[Tradability] = []
    previous: Decimal | None = None
    for session_date, close, volume in bars:
        out.append(
            assess_tradability(
                session_date=session_date,
                close=close,
                volume=volume,
                previous_close=previous,
                rule=rule,
            )
        )
        previous = close
    return tuple(out)
