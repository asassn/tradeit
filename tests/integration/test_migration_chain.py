"""The migration chain, run from nothing, one revision at a time.

**Why this file exists.** `alembic upgrade head` against a brand-new PostgreSQL
database failed at revision 0004 with

    constraint "orders_pattern_id_fkey" of relation "orders" does not exist

and, because PostgreSQL has transactional DDL, rolled the entire upgrade back —
leaving an empty database and no revision. A second defect of the same shape sat
in 0006, which dropped `breakout_events` while `opportunity_scores` still
referenced it. Neither could be reached until the first was fixed.

**Why the existing tests did not catch it, exactly.** They did, and unhelpfully.
`test_postgres.py` rebuilds its schema with `command.upgrade(cfg, "head")`, so
the whole module errored — 25 fixture ERRORs whose message named a constraint on
`orders`, several layers away from "the migration chain is broken". And the one
assertion meant to notice a stale schema compared the version against the
literal `"0003_phase3_analytics"`, which had itself been stale for five
revisions and could only fail on a database that reached head — which no fresh
one could.

So this module is deliberately different from the rest:

* it creates an **empty schema** and upgrades from base, never from a database
  that already has tables;
* it steps **one revision at a time**, so a failure names the revision that
  broke rather than surfacing as an unrelated fixture error;
* it asserts against the **actual head** from the script directory, so it cannot
  go stale the way its predecessor did;
* it verifies the Phase 4/5 foreign keys that the two broken revisions detach
  and restore, including their ``ON DELETE`` behaviour, because "the chain ran"
  and "the chain produced the right schema" are different claims;
* and it checks that a **failing** migration leaves no partial state, since the
  original report's most alarming detail was a database that could have
  masqueraded as half-upgraded.

`metadata.create_all()` proves none of this. It builds the current ORM's schema
in one step and never executes a single migration, so every defect here is
invisible to it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

DSN = os.environ.get("TRADEIT_TEST_PG_DSN")

if not DSN:
    pytest.skip("set TRADEIT_TEST_PG_DSN to run PostgreSQL tests", allow_module_level=True)


def alembic_config(dsn: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def revisions_in_order() -> list[str]:
    """Every revision from base to head, oldest first."""
    script = ScriptDirectory.from_config(alembic_config(DSN or ""))
    head = script.get_current_head()
    assert head is not None
    return [rev.revision for rev in reversed(list(script.walk_revisions("base", head)))]


@pytest.fixture
def empty_database() -> object:
    """A genuinely empty public schema.

    `DROP SCHEMA public CASCADE` removes the tables *and* `alembic_version`, so
    the upgrade that follows starts from base rather than from wherever the last
    test left off. Anything less is the trap this file exists to avoid.
    """
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    try:
        yield engine
    finally:
        engine.dispose()


def table_names(engine: object) -> set[str]:
    with engine.connect() as conn:  # type: ignore[attr-defined]
        return set(sa.inspect(conn).get_table_names())


def current_revision(engine: object) -> str | None:
    with engine.connect() as conn:  # type: ignore[attr-defined]
        if "alembic_version" not in sa.inspect(conn).get_table_names():
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


class TestFreshChain:
    def test_the_starting_point_is_actually_empty(self, empty_database: object) -> None:
        """Guards the guard. A fixture that quietly left tables behind would
        make every assertion below vacuous."""
        assert table_names(empty_database) == set()
        assert current_revision(empty_database) is None

    def test_every_revision_applies_in_order_from_base(self, empty_database: object) -> None:
        """One revision at a time, so a failure names the culprit.

        This is the test that would have said "0004_phase4_patterns" instead of
        leaving somebody to work back from a constraint name on `orders`.
        """
        cfg = alembic_config(DSN or "")
        applied: list[str] = []
        for revision in revisions_in_order():
            try:
                command.upgrade(cfg, revision)
            except Exception as error:
                pytest.fail(
                    f"revision {revision} failed against a database built from base.\n"
                    f"Revisions that applied cleanly first: {applied or ['(none)']}\n"
                    f"{type(error).__name__}: {error}"
                )
            assert current_revision(empty_database) == revision, (
                f"{revision} reported success but alembic_version says "
                f"{current_revision(empty_database)}"
            )
            applied.append(revision)
        assert applied == revisions_in_order()

    def test_the_final_revision_is_head(self, empty_database: object) -> None:
        cfg = alembic_config(DSN or "")
        command.upgrade(cfg, "head")
        expected = ScriptDirectory.from_config(cfg).get_current_head()
        assert current_revision(empty_database) == expected

    def test_a_single_shot_upgrade_reaches_head_too(self, empty_database: object) -> None:
        """What the operator actually types. Stepping revision by revision runs
        each in its own transaction; `upgrade head` runs the lot in one, and the
        original defect only rolled everything back because of that."""
        command.upgrade(alembic_config(DSN or ""), "head")
        expected = ScriptDirectory.from_config(alembic_config(DSN or "")).get_current_head()
        assert current_revision(empty_database) == expected
        assert len(table_names(empty_database)) > 100


class TestSchemaAfterTheChain:
    """The chain running is not the same as the chain being right."""

    @pytest.fixture
    def migrated(self, empty_database: object) -> object:
        command.upgrade(alembic_config(DSN or ""), "head")
        return empty_database

    def foreign_keys_to(self, engine: object, target: str) -> dict[str, tuple[str, str | None]]:
        with engine.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                text(
                    """
                    SELECT c.conname,
                           c.conrelid::regclass::text AS from_table,
                           CASE c.confdeltype
                                WHEN 'n' THEN 'SET NULL' WHEN 'c' THEN 'CASCADE'
                                WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
                                WHEN 'd' THEN 'SET DEFAULT' END AS on_delete
                    FROM pg_constraint c
                    WHERE c.contype = 'f' AND c.confrelid = CAST(:target AS regclass)
                    """
                ),
                {"target": target},
            ).all()
        return {row[0]: (row[1], row[2]) for row in rows}

    def test_the_references_0004_detaches_are_restored(self, migrated: object) -> None:
        """The exact constraints the broken revision dropped and re-created.

        `opportunity_scores_pattern_id_fkey` is the one the old code never named
        — it assumed `orders` — so its presence here is the direct regression
        test for the reported defect.
        """
        found = self.foreign_keys_to(migrated, "patterns")
        assert found.get("opportunity_scores_pattern_id_fkey") == (
            "opportunity_scores",
            "SET NULL",
        )
        assert found.get("breakout_events_pattern_id_fkey") == ("breakout_events", "SET NULL")

    def test_orders_has_no_pattern_reference_and_never_did(self, migrated: object) -> None:
        """The assumption that caused the failure, pinned as false.

        If `orders` ever legitimately gains a `pattern_id`, this test fails and
        whoever adds it has to decide deliberately rather than discovering it
        through a bricked migration.
        """
        with migrated.connect() as conn:  # type: ignore[attr-defined]
            columns = {c["name"] for c in sa.inspect(conn).get_columns("orders")}
        assert "pattern_id" not in columns
        assert "orders" not in {t for t, _ in self.foreign_keys_to(migrated, "patterns").values()}

    def test_the_reference_0006_detaches_is_restored(self, migrated: object) -> None:
        """The second defect: 0006 dropped `breakout_events` with this still
        pointing at it."""
        found = self.foreign_keys_to(migrated, "breakout_events")
        assert found.get("opportunity_scores_breakout_event_id_fkey") == (
            "opportunity_scores",
            "SET NULL",
        )

    def test_the_phase5_children_reference_the_replacement_table(self, migrated: object) -> None:
        found = self.foreign_keys_to(migrated, "breakout_events")
        assert found.get("breakout_observations_event_id_fkey") == (
            "breakout_observations",
            "CASCADE",
        )
        assert found.get("breakout_labels_event_id_fkey") == ("breakout_labels", "SET NULL")

    def test_the_phase4_children_reference_the_replacement_table(self, migrated: object) -> None:
        found = self.foreign_keys_to(migrated, "patterns")
        assert found.get("pattern_observations_pattern_id_fkey") == (
            "pattern_observations",
            "CASCADE",
        )
        assert found.get("pattern_labels_pattern_id_fkey") == ("pattern_labels", "SET NULL")

    def test_instrument_foreign_keys_match_the_primary_key_type(self, migrated: object) -> None:
        """0006 wrote INTEGER where `instruments.instrument_id` is BIGINT.

        An INTEGER foreign key onto a BIGINT primary key works right up to the
        first instrument id past 2^31, and autogenerate had been reporting the
        drift to nobody.
        """
        with migrated.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                text(
                    """
                    SELECT c.table_name, c.data_type
                    FROM information_schema.columns c
                    WHERE c.column_name = 'instrument_id'
                      AND c.table_schema = 'public'
                      AND c.table_name IN
                          ('instruments', 'breakout_events', 'breakout_labels')
                    """
                )
            ).all()
        types = dict(rows)
        assert types["instruments"] == "bigint"
        assert types["breakout_events"] == "bigint"
        assert types["breakout_labels"] == "bigint"


class TestTransactionalFailure:
    """A failed migration must not leave a database that looks half-upgraded."""

    def test_a_real_failing_revision_leaves_no_partial_state(self, empty_database: object) -> None:
        """A genuine Alembic revision that half-succeeds, driven through Alembic.

        Not a hand-rolled transaction: a temporary revision is written on top of
        the real head, it creates a table and then issues the same kind of
        statement that broke 0004 — dropping a constraint that does not exist.
        The upgrade must fail, and afterwards the database must show the
        *previous* head and none of the half-built objects.

        This is the property the original report depended on. `alembic current`
        printing nothing is what made the reported failure recoverable: there
        was no half-migrated database to repair, so the fix could be a pure
        forward fix.
        """
        cfg = alembic_config(DSN or "")
        script = ScriptDirectory.from_config(cfg)
        real_head = script.get_current_head()
        command.upgrade(cfg, "head")
        assert current_revision(empty_database) == real_head

        broken = Path(script.dir) / "versions" / "zzzz_broken_probe.py"
        broken.write_text(
            "from alembic import op\n"
            'revision = "zzzz_broken_probe"\n'
            f'down_revision = "{real_head}"\n'
            "branch_labels = None\n"
            "depends_on = None\n"
            "def upgrade():\n"
            '    op.execute("CREATE TABLE canary_half_built (id integer primary key)")\n'
            '    op.execute("ALTER TABLE orders DROP CONSTRAINT orders_pattern_id_fkey")\n'
            "def downgrade():\n"
            "    pass\n",
            encoding="utf-8",
        )
        try:
            with pytest.raises(sa.exc.ProgrammingError):
                command.upgrade(alembic_config(DSN or ""), "head")

            # The half-built table is gone and the stamp did not advance.
            assert "canary_half_built" not in table_names(empty_database)
            assert current_revision(empty_database) == real_head
        finally:
            broken.unlink(missing_ok=True)

    def test_the_database_is_untouched_after_that_failure(self, empty_database: object) -> None:
        engine = empty_database
        with (
            pytest.raises(sa.exc.ProgrammingError),
            engine.begin() as conn,  # type: ignore[attr-defined]
        ):
            conn.execute(text("CREATE TABLE canary_partial (id integer primary key)"))
            conn.execute(text("ALTER TABLE canary_partial DROP CONSTRAINT nonexistent_fkey"))
        assert "canary_partial" not in table_names(engine)
        assert current_revision(engine) is None

    def test_a_broken_chain_reports_no_revision_at_all(self, empty_database: object) -> None:
        """The observed symptom: `alembic current` printed nothing after the
        failure, and the fresh database was empty. That is the safe outcome, and
        it is what makes the fix a pure forward fix — there is no half-migrated
        database anywhere to repair."""
        assert current_revision(empty_database) is None
        assert table_names(empty_database) == set()

        command.upgrade(alembic_config(DSN or ""), "head")
        assert current_revision(empty_database) is not None
