#!/usr/bin/env python
"""Backfill prices for every identity confirmed from a filing.

Reads the tickers out of the corpus rather than taking a list: the corpus is
the record of what has been *established*, and a hand-maintained symbol list
beside it would drift from that within a week.

Resolution is ``alias_kind="ticker"`` -- the evidence-backed kind. Vendor spans
are not consulted, so a symbol whose identity was never confirmed lands nothing
and is reported, exactly as it should be.

    PYTHONPATH=src .venv/bin/python scripts/research01_backfill_confirmed.py --dry-run
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
from tradeit.storage.tables import (
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

START, END = dt.date(1990, 1, 1), dt.date(2026, 9, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--progress", default=".research01_confirmed_backfill.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()

    tickers = sorted(
        set(
            session.scalars(
                select(SymbolAlias.alias_value).where(
                    SymbolAlias.alias_kind == "ticker",
                    SymbolAlias.knowledge_source == "edgar_filing_text",
                )
            ).all()
        )
    )
    if args.limit:
        tickers = tickers[: args.limit]
    symbols = tuple(f"{t}.US" for t in tickers)
    plan = BackfillPlan(symbols=symbols, start=START, end=END)
    print(
        f"confirmed tickers: {len(symbols):,}   calls: {plan.estimated_calls:,}"
        f"   fits in one day: {plan.fits_in_one_day}"
    )
    if args.dry_run:
        return 0

    report = run_backfill(
        session,
        HttpEodhdClient(api_token=resolve_api_token()),
        plan,
        BackfillProgress.load(Path(args.progress)),
        alias_kind="ticker",
    )
    session.commit()
    print(json.dumps(report.summary(), indent=1))
    print(
        json.dumps(
            {
                "price_facts": session.scalar(select(func.count()).select_from(SecurityPriceFact)),
                "actions": session.scalar(
                    select(func.count()).select_from(SecurityCorporateActionFact)
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
