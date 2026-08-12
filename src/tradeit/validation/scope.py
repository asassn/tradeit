"""Which instruments a Phase 4/5 rate is actually computed over.

Three populations get conflated the moment anyone writes ``SELECT count(*)``,
and the diagnostic scan made the cost obvious: ``diag-01`` covered seven
instruments inside a seventy-eight instrument snapshot, and
``phase4.concentration`` duly reported seventy-one instruments as having
produced no detections. Every one of those seventy-one was simply never
scanned. The number was true of the database and false about the detectors,
which is the most expensive kind of wrong a validation report can be.

So the three are named and kept apart:

``snapshot``
    Instruments the snapshot holds bars for. The ceiling.
``requested``
    Instruments the scan set out to cover — after ticker/id restriction and the
    capability filter. Read from ``scan_runs.requested_instrument_ids``.
``completed``
    Instruments whose patterns and breakouts are actually committed, from the
    ``scan_progress`` ledger. **This is the denominator for every empirical
    rate**, because it is the only one of the three that describes the data a
    check is looking at.

A requested instrument that never completed is not a silent detector; it is an
interrupted scan, and the difference is reported rather than averaged away.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe
from tradeit.storage import tables as tbl

__all__ = ["ScanScope", "resolve_scope"]


@dataclass(frozen=True, slots=True)
class ScanScope:
    """The three populations, and the spans that go with the completed one."""

    snapshot: frozenset[int]
    requested: frozenset[int]
    completed: frozenset[int]
    #: scan_id -> status, for every run against this snapshot.
    runs: tuple[tuple[str, str], ...] = ()
    #: instrument_id -> (first_session, last_session) actually scanned.
    spans: dict[int, tuple[dt.date, dt.date]] = field(default_factory=dict)

    @property
    def is_known(self) -> bool:
        """Whether a scan ledger was found at all.

        False means no ``scan_runs`` row covers this snapshot — patterns may
        still be present from a build that predates the ledger, and a check
        should say so rather than quietly treating the snapshot as the scope.
        """
        return bool(self.runs)

    @property
    def requested_but_incomplete(self) -> frozenset[int]:
        return self.requested - self.completed

    @property
    def unscanned(self) -> frozenset[int]:
        """In the snapshot, never requested. Not evidence about anything."""
        return self.snapshot - self.requested - self.completed

    def instrument_years(self) -> float:
        """Scanned instrument-years, summed per instrument over its own span.

        Not ``(latest - earliest) * count``. That form silently credits an
        instrument listed in 2020 with a decade of history because something
        else in the universe had one, which inflates the denominator of every
        per-year rate — and inflates it most for exactly the young, volatile
        names a detection-rate check is most interested in.
        """
        return sum(
            (last - first).days / 365.25 for first, last in self.spans.values() if last > first
        )

    def describe(self) -> dict[str, object]:
        return {
            "snapshot_instruments": len(self.snapshot),
            "requested_instruments": len(self.requested),
            "completed_instruments": len(self.completed),
            "requested_but_incomplete": len(self.requested_but_incomplete),
            "never_requested": len(self.unscanned),
            "scan_runs": [f"{scan_id} ({status})" for scan_id, status in self.runs],
            "scanned_instrument_years": round(self.instrument_years(), 2),
        }


def resolve_scope(
    session: Session,
    snapshot_id: str,
    *,
    timeframe: Bartimeframe = Bartimeframe.D1,
) -> ScanScope:
    """Read the three populations for one snapshot.

    Falls back to "the snapshot is the scope" only when no scan ledger exists,
    and :attr:`ScanScope.is_known` reports which of the two happened so a check
    never presents a fallback as a measurement.
    """
    snapshot = frozenset(
        int(value)
        for value in session.scalars(
            select(tbl.OhlcvBar.instrument_id)
            .where(tbl.OhlcvBar.timeframe == timeframe.value)
            .distinct()
        )
    )

    runs = session.execute(
        select(
            tbl.ScanRun.id,
            tbl.ScanRun.scan_id,
            tbl.ScanRun.status,
            tbl.ScanRun.requested_instrument_ids,
        )
        .where(tbl.ScanRun.snapshot_id == snapshot_id)
        .order_by(tbl.ScanRun.started_at)
    ).all()
    if not runs:
        return ScanScope(snapshot=snapshot, requested=snapshot, completed=snapshot)

    requested: set[int] = set()
    for _run_id, _scan_id, _status, ids in runs:
        # NULL means "the whole snapshot" — the column was added after the
        # ledger, so an older row cannot distinguish that from "unrecorded".
        requested |= snapshot if ids is None else {int(value) for value in ids}

    run_ids = [run_id for run_id, _, _, _ in runs]
    progress = session.execute(
        select(
            tbl.ScanProgress.instrument_id,
            func.min(tbl.ScanProgress.first_session),
            func.max(tbl.ScanProgress.last_session),
        )
        .where(tbl.ScanProgress.scan_run_id.in_(run_ids))
        .group_by(tbl.ScanProgress.instrument_id)
    ).all()

    spans: dict[int, tuple[dt.date, dt.date]] = {}
    completed: set[int] = set()
    for instrument_id, first, last in progress:
        completed.add(int(instrument_id))
        if first is not None and last is not None:
            spans[int(instrument_id)] = (first, last)

    return ScanScope(
        snapshot=snapshot,
        requested=frozenset(requested),
        completed=frozenset(completed),
        runs=tuple((scan_id, status) for _, scan_id, status, _ in runs),
        spans=spans,
    )
