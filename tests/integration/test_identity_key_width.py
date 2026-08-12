"""A re-minted identity is 35 characters and the column held 32.

The first real scan of AAPL reached session ~4,000 of 4,174 and PostgreSQL
refused ``671a05b24082ef63e6f16232:2026-07-23`` — 24 hex characters of content
hash, a colon, and an ISO date — into ``patterns.identity_key VARCHAR(32)``.

SQLite ignores ``VARCHAR`` lengths entirely, so **only PostgreSQL can prove
this**. Every test here runs against the real database, through the real
migration chain.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from tradeit.storage import tables
from tradeit.storage.tables import IDENTITY_KEY_LENGTH

DSN = os.environ.get("TRADEIT_TEST_PG_DSN")

if not DSN:
    pytest.skip("set TRADEIT_TEST_PG_DSN to run PostgreSQL tests", allow_module_level=True)

#: The exact key that failed, from the real scan.
FAILING_KEY = "671a05b24082ef63e6f16232:2026-07-23"

#: The unsuffixed identity it was minted to be distinct from.
BASE_KEY = "671a05b24082ef63e6f16232"


@pytest.fixture
def pg_session():
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DSN)
    command.upgrade(cfg, "head")

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    session.add(
        tables.Instrument(
            instrument_id=1,
            primary_exchange="XNYS",
            asset_class="common_stock",
            name="Test",
            source="test",
        )
    )
    session.flush()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def pattern(identity_key: str, *, start: dt.date, detector_version: int = 1) -> tables.Pattern:
    now = dt.datetime.now(dt.UTC)
    return tables.Pattern(
        identity_key=identity_key,
        instrument_id=1,
        pattern_type="bull_flag",
        timeframe="1d",
        detector_name="bull_flag",
        detector_version=detector_version,
        config_digest="",
        data_snapshot_digest="",
        state="forming",
        state_changed_at=now,
        first_detected_at=now,
        last_observed_at=now,
        structural_start_date=start,
        structural_end_date=start,
        first_detected_session=start,
        last_observed_session=start,
        quality=50.0,
        peak_quality=50.0,
        evidence_coverage=100.0,
        confidence=50.0,
        session_count=10,
    )


class TestTheColumnHoldsWhatTheCodeProduces:
    def test_the_exact_key_that_failed_now_persists(self, pg_session) -> None:
        assert len(FAILING_KEY) == 35 > 32, "fixture must be the key that overflowed"
        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23)))
        pg_session.commit()
        stored = pg_session.scalars(
            select(tables.Pattern.identity_key).where(tables.Pattern.identity_key == FAILING_KEY)
        ).one()
        assert stored == FAILING_KEY, "stored verbatim, not truncated"

    def test_a_remint_stays_distinct_from_the_identity_it_forked_from(self, pg_session) -> None:
        """The reason truncation would be worse than the crash.

        24 characters of hash plus a colon leaves seven of the ten-character
        date inside 32, so a cut key keeps ``:YYYY-MM`` and loses the day. Two
        structures re-minted in the same month would land on the same string,
        merging two separate lives of one structure — exactly what the suffix
        exists to prevent.
        """
        pg_session.add(pattern(BASE_KEY, start=dt.date(2024, 1, 3)))
        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23)))
        same_month = f"{BASE_KEY}:2026-07-03"
        pg_session.add(pattern(same_month, start=dt.date(2026, 7, 3)))
        pg_session.commit()

        keys = set(pg_session.scalars(select(tables.Pattern.identity_key)))
        assert keys == {BASE_KEY, FAILING_KEY, same_month}
        assert len(keys) == 3, "three lives, three rows, three distinct keys"
        # The pair that a 32-character column would have collapsed into one.
        assert FAILING_KEY[:32] == same_month[:32]

    def test_the_unique_constraint_still_applies_to_a_suffixed_key(self, pg_session) -> None:
        from sqlalchemy.exc import IntegrityError

        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23)))
        pg_session.commit()
        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23)))
        with pytest.raises(IntegrityError):
            pg_session.commit()
        pg_session.rollback()

    def test_the_same_key_under_a_new_detector_version_is_a_separate_row(self, pg_session) -> None:
        # uq_pattern_identity is (identity_key, detector_version), and widening
        # the column must not have changed which rows it separates.
        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23), detector_version=1))
        pg_session.add(pattern(FAILING_KEY, start=dt.date(2026, 7, 23), detector_version=2))
        pg_session.commit()
        assert pg_session.scalar(select(func.count()).select_from(tables.Pattern)) == 2

    def test_a_breakout_event_copies_the_suffixed_pattern_key(self, pg_session) -> None:
        """The second affected column.

        ``breakout_events.pattern_key`` holds the pattern identity verbatim. It
        had not failed yet only because `_persist` writes patterns first, so
        the scan aborted one table earlier.
        """
        pg_session.add(
            tables.BreakoutEvent(
                event_key="a" * 24,
                pattern_key=FAILING_KEY,
                instrument_id=1,
                timeframe="1d",
                state="not_approaching",
                opened_session=dt.date(2026, 7, 23),
                last_observed_session=dt.date(2026, 7, 23),
                attempt_number=1,
                boundary_level=10,
                boundary_kind="structural_pattern_boundary",
                boundary_method="swing_highs",
                boundary_anchor_date=dt.date(2026, 7, 1),
                boundary_tolerance_pct=0.01,
            )
        )
        pg_session.commit()
        stored = pg_session.scalars(select(tables.BreakoutEvent.pattern_key)).one()
        assert stored == FAILING_KEY


class TestTheSchemaMatchesTheDerivation:
    def test_every_identity_column_is_the_declared_width(self, pg_session) -> None:
        rows = pg_session.execute(
            text(
                """
                SELECT table_name, column_name, character_maximum_length
                FROM information_schema.columns
                WHERE (table_name, column_name) IN (
                    ('patterns', 'identity_key'),
                    ('breakout_events', 'event_key'),
                    ('breakout_events', 'pattern_key')
                )
                ORDER BY table_name, column_name
                """
            )
        ).all()
        assert len(rows) == 3, "all three identity columns must exist"
        for table_name, column_name, width in rows:
            assert width == IDENTITY_KEY_LENGTH, f"{table_name}.{column_name} is {width}"

    def test_the_migration_chain_reaches_the_new_head(self, pg_session) -> None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("alembic.ini")
        head = ScriptDirectory.from_config(cfg).get_current_head()
        current = pg_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert current == head
