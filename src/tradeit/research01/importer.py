"""Landing a vendor price file against curated identity, or refusing to.

**The importer cannot create identity.** It has no write path to ``securities``,
``issuers``, ``issuer_identifiers`` or ``symbol_aliases``, and that is
structural rather than a convention: a ticker with no evidenced security is
reported as unresolved, never minted as a new row. An importer able to invent an
issuer would make ``MANUAL_VERIFIED`` meaningless, because the corpus could then
grow identities nobody read a filing for.

**Splicing is prevented by construction, not by a check.** Every bar resolves
independently, at its own session date, through the ``symbol_aliases`` interval
covering that date. Bars either side of an identity break therefore resolve to
different securities because they were never joined in the first place -- there
is no concatenation step to disable and no tolerance to tune.

That inverts how the output reads, and the inversion is the point:

> **A continuous series across a known identity break is a FAILURE.** It looks
> like complete coverage. ``GM`` is in the EODHD sample for exactly this reason
> -- two unrelated registrants held that ticker across 2009 -- and the vendor was
> not told what was being tested.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.calendar import TradingCalendar
from tradeit.research01.pit import KnowledgeTimeBasis, knowledge_time_for
from tradeit.storage.tables import SecurityPriceFact, SymbolAlias

__all__ = [
    "AliasResolver",
    "Delivery",
    "ImportResult",
    "RejectReason",
    "RejectedBar",
    "Resolution",
    "VendorBar",
    "import_price_bars",
    "resolve_security",
]


class Resolution(StrEnum):
    """The three outcomes of asking which security a ticker meant on a date."""

    RESOLVED = "resolved"
    #: No alias interval covers this ticker on this date. **Not** a new
    #: security, and not the nearest one either.
    UNRESOLVED_NO_ALIAS = "unresolved_no_alias"
    #: Two or more securities claim this ticker at the same instant. On
    #: PostgreSQL ``ex_alias_no_overlap`` makes this unreachable; on SQLite,
    #: where ``EXCLUDE USING gist`` does not exist, this branch is the only
    #: enforcement there is. It is load-bearing, not decorative.
    UNRESOLVED_AMBIGUOUS = "unresolved_ambiguous"


class RejectReason(StrEnum):
    NO_ALIAS = "no_alias"
    #: No issuer carries this registry identifier. Not a new issuer, and
    #: emphatically not the issuer that merely has it as a *related* identity.
    NO_ISSUER = "no_issuer"
    AMBIGUOUS_ALIAS = "ambiguous_alias"
    #: Already present at this exact revision. Re-running an import is a no-op.
    DUPLICATE = "duplicate"
    #: The row is missing the value its own action type requires -- a split
    #: without a ratio, a dividend without an amount. Not defaulted, because a
    #: split silently ratioed 1.0 is a split that does nothing and looks fine.
    INCOMPLETE = "incomplete"
    #: The session date is not a trading session on the exchange calendar.
    #:
    #: Measured 2026-09-07: 3,930 bars in the corpus fall on dates the US market
    #: was closed -- July 4th, Thanksgiving, Good Friday, Juneteenth, and
    #: 2025-01-09, the national day of mourning. Eight to ten dates a year,
    #: every year. The vendor emits them and nothing refused them.
    #:
    #: A bar on a day with no trading is not a price. It is refused rather than
    #: moved, because there is no session to move it to and inventing one would
    #: put a fabricated observation into a corpus whose whole claim is that it
    #: does not fabricate.
    NON_SESSION_DATE = "non_session_date"
    #: The bar is not a bar: its high is below its open or close, its low is
    #: above one of them, or its volume is negative. EODHD returns
    #: ``open=high=low=0`` with a non-zero close for thinly traded delisted
    #: names -- measured on the first real run, where it killed the job through
    #: ``ck_security_price_high`` after 65 symbols.
    #:
    #: **Rejected, never repaired.** Setting the high to the close would invent
    #: a price that nobody printed, and a fabricated bar is worse than a
    #: missing one precisely because it looks usable.
    INCOHERENT_BAR = "incoherent_bar"
    #: The source dates the fact as knowable **before the event it describes**.
    #: A filing submitted in November carrying a value for the quarter ending
    #: 31 December is either a forward declaration -- a dividend declared for a
    #: period not yet closed, which really is knowable then -- or a look-ahead
    #: defect in the source. Nothing in the row distinguishes them, and
    #: inventing a tag taxonomy to guess would be exactly the fabrication this
    #: corpus refuses, so the row is skipped and counted.
    KNOWLEDGE_PRECEDES_EVENT = "knowledge_precedes_event"


@dataclass(frozen=True, slots=True)
class Delivery:
    """One vendor hand-off. ``delivered_at`` is the corpus's own clock."""

    vendor: str
    delivered_at: dt.datetime
    filename: str = ""


@dataclass(frozen=True, slots=True)
class VendorBar:
    """One row as the vendor sent it, before any identity is attached."""

    ticker: str
    session_date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    adjustment_basis: str = "raw"
    volume_adjusted: bool = False
    disseminated_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class RejectedBar:
    """One rejected input, whatever kind of input it was.

    ``payload`` is the original row -- a :class:`VendorBar` or a filing row --
    kept whole so a caller can inspect or retry it. ``subject`` is what could
    not be resolved, rendered by the importer that knows: a ticker for a price
    bar, a namespaced identifier for a filing. Generalised from an earlier
    ``ticker``-only field when filings arrived, rather than adding a second
    result type that would have reported the same three outcomes differently.
    """

    payload: object
    reason: RejectReason
    subject: str = ""
    detail: str = ""


@dataclass(slots=True)
class ImportResult:
    """What landed, what did not, and why -- returned rather than persisted.

    Deliberately not written to ``quarantined_rows``: that table foreign-keys to
    ``ingestion_runs``, which is ``full-01`` machinery, and re-coupling the two
    corpora would undo the separation milestone 2 exists to establish. A
    research-01 reject table is its own scoped proposition.
    """

    landed: int = 0
    rejected: list[RejectedBar] = field(default_factory=list)
    securities_touched: set[int] = field(default_factory=set)

    @property
    def unresolved(self) -> list[RejectedBar]:
        """Everything identity could not place. **Not** duplicates, which are a
        successful no-op rather than a failure to resolve."""
        return [
            r
            for r in self.rejected
            if r.reason
            in {RejectReason.NO_ALIAS, RejectReason.AMBIGUOUS_ALIAS, RejectReason.NO_ISSUER}
        ]

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for r in self.rejected:
            counts[str(r.reason)] = counts.get(str(r.reason), 0) + 1
        return {
            "landed": self.landed,
            "rejected": len(self.rejected),
            "rejected_by_reason": counts,
            "securities_touched": sorted(self.securities_touched),
            "unresolved_subjects": sorted({r.subject for r in self.unresolved}),
        }


def resolve_security(
    session: Session, *, ticker: str, on: dt.date, alias_kind: str = "ticker"
) -> tuple[int | None, Resolution]:
    """Which security held this ticker on this date, according to curated evidence.

    Resolution is **per date**, which is what makes a splice impossible: the
    question is never "which security is this ticker" but "which security was it
    on the day of this bar".

    Where several alias rows cover the date, only those at the **latest**
    ``knowledge_time`` are considered -- the current belief. That is the same
    revision rule the fundamentals read uses, and it means a corrected mapping
    supersedes rather than competes with the one it replaces.
    """
    rows = session.scalars(
        select(SymbolAlias).where(
            SymbolAlias.alias_kind == alias_kind,
            SymbolAlias.alias_value == ticker,
            SymbolAlias.valid_from <= on,
            (SymbolAlias.valid_to.is_(None)) | (SymbolAlias.valid_to > on),
        )
    ).all()
    if not rows:
        return None, Resolution.UNRESOLVED_NO_ALIAS

    latest = max(r.knowledge_time for r in rows)
    current = {r.security_id for r in rows if r.knowledge_time == latest}
    if len(current) > 1:
        return None, Resolution.UNRESOLVED_AMBIGUOUS
    return current.pop(), Resolution.RESOLVED


class AliasResolver:
    """:func:`resolve_security` with the alias rows loaded once per ticker.

    **Why this exists.** ``import_price_bars`` resolves every bar
    independently, which is the property that makes a splice impossible, and it
    costs one query per bar. A live registrant carries roughly nine thousand
    sessions, so importing one symbol issued about eighteen thousand queries and
    the live universe backfill was measured at 128 symbols an hour -- forty
    hours for the run.

    The alias rows for a ticker do not change during an import, so they can be
    read once and the *same per-date rules* applied in memory. **Resolution
    stays per date.** Nothing here widens a window, merges an interval or
    caches an answer across dates; only the fetch is hoisted.

    :func:`resolve_security` remains the reference implementation and is not
    touched. ``test_resolver_agrees_with_resolve_security`` asserts the two
    return identical answers, because two implementations of one rule that can
    disagree are two rules.
    """

    __slots__ = ("_alias_kind", "_cache", "_session")

    def __init__(self, session: Session, *, alias_kind: str = "ticker") -> None:
        self._session = session
        self._alias_kind = alias_kind
        self._cache: dict[str, list[SymbolAlias]] = {}

    def _rows(self, ticker: str) -> list[SymbolAlias]:
        held = self._cache.get(ticker)
        if held is None:
            held = list(
                self._session.scalars(
                    select(SymbolAlias).where(
                        SymbolAlias.alias_kind == self._alias_kind,
                        SymbolAlias.alias_value == ticker,
                    )
                ).all()
            )
            self._cache[ticker] = held
        return held

    def resolve(self, ticker: str, on: dt.date) -> tuple[int | None, Resolution]:
        """The same answer :func:`resolve_security` gives, without the query."""
        rows = [
            r
            for r in self._rows(ticker)
            if r.valid_from <= on and (r.valid_to is None or r.valid_to > on)
        ]
        if not rows:
            return None, Resolution.UNRESOLVED_NO_ALIAS
        latest = max(r.knowledge_time for r in rows)
        current = {r.security_id for r in rows if r.knowledge_time == latest}
        if len(current) > 1:
            return None, Resolution.UNRESOLVED_AMBIGUOUS
        return current.pop(), Resolution.RESOLVED


class _KnownFacts:
    """The keys already stored for a security, read once instead of per bar.

    The duplicate check asks whether one ``(security, session, basis,
    knowledge_time)`` row exists. For a security being imported for the first
    time the answer is no, nine thousand times over, and each no costs a query.

    Loading the security's existing keys once turns that into a set membership
    test. Newly inserted keys are added as they go, so a duplicate *within* one
    delivery is still refused -- which the per-bar query caught by seeing the
    flushed row, and a set that only knew about pre-existing rows would miss.
    """

    __slots__ = ("_known", "_session")

    def __init__(self, session: Session) -> None:
        self._session = session
        self._known: dict[int, set[tuple[dt.date, str, dt.datetime]]] = {}

    def _keys(self, security_id: int) -> set[tuple[dt.date, str, dt.datetime]]:
        held = self._known.get(security_id)
        if held is None:
            held = {
                (session_date, basis, known)
                for session_date, basis, known in self._session.execute(
                    select(
                        SecurityPriceFact.session_date,
                        SecurityPriceFact.adjustment_basis,
                        SecurityPriceFact.knowledge_time,
                    ).where(SecurityPriceFact.security_id == security_id)
                ).all()
            }
            self._known[security_id] = held
        return held

    def seen(self, security_id: int, session_date: dt.date, basis: str, known: dt.datetime) -> bool:
        return (session_date, basis, known) in self._keys(security_id)

    def record(
        self, security_id: int, session_date: dt.date, basis: str, known: dt.datetime
    ) -> None:
        self._keys(security_id).add((session_date, basis, known))


def _incoherent(bar: VendorBar) -> str:
    """Why this row is not a price bar, or empty if it is.

    Mirrors the four check constraints on ``security_price_facts`` exactly. They
    would refuse the row anyway -- as an ``IntegrityError`` that aborts the
    transaction and loses the rest of a paid fetch. This refuses it by name,
    with a count, and lets the run continue.
    """
    if bar.high < bar.low:
        return f"high {bar.high} below low {bar.low}"
    if bar.high < bar.open or bar.high < bar.close:
        return f"high {bar.high} below open {bar.open} or close {bar.close}"
    if bar.low > bar.open or bar.low > bar.close:
        return f"low {bar.low} above open {bar.open} or close {bar.close}"
    if bar.volume < 0:
        return f"negative volume {bar.volume}"
    return ""


def import_price_bars(
    session: Session,
    bars: list[VendorBar],
    delivery: Delivery,
    *,
    alias_kind: str = "ticker",
    calendar: TradingCalendar | None = None,
) -> ImportResult:
    """Land what resolves; report what does not. Never guess, never create.

    ``alias_kind`` defaults to ``"ticker"`` -- evidence-backed identity. Passing
    ``"vendor_symbol"`` resolves against spans derived from a vendor instead,
    which is weaker and must therefore be **asked for at the call site** rather
    than fallen back to. Nothing here silently widens the search when the
    curated answer is absent.

    Idempotent: a bar already present at the same
    ``(security_id, session_date, adjustment_basis, knowledge_time)`` is
    reported as :attr:`RejectReason.DUPLICATE` rather than inserted, so
    re-running a delivery changes nothing.
    """
    result = ImportResult()
    resolver = AliasResolver(session, alias_kind=alias_kind)
    known_facts = _KnownFacts(session)
    sessions = calendar or TradingCalendar()
    for bar in bars:
        if not sessions.is_session(bar.session_date):
            result.rejected.append(
                RejectedBar(
                    bar,
                    RejectReason.NON_SESSION_DATE,
                    bar.ticker,
                    f"{bar.session_date} is not a trading session on "
                    f"{sessions.name}; a day with no trading has no price",
                )
            )
            continue
        security_id, resolution = resolver.resolve(bar.ticker, bar.session_date)
        if resolution is Resolution.UNRESOLVED_NO_ALIAS:
            result.rejected.append(
                RejectedBar(
                    bar,
                    RejectReason.NO_ALIAS,
                    bar.ticker,
                    f"no security evidenced for {bar.ticker!r} on {bar.session_date}",
                )
            )
            continue
        if resolution is Resolution.UNRESOLVED_AMBIGUOUS:
            result.rejected.append(
                RejectedBar(
                    bar,
                    RejectReason.AMBIGUOUS_ALIAS,
                    bar.ticker,
                    f"{bar.ticker!r} claimed by more than one security on {bar.session_date}",
                )
            )
            continue

        assert security_id is not None
        why = _incoherent(bar)
        if why:
            result.rejected.append(RejectedBar(bar, RejectReason.INCOHERENT_BAR, bar.ticker, why))
            continue

        knowledge_time, basis = knowledge_time_for(
            adjustment_basis=bar.adjustment_basis,
            session_date=bar.session_date,
            delivered_at=delivery.delivered_at,
            disseminated_at=bar.disseminated_at,
        )
        # event_time is when the bar happened. For a raw bar on a session that
        # is the close, so knowledge_time == event_time and the
        # ck_security_price_knowledge check holds at its boundary.
        event_time = (
            knowledge_time
            if basis is KnowledgeTimeBasis.SESSION_CLOSE
            else min(knowledge_time, _session_instant(bar.session_date))
        )

        if known_facts.seen(security_id, bar.session_date, bar.adjustment_basis, knowledge_time):
            result.rejected.append(
                RejectedBar(
                    bar, RejectReason.DUPLICATE, bar.ticker, "already present at this revision"
                )
            )
            continue

        known_facts.record(security_id, bar.session_date, bar.adjustment_basis, knowledge_time)
        session.add(
            SecurityPriceFact(
                security_id=security_id,
                session_date=bar.session_date,
                adjustment_basis=bar.adjustment_basis,
                event_time=event_time,
                knowledge_time=knowledge_time,
                knowledge_time_basis=str(basis),
                knowledge_source=delivery.vendor,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                volume_adjusted=bar.volume_adjusted,
                source=delivery.filename or delivery.vendor,
            )
        )
        result.landed += 1
        result.securities_touched.add(security_id)
    session.flush()
    return result


def _session_instant(day: dt.date) -> dt.datetime:
    """Midnight UTC on the session date -- a floor, never a knowledge claim."""
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
