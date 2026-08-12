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
from tradeit.scanning.episodes import segment_sessions
from tradeit.scanning.runner import SCAN_DOES_NOT_PRODUCE
from tradeit.storage import tables as t
from tradeit.validation.checks import CheckStatus, assert_no_performance_claims
from tradeit.validation.context import load_context
from tradeit.validation.scan_checks import BreakoutLifecycle, scan_checks

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


#: Sessions omitted from instrument 2, wide enough to end its episode. Shaped
#: like the real BBBY case: a long hole, and prices on the far side that belong
#: to a different security.
BREAK_FROM, BREAK_TO = 200, 340


def write_package(root: Path, *, structural_break: bool = False) -> PackageManifest:
    rows = ["date,symbol,open,high,low,close,volume"]
    for index, _symbol in enumerate(SYMBOLS, start=1):
        price = 40.0 + index * 25
        for step, day in enumerate(SESSIONS):
            price *= 1.0 + (0.006 if step % 5 else -0.013)
            if structural_break and index == 2:
                if BREAK_FROM <= step < BREAK_TO:
                    continue
                if step == BREAK_TO:
                    # A different listing entirely: nothing about the pre-break
                    # levels means anything here.
                    price = 7.5
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
def broken_snapshot(db_session: Session, tmp_path: Path) -> str:
    """A snapshot whose second instrument has a structural break in it."""
    manifest = write_package(tmp_path, structural_break=True)
    sink = DatabaseSink(session=db_session, manifest=manifest, source_path=tmp_path)
    report = PackageImporter(manifest, tmp_path, options=ImportOptions(), sink=sink).run()
    package = sink.finalise(report)
    db_session.commit()
    return str(package.snapshot_id)


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


class TestAStructuralBreakResetsEverything:
    """A long discontinuity ends the analytical history; nothing crosses it.

    The real case: 3,638 bars under the ticker BBBY from 2010 to 2026 with a
    536-session hole, where the bars after the hole belong to whatever took the
    symbol. Every property here is one that, if it failed, would let a dead
    company's structures reach a live listing.
    """

    def test_the_feed_never_shows_a_pre_break_bar_after_the_break(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        """The floor is a floor, not a hint.

        This is the load-bearing one: it is what stops a 200-day average, an
        ATR, a pivot search and a boundary from reaching back across the break.
        """
        feed = CausalFeed(db_session)
        sessions = feed.sessions(2, as_of=AS_OF)
        episodes = segment_sessions(sessions)
        assert len(episodes) == 2, "fixture must contain a structural break"
        second = episodes[1]

        for view in feed.walk(
            2, second.sessions, mode=feed.mode_for(2, AS_OF)[0], as_of=AS_OF, floor=second.start
        ):
            assert min(bar.session_date for bar in view.bars) >= second.start

    def test_the_unbroken_instrument_is_one_episode(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        feed = CausalFeed(db_session)
        assert len(segment_sessions(feed.sessions(1, as_of=AS_OF))) == 1

    def test_no_pattern_identity_survives_the_break(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        """A pattern created before the break may not reappear after it.

        Not "is unlikely to" — cannot. Each episode gets its own tracker, so a
        pre-break identity has no object on the far side to be advanced onto.
        """
        SnapshotScanner(
            db_session,
            broken_snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()

        episodes = segment_sessions(CausalFeed(db_session).sessions(2, as_of=AS_OF))
        boundary = episodes[1].start
        straddling = db_session.scalars(
            select(t.Pattern).where(
                t.Pattern.instrument_id == 2,
                t.Pattern.structural_start_date < boundary,
                t.Pattern.last_observed_session >= boundary,
            )
        ).all()
        assert straddling == [], (
            "a pattern whose structure began before the break was still being "
            f"observed after it: {[p.identity_key for p in straddling]}"
        )

    def test_no_pattern_observation_straddles_the_break(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        """Lifecycle state does not flow across the boundary either."""
        SnapshotScanner(
            db_session,
            broken_snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()
        episodes = segment_sessions(CausalFeed(db_session).sessions(2, as_of=AS_OF))
        boundary = episodes[1].start

        rows = db_session.execute(
            select(
                t.Pattern.identity_key,
                func.min(t.PatternObservation.session_date),
                func.max(t.PatternObservation.session_date),
            )
            .join(t.PatternObservation, t.PatternObservation.pattern_id == t.Pattern.id)
            .where(t.Pattern.instrument_id == 2)
            .group_by(t.Pattern.identity_key)
        ).all()
        offenders = [key for key, first, last in rows if first < boundary <= last]
        assert offenders == [], f"observations straddle the break: {offenders}"

    def test_no_breakout_event_watches_a_boundary_from_the_other_side(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        """A monitor must not carry a dead listing's level into a live one."""
        SnapshotScanner(
            db_session,
            broken_snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()
        episodes = segment_sessions(CausalFeed(db_session).sessions(2, as_of=AS_OF))
        boundary = episodes[1].start

        rows = db_session.execute(
            select(
                t.BreakoutEvent.event_key,
                t.BreakoutEvent.opened_session,
                func.max(t.BreakoutObservation.session_date),
            )
            .join(
                t.BreakoutObservation,
                t.BreakoutObservation.event_id == t.BreakoutEvent.id,
            )
            .where(t.BreakoutEvent.instrument_id == 2)
            .group_by(t.BreakoutEvent.event_key, t.BreakoutEvent.opened_session)
        ).all()
        offenders = [key for key, opened, last in rows if opened < boundary <= last]
        assert offenders == [], f"breakout events straddle the break: {offenders}"

    def test_the_scan_report_names_the_episodes(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        report = SnapshotScanner(
            db_session,
            broken_snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()
        broken = next(i for i in report.scanned if i.instrument_id == 2)
        assert len(broken.episodes) == 2
        assert broken.episodes[1].break_sessions >= 21
        payload = broken.to_payload()["episodes"]
        assert len(payload) == 2 and payload[1]["break_sessions_before"] >= 21

    def test_no_bar_is_manufactured_to_fill_the_gap(
        self, db_session: Session, broken_snapshot: str
    ) -> None:
        SnapshotScanner(
            db_session,
            broken_snapshot,
            options=ScanOptions(code_version="test", progress_every=0),
        ).run()
        episodes = segment_sessions(CausalFeed(db_session).sessions(2, as_of=AS_OF))
        inside = db_session.scalar(
            select(func.count())
            .select_from(t.OhlcvBar)
            .where(
                t.OhlcvBar.instrument_id == 2,
                t.OhlcvBar.session_date > episodes[0].end,
                t.OhlcvBar.session_date < episodes[1].start,
            )
        )
        assert inside == 0, "the gap must stay a gap"


class TestTheScanChecksSurviveTheirOwnGuard:
    """The failure that aborted the post-scan validation of `diag-01`.

    Every Phase 4/5 check is passed through
    :func:`~tradeit.validation.checks.assert_no_performance_claims` by the
    runner. Until the seven-instrument scan there had never been a database
    with breakouts in it during a test, so `phase5.lifecycle` had only ever
    SKIPped — and the summary it builds when it has something to report said
    "all transitions are edges of the lifecycle", which the guard read as
    *trading* edge and refused.

    A guard is only tested by the strings a real run produces, so this scans,
    runs the checks for real, and puts each result through the guard.

    The fixture tickers are ALPHA and BETA. That is not a coincidence any more:
    `alpha` is a forbidden measure, `phase4.concentration` interpolates ticker
    names into its examples, and a symbol is data rather than a claim.
    """

    def test_every_scan_check_result_passes_the_performance_guard(
        self, db_session: Session, snapshot: str
    ) -> None:
        SnapshotScanner(
            db_session, snapshot, options=ScanOptions(code_version="test", progress_every=0)
        ).run()
        context = load_context(db_session, snapshot, universe=None)
        conclusive = 0
        for check in scan_checks():
            result = check.run(context)
            assert_no_performance_claims(result)
            conclusive += result.status is not CheckStatus.SKIPPED
        assert conclusive == len(scan_checks()), (
            "every scan check must have had data to run on — a SKIP here means "
            "this test proves nothing, which is exactly how the defect survived"
        )

    def test_the_lifecycle_check_reports_transitions_without_saying_edge(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The rename, asserted on the real summary rather than on a fixture."""
        SnapshotScanner(
            db_session, snapshot, options=ScanOptions(code_version="test", progress_every=0)
        ).run()
        result = BreakoutLifecycle().run(load_context(db_session, snapshot, universe=None))
        assert result.status is CheckStatus.PASS
        assert "transition" in result.summary
        assert "state_transitions" in result.evidence
        assert "illegal_transitions" in result.evidence
        assert result.evidence["illegal_transitions"] == 0


class TestACrashLeavesAResumableScan:
    """What the real AAPL failure did to `diag-01`, tested rather than assumed.

    The scan is instrument-transactional: everything for one instrument commits
    with its progress row or not at all. These assert the consequences that
    matter after a crash — no half-written instrument, no false progress row,
    and a re-run that finishes the job without duplicating what committed.
    """

    def test_a_crash_writes_nothing_for_the_instrument_in_flight(
        self, db_session: Session, snapshot: str
    ) -> None:
        scanner = SnapshotScanner(
            db_session,
            snapshot,
            options=ScanOptions(scan_id="crashy", code_version="test", progress_every=0),
        )
        real_persist = scanner._persist
        calls = {"n": 0}

        def explode(*args: object, **kwargs: object) -> None:
            calls["n"] += 1
            if calls["n"] == 2:  # let the first instrument through, break the second
                raise RuntimeError("simulated database failure mid-persist")
            real_persist(*args, **kwargs)  # type: ignore[arg-type]

        scanner._persist = explode  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="simulated"):
            scanner.run()

        run = db_session.scalars(select(t.ScanRun).where(t.ScanRun.scan_id == "crashy")).one()
        # The run row survives the crash and says what happened, so an operator
        # can tell "never started" from "stopped part way".
        assert run.status == "failed"
        progress = db_session.scalars(
            select(t.ScanProgress).where(t.ScanProgress.scan_run_id == run.id)
        ).all()
        assert [row.instrument_id for row in progress] == [1], (
            "exactly the instrument that committed has a progress row"
        )
        # Nothing at all for the instrument that was in flight.
        assert (
            db_session.scalar(
                select(func.count()).select_from(t.Pattern).where(t.Pattern.instrument_id == 2)
            )
            == 0
        )
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(t.BreakoutEvent)
                .where(t.BreakoutEvent.instrument_id == 2)
            )
            == 0
        )

    def test_rerunning_the_same_scan_id_finishes_it_without_duplicating(
        self, db_session: Session, snapshot: str
    ) -> None:
        """The operator's actual next step after a crash."""
        scanner = SnapshotScanner(
            db_session,
            snapshot,
            options=ScanOptions(scan_id="crashy", code_version="test", progress_every=0),
        )
        real_persist = scanner._persist
        calls = {"n": 0}

        def explode(*args: object, **kwargs: object) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated database failure mid-persist")
            real_persist(*args, **kwargs)  # type: ignore[arg-type]

        scanner._persist = explode  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            scanner.run()
        after_crash = _counts(db_session)
        assert after_crash[0] > 0, "the first instrument did commit"

        report = SnapshotScanner(
            db_session,
            snapshot,
            options=ScanOptions(scan_id="crashy", code_version="test", progress_every=0),
        ).run()

        # The completed instrument is resumed, the interrupted one is scanned.
        assert len(report.resumed_instruments) == 1
        assert [i.instrument_id for i in report.scanned] == [2]

        run = db_session.scalars(select(t.ScanRun).where(t.ScanRun.scan_id == "crashy")).one()
        assert run.status == "completed"
        progress = db_session.scalars(
            select(t.ScanProgress).where(t.ScanProgress.scan_run_id == run.id)
        ).all()
        assert sorted(row.instrument_id for row in progress) == [1, 2]
        assert len(progress) == 2, "one row per instrument, never two"

        # Nothing the first run committed was written a second time.
        final = _counts(db_session)
        assert final[0] > after_crash[0], "the second instrument added patterns"
        first_instrument_patterns = db_session.scalar(
            select(func.count()).select_from(t.Pattern).where(t.Pattern.instrument_id == 1)
        )
        assert first_instrument_patterns == after_crash[0]

    def test_a_third_run_after_completion_changes_nothing(
        self, db_session: Session, snapshot: str
    ) -> None:
        options = ScanOptions(scan_id="crashy", code_version="test", progress_every=0)
        SnapshotScanner(db_session, snapshot, options=options).run()
        before = _counts(db_session)
        again = SnapshotScanner(db_session, snapshot, options=options).run()
        assert again.scanned == []
        assert _counts(db_session) == before
