#!/usr/bin/env python
"""Stage A of the pattern program: conditional probabilities, no claims.

``PATTERN_PROGRAM.md``. For every entry the scan produced, this records what a
trader would have lived — at **five horizons**, tagged by **market regime**, and
beside a **placebo** drawn from the same session. It tests nothing and charges
no trial. Its output is a fact about 2010-2019, and the program's rule is that
an atlas number may never be quoted as evidence.

Three things the earlier pattern work did not do, and the owner was right about
all three:

* **regime**: every prior probability pooled a decade that was 80% uptrend by
  this corpus's own index, so "a bull flag works" and "the market rose" were
  never separated;
* **horizon**: everything was 63 sessions, which is one arbitrary choice out of
  many a trader might hold for;
* **the stop**: reported both ways here, because "did it continue" and "did it
  continue without first taking me out" are different questions and the second
  is the one a stop-using trader lives.

**Items 21-40, the candlesticks, ride on this file and cost no scan.** The
program says they are measured as *entry filters on the structural patterns*,
not as standalone signals, and a filter is evaluated at the **signal bar** --
the session whose close triggered the entry, which is the bar before the entry
open. That bar is already loaded here, so every shape in
``tradeit.patterns.candlesticks`` is recorded per trade and the atlas can ask
whether a bull flag that broke out on a bullish engulfing did better than one
that did not. Asking it the other way round -- scanning for hammers and seeing
what follows -- is a different and much weaker question, and is not what the
owner's reference chart describes.

``--direction short`` prices the bear side. The events arrive already in real
prices from the scan's mirror (``tradeit.patterns.mirror``), and **every number
below them is computed with short arithmetic on those real prices** -- a stop
*above* the entry, a target *below* it, a profit when price falls, and a borrow
fee the long side does not pay. The mirror never reaches this file, precisely
so a mirrored long's return can never be reported as a short's.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from signal_jump_guard import load_jumps
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.patterns.candlesticks import CONTEXTUAL, SHAPES, prior_trend, shapes_at
from tradeit.patterns.mirror import borrow_cost, short_return
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

HORIZONS = (5, 10, 21, 63, 126)
SEED = 20260924
#: Annualised borrow, charged to every short. This corpus carries no borrow
#: rate and no locate, so it is an assumption, not a measurement: 3%/yr is
#: above general collateral and far below a hard-to-borrow name. The study
#: reports the sensitivity and says plainly that a security nobody will lend is
#: not expensive to short but impossible.
BORROW_ANNUAL = 0.03
#: Sessions of index history the trend regime is judged against.
REGIME_LOOKBACK = 200
#: Sessions of index returns in one volatility reading, and of readings the
#: median is taken over. 21 is a month; 252 is the year before it.
VOL_WINDOW = 21
VOL_LOOKBACK = 252
ARMS = ("closed_above", "retest_confirmed", "confirmed")
COLUMNS = (
    "direction",
    "pattern",
    "attempt",
    "arm",
    "leg",
    "trade_id",
    "security_id",
    "entry_date",
    "regime",
    "vol_regime",
    "final_state",
    "stop_fraction",
    "stopped_within_63",
    "reached_1r_first",
    *[f"up_{h}" for h in HORIZONS],
    *[f"ret_{h}" for h in HORIZONS],
    *[f"net_{h}" for h in HORIZONS],
    "signal_trend",
    *[f"cs_{name}" for name in (*SHAPES, *CONTEXTUAL)],
)


def regimes(path: str) -> dict[dt.date, tuple[str, str]]:
    """Two independent readings of the market on each session.

    **Trend** -- uptrend when the index is at or above its own 200-session
    average. Item 1's definition, unchanged, so its entries stay comparable.

    **Volatility** -- turbulent when the index's trailing 21-session realised
    volatility is above its own median over the year before it, quiet
    otherwise. Added 2026-09-24 at the owner's direction: *"i just dont think
    you are ... testing the right regimes."* Trend and volatility are not the
    same question and a decade that mostly rose contains both calm advances and
    violent ones. The median is trailing rather than whole-sample, so a session
    is never classified against volatility that had not happened yet.
    """
    rows = []
    with open(path) as handle:
        for row in csv.DictReader(handle):
            rows.append((dt.date.fromisoformat(row["session_date"]), float(row["level"])))
    levels = np.array([level for _, level in rows])
    moves = np.diff(levels) / levels[:-1]
    # Each rolling deviation is computed once and reused, rather than
    # recomputed inside the median's own window: the naive nesting is 252x21
    # deviations per session and takes minutes on a decade.
    rolling = np.full(len(rows), np.nan)
    for index in range(VOL_WINDOW, len(rows)):
        rolling[index] = moves[index - VOL_WINDOW : index].std()
    out: dict[dt.date, tuple[str, str]] = {}
    for index, (day, level) in enumerate(rows):
        if index < REGIME_LOOKBACK:
            continue
        trend = (
            "uptrend"
            if level >= float(levels[index - REGIME_LOOKBACK : index].mean())
            else "downtrend"
        )
        history = rolling[max(VOL_WINDOW, index - VOL_LOOKBACK) : index]
        history = history[np.isfinite(history)]
        vol = "unclassified"
        if np.isfinite(rolling[index]) and history.size:
            vol = "turbulent" if rolling[index] >= float(np.median(history)) else "quiet"
        out[day] = (trend, vol)
    return out


def outcomes(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start: int,
    entry: float,
    stop: float,
    cost: float = 0.0010,
    short: bool = False,
) -> dict[str, str] | None:
    """What happened after entry, with and without the stop.

    ``short`` flips every inequality rather than flipping the series: the stop
    sits above the entry and is hit by a HIGH, the target sits below and is hit
    by a LOW, and the trade profits when price falls. Written out rather than
    delegated to the mirror because the mirror does not preserve returns --
    ``tradeit.patterns.mirror`` says why, and this is the file that would
    otherwise get it wrong.
    """
    risk = stop - entry if short else entry - stop
    if risk <= 0 or start + 1 >= close.shape[0]:
        return None
    target = entry - risk if short else entry + risk
    out: dict[str, str] = {}
    # First touch, over the longest horizon a stop-using trader would hold.
    available = min(63, close.shape[0] - start - 1)
    first = ""
    stop_at = None
    for j in range(start + 1, start + 1 + available):
        if (high[j] >= stop) if short else (low[j] <= stop):
            first, stop_at = first or "stop", j
            break
        if (low[j] <= target) if short else (high[j] >= target):
            first = first or "target"
            break
    out["reached_1r_first"] = "1" if first == "target" else "0"
    out["stopped_within_63"] = "1" if first == "stop" else "0"
    for horizon in HORIZONS:
        if start + horizon >= close.shape[0]:
            out[f"up_{horizon}"] = out[f"ret_{horizon}"] = out[f"net_{horizon}"] = ""
            continue
        raw = float(close[start + horizon]) / entry - 1.0
        # "up" means "moved the way the trade wanted", so a short reads it
        # inverted. A column that meant one thing in one file and another in
        # the next is how a sign error survives review.
        out[f"up_{horizon}"] = "1" if (raw < 0 if short else raw > 0) else "0"
        out[f"ret_{horizon}"] = f"{-raw if short else raw:.6f}"
        # With the stop honoured: if it fired inside this horizon, the trade
        # ended there. Reported separately because a continuation probability
        # that ignores the stop describes a trade nobody took.
        held = min(horizon, stop_at - start) if stop_at is not None else horizon
        exit_price = (
            stop
            if stop_at is not None and stop_at <= start + horizon
            else float(close[start + horizon])
        )
        if short:
            # The arithmetic the unit tests cover, not a second copy of it: a
            # duplicated formula here would be the one that actually ran while
            # the tested one sat unused.
            net = short_return(entry, exit_price, cost) - borrow_cost(held, BORROW_ANNUAL)
        else:
            net = (exit_price * (1 - cost)) / (entry * (1 + cost)) - 1.0
        out[f"net_{horizon}"] = f"{net:.6f}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--events", required=True)
    ap.add_argument("--eligible", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--jumps", default="")
    ap.add_argument("--direction", choices=("long", "short"), default="long")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    short = args.direction == "short"
    regime = regimes(args.index)
    counted = Counter(regime.values())
    print(f"regime: {dict(sorted(counted.items()))}", flush=True)
    events: list[dict[str, str]] = []
    for path in args.events.split(","):
        with open(path.strip()) as handle:
            events.extend(csv.DictReader(handle))
    pool: dict[str, list[int]] = defaultdict(list)
    for path in args.eligible.split(","):
        with open(path.strip()) as handle:
            for row in csv.DictReader(handle):
                pool[row["session_date"]].append(int(row["security_id"]))

    rng = np.random.default_rng(SEED)
    legs: dict[int, list[tuple[str, str, int, dict[str, str]]]] = defaultdict(list)
    trade_id = 0
    for event in events:
        for arm in ARMS:
            day, entry = event[f"{arm}_date"], event[f"{arm}_entry"]
            # The regime table is keyed by date, and this is a string from a
            # CSV: comparing the two silently skipped every trade the first
            # time, and produced an empty file rather than an error.
            if not day or not entry or dt.date.fromisoformat(day) not in regime:
                continue
            candidates = pool.get(day)
            if not candidates:
                continue
            trade = {
                "arm": arm,
                "entry_date": day,
                "entry": entry,
                "stop": event["stop"],
                # Carried from the scan, not re-derived. Without these two the
                # eleven setups would pool into one row and the atlas would
                # answer a question nobody asked.
                "pattern": event.get("pattern", ""),
                "attempt": event.get("attempt", "1"),
                # Item 20, the bull trap, is this column. The scan already
                # records where each event ended, so "what happens after a
                # breakout that fails" is a report-time question and needs no
                # second scan.
                "final_state": event.get("final_state", ""),
            }
            legs[int(event["security_id"])].append((arm, "rule", trade_id, trade))
            legs[int(candidates[rng.integers(len(candidates))])].append(
                (arm, "placebo", trade_id, trade)
            )
            trade_id += 1
    print(f"{trade_id:,} trades over {len(legs):,} securities", flush=True)

    flagged = load_jumps(args.jumps.split(",")) if args.jumps else {}
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    as_of = dt.datetime(2019, 12, 31, 21, tzinfo=dt.UTC)
    rows: dict[int, dict[str, tuple[object, ...]]] = defaultdict(dict)
    t0 = time.time()
    for n, (security_id, wanted) in enumerate(sorted(legs.items()), 1):
        bars = price_series(
            session, security_id, as_of=as_of, start=dt.date(2009, 1, 1), end=dt.date(2019, 12, 31)
        )
        if len(bars) < 2:
            continue
        place = {b.session_date: i for i, b in enumerate(bars)}
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        close = np.array([float(b.close) for b in bars])
        open_ = np.array([float(b.open) for b in bars])
        suspect = set(flagged.get(security_id, ()))
        for arm, leg, tid, trade in wanted:
            day = dt.date.fromisoformat(trade["entry_date"])
            start = place.get(day)
            if start is None or open_[start] <= 0:
                continue
            if {b.session_date for b in bars[start : start + 1 + max(HORIZONS)]} & suspect:
                continue
            entry = float(open_[start])
            # The signal bar is the session BEFORE the entry open -- the close
            # that triggered the breakout. Reading the shape off the entry bar
            # instead would be a look-ahead: that bar is still forming when
            # the trade is placed.
            signal = start - 1
            shapes = (
                shapes_at(open_, high, low, close, signal)
                if signal >= 0
                else dict.fromkeys((*SHAPES, *CONTEXTUAL), False)
            )
            # The placebo inherits the rule's stop DISTANCE, not its price.
            reference, level = float(trade["entry"]), float(trade["stop"])
            fraction = (level - reference) / reference if short else (reference - level) / reference
            stop = entry * (1.0 + fraction) if short else entry * (1.0 - fraction)
            found = outcomes(high, low, close, start, entry, stop, short=short)
            if found is None:
                continue
            rows[tid][leg] = (
                args.direction,
                trade["pattern"],
                trade["attempt"],
                arm,
                leg,
                tid,
                security_id,
                trade["entry_date"],
                regime[day][0],
                regime[day][1],
                trade["final_state"],
                f"{fraction:.6f}",
                found["stopped_within_63"],
                found["reached_1r_first"],
                *[found[f"up_{h}"] for h in HORIZONS],
                *[found[f"ret_{h}"] for h in HORIZONS],
                *[found[f"net_{h}"] for h in HORIZONS],
                prior_trend(close, signal) if signal >= 0 else "unknown",
                *["1" if shapes[name] else "0" for name in (*SHAPES, *CONTEXTUAL)],
            )
        if n % 500 == 0 or n == len(legs):
            print(f"  {n}/{len(legs)} securities [{time.time() - t0:.0f}s]", flush=True)

    written = 0
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for tid in sorted(rows):
            pair = rows[tid]
            if "rule" in pair and "placebo" in pair:
                writer.writerow(pair["rule"])
                writer.writerow(pair["placebo"])
                written += 1
    print(f"\n{written:,} complete pairs -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
