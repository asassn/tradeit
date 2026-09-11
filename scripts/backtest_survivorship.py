#!/usr/bin/env python
"""Run the same strategy twice: on the survivors, and on everybody.

This is the measurement the whole corpus effort exists to make possible. The
same rule, the same period, the same costs -- the only difference is whether
the universe includes the companies whose prices stopped.

**Why it matters more than any strategy result.** A universe chosen today from
companies that still exist has already been filtered by the outcome being
measured. Every failure was removed before the test began, so the strategy is
never given the chance to buy one. The gap between the two arms below is the
size of that lie for this rule over this period.

The universe is built from the prices themselves rather than from EDGAR exit
filings: a security is in the *died* arm if its raw price series stops before
the window ends. That is a deliberately narrow definition -- it catches
acquisitions and quiet delistings alongside failures, and it says nothing about
why the quotes ended -- but it is measured rather than inferred, and it is the
population that matters for survivorship.

**The delisting recovery assumption dominates the died arm**, so it is a
required argument and both extremes are worth running. ``--delisting-recovery
1.0`` says every delisted holding was sold at its last quoted price, which is
optimistic. ``0.0`` says every one went to nothing, which is too harsh for the
acquisitions. The truth is between them and this system cannot yet tell the
cases apart, so it makes you choose and it prints what you chose.
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
from tradeit.core.enums import ArtifactKind, ExitReason
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig


def _spans(path: str) -> list[tuple[int, str, str, int]]:
    with open(path) as handle:
        return [
            (int(sid), first, last, int(count)) for sid, first, last, count in csv.reader(handle)
        ]


def _arms(
    spans: list[tuple[int, str, str, int]],
    start: str,
    end: str,
    min_bars: int,
    cap: int,
    offset: int = 0,
) -> tuple[list[int], list[int]]:
    """Securities tradeable at the start, split by whether they lasted.

    Sampled deterministically by taking every Nth of the sorted list, so a
    rerun picks the same names and the two arms stay comparable.

    ``offset`` takes the (i*N + offset)th instead: the same construction over
    different names. **One sample is not enough to quote a number from**, and
    this is how that was found: across four disjoint samples of 2000-2009 the
    survivors arm alone ranged from -13.1% to +44.8%, and a change to 1.8% of
    one sample's bars moved it thirteen points by altering a single admission
    in January 2001 that cascaded through 190 later trades. At cap 400 the died
    arm's step is 4.34, so offsets 0-3 are disjoint there.
    """
    alive = [row for row in spans if row[1] <= start <= row[2] and row[3] >= min_bars]
    died = sorted(row[0] for row in alive if row[2] < end)
    survived = sorted(row[0] for row in alive if row[2] >= end)

    def thin(ids: list[int], limit: int) -> list[int]:
        if len(ids) <= limit:
            return ids
        step = len(ids) / limit
        return [ids[min(len(ids) - 1, int(i * step) + offset)] for i in range(limit)]

    return thin(survived, cap), thin(died, cap)


def _manifest(config: StrategyConfig, label: str, bars: int) -> RunManifest:
    now = dt.datetime.now(dt.UTC)
    return RunManifest(
        run_id=f"survivorship-{label}-{now.date()}",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, f"research-01/{label}", {"raw_bars": bars}, now
        ),
        feature_set=None,
        model=None,
        code_version="survivorship-comparison",
        created_at=now,
    )


def _run(
    session: Session,
    config: StrategyConfig,
    universe: list[int],
    label: str,
    args: argparse.Namespace,
) -> tuple[BacktestResult, CorpusSessionData]:
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    t0 = time.time()
    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=start,
        end=end,
        candidate_source=MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct),
    )
    print(
        f"  {label:<10} {len(universe):>5,} securities  {data.bar_count:>9,} bars  "
        f"{data.session_count:>5,} sessions  loaded in {time.time() - t0:>5.1f}s"
    )
    spec = BacktestSpec(
        name=f"ma_cross/{label}",
        start=start,
        end=end,
        universe=label,
        initial_capital=args.capital,
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = build_engine(
        config,
        data,
        _manifest(config, label, data.bar_count),
        participation=args.participation,
        risk_free_rate=args.risk_free,
        delisting_after_sessions=args.delisting_after,
        delisting_recovery=args.delisting_recovery,
    )
    t0 = time.time()
    result = engine.run(spec)
    print(f"  {label:<10} ran in {time.time() - t0:.1f}s, {len(result.trades):,} trades")
    return result, data


def _report(label: str, result: BacktestResult) -> None:
    metrics = result.metrics
    delisted = [t for t in result.trades if t.exit_reason == str(ExitReason.DELISTED_EXIT)]
    print(f"\n{label}")
    if metrics is None:
        print("  no metrics")
        return
    print(f"  total return   {metrics.total_return_pct:>9.2%}")
    print(f"  CAGR           {metrics.cagr:>9.2%}")
    print(f"  max drawdown   {metrics.max_drawdown_pct:>9.2%}")
    sharpe = "     none" if metrics.sharpe is None else f"{metrics.sharpe:>9.2f}"
    print(f"  sharpe         {sharpe}")
    print(f"  win rate       {metrics.win_rate:>9.2%}")
    print(f"  trades         {metrics.trade_count:>9,}")
    print(f"  delisted exits {len(delisted):>9,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True, help="CSV of security_id,first,last,count")
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--cap", type=int, default=600, help="securities per arm")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.03)
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, required=True)
    ap.add_argument(
        "--offset",
        type=int,
        default=0,
        help="which of several disjoint equally-valid samples to draw; 0 is the original",
    )
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="ma_cross_baseline")

    survived, died = _arms(
        _spans(args.spans), args.start, args.end, args.min_bars, args.cap, args.offset
    )
    print(f"universe {args.start} .. {args.end}  (sample offset {args.offset})")
    print(f"  survivors {len(survived):,}   died {len(died):,}")
    print(f"  delisting recovery assumption: {args.delisting_recovery}")
    print()

    survivors_only, _ = _run(session, config, survived, "survivors", args)
    everybody, _ = _run(session, config, sorted(survived + died), "everybody", args)

    _report("SURVIVORS ONLY  (the biased view)", survivors_only)
    _report("EVERYBODY       (survivorship-honest)", everybody)

    a, b = survivors_only.metrics, everybody.metrics
    if a is not None and b is not None:
        print("\n--- the survivorship gap ---")
        print(
            f"  return  {a.total_return_pct:>8.2%}  ->  {b.total_return_pct:>8.2%}"
            f"   ({b.total_return_pct - a.total_return_pct:+.2%})"
        )
        print(
            f"  drawdown{a.max_drawdown_pct:>8.2%}  ->  {b.max_drawdown_pct:>8.2%}"
            f"   ({b.max_drawdown_pct - a.max_drawdown_pct:+.2%})"
        )
        print(
            "\nThe difference is not a strategy result. It is what excluding the "
            "companies that\nfailed was worth to this rule over this period."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
