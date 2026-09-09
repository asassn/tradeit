#!/usr/bin/env python
"""Make the corpus self-describing, and stop one column meaning three things.

Two jobs, both answering the same complaint: *there is no reason to need a data
dictionary to interpret the data.*

**1. Separate identity prose from share-class titles.** ``securities.class_label``
is documented as the filing's own words for a share class. For 34 control
securities it instead holds paragraphs of identity reasoning -- up to 4,156
characters -- which the data dictionary records as trap 0.5 with the
instruction to check ``source`` before believing the column. The prose moves to
``identity_evidence`` (migration 0017) and ``class_label`` becomes NULL on
those rows, which is the honest value: a share-class title was never
established for them.

Nothing is discarded. The prose is *moved*, and this script refuses to run the
move unless the destination column is empty on every row it would write, so a
second run cannot overwrite a first one's work.

**2. Write the reading guide into the file.** ``corpus_readme`` states what the
views cannot fix -- that names are point-in-time, that a fetch "failure" often
means the system refused to guess, that no backtest here is evidence of
profitability. A tool that opens this file finds them without being handed a
markdown document alongside it.

The corpus was built by ``create_all`` rather than by alembic and has no
``alembic_version`` table, so the column is added with a direct, idempotent
``ALTER``. The migration exists for databases that *are* alembic-managed.

``--dry-run`` reports what each step would do and writes nothing.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.views import README, VIEWS, prepare_corpus
from tradeit.storage.session import install_sqlite_busy_timeout

_PROSE_SOURCE = "control_identity_evidence"


def _has_column(session: Session, table: str, column: str) -> bool:
    rows = session.execute(text(f"pragma table_info({table})")).all()
    return any(row[1] == column for row in rows)


def _separate_identity_prose(session: Session, *, dry_run: bool) -> int:
    """Move identity prose out of ``class_label``. Returns rows moved."""
    if not _has_column(session, "securities", "identity_evidence"):
        if dry_run:
            print("  would add securities.identity_evidence")
        else:
            session.execute(text("alter table securities add column identity_evidence text"))
            session.commit()
            print("  added securities.identity_evidence")

    if dry_run and not _has_column(session, "securities", "identity_evidence"):
        pending = session.execute(
            text("select count(*) from securities where source = :s and class_label is not null"),
            {"s": _PROSE_SOURCE},
        ).scalar_one()
        print(f"  would move {pending} class_label values to identity_evidence")
        return int(pending)

    # Refuse to overwrite: if the destination already holds something on a row
    # we would write, a previous run did this and the source is not what we
    # think it is.
    conflicts = session.execute(
        text(
            "select count(*) from securities "
            "where source = :s and class_label is not null and identity_evidence is not null"
        ),
        {"s": _PROSE_SOURCE},
    ).scalar_one()
    if conflicts:
        raise SystemExit(
            f"refusing to move: {conflicts} rows already have identity_evidence set "
            "alongside a class_label. Inspect them before rerunning."
        )

    pending = session.execute(
        text("select count(*) from securities where source = :s and class_label is not null"),
        {"s": _PROSE_SOURCE},
    ).scalar_one()
    if dry_run:
        print(f"  would move {pending} class_label values to identity_evidence")
        return int(pending)
    if pending:
        session.execute(
            text(
                "update securities set identity_evidence = class_label, class_label = null "
                "where source = :s and class_label is not null"
            ),
            {"s": _PROSE_SOURCE},
        )
        session.commit()
    print(f"  moved {pending} class_label values to identity_evidence")
    return int(pending)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--analyze",
        action="store_true",
        help="run ANALYZE afterwards so the query planner has statistics",
    )
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    print("1. identity prose")
    moved = _separate_identity_prose(session, dry_run=args.dry_run)

    print("2. reading guide")
    if args.dry_run:
        print(f"  would rebuild {len(VIEWS)} views, trading_sessions and {len(README)} entries")
        return 0
    sessions, entries = prepare_corpus(session)
    print(f"  trading_sessions: {sessions:,}")
    print(f"  views: {', '.join(VIEWS)}")
    print(f"  corpus_readme: {entries} entries")

    if args.analyze:
        print("3. ANALYZE")
        session.execute(text("analyze"))
        session.commit()
        print("  done")

    print(f"\nmoved {moved} prose values; the file now explains itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
