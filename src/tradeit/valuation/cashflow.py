"""Free cash flow, and the enterprise value it is a yield on.

**Why this module exists beside `multiples.py`.** A P/E divides price by an
accounting opinion. Free cash flow over enterprise value divides the *whole
company's* price by the cash it actually produced, and the two differences are
not cosmetic:

*Cash rather than earnings.* Earnings are the output of accrual choices ---
when revenue is recognised, how assets are depreciated, what is capitalised.
Cash from operations minus capital spending is what was left over, and it is
far harder to present favourably.

*Enterprise value rather than price.* Price ignores the balance sheet. Two
companies on the same P/E, one debt-free and one levered three times, are not
equally priced: the levered one's equity is a thin slice of a bigger, riskier
whole. ``EV = market cap + debt - cash`` prices the whole thing.

**The failure this module is built around, and it is a one-way one.** Debt is
tagged a dozen different ways in XBRL, and a filer using a tag we do not read
looks debt-free. Treating an absent tag as zero debt *understates* enterprise
value, which *overstates* the yield, which makes the most levered companies in
the universe screen as the best value --- the same inversion that negative
earnings produce in `multiples.py`, arriving by a different door.

Measured over 40 corpus securities on 2026-11-17: operating cash flow resolves
for 39, cash for 39, capital expenditure for 29, and a long-term debt tag for
26. So a quarter of the sample would silently become debt-free. **Debt must be
positively established or the enterprise value is UNRESOLVED**, and the tags
that established it travel with the answer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tradeit.research01.fundamental_signals import FundamentalHistory
from tradeit.valuation.multiples import (
    BALANCE_FRESH_DAYS,
    PriceBar,
    Unresolved,
    shares_outstanding,
)

__all__ = [
    "CAPEX_METRIC",
    "CASH_METRICS",
    "CURRENT_DEBT_METRICS",
    "LONG_TERM_DEBT_METRICS",
    "OPERATING_CASH_METRICS",
    "EnterpriseValue",
    "FreeCashFlow",
    "enterprise_value",
    "free_cash_flow",
    "implied_growth",
]

#: Operating cash flow, in preference order. The continuing-operations variant
#: is the fallback rather than the primary: it excludes discontinued segments,
#: which is the better number for forecasting and the wrong one for "what did
#: this company's operations produce", and only 29 of 40 securities carry it.
OPERATING_CASH_METRICS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)

#: Capital expenditure. Reported as a positive payment, so it is subtracted.
CAPEX_METRIC = "PaymentsToAcquirePropertyPlantAndEquipment"

#: Cash and equivalents. Marketable securities are **not** added: they are a
#: judgement about which holdings are cash-like, they are tagged
#: inconsistently, and adding them lowers enterprise value --- the same
#: direction as understating debt, so the same caution applies.
CASH_METRICS = ("CashAndCashEquivalentsAtCarryingValue",)

#: Long-term debt, in preference order. ``LongTermDebt`` is the total where a
#: filer reports one; the non-current tag is the more common split. They are
#: never summed, which would double count.
LONG_TERM_DEBT_METRICS = (
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
)

#: The current portion, same rule: the first that resolves wins, never a sum.
CURRENT_DEBT_METRICS = (
    "LongTermDebtCurrent",
    "DebtCurrent",
    "ShortTermBorrowings",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
)


@dataclass(frozen=True, slots=True)
class FreeCashFlow:
    """One fiscal year of cash from operations, less capital spending."""

    value: float
    operating_cash: float
    capital_expenditure: float
    period_end: dt.date
    known: dt.date
    operating_metric: str

    @property
    def explanation(self) -> str:
        return (
            f"free cash flow {self.value:,.0f} = {self.operating_metric} "
            f"{self.operating_cash:,.0f} - {CAPEX_METRIC} "
            f"{self.capital_expenditure:,.0f} (fiscal year to "
            f"{self.period_end}, first filed {self.known})"
        )


@dataclass(frozen=True, slots=True)
class EnterpriseValue:
    """What the whole company costs, and the tags that said so."""

    value: float
    market_cap: float
    debt: float
    cash: float
    shares: float
    price: float
    price_session: dt.date
    #: Which metric supplied each component. Carried because a reader cannot
    #: otherwise tell a company with no debt from one whose debt we failed to
    #: find, and those are different facts with the same number.
    debt_metrics: tuple[str, ...]
    cash_metric: str | None

    @property
    def debt_may_be_understated(self) -> str | None:
        """Whether only part of the debt was found, or ``None``.

        A current portion with no long-term portion is the tell: a company
        with debt falling due this year almost always has more behind it, and
        finding only the near leg means the far one is tagged in a way we do
        not read. Corpus security 28 is the worked example --- $54.1m of
        current capital-lease obligations and no long-term tag at all.

        Reported rather than corrected, because the correction would be a
        guess. It matters because the error runs one way: missing debt
        understates enterprise value and overstates every yield on it.
        """
        if not self.debt_metrics:
            return None
        if any(metric in LONG_TERM_DEBT_METRICS for metric in self.debt_metrics):
            return None
        return (
            f"only a current portion of debt resolved ({', '.join(self.debt_metrics)}) "
            "and no long-term tag; any long-term debt tagged otherwise is missing "
            "here, which understates enterprise value and overstates the yield"
        )

    @property
    def explanation(self) -> str:
        cash_from = self.cash_metric or "no cash tag resolved; treated as zero"
        return (
            f"enterprise value {self.value:,.0f} = market cap {self.market_cap:,.0f} "
            f"({self.shares:,.0f} shares x {self.price:,.4f}) + debt {self.debt:,.0f} "
            f"({', '.join(self.debt_metrics)}) - cash {self.cash:,.0f} ({cash_from})"
        )


def free_cash_flow(history: FundamentalHistory, on: dt.date) -> FreeCashFlow | Unresolved:
    """Free cash flow for the latest **fiscal year** usable on ``on``.

    **The fiscal year, not a trailing twelve months, and the name says so.**
    The first version of this summed four stand-alone quarters, the way
    earnings are summed, and resolved for **no security at all**. The cause is
    a read rule worth having: **cash-flow statements are tagged cumulatively.**
    Measured on corpus security 1 --- `NetIncomeLoss` carries 138 stand-alone
    quarterly rows, `NetCashProvidedByUsedInOperatingActivities` carries 28,
    one per year. A cash-flow statement reports year-to-date at every filing:
    three months at Q1, six at the half, nine at Q3, twelve at the 10-K, so
    ``duration_qtrs`` runs 1, 2, 3, 4 once each per year and stand-alone
    quarters exist only for fiscal Q1.

    Summing "four consecutive rows" of that spans four *years*. The span check
    in :func:`~tradeit.valuation.multiples.trailing_twelve_months` caught it
    and returned nothing, which is why this surfaced as absence rather than as
    a number four times too large --- the check was written for a missing
    quarter and caught a different bug than it was aimed at.

    So both legs are read as the annual figure, which is directly reported and
    genuinely twelve months, under the same fifteen-month staleness rule
    `fundamental_signals` applies to any annual value. The cost is currency:
    this lags by up to a year where a trailing figure would not, and that is
    preferable to a number assembled from periods that do not abut.

    **Capital expenditure is required, not optional.** Operating cash flow on
    its own is not free cash flow, and a capital-intensive company would look
    like a capital-light one. Where the tag is absent the answer is
    UNRESOLVED, which costs roughly a quarter of the universe and is the
    honest price.
    """
    operating = None
    operating_metric = ""
    for metric in OPERATING_CASH_METRICS:
        operating = history.latest_annual(metric, on)
        if operating is not None:
            operating_metric = metric
            break
    if operating is None:
        return Unresolved(
            "free_cash_flow",
            f"no fiscal year of operating cash flow was usable and fresh on {on}",
        )
    capex = history.latest_annual(CAPEX_METRIC, on)
    if capex is None:
        return Unresolved(
            "free_cash_flow",
            (
                f"no fiscal year of {CAPEX_METRIC} was usable and fresh on {on}; "
                "operating cash flow alone is not free cash flow and would make a "
                "capital-intensive company look capital-light"
            ),
        )
    if operating.period_end != capex.period_end:
        # Both legs must describe the same year or the subtraction is
        # meaningless -- a growing company's capex from a later year against
        # an earlier year's cash flow understates free cash flow, and the
        # arithmetic gives no sign of it.
        return Unresolved(
            "free_cash_flow",
            (
                f"operating cash flow covers the year to {operating.period_end} and "
                f"{CAPEX_METRIC} the year to {capex.period_end}; subtracting across "
                "different years is not free cash flow"
            ),
        )
    return FreeCashFlow(
        value=operating.value - capex.value,
        operating_cash=operating.value,
        capital_expenditure=capex.value,
        period_end=operating.period_end,
        known=max(operating.known, capex.known),
        operating_metric=operating_metric,
    )


def _first_balance(
    history: FundamentalHistory, metrics: tuple[str, ...], on: dt.date
) -> tuple[str, float] | None:
    """The first metric in ``metrics`` with a fresh balance usable on ``on``.

    First rather than largest, and never a sum: the candidates overlap --- a
    total and its own non-current component both appear --- so adding them
    double counts, and taking the largest would silently prefer whichever
    definition is widest.
    """
    for metric in metrics:
        fact = history.instant_known_by(metric, on)
        if fact is None:
            continue
        if (on - fact.known).days > BALANCE_FRESH_DAYS:
            continue
        return metric, fact.value
    return None


def enterprise_value(
    price: PriceBar, history: FundamentalHistory, on: dt.date
) -> EnterpriseValue | Unresolved:
    """Market capitalisation plus debt, less cash.

    **An absent debt tag is UNRESOLVED, never zero.** It is tempting to treat
    a missing tag as a debt-free balance sheet, and it is the one substitution
    in this package that would be actively dangerous: understated debt
    understates enterprise value, overstates every yield computed on it, and
    sorts the most levered companies in the universe to the top of a cheapness
    ranking. A quarter of the measured sample carries no debt tag we read.

    Cash is treated differently and deliberately so. A missing cash tag
    *overstates* enterprise value and understates the yield, which is the safe
    direction, so it is allowed through as zero with the absence recorded in
    :attr:`EnterpriseValue.cash_metric`.
    """
    shares = shares_outstanding(history, on)
    if shares is None:
        return Unresolved("enterprise_value", f"no usable, fresh share count on {on}")
    long_term = _first_balance(history, LONG_TERM_DEBT_METRICS, on)
    current = _first_balance(history, CURRENT_DEBT_METRICS, on)
    if long_term is None and current is None:
        return Unresolved(
            "enterprise_value",
            (
                f"no debt tag resolved on {on}. This is not read as a debt-free "
                "balance sheet: understated debt understates enterprise value and "
                "sorts the most levered companies as the cheapest"
            ),
        )
    debt_metrics: list[str] = []
    debt = 0.0
    for part in (long_term, current):
        if part is not None:
            debt_metrics.append(part[0])
            debt += part[1]
    cash_found = _first_balance(history, CASH_METRICS, on)
    cash = cash_found[1] if cash_found else 0.0
    market_cap = shares.value * price.close
    return EnterpriseValue(
        value=market_cap + debt - cash,
        market_cap=market_cap,
        debt=debt,
        cash=cash,
        shares=shares.value,
        price=price.close,
        price_session=price.session_date,
        debt_metrics=tuple(debt_metrics),
        cash_metric=cash_found[0] if cash_found else None,
    )


def implied_growth(fcf_yield: float, discount_rate: float) -> float:
    """What perpetual growth this price implies, at a stated required return.

    **The reverse of a valuation, which is the honest direction.** A forward
    DCF needs a growth forecast and returns a price; every argument then
    happens inside the forecast, invisibly. Running it backwards needs no
    forecast at all and returns a statement about the price: *the market is
    paying for this much growth, forever.* A reader can compare that with what
    the company has actually delivered, which is a fact they can check.

    From the perpetuity form ``EV = FCF / (r - g)``, so ``g = r - FCF/EV``.
    One assumption rather than three --- no horizon, no terminal rate, no
    fade --- and the arithmetic is simple enough to verify in your head, which
    is the point: a model whose output cannot be checked is a model that gets
    believed.

    **Its limitation, stated rather than hidden.** Constant growth forever
    understates what a genuinely fast-growing company can justify, because
    real growth decays and this model never lets it. So a high implied growth
    is evidence the price needs defending, not proof that it is wrong. The
    caller supplies ``discount_rate``; there is no default, because the answer
    is meaningless without knowing which required return produced it.
    """
    return discount_rate - fcf_yield
