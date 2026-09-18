"""Point-in-time fundamental signals -- the definitions of FUNDAMENTALS_2026-09-18.

Each test names a way a fundamental study reads the future without saying so: a
restatement leaking back, a filing used on the day it arrived, a fourth quarter
read months late, a stale annual figure carried forward.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import ClassVar

import pytest

from tradeit.research01.fundamental_signals import (
    ANNUAL_FRESH_DAYS,
    SUE_FRESH_DAYS,
    Fact,
    FundamentalHistory,
    first_filed,
)

D = dt.date


class TestAsFirstFiled:
    def test_a_later_restatement_is_ignored(self) -> None:
        rows = [
            ("Assets", D(2015, 12, 31), 0, 100.0, D(2016, 2, 20)),
            ("Assets", D(2015, 12, 31), 0, 130.0, D(2017, 2, 20)),  # restated
        ]
        (fact,) = first_filed(rows)
        assert fact.value == 100.0
        assert fact.known == D(2016, 2, 20)

    def test_a_filing_is_not_usable_on_the_day_it_arrived(self) -> None:
        """Stamped at midnight, possibly filed after the close."""
        facts = first_filed(
            [
                ("NetIncomeLoss", D(2015, 12, 31), 4, 10.0, D(2016, 2, 20)),
                (
                    "NetCashProvidedByUsedInOperatingActivities",
                    D(2015, 12, 31),
                    4,
                    8.0,
                    D(2016, 2, 20),
                ),
                ("Assets", D(2015, 12, 31), 0, 100.0, D(2016, 2, 20)),
                ("Assets", D(2014, 12, 31), 0, 100.0, D(2015, 2, 20)),
            ]
        )
        history = FundamentalHistory(facts)
        assert history.accruals(D(2016, 2, 20)) is None
        assert history.accruals(D(2016, 2, 21)) == pytest.approx(0.02)


class TestFiscalQ4:
    """Apple FY2012, from the corpus."""

    ROWS: ClassVar[list[tuple[str, dt.date, int, float, dt.date]]] = [
        ("NetIncomeLoss", D(2012, 6, 30), 3, 33_510.0, D(2012, 7, 25)),
        ("NetIncomeLoss", D(2012, 9, 30), 4, 41_733.0, D(2012, 10, 31)),
        # The stand-alone Q4, first printed six months later as a comparative.
        ("NetIncomeLoss", D(2012, 9, 30), 1, 8_223.0, D(2013, 4, 24)),
    ]

    def test_q4_is_derived_and_known_at_the_10k(self) -> None:
        history = FundamentalHistory(first_filed(self.ROWS))
        q4 = next(q for q in history._quarters if q.period_end == D(2012, 9, 30))
        assert q4.value == pytest.approx(8_223.0)
        assert q4.known == D(2012, 10, 31)

    def test_without_the_nine_months_the_late_print_is_all_there_is(self) -> None:
        history = FundamentalHistory(first_filed(self.ROWS[1:]))
        q4 = next(q for q in history._quarters if q.period_end == D(2012, 9, 30))
        assert q4.known == D(2013, 4, 24)


FIRST_QUARTER_END = D(2013, 3, 31)


def _quarters(values: list[float], first_end: dt.date = FIRST_QUARTER_END) -> list[Fact]:
    """Stand-alone quarterly net income, each filed 40 days after its quarter."""
    facts = []
    end = first_end
    for value in values:
        facts.append(Fact("NetIncomeLoss", end, 1, value, end + dt.timedelta(days=40)))
        month = end.month + 3
        year = end.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        end = D(year, month, [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return facts


class TestSUE:
    VALUES: ClassVar[list[float]] = [10, 11, 9, 12, 12, 12, 10, 13, 13, 14, 11, 15, 20.0]

    def test_standardised_by_the_prior_seasonal_differences(self) -> None:
        facts = _quarters(self.VALUES)
        history = FundamentalHistory(facts)
        on = facts[-1].known + dt.timedelta(days=1)
        diffs = [self.VALUES[i] - self.VALUES[i - 4] for i in range(4, len(self.VALUES))]
        expected = diffs[-1] / statistics.stdev(diffs[:-1])
        assert history.sue(on) == pytest.approx(expected)

    def test_a_stale_announcement_carries_no_surprise(self) -> None:
        facts = _quarters(self.VALUES)
        history = FundamentalHistory(facts)
        late = facts[-1].known + dt.timedelta(days=SUE_FRESH_DAYS + 1)
        assert history.sue(late) is None

    def test_too_little_history_is_no_signal(self) -> None:
        facts = _quarters(self.VALUES[:9])  # five seasonal differences, need 6 + 1
        history = FundamentalHistory(facts)
        assert history.sue(facts[-1].known + dt.timedelta(days=1)) is None

    def test_the_latest_quarter_is_not_used_before_it_is_filed(self) -> None:
        facts = _quarters(self.VALUES)
        history = FundamentalHistory(facts)
        before = history.sue(facts[-1].known)  # the day it is filed: not yet usable
        after = history.sue(facts[-1].known + dt.timedelta(days=1))
        assert before != after


class TestAnnualSignals:
    FACTS = first_filed(
        [
            ("Assets", D(2014, 12, 31), 0, 200.0, D(2015, 2, 20)),
            ("Assets", D(2015, 12, 31), 0, 250.0, D(2016, 2, 20)),
            ("NetIncomeLoss", D(2015, 12, 31), 4, 30.0, D(2016, 2, 20)),
            (
                "NetCashProvidedByUsedInOperatingActivities",
                D(2015, 12, 31),
                4,
                21.0,
                D(2016, 2, 20),
            ),
            ("GrossProfit", D(2015, 12, 31), 4, 100.0, D(2016, 2, 20)),
        ]
    )
    ON = D(2016, 3, 1)

    def test_asset_growth(self) -> None:
        assert FundamentalHistory(self.FACTS).asset_growth(self.ON) == pytest.approx(0.25)

    def test_accruals_over_average_assets(self) -> None:
        assert FundamentalHistory(self.FACTS).accruals(self.ON) == pytest.approx(9.0 / 225.0)

    def test_gross_profitability(self) -> None:
        assert FundamentalHistory(self.FACTS).gross_profitability(self.ON) == pytest.approx(0.4)

    def test_a_company_that_stopped_filing_drops_out(self) -> None:
        history = FundamentalHistory(self.FACTS)
        stale = D(2016, 2, 20) + dt.timedelta(days=ANNUAL_FRESH_DAYS + 1)
        assert history.asset_growth(stale) is None
        assert history.accruals(stale) is None
        assert history.gross_profitability(stale) is None

    def test_no_year_earlier_balance_no_growth(self) -> None:
        facts = [f for f in self.FACTS if f.period_end != D(2014, 12, 31)]
        assert FundamentalHistory(facts).asset_growth(self.ON) is None
