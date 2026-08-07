"""Phase 4 pattern persistence

Replaces the Phase 2 draft `patterns` table. That draft keyed uniqueness on
`(instrument, type, start_date, detected_on)`, which minted a new row every day
the same structure was re-detected -- the exact failure the Phase 4 brief
forbids. Identity now keys on a content hash of the things that do not change
as a pattern evolves, current state lives on the pattern row, and every
historical belief lives in the append-only `pattern_observations` table.

The old table is dropped rather than migrated. Nothing has run against it, and
carrying forward rows whose identity semantics were wrong would import the bug
into the new schema.

Revision ID: 0004_phase4_patterns
Revises: 0003_phase3_analytics
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import JSONB_OR_JSON, PRICE, UTCDateTime

revision: str = "0004_phase4_patterns"
down_revision: str | None = "0003_phase3_analytics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Dependents that carry a foreign key to `patterns.id`.
_DEPENDENTS = ("breakout_events", "orders")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # PostgreSQL refuses to drop a table that is still referenced, so the
    # dependents' foreign keys come off first and go back on at the end.
    #
    # SQLite is skipped deliberately rather than defensively. It has no
    # ALTER-based constraint drop -- Alembic emulates one by recreating the
    # whole table -- and it does not enforce the reference in the first place
    # unless foreign_keys pragma is on. Attempting the drop there fails on a
    # constraint name that never existed.
    if dialect != "sqlite":
        for table in _DEPENDENTS:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(f"{table}_pattern_id_fkey", type_="foreignkey")

    op.drop_table("patterns")

    op.create_table(
        "patterns",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False),
        sa.Column("identity_key", sa.String(length=32), nullable=False),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False
        ),
        sa.Column("pattern_type", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("detector_name", sa.String(length=40), nullable=False),
        sa.Column("detector_version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("data_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("state_changed_at", UTCDateTime(), nullable=False),
        sa.Column("terminal_at", UTCDateTime(), nullable=True),
        sa.Column("first_detected_at", UTCDateTime(), nullable=False),
        sa.Column("last_observed_at", UTCDateTime(), nullable=False),
        sa.Column("structural_start_date", sa.Date(), nullable=False),
        sa.Column("structural_end_date", sa.Date(), nullable=False),
        sa.Column("first_detected_session", sa.Date(), nullable=False),
        sa.Column("last_observed_session", sa.Date(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("peak_quality", sa.Float(), nullable=False),
        sa.Column("evidence_coverage", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("resistance_price", PRICE, nullable=True),
        sa.Column("support_price", PRICE, nullable=True),
        sa.Column("invalidation_price", PRICE, nullable=True),
        sa.Column("geometry", JSONB_OR_JSON, nullable=True),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.Column(
            "run_manifest_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=True
        ),
        sa.CheckConstraint("structural_end_date >= structural_start_date", name="ck_pattern_dates"),
        sa.CheckConstraint("quality >= 0 AND quality <= 100", name="ck_pattern_quality"),
        sa.CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_pattern_coverage"
        ),
        sa.CheckConstraint(
            "support_price IS NULL OR resistance_price IS NULL OR support_price < resistance_price",
            name="ck_pattern_support_below_resistance",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_manifest_id"], ["run_manifests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_key", "detector_version", name="uq_pattern_identity"),
    )
    op.create_index("ix_pattern_active", "patterns", ["state", "last_observed_session"])
    op.create_index("ix_pattern_instrument", "patterns", ["instrument_id", "pattern_type", "state"])

    op.create_table(
        "pattern_observations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False),
        sa.Column("pattern_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("evidence_coverage", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("component_scores", JSONB_OR_JSON, nullable=True),
        sa.Column("supporting_evidence", JSONB_OR_JSON, nullable=True),
        sa.Column("contradicting_evidence", JSONB_OR_JSON, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("quality >= 0 AND quality <= 100", name="ck_observation_quality"),
        sa.ForeignKeyConstraint(["pattern_id"], ["patterns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_id", "session_date", name="uq_pattern_observation"),
    )
    op.create_index("ix_pattern_observation_session", "pattern_observations", ["session_date"])

    op.create_table(
        "pattern_relationships",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False),
        sa.Column(
            "from_pattern_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False
        ),
        sa.Column(
            "to_pattern_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False
        ),
        sa.Column("relationship", sa.String(length=24), nullable=False),
        sa.Column("established_at", UTCDateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("from_pattern_id <> to_pattern_id", name="ck_pattern_no_self_relation"),
        sa.ForeignKeyConstraint(["from_pattern_id"], ["patterns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_pattern_id"], ["patterns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_pattern_id", "to_pattern_id", "relationship", name="uq_pattern_relationship"
        ),
    )
    op.create_index(
        "ix_pattern_relationship_to", "pattern_relationships", ["to_pattern_id", "relationship"]
    )

    op.create_table(
        "pattern_labels",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False),
        sa.Column(
            "instrument_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=False
        ),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("pattern_type", sa.String(length=40), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("human_quality", sa.Float(), nullable=True),
        sa.Column("reviewer_confidence", sa.Float(), nullable=True),
        sa.Column("is_pattern", sa.Boolean(), nullable=False),
        sa.Column("annotated_pivots", JSONB_OR_JSON, nullable=True),
        sa.Column("annotated_boundaries", JSONB_OR_JSON, nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("detector_name", sa.String(length=40), nullable=True),
        sa.Column("detector_version", sa.Integer(), nullable=True),
        sa.Column("detector_quality", sa.Float(), nullable=True),
        sa.Column("detector_state", sa.String(length=32), nullable=True),
        sa.Column("pattern_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), nullable=True),
        sa.Column("labelled_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "human_quality IS NULL OR (human_quality >= 0 AND human_quality <= 100)",
            name="ck_label_quality",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.instrument_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["pattern_id"], ["patterns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "as_of_session",
            "timeframe",
            "pattern_type",
            "reviewer",
            name="uq_pattern_label",
        ),
    )
    op.create_index("ix_pattern_label_lookup", "pattern_labels", ["pattern_type", "as_of_session"])

    if dialect != "sqlite":
        for table in _DEPENDENTS:
            with op.batch_alter_table(table) as batch:
                batch.create_foreign_key(
                    f"{table}_pattern_id_fkey",
                    "patterns",
                    ["pattern_id"],
                    ["id"],
                    ondelete="SET NULL",
                )


def downgrade() -> None:
    op.drop_table("pattern_labels")
    op.drop_table("pattern_relationships")
    op.drop_table("pattern_observations")
    op.drop_table("patterns")
