"""Separate what was knowable at detection from what was measured later

Three columns, all nullable, all answering questions the schema could not
previously express. Each one existed implicitly and was being read off a field
that meant something else.

**patterns.structure_known_through** — the structure's end date *as measured on
the session the pattern was first detected*, written once and never updated.

``patterns.structural_end_date`` moves: a consolidation that keeps consolidating
is a longer consolidation, so every re-detection extends it, legitimately, from
bars that had printed by then. ``phase4.causality`` compared
``first_detected_session`` against it and reported 5,849 of 156,433 patterns as
detected before their own evidence. Re-running each detector over exactly the
bar prefix available on the failing session reproduced the geometry with an end
date on the detection session in every reconstructed case — the detectors were
causal and the comparison was against a field that had moved on. With both dates
present the check compares like with like.

**pattern_observations.structure_end_observed** — the same measurement, per
session, so the whole series is on file rather than only its latest value. The
pattern row can then hold current state without the history having to be
inferred from it, which is the property the observation log exists to provide
and was silently missing for this one field.

**scan_runs.requested_instrument_ids** — which instruments a scan set out to
cover, not merely how many. ``diag-01`` scanned seven instruments inside a
seventy-eight instrument snapshot and ``phase4.concentration`` reported
seventy-one as having produced no detections. All seventy-one were simply never
scanned. A count cannot tell those apart; a list can.

**No backfill is possible for the first two.** Only the current geometry was
ever stored, so what a pattern was measured as on a past session is not
recoverable from the database — it is not that the value is expensive to
compute, it is that it was never written down. Existing rows keep NULL and
``phase4.causality`` reports them as *unassessable* rather than passing them,
which is the same rule the gate applies everywhere else: a check that could not
run is never a check that passed.

Revision ID: 0011_scan_provenance
Revises: 0010_identity_key_width
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_scan_provenance"
down_revision: str | None = "0010_identity_key_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    """JSONB on PostgreSQL, JSON elsewhere — mirrors ``tables.JSONB_OR_JSON``."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("patterns", sa.Column("structure_known_through", sa.Date(), nullable=True))
    op.add_column(
        "pattern_observations",
        sa.Column("structure_end_observed", sa.Date(), nullable=True),
    )
    op.add_column(
        "scan_runs",
        sa.Column("requested_instrument_ids", _json_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_runs", "requested_instrument_ids")
    op.drop_column("pattern_observations", "structure_end_observed")
    op.drop_column("patterns", "structure_known_through")
