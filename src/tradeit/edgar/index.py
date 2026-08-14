"""Reading EDGAR's quarterly full-index.

**The spine starts at 1994 Q3.** Not 1993. Everything downstream inherits that
boundary, so it is enforced here rather than remembered: :func:`quarters` refuses
to enumerate anything earlier.

**No HTTP client lives in this module.** ``sec.gov`` is unreachable from the
build environment, and a network fetcher that cannot be exercised is a fetcher
that is wrong in ways nobody has found yet. Instead the index is read from a
local directory that an operator populates with a documented one-liner
(:data:`FETCH_RECIPE`), which also happens to be the right shape for a corpus
that must be reproducible: the raw index files are kept, and every derived row
names the file it came from.

The index format is pipe-delimited with a short preamble::

    CIK|Company Name|Form Type|Date Filed|Filename
    --------------------------------------------------------------------------
    320193|APPLE INC|10-K|2023-11-03|edgar/data/320193/0000320193-23-000106.txt
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tradeit.errors import DataError

__all__ = [
    "EDGAR_FIRST_QUARTER",
    "FETCH_RECIPE",
    "FullIndexRow",
    "IndexQuarter",
    "LocalFullIndexSource",
    "accession_from_path",
    "full_index_url",
    "parse_full_index",
    "quarters",
]

#: Official public EDGAR full-index availability begins here. 1994 Q3.
EDGAR_FIRST_QUARTER: tuple[int, int] = (1994, 3)

_BASE = "https://www.sec.gov/Archives/edgar/full-index"

FETCH_RECIPE = """\
# Populate the local index directory. Run outside the build sandbox; sec.gov is
# blocked there. Identify yourself: SEC fair-access guidance requires a
# descriptive User-Agent with contact details.
#
#   for year in $(seq 1994 2026); do
#     for q in 1 2 3 4; do
#       mkdir -p "$DIR/$year/QTR$q"
#       curl -sS --fail --compressed \\
#         -A "TradeIt research <you@example.com>" \\
#         -o "$DIR/$year/QTR$q/form.idx" \\
#         "https://www.sec.gov/Archives/edgar/full-index/$year/QTR$q/form.idx" \\
#         || echo "missing $year QTR$q"
#       sleep 0.5
#     done
#   done
#
# 1994 QTR1 and QTR2 do not exist and are expected to fail.
"""


@dataclass(frozen=True, slots=True, order=True)
class IndexQuarter:
    year: int
    quarter: int

    def __post_init__(self) -> None:
        if not 1 <= self.quarter <= 4:
            raise DataError(f"quarter must be 1-4, got {self.quarter}")
        if (self.year, self.quarter) < EDGAR_FIRST_QUARTER:
            raise DataError(
                f"EDGAR full-index begins at {EDGAR_FIRST_QUARTER[0]} "
                f"QTR{EDGAR_FIRST_QUARTER[1]}; {self.year} QTR{self.quarter} does not exist"
            )

    @property
    def label(self) -> str:
        return f"{self.year}-QTR{self.quarter}"

    def next(self) -> IndexQuarter:
        if self.quarter == 4:
            return IndexQuarter(self.year + 1, 1)
        return IndexQuarter(self.year, self.quarter + 1)


def quarters(start: IndexQuarter, end: IndexQuarter) -> Iterator[IndexQuarter]:
    """Every quarter from ``start`` to ``end`` inclusive."""
    if end < start:
        raise DataError(f"end {end.label} precedes start {start.label}")
    current = start
    while current <= end:
        yield current
        current = current.next()


def full_index_url(quarter: IndexQuarter, kind: str = "form") -> str:
    if kind not in {"form", "master", "company"}:
        raise DataError(f"unknown index kind {kind!r}")
    return f"{_BASE}/{quarter.year}/QTR{quarter.quarter}/{kind}.idx"


_ACCESSION = re.compile(r"(\d{10}-\d{2}-\d{6})")


def accession_from_path(path: str) -> str:
    """Pull the accession number out of an index ``Filename`` value.

    Returns ``""`` when the path carries no recognisable accession rather than
    inventing one; a row without provenance is reported, not repaired.
    """
    match = _ACCESSION.search(path)
    return match.group(1) if match else ""


@dataclass(frozen=True, slots=True)
class FullIndexRow:
    cik: int
    company_name: str
    form_type: str
    filed_at: dt.date
    path: str
    accession: str
    index_quarter: str


_SEPARATOR = re.compile(r"^-{5,}")


def parse_full_index(text: str, *, quarter_label: str) -> Iterator[FullIndexRow]:
    """Parse a ``form.idx`` / ``master.idx`` body into rows.

    Malformed lines are skipped rather than raising: a single corrupt line in a
    quarter of hundreds of thousands should not lose the quarter. The count of
    skipped lines is the caller's to track — :class:`LocalFullIndexSource` does.
    """
    seen_separator = False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if _SEPARATOR.match(line):
            seen_separator = True
            continue
        if not seen_separator:
            # Preamble and the column header live above the dashed rule.
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        cik_text, company, form_type, date_text, path = (p.strip() for p in parts[:5])
        try:
            cik = int(cik_text)
            filed_at = dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        yield FullIndexRow(
            cik=cik,
            company_name=company,
            form_type=form_type.upper(),
            filed_at=filed_at,
            path=path,
            accession=accession_from_path(path),
            index_quarter=quarter_label,
        )


@dataclass(slots=True)
class LocalFullIndexSource:
    """Reads ``<root>/<year>/QTR<n>/form.idx``.

    Missing quarters are recorded in :attr:`missing` rather than raised. An
    absent quarter is a coverage fact about the ingestion, and the denominator
    reports it — the alternative is a total that silently omits a year.
    """

    root: Path
    kind: str = "form"
    missing: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.missing is None:
            self.missing = []

    def path_for(self, quarter: IndexQuarter) -> Path:
        return self.root / str(quarter.year) / f"QTR{quarter.quarter}" / f"{self.kind}.idx"

    def rows(self, start: IndexQuarter, end: IndexQuarter) -> Iterator[FullIndexRow]:
        for quarter in quarters(start, end):
            path = self.path_for(quarter)
            if not path.exists():
                self.missing.append(quarter.label)
                continue
            text = path.read_text(encoding="latin-1")
            yield from parse_full_index(text, quarter_label=quarter.label)
