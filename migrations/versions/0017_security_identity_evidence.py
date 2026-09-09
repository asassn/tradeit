"""Give identity prose a column of its own

``securities.class_label`` is documented as the filing's own words for a share
class -- ``"Common Stock, $.01 par value"`` -- kept verbatim because par values
are what separate two issuers that share a name.

For 34 rows it holds something else entirely: paragraphs of reasoning about why
an identity was or was not established, up to 4,156 characters. The data
dictionary lists this as trap 0.5, *"``class_label`` is not always a class
label"*, with the instruction to filter on ``source`` before believing it.

A trap that can be removed should be removed rather than documented. The prose
is real evidence and is not being discarded; it moves to a column that means
what it holds. ``note`` was not that column -- it already carries a short
routing tag (``AAPL/primary``) on every one of those rows, and appending
paragraphs to it would trade one overloaded column for another.

After the move ``class_label`` is NULL for those rows, which is the honest
value: a share-class title was never established for a control security seeded
from identity evidence. NULL says "not known"; the prose said "here is an essay
where a title belongs".

**The revision id is short on purpose** -- ``alembic_version.version_num`` is a
``varchar(32)`` that PostgreSQL enforces and SQLite does not.

Revision ID: 0017_security_identity_evidence
Revises: 0016_fundamental_cross_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_security_identity_evidence"
down_revision: str | None = "0016_fundamental_cross_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "securities"
_COLUMN = "identity_evidence"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
