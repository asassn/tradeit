"""The as-filed statement spine, with the dates that make it readable.

Three rules this route encodes, each of which produces a confidently wrong
answer if skipped.

**A value is what was FIRST filed.** A figure for one period appears in its
own filing and then again, as a comparative, in later ones — sometimes
restated. Showing the latest row for a period shows the restatement, which is
not what anybody could act on at the time. This reads the earliest filing per
period, the rule `research01.fundamental_signals` already applies.

**Revenue lives under two tags and they are the same line.** ASC 606 renamed
it in 2018: `Revenues` before, `RevenueFromContractWithCustomerExcludingAssessedTax`
after. Measured over 40 securities, 25 carry the first and 20 the second.
Reading either alone shows a company whose revenue stops or starts in 2018,
which looks like a business event and is an accounting-standard change. They
are coalesced, and the payload names which tag supplied each year.

**The corpus's fundamentals are not point-in-time before roughly 2013.**
`knowledge_time` begins in 2009 and the median gap from `period_end` is 1,574
days, because SEC's XBRL datasets restate history — Apple's
`StockholdersEquity` reaches back to 2006 with a first knowledge time of
2009-07-22. A reader taking those early rows as contemporaneous is reading a
2009 filing as if it were a 2006 one, and that caveat travels with every
response rather than living in a document.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

__all__ = ["LINES", "NOT_POINT_IN_TIME_BEFORE", "fundamentals_payload"]

#: The year before which this corpus's fundamentals are not point-in-time.
NOT_POINT_IN_TIME_BEFORE = 2013

#: Quarters a value spans. **A balance is an instant and a flow is not**, and
#: conflating them is how the balance sheet disappeared from the first version
#: of this route: it queried `duration_qtrs = 4` for every line, which is right
#: for revenue and net income and excludes every balance-sheet item, because
#: total assets is a position on a date rather than a sum over a year.
#: `fundamental_signals` draws the same distinction with `instant_known_by`.
INSTANT = 0
FISCAL_YEAR = 4

#: The statement spine, as (statement, label, duration, tags) in reading order.
#: Several lines have more than one tag because the filer's vocabulary changed;
#: the first tag present for a period wins, and the payload says which it was.
LINES: list[tuple[str, str, int, tuple[str, ...]]] = [
    (
        "income",
        "Revenue",
        FISCAL_YEAR,
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    ),
    ("income", "Gross profit", FISCAL_YEAR, ("GrossProfit",)),
    ("income", "Operating income", FISCAL_YEAR, ("OperatingIncomeLoss",)),
    ("income", "Net income", FISCAL_YEAR, ("NetIncomeLoss",)),
    ("balance", "Total assets", INSTANT, ("Assets",)),
    ("balance", "Total liabilities", INSTANT, ("Liabilities",)),
    ("balance", "Shareholders' equity", INSTANT, ("StockholdersEquity",)),
    ("balance", "Cash", INSTANT, ("CashAndCashEquivalentsAtCarryingValue",)),
    ("balance", "Shares outstanding", INSTANT, ("CommonStockSharesOutstanding",)),
    (
        "cash flow",
        "Operating cash flow",
        FISCAL_YEAR,
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    (
        "cash flow",
        "Capital expenditure",
        FISCAL_YEAR,
        ("PaymentsToAcquirePropertyPlantAndEquipment",),
    ),
]

#: Fiscal years shown. Enough to see a trend without turning a statement into
#: a data dump.
YEARS = 10


def fundamentals_payload(session: Session, security_id: int) -> dict[str, Any]:
    """One security's annual statement spine, as first filed."""
    wanted = sorted({tag for _, _, _, tags in LINES for tag in tags})

    rows = session.execute(
        text(
            "select metric, coalesce(duration_qtrs, 0) as dur, period_end, value, "
            "       date(knowledge_time) as known "
            "from ("
            "  select metric, duration_qtrs, period_end, value, knowledge_time, "
            "         row_number() over ("
            "           partition by metric, duration_qtrs, period_end "
            # First filed, not latest: a later row for the same period is a
            # comparative or a restatement, and neither was knowable then.
            "           order by knowledge_time asc"
            "         ) as rn "
            "  from security_fundamental_facts "
            "  where security_id = :sid and value is not null "
            # Both durations, because a balance is an instant and a flow is
            # not. Filtering to one excluded the entire balance sheet.
            "    and coalesce(duration_qtrs, 0) in (0, 4) and metric in :tags"
            ") where rn = 1 order by period_end desc"
        ).bindparams(bindparam("tags", expanding=True), sid=security_id),
        {"tags": wanted},
    ).all()

    if not rows:
        return {
            "security_id": security_id,
            "available": False,
            "note": (
                "no annual fundamentals for this security. The corpus's spine comes "
                "from the SEC Financial Statement Data Sets and effectively begins "
                "2011Q3 — the earlier archives are headers"
            ),
        }

    by_tag: dict[tuple[str, int, str], tuple[float, str]] = {}
    for row in rows:
        by_tag[(str(row.metric), int(row.dur), str(row.period_end))] = (
            float(row.value),
            str(row.known),
        )

    # **The columns are fiscal years, and the balance sheet is "as at" each
    # of them.** Instants land on quarter ends and flows on fiscal-year ends,
    # so taking every period_end that appears produced a column set where a
    # 2025-12-31 balance sat beside a 2025-09-30 income statement and each
    # left the other blank — a statement with holes in it that are an artefact
    # of the join rather than of the filing.
    #
    # Flow periods define the columns; a balance is read at exactly that date.
    # A security with balances and no flows falls back to its own instants,
    # so it is not reduced to no statement at all.
    flow_periods = sorted(
        {str(row.period_end) for row in rows if int(row.dur) == FISCAL_YEAR},
        reverse=True,
    )
    periods = (flow_periods or sorted({str(row.period_end) for row in rows}, reverse=True))[:YEARS]

    lines: list[dict[str, Any]] = []
    for statement, label, duration, tags in LINES:
        values: list[dict[str, Any]] = []
        for period in periods:
            found: dict[str, Any] = {"period_end": period, "value": None, "tag": None}
            for tag in tags:
                hit = by_tag.get((tag, duration, period))
                if hit is not None:
                    found = {
                        "period_end": period,
                        "value": hit[0],
                        "first_filed": hit[1],
                        "tag": tag,
                        # Flagged per cell, because the gap is per filing:
                        # a 2006 period first filed in 2009 is a 2009
                        # document being read as a 2006 one.
                        "point_in_time": int(period[:4]) >= NOT_POINT_IN_TIME_BEFORE,
                    }
                    break
            values.append(found)
        if any(value["value"] is not None for value in values):
            lines.append({"statement": statement, "label": label, "values": values})

    # Named rather than left for the reader to spot: a line whose tag changes
    # mid-series is an accounting-standard change, not a business event.
    switched = [
        line["label"] for line in lines if len({v["tag"] for v in line["values"] if v["tag"]}) > 1
    ]

    earliest = periods[-1] if periods else None
    return {
        "security_id": security_id,
        "available": True,
        "periods": periods,
        "lines": lines,
        "tag_changes": switched,
        "tag_change_note": (
            None
            if not switched
            else (
                f"{', '.join(switched)} is reported under more than one tag across "
                "these years. ASC 606 renamed revenue in 2018; a line that appears "
                "to stop and restart is an accounting-standard change, not a "
                "business event"
            )
        ),
        "first_filed_note": (
            "every figure is the value as FIRST filed, with the date it became "
            "public. A later filing restating the same period is not shown: it was "
            "not knowable at the time and acting on it is look-ahead"
        ),
        "point_in_time_note": (
            None
            if earliest is None or int(earliest[:4]) >= NOT_POINT_IN_TIME_BEFORE
            else (
                f"periods before {NOT_POINT_IN_TIME_BEFORE} are marked, and they are "
                "not point-in-time. This corpus's `knowledge_time` begins in 2009 and "
                "the median gap from period end to knowledge time is 1,574 days, "
                "because SEC's XBRL datasets restate history — so an early row is a "
                "later filing being read as a contemporaneous one"
            )
        ),
    }
