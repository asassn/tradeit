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

**EDGAR ships two physically different layouts, and they are not
interchangeable.** An earlier version of this module assumed the pipe-delimited
one and was pointed at the fixed-width one; every authentic 1994 Q3 row was
silently skipped and the file reported zero filings. A zero that means "we could
not read the file" is indistinguishable, downstream, from a zero that means
"nothing happened that quarter" -- and in a survivorship denominator that is the
most dangerous kind of wrong there is. Hence :func:`parse_index_header`, which
reads the layout from the file's own header, and :func:`_assert_parse_plausible`,
which refuses to report an unreadable file as an empty one.

``master.idx`` -- **pipe-delimited**, CIK first::

    CIK|Company Name|Form Type|Date Filed|Filename
    --------------------------------------------------------------------------
    320193|APPLE INC|10-K|2023-11-03|edgar/data/320193/0000320193-23-000106.txt

``form.idx`` -- **fixed-width**, form type first::

    Form Type   Company Name          CIK       Date Filed   File Name
    ------------------------------------------------------------------------
    10-C        3COM CORP             738076    1994-08-24   edgar/data/738076/...

``company.idx`` -- fixed-width, company name first. Same machinery: the header
gives the order, so no filename-to-layout table has to be maintained.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tradeit.errors import DataError

__all__ = [
    "EDGAR_FIRST_QUARTER",
    "FETCH_RECIPE",
    "FullIndexRow",
    "IndexHeader",
    "IndexLayout",
    "IndexQuarter",
    "LocalFullIndexSource",
    "ParsedIndex",
    "accession_from_path",
    "full_index_url",
    "parse_full_index",
    "parse_index",
    "parse_index_header",
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

#: Header labels EDGAR uses, mapped to our field names. Longer labels are
#: matched before shorter ones so ``File Name`` is never mistaken for a
#: substring of something else.
_HEADER_LABELS: tuple[tuple[str, str], ...] = (
    ("company name", "company_name"),
    ("form type", "form_type"),
    ("date filed", "filed_at"),
    ("file name", "path"),
    ("filename", "path"),
    ("cik", "cik"),
)

_REQUIRED_FIELDS = frozenset({"cik", "company_name", "form_type", "filed_at", "path"})

#: Date formats seen across eras. ISO is universal in the modern files; the
#: others are cheap insurance and are tried in order.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d")

#: Above this many data lines, a high skip rate means the layout drifted rather
#: than that a few rows are corrupt.
_SKIP_GUARD_MIN_LINES = 100
_SKIP_GUARD_MAX_RATIO = 0.10


class IndexLayout(StrEnum):
    """The two physical shapes EDGAR ships, which are genuinely different.

    ``master.idx`` is pipe-delimited and ordered CIK first. ``form.idx`` and
    ``company.idx`` are **fixed-width** and ordered form-first and company-first
    respectively. Assuming one layout and reading the other silently yields zero
    rows, which is exactly the defect this module was rewritten to make
    impossible.
    """

    PIPE = "pipe"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class IndexHeader:
    """Column order and, for fixed-width files, where each column starts.

    Derived from the file's own header line rather than hard-coded per filename,
    so ``form.idx``, ``company.idx`` and ``master.idx`` are all handled by
    reading what the file says about itself.
    """

    layout: IndexLayout
    fields: tuple[str, ...]
    offsets: tuple[int, ...]
    raw: str


def _looks_like_header(line: str) -> bool:
    low = line.lower()
    return "cik" in low and "date filed" in low and ("form type" in low or "company name" in low)


def parse_index_header(line: str) -> IndexHeader:
    """Read column order (and fixed-width offsets) out of the header line."""
    low = line.lower()
    found: dict[str, int] = {}
    for label, field_name in _HEADER_LABELS:
        if field_name in found:
            continue
        position = low.find(label)
        if position >= 0:
            found[field_name] = position

    missing = _REQUIRED_FIELDS - set(found)
    if missing:
        raise DataError(
            f"unrecognised EDGAR index header {line.strip()!r}; missing {sorted(missing)}"
        )

    ordered = sorted(found.items(), key=lambda item: item[1])
    fields = tuple(name for name, _ in ordered)
    offsets = tuple(position for _, position in ordered)
    layout = IndexLayout.PIPE if "|" in line else IndexLayout.FIXED
    return IndexHeader(layout=layout, fields=fields, offsets=offsets, raw=line.rstrip())


def _coerce_date(text: str) -> dt.date | None:
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.UTC).date()
        except ValueError:
            continue
    return None


def _split_fixed(line: str, header: IndexHeader) -> list[str] | None:
    """Slice a fixed-width row at the header's column starts."""
    if len(line.rstrip()) <= header.offsets[-1]:
        return None
    bounds = [*header.offsets, len(line)]
    return [line[bounds[i] : bounds[i + 1]].strip() for i in range(len(header.offsets))]


def _split_fixed_fallback(line: str, header: IndexHeader) -> list[str] | None:
    """Recover a row whose long fields pushed past their column boundaries.

    The right-hand end of a row is rigidly structured -- path, then date, then
    CIK -- so it is peeled off by token. What remains is the two free-text
    fields, separated by the padding run of two or more spaces that the
    fixed-width layout guarantees. **This is not whitespace splitting**: form
    types like ``SC 13D`` and company names contain single spaces and survive
    intact, because only runs of two or more spaces are treated as a boundary.
    """
    # rsplit rather than searching for the CIK: a CIK also appears inside its
    # own path, so any index-of search finds the wrong occurrence. rsplit peels
    # exactly three tokens from the right and leaves the free text intact,
    # internal spacing and all.
    tail = line.rstrip().rsplit(maxsplit=3)
    if len(tail) < 4:
        return None
    prefix, cik_text, date_text, path = tail
    if not cik_text.isdigit() or _coerce_date(date_text) is None:
        return None

    parts = re.split(r"\s{2,}", prefix.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    first, second = parts[0].strip(), parts[1].strip()

    values: dict[str, str] = {"cik": cik_text, "filed_at": date_text, "path": path}
    text_fields = [f for f in header.fields if f in {"form_type", "company_name"}]
    if len(text_fields) != 2:
        return None
    values[text_fields[0]], values[text_fields[1]] = first, second
    return [values[name] for name in header.fields]


@dataclass(slots=True)
class ParsedIndex:
    """Rows plus the accounting needed to tell a real quarter from a broken read."""

    rows: list[FullIndexRow]
    quarter_label: str
    header: IndexHeader | None
    #: Non-empty lines below the separator that were candidates for parsing.
    data_lines_seen: int = 0
    #: Candidates that did not yield a row, with a sample retained for diagnosis.
    skipped: int = 0
    skipped_samples: tuple[str, ...] = ()
    #: Rows recovered by the fallback because column slicing failed.
    fallback_rows: int = 0

    @property
    def skip_ratio(self) -> float:
        return self.skipped / self.data_lines_seen if self.data_lines_seen else 0.0

    def summary(self) -> dict[str, object]:
        return {
            "quarter": self.quarter_label,
            "layout": str(self.header.layout) if self.header else None,
            "fields": list(self.header.fields) if self.header else None,
            "rows": len(self.rows),
            "data_lines_seen": self.data_lines_seen,
            "skipped": self.skipped,
            "skip_ratio": round(self.skip_ratio, 4),
            "fallback_rows": self.fallback_rows,
        }


def parse_index(text: str, *, quarter_label: str, strict: bool = True) -> ParsedIndex:
    """Parse an EDGAR index file, reading its layout from its own header.

    Individual malformed rows are skipped **with accounting** -- one corrupt line
    should not lose a quarter of hundreds of thousands. But a *format* mismatch
    must never masquerade as an empty quarter, so under ``strict`` this raises
    when the file plainly has data lines and none of them parsed, and when the
    skip rate over a large file indicates the layout drifted rather than that a
    few rows are bad.
    """
    header: IndexHeader | None = None
    seen_separator = False
    rows: list[FullIndexRow] = []
    data_lines = 0
    skipped = 0
    samples: list[str] = []
    fallbacks = 0

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if _SEPARATOR.match(line.strip()):
            seen_separator = True
            continue
        if header is None and _looks_like_header(line):
            header = parse_index_header(line)
            continue
        if not seen_separator:
            # Preamble. Nothing above the dashed rule is a filing.
            continue

        # Below the separator, every non-empty line is a filing candidate --
        # counted even when the header was unreadable, so that a file full of
        # data cannot be reported as an empty quarter.
        data_lines += 1
        if header is None:
            skipped += 1
            if len(samples) < 5:
                samples.append(line[:160])
            continue

        if header.layout is IndexLayout.PIPE:
            parts = [p.strip() for p in line.split("|")]
            values = parts if len(parts) >= len(header.fields) else None
        else:
            values = _split_fixed(line, header)
            if values is not None and not values[header.fields.index("cik")].isdigit():
                values = None
            if values is None:
                values = _split_fixed_fallback(line, header)
                if values is not None:
                    fallbacks += 1

        row = _row_from_values(values, header, quarter_label) if values else None
        if row is None:
            skipped += 1
            if len(samples) < 5:
                samples.append(line[:160])
            continue
        rows.append(row)

    parsed = ParsedIndex(
        rows=rows,
        quarter_label=quarter_label,
        header=header,
        data_lines_seen=data_lines,
        skipped=skipped,
        skipped_samples=tuple(samples),
        fallback_rows=fallbacks,
    )
    if strict:
        _assert_parse_plausible(parsed)
    return parsed


def _row_from_values(
    values: list[str] | None, header: IndexHeader, quarter_label: str
) -> FullIndexRow | None:
    if values is None:
        return None
    field_values = dict(zip(header.fields, values, strict=False))
    cik_text = field_values.get("cik", "")
    if not cik_text.isdigit():
        return None
    filed_at = _coerce_date(field_values.get("filed_at", ""))
    if filed_at is None:
        return None
    path = field_values.get("path", "")
    return FullIndexRow(
        cik=int(cik_text),
        company_name=field_values.get("company_name", "").strip(),
        form_type=field_values.get("form_type", "").strip().upper(),
        filed_at=filed_at,
        path=path,
        accession=accession_from_path(path),
        index_quarter=quarter_label,
    )


def _assert_parse_plausible(parsed: ParsedIndex) -> None:
    """A format failure must never look like a legitimately empty quarter.

    An empty quarter has no data lines. A broken parser has plenty of data lines
    and no rows. Those are different facts, and conflating them is how a
    zero-event year enters a survivorship denominator unchallenged.
    """
    if parsed.header is None and parsed.data_lines_seen == 0 and not parsed.rows:
        # Genuinely nothing to read: no header, no data. Callers treat this as
        # an empty file, which is a coverage fact rather than a parse failure.
        return
    if parsed.header is None:
        raise DataError(
            f"{parsed.quarter_label}: no recognisable EDGAR index header found; "
            "refusing to report zero filings for a file we could not read"
        )
    if parsed.data_lines_seen > 0 and not parsed.rows:
        raise DataError(
            f"{parsed.quarter_label}: {parsed.data_lines_seen} data lines and 0 parsed rows "
            f"(layout={parsed.header.layout}, fields={list(parsed.header.fields)}). "
            f"This is a format mismatch, not an empty quarter. First lines: "
            f"{list(parsed.skipped_samples[:2])}"
        )
    if (
        parsed.data_lines_seen >= _SKIP_GUARD_MIN_LINES
        and parsed.skip_ratio > _SKIP_GUARD_MAX_RATIO
    ):
        raise DataError(
            f"{parsed.quarter_label}: skipped {parsed.skipped} of {parsed.data_lines_seen} "
            f"lines ({parsed.skip_ratio:.1%}), above the {_SKIP_GUARD_MAX_RATIO:.0%} tolerance. "
            f"Samples: {list(parsed.skipped_samples[:2])}"
        )


def parse_full_index(text: str, *, quarter_label: str, strict: bool = True) -> list[FullIndexRow]:
    """Rows only. Returns a list rather than a generator so the format guard in
    :func:`parse_index` runs eagerly -- a generator would defer the check to
    iteration, which is precisely when nobody is looking.
    """
    return parse_index(text, quarter_label=quarter_label, strict=strict).rows


@dataclass(slots=True)
class LocalFullIndexSource:
    """Reads ``<root>/<year>/QTR<n>/<kind>.idx``.

    Missing quarters are recorded in :attr:`missing` rather than raised: an
    absent quarter is a coverage fact and the denominator reports it. A quarter
    that is *present but unreadable* is the opposite -- it raises, because a
    format failure reported as zero filings is how a broken parser becomes a
    published number.
    """

    root: Path
    kind: str = "form"
    missing: list[str] = field(default_factory=list)
    stats: list[ParsedIndex] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def path_for(self, quarter: IndexQuarter) -> Path:
        return self.root / str(quarter.year) / f"QTR{quarter.quarter}" / f"{self.kind}.idx"

    def rows(self, start: IndexQuarter, end: IndexQuarter) -> Iterator[FullIndexRow]:
        for quarter in quarters(start, end):
            path = self.path_for(quarter)
            if not path.exists():
                self.missing.append(quarter.label)
                continue
            text = path.read_text(encoding="latin-1")
            parsed = parse_index(text, quarter_label=quarter.label)
            self.stats.append(parsed)
            yield from parsed.rows
