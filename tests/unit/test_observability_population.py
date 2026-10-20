"""Checking the record's purity, not a population somebody constructed.

`check_population` had only ever been handed observations assembled in memory,
including by the Phase 8 audit, which built 50,000 identical values and
confirmed the expected answer. That proves the function works; it does not
prove the **record** is pure, and the record is what a later study would be
drawn from.

The property that matters here is the one a skip would destroy: **a row with no
recorded horizon blocks certification rather than being left out.** A record
that is 90% Swing and 10% unlabelled is not a Swing population — it is a Swing
population plus an unknown, and the unknown is what a later study would
silently absorb.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe
from tradeit.observability.population import check_recorded_scores
from tradeit.portfolio.mandate import Mandate
from tradeit.storage.tables import OpportunityScore

SESSION = dt.date(2015, 6, 1)


def _score(
    session: Session,
    instrument_id: int,
    mandate: str | None = "swing",
    timeframe: str | None = "1d",
) -> None:
    session.add(
        OpportunityScore(
            instrument_id=instrument_id,
            session_date=SESSION,
            direction="long",
            total=0.5,
            components={"s": 0.5},
            run_manifest_id=1,
            mandate=mandate,
            timeframe=timeframe,
        )
    )


def test_a_uniform_record_is_certified_for_its_own_horizon(db_session: Session) -> None:
    for i in range(5):
        _score(db_session, i)
    db_session.flush()
    result = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert result.total_rows == 5
    assert result.unattributable == 0
    assert result.certified is True


def test_the_same_record_is_refused_for_a_horizon_it_is_not(db_session: Session) -> None:
    """The check has to be able to fail, or its pass above says nothing."""
    for i in range(5):
        _score(db_session, i)
    db_session.flush()
    result = check_recorded_scores(
        db_session, mandate=Mandate.RETIREMENT, timeframe=Bartimeframe.D1
    )
    assert result.certified is False
    assert result.report is not None
    assert "undeclared_mandate" in result.report.describe()


def test_one_unlabelled_row_blocks_certification(db_session: Session) -> None:
    """Even though every attributable row agrees.

    This is the whole reason NULLs are counted rather than skipped: skipping
    would let a record half of whose rows are unattributable report a clean
    population, and the certification would mean nothing.
    """
    for i in range(9):
        _score(db_session, i)
    _score(db_session, 99, mandate=None, timeframe=None)
    db_session.flush()

    result = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert result.unattributable == 1
    assert result.report is not None
    assert result.report.admissible, "the attributable rows are themselves clean"
    assert result.certified is False, "an unattributable row did not block certification"
    assert "NOT\nskipped" in result.describe() or "NOT skipped" in result.describe()


def test_a_half_recorded_timeframe_is_also_unattributable(db_session: Session) -> None:
    """A mandate with no timeframe cannot be made into an Observation either."""
    _score(db_session, 1, mandate="swing", timeframe=None)
    db_session.flush()
    result = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert result.unattributable == 1
    assert result.certified is False


def test_an_unrecognised_horizon_string_is_counted_not_raised(
    db_session: Session,
) -> None:
    """A record written by an older or unknown writer is a fact about the
    record. Refusing to report at all would be less useful than reporting that
    it cannot be certified."""
    _score(db_session, 1)
    _score(db_session, 2, mandate="quarterly")
    db_session.flush()
    result = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert result.unattributable == 1
    assert result.total_rows == 2
    assert result.certified is False


def test_an_empty_record_is_not_certified(db_session: Session) -> None:
    """A population that passes because it is empty is the vacuous result this
    codebase has produced twice by promoting away the last instance of a shape.
    """
    result = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert result.total_rows == 0
    assert result.report is None
    assert result.certified is False


def test_the_unit_is_declared_by_the_caller_not_read_from_the_rows(
    db_session: Session,
) -> None:
    """Reading the majority horizon out of the data and checking the data
    against it would always pass — the check would be comparing the record to
    itself, which is the failure `BrokerExecutionService` refuses when it
    reconciles the broker against its own view.
    """
    for i in range(5):
        _score(db_session, i, mandate="day", timeframe="5m")
    db_session.flush()

    # The rows are uniformly Day/5m. Declaring Swing must still refuse.
    refused = check_recorded_scores(db_session, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
    assert refused.certified is False
    # And declaring what they actually are must pass, so the refusal above is
    # about the declaration rather than about the rows being unreadable.
    accepted = check_recorded_scores(db_session, mandate=Mandate.DAY, timeframe=Bartimeframe.M5)
    assert accepted.certified is True
