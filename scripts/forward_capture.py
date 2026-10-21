#!/usr/bin/env python
"""Start the forward record, where survivorship cannot go wrong.

The owner's decision of 2026-10-20: **no more spending on history.** Research
turns to evidence collected going forward instead. The arithmetic behind that
is better than it first looks and is worth stating, because it changes what
this script is for.

**Forward observations cost no ledger budget.** The 144 trials spent so far
were spent because the same fixed history was interrogated repeatedly, and
repeated looks at fixed data manufacture false positives — which is why the
hurdle keeps rising. A future that has not happened cannot be data-mined, so a
forward observation is worth more than a historical one even though there are
fewer of them. Buying more history would have made the multiple-comparisons
problem larger, not smaller.

**And survivorship is structurally impossible to get wrong going forward.** The
corpus problem that consumed months exists because a historical universe has to
be reconstructed, and the companies that failed are the ones hardest to find.
Here the universe is written down *before* anyone knows which members will
fail. A delisting is then **observed** — a symbol we asked about and got
nothing for — rather than inferred from an absence that might equally be a gap
in a vendor's coverage.

That distinction is the whole design, and the acquisition framework already
supports it: `FetchStatus` separates `EMPTY` from `REJECTED`, and `SymbolStatus`
separates `NOT_FOUND` from `UNAVAILABLE_HISTORICALLY` and from `UNKNOWN`. This
script's job is to make sure **every one of those outcomes is written down**,
including the boring ones.

Why Tiingo
----------

Measured, not chosen by preference. `EodhdAcquisition` is a deliberate stub —
`has_credential()` returns False and `fetch` returns `REJECTED` — so the EODHD
acquisition path does not exist whatever key is set. `TiingoAcquisition` is
fully implemented and declares
`AdjustmentPolicyDeclaration.RAW_UNADJUSTED`, which is what ADR-0005 requires:
raw exchange prints, never adjusted, so no future split is baked into a past
price.

What this writes, and where
---------------------------

A **separate** forward database. `research01.sqlite` is read-only evidence that
several projects depend on, and mixing a daily capture into it would make the
corpus a moving target. The forward record uses the same bitemporal schema, so
the two can be compared later without a translation layer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.acquisition.base import AcquisitionDataset
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.tiingo import TiingoAcquisition
from tradeit.storage.tables import Base

#: One row per symbol per session, recording what we asked for and what came
#: back -- including nothing. Deliberately its own table rather than a flag on
#: the price rows: a day with no price has no price row to carry a flag, and
#: that day is precisely the one survivorship turns on.
OBSERVATION_DDL = """
CREATE TABLE IF NOT EXISTS forward_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_returned INTEGER NOT NULL,
    note TEXT,
    UNIQUE (symbol, session_date, asked_at)
)
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--record",
        default="/Users/ericsasson/Documents/TradeItData/out/forward.sqlite",
        help="the forward corpus; never research01",
    )
    ap.add_argument(
        "--journal", default="/Users/ericsasson/Documents/TradeItData/out/forward_journal.jsonl"
    )
    ap.add_argument("--tickers", required=True)
    ap.add_argument(
        "--start",
        default=None,
        help="first session to ask about; defaults to --end minus --lookback",
    )
    ap.add_argument("--end", default=None, help="last session; defaults to today")
    ap.add_argument("--lookback", type=int, default=5, help="sessions when --start is omitted")
    ap.add_argument(
        "--max-symbols",
        type=int,
        default=None,
        help="stop after this many, to stay inside a free tier on the first run",
    )
    ap.add_argument("--dry-run", action="store_true", help="plan and count credits, fetch nothing")
    args = ap.parse_args()

    if Path(args.record).resolve() == Path("research01.sqlite").resolve():
        raise SystemExit("the forward record must not be written into the corpus")

    # UTC explicitly: a capture run near midnight local time must not disagree
    # with the session it says it asked about.
    end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(dt.UTC).date()
    start = (
        dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=args.lookback)
    )
    symbols = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]

    source = TiingoAcquisition()
    if not source.has_credential():
        raise SystemExit(
            "no Tiingo credential. The forward record is the one thing that cannot be "
            "caught up later -- a day not captured is gone -- so this refuses rather "
            "than writing an empty run that looks like a quiet market."
        )
    print(f"source   : tiingo ({source.credential_hint()}), {source.adjustment_policy()}")
    print(f"record   : {args.record}")
    print(f"journal  : {args.journal}")
    print(f"window   : {start} .. {end}   symbols: {len(symbols)}")

    requests = source.plan(symbols, start, end)
    credits = sum(source.credits_for(request) for request in requests)
    print(f"planned  : {len(requests)} requests, {credits} credits")
    if args.dry_run:
        print("\n--dry-run: nothing fetched, nothing written.")
        return 0

    engine = create_engine(f"sqlite:///{args.record}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(OBSERVATION_DDL))
    session: Session = sessionmaker(bind=engine, future=True)()
    journal = AcquisitionJournal(path=Path(args.journal))

    counts: Counter[str] = Counter()
    asked_at = dt.datetime.now(dt.UTC)
    t0 = time.time()
    for request in requests:
        if request.dataset is not AcquisitionDataset.DAILY_PRICES:
            continue
        outcome = source.fetch(request)
        counts[str(outcome.status)] += 1
        journal.record("tiingo", outcome)
        rows = len(outcome.rows)
        # **Every outcome is written, including EMPTY.** A symbol we asked about
        # and got nothing for is the observation a delisting is made of, and
        # skipping it would leave an absence indistinguishable from never having
        # asked -- which is exactly how a historical corpus loses its failures.
        session.execute(
            text(
                "INSERT OR IGNORE INTO forward_observations "
                "(symbol, session_date, asked_at, status, rows_returned, note) "
                "VALUES (:symbol, :session_date, :asked_at, :status, :rows, :note)"
            ),
            {
                "symbol": request.symbol,
                "session_date": end.isoformat(),
                "asked_at": asked_at.isoformat(),
                "status": str(outcome.status),
                "rows": rows,
                "note": (str(outcome.error)[:200] if outcome.error else None),
            },
        )
    session.commit()
    elapsed = time.time() - t0

    print(f"\nfetched in {elapsed:.1f}s:")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<12} {count:>5}")

    observed = session.execute(
        text("SELECT COUNT(*) FROM forward_observations WHERE asked_at = :a"),
        {"a": asked_at.isoformat()},
    ).scalar()
    silent = session.execute(
        text("SELECT COUNT(*) FROM forward_observations WHERE asked_at = :a AND rows_returned = 0"),
        {"a": asked_at.isoformat()},
    ).scalar()
    print(f"\n  {observed:,} observations written, {silent:,} of them silent")
    if not observed:
        raise SystemExit(
            "the run completed and recorded nothing. An empty forward record looks "
            "exactly like a quiet market, which is the one thing it must never be "
            "mistaken for."
        )
    print(
        "\n  A silent symbol is **recorded, not skipped.** That row is what a delisting\n"
        "  is made of: we asked on a dated day and got nothing, written down before\n"
        "  anyone knew whether it would ever print again. A historical corpus cannot\n"
        "  reconstruct that, which is why its oldest years are survivor-weighted and\n"
        "  this record's will not be."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
