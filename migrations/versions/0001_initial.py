"""Initial point-in-time schema.

Creates the bitemporal fact tables, the identity/universe interval tables, and
the ingestion audit trail. Everything here is designed around one query shape:
"the latest revision of a fact whose knowledge_time is at or before an as-of
instant". See docs/adr/0002-point-in-time-bitemporal-storage.md.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import UTCDateTime

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Overlap guards that SQLAlchemy's portable layer cannot express. A ticker may
#: be reassigned to a different company, and an instrument may leave and rejoin
#: a universe -- so a unique index would be wrong. What must never happen is two
#: rows claiming the same ticker (or the same universe slot) at the same time.
EXCLUSIONS = (
    (
        "symbol_mappings",
        "ex_symbol_no_overlap",
        "EXCLUDE USING gist (ticker WITH =, daterange(valid_from, valid_to, '[)') WITH &&)",
    ),
    (
        "universe_memberships",
        "ex_universe_no_overlap",
        "EXCLUDE USING gist ("
        "universe WITH =, instrument_id WITH =, "
        "daterange(valid_from, valid_to, '[)') WITH &&)",
    ),
)


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("requested_from", sa.Date(), nullable=True),
        sa.Column("requested_to", sa.Date(), nullable=True),
        sa.Column("started_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("finished_at", UTCDateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_recent", "ingestion_runs", ["dataset", "started_at"], unique=False
    )
    op.create_table(
        "instruments",
        sa.Column(
            "instrument_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("primary_exchange", sa.String(length=8), nullable=False),
        sa.Column("asset_class", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("first_trade_date", sa.Date(), nullable=True),
        sa.Column("listing_status", sa.String(length=16), nullable=False),
        sa.Column("delisted_date", sa.Date(), nullable=True),
        sa.Column("figi", sa.String(length=12), nullable=True),
        sa.Column("cik", sa.String(length=10), nullable=True),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "(listing_status = 'active' AND delisted_date IS NULL) OR (listing_status <> 'active' AND delisted_date IS NOT NULL)",
            name="ck_instrument_lifecycle",
        ),
        sa.PrimaryKeyConstraint("instrument_id"),
        sa.UniqueConstraint("figi"),
    )
    op.create_index(op.f("ix_instruments_cik"), "instruments", ["cik"], unique=False)
    op.create_table(
        "corporate_actions",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("action_type", sa.String(length=24), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("event_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_source", sa.String(length=16), nullable=False),
        sa.Column("ratio", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("cash_amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("new_ticker", sa.String(length=16), nullable=True),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint("cash_amount >= 0", name="ck_action_cash"),
        sa.CheckConstraint("ratio > 0", name="ck_action_ratio"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id", "action_type", "ex_date", "knowledge_time", name="uq_action_revision"
        ),
    )
    op.create_index(
        "ix_action_pit",
        "corporate_actions",
        ["instrument_id", "ex_date", "knowledge_time"],
        unique=False,
    )
    op.create_table(
        "earnings_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("session_hint", sa.String(length=16), nullable=True),
        sa.Column("fiscal_period", sa.String(length=4), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("event_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_source", sa.String(length=16), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("eps_actual", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("eps_estimate", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "fiscal_year",
            "fiscal_period",
            "knowledge_time",
            name="uq_earnings_revision",
        ),
    )
    op.create_index(
        "ix_earnings_pit",
        "earnings_events",
        ["instrument_id", "scheduled_date", "knowledge_time"],
        unique=False,
    )
    op.create_table(
        "fundamental_facts",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("fiscal_period", sa.String(length=4), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("event_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_source", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column(
            "restatement_of", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True
        ),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint("knowledge_time >= event_time", name="ck_fundamental_knowledge"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["restatement_of"], ["fundamental_facts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "metric",
            "fiscal_year",
            "fiscal_period",
            "knowledge_time",
            name="uq_fundamental_revision",
        ),
    )
    op.create_index(
        "ix_fundamental_pit",
        "fundamental_facts",
        ["instrument_id", "metric", "knowledge_time", "period_end"],
        unique=False,
    )
    op.create_table(
        "ohlcv_bars",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("event_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("knowledge_source", sa.String(length=16), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("vwap", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint("high >= low", name="ck_bar_high_low"),
        sa.CheckConstraint("high >= open AND high >= close", name="ck_bar_high"),
        sa.CheckConstraint("knowledge_time >= event_time", name="ck_bar_knowledge"),
        sa.CheckConstraint("low <= open AND low <= close", name="ck_bar_low"),
        sa.CheckConstraint("volume >= 0", name="ck_bar_volume"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id", "timeframe", "session_date", "knowledge_time", name="uq_bar_revision"
        ),
    )
    op.create_index(
        "ix_bar_pit",
        "ohlcv_bars",
        ["instrument_id", "timeframe", "session_date", "knowledge_time"],
        unique=False,
    )
    op.create_table(
        "quarantined_rows",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "ingestion_run_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quarantine_run", "quarantined_rows", ["ingestion_run_id"], unique=False)
    op.create_table(
        "symbol_mappings",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_symbol_interval"),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_symbol_instrument", "symbol_mappings", ["instrument_id", "valid_from"], unique=False
    )
    op.create_index(
        "ix_symbol_lookup", "symbol_mappings", ["ticker", "valid_from", "valid_to"], unique=False
    )
    op.create_table(
        "universe_memberships",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("universe", sa.String(length=64), nullable=False),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("exit_reason", sa.String(length=16), nullable=True),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_universe_interval"
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_universe_asof",
        "universe_memberships",
        ["universe", "valid_from", "valid_to"],
        unique=False,
    )
    op.create_index(
        "ix_universe_instrument",
        "universe_memberships",
        ["instrument_id", "universe"],
        unique=False,
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    # btree_gist lets a gist EXCLUDE constraint mix scalar equality with range
    # overlap; without it the ticker/universe columns cannot participate.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    for table, name, clause in EXCLUSIONS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {clause}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, name, _ in EXCLUSIONS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.drop_table("universe_memberships")
    op.drop_table("symbol_mappings")
    op.drop_table("quarantined_rows")
    op.drop_table("ohlcv_bars")
    op.drop_table("fundamental_facts")
    op.drop_table("earnings_events")
    op.drop_table("corporate_actions")
    op.drop_table("instruments")
    op.drop_table("ingestion_runs")
