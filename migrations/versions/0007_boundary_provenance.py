"""Empirical gate: boundary provenance on breakout events

A breakout event records the level it was judged against. Until now it did not
record *where that level came from*, and the two populations are not the same
object: a crossing of a level somebody typed into a research notebook and a
breakout of a causally-derived Phase 4 structure both produce a row, and any
rate computed over the union describes a mixture nobody chose.

`boundary_kind` makes the distinction queryable rather than inferable. The
production monitor writes only `structural_pattern_boundary`; research and
manual levels are tagged as what they are, and `other` exists so that "we do not
know where this came from" is expressible — a row that cannot state its
provenance must not be able to claim a good one.

The default is deliberately `other` rather than `structural_pattern_boundary`.
Backfilling the optimistic value would silently assert something about existing
rows that nobody verified; `other` says what is actually known about them, which
is nothing. No rows exist yet in any case.

See ADR-0025.

Revision ID: 0007_boundary_provenance
Revises: 0006_phase5_breakouts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_boundary_provenance"
down_revision: str | None = "0006_phase5_breakouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "breakout_events",
        sa.Column(
            "boundary_kind",
            sa.String(length=32),
            nullable=False,
            server_default="other",
        ),
    )
    op.create_index(
        "ix_breakout_boundary_kind", "breakout_events", ["boundary_kind", "state"]
    )


def downgrade() -> None:
    op.drop_index("ix_breakout_boundary_kind", table_name="breakout_events")
    op.drop_column("breakout_events", "boundary_kind")
