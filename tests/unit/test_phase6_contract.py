"""The point-in-time contract Phase 6 must satisfy, written before Phase 6 exists.

Order matters here. A contract written after the implementation describes what
was built; a contract written before it constrains what may be built. Phase 6 is
not authorized, nothing in this file scores a fundamental or ranks a company,
and none of it will need rewriting when that phase arrives — it tests the
*storage and read* guarantees that any fundamental scoring must rest on.

Four guarantees, in the order they fail in practice:

1. **A fact is invisible before it was filed.** The single most damaging bug
   available to a fundamentals pipeline, and the easiest to write by accident:
   join on ``period_end`` and every backtest gets the earnings number weeks
   early.
2. **A restatement does not rewrite the past.** As of a date between the
   original filing and its amendment, the *original* number is what a strategy
   could act on. A store that returns today's corrected figure for a historical
   as-of has handed the backtest the auditor's hindsight.
3. **A fiscal quarter is not a calendar quarter.** ``period_end`` is read from
   the data. Deriving it from ``fiscal_year`` and ``fiscal_period`` misplaces
   every non-December filer by up to eleven months — see the ``fiscal_calendar``
   entries in the validation universe.
4. **An estimated timestamp is labelled.** A fact whose knowledge_time came from
   a filing-deadline rule is marked ``ESTIMATED`` and stays marked, because a
   result resting on it is partly a measurement of the rule.

The fixtures here are constructed, not real. That is weaker evidence than a
verified restatement from EDGAR and it is labelled as such in the validation
universe, which carries the instructions for populating the real cases.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.clock import AsOfClock
from tradeit.core.enums import FiscalPeriod, KnowledgeTimeSource, TradingMode
from tradeit.data.packages.pointintime import (
    MINIMUM_FUNDAMENTAL_LAG_DAYS,
    REPORTED_TIME_COLUMNS,
    KnowledgeTimePolicy,
)
from tradeit.data.validation_universe import UniverseCategory, default_universe
from tradeit.storage import tables as t
from tradeit.storage.repositories import FundamentalRepository

UTC = dt.UTC

#: The worked example used throughout. A calendar Q1 that ended 31 March and was
#: filed on 8 May — the ordinary case, whose ordinary handling is what the whole
#: contract is about.
PERIOD_END = dt.date(2020, 3, 31)
FILED_AT = dt.datetime(2020, 5, 8, 20, 15, tzinfo=UTC)
AMENDED_AT = dt.datetime(2020, 11, 12, 21, 30, tzinfo=UTC)


def clock_at(day: dt.date) -> AsOfClock:
    return AsOfClock.at(
        dt.datetime.combine(day, dt.time(23, 59), tzinfo=UTC), mode=TradingMode.BACKTEST
    )


def add_instrument(session: Session, instrument_id: int = 1) -> None:
    session.add(
        t.Instrument(
            instrument_id=instrument_id,
            primary_exchange="XNYS",
            asset_class="common_stock",
            name="Contract Co",
            source="test",
        )
    )
    session.flush()


def add_fact(
    session: Session,
    *,
    value: Decimal,
    knowledge_time: dt.datetime,
    period_end: dt.date = PERIOD_END,
    fiscal_period: str = str(FiscalPeriod.Q1),
    fiscal_year: int = 2020,
    metric: str = "revenue",
    knowledge_source: KnowledgeTimeSource = KnowledgeTimeSource.REPORTED,
    instrument_id: int = 1,
) -> t.FundamentalFact:
    row = t.FundamentalFact(
        instrument_id=instrument_id,
        metric=metric,
        fiscal_period=fiscal_period,
        fiscal_year=fiscal_year,
        period_end=period_end,
        event_time=dt.datetime.combine(period_end, dt.time(23, 59), tzinfo=UTC),
        knowledge_time=knowledge_time,
        knowledge_source=str(knowledge_source),
        value=value,
        unit="USD",
        source="test",
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def repo(db_session: Session) -> FundamentalRepository:
    add_instrument(db_session)
    return FundamentalRepository(db_session)


# ---------------------------------------------------------------------------
# 1. A fact is invisible before it was filed
# ---------------------------------------------------------------------------


class TestVisibility:
    def test_a_fact_is_invisible_before_its_filing(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        # The day after the quarter ended, and a month after — still nothing.
        assert repo.latest_metric(clock_at(dt.date(2020, 4, 1)), 1, "revenue") is None
        assert repo.latest_metric(clock_at(dt.date(2020, 5, 1)), 1, "revenue") is None

    def test_a_fact_is_visible_after_its_filing(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        fact = repo.latest_metric(clock_at(dt.date(2020, 5, 9)), 1, "revenue")
        assert fact is not None
        assert fact.value == Decimal(1000)

    def test_the_boundary_is_the_filing_instant_not_the_filing_day(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        # Filed 20:15 UTC. A clock at 19:00 that day must not see it: a report
        # released after the close is not actionable during that session.
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        before = AsOfClock.at(dt.datetime(2020, 5, 8, 19, 0, tzinfo=UTC), mode=TradingMode.BACKTEST)
        after = AsOfClock.at(dt.datetime(2020, 5, 8, 20, 16, tzinfo=UTC), mode=TradingMode.BACKTEST)
        assert repo.latest_metric(before, 1, "revenue") is None
        assert repo.latest_metric(after, 1, "revenue") is not None

    def test_period_end_is_never_the_visibility_boundary(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        """The requirement the gate calls non-negotiable, stated as a test.

        A quarter ending 31 March was not knowable on 31 March. If this ever
        passes at ``period_end``, the join is on the wrong column and every
        result built on it is reading the future.
        """
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        assert repo.latest_metric(clock_at(PERIOD_END), 1, "revenue") is None

    def test_a_series_read_is_bounded_the_same_way_as_a_point_read(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        # Two different code paths, one guarantee. A series read that forgot the
        # bound would leak precisely where nobody looks.
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        add_fact(
            db_session,
            value=Decimal(1100),
            knowledge_time=dt.datetime(2020, 8, 6, 20, 15, tzinfo=UTC),
            period_end=dt.date(2020, 6, 30),
            fiscal_period=str(FiscalPeriod.Q2),
        )
        early = repo.series(clock_at(dt.date(2020, 5, 20)), 1, "revenue")
        late = repo.series(clock_at(dt.date(2020, 9, 1)), 1, "revenue")
        assert len(early) == 1
        assert len(late) == 2


# ---------------------------------------------------------------------------
# 2. A restatement does not rewrite the past
# ---------------------------------------------------------------------------


class TestRestatement:
    """The case that separates a point-in-time store from a history table."""

    def setup_facts(self, session: Session) -> None:
        add_fact(session, value=Decimal(1000), knowledge_time=FILED_AT)
        add_fact(session, value=Decimal(880), knowledge_time=AMENDED_AT)

    def test_before_the_amendment_the_original_number_is_returned(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        self.setup_facts(db_session)
        fact = repo.latest_metric(clock_at(dt.date(2020, 7, 1)), 1, "revenue")
        assert fact is not None
        assert fact.value == Decimal(1000), (
            "a read as of July returned the November correction; the store is "
            "handing the backtest the auditor's hindsight"
        )

    def test_after_the_amendment_the_corrected_number_is_returned(
        self, db_session: Session, repo: FundamentalRepository
    ) -> None:
        self.setup_facts(db_session)
        fact = repo.latest_metric(clock_at(dt.date(2021, 1, 1)), 1, "revenue")
        assert fact is not None
        assert fact.value == Decimal(880)

    def test_both_versions_are_retained(self, db_session: Session) -> None:
        # Not overwritten. The original is what somebody acted on, and deleting
        # it makes the historical decision unexplainable.
        self.setup_facts(db_session)
        rows = db_session.scalars(select(t.FundamentalFact)).all()
        assert {r.value for r in rows} == {Decimal(1000), Decimal(880)}
        assert len({r.knowledge_time for r in rows}) == 2

    def test_the_two_versions_share_a_fiscal_identity(self, db_session: Session) -> None:
        self.setup_facts(db_session)
        rows = db_session.scalars(select(t.FundamentalFact)).all()
        assert len({(r.metric, r.fiscal_year, r.fiscal_period, r.period_end) for r in rows}) == 1


# ---------------------------------------------------------------------------
# 3. A fiscal quarter is not a calendar quarter
# ---------------------------------------------------------------------------


class TestFiscalCalendar:
    def test_period_end_is_stored_not_derived(self, db_session: Session) -> None:
        # A September-fiscal-year filer's FY2023 Q1 ends in December 2022. Any
        # code that derives period_end from (fiscal_year, fiscal_period) places
        # this eleven months wrong.
        add_instrument(db_session)
        row = add_fact(
            db_session,
            value=Decimal(500),
            knowledge_time=dt.datetime(2023, 2, 2, 21, 30, tzinfo=UTC),
            period_end=dt.date(2022, 12, 31),
            fiscal_period=str(FiscalPeriod.Q1),
            fiscal_year=2023,
        )
        assert row.period_end.year == 2022
        assert row.fiscal_year == 2023

    def test_the_filing_lag_is_measured_from_the_real_period_end(self) -> None:
        policy = KnowledgeTimePolicy(require_reported_fundamentals=False)
        lag = policy.lag_for(FiscalPeriod.Q1)
        available = dt.date(2022, 12, 31) + dt.timedelta(days=lag)
        assert available.year == 2023
        # Not derived from the fiscal-year label, which would put it in 2024.
        assert available < dt.date(2023, 4, 1)

    def test_the_universe_carries_non_december_filers(self) -> None:
        universe = default_universe()
        fiscal = universe.of_category(UniverseCategory.FISCAL_CALENDAR)
        assert fiscal, "no fiscal-calendar stress cases; the contract is untested on real names"
        assert len(fiscal) >= 4
        for instrument in fiscal:
            assert instrument.note, f"{instrument.ticker} does not say what it stresses"

    def test_the_restatement_category_documents_why_it_is_empty(self) -> None:
        # Deliberately unpopulated: naming a company and a quarter would be
        # asserting an accounting history nobody in this environment can check.
        universe = default_universe()
        assert universe.of_category(UniverseCategory.RESTATEMENT) == []


# ---------------------------------------------------------------------------
# 4. An estimated timestamp stays labelled
# ---------------------------------------------------------------------------


class TestProvenanceSurvivesStorage:
    def test_an_estimated_timestamp_is_readable_from_the_stored_row(
        self, db_session: Session
    ) -> None:
        add_instrument(db_session)
        row = add_fact(
            db_session,
            value=Decimal(1000),
            knowledge_time=dt.datetime(2020, 5, 15, 20, tzinfo=UTC),
            knowledge_source=KnowledgeTimeSource.ESTIMATED,
        )
        assert row.knowledge_source == str(KnowledgeTimeSource.ESTIMATED)

    def test_a_reported_and_an_estimated_fact_are_distinguishable_by_query(
        self, db_session: Session
    ) -> None:
        add_instrument(db_session)
        add_fact(db_session, value=Decimal(1000), knowledge_time=FILED_AT)
        add_fact(
            db_session,
            value=Decimal(2000),
            knowledge_time=dt.datetime(2020, 8, 14, 20, tzinfo=UTC),
            period_end=dt.date(2020, 6, 30),
            fiscal_period=str(FiscalPeriod.Q2),
            knowledge_source=KnowledgeTimeSource.ESTIMATED,
        )
        estimated = db_session.scalars(
            select(t.FundamentalFact).where(t.FundamentalFact.knowledge_source == "estimated")
        ).all()
        assert len(estimated) == 1
        assert estimated[0].value == Decimal(2000)

    def test_a_filing_may_not_be_dated_at_its_own_period_end(self) -> None:
        assert MINIMUM_FUNDAMENTAL_LAG_DAYS >= 1

    def test_the_columns_that_carry_a_real_publication_instant_are_named(self) -> None:
        # The linkage design: when the fundamentals export carries no timestamp,
        # one of these columns — supplied directly or joined from the FILINGS
        # dataset — is what turns an ESTIMATED row into a REPORTED one.
        assert "filing_timestamp" in REPORTED_TIME_COLUMNS
        assert "filed_at" in REPORTED_TIME_COLUMNS
        # Canonical contract names come first, because normalization renames
        # source columns onto them before this list is consulted.
        assert REPORTED_TIME_COLUMNS.index("filing_timestamp") < REPORTED_TIME_COLUMNS.index(
            "filed_at"
        )


# ---------------------------------------------------------------------------
# What this contract deliberately does not test
# ---------------------------------------------------------------------------


def test_the_contract_evaluates_nothing() -> None:
    """Phase 6 is not authorized, and this file must not anticipate it.

    The contract is about *when a number is visible*, never about what the
    number is worth. If a valuation helper ever appears in this module, the
    boundary between "the data layer is correct" and "the valuation model is
    good" has been crossed in the place least likely to be noticed.

    Only module-level helpers are inspected; the test functions describe what
    they forbid and would otherwise trip the check by naming it.
    """
    import tests.unit.test_phase6_contract as module

    forbidden = ("score", "rank", "valuation", "composite", "attractive")
    helpers = [name.lower() for name in dir(module) if not name.startswith(("_", "test", "Test"))]
    assert not [n for n in helpers if any(term in n for term in forbidden)]
