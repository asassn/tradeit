"""Orchestration: fetch, cache, journal, normalize, write, verify, report.

The shape of a run, and why each step is where it is:

1. **Plan.** Expand the universe or the explicit symbol list into requests.
   Metadata before prices, so a symbol the vendor does not know is discovered on
   a small request rather than after downloading fifteen years of nothing.
2. **Fetch, one request at a time**, through the raw cache. A cached entry is
   reused unless `--force-refresh`, which is what makes a rerun resume rather
   than restart.
3. **Journal every request** as it completes, not at the end. A process killed
   mid-run still leaves a complete record of everything before the kill.
4. **Normalize and stream to disk.** Rows are written per symbol rather than
   accumulated, so memory does not scale with the universe.
5. **Write the manifest** from what actually arrived — observed coverage, real
   row counts, real digests, the datasets that failed — never from what was
   requested.
6. **Verify**, by running the package's own validator. An acquisition that
   reports success without checking its output is a report about intentions.

**Three package statuses, because two would lie.** A universe download where one
delisted ticker 404s is not a failure — losing one name costs one test — but it
is also not a clean success, and reporting it as `VALID` would let a slow
erosion of coverage go unnoticed. `VALID_WITH_WARNINGS` is the honest middle,
and the failed symbols are listed by name so the erosion is visible.

**Nothing here fabricates a number.** Every count in the summary is measured
from the files that were written.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
)
from tradeit.acquisition.cache import RawCache, sha256_bytes
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.normalize import (
    ADJUSTED_COLUMNS,
    NormalizedRows,
    assign_instrument_ids,
    normalize_metadata,
    normalize_prices,
)
from tradeit.data.packages.manifest import (
    WORKSPACE_DIRNAME,
    Coverage,
    DatasetFile,
    PackageManifest,
    file_digest,
    verify_files,
)
from tradeit.data.packages.spec import DATASET_SPECS, AdjustmentPolicyDeclaration, DatasetKind
from tradeit.errors import ConfigError

#: Version of this tool, recorded in every manifest. Bumped when the acquisition
#: behaviour changes in a way that could make two packages differ over the same
#: vendor data.
ACQUISITION_TOOL_VERSION = "1.0.0"

#: Bytes per daily bar row in the written CSV, measured from real output and
#: used only for the pre-download estimate. Deliberately approximate; the
#: post-run report prints the actual sizes.
ESTIMATED_BYTES_PER_BAR = 60

#: Trading sessions per calendar year, for the same estimate.
SESSIONS_PER_YEAR = 252


class PackageStatus(StrEnum):
    """The three outcomes an acquisition can honestly report."""

    VALID = "PACKAGE_VALID"
    VALID_WITH_WARNINGS = "PACKAGE_VALID_WITH_WARNINGS"
    INVALID = "PACKAGE_INVALID"

    @property
    def snapshot_ready(self) -> bool:
        """Whether `tradeit data import` should be run against this package."""
        return self is not PackageStatus.INVALID


@dataclass(frozen=True, slots=True)
class AcquisitionOptions:
    """Everything an operator can change about a run."""

    start: dt.date
    end: dt.date
    #: Re-fetch even when the raw cache already holds the response.
    force_refresh: bool = False
    #: Attempt only the requests a previous journal recorded as retryable.
    retry_failed_only: bool = False
    #: Written into the manifest. `raw_unadjusted` is the truth for Tiingo's
    #: `open`/`high`/`low`/`close` columns, which is what the package stores.
    adjustment_policy: AdjustmentPolicyDeclaration = AdjustmentPolicyDeclaration.RAW_UNADJUSTED
    #: Compress the bar file. On by default: it is the only large file, and
    #: gzip streams where Parquet does not.
    compress_bars: bool = True
    package_name: str = ""
    timezone: str = "America/New_York"

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ConfigError(f"end {self.end} is before start {self.start}")


@dataclass(slots=True)
class SymbolOutcome:
    """Per-symbol result, for the report and for a later retry."""

    symbol: str
    instrument_id: int
    metadata: FetchStatus | None = None
    prices: FetchStatus | None = None
    bars: int = 0
    splits: int = 0
    dividends: int = 0
    first_session: dt.date | None = None
    last_session: dt.date | None = None
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.prices is not None and self.prices.is_success and self.bars > 0


@dataclass(slots=True)
class AcquisitionReport:
    """What the run did. Every number measured, none estimated."""

    provider: str
    options: AcquisitionOptions
    output: Path
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    symbols: list[SymbolOutcome] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    missing_datasets: list[str] = field(default_factory=list)
    files: dict[str, int] = field(default_factory=dict)
    raw_bytes: int = 0
    package_bytes: int = 0
    raw_files: int = 0
    manifest_path: Path | None = None
    observed_start: dt.date | None = None
    observed_end: dt.date | None = None
    cached_requests: int = 0
    fetched_requests: int = 0

    @property
    def successful(self) -> list[SymbolOutcome]:
        return [s for s in self.symbols if s.succeeded]

    @property
    def failed(self) -> list[SymbolOutcome]:
        return [s for s in self.symbols if not s.succeeded]

    @property
    def status(self) -> PackageStatus:
        if self.problems or not self.successful:
            return PackageStatus.INVALID
        if self.failed or self.findings:
            return PackageStatus.VALID_WITH_WARNINGS
        return PackageStatus.VALID

    def render(self) -> str:
        status = self.status
        if status is PackageStatus.INVALID and self.problems and not self.symbols:
            # Nothing was attempted, so the counts are all zero and the vendor
            # limitations are irrelevant. Printing them would bury the one line
            # that says what to do, which is the only line that matters here.
            return "\n".join(
                [
                    "ACQUISITION SUMMARY",
                    "=" * 72,
                    f"Package status       : {status}",
                    f"Provider             : {self.provider}",
                    "",
                    "Nothing was requested.",
                    "",
                    *[f"  {item}" for item in self.problems],
                    "",
                    "Fix the above and re-run.",
                ]
            )
        lines = [
            "ACQUISITION SUMMARY",
            "=" * 72,
            f"Package status       : {status}",
            f"Provider             : {self.provider}",
            f"Requested instruments: {len(self.symbols)}",
            f"Successful           : {len(self.successful)}",
            f"Failed               : {len(self.failed)}",
            "",
            f"Daily bars           : {self.rows.get(str(DatasetKind.DAILY_BARS), 0):,}",
            f"Splits               : {self.rows.get(str(DatasetKind.SPLITS), 0):,}",
            f"Dividends            : {self.rows.get(str(DatasetKind.DIVIDENDS), 0):,}",
            f"Instruments written  : {self.rows.get(str(DatasetKind.INSTRUMENTS), 0):,}",
            "",
            f"Coverage             : {self.observed_start} through {self.observed_end}",
            f"Requests fetched     : {self.fetched_requests:,}",
            f"Requests from cache  : {self.cached_requests:,}",
            "",
            f"Raw cache            : {_human(self.raw_bytes)} in {self.raw_files:,} files",
            f"Package              : {_human(self.package_bytes)} in {len(self.files)} files",
            f"Manifest             : {self.manifest_path}",
            f"Snapshot-ready       : {'YES' if status.snapshot_ready else 'NO'}",
        ]
        if self.failed:
            lines += ["", "Failed instruments", "-" * 72]
            for item in self.failed:
                reason = item.error or f"prices={item.prices}"
                lines.append(f"  {item.symbol:<10} {reason[:90]}")
            lines.append("")
            lines.append("  Retry only these with:  --retry-failed")
        if self.problems:
            # Before the findings and the limitations: these are the reasons the
            # package is unusable, and a reader scrolling past them to reach a
            # vendor caveat has been failed by the report.
            lines += ["", "Problems", "-" * 72]
            lines += [f"  - {item}" for item in self.problems]
        if self.findings:
            lines += ["", f"Findings ({len(self.findings)})", "-" * 72]
            lines += [f"  - {item}" for item in self.findings[:15]]
            if len(self.findings) > 15:
                lines.append(f"  ... and {len(self.findings) - 15} more; see the report file")
        if self.limitations:
            lines += ["", "Declared limitations (copied into the manifest)", "-" * 72]
            lines += [f"  - {item}" for item in self.limitations]

        lines += ["", "Next", "-" * 72]
        if status.snapshot_ready:
            lines += [
                f"  tradeit data inspect {self.output}",
                f"  tradeit data import  {self.output}",
                "  tradeit validate --snapshot <id printed by import>",
            ]
        else:
            lines.append("  Fix the problems above and re-run. Nothing was imported.")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "provider": self.provider,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "requested": [s.symbol for s in self.symbols],
            "successful": [s.symbol for s in self.successful],
            "failed": {s.symbol: (s.error or str(s.prices)) for s in self.failed},
            "rows": dict(self.rows),
            "files": dict(self.files),
            "raw_bytes": self.raw_bytes,
            "package_bytes": self.package_bytes,
            "observed_start": self.observed_start.isoformat() if self.observed_start else None,
            "observed_end": self.observed_end.isoformat() if self.observed_end else None,
            "findings": list(self.findings),
            "problems": list(self.problems),
            "limitations": list(self.limitations),
            "missing_datasets": list(self.missing_datasets),
            "tool_version": ACQUISITION_TOOL_VERSION,
        }


def estimate_size(symbols: Sequence[str], start: dt.date, end: dt.date) -> str:
    """A pre-download order of magnitude, clearly labelled as one."""
    years = max((end - start).days / 365.25, 0.0)
    bars = int(len(symbols) * years * SESSIONS_PER_YEAR)
    raw = bars * 220  # vendor JSON is roughly 3-4x the CSV it becomes
    package = bars * ESTIMATED_BYTES_PER_BAR
    return (
        f"~{bars:,} daily bars over {len(symbols)} symbols and {years:.1f} years. "
        f"Rough disk estimate: {_human(raw)} raw cache + {_human(package)} package "
        f"(uncompressed). Actual sizes are reported when the run finishes."
    )


class AcquisitionRunner:
    """One acquisition run against one provider."""

    def __init__(
        self,
        provider: Any,
        symbols: Sequence[str],
        output: Path,
        options: AcquisitionOptions,
    ) -> None:
        if not symbols:
            raise ConfigError("no symbols to acquire")
        self.provider = provider
        self.symbols = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        self.output = Path(output)
        self.options = options
        self.ids = assign_instrument_ids(self.symbols)
        # Inside the package, so one directory is the whole handoff. The name
        # is reserved by the package format precisely for this.
        self.workspace = self.output / WORKSPACE_DIRNAME
        self.cache = RawCache(self.workspace)
        self.journal = AcquisitionJournal(self.workspace / "journal.jsonl")

    # -- the run -------------------------------------------------------------

    def run(self) -> AcquisitionReport:
        report = AcquisitionReport(
            provider=self.provider.name,
            options=self.options,
            output=self.output,
            started_at=dt.datetime.now(dt.UTC),
            limitations=list(self.provider.limitations()),
            missing_datasets=[str(k) for k in self.provider.missing_datasets()],
        )
        if not self.provider.has_credential():
            report.problems.append(
                f"no API key found. Set {self.provider.credential_env} and re-run. "
                "Nothing was requested."
            )
            report.finished_at = dt.datetime.now(dt.UTC)
            return report

        targets = self._targets()
        if not targets:
            report.problems.append(
                "--retry-failed was given but the journal records no retryable "
                "failures. Either everything succeeded, or the failures were "
                "vendor rejections, which are not retried automatically."
            )
            report.finished_at = dt.datetime.now(dt.UTC)
            return report

        collected = NormalizedRows()
        for symbol in targets:
            outcome = self._acquire_symbol(symbol, collected, report)
            report.symbols.append(outcome)

        self._write_package(collected, report)
        report.findings.extend(collected.findings)
        report.finished_at = dt.datetime.now(dt.UTC)
        report.raw_bytes = self.cache.total_bytes()
        report.raw_files = self.cache.file_count()
        return report

    def _targets(self) -> list[str]:
        if not self.options.retry_failed_only:
            return self.symbols
        wanted = {r.symbol for r in self.journal.failed_requests()}
        return [s for s in self.symbols if s in wanted]

    # -- one symbol ----------------------------------------------------------

    def _acquire_symbol(
        self, symbol: str, collected: NormalizedRows, report: AcquisitionReport
    ) -> SymbolOutcome:
        instrument_id = self.ids[symbol]
        outcome = SymbolOutcome(symbol=symbol, instrument_id=instrument_id)

        meta_request = FetchRequest(dataset=AcquisitionDataset.SYMBOL_META, symbol=symbol)
        meta = self._fetch(meta_request, report)
        outcome.metadata = meta.status
        if meta.status.is_success and meta.rows:
            collected.extend(
                normalize_metadata(
                    symbol, instrument_id, meta.rows[0], default_start=self.options.start
                )
            )
        elif not meta.status.is_success:
            # Metadata is useful and not essential: a package with prices and a
            # placeholder instrument row is importable, one with neither is not.
            collected.extend(
                normalize_metadata(symbol, instrument_id, {}, default_start=self.options.start)
            )
            collected.findings.append(
                f"{symbol}: metadata unavailable ({meta.status}); the instrument row "
                "carries the ticker as its name and an UNKNOWN exchange"
            )

        price_request = FetchRequest(
            dataset=AcquisitionDataset.DAILY_PRICES,
            symbol=symbol,
            start=self.options.start,
            end=self.options.end,
        )
        prices = self._fetch(price_request, report)
        outcome.prices = prices.status
        if not prices.status.is_success:
            outcome.error = prices.error
            return outcome

        normalized = normalize_prices(symbol, instrument_id, prices.rows)
        collected.extend(normalized)
        outcome.bars = normalized.count(DatasetKind.DAILY_BARS)
        outcome.splits = normalized.count(DatasetKind.SPLITS)
        outcome.dividends = normalized.count(DatasetKind.DIVIDENDS)
        sessions = [
            dt.date.fromisoformat(row["session_date"])
            for row in normalized.rows.get(DatasetKind.DAILY_BARS, [])
        ]
        if sessions:
            outcome.first_session = min(sessions)
            outcome.last_session = max(sessions)
        elif prices.status is FetchStatus.EMPTY:
            outcome.error = "the vendor returned no rows for this symbol and date range"
        return outcome

    def _fetch(self, request: FetchRequest, report: AcquisitionReport) -> FetchOutcome:
        """Fetch through the cache, journal the result either way."""
        if self.options.force_refresh:
            self.cache.discard(self.provider.name, request)

        entry = self.cache.get(self.provider.name, request)
        if entry is not None:
            raw = entry.read()
            rows = _decode_rows(self.provider, raw)
            outcome = FetchOutcome(
                request=request,
                status=FetchStatus.CACHED,
                url=entry.url,
                raw=raw,
                rows=rows,
            )
            report.cached_requests += 1
            self.journal.record(
                self.provider.name,
                outcome,
                source_file=str(entry.path),
                sha256=entry.digest,
            )
            return outcome

        fetched: FetchOutcome = self.provider.fetch(request)
        report.fetched_requests += 1
        source_file = ""
        digest = ""
        if fetched.status.is_success and fetched.raw:
            stored = self.cache.put(self.provider.name, request, fetched.raw, url=fetched.url)
            source_file = str(stored.path)
            digest = stored.digest
        self.journal.record(self.provider.name, fetched, source_file=source_file, sha256=digest)
        return fetched

    # -- writing -------------------------------------------------------------

    def _write_package(self, collected: NormalizedRows, report: AcquisitionReport) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        files: list[DatasetFile] = []

        for dataset in (
            DatasetKind.INSTRUMENTS,
            DatasetKind.SYMBOL_MAPPINGS,
            DatasetKind.DAILY_BARS,
            DatasetKind.SPLITS,
            DatasetKind.DIVIDENDS,
            DatasetKind.DELISTINGS,
        ):
            rows = collected.rows.get(dataset, [])
            if not rows:
                continue
            compress = dataset is DatasetKind.DAILY_BARS and self.options.compress_bars
            name = f"{dataset}.csv.gz" if compress else f"{dataset}.csv"
            path = self.output / name
            columns = _columns_for(dataset, rows)
            _write_csv(path, rows, columns, compress=compress)
            report.rows[str(dataset)] = len(rows)
            report.files[name] = path.stat().st_size
            files.append(
                DatasetFile(
                    path=name,
                    dataset=dataset,
                    sha256=file_digest(path),
                    rows=len(rows),
                )
            )

        if collected.adjusted:
            # Not a package dataset: the package's prices are the raw ones. This
            # sidecar exists so our adjustment can be checked against the
            # vendor's rather than agreeing with it by construction.
            sidecar = self.workspace / "vendor_adjusted_prices.csv.gz"
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            _write_csv(sidecar, collected.adjusted, list(ADJUSTED_COLUMNS), compress=True)
            report.files[sidecar.name] = sidecar.stat().st_size

        if not files:
            report.problems.append("no dataset produced a single row, so no package was written")
            return

        sessions = [
            dt.date.fromisoformat(row["session_date"])
            for row in collected.rows.get(DatasetKind.DAILY_BARS, [])
        ]
        report.observed_start = min(sessions) if sessions else None
        report.observed_end = max(sessions) if sessions else None

        manifest = self._manifest(files, collected, report)
        path = self.output / "manifest.toml"
        path.write_text(_render_manifest(manifest), encoding="utf-8")
        report.manifest_path = path
        report.package_bytes = sum(p.stat().st_size for p in self.output.glob("*") if p.is_file())

        report.problems.extend(verify_files(manifest, self.output))

    def _manifest(
        self,
        files: list[DatasetFile],
        collected: NormalizedRows,
        report: AcquisitionReport,
    ) -> PackageManifest:
        sessions = [
            dt.date.fromisoformat(row["session_date"])
            for row in collected.rows.get(DatasetKind.DAILY_BARS, [])
        ]
        limitations = [
            *self.provider.limitations(),
            f"acquired by tradeit data acquire {ACQUISITION_TOOL_VERSION} on "
            f"{report.started_at.date().isoformat()}",
            "asset_class is not vendor-supplied and defaults to common_stock; ETFs "
            "and common shares are indistinguishable in this package",
            "listing_status is inferred from the vendor's last observation date, "
            "not from a delisting notice",
        ]
        missing = [k for k in self.provider.missing_datasets() if k in DATASET_SPECS]
        if missing:
            limitations.append(
                "datasets not acquired because this provider does not supply them: "
                + ", ".join(sorted(str(k) for k in missing))
            )
        if report.failed:
            limitations.append(
                f"{len(report.failed)} of {len(report.symbols)} requested instruments "
                "are absent: " + ", ".join(sorted(s.symbol for s in report.failed))
            )

        return PackageManifest(
            name=self.options.package_name or f"{self.provider.name}-daily",
            provider=self.provider.name,
            export_date=report.started_at.date(),
            coverage=Coverage(
                start=min(sessions) if sessions else self.options.start,
                end=max(sessions) if sessions else self.options.end,
                instruments=len(report.successful) or None,
            ),
            timezone=self.options.timezone,
            adjustment_policy=self.options.adjustment_policy,
            files=tuple(files),
            known_limitations=tuple(limitations),
            vendor_dataset=f"{self.provider.name}:daily",
            description=(
                f"Acquired {report.started_at.date().isoformat()} for empirical "
                f"validation. Requested {self.options.start} to {self.options.end}."
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns_for(dataset: DatasetKind, rows: Sequence[dict[str, str]]) -> list[str]:
    """Contract order first, then anything else the rows carry.

    Contract order rather than insertion order so two runs produce
    byte-identical files over identical data, which is what makes a digest
    comparison meaningful.
    """
    spec = DATASET_SPECS[dataset]
    known = [c.name for c in spec.columns]
    present = {key for row in rows for key in row}
    ordered = [name for name in known if name in present]
    extra = sorted(present - set(known))
    return ordered + extra


def _write_csv(
    path: Path, rows: Sequence[dict[str, str]], columns: Sequence[str], *, compress: bool
) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    payload = buffer.getvalue().encode("utf-8")
    if compress:
        # mtime=0 so the same rows produce the same bytes on every run; gzip
        # otherwise stamps the current time into the header and every digest
        # changes for no reason.
        with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as handle:
            handle.write(payload)
    else:
        path.write_bytes(payload)


def _decode_rows(provider: Any, raw: bytes) -> tuple[dict[str, Any], ...]:
    """Turn a cached response body back into rows using the provider's own reader.

    The provider owns the response shape, so replaying a cached file has to go
    through the same decoding as a live one. A cached body that no longer
    decodes yields no rows rather than raising: the digest matched, so the file
    is intact, and the likely cause is an adapter change — which is a finding
    for the report, not a crash.
    """
    import json

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    reader = getattr(provider, "_rows", None)
    if reader is None:
        return (
            tuple(row for row in decoded if isinstance(row, dict))
            if isinstance(decoded, list)
            else ()
        )
    return tuple(reader(decoded))


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _render_manifest(manifest: PackageManifest) -> str:
    """Write the manifest as TOML.

    Hand-rendered rather than via a TOML writer: the project has no TOML
    serializer dependency, the shape is fixed, and every top-level key must
    precede the first table header or TOML silently nests it.
    """
    lines = [
        "# Generated by `tradeit data acquire`. Every hash below was computed from",
        "# the file as written; re-running acquisition regenerates this file.",
        "",
        f"format_version = {manifest.format_version}",
        f'name = "{manifest.name}"',
        f'provider = "{manifest.provider}"',
        f"export_date = {manifest.export_date.isoformat()}",
        f'timezone = "{manifest.timezone}"',
        f'adjustment_policy = "{manifest.adjustment_policy}"',
        f'vendor_dataset = "{manifest.vendor_dataset}"',
        f"description = {_toml_string(manifest.description)}",
        "",
        "known_limitations = [",
        *[f"  {_toml_string(item)}," for item in manifest.known_limitations],
        "]",
        "",
        "[coverage]",
        f"start = {manifest.coverage.start.isoformat()}",
        f"end = {manifest.coverage.end.isoformat()}",
    ]
    if manifest.coverage.instruments is not None:
        lines.append(f"instruments = {manifest.coverage.instruments}")
    lines.append("")
    for item in manifest.files:
        lines += [
            "[[files]]",
            f'path = "{item.path}"',
            f'dataset = "{item.dataset}"',
            f'sha256 = "{item.sha256}"',
        ]
        if item.rows is not None:
            lines.append(f"rows = {item.rows}")
        lines.append("")
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


__all__ = [
    "ACQUISITION_TOOL_VERSION",
    "AcquisitionOptions",
    "AcquisitionReport",
    "AcquisitionRunner",
    "PackageStatus",
    "SymbolOutcome",
    "estimate_size",
    "sha256_bytes",
    "time",
]
