#!/usr/bin/env python
"""Daily 250-session relative-strength percentiles, for the gate test.

The signal studies sampled every 21st session; a gate is consulted on **every**
session a candidate is decided, so this computes the rank for each security on
each session of the window and writes it out. The backtest then reads it rather
than recomputing, which keeps the gated and ungated arms reading identical
numbers.

Same engine path as §18 and §19 -- ``compare`` against QQQ, then
``rank_cross_section`` across the securities of that sample holding a served bar
that day. Nothing is reimplemented here; the engine is called.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_survivorship import _arms, _spans
from signal_research_relative_strength import BENCHMARK, BENCHMARK_ID, _load
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.relative_strength import RelativeStrengthEngine
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import RelativeStrengthConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
LOOKBACK = 250


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--start", default="2020-01-02")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--history-from", default="2019-01-01")
    ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--offset", type=int, required=True)
    ap.add_argument("--tag", default="rs250gate")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    history_from = dt.date.fromisoformat(args.history_from)
    survived, died = _arms(
        _spans(str(args.spans)), args.start, args.end, 250, args.cap, args.offset
    )
    universe = sorted(survived + died)
    engine = RelativeStrengthEngine(RelativeStrengthConfig())
    keep = LOOKBACK + 1

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    t0 = time.time()
    bench = _load(session, BENCHMARK_ID, history_from, end)
    series = {sid: _load(session, sid, history_from, end) for sid in universe}
    print(
        f"sample {args.offset}: {len(universe)} securities loaded in {time.time() - t0:.0f}s",
        flush=True,
    )

    path = OUT / f"{args.tag}_map_{args.offset}.csv"
    written = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["security_id", "session_date", "pct_250"])
        for day in bench.dates:
            if day < start:
                continue
            present = [sid for sid in universe if series[sid].index_of(day) is not None]
            relative: dict[int, float | None] = {}
            for sid in present:
                window = series[sid].upto(day, keep)
                comparison = engine.compare(window, {BENCHMARK: bench.between(min(window), day)})
                found = comparison.get((BENCHMARK, LOOKBACK))
                relative[sid] = None if found is None else found.relative_performance
            ranked = engine.rank_cross_section(day, present, relative)
            for sid in present:
                value = ranked.get(sid)
                if value is not None:
                    writer.writerow([sid, day.isoformat(), f"{value:.6f}"])
                    written += 1
    print(
        f"sample {args.offset}: {written:,} ranks -> {path} [{time.time() - t0:.0f}s]", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
