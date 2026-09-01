"""Milestone 5's gate: fundamentals reconciled against filings.

Two independent SEC products describe the same filings -- the quarterly
full-index and the Financial Statement Data Sets -- and they are produced by
different pipelines. Agreement on a filing date is therefore evidence, and
disagreement is a finding rather than a rounding error, because
``knowledge_time`` is derived from that date: a disagreement is a disagreement
about **when a number became usable**.

Measured on real data (full-index 2014Q4-2015Q2 against FSDS 2015q1, seeded
corpus): 3,520 facts, 100% linked, **zero disagreements**.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    FilingRow,
    import_filings,
    import_fsds_quarter,
    reconcile_fundamentals_against_filings,
)
from tradeit.storage.tables import Issuer, IssuerIdentifier, Security, SecurityFundamentalFact

ADSH = "0000000000-15-000001"
SUB_HEADER = "adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled\n"
NUM_HEADER = "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"


def _corpus(session: Session) -> None:
    issuer = Issuer(display_name="TEST CO", source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value="320193",
            value_normalized="320193",
            role="primary",
            citation="test",
            source="test",
        )
    )
    session.add(
        Security(
            issuer_id=issuer.issuer_id,
            security_type="common_stock",
            currency="USD",
            source="test",
        )
    )
    session.flush()


def _zip(tmp_path: Path, filed: str = "20150204") -> Path:
    path = tmp_path / "2015q1.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "sub.txt", SUB_HEADER + f"{ADSH}\t320193\tTEST CO\t10-K\t20141231\t2015\tFY\t{filed}\n"
        )
        z.writestr(
            "num.txt",
            NUM_HEADER + f"{ADSH}\tRevenues\tus-gaap/2014\t20141231\t4\tUSD\t\t\t100\t\n",
        )
    return path


def _filing(session: Session, filed_at: dt.date) -> None:
    import_filings(
        session,
        [FilingRow(cik=320193, form_type="10-K", filed_at=filed_at, accession=ADSH)],
        "edgar_full_index",
    )


class TestReconciliation:
    def test_a_fact_links_to_the_filing_it_came_from(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The join is on the SEC's own accession, not on anything we derived."""
        _corpus(db_session)
        _filing(db_session, dt.date(2015, 2, 4))
        import_fsds_quarter(db_session, _zip(tmp_path))

        fact = db_session.scalars(select(SecurityFundamentalFact)).one()
        assert fact.filing_id is not None

        report = reconcile_fundamentals_against_filings(db_session)
        assert report.facts == 1
        assert report.linked == 1
        assert report.unlinked == 0
        assert report.linked_share == 1.0
        assert report.disagreements == []

    def test_an_unlinked_fact_is_reported_and_is_not_called_a_defect(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """We may simply not have loaded that quarter of the index.

        Conflating a coverage statement with a defect would make the gate fire
        on the wrong thing -- and the honest reading is that the corpus cannot
        yet say where this number came from, not that the number is wrong.
        """
        _corpus(db_session)
        import_fsds_quarter(db_session, _zip(tmp_path))  # no filing loaded

        report = reconcile_fundamentals_against_filings(db_session)
        assert report.facts == 1
        assert report.linked == 0
        assert report.unlinked == 1
        assert report.disagreements == []

    def test_a_filed_date_disagreement_between_the_two_sources_is_detected(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The cross-check proper.

        The index says one filing date, FSDS says another. Because
        knowledge_time is derived from the FSDS date, the two sources disagree
        about when the number became usable -- which is exactly the kind of
        thing that silently shifts a backtest.
        """
        _corpus(db_session)
        _filing(db_session, dt.date(2015, 2, 4))
        import_fsds_quarter(db_session, _zip(tmp_path, filed="20150211"))

        report = reconcile_fundamentals_against_filings(db_session)
        assert report.linked == 1
        assert len(report.disagreements) == 1
        disagreement = report.disagreements[0]
        assert disagreement.accession == ADSH
        assert disagreement.field == "filed"
        assert (disagreement.index_says, disagreement.fsds_says) == ("2015-02-04", "2015-02-11")

    def test_the_report_counts_filings_that_carry_facts_separately(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Holding a filing and having its numbers are different questions.

        Most filings are 8-Ks and ownership forms with no statement data at
        all, so `filings_held` far exceeding `filings_carrying_facts` is the
        normal shape rather than a gap.
        """
        _corpus(db_session)
        _filing(db_session, dt.date(2015, 2, 4))
        import_filings(
            db_session,
            [
                FilingRow(
                    cik=320193,
                    form_type="8-K",
                    filed_at=dt.date(2015, 3, 1),
                    accession="0000000000-15-000002",
                )
            ],
            "edgar_full_index",
        )
        import_fsds_quarter(db_session, _zip(tmp_path))

        report = reconcile_fundamentals_against_filings(db_session)
        assert report.filings_held == 2
        assert report.filings_carrying_facts == 1
