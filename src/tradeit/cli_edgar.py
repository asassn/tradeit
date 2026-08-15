"""The ``tradeit edgar`` command group.

Three commands, in the order an operator uses them::

    tradeit edgar fetch-recipe                 # how to populate the index dir
    tradeit edgar inspect-index FILE.idx        # diagnose one file's parse rate
    tradeit edgar denominator --index-root DIR # build and report
    tradeit edgar controls --diagnose          # control identity + citations
    tradeit edgar verify-control AAPL ...      # gather CIK candidates for one control
    tradeit edgar cik-filings 1100683 ...      # every filing for one CIK, chronologically

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
from pathlib import Path
from typing import Any

from tradeit.edgar.control_evidence import load_control_evidence, resolve_controls
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.identity import MappingStatus
from tradeit.edgar.index import FETCH_RECIPE, FullIndexRow, IndexQuarter, parse_index
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
