#!/usr/bin/env python
"""Run the backtester against the real corpus.

**What this is not.** It is not evidence that anything is profitable. The
survivorship gate reads ``SURVIVOR_BIASED`` against ``research-01``, so the
companies that failed are substantially absent and a good result means the
failures are missing rather than that the rule works. This script exists to
exercise the machinery on 71 million real bars instead of scripted ones, and to
make the numbers it prints impossible to mistake for a finding.

The rule it runs is a **moving-average crossover baseline** -- one of the
baselines the roadmap requires every candidate signal to beat, not a strategy
anybody is proposing. It is here because a backtester needs something to trade,
and a deliberately unimpressive rule is the honest choice: if the plumbing
flatters anything, it will flatter this, and that is worth seeing.

Everything the run needs is declared on the command line or in ``StrategyConfig``.
Nothing is defaulted quietly.
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
from tradeit.backtesting.overfitting import TrialLedger
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.02)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    resolved = _universe(session, tickers)
    missing = sorted(set(tickers) - set(resolved))
    print(f"universe: {len(resolved)} of {len(tickers)} tickers resolved")
    if missing:
        print(f"  not found: {', '.join(missing)}")

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
    print(f"  {data.excluded_out_of_window:,} bars excluded as outside a ticker's own interval")
    print(f"  {data.split_count} split events inside the window")

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
        manifest=_manifest(config, dt.datetime.now(dt.UTC), loaded),
    )

    print("running ...")
    t0 = time.time()
    result = engine.run(spec)
    print(f"  {data.session_count:,} sessions in {time.time() - t0:.1f}s")

    metrics = result.metrics
    print("\n--- result ---")
    print(f"trades            {len(result.trades):,}")
    if metrics is not None:
        print(f"total return      {metrics.total_return_pct:>8.2%}")
        print(f"CAGR              {metrics.cagr:>8.2%}")
        print(
            f"max drawdown      {metrics.max_drawdown_pct:>8.2%}  "
            f"({metrics.max_drawdown_duration_sessions} sessions)"
        )
        sharpe = "    none" if metrics.sharpe is None else f"{metrics.sharpe:>8.2f}"
        print(f"sharpe            {sharpe}")
        print(f"win rate          {metrics.win_rate:>8.2%}")
        print(f"exposure          {metrics.exposure_pct:>8.2%}")
        print(f"turnover          {metrics.turnover:>8.2f}x/yr")
        print(f"enough trades?    {metrics.statistically_meaningful}")
    print(f"trustworthy       {result.trustworthy}")

    ledger = TrialLedger(hypothesis="ma_cross_baseline")
    ledger.record(spec, at=dt.datetime.now(dt.UTC))
    verdict, reason = ledger.assess(result)
    print(f"\ntrial check       {verdict}: {reason}")

    print(
        "\nNOT EVIDENCE OF PROFITABILITY. The survivorship gate reads "
        "SURVIVOR_BIASED against this corpus:\nthe companies that failed are "
        "substantially absent, so a good number here means the failures are\n"
        "missing, not that the rule works. This run exercises the machinery."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
