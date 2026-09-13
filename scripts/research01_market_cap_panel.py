#!/usr/bin/env python
"""Market capitalisation joined to the §26 panel, point in time.

``docs/prereg/MARKET_CAP_2026-09-13.md``. Shares outstanding come from
``security_fundamental_facts``; the value used on a session is the LATEST fact
whose ``knowledge_time`` is on or before that session, and a session with
nothing yet knowable is dropped rather than back-filled. ``knowledge_time``
here is the SEC FSDS publication date, which lags a filer's own disclosure by
about 240 days on average -- later than reality, which is the safe direction.

The price side reuses ``voliq_observations_*`` rather than resampling: those
carry the same sessions, the same universes and the engine's own
``avg_dollar_volume_20``, so the liquidity floor and the market cap are
measured on identical rows. Close comes from the corpus through the same
``price_series`` read rule every other section used.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
#: Both are counts of common shares. Neither is free float -- dei:EntityPublicFloat
#: is absent from this corpus -- so insider and restricted shares are included.
SHARE_METRICS = ("CommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic")
HORIZONS = (21, 63)


def _share_facts(ids: list[int], db: str) -> dict[int, tuple[list[str], list[float]]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=300000")
    out: dict[int, tuple[list[str], list[float]]] = {}
    q = (
        "select security_id, date(knowledge_time), value from security_fundamental_facts "
        f"where metric in {SHARE_METRICS} and value > 0 and knowledge_time is not null "
        f"and security_id in ({','.join('?' * len(ids))}) order by security_id, knowledge_time"
    )
    for sid, kt, val in con.execute(q, ids):
        times, values = out.setdefault(sid, ([], []))
        times.append(kt)
        values.append(float(val))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="research01.sqlite")
    ap.add_argument("--source-tag", default="voliq")
    ap.add_argument("--tag", default="mcap")
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for k in range(args.samples):
        with (OUT / f"{args.source_tag}_observations_{k}.csv").open() as handle:
            rows.extend(csv.DictReader(handle))
    ids = sorted({int(r["security_id"]) for r in rows})
    facts = _share_facts(ids, args.db)
    print(
        f"{len(rows):,} source observations; {len(facts):,} of {len(ids):,} securities have shares"
    )

    end = dt.date.fromisoformat(args.end)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(f"sqlite:///{args.db}", future=True)),
        future=True,
    )()

    closes: dict[int, dict[dt.date, float]] = {}
    for n, sid in enumerate(ids, 1):
        bars = price_series(
            session, sid, as_of=as_of, start=dt.date.fromisoformat(args.start), end=end
        )
        closes[sid] = {b.session_date: float(b.close) for b in bars}
        if n % 500 == 0:
            print(f"  {n}/{len(ids)} price series", flush=True)

    path = OUT / f"{args.tag}_observations.csv"
    kept = dropped_no_fact = dropped_no_close = 0
    with path.open("w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(
            [
                "sample",
                "security_id",
                "session_date",
                "market_cap",
                "close",
                "shares",
                "avg_dollar_volume_20",
                "realized_volatility_60",
                *(str(h) for h in HORIZONS),
            ]
        )
        for r in rows:
            sid = int(r["security_id"])
            day = r["session_date"]
            f = facts.get(sid)
            if not f:
                dropped_no_fact += 1
                continue
            i = bisect.bisect_right(f[0], day)
            if i == 0:
                # Nothing was knowable on this session. Fail closed.
                dropped_no_fact += 1
                continue
            close = closes.get(sid, {}).get(dt.date.fromisoformat(day))
            if not close or close <= 0:
                dropped_no_close += 1
                continue
            shares = f[1][i - 1]
            w.writerow(
                [
                    r["sample"],
                    sid,
                    day,
                    f"{shares * close:.2f}",
                    f"{close:.6f}",
                    f"{shares:.0f}",
                    r["avg_dollar_volume_20"],
                    r["realized_volatility_60"],
                    *(r[str(h)] for h in HORIZONS),
                ]
            )
            kept += 1
    print(
        f"{kept:,} observations -> {path}\n"
        f"  dropped, no fact knowable yet: {dropped_no_fact:,}\n"
        f"  dropped, no served close:      {dropped_no_close:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
