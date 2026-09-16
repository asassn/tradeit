#!/usr/bin/env python
"""Extend a security's history backwards where Sharadar starts earlier.

**Sharadar's own metadata overstates what it serves, and this was measured
before the run.** Its ``tickers`` table gives AAPL, MSFT and KO a
``firstpricedate`` of 1986, and its ``stocks`` table returns **nothing** for any
of them before **1997-12-31**: the field describes when the security existed,
not what this plan holds. Taking the field at face value made 6,099 securities
look extendable; against the measured floor the real number is **2,219**, and a
sample of twelve returned earlier bars for twelve.

A bar dated just before our first session resolves against an existing alias
interval for 94% of them, so the identity to land it already exists and nothing
new has to be asserted.

**What lands and what is refused.** Bars are labelled with the curated ticker --
or the vendor span, where that is all the security has -- that covers each bar's
own date, exactly as the other Sharadar runs do. A bar earlier than any interval
keeps an unresolvable label and ``import_price_bars`` refuses it. Only sessions
**before** the corpus's current first print are offered, so this adds history
rather than restating what is already held.

**The limitation this creates, stated before the run rather than after.** The
survivorship denominator is blind to exchange delistings before roughly 2002
(``EDGAR_DELISTING_DENOMINATOR.md`` §7be) and the gate's own coverage by exit
year is 25.4% for 1998 and lower before it. Extending the *survivors*' history
into those years therefore makes that window **more** survivor-heavy, not less:
the companies that failed in it are largely absent and cannot be counted. A
study that reaches back there is reading a survivor-weighted universe, and no
measurement in this corpus can tell it how badly.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_backfill_actions_sharadar import _alias_rows
from research01_backfill_sharadar import UNCOVERED, _label, _relabel
from research01_probe_sharadar import OUT, PAGE, TICKERS, _get, _key, _rows
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.importer import Delivery, import_price_bars
from tradeit.research01.sharadar_client import parse_sep_bars
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerIdentifier, Security, SecurityPriceFact

PROGRESS = OUT / "sharadar_history_progress.json"
CIK_IN_URL = re.compile(r"CIK=0*(\d+)")
#: A shorter head start than this is not worth a request.
MIN_GAP_DAYS = 30
#: The earliest session Sharadar's ``stocks`` table serves on this plan, measured
#: rather than read from ``tickers.firstpricedate``, which claims 1986 for
#: securities whose prices begin here.
SHARADAR_FLOOR = dt.date(1997, 12, 31)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    key = _key()
    if not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    by_cik: dict[int, list[dict]] = collections.defaultdict(list)
    for row in json.loads(TICKERS.read_text()):
        found = CIK_IN_URL.search(row.get("secfilings") or "")
        if found:
            by_cik[int(found.group(1))].append(row)
    cik_of = {
        security_id: int(cik)
        for security_id, cik in session.execute(
            select(Security.security_id, IssuerIdentifier.value_normalized)
            .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
            .where(IssuerIdentifier.namespace == "sec_cik")
        ).all()
    }
    firsts = session.execute(
        select(SecurityPriceFact.security_id, func.min(SecurityPriceFact.session_date))
        .where(SecurityPriceFact.adjustment_basis == "raw")
        .group_by(SecurityPriceFact.security_id)
    ).all()

    completed: set[int] = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    targets: list[tuple[int, str, dt.date]] = []
    for security_id, first in firsts:
        if security_id in completed:
            continue
        rows = by_cik.get(cik_of.get(security_id, -1), [])
        if len(rows) != 1 or not rows[0].get("firstpricedate"):
            continue
        theirs = max(SHARADAR_FLOOR, dt.date.fromisoformat(rows[0]["firstpricedate"]))
        if (first - theirs).days >= MIN_GAP_DAYS:
            targets.append((security_id, rows[0]["ticker"], first))
    targets.sort()
    print(
        f"securities Sharadar starts earlier for: {len(targets):,}; "
        f"completed {len(completed):,}; this run {min(len(targets), args.limit):,}"
    )

    delivery = Delivery(
        vendor="sharadar",
        delivered_at=dt.datetime.now(dt.UTC),
        filename="sharadar-history-extension",
    )
    before = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    tally: collections.Counter[str] = collections.Counter()

    for n, (security_id, ticker, first) in enumerate(targets[: args.limit], 1):
        kind, aliases = _alias_rows(session, security_id)
        if not aliases:
            tally["refused: no alias interval"] += 1
            completed.add(security_id)
            continue
        cutoff = (first - dt.timedelta(days=1)).isoformat()
        status, text = _get(
            f"stocks?ticker={ticker}&date.gte={SHARADAR_FLOOR.isoformat()}"
            f"&date.lte={cutoff}&limit={PAGE}",
            key,
        )
        time.sleep(args.interval)
        if status != 200:
            tally[f"http {status}"] += 1
            continue
        bars = []
        for bar in parse_sep_bars(UNCOVERED, _rows(text)):
            label = _label(aliases, bar.session_date)
            bars.append(bar if label == UNCOVERED else _relabel(bar, label))
        if not bars:
            tally["Sharadar returned nothing earlier"] += 1
            completed.add(security_id)
            continue
        landed = import_price_bars(session, bars, delivery, alias_kind=kind)
        session.commit()
        completed.add(security_id)
        PROGRESS.write_text(json.dumps(sorted(completed)))
        tally["securities extended" if landed.landed else "nothing resolved"] += 1
        tally["bars landed"] += landed.landed
        for reason, count in landed.summary()["rejected_by_reason"].items():  # type: ignore[union-attr]
            tally[f"rejected {reason}"] += count
        if args.show:
            print(f"  security {security_id:<6} {ticker:<8} before {first}: landed {landed.landed}")
        if n % 250 == 0 or n == min(len(targets), args.limit):
            print(f"  {n}/{min(len(targets), args.limit)}  {dict(tally)}", flush=True)

    PROGRESS.write_text(json.dumps(sorted(completed)))
    after = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    print(
        f"\n{json.dumps(dict(tally), indent=1)}\n"
        f"price facts {before:,} -> {after:,} (+{after - before:,})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
