"""Record what a second vendor's printed close says about a contradicted split

`RESEARCH_01_DATA_DICTIONARY.md` §0.1a: some EODHD `raw` closes are already
split-adjusted, so applying the recorded split adjusts twice.
`tradeit.research01.series.split_evidence` decides that from the stored prints
alone, and at 1,872 splits the prints contradict each other or the ratio — the
`raw` basis flat while `total` moves by the ratio, a ratio recorded inverted, a
split recorded twice. The reads withhold 1,634,744 prints before those rather
than guess.

A second vendor's **printed** close settles many of them: if the stored `raw`
equals Sharadar's `closeunadj` the split is genuine, and if it equals Sharadar's
split-adjusted `close` the stored `raw` is already adjusted. Measured on a
120-split sample before this table existed: 64% decided, 10% undecidable because
Sharadar itself showed no adjustment on that session yet.

**Why a table rather than a read-time lookup.** `adjudicated_bound` set the
pattern deliberately — a read returns "a recorded, cited fact rather than
recomputing a heuristic at query time". A verdict here therefore carries what
decided it: the vendor, the session compared, and the three closes, so anyone
can check the call without re-fetching anything.

**Append-only, one verdict per (security, ex_date, source).** A second source may
disagree with the first; both rows stay and the read takes the latest
`knowledge_time`, exactly as every other revisioned fact here does.

Revision ID: 0019_split_price_verdicts
Revises: 0018_issuer_sic_observations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import UTCDateTime

revision: str = "0019_split_price_verdicts"
down_revision: str | None = "0018_issuer_sic_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Defined locally, as 0013 and 0018 do: a migration should not shift because a
#: constant moved in application code long after it ran.
PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
#: ``Numeric(18, 6)``, matching ``tables.PRICE`` and every price column 0002
#: created. The first draft of this migration wrote ``Numeric(20, 6)`` and the
#: ORM drift guard caught it the moment the table was added to
#: ``RESEARCH01_TABLES`` -- which it had not been, so the guard was blind to
#: this table until then.
#:
#: **Edited in place rather than corrected by a follow-up migration**, and the
#: reason is specific to what these columns are: SQLite does not enforce numeric
#: precision, and ``UTCDateTime``'s timezone flag changes binding rather than
#: storage, so neither difference is physically present in a database this
#: migration has already built. A follow-up ``alter_column`` would rebuild a
#: 73 GB table to change nothing on disk. The alternative was rejected on that
#: basis; if this schema is ever run on a backend that *does* enforce precision,
#: the follow-up becomes the right answer and this note is the record of why it
#: was not needed here.
PRICE = sa.Numeric(18, 6)

_TABLE = "security_split_price_verdicts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ex_date", sa.Date(), nullable=False),
        #: 'in_raw' -- the stored raw close is the print, so apply the split.
        #: 'already_adjusted' -- the stored raw close is the vendor's adjusted
        #: one, so do not apply it again.
        sa.Column("verdict", sa.String(32), nullable=False),
        #: Who decided, e.g. 'sharadar'. Not a free-text note: the read takes the
        #: latest knowledge_time across sources and an audit needs to know which.
        sa.Column("decided_by", sa.String(32), nullable=False),
        sa.Column("compared_session", sa.Date(), nullable=False),
        #: The three closes the call rests on, stored so it can be checked.
        sa.Column("stored_raw_close", PRICE, nullable=False),
        sa.Column("vendor_printed_close", PRICE, nullable=False),
        sa.Column("vendor_adjusted_close", PRICE, nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "ingested_at",
            UTCDateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "verdict IN ('in_raw', 'already_adjusted')", name="ck_split_verdict_value"
        ),
        sa.CheckConstraint(
            "stored_raw_close > 0 AND vendor_printed_close > 0 AND vendor_adjusted_close > 0",
            name="ck_split_verdict_prices",
        ),
        sa.UniqueConstraint(
            "security_id",
            "ex_date",
            "decided_by",
            "knowledge_time",
            name="uq_split_verdict_revision",
        ),
    )
    op.create_index(
        "ix_split_verdict_lookup", _TABLE, ["security_id", "ex_date", "knowledge_time"]
    )


def downgrade() -> None:
    op.drop_index("ix_split_verdict_lookup", table_name=_TABLE)
    op.drop_table(_TABLE)
