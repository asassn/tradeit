"""The ``tradeit edgar`` command group.

Three commands, in the order an operator uses them::

    tradeit edgar fetch-recipe                 # how to populate the index dir
    tradeit edgar inspect-index FILE.idx        # diagnose one file's parse rate
    tradeit edgar denominator --index-root DIR # build and report
    tradeit edgar controls                     # the 30-control verification table

``fetch-recipe`` prints shell rather than running it. Downloading ~130 quarterly
index files is a long, rate-limited, network-dependent operation that belongs in
an operator's own shell where it can be resumed, and ``sec.gov`` is unreachable
from the build environment anyway.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from tradeit.edgar.controls import CONTROL_UNIVERSE, unverified, verification_table
from tradeit.edgar.index import FETCH_RECIPE, IndexQuarter, parse_index
from tradeit.edgar.pipeline import BuildOptions, build_denominator

__all__ = ["add_edgar_commands"]


def _quarter(text: str) -> IndexQuarter:
    year_text, _, quarter_text = text.upper().partition("Q")
    return IndexQuarter(int(year_text), int(quarter_text or 1))


def cmd_fetch_recipe(_: argparse.Namespace) -> int:
    print(FETCH_RECIPE)
    return 0


def cmd_denominator(args: argparse.Namespace) -> int:
    options = BuildOptions(
        index_root=Path(args.index_root),
        start=_quarter(args.start),
        end=_quarter(args.end),
        as_of=dt.date.fromisoformat(args.as_of) if args.as_of else None,
        quiet_quarters=args.quiet_quarters,
    )
    denominator = build_denominator(options)
    report: dict[str, Any] = denominator.report()
    report["cohort_survival"] = {
        str(year): row for year, row in denominator.cohort_survival().items()
    }
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"registrants           : {report['registrants']}")
    if report["missing_quarters"]:
        missing = report["missing_quarters"]
        print(f"MISSING index quarters: {len(missing)} -> {missing[:8]}")
        print("  a missing quarter is a coverage gap, not a zero")
    print("\nby evidence type")
    for key, value in report["counts_by_evidence_type"].items():
        print(f"  {key:42s} {value:>9,}")
    print("\nby evidence strength")
    for key, value in report["counts_by_strength"].items():
        print(f"  {key:42s} {value:>9,}")
    print(f"\nundated exits (no year assignable): {report['undated_exits']:,}")
    print("  cessation candidates carry no date by construction")
    print("\nconfirmed terminations by year")
    for year, value in report["counts_by_year_confirmed"].items():
        print(f"  {year}  {value:>9,}")
    print("\nidentity mapping")
    for key, value in report["mapping_counts"].items():
        print(f"  {key:42s} {value:>9,}")
    return 0


def cmd_inspect_index(args: argparse.Namespace) -> int:
    """Diagnose one index file without raising, so format drift is legible.

    Runs with ``strict=False`` deliberately: the point is to *see* the failure
    breakdown, and a guard that raised first would hide the thing being
    diagnosed.
    """
    path = Path(args.file)
    text = path.read_text(encoding="latin-1")
    parsed = parse_index(text, quarter_label=args.label or path.stem, strict=False)
    report = parsed.summary()

    if args.json:
        report["skip_samples"] = parsed.skip_samples
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"file            : {path}")
    print(f"bytes           : {path.stat().st_size:,}")
    print(f"layout          : {report['layout']}")
    print(f"fields          : {report['fields']}")
    if parsed.header:
        print(f"header offsets  : {list(parsed.header.offsets)}")
        print(f"header raw      : {parsed.header.raw!r}")
    print()
    print(f"candidate_rows  : {report['candidate_rows']:,}")
    print(f"parsed_rows     : {report['parsed_rows']:,}")
    print(f"skipped_rows    : {report['skipped_rows']:,}")
    print(f"skip_rate       : {parsed.skip_ratio:.2%}")
    if parsed.split_rules:
        print(f"split_rules     : {report['split_rules']}")
    if parsed.skip_reasons:
        print("\nskip_reason_counts")
        for reason, count in sorted(parsed.skip_reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:30s} {count:>9,}")
        print("\nrepresentative samples per reason")
        for reason, samples in parsed.skip_samples.items():
            print(f"  [{reason}]")
            for sample in samples:
                print(f"    {sample}")
    if parsed.rows:
        print("\nfirst three parsed rows")
        for row in parsed.rows[:3]:
            print(
                f"  {row.form_type:12s} {row.company_name[:40]:40s} {row.cik:>10d} "
                f"{row.filed_at} {row.accession}"
            )
    return 0


def cmd_controls(args: argparse.Namespace) -> int:
    rows = verification_table()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    pending = unverified()
    print(
        f"controls: {len(CONTROL_UNIVERSE)}   manual-verified: "
        f"{len(CONTROL_UNIVERSE) - len(pending)}   pending: {len(pending)}"
    )
    print()
    print(f"{'ticker':8s} {'status':16s} {'cik':>10s}  class")
    for row in rows:
        cik = row["cik"]
        print(
            f"{row['ticker']!s:8s} {row['mapping_status']!s:16s} "
            f"{('-' if cik is None else str(cik)):>10s}  {row['control_class']}"
        )
    if pending:
        print(
            f"\n{len(pending)} controls are not MANUAL_VERIFIED. "
            "Milestone 0b is not complete. No CIK is guessed."
        )
    return 0


def add_edgar_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    edgar = sub.add_parser("edgar", help="SEC EDGAR lifecycle evidence and the denominator")
    edgar_sub = edgar.add_subparsers(dest="edgar_command", required=True)

    edgar_sub.add_parser(
        "fetch-recipe", help="print the shell that populates a local index directory"
    ).set_defaults(func=cmd_fetch_recipe)

    denom = edgar_sub.add_parser("denominator", help="build the survivorship denominator")
    denom.add_argument("--index-root", required=True, help="directory of <year>/QTR<n>/form.idx")
    denom.add_argument("--start", default="1994Q3", help="first quarter (default 1994Q3)")
    denom.add_argument("--end", default="2026Q2", help="last quarter")
    denom.add_argument("--as-of", default=None, help="ISO date the cessation rule measures against")
    denom.add_argument("--quiet-quarters", type=int, default=8)
    denom.add_argument("--json", action="store_true")
    denom.set_defaults(func=cmd_denominator)

    inspect = edgar_sub.add_parser(
        "inspect-index", help="diagnose one .idx file: parse rates and skip reasons"
    )
    inspect.add_argument("file", help="path to a form.idx / master.idx / company.idx")
    inspect.add_argument("--label", default=None, help="quarter label for the report")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect_index)

    controls = edgar_sub.add_parser("controls", help="the 30-control verification table")
    controls.add_argument("--json", action="store_true")
    controls.set_defaults(func=cmd_controls)
