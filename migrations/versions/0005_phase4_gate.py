"""Phase 4 completion gate: relationship provenance and the labelling vocabulary

Two changes, both driven by the completion gate.

**Relationship provenance.** A relationship edge is a claim about what was
visible at a moment. `established_at` records when the row was written, which is
not the same thing: a backfill written today about a session six months ago
would carry today's timestamp and be indistinguishable from an edge that was
genuinely derivable then. `as_of_session` records the knowledge boundary the
edge was derived under, which is what makes the relationship history
reproducible.

**The labelling vocabulary.** The Phase 2 draft carried a boolean `is_pattern`,
which cannot express the three cases that actually matter in a review workflow:
a reviewer who judged the structure ambiguous, a reviewer who declined to judge,
and an example nobody can judge. Those have different downstream consequences --
an ambiguous label is data, an abstention needs reassigning, an unjudgeable
example needs retiring -- so the boolean becomes a five-value `label`.

`revision` makes the table genuinely append-only. The old unique constraint
allowed one row per (example, reviewer), so a reviewer changing their mind had
to overwrite their previous opinion. A reviewer changing their mind is itself a
fact about how hard the example is, and inter-reviewer agreement computed after
overwrites is computed over a population that erased its own disagreements.

`detector_coverage` and `config_digest` pin what the detector was doing at
labelling time. Without them, human-versus-detector agreement is computed
against whatever the detector does when the query is run, which is a different
detector.

The boolean is dropped rather than carried. Nothing has labelled anything, so
there are no rows to preserve, and keeping a column that no code reads is how a
schema accumulates fossils.

Revision ID: 0005_phase4_gate
Revises: 0004_phase4_patterns
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase4_gate"
down_revision: str | None = "0004_phase4_patterns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pattern_relationships",
        sa.Column("as_of_session", sa.Date(), nullable=True),
    )

    op.add_column(
        "pattern_labels",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "pattern_labels",
        sa.Column("label", sa.String(length=24), nullable=False, server_default="positive"),
    )
    op.add_column("pattern_labels", sa.Column("detector_coverage", sa.Float(), nullable=True))
    op.add_column(
        "pattern_labels", sa.Column("config_digest", sa.String(length=64), nullable=True)
    )

    # The uniqueness rule changes shape: revision joins the key so a re-review
    # is a new row rather than an overwrite.
    with op.batch_alter_table("pattern_labels") as batch:
        batch.drop_constraint("uq_pattern_label", type_="unique")
        batch.create_unique_constraint(
            "uq_pattern_label",
            ["instrument_id", "as_of_session", "timeframe", "pattern_type", "reviewer", "revision"],
        )
        batch.drop_column("is_pattern")


def downgrade() -> None:
    with op.batch_alter_table("pattern_labels") as batch:
        batch.add_column(
            sa.Column("is_pattern", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.drop_constraint("uq_pattern_label", type_="unique")
        batch.create_unique_constraint(
            "uq_pattern_label",
            ["instrument_id", "as_of_session", "timeframe", "pattern_type", "reviewer"],
        )

    op.drop_column("pattern_labels", "config_digest")
    op.drop_column("pattern_labels", "detector_coverage")
    op.drop_column("pattern_labels", "label")
    op.drop_column("pattern_labels", "revision")
    op.drop_column("pattern_relationships", "as_of_session")
