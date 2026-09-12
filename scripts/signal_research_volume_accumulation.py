#!/usr/bin/env python
"""``volume_accumulation`` measured through the indicator engine itself.

The last weighted scoring factor never measured directly. Its stand-in,
``relative_volume_20``, passed in sample and was rejected out of sample; the
factor has not been tested.

**This factor has no engine of its own** -- no module, no scorer, and nothing
in the codebase reads the name but the weights dict. What exists is the
indicator registry, and two of its features are accumulation claims in their
own words: ``volume_momentum`` ("change in average volume -- accumulation
building or fading") and ``obv_trend`` (net signed volume as a share of volume
traded, the usable and *signed* form of OBV). Both come from
:class:`~tradeit.analytics.indicators.IndicatorEngine`, computed by the engine
and read here, not reimplemented.

``obv_trend`` exists because looking for this factor's engine found a defect:
the registry published ``slope(abs(obv) + 1, lookback)``, which returns the
same number for a security that closed up every session and one that closed
down every session. Fixed in ``920f703`` before measuring.

Specification: ``docs/prereg/VOLUME_ACCUMULATION_2026-09-12.md``, committed
before the first run. Four disjoint samples, because §15 established that one
sample's number carries noise of the size being measured.

**Why whole-series computation is causal.** The engine's kernels only ever look
backwards -- the property `tests/unit/test_causality.py` asserts feature by
feature -- so a value at index *i* depends on bars up to *i* and no later. That
is what lets this compute each security once and then sample, instead of
recomputing a window per scan date.
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


class _Series:
    """Dates, closes and volumes as parallel lists, for the sampling loop."""

    __slots__ = ("closes", "dates", "volumes")

    def __init__(self, dates: list[dt.date], closes: list[float], volumes: list[float]) -> None:
        self.dates = dates
        self.closes = closes
        self.volumes = volumes


SIGNALS = ("volume_momentum", "obv_trend")
HORIZONS = (21, 63)


class _Bar:
    """What IndicatorEngine.compute reads, and nothing else.

    Carries the real high and low even though neither signal here reads them.
    The engine computes every registered feature, and a bar whose high and low
    were filled in with the close would hand a later reader of this file
    plausible ATR and range numbers that are fiction.
    """

    __slots__ = ("close", "high", "low", "session_date", "volume")

    def __init__(self, day: dt.date, high: float, low: float, close: float, volume: float) -> None:
        self.session_date = day
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--start", default="2000-01-04")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--history-from", default="1999-01-01")
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--offset", type=int, required=True)
    ap.add_argument("--tag", default="volacc")
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
    t0 = time.time()
    rows: list[list[object]] = []
    used = 0
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    for n, sid in enumerate(universe, 1):
        loaded = price_series(session, sid, as_of=as_of, start=history_from, end=end)
        if len(loaded) < 260:
            continue
        bars = [
            _Bar(b.session_date, float(b.high), float(b.low), float(b.close), float(b.volume))
            for b in loaded
        ]
        series = _Series(
            [b.session_date for b in bars],
            [b.close for b in bars],
            [b.volume for b in bars],
        )
        computed = engine.compute(bars, instrument_id=sid)
        values = {name: computed.values[name] for name in SIGNALS}
        used += 1
        for i, day in enumerate(series.dates):
            if day < start or i % args.stride:
                continue
            if series.volumes[i] <= 0 or series.closes[i] <= 0:
                continue
            signal = [values[name][i] for name in SIGNALS]
            if not any(np.isfinite(v) for v in signal):
                continue
            forward: list[str] = []
            for h in HORIZONS:
                j = i + h
                forward.append(
                    f"{series.closes[j] / series.closes[i] - 1.0:.8f}"
                    if j < len(series.closes) and series.volumes[j] > 0
                    else ""
                )
            if not any(forward):
                continue
            rows.append(
                [args.offset, sid, day.isoformat()]
                + [f"{v:.8f}" if np.isfinite(v) else "" for v in signal]
                + forward
            )
        if n % 200 == 0:
            print(
                f"  {n}/{len(universe)} securities  {len(rows):,} rows [{time.time() - t0:.0f}s]",
                flush=True,
            )

    path = OUT / f"{args.tag}_observations_{args.offset}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sample", "security_id", "session_date", *SIGNALS, *(str(h) for h in HORIZONS)]
        )
        writer.writerows(rows)
    print(
        f"sample {args.offset}: {used:,} securities, {len(rows):,} observations -> {path} "
        f"[{time.time() - t0:.0f}s]",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
