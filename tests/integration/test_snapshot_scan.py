"""The scan path: causality, identity, idempotency, and no recommendations.

These are the properties whose absence would corrupt every Phase 4/5 number
computed afterwards, so they are tested against a real import rather than a
hand-built object graph.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe
from tradeit.data.packages.database import DatabaseSink
from tradeit.data.packages.importer import ImportOptions, PackageImporter
from tradeit.data.packages.manifest import (
    Coverage,
    DatasetFile,
    PackageManifest,
    file_digest,
)
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.errors import DataError
from tradeit.scanning import CausalFeed, FeedMode, ScanOptions, SnapshotScanner
from tradeit.scanning.runner import SCAN_DOES_NOT_PRODUCE
from tradeit.storage import tables as t

# A scan walks every session of every fixture instrument through twelve
# detectors and the breakout engine. That is the point — the properties under
# test only exist over a full walk — and it costs minutes rather than seconds,
# so the whole module is marked `performance` and excluded from the default run.
# Run it with `pytest -m performance tests/integration/test_snapshot_scan.py`.
pytestmark = pytest.mark.performance

START = dt.date(2019, 1, 2)
END = dt.date(2020, 12, 31)
SESSIONS = get_calendar().sessions_between(START, END)
SYMBOLS = ("ALPHA", "BETA")

BAR_COLUMNS = {
    "session_date": "date",
    "instrument_id": "symbol",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}


def write_package(root: Path) -> PackageManifest:
    rows = ["date,symbol,open,high,low,close,volume"]
    for index, _symbol in enumerate(SYMBOLS, start=1):
        price = 40.0 + index * 25
        for step, day in enumerate(SESSIONS):
            price *= 1.0 + (0.006 if step % 5 else -0.013)
            rows.append(
                f"{day},{index},{price * 0.995:.2f},{price * 1.015:.2f},"
                f"{price * 0.985:.2f},{price:.2f},{900_000 + step * 137}"
            )
    (root / "bars.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "instruments.csv").write_text(
        "instrument_id,name,primary_exchange,asset_class\n"
        + "".join(f"{i},{s} Inc,XNYS,common_stock\n" for i, s in enumerate(SYMBOLS, start=1)),
        encoding="utf-8",
    )
    (root / "symbols.csv").write_text(
        "instrument_id,ticker,valid_from\n"
        + "".join(f"{i},{s},2010-01-01\n" for i, s in enumerate(SYMBOLS, start=1)),
        encoding="utf-8",
    )
    return PackageManifest(
        name="scan-fixture",
        provider="fixture",
        export_date=END,
        coverage=Coverage(start=START, end=END, instruments=len(SYMBOLS)),
        timezone="America/New_York",
        adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
        files=(
            DatasetFile(
                path="instruments.csv",
                dataset=DatasetKind.INSTRUMENTS,
                sha256=file_digest(root / "instruments.csv"),
            ),
            DatasetFile(
                path="symbols.csv",
                dataset=DatasetKind.SYMBOL_MAPPINGS,
                sha256=file_digest(root / "symbols.csv"),
            ),
            DatasetFile(
                path="bars.csv",
                dataset=DatasetKind.DAILY_BARS,
                sha256=file_digest(root / "bars.csv"),
                columns=BAR_COLUMNS,
            ),
        ),
    )


@pytest.fixture
def snapshot(db_session: Session, tmp_path: Path) -> str:
    manifest = write_package(tmp_path)
    sink = DatabaseSink(session=db_session, manifest=manifest, source_path=tmp_path)
    report = PackageImporter(manifest, tmp_path, options=ImportOptions(), sink=sink).run()
    package = sink.finalise(report)
    db_session.commit()
    return str(package.snapshot_id)


AS_OF = dt.datetime.combine(END, dt.time(23, 59, 59), tzinfo=dt.UTC)


class TestTheCausalFeed:
    def test_no_view_ever_contains_a_bar_from_the_future(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The property everything downstream rests on."""
        feed = CausalFeed(db_session)
        sessions = feed.sessions(1, as_of=AS_OF)
        mode, _ = feed.mode_for(1, AS_OF)
        seen = 0
        for view in feed.walk(1, sessions, mode=mode, as_of=AS_OF):
            seen += 1
            assert view.bars, "a view with no bars cannot be evaluated"
            assert max(bar.session_date for bar in view.bars) == view.session_date
        assert seen == len(sessions)

    def test_each_view_is_a_prefix_of_the_next(self, db_session: Session, snapshot: str) -> None:
        feed = CausalFeed(db_session)
        sessions = feed.sessions(1, as_of=AS_OF)
        mode, _ = feed.mode_for(1, AS_OF)
        previous: list[dt.date] = []
        for view in feed.walk(1, sessions, mode=mode, as_of=AS_OF):
            dates = [bar.session_date for bar in view.bars]
            assert dates[: len(previous)] == previous
            previous = dates

    def test_both_feed_modes_produce_identical_views(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The claim the optimisation rests on, checked rather than asserted.

        `VERIFIED_PREFIX` is only allowed when it is *equivalent* to one
        point-in-time read per session. This proves the equivalence on a
        snapshot that qualifies, which is what makes the precondition a
        precondition rather than a hope.
        """
        feed = CausalFeed(db_session)
        sessions = feed.sessions(1, as_of=AS_OF)
        assert feed.mode_for(1, AS_OF)[0] is FeedMode.VERIFIED_PREFIX

        fast = list(feed.walk(1, sessions, mode=FeedMode.VERIFIED_PREFIX, as_of=AS_OF))
        slow = list(feed.walk(1, sessions, mode=FeedMode.PER_SESSION_READ, as_of=AS_OF))
        assert len(fast) == len(slow) == len(sessions)
        for a, b in zip(fast, slow, strict=True):
            assert a.session_date == b.session_date
            assert [bar.session_date for bar in a.bars] == [bar.session_date for bar in b.bars]
            assert [bar.close for bar in a.bars] == [bar.close for bar in b.bars]

    def test_a_revised_bar_forces_the_slow_path(self, db_session: Session, snapshot: str) -> None:
        """A revision makes prefixing wrong, and the feed must notice."""
        original = db_session.scalars(
            select(t.OhlcvBar).where(t.OhlcvBar.instrument_id == 1).limit(1)
        ).one()
        db_session.add(
            t.OhlcvBar(
                instrument_id=original.instrument_id,
                timeframe=original.timeframe,
                session_date=original.session_date,
                event_time=original.event_time,
                knowledge_time=original.knowledge_time + dt.timedelta(days=400),
                knowledge_source=original.knowledge_source,
                open=original.open,
                high=original.high,
                low=original.low,
                close=original.close,
                volume=original.volume,
                quality=original.quality,
                source=original.source,
            )
        )
        db_session.commit()
        mode, reason = CausalFeed(db_session).mode_for(1, AS_OF)
        assert mode is FeedMode.PER_SESSION_READ
        assert "revision" in reason

    def test_the_evaluation_instant_is_never_before_the_exchange_close(
        self, db_session: Session, snapshot: str
    ) -> None:
        # A snapshot that stamped a bar mid-session must not pull the
        # evaluation forward into the session it summarises.
        feed = CausalFeed(db_session)
        day = SESSIONS[10]
        early = dt.datetime.combine(day, dt.time(12, 0), tzinfo=dt.UTC)
        assert feed.clock_for(day, early).as_of == feed.calendar_close(day)


class TestTheScan:
    def test_a_scan_persists_patterns_and_records_its_provenance(
        self, db_session: Session, snapshot: str
    ) -> None:
        report = SnapshotScanner(
            db_session,
            snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()

        assert len(report.scanned) == len(SYMBOLS)
        assert sum(i.sessions_scanned for i in report.scanned) == len(SESSIONS) * len(SYMBOLS)

        run = db_session.scalars(select(t.ScanRun).where(t.ScanRun.scan_id == report.scan_id)).one()
        assert run.status == "completed"
        assert run.snapshot_id == snapshot
        assert run.code_version == "test"
        assert run.pattern_config_digest and run.breakout_config_digest
        assert run.timeframe == str(Bartimeframe.D1)

        progress = db_session.scalars(
            select(t.ScanProgress).where(t.ScanProgress.scan_run_id == run.id)
        ).all()
        assert {row.ticker for row in progress} == set(SYMBOLS)

    def test_resuming_adds_nothing_and_rescans_nothing(
        self, db_session: Session, snapshot: str
    ) -> None:
        """Idempotency, which is what makes an interrupted long scan safe."""
        options = ScanOptions(scan_id="fixed", code_version="test", progress_every=0)
        first = SnapshotScanner(db_session, snapshot, options=options).run()
        before = _counts(db_session)

        second = SnapshotScanner(db_session, snapshot, options=options).run()
        assert second.scanned == []
        assert len(second.resumed_instruments) == len(SYMBOLS)
        assert _counts(db_session) == before
        assert first.scan_id == second.scan_id

    def test_forcing_a_rescan_writes_no_duplicate_observations(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The repositories are idempotent by session; prove it end to end."""
        options = ScanOptions(scan_id="fixed", code_version="test", progress_every=0)
        SnapshotScanner(db_session, snapshot, options=options).run()
        before = _counts(db_session)

        forced = ScanOptions(scan_id="fixed", code_version="test", progress_every=0, force=True)
        SnapshotScanner(db_session, snapshot, options=forced).run()
        assert _counts(db_session) == before

    def test_restricting_to_one_ticker_scans_only_that_one(
        self, db_session: Session, snapshot: str
    ) -> None:
        report = SnapshotScanner(
            db_session,
            snapshot,
            options=ScanOptions(tickers=("ALPHA",), code_version="test", progress_every=0),
        ).run()
        assert [i.ticker for i in report.scanned] == ["ALPHA"]

    def test_an_unknown_snapshot_is_refused_rather_than_scanned_empty(
        self, db_session: Session
    ) -> None:
        with pytest.raises(DataError, match="no imported snapshot"):
            SnapshotScanner(db_session, "does-not-exist").run()

    def test_every_persisted_observation_falls_inside_the_scan_window(
        self, db_session: Session, snapshot: str
    ) -> None:
        SnapshotScanner(
            db_session, snapshot, options=ScanOptions(code_version="test", progress_every=0)
        ).run()
        bounds = db_session.execute(
            select(
                func.min(t.PatternObservation.session_date),
                func.max(t.PatternObservation.session_date),
            )
        ).one()
        if bounds[0] is None:
            pytest.skip("this fixture produced no patterns")
        assert bounds[0] >= SESSIONS[0]
        assert bounds[1] <= SESSIONS[-1]

    def test_the_report_states_what_it_does_not_produce(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The complement of the gate's ban on performance measures."""
        report = SnapshotScanner(
            db_session, snapshot, options=ScanOptions(code_version="test", progress_every=0)
        ).run()
        text = report.render()
        for claim in SCAN_DOES_NOT_PRODUCE:
            assert claim in text
        assert "not a signal, a recommendation" in text
        payload = report.to_payload()
        for forbidden in ("win_rate", "expectancy", "profit", "sharpe", "return"):
            assert not any(forbidden in key for key in payload)


def _counts(session: Session) -> tuple[int, int, int, int]:
    return tuple(  # type: ignore[return-value]
        session.scalar(select(func.count()).select_from(table)) or 0
        for table in (
            t.Pattern,
            t.PatternObservation,
            t.BreakoutEvent,
            t.BreakoutObservation,
        )
    )
