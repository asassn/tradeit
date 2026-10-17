#!/usr/bin/env python
"""Monte Carlo and parameter perturbation on the real corpus, for the first time.

Both modules shipped with Phase 9 -- 16 and 19 passing unit tests -- and
neither had ever been handed a real backtest. This closes the last of the three
unexercised Phase 9 modules found by the audit of 2026-10-17.

**Three questions, three methods, and they are not interchangeable.**

``trade_sequence``
    Resample the observed trades. *Given this edge, how bad could the path have
    been?* The reported drawdown is one draw; the 5th percentile is the number
    that should size a position.
``bootstrap_returns``
    Resample the equity curve's periodic returns instead, so the stretches with
    nothing open are included. A strategy flat most of the time looks very
    different here, correctly.
``parameter_perturbation``
    Re-run the whole backtest under jittered parameters. ``montecarlo.py``
    refuses this method and points at ``sensitivity.py``, which is the
    re-runner -- the two are a designed pair, not a gap.

**Perturbation is the check this project needed and did not run.** §52 spent a
trial on an in-sample t of 3.82 and §53 on 4.44; both collapsed out of sample.
Fragility asks a different question from a hold-out: *how much of the edge
survives moving every parameter at once?* It jitters all of them together on
purpose, because one-at-a-time systematically understates fragility -- a
strategy can be robust to each parameter alone and fall apart when two move
together, which is what a real deployment does.

The rule is the same **moving-average crossover baseline** as the other
real-data scripts, and for the same reason: this run exercises machinery, and a
baseline nobody proposes cannot be mistaken for a finding.

**What this is not.** It is not evidence that anything is profitable. The

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

from tradeit.backtesting.base import BacktestSpec, MonteCarloSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.montecarlo import ResamplingMonteCarlo
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.backtesting.sensitivity import ParameterPerturbation
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


def _percentiles(label: str, mc: object) -> None:
    rp = mc.return_percentiles  # type: ignore[attr-defined]
    dd = mc.drawdown_percentiles  # type: ignore[attr-defined]
    done = mc.iterations_completed  # type: ignore[attr-defined]
    print(f"\n--- {label}: {done:,} iterations ---")
    if not rp:
        print("  no iterations completed -- too few trades to resample")
        return
    keys = sorted(rp)
    print("  percentile   " + "".join(f"{p:>9}" for p in keys))
    print("  total return " + "".join(f"{rp[p]:>8.2%}" for p in keys))
    print("  max drawdown " + "".join(f"{dd[p]:>8.2%}" for p in keys))
    print(f"  probability of ruin: {mc.ruin_probability:.2%}")  # type: ignore[attr-defined]


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
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, default=Decimal(1))
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument(
        "--perturb-iterations",
        type=int,
        default=100,
        help="each one RE-RUNS THE WHOLE BACKTEST, so this is the expensive knob. "
        "MonteCarloSpec refuses fewer than 100 -- 'percentiles nobody should quote' -- "
        "so 100 is both the floor and the default",
    )
    ap.add_argument("--perturbation-pct", type=float, default=0.10)
    ap.add_argument(
        "--block-size",
        type=int,
        default=None,
        help="resample trades in blocks, which preserves streakiness",
    )
    # Stated rather than defaulted: the fraction of starting equity at or below
    # which a resampled path counts as ruined. 0.5 is "lost half the account".
    ap.add_argument("--ruin-threshold", type=float, default=0.50)
    ap.add_argument(
        "--max-fragility",
        type=float,
        default=0.50,
        help="share of the edge allowed to disappear at the median jittered draw",
    )
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    resolved = _universe(session, tickers)
    print(f"universe: {len(resolved)} of {len(tickers)} tickers resolved")

    base_config = StrategyConfig(name="ma_cross_baseline")
    print(f"loading bars {start} to {end} ...")
    t0 = time.time()
    shared_rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
    data = CorpusSessionData(
        session=session,
        universe=tuple(sorted(resolved.values())),
        start=start,
        end=end,
        candidate_source=shared_rule,
    )
    shared_rule.splits_on = data.splits_on
    loaded = data.bar_count
    print(f"  {loaded:,} raw bars over {data.session_count:,} sessions in {time.time() - t0:.1f}s")

    spec = BacktestSpec(
        name="ma_cross_baseline",
        start=start,
        end=end,
        universe=",".join(sorted(resolved)),
        initial_capital=args.capital,
        strategy_config_digest=base_config.digest,
        cost_model="participation",
        fill_model="bar",
    )

    def build(config: StrategyConfig) -> EventDrivenEngine:
        """One engine per config. The data is shared; the strategy is not."""
        rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
        rule.splits_on = data.splits_on
        # The rule reads bars the engine hands it, so the candidate source must
        # be the instance this engine will drive -- sharing one across perturbed
        # runs would carry its moving-average state between them.
        return EventDrivenEngine(
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

    print("running the baseline ...")
    t0 = time.time()
    base_result = build(base_config).run(spec)
    metrics = base_result.metrics
    print(f"  {len(base_result.trades):,} trades in {time.time() - t0:.1f}s")
    if metrics is None:
        raise SystemExit("the baseline produced no metrics; nothing to resample")
    print(
        f"  total return {metrics.total_return_pct:.2%}   max drawdown "
        f"{metrics.max_drawdown_pct:.2%}   enough trades? {metrics.statistically_meaningful}"
    )

    resampler = ResamplingMonteCarlo(ruin_threshold=args.ruin_threshold)
    for method in ("trade_sequence", "bootstrap_returns"):
        mc_spec = MonteCarloSpec(
            method=method, iterations=args.iterations, block_size=args.block_size
        )
        t0 = time.time()
        _percentiles(method, resampler.run(base_result, mc_spec))
        print(f"  [{time.time() - t0:.1f}s]")

    # Every parameter moves at once, by design. See sensitivity.py.
    # Verified against StrategyConfig rather than guessed -- a bad dotted path
    # raises at construction, which is how the fourth one here was caught.
    # These are the four that decide how much is risked and when it is cut.
    paths = (
        "sizing.risk_per_trade_pct",
        "sizing.max_position_pct_of_equity",
        "risk.max_portfolio_heat_pct",
        "exits.initial_stop_atr_multiple",
    )
    perturbation = ParameterPerturbation(
        base=base_config,
        parameters=paths,
        rerun=lambda config: build(config).run(spec),
    )
    mc_spec = MonteCarloSpec(
        method="parameter_perturbation",
        iterations=args.perturb_iterations,
        perturbation_pct=args.perturbation_pct,
    )
    print(
        f"\nperturbing {len(paths)} parameters by +/-{args.perturbation_pct:.0%}, all at once, "
        f"{args.perturb_iterations} re-runs ..."
    )
    t0 = time.time()
    report = perturbation.report(base_result, mc_spec)
    print(f"  [{time.time() - t0:.1f}s]")
    print("\n--- fragility ---")
    print(f"  baseline return     {report.baseline_return_pct:>9.2%}")
    median = report.median_return_pct
    print(f"  median jittered     {median:>9.2%}" if median is not None else "  median jittered --")
    fragility = report.fragility
    if fragility is None:
        print("  fragility           undefined -- the baseline made nothing to lose")
    else:
        print(f"  fragility           {fragility:>9.2%} of the edge gone at the median draw")
        verdict = report.survives(args.max_fragility)
        print(f"  survives {args.max_fragility:.0%}?       {verdict}")
    print(f"\n{report.explain()}")
    print(
        "\nNothing here is evidence: fifteen large-cap survivors and a baseline rule.\n"
        "Fragility is the question a hold-out cannot ask -- how much of the edge\n"
        "survives moving every parameter at once."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
