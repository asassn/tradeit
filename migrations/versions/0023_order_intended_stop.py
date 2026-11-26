"""An order must carry the stop it was decided with, so it survives a restart

Found 2026-11-26 (`docs/PLAN_FROM_BOOK_2026-11-26.md`): a paper session run one
process per session placed 379 orders over 144 sessions and filled none. An
order decided at a close fills at the next session's bars; the record held
positions and no orders, so every order in flight when a process ended was
dropped, and the next process decided the same exit again and dropped it again.

The paper session now writes its orders to `orders` and restores the ones still
resting. One fact about an entry order is ours and not the broker's, and
`orders` had nowhere to put it: **the protective stop the entry was decided
with**. `LiveShapedSession._pending_stops` holds it in memory and pops it when
the entry fills, because the initial stop defines R. Resumed without it, the
fill falls back to a fixed fraction of the fill price — a stop decided for no
trade.

It is not `orders.stop_price`. That column is the trigger of a STOP or
STOP_LIMIT order, which the venue acts on; this one is never sent to a venue
and is placed only after the fill. Overloading one column with both would make
a market entry read as a stop order.

## Nullable, with no backfill

Null for exits, which have no protective stop, and for every row written
before this migration, for which nobody recorded one. Not a trading-logic
change: nothing about how an order is decided or filled moves.

Revision ID: 0023_order_intended_stop
Revises: 0022_position_ladder_state
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_order_intended_stop"
down_revision: str | None = "0022_position_ladder_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Numeric(18, 6) matches `PRICE`, as every other price column on `orders`.
    op.add_column("orders", sa.Column("intended_stop_price", sa.Numeric(18, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "intended_stop_price")
