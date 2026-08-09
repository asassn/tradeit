"""When a fact became knowable — the requirement that is not negotiable.

A quarter ending March 31 was not knowable on March 31. The company had not
closed its books, no filing existed, and nobody outside the finance department
could have acted on the number. Treating the period-end date as the availability
date is the single most common way a backtest invents profit: it hands the
strategy every earnings surprise weeks before the market saw it, and the
resulting equity curve is not merely optimistic, it is describing a different
universe.

So this module refuses to let ``knowledge_time`` equal ``period_end`` for any
fundamental fact, under any configuration. There is no flag to turn that off.

Three routes to a knowledge time, in strict order of preference:

1. **Reported.** The vendor supplied a filing or publication timestamp. Use it,
   source ``REPORTED``. This is the only route that is evidence rather than
   assumption, and it is the reason ``FILINGS`` exists as a dataset.
2. **Structural.** The fact's own event defines when it existed — a daily bar is
   knowable when its session closes. Source ``VENDOR_INGEST``, because the
   placement is exact but the fact reached us through a vendor.
3. **Estimated.** A regulatory-deadline lag applied to the period end. Source
   ``ESTIMATED``, counted per dataset, and reported. Any measurement resting on
   these rows is partly a measurement of the lag rule, and the import report
   says so in those words.

Route 3 is **off by default.** A package whose fundamentals carry no filing
timestamp quarantines them, with a message naming the columns that would fix it,
because an estimated fundamental timestamp is the assumption most likely to be
forgotten and the one whose consequences are largest. An operator who has read
the tradeoff turns it on; nobody inherits it.

The estimated lags are **filing deadlines, not typical filing dates.** Most
issuers file earlier, so the deadline systematically places facts *later* than
they were truly knowable. That direction is deliberate: it costs a strategy
opportunities it might really have had, and never grants it one it did not.
Erring the other way would manufacture exactly the edge this module exists to
prevent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tradeit.core.enums import FiscalPeriod, KnowledgeTimeSource
from tradeit.data.packages.spec import FUNDAMENTAL_DATASETS, DatasetKind
from tradeit.data.packages.stages import PointInTimeRecord, ValidatedRecord
from tradeit.errors import DataError

#: Columns that, if mapped, carry a real publication instant. Order matters:
#: the accepted timestamp is the first of these present, because a filing
#: acceptance time is stronger evidence than a vendor's own ingest time.
REPORTED_TIME_COLUMNS: tuple[str, ...] = (
    # The canonical contract names first — these are what normalization produces.
    "filing_timestamp",
    "announced_time",
    # Then names a manifest may have mapped through unchanged.
    "filed_at",
    "filing_date",
    "accepted_at",
    "published_at",
    "announced_at",
    "report_date",
    "knowledge_time",
)

#: SEC deadlines for a non-accelerated filer, in calendar days after period end.
#: Quarterly reports on Form 10-Q, annual on 10-K. Used only when route 1 and
#: route 2 are both unavailable.
DEFAULT_QUARTERLY_LAG_DAYS = 45
DEFAULT_ANNUAL_LAG_DAYS = 90

#: The floor. A fundamental fact may never be marked knowable within this many
#: days of its own period end, whatever a file claims, because a report filed
#: the same week its quarter closed is a data error rather than a fast filer.
MINIMUM_FUNDAMENTAL_LAG_DAYS = 1

#: Datasets that describe *identity* rather than events: what a security is,
#: what an exchange is called. They carry no date of their own, so they are
#: placed at the package's export date.
#:
#: This is honest but it is not free, and the honesty is the point. An
#: instrument master exported today lists today's roster with today's
#: classification. A company reclassified in 2018 appears under its 2024 sector
#: for its entire history, and a security that was renamed appears only under
#: its current name. That is a real point-in-time hazard living inside a
#: reference file, and it is why ``SECTORS`` and ``SYMBOL_MAPPINGS`` are
#: interval-based datasets rather than columns on the instrument master. The
#: import report states the limitation once per reference dataset instead of
#: letting the rows arrive looking as timeless as they pretend to be.
REFERENCE_DATASETS: frozenset[DatasetKind] = frozenset(
    {DatasetKind.INSTRUMENTS, DatasetKind.EXCHANGES}
)

#: The caveat attached to every reference row, quoted in the import report.
REFERENCE_SNAPSHOT_CAVEAT = (
    "reference rows carry no date of their own and are placed at the package "
    "export date. The roster and its classifications are therefore an "
    "export-date snapshot: a security reclassified or renamed during the "
    "covered period appears throughout under its export-date values."
)


@dataclass(frozen=True, slots=True)
class KnowledgeTimePolicy:
    """How to place facts in time when the vendor did not.

    ``session_close`` is the exchange-local instant a daily bar is complete.
    16:00 New York is the default; an operator importing a different venue sets
    it rather than accepting a value that is wrong by hours for their market.
    """

    timezone: str = "America/New_York"
    session_close: dt.time = dt.time(16, 0)
    #: Vendor processing delay added to a session close. Zero by default: the
    #: bar existed at the close even if the file arrived later, and inflating
    #: this would push signals a day forward for no evidential reason.
    bar_publication_lag: dt.timedelta = dt.timedelta(0)
    quarterly_lag_days: int = DEFAULT_QUARTERLY_LAG_DAYS
    annual_lag_days: int = DEFAULT_ANNUAL_LAG_DAYS
    #: Quarantine fundamental and earnings rows that carry no publication
    #: timestamp, rather than estimating one.
    #:
    #: **Default on.** An estimated fundamental timestamp is the assumption most
    #: likely to be forgotten and the one whose consequences are largest, so the
    #: safe behaviour is the one an operator gets without thinking about it. The
    #: quarantine message names the columns that would fix it and says exactly
    #: what turning this off means. Turning it off is a defensible choice — a
    #: deadline-based estimate is honest and it is marked ESTIMATED on every row
    #: — but it should be a choice somebody made, not a default they inherited.
    require_reported_fundamentals: bool = True

    def __post_init__(self) -> None:
        if self.quarterly_lag_days < MINIMUM_FUNDAMENTAL_LAG_DAYS:
            raise DataError(
                f"quarterly_lag_days={self.quarterly_lag_days} is below the "
                f"{MINIMUM_FUNDAMENTAL_LAG_DAYS}-day floor. A quarter is not "
                "knowable on the day it ends; see docs/POINT_IN_TIME.md."
            )
        if self.annual_lag_days < self.quarterly_lag_days:
            raise DataError(
                "annual_lag_days must be at least quarterly_lag_days; annual "
                "reports are audited and file later, never sooner."
            )

    @property
    def zone(self) -> dt.tzinfo:
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.timezone)

    def lag_for(self, period: object) -> int:
        if period in (FiscalPeriod.FY, "FY", "fy", "annual", "A"):
            return self.annual_lag_days
        return self.quarterly_lag_days

    def close_instant(self, session: dt.date) -> dt.datetime:
        local = dt.datetime.combine(session, self.session_close, tzinfo=self.zone)
        return (local + self.bar_publication_lag).astimezone(dt.UTC)


class PointInTimeError(DataError):
    """A row could not be placed in time honestly. It quarantines."""


def _reported_time(record: ValidatedRecord) -> tuple[dt.datetime | None, str]:
    for column in REPORTED_TIME_COLUMNS:
        value = record.values.get(column)
        if value is None:
            continue
        if isinstance(value, dt.datetime):
            return _as_utc(value, column), column
        if isinstance(value, dt.date):
            # A filing *date* without a time. Place it at end of day in the
            # package's zone: a report filed on the 15th was not actionable
            # before the 15th opened, and the close is the first moment we can
            # be sure it was public.
            return None, column
    return None, ""


def _as_utc(value: dt.datetime, column: str) -> dt.datetime:
    if value.tzinfo is None:
        raise PointInTimeError(
            f"{column} is a naive timestamp. Normalization should have localised "
            "it; a naive knowledge_time cannot be compared against an as-of clock."
        )
    return value.astimezone(dt.UTC)


def assign_knowledge_time(
    record: ValidatedRecord,
    policy: KnowledgeTimePolicy,
    *,
    export_date: dt.date | None = None,
) -> PointInTimeRecord:
    """Place one validated row in time, recording how.

    ``export_date`` comes from the manifest and is used only for reference
    datasets, which have no date of their own. Omitting it for a package that
    contains one is an error rather than a default, because the alternative —
    quietly using today — would make the same file import differently tomorrow.
    """
    dataset = record.dataset
    if dataset is DatasetKind.EARNINGS:
        return _earnings(record, policy)
    if dataset in FUNDAMENTAL_DATASETS:
        return _fundamental(record, policy)
    if dataset in REFERENCE_DATASETS:
        return _reference(record, policy, export_date)
    return _market(record, policy)


def _reference(
    record: ValidatedRecord,
    policy: KnowledgeTimePolicy,
    export_date: dt.date | None,
) -> PointInTimeRecord:
    reported, column = _reported_time(record)
    if reported is not None:
        return PointInTimeRecord(
            validated=record,
            knowledge_time=reported,
            knowledge_source=KnowledgeTimeSource.REPORTED,
            knowledge_note=f"vendor supplied {column}",
        )
    if export_date is None:
        raise PointInTimeError(
            f"{record.dataset} is a reference dataset with no date of its own, "
            "and no package export date was supplied to place it. This is a "
            "caller error rather than a data error."
        )
    return PointInTimeRecord(
        validated=record,
        knowledge_time=policy.close_instant(export_date),
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        knowledge_note=(
            f"placed at the package export date {export_date}. {REFERENCE_SNAPSHOT_CAVEAT}"
        ),
    )


def _market(record: ValidatedRecord, policy: KnowledgeTimePolicy) -> PointInTimeRecord:
    reported, column = _reported_time(record)
    if reported is not None:
        return PointInTimeRecord(
            validated=record,
            knowledge_time=reported,
            knowledge_source=KnowledgeTimeSource.REPORTED,
            knowledge_note=f"vendor supplied {column}",
        )

    session = _structural_date(record)
    if session is None:
        raise PointInTimeError(
            f"{record.dataset} row has no date the importer can place in time. "
            f"Expected one of session_date, ex_date, effective_date, valid_from, "
            f"delisted_date, or a mapped timestamp column "
            f"({', '.join(REPORTED_TIME_COLUMNS)})."
        )
    return PointInTimeRecord(
        validated=record,
        knowledge_time=policy.close_instant(session),
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        knowledge_note=(
            f"placed at the {policy.session_close} {policy.timezone} close of {session}, "
            "the session in which the fact came into existence"
        ),
    )


def _structural_date(record: ValidatedRecord) -> dt.date | None:
    for column in (
        "session_date",
        "bar_start",
        "ex_date",
        "effective_date",
        "delisted_date",
        "last_trade_date",
        "valid_from",
        "date",
    ):
        value = record.values.get(column)
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
    return None


def _fundamental(record: ValidatedRecord, policy: KnowledgeTimePolicy) -> PointInTimeRecord:
    period_end = record.values.get("period_end")
    if isinstance(period_end, dt.datetime):
        period_end = period_end.date()
    if not isinstance(period_end, dt.date):
        raise PointInTimeError("a fundamental fact needs period_end to be placed in time at all")

    reported, column = _reported_time(record)
    if reported is not None:
        floor = _floor(period_end, policy)
        if reported < floor:
            raise PointInTimeError(
                f"{column}={reported.isoformat()} is within "
                f"{MINIMUM_FUNDAMENTAL_LAG_DAYS} day(s) of period_end={period_end}. "
                "A report cannot be filed before the period it reports has closed "
                "and been compiled; importing this would hand every backtest the "
                "result early. Fix the export or drop the column."
            )
        return PointInTimeRecord(
            validated=record,
            knowledge_time=reported,
            knowledge_source=KnowledgeTimeSource.REPORTED,
            knowledge_note=f"vendor supplied {column}",
        )

    filing_date = _filing_date(record)
    if filing_date is not None:
        if filing_date <= period_end:
            raise PointInTimeError(
                f"filing date {filing_date} is not after period_end={period_end}; "
                "the report would predate the period it reports on"
            )
        instant = policy.close_instant(filing_date)
        return PointInTimeRecord(
            validated=record,
            knowledge_time=instant,
            knowledge_source=KnowledgeTimeSource.REPORTED,
            knowledge_note=(
                f"filing date {filing_date} with no time; placed at that day's close, "
                "the first instant the filing was certainly public"
            ),
        )

    if policy.require_reported_fundamentals:
        raise PointInTimeError(
            "no filing or publication timestamp, and the policy requires one. "
            f"Map any of {', '.join(REPORTED_TIME_COLUMNS)} in the manifest, or "
            "supply the FILINGS dataset, or set require_reported_fundamentals "
            "to false and accept ESTIMATED timestamps with the caveat they carry."
        )

    lag = policy.lag_for(record.values.get("fiscal_period"))
    available = period_end + dt.timedelta(days=lag)
    return PointInTimeRecord(
        validated=record,
        knowledge_time=policy.close_instant(available),
        knowledge_source=KnowledgeTimeSource.ESTIMATED,
        knowledge_note=(
            f"no filing timestamp in the package; placed {lag} days after "
            f"period_end={period_end} using the regulatory filing deadline. This "
            "is an assumption, not an observation, and any result computed from "
            "this row is partly a measurement of the lag rule."
        ),
    )


def _earnings(record: ValidatedRecord, policy: KnowledgeTimePolicy) -> PointInTimeRecord:
    """Place an earnings-calendar entry.

    The asymmetry matters. An earnings *date* is announced weeks ahead, and a
    screen that knows about it early can avoid holding into the print — that is
    a real, legitimate edge, and ``announced_time`` is what licenses it.

    With no announcement timestamp the entry is placed on the scheduled session
    itself. That is the conservative direction: proximity filters then see
    nothing coming and behave as if every report were a surprise. It costs the
    strategy caution it could really have exercised, which is the error worth
    making. The opposite default — assuming the calendar was always known —
    would hand every backtest a lookahead nobody would notice.
    """
    announced = record.values.get("announced_time")
    if isinstance(announced, dt.datetime):
        return PointInTimeRecord(
            validated=record,
            knowledge_time=_as_utc(announced, "announced_time"),
            knowledge_source=KnowledgeTimeSource.REPORTED,
            knowledge_note="vendor supplied announced_time",
        )
    scheduled = record.values.get("scheduled_date")
    if not isinstance(scheduled, dt.date):
        raise PointInTimeError("an earnings entry needs scheduled_date to be placed in time at all")
    if policy.require_reported_fundamentals:
        raise PointInTimeError(
            "no announced_time, and the policy requires a reported timestamp. "
            "Map announced_time in the manifest, or set "
            "require_reported_fundamentals to false to accept the conservative "
            "assumption that the date was knowable only on the day itself."
        )
    return PointInTimeRecord(
        validated=record,
        knowledge_time=policy.close_instant(scheduled),
        knowledge_source=KnowledgeTimeSource.ESTIMATED,
        knowledge_note=(
            "no announcement timestamp in the package; the entry is treated as "
            "knowable only on its scheduled session. Proximity filters computed "
            "from these rows will see fewer upcoming events than a live system "
            "would — conservative, but not what a live system experienced."
        ),
    )


def _floor(period_end: dt.date, policy: KnowledgeTimePolicy) -> dt.datetime:
    return policy.close_instant(period_end + dt.timedelta(days=MINIMUM_FUNDAMENTAL_LAG_DAYS))


def _filing_date(record: ValidatedRecord) -> dt.date | None:
    for column in REPORTED_TIME_COLUMNS:
        value = record.values.get(column)
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
    return None


def describe_policy(policy: KnowledgeTimePolicy) -> str:
    """Human-readable summary for the import report."""
    return "\n".join(
        [
            "Knowledge-time policy",
            f"  market facts   : session close {policy.session_close} {policy.timezone}"
            + (
                f" + {policy.bar_publication_lag}"
                if policy.bar_publication_lag
                else " (no vendor lag added)"
            ),
            f"  quarterly facts: period_end + {policy.quarterly_lag_days}d when no filing "
            "timestamp is supplied (ESTIMATED)",
            f"  annual facts   : period_end + {policy.annual_lag_days}d when no filing "
            "timestamp is supplied (ESTIMATED)",
            f"  reported floor : a filing may not be dated within "
            f"{MINIMUM_FUNDAMENTAL_LAG_DAYS}d of its own period end",
            "  strict mode    : "
            + (
                "on — fundamentals without a filing timestamp are quarantined"
                if policy.require_reported_fundamentals
                else "off — fundamentals without a filing timestamp are ESTIMATED and counted"
            ),
        ]
    )


#: Datasets whose rows may never be placed at their period end, checked by test.
PERIOD_END_FORBIDDEN: frozenset[DatasetKind] = FUNDAMENTAL_DATASETS

__all__ = [
    "DEFAULT_ANNUAL_LAG_DAYS",
    "DEFAULT_QUARTERLY_LAG_DAYS",
    "MINIMUM_FUNDAMENTAL_LAG_DAYS",
    "PERIOD_END_FORBIDDEN",
    "REFERENCE_DATASETS",
    "REFERENCE_SNAPSHOT_CAVEAT",
    "REPORTED_TIME_COLUMNS",
    "KnowledgeTimePolicy",
    "PointInTimeError",
    "assign_knowledge_time",
    "describe_policy",
]
