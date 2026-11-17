"""Price multiples, point-in-time, with every input they were computed from.

Pure functions over already-fetched facts, following
:mod:`tradeit.research01.fundamental_signals`: nothing here reads a database,
so every point-in-time rule can be tested exactly.

**Three read rules this module encodes, each of which returns a confident wrong
number if broken.**

*The price must be the RAW print, never the adjusted one.* `v_prices` is split-
and dividend-adjusted and says of itself that it is *"the current belief"*;
`v_prices_raw` is *"what a trader saw on the day"*. A multiple divides a price
by a per-share figure that was reported in the share count of its own time, so
pairing an adjusted price with an as-reported EPS gets the answer wrong by
exactly every split since --- a 2-for-1 halves the numerator and leaves the
denominator alone, printing a company at half its true multiple. The functions
below take the price as an argument and cannot check this, so
:class:`PriceBar` records which basis was used and the evidence carries it.

*A value is what was FIRST filed, and usable only when `known < on`.* Inherited
whole from `fundamental_signals`: `knowledge_time` is stamped at midnight and a
filing can arrive after the close.

*NULL is not zero.* The corpus has 2,664,473 rows (2.895%) with a NULL value,
clustered in concepts a filing tags whether or not there is an amount. A reader
that treats one as zero puts a company with unknown equity at the bottom of
every quality ranking. Facts reaching this module have already been filtered,
and the functions below refuse rather than substitute.

**Negative denominators are UNRESOLVED, not negative multiples.** A loss-making
company has no meaningful P/E: the arithmetic yields a negative number that
sorts *below* every cheap company in any ascending ranking, so the most
distressed names in the universe come out looking like the best value. This is
the single most common way a screen built on multiples goes wrong, and it is
refused here rather than left to every caller to remember.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from tradeit.research01.fundamental_signals import Fact, FundamentalHistory

__all__ = [
    "BALANCE_FRESH_DAYS",
    "EARNINGS_METRIC",
    "EQUITY_METRIC",
    "SHARES_METRIC",
    "TTM_QUARTERS",
    "Multiple",
    "PriceBar",
    "Unresolved",
    "price_to_book",
    "price_to_earnings",
    "rank_within",
    "shares_outstanding",
    "trailing_twelve_months",
]

#: Quarters in a trailing-twelve-month figure.
TTM_QUARTERS = 4

#: The US-GAAP tags this module reads. Spelled out rather than guessed at the
#: call site: these are the exact strings the SEC Financial Statement Data Sets
#: use, and a near-miss returns no facts rather than an error.
EARNINGS_METRIC = "NetIncomeLoss"
EQUITY_METRIC = "StockholdersEquity"
SHARES_METRIC = "CommonStockSharesOutstanding"

#: A trailing-twelve-month figure built from quarters spanning more than this
#: is not twelve months. Four quarters nominally span 365 days; the slack
#: covers 52/53-week fiscal calendars and the odd shifted period end without
#: admitting a gap where a quarter is missing entirely.
TTM_SPAN_DAYS = (330, 400)

#: A balance-sheet figure older than this is not describing the company whose
#: price is in the numerator. Fifteen months, matching the annual staleness
#: rule `fundamental_signals` already applies, because a company that has
#: stopped filing is the case both rules exist to catch.
BALANCE_FRESH_DAYS = 456


@dataclass(frozen=True, slots=True)
class PriceBar:
    """The price side of a multiple, with the basis it was read on.

    ``basis`` is carried rather than assumed because the whole module is wrong
    if it is ``"total"``: see this file's header. It is a string and not a
    check, since the functions here never see the database --- but it travels
    into the evidence, so a wrong one is visible rather than silent.
    """

    session_date: dt.date
    close: float
    basis: str = "raw"


@dataclass(frozen=True, slots=True)
class Unresolved:
    """Why a multiple could not be computed.

    A first-class answer, not an error. The project's rule is that a number
    which cannot be traced to evidence is worse than no number, because it will
    be acted on; this is the shape that takes here.
    """

    basis: str
    reason: str


@dataclass(frozen=True, slots=True)
class Multiple:
    """One multiple and everything it was computed from.

    **Every input is carried, which is the point.** The roadmap forbids
    shipping a valuation label without the reasoning that produced it, and the
    cheapest way to honour that is to make the reasoning inseparable from the
    number: there is no constructor here that takes a value alone.
    """

    basis: str
    value: float
    price: float
    price_session: dt.date
    price_basis: str
    denominator: float
    denominator_metric: str
    denominator_period_end: dt.date
    #: The filing date the denominator first became usable on.
    denominator_known: dt.date

    @property
    def explanation(self) -> str:
        """One line a person can check, in the order they would check it."""
        return (
            f"{self.basis} {self.value:,.2f} = close {self.price:,.4f} "
            f"({self.price_basis}, {self.price_session}) / "
            f"{self.denominator_metric} {self.denominator:,.4f} per share "
            f"(period ending {self.denominator_period_end}, "
            f"first filed {self.denominator_known})"
        )


def trailing_twelve_months(history: FundamentalHistory, metric: str, on: dt.date) -> Fact | None:
    """The last four stand-alone quarters of ``metric`` usable on ``on``.

    Returns a :class:`Fact` whose ``known`` is the **latest** of the four, so a
    reader cannot use the sum before every part of it was public. Fewer than
    four quarters returns ``None`` rather than annualising three, because a
    three-quarter figure scaled up is a forecast wearing a measurement's
    clothes.

    The span is checked because four consecutive *rows* are not necessarily
    four consecutive *quarters*: a company that skipped a filing, or whose
    history has a hole, would otherwise have a stale quarter silently stand in
    for a missing one and produce a figure spanning two years.
    """
    quarters = history.quarters_known_by(metric, on)
    if len(quarters) < TTM_QUARTERS:
        return None
    window = quarters[-TTM_QUARTERS:]
    earliest, latest = window[0], window[-1]
    span = (latest.period_end - earliest.period_end).days
    low, high = TTM_SPAN_DAYS
    # The span measures end-to-end across four periods, so it is three
    # quarters wide, not four: ~273 days nominal. Compared against a
    # year-wide window because the fourth quarter's own length completes it.
    if not low <= span + 92 <= high:
        return None
    return Fact(
        metric=metric,
        period_end=latest.period_end,
        duration=TTM_QUARTERS,
        value=sum(q.value for q in window),
        known=max(q.known for q in window),
    )


def shares_outstanding(history: FundamentalHistory, on: dt.date) -> Fact | None:
    """Shares outstanding as a balance, fresh enough to describe the company."""
    shares = history.instant_known_by(SHARES_METRIC, on)
    if shares is None or shares.value <= 0:
        return None
    if (on - shares.known).days > BALANCE_FRESH_DAYS:
        return None
    return shares


def price_to_earnings(
    price: PriceBar, history: FundamentalHistory, on: dt.date
) -> Multiple | Unresolved:
    """Price over trailing-twelve-month earnings per share.

    Earnings are summed from four stand-alone quarters rather than read from
    ``EarningsPerShareDiluted`` directly. That tag exists and would be one
    read, and it is the wrong one here: a per-share figure is denominated in
    the share count of its own filing, so a security that split between the
    oldest quarter and today carries a mixture of share counts across the four.
    Summing dollar earnings and dividing once by a current share count keeps a
    single denominator, which is also the only way the result can be compared
    with the same security's own history across a split.
    """
    earnings = trailing_twelve_months(history, EARNINGS_METRIC, on)
    if earnings is None:
        return Unresolved(
            "price_to_earnings",
            f"no four consecutive quarters of {EARNINGS_METRIC} were usable on {on}",
        )
    if earnings.value <= 0:
        return Unresolved(
            "price_to_earnings",
            (
                f"trailing twelve-month {EARNINGS_METRIC} is {earnings.value:,.0f}; "
                "a multiple on non-positive earnings sorts the most distressed "
                "names as the cheapest"
            ),
        )
    shares = shares_outstanding(history, on)
    if shares is None:
        return Unresolved(
            "price_to_earnings",
            f"no {SHARES_METRIC} balance was usable and fresh on {on}",
        )
    per_share = earnings.value / shares.value
    return Multiple(
        basis="price_to_earnings",
        value=price.close / per_share,
        price=price.close,
        price_session=price.session_date,
        price_basis=price.basis,
        denominator=per_share,
        denominator_metric=f"{EARNINGS_METRIC} (trailing twelve months)",
        denominator_period_end=earnings.period_end,
        denominator_known=max(earnings.known, shares.known),
    )


def price_to_book(
    price: PriceBar, history: FundamentalHistory, on: dt.date
) -> Multiple | Unresolved:
    """Price over shareholders' equity per share.

    Both inputs are balances, so both are read as instants and both must be
    fresh. Negative equity is refused for the same reason negative earnings
    are: the arithmetic succeeds and the ranking is inverted.
    """
    equity = history.instant_known_by(EQUITY_METRIC, on)
    if equity is None:
        return Unresolved("price_to_book", f"no {EQUITY_METRIC} was usable on {on}")
    if (on - equity.known).days > BALANCE_FRESH_DAYS:
        return Unresolved(
            "price_to_book",
            f"the latest {EQUITY_METRIC} was first filed {equity.known}, too stale for {on}",
        )
    if equity.value <= 0:
        return Unresolved(
            "price_to_book",
            (
                f"{EQUITY_METRIC} is {equity.value:,.0f}; a multiple on "
                "non-positive book value sorts an insolvent balance sheet as cheap"
            ),
        )
    shares = shares_outstanding(history, on)
    if shares is None:
        return Unresolved(
            "price_to_book",
            f"no {SHARES_METRIC} balance was usable and fresh on {on}",
        )
    per_share = equity.value / shares.value
    return Multiple(
        basis="price_to_book",
        value=price.close / per_share,
        price=price.close,
        price_session=price.session_date,
        price_basis=price.basis,
        denominator=per_share,
        denominator_metric=f"{EQUITY_METRIC} per share",
        denominator_period_end=equity.period_end,
        denominator_known=max(equity.known, shares.known),
    )


def rank_within(value: float, population: Sequence[float]) -> float | None:
    """Where ``value`` sits in ``population``, as a fraction from 0 to 1.

    The fraction of the population **at or below** ``value``, so 0.1 reads as
    *cheaper than 90% of its own history* for a multiple where lower is
    cheaper. Returns ``None`` for an empty population rather than 0.5, because
    a midpoint returned for "nothing to compare against" is the fabricated
    certainty this project refuses everywhere else: it reads as *typical*.

    No minimum population is imposed here. That is a judgement about how much
    history makes a comparison worth showing, it belongs with the label's own
    unapproved threshold, and inventing it in a helper would hide it.
    """
    if not population:
        return None
    at_or_below = sum(1 for other in population if other <= value)
    return at_or_below / len(population)
