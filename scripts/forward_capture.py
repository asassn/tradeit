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
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import _universe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.tiingo import TiingoAcquisition
from tradeit.forward import capture_forward
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import Base


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
    ap.add_argument(
        "--corpus",
        default="sqlite:///research01.sqlite",
        help=(
            "read only, and only to resolve tickers to the ids their bars are "
            "stored under, so the forward record joins to the corpus"
        ),
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

    # Resolved from the corpus, read-only, so a forward bar lands under the same
    # id as its history. Without this the two records could never be joined and
    # the forward data would start a second, parallel universe of identifiers.
    corpus: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.corpus, future=True)),
        future=True,
    )()
    with corpus:
        instrument_ids = _universe(corpus, symbols)
    missing = sorted(set(symbols) - set(instrument_ids))
    print(f"ids      : {len(instrument_ids)} of {len(symbols)} resolved from the corpus")
    if missing:
        # Named, not counted. A symbol with no id still gets its observation
        # recorded -- the survivorship record does not depend on having an id --
        # but its prices have nowhere to go, and that is worth seeing.
        print(f"  no id for: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")

    engine = create_engine(f"sqlite:///{args.record}", future=True)
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine, future=True)()
    journal = AcquisitionJournal(path=Path(args.journal))

    # The capture itself lives in `tradeit.forward`, not here. It is meant to
    # run unattended for years, and the behaviour that matters -- every outcome
    # recorded, including the silent ones -- is invisible when it breaks. A
    # mutant that skipped silences fails five tests there; in a script it would
    # have failed nothing.
    asked_at = dt.datetime.now(dt.UTC)
    t0 = time.time()
    report = capture_forward(
        session,
        source,
        symbols,
        start=start,
        end=end,
        asked_at=asked_at,
        instrument_ids=instrument_ids,
        journal=journal,
    )
    session.commit()

    print(f"\nfetched in {time.time() - t0:.1f}s:")
    print(report.describe())
    if not report.recorded:
        raise SystemExit(
            "the run completed and recorded nothing. An empty forward record looks "
            "exactly like a quiet market, which is the one thing it must never be "
            "mistaken for."
        )
    if not report.complete:
        raise SystemExit(
            "some requests produced no observation at all. That is a hole in the "
            "record, not a quiet market, and continuing would paper over it."
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
