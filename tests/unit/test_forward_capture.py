"""The forward record, and the one behaviour that is invisible when it breaks.

**Every outcome is recorded, including the silent ones.** A symbol asked about
that returns nothing produces a row, and that row is what a delisting is made
of: written on a dated day, before anyone knew whether it would ever print
again.

If a future change made the capture skip silences — a plausible "we only need
the rows that have data" — the record would lose exactly the observations that
make it survivorship-honest, with no error, no failing test and no visible
change until somebody tried to measure a delisting years later. That is the
same defect the historical corpus has, which took months to characterise and
cannot be fixed. Here it is one line away and silent, so it has a test.

The source is a **stub**, deliberately. The live feed cannot be made to delist
a company on demand, and a capture whose silent path is only exercised in
production has no test at all.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
)
from tradeit.acquisition.normalize import NormalizedRows, normalize_prices
from tradeit.forward import NoCredential, capture_forward
from tradeit.storage.tables import ForwardObservation

SESSION = dt.date(2026, 10, 20)
ASKED = dt.datetime(2026, 10, 20, 21, tzinfo=dt.UTC)


@dataclass
class _StubSource:
    """A source whose answers are chosen by the test.

    ``silent`` names symbols that return nothing, which is the case that
    matters and the one a live feed will not produce to order.
    """

    silent: set[str] = field(default_factory=set)
    rejected: set[str] = field(default_factory=set)
    credential: bool = True
    fetched: list[str] = field(default_factory=list)

    def has_credential(self) -> bool:
        return self.credential

    def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]:
        return [
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, symbol, start, end)
            for symbol in symbols
        ]

    def normalize(self, outcome: FetchOutcome, ids) -> NormalizedRows:  # type: ignore[no-untyped-def]
        symbol = outcome.request.symbol
        instrument_id = ids.get(symbol)
        if instrument_id is None:
            return NormalizedRows()
        return normalize_prices(symbol, instrument_id, outcome.rows)

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        symbol = request.symbol
        self.fetched.append(symbol)
        if symbol in self.rejected:
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error="not found",
            )
        if symbol in self.silent:
            return FetchOutcome(request=request, status=FetchStatus.EMPTY)
        return FetchOutcome(
            request=request,
            status=FetchStatus.OK,
            rows=(
                {
                    "date": "2026-10-20",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.5,
                    "close": 100.0,
                    "volume": 1_000_000,
                },
            ),
        )


def _capture(session: Session, source: _StubSource, symbols: Sequence[str], **kw: object):
    return capture_forward(
        session,
        source,
        symbols,
        start=SESSION - dt.timedelta(days=5),
        end=SESSION,
        asked_at=kw.pop("asked_at", ASKED),  # type: ignore[arg-type]
        # Named explicitly because `capture_forward` no longer defaults it. The
        # default was `"tiingo"`, and it silently stamped a second vendor's bars
        # with the first vendor's name.
        provider=kw.pop("provider", "tiingo"),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def test_a_silent_symbol_is_recorded_not_skipped(db_session: Session) -> None:
    """The invariant. Losing this loses the survivorship honesty, silently."""
    source = _StubSource(silent={"GONE"})
    report = _capture(db_session, source, ["AAPL", "GONE"])
    db_session.flush()

    rows = {row.symbol: row for row in db_session.query(ForwardObservation).all()}
    assert set(rows) == {"AAPL", "GONE"}, "a silent symbol produced no observation"
    assert rows["GONE"].rows_returned == 0
    assert rows["GONE"].status == "empty"
    assert report.silent == 1
    assert report.recorded == 2


def test_a_rejected_symbol_is_recorded_too(db_session: Session) -> None:
    """Rejected and empty are different answers and both are observations."""
    source = _StubSource(rejected={"NOPE"})
    _capture(db_session, source, ["NOPE"])
    db_session.flush()

    row = db_session.query(ForwardObservation).one()
    assert row.status == "rejected"
    assert row.rows_returned == 0
    assert row.note is not None


def test_every_request_produces_an_observation(db_session: Session) -> None:
    """`complete` is about the record having no holes, **not** about every
    symbol returning data. A silence is a successful observation."""
    source = _StubSource(silent={"A", "B"}, rejected={"C"})
    report = _capture(db_session, source, ["A", "B", "C", "D"])
    db_session.flush()

    assert report.requested == 4
    assert report.recorded == 4
    assert report.silent == 3
    assert report.complete is True
    assert db_session.query(ForwardObservation).count() == 4


def test_the_same_ask_twice_is_idempotent(db_session: Session) -> None:
    """A retried capture after a crash must not double the record."""
    source = _StubSource()
    _capture(db_session, source, ["AAPL"])
    db_session.flush()
    report = _capture(db_session, source, ["AAPL"])
    db_session.flush()

    assert db_session.query(ForwardObservation).count() == 1
    assert report.recorded == 1


def test_a_later_ask_about_the_same_session_is_a_second_observation(
    db_session: Session,
) -> None:
    """A vendor may revise. "We asked Monday and got nothing; we asked Friday
    and got a price" is two facts, both evidence."""
    # `MSFT` prints and is otherwise irrelevant. It is here because a silence
    # is only interpretable against a session that demonstrably happened: with
    # `AAPL` alone and silent, nothing establishes that the session exists, and
    # the capture withholds the observation rather than writing a false exit.
    # Production universes always have a printing majority; a one-symbol
    # universe was a convenience of this test, not its subject.
    silent_source = _StubSource(silent={"AAPL"})
    _capture(db_session, silent_source, ["AAPL", "MSFT"], asked_at=ASKED)
    db_session.flush()

    later = ASKED + dt.timedelta(days=4)
    _capture(db_session, _StubSource(), ["AAPL", "MSFT"], asked_at=later)
    db_session.flush()

    rows = (
        db_session.query(ForwardObservation)
        .filter(ForwardObservation.symbol == "AAPL")
        .order_by(ForwardObservation.asked_at)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].rows_returned == 0
    assert rows[1].rows_returned == 1
    assert rows[0].session_date == rows[1].session_date


def test_no_credential_refuses_rather_than_recording_an_empty_run(
    db_session: Session,
) -> None:
    """An empty forward record is indistinguishable from a quiet market, which
    is the one thing it must never be mistaken for."""
    with pytest.raises(NoCredential, match="cannot be caught up"):
        _capture(db_session, _StubSource(credential=False), ["AAPL"])
    assert db_session.query(ForwardObservation).count() == 0


def test_the_ask_time_is_passed_in_not_read_from_the_clock(db_session: Session) -> None:
    """A capture crossing midnight UTC must stamp one consistent instant, and a
    function reading the clock per row could not be tested for idempotency."""
    stamp = dt.datetime(1999, 3, 1, 12, tzinfo=dt.UTC)
    _capture(db_session, _StubSource(), ["AAPL", "MSFT"], asked_at=stamp)
    db_session.flush()
    assert {row.asked_at for row in db_session.query(ForwardObservation).all()} == {stamp}


def test_only_daily_price_requests_are_captured(db_session: Session) -> None:
    """A source that also plans metadata requests must not have them recorded
    as price observations, which would make silent symbols look answered."""

    @dataclass
    class _MixedSource(_StubSource):
        def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]:
            out: list[FetchRequest] = []
            for symbol in symbols:
                out.append(FetchRequest.one(AcquisitionDataset.SYMBOL_META, symbol, start, end))
                out.append(FetchRequest.one(AcquisitionDataset.DAILY_PRICES, symbol, start, end))
            return out

    # `HERE` prints, so the session is established and `GONE`'s silence means
    # what it says. See the note in
    # `test_a_later_ask_about_the_same_session_is_a_second_observation`.
    report = _capture(db_session, _MixedSource(silent={"GONE"}), ["GONE", "HERE"])
    db_session.flush()
    assert report.requested == 2, "one DAILY_PRICES request per symbol, no SYMBOL_META"
    gone = db_session.query(ForwardObservation).filter(ForwardObservation.symbol == "GONE").all()
    assert len(gone) == 1, "a metadata request was recorded as a price observation"
    assert gone[0].rows_returned == 0


def test_the_journal_receives_every_outcome_when_one_is_given(
    db_session: Session,
) -> None:
    """The journal and the table are separate records of the same event, and
    both must see the silences."""

    @dataclass
    class _SpyJournal:
        seen: list[tuple[str, str]] = field(default_factory=list)

        def record(self, provider: str, outcome: FetchOutcome) -> None:
            self.seen.append((provider, str(outcome.status)))

    spy = _SpyJournal()
    _capture(db_session, _StubSource(silent={"GONE"}), ["AAPL", "GONE"], journal=spy)
    assert sorted(status for _, status in spy.seen) == ["empty", "ok"]


def test_prices_are_written_when_the_symbol_has_an_id(db_session: Session) -> None:
    """A capture that records only observations stores no data to trade on."""
    from tradeit.storage.tables import OhlcvBar

    _capture(db_session, _StubSource(), ["AAPL"], instrument_ids={"AAPL": 7}, provider="tiingo")
    db_session.flush()

    bar = db_session.query(OhlcvBar).one()
    assert bar.instrument_id == 7
    assert bar.close == Decimal("100.0")
    # Asserted against the provider this call passed, not the literal "tiingo".
    # The literal was true for the wrong reason: `capture_forward` defaulted
    # `provider` to "tiingo", this test passed none, and so it pinned the
    # default rather than the invariant -- which is why it stayed green while a
    # second vendor's bars were being stamped with the first vendor's name.
    # `test_a_bar_carries_the_vendor_that_was_actually_asked` covers the other
    # vendor.
    assert bar.source == "tiingo", "a bar that cannot name its vendor cannot be rechecked"
    assert bar.knowledge_source == "vendor_ingest"


def test_knowledge_time_is_when_we_asked_not_when_the_session_closed(
    db_session: Session,
) -> None:
    """Different facts, and the distinction is the whole bitemporal point: a
    price learned three days late is still a price for its session."""
    from tradeit.storage.tables import OhlcvBar

    late = dt.datetime(2026, 10, 23, 12, tzinfo=dt.UTC)
    _capture(db_session, _StubSource(), ["AAPL"], instrument_ids={"AAPL": 7}, asked_at=late)
    db_session.flush()

    bar = db_session.query(OhlcvBar).one()
    assert bar.knowledge_time == late
    assert bar.session_date == dt.date(2026, 10, 20)
    assert bar.event_time.date() == dt.date(2026, 10, 20)


def test_an_unmapped_symbol_still_gets_its_observation(db_session: Session) -> None:
    """The survivorship record does not depend on having an id, and a symbol
    whose prices had nowhere to go is counted rather than dropped quietly."""
    from tradeit.storage.tables import OhlcvBar

    report = _capture(db_session, _StubSource(), ["AAPL"], instrument_ids={})
    db_session.flush()

    assert db_session.query(ForwardObservation).count() == 1
    assert db_session.query(OhlcvBar).count() == 0
    assert report.unmapped == ("AAPL",)
    assert "no known id" in report.describe()


def test_re_running_writes_no_duplicate_bars(db_session: Session) -> None:
    """A later ask adds an observation and must not add a second price for a
    session already held."""
    from tradeit.storage.tables import OhlcvBar

    _capture(db_session, _StubSource(), ["AAPL"], instrument_ids={"AAPL": 7})
    db_session.flush()
    later = _capture(
        db_session,
        _StubSource(),
        ["AAPL"],
        instrument_ids={"AAPL": 7},
        asked_at=ASKED + dt.timedelta(days=1),
    )
    db_session.flush()

    assert db_session.query(OhlcvBar).count() == 1
    assert later.bars_written == 0
    assert db_session.query(ForwardObservation).count() == 2


def test_a_row_the_vendor_sent_that_cannot_be_read_is_reported(
    db_session: Session,
) -> None:
    """Carried out rather than logged: "the vendor sent a row we could not
    parse" is evidence about the vendor."""

    @dataclass
    class _BadRowSource(_StubSource):
        def fetch(self, request: FetchRequest) -> FetchOutcome:
            return FetchOutcome(
                request=request,
                status=FetchStatus.OK,
                rows=({"date": "2026-10-20", "close": 100.0},),  # no open/high/low
            )

    report = _capture(db_session, _BadRowSource(), ["AAPL"], instrument_ids={"AAPL": 7})
    db_session.flush()
    assert report.bars_written == 0
    assert report.findings, "an unreadable row was dropped without a finding"
    # Searched across findings rather than pinned to index 0. The capture may
    # also report that no symbol printed for the session -- which is true here,
    # since the only symbol's only row was unreadable and therefore undatable --
    # and an index made this test depend on which finding came first.
    assert any("missing" in finding for finding in report.findings), report.findings


def test_a_bar_carries_the_vendor_that_was_actually_asked(db_session: Session) -> None:
    """**The defect this was written for, and why the obvious test missed it.**

    `capture_forward` took ``provider: str = "tiingo"`` and the only caller
    never passed it. When EODHD was added as a second vendor its 60 bars were
    written with ``source = "tiingo"``, which is not a cosmetic mislabel:
    Tiingo declares `RAW_UNADJUSTED` and EODHD declares `UNKNOWN`, so a bar
    wearing the wrong vendor's name has an adjustment basis that is
    *unknowable* rather than merely unknown, and ADR-0005 is unenforceable on
    it.

    `tests/unit/test_forward_universe.py` asserts the two vendor universes stay
    disjoint, and it passed throughout -- the files were disjoint. A separate
    check that no instrument carried bars from two vendors also passed, because
    **every label was the same wrong one.** Both were true statements about a
    corrupt record. This asserts the thing neither could: the label equals what
    was asked.
    """
    from tradeit.storage.tables import OhlcvBar

    report = _capture(
        db_session, _StubSource(), ["AAPL"], instrument_ids={"AAPL": 7}, provider="eodhd"
    )
    db_session.commit()
    assert report.bars_written >= 1
    sources = {bar.source for bar in db_session.query(OhlcvBar).all()}
    assert sources == {"eodhd"}, (
        f"bars were written as {sources} after asking eodhd; a provider label that "
        "does not match the vendor asked makes the adjustment basis unknowable"
    )


# ---------------------------------------------------------------------------
# `rows_returned` counts the session asked about, not the window fetched.
# ---------------------------------------------------------------------------


@dataclass
class _StoppedMidWindow(_StubSource):
    """Prints two sessions before `end`, and nothing on `end` itself.

    This is the ordinary shape of an exit: a security trades on Monday and
    Tuesday, stops, and the capture still asks about a window that contains
    both. A live feed will not produce it to order, which is why it is stubbed.
    """

    stopped: set[str] = field(default_factory=set)

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        if request.symbol not in self.stopped:
            return super().fetch(request)
        self.fetched.append(request.symbol)
        return FetchOutcome(
            request=request,
            status=FetchStatus.OK,
            rows=(
                {
                    "date": str(SESSION - dt.timedelta(days=2)),
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.5,
                    "close": 100.0,
                    "volume": 1_000_000,
                },
            ),
        )


def test_a_symbol_that_stopped_printing_mid_window_is_recorded_silent(
    db_session: Session,
) -> None:
    """**The defect this replaced, and it affected every exit.**

    `rows_returned` was `len(outcome.rows)` -- the count for the whole
    `start..end` window. Measured on the live record 2026-10-23: every covered
    symbol read 5 or 6 against a 7-day window, so "session 2026-10-21,
    rows_returned=5" never meant "this session printed".

    A security that stopped trading therefore kept returning the previous six
    sessions and was recorded `ok`, **not silent**, for six more days. The
    silence a delisting is made of only appeared once the entire window
    emptied, dated to that later run rather than to the session the security
    actually stopped. `ABAX` and `AUTO` were caught only because they had no
    data anywhere in the window.

    Here `GONE` printed two sessions ago and not on `end`, while `HERE` printed
    on `end`. The old code recorded `GONE` as `ok` with one row.
    """
    source = _StoppedMidWindow(stopped={"GONE"})
    report = _capture(db_session, source, ["GONE", "HERE"], instrument_ids={"GONE": 7, "HERE": 8})
    db_session.flush()

    gone = db_session.query(ForwardObservation).filter(ForwardObservation.symbol == "GONE").one()
    assert gone.rows_returned == 0, (
        "a symbol that did not print on the session asked about was recorded as "
        "having printed; rows_returned is counting the window, not the session"
    )
    assert gone.session_date == SESSION
    assert report.silent == 1


def test_the_earlier_bars_of_a_stopped_symbol_are_still_written(db_session: Session) -> None:
    """The fix must not become a different kind of data loss.

    A symbol silent on `end` may have printed earlier in the window, and those
    bars are real. An early `continue` after counting the silence would have
    discarded them -- trading a wrong observation for a missing price.
    """
    from tradeit.storage.tables import OhlcvBar

    source = _StoppedMidWindow(stopped={"GONE"})
    _capture(db_session, source, ["GONE", "HERE"], instrument_ids={"GONE": 7, "HERE": 8})
    db_session.flush()

    bars = db_session.query(OhlcvBar).filter(OhlcvBar.instrument_id == 7).all()
    assert len(bars) == 1, "the stopped symbol's earlier bar was dropped"
    assert bars[0].session_date == SESSION - dt.timedelta(days=2)


def test_a_session_nobody_printed_records_no_silence_at_all(db_session: Session) -> None:
    """**195 false exits in one night, avoided.**

    If no symbol printed for the session, the session is not evidence about any
    symbol: either the vendor has not published it, or the calendar is wrong
    about it being a session. Writing a silence per symbol would be one false
    exit for every name in the universe.

    Fails closed with no universe-size threshold: with no cross-sectional
    evidence that the session happened, a silence is uninterpretable whether
    the universe holds one name or a thousand.
    """
    source = _StoppedMidWindow(stopped={"A", "B", "C"})
    report = _capture(db_session, source, ["A", "B", "C"], instrument_ids={"A": 1, "B": 2, "C": 3})
    db_session.flush()

    assert db_session.query(ForwardObservation).count() == 0
    assert report.silent == 0
    assert report.recorded == 0
    assert any("no symbol printed" in finding for finding in report.findings), report.findings
    # The bars are still written: they are real data about sessions that did
    # happen, and withholding them would make the fix a data-loss bug.
    assert report.bars_written == 3

    # **And the report must not call this a hole.** The first live run after
    # this distinction existed said "149 requests produced no observation at
    # all, which is a hole in the record" about the one outcome that was
    # correct. A report that cries wolf on correct behaviour trains the
    # operator to ignore the warning that matters.
    assert report.session_unobserved
    assert report.complete, "declining to record a silence about an unpublished session"
    assert "nothing to be silent about" in report.describe()
    assert "hole in the record" not in report.describe()


def test_a_rejection_is_recorded_even_when_nobody_printed(db_session: Session) -> None:
    """Only *successful* zero-row observations are suppressed.

    `EMPTY` is a success -- the vendor answered and the answer was empty -- and
    that is the row a reader takes for "this security did not trade". A
    `REJECTED` outcome carries its own explanation and no reader could mistake
    it for an exit, so suppressing it would lose the distinction between a
    refusal and a silence, which the whole design turns on.
    """
    source = _StoppedMidWindow(stopped={"QUIET"}, rejected={"NOPE"})
    _capture(db_session, source, ["QUIET", "NOPE"], instrument_ids={"QUIET": 1})
    db_session.flush()

    rows = {r.symbol: r for r in db_session.query(ForwardObservation).all()}
    assert set(rows) == {"NOPE"}, (
        "a vendor refusal was suppressed along with the silences, or a silence "
        "survived a session nobody printed in"
    )
    assert rows["NOPE"].status == str(FetchStatus.REJECTED)
