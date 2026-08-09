"""The import pipeline: manifest in, dated facts out, nothing lost on the way.

Order of operations, and why it is this order:

1. **Resolve and read the manifest.** A package that cannot describe itself is
   not imported at all. There is no "best effort" mode.
2. **Verify digests before reading a single row.** Reading first and checking
   later means a corrupt file has already produced rows by the time anyone
   knows, and partially-imported corrupt data is worse than none.
3. **Datasets in dependency order.** Instruments before bars, so that a report
   about unresolved instrument references is available while the bars are being
   read rather than after.
4. **Per row: normalize, validate, cross-check, date, write.** Each stage can
   send the row to quarantine with its own :class:`Stage` recorded. No stage can
   drop a row by any other route — that is the invariant this module exists to
   hold, and it is asserted by test rather than trusted.
5. **Snapshot identity last**, computed from the manifest digest and the
   observed counts, so that two imports of the same bytes produce the same
   snapshot id and an import of altered bytes cannot.

**The abort threshold.** An import whose quarantine rate exceeds
:attr:`ImportOptions.max_quarantine_rate` stops rather than continuing. A file
where one row in three is unreadable is a mapping mistake, not a data-quality
problem, and grinding through ten million of them to produce a report nobody
reads wastes the operator's afternoon. The threshold is generous, it applies
only after a minimum sample, and it is stated in the report either way.

**What this module will not do.** It will not repair a row by inference, will
not drop a row it cannot use, and will not assign a fundamental fact a
knowledge_time on its period-end date. The first two are enforced here; the
third is enforced in :mod:`tradeit.data.packages.pointintime`, which has no
setting that relaxes it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tradeit.data.packages.manifest import (
    DatasetFile,
    PackageManifest,
    file_digest,
    load_manifest,
    verify_files,
)
from tradeit.data.packages.normalize import (
    CoercionError,
    NormalizationPolicy,
    coerce,
    is_null,
)
from tradeit.data.packages.pointintime import (
    REFERENCE_DATASETS,
    REFERENCE_SNAPSHOT_CAVEAT,
    KnowledgeTimePolicy,
    PointInTimeError,
    assign_knowledge_time,
    describe_policy,
)
from tradeit.data.packages.readers import RawRow, peek_header, read_rows, resolve_package
from tradeit.data.packages.sinks import CountingSink, RecordSink
from tradeit.data.packages.spec import (
    DATASET_SPECS,
    DatasetKind,
    ValidationCapability,
    capabilities_for,
)
from tradeit.data.packages.stages import (
    Correction,
    DatasetOutcome,
    NormalizedRecord,
    QuarantineEntry,
    RawRecord,
    Stage,
    ValidatedRecord,
)
from tradeit.data.packages.validate import (
    SeriesCheckConfig,
    SeriesState,
    ValidationFailure,
    combine,
    validate_row,
)
from tradeit.errors import DataError

#: Reference data before the facts that point at it. Datasets absent from a
#: package are skipped; datasets present but unlisted here import last, in
#: manifest order, which is the safe default for a kind added later.
IMPORT_ORDER: tuple[DatasetKind, ...] = (
    DatasetKind.EXCHANGES,
    DatasetKind.INSTRUMENTS,
    DatasetKind.SYMBOL_MAPPINGS,
    DatasetKind.SECTORS,
    DatasetKind.UNIVERSE_MEMBERSHIP,
    DatasetKind.DELISTINGS,
    DatasetKind.SPLITS,
    DatasetKind.DIVIDENDS,
    DatasetKind.CORPORATE_ACTIONS,
    DatasetKind.DAILY_BARS,
    DatasetKind.INTRADAY_BARS,
    DatasetKind.FILINGS,
    DatasetKind.EARNINGS,
    DatasetKind.FUNDAMENTALS,
)

#: Below this many rows, the quarantine rate is noise and the abort check is
#: skipped. Ten bad rows out of twelve says nothing; ten thousand out of twelve
#: thousand says the columns are mapped wrong.
ABORT_MINIMUM_ROWS = 500


@dataclass(frozen=True, slots=True)
class ImportOptions:
    """Everything an operator can change about an import run."""

    #: Execute every stage and write nothing. The default sink is already
    #: counting-only, so a dry run differs from a real one solely in the sink.
    dry_run: bool = False
    #: Re-hash every file and compare with the manifest. Leave on. The only
    #: reason to turn it off is a package too large to hash twice in a session,
    #: and the report records that the check was skipped.
    verify_digests: bool = True
    max_quarantine_rate: float = 0.05
    series: SeriesCheckConfig = field(default_factory=SeriesCheckConfig)
    knowledge: KnowledgeTimePolicy = field(default_factory=KnowledgeTimePolicy)
    #: Stop after this many rows per file. For smoke-testing a large package;
    #: an import run with a limit is marked partial and its snapshot id carries
    #: a ``partial`` marker so it can never be mistaken for a full one.
    limit_rows: int | None = None
    #: Datasets to import; empty means everything the manifest declares.
    only: tuple[DatasetKind, ...] = ()

    def __post_init__(self) -> None:
        if not 0 < self.max_quarantine_rate <= 1:
            raise DataError("max_quarantine_rate must be in (0, 1]")
        if self.limit_rows is not None and self.limit_rows <= 0:
            raise DataError("limit_rows must be positive when set")


@dataclass(slots=True)
class ImportReport:
    """What happened, in enough detail to act on.

    Deliberately holds counters and problem strings rather than the rows: a
    report that held the data could not describe an import too large to hold.
    """

    manifest: PackageManifest
    root: Path
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    outcomes: dict[DatasetKind, DatasetOutcome] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    capability: ValidationCapability | None = None
    aborted: bool = False
    abort_reason: str = ""
    partial: bool = False
    digests_verified: bool = True
    observed_start: dt.date | None = None
    observed_end: dt.date | None = None

    @property
    def rows_read(self) -> int:
        return sum(o.rows_read for o in self.outcomes.values())

    @property
    def rows_written(self) -> int:
        return sum(o.rows_point_in_time for o in self.outcomes.values())

    @property
    def rows_quarantined(self) -> int:
        return sum(o.rows_quarantined for o in self.outcomes.values())

    @property
    def rows_estimated(self) -> int:
        return sum(o.estimated_knowledge_time for o in self.outcomes.values())

    @property
    def quarantine_rate(self) -> float:
        return self.rows_quarantined / self.rows_read if self.rows_read else 0.0

    @property
    def snapshot_id(self) -> str:
        """Identity of exactly this import of exactly these bytes.

        Combines the manifest digest — which already covers every file's
        SHA-256 — with the observed per-dataset counts. The counts are included
        because a run that aborted halfway read the same bytes as one that
        finished, and the two must not share an id.
        """
        payload = json.dumps(
            {
                "manifest": self.manifest.digest(),
                "counts": {str(k): v.rows_point_in_time for k, v in sorted(self.outcomes.items())},
                "aborted": self.aborted,
                "partial": self.partial,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        suffix = "-partial" if self.partial or self.aborted else ""
        return f"{self.manifest.name}-{digest[:16]}{suffix}"

    def outcome(self, dataset: DatasetKind) -> DatasetOutcome:
        if dataset not in self.outcomes:
            self.outcomes[dataset] = DatasetOutcome(dataset=dataset)
        return self.outcomes[dataset]

    # -- rendering -----------------------------------------------------------

    def render(self) -> str:
        lines = [
            f"Import report — {self.manifest.name} ({self.manifest.provider})",
            "=" * 72,
            f"snapshot id     : {self.snapshot_id}",
            f"package root    : {self.root}",
            f"export date     : {self.manifest.export_date}",
            f"declared cover  : {self.manifest.coverage.start} .. {self.manifest.coverage.end}",
            f"observed cover  : {self.observed_start} .. {self.observed_end}",
            f"adjustment      : {self.manifest.adjustment_policy}",
            f"digests verified: {'yes' if self.digests_verified else 'NO — check skipped'}",
            "",
            f"rows read       : {self.rows_read:,}",
            f"rows imported   : {self.rows_written:,}",
            f"rows quarantined: {self.rows_quarantined:,} ({self.quarantine_rate:.2%})",
            f"estimated times : {self.rows_estimated:,}",
            "",
        ]
        if self.aborted:
            lines += ["ABORTED", f"  {self.abort_reason}", ""]
        if self.partial:
            lines += [
                "PARTIAL — a row limit was in force. This snapshot is a smoke test "
                "and must not be used as evidence.",
                "",
            ]

        lines.append("Per dataset")
        lines.append("-" * 72)
        for kind in sorted(self.outcomes, key=str):
            out = self.outcomes[kind]
            lines.append(
                f"  {kind:<22} read {out.rows_read:>9,}  imported {out.rows_point_in_time:>9,}"
                f"  quarantined {out.rows_quarantined:>7,}  corrected {out.rows_corrected:>7,}"
            )
            for reason, count in sorted(out.quarantine_reasons.items(), key=lambda kv: -kv[1])[:5]:
                lines.append(f"      quarantine: {count:>7,}  {reason[:90]}")
            for flag, count in sorted(out.flags.items(), key=lambda kv: -kv[1]):
                lines.append(f"      flag      : {count:>7,}  {flag}")
            if out.estimated_knowledge_time:
                lines.append(
                    f"      NOTE      : {out.estimated_knowledge_time:,} rows carry an "
                    "ESTIMATED knowledge_time derived from a filing-deadline rule. "
                    "Results computed from them measure the rule as well as the market."
                )

        if self.manifest.known_limitations:
            lines += ["", "Declared limitations (from the manifest)", "-" * 72]
            lines += [f"  - {item}" for item in self.manifest.known_limitations]

        if self.capability is not None:
            lines += ["", "Validation capability", "-" * 72, self.capability.render()]

        if self.problems:
            lines += ["", "Problems", "-" * 72]
            lines += [f"  - {item}" for item in self.problems]
        if self.notes:
            lines += ["", "Notes", "-" * 72]
            lines += [f"  - {item}" for item in self.notes]

        lines += ["", describe_policy_block()]
        return "\n".join(lines)

    def to_payload(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "manifest": self.manifest.to_payload(),
            "root": str(self.root),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_quarantined": self.rows_quarantined,
            "rows_estimated": self.rows_estimated,
            "quarantine_rate": round(self.quarantine_rate, 6),
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "partial": self.partial,
            "digests_verified": self.digests_verified,
            "observed_start": self.observed_start.isoformat() if self.observed_start else None,
            "observed_end": self.observed_end.isoformat() if self.observed_end else None,
            "datasets": {str(k): v.to_payload() for k, v in sorted(self.outcomes.items())},
            "problems": list(self.problems),
            "notes": list(self.notes),
        }


def describe_policy_block() -> str:
    return (
        "Stages: raw -> normalized -> validated -> point-in-time. Raw files are "
        "unmodified on disk and digest-verified; every deviation from the source "
        "text is recorded as a correction; every unusable row is quarantined "
        "with its file, line, stage and reason. No row is discarded."
    )


class PackageImporter:
    """Runs one package through the pipeline."""

    def __init__(
        self,
        manifest: PackageManifest,
        root: Path,
        *,
        options: ImportOptions | None = None,
        sink: RecordSink | None = None,
    ) -> None:
        self.manifest = manifest
        self.root = root
        self.options = options or ImportOptions()
        self.sink: RecordSink = sink or CountingSink()
        self.normalization = NormalizationPolicy(
            date_formats=manifest.date_formats,
            timezone=manifest.timezone,
            decimal_comma=manifest.decimal_comma,
            thousands_comma=not manifest.decimal_comma,
        )

    # -- orchestration -------------------------------------------------------

    def run(self) -> ImportReport:
        report = ImportReport(
            manifest=self.manifest,
            root=self.root,
            started_at=dt.datetime.now(dt.UTC),
            partial=self.options.limit_rows is not None,
            digests_verified=self.options.verify_digests,
        )
        report.capability = capabilities_for(self.manifest.datasets)

        for dataset, missing in self.manifest.missing_dependencies().items():
            report.notes.append(
                f"{dataset} is present but {', '.join(str(m) for m in missing)} is not; "
                f"{DATASET_SPECS[dataset].summary.lower()} will not resolve against "
                "reference data."
            )
        if not self.manifest.adjustment_policy.is_usable_for_patterns:
            report.problems.append(
                "adjustment_policy is UNKNOWN. Pattern geometry drawn through an "
                "unknown adjustment is not interpretable; the package can be "
                "imported but must not be used as validation evidence."
            )

        if self.options.verify_digests:
            problems = verify_files(self.manifest, self.root)
            if problems:
                report.problems.extend(problems)
                report.aborted = True
                report.abort_reason = (
                    "file verification failed; no rows were read. A file whose bytes "
                    "do not match the manifest cannot be attributed to this package."
                )
                report.finished_at = dt.datetime.now(dt.UTC)
                return report
        else:
            report.notes.append(
                "digest verification was skipped by request; the rows below are not "
                "provably the bytes the manifest describes"
            )

        for dataset in self.manifest.datasets:
            if dataset in REFERENCE_DATASETS:
                report.notes.append(f"{dataset}: {REFERENCE_SNAPSHOT_CAVEAT}")

        for dataset in self._ordered_datasets():
            for spec_file in self.manifest.files_for(dataset):
                self._import_file(spec_file, report)
                self.sink.flush()
                if report.aborted:
                    report.finished_at = dt.datetime.now(dt.UTC)
                    return report

        report.finished_at = dt.datetime.now(dt.UTC)
        self._check_coverage(report)
        return report

    def _ordered_datasets(self) -> list[DatasetKind]:
        present = list(self.manifest.datasets)
        if self.options.only:
            present = [d for d in present if d in self.options.only]
        ranked = sorted(
            present,
            key=lambda d: IMPORT_ORDER.index(d) if d in IMPORT_ORDER else len(IMPORT_ORDER),
        )
        return ranked

    # -- one file ------------------------------------------------------------

    def _import_file(self, spec_file: DatasetFile, report: ImportReport) -> None:
        dataset = spec_file.dataset
        outcome = report.outcome(dataset)
        outcome.files += 1
        path = self.root / spec_file.path
        spec = DATASET_SPECS[dataset]

        header = peek_header(path)
        self._report_mapping(spec_file, header, report)

        series = SeriesState(config=self.options.series, export_date=self.manifest.export_date)
        limit = self.options.limit_rows

        for line_number, row in enumerate(read_rows(path), start=2):
            if limit is not None and outcome.rows_read >= limit:
                report.notes.append(f"{spec_file.path}: stopped at the {limit:,}-row limit")
                break
            outcome.rows_read += 1
            raw = RawRecord(
                dataset=dataset,
                source_file=spec_file.path,
                line_number=line_number,
                values=row,
            )
            self._process(raw, spec_file, series, outcome, report)

            if self._should_abort(outcome):
                report.aborted = True
                report.abort_reason = (
                    f"{spec_file.path}: {outcome.rows_quarantined:,} of "
                    f"{outcome.rows_read:,} rows quarantined "
                    f"({outcome.quarantine_rate:.1%}), above the "
                    f"{self.options.max_quarantine_rate:.0%} threshold. A rate this "
                    "high is a column mapping or a format mismatch rather than "
                    "dirty data; check the mapping report above and re-run."
                )
                return

        if spec_file.rows is not None and outcome.rows_read != spec_file.rows and limit is None:
            report.problems.append(
                f"{spec_file.path}: manifest declares {spec_file.rows:,} rows, "
                f"file has {outcome.rows_read:,}"
            )
        _ = spec  # kept for readability of the mapping report above

    def _report_mapping(
        self, spec_file: DatasetFile, header: Sequence[str], report: ImportReport
    ) -> None:
        spec = DATASET_SPECS[spec_file.dataset]
        present = set(header)
        wanted = {c.name: spec_file.source_column(c.name) for c in spec.columns}
        missing_required = sorted(
            name
            for name, source in wanted.items()
            if source not in present and (spec.column(name) and spec.column(name).required)  # type: ignore[union-attr]
        )
        if missing_required:
            report.problems.append(
                f"{spec_file.path}: required field(s) {missing_required} are not in the "
                f"file. The file has {sorted(present)}; the manifest maps "
                f"{ {k: v for k, v in wanted.items() if k in missing_required} }. "
                "Every row will quarantine until this is fixed."
            )
        unused = sorted(present - set(wanted.values()))
        if unused:
            report.notes.append(
                f"{spec_file.path}: columns {unused} are in the file but not in the "
                f"{spec_file.dataset} contract; they are preserved in the raw file "
                "and not imported."
            )

    # -- one row -------------------------------------------------------------

    def _process(
        self,
        raw: RawRecord,
        spec_file: DatasetFile,
        series: SeriesState,
        outcome: DatasetOutcome,
        report: ImportReport,
    ) -> None:
        try:
            normalized = self._normalize(raw, spec_file)
        except CoercionError as error:
            self._quarantine(raw, Stage.NORMALIZED, str(error), outcome)
            return
        outcome.rows_normalized += 1
        if normalized.corrections:
            outcome.rows_corrected += 1
            outcome.corrections += len(normalized.corrections)
            for correction in normalized.corrections:
                self.sink.correction(raw, correction)

        try:
            flags = validate_row(normalized)
            series_flags = series.check(normalized)
        except ValidationFailure as error:
            self._quarantine(raw, Stage.VALIDATED, str(error), outcome)
            return
        validated = ValidatedRecord(normalized=normalized, flags=combine(flags, series_flags))
        outcome.rows_validated += 1
        outcome.note_flags(validated.flags)

        try:
            dated = assign_knowledge_time(
                validated,
                self.options.knowledge,
                export_date=self.manifest.export_date,
            )
        except PointInTimeError as error:
            self._quarantine(raw, Stage.POINT_IN_TIME, str(error), outcome)
            return
        outcome.rows_point_in_time += 1
        if dated.is_estimated:
            outcome.estimated_knowledge_time += 1

        self._observe_coverage(normalized, report)
        if not self.options.dry_run:
            self.sink.write(dated)

    def _normalize(self, raw: RawRecord, spec_file: DatasetFile) -> NormalizedRecord:
        spec = DATASET_SPECS[raw.dataset]
        values: dict[str, object] = {}
        corrections: list[Correction] = []

        for column in spec.columns:
            source = spec_file.source_column(column.name)
            text = raw.values.get(source)
            if is_null(text, self.normalization):
                if column.required:
                    raise CoercionError(
                        f"{column.name} (from column {source!r}) is empty, and the "
                        f"{raw.dataset} contract requires it: {column.description}"
                    )
                values[column.name] = None
                continue
            assert text is not None  # narrowed by is_null
            value, made = coerce(column.kind, text, column.name, self.normalization)
            values[column.name] = value
            corrections.extend(made)

        if spec_file.timeframe is not None and "timeframe" not in values:
            values["timeframe"] = spec_file.timeframe
        return NormalizedRecord(raw=raw, values=values, corrections=tuple(corrections))

    def _quarantine(
        self, raw: RawRecord, stage: Stage, reason: str, outcome: DatasetOutcome
    ) -> None:
        entry = QuarantineEntry.of(
            raw,
            stage=stage,
            reason=reason,
            identifier=_identifier(raw.values),
        )
        outcome.note_quarantine(entry)
        self.sink.quarantine(entry)

    def _should_abort(self, outcome: DatasetOutcome) -> bool:
        return (
            outcome.rows_read >= ABORT_MINIMUM_ROWS
            and outcome.quarantine_rate > self.options.max_quarantine_rate
        )

    # -- coverage ------------------------------------------------------------

    def _observe_coverage(self, record: NormalizedRecord, report: ImportReport) -> None:
        for name in ("session_date", "ex_date", "period_end", "valid_from"):
            value = record.values.get(name)
            if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
                if report.observed_start is None or value < report.observed_start:
                    report.observed_start = value
                if report.observed_end is None or value > report.observed_end:
                    report.observed_end = value
                return

    def _check_coverage(self, report: ImportReport) -> None:
        declared = self.manifest.coverage
        if report.observed_start is None or report.observed_end is None:
            return
        if report.observed_start > declared.start or report.observed_end < declared.end:
            report.problems.append(
                f"declared coverage {declared.start}..{declared.end} but the data spans "
                f"{report.observed_start}..{report.observed_end}. The difference is not "
                "cosmetic: a validation that claims to cover a bear market and does not "
                "is a different result from the one it reports."
            )


def _identifier(values: RawRow) -> str | None:
    for key in ("instrument_id", "ticker", "symbol", "cik"):
        value = values.get(key)
        if value:
            return value[:64]
    return None


def import_package(
    source: Path,
    *,
    options: ImportOptions | None = None,
    sink: RecordSink | None = None,
    workspace: Path | None = None,
) -> ImportReport:
    """Import a package directory or archive and return its report."""
    root = resolve_package(source, workspace)
    manifest = load_manifest(root / "manifest.toml")
    importer = PackageImporter(manifest, root, options=options, sink=sink)
    return importer.run()


def inspect_package(source: Path, workspace: Path | None = None) -> str:
    """Describe a package without importing it.

    The first thing to run against an unfamiliar export: it verifies the
    digests, states what the manifest claims, and lists which validations the
    declared datasets do and do not enable — before anyone waits for a
    ten-million-row read.
    """
    root = resolve_package(source, workspace)
    manifest = load_manifest(root / "manifest.toml")
    problems = verify_files(manifest, root)
    capability = capabilities_for(manifest.datasets)

    lines = [
        f"Package: {manifest.name} ({manifest.provider})",
        f"  format version : {manifest.format_version}",
        f"  export date    : {manifest.export_date}",
        f"  coverage       : {manifest.coverage.start} .. {manifest.coverage.end}"
        + (
            f" over {manifest.coverage.instruments:,} instruments"
            if manifest.coverage.instruments
            else ""
        ),
        f"  timezone       : {manifest.timezone}",
        f"  adjustment     : {manifest.adjustment_policy}",
        f"  manifest digest: {manifest.digest()}",
        f"  snapshot id    : {manifest.snapshot_id()}",
        "",
        "Files",
        "-" * 72,
    ]
    for item in manifest.files:
        path = root / item.path
        size = f"{path.stat().st_size / 1e6:.1f} MB" if path.exists() else "MISSING"
        lines.append(f"  {item.dataset:<22} {item.path:<40} {size}")
    lines += ["", capability.render()]
    if manifest.known_limitations:
        lines += ["", "Declared limitations", "-" * 72]
        lines += [f"  - {item}" for item in manifest.known_limitations]
    lines += ["", "Verification", "-" * 72]
    lines += [f"  - {item}" for item in problems] if problems else ["  all files verified"]
    lines += ["", describe_policy(KnowledgeTimePolicy())]
    return "\n".join(lines)


def iter_rows(root: Path, spec_file: DatasetFile) -> Iterator[RawRow]:
    """Raw rows of one declared file. Exposed for diagnostics."""
    yield from read_rows(root / spec_file.path)


def rehash(root: Path, spec_file: DatasetFile) -> str:
    """Current digest of a declared file, for regenerating a stale manifest."""
    return file_digest(root / spec_file.path)


__all__ = [
    "ABORT_MINIMUM_ROWS",
    "IMPORT_ORDER",
    "ImportOptions",
    "ImportReport",
    "PackageImporter",
    "import_package",
    "inspect_package",
    "iter_rows",
    "rehash",
]
