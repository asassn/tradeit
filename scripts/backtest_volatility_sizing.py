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
unremarkable moving-average baseline and the corpus gate reads
``PARTIALLY_SURVIVORSHIP_CORRECTED`` at 38.7% -- it read ``SURVIVOR_BIASED``
when this file was written. **What is being compared is the difference between
the arms, not the level of either**, and §7h of the denominator document is why
that matters: a paired comparison loses the same missing companies from both
arms, so it survives a coverage hole that an absolute claim does not.

**Liquidity and disjoint samples, added 2026-09-13.** §27 measured every
apparent edge in §26 as illiquidity, so ``--min-dollar-volume`` admits a
security on its **trailing year's** median dollar volume -- 2009 data for a 2010
start, never the test window, so no future information enters the universe --
and ``--offset`` cuts the survivors into disjoint samples the way §20 and §25
did, because one sample's number carries noise the size of the effect.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import MovingAverageCross, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.research01.series import price_series
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig


def _liquid(
    session: Session, ids: list[int], year_end: str, floor: float, *, cache: Path | None
) -> set[int]:
    """Securities whose median dollar volume before the window clears ``floor``.

    Causal by construction: the admission window ends the day before the test
    window opens, so nothing the universe knows comes from inside the test. A
    security with fewer than 100 traded sessions in it is refused rather than
    guessed at.

    **Read through ``price_series`` since 2026-09-18**, so each session's dollar
    volume is the money that traded (DATA_DICTIONARY §0.9). The first version
    multiplied the stored close by the stored volume in SQL -- a raw price times
    a volume the vendor had already restated for every later split -- so a
    company that later split forward was admitted on turnover it never had, and
    one that later reverse-split was refused on turnover it did have. §28 was
    measured through that read.

    **The window is two years, not the one the name suggests**: it runs from
    January of ``year_end``'s previous year. That is what §28 measured, so the
    re-run keeps it and changes only the volume read; the discrepancy is recorded
    here and in §36 rather than fixed alongside and confounded with it.
    """
    if floor <= 0:
        return set(ids)
    if cache is not None and cache.exists():
        return set(json.loads(cache.read_text()))
    end = dt.date.fromisoformat(year_end)
    start = dt.date(end.year - 1, 1, 1)
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    keep: set[int] = set()
    for n, sid in enumerate(ids, 1):
        bars = price_series(session, sid, as_of=as_of, start=start, end=end)
        traded = [float(b.close * b.volume) for b in bars if b.volume > 0]
        if len(traded) >= 100 and statistics.median(traded) >= floor:
            keep.add(sid)
        if n % 500 == 0:
            print(f"  admission: {n:,}/{len(ids):,} read, {len(keep):,} clear", flush=True)
    if cache is not None:
        cache.write_text(json.dumps(sorted(keep)))
    return keep


def _universe(
    path: str,
    start: str,
    end: str,
    min_bars: int,
    cap: int,
    *,
    session: Session,
    floor: float,
    offset: int,
    cache: Path | None,
) -> list[int]:
    with open(path) as handle:
        spans = [
            (int(sid), first, last, int(count)) for sid, first, last, count in csv.reader(handle)
        ]
    alive = [row for row in spans if row[1] <= start <= row[2] and row[3] >= min_bars]
    liquid = _liquid(
        session,
        sorted(row[0] for row in alive),
        f"{int(start[:4]) - 1}-12-31",
        floor,
        cache=cache,
    )
    alive = [row for row in alive if row[0] in liquid]
    died = sorted(row[0] for row in alive if row[2] < end)
    survived = sorted(row[0] for row in alive if row[2] >= end)

    def slice_(ids: list[int], limit: int) -> list[int]:
        """The offset-th disjoint block of `limit` ids, as §20 and §25 cut them."""
        lo = offset * limit
        return ids[lo : lo + limit]

    s_, d_ = slice_(survived, cap), slice_(died, cap)
    chosen = sorted(s_ + d_)
    print(
        f"universe: {len(chosen):,} securities ({len(s_):,} survived, {len(d_):,} stopped "
        f"printing) from {len(survived):,}/{len(died):,} clearing ${floor:,.0f}/day, "
        f"offset {offset}"
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
    rule.splits_on = data.splits_on
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
    ap.add_argument(
        "--min-dollar-volume",
        type=float,
        default=0.0,
        help="admit only securities whose TRAILING-year median dollar volume clears this",
    )
    ap.add_argument(
        "--offset", type=int, default=0, help="which disjoint block of the universe to use"
    )
    ap.add_argument(
        "--liquid-cache",
        default=None,
        help="JSON file holding the admitted set, so disjoint samples read it once",
    )
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="vol_sizing_experiment")
    liquidity = config.liquidity
    universe = _universe(
        args.spans,
        args.start,
        args.end,
        args.min_bars,
        args.cap,
        session=session,
        floor=args.min_dollar_volume,
        offset=args.offset,
        cache=Path(args.liquid_cache) if args.liquid_cache else None,
    )

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
    if a is not None and b is not None:
        print(
            f"RESULT,{args.offset},{args.min_dollar_volume:.0f},{args.delisting_recovery},"
            f"{a.total_return_pct:.6f},{b.total_return_pct:.6f},{a.cagr:.6f},{b.cagr:.6f},"
            f"{a.max_drawdown_pct:.6f},{b.max_drawdown_pct:.6f},"
            f"{'' if a.sharpe is None else format(a.sharpe, '.6f')},"
            f"{'' if b.sharpe is None else format(b.sharpe, '.6f')},"
            f"{a.trade_count},{b.trade_count}"
        )
    print(
        "\nThe difference between the arms is the result. Neither level is evidence of\n"
        "profitability: the entry rule is a deliberately unremarkable baseline and the\n"
        "corpus gate reads PARTIALLY_SURVIVORSHIP_CORRECTED at 38.7%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
