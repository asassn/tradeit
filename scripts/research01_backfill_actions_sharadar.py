#!/usr/bin/env python
"""Fill missing splits from Sharadar, for securities EODHD had none for.

**Why this replaced the EODHD run.** 8,818 of 17,428 priced securities carry no
corporate action at all. Asked for 105 of them, EODHD returned nothing every
time -- and it was not a lookup failure: 15 of 15 of those symbols returned
price data from the same client in the same window. Its action feed simply has
none for that population. The control settles it: THQ split four times between
1999 and 2012, EODHD returns **no splits at all** for ``THQI.US`` across
2011-2013, and Sharadar returns all four.

A missing split is the open limitation in
``RESEARCH_01_DATA_DICTIONARY.md`` §0.1a -- nothing is double-counted, but the
earlier ``raw`` levels are already adjusted with no record saying so, and a price
floor or a market capitalisation built on them is wrong. Landing the split makes
the series testable by :func:`~tradeit.research01.series.split_evidence`, which
is what decides whether those levels can be trusted.

Measured before running: of 40 actionless securities Sharadar covers, it holds an
in-window split for 5%. A modest yield on purpose -- these are the securities
nobody else recorded a split for, which is exactly the population the limitation
is about.

**Splits only.** Sharadar's dividend ``value`` is restated for later splits --
measured at 52 of 60, against 0 matching the amount declared -- so landing it
would carry a later ratio into an earlier payment. See
:mod:`tradeit.research01.sharadar_client`.

**Two refusals, counted rather than worked around:** an issuer whose CIK carries
more than one Sharadar security, or more than one of ours, where nothing in the
vendor's row says which share class it means; and a split on a date no alias of
ours covers, which keeps an unresolvable label so the importer refuses it.
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

from research01_backfill_sharadar import UNCOVERED, _label, _relabel
from research01_probe_sharadar import OUT, PAGE, TICKERS, _get, _key, _rows
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.actions import import_corporate_actions
from tradeit.research01.importer import Delivery
from tradeit.research01.sharadar_client import parse_splits
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

PROGRESS = OUT / "sharadar_actions_progress.json"
CIK_IN_URL = re.compile(r"CIK=0*(\d+)")


def _alias_rows(
    session: Session, security_id: int
) -> tuple[str, list[tuple[str, dt.date, dt.date | None]]]:
    """This security's own alias intervals, and which kind they are.

    A curated ticker is preferred; a vendor span is used only where there is no
    curated one, which is the same order of preference the importer enforces by
    refusing to resolve a vendor span unless asked.
    """
    for kind in ("ticker", "vendor_symbol"):
        rows = session.execute(
            select(SymbolAlias.alias_value, SymbolAlias.valid_from, SymbolAlias.valid_to).where(
                SymbolAlias.security_id == security_id, SymbolAlias.alias_kind == kind
            )
        ).all()
        if rows:
            return kind, [(value, start, end) for value, start, end in rows]
    return "ticker", []


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
    pairs = session.execute(
        select(Security.security_id, IssuerIdentifier.value_normalized)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(IssuerIdentifier.namespace == "sec_cik")
    ).all()
    cik_of = {security_id: int(cik) for security_id, cik in pairs}
    securities_per_cik: collections.Counter[int] = collections.Counter(cik_of.values())

    priced = {
        s
        for (s,) in session.execute(
            select(SecurityPriceFact.security_id)
            .where(SecurityPriceFact.adjustment_basis == "raw")
            .distinct()
        ).all()
    }
    already = {
        s
        for (s,) in session.execute(
            select(SecurityCorporateActionFact.security_id)
            .where(SecurityCorporateActionFact.source.like("sharadar%"))
            .distinct()
        ).all()
    }
    completed: set[int] = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    targets = sorted(
        s
        for s in priced
        if s not in already and s not in completed and by_cik.get(cik_of.get(s, -1))
    )
    print(
        f"priced securities Sharadar covers and we have not asked: {len(targets):,}; "
        f"this run {min(len(targets), args.limit):,}"
    )

    delivery = Delivery(
        vendor="sharadar", delivered_at=dt.datetime.now(dt.UTC), filename="sharadar-actions"
    )
    before = session.scalar(select(func.count()).select_from(SecurityCorporateActionFact)) or 0
    tally: collections.Counter[str] = collections.Counter()

    for n, security_id in enumerate(targets[: args.limit], 1):
        cik = cik_of[security_id]
        rows = by_cik[cik]
        if len(rows) > 1 or securities_per_cik[cik] > 1:
            # Nothing in the vendor's row says which share class it is.
            tally["refused: more than one security under the CIK"] += 1
            completed.add(security_id)
            continue
        kind, aliases = _alias_rows(session, security_id)
        if not aliases:
            tally["refused: no alias interval"] += 1
            completed.add(security_id)
            continue
        status, text = _get(f"actions?ticker={rows[0]['ticker']}&limit={PAGE}", key)
        time.sleep(args.interval)
        if status != 200:
            tally[f"http {status}"] += 1
            continue
        splits = [
            _relabel(split, _label(aliases, split.ex_date))
            for split in parse_splits(UNCOVERED, _rows(text))
        ]
        if not splits:
            tally["Sharadar has no split for it"] += 1
            completed.add(security_id)
            PROGRESS.write_text(json.dumps(sorted(completed)))
            continue
        landed = import_corporate_actions(session, splits, delivery, alias_kind=kind)
        session.commit()
        completed.add(security_id)
        PROGRESS.write_text(json.dumps(sorted(completed)))
        tally["securities with a split"] += 1
        tally["splits landed"] += landed.landed
        for reason, count in landed.summary()["rejected_by_reason"].items():  # type: ignore[union-attr]
            tally[f"rejected {reason}"] += count
        if args.show:
            print(f"  security {security_id:<6} {rows[0]['ticker']:<8} landed {landed.landed}")
        if n % 250 == 0 or n == min(len(targets), args.limit):
            print(f"  {n}/{min(len(targets), args.limit)}  {dict(tally)}", flush=True)

    PROGRESS.write_text(json.dumps(sorted(completed)))
    after = session.scalar(select(func.count()).select_from(SecurityCorporateActionFact)) or 0
    print(
        f"\n{json.dumps(dict(tally), indent=1)}\n"
        f"actions {before:,} -> {after:,} (+{after - before:,})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
