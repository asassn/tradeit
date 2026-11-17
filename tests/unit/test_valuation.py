"""Multiples: the ways one returns a confident wrong number.

Pure functions over facts, so every point-in-time rule is testable exactly --
the same property `test_research01_fundamental_signals` relies on.

Grouped by what they protect against, because a flat list reads as arithmetic
and almost none of this is arithmetic. The panel these feed is tested in
`test_valuation_panel.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.research01.fundamental_signals import Fact, FundamentalHistory
from tradeit.valuation import (
    Multiple,
    PriceBar,
    Unresolved,
    panel_for,
    price_to_book,
    price_to_earnings,
    rank_within,
)
from tradeit.valuation.panel import NOT_POINT_IN_TIME_BEFORE

ON = dt.date(2020, 6, 1)


def _quarters(
    metric: str, values: list[float], *, last_end: dt.date = dt.date(2020, 3, 31)
) -> list[Fact]:
    """Four consecutive stand-alone quarters ending at ``last_end``."""
    out: list[Fact] = []
    for index, value in enumerate(reversed(values)):
        end = last_end - dt.timedelta(days=91 * index)
        out.append(
            Fact(
                metric=metric,
                period_end=end,
                duration=1,
                value=value,
                known=end + dt.timedelta(days=30),
            )
        )
    return list(reversed(out))


def _balance(metric: str, value: float, *, end: dt.date = dt.date(2020, 3, 31)) -> Fact:
    return Fact(
        metric=metric,
        period_end=end,
        duration=0,
        value=value,
        known=end + dt.timedelta(days=30),
    )


def _history(
    *,
    earnings: list[float] | None = None,
    equity: float | None = 1_000.0,
    shares: float | None = 100.0,
) -> FundamentalHistory:
    facts: list[Fact] = []
    if earnings is not None:
        facts += _quarters("NetIncomeLoss", earnings)
    if equity is not None:
        facts.append(_balance("StockholdersEquity", equity))
    if shares is not None:
        facts.append(_balance("CommonStockSharesOutstanding", shares))
    return FundamentalHistory(facts)


def _price(close: float = 100.0, *, basis: str = "raw") -> PriceBar:
    return PriceBar(session_date=ON, close=close, basis=basis)


# ---------------------------------------------------------------------------
# The inverted ranking. The reason this module exists.
# ---------------------------------------------------------------------------


def test_negative_earnings_are_unresolved_not_a_negative_multiple() -> None:
    """The single most common way a screen built on multiples goes wrong.

    The arithmetic succeeds on a loss-making company and yields a negative
    number, which sorts *below* every cheap company in any ascending ranking --
    so the most distressed names in the universe come out looking like the best
    value. Measured on the real corpus 2026-11-16: two of five probed
    securities were loss-making with negative equity, both delisted, one at
    -$1.28bn of trailing earnings.
    """
    result = price_to_earnings(_price(), _history(earnings=[-50, -50, -50, -50]), ON)
    assert isinstance(result, Unresolved)
    assert "non-positive earnings" in result.reason


def test_negative_book_value_is_unresolved_not_a_negative_multiple() -> None:
    """Same failure on the balance sheet: an insolvent company would sort as
    cheap."""
    result = price_to_book(_price(), _history(equity=-5_000.0), ON)
    assert isinstance(result, Unresolved)
    assert "non-positive book value" in result.reason


def test_zero_earnings_are_refused_rather_than_dividing() -> None:
    """Break-even is not a valuation either, and the guard must be `<= 0`
    rather than `< 0` or this raises instead of answering."""
    result = price_to_earnings(_price(), _history(earnings=[25, -25, 10, -10]), ON)
    assert isinstance(result, Unresolved)


def test_positive_earnings_do_resolve() -> None:
    """The companion, so the refusals above cannot go vacuous by refusing
    everything."""
    result = price_to_earnings(_price(close=100.0), _history(earnings=[25, 25, 25, 25]), ON)
    assert isinstance(result, Multiple)
    # 100 of earnings over 100 shares = 1.00 per share; 100 / 1 = 100.
    assert result.value == pytest.approx(100.0)
    assert result.denominator == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Point-in-time.
# ---------------------------------------------------------------------------


def test_a_fact_filed_on_the_session_is_not_yet_usable() -> None:
    """`knowledge_time` is stamped at midnight and a filing can arrive after
    the close, so the rule is `known < on`, inherited whole from
    `fundamental_signals`. An off-by-one here is look-ahead."""
    facts = _quarters("NetIncomeLoss", [25, 25, 25, 25])
    latest = max(f.known for f in facts)
    history = FundamentalHistory(
        [
            *facts,
            _balance("CommonStockSharesOutstanding", 100.0),
            _balance("StockholdersEquity", 1_000.0),
        ]
    )
    assert isinstance(price_to_earnings(PriceBar(latest, 100.0), history, latest), Unresolved)
    day_after = latest + dt.timedelta(days=1)
    assert isinstance(price_to_earnings(PriceBar(day_after, 100.0), history, day_after), Multiple)


def test_three_quarters_are_not_annualised() -> None:
    """A three-quarter figure scaled up is a forecast wearing a measurement's
    clothes."""
    history = FundamentalHistory(
        [
            *_quarters("NetIncomeLoss", [25, 25, 25])[:3],
            _balance("CommonStockSharesOutstanding", 100.0),
        ]
    )
    assert isinstance(price_to_earnings(_price(), history, ON), Unresolved)


def test_four_rows_spanning_two_years_are_not_twelve_months() -> None:
    """Four consecutive *rows* are not four consecutive *quarters*.

    A company that skipped a filing would otherwise have a stale quarter
    silently stand in for a missing one, producing a "trailing twelve month"
    figure that spans two years and a multiple that is wrong by roughly half.
    """
    facts = [
        Fact("NetIncomeLoss", dt.date(2018, 3, 31), 1, 25.0, dt.date(2018, 4, 30)),
        Fact("NetIncomeLoss", dt.date(2018, 6, 30), 1, 25.0, dt.date(2018, 7, 30)),
        Fact("NetIncomeLoss", dt.date(2019, 12, 31), 1, 25.0, dt.date(2020, 1, 30)),
        Fact("NetIncomeLoss", dt.date(2020, 3, 31), 1, 25.0, dt.date(2020, 4, 30)),
        _balance("CommonStockSharesOutstanding", 100.0),
    ]
    assert isinstance(price_to_earnings(_price(), FundamentalHistory(facts), ON), Unresolved)


def test_a_stale_balance_does_not_describe_the_company() -> None:
    """A company that has stopped filing still has a last balance sheet, and
    pairing today's price with it is a multiple about a company that may no
    longer exist in that shape."""
    old = _balance("StockholdersEquity", 1_000.0, end=dt.date(2015, 3, 31))
    shares = _balance("CommonStockSharesOutstanding", 100.0, end=dt.date(2015, 3, 31))
    result = price_to_book(_price(), FundamentalHistory([old, shares]), ON)
    assert isinstance(result, Unresolved)
    assert "too stale" in result.reason


# ---------------------------------------------------------------------------
# Identity of the denominator.
# ---------------------------------------------------------------------------


def test_missing_shares_outstanding_is_unresolved_not_assumed() -> None:
    result = price_to_earnings(_price(), _history(earnings=[25, 25, 25, 25], shares=None), ON)
    assert isinstance(result, Unresolved)
    assert "CommonStockSharesOutstanding" in result.reason


def test_a_multiple_carries_every_input_it_was_computed_from() -> None:
    """The roadmap forbids shipping a valuation label without the reasoning
    that produced it, and the cheapest way to honour that is to make the
    reasoning inseparable from the number."""
    result = price_to_earnings(_price(close=100.0), _history(earnings=[25, 25, 25, 25]), ON)
    assert isinstance(result, Multiple)
    line = result.explanation
    for expected in ("100.0000", "raw", str(ON), "NetIncomeLoss", "first filed"):
        assert expected in line, line


def test_an_adjusted_price_is_flagged_as_the_wrong_basis() -> None:
    """`v_prices` is split- and dividend-adjusted and calls itself "the current
    belief"; a multiple needs `v_prices_raw`, "what a trader saw on the day".
    Pairing an adjusted close with an as-reported per-share figure is wrong by
    every split since -- a 2-for-1 prints the company at half its true
    multiple."""
    panel = panel_for(1, _price(basis="total"), _history(earnings=[25, 25, 25, 25]), ON)
    assert any("raw print" in caveat for caveat in panel.caveats)


def test_a_raw_price_raises_no_basis_caveat() -> None:
    panel = panel_for(1, _price(), _history(earnings=[25, 25, 25, 25]), ON)
    assert not any("raw print" in caveat for caveat in panel.caveats)


# ---------------------------------------------------------------------------
# Ranking within a population.
# ---------------------------------------------------------------------------


def test_an_empty_population_ranks_as_nothing_not_as_the_midpoint() -> None:
    """0.5 returned for "nothing to compare against" reads as *typical*, which
    is the fabricated certainty this project refuses everywhere else."""
    assert rank_within(10.0, []) is None


def test_rank_is_the_fraction_at_or_below() -> None:
    assert rank_within(3.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(0.75)
    assert rank_within(0.5, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(0.0)


def test_history_is_repriced_rather_than_projected_backwards() -> None:
    """Each past session is re-paired with the fundamentals usable *then*.

    The cheap way -- today's earnings against last year's price -- makes every
    company that has grown look like it was once expensive, which is an
    artefact of the method rather than a fact about the company. Here earnings
    only become usable partway through the price history, so a correct
    implementation can rank against the later sessions alone.
    """
    history = _history(earnings=[25, 25, 25, 25])
    past = [PriceBar(ON - dt.timedelta(days=days), 100.0) for days in (900, 800, 30, 20, 10)]
    panel = panel_for(1, _price(close=100.0), history, ON, past_prices=past)
    size = panel.multiple_history_size["price_to_earnings"]
    assert size == 3, "only the sessions where the fundamentals were already filed may count"


def test_a_future_session_never_enters_the_history() -> None:
    """Look-ahead through the back door: a caller passing a full price series
    would otherwise rank today against tomorrow."""
    history = _history(earnings=[25, 25, 25, 25])
    past = [PriceBar(ON + dt.timedelta(days=days), 100.0) for days in (1, 2, 3)]
    panel = panel_for(1, _price(), history, ON, past_prices=past)
    assert "price_to_earnings" not in panel.multiple_ranks


def test_history_reaching_before_2013_carries_the_point_in_time_caveat() -> None:
    """`knowledge_time` begins in 2009 and the median gap from `period_end` is
    1,574 days, because SEC's XBRL datasets restate history. A valuation
    history reaching back past that is not what a reader could have seen."""
    history = _history(earnings=[25, 25, 25, 25])
    past = [PriceBar(NOT_POINT_IN_TIME_BEFORE - dt.timedelta(days=1), 100.0)]
    panel = panel_for(1, _price(), history, ON, past_prices=past)
    assert any("not point-in-time" in caveat for caveat in panel.caveats)


def test_a_history_inside_the_point_in_time_window_carries_no_such_caveat() -> None:
    history = _history(earnings=[25, 25, 25, 25])
    past = [PriceBar(ON - dt.timedelta(days=20), 100.0)]
    panel = panel_for(1, _price(), history, ON, past_prices=past)
    assert not any("not point-in-time" in caveat for caveat in panel.caveats)
