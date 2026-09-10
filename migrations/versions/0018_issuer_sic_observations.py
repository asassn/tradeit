"""Record SIC as observed, not as a label

A company's SIC is not a property it has; it is a property the SEC asserted on
a particular filing, and it changes. Storing one row per issuer would answer
"what sector is this?" and quietly get "what sector was this in 2008?" wrong --
which is the only version of the question a backtest asks.

So the table holds **observations**: one row per (issuer, filing), each
carrying the code, the SEC's own wording where the source gave one, the
official division, the date of the filing that stated it, and the accession
number as the citation. "SIC as of date D" is then the latest observation at or
before D, which is a point-in-time read and improves as more observations are
added rather than being overwritten by them.

``sectors`` already exists and is not used for this. It is keyed on
``instrument_id`` and SIC is assigned to a **filer** -- a CIK, an issuer -- so a
security inherits its issuer's classification rather than carrying its own.
Reusing that table would have meant writing the same fact once per share class
and hoping they never disagreed.

**The revision id is short on purpose** -- ``alembic_version.version_num`` is a
``varchar(32)`` that PostgreSQL enforces and SQLite does not.

Revision ID: 0018_issuer_sic_observations
Revises: 0017_security_identity_evidence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import UTCDateTime

revision: str = "0018_issuer_sic_observations"
down_revision: str | None = "0017_security_identity_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Defined locally, as 0013 does: a migration should not shift because a
#: constant moved in application code long after it ran.
PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")

_TABLE = "issuer_sic_observations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "issuer_id",
            PK,
            sa.ForeignKey("issuers.issuer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sic_code", sa.Integer(), nullable=False),
        sa.Column("sic_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("division", sa.String(96), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("knowledge_time", UTCDateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.UniqueConstraint("issuer_id", "accession", name="uq_issuer_sic_observation"),
        sa.CheckConstraint("sic_code between 100 and 9999", name="ck_issuer_sic_range"),
    )
    op.create_index("ix_issuer_sic_pit", _TABLE, ["issuer_id", "observed_on", "knowledge_time"])


def downgrade() -> None:
    op.drop_index("ix_issuer_sic_pit", table_name=_TABLE)
    op.drop_table(_TABLE)
