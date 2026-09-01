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

from tradeit.research01.pit import KnowledgeTimeBasis, knowledge_time_for
from tradeit.storage.tables import SecurityPriceFact, SymbolAlias

__all__ = [
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


def import_price_bars(session: Session, bars: list[VendorBar], delivery: Delivery) -> ImportResult:
    """Land what resolves; report what does not. Never guess, never create.

    Idempotent: a bar already present at the same
    ``(security_id, session_date, adjustment_basis, knowledge_time)`` is
    reported as :attr:`RejectReason.DUPLICATE` rather than inserted, so
    re-running a delivery changes nothing.
    """
    result = ImportResult()
    for bar in bars:
        security_id, resolution = resolve_security(session, ticker=bar.ticker, on=bar.session_date)
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

        exists = session.scalars(
            select(SecurityPriceFact.id).where(
                SecurityPriceFact.security_id == security_id,
                SecurityPriceFact.session_date == bar.session_date,
                SecurityPriceFact.adjustment_basis == bar.adjustment_basis,
                SecurityPriceFact.knowledge_time == knowledge_time,
            )
        ).first()
        if exists is not None:
            result.rejected.append(
                RejectedBar(
                    bar, RejectReason.DUPLICATE, bar.ticker, "already present at this revision"
                )
            )
            continue

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
