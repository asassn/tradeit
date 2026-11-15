"""A vendor changing its mind: both answers kept, neither vendor declared right.

`ELME_VENUE_BREAK_2026-11-15.md` §3 found three of sixteen bars revised by the
vendor after we captured them, and recorded the gap plainly: we keep the
first-seen values, *"and nothing in the record says so. The divergence is only
visible by re-asking, which nothing does."*

The architecture had already answered this and the capture was not using it.
`OhlcvBar`'s unique key has included `knowledge_time` since `0001_initial`, its
docstring reads *"Append-only; revisions are new rows"*, and
`BarRepository.history` has always resolved revisions with a window function
bounded by the caller's clock (ADR-0005). But `_write_bars` skipped any session
it already held, so **no revision was ever written**: measured 2026-11-15, every
one of the 4,327 bars in the forward record held exactly one `knowledge_time`.
The path was latent, not working.

These tests hold the four properties that make writing revisions safe, and the
one that makes it worth doing. **Each was run against a deliberately broken copy
and shown to fail**; the mutant is named in the test that caught it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.acquisition.base import AcquisitionDataset, FetchOutcome, FetchRequest, FetchStatus
from tradeit.acquisition.normalize import NormalizedRows, normalize_prices
from tradeit.core.clock import AsOfClock
from tradeit.core.enums import AdjustmentPolicy, Bartimeframe
from tradeit.forward.capture import capture_forward
from tradeit.storage.repositories import BarRepository
from tradeit.storage.tables import OhlcvBar

SESSION = dt.date(2026, 10, 20)
FIRST_ASK = dt.datetime(2026, 10, 20, 22, 10, tzinfo=dt.UTC)
LATER_ASK = dt.datetime(2026, 10, 27, 22, 10, tzinfo=dt.UTC)


@dataclass
class _RevisingSource:
    """A vendor whose answer the test can change between asks.

    The live source cannot be made to revise a bar on demand, which is why the
    revision path sat unexercised: there was no way to produce one except by
    waiting for a vendor to do it.
    """

    close: float = 100.0
    volume: float = 1_000_000
    scale: str | None = None
    #: Which session the vendor dates its row to. Defaulting to the session
    #: asked about is the ordinary case; dating it earlier is how a test reaches
    #: the `session_unobserved` branch, which writes bars without an
    #: observation and is therefore the one path that can re-enter
    #: `_write_bars` at an instant it has already written.
    row_date: dt.date | None = None
    fetched: list[str] = field(default_factory=list)

    def has_credential(self) -> bool:
        return True

    def plan(self, symbols, start: dt.date, end: dt.date) -> list[FetchRequest]:  # type: ignore[no-untyped-def]
        return [
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, symbol, start, end)
            for symbol in symbols
        ]

    def normalize(self, outcome: FetchOutcome, ids) -> NormalizedRows:  # type: ignore[no-untyped-def]
        instrument_id = ids.get(outcome.request.symbol)
        if instrument_id is None:
            return NormalizedRows()
        return normalize_prices(outcome.request.symbol, instrument_id, outcome.rows)

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        self.fetched.append(request.symbol)
        close: object = self.scale if self.scale is not None else self.close
        return FetchOutcome(
            request=request,
            status=FetchStatus.OK,
            rows=(
                {
                    "date": (self.row_date or SESSION).isoformat(),
                    "open": 99.0,
                    "high": max(101.0, float(close) + 1),
                    "low": min(98.5, float(close) - 1),
                    "close": close,
                    "volume": self.volume,
                },
            ),
        )


def _ask(session: Session, source: _RevisingSource, *, asked_at: dt.datetime):  # type: ignore[no-untyped-def]
    report = capture_forward(
        session,
        source,
        ["AAPL"],
        start=SESSION - dt.timedelta(days=5),
        end=SESSION,
        asked_at=asked_at,
        instrument_ids={"AAPL": 7},
        provider="eodhd",
    )
    session.flush()
    return report


def _bars(session: Session) -> list[OhlcvBar]:
    return list(
        session.execute(
            select(OhlcvBar).where(OhlcvBar.instrument_id == 7).order_by(OhlcvBar.knowledge_time)
        ).scalars()
    )


def test_a_revised_bar_is_added_and_the_first_answer_is_kept(db_session: Session) -> None:
    """**The whole point. Nothing is overwritten and nothing is discarded.**

    Mutant: in place of appending a revision, update the held row. Caught -- one
    row survives and the first answer is gone, which is the state
    `ELME_VENUE_BREAK_2026-11-15.md` §3 describes as the record diverging from
    the vendor with nothing saying so.
    """
    source = _RevisingSource()
    _ask(db_session, source, asked_at=FIRST_ASK)

    source.close = 95.0
    report = _ask(db_session, source, asked_at=LATER_ASK)

    held = _bars(db_session)
    assert len(held) == 2, "a revision is a new row, not an edit"
    assert [bar.close for bar in held] == [Decimal("100.0"), Decimal("95.0")]
    assert [bar.knowledge_time for bar in held] == [FIRST_ASK, LATER_ASK]
    assert report.bars_revised == 1
    assert report.bars_written == 0, "a revised session is not a session newly acquired"


def test_a_backtest_dated_before_the_revision_still_sees_the_first_answer(
    db_session: Session,
) -> None:
    """**The look-ahead property, which is why revisions may be written at all.**

    This is what the never-overwrite rule was protecting by hand, and the
    bounded read gives it for free: a clock set before the revision cannot see
    it, so a backtest reads the price that was knowable on the day. A clock set
    after reads the vendor's current answer. Both are true statements about
    different instants, which is the entire bitemporal point (ADR-0005).

    Mutant: drop `knowledge_time <= clock.as_of` from `BarRepository.history`.
    Caught -- the backtest clock saw 95.0, a number that did not exist on the
    date it was reading.
    """
    source = _RevisingSource()
    _ask(db_session, source, asked_at=FIRST_ASK)
    source.close = 95.0
    _ask(db_session, source, asked_at=LATER_ASK)

    repo = BarRepository(db_session)
    before = repo.history(
        AsOfClock.at(FIRST_ASK + dt.timedelta(hours=1)),
        7,
        timeframe=Bartimeframe.D1,
        adjustment=AdjustmentPolicy.NONE,
    )
    after = repo.history(
        AsOfClock.at(LATER_ASK + dt.timedelta(hours=1)),
        7,
        timeframe=Bartimeframe.D1,
        adjustment=AdjustmentPolicy.NONE,
    )

    assert [bar.close for bar in before] == [Decimal("100.0")], "first-seen, as of that clock"
    assert [bar.close for bar in after] == [Decimal("95.0")], "the newest revision, later"


def test_an_identical_answer_later_writes_nothing(db_session: Session) -> None:
    """**The no-bloat rule, without which the fix buries what it reports.**

    The capture re-asks a seven-session window every night. A row written per
    ask regardless of content would add roughly 1,350 rows a night carrying no
    information, and the revisions that matter would be needles in it.

    Mutant: write whenever `knowledge_time` differs. Caught -- a second
    identical row appeared and `bars_revised` claimed a revision that did not
    happen.
    """
    source = _RevisingSource()
    _ask(db_session, source, asked_at=FIRST_ASK)

    report = _ask(db_session, source, asked_at=LATER_ASK)

    assert len(_bars(db_session)) == 1, "the vendor confirming what we hold is not a new fact"
    assert (report.bars_written, report.bars_revised) == (0, 0)


def test_the_same_number_at_a_different_scale_is_not_a_revision(db_session: Session) -> None:
    """``0.05`` and ``0.050000`` are one number, not two.

    The database returns a fixed scale and the vendor does not. Comparing the
    text would report a revision on every bar whose trailing zeros the vendor
    dropped -- and a detector that fires on everything is read as firing on
    nothing, which is the failure mode that makes a safeguard decoration.

    Mutant: compare `str(row[field]) != str(current)`. Caught.
    """
    source = _RevisingSource(scale="100.0")
    _ask(db_session, source, asked_at=FIRST_ASK)

    source.scale = "100.000000"
    report = _ask(db_session, source, asked_at=LATER_ASK)

    assert len(_bars(db_session)) == 1
    assert report.bars_revised == 0


def test_the_report_names_the_field_that_moved(db_session: Session) -> None:
    """A count alone buries the interesting case.

    ELME's 2026-11-05 bar moved **53% on volume** while its close moved a tenth
    of a cent. "1 bar revised" would have hidden the fact worth seeing, so the
    report says which field changed and from what.

    Mutant: return a bare bool from `_difference` and report "revised". Caught.
    """
    source = _RevisingSource()
    _ask(db_session, source, asked_at=FIRST_ASK)

    source.volume = 350_462
    report = _ask(db_session, source, asked_at=LATER_ASK)

    assert report.bars_revised == 1
    assert len(report.revisions) == 1
    assert "volume" in report.revisions[0]
    assert "350462" in report.revisions[0].replace(",", "")
    assert "volume" in report.describe()


def test_the_same_ask_at_the_same_instant_stays_idempotent(db_session: Session) -> None:
    """A capture retried after a crash must not double the record.

    The unique key is ``(instrument_id, timeframe, session_date,
    knowledge_time)``, so a second insert at the same instant would be refused
    by the database -- which is a crash, not an idempotent retry. This holds
    even once a revision exists, which is the case the simple existence check
    used to cover for free and no longer does.

    **Two wrong versions of this test preceded this one, and both passed.**

    The first replayed the *identical* answer, which the identical-values rule
    already drops -- so it stayed green with the guard removed and was proving a
    different rule.

    The second changed the answer but still went through the ordinary path,
    where `capture_forward` finds the existing `ForwardObservation` for
    ``(symbol, session, asked_at)`` and returns before `_write_bars` is reached
    at all. The guard is unreachable that way, so that test could not fail
    either.

    **The one path that re-enters `_write_bars` at an instant it has already
    written is the `session_unobserved` branch**, which writes bars for earlier
    sessions in the window *before* the observation lookup, precisely because no
    observation is owed for a session nothing printed. So the vendor here dates
    its row before the session asked about.

    Two answers cannot share one `knowledge_time` -- it is part of the unique
    key -- so the first stands and the contradiction is **reported rather than
    dropped**. Silently discarding evidence about a vendor is the one thing this
    record exists not to do.

    Mutant: drop the `knowledge_time == asked_at` check. Caught -- an
    IntegrityError on the replay.
    """
    earlier = SESSION - dt.timedelta(days=1)
    source = _RevisingSource(row_date=earlier)
    first = _ask(db_session, source, asked_at=FIRST_ASK)
    assert first.session_unobserved, "the premise: nothing printed for the session asked about"
    assert first.bars_written == 1, "the earlier session's bar is still real and still written"

    source.close = 90.0
    replay = _ask(db_session, source, asked_at=FIRST_ASK)

    held = _bars(db_session)
    assert len(held) == 1, "the replay must not add a row the unique key forbids"
    assert held[0].close == Decimal("100.0"), "the first answer at that instant stands"
    assert (replay.bars_written, replay.bars_revised) == (0, 0)
    assert any("same knowledge_time" in note for note in replay.revisions), (
        "a contradiction the record cannot hold must still be reported"
    )


def test_a_revision_is_never_read_as_a_price_move(db_session: Session) -> None:
    """**The bug writing revisions would otherwise introduce.**

    The discontinuity scan reads bars and pairs consecutive ones. A revised
    session holds two rows, so a flat read hands the scan two bars for one date
    and the difference between them looks like a move -- here a halving, which
    is exactly at the threshold. It is not a move; it is the vendor changing its
    mind, and reporting it as a 2x collapse would be a confident wrong answer of
    the kind this project treats as worse than no answer.

    Mutant: drop the newest-revision resolution from `scan_record`. Caught.
    """
    from tradeit.forward.discontinuity import scan_record

    source = _RevisingSource()
    _ask(db_session, source, asked_at=FIRST_ASK)
    source.close = 50.0
    _ask(db_session, source, asked_at=LATER_ASK)

    scan = scan_record(db_session)

    assert scan.findings == (), "a revision is not a price move"
    assert scan.bars == 1, "the scan reads one bar per session, the newest"
    assert scan.superseded_revisions == 1
    assert "superseded" in scan.describe()


def test_the_pure_scan_guards_against_an_unresolved_caller() -> None:
    """Defence in depth: the guard that does not depend on remembering.

    `scan_record` resolves revisions, but `find_discontinuities` is public and
    takes whatever bars it is handed. A future caller reading the table flat
    would otherwise get a revision reported as a move, so the pure function
    skips a pair sharing a session date too.

    Mutant: remove the `earlier.session_date == later.session_date` guard.
    Caught.
    """
    from tradeit.forward.discontinuity import find_discontinuities

    @dataclass(frozen=True)
    class _Bar:
        instrument_id: int
        session_date: dt.date
        close: Decimal

    two_answers_one_session = [
        _Bar(7, SESSION, Decimal("100")),
        _Bar(7, SESSION, Decimal("5")),
    ]

    assert find_discontinuities(two_answers_one_session) == []
