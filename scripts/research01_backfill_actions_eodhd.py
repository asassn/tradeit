#!/usr/bin/env python
"""Ask EODHD for the splits and dividends of securities that have neither.

Measured 2026-09-16: of 17,428 securities carrying raw prints, **8,818 have no
corporate action at all** -- 11,613,511 price rows with nothing recorded about
the share count behind them. 7,799 of those carry a curated ticker, which is
what this run asks about; the other 980 have only a vendor span and are left to
the vendor that supplied it.

**Why this is not cosmetic.** `RESEARCH_01_DATA_DICTIONARY.md` §0.1a records the
limitation this attacks: a split the vendor never recorded is not caught, so
earlier `raw` levels can be adjusted with nothing to say so, and a price floor or
market capitalisation built on them is wrong. A missing split is also the reason
some series look continuous where a print should step. Dividends matter for a
different reason: without them a total-return basis is the vendor's own, and
`pit.py` already established that one carries the vendor's adjustment epoch.

**Every request is bounded to the alias interval**, per symbol, for the reason
``research01_backfill_exits`` gives: a dead registrant's ticker is often held by
someone else now, and an unbounded request returns the successor's actions.
``resolve_security`` would refuse them per date anyway -- rejection is detection
-- but not asking is prevention.

**Nothing is inferred from silence.** A symbol that returns no split is recorded
as attempted, not as a security with no splits: this run cannot distinguish "the
vendor has none" from "the vendor has none *for this symbol*", and the progress
file exists so the difference is auditable later.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_probe_sharadar import OUT
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.actions import import_corporate_actions
from tradeit.research01.eodhd_client import (
    HttpEodhdClient,
    parse_dividends,
    parse_splits,
    resolve_api_token,
)
from tradeit.research01.importer import Delivery
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityCorporateActionFact, SecurityPriceFact, SymbolAlias

PROGRESS = OUT / "eodhd_actions_progress.json"


def _targets(session: Session) -> list[tuple[int, str, dt.date, dt.date]]:
    """(security, symbol, from, to) for each curated ticker of an actionless security."""
    priced = (
        select(
            SecurityPriceFact.security_id.label("security_id"),
            func.min(SecurityPriceFact.session_date).label("first_session"),
            func.max(SecurityPriceFact.session_date).label("last_session"),
        )
        .where(SecurityPriceFact.adjustment_basis == "raw")
        .group_by(SecurityPriceFact.security_id)
        .subquery()
    )
    has_action = select(SecurityCorporateActionFact.security_id).distinct().scalar_subquery()
    rows = session.execute(
        select(
            SymbolAlias.security_id,
            SymbolAlias.alias_value,
            SymbolAlias.valid_from,
            SymbolAlias.valid_to,
            priced.c.first_session,
            priced.c.last_session,
        )
        .join(priced, priced.c.security_id == SymbolAlias.security_id)
        .where(SymbolAlias.alias_kind == "ticker", SymbolAlias.security_id.not_in(has_action))
    ).all()

    out: list[tuple[int, str, dt.date, dt.date]] = []
    for security_id, symbol, valid_from, valid_to, first_session, last_session in rows:
        # The alias interval is half-open, so the last owned day is the one
        # before valid_to; an open interval is bounded by the prints instead.
        start = max(valid_from, first_session) if valid_from else first_session
        end = min(valid_to - dt.timedelta(days=1), last_session) if valid_to else last_session
        if end <= start:
            continue
        out.append((security_id, f"{symbol}.US", start, end))
    out.sort()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.15)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    targets = _targets(session)
    completed: set[str] = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    todo = [t for t in targets if f"{t[0]}|{t[1]}" not in completed][: args.limit]
    print(
        f"curated tickers on securities with no corporate action: {len(targets):,}; "
        f"completed {len(completed):,}; this run {len(todo):,}"
    )

    client = HttpEodhdClient(api_token=resolve_api_token(env_file=Path(".env")))
    delivery = Delivery(
        vendor="eodhd", delivered_at=dt.datetime.now(dt.UTC), filename="eodhd-actions-backfill"
    )
    tally: collections.Counter[str] = collections.Counter()
    before = session.scalar(select(func.count()).select_from(SecurityCorporateActionFact)) or 0

    for n, (security_id, symbol, start, end) in enumerate(todo, 1):
        token = f"{security_id}|{symbol}"
        try:
            actions = parse_splits(symbol, client.splits(symbol, start, end))
            actions += parse_dividends(symbol, client.dividends(symbol, start, end))
        except Exception as error:
            # One bad symbol must not cost the rest of a paid run.
            tally[f"failed: {type(error).__name__}"] += 1
            completed.add(token)
            continue
        landed = import_corporate_actions(session, actions, delivery)
        session.commit()
        completed.add(token)
        PROGRESS.write_text(json.dumps(sorted(completed)))
        tally["symbols asked"] += 1
        tally["actions landed"] += landed.landed
        if not actions:
            tally["vendor returned nothing"] += 1
        for reason, count in landed.summary()["rejected_by_reason"].items():  # type: ignore[union-attr]
            tally[f"rejected {reason}"] += count
        if args.show:
            print(f"  {symbol:<12} {start}..{end}  landed {landed.landed}")
        if n % 100 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}  {dict(tally)}", flush=True)
        time.sleep(args.interval)

    PROGRESS.write_text(json.dumps(sorted(completed)))
    after = session.scalar(select(func.count()).select_from(SecurityCorporateActionFact)) or 0
    print(
        f"\n{json.dumps(dict(tally), indent=1)}\n"
        f"actions {before:,} -> {after:,} (+{after - before:,})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
