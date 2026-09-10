#!/usr/bin/env python
"""Does sector strength predict anything?

``ScoringConfig`` weights ``sector_strength`` at 0.10 and it has never been
tested, because until 2026-09-09 this corpus held no sector classification at
all. ``issuer_sic_observations`` now covers 93.5% of priced securities, so the
question can finally be asked.

**It tests the real engine, not a proxy for it.**
:class:`~tradeit.analytics.sectors.SectorStrengthEngine` computes the composite
score the system would actually use -- relative and absolute return, relative
momentum, breadth above two moving averages, participation -- weighted by
``SectorStrengthConfig``. Substituting "the sector's trailing return" would have
been easier and would have tested a strawman: if the composite fails it should
fail as the composite, and if it works the weights that made it work are the
declared ones.

Point-in-time, in three places that each matter
-----------------------------------------------

**Membership.** A security's sector is resolved from the classification in
force on the session, not today's. ``issuer_sic_observations`` holds
observations with the filing date that stated them, and the engine takes the
roster as an argument precisely so a caller cannot hand it today's mapping
without that being visible.

**Coverage.** An issuer whose earliest classifying filing is later than the
session has *no* sector then, and is excluded from that session rather than
back-filled. This bites hardest in the early years and the run reports how
often, because a study that quietly back-filled would be reading 2010's
classification into 2001.

**Everything else** carries over from the first signal study: a universe that
includes the companies that failed, non-overlapping sampling, liquidity floors
applied on trailing data, and costs charged against the turnover the horizon
forces.

The direction is declared, not derived
--------------------------------------

**POSITIVE**: securities in strong sectors outperform. That is the premise
behind giving the factor any weight at all, so it is the hypothesis on trial,
and it is fixed here before the run.
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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.kernels import rate_of_change, sma
from tradeit.analytics.sectors import SectorMemberInput, SectorStrengthEngine
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


def _sector_history(session: Session) -> dict[int, list[tuple[dt.date, str]]]:
    """Per issuer, its classifications oldest first. Resolved per session later."""
    history: dict[int, list[tuple[dt.date, str]]] = defaultdict(list)
    rows = session.execute(
        text(
            "select issuer_id, observed_on, division from issuer_sic_observations "
            "order by issuer_id, observed_on"
        )
    ).all()
    for issuer_id, observed_on, division in rows:
        day = (
            observed_on
            if isinstance(observed_on, dt.date)
            else dt.date.fromisoformat(str(observed_on))
        )
        history[int(issuer_id)].append((day, division))
    return history


def _sector_on(history: list[tuple[dt.date, str]] | None, session_date: dt.date) -> str | None:
    """The classification in force on a date, or ``None`` if none was yet."""
    if not history:
        return None
    found: str | None = None
    for observed_on, division in history:
        if observed_on <= session_date:
            found = division
        else:
            break
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--horizons", default="21,63")
    ap.add_argument("--min-observations", type=int, default=500)
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--quantile", type=float, default=0.2)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    horizons = [int(h) for h in args.horizons.split(",")]
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="sector_strength_study")
    engine = SectorStrengthEngine(config.sector_strength)
    liquidity = config.liquidity
    rule = PromotionRule(
        min_observations=args.min_observations,
        min_abs_t_statistic=args.min_t,
        quantile_fraction=args.quantile,
    )

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    history = _sector_history(session)
    sec_to_issuer = dict(
        session.execute(text("select security_id, issuer_id from securities")).all()
    )
    trials = len(horizons)
    print(f"\n1 signal x {len(horizons)} horizons = {trials} trials")
    print(
        f"hurdle at {trials} trials: |t| > {expected_max_of_normals(trials):.2f} "
        f"on top of the {args.min_t:.1f} floor"
    )
    print("declared direction: POSITIVE (strong sectors outperform)\n")

    lookbacks = list(config.sector_strength.momentum_lookbacks)
    ma_periods = list(config.sector_strength.breadth_ma_periods)
    warmup = max(max(ma_periods), max(lookbacks)) + 2

    print("loading price history ...")
    t0 = time.time()
    per_security: dict[int, dict[str, object]] = {}
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    for security_id in universe:
        bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
        if len(bars) < warmup + max(horizons) + 10:
            continue
        close = np.array([float(b.close) for b in bars])
        volume = np.array([float(b.volume) for b in bars])
        per_security[security_id] = {
            "dates": [b.session_date for b in bars],
            "close": close,
            "adv": sma(close * volume, liquidity.dollar_volume_lookback),
            "roc": {lb: rate_of_change(close, lb) for lb in lookbacks},
            "ma": {p: sma(close, p) for p in ma_periods},
            "index": {b.session_date: i for i, b in enumerate(bars)},
        }
    print(f"  {len(per_security):,} securities with enough history [{time.time() - t0:.0f}s]")

    all_dates = sorted({d for s in per_security.values() for d in s["dates"]})  # type: ignore[union-attr]

    for horizon in horizons:
        stride = horizon
        sampled = all_dates[warmup::stride]
        observations: list[Observation] = []
        no_sector = considered = 0

        for day in sampled:
            members: dict[str, list[SectorMemberInput]] = defaultdict(list)
            forward: dict[int, float] = {}
            market: dict[int, list[float]] = {lb: [] for lb in lookbacks}
            for security_id, data in per_security.items():
                i = data["index"].get(day)  # type: ignore[union-attr]
                if i is None or i < warmup:
                    continue
                close = data["close"]  # type: ignore[index]
                if i + horizon >= len(close) or close[i] <= 0:
                    continue
                if not (liquidity.min_price <= close[i] <= liquidity.max_price):
                    continue
                adv = data["adv"][i]  # type: ignore[index]
                if not np.isfinite(adv) or adv < liquidity.min_avg_dollar_volume:
                    continue
                considered += 1
                sector = _sector_on(history.get(sec_to_issuer.get(security_id, -1)), day)
                if sector is None:
                    no_sector += 1
                    continue
                returns = {lb: float(data["roc"][lb][i]) for lb in lookbacks}  # type: ignore[index]
                if any(not np.isfinite(v) for v in returns.values()):
                    continue
                for lb, value in returns.items():
                    market[lb].append(value)
                members[sector].append(
                    SectorMemberInput(
                        instrument_id=security_id,
                        return_over=dict(returns),
                        above_ma={
                            p: bool(close[i] > data["ma"][p][i])  # type: ignore[index]
                            if np.isfinite(data["ma"][p][i])  # type: ignore[index]
                            else None
                            for p in ma_periods
                        },
                    )
                )
                forward[security_id] = float(close[i + horizon] / close[i] - 1.0)

            market_return = {lb: (float(np.mean(v)) if v else None) for lb, v in market.items()}
            results = engine.rank(
                [
                    engine.evaluate_sector(day, sector, roster, market_return)
                    for sector, roster in members.items()
                ]
            )
            score_of = {r.sector: r.score for r in results if r.usable}
            for sector, roster in members.items():
                score = score_of.get(sector)
                if score is None:
                    continue
                for member in roster:
                    outcome = forward.get(member.instrument_id)
                    if outcome is None:
                        continue
                    observations.append(
                        Observation(
                            session_date=day,
                            instrument_id=member.instrument_id,
                            signal=float(score),
                            outcome=outcome,
                        )
                    )

        study = SignalStudy(
            name="sector_strength",
            target=StudyTarget(
                kind=TargetKind.FORWARD_RETURN,
                horizon_sessions=horizon,
                description=f"{horizon}-session forward return",
            ),
            orientation=Orientation.POSITIVE,
            observations=tuple(observations),
            sampling_stride_sessions=stride,
        )
        prices = [
            float(d["close"][len(d["close"]) // 2])
            for d in per_security.values()  # type: ignore[index]
        ]
        median_price = float(np.median(prices)) if prices else 1.0
        verdict, reason = study.verdict(
            rule, config.costs, average_price=median_price, baselines=None
        )
        ic = study.information_coefficient()
        t_stat = study.t_statistic()
        spread_t = study.spread_t_statistic(args.quantile)
        mean_sp = study.quantile_spread(args.quantile)
        med_sp = study.robust_quantile_spread(args.quantile)
        net = study.net_annual_spread(args.quantile, config.costs, average_price=median_price)

        print(f"=== horizon {horizon} sessions ({len(sampled)} sampled sessions) ===")
        share = no_sector / considered if considered else 0.0
        print(
            f"  {considered:,} security-sessions passed liquidity; "
            f"{no_sector:,} ({share:.1%}) had no classification yet and were dropped"
        )
        print(f"  observations {study.count:,}   effective {study.effective_observations:,.0f}")
        print(
            f"  IC {'' if ic is None else f'{ic:+.3f}'}   "
            f"t {'' if t_stat is None else f'{t_stat:+.2f}'}   "
            f"spread t {'' if spread_t is None else f'{spread_t:+.2f}'}"
        )
        print(
            f"  mean spread {'' if mean_sp is None else f'{mean_sp:+.2%}'}   "
            f"median {'' if med_sp is None else f'{med_sp:+.2%}'}   "
            f"net {'' if net is None else f'{net:+.1%}'}/yr"
        )
        print(f"  VERDICT {verdict}: {reason}\n")

    print(
        "Evidence, not a change. sector_strength carries 0.10 of the scoring weight\n"
        "and moving it is a decision, not a consequence of this run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
