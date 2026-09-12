#!/usr/bin/env python
"""Daily cross-sectional `obv_trend` percentiles, for the gate test.

§24 measured high signed accumulation predicting lower returns over 21
sessions -- the same sign in eight samples across two decades. A gate acts on
a *rank* among that day's candidates, not on a raw value, so this computes the
percentile of `obv_trend` for every security on every session of the window.

The indicator comes from :class:`~tradeit.analytics.indicators.IndicatorEngine`
computed once per security over its whole series, which is causal: the kernels
only look backwards, the property `tests/unit/test_causality.py` asserts feature
by feature. The ranking is then per session across the securities holding a
served bar that day -- the survivorship control, since ranking against today's
survivors would quietly drop the companies that failed.
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

import numpy as np
from backtest_survivorship import _arms, _spans
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.indicators import IndicatorEngine
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import IndicatorConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
SIGNAL = "obv_trend"
#: A percentile over a handful of names is a number, not a rank. The same floor
#: RelativeStrengthConfig sets for its cross-sections.
MIN_PEERS = 20


class _Bar:
    __slots__ = ("close", "high", "low", "session_date", "volume")

    def __init__(self, day: dt.date, high: float, low: float, close: float, volume: float) -> None:
        self.session_date, self.high, self.low, self.close, self.volume = (
            day,
            high,
            low,
            close,
            volume,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--start", default="2020-01-02")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--history-from", default="2019-01-01")
    ap.add_argument("--cap", type=int, default=150)
    ap.add_argument("--offset", type=int, required=True)
    ap.add_argument("--tag", default="obvgate")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    history_from = dt.date.fromisoformat(args.history_from)
    survived, died = _arms(
        _spans(str(args.spans)), args.start, args.end, 250, args.cap, args.offset
    )
    universe = sorted(survived + died)
    engine = IndicatorEngine(IndicatorConfig())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)

    t0 = time.time()
    values: dict[dt.date, dict[int, float]] = {}
    used = 0
    for sid in universe:
        bars = price_series(session, sid, as_of=as_of, start=history_from, end=end)
        if len(bars) < 40:
            continue
        computed = engine.compute(
            [
                _Bar(b.session_date, float(b.high), float(b.low), float(b.close), float(b.volume))
                for b in bars
            ],
            instrument_id=sid,
        )
        series = computed.values[SIGNAL]
        used += 1
        for i, bar in enumerate(bars):
            if bar.session_date < start or not np.isfinite(series[i]):
                continue
            values.setdefault(bar.session_date, {})[sid] = float(series[i])
    print(
        f"sample {args.offset}: {used:,} securities, {len(values):,} sessions "
        f"[{time.time() - t0:.0f}s]",
        flush=True,
    )

    path = OUT / f"{args.tag}_map_{args.offset}.csv"
    written = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["security_id", "session_date", "pct_obv"])
        for day in sorted(values):
            present = values[day]
            if len(present) < MIN_PEERS:
                continue
            ids = list(present)
            ordered = np.sort(np.array([present[i] for i in ids], dtype=float))
            for sid in ids:
                # Fraction at or below, matching percentile_rank's convention.
                rank = float(np.searchsorted(ordered, present[sid], side="right")) / ordered.size
                writer.writerow([sid, day.isoformat(), f"{rank:.6f}"])
                written += 1
    print(
        f"sample {args.offset}: {written:,} ranks -> {path} [{time.time() - t0:.0f}s]", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
