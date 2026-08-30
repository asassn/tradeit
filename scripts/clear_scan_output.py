"""Discard the Phase 4/5 output of a scan, leaving its inputs untouched.

A scan's *output* is patterns, breakout events, their observation logs and the
run's ledger rows. Its *inputs* — imported bars, instruments, symbol mappings,
the data package itself — are written by the importer and are not touched here,
by construction: nothing this script deletes has a foreign key pointing at any
of them.

**Why this exists rather than a pasted SQL blob.** Deleting is the one operation
that cannot be undone by running it again, and the ordering matters (breakout
events before patterns, so a `SET NULL` on `breakout_events.pattern_id` never
runs). Code in the repository can be read before it is run, and it prints what
it will do before doing it.

Dry run by default. It prints the counts and exits without touching anything
unless ``--confirm`` is passed.

    python scripts/clear_scan_output.py --scan-id diag-01
    python scripts/clear_scan_output.py --scan-id diag-01 --confirm

**Cascades, so the printed counts are the whole story.** Deleting a pattern
removes its observations and relationships (`ON DELETE CASCADE`). Deleting a
breakout event removes its observations and relationships. Human labels
(`pattern_labels`, `breakout_labels`) and `opportunity_scores` are `SET NULL`
rather than deleted — the rows survive with a dangling reference, so this script
counts them and refuses to run if any exist unless ``--orphan-labels`` is also
passed. Losing hand-made labels to a diagnostic reset is not a trade worth
making silently.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tradeit.storage import tables as t
from tradeit.storage.session import session_scope

#: Tables whose contents are a scan's *inputs*. Printed before and after so the
#: claim "your snapshot is untouched" is a measurement rather than an assurance.
INPUT_TABLES = (
    ("ohlcv_bars", t.OhlcvBar),
    ("instruments", t.Instrument),
    ("symbol_mappings", t.SymbolMapping),
    ("data_packages", t.DataPackage),
)


def count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def snapshot_of(session: Session, scan_id: str) -> str | None:
    return session.scalar(select(t.ScanRun.snapshot_id).where(t.ScanRun.scan_id == scan_id))


def report(session: Session, scan_id: str) -> dict[str, int]:
    """What deleting would remove. Read-only."""
    return {
        "patterns": count(session, t.Pattern),
        "pattern_observations": count(session, t.PatternObservation),
        "pattern_relationships": count(session, t.PatternRelationship),
        "breakout_events": count(session, t.BreakoutEvent),
        "breakout_observations": count(session, t.BreakoutObservation),
        "scan_runs": int(
            session.scalar(
                select(func.count()).select_from(t.ScanRun).where(t.ScanRun.scan_id == scan_id)
            )
            or 0
        ),
        "scan_progress": int(
            session.scalar(
                select(func.count())
                .select_from(t.ScanProgress)
                .join(t.ScanRun, t.ScanRun.id == t.ScanProgress.scan_run_id)
                .where(t.ScanRun.scan_id == scan_id)
            )
            or 0
        ),
    }


def labels(session: Session) -> dict[str, int]:
    return {
        "pattern_labels": count(session, t.PatternLabel),
        "breakout_labels": count(session, t.BreakoutLabel),
        "opportunity_scores": count(session, t.OpportunityScore),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-id", required=True, help="the scan whose output to discard")
    parser.add_argument(
        "--confirm", action="store_true", help="actually delete; without this it is a dry run"
    )
    parser.add_argument(
        "--orphan-labels",
        action="store_true",
        help="proceed even though human labels or opportunity scores reference these rows",
    )
    args = parser.parse_args(argv)

    with session_scope() as session:
        snapshot = snapshot_of(session, args.scan_id)
        if snapshot is None:
            print(f"no scan_runs row with scan_id {args.scan_id!r}", file=sys.stderr)
            known = session.scalars(select(t.ScanRun.scan_id)).all()
            if known:
                print(f"known scan ids: {', '.join(sorted(known))}", file=sys.stderr)
            return 1

        print(f"scan {args.scan_id!r} ran against snapshot {snapshot}\n")
        print("Phase 4/5 output that would be deleted:")
        for name, value in report(session, args.scan_id).items():
            print(f"  {name:<24} {value:>12,}")

        held = {name: value for name, value in labels(session).items() if value}
        if held:
            print("\nrows that would be ORPHANED (kept, reference set to NULL):")
            for name, value in held.items():
                print(f"  {name:<24} {value:>12,}")

        print("\ninputs, which are not touched:")
        before = {name: count(session, model) for name, model in INPUT_TABLES}
        for name, value in before.items():
            print(f"  {name:<24} {value:>12,}")

        if held and not args.orphan_labels:
            print(
                "\nrefusing: the rows above were made by hand and would be left "
                "pointing at nothing. Re-run with --orphan-labels if that is what "
                "you want.",
                file=sys.stderr,
            )
            return 2

        if not args.confirm:
            print("\ndry run; nothing was deleted. Re-run with --confirm to proceed.")
            return 0

        # Breakout events first: `breakout_events.pattern_id` is ON DELETE SET
        # NULL, so deleting patterns first would rewrite rows about to be
        # deleted anyway.
        session.execute(delete(t.BreakoutEvent))
        session.execute(delete(t.Pattern))
        session.execute(
            delete(t.ScanRun).where(t.ScanRun.scan_id == args.scan_id)
        )  # scan_progress cascades
        session.commit()

        print("\ndeleted. Remaining:")
        for name, value in report(session, args.scan_id).items():
            print(f"  {name:<24} {value:>12,}")
        print("\ninputs after the delete (must match the numbers above):")
        after = {name: count(session, model) for name, model in INPUT_TABLES}
        for name, value in after.items():
            mark = "ok" if value == before[name] else "CHANGED"
            print(f"  {name:<24} {value:>12,}  {mark}")
        if after != before:
            print("\ninput tables changed; this is a bug in this script", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
