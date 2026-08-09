"""The ``tradeit data`` and ``tradeit validate`` command group.

Kept out of :mod:`tradeit.cli` because the data commands are the entire
operator-facing surface of the empirical gate, and they should be readable
without scrolling past a demo ingest.

The command order mirrors the order an operator actually uses them:

    tradeit data package-spec > DATA_REQUIRED.md   # what to buy or export
    tradeit data template ./my-package             # a manifest to fill in
    tradeit data inspect ./my-package              # what did I just get?
    tradeit data import ./my-package --dry-run     # what would it do?
    tradeit data import ./my-package               # do it
    tradeit validate --snapshot <id>               # what does it say?

``--dry-run`` sits deliberately between inspect and import. It executes every
stage — reads, normalizes, validates, dates, decides each quarantine — and
writes nothing, so "what will this do to my database?" is answerable before it
does it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

from tradeit.data.packages.database import DatabaseSink
from tradeit.data.packages.importer import (
    ImportOptions,
    PackageImporter,
    inspect_package,
)
from tradeit.data.packages.manifest import build_manifest_template, load_manifest
from tradeit.data.packages.pointintime import KnowledgeTimePolicy, describe_policy
from tradeit.data.packages.readers import resolve_package
from tradeit.data.packages.sinks import CountingSink, JsonlSink, RecordSink
from tradeit.data.packages.spec import DATASET_SPECS, DatasetKind, describe_dataset
from tradeit.data.validation_universe import default_universe
from tradeit.errors import TradeitError
from tradeit.storage.session import session_scope
from tradeit.validation.context import load_context
from tradeit.validation.runner import run_validation


def cmd_package_spec(args: argparse.Namespace) -> int:
    """Emit the data specification an operator hands to a vendor."""
    lines = [
        "# Data required for empirical validation",
        "",
        "Every dataset below is optional except the manifest. Supplying fewer",
        "datasets does not break the import; it reduces what can be validated,",
        "and the import report states exactly what each missing file costs.",
        "",
        "Columns are described by **meaning**, not by any vendor's name for them.",
        "The manifest maps your column names onto these; the importer never",
        "guesses, because a `close` column that actually holds adjusted closes",
        "would otherwise be accepted silently and produce patterns that never",
        "existed.",
        "",
    ]
    for kind in DatasetKind:
        lines.append(describe_dataset(kind))
        lines.append("")
    lines += [
        "## Point-in-time requirements",
        "",
        "```",
        describe_policy(KnowledgeTimePolicy()),
        "```",
        "",
        "A quarter ending 31 March was not knowable on 31 March. Supply a filing",
        "or publication timestamp for every fundamental fact, or the importer",
        "quarantines them rather than guessing when they became available.",
        "",
    ]
    text = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    """Generate a starter manifest for the files already in a directory."""
    root = Path(args.path)
    if not root.is_dir():
        print(f"{root} is not a directory", file=sys.stderr)
        return 2
    text = build_manifest_template(root, name=args.name, provider=args.provider)
    target = root / "manifest.toml"
    if target.exists() and not args.force:
        print(f"{target} already exists; pass --force to overwrite", file=sys.stderr)
        return 2
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    print("Fill in every PLEASE_SET value before importing. They are deliberately")
    print("left unparseable so an unfilled template cannot be imported by accident.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Describe a package and verify its digests without reading a row."""
    with tempfile.TemporaryDirectory() as workspace:
        print(inspect_package(Path(args.path), Path(workspace)))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Run a package through the pipeline, with or without writing it."""
    options = ImportOptions(
        dry_run=args.dry_run,
        verify_digests=not args.skip_digests,
        limit_rows=args.limit,
        knowledge=KnowledgeTimePolicy(
            require_reported_fundamentals=not args.estimate_filing_dates,
        ),
        only=tuple(DatasetKind(name) for name in (args.only or ())),
    )

    with tempfile.TemporaryDirectory() as workspace:
        root = resolve_package(Path(args.path), Path(workspace))
        manifest = load_manifest(root / "manifest.toml")

        if args.dry_run:
            dry_sink: RecordSink = JsonlSink(Path(args.jsonl)) if args.jsonl else CountingSink()
            report = PackageImporter(manifest, root, options=options, sink=dry_sink).run()
            if isinstance(dry_sink, JsonlSink):
                dry_sink.close()
            print(report.render())
            print()
            print("DRY RUN — nothing was written.")
            return 1 if report.aborted else 0

        with session_scope() as session:
            sink = DatabaseSink(
                session=session,
                manifest=manifest,
                source_path=Path(args.path).resolve(),
                code_version=args.code_version,
            )
            report = PackageImporter(manifest, root, options=options, sink=sink).run()
            package = sink.finalise(report)
            print(report.render())
            print()
            print(f"snapshot id: {package.snapshot_id}")
            print(f"validate it with: tradeit validate --snapshot {package.snapshot_id}")
            return 1 if report.aborted else 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Run every check against one imported snapshot."""
    as_of = (
        dt.datetime.combine(dt.date.fromisoformat(args.as_of), dt.time(23, 59), tzinfo=dt.UTC)
        if args.as_of
        else None
    )
    with session_scope() as session:
        context = load_context(
            session,
            args.snapshot,
            as_of=as_of,
            universe=default_universe(),
            code_version=args.code_version,
        )
        run = run_validation(context)

    print(run.render())
    if args.json:
        Path(args.json).write_text(
            json.dumps(run.to_payload(), indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")

    usable, _ = run.is_evidence
    # Exit non-zero when the run is not citable. A CI job that treats a
    # half-blocked run as success is a CI job that will eventually approve one.
    return 0 if usable else 1


def cmd_datasets(_: argparse.Namespace) -> int:
    """List the datasets a package may contain and what each unlocks."""
    for kind, spec in DATASET_SPECS.items():
        print(f"{kind:<24} {spec.summary}")
        if spec.requires:
            print(f"{'':<24} requires: {', '.join(str(d) for d in spec.requires)}")
        for item in spec.enables:
            print(f"{'':<24}   + {item}")
    return 0


def add_data_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach the data and validation commands to the top-level parser."""
    data = sub.add_parser("data", help="offline data packages")
    data_sub = data.add_subparsers(dest="data_command", required=True)

    spec = data_sub.add_parser("package-spec", help="emit the column contract to hand a vendor")
    spec.add_argument("--output", default=None, help="write to this file instead of stdout")
    spec.set_defaults(func=cmd_package_spec)

    data_sub.add_parser("datasets", help="list datasets and what each unlocks").set_defaults(
        func=cmd_datasets
    )

    template = data_sub.add_parser("template", help="generate a starter manifest.toml")
    template.add_argument("path")
    template.add_argument("--name", default="package")
    template.add_argument("--provider", default="unknown")
    template.add_argument("--force", action="store_true")
    template.set_defaults(func=cmd_template)

    inspect = data_sub.add_parser("inspect", help="describe a package and verify its digests")
    inspect.add_argument("path")
    inspect.set_defaults(func=cmd_inspect)

    imp = data_sub.add_parser("import", help="import a package directory or .zip")
    imp.add_argument("path")
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="execute every stage and write nothing",
    )
    imp.add_argument(
        "--jsonl",
        default=None,
        help="with --dry-run, write the dated records and quarantine to this directory",
    )
    imp.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after N rows per file; marks the snapshot partial",
    )
    imp.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="import only these datasets",
    )
    imp.add_argument(
        "--skip-digests",
        action="store_true",
        help="do not re-hash files (recorded in the report; leave off)",
    )
    imp.add_argument(
        "--estimate-filing-dates",
        action="store_true",
        help=(
            "accept a filing-deadline estimate for fundamentals with no publication "
            "timestamp. Every affected row is marked ESTIMATED and counted; without "
            "this flag they are quarantined rather than guessed."
        ),
    )
    imp.add_argument("--code-version", default=None)
    imp.set_defaults(func=cmd_import)

    validate = sub.add_parser("validate", help="run the empirical checks over a snapshot")
    validate.add_argument("--snapshot", required=True, help="snapshot id from a data import")
    validate.add_argument("--as-of", default=None, help="ISO date; defaults to the export date")
    validate.add_argument("--json", default=None, help="also write the report payload here")
    validate.add_argument("--code-version", default="unknown")
    validate.set_defaults(func=cmd_validate)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point, so the data commands are usable on their own."""
    parser = argparse.ArgumentParser(prog="tradeit-data", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add_data_commands(sub)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TradeitError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


__all__ = [
    "add_data_commands",
    "cmd_datasets",
    "cmd_import",
    "cmd_inspect",
    "cmd_package_spec",
    "cmd_template",
    "cmd_validate",
]


if __name__ == "__main__":
    sys.exit(main())
