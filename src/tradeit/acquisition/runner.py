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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    SymbolStatus,
)
from tradeit.acquisition.cache import RawCache, sha256_bytes
from tradeit.acquisition.credits import CreditLedger
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.normalize import (
    ADJUSTED_COLUMNS,
    NormalizedRows,
    assign_instrument_ids,
)
from tradeit.acquisition.reconstruct import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    RECONSTRUCTION_COLUMNS,
    RECONSTRUCTION_LABEL,
    ReconstructionQuality,
    ReconstructionResult,
    reconstruct_symbol,
)
from tradeit.data.packages.manifest import (
    WORKSPACE_DIRNAME,
    Coverage,
    DatasetFile,
    PackageManifest,
    Provenance,
    file_digest,
    render_manifest,
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
    #: The run stopped because the plan's daily credits ran out. Everything
    #: acquired so far is written and importable; the universe is simply not
    #: complete yet. **Not a corrupt package** — re-running the same command
    #: after the allowance resets continues from here.
    INCOMPLETE_QUOTA = "ACQUISITION_INCOMPLETE_QUOTA"
    INVALID = "PACKAGE_INVALID"

    @property
    def snapshot_ready(self) -> bool:
        """Whether `tradeit data import` should be run against this package.

        A quota-truncated package is importable — the rows in it are real — but
        it covers fewer instruments than asked for, so importing it before the
        run is finished produces a snapshot nobody should cite as the universe.
        """
        return self is not PackageStatus.INVALID

    @property
    def is_complete(self) -> bool:
        return self in (PackageStatus.VALID, PackageStatus.VALID_WITH_WARNINGS)


@dataclass(frozen=True, slots=True)
class AcquisitionOptions:
    """Everything an operator can change about a run."""

    start: dt.date
    end: dt.date
    #: Re-fetch even when the raw cache already holds the response.
    force_refresh: bool = False
    #: Attempt only the requests a previous journal recorded as retryable.
    retry_failed_only: bool = False
    #: Overrides the provider's own declaration. Almost never right to set:
    #: what the price columns contain is a fact about the vendor, not a
    #: preference, and the provider is the thing that knows it.
    adjustment_policy: AdjustmentPolicyDeclaration | None = None
    #: Longest the runner will sleep for a rate or credit limit before giving
    #: up on pacing and letting the request fail. ``None`` means wait as long
    #: as the limit requires.
    max_wait_s: float | None = None
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
    symbol_status: SymbolStatus | None = None
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
    ledger: CreditLedger = field(default_factory=CreditLedger)
    #: Set when the run stopped because the plan's daily credits ran out.
    quota_stopped: bool = False
    #: Distinguishes "there is a problem with this package" from "this run was
    #: cut short and can be continued".
    problems_are_recoverable: bool = False
    notes: list[str] = field(default_factory=list)
    reconstruction: list[ReconstructionResult] = field(default_factory=list)
    capabilities: dict[str, str] = field(default_factory=dict)
    adjustment_policy: str = ""

    @property
    def successful(self) -> list[SymbolOutcome]:
        return [s for s in self.symbols if s.succeeded]

    @property
    def failed(self) -> list[SymbolOutcome]:
        return [s for s in self.symbols if not s.succeeded]

    @property
    def status(self) -> PackageStatus:
        if self.problems and not self.problems_are_recoverable:
            return PackageStatus.INVALID
        if not self.successful:
            return PackageStatus.INVALID
        if self.quota_stopped:
            # Everything acquired is real and importable. What is missing is
            # the rest of the universe, and the remedy is time rather than
            # repair — so this is its own state, not a failure.
            return PackageStatus.INCOMPLETE_QUOTA
        if self.failed or self.findings:
            return PackageStatus.VALID_WITH_WARNINGS
        return PackageStatus.VALID

    @property
    def symbols_remaining(self) -> list[str]:
        return sorted(s.symbol for s in self.failed)

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
            f"Adjustment           : {self.adjustment_policy or 'unknown'}",
            "",
            f"Requests fetched     : {self.fetched_requests:,}",
            f"Requests from cache  : {self.cached_requests:,}",
            *self.ledger.render(),
            *self._projection_lines(),
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
        if self.notes:
            lines += ["", "Notes", "-" * 72]
            lines += [f"  - {item}" for item in self.notes]
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

        if self.reconstruction:
            lines += ["", "Raw-price reconstruction (DERIVED, not vendor raw)", "-" * 72]
            lines.extend(f"  {entry.summary()}" for entry in self.reconstruction[:10])
            if len(self.reconstruction) > 10:
                lines.append(f"  ... and {len(self.reconstruction) - 10} more")

        if self.capabilities:
            lines += ["", "Provider capabilities as exercised", "-" * 72]
            for name, state in sorted(self.capabilities.items()):
                lines.append(f"  {name:<38} {state}")

        lines += ["", "Next", "-" * 72]
        if status is PackageStatus.INCOMPLETE_QUOTA:
            lines += [
                "  The daily credit allowance ran out. Nothing is wrong with what was",
                "  acquired — it is simply not the whole universe yet.",
                "",
                "  Wait for the allowance to reset, then run the SAME command again.",
                "  Already-downloaded symbols are read from disk and cost no credits.",
                "",
                f"  Symbols still needed: {len(self.failed)}",
            ]
        elif status.snapshot_ready:
            lines += [
                f"  tradeit data inspect {self.output}",
                f"  tradeit data import  {self.output}",
                "  tradeit validate --snapshot <id printed by import>",
            ]
        else:
            lines.append("  Fix the problems above and re-run. Nothing was imported.")
        return "\n".join(lines)

    def _projection_lines(self) -> list[str]:
        """What finishing the universe would cost, from measured usage.

        Measured rather than documented: the projection is a statement about
        this account and this plan. No wall-clock estimate is offered, because
        the only honest input to one would be the vendor's own throttling
        behaviour, which varies.
        """
        done = len(self.successful)
        remaining = len(self.failed)
        per_symbol = self.ledger.measured_cost_per_symbol(done)
        if per_symbol is None or not remaining:
            return []
        needed = round(per_symbol * remaining)
        return [
            f"Measured cost        : {per_symbol:.1f} credits per completed symbol",
            f"Estimated to finish  : ~{needed:,} more credits for {remaining} symbol(s)",
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "credits": self.ledger.to_payload(),
            "quota_stopped": self.quota_stopped,
            "capabilities": dict(self.capabilities),
            "adjustment_policy": self.adjustment_policy,
            "symbols_remaining": self.symbols_remaining,
            "reconstruction": [
                {
                    "symbol": item.symbol,
                    "quality": str(item.quality),
                    "sessions_changed": item.sessions_changed,
                    "sessions_total": item.sessions_total,
                    "splits": item.census.to_payload(),
                    "label": RECONSTRUCTION_LABEL,
                }
                for item in self.reconstruction
            ],
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
            "notes": list(self.notes),
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
    """One acquisition run against one provider.

    Knows nothing about any vendor's endpoints. It asks the provider for a plan,
    fetches each request through the raw cache, journals the outcome, hands
    successful outcomes back to the provider to normalize, accounts for credits,
    and writes the package. Adding a third vendor touches none of this.
    """

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
        self.ledger = CreditLedger(
            per_minute_allowance=getattr(provider, "credits_per_minute", None)
        )

    # -- the run -------------------------------------------------------------

    def run(self) -> AcquisitionReport:
        report = AcquisitionReport(
            provider=self.provider.name,
            options=self.options,
            output=self.output,
            started_at=dt.datetime.now(dt.UTC),
            missing_datasets=[str(k) for k in self.provider.missing_datasets()],
            ledger=self.ledger,
        )
        if not self.provider.has_credential():
            report.problems.append(
                f"no API key found. Set {self.provider.credential_env} and re-run. "
                "Nothing was requested."
            )
            report.limitations = list(self.provider.limitations())
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
        requests = self.provider.plan(targets, self.options.start, self.options.end)
        symbol_state: dict[str, SymbolOutcome] = {
            symbol: SymbolOutcome(symbol=symbol, instrument_id=self.ids[symbol])
            for symbol in targets
        }

        for request in requests:
            outcome = self._fetch(request, report)

            if outcome.status.stops_the_run:
                # The daily allowance is gone. Waiting will not help and every
                # further request would spend a retry budget on a certainty, so
                # the run stops here with everything acquired so far intact.
                self.ledger.daily_exhausted = True
                report.quota_stopped = True
                report.problems_are_recoverable = True
                report.notes.append(
                    f"stopped after {self.ledger.requests} request(s): "
                    f"{outcome.error or 'the daily credit allowance is exhausted'}"
                )
                break

            self._record_symbol_states(request, outcome, symbol_state)
            if not outcome.status.is_success:
                continue

            normalized = self.provider.normalize(outcome, self.ids)
            collected.extend(normalized)
            self._tally(request, normalized, symbol_state)

        report.symbols = [symbol_state[s] for s in targets]
        self._reconstruct(collected, report)
        self._write_package(collected, report)
        report.findings.extend(collected.findings)
        report.findings.extend(self._coverage_findings())
        # Read *after* the run, not before: a provider discovers what its plan
        # actually includes by being used, and limitations captured up front
        # would contradict the capability table three lines below them.
        report.limitations = list(self.provider.limitations())
        report.capabilities = {
            name: str(state) for name, state in self.provider.capabilities().items()
        }
        report.finished_at = dt.datetime.now(dt.UTC)
        report.raw_bytes = self.cache.total_bytes()
        report.raw_files = self.cache.file_count()
        return report

    def _targets(self) -> list[str]:
        if not self.options.retry_failed_only:
            return self.symbols
        wanted = {r.symbol for r in self.journal.failed_requests()}
        # Also retry anything the journal has never seen succeed: a run that
        # stopped at a quota wall never issued those requests at all, so they
        # are absent rather than failed.
        succeeded = {
            e.symbol
            for e in self.journal.entries
            if e.succeeded and e.dataset == str(AcquisitionDataset.DAILY_PRICES)
        }
        return [s for s in self.symbols if s in wanted or s not in succeeded]

    def _record_symbol_states(
        self,
        request: FetchRequest,
        outcome: FetchOutcome,
        state: dict[str, SymbolOutcome],
    ) -> None:
        """Attribute a batch outcome to its members individually.

        The point of per-symbol attribution: one unknown ticker in a batch of
        eight must not discard the other seven, and must not be recorded as a
        failure of all eight.
        """
        if request.dataset is not AcquisitionDataset.DAILY_PRICES:
            return
        for symbol in request.symbols:
            item = state.get(symbol)
            if item is None:
                continue
            item.prices = outcome.status
            symbol_status = outcome.per_symbol.get(symbol)
            if symbol_status is not None:
                item.symbol_status = symbol_status
                if symbol_status is not SymbolStatus.VALID and not item.error:
                    item.error = f"vendor reports {symbol_status}"
            elif not outcome.status.is_success and not item.error:
                item.error = outcome.error

    def _tally(
        self,
        request: FetchRequest,
        normalized: NormalizedRows,
        state: dict[str, SymbolOutcome],
    ) -> None:
        """Count what this response contributed, per symbol."""
        by_instrument: dict[str, SymbolOutcome] = {
            str(item.instrument_id): item for item in state.values()
        }
        for dataset, rows in normalized.rows.items():
            for row in rows:
                item = by_instrument.get(row.get("instrument_id", ""))
                if item is None:
                    continue
                if dataset is DatasetKind.DAILY_BARS:
                    item.bars += 1
                    session = dt.date.fromisoformat(row["session_date"])
                    item.first_session = min(item.first_session or session, session)
                    item.last_session = max(item.last_session or session, session)
                elif dataset is DatasetKind.SPLITS:
                    item.splits += 1
                elif dataset is DatasetKind.DIVIDENDS:
                    item.dividends += 1
        _ = request

    def _coverage_findings(self) -> list[str]:
        checker = getattr(self.provider, "coverage_findings", None)
        if checker is None:
            return []
        found = checker(self.options.start, self.options.end)
        return list(found)

    # -- reconstruction ------------------------------------------------------

    def _reconstruct(self, collected: NormalizedRows, report: AcquisitionReport) -> None:
        """Invert a vendor's split adjustment, where there is one to invert.

        Only runs when the provider declares its prices split-adjusted. For a
        raw-price vendor there is nothing to reconstruct, and producing a
        sidecar of identical numbers would imply otherwise.
        """
        policy = self.provider.adjustment_policy()
        if policy is not AdjustmentPolicyDeclaration.SPLIT_ADJUSTED:
            return

        splits_by_symbol = getattr(self.provider, "splits_by_symbol", {})
        support = getattr(self.provider, "support", {})
        split_support = support.get(AcquisitionDataset.SPLITS, CapabilitySupport.UNKNOWN)
        available = split_support in (
            CapabilitySupport.AVAILABLE,
            CapabilitySupport.EMPTY_VALID_RESPONSE,
        )
        reason = "" if available else f"the splits endpoint reported {split_support}"

        bars_by_instrument: dict[str, list[dict[str, str]]] = {}
        for row in collected.rows.get(DatasetKind.DAILY_BARS, []):
            bars_by_instrument.setdefault(row["instrument_id"], []).append(row)

        for symbol, instrument_id in self.ids.items():
            bars = bars_by_instrument.get(str(instrument_id))
            if not bars:
                continue
            result = reconstruct_symbol(
                symbol,
                sorted(bars, key=lambda r: r["session_date"]),
                splits_by_symbol.get(symbol, []),
                splits_available=available,
                unavailable_reason=reason,
                price_provider=self.provider.name,
                split_provider=self.provider.name,
            )
            report.reconstruction.append(result)

    # -- one request ---------------------------------------------------------

    def _fetch(self, request: FetchRequest, report: AcquisitionReport) -> FetchOutcome:
        """Fetch through the cache, pace on credits, journal the result."""
        if self.options.force_refresh:
            self.cache.discard(self.provider.name, request)

        entry = self.cache.get(self.provider.name, request)
        if entry is not None:
            raw = entry.read()
            rows, per_symbol = _replay(self.provider, request, raw)
            outcome = FetchOutcome(
                request=request,
                status=FetchStatus.CACHED,
                url=entry.url,
                raw=raw,
                rows=rows,
                per_symbol=per_symbol,
            )
            report.cached_requests += 1
            self.journal.record(
                self.provider.name,
                outcome,
                source_file=str(entry.path),
                sha256=entry.digest,
            )
            return outcome

        cost = self._cost_of(request)
        waited = self._pace(cost)

        fetched: FetchOutcome = self.provider.fetch(request)
        report.fetched_requests += 1
        self.ledger.record(fetched.credits, charged_fallback=cost)
        if waited:
            fetched = replace(fetched, waited_s=waited)

        source_file = ""
        digest = ""
        if fetched.status.is_success and fetched.raw:
            stored = self.cache.put(self.provider.name, request, fetched.raw, url=fetched.url)
            source_file = str(stored.path)
            digest = stored.digest
        self.journal.record(self.provider.name, fetched, source_file=source_file, sha256=digest)
        return fetched

    def _cost_of(self, request: FetchRequest) -> int:
        pricer = getattr(self.provider, "credits_for", None)
        return int(pricer(request)) if pricer else len(request.symbols)

    def _pace(self, cost: int) -> float:
        """Wait if spending ``cost`` now would breach the plan's allowance.

        Sleeping rather than failing: a per-minute wall is a delay, not an
        error, and turning it into one would abandon a run that only needed to
        breathe. The wait is recorded so a slow run is explicable.
        """
        delay = self.ledger.should_pause(cost)
        if delay <= 0:
            return 0.0
        if self.options.max_wait_s is not None and delay > self.options.max_wait_s:
            return 0.0
        time.sleep(delay)
        self.ledger.note_wait(delay)
        self.ledger.open_new_window()
        return delay

    def _policy(self) -> AdjustmentPolicyDeclaration:
        """What the package's price columns actually contain.

        The provider decides; the option only overrides. This is the field that
        stops split-adjusted prices being labelled raw, and getting it from the
        adapter rather than from a default is what makes that automatic for
        every future vendor.
        """
        override = self.options.adjustment_policy
        if override is not None:
            return override
        policy = self.provider.adjustment_policy()
        return (
            policy
            if isinstance(policy, AdjustmentPolicyDeclaration)
            else (AdjustmentPolicyDeclaration.UNKNOWN)
        )

    def _write_package(self, collected: NormalizedRows, report: AcquisitionReport) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        report.adjustment_policy = str(self._policy())
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

        reconstructed = [row for item in report.reconstruction for row in item.rows]
        if reconstructed:
            # A DERIVED artefact, deliberately outside the package's declared
            # files. It carries the vendor's original value, the factor, the
            # splits responsible and the algorithm version, so somebody who
            # disagrees with the method can redo it without re-downloading —
            # and so nothing here can be mistaken for a vendor price.
            path = self.workspace / "reconstructed_raw_prices.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_csv(path, reconstructed, list(RECONSTRUCTION_COLUMNS), compress=True)
            report.files[path.name] = path.stat().st_size

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
        path.write_text(render_manifest(manifest), encoding="utf-8")
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

        applied = [r for r in report.reconstruction if r.quality is ReconstructionQuality.APPLIED]
        provenance = Provenance(
            price_provider=self.provider.name,
            price_representation=str(self._policy()),
            # Same vendor for all three on a single-provider run. Written out
            # explicitly rather than left blank so that a package later enriched
            # by a second vendor shows a *change* in this table rather than a
            # field appearing from nowhere.
            dividend_provider=(
                self.provider.name if report.rows.get(str(DatasetKind.DIVIDENDS)) else ""
            ),
            split_provider=self.provider.name if report.rows.get(str(DatasetKind.SPLITS)) else "",
            reconstruction_performed=bool(applied),
            reconstruction_algorithm=RECONSTRUCTION_ALGORITHM_VERSION if applied else "",
            reconstruction_label=RECONSTRUCTION_LABEL if applied else "",
            reconstruction_file=(
                f"{WORKSPACE_DIRNAME}/reconstructed_raw_prices.csv.gz" if applied else ""
            ),
        )

        return PackageManifest(
            name=self.options.package_name or f"{self.provider.name}-daily",
            provider=self.provider.name,
            export_date=report.started_at.date(),
            provenance=provenance,
            coverage=Coverage(
                start=min(sessions) if sessions else self.options.start,
                end=max(sessions) if sessions else self.options.end,
                instruments=len(report.successful) or None,
            ),
            timezone=self.options.timezone,
            adjustment_policy=self._policy(),
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


def _replay(
    provider: Any, request: FetchRequest, raw: bytes
) -> tuple[tuple[dict[str, Any], ...], dict[str, SymbolStatus]]:
    """Re-decode a cached body through the provider that produced it.

    The provider owns the response shape, so replaying a cached file has to go
    through the same interpretation as a live one — otherwise a resumed run and
    a fresh run would disagree about the same bytes.

    A cached body that no longer decodes yields no rows rather than raising: the
    digest matched, so the file is intact, and the likely cause is an adapter
    change. That is a finding for the report, not a crash.
    """
    import json

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return (), {}

    interpreter = getattr(provider, "_rows_and_statuses", None)
    if interpreter is not None:
        rows, statuses = interpreter(request, decoded)
        return tuple(rows), dict(statuses)

    reader = getattr(provider, "_rows", None)
    if reader is not None:
        return tuple(reader(decoded)), {}
    if isinstance(decoded, list):
        return tuple(row for row in decoded if isinstance(row, dict)), {}
    return (), {}


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


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
