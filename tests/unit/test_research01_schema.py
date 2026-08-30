"""The research-01 schema: drift against its migration, and the FRC round-trip.

Two things are checked here and they are different questions.

**Drift** -- the ORM and migration ``0013`` must describe the same tables and
the same columns. The full drift gate runs against PostgreSQL in
``tests/integration/test_migration_chain.py`` and needs a container; this one
reads the migration source and needs nothing, so a mismatch is caught while it
is still cheap. It found a real one on the first run: the migration had invented
an ``updated_at`` column because ``TimestampMixin`` was assumed rather than read,
when the mixin is ``ingested_at`` and ``source``.

**FRC** -- the regulator-neutral identity claim, exercised end to end. A schema
that merely *has* a namespace column has not shown it can key an issuer on
something other than an SEC CIK, which is the whole reason these tables are
shaped this way.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tradeit.edgar.control_evidence import IdentifierNamespace, IdentifierRole, IdentityRelation
from tradeit.storage.tables import (
    Base,
    Issuer,
    IssuerIdentifier,
    IssuerRelatedIdentity,
    Listing,
    Security,
    SecurityPriceFact,
)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0013_research01_security_schema.py"
)

#: The twelve tables milestone 2 adds. Named explicitly rather than derived, so
#: that deleting one from the ORM fails here instead of shrinking the check.
RESEARCH01_TABLES = frozenset(
    {
        "issuers",
        "issuer_identifiers",
        "issuer_related_identities",
        "securities",
        "security_identifiers",
        "listings",
        "symbol_aliases",
        "security_relationships",
        "filings",
        "security_price_facts",
        "security_corporate_action_facts",
        "security_fundamental_facts",
    }
)


def _migration_tables() -> dict[str, set[str]]:
    """Table -> column names, parsed from the migration's ``op.create_table`` calls.

    Static parsing rather than execution: running the migration needs an Alembic
    context and a database, and the question here is only whether the two
    descriptions agree.
    """
    tree = ast.parse(MIGRATION.read_text())
    audit: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_audit":
            audit = {
                c.args[0].value
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "Column"
                and isinstance(c.args[0], ast.Constant)
            }

    tables: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
        ):
            continue
        name = node.args[0].value
        cols = {
            a.args[0].value
            for a in node.args[1:]
            if isinstance(a, ast.Call)
            and isinstance(a.func, ast.Attribute)
            and a.func.attr == "Column"
            and isinstance(a.args[0], ast.Constant)
        }
        if any(isinstance(a, ast.Starred) for a in node.args):
            cols |= audit
        tables[name] = cols
    return tables


class TestMigrationOrmDrift:
    def test_the_migration_creates_exactly_the_twelve_new_tables(self) -> None:
        assert set(_migration_tables()) == RESEARCH01_TABLES

    def test_every_new_table_is_in_the_orm(self) -> None:
        assert set(Base.metadata.tables) >= RESEARCH01_TABLES

    @pytest.mark.parametrize("table", sorted(RESEARCH01_TABLES))
    def test_columns_match_the_orm(self, table: str) -> None:
        assert _migration_tables()[table] == {c.name for c in Base.metadata.tables[table].columns}

    def test_nothing_existing_was_re_keyed_onto_a_security(self) -> None:
        """The near-neighbours keep their own subject.

        ``ohlcv_bars``, ``corporate_actions`` and ``symbol_mappings`` were not
        absorbed, renamed or given a ``security_id``. They are load-bearing for
        ``full-01``, and a column added here would be the first step of exactly
        the merge this schema exists to avoid.
        """
        for table in ("ohlcv_bars", "corporate_actions", "symbol_mappings", "fundamental_facts"):
            columns = {c.name for c in Base.metadata.tables[table].columns}
            assert "instrument_id" in columns
            assert "security_id" not in columns

    def test_no_new_table_carries_a_bare_cik_column(self) -> None:
        """Identity is namespaced. A ``cik`` column would re-assume the SEC."""
        for table in RESEARCH01_TABLES:
            assert "cik" not in {c.name for c in Base.metadata.tables[table].columns}


def _frc(session: Session) -> Issuer:
    """First Republic Bank: an issuer with no SEC filer account at all."""
    issuer = Issuer(display_name="FIRST REPUBLIC BANK", source="test")
    session.add(issuer)
    session.flush()
    session.add_all(
        [
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace=str(IdentifierNamespace.FDIC_CERT),
                value="59017",
                value_normalized="59017",
                role=str(IdentifierRole.PRIMARY),
                citation="FDIC BankFind, certificate 59017",
                source="test",
            ),
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace=str(IdentifierNamespace.FRB_RSSD),
                value="4114567",
                value_normalized="4114567",
                role=str(IdentifierRole.CORROBORATING),
                citation="FFIEC NIC, RSSD 4114567",
                source="test",
            ),
            IssuerRelatedIdentity(
                issuer_id=issuer.issuer_id,
                namespace=str(IdentifierNamespace.SEC_CIK),
                value="0001132979",
                value_normalized="1132979",
                relation=str(IdentityRelation.UNRESOLVED),
                citation="SEC subject-company record",
                note="EIN 88-0157485 there against 80-0513856 on the FDIC registrant",
                source="test",
            ),
        ]
    )
    session.flush()
    return issuer


class TestFrcRoundTrip:
    """FRC is the case that decides whether identity is really regulator-neutral."""

    def test_the_primary_key_is_an_fdic_certificate_not_a_cik(self, db_session: Session) -> None:
        issuer = _frc(db_session)
        primary = db_session.scalars(
            select(IssuerIdentifier).where(
                IssuerIdentifier.issuer_id == issuer.issuer_id,
                IssuerIdentifier.role == str(IdentifierRole.PRIMARY),
            )
        ).all()
        assert len(primary) == 1
        assert (primary[0].namespace, primary[0].value_normalized) == ("fdic_cert", "59017")

    def test_the_frb_identifier_corroborates_and_does_not_discriminate(
        self, db_session: Session
    ) -> None:
        issuer = _frc(db_session)
        corroborating = db_session.scalars(
            select(IssuerIdentifier).where(
                IssuerIdentifier.issuer_id == issuer.issuer_id,
                IssuerIdentifier.role == str(IdentifierRole.CORROBORATING),
            )
        ).all()
        assert [c.value_normalized for c in corroborating] == ["4114567"]

    def test_the_sec_cik_is_present_but_is_not_an_identifier(self, db_session: Session) -> None:
        """The load-bearing assertion.

        ``SEC_CIK:1132979`` is recorded, because discarding it would lose real
        evidence. It is *not* an identifier of FRC, and the schema makes that
        structural: a resolver reading ``issuer_identifiers`` cannot reach it,
        because it is not in that table.
        """
        issuer = _frc(db_session)

        related = db_session.scalars(
            select(IssuerRelatedIdentity).where(IssuerRelatedIdentity.issuer_id == issuer.issuer_id)
        ).all()
        assert [(r.namespace, r.value_normalized) for r in related] == [("sec_cik", "1132979")]
        assert related[0].relation == "unresolved"

        # Resolving identity reads issuer_identifiers. The CIK is not there.
        identifiers = db_session.scalars(
            select(IssuerIdentifier).where(IssuerIdentifier.issuer_id == issuer.issuer_id)
        ).all()
        assert "sec_cik" not in {i.namespace for i in identifiers}

    def test_the_verbatim_value_survives_while_the_compared_one_is_normalized(
        self, db_session: Session
    ) -> None:
        """``0001132979`` is stored as written; ``1132979`` is what compares."""
        issuer = _frc(db_session)
        related = db_session.scalars(
            select(IssuerRelatedIdentity).where(IssuerRelatedIdentity.issuer_id == issuer.issuer_id)
        ).one()
        assert related.value == "0001132979"
        assert related.value_normalized == "1132979"

    def test_a_second_primary_identifier_is_refused(self, db_session: Session) -> None:
        """Two primaries discriminate nothing, so the schema admits only one."""
        issuer = _frc(db_session)
        db_session.add(
            IssuerIdentifier(
                issuer_id=issuer.issuer_id,
                namespace=str(IdentifierNamespace.SEC_CIK),
                value="1132979",
                value_normalized="1132979",
                role=str(IdentifierRole.PRIMARY),
                citation="wrong: FRC has no SEC filer account",
                source="test",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_one_registry_value_cannot_name_two_issuers(self, db_session: Session) -> None:
        """The anti-splice rule: one institution recorded twice must collide."""
        _frc(db_session)
        other = Issuer(display_name="SOMEONE ELSE", source="test")
        db_session.add(other)
        db_session.flush()
        db_session.add(
            IssuerIdentifier(
                issuer_id=other.issuer_id,
                namespace=str(IdentifierNamespace.FDIC_CERT),
                value="59017",
                value_normalized="59017",
                role=str(IdentifierRole.PRIMARY),
                citation="duplicate of FRC's certificate",
                source="test",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()


class TestSeparationOfConcerns:
    def test_a_security_carries_no_venue_and_no_ticker(self) -> None:
        columns = {c.name for c in Base.metadata.tables["securities"].columns}
        assert "venue" not in columns
        assert "exchange" not in columns
        assert "ticker" not in columns

    def test_the_venue_string_is_stored_verbatim(self, db_session: Session) -> None:
        """Six Nasdaq renderings coexist deliberately; nothing normalises them."""
        issuer = _frc(db_session)
        security = Security(
            issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
        )
        db_session.add(security)
        db_session.flush()
        renderings = [
            "The Nasdaq Stock Market LLC",
            "NASDAQ Global Select Market",
            "New York Stock Exchange",
        ]
        db_session.add_all(
            [
                Listing(
                    security_id=security.security_id,
                    venue=v,
                    listed_from=dt.date(2010, 1, 4),
                    source="test",
                )
                for v in renderings
            ]
        )
        db_session.flush()
        stored = db_session.scalars(
            select(Listing.venue).where(Listing.security_id == security.security_id)
        ).all()
        assert sorted(stored) == sorted(renderings)

    def test_the_three_adjustment_bases_coexist_for_one_session(self, db_session: Session) -> None:
        """What ``ohlcv_bars`` cannot express: it is unadjusted-only."""
        issuer = _frc(db_session)
        security = Security(
            issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
        )
        db_session.add(security)
        db_session.flush()
        moment = dt.datetime(2011, 4, 4, tzinfo=dt.UTC)
        db_session.add_all(
            [
                SecurityPriceFact(
                    security_id=security.security_id,
                    session_date=dt.date(2011, 4, 4),
                    adjustment_basis=basis,
                    event_time=moment,
                    knowledge_time=moment,
                    knowledge_source="test",
                    open=1,
                    high=2,
                    low=1,
                    close=2,
                    volume=100,
                    volume_adjusted=False,
                    source="test",
                )
                for basis in ("raw", "split", "total")
            ]
        )
        db_session.flush()
        rows = db_session.scalars(
            select(SecurityPriceFact.adjustment_basis).where(
                SecurityPriceFact.security_id == security.security_id
            )
        ).all()
        assert sorted(rows) == ["raw", "split", "total"]
