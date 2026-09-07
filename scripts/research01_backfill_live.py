#!/usr/bin/env python
"""Fetch prices for the registrants that are still trading.

**The corpus is the wrong shape and this is the correction.** Measured
2026-09-06: `research-01` holds prices for 5,732 registrants with a confirmed
dated exit and **72** without one. Every stage of the work so far was scoped to
the survivorship gate, whose numerator is dead registrants, so all of the effort
went into one ratio's numerator and nobody fetched the universe.

A corpus of failures with no survivors is not survivorship-safe. It is the
mirror image of the bias, and it is equally unusable: there is nothing to rank,
nothing to benchmark against, and no portfolio to construct out of 72 names.

**What makes a registrant "live" here is the absence of a bound**, which is the
same fact ``research01_backfill_dead.py`` uses to exclude them. An alias from
``sec_company_tickers`` carries ``valid_to = NULL`` because the SEC publishes it
as a *current* ticker; a dead registrant's alias is closed at its last evidenced
activity. The two scripts partition the corpus on that column and neither can
silently take the other's rows.

**A floor still applies, and for the same reason it does for the dead.** A
ticker trading today may have been held by a different company before this
registrant existed -- the predecessor problem, which a ceiling does nothing
about. The registrant's first EDGAR filing supplies it: a US issuer registers
before it lists, so the first filing precedes the first trade and the bound is
conservative by construction.

**There is no ceiling.** A live registrant owns its symbol through to the
present, so the window ends at ``--as-of``. That is the one real difference
from the dead run, and it is why the two are separate scripts rather than a
flag: a bound that is absent by *design* and one that is absent by *omission*
look identical in a column, and only the script that knows which it is may act.

    EODHD_API_KEY=... PYTHONPATH=src .venv/bin/python \\
        scripts/research01_backfill_live.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, distinct, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.backfill import BackfillProgress, plan_for, run_backfill
from tradeit.research01.eodhd_client import HttpEodhdClient, resolve_api_token
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerIdentifier, Security, SecurityPriceFact, SymbolAlias

DEFAULT_CACHE = "/Users/ericsasson/Documents/TradeItData/out/edgar_facts.json"

#: EDGAR's full index begins 1994 Q3, so a "first filing" on one of the first
#: days it covers means "the archive starts here", not "the registrant listed
#: here". Those get no floor rather than a false one -- the same rule the dead
#: run applies, and for the same reason.
ARCHIVE_FLOOR = dt.date(1994, 10, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--edgar-cache", default=DEFAULT_CACHE)
    ap.add_argument("--progress", default=".research01_live_backfill.json")
    # UTC rather than local: the corpus is dated in UTC everywhere else, and a
    # machine in a different zone must not produce a different window.
    ap.add_argument("--as-of", default=dt.datetime.now(dt.UTC).date().isoformat())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    facts = json.loads(Path(args.edgar_cache).read_text())
    first_seen = {
        int(cik): dt.date.fromisoformat(value) for cik, value in facts["first_seen"].items()
    }
    exits = {int(cik) for cik in facts["exits"]}

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    # Unbounded aliases only: valid_to IS NULL is what "the SEC publishes this
    # as a current ticker" looks like in this schema.
    rows = session.execute(
        select(SymbolAlias.alias_value, SymbolAlias.security_id, IssuerIdentifier.value_normalized)
        .join(Security, Security.security_id == SymbolAlias.security_id)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.valid_to.is_(None),
            IssuerIdentifier.namespace == "sec_cik",
        )
    ).all()

    already = {
        int(value)
        for value in session.scalars(
            select(IssuerIdentifier.value_normalized)
            .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
            .join(SecurityPriceFact, SecurityPriceFact.security_id == Security.security_id)
            .where(IssuerIdentifier.namespace == "sec_cik")
            .distinct()
        ).all()
    }

    windows: dict[str, tuple[dt.date, dt.date]] = {}
    symbols: list[str] = []
    dead_skipped = floored = 0
    for ticker, _security_id, cik_text in sorted(rows):
        cik = int(cik_text)
        if cik in exits:
            # A registrant with a dated exit is the other script's work, even
            # if it also carries an unbounded alias. Fetching it here would
            # request the successor's bars, which is the whole hazard.
            dead_skipped += 1
            continue
        if cik in already:
            continue
        floor = first_seen.get(cik)
        start = floor if floor and floor >= ARCHIVE_FLOOR else dt.date(1990, 1, 1)
        if floor and floor >= ARCHIVE_FLOOR:
            floored += 1
        windows[ticker] = (start, as_of)
        symbols.append(ticker)

    symbols = sorted(set(symbols))
    if args.limit:
        symbols = symbols[: args.limit]

    plan = plan_for(symbols, dt.date(1990, 1, 1), as_of, windows=windows)
    print(f"live registrants with a current ticker : {len(rows):,}")
    print(f"  skipped, has a dated exit            : {dead_skipped:,}")
    print(f"  skipped, already priced              : {len(rows) - dead_skipped - len(symbols):,}")
    print(f"distinct symbols to fetch              : {len(symbols):,}")
    print(f"  with an evidenced start floor        : {floored:,}")
    print(f"estimated calls                        : {plan.estimated_calls:,}")
    print(f"fits in one day's budget               : {plan.fits_in_one_day}")
    if args.dry_run:
        for symbol in symbols[:8]:
            start, end = plan.window_for(symbol)
            print(f"    {symbol:<10} {start} .. {end}")
        print("\n(dry run; nothing fetched)")
        return 0

    progress = BackfillProgress.load(Path(args.progress))
    client = HttpEodhdClient(api_token=resolve_api_token(env_file=Path(".env")))
    report = run_backfill(session, client, plan, progress)
    print(json.dumps(report.summary(), indent=1, default=str))
    priced = session.scalar(select(func.count(distinct(SecurityPriceFact.security_id))))
    print(f"securities with prices, corpus-wide: {priced:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
