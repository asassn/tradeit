"""The journal's one writer, and the refusals that keep the record honest.

``journal_entries`` had existed as a table for several phases with no writer at
all when Phase 11 was audited. The tests here pin the decisions taken when one
was finally written, each of which prevents a record that looks fine and is
wrong.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tradeit.execution.journal import (
    SUMMARY_LIMIT,
    JournalEntryKind,
    RejectedCandidate,
    record_entry,
)
from tradeit.storage.tables import JournalEntry

SESSION = dt.date(2015, 6, 1)


def test_an_entry_is_added_but_not_committed(db_session: Session) -> None:
    """The journal belongs in the same transaction as what it describes.

    Committing here would leave a journal recording a trade that then rolled
    back -- a worse record than none, because it is confidently wrong.
    """
    record_entry(
        db_session,
        kind=JournalEntryKind.ENTRY,
        session_date=SESSION,
        summary="bought 100 shares",
    )
    assert db_session.new, "the entry should be pending, not already committed"
    db_session.flush()
    assert db_session.query(JournalEntry).count() == 1


def test_what_was_passed_over_is_recorded_with_what_was_taken(db_session: Session) -> None:
    """The part that makes the journal worth keeping.

    A record of what was bought explains nothing on its own: the question six
    months later is "why that one", and the answer is the slate it beat.
    """
    entry = record_entry(
        db_session,
        kind=JournalEntryKind.ENTRY,
        session_date=SESSION,
        summary="bought AAPL",
        instrument_id=1,
        rejected=(
            RejectedCandidate(instrument_id=2, score=0.81, reason="sector at cap"),
            RejectedCandidate(instrument_id=3, score=0.64, reason="lower fit"),
        ),
    )
    db_session.flush()
    assert entry.context is not None
    rejected = entry.context["rejected"]
    assert isinstance(rejected, list)
    assert [item["instrument_id"] for item in rejected] == [2, 3]
    assert rejected[0]["reason"] == "sector at cap"


def test_two_answers_to_what_was_passed_over_is_refused(db_session: Session) -> None:
    with pytest.raises(ValueError, match="two answers"):
        record_entry(
            db_session,
            kind=JournalEntryKind.ENTRY,
            session_date=SESSION,
            summary="bought AAPL",
            context={"rejected": ["something else"]},
            rejected=(RejectedCandidate(instrument_id=2, score=0.1, reason="x"),),
        )


def test_an_over_long_summary_raises_rather_than_truncating(db_session: Session) -> None:
    """A silently truncated summary is a corrupted record."""
    with pytest.raises(ValueError, match=str(SUMMARY_LIMIT)):
        record_entry(
            db_session,
            kind=JournalEntryKind.NOTE,
            session_date=SESSION,
            summary="x" * (SUMMARY_LIMIT + 1),
        )


def test_an_empty_summary_is_refused(db_session: Session) -> None:
    with pytest.raises(ValueError, match="records nothing"):
        record_entry(db_session, kind=JournalEntryKind.NOTE, session_date=SESSION, summary="   ")


def test_decimals_are_stored_as_strings_not_floats(db_session: Session) -> None:
    """`float(Decimal)` loses precision on exactly the values a journal holds.

    Prices and quantities round-trip exactly as strings. A reader wanting
    arithmetic reconstructs the Decimal; a float cannot be un-rounded.
    """
    entry = record_entry(
        db_session,
        kind=JournalEntryKind.ENTRY,
        session_date=SESSION,
        summary="bought",
        context={"price": Decimal("123.456789012345678"), "nested": {"qty": Decimal("0.1")}},
    )
    db_session.flush()
    assert entry.context is not None
    assert entry.context["price"] == "123.456789012345678"
    assert entry.context["nested"] == {"qty": "0.1"}


def test_decimals_inside_lists_are_converted_too(db_session: Session) -> None:
    entry = record_entry(
        db_session,
        kind=JournalEntryKind.ENTRY,
        session_date=SESSION,
        summary="bought",
        context={"fills": [Decimal("1.5"), Decimal("2.5")]},
    )
    db_session.flush()
    assert entry.context is not None
    assert entry.context["fills"] == ["1.5", "2.5"]


def test_occurred_at_defaults_to_the_session_not_to_now(db_session: Session) -> None:
    """A journal replayed over history must not stamp itself with today."""
    entry = record_entry(
        db_session,
        kind=JournalEntryKind.NOTE,
        session_date=SESSION,
        summary="note",
    )
    assert entry.occurred_at.date() == SESSION


def test_the_entry_type_is_an_enum_so_the_column_can_be_grouped(db_session: Session) -> None:
    entry = record_entry(
        db_session,
        kind=JournalEntryKind.DECLINED,
        session_date=SESSION,
        summary="declined",
    )
    assert entry.entry_type == "declined"


def test_nothing_else_in_src_constructs_a_journal_entry() -> None:
    """The single-writer discipline, asserted structurally.

    Append-only is a property of how a table is used, and SQL offers no way to
    forbid an UPDATE to a caller holding a session. So this is the guard: one
    module writes `JournalEntry`, and a second one appearing fails here rather
    than being discovered later by two writers disagreeing.

    If this fails because a new legitimate writer was added, the fix is to
    route it through `record_entry` -- not to extend the allow-list.
    """
    root = Path(__file__).resolve().parents[2] / "src" / "tradeit"
    allowed = {root / "execution" / "journal.py", root / "storage" / "tables.py"}
    offenders: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if path in allowed:
            continue
        text = path.read_text()
        # Scoped to the ORM model on purpose. A first version matched the bare
        # string "JournalEntry(" and flagged `acquisition/journal.py`, which
        # defines its own unrelated `JournalEntry` dataclass for download
        # logging -- a name collision, not a second writer. Matching a name
        # across a codebase that reuses it proves nothing.
        imports_orm = "from tradeit.storage.tables import" in text and "JournalEntry" in text
        if imports_orm and "JournalEntry(" in text:
            offenders.append(path.relative_to(root))
    assert offenders == [], f"JournalEntry constructed outside the journal writer: {offenders}"


def test_two_unrelated_classes_are_named_journalentry_and_this_pins_it() -> None:
    """A hazard found by the test above, recorded rather than renamed.

    `tradeit.acquisition.journal.JournalEntry` is a JSONL row about a download.
    `tradeit.storage.tables.JournalEntry` is a trade-journal record. They are
    different concepts sharing a name, which is exactly what this project's
    "keep similar concepts apart" discipline warns about -- and renaming an
    established module as a drive-by is worse than recording the collision.

    If one is ever renamed, this test fails and the single-writer check above
    can drop its scoping.
    """
    from tradeit.acquisition.journal import JournalEntry as AcquisitionEntry
    from tradeit.storage.tables import JournalEntry as TradeEntry

    assert AcquisitionEntry is not TradeEntry
    assert AcquisitionEntry.__module__ != TradeEntry.__module__
