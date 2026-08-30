"""Reflecting a database's actual foreign keys, for migrations that replace a table.

Several revisions in this project replace a Phase-2 draft table outright rather
than altering it. PostgreSQL refuses to drop a table that anything still
references, so each of those revisions has to detach the referring foreign keys
first and put them back afterwards.

**The wrong way to do that is to hard-code which tables refer to it**, and this
module exists because that is what was done and it bricked a fresh install.
Revision 0004 assumed ``("breakout_events", "orders")`` referenced ``patterns``.
`orders` has no ``pattern_id`` column at all — the second referrer created in
0002 is `opportunity_scores` — so ``alembic upgrade head`` on an empty database
issued ``ALTER TABLE orders DROP CONSTRAINT orders_pattern_id_fkey`` against a
constraint that never existed, and PostgreSQL's transactional DDL rolled the
whole chain back to nothing. Revision 0006 had the mirror-image bug: it dropped
`breakout_events` while `opportunity_scores.breakout_event_id` still pointed at
it, having no detach step at all.

Both are the same mistake — a claim about schema history written down once and
never checked against a database — and both are invisible to any test that
starts from a schema which already exists.

**This is not ``IF EXISTS``.** These constraints are not optional: at the point
each revision runs they definitely exist, and the table cannot be dropped while
they do. Asking the database which ones they are is strictly more correct than
naming them, because a reflected list cannot be wrong about the database it is
looking at. :func:`references_to` refusing to return an empty list is what keeps
that from degrading into "drop whatever happens to be there".

Constraint names, columns and ``ON DELETE`` are all read back and replayed, so
the restored constraint is the one that was removed rather than whatever the
migration file believed it should be.
"""

from __future__ import annotations

import sqlalchemy as sa

#: Backends where a foreign key cannot be dropped by name.
#:
#: SQLite has no ALTER-based constraint drop — Alembic emulates one by copying
#: the whole table — and does not enforce the reference at all unless the
#: ``foreign_keys`` pragma is on. Detaching there fails on a name that was never
#: created, so these revisions skip the dance entirely. Deliberate, not
#: defensive: the drop-and-replace works on SQLite precisely because nothing is
#: enforcing the reference.
DIALECTS_WITHOUT_CONSTRAINT_DROP = frozenset({"sqlite"})


class ForeignKeyReference:
    """One foreign key pointing at a table, as the database actually has it.

    A plain class rather than a dataclass on purpose. Alembic executes a
    revision module without registering it in ``sys.modules``, and
    ``@dataclass`` resolves annotations through
    ``sys.modules[cls.__module__].__dict__``, which is ``None`` there — so a
    dataclass defined in a revision file raises at import and the entire chain
    refuses to run. This module is imported normally and would be fine, but the
    type crosses back into revision files and staying boring costs nothing.
    """

    __slots__ = ("columns", "name", "ondelete", "referred_columns", "table")

    def __init__(
        self,
        table: str,
        name: str,
        columns: list[str],
        referred_columns: list[str],
        ondelete: str | None,
    ) -> None:
        self.table = table
        self.name = name
        self.columns = columns
        self.referred_columns = referred_columns
        self.ondelete = ondelete

    def __repr__(self) -> str:
        return f"{self.name} on {self.table}({', '.join(self.columns)})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ForeignKeyReference):
            return NotImplemented
        return (self.table, self.name, self.columns, self.ondelete) == (
            other.table,
            other.name,
            other.columns,
            other.ondelete,
        )

    def __hash__(self) -> int:
        return hash((self.table, self.name, tuple(self.columns)))


def references_to(
    bind: sa.engine.Connection,
    target: str,
    *,
    expect_at_least: int = 1,
) -> list[ForeignKeyReference]:
    """Every foreign key referencing ``target``, discovered by reflection.

    ``expect_at_least`` is the guard that keeps this from being a shrug. A
    revision that replaces a referenced table knows how many referrers the
    previous revision created; finding fewer means the database is not in the
    state being migrated from, and dropping the table anyway would take a schema
    nobody has verified with it. Raising is the right answer — PostgreSQL will
    roll the transaction back and the operator still has a working database.

    Raises when a referring constraint has no name, because there is then
    nothing to drop by name and nothing to restore. Failing here is louder and
    earlier than the ``cannot drop table ... other objects depend on it`` that
    would follow one statement later.
    """
    inspector = sa.inspect(bind)
    found: list[ForeignKeyReference] = []
    for table in sorted(inspector.get_table_names()):
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") != target:
                continue
            name = fk.get("name")
            if not name:
                raise RuntimeError(
                    f"{table} references {target} through an unnamed foreign key, so it "
                    "cannot be detached and restored by name"
                )
            found.append(
                ForeignKeyReference(
                    table=table,
                    name=str(name),
                    columns=list(fk["constrained_columns"]),
                    referred_columns=list(fk.get("referred_columns") or ["id"]),
                    ondelete=(fk.get("options") or {}).get("ondelete"),
                )
            )
    if len(found) < expect_at_least:
        raise RuntimeError(
            f"expected at least {expect_at_least} foreign key(s) referencing {target!r} "
            f"but found {len(found)}: {found}. This database is not in the state this "
            "revision migrates from; refusing to drop the table."
        )
    return found


def detach_references(op: sa.engine.Connection, references: list[ForeignKeyReference]) -> None:
    """Drop each reflected foreign key by its real name.

    ``op`` is Alembic's operations proxy; it is typed loosely because importing
    Alembic here would make this module unusable outside a migration run, and it
    is worth being able to unit-test the reflection half on its own.
    """
    for reference in references:
        op.drop_constraint(reference.name, reference.table, type_="foreignkey")  # type: ignore[attr-defined]


def restore_references(
    op: sa.engine.Connection,
    references: list[ForeignKeyReference],
    target: str,
) -> None:
    """Recreate each foreign key exactly as it was.

    Symmetric with :func:`detach_references` by construction rather than by two
    hand-maintained lists agreeing — which is the property whose absence caused
    the original defect.
    """
    for reference in references:
        op.create_foreign_key(  # type: ignore[attr-defined]
            reference.name,
            reference.table,
            target,
            reference.columns,
            reference.referred_columns,
            ondelete=reference.ondelete,
        )


def supports_constraint_drop(bind: sa.engine.Connection) -> bool:
    return bind.dialect.name not in DIALECTS_WITHOUT_CONSTRAINT_DROP


__all__ = [
    "DIALECTS_WITHOUT_CONSTRAINT_DROP",
    "ForeignKeyReference",
    "detach_references",
    "references_to",
    "restore_references",
    "supports_constraint_drop",
]
