"""Survivorship controls: what each one needs, and whether it is actually there.

A survivorship control is a security that *stopped existing*. Its job is to be
present in the snapshot so that any rate computed over that snapshot is not a
rate over survivors. Two design errors were found in how this was checked, and
both are corrected here.

**One universal request window is the wrong model.** Asking every control for
2010→present cannot return Enron, which stopped trading in 2004. The absence
then arrives at the gate looking like a vendor coverage gap. So each control
carries the interval in which it *economically existed*, and the requirement is
that the acquisition covers **that** interval — not that every control appears
in one window chosen for the survivors. :func:`required_history_start` computes
the earliest date a request must reach to exercise every configured control,
which is a number an operator can act on before spending a credit.

**Presence in a symbol table is not coverage.** A control is covered when the
snapshot holds *usable historical bars* over its active interval: enough
sessions to warm an indicator up, and a series that actually runs to the end of
the security's life. A ticker row with forty bars proves nothing about what a
screen would have seen in 2008, and counting it as coverage is how a
survivorship guarantee becomes a formality.

**A classification is not a pass.** Every status below except ``COVERED`` leaves
the check FAILing. Knowing *why* a control is missing changes who fixes it and
how; it never changes whether the snapshot is survivorship-safe.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tradeit.data.validation_universe import ValidationInstrument, ValidationUniverse

__all__ = [
    "MIN_CONTROL_SESSIONS",
    "TAIL_TOLERANCE_DAYS",
    "TICKER_REUSE_TOLERANCE_DAYS",
    "AcquisitionOutcomeView",
    "AcquisitionRecordView",
    "ControlCoverage",
    "ObservedSeries",
    "RosterSummary",
    "SurvivorshipStatus",
    "classify_roster",
    "control_required_start",
    "render_roster",
    "required_history_start",
]

#: Sessions of history a delisted control needs before it can exercise anything.
#: One trading year: long enough to warm up every Phase 3 indicator (the longest
#: declared warm-up is 200 sessions) and to contain the pre-collapse regime the
#: control exists to make visible. A control with fewer bars is *present* and not
#: *usable*, and the two are reported differently.
MIN_CONTROL_SESSIONS = 252

#: How far the last observed bar may fall short of the recorded last trade date
#: before the series is judged not to reach the end of the security's life.
#: Generous, because ``last_trade_date`` in the universe is a research figure and
#: a vendor's final print may legitimately be a few sessions earlier — a halt
#: before the delisting, or a final day with no trade.
TAIL_TOLERANCE_DAYS = 21

#: How far past a control's last trade date its series may run before the
#: symbol is judged to have been reused. A quarter: long enough to absorb a
#: research date that is a few weeks off or a final week of pink-sheet prints,
#: far short of the years that separate a delisting from a new listing under
#: the same string.
#:
#: This bound exists because the first real snapshot silently passed one
#: control on a *different company's* prices. BBBY was recorded as COVERED with
#: 3,638 bars from 2010 to 2026 — Bed Bath & Beyond stopped trading in May
#: 2023, and the bars after a 536-session hole belong to whatever took the
#: ticker. Coverage measured only from "enough bars, recent enough" cannot see
#: that, and the failure direction is the flattering one.
TICKER_REUSE_TOLERANCE_DAYS = 92


class SurvivorshipStatus(StrEnum):
    """What became of one control. Only ``COVERED`` is a pass."""

    #: Usable historical bars over the control's active interval.
    COVERED = "COVERED"
    #: Bars exist but do not amount to a usable history — too few sessions, or a
    #: series that stops long before the security did.
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    #: The acquisition asked for a window that excludes this security's life.
    #: **Ours to fix**, and fixable with one argument.
    REQUEST_WINDOW_EXCLUDED = "REQUEST_WINDOW_EXCLUDED"
    #: The vendor was asked under a symbol it does not use for this security,
    #: and the alternatives the universe records were never tried. **Ours.**
    WRONG_ALIAS = "WRONG_ALIAS"
    #: The vendor does not recognise the symbol.
    NOT_FOUND = "NOT_FOUND"
    #: The vendor recognises it and holds no history over the window. This is
    #: the one that genuinely means "no delisted coverage".
    PROVIDER_HISTORY_UNAVAILABLE = "PROVIDER_HISTORY_UNAVAILABLE"
    #: Entitlement, not absence.
    PLAN_RESTRICTED = "PLAN_RESTRICTED"
    #: The vendor needs an exchange or MIC to disambiguate.
    AMBIGUOUS = "AMBIGUOUS"
    #: Transport failure. Evidence of nothing.
    PROVIDER_ERROR = "PROVIDER_ERROR"
    #: Absent, and nothing was recorded about why.
    UNRESOLVED = "UNRESOLVED"

    @property
    def is_covered(self) -> bool:
        return self is SurvivorshipStatus.COVERED

    @property
    def is_our_defect(self) -> bool:
        """Whether the fix is on this side of the vendor boundary.

        Separate from the verdict on purpose: it changes who does the work, not
        whether the snapshot is survivorship-safe.
        """
        return self in (
            SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED,
            SurvivorshipStatus.WRONG_ALIAS,
            SurvivorshipStatus.AMBIGUOUS,
        )

    @property
    def is_evidence_of_absence(self) -> bool:
        """Whether this outcome supports "the vendor does not have it".

        ``PLAN_RESTRICTED`` and ``PROVIDER_ERROR`` are explicitly not: an
        entitlement refusal and a timeout say nothing about what a vendor holds,
        and retiring a control on the strength of a billing decision is the
        failure this module exists to prevent.
        """
        return self in (
            SurvivorshipStatus.NOT_FOUND,
            SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE,
        )

    @property
    def remedy(self) -> str:
        return _REMEDIES[self]


_REMEDIES: dict[SurvivorshipStatus, str] = {
    SurvivorshipStatus.COVERED: "none",
    SurvivorshipStatus.INSUFFICIENT_HISTORY: (
        "re-acquire this symbol over its full active interval; the bars present do "
        "not amount to a usable history"
    ),
    SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED: (
        "re-acquire with a start date at or before this control's required history start"
    ),
    SurvivorshipStatus.WRONG_ALIAS: (
        "request the recorded alias candidates and keep whichever the vendor resolves"
    ),
    SurvivorshipStatus.NOT_FOUND: (
        "confirm the symbol against the vendor's own reference list, then source this "
        "history elsewhere if it is genuinely absent"
    ),
    SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE: (
        "source this history from a provider with delisted coverage"
    ),
    SurvivorshipStatus.PLAN_RESTRICTED: (
        "an entitlement answer, not an absence; the data exists on another plan"
    ),
    SurvivorshipStatus.AMBIGUOUS: "re-request with an explicit exchange or MIC code",
    SurvivorshipStatus.PROVIDER_ERROR: "retry; this outcome is not evidence of anything",
    SurvivorshipStatus.UNRESOLVED: (
        "re-acquire with a build that records per-symbol acquisition outcomes"
    ),
}

#: ``SymbolStatus`` values, as strings so a package written by a different build
#: still classifies and an unrecognised value falls through to UNRESOLVED.
_SYMBOL_STATUS_MAP: dict[str, SurvivorshipStatus] = {
    "not_found": SurvivorshipStatus.NOT_FOUND,
    "unavailable_historically": SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE,
    "plan_restricted": SurvivorshipStatus.PLAN_RESTRICTED,
    "ambiguous": SurvivorshipStatus.AMBIGUOUS,
}

#: ``FetchStatus`` values, consulted only when ``symbol_status`` said nothing.
_FETCH_STATUS_MAP: dict[str, SurvivorshipStatus] = {
    "empty": SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE,
    "rejected": SurvivorshipStatus.NOT_FOUND,
    "unreachable": SurvivorshipStatus.PROVIDER_ERROR,
    "rate_limited": SurvivorshipStatus.PROVIDER_ERROR,
    "quota_exhausted": SurvivorshipStatus.PROVIDER_ERROR,
    "malformed": SurvivorshipStatus.PROVIDER_ERROR,
}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcquisitionOutcomeView:
    """One requested symbol's result, as read back from a snapshot."""

    ticker: str
    symbol_status: str = ""
    fetch_status: str = ""
    bars: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class AcquisitionRecordView:
    """What a snapshot records about the run that produced it.

    ``is_empty`` is the distinction that matters: a package written before
    outcomes were recorded says nothing, and "nothing was recorded" must never
    be read as "nothing failed".
    """

    provider: str = ""
    requested_start: dt.date | None = None
    requested_end: dt.date | None = None
    outcomes: tuple[AcquisitionOutcomeView, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.outcomes

    def by_ticker(self) -> dict[str, AcquisitionOutcomeView]:
        return {item.ticker: item for item in self.outcomes}

    @classmethod
    def from_payload(cls, raw: object) -> AcquisitionRecordView:
        if not isinstance(raw, dict):
            return cls()
        entries = raw.get("outcomes")
        outcomes: list[AcquisitionOutcomeView] = []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ticker = str(entry.get("ticker") or "")
                if not ticker:
                    continue
                outcomes.append(
                    AcquisitionOutcomeView(
                        ticker=ticker,
                        symbol_status=str(entry.get("symbol_status") or ""),
                        fetch_status=str(entry.get("fetch_status") or ""),
                        bars=int(entry.get("bars") or 0),
                        error=str(entry.get("error") or ""),
                    )
                )
        return cls(
            provider=str(raw.get("provider") or ""),
            requested_start=_as_date(raw.get("requested_start")),
            requested_end=_as_date(raw.get("requested_end")),
            outcomes=tuple(outcomes),
        )


@dataclass(frozen=True, slots=True)
class ObservedSeries:
    """What the snapshot actually holds for one ticker.

    The evidence that separates *present* from *covered*. Supplied by the caller
    from the database rather than derived here, so this module stays testable
    without one.
    """

    ticker: str
    first_session: dt.date
    last_session: dt.date
    bars: int


# ---------------------------------------------------------------------------
# Per-control result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlCoverage:
    """One survivorship control, its requirement, and what was delivered."""

    ticker: str
    alias_candidates: tuple[str, ...]
    #: The interval in which the security economically existed. ``active_start``
    #: is the universe's ``first_trade_date`` when recorded, otherwise the
    #: required-history start derived from ``active_end``.
    active_start: dt.date | None
    active_end: dt.date | None
    #: The earliest date a request must reach for this control to be usable.
    required_start: dt.date | None
    requested_start: dt.date | None
    requested_end: dt.date | None
    #: What the vendor said.
    acquisition_outcome: str
    #: Did the provider hand over any bars at all for this symbol?
    provider_supplied_bars: bool
    observed: ObservedSeries | None
    status: SurvivorshipStatus
    detail: str = ""

    @property
    def is_covered(self) -> bool:
        return self.status.is_covered

    def to_payload(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "alias_candidates": list(self.alias_candidates),
            "active_start": _iso(self.active_start),
            "active_end": _iso(self.active_end),
            "required_history_start": _iso(self.required_start),
            "requested_start": _iso(self.requested_start),
            "requested_end": _iso(self.requested_end),
            "acquisition_outcome": self.acquisition_outcome,
            "provider_supplied_bars": self.provider_supplied_bars,
            "observed_first_session": _iso(self.observed.first_session) if self.observed else None,
            "observed_last_session": _iso(self.observed.last_session) if self.observed else None,
            "observed_bars": self.observed.bars if self.observed else 0,
            "covered": self.is_covered,
            "status": str(self.status),
            "detail": self.detail,
            "remedy": self.status.remedy,
        }


@dataclass(slots=True)
class RosterSummary:
    """Every configured control, plus what the operator should do next."""

    entries: tuple[ControlCoverage, ...] = ()
    requested_start: dt.date | None = None
    requested_end: dt.date | None = None
    #: Earliest date a single acquisition must reach to exercise every control.
    required_history_start: dt.date | None = None
    record_is_empty: bool = True
    observations_available: bool = False

    @property
    def covered(self) -> tuple[ControlCoverage, ...]:
        return tuple(e for e in self.entries if e.is_covered)

    @property
    def uncovered(self) -> tuple[ControlCoverage, ...]:
        return tuple(e for e in self.entries if not e.is_covered)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(str(e.status) for e in self.entries))

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_start": _iso(self.requested_start),
            "requested_end": _iso(self.requested_end),
            "required_history_start": _iso(self.required_history_start),
            "acquisition_outcomes_recorded": not self.record_is_empty,
            "bar_observations_available": self.observations_available,
            "controls_expected": len(self.entries),
            "controls_covered": len(self.covered),
            "counts": self.counts,
            "controls": [entry.to_payload() for entry in self.entries],
        }


# ---------------------------------------------------------------------------
# The preflight number
# ---------------------------------------------------------------------------


def control_required_start(
    instrument: ValidationInstrument, *, sessions: int = MIN_CONTROL_SESSIONS
) -> dt.date | None:
    """Earliest date a request must reach for this control to be *usable*.

    Not the last trade date: a series beginning the week a company collapses
    contains no pre-collapse regime and cannot warm up an indicator, so it
    exercises nothing. ``first_trade_date`` wins when the universe records one;
    otherwise the last trade date is walked back by ``sessions`` trading days,
    approximated at 252 a year. Approximate on purpose — this becomes an
    operator's ``--start`` argument, and being a month early costs nothing while
    being a day late costs the control.
    """
    if instrument.first_trade_date is not None:
        return instrument.first_trade_date
    if instrument.last_trade_date is None:
        return None
    return instrument.last_trade_date - dt.timedelta(days=int(sessions * 365.25 / 252) + 1)


def required_history_start(
    universe: ValidationUniverse, *, sessions: int = MIN_CONTROL_SESSIONS
) -> dt.date | None:
    """The single ``--start`` that exercises every configured control.

    The preflight number. Printed before an acquisition so the operator learns
    the window is too narrow while it still costs one argument to fix, rather
    than afterwards from a survivorship failure that looks like a vendor gap.
    """
    starts = [
        start
        for instrument in universe.delisted
        if (start := control_required_start(instrument, sessions=sessions)) is not None
    ]
    return min(starts) if starts else None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_roster(
    expected: list[ValidationInstrument],
    present: set[str],
    record: AcquisitionRecordView,
    *,
    requested_start: dt.date | None,
    requested_end: dt.date | None = None,
    observed: dict[str, ObservedSeries] | None = None,
    min_sessions: int = MIN_CONTROL_SESSIONS,
) -> RosterSummary:
    """Decide, for every control, whether it is genuinely covered and why not.

    ``observed`` maps ticker to the bars the snapshot actually holds. When it is
    ``None`` the snapshot could not be measured, and a control present in the
    symbol table is reported as ``INSUFFICIENT_HISTORY`` rather than as covered
    — presence is not evidence of usable history, and assuming it is would be
    the weakening this module exists to prevent.
    """
    by_ticker = record.by_ticker()
    entries = tuple(
        _classify_one(
            instrument,
            present=present,
            by_ticker=by_ticker,
            observed=observed,
            requested_start=requested_start,
            requested_end=requested_end,
            outcomes_recorded=not record.is_empty,
            min_sessions=min_sessions,
        )
        for instrument in expected
    )
    starts = [e.required_start for e in entries if e.required_start is not None]
    return RosterSummary(
        entries=entries,
        requested_start=requested_start,
        requested_end=requested_end,
        required_history_start=min(starts) if starts else None,
        record_is_empty=record.is_empty,
        observations_available=observed is not None,
    )


def _classify_one(
    instrument: ValidationInstrument,
    *,
    present: set[str],
    by_ticker: dict[str, AcquisitionOutcomeView],
    observed: dict[str, ObservedSeries] | None,
    requested_start: dt.date | None,
    requested_end: dt.date | None,
    outcomes_recorded: bool,
    min_sessions: int,
) -> ControlCoverage:
    ticker = instrument.ticker
    aliases = instrument.alias_candidates
    active_end = instrument.last_trade_date
    required_start = control_required_start(instrument, sessions=min_sessions)
    outcome = by_ticker.get(ticker)
    series = (observed or {}).get(ticker)

    def build(
        status: SurvivorshipStatus, detail: str, *, supplied: bool | None = None
    ) -> ControlCoverage:
        return ControlCoverage(
            ticker=ticker,
            alias_candidates=aliases,
            active_start=instrument.first_trade_date or required_start,
            active_end=active_end,
            required_start=required_start,
            requested_start=requested_start,
            requested_end=requested_end,
            acquisition_outcome=_describe(outcome),
            provider_supplied_bars=(
                supplied
                if supplied is not None
                else bool(series and series.bars) or bool(outcome and outcome.bars)
            ),
            observed=series,
            status=status,
            detail=detail,
        )

    # 1. Is it actually here, with a usable history? First, because a snapshot
    #    holding the bars settles the question whatever the acquisition record
    #    says about them.
    if ticker in present or series is not None:
        if series is None:
            return build(
                SurvivorshipStatus.INSUFFICIENT_HISTORY,
                "present in the symbol table, but this snapshot's bars were not "
                "measured, so usable history is unproven",
                supplied=True,
            )
        problems = _usability_problems(series, active_end, min_sessions)
        if problems:
            return build(SurvivorshipStatus.INSUFFICIENT_HISTORY, "; ".join(problems))
        return build(
            SurvivorshipStatus.COVERED,
            f"{series.bars:,} bars {series.first_session.isoformat()}.."
            f"{series.last_session.isoformat()}",
        )

    # 2. Could the request have returned it at all? Checked before the vendor's
    #    answer, because when it applies the vendor's answer is about years this
    #    security did not trade in, and classifying by it blames the provider
    #    for our own request.
    if (
        requested_start is not None
        and required_start is not None
        and required_start < requested_start
    ):
        shortfall = (
            f"needs history from {required_start.isoformat()}"
            + (f" (last traded {active_end.isoformat()})" if active_end else "")
            + f", but the request began {requested_start.isoformat()}"
        )
        return build(SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED, shortfall, supplied=False)

    # 3. What did the vendor say?
    if outcome is None:
        if (
            outcomes_recorded
            and aliases
            and not any(alias in by_ticker or alias in present for alias in aliases)
        ):
            return build(
                SurvivorshipStatus.WRONG_ALIAS,
                f"not requested under this symbol or any recorded alias ({', '.join(aliases)})",
                supplied=False,
            )
        return build(
            SurvivorshipStatus.UNRESOLVED,
            "no acquisition outcome recorded for this symbol",
            supplied=False,
        )

    status = _SYMBOL_STATUS_MAP.get(outcome.symbol_status.lower()) or _FETCH_STATUS_MAP.get(
        outcome.fetch_status.lower()
    )
    if status is None:
        return build(SurvivorshipStatus.UNRESOLVED, f"requested; {_describe(outcome)}")
    # The vendor rejecting *this* string is not the vendor lacking the security,
    # when the universe records that it traded under another one and that one
    # was never tried.
    if (
        status is SurvivorshipStatus.NOT_FOUND
        and aliases
        and not any(alias in by_ticker or alias in present for alias in aliases)
    ):
        return build(
            SurvivorshipStatus.WRONG_ALIAS,
            f"{outcome.symbol_status or outcome.fetch_status} for {ticker!r}; "
            f"aliases not attempted ({', '.join(aliases)})",
            supplied=False,
        )
    return build(status, (outcome.error.strip() or _describe(outcome))[:160])


def _usability_problems(
    series: ObservedSeries, active_end: dt.date | None, min_sessions: int
) -> list[str]:
    """Why these bars do not amount to a usable control history."""
    problems: list[str] = []
    if series.bars < min_sessions:
        problems.append(
            f"{series.bars:,} bars, fewer than the {min_sessions:,} a control needs to "
            "warm up an indicator and show its pre-collapse regime"
        )
    if active_end is not None:
        shortfall = (active_end - series.last_session).days
        if shortfall > TAIL_TOLERANCE_DAYS:
            problems.append(
                f"series ends {series.last_session.isoformat()}, {shortfall} days before "
                f"the recorded last trade {active_end.isoformat()}"
            )
        # The mirror case, and the dangerous one. A control that stopped
        # trading in 2023 cannot have prices in 2026: the ticker was reused,
        # and the vendor has spliced a *surviving* company's history onto a
        # failed one under a single symbol string. Counting that as coverage
        # is the exact substitution this module forbids — a delisted control
        # marked present on the strength of a different company's prices —
        # and it happens without anyone deciding to do it.
        overrun = (series.last_session - active_end).days
        if overrun > TICKER_REUSE_TOLERANCE_DAYS:
            problems.append(
                f"series runs to {series.last_session.isoformat()}, {overrun} days *after* "
                f"the recorded last trade {active_end.isoformat()}: the symbol has been "
                "reused and this history is not one security's"
            )
    return problems


def _describe(outcome: AcquisitionOutcomeView | None) -> str:
    if outcome is None:
        return "not requested"
    return (
        f"symbol_status={outcome.symbol_status or 'unrecorded'} "
        f"fetch_status={outcome.fetch_status or 'unrecorded'} bars={outcome.bars}"
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_roster(summary: RosterSummary) -> list[str]:
    """The full roster, one block per control. Never truncated.

    Eleven controls is the whole roster; abbreviating it to five hides exactly
    the names a reader opened the report to find.
    """
    lines = [
        f"  {'ticker':<8} {'active period':<25} {'requested':<25} {'status':<29} covered",
        f"  {'-' * 8} {'-' * 25} {'-' * 25} {'-' * 29} -------",
    ]
    for entry in summary.entries:
        active = f"{_iso(entry.active_start) or '?'} .. {_iso(entry.active_end) or 'open'}"
        asked = f"{_iso(entry.requested_start) or '?'} .. {_iso(entry.requested_end) or '?'}"
        lines.append(
            f"  {entry.ticker:<8} {active:<25} {asked:<25} {entry.status!s:<29} "
            f"{'yes' if entry.is_covered else 'NO'}"
        )
    lines.append("")
    for entry in summary.entries:
        observed = (
            f"{entry.observed.bars:,} bars {_iso(entry.observed.first_session)}.."
            f"{_iso(entry.observed.last_session)}"
            if entry.observed
            else "no bars in this snapshot"
        )
        lines += [
            f"  {entry.ticker}",
            f"    aliases          {', '.join(entry.alias_candidates) or 'none recorded'}",
            f"    needs history    from {_iso(entry.required_start) or 'unknown'}",
            f"    provider said    {entry.acquisition_outcome}",
            f"    supplied bars    {'yes' if entry.provider_supplied_bars else 'no'}",
            f"    snapshot holds   {observed}",
            f"    status           {entry.status}  — {entry.detail}",
        ]
        if not entry.is_covered:
            lines.append(f"    remedy           {entry.status.remedy}")
        lines.append("")

    if summary.required_history_start is not None:
        lines += [
            "  PREFLIGHT: a single acquisition exercises every configured control only",
            f"  if it starts at or before {summary.required_history_start.isoformat()}.",
        ]
        if summary.requested_start and summary.requested_start > summary.required_history_start:
            lines.append(
                f"  This snapshot's request began {summary.requested_start.isoformat()}, "
                "which is too late."
            )
    if summary.record_is_empty:
        lines += [
            "",
            "  This snapshot records no per-symbol acquisition outcomes, so a control",
            "  absent for a reason other than the request window cannot be told apart",
            "  from one never asked for. Re-acquire to populate [acquisition].",
        ]
    if not summary.observations_available:
        lines += [
            "",
            "  Bar counts were not measured, so no control can be confirmed usable.",
        ]
    ours = [e for e in summary.uncovered if e.status.is_our_defect]
    if ours:
        lines += ["", "  Fixable on this side of the vendor boundary:"]
        lines += [f"    {e.ticker:<8} {e.status.remedy}" for e in ours]
    return lines


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None


def _as_date(value: object) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
