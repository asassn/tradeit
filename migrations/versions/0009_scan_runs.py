"""The empirical scan: provenance for a Phase 4/5 pass, and its resume ledger

Phase 4 and Phase 5 were implemented and never run over real imported bars. The
missing piece was never the detectors — it was the command that drives them
causally across a snapshot and persists what they produce. These two tables are
what make such a run *attributable* and *resumable*, which are the two
properties that separate a scan from a script somebody ran once.

`scan_runs` is the provenance anchor: which snapshot, which timeframe, which
code version, which detector and engine configuration digests, over which
sessions, and the per-detector tallies. Deliberately not `run_manifests`, which
anchors decision-producing runs and requires a strategy configuration digest —
a scan produces observations, not decisions, and inventing a strategy digest to
satisfy a foreign key would put a fiction in the provenance chain.

`scan_progress` is the resume ledger, one row per completed instrument. It is
written in the same transaction as that instrument's patterns and breakouts, so
a progress row exists if and only if the observations were committed. A scan
interrupted after 30 of 78 instruments resumes at the 31st with nothing
duplicated and nothing half-written — and because the pattern and breakout
repositories are already idempotent by session, re-running a completed
instrument is safe rather than merely wasteful.

Revision ID: 0009_scan_runs
Revises: 0008_data_packages
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009_scan_runs"
down_revision: str | None = "0008_data_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JSONB_OR_JSON = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column("scan_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_session", sa.Date()),
        sa.Column("end_session", sa.Date()),
        sa.Column("code_version", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("pattern_config_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "breakout_config_digest", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("breakout_profile", sa.String(length=32)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("instruments_requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("instruments_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", JSONB_OR_JSON),
    )
    op.create_index("ix_scan_run_snapshot", "scan_runs", ["snapshot_id", "started_at"])
    op.create_index("ix_scan_run_status", "scan_runs", ["status"])

    op.create_table(
        "scan_progress",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "scan_run_id",
            PK,
            sa.ForeignKey("scan_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("instrument_id", PK, nullable=False),
        sa.Column("ticker", sa.String(length=16)),
        sa.Column("first_session", sa.Date()),
        sa.Column("last_session", sa.Date()),
        sa.Column("sessions_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bars_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("patterns_persisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("breakouts_persisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("feed_mode", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("elapsed_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("scan_run_id", "instrument_id", name="uq_scan_progress"),
    )
    op.create_index("ix_scan_progress_run", "scan_progress", ["scan_run_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_progress_run", table_name="scan_progress")
    op.drop_table("scan_progress")
    op.drop_index("ix_scan_run_status", table_name="scan_runs")
    op.drop_index("ix_scan_run_snapshot", table_name="scan_runs")
    op.drop_table("scan_runs")
