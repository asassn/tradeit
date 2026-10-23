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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.acquisition.base import AcquisitionDataset, FetchOutcome, FetchRequest
from tradeit.acquisition.normalize import NormalizedRows
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.data.packages.spec import DatasetKind
from tradeit.storage.tables import ForwardObservation, OhlcvBar

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

    def normalize(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows: ...


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
    bars_written: int = 0
    #: Symbols that returned rows but whose id is unknown, so their prices were
    #: not written. **Counted, never silently dropped**: a symbol whose
    #: observation exists while its prices do not is a hole that reads like a
    #: halt.
    unmapped: tuple[str, ...] = ()
    #: What the normaliser reported about rows it could not read. Carried out
    #: rather than logged, because "the vendor sent a row we could not parse"
    #: is evidence about the vendor.
    findings: tuple[str, ...] = ()
    by_status: dict[str, int] = field(default_factory=dict)
    #: Whether no symbol printed for ``session_date``, so no observation was
    #: owed. **Distinct from recording nothing by accident**, which is what
    #: this flag exists to stop the report calling it: declining to write 195
    #: silences about a session the vendor has not published is the capture
    #: working, and a report that labels it "a hole in the record" trains the
    #: operator to ignore the warning that matters.
    session_unobserved: bool = False

    @property
    def complete(self) -> bool:
        """Whether every observation that was owed was recorded.

        **Not whether every request returned data.** A silence is a successful
        observation; an unrecorded request is a hole in the record.

        When the session itself was never observed -- no symbol printed -- no
        observation was owed, so a capture that recorded none is complete. The
        first live run after this distinction existed reported *"149 requests
        produced no observation at all, which is a hole in the record"* about
        the one outcome that was correct.
        """
        if self.session_unobserved:
            return self.recorded == 0
        return self.recorded == self.requested

    def describe(self) -> str:
        lines = [
            f"{self.session_date}: {self.requested} requested, "
            f"{self.recorded} recorded, {self.silent} silent",
        ]
        lines.append(f"  {self.bars_written} bars written")
        for status, count in sorted(self.by_status.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {status:<12} {count:>5}")
        if self.unmapped:
            lines.append(
                f"  {len(self.unmapped)} symbol(s) returned rows with no known id, so "
                f"their prices were not written: {', '.join(sorted(self.unmapped)[:8])}"
            )
        for finding in self.findings[:5]:
            lines.append(f"  finding: {finding}")
        if self.session_unobserved:
            lines.append(
                f"  no observation is owed for {self.session_date}: nothing printed, so "
                "there is nothing to be silent about. Bars for earlier sessions in the "
                "window were written."
            )
        if not self.complete:
            lines.append(
                f"  {self.requested - self.recorded} request(s) produced no observation "
                "at all, which is a hole in the record rather than a quiet market"
            )
        return "\n".join(lines)


def _row_session(row: Mapping[str, object]) -> dt.date | None:
    """A normalised row's session date, or ``None`` if it has not got one.

    Returns ``None`` rather than raising: a row the adapter could not date is a
    row that cannot count towards "did this session print", and the normaliser
    has already reported it as a finding. Raising here would turn one unreadable
    row into a failed capture, and a day not captured cannot be caught up.
    """
    value = row.get("session_date")
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def capture_forward(
    session: Session,
    source: CaptureSource,
    symbols: Sequence[str],
    *,
    start: dt.date,
    end: dt.date,
    asked_at: dt.datetime,
    instrument_ids: Mapping[str, int] | None = None,
    journal: object | None = None,
    provider: str,
) -> CaptureReport:
    """Ask about every symbol and record every answer, including silence.

    ``provider`` is **required and has no default.** It used to default to
    ``"tiingo"`` and the only caller never passed it, so when a second vendor
    was added every EODHD bar was written with ``source = "tiingo"`` -- 60 of
    them, before anyone looked. That defeats the entire reason the two vendors
    are kept apart: Tiingo declares ``RAW_UNADJUSTED`` and EODHD declares
    ``UNKNOWN``, and a mislabelled bar makes a series whose adjustment basis is
    unknowable rather than merely unknown. Worse, the test asserting *"no
    instrument carries bars from two vendors"* passed -- because every label
    was the same wrong one. A default on a provenance field is a lie waiting
    for a second caller.

    ``asked_at`` is **required and passed in**, never read from the clock here.
    A capture replayed over history, or one crossing midnight UTC mid-run, must
    stamp every row with one consistent instant — and a function that read the
    clock per row could not be tested for the idempotency its unique
    constraint provides.

    ``instrument_ids`` maps each symbol to the id its bars are stored under.
    **Without it the observations are still recorded and no prices are
    written**, which is a deliberate split rather than a half-failure: knowing
    *that* a symbol printed is the survivorship record and is worth keeping even
    when the prices have nowhere to go. A symbol that returned rows and has no
    id is counted in :attr:`CaptureReport.unmapped`, never dropped quietly.

    Raises :class:`NoCredential` rather than recording an empty run. An empty
    forward record is indistinguishable from a quiet market, and that is the one
    thing it must never be mistaken for.
    """
    ids = dict(instrument_ids or {})
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
    bars_written = 0
    unmapped: list[str] = []
    findings: list[str] = []

    # **Fetched before anything is recorded, because one question has to be
    # answered first: did this session happen at all?**
    #
    # `rows_returned` used to be `len(outcome.rows)` -- the row count for the
    # whole `start..end` window. Measured on the live record 2026-10-23: every
    # covered symbol read 5 or 6 against a 7-day window, so "session
    # 2026-10-21, rows_returned=5" never meant "this session printed".
    #
    # That defeated the one thing this record exists for. **A security that
    # stops trading today still returns the previous six sessions**, so it was
    # recorded `ok` rather than silent for six more days, and the silence that
    # constitutes the exit only appeared once the entire window emptied --
    # dated to that later run's `end` rather than to the session the security
    # actually stopped. It affected every exit, not an unlucky few. `ABAX` and
    # `AUTO` were caught only because they had no data anywhere in the window.
    #
    # So the count is now rows dated `end`, and the window keeps doing the job
    # it was designed for: recovering sessions a sleeping laptop missed, whose
    # bars are written per row date regardless.
    fetched: list[tuple[FetchRequest, FetchOutcome, NormalizedRows, int]] = []
    for request in requests:
        outcome = source.fetch(request)
        statuses[str(outcome.status)] += 1
        if journal is not None:
            journal.record(provider, outcome)  # type: ignore[attr-defined]
        # Normalised via the adapter so the session date is canonical rather
        # than read out of a vendor-specific raw field. `normalize` returns
        # nothing for a symbol with no id, so an unmapped symbol is counted
        # against a placeholder that is **never persisted** -- its rows are
        # re-normalised with the real id below before any bar is written.
        probe = source.normalize(outcome, {request.symbol: ids.get(request.symbol, 0)})
        on_session = sum(
            1 for row in probe.rows.get(DatasetKind.DAILY_BARS, []) if _row_session(row) == end
        )
        fetched.append((request, outcome, probe, on_session))

    # **If no symbol printed, the session is not evidence about any symbol.**
    # Either it has not been published yet, or the calendar is wrong about it
    # being a session at all. Writing a silence per symbol would be one false
    # exit for every name in the universe -- 195 of them -- which is what the
    # two dead tickers were a two-row preview of.
    #
    # **Only *successful* zero-row observations are suppressed.** `EMPTY` is a
    # success -- the vendor answered and the answer was empty -- and that is
    # the row that reads as "this security did not trade". A `REJECTED`,
    # `RATE_LIMITED` or `MALFORMED` outcome is recorded regardless, because its
    # status already says it is a fact about the vendor and no reader could
    # mistake it for an exit. Suppressing those would lose the distinction
    # between a refusal and a silence, which the whole design turns on.
    #
    # **No universe-size threshold**, deliberately: with no cross-sectional
    # evidence that the session happened, a silence is uninterpretable whether
    # the universe holds one name or a thousand. Bars for earlier sessions in
    # the window are written either way -- they are real data about sessions
    # that did happen.
    session_unobserved = bool(requests) and not any(count for _, _, _, count in fetched)
    if session_unobserved:
        findings.append(
            f"no symbol printed for {end}, so no silence is recorded against it: a "
            "session nothing traded in is a fact about the vendor or the calendar "
            "rather than about any security"
        )

    for request, outcome, _probe, rows in fetched:
        if session_unobserved and outcome.status.is_success:
            # Bars from earlier sessions in the window still belong in the
            # record; only the observation is withheld.
            instrument_id = ids.get(request.symbol)
            if instrument_id is not None and outcome.rows:
                normalized = source.normalize(outcome, {request.symbol: instrument_id})
                findings.extend(normalized.findings)
                bars_written += _write_bars(
                    session, normalized, instrument_id, asked_at=asked_at, provider=provider
                )
            continue
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
            # observation a delisting is made of: the symbol did not print on
            # *this* session, on a day when other symbols did.
            silent += 1
            # No `continue`. A symbol silent on `end` may still have printed
            # earlier in the window -- that is the ordinary shape of an exit
            # mid-window -- and those bars are real and must be written.
            # Returning early here is what would turn the fix into a different
            # kind of data loss.

        instrument_id = ids.get(request.symbol)
        if instrument_id is None:
            if outcome.rows:
                unmapped.append(request.symbol)
            continue
        if not outcome.rows:
            continue
        normalized = source.normalize(outcome, {request.symbol: instrument_id})
        findings.extend(normalized.findings)
        bars_written += _write_bars(
            session, normalized, instrument_id, asked_at=asked_at, provider=provider
        )

    return CaptureReport(
        session_date=end,
        asked_at=asked_at,
        requested=len(requests),
        recorded=recorded,
        silent=silent,
        bars_written=bars_written,
        unmapped=tuple(unmapped),
        findings=tuple(findings),
        by_status=dict(statuses),
        session_unobserved=session_unobserved,
    )


def _write_bars(
    session: Session,
    normalized: NormalizedRows,
    instrument_id: int,
    *,
    asked_at: dt.datetime,
    provider: str,
) -> int:
    """Persist the bars a response normalised to, skipping ones already held.

    ``knowledge_time`` is the instant we **asked**, not the session's close.
    They are different facts and the distinction is the whole bitemporal point:
    a price learned three days late is still a price for its session, and a
    strategy may only use what was knowable at the time.

    ``knowledge_source`` is ``VENDOR_INGEST`` rather than ``REPORTED``, because
    an exchange reported the print and a vendor relayed it; recording the relay
    as the report would overstate what we know about the chain.

    Bars already held for the same session are **left alone rather than
    updated**. A later ask that disagrees should add a revision, not overwrite
    one -- but writing that revision needs a decision about which vendor wins,
    and this does not invent one. The disagreement is visible in the
    observation rows either way.
    """
    rows = normalized.rows.get(DatasetKind.DAILY_BARS, [])
    written = 0
    for row in rows:
        session_date = dt.date.fromisoformat(row["session_date"])
        existing = session.execute(
            select(OhlcvBar.id).where(
                OhlcvBar.instrument_id == instrument_id,
                OhlcvBar.session_date == session_date,
                OhlcvBar.timeframe == str(Bartimeframe.D1),
            )
        ).first()
        if existing is not None:
            continue
        session.add(
            OhlcvBar(
                instrument_id=instrument_id,
                timeframe=str(Bartimeframe.D1),
                session_date=session_date,
                event_time=dt.datetime.combine(session_date, dt.time(20, 0), tzinfo=dt.UTC),
                knowledge_time=asked_at,
                knowledge_source=str(KnowledgeTimeSource.VENDOR_INGEST),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                # NOT NULL in the schema, and rightly so: a bar that cannot
                # name where it came from cannot be re-checked against the
                # vendor that sent it. The first version omitted it and the
                # constraint refused the insert.
                source=provider,
            )
        )
        written += 1
    return written
