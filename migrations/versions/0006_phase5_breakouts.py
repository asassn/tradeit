"""Phase 5: breakout events, observations, relationships and labels

The Phase 2 `breakout_events` table was a sketch and is replaced outright.

It keyed uniqueness on `(instrument, pattern, session, status)`, which makes it
a status log rather than an identity table: an *attempt* had no row of its own.
That has three consequences the Phase 5 brief rules out. Attempts are not
countable, so item 26's attempt history cannot exist. There is no place to store
the boundary the attempt was judged against, so an event read back later would
be re-judged against whatever the pattern's resistance had become — resistance
refined with post-breakout bars, which item 1 forbids. And a single `status`
column cannot express the distinctions the phase is about: an intraday
penetration is not a close above, a rejection is not a failure, and an expiry is
not a failure either.

The replacement is the same shape as the Phase 4 pattern schema: one identity
row per attempt holding current state, plus an append-only observation log. The
observation log is what makes item 15 true — an event that failed on Thursday
still shows that it was CONFIRMED on Tuesday, because the earlier row cannot be
written twice.

The old table is dropped rather than migrated. Nothing has written to it: no
phase implemented a breakout monitor, so there are no rows to preserve, and
carrying a column set that no code reads is how a schema accumulates fossils.

`breakout_labels` carries `knowledge_horizon_session` alongside
`as_of_session`, with a check constraint that the horizon is not before the
session. Some breakout labels are decidable from the breakout bar and some
require a window after it; a corpus that does not record which is which has
labels that silently encode different amounts of future information.

Revision ID: 0006_phase5_breakouts
Revises: 0005_phase4_gate
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import JSONB_OR_JSON, PK, PRICE, UTCDateTime

revision: str = "0006_phase5_breakouts"
down_revision: str | None = "0005_phase4_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("breakout_events")

    op.create_table(
        "breakout_events",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(length=32), nullable=False),
        sa.Column(
            "instrument_id",
            sa.Integer(),
            sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pattern_id", PK, sa.ForeignKey("patterns.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("pattern_key", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="1d"),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pattern_detector_name", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("pattern_detector_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pattern_config_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "breakout_config_digest", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("scorer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data_snapshot_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("profile", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("terminal_reason", sa.String(length=40), nullable=True),
        sa.Column("confirmed_path", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("opened_session", sa.Date(), nullable=False),
        sa.Column("first_approach_session", sa.Date(), nullable=True),
        sa.Column("first_penetration_session", sa.Date(), nullable=True),
        sa.Column("first_qualifying_close_session", sa.Date(), nullable=True),
        sa.Column("confirmed_session", sa.Date(), nullable=True),
        sa.Column("last_observed_session", sa.Date(), nullable=False),
        sa.Column("boundary_level", PRICE, nullable=False),
        sa.Column("boundary_anchor_date", sa.Date(), nullable=False),
        sa.Column("boundary_tolerance_pct", sa.Float(), nullable=False),
        sa.Column("boundary_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("boundary_method", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("boundary_touches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("boundary_slope", sa.Float(), nullable=False, server_default="0"),
        sa.Column("atr_at_open", sa.Float(), nullable=True),
        sa.Column("pattern_type", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("pattern_quality", sa.Float(), nullable=False, server_default="0"),
        sa.Column("breakout_quality", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confirmation_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("breakout_close", PRICE, nullable=True),
        sa.Column("breakout_low", PRICE, nullable=True),
        sa.Column("breakout_volume", sa.Float(), nullable=True),
        sa.Column("qualifying_closes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gap_class", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column(
            "earnings_context",
            sa.String(length=32),
            nullable=False,
            server_default="unknown_event_context",
        ),
        sa.Column("retest", JSONB_OR_JSON, nullable=True),
        sa.Column("quality_components", JSONB_OR_JSON, nullable=True),
        sa.Column("confirmation_components", JSONB_OR_JSON, nullable=True),
        sa.Column(
            "run_manifest_id",
            PK,
            sa.ForeignKey("run_manifests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("event_key", name="uq_breakout_event_identity"),
        sa.UniqueConstraint(
            "instrument_id",
            "timeframe",
            "pattern_key",
            "attempt_number",
            name="uq_breakout_attempt",
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_breakout_attempt"),
        sa.CheckConstraint(
            "breakout_quality >= 0 AND breakout_quality <= 100", name="ck_breakout_quality"
        ),
        sa.CheckConstraint(
            "confirmation_score >= 0 AND confirmation_score <= 100",
            name="ck_breakout_confirmation",
        ),
        sa.CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_breakout_coverage"
        ),
    )
    op.create_index("ix_breakout_active", "breakout_events", ["state", "last_observed_session"])
    op.create_index(
        "ix_breakout_instrument", "breakout_events", ["instrument_id", "timeframe", "state"]
    )
    op.create_index("ix_breakout_pattern", "breakout_events", ["pattern_id", "opened_session"])

    op.create_table(
        "breakout_observations",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            PK,
            sa.ForeignKey("breakout_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("observed_at", UTCDateTime, nullable=False),
        sa.Column("knowledge_time", UTCDateTime, nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=True),
        sa.Column("breakout_quality", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confirmation_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_coverage", sa.Float(), nullable=False, server_default="100"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("distance_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("distance_atr", sa.Float(), nullable=True),
        sa.Column("measurements", JSONB_OR_JSON, nullable=True),
        sa.Column("supporting_evidence", JSONB_OR_JSON, nullable=True),
        sa.Column("contradicting_evidence", JSONB_OR_JSON, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", "session_date", name="uq_breakout_observation"),
        sa.CheckConstraint(
            "breakout_quality >= 0 AND breakout_quality <= 100",
            name="ck_breakout_observation_quality",
        ),
    )
    op.create_index("ix_breakout_observation_session", "breakout_observations", ["session_date"])

    op.create_table(
        "breakout_relationships",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "from_event_id",
            PK,
            sa.ForeignKey("breakout_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_event_id",
            PK,
            sa.ForeignKey("breakout_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship", sa.String(length=24), nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "from_event_id", "to_event_id", "relationship", name="uq_breakout_relationship"
        ),
        sa.CheckConstraint("from_event_id <> to_event_id", name="ck_breakout_no_self_relation"),
    )
    op.create_index(
        "ix_breakout_relationship_to", "breakout_relationships", ["to_event_id", "relationship"]
    )

    op.create_table(
        "breakout_labels",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            PK,
            sa.ForeignKey("breakout_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "instrument_id",
            sa.Integer(),
            sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("knowledge_horizon_session", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="1d"),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("label", sa.String(length=24), nullable=False),
        sa.Column("reviewer_confidence", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("engine_state", sa.String(length=32), nullable=True),
        sa.Column("engine_breakout_quality", sa.Float(), nullable=True),
        sa.Column("engine_confirmation_score", sa.Float(), nullable=True),
        sa.Column("engine_coverage", sa.Float(), nullable=True),
        sa.Column("engine_profile", sa.String(length=32), nullable=True),
        sa.Column("breakout_config_digest", sa.String(length=64), nullable=True),
        sa.Column("scorer_version", sa.Integer(), nullable=True),
        sa.Column("labelled_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint(
            "instrument_id",
            "as_of_session",
            "timeframe",
            "reviewer",
            "revision",
            name="uq_breakout_label",
        ),
        sa.CheckConstraint(
            "knowledge_horizon_session >= as_of_session", name="ck_breakout_label_horizon"
        ),
    )
    op.create_index("ix_breakout_label_lookup", "breakout_labels", ["label", "as_of_session"])


def downgrade() -> None:
    op.drop_index("ix_breakout_label_lookup", table_name="breakout_labels")
    op.drop_table("breakout_labels")
    op.drop_index("ix_breakout_relationship_to", table_name="breakout_relationships")
    op.drop_table("breakout_relationships")
    op.drop_index("ix_breakout_observation_session", table_name="breakout_observations")
    op.drop_table("breakout_observations")
    op.drop_index("ix_breakout_pattern", table_name="breakout_events")
    op.drop_index("ix_breakout_instrument", table_name="breakout_events")
    op.drop_index("ix_breakout_active", table_name="breakout_events")
    op.drop_table("breakout_events")

    # Restore the Phase 2 sketch so the downgrade path lands on the schema the
    # previous revision described, rather than on an approximation of it.
    op.create_table(
        "breakout_events",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "instrument_id",
            sa.Integer(),
            sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pattern_id", PK, sa.ForeignKey("patterns.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pivot_price", PRICE, nullable=False),
        sa.Column("trigger_price", PRICE, nullable=True),
        sa.Column("distance_to_pivot_pct", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column("follow_through_pct", sa.Float(), nullable=True),
        sa.Column(
            "run_manifest_id",
            PK,
            sa.ForeignKey("run_manifests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "instrument_id", "pattern_id", "session_date", "status", name="uq_breakout_event"
        ),
    )
    op.create_index("ix_breakout_session", "breakout_events", ["session_date", "status"])
    op.create_index("ix_breakout_pattern", "breakout_events", ["pattern_id", "session_date"])
