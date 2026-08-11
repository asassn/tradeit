"""Why a delisted security is absent — which is six different problems.

``data.survivorship_coverage`` used to report one number: *N of 11 delisted
names are absent*. That number is correct and nearly useless, because the six
situations it covers have six different remedies and only one of them is the
vendor's fault:

* the security stopped trading **before the requested window began**, so no
  request could ever have returned it — our acquisition plan is wrong;
* the security is listed at the vendor under **a different symbol** than the
  one the universe names, typically because a bankruptcy renamed it — our
  identity resolution is wrong;
* the vendor **does not know the symbol**;
* the vendor knows it but **holds no history** over the window;
* the vendor holds it but **the plan does not include it**;
* nothing was recorded, so the honest answer is **unresolved**.

Only the third and fourth are "the provider has no delisted coverage". The
first two are ours, and reporting them as vendor gaps would send the fix to the
wrong place — and, worse, would make "no vendor supplies this" look like a
justification for quietly dropping the control.

**Nothing here can turn a FAIL into a PASS.** Classification changes the
remedy, never the verdict: a survivorship control that is not in the snapshot is
not in the snapshot, and every rate computed over that snapshot is a rate over
survivors. :func:`RosterEntry.is_covered` is true only for names actually
present.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tradeit.data.validation_universe import ValidationInstrument

__all__ = [
    "AcquisitionOutcomeView",
    "AcquisitionRecordView",
    "RosterEntry",
    "RosterSummary",
    "SurvivorshipStatus",
    "classify_roster",
    "render_roster",
]


class SurvivorshipStatus(StrEnum):
    """What happened to one delisted control."""

    PRESENT = "present"
    #: Last trade predates the requested start date. Unobtainable by
    #: construction: the request asked for a period in which the security no
    #: longer existed.
    OUTSIDE_REQUESTED_WINDOW = "outside_requested_window"
    #: Requested under a symbol the vendor does not use for this security, and
    #: the alternative symbols the universe records were never tried.
    SYMBOL_ALIAS_NOT_ATTEMPTED = "symbol_alias_not_attempted"
    #: The vendor does not recognise the symbol.
    NOT_FOUND_AT_PROVIDER = "not_found_at_provider"
    #: The vendor recognises it and holds no history over the window. This is
    #: the one that actually means "no delisted coverage".
    NO_HISTORY_AT_PROVIDER = "no_history_at_provider"
    #: Entitlement, not absence. HTTP 402/403.
    NOT_AVAILABLE_ON_PLAN = "not_available_on_plan"
    #: The vendor needs an exchange or MIC to disambiguate the symbol.
    AMBIGUOUS_AT_PROVIDER = "ambiguous_at_provider"
    #: Network or transport failure. Retryable, so not evidence of anything.
    PROVIDER_ERROR = "provider_error"
    #: Absent, and nothing was recorded about why.
    UNRESOLVED = "unresolved"

    @property
    def is_covered(self) -> bool:
        return self is SurvivorshipStatus.PRESENT

    @property
    def is_our_defect(self) -> bool:
        """Whether the fix is on this side of the vendor boundary.

        Kept separate from the pass/fail verdict on purpose. It changes who
        does the work, not whether the snapshot is survivorship-safe.
        """
        return self in (
            SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW,
            SurvivorshipStatus.SYMBOL_ALIAS_NOT_ATTEMPTED,
        )

    @property
    def is_evidence_of_absence(self) -> bool:
        """Whether this outcome supports "the vendor does not have it".

        ``NOT_AVAILABLE_ON_PLAN`` and ``PROVIDER_ERROR`` are explicitly not:
        an entitlement refusal and a timeout both say nothing about what the
        vendor holds, and treating them as coverage findings would retire a
        control on the strength of a billing decision.
        """
        return self in (
            SurvivorshipStatus.NOT_FOUND_AT_PROVIDER,
            SurvivorshipStatus.NO_HISTORY_AT_PROVIDER,
        )

    @property
    def remedy(self) -> str:
        return _REMEDIES[self]


_REMEDIES: dict[SurvivorshipStatus, str] = {
    SurvivorshipStatus.PRESENT: "none",
    SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW: (
        "re-acquire with a start date at or before this security's last trade date"
    ),
    SurvivorshipStatus.SYMBOL_ALIAS_NOT_ATTEMPTED: (
        "request the recorded alias candidates and keep whichever the vendor resolves"
    ),
    SurvivorshipStatus.NOT_FOUND_AT_PROVIDER: (
        "confirm the symbol against the vendor's own reference list, then source "
        "this history elsewhere if it is genuinely absent"
    ),
    SurvivorshipStatus.NO_HISTORY_AT_PROVIDER: (
        "source this history from a provider with delisted coverage"
    ),
    SurvivorshipStatus.NOT_AVAILABLE_ON_PLAN: (
        "an entitlement answer, not an absence; the data exists on another plan"
    ),
    SurvivorshipStatus.AMBIGUOUS_AT_PROVIDER: ("re-request with an explicit exchange or MIC code"),
    SurvivorshipStatus.PROVIDER_ERROR: "retry; this outcome is not evidence of anything",
    SurvivorshipStatus.UNRESOLVED: (
        "re-acquire with a build that records per-symbol acquisition outcomes"
    ),
}

#: ``SymbolStatus`` values, mapped to what they mean for a missing control.
#: Strings rather than the enum so a package written by a different build still
#: classifies, and an unrecognised value falls through to UNRESOLVED rather than
#: raising.
_SYMBOL_STATUS_MAP: dict[str, SurvivorshipStatus] = {
    "not_found": SurvivorshipStatus.NOT_FOUND_AT_PROVIDER,
    "unavailable_historically": SurvivorshipStatus.NO_HISTORY_AT_PROVIDER,
    "plan_restricted": SurvivorshipStatus.NOT_AVAILABLE_ON_PLAN,
    "ambiguous": SurvivorshipStatus.AMBIGUOUS_AT_PROVIDER,
}

#: ``FetchStatus`` values, consulted only when ``symbol_status`` said nothing.
_FETCH_STATUS_MAP: dict[str, SurvivorshipStatus] = {
    "empty": SurvivorshipStatus.NO_HISTORY_AT_PROVIDER,
    "rejected": SurvivorshipStatus.NOT_FOUND_AT_PROVIDER,
    "unreachable": SurvivorshipStatus.PROVIDER_ERROR,
    "rate_limited": SurvivorshipStatus.PROVIDER_ERROR,
    "quota_exhausted": SurvivorshipStatus.PROVIDER_ERROR,
    "malformed": SurvivorshipStatus.PROVIDER_ERROR,
}


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
class RosterEntry:
    """One expected delisted control and what became of it."""

    ticker: str
    last_trade_date: dt.date | None
    status: SurvivorshipStatus
    detail: str = ""
    alias_candidates: tuple[str, ...] = ()

    @property
    def is_covered(self) -> bool:
        return self.status.is_covered

    def to_payload(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "last_trade_date": (self.last_trade_date.isoformat() if self.last_trade_date else None),
            "status": str(self.status),
            "detail": self.detail,
            "remedy": self.status.remedy,
            "alias_candidates": list(self.alias_candidates),
        }


@dataclass(slots=True)
class RosterSummary:
    entries: tuple[RosterEntry, ...] = ()
    #: Set when the requested window is unknown, so "before the window" could
    #: not be evaluated and those names fall through to UNRESOLVED.
    requested_start: dt.date | None = None
    record_is_empty: bool = True

    @property
    def missing(self) -> tuple[RosterEntry, ...]:
        return tuple(e for e in self.entries if not e.is_covered)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(str(e.status) for e in self.entries))

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_start": (self.requested_start.isoformat() if self.requested_start else None),
            "acquisition_outcomes_recorded": not self.record_is_empty,
            "counts": self.counts,
            "roster": [entry.to_payload() for entry in self.entries],
        }


def classify_roster(
    expected: list[ValidationInstrument],
    present: set[str],
    record: AcquisitionRecordView,
    *,
    requested_start: dt.date | None,
) -> RosterSummary:
    """Classify every expected delisted control against what actually arrived.

    ``requested_start`` is the date the acquisition *asked* from, which is not
    the snapshot's coverage start. A snapshot that begins 2010-01-04 because
    that is the first session in the requested window looks identical to one
    that begins there because the vendor had nothing earlier, and only the first
    explains why a security that stopped trading in 2008 is absent.
    """
    by_ticker = record.by_ticker()
    entries: list[RosterEntry] = [
        _classify_one(
            instrument,
            present,
            by_ticker,
            requested_start=requested_start,
            outcomes_recorded=not record.is_empty,
        )
        for instrument in expected
    ]
    return RosterSummary(
        entries=tuple(entries),
        requested_start=requested_start,
        record_is_empty=record.is_empty,
    )


def _classify_one(
    instrument: ValidationInstrument,
    present: set[str],
    by_ticker: dict[str, AcquisitionOutcomeView],
    *,
    requested_start: dt.date | None,
    outcomes_recorded: bool,
) -> RosterEntry:
    ticker = instrument.ticker
    aliases = instrument.alias_candidates
    if ticker in present:
        return RosterEntry(
            ticker=ticker,
            last_trade_date=instrument.last_trade_date,
            status=SurvivorshipStatus.PRESENT,
            detail="in the snapshot",
            alias_candidates=aliases,
        )

    # Checked before the vendor's answer, because when it applies the vendor's
    # answer is about a period this security did not trade in and classifying
    # by it would blame the provider for our own request.
    last = instrument.last_trade_date
    if requested_start is not None and last is not None and last < requested_start:
        return RosterEntry(
            ticker=ticker,
            last_trade_date=last,
            status=SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW,
            detail=(
                f"last traded {last.isoformat()}, before the requested start "
                f"{requested_start.isoformat()}"
            ),
            alias_candidates=aliases,
        )

    outcome = by_ticker.get(ticker)
    if outcome is None:
        # "Absent from the record" means *not requested* only when there is a
        # record. Without one, a symbol that was requested and refused and one
        # that was never asked for look identical, and calling that "alias not
        # attempted" would invent a diagnosis the evidence does not support.
        if (
            outcomes_recorded
            and aliases
            and not any(alias in by_ticker or alias in present for alias in aliases)
        ):
            return RosterEntry(
                ticker=ticker,
                last_trade_date=last,
                status=SurvivorshipStatus.SYMBOL_ALIAS_NOT_ATTEMPTED,
                detail=(
                    f"not requested under this symbol or any recorded alias ({', '.join(aliases)})"
                ),
                alias_candidates=aliases,
            )
        return RosterEntry(
            ticker=ticker,
            last_trade_date=last,
            status=SurvivorshipStatus.UNRESOLVED,
            detail="no acquisition outcome recorded for this symbol",
            alias_candidates=aliases,
        )

    status = _SYMBOL_STATUS_MAP.get(outcome.symbol_status.lower()) or _FETCH_STATUS_MAP.get(
        outcome.fetch_status.lower()
    )
    if status is None:
        return RosterEntry(
            ticker=ticker,
            last_trade_date=last,
            status=SurvivorshipStatus.UNRESOLVED,
            detail=(
                f"requested; symbol_status={outcome.symbol_status or 'unrecorded'} "
                f"fetch_status={outcome.fetch_status or 'unrecorded'} bars={outcome.bars}"
            ),
            alias_candidates=aliases,
        )
    # The vendor rejecting *this* string is not the vendor lacking the
    # security, when the universe already records that it traded under another
    # one and that one was never tried.
    if (
        status is SurvivorshipStatus.NOT_FOUND_AT_PROVIDER
        and aliases
        and not any(alias in by_ticker or alias in present for alias in aliases)
    ):
        return RosterEntry(
            ticker=ticker,
            last_trade_date=last,
            status=SurvivorshipStatus.SYMBOL_ALIAS_NOT_ATTEMPTED,
            detail=(
                f"{outcome.symbol_status or outcome.fetch_status} for {ticker!r}; "
                f"aliases not attempted ({', '.join(aliases)})"
            ),
            alias_candidates=aliases,
        )
    detail = outcome.error.strip() or (
        f"symbol_status={outcome.symbol_status or 'unrecorded'} "
        f"fetch_status={outcome.fetch_status or 'unrecorded'}"
    )
    return RosterEntry(
        ticker=ticker,
        last_trade_date=last,
        status=status,
        detail=detail[:160],
        alias_candidates=aliases,
    )


def render_roster(summary: RosterSummary) -> list[str]:
    """The full table, one line per control. Never truncated.

    Eleven rows is the whole roster; abbreviating it to five would hide exactly
    the names a reader is looking for.
    """
    lines = [
        f"  {'ticker':<8} {'last trade':<12} {'status':<28} detail",
        f"  {'-' * 8} {'-' * 12} {'-' * 28} {'-' * 28}",
    ]
    for entry in summary.entries:
        last = entry.last_trade_date.isoformat() if entry.last_trade_date else "-"
        lines.append(f"  {entry.ticker:<8} {last:<12} {entry.status!s:<28} {entry.detail}")
    if summary.record_is_empty:
        lines += [
            "",
            "  This snapshot records no per-symbol acquisition outcomes, so names",
            "  absent for a reason other than the requested window cannot be told",
            "  apart. Re-acquire to populate [acquisition] in the manifest.",
        ]
    ours = [e for e in summary.missing if e.status.is_our_defect]
    if ours:
        lines += ["", "  Fixable on this side of the vendor boundary:"]
        lines += [f"    {e.ticker:<8} {e.status.remedy}" for e in ours]
    return lines


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
