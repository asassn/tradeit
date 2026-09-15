#!/usr/bin/env python
"""Land Sharadar prices for dated exits the corpus could not price.

Authorised by the owner on 2026-09-14 ("yes ingest") after
``docs/PROPOSAL_SURVIVORSHIP_DATA_2026-09-14.md`` measured 2,877 exits whose
Sharadar series fit their EDGAR exit dates.

**Scope: exits whose identity the corpus already holds.** 1,666 of the 2,877
have curated ticker aliases; their bars are labelled from those aliases and land
through :func:`~tradeit.research01.importer.import_price_bars` exactly as EODHD's
do. The other 1,211 have no alias, and giving them one on a vendor's say-so is
identity creation the importer structurally refuses -- a separate question, not
answered here.

**The identity bridge is the CIK, and the alias interval bounds what lands.**
Sharadar's symbol (``CVNS1``, ``BBBYQ``) resolves to nothing in this corpus, so
each bar is relabelled with the curated alias that *this CIK's own security* held
on *that bar's date*. A bar on a date no such alias covers keeps a symbol that
cannot resolve and is rejected as ``NO_ALIAS`` -- counted, never forced in. The
CIK itself is Sharadar's assertion, tested in the probe against the independent
EDGAR exit date; the per-bar interval check then refuses anything outside the
registrant's curated life, which is what stops a successor's bars.

**Resumable, committed before checkpoint**, for the reason
``tradeit.research01.backfill.run_backfill`` gives: a checkpoint ahead of its
commit silently loses data on an interruption.

Staged the way the owner asked: ``--limit 5``, then ``--limit 100``, then all.
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

from research01_probe_sharadar import OUT, PAGE, _get, _key, _rows
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.acquisition.redaction import redact_text
from tradeit.research01.actions import import_corporate_actions
from tradeit.research01.importer import Delivery, import_price_bars
from tradeit.research01.sharadar_client import parse_sep_bars, parse_splits
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityPriceFact,
    SymbolAlias,
)

PROGRESS = OUT / "sharadar_backfill_progress.json"
FAILURES = OUT / "sharadar_backfill_failures.json"
#: A label that no alias row can carry, so an uncovered bar is rejected by the
#: importer rather than dropped here where nobody would count it.
UNCOVERED = "~uncovered~"


def _aliases(session: Session, cik: int) -> list[tuple[str, dt.date, dt.date | None]]:
    rows = session.execute(
        select(SymbolAlias.alias_value, SymbolAlias.valid_from, SymbolAlias.valid_to)
        .join(Security, Security.security_id == SymbolAlias.security_id)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .where(
            IssuerIdentifier.namespace == "sec_cik",
            IssuerIdentifier.value_normalized == str(cik),
            SymbolAlias.alias_kind == "ticker",
        )
    ).all()
    return [(value, start, end) for value, start, end in rows]


def _label(aliases: list[tuple[str, dt.date, dt.date | None]], on: dt.date) -> str:
    covering = {v for v, start, end in aliases if start <= on and (end is None or end > on)}
    # Two of this registrant's own aliases on one date is ambiguity the importer
    # must see, so the bar keeps an unresolvable label rather than a guessed one.
    return covering.pop() if len(covering) == 1 else UNCOVERED


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.4)
    args = ap.parse_args()

    key = _key()
    if not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2
    exits_payload = json.loads((OUT / "dated_exits.json").read_text())
    resolved = {int(c) for c in exits_payload["resolved"]}
    probe: dict[int, dict] = {}
    for line in (OUT / "sharadar_price_probe.jsonl").read_text().splitlines():
        record = json.loads(line)
        probe[record["cik"]] = record
    targets = sorted(
        (r for r in probe.values() if r["grade"] == "PLAUSIBLE" and r["cik"] in resolved),
        key=lambda r: r["cik"],
    )
    completed: set[int] = (
        set(json.loads(PROGRESS.read_text())["completed"]) if PROGRESS.exists() else set()
    )
    todo = [t for t in targets if t["cik"] not in completed][: args.limit]
    print(
        f"targets with curated identity: {len(targets):,}; completed {len(completed):,}; "
        f"this run {len(todo):,}"
    )

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    delivery = Delivery(
        vendor="sharadar",
        delivered_at=dt.datetime.now(dt.UTC),
        filename="sharadar-backfill",
    )
    before = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    tally: collections.Counter[str] = collections.Counter()
    failures: list[dict] = json.loads(FAILURES.read_text()) if FAILURES.exists() else []
    for n, target in enumerate(todo, 1):
        cik, vendor_ticker = target["cik"], target["ticker"]
        aliases = _aliases(session, cik)
        if not aliases:
            tally["no_curated_alias"] += 1
            failures.append({"cik": cik, "why": "no curated ticker alias"})
            completed.add(cik)
            continue
        status, text = _get(f"stocks?ticker={vendor_ticker}&limit={PAGE}", key)
        a_status, a_text = _get(f"actions?ticker={vendor_ticker}&limit={PAGE}", key)
        if status != 200 or a_status != 200:
            failures.append(
                {"cik": cik, "why": redact_text(f"HTTP {status}/{a_status} {text[:120]}", key)}
            )
            tally["http_failure"] += 1
            continue
        rows = _rows(text)
        bars = []
        for bar in parse_sep_bars(UNCOVERED, rows):
            label = _label(aliases, bar.session_date)
            bars.append(bar if label == UNCOVERED else _relabel(bar, label))
        splits = [
            _relabel(s, _label(aliases, s.ex_date)) for s in parse_splits(UNCOVERED, _rows(a_text))
        ]
        landed = import_price_bars(session, bars, delivery)
        acted = import_corporate_actions(session, splits, delivery)
        session.commit()
        completed.add(cik)
        PROGRESS.write_text(json.dumps({"completed": sorted(completed)}))
        tally["securities"] += 1
        tally["bars_landed"] += landed.landed
        tally["splits_landed"] += acted.landed
        for reason, count in landed.summary()["rejected_by_reason"].items():  # type: ignore[union-attr]
            tally[f"bars_rejected_{reason}"] += count
        if n % 25 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}  {dict(tally)}", flush=True)
        time.sleep(args.interval)
    FAILURES.write_text(json.dumps(failures, indent=1))
    after = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    print(
        f"\n{json.dumps(dict(tally), indent=1)}\n"
        f"price facts {before:,} -> {after:,} (+{after - before:,})"
    )
    return 0


def _relabel(item, label):  # type: ignore[no-untyped-def]
    """A frozen dataclass with its ticker replaced -- nothing else touched."""
    import dataclasses

    return dataclasses.replace(item, ticker=label)


if __name__ == "__main__":
    raise SystemExit(main())
