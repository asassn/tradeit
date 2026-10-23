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

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.acquisition.eodhd import EodhdAcquisition
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.tiingo import TiingoAcquisition
from tradeit.forward import capture_forward
from tradeit.forward.identity import TickerAlias, resolve_as_of
from tradeit.forward.sessions import latest_session_at_or_before, market_today
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import Base


def _as_date(raw: object) -> dt.date:
    """SQLite returns a DATE column as a string; Postgres returns a date."""
    return raw if isinstance(raw, dt.date) else dt.date.fromisoformat(str(raw)[:10])


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
        "--source",
        default="tiingo",
        choices=("tiingo", "eodhd"),
        help="which vendor to ask; one per run, because their adjustment bases differ",
    )
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

    # **The exchange's calendar, not UTC.** This line used to read
    # `dt.datetime.now(dt.UTC).date()`, with a comment saying UTC was chosen so
    # that "a capture run near midnight local time must not disagree with the
    # session it says it asked about". The reasoning was inverted: the job fires
    # at 22:10 America/New_York, which is already tomorrow in UTC, so every
    # scheduled run asked for a window ending tomorrow, got today's bar, and
    # recorded the observation under tomorrow's date. Measured 2026-10-22 at
    # 22:13 EDT -- bars on 2026-10-22, observations on 2026-10-23.
    #
    # Snapped to a real session below, once the corpus calendar is open.
    requested_end = dt.date.fromisoformat(args.end) if args.end else market_today()
    symbols = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]

    # **One vendor per run, never pooled.** Tiingo declares `RAW_UNADJUSTED`
    # and EODHD declares `UNKNOWN`, and a series mixing the two bases is the
    # §0.1a defect reintroduced through the back door -- a price whose
    # adjustment state depends on which vendor happened to answer that day.
    # Each security is assigned to exactly one vendor in `ops/`, the bars carry
    # `source`, and the two records stay separable forever.
    source = {"tiingo": TiingoAcquisition, "eodhd": EodhdAcquisition}[args.source]()
    if not source.has_credential():
        raise SystemExit(
            f"no {args.source} credential. The forward record is the one thing that "
            "cannot be caught up later -- a day not captured is gone -- so this "
            "refuses rather than writing an empty run that looks like a quiet market."
        )
    print(f"source   : {args.source} ({source.credential_hint()}), {source.adjustment_policy()}")
    print(f"record   : {args.record}")
    print(f"journal  : {args.journal}")

    # Resolved from the corpus, read-only, so a forward bar lands under the same
    # id as its history. Without this the two records could never be joined and
    # the forward data would start a second, parallel universe of identifiers.
    corpus: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.corpus, future=True)),
        future=True,
    )()
    # **Resolved as of `end`, not by ticker.** `_universe` is still what the
    # backtest scripts use and it has no date filter: it returns the *earliest*
    # holder of a ticker, which for 1,471 of the corpus's 17,342 reused tickers
    # answers a question nobody asked. It also returned an id for `ABAX` and
    # `AUTO`, which no security has held since 2018 and 2022 -- so the capture
    # asked Tiingo about them daily and wrote `empty`, which is a dated 2026
    # delisting claim about tickers that were already gone. See
    # `tradeit.forward.identity` for the measurement and the reasoning.
    with corpus:
        # **Snapped to the latest real session at or before the market-local
        # date.** On a Saturday the vendor still returns Friday's bar for a
        # window ending Saturday, so an unsnapped run would assert a Saturday
        # session that had a price -- and, on a universe with a genuine exit, a
        # non-existent session in which a company was silent. `trading_sessions`
        # is a materialised table precisely because an exchange calendar cannot
        # be expressed in SQL (§3a); it spans 1990-01-02 to 2030-12-31.
        end = latest_session_at_or_before(
            (
                dt.date.fromisoformat(str(row[0])[:10])
                for row in corpus.execute(
                    text(
                        "select session_date from trading_sessions "
                        "where session_date <= :d order by session_date desc limit 1"
                    ),
                    {"d": requested_end.isoformat()},
                ).all()
            ),
            requested_end,
        )
        if end != requested_end:
            print(f"session  : {requested_end} is not a session; using {end}")
        start = (
            dt.date.fromisoformat(args.start)
            if args.start
            else end - dt.timedelta(days=args.lookback)
        )
        print(f"window   : {start} .. {end}   symbols: {len(symbols)}")

        aliases = [
            TickerAlias(
                ticker=str(row[0]),
                security_id=int(row[1]),
                valid_from=_as_date(row[2]),
                valid_to=_as_date(row[3]) if row[3] else None,
            )
            for row in corpus.execute(
                text(
                    "select alias_value, security_id, valid_from, valid_to "
                    "from symbol_aliases where alias_kind='ticker' and alias_value in :t"
                ).bindparams(bindparam("t", value=list(symbols), expanding=True))
            ).all()
        ]
    # The record is opened *before* resolution, because the exclusion rule below
    # has to consult what has already been observed.
    engine = create_engine(f"sqlite:///{args.record}", future=True)
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine, future=True)()
    ever_printed = {
        str(row[0])
        for row in session.execute(
            text("select distinct symbol from forward_observations where rows_returned > 0")
        ).all()
    }

    instrument_ids, unresolved = resolve_as_of(aliases, symbols, as_of=end)

    # **A symbol that has already printed is never excluded**, whatever the
    # corpus now says about its ticker.
    #
    # Without this the guard becomes a trap. `research-01` is a frozen corpus,
    # so it will not learn that a company delisted next year -- but the moment
    # it is refreshed, a ticker whose alias has just been closed would start
    # resolving to nothing, and the capture would stop asking about it **exactly
    # as its exit was being observed.** The delisting would vanish from the
    # record as a gap rather than appear in it as a silence, which is the one
    # outcome this entire record exists to prevent, and it would do so quietly.
    #
    # So the rule is narrower than "resolvable": a symbol with no bar ever
    # recorded has no baseline, and its silence says nothing. A symbol that
    # printed and then went quiet is an observation, and it keeps being asked
    # about under the id it already has.
    # No id is recovered for a kept symbol, and none is needed: the case being
    # protected is a symbol that has gone *silent*, and a silence produces no
    # bar to file. If it prints again the existing "no id for" report names it,
    # and `forward_observations` does not carry an instrument id at all -- the
    # observation is the evidence, and it stands on the symbol and the date.
    kept = sorted(s for s in unresolved if s in ever_printed)
    for symbol in kept:
        del unresolved[symbol]

    print(f"ids      : {len(instrument_ids)} of {len(symbols)} resolved as of {end}")
    for symbol in kept:
        print(f"  kept     : {symbol} (already printed; its silence is evidence)")
    if unresolved:
        # **Dropped from the ask, not asked and recorded.** A ticker nobody
        # holds on this date is a fact about neither the vendor nor the market,
        # so there is no honest observation to write for it. Reported here,
        # where a universe problem belongs.
        for ticker, reason in sorted(unresolved.items()):
            print(f"  not asked: {ticker} ({reason})")
        symbols = [s for s in symbols if s not in unresolved]
        print(f"  asking   : {len(symbols)} symbols after exclusions")
    if not symbols:
        print("no resolvable symbols; refusing to record a capture of nothing")
        return 2

    # **Planned after resolution**, so the count is what will actually be
    # asked. It used to be computed first and `--dry-run` returned before the
    # corpus was touched, which meant a dry run could not show an excluded
    # ticker -- the one thing it most needs to show, since an exclusion is a
    # universe defect and the whole point of a dry run is to find those for
    # free.
    requests = source.plan(symbols, start, end)
    credits = sum(source.credits_for(request) for request in requests)
    print(f"planned  : {len(requests)} requests, {credits} credits")
    if args.dry_run:
        print("\n--dry-run: nothing fetched, nothing written.")
        return 0

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
        provider=args.source,
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
