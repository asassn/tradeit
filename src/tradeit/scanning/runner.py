"""Driving Phase 4 and Phase 5 across an imported snapshot, session by session.

Phase 4's detectors and Phase 5's breakout engine were both implemented and
both tested against synthetic corpora. Neither had ever been run over real
imported bars, and the reason was not the detectors — it was that nothing
existed to *drive* them: to walk a snapshot chronologically, hand each session
only what was knowable then, keep pattern and event identities alive across
sessions, and write the results down. This module is that driver.

Four properties it is built around, in order of how badly their absence would
corrupt the output:

**Causality.** Bars arrive through :class:`~tradeit.scanning.feed.CausalFeed`,
which either issues one point-in-time read per session or proves the snapshot
lets it prefix a single read. A detector is never handed a bar dated after the
session it is evaluating, and never a revision that did not exist yet.

**Identity.** One :class:`~tradeit.patterns.scanner.PatternScanner` and one
:class:`~tradeit.breakouts.monitor.BreakoutMonitor` per instrument, alive for
the whole walk. A fresh scanner per session would re-mint every pattern every
day — the failure the identity design exists to prevent — and would make
breakout attempt numbering meaningless.

**Idempotency.** Everything for one instrument commits in one transaction
together with its :class:`~tradeit.storage.tables.ScanProgress` row, so the
progress ledger and the observations cannot disagree. Resume skips instruments
that already have a row. The two repositories are themselves idempotent by
session, so re-running one is safe rather than merely wasteful.

**Observation, not recommendation.** A confirmed breakout is a recorded event.
Nothing here ranks, sizes, or selects, and :data:`SCAN_PRODUCES` says so in the
report, because a table called ``breakout_events`` full of ``CONFIRMED`` rows is
exactly the artefact somebody will eventually read as a buy list.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.breakouts.base import BreakoutEvent
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.monitor import BreakoutMonitor
from tradeit.breakouts.persistence import BreakoutRepository
from tradeit.core.enums import Bartimeframe
from tradeit.data.packages.capability import CapabilityIndex
from tradeit.errors import DataError
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.persistence import PatternRepository
from tradeit.patterns.scanner import PatternScanner
from tradeit.patterns.tracking import PatternTracker
from tradeit.reproducibility.versioning import content_hash
from tradeit.scanning.episodes import AnalyticalEpisode, segment_sessions
from tradeit.scanning.feed import CausalFeed, FeedMode
from tradeit.storage import tables as t

__all__ = [
    "SCAN_PRODUCES",
    "InstrumentScan",
    "ScanOptions",
    "ScanReport",
    "SnapshotScanner",
]

#: Printed in every report. The gate forbids performance measures; this is the
#: complementary statement, that what *is* produced is not a decision either.
SCAN_PRODUCES: tuple[str, ...] = (
    "pattern observations with their state transitions",
    "breakout events with their lifecycle observations",
    "per-detector and per-state counts",
)

#: And what it deliberately does not produce.
SCAN_DOES_NOT_PRODUCE: tuple[str, ...] = (
    "any ranking, score threshold or selection among instruments",
    "any statement that a confirmed breakout should be traded",
    "any forward return, win rate, expectancy or profitability measure",
)


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """What to scan, and how far."""

    #: Stable identifier for this scan. Re-running with the same one resumes it.
    scan_id: str = ""
    timeframe: Bartimeframe = Bartimeframe.D1
    #: Restrict to these tickers. Empty means every instrument in the snapshot.
    tickers: tuple[str, ...] = ()
    #: Restrict to these surrogate instrument ids. Unioned with ``tickers``, so
    #: a diagnostic set can name an instrument the validation report identified
    #: by number before anyone knows its symbol. Identity in this system is the
    #: surrogate key; the ticker is a label on it.
    instrument_ids: tuple[int, ...] = ()
    start: dt.date | None = None
    end: dt.date | None = None
    #: Skip instruments the snapshot's capability index does not mark as
    #: carrying price data. Never used to *widen* eligibility.
    respect_capabilities: bool = True
    #: Re-scan instruments that already have a progress row for this scan_id.
    force: bool = False
    breakout_profile: str | None = None
    code_version: str = "unknown"
    #: Sessions after which a long instrument reports progress.
    progress_every: int = 500

    def resolved_scan_id(self, snapshot_id: str) -> str:
        if self.scan_id:
            return self.scan_id
        digest = content_hash(
            {
                "snapshot": snapshot_id,
                "timeframe": str(self.timeframe),
                "tickers": sorted(self.tickers),
                "instrument_ids": sorted(self.instrument_ids),
                "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None,
                "profile": self.breakout_profile or "",
            }
        )[:16]
        return f"scan-{digest}"


@dataclass(slots=True)
class InstrumentScan:
    """What one instrument's walk produced."""

    instrument_id: int
    ticker: str
    sessions_scanned: int = 0
    bars_read: int = 0
    first_session: dt.date | None = None
    last_session: dt.date | None = None
    feed_mode: FeedMode = FeedMode.PER_SESSION_READ
    feed_reason: str = ""
    #: The analytical episodes this instrument's series was split into. More
    #: than one means a structural break ended a history and started another.
    episodes: tuple[AnalyticalEpisode, ...] = ()
    patterns_persisted: int = 0
    breakouts_persisted: int = 0
    detections: Counter[str] = field(default_factory=Counter)
    pattern_states: Counter[str] = field(default_factory=Counter)
    breakout_states: Counter[str] = field(default_factory=Counter)
    skipped_detectors: Counter[str] = field(default_factory=Counter)
    elapsed: float = 0.0
    skipped_reason: str = ""

    @property
    def was_scanned(self) -> bool:
        return not self.skipped_reason

    def to_payload(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "sessions_scanned": self.sessions_scanned,
            "bars_read": self.bars_read,
            "first_session": self.first_session.isoformat() if self.first_session else None,
            "last_session": self.last_session.isoformat() if self.last_session else None,
            "feed_mode": str(self.feed_mode),
            "feed_reason": self.feed_reason,
            "episodes": [episode.to_payload() for episode in self.episodes],
            "patterns_persisted": self.patterns_persisted,
            "breakouts_persisted": self.breakouts_persisted,
            "detections": dict(sorted(self.detections.items())),
            "pattern_states": dict(sorted(self.pattern_states.items())),
            "breakout_states": dict(sorted(self.breakout_states.items())),
            "elapsed_seconds": round(self.elapsed, 3),
            "skipped_reason": self.skipped_reason,
        }


@dataclass(slots=True)
class ScanReport:
    """What the whole scan did. Every number measured, none estimated."""

    scan_id: str
    snapshot_id: str
    timeframe: Bartimeframe
    as_of: dt.datetime
    code_version: str
    pattern_config_digest: str
    breakout_config_digest: str
    breakout_profile: str | None = None
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    finished_at: dt.datetime | None = None
    instruments: list[InstrumentScan] = field(default_factory=list)
    resumed_instruments: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def scanned(self) -> list[InstrumentScan]:
        return [i for i in self.instruments if i.was_scanned]

    @property
    def detections(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for item in self.scanned:
            total.update(item.detections)
        return total

    @property
    def pattern_states(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for item in self.scanned:
            total.update(item.pattern_states)
        return total

    @property
    def breakout_states(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for item in self.scanned:
            total.update(item.breakout_states)
        return total

    @property
    def instruments_with_no_detections(self) -> list[str]:
        return [
            i.ticker or str(i.instrument_id) for i in self.scanned if not sum(i.detections.values())
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "snapshot_id": self.snapshot_id,
            "timeframe": str(self.timeframe),
            "as_of": self.as_of.isoformat(),
            "code_version": self.code_version,
            "pattern_config_digest": self.pattern_config_digest,
            "breakout_config_digest": self.breakout_config_digest,
            "breakout_profile": self.breakout_profile,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "instruments_scanned": len(self.scanned),
            "instruments_resumed": list(self.resumed_instruments),
            "sessions_scanned": sum(i.sessions_scanned for i in self.scanned),
            "patterns_persisted": sum(i.patterns_persisted for i in self.scanned),
            "breakouts_persisted": sum(i.breakouts_persisted for i in self.scanned),
            "detections_by_detector": dict(sorted(self.detections.items())),
            "pattern_states": dict(sorted(self.pattern_states.items())),
            "breakout_states": dict(sorted(self.breakout_states.items())),
            "instruments_with_no_detections": self.instruments_with_no_detections,
            "produces": list(SCAN_PRODUCES),
            "does_not_produce": list(SCAN_DOES_NOT_PRODUCE),
            "instruments": [i.to_payload() for i in self.instruments],
            "problems": list(self.problems),
            "notes": list(self.notes),
        }

    def render(self) -> str:
        scanned = self.scanned
        sessions = sum(i.sessions_scanned for i in scanned)
        lines = [
            "EMPIRICAL SCAN",
            "=" * 78,
            f"scan id              : {self.scan_id}",
            f"snapshot             : {self.snapshot_id}",
            f"timeframe            : {self.timeframe}",
            f"as of                : {self.as_of.isoformat()}",
            f"code version         : {self.code_version}",
            f"pattern config       : {self.pattern_config_digest}",
            f"breakout config      : {self.breakout_config_digest}"
            + (f"  profile={self.breakout_profile}" if self.breakout_profile else ""),
            "",
            f"instruments scanned  : {len(scanned)}",
            f"instruments resumed  : {len(self.resumed_instruments)}  (already complete)",
            f"sessions evaluated   : {sessions:,}",
            f"bars read            : {sum(i.bars_read for i in scanned):,}",
            f"patterns persisted   : {sum(i.patterns_persisted for i in scanned):,}",
            f"breakouts persisted  : {sum(i.breakouts_persisted for i in scanned):,}",
            f"elapsed              : {sum(i.elapsed for i in scanned):.1f}s",
        ]

        detections = self.detections
        lines += ["", "Detections by detector", "-" * 78]
        if detections:
            width = max(len(name) for name in detections)
            for name, count in sorted(detections.items()):
                per_session = count / sessions if sessions else 0.0
                lines.append(f"  {name:<{width}}  {count:>8,}   {per_session:.4f} per session")
        else:
            lines.append("  none")

        for title, counts in (
            ("Pattern states (final, per identity)", self.pattern_states),
            ("Breakout event states (final)", self.breakout_states),
        ):
            lines += ["", title, "-" * 78]
            lines += (
                [f"  {name:<32} {count:>8,}" for name, count in sorted(counts.items())]
                if counts
                else ["  none"]
            )

        silent = self.instruments_with_no_detections
        lines += ["", "Coverage", "-" * 78]
        lines.append(
            f"  instruments with zero detections   {len(silent)} of {len(scanned)}"
            + (f"   ({', '.join(silent[:10])})" if silent else "")
        )
        modes = Counter(str(i.feed_mode) for i in scanned)
        lines.append(f"  causal feed modes                  {dict(modes)}")

        if self.problems:
            lines += ["", "Problems", "-" * 78] + [f"  - {p}" for p in self.problems]
        if self.notes:
            lines += ["", "Notes", "-" * 78] + [f"  - {n}" for n in self.notes]

        lines += [
            "",
            "What this scan produced",
            "-" * 78,
            *[f"  + {item}" for item in SCAN_PRODUCES],
            *[f"  - {item}" for item in SCAN_DOES_NOT_PRODUCE],
            "",
            "  A CONFIRMED breakout is a recorded observation about price behaviour.",
            "  It is not a signal, a recommendation, or a reason to buy anything.",
            "",
            "Next",
            "-" * 78,
            f"  tradeit validate --snapshot {self.snapshot_id}",
            "  The Phase 4 and Phase 5 gate checks read what this scan persisted.",
        ]
        return "\n".join(lines)


class SnapshotScanner:
    """Runs the Phase 4 detectors and the Phase 5 engine over one snapshot."""

    def __init__(
        self,
        session: Session,
        snapshot_id: str,
        *,
        options: ScanOptions | None = None,
        pattern_config: PatternEngineConfig | None = None,
        breakout_config: BreakoutEngineConfig | None = None,
        capabilities: CapabilityIndex | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.session = session
        self.snapshot_id = snapshot_id
        self.options = options or ScanOptions()
        self.pattern_config = pattern_config or PatternEngineConfig()
        self.breakout_config = breakout_config or BreakoutEngineConfig()
        self.capabilities = capabilities or CapabilityIndex()
        self.on_progress = on_progress or (lambda _line: None)
        self.feed = CausalFeed(session, timeframe=self.options.timeframe)

    # -- entry point ---------------------------------------------------------

    def run(self) -> ScanReport:
        package = self._package()
        as_of = _end_of_day(package.observed_end or package.coverage_end)
        scan_id = self.options.resolved_scan_id(self.snapshot_id)

        report = ScanReport(
            scan_id=scan_id,
            snapshot_id=self.snapshot_id,
            timeframe=self.options.timeframe,
            as_of=as_of,
            code_version=self.options.code_version,
            pattern_config_digest=content_hash(self.pattern_config.model_dump(mode="json"))[:32],
            breakout_config_digest=content_hash(self.breakout_config.model_dump(mode="json"))[:32],
            breakout_profile=self.options.breakout_profile,
        )
        if package.adjustment_policy != "raw_unadjusted":
            report.notes.append(
                f"the snapshot's prices are {package.adjustment_policy}; boundaries and "
                "levels are in that representation, and a window straddling a split is "
                "rescaled non-uniformly"
            )

        run_row = self._scan_run_row(report)
        done = self._completed_instruments(run_row.id)
        targets = self._targets()
        report.notes.append(f"{len(targets)} instrument(s) selected from the snapshot")

        for position, (instrument_id, ticker) in enumerate(targets, start=1):
            if instrument_id in done and not self.options.force:
                report.resumed_instruments.append(ticker or str(instrument_id))
                continue
            self.on_progress(f"[{position}/{len(targets)}] {ticker or instrument_id}")
            outcome = self._scan_instrument(instrument_id, ticker, as_of=as_of)
            report.instruments.append(outcome)
            if outcome.was_scanned:
                self._record_progress(run_row.id, outcome)
            # One commit per instrument, with its progress row. An interrupt
            # loses at most the instrument in flight, and the database rolls it
            # back rather than leaving it half-scanned.
            self.session.commit()

        run_row.status = "completed"
        run_row.finished_at = dt.datetime.now(dt.UTC)
        run_row.instruments_completed = len(report.scanned) + len(report.resumed_instruments)
        report.finished_at = run_row.finished_at
        run_row.report = report.to_payload()
        self.session.commit()
        return report

    # -- selection -----------------------------------------------------------

    def _package(self) -> t.DataPackage:
        package = self.session.scalars(
            select(t.DataPackage).where(t.DataPackage.snapshot_id == self.snapshot_id)
        ).first()
        if package is None:
            raise DataError(
                f"no imported snapshot {self.snapshot_id!r}. A scan is defined against a "
                "snapshot so its output can be attributed to identified bytes; there is "
                "no path from 'a database somewhere' to a persisted pattern."
            )
        return package

    def _targets(self) -> list[tuple[int, str]]:
        """(instrument_id, ticker) pairs to scan, in a stable order."""
        rows = self.session.execute(
            select(t.OhlcvBar.instrument_id, func.count())
            .where(t.OhlcvBar.timeframe == self.options.timeframe.value)
            .group_by(t.OhlcvBar.instrument_id)
        ).all()
        tickers = {
            row.instrument_id: row.ticker
            for row in self.session.scalars(select(t.SymbolMapping)).all()
        }
        wanted = {ticker.upper() for ticker in self.options.tickers}
        wanted_ids = set(self.options.instrument_ids)
        restricted = bool(wanted or wanted_ids)
        eligible = self._price_eligible()

        targets: list[tuple[int, str]] = []
        for instrument_id, _count in rows:
            ticker = tickers.get(instrument_id, "")
            named = ticker.upper() in wanted or instrument_id in wanted_ids
            if restricted and not named:
                continue
            # An explicitly named instrument is scanned whatever the capability
            # index says. Naming one is a deliberate diagnostic act, and the
            # report records which instruments carry only scale-invariant
            # eligibility, so the widening is visible rather than silent.
            if not named and eligible is not None and ticker and ticker not in eligible:
                continue
            targets.append((instrument_id, ticker))
        targets.sort(key=lambda pair: (pair[1] or "", pair[0]))
        return targets

    def _price_eligible(self) -> set[str] | None:
        """Tickers the capability index marks as carrying price data.

        ``None`` when capabilities were never recorded, which means *unknown*
        and is answered by scanning everything the snapshot holds bars for.
        Never used to widen eligibility beyond that: an instrument absent from
        a recorded index is not scanned, because the index is the only thing
        that knows an instrument's data was refused rather than missing.
        """
        if not self.options.respect_capabilities or self.capabilities.is_empty:
            return None
        # Keyed on ticker, not on the package's instrument_id: the importer
        # assigns its own surrogate keys, so the package's numbering and the
        # database's are two different sequences that happen to look alike.
        eligible = set(self.capabilities.price_eligible)
        return {
            record.ticker
            for record in self.capabilities.records
            if record.ticker and record.instrument_id in eligible
        }

    def _scan_run_row(self, report: ScanReport) -> t.ScanRun:
        existing = self.session.scalars(
            select(t.ScanRun).where(t.ScanRun.scan_id == report.scan_id)
        ).first()
        if existing is not None:
            existing.status = "running"
            existing.finished_at = None
            self.session.flush()
            return existing
        row = t.ScanRun(
            scan_id=report.scan_id,
            snapshot_id=report.snapshot_id,
            timeframe=str(report.timeframe),
            as_of=report.as_of,
            start_session=self.options.start,
            end_session=self.options.end,
            code_version=report.code_version,
            pattern_config_digest=report.pattern_config_digest,
            breakout_config_digest=report.breakout_config_digest,
            breakout_profile=report.breakout_profile,
            status="running",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _completed_instruments(self, scan_run_id: int) -> set[int]:
        rows = self.session.scalars(
            select(t.ScanProgress.instrument_id).where(t.ScanProgress.scan_run_id == scan_run_id)
        ).all()
        return {int(value) for value in rows}

    def _record_progress(self, scan_run_id: int, outcome: InstrumentScan) -> None:
        """Write or refresh this instrument's ledger entry.

        Updates in place when a row already exists, which is the ``--force``
        path: re-scanning a completed instrument is legitimate (the two
        repositories are idempotent by session, so it writes no duplicate
        observations) and must refresh the ledger rather than collide with it.
        Inserting blindly made the unique constraint fire and took the whole
        run down — found by the test that asserts a forced re-scan changes no
        counts.
        """
        row = self.session.scalars(
            select(t.ScanProgress).where(
                t.ScanProgress.scan_run_id == scan_run_id,
                t.ScanProgress.instrument_id == outcome.instrument_id,
            )
        ).first()
        if row is None:
            row = t.ScanProgress(scan_run_id=scan_run_id, instrument_id=outcome.instrument_id)
            self.session.add(row)
        row.ticker = outcome.ticker or None
        row.first_session = outcome.first_session
        row.last_session = outcome.last_session
        row.sessions_scanned = outcome.sessions_scanned
        row.bars_read = outcome.bars_read
        row.patterns_persisted = outcome.patterns_persisted
        row.breakouts_persisted = outcome.breakouts_persisted
        row.feed_mode = str(outcome.feed_mode)
        row.elapsed_seconds = outcome.elapsed
        row.finished_at = dt.datetime.now(dt.UTC)
        self.session.flush()

    # -- the per-instrument walk --------------------------------------------

    def _scan_instrument(
        self, instrument_id: int, ticker: str, *, as_of: dt.datetime
    ) -> InstrumentScan:
        started = time.perf_counter()
        outcome = InstrumentScan(instrument_id=instrument_id, ticker=ticker)

        sessions = self.feed.sessions(
            instrument_id, as_of=as_of, start=self.options.start, end=self.options.end
        )
        if not sessions:
            outcome.skipped_reason = "no bars in the requested window"
            return outcome
        outcome.first_session, outcome.last_session = sessions[0], sessions[-1]

        mode, reason = self.feed.mode_for(instrument_id, as_of)
        outcome.feed_mode, outcome.feed_reason = mode, reason

        # A long discontinuity is not a gap in one history; it is the end of
        # one and the start of another. Each episode gets its own scanner,
        # tracker and monitor, and its own bar floor, so no indicator, pivot,
        # pattern identity or breakout boundary reaches back across the break.
        # See `tradeit.scanning.episodes` for why a reset rather than a bridge.
        episodes = segment_sessions(sessions)
        outcome.episodes = tuple(episodes)
        if len(episodes) > 1:
            self.on_progress(
                f"    {ticker or instrument_id}: {len(episodes)} analytical episodes; "
                "structural break(s) of "
                + ", ".join(str(e.break_sessions) for e in episodes[1:])
                + " sessions"
            )

        evaluated = 0
        for episode in episodes:
            # One of each per episode. A fresh scanner per *session* would
            # re-mint every pattern every day; one shared across a break would
            # carry a dead company's structures into a live listing.
            scanner = PatternScanner(config=self.pattern_config, tracker=PatternTracker())
            monitor = BreakoutMonitor(self.breakout_config, profile=self.options.breakout_profile)
            for view in self.feed.walk(
                instrument_id,
                episode.sessions,
                mode=mode,
                as_of=as_of,
                floor=episode.start,
            ):
                result = scanner.scan_incremental(
                    instrument_id, self.options.timeframe, view.bars, view.session_date
                )
                evaluated += 1
                outcome.sessions_scanned += 1
                outcome.bars_read += len(view.bars)
                for instance in result.instances:
                    outcome.detections[instance.detector_name] += 1
                for skipped in result.skipped:
                    outcome.skipped_detectors[skipped.name] += 1

                monitor.observe(
                    instrument_id,
                    self.options.timeframe,
                    scanner.open_patterns(),
                    view.bars,
                    view.session_date,
                    knowledge_time=view.knowledge_time,
                )
                if self.options.progress_every and evaluated % self.options.progress_every == 0:
                    self.on_progress(
                        f"    {ticker or instrument_id}: {evaluated}/{len(sessions)} sessions"
                    )
            self._persist(scanner, monitor, outcome)

        outcome.elapsed = time.perf_counter() - started
        return outcome

    def _persist(
        self, scanner: PatternScanner, monitor: BreakoutMonitor, outcome: InstrumentScan
    ) -> None:
        """Write one instrument's patterns and events, then link them.

        Patterns first, so a breakout event can carry the database id of the
        pattern whose boundary it was watching. A link that has to be repaired
        afterwards is a link that will be missing for the rows written before
        somebody noticed.
        """
        patterns = PatternRepository(self.session)
        pattern_ids: dict[str, int] = {}
        for tracked in [*scanner.open_patterns(), *scanner.closed_patterns()]:
            row = patterns.save(tracked)
            pattern_ids[tracked.identity_key] = row.id
            outcome.patterns_persisted += 1
            outcome.pattern_states[str(tracked.current.state)] += 1

        breakouts = BreakoutRepository(self.session)
        for event in monitor.events.values():
            if not event.observations:
                # An event the monitor opened and never evaluated has no
                # history. The repository refuses it, correctly; skipping it
                # here keeps that refusal from aborting a whole instrument.
                continue
            breakouts.save(event, pattern_id=pattern_ids.get(event.boundary.pattern_key))
            outcome.breakouts_persisted += 1
            outcome.breakout_states[str(event.state)] += 1


def _end_of_day(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=dt.UTC)


def summarise_events(events: Sequence[BreakoutEvent]) -> dict[str, int]:
    """State tally for a sequence of events. Used by the report and by tests."""
    return dict(Counter(str(event.state) for event in events))
