"""A recorded score must say which horizon it belongs to

Phase 8 built `portfolio/mandate.py` so that Day, Swing and Retirement are
**three separate statistical populations** and no learning, backtesting or
scoring population may mix them. `check_population` makes that executable, and
the audit of 2026-10-17 exercised it against the pattern atlas.

**It could not be applied to a persisted score, because the table does not say
which population the score is from.** `opportunity_scores` carries
`instrument_id`, `session_date`, `direction`, `total` and its components, and
nothing about the horizon. So the three-horizon separation existed in memory,
in the values passed to `check_population`, and nowhere in the record.

Found on 2026-10-20 while building Phase 10's per-ticker panel, which the
roadmap specifies as *"one security, three independent conclusions — Day, Swing
and Retirement — each with its own supporting and contradicting factors"* and
which *"depends on the multi-timeframe gate, which is what makes the three
horizons separate populations rather than three labels."* A panel built over
this table could only ever have shown three labels.

## Nullable, and why that is not laziness

**Nullable with no default and no backfill.** 1,956 scores already exist in the
decision record from the 2003-2019 run, and every one of them *is* Swing/1d —
the scan ran `Bartimeframe.D1` throughout. Writing that in as a default would be
correct for those rows and wrong as a rule: the next caller to forget the
argument would silently get Swing, and a score mislabelled as a horizon it did
not come from is worse than one labelled `NULL`. A NULL says *nobody recorded
it*, which is a fact a reader can act on.

So the columns are nullable, the writer requires them, and a NULL in the record
marks a row written before this migration rather than a row whose horizon was
guessed.

## Strings rather than enums

`String(16)`, matching how `direction` is already stored here and how
`entry_type` is stored on `journal_entries`. The values come from
`Mandate` and `Bartimeframe`, and the writer is the thing that constrains them;
a database enum would need its own migration every time the mandate table in
`MULTI_TIMEFRAME_MANDATES.md` §4 grew a row.

Revision ID: 0020_score_horizon
Revises: 0019_split_price_verdicts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_score_horizon"
down_revision: str | None = "0019_split_price_verdicts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunity_scores", sa.Column("mandate", sa.String(16), nullable=True))
    op.add_column("opportunity_scores", sa.Column("timeframe", sa.String(16), nullable=True))
    # Indexed together with the instrument, because the per-ticker panel's
    # query is "this security, this horizon, most recent first" and that is the
    # only access pattern these columns exist to serve.
    op.create_index(
        "ix_score_horizon",
        "opportunity_scores",
        ["instrument_id", "mandate", "session_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_score_horizon", table_name="opportunity_scores")
    op.drop_column("opportunity_scores", "timeframe")
    op.drop_column("opportunity_scores", "mandate")
