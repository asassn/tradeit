#!/usr/bin/env python
"""Forward returns for every candidate the gate ruled on -- the PRIMARY test.

``docs/prereg/OBV_TREND_GATE_2026-09-12.md`` makes the candidate stream the
primary test and the portfolio the secondary one, because a gate's claim is
about the candidates it refuses and eight paired portfolio differences cannot
resolve an effect of a few points a year.

This joins each recorded decision -- sample, session, security, `obv_trend`
percentile, admitted or refused -- to the forward return of that security from
that session's close, under the convention every prior section used: close to
close, and only where the forward bar is served with positive volume
(`signal_research_volume_accumulation.py`). A security whose series simply ends
inside the horizon is written out separately with its last traded close, so a
reader can see whether deaths carry the result rather than having to assume.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HORIZONS = (21, 63)
#: A series whose last served bar falls this many sessions or more before the
#: window's end stopped trading; one that runs to the end is merely truncated by
#: the window, which is a different fact and must not be priced as an exit.
TRUNCATION_SLACK = 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tag", default="obvgate")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--start", default="2020-01-02")
    ap.add_argument("--end", default="2024-12-31")
    args = ap.parse_args()

    end = dt.date.fromisoformat(args.end)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    path = OUT / f"{args.tag}_candidates.csv"
    written = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sample",
                "session_date",
                "security_id",
                "rank",
                "admitted",
                *(f"{h}" for h in HORIZONS),
                *(f"imputed_{h}" for h in HORIZONS),
            ]
        )
        for k in range(args.samples):
            decisions = OUT / f"{args.tag}_decisions_{k}.csv"
            rows = list(csv.DictReader(decisions.open()))
            cache: dict[int, tuple[list[dt.date], list[float], list[float]]] = {}
            for row in rows:
                sid = int(row["instrument_id"])
                if sid not in cache:
                    bars = price_series(
                        session,
                        sid,
                        as_of=as_of,
                        start=dt.date.fromisoformat(args.start),
                        end=end,
                    )
                    cache[sid] = (
                        [b.session_date for b in bars],
                        [float(b.close) for b in bars],
                        [float(b.volume) for b in bars],
                    )
                dates, closes, volumes = cache[sid]
                day = dt.date.fromisoformat(row["session_date"])
                try:
                    i = dates.index(day)
                except ValueError:
                    continue
                if closes[i] <= 0:
                    continue
                # Did the series stop, or did the window? Only the first is an exit.
                stopped = dates[-1] < end and (end - dates[-1]).days > TRUNCATION_SLACK
                forward: list[str] = []
                imputed: list[int] = []
                for h in HORIZONS:
                    j = i + h
                    if j < len(closes) and volumes[j] > 0:
                        forward.append(f"{closes[j] / closes[i] - 1.0:.8f}")
                        imputed.append(0)
                    elif stopped and j >= len(closes):
                        # Ran off the end of a series that stopped trading: the
                        # last traded close, flagged so the registered view can
                        # drop it and the recovery views can price it.
                        forward.append(f"{closes[-1] / closes[i] - 1.0:.8f}")
                        imputed.append(1)
                    else:
                        forward.append("")
                        imputed.append(0)
                if not any(forward):
                    continue
                writer.writerow(
                    [k, day.isoformat(), sid, row["rank"], row["admitted"], *forward, *imputed]
                )
                written += 1
            print(f"  sample {k}: {len(rows):,} decisions, {written:,} rows so far", flush=True)
    print(f"{written:,} candidate observations -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
