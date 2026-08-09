"""The stages a row passes through, kept as separate objects on purpose.

The gate requires that RAW, NORMALIZED, VALIDATED and POINT-IN-TIME stay
distinguishable, that source values are never overwritten during normalization,
and that corrections are traceable. The cheapest way to satisfy all three at
once is to make each stage a different type that *contains* the previous one
rather than replacing it.

So :class:`NormalizedRecord` holds its :class:`RawRecord`, not a copy of some of
its fields; :class:`ValidatedRecord` holds its normalized record; and
:class:`PointInTimeRecord` holds the validated one. At any depth you can ask
"what did the file actually say here?" and get the answer without a join. The
memory cost is real and it is the price of being able to answer that question.

Each deviation from the file is a :class:`Correction` naming the field, the raw
text, the value substituted, the rule that did it and why. A normalization with
no corrections attached made no changes; that is checkable, and it is checked.

**Where the raw bytes live.** Not here. The package file itself is the raw
record — immutable on disk with its SHA-256 in the manifest — and
:class:`RawRecord` carries the file, the line and the verbatim cell values so a
row can be pointed at. Copying every byte of a 40 GB export into the database to
call it "preserved" would cost storage without adding a guarantee the digest
does not already give.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from tradeit.core.enums import DataQualityFlag, KnowledgeTimeSource
from tradeit.data.packages.readers import RawRow
from tradeit.data.packages.spec import DatasetKind


class Stage(StrEnum):
    """Where a row is in the pipeline. Persisted on quarantine entries.

    Knowing that a row died at NORMALIZED rather than VALIDATED is the
    difference between "the vendor writes dates as DD/MM/YYYY" and "the vendor's
    high is below its low". Those want different fixes from different people.
    """

    RAW = "raw"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    POINT_IN_TIME = "point_in_time"
    DERIVED = "derived"

    @property
    def describe(self) -> str:
        return _STAGE_DESCRIPTIONS[self]


_STAGE_DESCRIPTIONS: Mapping[Stage, str] = {
    Stage.RAW: (
        "exactly what the file said, as text. Never modified, never re-encoded, never re-ordered."
    ),
    Stage.NORMALIZED: (
        "source columns renamed to the canonical contract and cell text coerced "
        "to types. Every deviation from the raw text is recorded as a correction."
    ),
    Stage.VALIDATED: (
        "internal consistency checked — high above low, volume non-negative, "
        "intervals not inverted. Suspicious-but-usable rows carry quality flags "
        "rather than being dropped."
    ),
    Stage.POINT_IN_TIME: (
        "a knowledge_time attached, with its provenance. This is the only stage "
        "a backtest may read, because it is the only one that knows when a fact "
        "became knowable."
    ),
    Stage.DERIVED: (
        "computed from point-in-time facts. Not produced by the importer; named "
        "here so the vocabulary is complete."
    ),
}


def _canonical(value: object) -> object:
    """JSON-safe rendering that keeps Decimal exact.

    ``float(Decimal("0.1"))`` is a different number, and a digest computed over
    the float would not identify the row that was actually imported.
    """
    if isinstance(value, Decimal):
        return f"D:{value}"
    if isinstance(value, dt.datetime):
        return f"DT:{value.isoformat()}"
    if isinstance(value, dt.date):
        return f"d:{value.isoformat()}"
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    return value


def row_digest(values: Mapping[str, object]) -> str:
    """Stable content hash of a row, independent of column order."""
    payload = json.dumps(
        {str(k): _canonical(v) for k, v in sorted(values.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One line of one file, verbatim.

    ``line_number`` is 1-based and counts the header, so it matches what a text
    editor shows. A person told "row 4,318 is bad" should be able to press
    ctrl-G and land on it.
    """

    dataset: DatasetKind
    source_file: str
    line_number: int
    values: RawRow

    @property
    def identity(self) -> str:
        return f"{self.dataset}:{self.source_file}:{self.line_number}"

    def digest(self) -> str:
        return row_digest(dict(self.values))

    def payload(self) -> str:
        """The row as JSON text, for the quarantine table.

        Written from the raw cells, so a quarantined row shows the operator
        their own data rather than the importer's reading of it.
        """
        return json.dumps(self.values, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class Correction:
    """A single traceable deviation from the source text.

    ``rule`` names the code that made the change so a surprising correction can
    be found and argued with; ``reason`` says why in words an operator can act
    on. Both, because "trim_whitespace" without a reason is opaque and "the
    vendor pads with spaces" without a rule name is unfindable.
    """

    field: str
    raw: str | None
    corrected: str
    rule: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.raw!r} -> {self.corrected!r} [{self.rule}] {self.reason}"

    def to_payload(self) -> dict[str, object]:
        return {
            "field": self.field,
            "raw": self.raw,
            "corrected": self.corrected,
            "rule": self.rule,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """Typed values under canonical names, with the raw row still attached."""

    raw: RawRecord
    values: dict[str, Any]
    corrections: tuple[Correction, ...] = ()

    @property
    def dataset(self) -> DatasetKind:
        return self.raw.dataset

    @property
    def was_modified(self) -> bool:
        return bool(self.corrections)

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def require(self, name: str) -> Any:
        if name not in self.values or self.values[name] is None:
            raise KeyError(f"{self.raw.identity} has no value for {name!r}")
        return self.values[name]


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    """A normalized row that passed its dataset's consistency rules.

    ``flags`` carries the non-fatal findings. A row with
    ``SUSPECT_ZERO_VOLUME`` is imported and flagged rather than rejected,
    because dropping it makes a data problem look like a market holiday — the
    reasoning already recorded on :class:`~tradeit.core.enums.DataQualityFlag`.
    """

    normalized: NormalizedRecord
    flags: tuple[DataQualityFlag, ...] = ()

    @property
    def raw(self) -> RawRecord:
        return self.normalized.raw

    @property
    def dataset(self) -> DatasetKind:
        return self.normalized.dataset

    @property
    def values(self) -> dict[str, Any]:
        return self.normalized.values

    @property
    def is_clean(self) -> bool:
        return not [f for f in self.flags if f is not DataQualityFlag.OK]


@dataclass(frozen=True, slots=True)
class PointInTimeRecord:
    """A validated row with the instant it became knowable, and how we know.

    ``knowledge_source`` is not decoration. A fundamental fact whose
    knowledge_time was derived from a lag rule is
    :attr:`~tradeit.core.enums.KnowledgeTimeSource.ESTIMATED`, and any result
    computed from it inherits that caveat. The importer counts estimated rows
    per dataset and the report states the count, because a validation run over
    silently-estimated timestamps is a validation of the lag rule.
    """

    validated: ValidatedRecord
    knowledge_time: dt.datetime
    knowledge_source: KnowledgeTimeSource
    knowledge_note: str = ""

    @property
    def raw(self) -> RawRecord:
        return self.validated.raw

    @property
    def dataset(self) -> DatasetKind:
        return self.validated.dataset

    @property
    def values(self) -> dict[str, Any]:
        return self.validated.values

    @property
    def is_estimated(self) -> bool:
        return self.knowledge_source is KnowledgeTimeSource.ESTIMATED


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    """A row that could not proceed, kept with its reason and its stage.

    Quarantine is not deletion and not a warning log. The row is retained
    verbatim, addressable by file and line, so that "why is there no bar for
    2021-03-04?" is a query rather than an investigation. Nothing in the
    importer may discard a row by any other route.
    """

    dataset: DatasetKind
    source_file: str
    line_number: int
    stage: Stage
    reason: str
    payload: str
    identifier: str | None = None

    @classmethod
    def of(
        cls,
        record: RawRecord,
        *,
        stage: Stage,
        reason: str,
        identifier: str | None = None,
    ) -> QuarantineEntry:
        return cls(
            dataset=record.dataset,
            source_file=record.source_file,
            line_number=record.line_number,
            stage=stage,
            reason=reason,
            payload=record.payload(),
            identifier=identifier,
        )

    def __str__(self) -> str:
        where = f"{self.source_file}:{self.line_number}"
        return f"[{self.stage}] {where} {self.reason}"


@dataclass(slots=True)
class DatasetOutcome:
    """Per-dataset counters for the import report.

    Kept as counts rather than as the rows themselves so that importing ten
    million bars does not require holding ten million objects to report on them.
    """

    dataset: DatasetKind
    files: int = 0
    rows_read: int = 0
    rows_normalized: int = 0
    rows_validated: int = 0
    rows_point_in_time: int = 0
    rows_quarantined: int = 0
    rows_corrected: int = 0
    corrections: int = 0
    estimated_knowledge_time: int = 0
    flags: dict[str, int] = field(default_factory=dict)
    quarantine_reasons: dict[str, int] = field(default_factory=dict)

    def note_flags(self, flags: Sequence[DataQualityFlag]) -> None:
        for flag in flags:
            if flag is DataQualityFlag.OK:
                continue
            self.flags[str(flag)] = self.flags.get(str(flag), 0) + 1

    def note_quarantine(self, entry: QuarantineEntry) -> None:
        self.rows_quarantined += 1
        key = entry.reason.split(";")[0].strip()
        self.quarantine_reasons[key] = self.quarantine_reasons.get(key, 0) + 1

    @property
    def quarantine_rate(self) -> float:
        return self.rows_quarantined / self.rows_read if self.rows_read else 0.0

    def to_payload(self) -> dict[str, object]:
        return {
            "dataset": str(self.dataset),
            "files": self.files,
            "rows_read": self.rows_read,
            "rows_normalized": self.rows_normalized,
            "rows_validated": self.rows_validated,
            "rows_point_in_time": self.rows_point_in_time,
            "rows_quarantined": self.rows_quarantined,
            "rows_corrected": self.rows_corrected,
            "corrections": self.corrections,
            "estimated_knowledge_time": self.estimated_knowledge_time,
            "quarantine_rate": round(self.quarantine_rate, 6),
            "flags": dict(sorted(self.flags.items())),
            "quarantine_reasons": dict(sorted(self.quarantine_reasons.items())),
        }


__all__ = [
    "Correction",
    "DatasetOutcome",
    "NormalizedRecord",
    "PointInTimeRecord",
    "QuarantineEntry",
    "RawRecord",
    "Stage",
    "ValidatedRecord",
    "row_digest",
]
