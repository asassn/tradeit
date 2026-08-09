"""The acquisition journal: one line per request, whatever happened.

A download that fails halfway leaves two questions — *what did I get?* and *what
should I do about the rest?* — and neither is answerable from a progress bar
that has already scrolled away. The journal answers both. It is append-only
JSONL, written as the run proceeds rather than at the end, so a process killed
mid-flight still leaves a complete record of everything before the kill.

Every line records: provider, dataset, symbol, requested range, request
timestamp, result status, rows received, source file, checksum, retry count, and
the failure message where there was one.

**No credentials, ever.** URLs are redacted before they reach here, the API key
is never a field, and a test walks a written journal asserting that a known
token string appears nowhere in it. That test exists because the natural way to
debug an auth failure is to log the request, and the natural way to log a
request is to log the URL.

The journal is also the input to a *retry* run: `failed_requests()` returns the
work that did not succeed, so a second invocation can attempt those and leave
the rest alone.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One recorded request."""

    provider: str
    dataset: str
    symbol: str
    requested_start: str | None
    requested_end: str | None
    requested_at: str
    status: str
    http_status: int | None
    rows: int
    source_file: str
    sha256: str
    attempts: int
    elapsed_s: float
    url: str
    error: str

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "symbol": self.symbol,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "requested_at": self.requested_at,
            "status": self.status,
            "http_status": self.http_status,
            "rows": self.rows,
            "source_file": self.source_file,
            "sha256": self.sha256,
            "attempts": self.attempts,
            "elapsed_s": round(self.elapsed_s, 3),
            "url": self.url,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> JournalEntry:
        return cls(
            provider=str(payload.get("provider", "")),
            dataset=str(payload.get("dataset", "")),
            symbol=str(payload.get("symbol", "")),
            requested_start=_optional_str(payload.get("requested_start")),
            requested_end=_optional_str(payload.get("requested_end")),
            requested_at=str(payload.get("requested_at", "")),
            status=str(payload.get("status", "")),
            http_status=_optional_int(payload.get("http_status")),
            rows=_as_int(payload.get("rows")) or 0,
            source_file=str(payload.get("source_file", "")),
            sha256=str(payload.get("sha256", "")),
            attempts=_as_int(payload.get("attempts")) or 1,
            elapsed_s=_as_float(payload.get("elapsed_s")),
            url=str(payload.get("url", "")),
            error=str(payload.get("error", "")),
        )

    @property
    def succeeded(self) -> bool:
        return FetchStatus(self.status).is_success if self.status else False


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else _as_int(value)


def _as_int(value: object) -> int:
    """Coerce a JSON scalar, treating anything unreadable as zero.

    A journal line is diagnostic output, not a fact anyone computes from, so a
    corrupt count must not stop the file being read — the reason someone is
    reading it is usually that something already went wrong.
    """
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class AcquisitionJournal:
    """Append-only record of every request a run issued."""

    path: Path
    entries: list[JournalEntry] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.entries is None:
            self.entries = []
        if self.path.exists():
            self.entries = self._read()

    def _read(self) -> list[JournalEntry]:
        out: list[JournalEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(JournalEntry.from_payload(json.loads(line)))
            except (json.JSONDecodeError, ValueError, TypeError):
                # A truncated final line is the expected shape of an
                # interrupted run. Skip it rather than refusing to read the
                # journal that explains the interruption.
                continue
        return out

    def record(
        self,
        provider: str,
        outcome: FetchOutcome,
        *,
        source_file: str = "",
        sha256: str = "",
    ) -> JournalEntry:
        request = outcome.request
        entry = JournalEntry(
            provider=provider,
            dataset=str(request.dataset),
            symbol=request.symbol,
            requested_start=request.start.isoformat() if request.start else None,
            requested_end=request.end.isoformat() if request.end else None,
            requested_at=dt.datetime.now(dt.UTC).isoformat(),
            status=str(outcome.status),
            http_status=outcome.http_status,
            rows=outcome.row_count,
            source_file=source_file,
            sha256=sha256,
            attempts=outcome.attempts,
            elapsed_s=outcome.elapsed_s,
            url=outcome.url,
            error=outcome.error,
        )
        self.entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_payload(), sort_keys=True) + "\n")
            handle.flush()
        return entry

    # -- reading the record --------------------------------------------------

    def status_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(e.status for e in self.entries).items()))

    def failures(self) -> list[JournalEntry]:
        return [e for e in self.entries if not e.succeeded]

    def failed_requests(self) -> list[FetchRequest]:
        """The work a retry run should attempt.

        Deduplicated on identity and filtered to statuses where retrying is
        plausible. A symbol the vendor rejected is *not* included — re-asking
        the same question expecting a different answer is how a bad key becomes
        a ban — and it is reported instead.
        """
        seen: set[tuple[str, str, str | None, str | None]] = set()
        out: list[FetchRequest] = []
        succeeded = {
            (e.dataset, e.symbol, e.requested_start, e.requested_end)
            for e in self.entries
            if e.succeeded
        }
        for entry in reversed(self.entries):
            key = (entry.dataset, entry.symbol, entry.requested_start, entry.requested_end)
            if entry.succeeded or key in succeeded or key in seen:
                continue
            if not FetchStatus(entry.status).is_retryable:
                continue
            seen.add(key)
            out.append(
                FetchRequest(
                    dataset=AcquisitionDataset(entry.dataset),
                    symbols=(entry.symbol,),
                    start=_as_date(entry.requested_start),
                    end=_as_date(entry.requested_end),
                )
            )
        return list(reversed(out))

    def rejected_symbols(self) -> list[str]:
        """Symbols the vendor refused. Reported, never retried automatically."""
        return sorted({e.symbol for e in self.entries if e.status == str(FetchStatus.REJECTED)})

    def rows_by_dataset(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for entry in self.entries:
            if entry.succeeded:
                totals[entry.dataset] = totals.get(entry.dataset, 0) + entry.rows
        return dict(sorted(totals.items()))


def _as_date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


__all__ = ["AcquisitionJournal", "JournalEntry"]
