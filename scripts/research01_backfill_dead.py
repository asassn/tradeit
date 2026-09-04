#!/usr/bin/env python
"""Fetch prices for the dead registrants, bounded by their own lifetimes.

**This is the run that moves the survivorship gate.** The gate counts
registrants with a confirmed dated EDGAR exit whose prices we hold. Everything
built so far -- the denominator, the XBRL cohort, 92 million fundamental facts,
3,745 tickers taken from the registrants' own annual reports -- changes nothing
about it, because the gate measures prices and we have none for these companies.

**Every request is bounded at the registrant's last evidenced activity.** A dead
registrant's plain ticker is frequently held by a different company today, so
asking for the whole history returns the successor's bars alongside this
registrant's. ``resolve_security`` resolves per bar date and would reject them,
but rejection is detection and not asking is prevention -- and it keeps the
unresolved count meaningful: what remains is genuinely unmappable rather than
merely outside an interval.

``valid_to`` is **exclusive**, so the last day this registrant owns the symbol
is the day before it, and that is what the vendor is asked for.

**A symbol that returns nothing is the correct outcome, not a failure.** If
EODHD serves the plain ticker as the living company's series, a request bounded
at 2011 returns an empty range. No data beats another company's data.

    PYTHONPATH=src .venv/bin/python scripts/research01_backfill_dead.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.backfill import BackfillPlan, BackfillProgress, run_backfill
from tradeit.research01.eodhd_client import HttpEodhdClient, resolve_api_token
from tradeit.storage.tables import (
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

#: No US equity history is expected before this, and asking for less costs
#: nothing: the vendor returns what it has.
FLOOR = dt.date(1990, 1, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--progress", default=".research01_dead_backfill.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session: Session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()

    # Bounded aliases only: an unbounded one is a living registrant bound from
    # company_tickers.json, and this run is about the dead.
    rows = session.execute(
        select(SymbolAlias.alias_value, SymbolAlias.valid_to).where(
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.knowledge_source == "edgar_filing_text",
            SymbolAlias.valid_to.is_not(None),
        )
    ).all()

    windows: dict[str, tuple[dt.date, dt.date]] = {}
    for ticker, valid_to in sorted(rows):
        symbol = f"{ticker}.US"
        # valid_to is exclusive; the last day owned is the day before it.
        last_owned = valid_to - dt.timedelta(days=1)
        # Where a symbol is claimed by two registrants in different eras, the
        # LATER bound is kept: the union covers both, and per-bar resolution
        # decides which security each bar belongs to. Narrowing to the earlier
        # one would silently starve the later registrant.
        existing = windows.get(symbol)
        if existing is None or last_owned > existing[1]:
            windows[symbol] = (FLOOR, last_owned)

    symbols = tuple(sorted(windows))
    if args.limit:
        symbols = symbols[: args.limit]
        windows = {s: windows[s] for s in symbols}

    plan = BackfillPlan(
        symbols=symbols, start=FLOOR, end=dt.datetime.now(dt.UTC).date(), windows=windows
    )
    spans = [(e - s).days / 365.25 for s, e in windows.values()]
    print(f"dead registrants with a ticker : {len(rows):,}")
    print(f"distinct symbols to fetch      : {len(symbols):,}")
    print(f"estimated calls                : {plan.estimated_calls:,}")
    print(f"fits in one day's budget       : {plan.fits_in_one_day}")
    if spans:
        print(
            f"request windows                : median {sorted(spans)[len(spans) // 2]:.1f} years, "
            f"latest end {max(e for _, e in windows.values())}"
        )
    if args.dry_run:
        for symbol in symbols[:6]:
            start, end = windows[symbol]
            print(f"    {symbol:<12} {start} .. {end}")
        print("\n(dry run; no requests issued, nothing written)")
        return 0

    before = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    report = run_backfill(
        session,
        HttpEodhdClient(api_token=resolve_api_token()),
        plan,
        BackfillProgress.load(Path(args.progress)),
        alias_kind="ticker",
    )
    session.commit()
    after = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0

    print(json.dumps(report.summary(), indent=1))
    print(
        json.dumps(
            {
                "price_facts_before": before,
                "price_facts_after": after,
                "price_facts_added": after - before,
                "actions_total": session.scalar(
                    select(func.count()).select_from(SecurityCorporateActionFact)
                ),
                "securities_with_prices": session.scalar(
                    select(func.count(func.distinct(SecurityPriceFact.security_id)))
                ),
            },
            indent=1,
        )
    )
    print(
        "\nA symbol that returned nothing is the correct outcome where the vendor "
        "serves the plain ticker as a later company's series: no data beats another "
        "company's data."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
