"""Make a scan run an isolation boundary, not just a label

Adding ``scan_run_id`` to record *which run inserted a row* would have been
attribution. It would not have been isolation, and the difference is the whole
point of this migration.

The repositories look a row up before writing it —
``PatternRepository._find(identity_key, detector_version)`` and
``BreakoutRepository._find(event_key)`` — and both keys are content hashes of
the instrument, timeframe and structure. Two scans of the same snapshot under
the same configuration therefore derive **byte-identical keys**. With a global
unique constraint the second scan finds the first scan's row and *advances* it:
no duplicate, no error, and afterwards one row carrying the insert-time values
of run A and the advance-time values of run B, with nothing in the schema able
to separate them. A ``scan_run_id`` naming run A would then be provenance that
reads as authoritative and is false.

So the lookups are scoped in the repositories and the constraints are scoped
here, together:

* ``uq_pattern_identity`` -> ``(scan_run_id, identity_key, detector_version)``
* ``uq_breakout_event_identity`` -> ``(scan_run_id, event_key)``
* ``uq_breakout_attempt`` -> ``(scan_run_id, instrument_id, timeframe,
  pattern_key, attempt_number)``

The third one matters and is easy to miss. Separating event *keys* is not
enough: ``uq_breakout_attempt`` keys an attempt directly, so leaving it global
would let run B's attempt 2 on a boundary collide with run A's attempt 2 even
after the event keys no longer collide.

**Observations get no column.** ``pattern_observations.pattern_id`` and
``breakout_observations.event_id`` are ``ON DELETE CASCADE`` foreign keys to
rows that are now run-scoped, so an observation's run is a property of a chain
that cannot be re-pointed. Adding a run column to the child would create a
second source of truth able to disagree with the first.

**Nullable, deliberately.** Three kinds of row have no scan and must stay
representable: hand-made labels and their patterns, test fixtures, and anything
written before this column existed. The column cannot be ``NOT NULL`` without
breaking them. What the schema cannot express, the repository does — it takes
the scan run as a constructor argument, and the scanner always supplies one.

**No backfill.** Existing rows keep NULL. They were produced by a build whose
lookups were global, so attributing them to any run would be a guess, and a
guessed provenance is worse than an absent one. The intended path for the
``diag-02`` corpus is ``scripts/clear_scan_output.py``, not a backfill.

``NULLS NOT DISTINCT`` on all three constraints keeps unscoped rows behaving
exactly as they did before this migration. Without it PostgreSQL treats every
NULL as distinct, so two legacy rows with the same identity key would stop
conflicting — a silent weakening of a constraint that has held since the schema
was written.

Revision ID: 0012_run_scoped_derivation
Revises: 0011_scan_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_run_scoped_derivation"
down_revision: str | None = "0011_scan_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, constraint, columns as they will be after this migration).
SCOPED: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("patterns", "uq_pattern_identity", ("scan_run_id", "identity_key", "detector_version")),
    ("breakout_events", "uq_breakout_event_identity", ("scan_run_id", "event_key")),
    (
        "breakout_events",
        "uq_breakout_attempt",
        ("scan_run_id", "instrument_id", "timeframe", "pattern_key", "attempt_number"),
    ),
)

#: The same constraints as they were before, for the downgrade.
GLOBAL: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("patterns", "uq_pattern_identity", ("identity_key", "detector_version")),
    ("breakout_events", "uq_breakout_event_identity", ("event_key",)),
    (
        "breakout_events",
        "uq_breakout_attempt",
        ("instrument_id", "timeframe", "pattern_key", "attempt_number"),
    ),
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        # SQLite cannot ALTER a constraint; the tables are created from the
        # model metadata in tests, which already carries the scoped form.
        return
    op.add_column("patterns", sa.Column("scan_run_id", sa.BigInteger(), nullable=True))
    op.add_column("breakout_events", sa.Column("scan_run_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_pattern_scan_run", "patterns", "scan_runs", ["scan_run_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_breakout_scan_run",
        "breakout_events",
        "scan_runs",
        ["scan_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_pattern_scan_run", "patterns", ["scan_run_id"])
    op.create_index("ix_breakout_scan_run", "breakout_events", ["scan_run_id"])

    for table, name, columns in SCOPED:
        op.drop_constraint(name, table, type_="unique")
        # NULLS NOT DISTINCT is not expressible through `create_unique_constraint`,
        # so the DDL is written out. PostgreSQL 15+.
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"UNIQUE NULLS NOT DISTINCT ({', '.join(columns)})"
            )
        )


def downgrade() -> None:
    if _is_sqlite():
        return
    bind = op.get_bind()
    for table, name, columns in GLOBAL:
        # Narrowing back to a global constraint fails outright if two runs hold
        # the same key — which is precisely the state this migration exists to
        # make possible. Say so rather than letting PostgreSQL report a
        # duplicate-key error with no explanation.
        clash = bind.execute(
            sa.text(
                f"SELECT count(*) FROM (SELECT 1 FROM {table} "
                f"GROUP BY {', '.join(columns)} HAVING count(*) > 1) AS d"
            )
        ).scalar_one()
        if clash:
            raise RuntimeError(
                f"{clash} group(s) in {table} share {list(columns)} across scan runs. "
                "Restoring the global constraint would require discarding one run's "
                "derived corpus. Clear the runs you no longer want with "
                "scripts/clear_scan_output.py first."
            )
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(name, table, list(columns))

    op.drop_index("ix_breakout_scan_run", table_name="breakout_events")
    op.drop_index("ix_pattern_scan_run", table_name="patterns")
    op.drop_constraint("fk_breakout_scan_run", "breakout_events", type_="foreignkey")
    op.drop_constraint("fk_pattern_scan_run", "patterns", type_="foreignkey")
    op.drop_column("breakout_events", "scan_run_id")
    op.drop_column("patterns", "scan_run_id")
