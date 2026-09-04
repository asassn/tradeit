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

**And a floor as well as a ceiling.** The first run's audit found ``AAAB``
carrying bars from 1999 to 2003 under a registrant that did not exist until
about 2010: the ceiling stops the *successor's* data and does nothing about a
*predecessor's*. A ticker reused twice needs both. The registrant's first EDGAR
filing supplies the floor -- a US issuer registers before it lists, so its first
filing precedes its first trade and the bound is conservative by construction.

**Except where that date is an artefact of the archive.** EDGAR's full index
begins in 1994Q3, so a first filing dated 1994 means "present when the record
started", not "listed in 1994". Measured: 139 of 3,809 aliases, 3.7%. Those keep
the open floor, because using EDGAR's start date as a company's start date would
assert something no evidence supports.

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
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

#: No US equity history is expected before this, and asking for less costs
#: nothing: the vendor returns what it has.
FLOOR = dt.date(1990, 1, 1)

#: EDGAR's full index begins in 1994Q3. A registrant whose first filing falls in
#: that year was already there when the record started, so the date bounds the
#: ARCHIVE and not the company, and must not be used as a floor.
ARCHIVE_START_YEAR = 1994


def _apply_floors(
    session: Session, first_seen: dict[int, dt.date], *, apply: bool
) -> tuple[int, int]:
    """Put the evidenced start on the alias, and count the bars it excludes.

    **The fetch window and the corpus must agree**, or a bound that keeps the
    predecessor's bars out of the next request leaves the ones already fetched
    in place. ``resolve_security`` tests ``valid_from <= on``, so an alias still
    opening in 1990 would go on admitting them.

    **Nothing is deleted.** A first draft of this purged everything outside the
    interval and would have destroyed 182,462 bars -- of which only 12,824
    predate a registrant. The rest sit *after* ``valid_to`` and were deliberately
    retained by the splice adjudication, on the recorded ground that discarding
    them would destroy the record of a defect that took three attempts to find.
    The interval says which bars belong and ``price_series`` reads both ends of
    it; that is the mechanism, and deleting would have quietly reversed a
    written decision to get the same effect.
    """
    rows = session.execute(
        select(SymbolAlias, IssuerIdentifier.value_normalized)
        .join(Security, Security.security_id == SymbolAlias.security_id)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.knowledge_source == "edgar_filing_text",
            SymbolAlias.valid_to.is_not(None),
            IssuerIdentifier.namespace == "sec_cik",
        )
    ).all()

    tightened = 0
    for alias, cik in rows:
        registered = first_seen.get(int(cik))
        if registered is None or registered.year <= ARCHIVE_START_YEAR:
            continue
        floor = max(FLOOR, registered)
        if floor <= alias.valid_from or floor >= alias.valid_to:
            continue
        tightened += 1
        if apply:
            alias.valid_from = floor
    if apply:
        session.flush()

    if apply:
        session.commit()
    outside = 0
    for alias, _cik in rows:
        outside += (
            session.scalar(
                select(func.count())
                .select_from(SecurityPriceFact)
                .where(
                    SecurityPriceFact.security_id == alias.security_id,
                    SecurityPriceFact.session_date < alias.valid_from,
                )
            )
            or 0
        )
    return tightened, outside


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--progress", default=".research01_dead_backfill.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--failures",
        default="/Users/ericsasson/Documents/TradeItData/out/dead_backfill_failures.json",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--edgar-cache",
        default="/Users/ericsasson/Documents/TradeItData/out/edgar_facts.json",
        help="first_seen per CIK; built by research01_adjudicate.py",
    )
    args = ap.parse_args()

    first_seen = {
        int(cik): dt.date.fromisoformat(value)
        for cik, value in json.loads(Path(args.edgar_cache).read_text())["first_seen"].items()
    }

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    tightened, purged = _apply_floors(session, first_seen, apply=not args.dry_run)
    print(f"alias floors written           : {tightened:,}")
    print(f"bars now excluded by that floor: {purged:,} (retained, not deleted)")

    # Bounded aliases only: an unbounded one is a living registrant bound from
    # company_tickers.json, and this run is about the dead.
    rows = session.execute(
        select(SymbolAlias.alias_value, SymbolAlias.valid_to, IssuerIdentifier.value_normalized)
        .join(Security, Security.security_id == SymbolAlias.security_id)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.knowledge_source == "edgar_filing_text",
            SymbolAlias.valid_to.is_not(None),
            IssuerIdentifier.namespace == "sec_cik",
        )
    ).all()

    windows: dict[str, tuple[dt.date, dt.date]] = {}
    floored = 0
    for ticker, valid_to, cik in sorted(rows):
        symbol = f"{ticker}.US"
        # valid_to is exclusive; the last day owned is the day before it.
        last_owned = valid_to - dt.timedelta(days=1)
        registered = first_seen.get(int(cik))
        if registered is not None and registered.year > ARCHIVE_START_YEAR:
            floor = max(FLOOR, registered)
            floored += 1
        else:
            floor = FLOOR
        # Where a symbol is claimed by two registrants in different eras the
        # window must cover BOTH, so the widest is kept: per-bar resolution
        # decides which security each bar belongs to, and narrowing here would
        # silently starve one of them.
        existing = windows.get(symbol)
        if existing is None:
            windows[symbol] = (floor, last_owned)
        else:
            windows[symbol] = (min(existing[0], floor), max(existing[1], last_owned))

    symbols = tuple(sorted(windows))
    if args.limit:
        symbols = symbols[: args.limit]
        windows = {s: windows[s] for s in symbols}

    plan = BackfillPlan(
        symbols=symbols, start=FLOOR, end=dt.datetime.now(dt.UTC).date(), windows=windows
    )
    spans = [(e - s).days / 365.25 for s, e in windows.values()]
    print(f"dead registrants with a ticker : {len(rows):,}")
    print(f"  with an evidenced start floor : {floored:,}")
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
    if report.failures:
        # **The summary counts failures; it does not keep them.** 194 symbols
        # failed in the first full run and the reasons went to nothing but an
        # integer, so "which ones, and why" could not be answered without
        # paying for the whole fetch again. A paid run's failures are evidence.
        Path(args.failures).write_text(
            json.dumps([{"symbol": s_, "error": e} for s_, e in report.failures], indent=1)
        )
        print(f"wrote {len(report.failures)} failures to {args.failures}")
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
