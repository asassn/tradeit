"""Adding one vendor's corporate actions to another vendor's price package.

**Why this exists.** Twelve Data supplies daily bars that are split-adjusted, so
the raw exchange prints have to be reconstructed by inverting the adjustment —
and inverting it requires a split schedule. On the plan this project actually
holds, Twelve Data's ``/splits`` endpoint answers with a plan restriction. That
is not "this stock never split"; it is "we are not allowed to ask", and the
acquisition code already refuses to conflate the two. The consequence is a
package whose reconstruction is honestly marked *not attempted*. FMP serves a
split history on a free account, so the missing input is obtainable — from a
different vendor.

**Why it is a separate pass rather than a step inside acquisition.** Price
acquisition on a free plan spans days: the daily credit allowance runs out, the
run stops cleanly, and the operator resumes tomorrow. A package therefore exists
in a usable-but-incomplete state for a long time, and the split schedule for the
symbols already downloaded is useful immediately. Making enrichment a pass over
an *existing package directory* means it can run at any point, repeatedly,
without re-downloading a single bar. ``tradeit data enrich`` is the primitive;
``tradeit data acquire --split-provider fmp`` runs the same pass straight after
acquisition for the common case.

**The seam is deliberately small.** A :class:`CorporateActionSource` answers one
question — *what splits does this vendor hold for this symbol?* — and returns a
:class:`CorporateActionLookup` that distinguishes "none recorded" from "not on
your plan" from "we could not ask". That is the whole interface. It is not a
provider-composition framework: there is no merge strategy registry, no priority
DSL, no plan negotiation. When a second enrichment vendor or a dividend source
arrives, it implements one method.

**Cross-vendor facts stay separated.** The reconstructed series is computed from
one vendor's adjusted bars and another vendor's split schedule, which means it
was supplied by **neither**. Every reconstructed row carries both provider names,
the manifest gains a ``[provenance]`` table naming both, and the values keep the
label ``RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED``. A consumer that wants to know
where a number came from is never left inferring it from the package's top-level
``provider`` field.

**Disagreements are reported, never averaged.** If the primary provider already
supplied splits and the secondary disagrees about a date or a ratio, both facts
go in the report and the manifest, the enrichment provider's schedule is used for
reconstruction *because it was named on the command line*, and nothing is
silently reconciled. Two vendors disagreeing about a corporate action is a
finding for a person, not a rounding problem.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
)
from tradeit.acquisition.cache import RawCache
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.reconstruct import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    RECONSTRUCTION_COLUMNS,
    RECONSTRUCTION_LABEL,
    ReconstructionQuality,
    ReconstructionResult,
    SplitEvent,
    reconstruct_symbol,
)
from tradeit.data.packages.manifest import (
    WORKSPACE_DIRNAME,
    DatasetFile,
    PackageManifest,
    Provenance,
    file_digest,
    load_manifest,
    render_manifest,
    verify_files,
)
from tradeit.data.packages.readers import read_rows
from tradeit.data.packages.spec import DATASET_SPECS, AdjustmentPolicyDeclaration, DatasetKind
from tradeit.errors import ConfigError, TradeitError

#: Bumped when the enrichment behaviour changes in a way that could make two
#: packages differ over the same vendor data.
ENRICHMENT_TOOL_VERSION = "1.0.0"

#: The limitation line an un-enriched split-adjusted package carries. Matched by
#: substring so the enricher can *replace* it rather than leaving a manifest that
#: says both "reconstruction was not attempted" and "reconstruction used FMP".
NOT_ATTEMPTED_MARKERS: tuple[str, ...] = (
    "the splits endpoint is not available on this subscription",
    "the splits endpoint errored during acquisition",
    "the splits endpoint was not exercised",
)


@dataclass(frozen=True, slots=True)
class CorporateActionLookup:
    """One vendor's answer about one symbol's corporate actions.

    ``support`` is the load-bearing field and the reason this is not just a list
    of events. An empty list and a plan restriction both produce zero splits, and
    only one of them is evidence that no splits happened. Anything that reads
    ``splits`` without reading ``support`` can write "this security never split"
    into a package about a security that split four times.
    """

    symbol: str
    support: CapabilitySupport
    status: FetchStatus
    splits: tuple[SplitEvent, ...] = ()
    #: The vendor's bytes verbatim, for the raw cache.
    raw: bytes = b""
    #: Redacted. Never contains a credential.
    url: str = ""
    error: str = ""
    #: Problems in the vendor's own rows: a zero denominator, an unparseable
    #: date. Reported rather than repaired.
    findings: tuple[str, ...] = ()
    http_status: int | None = None
    elapsed_s: float = 0.0

    @property
    def is_usable(self) -> bool:
        """Whether this answer may be used as a split schedule at all.

        ``EMPTY_VALID_RESPONSE`` counts: the vendor answered and held nothing,
        which is a schedule of zero splits. ``NOT_AVAILABLE_ON_PLAN`` and every
        error state do not, because their zero splits mean "unknown".
        """
        return self.support in (
            CapabilitySupport.AVAILABLE,
            CapabilitySupport.EMPTY_VALID_RESPONSE,
        )

    def to_outcome(self, request: FetchRequest) -> FetchOutcome:
        """Shape this as a fetch outcome so it can go in the shared journal."""
        return FetchOutcome(
            request=request,
            status=self.status,
            url=self.url,
            raw=self.raw,
            rows=tuple(
                {"ex_date": event.ex_date.isoformat(), "ratio": str(event.ratio)}
                for event in self.splits
            ),
            http_status=self.http_status,
            error=self.error,
            elapsed_s=self.elapsed_s,
        )


@runtime_checkable
class CorporateActionSource(Protocol):
    """A vendor consulted for corporate actions only.

    Deliberately *not* an :class:`~tradeit.acquisition.base.AcquisitionProvider`.
    An acquisition provider plans a whole download, owns batching and credit
    pricing, and writes a package. A source answers one question per symbol and
    writes nothing. Conflating them would either force FMP to grow a price
    pipeline it must not have, or dilute the acquisition protocol until "what
    does a provider do?" has no answer.
    """

    name: str
    credential_env: str
    #: What this source supplies. ``SPLITS`` today; a dividend source would
    #: implement the same shape with a different value here.
    dataset: AcquisitionDataset

    def has_credential(self) -> bool:
        """Whether a key is present. Never returns or logs the key itself."""

    def credential_hint(self) -> str:
        """A non-secret acknowledgement that a key is present."""

    def lookup(self, symbol: str) -> CorporateActionLookup:
        """One symbol. Must not raise for a vendor-side failure."""

    def replay(self, symbol: str, raw: bytes) -> CorporateActionLookup:
        """Re-interpret a cached response body.

        The source owns the response shape, so replaying a cached file has to go
        through the same interpretation as a live one — otherwise a resumed pass
        and a fresh pass would disagree about the same bytes.
        """

    def limitations(self) -> list[str]:
        """What this source cannot supply, copied into the manifest."""

    def capabilities(self) -> Mapping[str, CapabilitySupport]:
        """What is known to work, what is plan-restricted, what is untested."""


_SOURCES: dict[str, type] = {}


def register_source(cls: type) -> type:
    """Register a corporate-action source under its ``name``.

    A separate registry from the acquisition providers', not a shared one with a
    flag. The two answer different questions and are selected by different
    command-line options, and one registry would make ``--provider fmp``
    accepted at parse time and refused three layers down, after the operator had
    already decided what the tool could do.
    """
    name = getattr(cls, "name", "")
    if not name:
        raise ConfigError(f"{cls!r} has no name and cannot be registered")
    _SOURCES[name] = cls
    return cls


def available_sources() -> list[str]:
    return sorted(_SOURCES)


def get_source_class(name: str) -> type:
    try:
        return _SOURCES[name]
    except KeyError:
        raise ConfigError(
            f"no corporate-action source named {name!r}. Available: {available_sources()}"
        ) from None


class EnrichmentStatus(StrEnum):
    """What the pass achieved, at package level."""

    #: Every symbol got a usable answer.
    ENRICHED = "ENRICHED"
    #: Some symbols did, some did not. The package is better than it was and
    #: still incomplete, and the report names which symbols are which.
    PARTIAL = "ENRICHED_PARTIAL"
    #: The source answered for nobody — no credential, plan restriction across
    #: the board, or the network. **Nothing was written**, so the package is
    #: exactly as it was.
    NOT_ENRICHED = "NOT_ENRICHED"
    #: The package could not be read or the write failed.
    FAILED = "ENRICHMENT_FAILED"

    @property
    def wrote_anything(self) -> bool:
        return self in (EnrichmentStatus.ENRICHED, EnrichmentStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class EnrichmentOptions:
    """Everything an operator can change about an enrichment pass."""

    #: Re-ask the vendor even where the raw cache already holds its answer.
    force_refresh: bool = False
    #: Invert the price adjustment once the schedule is in hand. Off makes this
    #: a pure "fetch the splits" pass, which is occasionally what is wanted.
    reconstruct: bool = True
    #: Longest to sleep for a rate limit before giving up on pacing.
    max_wait_s: float | None = None
    #: Symbols to enrich. Empty means every symbol the package has prices for.
    symbols: tuple[str, ...] = ()


@dataclass(slots=True)
class SymbolEnrichment:
    """Per-symbol result."""

    symbol: str
    instrument_id: int
    support: CapabilitySupport = CapabilitySupport.UNKNOWN
    status: FetchStatus | None = None
    splits_found: int = 0
    splits_in_coverage: int = 0
    sessions_changed: int = 0
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.support in (
            CapabilitySupport.AVAILABLE,
            CapabilitySupport.EMPTY_VALID_RESPONSE,
        )


@dataclass(slots=True)
class EnrichmentReport:
    """What the pass did. Every number measured from what was written."""

    package: Path
    price_provider: str
    split_provider: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    symbols: list[SymbolEnrichment] = field(default_factory=list)
    reconstruction: list[ReconstructionResult] = field(default_factory=list)
    splits_written: int = 0
    findings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    capabilities: dict[str, str] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    manifest_path: Path | None = None
    requests: int = 0
    cached: int = 0
    waited_s: float = 0.0
    quota_stopped: bool = False

    @property
    def enriched(self) -> list[SymbolEnrichment]:
        return [s for s in self.symbols if s.usable]

    @property
    def unenriched(self) -> list[SymbolEnrichment]:
        return [s for s in self.symbols if not s.usable]

    @property
    def status(self) -> EnrichmentStatus:
        if self.problems:
            return EnrichmentStatus.FAILED
        if not self.enriched:
            return EnrichmentStatus.NOT_ENRICHED
        if self.unenriched:
            return EnrichmentStatus.PARTIAL
        return EnrichmentStatus.ENRICHED

    def render(self) -> str:
        status = self.status
        lines = [
            "ENRICHMENT SUMMARY",
            "=" * 72,
            f"Status               : {status}",
            f"Package              : {self.package}",
            f"Price provider       : {self.price_provider}",
            f"Split provider       : {self.split_provider}",
            "",
            f"Symbols requested    : {len(self.symbols)}",
            f"Answered usefully    : {len(self.enriched)}",
            f"Not answered         : {len(self.unenriched)}",
            f"Split records written: {self.splits_written:,}",
            "",
            f"Requests issued      : {self.requests:,}",
            f"Answers from cache   : {self.cached:,}",
        ]
        if self.waited_s:
            lines.append(f"Paused for limits    : {self.waited_s:.0f}s")
        lines.append(f"Manifest             : {self.manifest_path}")

        if self.unenriched:
            lines += ["", "Not enriched", "-" * 72]
            for item in self.unenriched:
                lines.append(f"  {item.symbol:<10} {item.support:<24} {item.error[:60]}")
            lines += [
                "",
                "  These symbols have NO split schedule from this source. Their prices",
                "  keep the primary provider's adjustment and the reconstruction for",
                "  them is marked not attempted. That is not a claim that they never",
                "  split.",
            ]
        if self.reconstruction:
            lines += ["", "Raw-price reconstruction (DERIVED, not vendor raw)", "-" * 72]
            lines += [f"  {item.summary()}" for item in self.reconstruction[:10]]
            if len(self.reconstruction) > 10:
                lines.append(f"  ... and {len(self.reconstruction) - 10} more")
        if self.conflicts:
            lines += ["", f"Split-schedule CONFLICTS ({len(self.conflicts)})", "-" * 72]
            lines += [f"  - {item}" for item in self.conflicts[:20]]
            if len(self.conflicts) > 20:
                lines.append(f"  ... and {len(self.conflicts) - 20} more; see the report file")
            lines += [
                "",
                "  NOT resolved automatically. Two sources disagreeing about a corporate",
                "  action is a question for a person. A split that simply falls outside",
                "  this package's window is not listed here — that is correct behaviour",
                "  and is reported under Findings.",
            ]
        if self.notes:
            lines += ["", "Notes", "-" * 72]
            lines += [f"  - {item}" for item in self.notes]
        if self.problems:
            lines += ["", "Problems", "-" * 72]
            lines += [f"  - {item}" for item in self.problems]
        if self.findings:
            lines += ["", f"Findings ({len(self.findings)})", "-" * 72]
            lines += [f"  - {item}" for item in self.findings[:15]]
            if len(self.findings) > 15:
                lines.append(f"  ... and {len(self.findings) - 15} more; see the report file")
        if self.capabilities:
            lines += ["", "Source capabilities as exercised", "-" * 72]
            for name, state in sorted(self.capabilities.items()):
                lines.append(f"  {name:<38} {state}")

        lines += ["", "Next", "-" * 72]
        if status.wrote_anything:
            lines += [
                f"  tradeit data inspect {self.package}",
                f"  tradeit data import  {self.package}",
            ]
        else:
            lines.append("  Nothing was written. The package is unchanged.")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "package": str(self.package),
            "price_provider": self.price_provider,
            "split_provider": self.split_provider,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "tool_version": ENRICHMENT_TOOL_VERSION,
            "splits_written": self.splits_written,
            "requests": self.requests,
            "cached": self.cached,
            "symbols": [
                {
                    "symbol": item.symbol,
                    "support": str(item.support),
                    "status": str(item.status) if item.status else None,
                    "splits_found": item.splits_found,
                    "splits_in_coverage": item.splits_in_coverage,
                    "sessions_changed": item.sessions_changed,
                    "error": item.error,
                }
                for item in self.symbols
            ],
            "reconstruction": [
                {
                    "symbol": item.symbol,
                    "quality": str(item.quality),
                    "sessions_changed": item.sessions_changed,
                    "sessions_total": item.sessions_total,
                    "splits_used": len(item.splits_used),
                    "price_provider": item.price_provider,
                    "split_provider": item.split_provider,
                    "label": RECONSTRUCTION_LABEL,
                    "algorithm": RECONSTRUCTION_ALGORITHM_VERSION,
                }
                for item in self.reconstruction
            ],
            "conflicts": list(self.conflicts),
            "findings": list(self.findings),
            "problems": list(self.problems),
            "notes": list(self.notes),
            "limitations": list(self.limitations),
            "capabilities": dict(self.capabilities),
        }


class PackageEnricher:
    """One enrichment pass over one existing package.

    Reads the package the primary provider wrote, asks a secondary source for
    split schedules, writes the splits dataset, re-runs the raw reconstruction
    with those splits, and rewrites the manifest so the package says which
    vendor supplied which part.

    Knows nothing about FMP. It knows about :class:`CorporateActionSource`, the
    package format, and the reconstruction arithmetic.
    """

    def __init__(
        self,
        package: Path,
        source: Any,
        options: EnrichmentOptions | None = None,
    ) -> None:
        self.package = Path(package)
        self.source = source
        self.options = options or EnrichmentOptions()
        self.workspace = self.package / WORKSPACE_DIRNAME
        self.cache = RawCache(self.workspace)
        self.journal = AcquisitionJournal(self.workspace / "journal.jsonl")

    # -- the pass ------------------------------------------------------------

    def run(self) -> EnrichmentReport:
        report = EnrichmentReport(
            package=self.package,
            price_provider="",
            split_provider=self.source.name,
            started_at=dt.datetime.now(dt.UTC),
        )
        try:
            manifest = load_manifest(self.package / "manifest.toml")
        except TradeitError as error:
            # A missing or malformed manifest is an operator problem, reported
            # in the same place as every other one rather than as a traceback.
            report.problems.append(str(error))
            report.finished_at = dt.datetime.now(dt.UTC)
            return report
        report.price_provider = manifest.provider

        if not self.source.has_credential():
            report.problems.append(
                f"no API key found. Set {self.source.credential_env} and re-run. "
                "Nothing was requested and the package is unchanged."
            )
            report.finished_at = dt.datetime.now(dt.UTC)
            return report

        try:
            tickers = self._tickers(manifest)
            bars = self._bars(manifest)
        except ConfigError as error:
            report.problems.append(str(error))
            report.finished_at = dt.datetime.now(dt.UTC)
            return report

        wanted = self._wanted(tickers)
        if not wanted:
            report.problems.append(
                "no symbols to enrich. The package declares no symbol_mappings "
                "file, or --symbols named tickers this package does not contain."
            )
            report.finished_at = dt.datetime.now(dt.UTC)
            return report

        schedules: dict[str, tuple[SplitEvent, ...]] = {}
        for symbol in wanted:
            instrument_id = tickers[symbol]
            state = SymbolEnrichment(symbol=symbol, instrument_id=instrument_id)
            report.symbols.append(state)

            lookup = self._lookup(symbol, report)
            state.support = lookup.support
            state.status = lookup.status
            state.error = lookup.error
            report.findings.extend(lookup.findings)

            if lookup.status.stops_the_run:
                # The daily allowance is gone. Everything already written stays;
                # the remaining symbols are simply not done yet, and re-running
                # tomorrow continues from the cache.
                report.quota_stopped = True
                report.notes.append(
                    f"stopped at {symbol} after {report.requests} request(s): "
                    f"{lookup.error or 'the daily request allowance is exhausted'}"
                )
                break
            if not lookup.is_usable:
                continue

            state.splits_found = len(lookup.splits)
            schedules[symbol] = lookup.splits

        self._write(manifest, tickers, bars, schedules, report)
        report.limitations = list(self.source.limitations())
        report.capabilities = {
            name: str(state) for name, state in self.source.capabilities().items()
        }
        report.finished_at = dt.datetime.now(dt.UTC)
        return report

    # -- reading the package -------------------------------------------------

    def _tickers(self, manifest: PackageManifest) -> dict[str, int]:
        """Ticker to instrument id, from the package's own symbol mappings.

        The package's mapping rather than a fresh assignment: re-deriving ids
        here would renumber the instruments and attach split rows to the wrong
        securities, which is the kind of error that produces a plausible-looking
        price series for the wrong company.
        """
        files = manifest.files_for(DatasetKind.SYMBOL_MAPPINGS)
        if not files:
            raise ConfigError(
                f"{self.package} declares no symbol_mappings file, so there is no "
                "way to say which instrument_id a ticker belongs to. Enrichment "
                "would have to guess, and a wrong guess attaches one company's "
                "splits to another company's prices."
            )
        out: dict[str, int] = {}
        for file in files:
            ticker_column = file.source_column("ticker")
            id_column = file.source_column("instrument_id")
            for row in read_rows(self.package / file.path):
                ticker = (row.get(ticker_column) or "").strip().upper()
                identifier = row.get(id_column)
                if not ticker or identifier is None:
                    continue
                try:
                    out[ticker] = int(identifier)
                except ValueError:
                    continue
        return out

    def _bars(self, manifest: PackageManifest) -> dict[int, list[dict[str, str]]]:
        """Daily bars per instrument, in canonical column names.

        Read through the manifest's column mapping, so a hand-assembled package
        whose file calls the column ``date`` is handled the same as one this
        tool wrote.
        """
        out: dict[int, list[dict[str, str]]] = {}
        for file in manifest.files_for(DatasetKind.DAILY_BARS):
            columns = {name: file.source_column(name) for name in _BAR_FIELDS}
            for row in read_rows(self.package / file.path):
                identifier = row.get(columns["instrument_id"])
                session = row.get(columns["session_date"])
                if identifier is None or session is None:
                    continue
                try:
                    key = int(identifier)
                except ValueError:
                    continue
                canonical = {"instrument_id": str(key), "session_date": session}
                for name in ("open", "high", "low", "close", "volume"):
                    value = row.get(columns[name])
                    if value is not None:
                        canonical[name] = value
                out.setdefault(key, []).append(canonical)
        for rows in out.values():
            rows.sort(key=lambda r: r["session_date"])
        return out

    def _wanted(self, tickers: Mapping[str, int]) -> list[str]:
        if not self.options.symbols:
            return sorted(tickers)
        asked = [s.strip().upper() for s in self.options.symbols if s.strip()]
        return [s for s in asked if s in tickers]

    # -- one symbol ----------------------------------------------------------

    def _lookup(self, symbol: str, report: EnrichmentReport) -> CorporateActionLookup:
        """Ask the source, through the raw cache, and journal the answer.

        The same cache and the same journal the acquisition run used. A split
        schedule is a vendor response like any other, and keeping it in one place
        means "what did I ask, and what came back?" has one answer per package
        rather than one per tool.
        """
        request = FetchRequest.one(self.source.dataset, symbol)
        if self.options.force_refresh:
            self.cache.discard(self.source.name, request)

        entry = self.cache.get(self.source.name, request)
        if entry is not None:
            replayed: CorporateActionLookup = self.source.replay(symbol, entry.read())
            report.cached += 1
            self.journal.record(
                self.source.name,
                replayed.to_outcome(request),
                source_file=str(entry.path),
                sha256=entry.digest,
            )
            return replayed

        waited = self._pace()
        report.waited_s += waited
        lookup: CorporateActionLookup = self.source.lookup(symbol)
        report.requests += 1

        source_file = ""
        digest = ""
        if lookup.status.is_success and lookup.raw:
            stored = self.cache.put(self.source.name, request, lookup.raw, url=lookup.url)
            source_file = str(stored.path)
            digest = stored.digest
        self.journal.record(
            self.source.name,
            lookup.to_outcome(request),
            source_file=source_file,
            sha256=digest,
        )
        return lookup

    def _pace(self) -> float:
        """Let the source throttle itself, and record how long it cost.

        The source owns its own limiter because only it knows the vendor's
        terms. This function exists so the waiting shows up in the report rather
        than looking like a hang.
        """
        pacer = getattr(self.source, "wait_for_slot", None)
        if pacer is None:
            return 0.0
        started = time.monotonic()
        pacer(max_wait_s=self.options.max_wait_s)
        return time.monotonic() - started

    # -- writing -------------------------------------------------------------

    def _write(
        self,
        manifest: PackageManifest,
        tickers: Mapping[str, int],
        bars: Mapping[int, list[dict[str, str]]],
        schedules: Mapping[str, tuple[SplitEvent, ...]],
        report: EnrichmentReport,
    ) -> None:
        if not schedules:
            report.notes.append(
                f"{self.source.name} supplied no usable split schedule for any "
                "requested symbol, so nothing was written and the package is "
                "byte-for-byte as it was. That is a statement about this "
                "source's answers, NOT about whether these securities split."
            )
            return

        by_instrument = {identifier: ticker for ticker, identifier in tickers.items()}
        existing = self._existing_splits(manifest, by_instrument)
        rows: list[dict[str, str]] = []
        chosen: dict[str, tuple[SplitEvent, ...]] = {}

        for symbol in sorted(schedules):
            events = schedules[symbol]
            prior = existing.get(symbol, ())
            if prior:
                report.conflicts.extend(compare_schedules(symbol, prior, events, self.source.name))
            chosen[symbol] = events
            rows.extend(
                _split_row(tickers[symbol], event, self.source.name)
                for event in sorted(events, key=lambda e: e.ex_date)
            )

        # Symbols the source could not answer for keep whatever the primary
        # provider supplied. Dropping those rows would delete real records to
        # make a report tidier.
        for symbol, events in sorted(existing.items()):
            if symbol in chosen or symbol not in tickers:
                continue
            rows.extend(
                _split_row(tickers[symbol], event, event.source or manifest.provider)
                for event in sorted(events, key=lambda e: e.ex_date)
            )

        report.splits_written = len(rows)
        for state in report.symbols:
            events = chosen.get(state.symbol, ())
            coverage = _coverage(bars.get(state.instrument_id, []))
            if coverage is not None:
                state.splits_in_coverage = sum(
                    1 for e in events if coverage[0] <= e.ex_date <= coverage[1]
                )

        if self.options.reconstruct:
            self._reconstruct(manifest, tickers, bars, chosen, report)

        splits_file = self._write_splits(rows, report)
        self._rewrite_manifest(manifest, splits_file, report)

    def _existing_splits(
        self, manifest: PackageManifest, by_instrument: Mapping[int, str]
    ) -> dict[str, tuple[SplitEvent, ...]]:
        """Whatever split records the package already holds, per symbol."""
        out: dict[str, list[SplitEvent]] = {}
        for file in manifest.files_for(DatasetKind.SPLITS):
            columns = {
                name: file.source_column(name)
                for name in (
                    "instrument_id",
                    "ex_date",
                    "ratio",
                    "numerator",
                    "denominator",
                    "split_type",
                    "source_provider",
                )
            }
            for row in read_rows(self.package / file.path):
                identifier = row.get(columns["instrument_id"])
                ex_date = row.get(columns["ex_date"])
                ratio = row.get(columns["ratio"])
                if identifier is None or ex_date is None or ratio is None:
                    continue
                try:
                    symbol = by_instrument.get(int(identifier), "")
                    event = SplitEvent(
                        ex_date=dt.date.fromisoformat(ex_date),
                        ratio=Decimal(ratio),
                        source=str(row.get(columns["source_provider"]) or manifest.provider),
                        numerator=_int_or_none(row.get(columns["numerator"])),
                        denominator=_int_or_none(row.get(columns["denominator"])),
                        split_type=str(row.get(columns["split_type"]) or ""),
                    )
                except (ValueError, ArithmeticError):
                    continue
                if symbol:
                    out.setdefault(symbol, []).append(event)
        return {symbol: tuple(events) for symbol, events in out.items()}

    def _reconstruct(
        self,
        manifest: PackageManifest,
        tickers: Mapping[str, int],
        bars: Mapping[int, list[dict[str, str]]],
        schedules: Mapping[str, tuple[SplitEvent, ...]],
        report: EnrichmentReport,
    ) -> None:
        """Invert the primary provider's split adjustment, using these splits.

        Runs only for a package that declares its prices split-adjusted. For a
        raw-price package there is nothing to invert, and emitting a sidecar of
        identical numbers would imply otherwise; the splits are still written,
        because a split schedule is worth having either way.
        """
        if manifest.adjustment_policy is not AdjustmentPolicyDeclaration.SPLIT_ADJUSTED:
            report.notes.append(
                f"the package declares {manifest.adjustment_policy} prices, so there "
                "is no split adjustment to invert. The split schedule was written; "
                "no reconstruction was performed."
            )
            return

        for symbol in sorted(schedules):
            rows = bars.get(tickers[symbol], [])
            if not rows:
                continue
            result = reconstruct_symbol(
                symbol,
                rows,
                schedules[symbol],
                splits_available=True,
                price_provider=manifest.provider,
                split_provider=self.source.name,
            )
            report.reconstruction.append(result)
            report.conflicts.extend(result.conflicts)
            report.findings.extend(result.notes)
            for state in report.symbols:
                if state.symbol == symbol:
                    state.sessions_changed = result.sessions_changed

        applied = [r for r in report.reconstruction if r.rows]
        if not applied:
            return
        path = self.workspace / "reconstructed_raw_prices.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(
            path,
            [row for item in applied for row in item.rows],
            list(RECONSTRUCTION_COLUMNS),
        )
        report.files[path.name] = path.stat().st_size

    def _write_splits(self, rows: Sequence[dict[str, str]], report: EnrichmentReport) -> str:
        """Write the splits dataset, replacing any previous one.

        One file rather than a second one alongside it: two splits files would
        both be declared, both be read, and every event in the overlap would be
        applied twice by anything that summed them.
        """
        name = f"{DatasetKind.SPLITS}.csv"
        path = self.package / name
        ordered = sorted(rows, key=lambda r: (int(r["instrument_id"]), r["ex_date"]))
        columns = _columns_for(DatasetKind.SPLITS, ordered)
        _write_csv(path, ordered, columns, compress=False)
        report.files[name] = path.stat().st_size
        return name

    def _rewrite_manifest(
        self, manifest: PackageManifest, splits_file: str, report: EnrichmentReport
    ) -> None:
        """Rewrite the manifest so the package states what just happened."""
        path = self.package / splits_file
        entry = DatasetFile(
            path=splits_file,
            dataset=DatasetKind.SPLITS,
            sha256=file_digest(path),
            rows=report.splits_written,
            note=f"corporate actions supplied by {self.source.name}, not by {manifest.provider}",
        )
        files = [f for f in manifest.files if f.dataset is not DatasetKind.SPLITS]
        files.append(entry)

        applied = [r for r in report.reconstruction if r.quality is ReconstructionQuality.APPLIED]
        provenance = Provenance(
            price_provider=manifest.provider,
            price_representation=str(manifest.adjustment_policy),
            dividend_provider=(
                manifest.provenance.dividend_provider if manifest.provenance else ""
            ),
            split_provider=self.source.name,
            reconstruction_performed=bool(applied),
            reconstruction_algorithm=RECONSTRUCTION_ALGORITHM_VERSION if applied else "",
            reconstruction_label=RECONSTRUCTION_LABEL if applied else "",
            reconstruction_file=(
                f"{WORKSPACE_DIRNAME}/reconstructed_raw_prices.csv.gz" if applied else ""
            ),
        )

        updated = manifest.model_copy(
            update={
                "files": tuple(files),
                "provenance": provenance,
                "known_limitations": tuple(
                    self._limitations(manifest, report, reconstructed=bool(applied))
                ),
            }
        )
        target = self.package / "manifest.toml"
        target.write_text(render_manifest(updated), encoding="utf-8")
        report.manifest_path = target
        report.problems.extend(verify_files(updated, self.package))

    def _limitations(
        self, manifest: PackageManifest, report: EnrichmentReport, *, reconstructed: bool
    ) -> list[str]:
        """The package's limitations, with the stale ones replaced.

        A manifest that said both "the splits endpoint is not available on this
        subscription" and "reconstruction used FMP split records" would be
        self-contradicting, and a reader would be entitled to believe either. So
        the lines the primary provider wrote about its own unavailable splits are
        removed, and what replaced them is stated.
        """
        kept = [
            item
            for item in manifest.known_limitations
            if not any(marker in item for marker in NOT_ATTEMPTED_MARKERS)
        ]
        dropped = len(manifest.known_limitations) - len(kept)
        if dropped:
            kept.append(
                f"the primary provider ({manifest.provider}) could not supply splits; "
                f"that limitation is superseded below, and {dropped} line(s) stating it "
                "were removed when this package was enriched"
            )
        if reconstructed:
            kept.append(
                f"raw reconstruction performed using {self.source.name} historical "
                "split records, applied to "
                f"{manifest.provider} split-adjusted daily prices. The reconstructed "
                f"series is DERIVED, labelled {RECONSTRUCTION_LABEL}, written to the "
                f"{WORKSPACE_DIRNAME} workspace, and was supplied directly by NEITHER "
                "provider"
            )
        kept.append(
            f"completeness of the raw reconstruction depends on {self.source.name} "
            "split-history coverage. A split this source does not hold leaves every "
            "price before it wrong by that split's factor, and nothing in the data "
            "reveals it"
        )
        kept.append(
            f"split records in this package come from {self.source.name} and carry a "
            "source_provider column naming it"
        )
        # The source's own caveats, verbatim. They are facts about the vendor —
        # what its date field means, what it was and was not asked for, how it
        # was paced — and the source is the thing that knows them.
        kept.extend(item for item in self.source.limitations() if item not in kept)
        if report.unenriched:
            kept.append(
                f"{len(report.unenriched)} of {len(report.symbols)} symbols have no "
                f"split schedule from {self.source.name}: "
                + ", ".join(sorted(s.symbol for s in report.unenriched))
                + ". Their reconstruction was not attempted, which is not a claim that "
                "they never split"
            )
        if report.conflicts:
            kept.append(
                f"{len(report.conflicts)} split-schedule conflict(s) were found and NOT "
                "resolved automatically; see the enrichment report in the "
                f"{WORKSPACE_DIRNAME} workspace"
            )
        kept.append(
            f"enriched by tradeit data enrich {ENRICHMENT_TOOL_VERSION} on "
            f"{report.started_at.date().isoformat()}"
        )
        return kept


# ---------------------------------------------------------------------------
# Cross-vendor comparison
# ---------------------------------------------------------------------------


def compare_schedules(
    symbol: str,
    primary: Sequence[SplitEvent],
    secondary: Sequence[SplitEvent],
    secondary_name: str,
) -> list[str]:
    """Where two vendors disagree about a symbol's splits.

    Returns findings. It does **not** merge, pick a winner, or average a ratio.
    The caller uses the enrichment source's schedule because that is what the
    operator asked for on the command line, and every place the two differ is
    reported so that choice is visible rather than assumed.
    """
    findings: list[str] = []
    by_date_primary: dict[dt.date, list[SplitEvent]] = {}
    for event in primary:
        by_date_primary.setdefault(event.ex_date, []).append(event)
    by_date_secondary: dict[dt.date, list[SplitEvent]] = {}
    for event in secondary:
        by_date_secondary.setdefault(event.ex_date, []).append(event)

    primary_name = next((e.source for e in primary if e.source), "the primary provider")

    for ex_date in sorted(set(by_date_primary) | set(by_date_secondary)):
        left = by_date_primary.get(ex_date, [])
        right = by_date_secondary.get(ex_date, [])
        if left and not right:
            findings.append(
                f"{symbol}: {primary_name} records a split on {ex_date} "
                f"({left[0].describe}) that {secondary_name} does not. The "
                f"{secondary_name} schedule was used for reconstruction, so this "
                "event was NOT applied."
            )
        elif right and not left:
            findings.append(
                f"{symbol}: {secondary_name} records a split on {ex_date} "
                f"({right[0].describe}) that {primary_name} does not."
            )
        else:
            left_ratios = {e.ratio for e in left}
            right_ratios = {e.ratio for e in right}
            if left_ratios != right_ratios:
                findings.append(
                    f"{symbol}: the two sources DISAGREE about the split on {ex_date} — "
                    f"{primary_name} says {sorted(str(r) for r in left_ratios)}, "
                    f"{secondary_name} says {sorted(str(r) for r in right_ratios)}. "
                    f"The {secondary_name} figure was used. Not reconciled."
                )
    return findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAR_FIELDS: tuple[str, ...] = (
    "instrument_id",
    "session_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def _split_row(instrument_id: int, event: SplitEvent, provider: str) -> dict[str, str]:
    """One canonical split row, keeping the vendor's own numbers.

    ``numerator`` and ``denominator`` survive alongside the derived ratio. A
    ratio of ``0.1`` could be 1-for-10 or 2-for-20, and when somebody later
    disputes the direction of a reverse split, the vendor's own pair is what the
    argument gets settled against.
    """
    row = {
        "instrument_id": str(instrument_id),
        "ex_date": event.ex_date.isoformat(),
        "ratio": _plain(event.ratio),
        "source_provider": provider,
    }
    if event.numerator is not None:
        row["numerator"] = str(event.numerator)
    if event.denominator is not None:
        row["denominator"] = str(event.denominator)
    if event.split_type:
        row["split_type"] = event.split_type
    # `announcement_time` is deliberately never written. The sources this project
    # can reach supply an effective date only, and an effective date presented as
    # an announcement would license research the data cannot support.
    return row


def _coverage(bars: Sequence[Mapping[str, str]]) -> tuple[dt.date, dt.date] | None:
    if not bars:
        return None
    return (
        dt.date.fromisoformat(bars[0]["session_date"]),
        dt.date.fromisoformat(bars[-1]["session_date"]),
    )


def _columns_for(dataset: DatasetKind, rows: Sequence[Mapping[str, str]]) -> list[str]:
    spec = DATASET_SPECS[dataset]
    known = [c.name for c in spec.columns]
    present = {key for row in rows for key in row}
    return [name for name in known if name in present] + sorted(present - set(known))


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
    *,
    compress: bool = True,
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


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "ENRICHMENT_TOOL_VERSION",
    "NOT_ATTEMPTED_MARKERS",
    "CorporateActionLookup",
    "CorporateActionSource",
    "EnrichmentOptions",
    "EnrichmentReport",
    "EnrichmentStatus",
    "PackageEnricher",
    "SymbolEnrichment",
    "available_sources",
    "compare_schedules",
    "get_source_class",
    "register_source",
]
