"""Seeding the identity backbone, and the three ways it could be silently wrong.

Each of these is a test that would pass vacuously if the seeder collapsed
something. `CLAUDE.md` records that tests have gone vacuous here twice; the
identity-break controls are the third opportunity, because a seeder that made
one security per *ticker* rather than one per *issuer* would leave the GM splice
test green on a corpus that had already destroyed the distinction it tests.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.edgar.control_evidence import load_control_evidence
from tradeit.research01 import (
    Delivery,
    RejectReason,
    VendorBar,
    import_price_bars,
    seed_identity,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    IssuerRelatedIdentity,
    Security,
    SymbolAlias,
)

#: Controls the evidence marks as identity breaks: one ticker, two unrelated
#: issuers. Read from the evidence rather than hard-coded, so adding a fourth
#: break to the corpus extends this test instead of bypassing it.
BREAKS = tuple(
    sorted(c.control_id for c in load_control_evidence().controls.values() if c.identity_break)
)


@pytest.fixture
def seeded(db_session: Session) -> Session:
    seed_identity(db_session)
    return db_session


class TestIdentityBreaksProduceTwoSecurities:
    def test_the_evidence_still_marks_more_than_one_break(self) -> None:
        """Guards the parametrisation itself: an empty list would pass silently."""
        assert len(BREAKS) >= 3
        assert {"GM", "BBBY", "AOL"} <= set(BREAKS)

    @pytest.mark.parametrize("control_id", BREAKS)
    def test_each_break_seeds_two_securities_under_two_issuers(
        self, seeded: Session, control_id: str
    ) -> None:
        securities = seeded.scalars(
            select(Security).where(Security.note.like(f"{control_id}/%"))
        ).all()
        assert len(securities) == 2, f"{control_id} collapsed into {len(securities)} security"
        # Two *issuers*, not one issuer with two share classes -- the whole point
        # is that these are unrelated companies that happened to share a ticker.
        assert len({s.issuer_id for s in securities}) == 2

    @pytest.mark.parametrize("control_id", BREAKS)
    def test_the_two_issuers_carry_different_primary_keys(
        self, seeded: Session, control_id: str
    ) -> None:
        issuers = seeded.scalars(select(Issuer).where(Issuer.note.like(f"{control_id}/%"))).all()
        keys = {
            seeded.scalars(
                select(IssuerIdentifier.value_normalized).where(
                    IssuerIdentifier.issuer_id == i.issuer_id,
                    IssuerIdentifier.role == "primary",
                )
            ).one()
            for i in issuers
        }
        assert len(keys) == 2, f"{control_id}: both issuers resolved to one key {keys}"


class TestFrcIsRegulatorNeutral:
    """The architecture that would regress with nothing failing."""

    def test_frc_keys_on_an_fdic_certificate(self, seeded: Session) -> None:
        issuer = seeded.scalars(select(Issuer).where(Issuer.note.like("FRC/%"))).one()
        primary = seeded.scalars(
            select(IssuerIdentifier).where(
                IssuerIdentifier.issuer_id == issuer.issuer_id,
                IssuerIdentifier.role == "primary",
            )
        ).one()
        assert (primary.namespace, primary.value_normalized) == ("fdic_cert", "59017")

    def test_the_frb_identifier_corroborates(self, seeded: Session) -> None:
        issuer = seeded.scalars(select(Issuer).where(Issuer.note.like("FRC/%"))).one()
        corroborating = seeded.scalars(
            select(IssuerIdentifier).where(
                IssuerIdentifier.issuer_id == issuer.issuer_id,
                IssuerIdentifier.role == "corroborating",
            )
        ).all()
        assert [(c.namespace, c.value_normalized) for c in corroborating] == [
            ("frb_rssd", "4114567")
        ]

    def test_the_sec_cik_is_related_and_unreachable_as_an_identifier(self, seeded: Session) -> None:
        issuer = seeded.scalars(select(Issuer).where(Issuer.note.like("FRC/%"))).one()
        related = seeded.scalars(
            select(IssuerRelatedIdentity).where(IssuerRelatedIdentity.issuer_id == issuer.issuer_id)
        ).one()
        assert (related.namespace, related.value_normalized) == ("sec_cik", "1132979")
        assert related.relation == "unresolved"
        # A resolver reads issuer_identifiers. The CIK is not in it.
        namespaces = set(
            seeded.scalars(
                select(IssuerIdentifier.namespace).where(
                    IssuerIdentifier.issuer_id == issuer.issuer_id
                )
            ).all()
        )
        assert "sec_cik" not in namespaces

    def test_no_seeded_issuer_anywhere_was_given_an_invented_sec_cik(self, seeded: Session) -> None:
        """FRC has no SEC filer account; nothing may have manufactured one."""
        issuer = seeded.scalars(select(Issuer).where(Issuer.note.like("FRC/%"))).one()
        assert (
            seeded.scalars(
                select(IssuerIdentifier).where(
                    IssuerIdentifier.issuer_id == issuer.issuer_id,
                    IssuerIdentifier.namespace == "sec_cik",
                )
            ).first()
            is None
        )


class TestOnlyFromEvidence:
    def test_every_identifier_traces_to_the_evidence_file(self, seeded: Session) -> None:
        rows = seeded.scalars(select(IssuerIdentifier)).all()
        assert rows
        assert {r.source for r in rows} == {"control_identity_evidence"}

    def test_an_unestablished_ticker_interval_is_reported_not_invented(
        self, db_session: Session
    ) -> None:
        """The corpus mostly does not establish when a ticker was held.

        Measured at seeding time: nearly every mapping lacks ``valid_from``. The
        seeder records each as ``ticker_interval`` rather than choosing a
        plausible date, because a manufactured interval is exactly the
        attribution the importer refuses to guess at.
        """
        report = seed_identity(db_session)
        assert report.gaps_by_kind().get("ticker_interval", 0) > 0
        # Every alias that *was* written came from an established interval.
        assert report.aliases == len(db_session.scalars(select(SymbolAlias)).all())
        assert report.aliases < report.securities

    def test_seeding_twice_changes_nothing(self, db_session: Session) -> None:
        first = seed_identity(db_session)
        second = seed_identity(db_session)
        assert first.issuers > 0
        assert second.issuers == 0
        assert second.reused == first.issuers
        assert len(db_session.scalars(select(Issuer)).all()) == first.issuers


class TestTheCorpusFailsClosedUntilIntervalsAreCurated:
    """A visible refusal, not a silent wrong answer.

    **This test is expected to change when ticker intervals are curated**, and
    when it does it must be *reworked* rather than re-pinned: the invariant is
    that an unestablished interval yields a refusal, not that GM specifically is
    unresolvable forever.
    """

    def test_a_gm_bar_cannot_be_imported_yet_and_says_so(self, seeded: Session) -> None:
        bar = VendorBar(
            ticker="GM",
            session_date=dt.date(2009, 5, 1),
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10.5"),
            volume=Decimal("1000"),
        )
        result = import_price_bars(
            seeded, [bar], Delivery("eodhd", dt.datetime(2026, 8, 30, tzinfo=dt.UTC))
        )
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NO_ALIAS
        # The refusal is reportable, which is what makes it not a silent failure.
        assert result.summary()["unresolved_tickers"] == ["GM"]
