#!/usr/bin/env python
"""Equal dollars against equal risk, on the same signal.

**Pre-registered.** Both arms, the universe, the period, the costs and the
criterion are fixed in this file before it is run, exactly as the
``relative_volume_20`` out-of-sample test was.

The hypothesis under test is not a signal. It is that the volatility
relationship -- the only one in ``SIGNAL_RESEARCH_01`` that never changed sign,
and the largest effect found -- is *capturable by sizing* even though a
quantile spread cannot value it. §7 of that document explains why the spread
cannot: sorting on volatility sorts on the variance of the thing being
averaged, so the high-volatility bucket holds the extreme outcomes by
construction.

A portfolio earns the arithmetic mean of its holdings **because it holds equal
dollar amounts.** One that holds equal *risk* amounts earns something else, and
that is an implementable change rather than a statistical trick.

With risk-based sizing, shares are ``risk_amount / (entry - stop)``, so the
stop rule *is* the sizing rule:

    arm A   fixed-percentage stop   entry - stop  ∝ price        notional constant
    arm B   ATR stop                entry - stop  ∝ volatility   notional ∝ 1/ATR%

Same universe, same entry rule, same costs, same period, same risk budget per
trade. **The arms differ in exactly one thing.**

**The criterion, fixed in advance:** arm B captures the relationship if it beats
arm A on risk-adjusted return -- Sharpe first, and drawdown as the check that
the Sharpe was not bought with leverage. If it does not, the volatility
relationship is real and not monetisable this way, which is an answer worth
having in writing.

Neither arm is expected to be profitable. The entry rule is a deliberately
unremarkable moving-average baseline and the corpus gate reads SURVIVOR_BIASED.
**What is being compared is the difference between the arms, not the level of
either.**
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import MovingAverageCross, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
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


def _manifest(config: StrategyConfig, arm: str, bars: int) -> RunManifest:
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"volsizing-{arm}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{arm}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="volatility-sizing",
        created_at=now,
    )


def _run(
    session: Session,
    config: StrategyConfig,
    universe: list[int],
    arm: str,
    rule: MovingAverageCross,
    args: argparse.Namespace,
) -> BacktestResult:
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    t0 = time.time()
    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=start,
        end=end,
        candidate_source=rule,
    )
    print(f"  {arm:<20} {data.bar_count:>9,} bars loaded in {time.time() - t0:>5.1f}s")
    spec = BacktestSpec(
        name=f"vol_sizing/{arm}",
        start=start,
        end=end,
        universe=arm,
        initial_capital=args.capital,
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = build_engine(
        config,
        data,
        _manifest(config, arm, data.bar_count),
        participation=args.participation,
        risk_free_rate=args.risk_free,
        delisting_after_sessions=args.delisting_after,
        delisting_recovery=args.delisting_recovery,
    )
    t0 = time.time()
    result = engine.run(spec)
    print(f"  {arm:<20} ran in {time.time() - t0:>5.1f}s, {len(result.trades):,} trades")
    return result


def _row(arm: str, result: BacktestResult) -> str:
    m = result.metrics
    if m is None:
        return f"  {arm:<22} no metrics"
    sharpe = "    none" if m.sharpe is None else f"{m.sharpe:>8.2f}"
    return (
        f"  {arm:<22}{m.total_return_pct:>10.2%}{m.cagr:>9.2%}"
        f"{m.max_drawdown_pct:>10.2%}{sharpe}{m.trade_count:>8,}{m.exposure_pct:>9.1%}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.03)
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, default=Decimal("0.5"))
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="vol_sizing_experiment")
    liquidity = config.liquidity
    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)

    shared = {
        "fast": args.fast,
        "slow": args.slow,
        "min_price": Decimal(str(liquidity.min_price)),
        "min_dollar_volume": Decimal(str(liquidity.min_avg_dollar_volume)),
        "dollar_volume_lookback": liquidity.dollar_volume_lookback,
    }
    arms = {
        "A_equal_dollar": MovingAverageCross(
            stop_pct=Decimal(str(config.exits.max_initial_stop_pct)), **shared
        ),
        "B_equal_risk": MovingAverageCross(
            stop_atr_multiple=Decimal(str(config.exits.initial_stop_atr_multiple)),
            atr_period=config.indicators.atr_period,
            **shared,
        ),
    }
    print(
        f"arm A stop: {config.exits.max_initial_stop_pct:.0%} of price  ->  equal dollar\n"
        f"arm B stop: {config.exits.initial_stop_atr_multiple:.1f} x "
        f"{config.indicators.atr_period}-day ATR  ->  equal risk\n"
        f"delisting recovery {args.delisting_recovery}, "
        f"risk-free {args.risk_free:.1%}\n"
    )

    results = {arm: _run(session, config, universe, arm, rule, args) for arm, rule in arms.items()}

    print(
        f"\n  {'arm':<22}{'return':>10}{'CAGR':>9}{'maxDD':>10}"
        f"{'Sharpe':>8}{'trades':>8}{'expos':>9}"
    )
    for arm, result in results.items():
        print(_row(arm, result))

    a, b = results["A_equal_dollar"].metrics, results["B_equal_risk"].metrics
    if a is not None and b is not None:
        print("\n--- the pre-registered comparison ---")
        if a.sharpe is not None and b.sharpe is not None:
            print(
                f"  Sharpe   A {a.sharpe:>6.2f}  ->  B {b.sharpe:>6.2f}   "
                f"({b.sharpe - a.sharpe:+.2f})"
            )
        print(
            f"  maxDD    A {a.max_drawdown_pct:>6.2%}  ->  B {b.max_drawdown_pct:>6.2%}   "
            f"({b.max_drawdown_pct - a.max_drawdown_pct:+.2%})"
        )
        print(
            f"  expos    A {a.exposure_pct:>6.1%}  ->  B {b.exposure_pct:>6.1%}   "
            f"({b.exposure_pct - a.exposure_pct:+.1%})"
        )
        # The criterion as registered: Sharpe first, drawdown as the check that
        # the Sharpe was not bought with more risk. A Sharpe that improves while
        # drawdown worsens is not a capture -- it is a different bet -- so it is
        # reported as mixed rather than forced into a yes.
        sharpe_better = a.sharpe is not None and b.sharpe is not None and b.sharpe > a.sharpe
        drawdown_ok = b.max_drawdown_pct <= a.max_drawdown_pct
        verdict = (
            "CAPTURES"
            if sharpe_better and drawdown_ok
            else "is MIXED on"
            if sharpe_better
            else "does NOT capture"
        )
        print(
            f"\n  VERDICT: equal-risk sizing {verdict} the volatility relationship on this period."
        )
        if sharpe_better and not drawdown_ok:
            print(
                "  Sharpe improved but drawdown did not. Under the registered "
                "criterion that is\n  not a capture: the risk-adjusted gain came "
                "with more risk, not less."
            )
    print(
        "\nThe difference between the arms is the result. Neither level is evidence of\n"
        "profitability: the entry rule is a deliberately unremarkable baseline and the\n"
        "corpus gate reads SURVIVOR_BIASED."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
