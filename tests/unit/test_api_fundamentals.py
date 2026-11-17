"""The as-filed statement spine.

Each test protects a rule that produces a confidently wrong statement if
skipped — and two of them exist because the first version of this route got
it wrong and the screen showed it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api.fundamentals_route import fundamentals_payload
from tradeit.storage.tables import Base


@pytest.fixture
def corpus(tmp_path: object) -> sessionmaker[Session]:
    dsn = f"sqlite:///{tmp_path / 'corpus.sqlite'}"  # type: ignore[operator]
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _fact(
    factory: sessionmaker[Session],
    metric: str,
    period_end: str,
    value: float,
    known: str,
    duration: int = 4,
) -> None:
    with factory() as s:
        s.execute(
            text(
                "insert into security_fundamental_facts (security_id, metric, "
                "fiscal_year, fiscal_period, duration_qtrs, period_end, event_time, "
                "knowledge_time, knowledge_source, value, unit, basis, source) "
                "values (1, :m, :y, 'FY', :d, :p, :p, :k, 'fsds', :v, 'USD', "
                "        'as_reported', 'fsds')"
            ),
            {
                "m": metric,
                "y": int(period_end[:4]),
                "d": duration,
                "p": period_end,
                "k": known,
                "v": value,
            },
        )
        s.commit()


def _read(factory: sessionmaker[Session]) -> dict[str, object]:
    with factory() as s:
        return fundamentals_payload(s, 1)


def _line(body: dict[str, object], label: str) -> dict[str, object]:
    return next(line for line in body["lines"] if line["label"] == label)  # type: ignore[union-attr,index]


def test_a_value_is_what_was_first_filed_not_what_was_restated(
    corpus: sessionmaker[Session],
) -> None:
    """A figure appears again in later filings, sometimes restated.

    Showing the latest row shows the restatement, which nobody could have
    acted on at the time. Apple's FY2017 revenue is the live example: first
    filed under the ASC 606 tag in **2019**, two years after the year ended.
    """
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 100.0, "2020-11-01")
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 95.0, "2021-11-01")  # restated
    values = _line(_read(corpus), "Net income")["values"]
    assert values[0]["value"] == pytest.approx(100.0)  # type: ignore[index]
    assert values[0]["first_filed"] == "2020-11-01"  # type: ignore[index]


def test_a_balance_is_an_instant_and_a_flow_is_not(
    corpus: sessionmaker[Session],
) -> None:
    """**The bug the screen found.**

    The first version queried `duration_qtrs = 4` for every line, which is
    right for revenue and excludes every balance-sheet item — total assets is
    a position on a date, not a sum over a year. The whole balance sheet
    silently vanished from the statement.
    """
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 100.0, "2020-11-01", duration=4)
    _fact(corpus, "Assets", "2020-09-30", 500.0, "2020-11-01", duration=0)
    body = _read(corpus)
    statements = {line["statement"] for line in body["lines"]}  # type: ignore[union-attr]
    assert "balance" in statements, "the balance sheet disappeared again"
    assert _line(body, "Total assets")["values"][0]["value"] == pytest.approx(500.0)  # type: ignore[index]


def test_the_columns_are_fiscal_years_and_balances_are_read_at_them(
    corpus: sessionmaker[Session],
) -> None:
    """**The second bug, exposed by fixing the first.**

    Instants land on quarter ends and flows on fiscal-year ends, so taking
    every period that appears produced columns where a December balance sat
    beside a September income statement and each left the other blank — a
    statement full of holes that are an artefact of the join.
    """
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 100.0, "2020-11-01", duration=4)
    _fact(corpus, "Assets", "2020-09-30", 500.0, "2020-11-01", duration=0)
    # A later quarterly balance, which must not become a column of its own.
    _fact(corpus, "Assets", "2020-12-31", 510.0, "2021-02-01", duration=0)
    body = _read(corpus)
    assert body["periods"] == ["2020-09-30"]
    assert len(_line(body, "Total assets")["values"]) == 1  # type: ignore[arg-type]


def test_revenue_is_coalesced_across_the_asc_606_rename(
    corpus: sessionmaker[Session],
) -> None:
    """ASC 606 renamed revenue in 2018.

    Reading either tag alone shows a company whose revenue stops or starts
    that year, which looks like a business event and is an accounting-standard
    change.
    """
    _fact(corpus, "Revenues", "2016-09-30", 200.0, "2016-11-01")
    _fact(
        corpus,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "2020-09-30",
        300.0,
        "2020-11-01",
    )
    body = _read(corpus)
    values = {v["period_end"]: v for v in _line(body, "Revenue")["values"]}  # type: ignore[union-attr]
    assert values["2016-09-30"]["value"] == pytest.approx(200.0)
    assert values["2020-09-30"]["value"] == pytest.approx(300.0)


def test_a_line_reported_under_two_tags_is_named(
    corpus: sessionmaker[Session],
) -> None:
    """So a reader does not take the rename for a business event."""
    _fact(corpus, "Revenues", "2016-09-30", 200.0, "2016-11-01")
    _fact(
        corpus,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "2020-09-30",
        300.0,
        "2020-11-01",
    )
    body = _read(corpus)
    assert body["tag_changes"] == ["Revenue"]
    assert "ASC 606" in body["tag_change_note"]  # type: ignore[operator]


def test_a_single_tag_raises_no_rename_warning(
    corpus: sessionmaker[Session],
) -> None:
    """Or the warning is noise and stops being read."""
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 100.0, "2020-11-01")
    body = _read(corpus)
    assert body["tag_changes"] == []
    assert body["tag_change_note"] is None


def test_periods_before_2013_are_marked_as_not_point_in_time(
    corpus: sessionmaker[Session],
) -> None:
    """`knowledge_time` begins in 2009 and the median gap from period end is
    1,574 days, because SEC's XBRL datasets restate history. An early row is a
    later filing being read as a contemporaneous one."""
    _fact(corpus, "NetIncomeLoss", "2008-09-30", 50.0, "2011-07-22")
    body = _read(corpus)
    assert _line(body, "Net income")["values"][0]["point_in_time"] is False  # type: ignore[index]
    assert "not point-in-time" in body["point_in_time_note"]  # type: ignore[operator]


def test_a_modern_only_statement_carries_no_point_in_time_warning(
    corpus: sessionmaker[Session],
) -> None:
    _fact(corpus, "NetIncomeLoss", "2020-09-30", 100.0, "2020-11-01")
    assert _read(corpus)["point_in_time_note"] is None


def test_a_security_with_no_fundamentals_says_why(
    corpus: sessionmaker[Session],
) -> None:
    body = _read(corpus)
    assert body["available"] is False
    assert "2011Q3" in body["note"]  # type: ignore[operator]
