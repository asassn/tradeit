"""Validation of the Phase 2 schema against real PostgreSQL.

Partitioning, JSONB, and most CHECK constraints cannot be exercised on SQLite,
and a schema that has only ever been described is a guess. Skipped unless
``TRADEIT_TEST_PG_DSN`` points at a throwaway database.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from tradeit.storage import tables

pytestmark = pytest.mark.integration

DSN = os.environ.get("TRADEIT_TEST_PG_DSN")
UTC = dt.UTC

if not DSN:
    pytest.skip("set TRADEIT_TEST_PG_DSN to run PostgreSQL tests", allow_module_level=True)


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DSN)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


#: Objects PostgreSQL creates from a partitioned parent: the child tables and
#: the per-child copies of the parent's indexes.
_PARTITION_CHILD = re.compile(r"^(indicator_values|system_logs)_(p\d{6}|default)(_.*)?$")


def _diff_object_name(entry: object) -> str:
    """Best-effort name of whatever a diff entry is about."""
    if not isinstance(entry, tuple) or len(entry) < 2:
        return ""
    target = entry[1]
    name = getattr(target, "name", None)
    return str(name) if name else ""


def _is_partition_child(entry: object) -> bool:
    """Whether a diff entry concerns a partition child rather than real drift.

    Partition children and their inherited indexes are physical objects created
    from the parent definition. The ORM deliberately does not model them, so
    autogenerate reports them as objects it would drop. Filtering them here is
    what lets this check catch genuine divergence between the migration and the
    ORM instead of drowning in expected noise.
    """
    return bool(_PARTITION_CHILD.match(_diff_object_name(entry)))


class TestMigration:
    def test_both_migrations_applied(self, session):
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0002_phase2_schema"

    def test_every_orm_table_exists_in_the_database(self, session):
        present = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "UNION SELECT relname FROM pg_class WHERE relkind = 'p'"
                )
            )
        }
        missing = set(tables.Base.metadata.tables) - present
        assert not missing, f"ORM defines tables the migration did not create: {sorted(missing)}"

    def test_the_migration_and_the_orm_have_not_drifted(self, engine):
        """Autogenerate against the live schema must find nothing to do."""
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, tables.Base.metadata)

        material = [d for d in diff if not _is_partition_child(d)]
        assert not material, f"schema drift detected: {material}"


class TestPartitioning:
    def test_the_high_volume_tables_are_partitioned(self, session):
        partitioned = {
            row[0]
            for row in session.execute(text("SELECT relname FROM pg_class WHERE relkind='p'"))
        }
        assert {"indicator_values", "system_logs"} <= partitioned

    def test_partitions_exist_ahead_of_need(self, session):
        """A partitioned table with no partition for today rejects every insert."""
        count = session.execute(
            text(
                "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhparent "
                "WHERE c.relname = 'indicator_values'"
            )
        ).scalar_one()
        assert count >= 24

    def test_rows_route_to_the_partition_for_their_month(self, session):
        session.add(
            tables.IndicatorValue(
                instrument_id=1,
                session_date=dt.date(2026, 3, 16),
                indicator="sma_50",
                timeframe="1d",
                feature_set_digest="deadbeef",
                value=101.5,
            )
        )
        session.flush()
        located = session.execute(
            text(
                "SELECT tableoid::regclass::text FROM indicator_values "
                "WHERE session_date = '2026-03-16' AND indicator = 'sma_50'"
            )
        ).scalar_one()
        assert located == "indicator_values_p202603"

    def test_a_date_bounded_query_prunes_partitions(self, session):
        plan = "\n".join(
            row[0]
            for row in session.execute(
                text(
                    "EXPLAIN SELECT * FROM indicator_values "
                    "WHERE session_date BETWEEN '2026-03-01' AND '2026-03-31'"
                )
            )
        )
        assert "indicator_values_p202603" in plan
        assert "indicator_values_p202512" not in plan, "pruning is not happening"

    def test_the_maintenance_helper_is_idempotent(self, session):
        for _ in range(2):
            session.execute(
                text("SELECT tradeit_ensure_month_partition('indicator_values'::regclass, :m)"),
                {"m": dt.date(2030, 1, 1)},
            )
        count = session.execute(
            text("SELECT count(*) FROM pg_class WHERE relname = 'indicator_values_p203001'")
        ).scalar_one()
        assert count == 1

    def test_the_default_partition_is_a_backstop_not_a_destination(self, session):
        """Rows landing in the default mean maintenance fell behind, and they
        block attaching a proper partition for that range later."""
        stray = session.execute(text("SELECT count(*) FROM indicator_values_default")).scalar_one()
        assert stray == 0


class TestConstraints:
    def _portfolio(self, session, **overrides) -> tables.Portfolio:
        payload = {
            "name": f"p-{uuid.uuid4().hex[:8]}",
            "mode": "paper",
            "initial_capital": Decimal("100000"),
            "inception_date": dt.date(2024, 1, 2),
        }
        payload.update(overrides)
        portfolio = tables.Portfolio(**payload)
        session.add(portfolio)
        session.flush()
        return portfolio

    def test_an_open_position_must_carry_a_stop(self, session):
        """A position without a stop has undefined risk and cannot be sized."""
        portfolio = self._portfolio(session)
        session.add(
            tables.Instrument(
                instrument_id=901,
                primary_exchange="XNYS",
                asset_class="common_stock",
                name="T",
                source="test",
            )
        )
        session.flush()
        session.add(
            tables.Position(
                portfolio_id=portfolio.id,
                instrument_id=901,
                status="open",
                quantity=Decimal("100"),
                average_entry_price=Decimal("50"),
                stop_price=None,
                opened_on=dt.date(2024, 3, 4),
            )
        )
        with pytest.raises(IntegrityError, match="ck_position_open_has_stop"):
            session.flush()

    def test_an_invalid_portfolio_mode_is_refused(self, session):
        with pytest.raises(IntegrityError, match="ck_portfolio_mode"):
            self._portfolio(session, mode="production")

    def test_a_pattern_stop_above_its_pivot_is_refused(self, session):
        """Stop above pivot means negative risk per share, which would make
        position sizing divide by a negative number and size backwards."""
        session.execute(
            text(
                "INSERT INTO artifact_versions (digest, kind, name, payload) "
                "VALUES ('d1', 'strategy_config', 'x', '{}'::jsonb), "
                "('d2', 'data_snapshot', 'x', '{}'::jsonb) ON CONFLICT DO NOTHING"
            )
        )
        session.add(
            tables.RunManifest(
                run_id=f"r-{uuid.uuid4().hex[:8]}",
                run_kind="scan",
                as_of=dt.datetime(2024, 3, 8, 21, tzinfo=UTC),
                manifest_digest="m1",
                strategy_config_digest="d1",
                data_snapshot_digest="d2",
                code_version="0.2.0",
            )
        )
        session.flush()
        manifest_id = session.execute(select(func.max(tables.RunManifest.id))).scalar_one()

        session.add(
            tables.Instrument(
                instrument_id=902,
                primary_exchange="XNYS",
                asset_class="common_stock",
                name="T2",
                source="test",
            )
        )
        session.flush()
        session.add(
            tables.Pattern(
                instrument_id=902,
                pattern_type="flat_base",
                status="complete",
                start_date=dt.date(2024, 1, 2),
                end_date=dt.date(2024, 3, 1),
                detected_on=dt.date(2024, 3, 1),
                last_evaluated_on=dt.date(2024, 3, 1),
                pivot_price=Decimal("50"),
                stop_price=Decimal("55"),
                length_sessions=40,
                quality=0.8,
                run_manifest_id=manifest_id,
            )
        )
        with pytest.raises(IntegrityError, match="ck_pattern_stop_below_pivot"):
            session.flush()

    def test_a_monte_carlo_run_below_one_hundred_iterations_is_refused(self, session):
        with pytest.raises(IntegrityError, match="ck_montecarlo_iterations"):
            session.execute(
                text(
                    "INSERT INTO monte_carlo_runs "
                    "(backtest_run_id, method, iterations, seed, iterations_completed) "
                    "VALUES (1, 'trade_sequence', 50, 1, 0)"
                )
            )

    def test_a_model_validating_before_it_finished_training_is_refused(self, session):
        """Validating on data the model was trained on is the definition of
        an unusable validation score."""
        session.execute(
            text(
                "INSERT INTO artifact_versions (digest, kind, name, payload) "
                "VALUES ('mdl', 'model', 'm', '{}'::jsonb) ON CONFLICT DO NOTHING"
            )
        )
        with pytest.raises(IntegrityError, match="ck_model_validation_after_training"):
            session.execute(
                text(
                    "INSERT INTO model_metadata "
                    "(digest, name, model_type, training_start, training_end, "
                    "validation_start, is_approved) VALUES "
                    "('mdl', 'm', 'xgboost', '2020-01-01', '2023-01-01', "
                    "'2022-01-01', false)"
                )
            )

    def test_a_news_sentiment_without_a_model_version_is_refused(self, session):
        """A score with no model version cannot be reproduced, and silently
        becomes tomorrow's model applied to yesterday's news."""
        with pytest.raises(IntegrityError, match="ck_news_sentiment_provenance"):
            session.execute(
                text(
                    "INSERT INTO news_items "
                    "(event_time, knowledge_time, knowledge_source, headline, sentiment, source) "
                    "VALUES ('2024-03-08T14:00:00Z', '2024-03-08T14:00:00Z', 'reported', "
                    "'headline', 0.8, 'test')"
                )
            )

    def test_a_score_outside_zero_to_one_is_refused(self, session):
        with pytest.raises(IntegrityError, match="ck_score_range"):
            session.execute(
                text(
                    "INSERT INTO opportunity_scores "
                    "(instrument_id, session_date, direction, total, components, "
                    "run_manifest_id) VALUES (1, '2024-03-08', 'long', 1.5, "
                    "'{}'::jsonb, 1)"
                )
            )


class TestJsonbBehaviour:
    def test_structured_payloads_round_trip(self, session):
        session.add(
            tables.ArtifactVersion(
                digest=f"h{uuid.uuid4().hex[:16]}",
                kind="strategy_config",
                name="baseline",
                payload={"sizing": {"risk_per_trade_pct": 0.005}, "weights": [0.2, 0.8]},
            )
        )
        session.flush()
        stored = session.execute(
            select(tables.ArtifactVersion.payload).order_by(
                tables.ArtifactVersion.created_at.desc()
            )
        ).first()
        assert stored[0]["sizing"]["risk_per_trade_pct"] == 0.005

    def test_jsonb_containment_is_queryable(self, session):
        """The reason score components are JSONB rather than TEXT: asking
        "which scores were driven by relative strength?" must not need a
        full-table scan and a JSON parse in Python.
        """
        digest = f"h{uuid.uuid4().hex[:16]}"
        session.add(
            tables.ArtifactVersion(
                digest=digest, kind="feature_set", name="v1", payload={"family": "momentum"}
            )
        )
        session.flush()
        found = session.execute(
            text("SELECT count(*) FROM artifact_versions WHERE payload @> :probe"),
            {"probe": '{"family": "momentum"}'},
        ).scalar_one()
        assert found >= 1
