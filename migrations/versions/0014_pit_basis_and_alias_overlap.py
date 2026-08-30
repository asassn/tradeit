"""Record how knowledge_time was derived, and forbid two aliases at one instant

Two changes, both closing gaps that milestone 2 left open.

**1. ``security_price_facts.knowledge_time_basis``.**

The point-in-time policy for a historical backfill has three answers depending
on what the source establishes, and the resulting timestamp does not say which
one was used:

* ``source_disseminated`` -- the source stated when the fact became public.
* ``session_close`` -- derived by rule from the session's close instant. Valid
  for a **raw** bar, because an EOD print was disseminated at the close.
* ``computed_at_delivery`` -- a vendor-delivered **adjusted** bar. Its value
  embeds every corporate action up to the moment the vendor computed it, so it
  did not exist, at that value, before the delivery.
* ``delivery_unestablished`` -- nothing established a historical instant, so the
  delivery time is used and the row is invisible to any earlier as-of. Fail
  closed rather than guess a date that would license a decision.

**The column is not derivable from ``adjustment_basis``**, which is the
simplification to resist. A *raw* bar whose ``session_date`` is not a trading
day also falls to ``delivery_unestablished``, and is then indistinguishable from
an ordinary raw bar by ``adjustment_basis`` alone. Only this column separates a
timestamp that was measured from one that was fallen back to.

**2. ``ex_alias_no_overlap`` on ``symbol_aliases``.**

``symbol_mappings`` has carried ``ex_symbol_no_overlap`` since 0001, and
``symbol_aliases`` was created in 0013 without an equivalent -- a gap, since two
rows claiming one ticker for two securities on one date is precisely the splice
this corpus exists to prevent.

It is **not** the same constraint. ``symbol_mappings`` holds current truth, so a
plain overlap ban is right there. ``symbol_aliases`` carries ``knowledge_time``,
so revisions must be allowed to overlap: a corrected mapping is a new row over
the same interval at a later instant. The constraint therefore includes
``knowledge_time WITH =`` and forbids only two *simultaneous* claims.
``alias_kind`` is in the key as well, because a ticker and a vendor's internal
symbol may legitimately be the same string for different securities.

**PostgreSQL only**, like the constraint it mirrors: ``EXCLUDE USING gist`` does
not exist on SQLite, where the unit suite runs. The importer's
``UNRESOLVED_AMBIGUOUS`` branch is therefore load-bearing rather than decorative
-- it is the only enforcement anywhere the constraint is absent.

Revision ID: 0014_pit_basis_and_alias_overlap
Revises: 0013_research01_security_schema
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_pit_basis_and_alias_overlap"
down_revision: str | None = "0013_research01_security_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXCLUSION = (
    "symbol_aliases",
    "ex_alias_no_overlap",
    "EXCLUDE USING gist ("
    "alias_kind WITH =, alias_value WITH =, knowledge_time WITH =, "
    "daterange(valid_from, valid_to, '[)') WITH &&)",
)


def upgrade() -> None:
    # Added WITH a server default and then stripped of it: the default exists
    # only to make the column NOT NULL for rows that already exist, and leaving
    # it in place would be drift against an ORM that declares none.
    op.add_column(
        "security_price_facts",
        sa.Column(
            "knowledge_time_basis",
            sa.String(24),
            nullable=False,
            server_default="session_close",
        ),
    )
    op.alter_column("security_price_facts", "knowledge_time_basis", server_default=None)
    op.create_check_constraint(
        "ck_security_price_kt_basis",
        "security_price_facts",
        "knowledge_time_basis IN ('source_disseminated', 'session_close', "
        "'computed_at_delivery', 'delivery_unestablished')",
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    table, name, clause = _EXCLUSION
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {clause}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        table, name, _ = _EXCLUSION
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.drop_constraint("ck_security_price_kt_basis", "security_price_facts", type_="check")
    op.drop_column("security_price_facts", "knowledge_time_basis")
