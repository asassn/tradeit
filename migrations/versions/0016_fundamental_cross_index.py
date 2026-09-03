"""Index the cross-sectional fundamental read

``ix_security_fundamental_pit`` leads with ``security_id``, which serves "this
company's history of this metric" -- the read a ticker page makes. **A screen
makes the opposite read**: this metric, for *every* company, for periods in a
range, as knowable on a date. It filters on no security at all, so an index
leading with one cannot be used and the planner falls back to a scan.

Measured on the corpus at 92,022,159 rows: one cross-section took **3 minutes
30 seconds**, with ``EXPLAIN QUERY PLAN`` reporting ``SCAN``. That is not a slow
query, it is a corpus that cannot answer the question it was built for.

``(metric, period_end, knowledge_time)`` in that order: equality on the metric
first, then the period range, then the point-in-time bound. Adding
``security_id`` would not help -- it is the column the read does not have.

**The revision id is short on purpose.** Alembic stores it in
``alembic_version.version_num``, a ``varchar(32)``. SQLite does not enforce a
varchar length and PostgreSQL does, so ``0016_fundamental_cross_section_index``
-- thirty-six characters -- passed the whole local suite and failed every
PostgreSQL integration test in CI. ``test_migration_revision_ids_fit_the_column``
now asserts the bound.

Revision ID: 0016_fundamental_cross_index
Revises: 0015_fundamental_duration
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_fundamental_cross_index"
down_revision: str | None = "0015_fundamental_duration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ix_security_fundamental_cross_section"
_TABLE = "security_fundamental_facts"
_COLUMNS = ["metric", "period_end", "knowledge_time"]


def upgrade() -> None:
    op.create_index(_NAME, _TABLE, _COLUMNS)


def downgrade() -> None:
    op.drop_index(_NAME, table_name=_TABLE)
