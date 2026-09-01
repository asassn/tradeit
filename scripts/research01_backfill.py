#!/usr/bin/env python
"""Seed identity, derive vendor spans, and backfill the control universe.

The order matters and is the whole point:

1. **Seed** issuers and securities from curated evidence only.
2. **Derive** a ``vendor_symbol`` span per symbol from the vendor's own EOD
   range. Curated ``ticker`` intervals are never overwritten.
3. **Backfill** prices and actions, resolving against ``vendor_symbol``
   **explicitly** — weaker identity has to be asked for, never fallen back to.

Usage:

    PYTHONPATH=src .venv/bin/python scripts/research01_backfill.py --dry-run
    PYTHONPATH=src .venv/bin/python scripts/research01_backfill.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tradeit.research01.backfill import BackfillPlan, BackfillProgress, run_backfill
from tradeit.research01.eodhd_client import HttpEodhdClient, resolve_api_token
from tradeit.research01.seed import seed_identity
from tradeit.research01.vendor_aliases import DerivedAlias, derive_vendor_aliases
from tradeit.storage.tables import (
    Base,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

#: The controls we have verified spans for. Deliberately explicit rather than
#: derived from the whole roster: this run is a measurement of the control
#: universe, and widening it silently would change what is being measured.
SYMBOLS = ("ETYS.US", "WBVN.US", "GM.US", "GM_old.US")
START, END = dt.date(1998, 1, 1), dt.date(2026, 9, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default="sqlite:///research01.sqlite")
    parser.add_argument("--progress", default=".research01_backfill.json")
    args = parser.parse_args()

    engine = create_engine(args.db, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()

    seeded = seed_identity(session)
    print(f"seed: {json.dumps(seeded.summary())}")

    client = HttpEodhdClient(api_token=resolve_api_token())

    # Derive a span per symbol, attached to the security whose curated note
    # names the matching control. No security, no alias -- identity is not
    # created here any more than it is in the importer.
    notes = {"ETYS.US": "ETYS/", "WBVN.US": "WBVN/", "GM.US": "GM/new_gm", "GM_old.US": "GM/old_gm"}
    derived: list[DerivedAlias] = []
    for symbol in SYMBOLS:
        security_id = session.scalars(
            select(Security.security_id).where(Security.note.like(f"{notes[symbol]}%"))
        ).first()
        if security_id is None:
            print(f"  {symbol}: no seeded security; skipped")
            continue
        rows = client.eod(symbol, START, END)
        days = sorted(dt.date.fromisoformat(r["date"]) for r in rows if r.get("date"))
        if not days:
            print(f"  {symbol}: vendor returned no sessions; skipped")
            continue
        derived.append(DerivedAlias(security_id, symbol.rsplit(".", 1)[0], days[0], days[-1]))
        print(f"  {symbol}: span {days[0]} .. {days[-1]}  -> security {security_id}")

    alias_report = derive_vendor_aliases(session, derived)
    print(f"aliases: {json.dumps(alias_report.summary())}")

    plan = BackfillPlan(symbols=SYMBOLS, start=START, end=END)
    report = run_backfill(
        session,
        client,
        plan,
        BackfillProgress.load(Path(args.progress)),
        dry_run=args.dry_run,
        alias_kind="vendor_symbol",
    )
    print(f"backfill: {json.dumps(report.summary())}")

    if not args.dry_run:
        session.commit()
        counts = {
            "securities": session.scalar(select(func.count()).select_from(Security)),
            "aliases": session.scalar(select(func.count()).select_from(SymbolAlias)),
            "price_facts": session.scalar(select(func.count()).select_from(SecurityPriceFact)),
            "actions": session.scalar(
                select(func.count()).select_from(SecurityCorporateActionFact)
            ),
        }
        print(f"corpus: {json.dumps(counts)}")
    print(f"api calls: {client.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
