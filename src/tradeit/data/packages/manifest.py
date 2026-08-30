"""The manifest: what a package claims about itself, in a form that can be checked.

A package is a directory (or an archive) of data files plus exactly one
``manifest.toml``. The manifest is not documentation — it is the only thing that
tells the importer how to read the files, and every field in it is either
verified against the files or refused.

Three things it must state, and the reason each is mandatory rather than
inferred:

**Which column means what.** Vendors disagree about names, and the same name can
mean different things: a column called ``close`` may hold the exchange print or
the split-adjusted close, and no amount of inspection can tell them apart. So
the manifest maps *our* field name to *their* column name, and the importer
never guesses. :data:`~tradeit.data.packages.spec.ColumnSpec.common_aliases`
exists to help a person write the mapping, and is never applied automatically.

**What the prices are.** ``adjustment_policy`` has no default. Adjusted closes
loaded as raw prices describe a history nobody could have seen (ADR-0005), and
the failure is invisible afterwards — the series looks perfectly plausible.

**What is in each file, byte for byte.** Every file carries a SHA-256 that the
importer verifies before reading a row. A manifest whose hashes do not match its
files is refused rather than repaired, because the interesting case is not
corruption; it is the file that was regenerated after the manifest was written,
and silently accepting that would attach one dataset's provenance to another's
contents.

The manifest also carries the fields that make a result attributable years
later: provider, export date, coverage window, timezone, known limitations, and
a licence note. None of them changes how a row is parsed. All of them change
whether a number computed from the package can be defended.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tradeit.core.enums import Bartimeframe
from tradeit.data.packages.spec import (
    DATASET_SPECS,
    PACKAGE_FORMAT_VERSION,
    AdjustmentPolicyDeclaration,
    DatasetKind,
)
from tradeit.errors import ConfigError, DataError

#: Read in chunks so a multi-gigabyte export does not have to fit in memory.
_HASH_CHUNK = 1 << 20

#: File extensions the readers understand. Parquet requires ``pyarrow``; the
#: importer reports its absence rather than failing obscurely inside pandas.
SUPPORTED_SUFFIXES: tuple[str, ...] = (".csv", ".csv.gz", ".tsv", ".tsv.gz", ".parquet")

#: Reserved directory name inside a package. Everything under it is provenance
#: rather than data — the acquisition tool's raw vendor responses, its journal,
#: its report — and is deliberately not declared in the manifest.
#:
#: It needs a name because :func:`verify_files` otherwise reports every raw
#: response as an undeclared data file and the importer aborts. The exception is
#: one directory, named here, rather than a general "ignore what you do not
#: recognise": an undeclared file at the package root is a file whose provenance
#: nobody can state, and that check is worth keeping sharp.
WORKSPACE_DIRNAME = "_acquisition"


def file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class PackageSection(BaseModel):
    """Frozen, and a typo'd key is an error rather than a silent default."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetFile(PackageSection):
    """One file, its dataset, and how to read it."""

    #: Path relative to the package root. Never absolute: a manifest that
    #: pointed outside its own directory would not be portable, and would make
    #: "which bytes produced this?" unanswerable on another machine.
    path: str = Field(min_length=1)
    dataset: DatasetKind
    #: SHA-256 of the file, lowercase hex. Verified before any row is read.
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    rows: int | None = Field(default=None, ge=0)
    #: Our field name -> the column name in this file. Fields absent from the
    #: mapping are read by their own name; fields absent from the file are
    #: reported against the dataset's contract.
    columns: Mapping[str, str] = Field(default_factory=dict)
    #: For intraday files that carry one width per file rather than a column.
    timeframe: Bartimeframe | None = None
    #: Free-text, surfaced in the import report.
    note: str = ""

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if Path(self.path).is_absolute() or ".." in Path(self.path).parts:
            raise ConfigError(
                f"file path {self.path!r} must be relative to the package root and "
                "must not escape it"
            )
        if not any(self.path.endswith(suffix) for suffix in SUPPORTED_SUFFIXES):
            raise ConfigError(
                f"{self.path!r} has an unsupported extension; expected one of "
                f"{list(SUPPORTED_SUFFIXES)}"
            )
        spec = DATASET_SPECS[self.dataset]
        mapped = set(self.columns)
        known = {column.name for column in spec.columns}
        unknown = sorted(mapped - known)
        if unknown:
            raise ConfigError(
                f"{self.path!r} maps {unknown}, which are not fields of the "
                f"{self.dataset} dataset. Expected some of {sorted(known)}."
            )
        return self

    def source_column(self, field_name: str) -> str:
        return self.columns.get(field_name, field_name)


class Coverage(PackageSection):
    """What the package claims to span.

    Checked against the data. A manifest claiming 2004-2024 over a file that
    stops in 2019 is not a rounding error — it is the difference between a
    validation that covered a bear market and one that did not, and the import
    report says so rather than letting the claim stand.
    """

    start: dt.date
    end: dt.date
    instruments: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.end < self.start:
            raise ConfigError(f"coverage ends {self.end} before it starts {self.start}")
        return self


class Provenance(PackageSection):
    """Which vendor supplied which part, when more than one did.

    ``provider`` at the top level names the package's *primary* source, and that
    was enough while one vendor supplied everything. It stops being enough the
    moment a package's prices come from one vendor and its split schedule from
    another: the reconstructed raw series is then a value **neither vendor
    supplied**, and a reader who saw only ``provider = "twelve_data"`` would
    attribute it to Twelve Data.

    Every field is optional and empty by default. An absent field means "not
    recorded", never "not applicable" — a package written before this table
    existed says nothing here, and inferring a split provider from the price
    provider is exactly the mistake the table exists to prevent.
    """

    #: Who supplied the OHLCV in the package's price columns.
    price_provider: str = ""
    #: What those columns actually contain, echoing ``adjustment_policy``. Kept
    #: alongside the provider because "Twelve Data daily bars" is not a
    #: statement about adjustment and is regularly read as one.
    price_representation: str = ""
    dividend_provider: str = ""
    split_provider: str = ""
    #: Whether a raw series was derived by inverting a split adjustment.
    reconstruction_performed: bool = False
    #: Version of the arithmetic, so a sidecar produced by an older algorithm is
    #: identifiable rather than silently mixed with a newer one.
    reconstruction_algorithm: str = ""
    #: The label those derived values carry. Never "raw vendor price".
    reconstruction_label: str = ""
    #: Where the derived series lives, relative to the package root. Deliberately
    #: inside the workspace directory and deliberately not a declared dataset
    #: file: it is DERIVED, and the importer must not read it as prices.
    reconstruction_file: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "price_provider": self.price_provider,
            "price_representation": self.price_representation,
            "dividend_provider": self.dividend_provider,
            "split_provider": self.split_provider,
            "reconstruction_performed": self.reconstruction_performed,
            "reconstruction_algorithm": self.reconstruction_algorithm,
            "reconstruction_label": self.reconstruction_label,
            "reconstruction_file": self.reconstruction_file,
        }


class InstrumentCapabilityEntry(PackageSection):
    """What one instrument's data supports, as recorded in the package.

    The TOML face of
    :class:`~tradeit.data.packages.capability.InstrumentCapabilityRecord`. A real
    universe acquisition is not uniform — the first full run had 78 instruments
    with prices and 34 with a verified split schedule — and a validation result
    over "78 instruments" that silently mixed the two would be uninterpretable.

    ``flags`` is a list of strings rather than an enum so that a package written
    by a newer build still loads here: an unrecognised flag is carried and
    ignored rather than making the manifest unreadable.
    """

    instrument_id: int = Field(ge=0)
    ticker: str = ""
    flags: tuple[str, ...] = ()
    #: Why a capability is absent. "the split source answered HTTP 402" names
    #: both the cause and the remedy where a bare count names neither.
    reason: str = ""
    split_provider: str = ""
    http_status: int | None = None


class AcquisitionOutcomeEntry(PackageSection):
    """What happened to one *requested* symbol, including the ones that failed.

    A package records the instruments it contains. That is exactly the wrong
    list for answering "why is LEH not here?", because a symbol that produced
    nothing produces no row anywhere, and its absence is then indistinguishable
    from never having been asked for.

    The acquisition run already knows: :class:`~tradeit.acquisition.base.SymbolStatus`
    separates ``not_found`` from ``plan_restricted`` from ``ambiguous`` from
    ``unavailable_historically``. That classification used to stop at the
    manifest boundary, so the survivorship check downstream could only say
    "absent" — one word covering six different problems with six different
    remedies. This carries it across.
    """

    ticker: str = Field(min_length=1)
    #: ``tradeit.acquisition.base.SymbolStatus``, as a string so a package
    #: written by a newer build still loads.
    symbol_status: str = ""
    #: ``tradeit.acquisition.base.FetchStatus`` for the price request.
    fetch_status: str = ""
    bars: int = Field(default=0, ge=0)
    error: str = ""


class AcquisitionRecord(PackageSection):
    """The requested window and the per-symbol result of asking for it.

    ``requested_start`` is not ``coverage.start``. Coverage is what came back;
    a security that stopped trading before the request even began cannot appear
    in it, and telling those two cases apart is the difference between "the
    vendor has no delisted coverage" and "we asked for the wrong years".
    """

    provider: str = ""
    requested_start: dt.date | None = None
    requested_end: dt.date | None = None
    outcomes: tuple[AcquisitionOutcomeEntry, ...] = ()

    def by_ticker(self) -> dict[str, AcquisitionOutcomeEntry]:
        return {entry.ticker: entry for entry in self.outcomes}

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "requested_start": (self.requested_start.isoformat() if self.requested_start else None),
            "requested_end": self.requested_end.isoformat() if self.requested_end else None,
            "outcomes": [
                {
                    "ticker": entry.ticker,
                    "symbol_status": entry.symbol_status,
                    "fetch_status": entry.fetch_status,
                    "bars": entry.bars,
                    "error": entry.error,
                }
                for entry in self.outcomes
            ],
        }


class PackageManifest(PackageSection):
    """Everything a package says about itself."""

    format_version: int = Field(default=PACKAGE_FORMAT_VERSION, ge=1)
    #: Short identifier for this package. Appears in the snapshot id, so it
    #: should be stable across re-exports of the same dataset.
    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    #: When the vendor produced the export, not when it was imported.
    export_date: dt.date
    coverage: Coverage
    #: IANA timezone the timestamps are expressed in, e.g. America/New_York.
    #: Required: a naive timestamp column with no declared zone is a session
    #: boundary nobody can reconstruct.
    timezone: str = Field(min_length=1, max_length=64)
    #: No default. See the module docstring.
    adjustment_policy: AdjustmentPolicyDeclaration
    files: tuple[DatasetFile, ...] = Field(min_length=1)

    #: ``strptime`` layouts for date columns this vendor writes ambiguously.
    #: The importer refuses to guess between ``%m/%d/%Y`` and ``%d/%m/%Y``, so a
    #: package whose dates look like ``03/04/2021`` must say which it means here
    #: or watch every row quarantine.
    date_formats: tuple[str, ...] = ()
    #: ``1.234,56`` rather than ``1,234.56``. Declared, never detected: a file
    #: where every value happens to have two decimal places reads identically
    #: under both conventions and means different numbers.
    decimal_comma: bool = False

    #: What the exporter knows is wrong or missing. Free-text, surfaced in every
    #: import report and stored with the package. A package that declares its
    #: gaps is worth more than one that appears complete.
    known_limitations: tuple[str, ...] = ()
    licence_note: str = ""
    #: Vendor's own dataset identifier, where one exists.
    vendor_dataset: str = ""
    description: str = ""

    #: Per-part attribution, for packages assembled from more than one vendor.
    #: ``None`` on a single-vendor package, where ``provider`` says everything.
    provenance: Provenance | None = None

    #: Per-instrument capability flags. Empty means "not recorded", which is not
    #: the same as "nothing is capable" — a package written before this existed
    #: says nothing here, and validation reports that as unknown rather than as
    #: zero eligible instruments.
    instrument_capabilities: tuple[InstrumentCapabilityEntry, ...] = ()

    #: What was asked for and what came back, symbol by symbol — including the
    #: symbols that came back with nothing. ``None`` means "not recorded",
    #: which downstream must report as unknown rather than as "nothing failed".
    acquisition: AcquisitionRecord | None = None

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.format_version > PACKAGE_FORMAT_VERSION:
            raise ConfigError(
                f"manifest declares format version {self.format_version}; this build "
                f"understands up to {PACKAGE_FORMAT_VERSION}. Reading it would risk "
                "misinterpreting fields added after this importer was written."
            )
        seen = [file.path for file in self.files]
        duplicates = sorted({p for p in seen if seen.count(p) > 1})
        if duplicates:
            raise ConfigError(f"manifest lists the same file twice: {duplicates}")
        return self

    # -- derived facts -------------------------------------------------------

    @property
    def datasets(self) -> tuple[DatasetKind, ...]:
        return tuple(dict.fromkeys(file.dataset for file in self.files))

    def files_for(self, dataset: DatasetKind) -> list[DatasetFile]:
        return [file for file in self.files if file.dataset is dataset]

    def missing_dependencies(self) -> dict[DatasetKind, tuple[DatasetKind, ...]]:
        """Datasets present whose prerequisites are not.

        Reported rather than refused. A package of daily bars with no instrument
        master is legitimately useful for a smoke test; it just cannot resolve
        instrument references, and the owner should learn that at import.
        """
        have = set(self.datasets)
        out: dict[DatasetKind, tuple[DatasetKind, ...]] = {}
        for kind in self.datasets:
            missing = tuple(d for d in DATASET_SPECS[kind].requires if d not in have)
            if missing:
                out[kind] = missing
        return out

    def digest(self) -> str:
        """Deterministic identity for the package's *contents*.

        Built from the file hashes and the fields that change interpretation —
        not from the export date or the description, so re-writing a note does
        not mint a new dataset identity, and swapping a file does.
        """
        payload = "|".join(
            [
                str(self.format_version),
                self.name,
                self.provider,
                str(self.adjustment_policy),
                self.timezone,
                *sorted(f"{file.dataset}:{file.path}:{file.sha256}" for file in self.files),
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def snapshot_id(self) -> str:
        """The identity a result cites when asked which dataset produced it."""
        return f"{self.name}-{self.digest()[:16]}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "name": self.name,
            "provider": self.provider,
            "export_date": self.export_date.isoformat(),
            "coverage": {
                "start": self.coverage.start.isoformat(),
                "end": self.coverage.end.isoformat(),
                "instruments": self.coverage.instruments,
            },
            "timezone": self.timezone,
            "adjustment_policy": str(self.adjustment_policy),
            "datasets": [str(d) for d in self.datasets],
            "files": [
                {
                    "path": file.path,
                    "dataset": str(file.dataset),
                    "sha256": file.sha256,
                    "rows": file.rows,
                }
                for file in self.files
            ],
            "known_limitations": list(self.known_limitations),
            "licence_note": self.licence_note,
            "vendor_dataset": self.vendor_dataset,
            "provenance": self.provenance.to_payload() if self.provenance else None,
            "instrument_capabilities": [
                {
                    "instrument_id": entry.instrument_id,
                    "ticker": entry.ticker,
                    "flags": list(entry.flags),
                    "reason": entry.reason,
                    "split_provider": entry.split_provider,
                    "http_status": entry.http_status,
                }
                for entry in self.instrument_capabilities
            ],
            "acquisition": self.acquisition.to_payload() if self.acquisition else None,
            "digest": self.digest(),
            "snapshot_id": self.snapshot_id(),
        }


def load_manifest(path: Path) -> PackageManifest:
    """Read and validate a ``manifest.toml``.

    TOML rather than JSON or YAML: it has comments, which a person writing one
    by hand will need, and it has no significant whitespace, which a person
    writing one by hand will get wrong.
    """
    if not path.exists():
        raise DataError(
            f"no manifest at {path}. Every package needs exactly one manifest.toml "
            "at its root; see docs/DATA_PACKAGE_SPEC.md for a template."
        )
    with path.open("rb") as handle:
        try:
            raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            # The generated template deliberately contains bare ``PLEASE_SET``
            # tokens, which are not valid TOML, so this is the first thing an
            # operator sees if they try to import an unfilled template. The
            # parser's own message names the line; this one says what to do
            # about it, which the parser cannot know.
            raise ConfigError(
                f"{path} is not valid TOML: {error}. If this file was produced by "
                "build_manifest_template, every PLEASE_SET placeholder must be "
                "replaced with a real value first — they are left unparseable on "
                "purpose, so an unfilled template cannot be imported by accident."
            ) from error
    try:
        return PackageManifest.model_validate(raw)
    except ValidationError as error:
        # Pydantic's own message is precise but arrives as a traceback, which
        # is not what an operator fixing a hand-written TOML file needs.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}" for item in error.errors()
        )
        raise ConfigError(f"{path} is not a valid manifest — {problems}") from error


def verify_files(manifest: PackageManifest, root: Path) -> list[str]:
    """Check every declared file exists and hashes as claimed.

    Returns problems rather than raising, so an import report can list all of
    them at once. A person fixing a package should not have to discover its
    faults one run at a time.
    """
    problems: list[str] = []
    for file in manifest.files:
        target = root / file.path
        if not target.exists():
            problems.append(f"{file.path}: declared in the manifest but not present")
            continue
        actual = file_digest(target)
        if actual != file.sha256:
            problems.append(
                f"{file.path}: sha256 is {actual[:16]}… but the manifest says "
                f"{file.sha256[:16]}…. The file changed after the manifest was "
                "written; regenerate the manifest rather than editing the hash, "
                "so the provenance describes the bytes that were actually read."
            )
    declared = {file.path for file in manifest.files}
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.name == "manifest.toml":
            continue
        relative = str(candidate.relative_to(root))
        if candidate.relative_to(root).parts[0] == WORKSPACE_DIRNAME:
            continue
        if relative not in declared and any(
            relative.endswith(suffix) for suffix in SUPPORTED_SUFFIXES
        ):
            problems.append(
                f"{relative}: present in the package but not declared in the "
                "manifest, so it will not be read"
            )
    return problems


def render_manifest(manifest: PackageManifest) -> str:
    """Write a manifest back out as TOML.

    Hand-rendered rather than via a TOML writer: the project has no TOML
    serializer dependency and the shape is fixed. The one rule that has already
    caused a bug is encoded in the ordering — **every top-level key must precede
    the first table header**, or TOML silently nests it, and
    ``known_limitations`` emitted after ``[coverage]`` becomes
    ``coverage.known_limitations`` and fails validation against a field nobody
    wrote.

    Lives here rather than in the acquisition runner because two writers now
    produce manifests — the runner when it acquires, and the enricher when a
    second vendor adds to an existing package — and two hand-rolled TOML
    emitters would drift on exactly the ordering rule above.
    """
    lines = [
        "# Generated by tradeit. Every hash below was computed from the file as",
        "# written; re-running acquisition or enrichment regenerates this file.",
        "",
        f"format_version = {manifest.format_version}",
        f"name = {_toml_string(manifest.name)}",
        f"provider = {_toml_string(manifest.provider)}",
        f"export_date = {manifest.export_date.isoformat()}",
        f"timezone = {_toml_string(manifest.timezone)}",
        f"adjustment_policy = {_toml_string(str(manifest.adjustment_policy))}",
        f"vendor_dataset = {_toml_string(manifest.vendor_dataset)}",
        f"description = {_toml_string(manifest.description)}",
    ]
    if manifest.licence_note:
        lines.append(f"licence_note = {_toml_string(manifest.licence_note)}")
    if manifest.date_formats:
        rendered = ", ".join(_toml_string(item) for item in manifest.date_formats)
        lines.append(f"date_formats = [{rendered}]")
    if manifest.decimal_comma:
        lines.append("decimal_comma = true")
    lines += [
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

    if manifest.provenance is not None:
        source = manifest.provenance
        lines += [
            "# Which vendor supplied which part. A reconstructed raw price derived",
            "# from one vendor's adjusted bars and another's split schedule was",
            "# supplied by neither, and this table is what says so.",
            "[provenance]",
            f"price_provider = {_toml_string(source.price_provider)}",
            f"price_representation = {_toml_string(source.price_representation)}",
            f"dividend_provider = {_toml_string(source.dividend_provider)}",
            f"split_provider = {_toml_string(source.split_provider)}",
            f"reconstruction_performed = {'true' if source.reconstruction_performed else 'false'}",
            f"reconstruction_algorithm = {_toml_string(source.reconstruction_algorithm)}",
            f"reconstruction_label = {_toml_string(source.reconstruction_label)}",
            f"reconstruction_file = {_toml_string(source.reconstruction_file)}",
            "",
        ]

    if manifest.acquisition is not None:
        record = manifest.acquisition
        lines += [
            "# What was requested and what came back. The requested window is not the",
            "# coverage above: coverage is what arrived, and a security that stopped",
            "# trading before the request began could never have been in it.",
            "[acquisition]",
            f"provider = {_toml_string(record.provider)}",
        ]
        if record.requested_start is not None:
            lines.append(f"requested_start = {record.requested_start.isoformat()}")
        if record.requested_end is not None:
            lines.append(f"requested_end = {record.requested_end.isoformat()}")
        lines.append("")
        for outcome in record.outcomes:
            lines += [
                "[[acquisition.outcomes]]",
                f"ticker = {_toml_string(outcome.ticker)}",
                f"symbol_status = {_toml_string(outcome.symbol_status)}",
                f"fetch_status = {_toml_string(outcome.fetch_status)}",
                f"bars = {outcome.bars}",
            ]
            if outcome.error:
                lines.append(f"error = {_toml_string(outcome.error)}")
            lines.append("")

    if manifest.instrument_capabilities:
        lines += [
            "# What each instrument's data supports. A universe acquisition is not",
            "# uniform: price history and a verified split schedule are different",
            "# facts, and a result that mixed them could not be interpreted.",
        ]
        for entry in manifest.instrument_capabilities:
            rendered_flags = ", ".join(_toml_string(flag) for flag in entry.flags)
            lines += [
                "[[instrument_capabilities]]",
                f"instrument_id = {entry.instrument_id}",
                f"ticker = {_toml_string(entry.ticker)}",
                f"flags = [{rendered_flags}]",
            ]
            if entry.reason:
                lines.append(f"reason = {_toml_string(entry.reason)}")
            if entry.split_provider:
                lines.append(f"split_provider = {_toml_string(entry.split_provider)}")
            if entry.http_status is not None:
                lines.append(f"http_status = {entry.http_status}")
            lines.append("")

    for file in manifest.files:
        lines += [
            "[[files]]",
            f"path = {_toml_string(file.path)}",
            f"dataset = {_toml_string(str(file.dataset))}",
            f"sha256 = {_toml_string(file.sha256)}",
        ]
        if file.rows is not None:
            lines.append(f"rows = {file.rows}")
        if file.note:
            lines.append(f"note = {_toml_string(file.note)}")
        if file.columns:
            rendered = ", ".join(
                f"{key} = {_toml_string(value)}" for key, value in sorted(file.columns.items())
            )
            lines.append(f"columns = {{ {rendered} }}")
        lines.append("")
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_manifest_template(
    root: Path,
    *,
    name: str,
    provider: str,
    datasets: Mapping[str, DatasetKind] | None = None,
) -> str:
    """Generate a starter ``manifest.toml`` for the files already in a directory.

    A convenience for the project owner, and deliberately incomplete: the
    adjustment policy, the coverage window and the timezone are emitted as
    placeholders that fail validation until someone fills them in. Emitting a
    plausible default for any of the three would let the most damaging kind of
    mistake pass unnoticed.
    """
    mapping = dict(datasets or {})
    lines = [
        "# Generated template. Fill in every PLEASE_SET value before importing.",
        "",
        f"format_version = {PACKAGE_FORMAT_VERSION}",
        f'name = "{name}"',
        f'provider = "{provider}"',
        f"export_date = {dt.datetime.now(tz=dt.UTC).date().isoformat()}",
        "",
        "# One of: raw_unadjusted, split_adjusted, total_return_adjusted, unknown.",
        "# There is deliberately no default: adjusted closes loaded as raw prices",
        "# describe a price history that never happened.",
        'adjustment_policy = "PLEASE_SET"',
        "",
        "# IANA zone the timestamp columns are expressed in.",
        'timezone = "PLEASE_SET"',
        "",
        "# What you already know is wrong or missing. Surfaced in every import",
        "# report: a package that declares its gaps is worth more than one that",
        "# appears complete.",
        "known_limitations = [",
        '  # "no delisted securities before 2010",',
        "]",
        'licence_note = ""',
        "",
        # Every top-level key must precede the first table header, or TOML reads
        # it as a member of that table. Emitting known_limitations after
        # [coverage] silently made it coverage.known_limitations, which failed
        # validation with a message pointing at the wrong field.
        "[coverage]",
        "start = PLEASE_SET  # e.g. 2004-01-02",
        "end = PLEASE_SET    # e.g. 2024-12-31",
        "",
    ]
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.name == "manifest.toml":
            continue
        relative = str(candidate.relative_to(root))
        if not any(relative.endswith(suffix) for suffix in SUPPORTED_SUFFIXES):
            continue
        kind = mapping.get(relative)
        lines += [
            "[[files]]",
            f'path = "{relative}"',
            f'dataset = "{kind or "PLEASE_SET"}"',
            f'sha256 = "{file_digest(candidate)}"',
            '# columns = { session_date = "date", close = "close_price" }',
            "",
        ]
    return "\n".join(lines)


__all__ = [
    "SUPPORTED_SUFFIXES",
    "WORKSPACE_DIRNAME",
    "Coverage",
    "DatasetFile",
    "InstrumentCapabilityEntry",
    "PackageManifest",
    "Provenance",
    "build_manifest_template",
    "file_digest",
    "load_manifest",
    "render_manifest",
    "verify_files",
]
