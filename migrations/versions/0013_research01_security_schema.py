"""The research-01 identity and fact schema: twelve additive tables

Nothing existing is renamed, re-keyed or dropped. That is the whole shape of
this migration, and it is a decision rather than caution.

``ohlcv_bars``, ``corporate_actions`` and ``symbol_mappings`` are near-neighbours
of three tables created here, and two of them (``corporate_actions``,
``ohlcv_bars``) already carry full point-in-time semantics. They were still not
extended, because every one of them keys on ``instruments.instrument_id``, and a
single ``instruments`` row carries an issuer key (``cik``), a security key
(``figi``), a listing venue (``primary_exchange``) and a lifecycle
(``listing_status``/``delisted_date``) at once. Survivorship research asks which
of those four ended and when, so the conflation is not a detail to work around
-- it is the thing being taken apart. All three are also load-bearing for
``full-01``, which is frozen.

**Identity is regulator-neutral, and the schema cannot assume an SEC CIK.**
``issuers`` has no ``cik`` column. Keys live in ``issuer_identifiers`` under an
explicit namespace, because ``FRC`` is a bank with no holding company, files its
Exchange Act reports with the FDIC, has no SEC filer account at all, and is
discriminated by ``FDIC_CERT:59017``. A ``cik`` column would have made that
issuer unrepresentable or invited a fabricated number.

Three constraints carry the identity guarantees:

* ``uq_issuer_primary_identifier`` -- a partial unique index admitting exactly
  one ``primary`` row per issuer. Two co-equal primaries discriminate nothing.
* ``uq_issuer_identifier_global`` -- unique on ``(namespace,
  value_normalized)``. The same registry value on two issuers means one
  institution was recorded twice.
* ``issuer_related_identities`` is a **separate table**, not a third role on
  ``issuer_identifiers``. An identifier that has not been shown to describe this
  issuer must not be one WHERE-clause slip away from identifying it. FRC's SEC
  subject-company CIK ``1132979`` lives there: that record reports EIN
  ``88-0157485`` while the FDIC registrant reports ``80-0513856``, and sameness
  is established in neither direction.

Verbatim storage is enforced by having nowhere to put a normalised form:
``listings.venue`` keeps the source's own rendering, ``filings.form_type`` keeps
``10-K405`` distinct from ``10-K``, and ``issuer_identifiers.value`` keeps
leading zeros while ``value_normalized`` alone is compared.

**No bridge to ``instruments``.** There is deliberately no foreign key and no
view joining an instrument to a security. That is an identity claim needing
evidence, and the obvious shortcut -- joining on ``instruments.cik`` -- is wrong
twice: it assumes an SEC CIK exists, and it assumes a CIK identifies a security
rather than a registrant.

Revision ID: 0013_research01_security_schema
Revises: 0012_run_scoped_derivation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tradeit.storage.tables import UTCDateTime

revision: str = "0013_research01_security_schema"
down_revision: str | None = "0012_run_scoped_derivation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
PRICE = sa.Numeric(18, 6)
QTY = sa.Numeric(24, 6)
RATIO = sa.Numeric(18, 8)
VALUE = sa.Numeric(28, 6)
#: The ORM declares every timestamp with ``UTCDateTime`` -- and ``DateTime``
#: in ``tables.py`` is an alias *for* it (line 80), so ``TimestampMixin``
#: columns are ``UTCDateTime`` as well. ``sa.DateTime`` renders identical
#: DDL and is a different Python type, which is drift Alembic reports and
#: no SQL comparison can see. Matches ``0001_initial``.
TS = UTCDateTime(timezone=True)

#: Created newest-last so every foreign key has its target. Dropped in reverse.
_TABLES = (
    "security_fundamental_facts",
    "security_corporate_action_facts",
    "security_price_facts",
    "filings",
    "security_relationships",
    "symbol_aliases",
    "listings",
    "security_identifiers",
    "securities",
    "issuer_related_identities",
    "issuer_identifiers",
    "issuers",
)


def _audit() -> tuple[sa.Column[object], ...]:
    """The TimestampMixin columns, identical on every table it is applied to."""
    return (
        sa.Column("ingested_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("source", sa.String(64), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "issuers",
        sa.Column("issuer_id", PK, primary_key=True, autoincrement=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit(),
    )

    op.create_table(
        "issuer_identifiers",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "issuer_id",
            PK,
            sa.ForeignKey("issuers.issuer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("value_normalized", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False),
        *_audit(),
        sa.UniqueConstraint("namespace", "value_normalized", name="uq_issuer_identifier_global"),
        sa.UniqueConstraint(
            "issuer_id", "namespace", "value_normalized", name="uq_issuer_identifier"
        ),
        sa.CheckConstraint(
            "role IN ('primary', 'corroborating')", name="ck_issuer_identifier_role"
        ),
    )
    op.create_index(
        "ix_issuer_identifier_lookup", "issuer_identifiers", ["namespace", "value_normalized"]
    )
    # Exactly one primary per issuer. A partial index rather than a plain unique
    # one, because corroborating identifiers are many and must stay unconstrained.
    op.create_index(
        "uq_issuer_primary_identifier",
        "issuer_identifiers",
        ["issuer_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary'"),
        sqlite_where=sa.text("role = 'primary'"),
    )

    op.create_table(
        "issuer_related_identities",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "issuer_id",
            PK,
            sa.ForeignKey("issuers.issuer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("value_normalized", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit(),
        sa.UniqueConstraint(
            "issuer_id", "namespace", "value_normalized", name="uq_issuer_related_identity"
        ),
        sa.CheckConstraint("relation IN ('unresolved')", name="ck_issuer_relation"),
    )

    op.create_table(
        "securities",
        sa.Column("security_id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "issuer_id",
            PK,
            sa.ForeignKey("issuers.issuer_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("security_type", sa.String(32), nullable=False),
        sa.Column("class_label", sa.Text()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit(),
    )
    op.create_index("ix_security_issuer", "securities", ["issuer_id", "security_type"])

    op.create_table(
        "security_identifiers",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("citation", sa.Text()),
        *_audit(),
        sa.UniqueConstraint("namespace", "value", name="uq_security_identifier_global"),
    )
    op.create_index("ix_security_identifier_lookup", "security_identifiers", ["namespace", "value"])

    op.create_table(
        "listings",
        sa.Column("listing_id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("venue", sa.String(128), nullable=False),
        sa.Column("listed_from", sa.Date()),
        sa.Column("listed_to", sa.Date()),
        sa.Column("evidence_strength", sa.String(24)),
        sa.Column("citation", sa.Text()),
        *_audit(),
        sa.CheckConstraint(
            "listed_to IS NULL OR listed_from IS NULL OR listed_to >= listed_from",
            name="ck_listing_interval",
        ),
    )
    op.create_index("ix_listing_security", "listings", ["security_id", "listed_from"])
    op.create_index("ix_listing_venue", "listings", ["venue"])

    op.create_table(
        "symbol_aliases",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_kind", sa.String(24), nullable=False),
        sa.Column("alias_value", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("knowledge_time", TS, nullable=False),
        sa.Column("knowledge_source", sa.String(32), nullable=False),
        sa.Column("citation", sa.Text()),
        *_audit(),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_alias_interval"),
    )
    op.create_index(
        "ix_alias_lookup", "symbol_aliases", ["alias_kind", "alias_value", "valid_from", "valid_to"]
    )
    op.create_index("ix_alias_security", "symbol_aliases", ["security_id", "valid_from"])

    op.create_table(
        "security_relationships",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "from_security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("effective_date", sa.Date()),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit(),
        sa.UniqueConstraint(
            "from_security_id", "to_security_id", "relation_type", name="uq_security_relationship"
        ),
        sa.CheckConstraint("from_security_id <> to_security_id", name="ck_security_rel_distinct"),
    )
    op.create_index("ix_security_rel_from", "security_relationships", ["from_security_id"])
    op.create_index("ix_security_rel_to", "security_relationships", ["to_security_id"])

    op.create_table(
        "filings",
        sa.Column("filing_id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "issuer_id",
            PK,
            sa.ForeignKey("issuers.issuer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("security_id", PK, sa.ForeignKey("securities.security_id", ondelete="SET NULL")),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("form_type", sa.String(32), nullable=False),
        sa.Column("filed_at", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date()),
        sa.Column("source_path", sa.Text()),
        *_audit(),
        sa.UniqueConstraint("accession", name="uq_filing_accession"),
    )
    op.create_index("ix_filing_issuer", "filings", ["issuer_id", "filed_at"])
    op.create_index("ix_filing_form", "filings", ["form_type", "filed_at"])

    op.create_table(
        "security_price_facts",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("adjustment_basis", sa.String(16), nullable=False),
        sa.Column("event_time", TS, nullable=False),
        sa.Column("knowledge_time", TS, nullable=False),
        sa.Column("knowledge_source", sa.String(32), nullable=False),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("volume", QTY, nullable=False),
        sa.Column("volume_adjusted", sa.Boolean(), nullable=False),
        *_audit(),
        sa.UniqueConstraint(
            "security_id",
            "session_date",
            "adjustment_basis",
            "knowledge_time",
            name="uq_security_price_revision",
        ),
        sa.CheckConstraint("high >= low", name="ck_security_price_high_low"),
        sa.CheckConstraint("high >= open AND high >= close", name="ck_security_price_high"),
        sa.CheckConstraint("low <= open AND low <= close", name="ck_security_price_low"),
        sa.CheckConstraint("volume >= 0", name="ck_security_price_volume"),
        sa.CheckConstraint("knowledge_time >= event_time", name="ck_security_price_knowledge"),
        sa.CheckConstraint(
            "adjustment_basis IN ('raw', 'split', 'total')", name="ck_security_price_basis"
        ),
    )
    op.create_index(
        "ix_security_price_pit",
        "security_price_facts",
        ["security_id", "adjustment_basis", "session_date", "knowledge_time"],
    )

    op.create_table(
        "security_corporate_action_facts",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("event_time", TS, nullable=False),
        sa.Column("knowledge_time", TS, nullable=False),
        sa.Column("knowledge_source", sa.String(32), nullable=False),
        sa.Column("ratio", RATIO),
        sa.Column("cash_amount", PRICE),
        sa.Column("currency", sa.String(3), nullable=False),
        *_audit(),
        sa.UniqueConstraint(
            "security_id",
            "action_type",
            "ex_date",
            "knowledge_time",
            name="uq_security_action_revision",
        ),
        sa.CheckConstraint("ratio IS NULL OR ratio > 0", name="ck_security_action_ratio"),
        sa.CheckConstraint(
            "cash_amount IS NULL OR cash_amount >= 0", name="ck_security_action_cash"
        ),
        sa.CheckConstraint("knowledge_time >= event_time", name="ck_security_action_knowledge"),
    )
    op.create_index(
        "ix_security_action_pit",
        "security_corporate_action_facts",
        ["security_id", "ex_date", "knowledge_time"],
    )

    op.create_table(
        "security_fundamental_facts",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            PK,
            sa.ForeignKey("securities.security_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filing_id", PK, sa.ForeignKey("filings.filing_id", ondelete="SET NULL")),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(4), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("event_time", TS, nullable=False),
        sa.Column("knowledge_time", TS, nullable=False),
        sa.Column("knowledge_source", sa.String(32), nullable=False),
        sa.Column("value", VALUE),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        *_audit(),
        sa.UniqueConstraint(
            "security_id",
            "metric",
            "fiscal_year",
            "fiscal_period",
            "basis",
            "knowledge_time",
            name="uq_security_fundamental_revision",
        ),
        sa.CheckConstraint(
            "knowledge_time >= event_time", name="ck_security_fundamental_knowledge"
        ),
        sa.CheckConstraint(
            "basis IN ('as_reported', 'restated')", name="ck_security_fundamental_basis"
        ),
    )
    op.create_index(
        "ix_security_fundamental_pit",
        "security_fundamental_facts",
        ["security_id", "metric", "knowledge_time", "period_end"],
    )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_table(table)
