"""Empirical gate: offline data package provenance and import corrections

Three tables and four columns, all in service of one question a validation
result must be able to answer: *which bytes produced this, and what did the
importer do to them on the way in?*

`data_packages` records one import of one package — the manifest digest, the
declared adjustment policy, the declared coverage next to the coverage actually
observed, and the whole import report. `data_package_files` records each file
with the SHA-256 that was verified before a line was read, and with the column
mapping, because the mapping *is* the interpretation: the same bytes read with
`close` pointed at an adjusted-close column produce a history that never
happened.

`import_corrections` records every deviation from the source text — field, raw
value, substituted value, rule, reason — so that "the importer changed my data"
is a query rather than an argument. It is high-volume by construction and that
is the accepted cost of a pipeline whose changes are visible.

The four new columns on `quarantined_rows` (`package_id`, `stage`,
`source_file`, `line_number`) turn "a row was rejected" into "this row, on this
line, at this stage". The stage is the useful part: a failure at `normalized`
means a date format needs declaring, at `validated` means the vendor's own
numbers contradict each other, at `point_in_time` means nothing in the package
says when the fact became knowable. Different fixes, different people.

`stage` defaults to `validated` because that is where every quarantine written
before this migration came from. There are no such rows yet, but a default that
describes the past correctly is cheaper than one that has to be explained.

Revision ID: 0008_data_packages
Revises: 0007_boundary_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008_data_packages"
down_revision: str | None = "0007_boundary_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JSONB_OR_JSON = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "data_packages",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("export_date", sa.Date(), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("observed_start", sa.Date()),
        sa.Column("observed_end", sa.Date()),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("adjustment_policy", sa.String(length=32), nullable=False),
        sa.Column("source_path", sa.Text()),
        sa.Column("licence_note", sa.Text()),
        sa.Column("vendor_dataset", sa.String(length=128)),
        sa.Column("known_limitations", JSONB_OR_JSON),
        sa.Column("report", JSONB_OR_JSON),
        sa.Column("rows_read", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_imported", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_quarantined", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "rows_estimated_knowledge_time",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("digests_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aborted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("code_version", sa.String(length=64)),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("snapshot_id", name="uq_data_package_snapshot"),
        sa.CheckConstraint("coverage_end >= coverage_start", name="ck_package_coverage"),
        sa.CheckConstraint(
            "adjustment_policy IN ('raw_unadjusted', 'split_adjusted', "
            "'total_return_adjusted', 'unknown')",
            name="ck_package_adjustment",
        ),
    )
    op.create_index("ix_data_package_name", "data_packages", ["name", "imported_at"])

    op.create_table(
        "data_package_files",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "package_id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            sa.ForeignKey("data_packages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("declared_rows", sa.BigInteger()),
        sa.Column("observed_rows", sa.BigInteger()),
        sa.Column("timeframe", sa.String(length=8)),
        sa.Column("column_map", JSONB_OR_JSON),
        sa.Column("note", sa.Text()),
        sa.UniqueConstraint("package_id", "path", name="uq_package_file"),
    )
    op.create_index("ix_package_file_dataset", "data_package_files", ["package_id", "dataset"])
    op.create_index("ix_package_file_digest", "data_package_files", ["sha256"])

    op.create_table(
        "import_corrections",
        sa.Column("id", PK, primary_key=True, autoincrement=True),
        sa.Column(
            "package_id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            sa.ForeignKey("data_packages.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "ingestion_run_id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        ),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("source_file", sa.String(length=512), nullable=False),
        sa.Column("line_number", sa.BigInteger(), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("raw_value", sa.Text()),
        sa.Column("corrected_value", sa.Text(), nullable=False),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_correction_package", "import_corrections", ["package_id", "dataset"])
    op.create_index("ix_correction_rule", "import_corrections", ["rule"])
    op.create_index("ix_correction_source", "import_corrections", ["source_file", "line_number"])

    op.add_column(
        "quarantined_rows",
        sa.Column(
            "package_id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            sa.ForeignKey("data_packages.id", ondelete="CASCADE"),
        ),
    )
    op.add_column(
        "quarantined_rows",
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="validated"),
    )
    op.add_column("quarantined_rows", sa.Column("source_file", sa.String(length=512)))
    op.add_column("quarantined_rows", sa.Column("line_number", sa.BigInteger()))
    op.create_index("ix_quarantine_package", "quarantined_rows", ["package_id", "dataset"])
    op.create_index("ix_quarantine_stage", "quarantined_rows", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_quarantine_stage", table_name="quarantined_rows")
    op.drop_index("ix_quarantine_package", table_name="quarantined_rows")
    op.drop_column("quarantined_rows", "line_number")
    op.drop_column("quarantined_rows", "source_file")
    op.drop_column("quarantined_rows", "stage")
    op.drop_column("quarantined_rows", "package_id")

    op.drop_index("ix_correction_source", table_name="import_corrections")
    op.drop_index("ix_correction_rule", table_name="import_corrections")
    op.drop_index("ix_correction_package", table_name="import_corrections")
    op.drop_table("import_corrections")

    op.drop_index("ix_package_file_digest", table_name="data_package_files")
    op.drop_index("ix_package_file_dataset", table_name="data_package_files")
    op.drop_table("data_package_files")

    op.drop_index("ix_data_package_name", table_name="data_packages")
    op.drop_table("data_packages")
