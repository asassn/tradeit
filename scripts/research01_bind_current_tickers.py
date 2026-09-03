#!/usr/bin/env python
"""Bind tickers from the SEC's own current-listings file.

``company_tickers.json`` is ``MappingEvidence.SEC_COMPANY_TICKERS`` -- ranked
**second, above filing text**, and not in ``_INSUFFICIENT_ALONE``, so it
establishes a mapping on its own. It is the SEC saying which ticker a CIK holds.

**What it cannot do is move the survivorship gate.** Measured against the seeded
XBRL cohort: 6,529 of 17,000 appear in it, and only 243 of those are registrants
with a confirmed dated exit. The gate counts *dead* registrants we can price, so
this file supplies the living half and almost none of the half that matters. It
is still worth binding -- a survivorship-safe corpus needs the survivors too, or
there is no cross-section to compute -- but the gate will not move on it and
this script says so rather than letting a coverage number imply otherwise.

**The 243 are refused, not bound.** A registrant carrying a confirmed dated exit
that the SEC lists as trading today is a contradiction: one of the two claims is
wrong. Measured across the whole denominator, 368 of 29,180 confirmed dated
exits are listed today, clustered in 2024-2026 -- the shape of a Form 25 for one
class filed too recently for a periodic report to have superseded it, which
``assert_exit_not_contradicted`` cannot catch because it guards against an
*earlier* periodic report, not a later one. Resolving that is denominator
methodology and needs its own proposition; binding here would settle it
silently, in favour of whichever source this script happened to trust.

**The interval is open and the citation says why.** A current-listings snapshot
evidences that a CIK holds a ticker *now*. It says nothing about when that
began, so a company that changed symbol in 2015 would have today's symbol
attached to its 2010 prices. ``valid_from`` is therefore unbounded and unclaimed,
exactly as the filing-text aliases are, and the splice that would follow is what
``series_coherence`` and ``adjudicate.py`` exist to catch.

    PYTHONPATH=src .venv/bin/python scripts/research01_bind_current_tickers.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.storage.tables import IssuerIdentifier, Security, SymbolAlias

DEFAULT_TICKERS = "/Users/ericsasson/Documents/TradeItData/edgar/reference/company_tickers.json"
KNOWLEDGE_SOURCE = "sec_company_tickers"


def _current(path: Path) -> tuple[dict[int, tuple[str, str]], set[int]]:
    """CIKs holding exactly one ticker, and the CIKs holding several.

    **A CIK with two symbols is refused, not resolved to the first one.** The
    file lists 10,396 symbols across 7,995 CIKs, so hundreds of registrants
    carry more than one -- every dual-class structure does. This corpus holds
    one security per seeded issuer, so there is no field in which "which class"
    could be recorded, and taking whichever the file listed first would attach
    one class's prices to a security that does not say which class it is.
    Mirrors ``_security_for_issuer``, which refuses an issuer holding several
    securities for the same reason.
    """
    raw = json.loads(path.read_text())
    entries = raw.values() if isinstance(raw, dict) else raw
    seen: dict[int, list[tuple[str, str]]] = {}
    for entry in entries:
        seen.setdefault(int(entry["cik_str"]), []).append(
            (str(entry["ticker"]).upper(), entry.get("title", ""))
        )
    single = {cik: pairs[0] for cik, pairs in seen.items() if len(pairs) == 1}
    several = {cik for cik, pairs in seen.items() if len(pairs) > 1}
    return single, several


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--edgar-cache", required=True, help="JSON from research01_adjudicate.py")
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.tickers)
    snapshot = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).date()
    current, multi_class = _current(path)
    exits = set(json.loads(Path(args.edgar_cache).read_text())["exits"])

    session: Session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()
    rows = session.execute(
        select(IssuerIdentifier.value_normalized, Security.security_id)
        .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
        .where(IssuerIdentifier.namespace == "sec_cik")
    ).all()
    by_cik = {int(v): s for v, s in rows}
    already = {
        s
        for s in session.scalars(
            select(SymbolAlias.security_id).where(SymbolAlias.alias_kind == "ticker")
        ).all()
    }

    bindable: list[tuple[int, int, str, str]] = []
    contradictions: list[int] = []
    ambiguous: list[int] = []
    for cik, security_id in sorted(by_cik.items()):
        if security_id in already:
            continue
        if cik in multi_class:
            ambiguous.append(cik)
            continue
        if cik not in current:
            continue
        if str(cik) in exits:
            contradictions.append(cik)
            continue
        ticker, title = current[cik]
        bindable.append((cik, security_id, ticker, title))

    print(
        f"company_tickers.json snapshot   : {snapshot}  "
        f"({len(current):,} single-ticker CIKs, {len(multi_class):,} with several)"
    )
    print(f"securities without a ticker yet : {len(by_cik) - len(already):,}")
    print(f"  bindable from this file       : {len(bindable):,}")
    print(f"  REFUSED, exit contradicts it  : {len(contradictions):,}")
    print(f"  REFUSED, CIK holds >1 ticker  : {len(ambiguous):,}")

    if args.dry_run:
        for cik, _sid, ticker, title in bindable[:8]:
            print(f"    {cik:<9} {ticker:<7} {title[:48]}")
        print("\n(dry run; nothing written)")
        return 0

    now = dt.datetime.now(dt.UTC)
    for cik, security_id, ticker, title in bindable:
        session.add(
            SymbolAlias(
                security_id=security_id,
                alias_kind="ticker",
                alias_value=ticker,
                # Unbounded and unclaimed: the file evidences that this CIK
                # holds this symbol TODAY and dates nothing before that.
                valid_from=dt.date(1990, 1, 1),
                valid_to=None,
                knowledge_time=now,
                knowledge_source=KNOWLEDGE_SOURCE,
                citation=(
                    f'SEC company_tickers.json, snapshot {snapshot}: CIK {cik} holds "{ticker}" '
                    f'("{title}"). Evidences CURRENT holding only; the start of the interval is '
                    "NOT evidenced and is left open deliberately."
                ),
                source=KNOWLEDGE_SOURCE,
            )
        )
    session.commit()
    print(f"\nbound {len(bindable):,} tickers")
    print(
        "The survivorship gate counts registrants with a confirmed dated exit that "
        "we can price. Of these, almost none are such registrants, so the gate does "
        "not move on this."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
