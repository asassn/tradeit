#!/usr/bin/env python
"""The 24-indicator screening pass, exactly as registered.

Specification, direction for every arm, criteria, controls and the stop rule
are in ``docs/prereg/INDICATOR_SCREEN_2026-09-17.md``, committed at ``c99ace9``
with Amendment 1 at ``449a547`` -- both **before** any indicator here was
computed on any session. This module implements that document and adds nothing
to it.

**Two halves, deliberately separate.** This script *scans* -- it computes the
24 signals at every sampled session and writes them out. It does not judge.
``signal_verdict_indicators.py`` reads the file it writes and applies the four
criteria. Keeping them apart means the scan can be re-analysed without being
re-run, and means no criterion can be quietly adjusted while looking at the
numbers it is about to be applied to.

**Why the floor is computed here rather than filtered later.** The registration
puts the $1M/day liquidity floor in the specification, for §27's reason: without
it the screen rediscovers the volatility-and-coverage artefact under 24 names. A
floor applied at analysis time is a floor that could have been chosen after
seeing the result, so it is applied at the point of sampling and the rejected
count is reported.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics import kernels as k
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout

#: The registered horizon. One, not two -- see the registration's reasoning.
HORIZON = 63
#: The registered floor, in dollars of 20-session average turnover.
DOLLAR_FLOOR = 1_000_000.0
#: Bars of history required before the first sample, set by the longest
#: trailing window any of the 24 needs (percent_rank and ROC over 252) plus a
#: margin for the kernels that warm up on top of another kernel.
HISTORY = 300

#: Every arm, with the direction **declared in the registration**. The sign is
#: read from here and never from the data: ``+1`` means the registration says
#: higher is better, ``-1`` that lower is.
#:
#: The two controls are marked. They are charged to the ledger like every other
#: arm, because a control exempt from the ledger is a free look at the data.
DECLARED: dict[str, int] = {
    "tema_20_distance": +1,
    "slope_sma_50_20": +1,
    "adx_14": +1,
    "aroon_oscillator_25": +1,
    "macd_histogram_norm": +1,
    "rsi_14": -1,
    "bollinger_percent_b_20": -1,
    "cci_20": -1,
    "fib_retracement_126": -1,
    "percent_rank_close_252": +1,
    "bollinger_bandwidth_20": -1,
    "atr_contraction_10_50": +1,
    "ulcer_index_14": -1,
    "atr_percent_14": -1,  # NEGATIVE CONTROL -- the §26 effect §27 closed
    "money_flow_index_14": -1,
    "chaikin_money_flow_20": +1,
    "volume_contraction_10_50": +1,
    "rolling_vwap_distance_20": +1,
    "td_buy_setup_count": +1,
    "td_sell_setup_count": -1,
    "gap_frequency_63": -1,
    "donchian_position_20": +1,
    "rate_of_change_252": +1,  # POSITIVE CONTROL -- §18 measured t +5.52
    "rate_of_change_21": -1,
}
CONTROLS = {"atr_percent_14": "negative", "rate_of_change_252": "positive"}


@dataclass(slots=True)
class Scanned:
    """One sampled session: every signal, the forward return, and the cut."""

    security_id: int
    session_date: dt.date
    signals: dict[str, float]
    forward: float
    #: Carried per row because criterion 4 conditions on it, and a conditioning
    #: variable recomputed at analysis time is a conditioning variable that
    #: could have been chosen to suit the answer.
    atr_percent: float


def _signal_panel(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> dict[str, np.ndarray]:
    """All 24 arms as full-length series, NaN through each one's warm-up.

    Composed from :mod:`tradeit.analytics.kernels` rather than reimplemented
    here: the kernels carry the causality guarantee that ``test_causality.py``
    asserts, and an indicator computed inline in a research script has no such
    proof.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        tema20 = k.tema(close, 20)
        sma50 = k.sma(close, 50)
        _, upper, lower, bandwidth = k.bollinger_bands(close, 20)
        _, _, adx14 = k.adx(high, low, close, 14)
        _, _, macd_hist = k.macd(close)
        atr14 = k.atr(high, low, close, 14)
        vwap20 = k.rolling_vwap(high, low, close, volume, 20)
        setup = k.td_setup_count(close)

        swing_high_126 = k.rolling_max(high, 126)
        swing_low_126 = k.rolling_min(low, 126)
        channel_high = k.rolling_max(high, 20)
        channel_low = k.rolling_min(low, 20)

        span_126 = swing_high_126 - swing_low_126
        span_20 = channel_high - channel_low
        band_span = upper - lower

        return {
            "tema_20_distance": np.where(tema20 > 0, close / tema20 - 1.0, np.nan),
            "slope_sma_50_20": k.slope(sma50, 20),
            "adx_14": adx14,
            "aroon_oscillator_25": k.aroon_oscillator(high, low, 25),
            "macd_histogram_norm": np.where(close > 0, macd_hist / close, np.nan),
            "rsi_14": k.rsi(close, 14),
            "bollinger_percent_b_20": np.where(band_span > 0, (close - lower) / band_span, np.nan),
            "cci_20": k.commodity_channel_index(high, low, close, 20),
            # 0 at the swing high, 1 at the swing low: how far the security has
            # given back. Declared NEGATIVE -- deeper is weaker.
            "fib_retracement_126": np.where(
                span_126 > 0, (swing_high_126 - close) / span_126, np.nan
            ),
            "percent_rank_close_252": k.percent_rank(close, 252),
            "bollinger_bandwidth_20": bandwidth,
            "atr_contraction_10_50": k.contraction_ratio(atr14, 10, 50),
            "ulcer_index_14": k.ulcer_index(close, 14),
            "atr_percent_14": k.atr_percent(high, low, close, 14),
            "money_flow_index_14": k.money_flow_index(high, low, close, volume, 14),
            "chaikin_money_flow_20": k.chaikin_money_flow(high, low, close, volume, 20),
            "volume_contraction_10_50": k.contraction_ratio(volume, 10, 50),
            "rolling_vwap_distance_20": np.where(vwap20 > 0, close / vwap20 - 1.0, np.nan),
            # Signed count split into its two arms, each declared separately.
            "td_buy_setup_count": np.maximum(setup, 0.0),
            "td_sell_setup_count": np.maximum(-setup, 0.0),
            "gap_frequency_63": k.gap_frequency(open_, close, 63, 0.02),
            "donchian_position_20": np.where(span_20 > 0, (close - channel_low) / span_20, np.nan),
            "rate_of_change_252": k.rate_of_change(close, 252),
            "rate_of_change_21": k.rate_of_change(close, 21),
        }


def _scan_security(
    session: Session,
    security_id: int,
    start: dt.date,
    end: dt.date,
    as_of: dt.datetime,
    stride: int,
) -> tuple[list[Scanned], dict[str, int]]:
    """Sample one security's decade, applying the floor and the endpoint rule."""
    tally: dict[str, int] = {"sampled": 0, "below_floor": 0, "untraded_endpoint": 0}
    bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
    if len(bars) < HISTORY + HORIZON + 1:
        return [], tally

    open_ = np.array([float(b.open) for b in bars])
    high = np.array([float(b.high) for b in bars])
    low = np.array([float(b.low) for b in bars])
    close = np.array([float(b.close) for b in bars])
    volume = np.array([float(b.volume) for b in bars])

    panel = _signal_panel(open_, high, low, close, volume)
    turnover = k.average_dollar_volume(high, low, close, volume, 20)

    out: list[Scanned] = []
    for i in range(HISTORY - 1, len(bars) - HORIZON, stride):
        if not np.isfinite(turnover[i]) or turnover[i] < DOLLAR_FLOOR:
            tally["below_floor"] += 1
            continue
        if volume[i] <= 0 or volume[i + HORIZON] <= 0:
            tally["untraded_endpoint"] += 1
            continue
        if close[i] <= 0 or close[i + HORIZON] <= 0:
            tally["untraded_endpoint"] += 1
            continue
        values = {name: float(series[i]) for name, series in panel.items()}
        if any(not np.isfinite(v) for v in values.values()):
            # An arm still in its warm-up would otherwise be scored on a
            # substituted number. Dropping the whole row keeps all 24 arms on
            # an identical sample, which is what makes their t-statistics
            # comparable to each other and to one hurdle.
            continue
        tally["sampled"] += 1
        out.append(
            Scanned(
                security_id=security_id,
                session_date=bars[i].session_date,
                signals=values,
                forward=float(close[i + HORIZON] / close[i] - 1.0),
                atr_percent=float(panel["atr_percent_14"][i]),
            )
        )
    return out, tally


def _universe(path: str, start: str, end: str, min_bars: int, cap: int) -> list[int]:
    """Identical construction to every prior run, so results are comparable."""
    with open(path) as handle:
        spans = [
            (int(sid), first, last, int(count)) for sid, first, last, count in csv.reader(handle)
        ]
    alive = [row for row in spans if row[1] <= start <= row[2] and row[3] >= min_bars]
    died = sorted(row[0] for row in alive if row[2] < end)
    survived = sorted(row[0] for row in alive if row[2] >= end)

    def thin(ids: list[int], limit: int) -> list[int]:
        if len(ids) <= limit:
            return ids
        step = len(ids) / limit
        return [ids[int(i * step)] for i in range(limit)]

    chosen = sorted(thin(survived, cap) + thin(died, cap))
    print(
        f"universe: {len(chosen):,} securities "
        f"({len(thin(survived, cap)):,} survived, {len(thin(died, cap)):,} died)"
    )
    return chosen


def _write(path: str, rows: Sequence[Scanned]) -> None:
    names = list(DECLARED)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["security_id", "session_date", "forward", "atr_percent", *names])
        for row in rows:
            writer.writerow(
                [
                    row.security_id,
                    row.session_date.isoformat(),
                    f"{row.forward:.8f}",
                    f"{row.atr_percent:.8f}",
                    *[f"{row.signals[n]:.8f}" for n in names],
                ]
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=5000, help="securities per arm")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--limit", type=int, default=0, help="stop after N securities (staging)")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
        print(f"  shard {args.shard}/{args.shards}: {len(universe):,} securities")
    if args.limit:
        universe = universe[: args.limit]
        print(f"  staged run: first {len(universe):,} securities")

    print(f"{len(DECLARED)} arms, horizon {HORIZON}, floor ${DOLLAR_FLOOR:,.0f}/day")
    t0 = time.time()
    rows: list[Scanned] = []
    totals = {"sampled": 0, "below_floor": 0, "untraded_endpoint": 0}
    used = 0
    for n, security_id in enumerate(universe, 1):
        got, tally = _scan_security(session, security_id, start, end, as_of, args.stride)
        for key, value in tally.items():
            totals[key] += value
        if got:
            used += 1
        rows.extend(got)
        if n % 100 == 0 or n == len(universe):
            print(
                f"  {n}/{len(universe)} securities  {len(rows):,} observations  "
                f"[{time.time() - t0:.0f}s]",
                flush=True,
            )

    _write(args.out, rows)
    print(
        f"\n{len(rows):,} observations from {used:,} securities\n"
        f"  rejected below the ${DOLLAR_FLOOR:,.0f} floor : {totals['below_floor']:,}\n"
        f"  rejected for an untraded endpoint  : {totals['untraded_endpoint']:,}\n"
        f"  -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
