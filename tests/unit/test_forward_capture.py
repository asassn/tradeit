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
    silent_source = _StubSource(silent={"AAPL"})
    _capture(db_session, silent_source, ["AAPL"], asked_at=ASKED)
    db_session.flush()

    later = ASKED + dt.timedelta(days=4)
    _capture(db_session, _StubSource(), ["AAPL"], asked_at=later)
    db_session.flush()

    rows = db_session.query(ForwardObservation).order_by(ForwardObservation.asked_at).all()
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

    report = _capture(db_session, _MixedSource(silent={"GONE"}), ["GONE"])
    db_session.flush()
    assert report.requested == 1
    assert db_session.query(ForwardObservation).count() == 1
    assert db_session.query(ForwardObservation).one().rows_returned == 0


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

    _capture(db_session, _StubSource(), ["AAPL"], instrument_ids={"AAPL": 7})
    db_session.flush()

    bar = db_session.query(OhlcvBar).one()
    assert bar.instrument_id == 7
    assert bar.close == Decimal("100.0")
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
    assert "missing" in report.findings[0]
