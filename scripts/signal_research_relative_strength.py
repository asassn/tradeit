#!/usr/bin/env python
"""``relative_strength`` measured by its real engine, on four disjoint samples.

Every earlier test of this factor used stand-ins -- ``momentum_21``,
``momentum_126``, ``momentum_252`` -- single raw returns scored one at a time.
The engine the strategy actually uses is different in two ways that matter:

* it **ranks**: each lookback's relative performance becomes a percentile
  across the universe on that date, and ``score()`` blends the percentiles;
* it **blends four horizons**, 20/60/120/250 sessions at 0.15/0.25/0.30/0.30,
  so 15% of its weight sits on the horizon whose stand-in reversed.

So this runs :class:`~tradeit.analytics.relative_strength.RelativeStrengthEngine`
end to end -- ``compare`` against a benchmark, ``rank_cross_section`` per
lookback over the securities holding a bar that day, ``score`` -- and nothing
reimplemented alongside it. Specification: ``docs/prereg/RELATIVE_STRENGTH_2026-09-12.md``,
committed before the first run.

**The benchmark is QQQ, because SPY is not in this corpus -- and that can
matter.** The pre-registration argued it could not: a common benchmark divides
every return by the same ``(1 + r_benchmark)``, which preserves order. The
first run's own check disproved it. The engine matches the benchmark to each
security's *own* sessions, so a security with gaps is measured against the
benchmark over a different span from its neighbours, and a different benchmark
can move its rank -- 4 of ~30 on the first date checked. So the score is also
computed against a flat benchmark on the same calendar, where relative
performance reduces to each security's own aligned return, and the verdict
must hold under both (pre-registration amendment 1).

**Four samples, not one**, because §15 found a single sample's number carries
noise of the size being measured. Each sample is ranked within itself, so a
percentile means the same thing in all four and the observations can be pooled.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_survivorship import _arms, _spans
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.relative_strength import RelativeStrengthEngine
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import RelativeStrengthConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
#: QQQ's security id in research01, measured: aliased from 1990, bars from
#: 1999-03-10, trading every session of the study window.
BENCHMARK_ID = 14
BENCHMARK = "QQQ"
#: A constant series on the benchmark's calendar, passed through the engine
#: like any benchmark. The engine only computes comparisons for benchmarks named
#: in its config, so the config used here names it too.
FLAT = "FLAT"


class Series:
    """One security's served bars, as parallel sorted lists."""

    __slots__ = ("closes", "dates", "volumes")

    def __init__(self, dates: list[dt.date], closes: list[float], volumes: list[float]) -> None:
        self.dates = dates
        self.closes = closes
        self.volumes = volumes

    def upto(self, day: dt.date, keep: int) -> dict[dt.date, float]:
        """The last ``keep`` bars on or before ``day`` -- nothing after it."""
        end = bisect.bisect_right(self.dates, day)
        start = max(0, end - keep)
        return dict(zip(self.dates[start:end], self.closes[start:end], strict=True))

    def between(self, first: dt.date, last: dt.date) -> dict[dt.date, float]:
        lo = bisect.bisect_left(self.dates, first)
        hi = bisect.bisect_right(self.dates, last)
        return dict(zip(self.dates[lo:hi], self.closes[lo:hi], strict=True))

    def index_of(self, day: dt.date) -> int | None:
        i = bisect.bisect_left(self.dates, day)
        return i if i < len(self.dates) and self.dates[i] == day else None


def _load(session: Session, sid: int, start: dt.date, end: dt.date) -> Series:
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    bars = price_series(session, sid, as_of=as_of, start=start, end=end)
    return Series(
        [b.session_date for b in bars],
        [float(b.close) for b in bars],
        [float(b.volume) for b in bars],
    )


def run_sample(
    session: Session, offset: int, args: argparse.Namespace, out_path: Path
) -> tuple[int, int]:
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    # Trailing history may begin before the scan window, for the warm-up
    # only: a 250-session lookback on the window's first day needs the year
    # before it. No outcome before ``start`` is ever computed.
    history_from = dt.date.fromisoformat(args.history_from) if args.history_from else start
    survived, died = _arms(_spans(str(args.spans)), args.start, args.end, 250, args.cap, offset)
    universe = sorted(survived + died)
    # The default config -- lookbacks, weights, minimum universe -- with FLAT
    # added to the benchmark list so compare() will evaluate it. Nothing that
    # score() or rank_cross_section() reads is changed.
    config = RelativeStrengthConfig(benchmarks=(*RelativeStrengthConfig().benchmarks, FLAT))
    engine = RelativeStrengthEngine(config)
    lookbacks = config.lookbacks
    keep = max(lookbacks) + 1
    horizons = (21, 63)

    t0 = time.time()
    bench = _load(session, BENCHMARK_ID, history_from, end)
    series = {sid: _load(session, sid, history_from, end) for sid in universe}
    print(
        f"sample {offset}: {len(universe)} securities loaded in {time.time() - t0:.0f}s; "
        f"benchmark {BENCHMARK} {len(bench.dates):,} sessions",
        flush=True,
    )

    # Scan on the benchmark's calendar: every stride-th session after the
    # longest lookback's warm-up, stopping where the longest horizon still fits.
    scan_days = [day for day in bench.dates[keep :: args.stride] if day >= start]
    rows: list[list[object]] = []
    for day in scan_days:
        present = [sid for sid in universe if series[sid].index_of(day) is not None]
        comparisons = {}
        flat_comparisons = {}
        for sid in present:
            window = series[sid].upto(day, keep)
            # The benchmark must cover the security's whole window. The first
            # version gave it a fixed 291 sessions, and for a security whose
            # last 251 bars span more than that -- gaps where bars were refused
            # or trading halted -- align_series silently cut the start off the
            # security's own window. The invariance check below caught it.
            bench_window = bench.between(min(window), day)
            comparisons[sid] = engine.compare(window, {BENCHMARK: bench_window})
            flat_comparisons[sid] = engine.compare(window, {FLAT: dict.fromkeys(bench_window, 1.0)})
        percentiles: dict[int, dict[int, float | None]] = {sid: {} for sid in present}
        flat_percentiles: dict[int, dict[int, float | None]] = {sid: {} for sid in present}
        for lookback in lookbacks:
            relative = {
                sid: (
                    c[(BENCHMARK, lookback)].relative_performance
                    if (BENCHMARK, lookback) in c
                    else None
                )
                for sid, c in comparisons.items()
            }
            ranked = engine.rank_cross_section(day, present, relative)
            # The same engine under a flat benchmark on the same calendar, where
            # relative performance reduces to each security's own aligned
            # return. Pre-registration amendment 1: the verdict must hold
            # under both, because the benchmark can move a gappy security's rank.
            flat_relative = {
                sid: (c[(FLAT, lookback)].relative_performance if (FLAT, lookback) in c else None)
                for sid, c in flat_comparisons.items()
            }
            flat_ranked = engine.rank_cross_section(day, present, flat_relative)
            for sid in present:
                flat_percentiles[sid][lookback] = flat_ranked.get(sid)
            for sid in present:
                percentiles[sid][lookback] = ranked.get(sid)

        for sid in present:
            score = engine.score(percentiles[sid])
            flat_score = engine.score(flat_percentiles[sid])
            if score is None or flat_score is None:
                continue
            s = series[sid]
            i = s.index_of(day)
            assert i is not None
            if s.volumes[i] <= 0 or s.closes[i] <= 0:
                continue  # a price nobody traded at is not an entry
            forward: list[str] = []
            for h in horizons:
                j = i + h
                if j >= len(s.closes) or s.volumes[j] <= 0:
                    forward.append("")
                else:
                    forward.append(f"{s.closes[j] / s.closes[i] - 1.0:.8f}")
            if not any(forward):
                continue
            rows.append(
                [offset, sid, day.isoformat(), f"{score:.4f}", f"{flat_score:.4f}"]
                + [
                    "" if percentiles[sid].get(lb) is None else f"{percentiles[sid][lb]:.6f}"
                    for lb in lookbacks
                ]
                + [
                    ""
                    if flat_percentiles[sid].get(lb) is None
                    else f"{flat_percentiles[sid][lb]:.6f}"
                    for lb in lookbacks
                ]
                + forward
            )
    with out_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sample", "security_id", "session_date", "rs_score", "rs_flat"]
            + [f"pct_{lb}" for lb in lookbacks]
            + [f"pct_flat_{lb}" for lb in lookbacks]
            + [str(h) for h in horizons]
        )
        writer.writerows(rows)
    print(
        f"sample {offset}: {len(rows):,} observations -> {out_path} [{time.time() - t0:.0f}s]",
        flush=True,
    )
    return len(rows), len(scan_days)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--offset", type=int, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--history-from",
        default=None,
        help="load trailing bars from this date for warm-up; scan dates still begin at --start",
    )
    ap.add_argument("--tag", default="rs", help="output file prefix")
    args = ap.parse_args()
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    out = args.out or OUT / f"{args.tag}_observations_{args.offset}.csv"
    run_sample(session, args.offset, args, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
