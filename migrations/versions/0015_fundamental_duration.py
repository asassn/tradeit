"""Record how long a fundamental value covers, and key on it

A number in the SEC Financial Statement Data Sets is identified by
``(adsh, tag, version, ddate, qtrs, uom, segments, coreg)``. Two of those
carry meaning our table could not express.

**``qtrs`` — the duration.** ``0`` is an instant (a balance-sheet item at a
date), ``1`` is a quarter, ``4`` is a year. **The same tag at the same period
end appears with different durations**: ``Revenues`` for the quarter ending
2014-12-31 and ``Revenues`` for the year ending 2014-12-31 are different facts
with the same name and date. Without this column they collide on
``uq_security_fundamental_revision``, and the loser is silently dropped -- a
quarterly figure overwriting an annual one, or the reverse, with nothing to
show for it.

So ``duration_qtrs`` is added **and joined to the uniqueness key**. It is
nullable because a source that does not state a duration must not have one
invented for it; ``NULLS NOT DISTINCT`` keeps two such rows colliding as they
did before rather than quietly multiplying.

**``segments`` / ``coreg`` — the dimensional breakdowns.** Not added, because
the importer does not load them: only consolidated rows (both empty) are
imported, and a dimensional row is skipped and counted rather than flattened
into the consolidated figure it would corrupt. If they are ever wanted they
need their own columns and their own key, not a silent merge into these.

Revision ID: 0015_fundamental_duration
Revises: 0014_pit_basis_and_alias_overlap
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_fundamental_duration"
down_revision: str | None = "0014_pit_basis_and_alias_overlap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("security_id", "metric", "fiscal_year", "fiscal_period", "basis", "knowledge_time")
_NEW = (
    "security_id",
    "metric",
    "fiscal_year",
    "fiscal_period",
    "duration_qtrs",
    "basis",
    "knowledge_time",
)


def upgrade() -> None:
    op.add_column(
        "security_fundamental_facts", sa.Column("duration_qtrs", sa.Integer(), nullable=True)
    )
    op.drop_constraint(
        "uq_security_fundamental_revision", "security_fundamental_facts", type_="unique"
    )
    kwargs = {}
    if op.get_bind().dialect.name == "postgresql":
        # Without this PostgreSQL treats every NULL as distinct, so rows with no
        # stated duration would stop conflicting -- a silent weakening of the
        # constraint at exactly the point the data is least trustworthy.
        kwargs["postgresql_nulls_not_distinct"] = True
    op.create_unique_constraint(
        "uq_security_fundamental_revision", "security_fundamental_facts", list(_NEW), **kwargs
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_security_fundamental_revision", "security_fundamental_facts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_security_fundamental_revision", "security_fundamental_facts", list(_OLD)
    )
    op.drop_column("security_fundamental_facts", "duration_qtrs")
