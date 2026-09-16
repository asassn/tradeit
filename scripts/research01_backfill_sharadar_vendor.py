#!/usr/bin/env python
"""Land Sharadar prices for verified exits the corpus cannot ticker.

``research01_backfill_sharadar.py`` landed the 1,666 dated exits whose identity
the corpus already held. **1,211 more have a Sharadar series whose dates fit
their EDGAR exit and no curated ticker at all** -- the resolver read their own
filings and found no symbol (`EDGAR_DELISTING_DENOMINATOR.md` §7e).

Giving those a ``ticker`` alias on a vendor's say-so is the identity creation
:mod:`tradeit.research01.importer` structurally refuses. The corpus already has
the weaker path for exactly this and it is used here unchanged:
:func:`~tradeit.research01.vendor_aliases.derive_vendor_aliases` writes the
vendor's span as ``alias_kind="vendor_symbol"``, curated tickers are never
overwritten, and the importer only resolves against it when a caller **asks at
the call site** -- which this script does, once, visibly.

**What that buys and what it costs.** It prices exits the corpus could not
price, which is what the survivorship gate measures. It does so on identity that
is a vendor's assertion tested against one independent fact: Sharadar's CIK and
its first and last price dates agreeing with EDGAR's exit date, already measured
per exit in ``sharadar_price_probe.jsonl``. That is weaker than a filing read and
is recorded as such, in the alias kind, in the citation, and here.

**Two refusals, both counted rather than worked around:**

* an issuer carrying **more than one security** -- picking one would be a guess,
  and the vendor's row does not say which share class it means;
* a security that already has a curated ticker -- `derive_vendor_aliases` skips
  it, because evidence outranks a span.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_backfill_sharadar import _relabel
from research01_probe_sharadar import OUT, PAGE, _get, _key, _rows
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.actions import import_corporate_actions
from tradeit.research01.importer import Delivery, import_price_bars
from tradeit.research01.sharadar_client import parse_sep_bars, parse_splits
from tradeit.research01.vendor_aliases import DerivedAlias, derive_vendor_aliases
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerIdentifier, Security, SecurityPriceFact

PROGRESS = OUT / "sharadar_vendor_backfill_progress.json"
MATCHES = OUT / "sharadar_match.json"


def _sole_security(session: Session, cik: int) -> int | None:
    """The one security under this CIK, or ``None`` if there is not exactly one."""
    rows = session.scalars(
        select(Security.security_id)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(
            IssuerIdentifier.namespace == "sec_cik",
            IssuerIdentifier.value_normalized == str(cik),
        )
    ).all()
    return rows[0] if len(rows) == 1 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.3)
    args = ap.parse_args()

    key = _key()
    if not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2

    exits_payload = json.loads((OUT / "dated_exits.json").read_text())
    resolved = {int(c) for c in exits_payload["resolved"]}
    matches = json.loads(MATCHES.read_text())
    probe: dict[int, dict] = {}
    for line in (OUT / "sharadar_price_probe.jsonl").read_text().splitlines():
        record = json.loads(line)
        probe[record["cik"]] = record
    targets = sorted(
        (r for r in probe.values() if r["grade"] == "PLAUSIBLE" and r["cik"] not in resolved),
        key=lambda r: r["cik"],
    )
    completed: set[int] = (
        set(json.loads(PROGRESS.read_text())["completed"]) if PROGRESS.exists() else set()
    )
    todo = [t for t in targets if t["cik"] not in completed][: args.limit]
    print(
        f"verified exits with no curated ticker: {len(targets):,}; "
        f"completed {len(completed):,}; this run {len(todo):,}"
    )

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    delivery = Delivery(
        vendor="sharadar",
        delivered_at=dt.datetime.now(dt.UTC),
        filename="sharadar-vendor-span-backfill",
    )
    before = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    tally: collections.Counter[str] = collections.Counter()

    for n, target in enumerate(todo, 1):
        cik = target["cik"]
        security_id = _sole_security(session, cik)
        if security_id is None:
            tally["refused: issuer has no single security"] += 1
            completed.add(cik)
            continue
        meta = matches.get(str(cik)) or {}
        first, last = meta.get("first"), meta.get("last")
        if not first or not last:
            tally["refused: no Sharadar span"] += 1
            completed.add(cik)
            continue
        symbol = target["ticker"]
        report = derive_vendor_aliases(
            session,
            [
                DerivedAlias(
                    security_id=security_id,
                    symbol=symbol,
                    first_session=dt.date.fromisoformat(first),
                    last_session=dt.date.fromisoformat(last),
                )
            ],
            vendor="sharadar",
        )
        if report.skipped_curated:
            tally["skipped: curated ticker exists"] += 1
            completed.add(cik)
            continue
        tally["vendor spans written"] += report.written

        status, text = _get(f"stocks?ticker={symbol}&limit={PAGE}", key)
        a_status, a_text = _get(f"actions?ticker={symbol}&limit={PAGE}", key)
        if status != 200 or a_status != 200:
            tally["http failure"] += 1
            session.rollback()
            continue
        bars = parse_sep_bars(symbol, _rows(text))
        splits = [_relabel(s, symbol) for s in parse_splits(symbol, _rows(a_text))]
        landed = import_price_bars(session, bars, delivery, alias_kind="vendor_symbol")
        acted = import_corporate_actions(session, splits, delivery, alias_kind="vendor_symbol")
        session.commit()
        completed.add(cik)
        PROGRESS.write_text(json.dumps({"completed": sorted(completed)}))
        tally["securities"] += 1
        tally["bars landed"] += landed.landed
        tally["splits landed"] += acted.landed
        for reason, count in landed.summary()["rejected_by_reason"].items():  # type: ignore[union-attr]
            tally[f"bars rejected {reason}"] += count
        if n % 25 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}  {dict(tally)}", flush=True)
        time.sleep(args.interval)

    PROGRESS.write_text(json.dumps({"completed": sorted(completed)}))
    after = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    print(
        f"\n{json.dumps(dict(tally), indent=1)}\n"
        f"price facts {before:,} -> {after:,} (+{after - before:,})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
