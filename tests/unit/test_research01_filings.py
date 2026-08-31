"""Landing EDGAR filings against seeded identity, and the one it must refuse.

The load-bearing test here is ``FRC``. Its SEC CIK sits in
``issuer_related_identities`` because sameness with the FDIC registrant is
established in neither direction, so filings under that CIK must **not** attach
to it. A resolver that consulted the related table would produce a corpus that
looks better and asserts something the evidence declines to.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    FilingRow,
    RejectReason,
    import_filings,
    resolve_issuer,
    seed_identity,
)
from tradeit.storage.tables import Filing, Issuer, IssuerRelatedIdentity

FILED = dt.date(2011, 2, 24)


@pytest.fixture
def seeded(db_session: Session) -> Session:
    seed_identity(db_session)
    return db_session


def _row(cik: int, form: str = "10-K", accession: str = "0000000000-11-000001") -> FilingRow:
    return FilingRow(
        cik=cik,
        form_type=form,
        filed_at=FILED,
        accession=accession,
        source_path=f"edgar/data/{cik}/{accession}.txt",
    )


class TestFrcsRelatedCikDoesNotResolve:
    def test_the_related_cik_is_present_in_the_corpus(self, seeded: Session) -> None:
        """Guards the test below: if the CIK were absent this would pass vacuously."""
        related = seeded.scalars(
            select(IssuerRelatedIdentity).where(IssuerRelatedIdentity.value_normalized == "1132979")
        ).one()
        assert related.relation == "unresolved"

    def test_it_resolves_to_no_issuer(self, seeded: Session) -> None:
        assert resolve_issuer(seeded, namespace="sec_cik", value="1132979") is None

    def test_a_filing_under_it_is_reported_not_attached_to_frc(self, seeded: Session) -> None:
        result = import_filings(seeded, [_row(1132979)], "edgar_full_index")
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NO_ISSUER
        assert result.summary()["unresolved_subjects"] == ["sec_cik:1132979"]

        frc = seeded.scalars(select(Issuer).where(Issuer.note.like("FRC/%"))).one()
        assert (
            seeded.scalars(select(Filing).where(Filing.issuer_id == frc.issuer_id)).first() is None
        )


class TestRoundTrip:
    def test_a_filing_lands_against_its_issuer(self, seeded: Session) -> None:
        issuer_id = resolve_issuer(seeded, namespace="sec_cik", value="320193")
        assert issuer_id is not None

        result = import_filings(seeded, [_row(320193)], "edgar_full_index")
        assert result.landed == 1

        filing = seeded.scalars(select(Filing)).one()
        assert filing.issuer_id == issuer_id
        assert filing.form_type == "10-K"
        assert filing.filed_at == FILED
        # Filings are the registrant's; a security is the exception, not the rule.
        assert filing.security_id is None

    def test_the_form_type_is_kept_verbatim(self, seeded: Session) -> None:
        """`10-K` is not `10-K405`, and nothing folds one into the other."""
        import_filings(
            seeded,
            [
                _row(320193, "10-K405", "0000000000-97-000001"),
                _row(320193, "10-K", "0000000000-98-000001"),
            ],
            "edgar_full_index",
        )
        forms = set(seeded.scalars(select(Filing.form_type)).all())
        assert forms == {"10-K405", "10-K"}

    def test_re_importing_an_accession_changes_nothing(self, seeded: Session) -> None:
        rows = [_row(320193)]
        first = import_filings(seeded, rows, "edgar_full_index")
        second = import_filings(seeded, rows, "edgar_full_index")
        assert (first.landed, second.landed) == (1, 0)
        assert second.rejected[0].reason is RejectReason.DUPLICATE
        assert len(seeded.scalars(select(Filing)).all()) == 1

    def test_an_unknown_cik_creates_nothing(self, seeded: Session) -> None:
        before = len(seeded.scalars(select(Issuer)).all())
        result = import_filings(seeded, [_row(999999999)], "edgar_full_index")
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NO_ISSUER
        assert len(seeded.scalars(select(Issuer)).all()) == before


class TestIdentityBreaksKeepTheirFilingsApart:
    def test_two_issuers_of_one_ticker_do_not_share_filings(self, seeded: Session) -> None:
        """GM's two registrants are different companies and file separately."""
        old_id = resolve_issuer(seeded, namespace="sec_cik", value="40730")
        new_id = resolve_issuer(seeded, namespace="sec_cik", value="1467858")
        assert old_id is not None and new_id is not None and old_id != new_id

        import_filings(
            seeded,
            [
                _row(40730, "10-K", "0000040730-08-000001"),
                _row(1467858, "10-K", "0001467858-10-000001"),
            ],
            "edgar_full_index",
        )
        by_issuer = {
            issuer_id: seeded.scalars(
                select(Filing.accession).where(Filing.issuer_id == issuer_id)
            ).all()
            for issuer_id in (old_id, new_id)
        }
        assert by_issuer[old_id] == ["0000040730-08-000001"]
        assert by_issuer[new_id] == ["0001467858-10-000001"]
