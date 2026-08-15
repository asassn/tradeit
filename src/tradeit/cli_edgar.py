"""The ``tradeit edgar`` command group.

Three commands, in the order an operator uses them::

    tradeit edgar fetch-recipe                 # how to populate the index dir
    tradeit edgar inspect-index FILE.idx        # diagnose one file's parse rate
    tradeit edgar denominator --index-root DIR # build and report
    tradeit edgar controls --diagnose          # control identity + citations
    tradeit edgar verify-control AAPL ...      # gather CIK candidates for one control
    tradeit edgar cik-filings 1100683 ...      # every filing for one CIK, chronologically
    tradeit edgar audit-paths --index-root DIR # corpus-wide raw-vs-parsed field audit
    tradeit edgar audit-exceptions ...          # explain each row the audit flagged

``fetch-recipe`` prints shell rather than running it. Downloading ~130 quarterly
index files is a long, rate-limited, network-dependent operation that belongs in
an operator's own shell where it can be resumed, and ``sec.gov`` is unreachable
from the build environment anyway.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tradeit.edgar.control_evidence import load_control_evidence, resolve_controls
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.identity import MappingStatus
from tradeit.edgar.index import (
    FETCH_RECIPE,
    FullIndexRow,
    IndexHeader,
    IndexQuarter,
    accession_from_path,
    explain_row,
    parse_index,
)
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


# ---------------------------------------------------------------------------
# The independent raw extractor
#
# Everything below deliberately duplicates work the production parser already
# does, by a *different* method, because an audit that calls the parser is an
# audit of nothing. The production fixed-width reader peels the row from the
# right with ``rsplit(maxsplit=3)`` and separates the two free-text fields using
# the header's column offsets. The extractor here never looks at the header, never
# splits on token counts, and instead matches the row's *shape*: padding runs of
# two or more spaces delimit the free-text fields, and the CIK, date and File Name
# are each pinned by their own literal form.
#
# When the shape does not match, the row is reported as an extraction failure. It
# is never guessed at, and it is never quietly counted as agreeing with the
# parser -- an auditor that cannot read a row has not verified that row.
# ---------------------------------------------------------------------------

#: File Name occurrences, used for the duplicate accounting only.
_RAW_PATH = re.compile(r"(edgar/data/\S+)", re.IGNORECASE)

#: A dashed rule closes the preamble. Re-derived here rather than imported.
_RAW_SEPARATOR = re.compile(r"^\s*-{5,}")

#: Shape-driven fixed-width extraction. ``\s{2,}`` is the column padding, which
#: is why ``SC 13D`` and ``AMERICAN TELEPHONE & TELEGRAPH CO`` survive: a single
#: space inside a field is never a delimiter. Both free-text groups are lazy, so
#: a name containing a double space still resolves -- the tail must be digits,
#: then a date, then a path, and no interior point of a name satisfies that.
_RAW_FIXED_ROW = re.compile(
    r"^(?P<form>\S.*?)\s{2,}"
    r"(?P<name>\S.*?)\s{2,}"
    r"(?P<cik>\d{1,10})\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{8})\s+"
    r"(?P<path>edgar/data/\S+?)\s*$",
    re.IGNORECASE,
)

#: Second-tier extraction for the historical rows the shape rule cannot read: a
#: company name wide enough to consume its column's padding runs straight into
#: the CIK, leaving ``...GENERAL L P5011`` as one token with no delimiter to find.
#: The CIK is then taken from the path's own ``edgar/data/<cik>/`` and required to
#: be the trailing digits of that token. This is corroboration, not independent
#: extraction -- the production parser recovers those rows the same way -- so rows
#: read this way are counted and reported under their own heading and never
#: folded into the independently-verified population.
_RAW_GLUED_ROW = re.compile(
    r"^(?P<form>\S.*?)\s{2,}"
    r"(?P<tail>\S.*?)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{8})\s+"
    r"(?P<path>edgar/data/\S+?)\s*$",
    re.IGNORECASE,
)

_RAW_PATH_CIK = re.compile(r"edgar/data/(\d+)/", re.IGNORECASE)

_RAW_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d")

#: strptime is width-lenient -- ``%Y%m%d`` happily reads the six-digit CIK
#: ``320193`` as the year 3201 -- and in a pipe row the auditor identifies fields
#: by shape, so a CIK that can pass for a date would steal the date's identity.
#: The shape is therefore pinned before any coercion is attempted.
_RAW_DATE_SHAPE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{8})$")


@dataclass(frozen=True, slots=True)
class _RawRow:
    """One index line as read by the auditor, with no help from the parser."""

    form_type: str
    company_name: str
    cik: int
    filed_at: dt.date
    path: str
    line: str
    #: ``True`` when the CIK came from the path rather than from its own column,
    #: which makes the CIK comparison corroborative rather than independent.
    cik_from_path: bool = False


def _raw_date(text: str) -> dt.date | None:
    if not _RAW_DATE_SHAPE.match(text):
        return None
    for fmt in _RAW_DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.UTC).date()
        except ValueError:
            continue
    return None


def _raw_extract_pipe(line: str) -> _RawRow | None:
    """Pipe rows by field *shape*, not by header order.

    The path, date and CIK identify themselves. Whichever of the two remaining
    fields is the form type follows from where the CIK sits: CIK first is the
    ``master.idx`` order, CIK third is the ``form.idx`` order. Any other
    arrangement is refused rather than assumed.
    """
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 5:
        return None
    path_at = next((i for i, p in enumerate(parts) if "edgar/data/" in p.lower()), None)
    date_at = next((i for i, p in enumerate(parts) if _raw_date(p) is not None), None)
    cik_at = next((i for i, p in enumerate(parts) if p.isdigit() and i != date_at), None)
    if path_at is None or date_at is None or cik_at is None:
        return None
    if cik_at == 0:
        form_at, name_at = 2, 1
    elif cik_at == 2:
        form_at, name_at = 0, 1
    else:
        return None
    filed_at = _raw_date(parts[date_at])
    if filed_at is None:
        return None
    return _RawRow(
        form_type=parts[form_at],
        company_name=parts[name_at],
        cik=int(parts[cik_at]),
        filed_at=filed_at,
        path=parts[path_at],
        line=line,
    )


def _raw_extract_glued(line: str) -> _RawRow | None:
    found = _RAW_GLUED_ROW.match(line)
    if found is None:
        return None
    path = found.group("path").strip()
    in_path = _RAW_PATH_CIK.search(path)
    if in_path is None:
        return None
    path_cik = in_path.group(1)
    tail = found.group("tail")
    if not tail.endswith(path_cik):
        return None
    filed_at = _raw_date(found.group("date"))
    if filed_at is None:
        return None
    return _RawRow(
        form_type=found.group("form").strip(),
        company_name=tail[: -len(path_cik)].strip(),
        cik=int(path_cik),
        filed_at=filed_at,
        path=path,
        line=line,
        cik_from_path=True,
    )


def _raw_extract(line: str) -> _RawRow | None:
    if "|" in line:
        return _raw_extract_pipe(line)
    stripped = line.rstrip()
    found = _RAW_FIXED_ROW.match(stripped)
    if found is None:
        return _raw_extract_glued(stripped)
    filed_at = _raw_date(found.group("date"))
    if filed_at is None:
        return None
    return _RawRow(
        form_type=found.group("form").strip(),
        company_name=found.group("name").strip(),
        cik=int(found.group("cik")),
        filed_at=filed_at,
        path=found.group("path").strip(),
        line=line,
    )


@dataclass(slots=True)
class _AuditTotals:
    """Every number the gate is decided on, so none of them is recomputed twice."""

    files: int = 0
    raw_candidates: int = 0
    raw_extracted: int = 0
    raw_corroborated: int = 0
    raw_failures: int = 0
    parsed_rows: int = 0
    compared: int = 0
    unverifiable: int = 0
    parser_skipped: int = 0
    raw_occurrences: int = 0
    raw_distinct: int = 0
    duplicate_rawpaths: int = 0
    form_case_only: int = 0
    name_mismatch: int = 0

    mismatches: dict[str, int] = field(default_factory=dict)

    def bump(self, category: str) -> None:
        self.mismatches[category] = self.mismatches.get(category, 0) + 1


_MISMATCH_CATEGORIES = ("form_type", "cik", "filed_at", "path", "accession")

#: The three fields ``classify_corpus`` actually consumes. A mismatch in any of
#: them is a denominator problem, not a provenance problem.
_CLASSIFICATION_INPUTS = ("cik", "form_type", "filed_at")


def cmd_audit_paths(args: argparse.Namespace) -> int:
    """Compare all five parsed fields against an independent read of the raw line.

    Blast-radius instrument for a reported path/accession corruption, widened to
    the fields that decide classification. For every parsed row it re-reads the
    exact source line -- ``FullIndexRow.source_line`` makes that alignment exact
    rather than inferred -- extracts form type, CIK, date and File Name by the
    independent rules above, and compares field by field.

    Mismatches are reported, never repaired and never skipped: a repair here
    destroys the evidence being sought. Lines the auditor cannot read are counted
    as extraction failures and excluded from the verified population, because
    "could not check" and "checked and agreed" are different facts.
    """
    root = Path(args.index_root)
    files = sorted(root.glob("*/QTR*/form.idx"))
    if not files:
        print(f"no form.idx files under {root}")
        return 2

    totals = _AuditTotals(files=len(files))
    by_quarter: dict[str, dict[str, int]] = {}
    dupes_by_quarter: dict[str, int] = {}
    dupe_examples: list[tuple[str, str, list[str]]] = []
    samples: dict[str, list[tuple[str, _RawRow, FullIndexRow]]] = {}
    failure_samples: list[tuple[str, int, str]] = []

    def note(label: str, category: str) -> None:
        by_quarter.setdefault(label, {})[category] = (
            by_quarter.setdefault(label, {}).get(category, 0) + 1
        )

    for path_file in files:
        label = f"{path_file.parent.parent.name}-{path_file.parent.name}"
        text = path_file.read_text(encoding="latin-1")
        parsed = parse_index(text, quarter_label=label, strict=False)
        totals.parsed_rows += len(parsed.rows)

        raw_by_line: dict[int, _RawRow] = {}
        first_line: dict[str, str] = {}
        repeated: dict[str, list[str]] = {}
        occurrences = 0
        seen_separator = False

        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.rstrip()
            if not line.strip():
                continue
            if _RAW_SEPARATOR.match(line):
                seen_separator = True
                continue
            if not seen_separator:
                continue

            totals.raw_candidates += 1

            # Duplicate accounting is deliberately kept on *occurrences*. One
            # File Name can legitimately appear on more than one index line --
            # the same document listed under two form types -- and collapsing
            # those into a set manufactures a shortfall that looks like missing
            # coverage.
            found = _RAW_PATH.search(line)
            if found:
                occurrences += 1
                value = found.group(1).strip()
                previous = first_line.get(value)
                if previous is None:
                    first_line[value] = line
                else:
                    repeated.setdefault(value, [previous]).append(line)

            extracted = _raw_extract(line)
            if extracted is None:
                totals.raw_failures += 1
                note(label, "raw_extraction_failure")
                if len(failure_samples) < 20:
                    failure_samples.append((label, number, line[:180]))
                continue
            if extracted.cik_from_path:
                totals.raw_corroborated += 1
                note(label, "cik_corroborated_from_path")
            else:
                totals.raw_extracted += 1
            raw_by_line[number] = extracted

        totals.raw_occurrences += occurrences
        totals.raw_distinct += len(first_line)
        duplicates = occurrences - len(first_line)
        totals.duplicate_rawpaths += duplicates
        if duplicates:
            dupes_by_quarter[label] = duplicates
            if len(dupe_examples) < 5 and repeated:
                name, repeats = next(iter(sorted(repeated.items())))
                dupe_examples.append((label, name, repeats[:2]))

        matched_lines: set[int] = set()
        for row in parsed.rows:
            raw_row = raw_by_line.get(row.source_line)
            if raw_row is None:
                totals.unverifiable += 1
                note(label, "parsed_but_unreadable_raw")
                continue
            matched_lines.add(row.source_line)
            totals.compared += 1

            differences: list[str] = []
            # The parser upper-cases form type; that is a documented production
            # normalisation, so it is compared case-insensitively AND the
            # case-only difference is counted, rather than being normalised out
            # of sight.
            if raw_row.form_type.upper() != row.form_type:
                differences.append("form_type")
            elif raw_row.form_type != row.form_type:
                totals.form_case_only += 1
            if raw_row.cik != row.cik:
                differences.append("cik")
            if raw_row.filed_at != row.filed_at:
                differences.append("filed_at")
            if raw_row.path != row.path:
                differences.append("path")
            if accession_from_path(raw_row.path) != row.accession:
                differences.append("accession")
            if raw_row.company_name != row.company_name:
                totals.name_mismatch += 1

            for category in differences:
                totals.bump(category)
                note(label, category)
                bucket = samples.setdefault(category, [])
                if len(bucket) < 5:
                    bucket.append((label, raw_row, row))

        skipped = len(raw_by_line) - len(matched_lines)
        if skipped:
            totals.parser_skipped += skipped
            note(label, "raw_read_but_parser_skipped")

    return _report_audit(
        totals, by_quarter, dupes_by_quarter, dupe_examples, samples, failure_samples
    )


def _report_audit(
    totals: _AuditTotals,
    by_quarter: dict[str, dict[str, int]],
    dupes_by_quarter: dict[str, int],
    dupe_examples: list[tuple[str, str, list[str]]],
    samples: dict[str, list[tuple[str, _RawRow, FullIndexRow]]],
    failure_samples: list[tuple[str, int, str]],
) -> int:
    print(f"index files scanned            : {totals.files}")
    print(f"raw candidate rows            : {totals.raw_candidates:,}")
    print(f"raw rows independently read   : {totals.raw_extracted:,}")
    print(
        f"  + CIK corroborated by path  : {totals.raw_corroborated:,} (name adjoins CIK, see note)"
    )
    print(f"raw-extraction FAILURES       : {totals.raw_failures:,}")
    print(f"rows parsed                   : {totals.parsed_rows:,}")
    print(f"rows compared field-by-field  : {totals.compared:,}")
    print(f"  parsed, raw unreadable      : {totals.unverifiable:,} (not verified either way)")
    print(f"  raw read, parser skipped    : {totals.parser_skipped:,}")
    print()
    print(f"FORM mismatches               : {totals.mismatches.get('form_type', 0):,}")
    print(f"CIK mismatches                : {totals.mismatches.get('cik', 0):,}")
    print(f"DATE mismatches               : {totals.mismatches.get('filed_at', 0):,}")
    print(f"PATH mismatches               : {totals.mismatches.get('path', 0):,}")
    print(f"ACCESSION mismatches          : {totals.mismatches.get('accession', 0):,}")
    print()
    print(f"raw File Name OCCURRENCES     : {totals.raw_occurrences:,}")
    print(f"raw File Name DISTINCT        : {totals.raw_distinct:,}")
    print(f"  duplicate File Names        : {totals.duplicate_rawpaths:,} (same path on >1 line)")
    print()
    print(f"form type differing by case   : {totals.form_case_only:,} (parser upper-cases)")
    print(f"company name differences      : {totals.name_mismatch:,} (informational, not an input)")

    interesting = sorted(by_quarter.items())
    if interesting:
        print("\ncounts by quarter, every mismatch, failure and corroboration category")
        for label, counts in interesting:
            detail = "  ".join(f"{k}={v:,}" for k, v in sorted(counts.items()))
            print(f"  {label}  {detail}")
    else:
        print("\nNo mismatch or failure in any quarter.")

    if dupes_by_quarter:
        print(f"\nduplicate File Names by quarter ({len(dupes_by_quarter)} quarters affected)")
        for label, count in sorted(dupes_by_quarter.items()):
            print(f"  {label}  {count:,}")
        print("\nrepresentative duplicated File Names (the raw lines sharing one path)")
        for label, name, repeats in dupe_examples:
            print(f"  {label}  {name}")
            for raw in repeats:
                print(f"    {raw[:160]}")

    if failure_samples:
        print("\nrepresentative raw-extraction failures (quarter, line number, raw line)")
        for label, number, line in failure_samples:
            print(f"  {label}:{number}\n    {line}")

    for category in _MISMATCH_CATEGORIES:
        bucket = samples.get(category)
        if not bucket:
            continue
        print(f"\nrepresentative {category.upper()} mismatches (raw line, then parsed object)")
        for label, raw_row, row in bucket:
            print(f"  {label}")
            print(f"    raw line : {raw_row.line[:170]}")
            print(
                f"    raw read : form={raw_row.form_type!r} cik={raw_row.cik} "
                f"filed={raw_row.filed_at.isoformat()} path={raw_row.path!r}"
            )
            print(
                f"    parsed   : form={row.form_type!r} cik={row.cik} "
                f"filed={row.filed_at.isoformat()} path={row.path!r} "
                f"accession={row.accession!r} line={row.source_line}"
            )

    total_mismatches = sum(totals.mismatches.get(c, 0) for c in _MISMATCH_CATEGORIES)
    classification_damage = sum(totals.mismatches.get(c, 0) for c in _CLASSIFICATION_INPUTS)

    print()
    if classification_damage:
        print("STOP. Classification inputs disagree with the raw index:")
        for category in _CLASSIFICATION_INPUTS:
            print(f"  {category}: {totals.mismatches.get(category, 0):,}")
        print("The denominator requires investigation before any further work.")
        return 1
    if total_mismatches:
        print(f"GATE NOT PASSED: {total_mismatches:,} provenance mismatch(es).")
        print("No classification input is affected, but path/accession provenance is.")
        return 1
    if totals.raw_failures or totals.unverifiable:
        print(
            f"GATE PARTIAL: every one of the {totals.compared:,} independently readable rows "
            f"agreed on all five fields, but {totals.raw_failures:,} raw line(s) could not be "
            f"read by the auditor and {totals.unverifiable:,} parsed row(s) are therefore "
            "unverified. Those rows are listed above and are neither passed nor failed."
        )
        return 1
    print(
        f"EDGAR parser/classification-input integrity gate: PASSED. All five fields "
        f"agreed on every one of the {totals.compared:,} rows, checked against an "
        f"independent read of each raw line. The denominator does not require regeneration."
    )
    if totals.raw_corroborated:
        print(
            f"Qualifier, stated rather than buried: on {totals.raw_corroborated:,} of those "
            "rows the company name adjoins the CIK with no delimiter, so the auditor took "
            "the CIK from the path's own edgar/data/<cik>/ -- the same corroboration the "
            "parser uses. Their form type, date, path and accession are independently "
            "verified; their CIK is corroborated, not independently extracted."
        )
    return 0


def cmd_audit_exceptions(args: argparse.Namespace) -> int:
    """Dump, in full, every row the auditor could read and the parser did not emit.

    The corpus audit answers "how many"; a two-row exception needs "which, and
    why". For each such row this prints the raw line verbatim, the independent
    extraction, the parser's *own* skip verdict -- obtained by re-running the
    production row reader on that line rather than by reasoning about it from
    outside -- and every other index line carrying the same File Name together
    with whether that line was parsed.

    That last part is the question that decides whether an omission matters: a
    filing whose File Name appears on another line that *was* parsed contributes
    no evidence event that the corpus does not already have.
    """
    root = Path(args.index_root)
    wanted = set(args.quarter or [])
    files = sorted(root.glob("*/QTR*/form.idx"))
    if not files:
        print(f"no form.idx files under {root}")
        return 2

    exceptions = 0
    corroborated = 0

    for path_file in files:
        label = f"{path_file.parent.parent.name}-{path_file.parent.name}"
        if wanted and label not in wanted:
            continue
        text = path_file.read_text(encoding="latin-1")
        parsed = parse_index(text, quarter_label=label, strict=False)
        emitted = {row.source_line: row for row in parsed.rows}

        header: IndexHeader | None = parsed.header
        lines = text.splitlines()

        # Every line carrying each File Name, so a skipped row can be checked
        # against its siblings rather than judged alone.
        occurrences: dict[str, list[int]] = {}
        seen_separator = False
        candidates: dict[int, str] = {}
        for number, raw in enumerate(lines, start=1):
            line = raw.rstrip()
            if not line.strip():
                continue
            if _RAW_SEPARATOR.match(line):
                seen_separator = True
                continue
            if not seen_separator:
                continue
            candidates[number] = line
            found = _RAW_PATH.search(line)
            if found:
                occurrences.setdefault(found.group(1).strip(), []).append(number)

        for number, line in candidates.items():
            extracted = _raw_extract(line)
            if extracted is None:
                continue
            skipped = number not in emitted
            if not skipped and not extracted.cik_from_path:
                continue
            if extracted.cik_from_path:
                corroborated += 1
            if skipped:
                exceptions += 1

            kind = "SKIPPED BY PARSER" if skipped else "parsed (CIK corroborated by path)"
            print(f"\n{'=' * 78}\n{label} line {number}  --  {kind}\n{'=' * 78}")
            print(f"raw line:\n  {line!r}")
            print("\nindependently extracted:")
            print(f"  form_type    : {extracted.form_type!r}")
            print(f"  company_name : {extracted.company_name!r}")
            source = "  (from path)" if extracted.cik_from_path else ""
            print(f"  cik          : {extracted.cik}{source}")
            print(f"  filed_at     : {extracted.filed_at.isoformat()}")
            print(f"  path         : {extracted.path!r}")
            print(f"  accession    : {accession_from_path(extracted.path)!r}")

            if header is None:
                print("\nparser verdict: header unreadable for this file")
            else:
                row, reason = explain_row(line, header, quarter_label=label, source_line=number)
                if row is None:
                    print(f"\nparser verdict: SKIPPED, reason = {reason}")
                else:
                    print("\nparser verdict: parsed")
                    print(
                        f"  form_type={row.form_type!r} company_name={row.company_name!r} "
                        f"cik={row.cik} filed_at={row.filed_at.isoformat()}"
                    )
                    print(f"  path={row.path!r} accession={row.accession!r}")

            siblings = occurrences.get(extracted.path, [])
            others = [n for n in siblings if n != number]
            print(f"\nsame File Name on {len(siblings)} index line(s) in this quarter")
            if not others:
                print("  no other line carries this File Name")
            for other in others:
                state = "PARSED" if other in emitted else "skipped"
                print(f"  line {other}  [{state}]")
                print(f"    {candidates[other]!r}")
                sibling = emitted.get(other)
                if sibling is not None:
                    print(
                        f"    -> form_type={sibling.form_type!r} cik={sibling.cik} "
                        f"filed_at={sibling.filed_at.isoformat()} "
                        f"accession={sibling.accession!r}"
                    )

    print(f"\n{'=' * 78}")
    print(f"rows the auditor read and the parser did not emit : {exceptions}")
    print(f"rows whose CIK was corroborated from the path     : {corroborated}")
    if exceptions == 0:
        print("\nNothing to explain: the parser emitted a row for every readable line.")
    return 0


def cmd_cik_filings(args: argparse.Namespace) -> int:
    """Every indexed filing for one CIK, chronologically.

    ``verify-control`` aggregates; this enumerates. Aggregates are what let a
    registrant's rename, deregistration or post-operational afterlife hide
    inside a filing count, so the enumeration is a separate command rather than
    a flag nobody passes.

    Registrant-name changes are flagged where they occur, because the index
    records the name as filed and that transition is itself the evidence of
    when a rename was first reflected.
    """
    cik = int(args.cik)
    root = Path(args.index_root)
    rows = [
        row
        for path in sorted(root.glob("*/QTR*/form.idx"))
        for row in _index_rows(path)
        if row.cik == cik
    ]
    rows.sort(key=lambda r: (r.filed_at, r.accession))

    if not rows:
        print(f"no filings found for CIK {cik} in {root}")
        return 0

    print(f"CIK {cik}: {len(rows)} filings, {rows[0].filed_at} .. {rows[-1].filed_at}")
    print("(this is filings_total: every indexed row for this CIK, name-independent)\n")
    print(f"{'#':>3} {'filed':10s} {'form':12s} {'company name':34s} {'accession':22s} path")

    previous_name: str | None = None
    for index, row in enumerate(rows, start=1):
        if previous_name is not None and row.company_name != previous_name:
            print(
                f"    ---- indexed registrant name changes here: "
                f"{previous_name!r} -> {row.company_name!r} ----"
            )
        previous_name = row.company_name
        print(
            f"{index:>3} {row.filed_at.isoformat():10s} {row.form_type:12s} "
            f"{row.company_name[:34]:34s} {row.accession:22s} {row.path}"
        )

    names: dict[str, list[str]] = {}
    for row in rows:
        span = names.setdefault(row.company_name, [row.filed_at.isoformat(), ""])
        span[1] = row.filed_at.isoformat()
    print("\nindexed registrant names and the span each was used over")
    for name, (first, last) in names.items():
        print(f"  {name:40s} {first} .. {last}")
    print(
        "\nA name change in the index is the date the rename was first REFLECTED in a\n"
        "filing, which is not necessarily the date it took legal effect. And a shared\n"
        "CIK is not by itself evidence of a continuing issuer -- that needs a filing\n"
        "that says so."
    )
    return 0


def cmd_controls(args: argparse.Namespace) -> int:
    evidence = load_control_evidence(args.evidence)
    resolved = resolve_controls(evidence)

    if args.json:
        print(json.dumps([r.summary() for r in resolved], indent=2))
        return 0

    counts: dict[str, int] = {}
    for r in resolved:
        counts[str(r.status)] = counts.get(str(r.status), 0) + 1
    verified = counts.get(str(MappingStatus.MANUAL_VERIFIED), 0)
    source = evidence.source_path
    print(f"evidence file : {source}{'' if source and source.exists() else '  (absent)'}")
    print(f"controls      : {len(resolved)}   manual-verified: {verified}")
    print(f"by status     : {dict(sorted(counts.items()))}")
    print()

    if not args.diagnose:
        print(f"{'control':8s} {'status':16s} {'cik':>10s} {'ticker':8s}  class")
        for r in resolved:
            first = r.mappings[0]
            cik = "-" if first.cik is None else str(first.cik)
            extra = f"  (+{len(r.mappings) - 1} more issuer)" if len(r.mappings) > 1 else ""
            print(
                f"{r.control.ticker:8s} {r.status!s:16s} {cik:>10s} "
                f"{first.ticker or '-':8s}  {r.control.control_class}{extra}"
            )
    else:
        for r in resolved:
            print(f"── {r.control.ticker}  [{r.status}]  {r.control.control_class}")
            print(f"   expected : {r.control.name}")
            if r.identity_break:
                print("   IDENTITY BREAK: several issuers shared this ticker; never merged")
            for m in r.mappings:
                print(f"   · issuer   : {m.issuer_label}")
                print(f"     cik      : {m.cik if m.cik is not None else '-'}")
                print(f"     ticker   : {m.ticker or '-'}")
                print(f"     evidence : {m.evidence or '-'}")
                print(f"     citation : {m.citation or '-'}")
                if m.valid_from or m.valid_to:
                    print(f"     valid    : {m.valid_from or '?'} .. {m.valid_to or '?'}")
                if m.scope_notes:
                    print(f"     scope    : {m.scope_notes}")
                for fact in m.lifecycle_facts:
                    print(f"     lifecycle fact [{fact.scope}] {fact.date} ({fact.date_source})")
                    print(f"       {fact.fact}")
                    print(f"       cite: {fact.citation}")
                    if fact.note:
                        print(f"       note: {fact.note}")
                if m.unresolved_reason:
                    print(f"     UNRESOLVED REASON: {m.unresolved_reason}")
            print()

    pending = [r for r in resolved if r.status is not MappingStatus.MANUAL_VERIFIED]
    if pending:
        print(
            f"\n{len(pending)} of {len(resolved)} controls are not MANUAL_VERIFIED. "
            "Milestone 0b is not complete. No CIK is guessed."
        )
    return 0


def _index_rows(path: Path) -> list[FullIndexRow]:
    """Parse one quarterly index file, non-strict, for candidate gathering."""
    return parse_index(
        path.read_text(encoding="latin-1"),
        quarter_label=f"{path.parent.parent.name}-{path.parent.name}",
        strict=False,
    ).rows


def cmd_verify_control(args: argparse.Namespace) -> int:
    """Gather CIK candidates for one control from local primary sources.

    **This proposes; it never promotes.** Output is candidate evidence for a
    human to check and record. Name matches are reported as name matches, which
    can never on their own reach RESOLVED -- so a candidate printed here is a
    lead, not a mapping.
    """
    control = next((c for c in CONTROL_UNIVERSE if c.ticker.upper() == args.control.upper()), None)
    if control is None:
        print(f"unknown control {args.control!r}; not in the 30-control universe")
        return 2

    print(f"control        : {control.ticker}  ({control.control_class})")
    print(f"expected name  : {control.name}")
    print(f"expected event : {control.expected_event}")
    print(f"route          : {control.verification_route}")
    print()

    terms = [t for t in re.split(r"[^A-Z0-9&]+", control.name.upper()) if len(t) > 2][:2]
    if not terms:
        print("no usable search terms from the control name")
        return 2

    root = Path(args.index_root)
    files = sorted(root.glob("*/QTR*/form.idx"))

    # Pass 1: which CIKs does the NAME search reach? A name search is the only
    # way in, because a CIK is what we are trying to discover.
    matched: dict[int, int] = {}
    for path in files:
        for row in _index_rows(path):
            if all(term in row.company_name.upper() for term in terms):
                matched[row.cik] = matched.get(row.cik, 0) + 1

    print(f"searched {len(files)} quarterly index files for terms {terms}")
    if not matched:
        print("\nNO CANDIDATES. Record the control as UNRESOLVED with this as the reason.")
        return 0

    # Pass 2: for each candidate CIK, count EVERY row it has -- not only the
    # ones whose registrant name happened to match. Once a CIK is a candidate,
    # the CIK is the identity, and a registrant that renamed or was indexed
    # under a variant spelling still filed those documents. Counting only
    # name-matched rows under-reports the filing count AND narrows the reported
    # date range, which is the more dangerous half of the same bug.
    hits: dict[int, dict[str, Any]] = {}
    for path in files:
        for row in _index_rows(path):
            if row.cik not in matched:
                continue
            entry = hits.setdefault(
                row.cik,
                {
                    "names": set(),
                    "first": row.filed_at,
                    "last": row.filed_at,
                    "n": 0,
                    "forms": set(),
                },
            )
            entry["names"].add(row.company_name)
            entry["first"] = min(entry["first"], row.filed_at)
            entry["last"] = max(entry["last"], row.filed_at)
            entry["forms"].add(row.form_type)
            entry["n"] += 1

    print(f"\n{len(hits)} candidate CIK(s):\n")
    for cik, entry in sorted(hits.items(), key=lambda kv: -kv[1]["n"]):
        print(
            f"  CIK {cik}   filings_total={entry['n']}   "
            f"filings_name_matched={matched[cik]}   {entry['first']} .. {entry['last']}"
        )
        unmatched = sorted(
            n for n in entry["names"] if not all(term in n.upper() for term in terms)
        )
        for name in sorted(entry["names"]):
            flag = "  <- did NOT match the search terms" if name in unmatched else ""
            print(f"    name : {name}{flag}")
        if unmatched:
            print(
                f"    NOTE: filings_total - filings_name_matched = "
                f"{entry['n'] - matched[cik]}, filed under the name variant(s) flagged above"
            )
        print(f"    forms: {sorted(entry['forms'])[:12]}")
    print(
        "\nThese are NAME MATCHES over primary SEC index data. Name matching can never\n"
        "on its own reach RESOLVED -- it establishes a CIK candidate, not a ticker.\n"
        "To record a mapping you still need a ticker source: company_tickers.json for a\n"
        "currently listed issuer (sec_company_tickers), or a filing that states the symbol\n"
        "(filing_document_text, citation = accession)."
    )
    if len(hits) > 1:
        print(
            "\nSeveral candidates: unless a filing distinguishes them, the honest status\n"
            "is AMBIGUOUS."
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
    controls.add_argument("--evidence", default=None, help="path to control_identity_evidence.json")
    controls.add_argument(
        "--diagnose",
        action="store_true",
        help="per-issuer detail: cik, ticker, evidence, citation, unresolved reason",
    )
    controls.add_argument("--json", action="store_true")
    controls.set_defaults(func=cmd_controls)

    audit = edgar_sub.add_parser(
        "audit-paths", help="corpus-wide check that parsed paths match the raw index lines"
    )
    audit.add_argument("--index-root", required=True, help="directory of <year>/QTR<n>/form.idx")
    audit.set_defaults(func=cmd_audit_paths)

    exceptions = edgar_sub.add_parser(
        "audit-exceptions",
        help="explain, in full, every row the auditor read and the parser did not emit",
    )
    exceptions.add_argument(
        "--index-root", required=True, help="directory of <year>/QTR<n>/form.idx"
    )
    exceptions.add_argument(
        "--quarter",
        action="append",
        help="limit to one quarter label, e.g. 1997-QTR1; repeatable",
    )
    exceptions.set_defaults(func=cmd_audit_exceptions)

    filings = edgar_sub.add_parser(
        "cik-filings", help="enumerate every indexed filing for one CIK, chronologically"
    )
    filings.add_argument("cik", help="the CIK to enumerate, e.g. 1100683")
    filings.add_argument("--index-root", required=True, help="directory of <year>/QTR<n>/form.idx")
    filings.set_defaults(func=cmd_cik_filings)

    verify = edgar_sub.add_parser(
        "verify-control", help="gather CIK candidates for one control from the local index"
    )
    verify.add_argument("control", help="control id, e.g. AAPL")
    verify.add_argument("--index-root", required=True, help="directory of <year>/QTR<n>/form.idx")
    verify.set_defaults(func=cmd_verify_control)
