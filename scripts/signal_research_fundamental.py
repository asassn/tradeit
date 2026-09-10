#!/usr/bin/env python
"""Does fundamental quality predict anything?

``ScoringConfig`` weights ``fundamental_quality`` at 0.15 (0.1667 after
``sector_strength`` was retired) and it has never been tested.

**The criteria are the ones ``FundamentalConfig`` already declares**, not
criteria invented here: return on equity at least ``min_return_on_equity``,
debt-to-equity at most ``max_debt_to_equity``, revenue growth at least
``min_revenue_growth_yoy``, earnings growth at least
``min_earnings_growth_yoy``. The signal is the fraction of those a security
meets, so what is on trial is the system's own definition of quality. Inventing
a nicer composite would have tested something nobody had declared.

The point-in-time problem, which is the real story
--------------------------------------------------

**These fundamentals are a backfill and were not knowable when they describe.**
Measured: ``knowledge_time`` begins in 2009 and the median gap from
``period_end`` to ``knowledge_time`` is **1,574 days**, with 97.6% exceeding
400. SEC's XBRL datasets start around 2009 and restate history, so the recorded
instant is when the corpus learned a fact, not when the market could have.

The consequence is not a caveat, it is a boundary. Securities with an annual
figure both *knowable* at the session and *fresh* (period end within two
years):

* 2005-06-30 — **0**
* 2010-06-30 — 409
* 2013-06-30 — 7,584

**So a fundamental study before roughly 2013 is not possible on this corpus at
all**, and a study that ignored ``knowledge_time`` would be reading 2016's
restatement into a 2013 decision. This runner refuses to: every figure is
bounded by knowledge time and by freshness, and the run reports how much that
costs.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import pickle
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.kernels import sma
from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.research01.series import price_series
from tradeit.signals.study import (
    Observation,
    Orientation,
    PromotionRule,
    SignalStudy,
    StudyTarget,
    TargetKind,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

#: Flow metrics are annual (duration 4); stock metrics are instants (duration 0).
FLOW = {"NetIncomeLoss": 4, "Revenues": 4}
STOCK = {"StockholdersEquity": 0, "Liabilities": 0}


def _universe(path: str, start: str, end: str, min_bars: int, cap: int) -> list[int]:
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
        f"({len(thin(survived, cap)):,} survived, {len(thin(died, cap)):,} stopped printing)"
    )
    return chosen


def _facts(
    session: Session, universe: list[int]
) -> dict[int, dict[str, list[tuple[str, str, float]]]]:
    """Per security, per metric: (knowledge_time, period_end, value), oldest first."""
    out: dict[int, dict[str, list[tuple[str, str, float]]]] = defaultdict(lambda: defaultdict(list))
    wanted = {**FLOW, **STOCK}
    statement = text(
        "select security_id, metric, knowledge_time, period_end, value "
        "from security_fundamental_facts "
        "where security_id in :ids and metric in :metrics and duration_qtrs = :dur "
        "order by security_id, metric, period_end"
    ).bindparams(bindparam("ids", expanding=True), bindparam("metrics", expanding=True))
    for duration in (4, 0):
        metrics = [m for m, d in wanted.items() if d == duration]
        for start in range(0, len(universe), 400):
            chunk = universe[start : start + 400]
            for sid, metric, kt, pe, value in session.execute(
                statement, {"ids": chunk, "metrics": metrics, "dur": duration}
            ):
                if value is None:
                    # A fact recorded with no value. Real, and not zero: the
                    # corpus holds these where a filing tagged a concept
                    # without a figure, and reading one as 0 would put a
                    # company with unknown equity at the bottom of every
                    # quality ranking. The 30-security staging run contained
                    # none, and the full run died on one after 32 minutes.
                    continue
                out[int(sid)][metric].append((str(kt)[:10], str(pe)[:10], float(value)))
    return out


def _shift_years(day: str, years: int) -> str:
    """``day`` moved back by whole years, in real calendar arithmetic.

    String surgery on the year is the obvious way and it is wrong: a period
    ending 2016-02-29 becomes "2015-02-29", which is not a date. It killed a
    32-minute run twice. Whole-year steps are approximated in days so that
    every February survives.
    """
    return (dt.date.fromisoformat(day) - dt.timedelta(days=365 * years)).isoformat()


def _latest(
    series: list[tuple[str, str, float]], as_of: str, fresh_years: int
) -> tuple[str, float] | None:
    """The newest figure knowable at ``as_of`` whose period is still fresh."""
    floor = _shift_years(as_of, fresh_years)
    best: tuple[str, float] | None = None
    for kt, pe, value in series:
        if kt > as_of or pe > as_of or pe < floor:
            continue
        if best is None or pe > best[0]:
            best = (pe, value)
    return best


def _prior(series: list[tuple[str, str, float]], as_of: str, period_end: str) -> float | None:
    """The figure one year before ``period_end``, also knowable at ``as_of``."""
    target = dt.date.fromisoformat(_shift_years(period_end, 1))
    for kt, pe, value in series:
        if kt <= as_of and abs((dt.date.fromisoformat(pe) - target).days) <= 45:
            return value
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2013-01-02")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--horizons", default="63")
    ap.add_argument("--min-observations", type=int, default=500)
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--quantile", type=float, default=0.2)
    ap.add_argument("--fresh-years", type=int, default=2)
    ap.add_argument("--cache", default=None, help="pickle path for the price load")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    horizons = [int(h) for h in args.horizons.split(",")]
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="fundamental_quality_study")
    fundamentals, liquidity = config.fundamental, config.liquidity
    rule = PromotionRule(
        min_observations=args.min_observations,
        min_abs_t_statistic=args.min_t,
        quantile_fraction=args.quantile,
    )

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    print(f"\n1 signal x {len(horizons)} horizons = {len(horizons)} trials")
    print(
        f"hurdle: |t| > {expected_max_of_normals(len(horizons)):.2f} "
        f"on top of the {args.min_t:.1f} floor"
    )
    print("declared direction: POSITIVE (higher quality outperforms)")
    print(
        f"criteria from FundamentalConfig: ROE >= {fundamentals.min_return_on_equity}, "
        f"D/E <= {fundamentals.max_debt_to_equity}, revenue growth >= "
        f"{fundamentals.min_revenue_growth_yoy}, earnings growth >= "
        f"{fundamentals.min_earnings_growth_yoy}\n"
    )

    cache = pathlib.Path(args.cache) if args.cache else None
    prices: dict[int, dict[str, object]] = {}
    if cache is not None and cache.exists():
        t0 = time.time()
        prices = pickle.loads(cache.read_bytes())
        print(f"prices from cache: {len(prices):,} securities [{time.time() - t0:.0f}s]")

    t0 = time.time()
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    if not prices:
        print("loading prices ...")
    for security_id in () if prices else universe:
        bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
        if len(bars) < 260 + max(horizons):
            continue
        close = np.array([float(b.close) for b in bars])
        volume = np.array([float(b.volume) for b in bars])
        prices[security_id] = {
            "dates": [b.session_date for b in bars],
            "close": close,
            "adv": sma(close * volume, liquidity.dollar_volume_lookback),
            "index": {b.session_date: i for i, b in enumerate(bars)},
        }
    if cache is not None and not cache.exists():
        cache.write_bytes(pickle.dumps(prices, protocol=pickle.HIGHEST_PROTOCOL))
        print(f"  {len(prices):,} securities [{time.time() - t0:.0f}s], cached to {cache}")

    print("loading fundamentals ...")
    t0 = time.time()
    facts = _facts(session, list(prices))
    print(
        f"  {sum(len(m) for f in facts.values() for m in f.values()):,} facts "
        f"for {len(facts):,} securities [{time.time() - t0:.0f}s]"
    )

    all_dates = sorted({d for p in prices.values() for d in p["dates"]})  # type: ignore[union-attr]

    for horizon in horizons:
        stride = horizon
        observations: list[Observation] = []
        liquid = scored = 0
        for day in all_dates[260::stride]:
            as_of_text = day.isoformat()
            for security_id, data in prices.items():
                i = data["index"].get(day)  # type: ignore[union-attr]
                if i is None or i + horizon >= len(data["close"]):  # type: ignore[arg-type]
                    continue
                close = data["close"]  # type: ignore[index]
                if close[i] <= 0 or not (liquidity.min_price <= close[i] <= liquidity.max_price):
                    continue
                adv = data["adv"][i]  # type: ignore[index]
                if not np.isfinite(adv) or adv < liquidity.min_avg_dollar_volume:
                    continue
                liquid += 1

                series = facts.get(security_id, {})
                income = _latest(series.get("NetIncomeLoss", []), as_of_text, args.fresh_years)
                equity = _latest(series.get("StockholdersEquity", []), as_of_text, args.fresh_years)
                if income is None or equity is None or equity[1] <= 0:
                    continue
                met: list[bool] = [
                    income[1] / equity[1] >= (fundamentals.min_return_on_equity or 0)
                ]

                debt = _latest(series.get("Liabilities", []), as_of_text, args.fresh_years)
                if debt is not None and fundamentals.max_debt_to_equity is not None:
                    met.append(debt[1] / equity[1] <= fundamentals.max_debt_to_equity)

                revenue = _latest(series.get("Revenues", []), as_of_text, args.fresh_years)
                if revenue is not None:
                    prior = _prior(series.get("Revenues", []), as_of_text, revenue[0])
                    if prior is not None and prior > 0:
                        met.append(revenue[1] / prior - 1 >= fundamentals.min_revenue_growth_yoy)
                prior_income = _prior(series.get("NetIncomeLoss", []), as_of_text, income[0])
                if prior_income is not None and prior_income > 0:
                    met.append(income[1] / prior_income - 1 >= fundamentals.min_earnings_growth_yoy)

                if len(met) < 2:
                    continue
                scored += 1
                observations.append(
                    Observation(
                        session_date=day,
                        instrument_id=security_id,
                        signal=sum(met) / len(met),
                        outcome=float(close[i + horizon] / close[i] - 1.0),
                    )
                )

        study = SignalStudy(
            name="fundamental_quality",
            target=StudyTarget(
                kind=TargetKind.FORWARD_RETURN,
                horizon_sessions=horizon,
                description=f"{horizon}-session forward return",
            ),
            orientation=Orientation.POSITIVE,
            observations=tuple(observations),
            sampling_stride_sessions=stride,
        )
        median_price = float(
            np.median([float(d["close"][len(d["close"]) // 2]) for d in prices.values()])  # type: ignore[index]
        )
        verdict, reason = study.verdict(
            rule, config.costs, average_price=median_price, baselines=None
        )
        print(f"=== horizon {horizon} sessions ===")
        share = scored / liquid if liquid else 0.0
        print(
            f"  {liquid:,} liquid security-sessions; {scored:,} ({share:.1%}) had "
            f"fundamentals knowable and fresh"
        )
        print(f"  observations {study.count:,}")
        ic, t_stat = study.information_coefficient(), study.t_statistic()
        print(
            f"  IC {'' if ic is None else f'{ic:+.3f}'}   "
            f"t {'' if t_stat is None else f'{t_stat:+.2f}'}   "
            f"spread t {study.spread_t_statistic(args.quantile)}"
        )
        print(
            f"  mean spread {study.quantile_spread(args.quantile)}   "
            f"median {study.robust_quantile_spread(args.quantile)}"
        )
        print(f"  VERDICT {verdict}: {reason}\n")

    print(
        "Evidence, not a change. fundamental_quality carries 0.1667 of the scoring\n"
        "weight and moving it is a decision, not a consequence of this run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
