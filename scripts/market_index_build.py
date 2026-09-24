#!/usr/bin/env python
"""Build this corpus's own equal-weighted market index, once, for the program.

``PATTERN_PROGRAM.md`` breaks every atlas entry down by market regime, and this
project has never had a market series to condition on. §49 built one inside a
strategy and threw it away at the end of the run; this writes it down.

**Eligible securities only, and returns winsorised.** The first version of this
script used every security and an unwinsorised mean, and the index reached
**1e54** -- destroyed by sub-penny names and the §0.10 discontinuities, exactly
the defects this corpus documents. An equal-weighted mean of returns is as
fragile as the worst row in it. So the index now takes only securities clearing
the studies' own floors -- $5, $1m a day, ATR <= 1.0 -- and caps each daily
return at +/-20%, which a liquid security almost never exceeds and which bounds
what any single bad print can do to a whole session.

**Equal-weighted, from returns, from the corpus itself.** Each session's level
moves by the mean one-session return of every security that traded that session
and the one before it. From returns rather than from a mean price, so a $500
security and a $5 one move it by the same percent; from this corpus rather than
from a bought index, so the population being conditioned on is the population
being measured -- including the securities that later died, which an index
vendor would have dropped.

**The survivorship note that travels with it.** The index inherits this corpus's
coverage: 52% of dated exits are priced (§7e), so a decline that killed
companies we cannot price is understated. That biases the index *up* in exactly
the periods a regime test cares about, and it is recorded rather than corrected.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import SecurityPriceFact

#: The studies' own floors, so the index describes the population they trade.
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 1_000_000.0
MAX_ATR_PERCENT = 1.0
#: A liquid security almost never moves this much in a session, and capping
#: bounds what one surviving bad print can do to a whole day's index.
WINSOR = 0.20


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--start", default="2009-01-02")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    ids = [
        row[0]
        for row in session.execute(
            select(SecurityPriceFact.security_id).distinct().order_by(SecurityPriceFact.security_id)
        ).all()
    ]
    ids = [s for n, s in enumerate(ids) if n % args.shards == args.shard]
    print(f"shard {args.shard}: {len(ids):,} securities", flush=True)

    total: dict[dt.date, float] = defaultdict(float)
    count: dict[dt.date, int] = defaultdict(int)
    t0 = time.time()
    for n, security_id in enumerate(ids, 1):
        bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
        if len(bars) < 30:
            continue
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        close = np.array([float(b.close) for b in bars])
        volume = np.array([float(b.volume) for b in bars])
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = k.atr_percent(high, low, close, 14)
            turnover = k.average_dollar_volume(high, low, close, volume, 20)
        eligible = (
            (close >= MIN_PRICE)
            & (volume > 0)
            & np.isfinite(turnover)
            & (turnover >= MIN_DOLLAR_VOLUME)
            & np.isfinite(atr_pct)
            & (atr_pct <= MAX_ATR_PERCENT)
        )
        previous = None
        for index, bar in enumerate(bars):
            price = float(close[index])
            if price > 0 and previous is not None and previous > 0 and eligible[index]:
                move = min(WINSOR, max(-WINSOR, price / previous - 1.0))
                total[bar.session_date] += move
                count[bar.session_date] += 1
            if price > 0:
                previous = price
        if n % 500 == 0 or n == len(ids):
            print(f"  {n}/{len(ids)} [{time.time() - t0:.0f}s]", flush=True)

    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("session_date", "return_sum", "securities"))
        for day in sorted(total):
            writer.writerow((day.isoformat(), f"{total[day]:.10f}", count[day]))
    print(f"{len(total):,} sessions -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
