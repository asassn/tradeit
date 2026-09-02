"""Reading the SEC Financial Statement Data Sets.

The two properties that carry the weight, both found in real 2015q1 data before
being pinned here:

1. **The same tag at the same period end appears with different durations.**
   Bell Atlantic filed ``Revenues`` for the quarter ending 2014-12-31 at
   $33.2bn and for the *year* ending 2014-12-31 at $127.1bn. Without
   ``duration_qtrs`` in the key these collide, one silently overwrites the
   other, and a quarterly figure is left masquerading as an annual one.

2. **``filed`` dates the fact, never ``ddate``.** GM's 2012 revenue appears in a
   filing submitted 2015-02-04. Dating it by its period would make a 2012
   comparative usable in 2012, when the restated view of it did not exist until
   three years later.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import RejectReason, import_fsds_quarter, read_quarter
from tradeit.storage.tables import Issuer, IssuerIdentifier, Security, SecurityFundamentalFact

SUB_HEADER = "adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled\n"
NUM_HEADER = "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"
ADSH = "0000000000-15-000001"


def _zip(tmp_path: Path, sub_rows: list[str], num_rows: list[str]) -> Path:
    path = tmp_path / "2015q1.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("sub.txt", SUB_HEADER + "".join(sub_rows))
        z.writestr("num.txt", NUM_HEADER + "".join(num_rows))
    return path


def _sub(cik: int = 320193, filed: str = "20150204", period: str = "20141231") -> str:
    return f"{ADSH}\t{cik}\tTEST CO\t10-K\t{period}\t2015\tFY\t{filed}\n"


def _num(tag: str, ddate: str, qtrs: int, value: str, segments: str = "", coreg: str = "") -> str:
    return f"{ADSH}\t{tag}\tus-gaap/2014\t{ddate}\t{qtrs}\tUSD\t{segments}\t{coreg}\t{value}\t\n"


def _issuer_with_security(session: Session, cik: str) -> Security:
    issuer = Issuer(display_name="TEST CO", source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value=cik,
            value_normalized=str(int(cik)),
            role="primary",
            citation="test",
            source="test",
        )
    )
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return security


class TestDurationIsPartOfIdentity:
    def test_quarterly_and_annual_of_one_tag_and_date_both_survive(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The Bell Atlantic case, from real 2015q1 data.

        Same tag, same period end, two durations. Both must land, and the
        annual figure must not be readable as the quarterly one.
        """
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub()],
            [
                _num("Revenues", "20141231", 1, "33192000000.0000"),
                _num("Revenues", "20141231", 4, "127079000000.0000"),
            ],
        )
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 2

        rows = db_session.scalars(
            select(SecurityFundamentalFact).where(SecurityFundamentalFact.metric == "Revenues")
        ).all()
        by_duration = {r.duration_qtrs: r for r in rows}
        assert set(by_duration) == {1, 4}
        assert by_duration[1].value == Decimal("33192000000.0000")
        assert by_duration[4].value == Decimal("127079000000.0000")
        # Same period end on both -- the date alone cannot separate them.
        assert by_duration[1].period_end == by_duration[4].period_end
        assert by_duration[1].fiscal_period != by_duration[4].fiscal_period


class TestFiledDatesTheFact:
    def test_a_comparative_is_knowable_when_filed_not_when_it_occurred(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """GM's 2012 revenue, disclosed in a 2015 filing."""
        _issuer_with_security(db_session, "320193")
        path = _zip(tmp_path, [_sub(filed="20150204")], [_num("Revenues", "20121231", 4, "1")])
        import_fsds_quarter(db_session, path)

        row = db_session.scalars(select(SecurityFundamentalFact)).one()
        assert row.period_end == dt.date(2012, 12, 31)
        assert row.knowledge_time.date() == dt.date(2015, 2, 4)
        # Three years apart, and the later one is what an as-of clock filters on.
        assert row.knowledge_time > row.event_time

    def test_the_period_end_is_never_used_as_the_knowledge_time(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _issuer_with_security(db_session, "320193")
        path = _zip(tmp_path, [_sub(filed="20150204")], [_num("Assets", "20141231", 0, "5")])
        import_fsds_quarter(db_session, path)
        row = db_session.scalars(select(SecurityFundamentalFact)).one()
        assert row.knowledge_time.date() != row.period_end


class TestDimensionalRowsAreSkipped:
    def test_a_segmented_row_is_not_loaded(self, db_session: Session, tmp_path: Path) -> None:
        """Revenue by geography is not revenue, and must not be summed into it."""
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub()],
            [
                _num("Revenues", "20141231", 4, "100", segments="Geography=US"),
                _num("Revenues", "20141231", 4, "250"),
            ],
        )
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 1
        row = db_session.scalars(select(SecurityFundamentalFact)).one()
        assert row.value == Decimal("250")

    def test_a_co_registrant_row_is_not_loaded(self, db_session: Session, tmp_path: Path) -> None:
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path, [_sub()], [_num("Revenues", "20141231", 4, "100", coreg="SUBSIDIARY")]
        )
        assert import_fsds_quarter(db_session, path).landed == 0


class TestIdentityIsNeverCreated:
    def test_an_unmapped_cik_lands_nothing_and_is_reported_once(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Reported per filing, not per number: one unmapped registrant would
        otherwise produce thousands of identical rejects."""
        path = _zip(
            tmp_path,
            [_sub(cik=999999999)],
            [_num("Revenues", "20141231", 4, "1"), _num("Assets", "20141231", 0, "2")],
        )
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 0
        assert result.summary()["unresolved_subjects"] == ["sec_cik:999999999"]
        assert db_session.scalars(select(Issuer)).all() == []

    def test_an_issuer_with_several_securities_is_ambiguous_not_guessed(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Revenue belongs to a company, not to one of its share classes.

        Recorded as a real modelling gap rather than settled by assigning the
        figure to whichever security happened to be created first.
        """
        security = _issuer_with_security(db_session, "320193")
        db_session.add(
            Security(
                issuer_id=security.issuer_id,
                security_type="preferred",
                currency="USD",
                source="test",
            )
        )
        db_session.flush()

        path = _zip(tmp_path, [_sub()], [_num("Revenues", "20141231", 4, "1")])
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 0
        assert "securities" in result.rejected[0].detail


class TestReader:
    def test_the_reader_streams_numbers_rather_than_materialising_them(
        self, tmp_path: Path
    ) -> None:
        path = _zip(tmp_path, [_sub()], [_num("Revenues", "20141231", 4, "1")])
        submissions, facts = read_quarter(path)
        assert set(submissions) == {ADSH}
        assert submissions[ADSH].filed == dt.date(2015, 2, 4)
        assert not isinstance(facts, list)
        assert [f.tag for f in facts] == ["Revenues"]


class TestKnowledgeCannotPrecedeItsEvent:
    """A 10-Q filed on 3 November carrying a value for the quarter ending 31
    December dates its own knowledge before its event.

    Real for a declared dividend -- the declaration happened in November -- and
    look-ahead for a reported result. `num.txt` says which it is nowhere, and
    inventing a tag taxonomy to guess would be the fabrication this corpus
    refuses. Caught on the first real import as an IntegrityError from
    `ck_security_fundamental_knowledge`, halfway through 2010q4; now refused by
    name, with a count.
    """

    def test_it_is_skipped_and_counted_while_the_rest_of_the_filing_lands(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub(filed="20101103", period="20101231")],
            [
                _num("CommonStockDividendsPerShareDeclared", "20101231", 1, "0.19"),
                _num("Revenues", "20100930", 1, "20343000000.0000"),
            ],
        )
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 1
        assert RejectReason.KNOWLEDGE_PRECEDES_EVENT in [r.reason for r in result.rejected]
        assert db_session.scalars(select(SecurityFundamentalFact.metric)).all() == ["Revenues"]

    def test_a_fact_filed_on_the_period_end_itself_is_kept(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The boundary is `knowledge_time < event_time`, not `<=`.

        A filing submitted on the closing date of the period it reports is
        unusual and not impossible, and the check constraint admits it.
        """
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub(filed="20141231", period="20141231")],
            [_num("Revenues", "20141231", 1, "1.0")],
        )
        assert import_fsds_quarter(db_session, path).landed == 1


class TestReRunningIsANoOp:
    """`RejectReason.DUPLICATE` promises it; for this importer it was false.

    `seen` lived for one call while `uq_security_fundamental_revision` lives in
    the database, so deleting a progress file and re-running -- exactly what a
    resumable job invites -- re-inserted a quarter and died on an
    IntegrityError. A side file and the corpus must not be able to disagree.
    """

    def test_the_second_import_lands_nothing_and_counts_the_duplicates(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub()],
            [
                _num("Revenues", "20141231", 1, "1.0"),
                _num("Revenues", "20141231", 4, "4.0"),
            ],
        )
        assert import_fsds_quarter(db_session, path).landed == 2

        again = import_fsds_quarter(db_session, path)
        assert again.landed == 0
        assert RejectReason.DUPLICATE in [r.reason for r in again.rejected]
        assert len(db_session.scalars(select(SecurityFundamentalFact.id)).all()) == 2

    def test_two_ddates_in_one_fiscal_year_are_one_revision(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The key mirrors the constraint, which omits `period_end` on purpose.

        Including the date here would admit rows the database then refuses.
        """
        _issuer_with_security(db_session, "320193")
        path = _zip(
            tmp_path,
            [_sub()],
            [
                _num("Revenues", "20140331", 1, "1.0"),
                _num("Revenues", "20140630", 1, "2.0"),
            ],
        )
        result = import_fsds_quarter(db_session, path)
        assert result.landed == 1
        assert RejectReason.DUPLICATE in [r.reason for r in result.rejected]
