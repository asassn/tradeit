"""Widen the pattern and breakout identity keys to hold a re-mint suffix

A pattern identity is ``content_hash(...)[:24]`` — 24 hex characters — and the
columns holding it were ``VARCHAR(32)``, sized for exactly that and nothing
else.

The tracker now appends ``":YYYY-MM-DD"`` in two cases, both added because
leaving them out corrupted the record:

* a structure re-detected after its identity has *terminated* is a second life,
  not a resurrection. Reusing the key merged them — over a four-year walk one
  identity was re-minted 151 times and persisted into a single ``patterns`` row
  whose history interleaves separate lives.
* a detection that would move an identity along an edge the lifecycle does not
  have is a different structure, and ``_fork`` mints it a new key.

24 + 1 + 10 = **35**, which does not fit in 32. The first real scan hit it on
AAPL at roughly session 4,000 of 4,174 with
``671a05b24082ef63e6f16232:2026-07-23`` and PostgreSQL refused the insert.

Three columns store one of these keys. Two hold a pattern identity *verbatim*
and are the ones that overflow:

* ``patterns.identity_key``
* ``breakout_events.pattern_key`` — the same string, copied. It had not failed
  yet only because ``_persist`` writes patterns before breakouts, so the scan
  aborted one table earlier.

The third, ``breakout_events.event_key``, is ``content_hash(...)[:24]`` of a
payload *containing* the pattern key, so its own length is invariant at 24 and
it never overflows. It is widened anyway: these three are one family of
identifiers, and leaving one narrower invites the next reader to assume the
difference is meaningful.

Nothing else stores a pattern key. ``pattern_relationships``,
``pattern_labels``, ``breakout_labels`` and ``opportunity_scores`` all
reference by integer foreign key.

**Widening a ``varchar(n)`` is metadata-only on PostgreSQL 9.2 and later**: no
table rewrite, no reindex, and the unique constraints
(``uq_pattern_identity``, ``uq_breakout_event_identity``) and the index on
``pattern_key`` are unaffected. SQLite does not enforce ``VARCHAR`` lengths at
all, so there is nothing to do there and the migration says so rather than
running a batch table copy for no effect.

The downgrade is deliberately lossy-safe: it refuses rather than truncating.
24 characters of hash plus a colon leaves seven of the ten-character date
inside 32, so a cut key keeps ``:YYYY-MM`` and loses the day — and two
structures re-minted in the same month would land on the same string, silently
merging two separate lives of one structure.

Revision ID: 0010_identity_key_width
Revises: 0009_scan_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_identity_key_width"
down_revision: str | None = "0009_scan_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, column) pairs holding a pattern or breakout identity key.
IDENTITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("patterns", "identity_key"),
    ("breakout_events", "event_key"),
    ("breakout_events", "pattern_key"),
)

OLD_WIDTH = 32
NEW_WIDTH = 64


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        # SQLite stores VARCHAR(n) as TEXT and ignores n. Emulating the ALTER
        # would copy both tables to change a constraint the engine does not
        # apply.
        return
    for table, column in IDENTITY_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=OLD_WIDTH),
            type_=sa.String(length=NEW_WIDTH),
            existing_nullable=False,
        )


def downgrade() -> None:
    if _is_sqlite():
        return
    bind = op.get_bind()
    for table, column in IDENTITY_COLUMNS:
        too_long = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE length({column}) > :n"),
            {"n": OLD_WIDTH},
        ).scalar_one()
        if too_long:
            raise RuntimeError(
                f"{too_long} row(s) in {table}.{column} are longer than {OLD_WIDTH} "
                "characters. Narrowing would cut the day off a re-minted identity, so "
                "two structures re-minted in the same month would collapse onto one "
                "key and two separate lives of one structure would merge. Delete the "
                "affected scan's patterns first if you genuinely want this."
            )
    for table, column in IDENTITY_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=NEW_WIDTH),
            type_=sa.String(length=OLD_WIDTH),
            existing_nullable=False,
        )
