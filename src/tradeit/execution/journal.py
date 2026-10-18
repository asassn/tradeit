"""The trade journal's one writer.

``journal_entries`` has existed as a table since Phase 4 and had **no
repository and no writer** when Phase 11 was audited on 2026-10-18. An
append-only record with nothing that appends to it is a table, not a memory.

**One writer, deliberately.** Append-only is a property of how a table is
*used*, not of how it is declared, and SQL offers no way to forbid an UPDATE to
a caller that has a session. So the discipline is structural instead: this is
the only module that writes `JournalEntry`, it exposes no update and no delete,
and a test asserts nothing else in `src/` constructs one. That is weaker than a
database constraint and stronger than a docstring, and it is chosen now rather
than after a second caller exists -- which is the whole reason the audit flagged
it before the work started.

What an entry must carry
------------------------

``context`` holds the decision environment, and the part that makes the journal
worth keeping is **what was passed over**. A record of what was bought explains
nothing on its own: six months later the question is never "what did we buy"
but "why that one", and the answer is the slate it beat. :func:`record_entry`
therefore takes ``rejected`` and writes it into the context rather than leaving
it to each caller to remember.

``summary`` is capped at 512 characters by the column. A silently truncated
summary is a corrupted record, so an over-long one **raises** -- the caller
knows what matters in its own sentence and the database does not.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from tradeit.storage.tables import JournalEntry

__all__ = [
    "JournalEntryKind",
    "RejectedCandidate",
    "record_entry",
]

#: The column's own limit. Kept here so the refusal cites a real constraint
#: rather than a number somebody chose.
SUMMARY_LIMIT = 512


class JournalEntryKind(StrEnum):
    """Why an entry exists.

    Deliberately not free text: `entry_type` is indexed and queried, and a
    column that holds "entry", "Entry", "ENTRY" and "opened" cannot be grouped.
    """

    ENTRY = "entry"
    EXIT = "exit"
    STOP_MOVED = "stop_moved"
    #: A candidate the system declined, recorded because the refusals are
    #: evidence about the system and the journal is where they survive.
    DECLINED = "declined"
    #: Reconciliation found the broker and our state disagreeing.
    DISCREPANCY = "discrepancy"
    #: Trading halted for a portfolio.
    HALT = "halt"
    NOTE = "note"


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """One candidate that lost, and why.

    ``score`` and ``reason`` are both required. A rejection with a score and no
    reason cannot be read later, and a reason with no score cannot be compared
    against what won.
    """

    instrument_id: int
    score: float
    reason: str


def record_entry(
    session: Session,
    *,
    kind: JournalEntryKind,
    session_date: dt.date,
    summary: str,
    occurred_at: dt.datetime | None = None,
    portfolio_id: int | None = None,
    position_id: int | None = None,
    trade_id: int | None = None,
    instrument_id: int | None = None,
    rationale: str | None = None,
    context: Mapping[str, object] | None = None,
    rejected: Sequence[RejectedCandidate] = (),
    author: str = "system",
) -> JournalEntry:
    """Append one entry. The only write path into ``journal_entries``.

    Returns the ORM object **without committing**: the journal entry belongs in
    the same transaction as whatever it describes. Committing here would leave a
    journal recording a trade that then rolled back, which is a worse record
    than none.
    """
    if not summary.strip():
        raise ValueError("a journal entry needs a summary; an empty one records nothing")
    if len(summary) > SUMMARY_LIMIT:
        raise ValueError(
            f"summary is {len(summary)} characters against the column's {SUMMARY_LIMIT}; "
            "truncating it would corrupt the record silently, and the caller knows which "
            "part of its own sentence matters"
        )
    payload: dict[str, object] = dict(context or {})
    if rejected:
        if "rejected" in payload:
            raise ValueError(
                "context already carries a 'rejected' key and `rejected=` was also given; "
                "two answers to 'what was passed over' is worse than none"
            )
        payload["rejected"] = [
            {
                "instrument_id": candidate.instrument_id,
                "score": candidate.score,
                "reason": candidate.reason,
            }
            for candidate in rejected
        ]
    entry = JournalEntry(
        portfolio_id=portfolio_id,
        position_id=position_id,
        trade_id=trade_id,
        instrument_id=instrument_id,
        entry_type=str(kind),
        session_date=session_date,
        occurred_at=occurred_at or dt.datetime.combine(session_date, dt.time(20, 0), tzinfo=dt.UTC),
        summary=summary,
        rationale=rationale,
        context=_jsonable(payload) if payload else None,
        author=author,
    )
    session.add(entry)
    return entry


def _jsonable(value: object) -> object:
    """Decimals to strings, recursively, and nothing else touched.

    `Decimal` is not JSON-serialisable and `float(Decimal)` loses precision on
    exactly the values a trade journal holds -- prices and quantities. Strings
    round-trip exactly, and a reader that wants arithmetic can reconstruct the
    Decimal. Chosen over float because the journal is a record, not a
    calculation.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
