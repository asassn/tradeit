"""The valuation panel: free cash flow, enterprise value, and no verdict.

Each test here corresponds to a way the panel would mislead rather than fail.
The grouping is deliberate: nothing in this file is testing arithmetic, which
is trivial and already right. It is testing the refusals.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.research01.fundamental_signals import Fact, FundamentalHistory
from tradeit.valuation import (
    EnterpriseValue,
    FreeCashFlow,
    PriceBar,
    Unresolved,
    enterprise_value,
    free_cash_flow,
    implied_growth,
    panel_for,
)

ON = dt.date(2020, 6, 1)
YEAR_END = dt.date(2019, 12, 31)


def _annual(metric: str, value: float, *, end: dt.date = YEAR_END) -> Fact:
    return Fact(metric, end, 4, value, end + dt.timedelta(days=60))


def _balance(metric: str, value: float, *, end: dt.date = YEAR_END) -> Fact:
    return Fact(metric, end, 0, value, end + dt.timedelta(days=60))


def _history(
    *,
    operating: float | None = 1_000.0,
    capex: float | None = 200.0,
    long_term_debt: float | None = 500.0,
    current_debt: float | None = None,
    cash: float | None = 100.0,
    shares: float | None = 100.0,
    equity: float | None = 2_000.0,
    extra: list[Fact] | None = None,
) -> FundamentalHistory:
    facts: list[Fact] = []
    if operating is not None:
        facts.append(_annual("NetCashProvidedByUsedInOperatingActivities", operating))
    if capex is not None:
        facts.append(_annual("PaymentsToAcquirePropertyPlantAndEquipment", capex))
    if long_term_debt is not None:
        facts.append(_balance("LongTermDebtNoncurrent", long_term_debt))
    if current_debt is not None:
        facts.append(_balance("LongTermDebtCurrent", current_debt))
    if cash is not None:
        facts.append(_balance("CashAndCashEquivalentsAtCarryingValue", cash))
    if shares is not None:
        facts.append(_balance("CommonStockSharesOutstanding", shares))
    if equity is not None:
        facts.append(_balance("StockholdersEquity", equity))
    facts.extend(extra or [])
    return FundamentalHistory(facts)


def _price(close: float = 50.0, *, basis: str = "raw", on: dt.date = ON) -> PriceBar:
    return PriceBar(session_date=on, close=close, basis=basis)


# ---------------------------------------------------------------------------
# Debt. The one-way error.
# ---------------------------------------------------------------------------


def test_no_debt_tag_is_unresolved_not_a_debt_free_balance_sheet() -> None:
    """The most dangerous substitution available in this package.

    Debt is tagged a dozen ways in XBRL and a filer using one we do not read
    looks debt-free. Treating absence as zero understates enterprise value,
    overstates every yield on it, and sorts the most levered companies in the
    universe to the top of a cheapness ranking. Measured over 40 corpus
    securities: a long-term debt tag resolves for 26, so a quarter of the
    sample would silently become debt-free.
    """
    result = enterprise_value(_price(), _history(long_term_debt=None), ON)
    assert isinstance(result, Unresolved)
    assert "not read as a debt-free balance sheet" in result.reason


def test_a_resolved_debt_tag_does_produce_an_enterprise_value() -> None:
    """The companion, so the refusal above cannot pass by refusing
    everything."""
    result = enterprise_value(_price(close=50.0), _history(), ON)
    assert isinstance(result, EnterpriseValue)
    # 100 shares x 50 = 5,000 market cap, + 500 debt - 100 cash.
    assert result.value == pytest.approx(5_400.0)


def test_missing_cash_is_allowed_through_because_the_error_runs_the_safe_way() -> None:
    """Deliberately asymmetric with debt, and the asymmetry is the reasoning.

    Missing cash *overstates* enterprise value and *understates* the yield,
    which cannot make a company look cheaper than it is. The absence is
    recorded rather than hidden.
    """
    result = enterprise_value(_price(), _history(cash=None), ON)
    assert isinstance(result, EnterpriseValue)
    assert result.cash == 0.0
    assert result.cash_metric is None


def test_overlapping_debt_tags_are_never_summed() -> None:
    """`LongTermDebt` is a total and `LongTermDebtNoncurrent` is its own
    component; adding them double counts, and taking the largest would
    silently prefer whichever definition is widest."""
    history = _history(long_term_debt=500.0, extra=[_balance("LongTermDebt", 600.0)])
    result = enterprise_value(_price(), history, ON)
    assert isinstance(result, EnterpriseValue)
    assert result.debt == pytest.approx(600.0), "the first tag in order wins, alone"
    assert result.debt_metrics == ("LongTermDebt",)


def test_only_a_current_portion_is_flagged_as_possibly_understated() -> None:
    """A company with debt due this year almost always has more behind it, so
    finding only the near leg means the far one is tagged otherwise. Corpus
    security 28 is the worked example."""
    result = enterprise_value(_price(), _history(long_term_debt=None, current_debt=54.0), ON)
    assert isinstance(result, EnterpriseValue)
    assert result.debt_may_be_understated is not None
    assert "understates enterprise value" in result.debt_may_be_understated


def test_a_full_debt_picture_raises_no_such_flag() -> None:
    result = enterprise_value(_price(), _history(current_debt=50.0), ON)
    assert isinstance(result, EnterpriseValue)
    assert result.debt_may_be_understated is None


# ---------------------------------------------------------------------------
# Free cash flow, and the cumulative-tagging rule that produced it.
# ---------------------------------------------------------------------------


def test_free_cash_flow_subtracts_capital_spending() -> None:
    result = free_cash_flow(_history(operating=1_000.0, capex=200.0), ON)
    assert isinstance(result, FreeCashFlow)
    assert result.value == pytest.approx(800.0)


def test_operating_cash_flow_alone_is_not_free_cash_flow() -> None:
    """Capital expenditure is required, not optional: without it a
    capital-intensive company reads as a capital-light one. The cost is
    roughly a quarter of the universe, measured."""
    result = free_cash_flow(_history(capex=None), ON)
    assert isinstance(result, Unresolved)
    assert "capital-light" in result.reason


def test_the_two_legs_must_cover_the_same_fiscal_year() -> None:
    """A growing company's later-year capex against an earlier year's cash
    flow understates free cash flow, and the arithmetic gives no sign of it."""
    history = FundamentalHistory(
        [
            _annual("NetCashProvidedByUsedInOperatingActivities", 1_000.0),
            # Both inside the staleness rule -- a year apart but fresh, or
            # that guard fires first and this test never reaches the mismatch
            # it is named for. It did, the first time it was written.
            _annual(
                "PaymentsToAcquirePropertyPlantAndEquipment",
                200.0,
                end=dt.date(2019, 6, 30),
            ),
        ]
    )
    result = free_cash_flow(history, ON)
    assert isinstance(result, Unresolved)
    assert "different years" in result.reason


def test_cumulative_quarters_are_not_summed_into_a_fiscal_year() -> None:
    """The bug this module was rewritten around.

    Cash-flow statements are tagged **cumulatively**: year-to-date at every
    filing, so ``duration_qtrs`` runs 1, 2, 3, 4 once each per year and
    stand-alone quarters exist only for fiscal Q1. Measured on corpus security
    1: `NetIncomeLoss` carries 138 stand-alone quarterly rows and
    `NetCashProvidedByUsedInOperatingActivities` carries 28, one a year.
    Summing four consecutive *rows* spans four *years*.

    Here only cumulative rows exist and no annual one, so there is nothing to
    read: a reader of quarters would find four rows and sum them to 2,500.
    """
    history = FundamentalHistory(
        [
            Fact(
                "NetCashProvidedByUsedInOperatingActivities",
                YEAR_END,
                d,
                250.0 * d,
                YEAR_END + dt.timedelta(days=60),
            )
            for d in (1, 2, 3)
        ]
        + [_annual("PaymentsToAcquirePropertyPlantAndEquipment", 200.0)]
    )
    assert isinstance(free_cash_flow(history, ON), Unresolved)


def test_a_stale_fiscal_year_is_refused() -> None:
    """A company that has stopped filing still has a last annual report, and
    pairing today's price with it values a company that may no longer exist in
    that shape."""
    old = dt.date(2015, 12, 31)
    history = FundamentalHistory(
        [
            _annual("NetCashProvidedByUsedInOperatingActivities", 1_000.0, end=old),
            _annual("PaymentsToAcquirePropertyPlantAndEquipment", 200.0, end=old),
        ]
    )
    assert isinstance(free_cash_flow(history, ON), Unresolved)


# ---------------------------------------------------------------------------
# The yield, and what the price implies.
# ---------------------------------------------------------------------------


def test_the_yield_is_free_cash_flow_over_enterprise_value() -> None:
    panel = panel_for(1, _price(close=50.0), _history(), ON)
    # 800 of free cash flow over 5,400 of enterprise value.
    assert panel.fcf_yield == pytest.approx(800 / 5_400)


def test_a_negative_enterprise_value_yields_nothing() -> None:
    """Net cash exceeding market capitalisation is real, and the yield it
    produces is negative-denominator nonsense that sorts to one extreme of any
    ranking -- the same inversion negative earnings cause."""
    panel = panel_for(1, _price(close=1.0), _history(cash=10_000.0), ON)
    assert panel.fcf_yield is None
    assert any(u.basis == "fcf_yield" for u in panel.unresolved)


def test_implied_growth_is_the_required_return_less_the_yield() -> None:
    """From ``EV = FCF / (r - g)``. One assumption rather than three, and
    simple enough that a reader can check it."""
    assert implied_growth(0.02, 0.09) == pytest.approx(0.07)


def test_the_implied_growth_is_shown_at_several_required_returns() -> None:
    """A grid, not a choice: the point is that the reader sees how much the
    answer depends on the rate, which a single number hides."""
    panel = panel_for(1, _price(), _history(), ON)
    assert len(panel.implied) > 1
    rates = {growth.discount_rate for growth in panel.implied}
    assert len(rates) == len(panel.implied), "each rate appears once"


# ---------------------------------------------------------------------------
# The value trap check.
# ---------------------------------------------------------------------------


def test_falling_cash_flow_is_flagged_beside_the_yield() -> None:
    """The reason the panel is not just a yield: a high yield on cash that is
    falling is a company being repriced, not a bargain. The cheapest decile of
    any screen is full of them."""
    older = dt.date(2016, 12, 31)
    history = _history(
        extra=[
            _annual("NetCashProvidedByUsedInOperatingActivities", 3_000.0, end=older),
            _annual("PaymentsToAcquirePropertyPlantAndEquipment", 200.0, end=older),
        ]
    )
    panel = panel_for(1, _price(), history, ON)
    assert any(flag.name == "free_cash_flow_declining" for flag in panel.quality)


def test_rising_cash_flow_raises_no_flag() -> None:
    older = dt.date(2016, 12, 31)
    history = _history(
        extra=[
            _annual("NetCashProvidedByUsedInOperatingActivities", 400.0, end=older),
            _annual("PaymentsToAcquirePropertyPlantAndEquipment", 100.0, end=older),
        ]
    )
    panel = panel_for(1, _price(), history, ON)
    assert not any(flag.name == "free_cash_flow_declining" for flag in panel.quality)


def test_negative_free_cash_flow_is_flagged_rather_than_yielded() -> None:
    panel = panel_for(1, _price(), _history(operating=100.0, capex=900.0), ON)
    assert any(flag.name == "negative_free_cash_flow" for flag in panel.quality)


# ---------------------------------------------------------------------------
# No verdict, and the things the panel says about itself.
# ---------------------------------------------------------------------------


def test_the_panel_carries_no_verdict_field() -> None:
    """The decision this module exists to record.

    A multiple has no absolute meaning without growth and risk; valuation is
    horizon-dependent, so one label across three horizons would be the global
    buy/sell number the architecture exists to prevent; and the four
    fundamental signals measured on this corpus all failed. If a verdict ever
    reappears here, this fails and the reasoning is back in somebody's memory.
    """
    panel = panel_for(1, _price(), _history(), ON)
    for forbidden in ("verdict", "label", "rating", "callout"):
        assert not hasattr(panel, forbidden), f"the panel grew a {forbidden}"


def test_the_sector_gap_is_stated_rather_than_quietly_omitted() -> None:
    """The roadmap asks for three comparisons and this ships two."""
    panel = panel_for(1, _price(), _history(), ON)
    assert any("sector-relative" in caveat for caveat in panel.caveats)


def test_every_number_is_explained_in_a_line_a_reader_can_check() -> None:
    panel = panel_for(1, _price(), _history(), ON)
    joined = " ".join(panel.explanations)
    for expected in ("enterprise value", "free cash flow", "required return"):
        assert expected in joined, joined


def test_the_yield_rank_counts_what_it_rests_on() -> None:
    """A percentile against three observations and one against three hundred
    are different claims, and the count is what distinguishes them."""
    past = [_price(close=40.0 + n, on=ON - dt.timedelta(days=30 * n)) for n in (1, 2, 3)]
    panel = panel_for(1, _price(), _history(), ON, past_prices=past)
    assert panel.fcf_yield_rank is not None
    assert panel.fcf_yield_history_size == 3


def test_a_future_session_never_enters_the_yield_history() -> None:
    """Look-ahead through the back door: a caller passing a full price series
    would otherwise rank today against tomorrow."""
    future = [_price(close=40.0, on=ON + dt.timedelta(days=n)) for n in (1, 2, 3)]
    panel = panel_for(1, _price(), _history(), ON, past_prices=future)
    assert panel.fcf_yield_history_size == 0
    assert panel.fcf_yield_rank is None
