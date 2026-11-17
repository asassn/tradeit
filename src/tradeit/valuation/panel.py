"""The valuation panel: four facts a reader can check, and no verdict.

**This replaces a one-word callout, by decision rather than by omission.**
The roadmap asks for *"undervalued / fairly valued / overvalued"* under the
condition that *"the callout is never shipped without the reasoning that
produced it --- a one-word label with no visible methodology is an opinion
wearing a badge"*. Building the reasoning first made three things clear, and
together they retire the label:

*A multiple has no absolute meaning.* For a stable company
``P/E ~= payout / (r - g)``, so the multiple is **determined by** growth and
risk. A P/E of 10 can be expensive and a P/E of 40 can be cheap, and any cut-
off that ignores growth and risk is comparing nothing. A label needs such a
cut-off; a ranking does not.

*Valuation is horizon-dependent.* Expensive barely matters over a swing
holding period and matters enormously over a retirement one. One label across
the three horizons would be the global buy/sell number the architecture exists
to prevent --- arriving in the one surface a person browses.

*The measured evidence here is negative.* `SIGNAL_SCOREBOARD` §40-41 tested
four fundamental signals point-in-time on this corpus --- earnings surprise,
gross profitability, asset growth, accruals --- and **none passed**. All four
are closed. Shipping a confident valuation verdict on a corpus where
fundamentals have not yet predicted anything would be asserting more than the
evidence carries.

So the panel reports what is measurable and lets the reader conclude:

1. **Free cash flow yield on enterprise value** --- cash, against the whole
   company's price, not earnings against the equity slice.
2. **Where that sits** in the security's own history.
3. **What the price implies** --- the perpetual growth the current yield is
   paying for, at required returns the reader picks, so the role of the
   discount rate is visible rather than buried in one number.
4. **Whether cheapness comes with deterioration** --- the value trap check.
   The cheapest decile of any screen is full of companies that are cheap for a
   reason.

P/E and P/B remain, as secondary and clearly so.

**What this buys beyond honesty: there is no threshold left to approve.** The
label needed one and could not derive it; every number here is measured or
supplied by the caller. The panel has no `UNAPPROVED` gate because it makes no
claim that needs one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from tradeit.research01.fundamental_signals import FundamentalHistory
from tradeit.valuation.cashflow import (
    EnterpriseValue,
    FreeCashFlow,
    enterprise_value,
    free_cash_flow,
    implied_growth,
)
from tradeit.valuation.multiples import (
    Multiple,
    PriceBar,
    Unresolved,
    price_to_book,
    price_to_earnings,
    rank_within,
)

__all__ = [
    "DEFAULT_DISCOUNT_RATES",
    "NOT_POINT_IN_TIME_BEFORE",
    "ImpliedGrowth",
    "QualityFlag",
    "ValuationPanel",
    "panel_for",
]

#: Required returns the implied-growth line is shown at. A **grid, not a
#: choice**: the point of showing several is that the reader sees how much the
#: answer depends on the rate, which a single number hides. Nothing here
#: asserts that any of them is correct, so none needs approving.
DEFAULT_DISCOUNT_RATES = (0.07, 0.09, 0.11)

#: Before roughly this year the corpus's fundamentals are **not**
#: point-in-time: `knowledge_time` begins in 2009 and the median gap from
#: `period_end` is 1,574 days, because SEC's XBRL datasets restate history
#: (`RESEARCH_01_DATA_DICTIONARY.md`). History reaching past it is not what a
#: reader could have seen at the time, and says so.
NOT_POINT_IN_TIME_BEFORE = dt.date(2013, 1, 1)

#: How far back the deterioration check looks, and the slack on finding a
#: comparable observation. Three years is long enough that one bad quarter
#: does not read as decline and short enough to still describe the company.
TREND_YEARS = 3
TREND_MATCH_DAYS = 180


@dataclass(frozen=True, slots=True)
class ImpliedGrowth:
    """What the price pays for, at one required return."""

    discount_rate: float
    growth: float

    @property
    def explanation(self) -> str:
        return (
            f"at a {self.discount_rate:.0%} required return, this price implies "
            f"{self.growth:.1%} free cash flow growth, perpetually"
        )


@dataclass(frozen=True, slots=True)
class QualityFlag:
    """One reason cheapness here might not be opportunity.

    Flags, not a score. A quality *score* would be a second number inviting
    the same compression the label was dropped to avoid; a reader can hold
    three sentences.
    """

    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class ValuationPanel:
    """One security's valuation evidence. Carries no verdict, by design."""

    security_id: int
    as_of: dt.date
    free_cash_flow: FreeCashFlow | None
    enterprise: EnterpriseValue | None
    #: Free cash flow over enterprise value. The headline number.
    fcf_yield: float | None
    #: Where that yield sits in the security's own history, 0 to 1. **Higher
    #: is cheaper** here, the opposite of the multiples below, because a yield
    #: inverts a multiple. Named rather than left for the reader to work out.
    fcf_yield_rank: float | None
    fcf_yield_history_size: int
    implied: tuple[ImpliedGrowth, ...]
    quality: tuple[QualityFlag, ...]
    #: Secondary. Kept because they are what most readers will look for first.
    multiples: tuple[Multiple, ...]
    multiple_ranks: dict[str, float]
    #: How many past observations each multiple's rank rests on. A percentile
    #: against three observations and one against three hundred are different
    #: claims, and the number is the only thing that distinguishes them.
    multiple_history_size: dict[str, int]
    unresolved: tuple[Unresolved, ...]
    caveats: tuple[str, ...]

    @property
    def explanations(self) -> tuple[str, ...]:
        """Every line a reader can check, in the order they would check it."""
        lines: list[str] = []
        if self.enterprise is not None:
            lines.append(self.enterprise.explanation)
        if self.free_cash_flow is not None:
            lines.append(self.free_cash_flow.explanation)
        if self.fcf_yield is not None:
            lines.append(
                f"free cash flow yield {self.fcf_yield:.2%} = free cash flow / enterprise value"
            )
        lines.extend(growth.explanation for growth in self.implied)
        lines.extend(multiple.explanation for multiple in self.multiples)
        return tuple(lines)


def _yield_on(price: PriceBar, history: FundamentalHistory, on: dt.date) -> float | None:
    """Free cash flow yield as it stood on one session, or ``None``."""
    cash = free_cash_flow(history, on)
    whole = enterprise_value(price, history, on)
    if not isinstance(cash, FreeCashFlow) or not isinstance(whole, EnterpriseValue):
        return None
    if whole.value <= 0:
        # Net cash exceeding market capitalisation. Real, and the yield it
        # produces is negative-denominator nonsense that would sort to one
        # extreme of any ranking.
        return None
    return cash.value / whole.value


def _deterioration(
    history: FundamentalHistory, on: dt.date, current: FreeCashFlow
) -> list[QualityFlag]:
    """Whether the cash behind the yield is shrinking.

    The value-trap check, and the reason the panel is not just a yield: a high
    yield on cash flow that is falling is a company being repriced, not a
    bargain. Compared against the same figure three years ago, computed the
    same way, so a change in method cannot masquerade as a change in business.
    """
    flags: list[QualityFlag] = []
    if current.value <= 0:
        flags.append(
            QualityFlag(
                "negative_free_cash_flow",
                f"trailing twelve-month free cash flow is {current.value:,.0f}; "
                "a yield on it is not a valuation",
            )
        )
        return flags
    then = on - dt.timedelta(days=365 * TREND_YEARS)
    past = free_cash_flow(history, then)
    if not isinstance(past, FreeCashFlow):
        return flags
    if (
        abs((past.period_end - (current.period_end - dt.timedelta(days=365 * TREND_YEARS))).days)
        > TREND_MATCH_DAYS
    ):
        return flags
    if past.value <= 0:
        return flags
    change = current.value / past.value - 1.0
    if change < 0:
        flags.append(
            QualityFlag(
                "free_cash_flow_declining",
                f"free cash flow is {abs(change):.0%} lower than {TREND_YEARS} years "
                f"ago ({past.value:,.0f} to {current.value:,.0f}); a high yield on "
                "falling cash is a repricing rather than a bargain",
            )
        )
    return flags


def panel_for(
    security_id: int,
    price: PriceBar,
    history: FundamentalHistory,
    on: dt.date,
    *,
    past_prices: Sequence[PriceBar] = (),
    discount_rates: Sequence[float] = DEFAULT_DISCOUNT_RATES,
) -> ValuationPanel:
    """Assemble one security's valuation panel as of ``on``.

    ``past_prices`` build the security's own history. Each is re-paired with
    the fundamentals that were usable *on that session*, so the history is a
    series of point-in-time observations rather than today's fundamentals
    projected backwards --- which would make every company that has grown look
    like it was once expensive, an artefact of the method and not a fact about
    the company.

    Sector-relative comparison is **owed and absent**. It needs the sector
    population resolved as of ``on``, which is a corpus-wide read rather than
    a per-security one. Stating it here rather than quietly shipping a two-way
    comparison where the roadmap asks for three.
    """
    cash = free_cash_flow(history, on)
    whole = enterprise_value(price, history, on)
    refused: list[Unresolved] = [
        result for result in (cash, whole) if isinstance(result, Unresolved)
    ]

    fcf = cash if isinstance(cash, FreeCashFlow) else None
    ev = whole if isinstance(whole, EnterpriseValue) else None
    fcf_yield: float | None = None
    if fcf is not None and ev is not None:
        if ev.value > 0:
            fcf_yield = fcf.value / ev.value
        else:
            refused.append(
                Unresolved(
                    "fcf_yield",
                    f"enterprise value is {ev.value:,.0f}: net cash exceeds market "
                    "capitalisation, and a yield on a non-positive denominator "
                    "sorts to an extreme of any ranking",
                )
            )

    usable_past = [past for past in past_prices if past.session_date < on]

    history_yields: list[float] = []
    if fcf_yield is not None:
        for past in usable_past:
            earlier = _yield_on(past, history, past.session_date)
            if earlier is not None:
                history_yields.append(earlier)
    yield_rank = rank_within(fcf_yield, history_yields) if fcf_yield is not None else None

    implied = (
        tuple(ImpliedGrowth(rate, implied_growth(fcf_yield, rate)) for rate in discount_rates)
        if fcf_yield is not None
        else ()
    )

    quality = _deterioration(history, on, fcf) if fcf is not None else []

    computed: list[Multiple] = []
    for multiple_result in (
        price_to_earnings(price, history, on),
        price_to_book(price, history, on),
    ):
        if isinstance(multiple_result, Multiple):
            computed.append(multiple_result)
        else:
            refused.append(multiple_result)
    ranks: dict[str, float] = {}
    sizes: dict[str, int] = {}
    for multiple in computed:
        population: list[float] = []
        for past in usable_past:
            # Named apart from the yield loop's `earlier` above: both are
            # "the same measure, one session back", and sharing the name made
            # mypy infer this one as a float.
            earlier_multiple = (
                price_to_earnings(past, history, past.session_date)
                if multiple.basis == "price_to_earnings"
                else price_to_book(past, history, past.session_date)
            )
            if isinstance(earlier_multiple, Multiple):
                population.append(earlier_multiple.value)
        rank = rank_within(multiple.value, population)
        if rank is not None:
            ranks[multiple.basis] = rank
            sizes[multiple.basis] = len(population)

    caveats: list[str] = []
    if price.basis != "raw":
        caveats.append(
            f"the price is on the '{price.basis}' basis; valuation needs the raw "
            "print, because an adjusted close is paired with per-share and "
            "balance-sheet figures reported in the share count of their own time"
        )
    earliest = min((past.session_date for past in usable_past), default=None)
    if earliest is not None and earliest < NOT_POINT_IN_TIME_BEFORE:
        caveats.append(
            f"the history reaches {earliest}, before roughly "
            f"{NOT_POINT_IN_TIME_BEFORE.year}, where this corpus's fundamentals are "
            "not point-in-time: SEC's XBRL datasets restate history, so those "
            "observations are not what a reader could have seen at the time"
        )
    if ev is not None and ev.debt_may_be_understated is not None:
        caveats.append(ev.debt_may_be_understated)
    caveats.append(
        "sector-relative comparison is not computed: it needs the sector "
        "population resolved as of this session, which is a corpus-wide read"
    )

    return ValuationPanel(
        security_id=security_id,
        as_of=on,
        free_cash_flow=fcf,
        enterprise=ev,
        fcf_yield=fcf_yield,
        fcf_yield_rank=yield_rank,
        fcf_yield_history_size=len(history_yields),
        implied=implied,
        quality=tuple(quality),
        multiples=tuple(computed),
        multiple_ranks=ranks,
        multiple_history_size=sizes,
        unresolved=tuple(refused),
        caveats=tuple(caveats),
    )
