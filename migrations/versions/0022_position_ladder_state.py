"""A position must carry the stop-ladder state the broker cannot report

`execution/live_shape.py` has said since Phase 11 exactly why this is needed,
in `Lot`'s own docstring:

> A broker reports quantity and average price. It does not report the high
> since entry, how many sessions we have held, or whether a partial profit was
> taken — those are ours, and **losing them would reset the stop ladder on
> every restart.**

The `positions` table had nowhere to put any of the three, so a restart did
reset the ladder — and because nothing wrote `positions` at all, the only
reason that never caused a wrong trade is that a paper session could not
restart in the first place. `LiveShapedSession.__post_init__` sets cash to
`initial_capital` and lots to empty, and until 2026-11-23 there was no loader.

Found while wiring the portfolio writers added the same week: the writers make
a portfolio persistable and are **not sufficient for a paper session to
resume**, because restoring from `positions` alone would hand the stop ladder a
fresh high-water mark, a session count of zero and no record of a partial sale.
A trailing stop resumed from a reset high is looser than the one that was in
force, and a time stop resumed from zero never fires.

## Nullable, with no backfill

**`highest_since_entry` is the field that cannot be recovered.** A session count
follows from `opened_on` and a trading calendar; a partial sale is visible in
`executions`. A running high needs every bar of the holding period replayed, and
there is no row in any database to replay it for — `positions` is empty in all
three decision records.

So all three are nullable and nothing is backfilled. A NULL says *nobody
recorded this*, which a reader can act on: the caller resuming a session must
decide what to do about a position whose ladder state is unknown, and the
honest options are to re-derive it from prices or to refuse to resume. Writing
a default would be a third option that looks like the first and is neither.

## Not a trading-logic change

No threshold moves, no rule changes, and nothing reads these columns yet. The
ladder behaves exactly as it did; this gives it somewhere to be written down.

Revision ID: 0022_position_ladder_state
Revises: 0021_forward_observations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_position_ladder_state"
down_revision: str | None = "0021_forward_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Numeric(18, 6) matches `PRICE`, the alias every other price column on
    # this table uses, rather than a fresh precision that would read as a
    # different kind of number.
    op.add_column(
        "positions",
        sa.Column("highest_since_entry", sa.Numeric(18, 6), nullable=True),
    )
    op.add_column("positions", sa.Column("sessions_held", sa.Integer(), nullable=True))
    op.add_column(
        "positions",
        sa.Column("partial_profit_taken", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("positions", "partial_profit_taken")
    op.drop_column("positions", "sessions_held")
    op.drop_column("positions", "highest_since_entry")
