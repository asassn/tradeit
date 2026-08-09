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
        if relative not in declared and any(
            relative.endswith(suffix) for suffix in SUPPORTED_SUFFIXES
        ):
            problems.append(
                f"{relative}: present in the package but not declared in the "
                "manifest, so it will not be read"
            )
    return problems


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
    "Coverage",
    "DatasetFile",
    "PackageManifest",
    "build_manifest_template",
    "file_digest",
    "load_manifest",
    "verify_files",
]
