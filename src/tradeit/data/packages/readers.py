"""Reading package files without letting the format leak into the domain.

The core domain layer knows about :class:`~tradeit.core.models.OhlcvBar`, not
about CSV. This module is the only place that knows a file can be gzipped, that
a ZIP archive might hold the whole package, or that Parquet needs ``pyarrow``.
Everything it returns is a plain ``dict[str, str | None]`` per row — deliberately
*not* typed values, because coercion is a separate step that has to be able to
quarantine a bad cell rather than raise on it.

Two decisions worth stating.

**Rows come back as strings.** A reader that parsed dates would have to decide
what to do with ``2019-02-30``, and the only useful answers are "quarantine this
row and say why" — which needs the row's identity and the dataset's contract,
neither of which a reader has. So parsing lives in the importer and readers stay
dumb.

**Nothing is streamed into memory whole unless it has to be.** CSV is read
line-by-line through :mod:`csv`, so a decade of minute bars does not have to fit
in RAM. Parquet is columnar and is read via pandas, which does load it; that is a
property of the format rather than a choice, and the docstring says so rather
than letting someone discover it at 40 GB.
"""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import IO

from tradeit.errors import DataError

#: Rows are dicts of source-column name to raw text. ``None`` means the cell was
#: absent or empty, which is different from the string "0" and must stay so.
RawRow = dict[str, str | None]


def _open_text(path: Path) -> IO[str]:
    if path.name.endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8-sig", newline="")
    return path.open(mode="r", encoding="utf-8-sig", newline="")


def _delimiter_for(name: str) -> str:
    return "\t" if ".tsv" in name else ","


def read_delimited(path: Path) -> Iterator[RawRow]:
    """Stream a CSV/TSV file, optionally gzipped.

    ``utf-8-sig`` rather than ``utf-8``: a byte-order mark on the first column
    name is common in vendor exports produced on Windows, and the resulting
    header ``\\ufeffdate`` matches nothing, which surfaces as "the manifest maps
    a column that is not in the file" — a confusing way to report an encoding
    detail.
    """
    with _open_text(path) as handle:
        reader = csv.DictReader(handle, delimiter=_delimiter_for(path.name))
        if reader.fieldnames is None:
            return
        for row in reader:
            yield {key: _clean(value) for key, value in row.items() if key is not None}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def read_parquet(path: Path) -> Iterator[RawRow]:
    """Read a Parquet file, if the optional dependency is installed.

    Parquet is columnar, so pandas materialises the whole file. That is the
    format's nature rather than this function's choice; for very large exports,
    CSV.gz streams and Parquet does not.
    """
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - pandas is a hard dependency
        raise DataError("pandas is required to read Parquet files") from error
    try:
        frame = pd.read_parquet(path)
    except ImportError as error:
        raise DataError(
            f"reading {path.name} needs the optional 'pyarrow' package, which is not "
            "installed. Either install it (pip install pyarrow) or re-export the "
            "package as CSV or CSV.gz, which this importer reads without extra "
            "dependencies."
        ) from error
    for record in frame.to_dict(orient="records"):
        yield {str(key): _stringify(value) for key, value in record.items()}


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    return text


def read_rows(path: Path) -> Iterator[RawRow]:
    """Read any supported file, dispatching on extension."""
    if path.name.endswith(".parquet"):
        yield from read_parquet(path)
    else:
        yield from read_delimited(path)


def count_rows(path: Path) -> int:
    """Row count without holding the file, for the import report."""
    return sum(1 for _ in read_rows(path))


def extract_archive(archive: Path, destination: Path) -> Path:
    """Unpack a ZIP package into a directory and return its root.

    Refuses entries with absolute paths or ``..`` segments. A ZIP that writes
    outside its extraction directory is a well-known attack and an easy accident;
    either way the result is files landing somewhere nobody looked, so the
    archive is rejected rather than partially extracted.
    """
    if not zipfile.is_zipfile(archive):
        raise DataError(f"{archive} is not a ZIP archive")
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            target = Path(member)
            if target.is_absolute() or ".." in target.parts:
                raise DataError(
                    f"archive entry {member!r} would write outside the extraction "
                    "directory; refusing the whole archive rather than extracting "
                    "part of it"
                )
        bundle.extractall(destination)

    return _package_root(destination)


def _package_root(directory: Path) -> Path:
    """Find the directory holding ``manifest.toml``.

    Archives are commonly built with a single top-level folder, so the manifest
    is one level down. Looking for it rather than assuming either layout means a
    package produced by ``zip -r`` and one produced by a GUI both work.
    """
    if (directory / "manifest.toml").exists():
        return directory
    candidates = sorted(directory.glob("*/manifest.toml"))
    if len(candidates) == 1:
        return candidates[0].parent
    if len(candidates) > 1:
        raise DataError(
            f"{directory} contains {len(candidates)} manifests; a package must have "
            "exactly one so that its provenance is unambiguous"
        )
    raise DataError(
        f"no manifest.toml found in {directory} or one level below it. See "
        "docs/DATA_PACKAGE_SPEC.md for the expected layout."
    )


def resolve_package(source: Path, workspace: Path | None = None) -> Path:
    """Return the package root for a directory or an archive.

    A directory is used in place; an archive is extracted to ``workspace``. The
    caller owns the workspace so a temporary extraction can be cleaned up and a
    persistent one can be kept for provenance.
    """
    if source.is_dir():
        return _package_root(source)
    if source.suffix.lower() == ".zip":
        if workspace is None:
            raise DataError("extracting an archive needs a workspace directory")
        return extract_archive(source, workspace)
    raise DataError(
        f"{source} is neither a directory nor a .zip archive. A package is a "
        "directory containing manifest.toml, or a ZIP of one."
    )


def peek_header(path: Path, limit: int = 1) -> list[str]:
    """Column names as they appear in the file.

    Used by the import report to say "your file has these columns" next to
    "the manifest maps these", which turns a mapping mistake from a puzzle into
    a diff.
    """
    if path.name.endswith(".parquet"):
        with suppress(Exception):
            import pandas as pd

            return [str(c) for c in pd.read_parquet(path).columns]
        return []
    with _open_text(path) as handle:
        reader = csv.reader(handle, delimiter=_delimiter_for(path.name))
        for row in reader:
            return [cell.strip() for cell in row][: limit * 1000]
    return []


def write_delimited(path: Path, rows: list[RawRow], columns: list[str]) -> None:
    """Write rows back out. Used for quarantine files and test fixtures.

    Quarantined rows are written as they arrived, not as they were interpreted,
    so a person fixing an export sees their own data rather than the importer's
    reading of it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("" if value is None else value) for key, value in row.items()})
    path.write_text(buffer.getvalue(), encoding="utf-8")


__all__ = [
    "RawRow",
    "count_rows",
    "extract_archive",
    "peek_header",
    "read_delimited",
    "read_parquet",
    "read_rows",
    "resolve_package",
    "write_delimited",
]
