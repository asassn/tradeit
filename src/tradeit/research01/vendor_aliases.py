"""Deriving symbol intervals from a vendor, and keeping them in their place.

The corpus holds evidence-backed identity for 34 issuers and an alias interval
for **one** of them, because `control_identity_evidence.json` records when a
ticker was held for exactly one mapping. Without intervals the importer resolves
nothing, so no data lands.

**The ISIN route was tried and withdrawn on measurement.** EODHD's symbol list
carries an ISIN for 71.3% of active names but only **31.1% of delisted** ones —
and for none of the four verified controls. It fails on precisely the population
this corpus exists for.

What is left is the vendor's own span: the first and last session it has for a
symbol. That is usable and it is **not a verified ticker interval**, for two
reasons the vendor states itself:

* On a plain rename with no delisting, EODHD *moves* history to the new symbol.
  A span therefore describes when the **symbol** had data, which can begin
  before that symbol was in use.
* Spans overlap. `GM_old` runs to 2011-03-31 while `GM` starts 2010-11-18 —
  133 days in which a date alone cannot say which security is meant.

So a derived interval is written as ``alias_kind="vendor_symbol"``, **never
``"ticker"``**. The importer resolves on ``"ticker"`` by default, so vendor
identity is never used by accident: a caller must ask for it, at the call site,
in code somebody can read.

**Curated intervals are never overwritten.** A security that already has a
``ticker`` alias keeps it, and a derived row is skipped rather than competing
with evidence.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.storage.tables import SymbolAlias

__all__ = ["DerivedAlias", "VendorAliasReport", "derive_vendor_aliases"]


@dataclass(frozen=True, slots=True)
class DerivedAlias:
    """A symbol, the security it is claimed for, and the span the vendor holds."""

    security_id: int
    symbol: str
    first_session: dt.date
    last_session: dt.date


@dataclass(slots=True)
class VendorAliasReport:
    written: int = 0
    skipped_curated: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    overlapping: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "written": self.written,
            "skipped_curated": self.skipped_curated,
            "skipped_existing": self.skipped_existing,
            "overlapping_pairs": [list(pair) for pair in self.overlapping],
        }


def derive_vendor_aliases(
    session: Session,
    derived: list[DerivedAlias],
    *,
    vendor: str = "eodhd",
    knowledge_time: dt.datetime | None = None,
) -> VendorAliasReport:
    """Write vendor spans as ``vendor_symbol`` aliases. Never as tickers.

    Overlaps between derived spans are **recorded and left in place**. Trimming
    one to make them disjoint would manufacture a boundary the evidence does not
    support; leaving them means the importer reports ``UNRESOLVED_AMBIGUOUS``
    for dates inside the overlap, which is the honest answer to "which security
    held this symbol that day".
    """
    report = VendorAliasReport()
    stamp = knowledge_time or dt.datetime.now(dt.UTC)

    for item in derived:
        curated = session.scalars(
            select(SymbolAlias.id).where(
                SymbolAlias.security_id == item.security_id,
                SymbolAlias.alias_kind == "ticker",
            )
        ).first()
        if curated is not None:
            # Evidence outranks a vendor span, and silently adding a second
            # claim beside it would make the resolver ambiguous where it had
            # been certain.
            report.skipped_curated.append(item.symbol)
            continue

        existing = session.scalars(
            select(SymbolAlias.id).where(
                SymbolAlias.security_id == item.security_id,
                SymbolAlias.alias_kind == "vendor_symbol",
                SymbolAlias.alias_value == item.symbol,
            )
        ).first()
        if existing is not None:
            report.skipped_existing.append(item.symbol)
            continue

        session.add(
            SymbolAlias(
                security_id=item.security_id,
                alias_kind="vendor_symbol",
                alias_value=item.symbol,
                valid_from=item.first_session,
                # Half-open, so the last session the vendor holds is inside the
                # interval rather than one day outside it.
                valid_to=item.last_session + dt.timedelta(days=1),
                knowledge_time=stamp,
                knowledge_source=f"{vendor}_symbol_span",
                citation=(
                    f"derived from {vendor} EOD span "
                    f"{item.first_session}..{item.last_session}; "
                    "a statement about the symbol, not a verified ticker interval"
                ),
                source=f"{vendor}_symbol_span",
            )
        )
        report.written += 1

    for i, a in enumerate(derived):
        for b in derived[i + 1 :]:
            if a.symbol == b.symbol:
                continue
            if a.first_session <= b.last_session and b.first_session <= a.last_session:
                if a.security_id != b.security_id:
                    continue
                report.overlapping.append((a.symbol, b.symbol))

    session.flush()
    return report
