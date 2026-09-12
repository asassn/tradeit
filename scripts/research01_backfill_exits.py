#!/usr/bin/env python
"""Price the dated exits whose identity is already resolved.

**The run that moves the gate, and the first target in this project that is both
concrete and reachable.** Measured 2026-09-12: 21,618 dated Exchange Act exits,
12,307 with an identity resolved, 8,079 priced. The 4,228 in between hold a
ticker and no prices, and coverage reaches 45% -- the grade at which the
standing rule lifts -- at roughly 1,650 of them.

It differs from ``research01_backfill_dead.py`` in what it selects. That script
takes aliases resolved from filing text; this one takes **the gate's own dated
exits**, dumped by ``research01_gate.py --dump-exits`` so the target set and the
measured set cannot drift apart.

**Every request is bounded to the registrant's own lifetime**, for the reason
the identity work exists: a dead company's plain ticker is frequently held by a
different company today, and an unbounded request returns the successor's bars
under the dead registrant's symbol. ``resolve_security`` would reject them on
the way in -- rejection is detection -- but not asking is prevention.

* **ceiling**: the last day the alias owns the ticker (``valid_to`` is
  exclusive, so the day before it), or, where the alias is open-ended, the
  registrant's own exit date. An open-ended alias on a dead registrant is a
  mapping that was never closed, not a company still trading.
* **floor**: the registrant's first EDGAR filing, since a US issuer registers
  before it lists -- except where that date is 1994, which is when the archive
  starts rather than when the company did.
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
from tradeit.storage.tables import IssuerIdentifier, Security, SecurityPriceFact, SymbolAlias

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
FLOOR = dt.date(1990, 1, 1)
ARCHIVE_START_YEAR = 1994


def build_windows(
    session: Session, exits: dict[int, dt.date], priced: set[int], first_seen: dict[int, dt.date]
) -> tuple[dict[str, tuple[dt.date, dt.date]], dict[str, int]]:
    """Symbol -> (floor, ceiling) for every dated exit that has a ticker and no prices."""
    rows = session.execute(
        select(
            IssuerIdentifier.value_normalized,
            SymbolAlias.alias_value,
            SymbolAlias.valid_from,
            SymbolAlias.valid_to,
            SymbolAlias.security_id,
        )
        .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
        .join(SymbolAlias, SymbolAlias.security_id == Security.security_id)
        .where(IssuerIdentifier.namespace == "sec_cik", SymbolAlias.alias_kind == "ticker")
    ).all()

    windows: dict[str, tuple[dt.date, dt.date]] = {}
    tally = {"aliases": 0, "not_an_exit": 0, "already_priced": 0, "open_ended": 0, "floored": 0}
    for cik_text, ticker, _valid_from, valid_to, _security_id in rows:
        tally["aliases"] += 1
        cik = int(cik_text)
        exit_date = exits.get(cik)
        if exit_date is None:
            tally["not_an_exit"] += 1
            continue
        if cik in priced:
            tally["already_priced"] += 1
            continue
        if valid_to is not None:
            ceiling = valid_to - dt.timedelta(days=1)
        else:
            # Never closed, and the registrant is dead: bound at its own exit.
            ceiling = exit_date
            tally["open_ended"] += 1
        registered = first_seen.get(cik)
        if registered is not None and registered.year > ARCHIVE_START_YEAR:
            floor = max(FLOOR, registered)
            tally["floored"] += 1
        else:
            floor = FLOOR
        if ceiling <= floor:
            continue
        symbol = f"{ticker}.US"
        # A symbol claimed by two registrants in different eras needs a window
        # covering both; per-bar resolution decides which owns each bar.
        existing = windows.get(symbol)
        windows[symbol] = (
            (floor, ceiling)
            if existing is None
            else (min(existing[0], floor), max(existing[1], ceiling))
        )
    return windows, tally


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--exits", type=Path, default=OUT / "dated_exits.json")
    ap.add_argument("--edgar-cache", type=Path, default=OUT / "edgar_facts.json")
    ap.add_argument("--progress", type=Path, default=OUT / "exit_backfill_progress.json")
    ap.add_argument("--failures", type=Path, default=OUT / "exit_backfill_failures.json")
    ap.add_argument("--limit", type=int, default=0, help="symbols this run; 0 means all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = json.loads(args.exits.read_text())
    exits = {int(k): dt.date.fromisoformat(v) for k, v in payload["exits"].items()}
    priced = set(payload["priced"])
    first_seen = {
        int(cik): dt.date.fromisoformat(value)
        for cik, value in json.loads(args.edgar_cache.read_text())["first_seen"].items()
    }
    print(f"dated exits in scope: {len(exits):,}; already priced: {len(priced):,}")

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    windows, tally = build_windows(session, exits, priced, first_seen)
    print(f"ticker aliases examined: {tally['aliases']:,}")
    print(f"  not a dated exit in scope : {tally['not_an_exit']:,}")
    print(f"  exit already priced       : {tally['already_priced']:,}")
    print(f"  open-ended alias, bounded at the exit date: {tally['open_ended']:,}")
    print(f"  floored at the first filing: {tally['floored']:,}")
    print(f"symbols to fetch: {len(windows):,}")

    progress = BackfillProgress.load(args.progress)
    todo = [s for s in sorted(windows) if s not in progress.completed]
    if args.limit:
        todo = todo[: args.limit]
    print(f"already completed: {len(progress.completed):,}; fetching {len(todo):,} this run")
    if args.dry_run:
        for symbol in todo[:10]:
            start, end = windows[symbol]
            print(f"  would GET {symbol:<12} {start} .. {end}")
        return 0
    if not todo:
        return 0

    plan = BackfillPlan(
        symbols=tuple(todo),
        start=FLOOR,
        end=dt.datetime.now(dt.UTC).date(),
        windows={s: windows[s] for s in todo},
    )
    client = HttpEodhdClient(api_token=resolve_api_token(env_file=Path(".env")))
    before = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    report = run_backfill(session, client, plan, progress)
    after = session.scalar(select(func.count()).select_from(SecurityPriceFact)) or 0
    print("\n" + json.dumps(report.summary(), indent=1, default=str))
    print(f"price facts {before:,} -> {after:,}  (+{after - before:,})")
    if report.failures:
        args.failures.write_text(json.dumps(report.failures, indent=1, default=str))
        print(f"  failures -> {args.failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
