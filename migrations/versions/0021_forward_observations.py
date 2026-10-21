"""The forward record gets a migration, because the drift guard was right

`forward_observations` was added to the ORM on 2026-10-20 with a docstring
saying **"No migration creates this"** — on the reasoning that the forward
corpus is a separate SQLite database built from `Base.metadata.create_all`,
and that the numbered migrations are research-01's chain and do not run
against it.

Both halves of that are true and the conclusion still did not follow. The
numbered chain builds the **platform** schema, and the invariant the drift
guard asserts is that the platform schema equals the ORM — one shared
`MetaData` describing every table the application knows about. A table present
in that metadata and absent from the chain is drift whatever other database
also happens to build it. The sentence was a correct description of how the
forward database gets its schema, mistaken for a licence to leave the platform
chain behind.

## Why a migration rather than an exemption

The alternative was to teach the guard about deliberately-ORM-only tables with
a named allowlist. Rejected. CLAUDE.md records that tests have gone vacuous
here twice, and an allowlist on a drift guard is the mechanism for a third: it
converts a question with one right answer into a list somebody appends to, and
the appending looks like maintenance. The guard is worth more absolute than
accommodating.

Creating the table in the platform schema also costs nothing and buys
something. It is empty there — the capture agent writes to
`TradeItData/out/forward.sqlite` — and the ORM's own docstring says the point
of sharing the schema is that *"the two can be compared later without a
translation layer"*. A migration is what makes that literal rather than
aspirational.

## How this went unnoticed

The two tests that caught it are integration tests requiring Postgres. They
are skipped on a laptop with no `TRADEIT_TEST_PG_DSN`, and CI's Type check
step had been failing before Test ever ran — so **the guard had been dormant,
not passing.** 4,578 tests run in CI against 4,536 locally; the 42-test
difference is where this sat. Discovered 2026-10-21 by the first CI run on
this branch that reached the Test step at all.

Matches the ORM exactly: the unique constraint on
`(symbol, session_date, asked_at)` makes asking twice about one session two
observations rather than a duplicate, and `ix_forward_silent` indexes
`rows_returned` because a silent row is the one survivorship turns on and
counting them is the capture's main query.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import UTCDateTime

revision: str = "0021_forward_observations"
down_revision: str | None = "0020_score_horizon"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forward_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        # `UTCDateTime(timezone=True)`, matching 0001_initial's spelling for
        # `event_time`. `UTCDateTime()` alone creates a bare TIMESTAMP and the
        # drift guard caught it: tables.py aliases `DateTime = UTCDateTime`, so
        # the ORM's `DateTime(timezone=True)` is the timezone-aware variant.
        sa.Column("asked_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        # `server_default` where the ORM has a Python-side `default=0`.
        # Alembic's drift check does not compare server defaults unless asked
        # to, so this is not drift -- and a NOT NULL column with no default is
        # a footgun on a table two different paths write to.
        sa.Column("rows_returned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("symbol", "session_date", "asked_at", name="uq_forward_observation"),
    )
    op.create_index("ix_forward_symbol", "forward_observations", ["symbol", "session_date"])
    op.create_index("ix_forward_silent", "forward_observations", ["session_date", "rows_returned"])


def downgrade() -> None:
    op.drop_index("ix_forward_silent", table_name="forward_observations")
    op.drop_index("ix_forward_symbol", table_name="forward_observations")
    op.drop_table("forward_observations")
