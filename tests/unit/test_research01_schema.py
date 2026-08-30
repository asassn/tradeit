"""The research-01 schema: drift against its migration, and the FRC round-trip.

Two things are checked here and they are different questions.

**Drift** -- the ORM and migration ``0013`` must describe the same tables, the
same columns, the same column *types* and the same nullability.

The type half exists because the first version of this guard did not have it.
It compared column names only, passed locally, and CI then failed on seven
columns where the migration declared ``sa.DateTime(timezone=True)`` and the ORM
declared ``UTCDateTime()``. **No SQL comparison could have caught that**: both
render ``TIMESTAMP WITH TIME ZONE``, and the difference is the Python type
object, which is exactly what Alembic compares. That was the second
type-assumption error in one migration -- the first invented an ``updated_at``
column because ``TimestampMixin`` was assumed rather than read -- so the guard is
now built to catch the class rather than the instance.

It works by **executing** the migration's ``upgrade()`` against a recorder that
stands in for ``alembic.op``, which yields the real :class:`sqlalchemy.Column`
objects the migration declares. Static parsing of the source could not do this:
a type is an expression, and comparing expressions as text would call
``UTCDateTime(timezone=True)`` and ``TS`` different when they are the same thing.

**What still needs PostgreSQL, and why.** This guard compares what the migration
*declares* to what the ORM declares. ``tests/integration/test_phase2_schema.py``
compares what a real database *has* to what the ORM declares, via Alembic's
``compare_metadata`` against a live schema. Three things only the latter can
see: whether the whole migration chain leaves these tables in the expected end
state (this file reads 0013 alone), whether PostgreSQL renders a declared type
or constraint differently from what was asked for, and whether the partial index
predicate is enforced by the deployed engine rather than merely declared. Neither
supersedes the other; this one exists so the common failures do not need a CI
round-trip.

**FRC** -- the regulator-neutral identity claim, exercised end to end. A schema
that merely *has* a namespace column has not shown it can key an issuer on
something other than an SEC CIK, which is the whole reason these tables are
shaped this way.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
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

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

#: Every migration touching these tables, in revision order. **A list, not a
#: single file**: 0013 creates the tables and 0014 adds a column to one, so a
#: guard reading only the first would report drift that the chain does not have.
#: Append here when a later migration touches research-01.
MIGRATIONS = (
    VERSIONS / "0013_research01_security_schema.py",
    VERSIONS / "0014_pit_basis_and_alias_overlap.py",
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


class _OpRecorder:
    """Stands in for ``alembic.op`` and replays schema changes onto a dict.

    Supports the operations these migrations actually use. Anything unsupported
    would raise rather than be silently ignored, which is the behaviour we want:
    a migration doing something this cannot model must not report "no drift".
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, sa.Column[Any]]] = {}

    # -- schema-shaping operations, replayed --------------------------------

    def create_table(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.tables[name] = {a.name: a for a in args if isinstance(a, sa.Column)}

    def add_column(self, table: str, column: sa.Column[Any], **kwargs: Any) -> None:
        self.tables.setdefault(table, {})[column.name] = column

    def drop_column(self, table: str, name: str, **kwargs: Any) -> None:
        self.tables.get(table, {}).pop(name, None)

    # -- operations that cannot change the column set -----------------------

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    def alter_column(self, *args: Any, **kwargs: Any) -> None:
        return None

    def create_check_constraint(self, *args: Any, **kwargs: Any) -> None:
        return None

    def drop_table(self, *args: Any, **kwargs: Any) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_bind(self) -> Any:
        """Non-PostgreSQL, so dialect-guarded blocks take their early return.

        The EXCLUDE constraint 0014 adds is PostgreSQL-only and is verified by
        the integration suite; it adds no column, so skipping it here changes
        nothing this guard measures.
        """
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


def _migration_columns() -> dict[str, dict[str, sa.Column[Any]]]:
    """Replay every research-01 migration in order and collect the result."""
    recorder = _OpRecorder()
    for path in MIGRATIONS:
        spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = recorder  # type: ignore[attr-defined]
        module.upgrade()
    return recorder.tables


def _type_key(type_: Any) -> tuple[Any, ...]:
    """A comparable fingerprint of a column type.

    The **Python class** leads, because that is what Alembic compares and what
    the CI failure turned on: ``UTCDateTime`` and ``DateTime`` render identical
    DDL and are not the same type. Length, precision and scale follow, so a
    ``String(32)`` never matches a ``String(64)``.
    """
    return (
        type(type_).__name__,
        getattr(type_, "length", None),
        getattr(type_, "precision", None),
        getattr(type_, "scale", None),
        getattr(type_, "timezone", None),
    )


class TestMigrationOrmDrift:
    def test_the_migration_creates_exactly_the_twelve_new_tables(self) -> None:
        assert set(_migration_columns()) == RESEARCH01_TABLES

    def test_every_new_table_is_in_the_orm(self) -> None:
        assert set(Base.metadata.tables) >= RESEARCH01_TABLES

    @pytest.mark.parametrize("table", sorted(RESEARCH01_TABLES))
    def test_columns_match_the_orm(self, table: str) -> None:
        assert set(_migration_columns()[table]) == {
            c.name for c in Base.metadata.tables[table].columns
        }

    @pytest.mark.parametrize("table", sorted(RESEARCH01_TABLES))
    def test_column_types_match_the_orm(self, table: str) -> None:
        """The check that was missing when CI caught seven drifted timestamps."""
        migration = _migration_columns()[table]
        orm = Base.metadata.tables[table].columns
        mismatched = {
            name: (_type_key(migration[name].type), _type_key(orm[name].type))
            for name in migration
            if name in orm and _type_key(migration[name].type) != _type_key(orm[name].type)
        }
        assert not mismatched, f"{table}: migration/ORM type drift {mismatched}"

    @pytest.mark.parametrize("table", sorted(RESEARCH01_TABLES))
    def test_nullability_matches_the_orm(self, table: str) -> None:
        migration = _migration_columns()[table]
        orm = Base.metadata.tables[table].columns
        # Primary keys are implicitly NOT NULL and Alembic renders them either
        # way, so they are compared on type and name rather than on this flag.
        mismatched = {
            name: (migration[name].nullable, orm[name].nullable)
            for name in migration
            if name in orm
            and not orm[name].primary_key
            and migration[name].nullable != orm[name].nullable
        }
        assert not mismatched, f"{table}: migration/ORM nullability drift {mismatched}"

    def test_every_point_in_time_column_is_utcdatetime(self) -> None:
        """The specific regression, pinned by name rather than only by comparison.

        A naive ``knowledge_time`` compared against an aware ``as_of`` is either
        a crash or a silently wrong comparison, which is why ``UTCDateTime``
        exists. Both sides are asserted so that changing them together -- the
        way the comparison test alone could be satisfied -- still fails.
        """
        migration = _migration_columns()
        for table in sorted(RESEARCH01_TABLES):
            for name in ("event_time", "knowledge_time", "ingested_at"):
                if name not in migration[table]:
                    continue
                assert type(migration[table][name].type).__name__ == "UTCDateTime", (
                    f"{table}.{name} in the migration"
                )
                assert (
                    type(Base.metadata.tables[table].columns[name].type).__name__ == "UTCDateTime"
                ), f"{table}.{name} in the ORM"

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
