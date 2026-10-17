#!/usr/bin/env python
"""Walk-forward on the real corpus, for the first time.

**The harness has never been run on real data.** `walkforward.py` has shipped
with 18 passing unit tests since Phase 9 and has only ever split scripted bars.
That is the same gap Phase 5's breakout engine carried -- built, synthetically
tested, and unexercised -- and when that engine finally met real prices it
worked, which is an argument for running the thing, not for assuming it.

**Why walk-forward matters more than the single split this project has used.**
52 and 53 each chose a rule on 2010-2019 and tested it once on 2020-2025. Both
kept their direction and lost two thirds of their magnitude, and a single split
cannot tell "the effect is small" from "the window was lucky". Walk-forward
re-chooses on each training span and scores the span after it, so it reports
**degradation** -- the in-sample advantage that does not survive -- and
**consistency** -- the share of out-of-sample spans that were positive. Those
are the two numbers a single split cannot produce.

The rule it runs is the same **moving-average crossover baseline** as
`backtest_research01.py`, deliberately: this run is about exercising the
harness, and a baseline that nobody is proposing cannot be mistaken for a
finding. Everything below the rule is the proven driver from that script,
reused rather than retyped so the two runs are comparable.

**What this is not.** It is not evidence that anything is profitable. The

**What this is not.** It is not evidence that anything is profitable. The
universe is fifteen large-cap survivors, so every number here is
survivorship-flattered by construction and a good result means the failures are
absent rather than that the rule works.

Everything the run needs is declared on the command line or in
``StrategyConfig``. Nothing is defaulted quietly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, "src")

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.backtesting.walkforward import ANCHORED, ROLLING, WalkForward
from tradeit.core.enums import ArtifactKind, SignalDirection
from tradeit.core.models import OhlcvBar
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

DEFAULT_TICKERS = "AAPL,MSFT,IBM,KO,JNJ,XOM,GE,WMT,PG,INTC,CSCO,PFE,MRK,DIS,T"


@dataclass
class MovingAverageCross:
    """A baseline, not a proposal.

    Long when the fast mean crosses above the slow one, with a stop a declared
    percentage below the close. It carries its own history because the engine
    hands it one session at a time -- and it only ever sees sessions it has
    already been given, in order, which is what keeps it honest.
    """

    fast: int
    slow: int
    stop_pct: Decimal
    closes: dict[int, deque[Decimal]] = field(default_factory=dict)
    _was_above: dict[int, bool] = field(default_factory=dict)

    def __call__(
        self, session_date: dt.date, bars: Mapping[int, OhlcvBar]
    ) -> Sequence[EntryCandidate]:
        out: list[EntryCandidate] = []
        for instrument_id, bar in bars.items():
            history = self.closes.setdefault(instrument_id, deque(maxlen=self.slow))
            history.append(bar.close)
            if len(history) < self.slow:
                continue
            values = list(history)
            fast_mean = sum(values[-self.fast :]) / self.fast
            slow_mean = sum(values) / self.slow
            above = fast_mean > slow_mean
            crossed_up = above and not self._was_above.get(instrument_id, above)
            self._was_above[instrument_id] = above
            if not crossed_up:
                continue
            strength = float((fast_mean - slow_mean) / slow_mean)
            out.append(
                EntryCandidate(
                    score=OpportunityScore(
                        instrument_id=instrument_id,
                        session_date=session_date,
                        direction=SignalDirection.LONG,
                        total=strength,
                        components=(
                            ScoreComponent(
                                name="ma_spread",
                                raw_value=strength,
                                normalised=strength,
                                weight=1.0,
                            ),
                        ),
                        feature_set_digest="ma_cross_baseline",
                        strategy_config_digest="baseline",
                    ),
                    entry_price=bar.close,
                    stop_price=bar.close * (1 - self.stop_pct),
                    sector=None,
                    average_dollar_volume=bar.close * bar.volume,
                )
            )
        return out


def _universe(session: Session, tickers: Sequence[str]) -> dict[str, int]:
    rows = session.execute(
        text(
            "select alias_value, security_id from symbol_aliases "
            "where alias_kind='ticker' and alias_value in :t order by valid_from"
        ).bindparams(bindparam("t", value=list(tickers), expanding=True))
    ).all()
    found: dict[str, int] = {}
    for ticker, security_id in rows:
        found.setdefault(ticker, security_id)
    return found


def _manifest(config: StrategyConfig, as_of: dt.datetime, bars: int) -> RunManifest:
    """A real manifest: the actual configuration, and what the corpus held."""
    return RunManifest(
        run_id=f"ma-cross-{as_of.date()}",
        as_of=as_of,
        strategy_config=config.version(as_of),
        data_snapshot=ArtifactVersion.of(
            ArtifactKind.DATA_SNAPSHOT, "research-01", {"raw_bars_loaded": bars}, as_of
        ),
        feature_set=None,
        model=None,
        code_version="research01-backtest",
        created_at=as_of,
    )


def _report(name: str, report: object) -> None:
    """Print one harness run. Degradation and consistency are the point."""
    results = report.results  # type: ignore[attr-defined]
    print(f"\n=== {name}: {len(results)} windows ===")
    if not results:
        print("  no window fitted in the span -- widen it or shorten the legs")
        return
    print(f"{'window':>7}{'train':>26}{'test':>26}{'in-sample':>12}{'out':>10}{'degradation':>13}")
    for n, result in enumerate(results, 1):
        w = result.window
        ins = result.in_sample.metrics.total_return_pct
        oos = result.out_of_sample.metrics.total_return_pct
        gap = result.degradation
        print(
            f"{n:>7}  {w.train_start}..{w.train_end}  {w.test_start}..{w.test_end}"
            # total_return_pct holds a FRACTION despite its name, so these use
            # the percent format rather than multiplying by hand.
            f"{ins:>12.2%}{oos:>10.2%}" + (f"{gap:>13.2%}" if gap is not None else f"{'--':>13}")
        )
    mean = report.mean_degradation  # type: ignore[attr-defined]
    cons = report.consistency  # type: ignore[attr-defined]
    trades = report.out_of_sample_trade_count  # type: ignore[attr-defined]
    print(f"  out-of-sample trades: {trades:,}")
    print(f"  mean degradation:     {mean:+.2%}" if mean is not None else "  mean degradation: --")
    print(
        f"  consistency:          {cons:.0%} of windows positive out of sample"
        if cons is not None
        else "  consistency: --"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--start", default="2003-01-02")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.02)
    # Stated, never defaulted quietly: the engine requires both and either one
    # inherited silently would decide the result. 10 matches the other
    # real-data scripts. Recovery 1.0 says a delisted holding was sold at its
    # last print -- optimistic in general, and inert here because this universe
    # is fifteen large-cap survivors with no delisting in the span. §50
    # measured the real mix at 73% paid and 6.1% wiped out.
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, default=Decimal(1))
    ap.add_argument("--train-sessions", type=int, default=756, help="~3 years")
    ap.add_argument("--test-sessions", type=int, default=252, help="~1 year")
    ap.add_argument(
        "--gap-sessions",
        type=int,
        default=0,
        help="sessions dropped between train and test. A 200-session rule needs its "
        "warm-up inside the TRAIN leg, not borrowed across the boundary",
    )
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    resolved = _universe(session, tickers)
    print(f"universe: {len(resolved)} of {len(tickers)} tickers resolved")

    config = StrategyConfig(name="ma_cross_baseline")
    rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
    print(f"loading bars {start} to {end} ...")
    t0 = time.time()
    data = CorpusSessionData(
        session=session,
        universe=tuple(sorted(resolved.values())),
        start=start,
        end=end,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on
    loaded = data.bar_count
    print(f"  {loaded:,} raw bars over {data.session_count:,} sessions in {time.time() - t0:.1f}s")

    spec = BacktestSpec(
        name="ma_cross_baseline",
        start=start,
        end=end,
        universe=",".join(sorted(resolved)),
        initial_capital=args.capital,
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = EventDrivenEngine(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing, risk=config.risk, max_participation=args.participation
            ),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=config.risk),
                    MaxPositionsRule(config=config.risk),
                    PositionSizeRule(
                        max_position_pct_of_equity=config.sizing.max_position_pct_of_equity
                    ),
                    GrossExposureRule(config=config.risk),
                ),
                sizing=config.sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=args.participation),
        data=data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=args.risk_free,
        ),
        delisting_after_sessions=args.delisting_after,
        delisting_recovery=args.delisting_recovery,
        manifest=_manifest(config, dt.datetime.now(dt.UTC), loaded),
    )

    # Both modes, because they answer different questions. ANCHORED grows the
    # training span and asks whether more history helps; ROLLING holds it fixed
    # and asks whether the rule keeps working as the market changes.
    for mode in (ANCHORED, ROLLING):
        harness = WalkForward(
            engine=engine,
            data=data,
            mode=mode,
            train_sessions=args.train_sessions,
            test_sessions=args.test_sessions,
            gap_sessions=args.gap_sessions,
        )
        t0 = time.time()
        _report(mode, harness.evaluate(spec))
        print(f"  [{time.time() - t0:.1f}s]")

    print(
        "\nDegradation is the in-sample advantage that did not survive; consistency is\n"
        "the share of out-of-sample spans that were positive. Neither is evidence of\n"
        "anything here -- the universe is fifteen large-cap survivors and the rule is\n"
        "a baseline. This run exercises a harness that had never seen real data."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
