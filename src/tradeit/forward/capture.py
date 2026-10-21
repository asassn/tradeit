"""Capturing the forward record, where every outcome is written down.

Moved out of ``scripts/`` on 2026-10-20 and given tests, for a reason worth
stating: **this is intended to run unattended, daily, for years**, and the one
behaviour that matters is the one that is invisible when it breaks.

The behaviour: *every outcome is recorded, including the silent ones.* A symbol
we asked about and got nothing for produces a row. If a future change made the
capture skip those — a plausible "tidy up, we only need the rows that have
data" — the record would lose exactly the observations that make it
survivorship-honest, and it would do so without any error, any failed test, and
any visible change until somebody tried to measure a delisting years later.

That is the same defect the historical corpus has, which took months to
characterise and cannot be fixed. Here it is one line away and silent. So it
has a test.

What a forward record buys
--------------------------

A historical universe must be reconstructed, and the companies that failed are
the hardest to find — `EDGAR_DELISTING_DENOMINATOR.md` §7be records the
denominator as blind to exchange delistings before roughly 2002. Forward, the
universe is written down **before anyone knows which members will fail**, so a
delisting is observed rather than inferred from an absence that might equally
be a vendor's coverage gap.

Asking twice is two observations, not a duplicate
--------------------------------------------------

``asked_at`` is part of the key. A vendor may revise, and *"we asked on Monday
and got nothing; we asked again on Friday and got a price"* is two facts, both
of which are evidence. Re-running a capture therefore adds rows rather than
replacing them, and the same ask at the same instant is idempotent.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.acquisition.base import AcquisitionDataset, FetchOutcome, FetchRequest
from tradeit.storage.tables import ForwardObservation

__all__ = ["CaptureReport", "CaptureSource", "capture_forward"]


class CaptureSource(Protocol):
    """The slice of an acquisition adapter this needs.

    A protocol rather than the concrete `TiingoAcquisition`, so the capture can
    be tested against a stub that returns silences on demand. The live source
    cannot be made to delist a company for a test, and a capture whose silent
    path is only exercised in production has no test at all.
    """

    def has_credential(self) -> bool: ...

    def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]: ...

    def fetch(self, request: FetchRequest) -> FetchOutcome: ...


class NoCredential(RuntimeError):
    """Refused rather than run, because a day not captured cannot be caught up."""


@dataclass(frozen=True, slots=True)
class CaptureReport:
    """What one capture asked for and what it recorded."""

    session_date: dt.date
    asked_at: dt.datetime
    requested: int
    recorded: int
    silent: int
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Whether every request produced a recorded observation.

        **Not whether every request returned data.** A silence is a successful
        observation; an unrecorded request is a hole in the record.
        """
        return self.recorded == self.requested

    def describe(self) -> str:
        lines = [
            f"{self.session_date}: {self.requested} requested, "
            f"{self.recorded} recorded, {self.silent} silent",
        ]
        for status, count in sorted(self.by_status.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {status:<12} {count:>5}")
        if not self.complete:
            lines.append(
                f"  {self.requested - self.recorded} request(s) produced no observation "
                "at all, which is a hole in the record rather than a quiet market"
            )
        return "\n".join(lines)


def capture_forward(
    session: Session,
    source: CaptureSource,
    symbols: Sequence[str],
    *,
    start: dt.date,
    end: dt.date,
    asked_at: dt.datetime,
    journal: object | None = None,
    provider: str = "tiingo",
) -> CaptureReport:
    """Ask about every symbol and record every answer, including silence.

    ``asked_at`` is **required and passed in**, never read from the clock here.
    A capture replayed over history, or one crossing midnight UTC mid-run, must
    stamp every row with one consistent instant — and a function that read the
    clock per row could not be tested for the idempotency its unique
    constraint provides.

    Raises :class:`NoCredential` rather than recording an empty run. An empty
    forward record is indistinguishable from a quiet market, and that is the one
    thing it must never be mistaken for.
    """
    if not source.has_credential():
        raise NoCredential(
            "no credential for the forward source. A day not captured cannot be "
            "caught up later, so this refuses rather than writing an empty run that "
            "looks like a quiet market."
        )

    requests = [
        request
        for request in source.plan(list(symbols), start, end)
        if request.dataset is AcquisitionDataset.DAILY_PRICES
    ]
    statuses: Counter[str] = Counter()
    recorded = 0
    silent = 0

    for request in requests:
        outcome = source.fetch(request)
        statuses[str(outcome.status)] += 1
        rows = len(outcome.rows)
        if journal is not None:
            journal.record(provider, outcome)  # type: ignore[attr-defined]
        existing = session.execute(
            select(ForwardObservation).where(
                ForwardObservation.symbol == request.symbol,
                ForwardObservation.session_date == end,
                ForwardObservation.asked_at == asked_at,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # The same ask at the same instant. Idempotent rather than a second
            # row, so a retried capture after a crash does not double the
            # record -- while a *later* ask about the same session still adds
            # one, because that is genuinely a second observation.
            recorded += 1
            if existing.rows_returned == 0:
                silent += 1
            continue
        session.add(
            ForwardObservation(
                symbol=request.symbol,
                session_date=end,
                asked_at=asked_at,
                status=str(outcome.status),
                rows_returned=rows,
                # The adapter redacts credentials from the URL and the error
                # before they reach an outcome, so this carries the endpoint
                # without its token. Verified 2026-10-20 against the stored row.
                note=(str(outcome.error)[:500] if outcome.error else None),
            )
        )
        recorded += 1
        if rows == 0:
            # **Counted, and the row is written either way.** This is the
            # observation a delisting is made of.
            silent += 1

    return CaptureReport(
        session_date=end,
        asked_at=asked_at,
        requested=len(requests),
        recorded=recorded,
        silent=silent,
        by_status=dict(statuses),
    )
