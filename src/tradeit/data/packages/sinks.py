"""Where imported rows go, kept separate from how they are produced.

The gate is explicit that the core domain layer must not be coupled to a
specific export format, and the same reasoning applies in the other direction:
the pipeline that reads, normalizes, validates and dates a row should not know
whether the result lands in PostgreSQL, in a JSONL file, or in a list held by a
test. So the pipeline talks to a :class:`RecordSink` and nothing else.

That buys three things that are hard to retrofit. A dry run is a real import
against :class:`CountingSink` — every stage executes, every quarantine decision
is made, and nothing is written, so "what would this package do to my database?"
is answerable before it does it. The unit suite runs the whole pipeline against
:class:`CollectingSink` with no database at all. And an operator whose target is
not this schema writes one class rather than forking the importer.

Sinks receive :class:`~tradeit.data.packages.stages.PointInTimeRecord`, never
raw or normalized rows. Nothing that has not been dated may be persisted,
because a row without a knowledge_time is a row a backtest can read too early,
and making that unrepresentable is cheaper than remembering to check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TextIO, runtime_checkable

from tradeit.data.packages.stages import (
    Correction,
    PointInTimeRecord,
    QuarantineEntry,
    RawRecord,
)


@runtime_checkable
class RecordSink(Protocol):
    """The importer's entire view of storage."""

    def write(self, record: PointInTimeRecord) -> None:
        """Accept one dated, validated row."""

    def quarantine(self, entry: QuarantineEntry) -> None:
        """Accept one row that could not proceed, with its reason."""

    def correction(self, record: RawRecord, correction: Correction) -> None:
        """Accept one traceable deviation from the source text."""

    def flush(self) -> None:
        """Commit whatever is pending. Called at the end of each file."""


@dataclass(slots=True)
class CountingSink:
    """Counts and discards. The dry-run sink.

    Discarding is the point: a dry run that accumulated rows would run out of
    memory on exactly the large imports where a dry run is most valuable.
    """

    written: int = 0
    quarantined: int = 0
    corrections: int = 0

    def write(self, record: PointInTimeRecord) -> None:
        self.written += 1

    def quarantine(self, entry: QuarantineEntry) -> None:
        self.quarantined += 1

    def correction(self, record: RawRecord, correction: Correction) -> None:
        self.corrections += 1

    def flush(self) -> None:
        return None


@dataclass(slots=True)
class CollectingSink:
    """Keeps everything in memory. For tests and small packages only."""

    records: list[PointInTimeRecord] = field(default_factory=list)
    quarantined: list[QuarantineEntry] = field(default_factory=list)
    corrections: list[tuple[RawRecord, Correction]] = field(default_factory=list)

    def write(self, record: PointInTimeRecord) -> None:
        self.records.append(record)

    def quarantine(self, entry: QuarantineEntry) -> None:
        self.quarantined.append(entry)

    def correction(self, record: RawRecord, correction: Correction) -> None:
        self.corrections.append((record, correction))

    def flush(self) -> None:
        return None

    # -- convenience for assertions -----------------------------------------

    @property
    def written(self) -> int:
        return len(self.records)

    def reasons(self) -> list[str]:
        return [entry.reason for entry in self.quarantined]


@dataclass(slots=True)
class JsonlSink:
    """Streams records to newline-delimited JSON.

    Exists so that a package can be imported and inspected without a database —
    the situation this whole gate is written for. One file per dataset, plus a
    quarantine file and a corrections file, because an operator diagnosing an
    export wants to open the quarantine on its own.
    """

    directory: Path
    _handles: dict[str, TextIO] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def _handle(self, name: str) -> TextIO:
        handle = self._handles.get(name)
        if handle is None:
            handle = (self.directory / f"{name}.jsonl").open("w", encoding="utf-8")
            self._handles[name] = handle
        return handle

    def write(self, record: PointInTimeRecord) -> None:
        payload = {
            "dataset": str(record.dataset),
            "source": record.raw.identity,
            "knowledge_time": record.knowledge_time.isoformat(),
            "knowledge_source": str(record.knowledge_source),
            "values": {k: _jsonable(v) for k, v in record.values.items()},
            "flags": [str(f) for f in record.validated.flags],
        }
        self._handle(str(record.dataset)).write(json.dumps(payload, separators=(",", ":")) + "\n")

    def quarantine(self, entry: QuarantineEntry) -> None:
        payload = {
            "dataset": str(entry.dataset),
            "file": entry.source_file,
            "line": entry.line_number,
            "stage": str(entry.stage),
            "reason": entry.reason,
            "identifier": entry.identifier,
            "raw": json.loads(entry.payload),
        }
        self._handle("quarantine").write(json.dumps(payload, separators=(",", ":")) + "\n")

    def correction(self, record: RawRecord, correction: Correction) -> None:
        payload = {"source": record.identity, **correction.to_payload()}
        self._handle("corrections").write(json.dumps(payload, separators=(",", ":")) + "\n")

    def flush(self) -> None:
        for handle in self._handles.values():
            handle.flush()

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()


def _jsonable(value: object) -> object:
    import datetime as dt
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


__all__ = ["CollectingSink", "CountingSink", "JsonlSink", "RecordSink"]
