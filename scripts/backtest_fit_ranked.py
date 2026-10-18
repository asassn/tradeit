#!/usr/bin/env python
"""Score 3 in the decision path, on real prices, against the same run without it.

Authorised 2026-10-18. Until now every portfolio result this project published
was ranked by ``DiversityAwareRanker`` with ``correlation_weight=0.0`` and
``sector_penalty_per_holding=0.0`` -- diversification off, the three-score
separation measured beside the decision and never inside it.

This runs the same moving-average baseline twice, changing exactly one thing:
the ranker. Everything else -- universe, window, sizing, stops, costs, fills,
delisting assumption, risk rules -- is identical, so the difference is
attributable.

**What is expected, and why saying so first matters.** ``score_fit``'s vetoes
overlap the risk engine deliberately: ``MostRestrictiveEngine`` already refuses
on position count, heat and sector exposure and remains the authority. So the
change should show up mostly in the **order** trades are taken, not in which
are admissible. A result where admissions barely move is the predicted
outcome, not a sign the wiring failed -- and predicting it here means it cannot
later be presented as a discovery.

**The standalone score must be normalised.** ``MovingAverageCross`` puts a raw
spread fraction in ``OpportunityScore.total``, and ``OpportunityFitRanker``
refuses to blend an out-of-range total rather than rescaling behind the
caller's back. The range is declared on the command line and recorded in the
output, because a blend whose scale came from nowhere is a number that cannot
be traced.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_correlation_limit import build_correlations
from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.opportunity import OpportunityFitRanker, normalise_total
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import AllocationCandidate, PortfolioState
from tradeit.portfolio.cycle import PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector FROM sectors
    WHERE scheme = 'SIC' AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


class NormalisingFitRanker(OpportunityFitRanker):
    """The fit ranker, with the scale stated at the boundary.

    Subclassed rather than built into the ranker on purpose: the ranker refuses
    an out-of-range total so that the *caller* has to say what range its scorer
    produces, and a default would defeat that. Here the range is a declared
    argument and is printed with the result.
    """

    def __init__(self, *args: object, lower: float, upper: float, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        object.__setattr__(self, "_lower", lower)
        object.__setattr__(self, "_upper", upper)

    def rank(self, candidates, portfolio, correlation=None):  # type: ignore[no-untyped-def]
        rescaled = [
            AllocationCandidate(
                score=normalise_total(
                    candidate.score,
                    lower=self._lower,  # type: ignore[attr-defined]
                    upper=self._upper,  # type: ignore[attr-defined]
                ),
                sizing=candidate.sizing,
                correlation_penalty=candidate.correlation_penalty,
                sector=candidate.sector,
            )
            for candidate in candidates
        ]
        return super().rank(rescaled, portfolio, correlation)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--standalone-weight", type=float, default=None)
    ap.add_argument(
        "--score-lower",
        type=float,
        default=0.0,
        help="declared minimum of the standalone scorer's range",
    )
    ap.add_argument(
        "--score-upper",
        type=float,
        default=0.20,
        help="declared maximum: the MA spread as a fraction of the slow mean",
    )
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    resolved = _universe(session, tickers)
    config = StrategyConfig(name="ma_cross_baseline")
    universe_ids = set(resolved.values())
    rows = session.execute(SECTORS_AS_OF, {"as_of": start.isoformat()}).all()
    sectors = {int(i): str(s) for i, s in rows if int(i) in universe_ids}
    weight = config.risk.max_sector_exposure_pct if args.standalone_weight is None else None
    del weight
    print(f"universe: {len(resolved)} of {len(tickers)} resolved, {len(sectors)} classified")
    print(
        f"standalone range declared as [{args.score_lower}, {args.score_upper}] "
        "-- the MA spread over the slow mean"
    )

    def make_data() -> tuple[CorpusSessionData, MovingAverageCross]:
        rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
        data = CorpusSessionData(
            session=session,
            universe=tuple(sorted(resolved.values())),
            start=start,
            end=end,
            candidate_source=rule,
        )
        rule.splits_on = data.splits_on
        return data, rule

    probe, _ = make_data()
    correlation = build_correlations(probe, list(probe.sessions(start, end)), 126)
    print(f"correlation source: {len(correlation):,} measured pairs")

    def run(label: str, fit_ranked: bool) -> tuple[int, float, float]:
        run_data, _ = make_data()
        if fit_ranked:
            kwargs: dict[str, object] = {
                "risk": config.risk,
                "sizing": config.sizing,
                "sectors": sectors,
                "lower": args.score_lower,
                "upper": args.score_upper,
            }
            if args.standalone_weight is not None:
                kwargs["standalone_weight"] = args.standalone_weight
            ranker: object = NormalisingFitRanker(**kwargs)  # type: ignore[arg-type]
        else:
            ranker = DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            )
        engine = EventDrivenEngine(
            cycle=PortfolioCycle(
                sizer=RiskBasedSizer(
                    sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02")
                ),
                engine=MostRestrictiveEngine(
                    rule_set=(
                        PortfolioHeatRule(config=config.risk),
                        MaxPositionsRule(config=config.risk),
                        PositionSizeRule(
                            max_position_pct_of_equity=config.sizing.max_position_pct_of_equity
                        ),
                        GrossExposureRule(config=config.risk),
                        SectorExposureRule(config=config.risk, sectors=sectors),
                    ),
                    sizing=config.sizing,
                ),
                ranker=ranker,  # type: ignore[arg-type]
                ladder=StopLadder(config=config.exits),
            ),
            costs=ParticipationCostModel(config=config.costs),
            fills=BarFillModel(max_participation=Decimal("0.02")),
            data=run_data,
            analyzer=StandardPerformanceAnalyzer(
                annualisation_factor=config.indicators.annualisation_factor,
                risk_free_rate=0.02,
            ),
            delisting_after_sessions=10,
            delisting_recovery=Decimal(1),
            manifest=_manifest(config, dt.datetime.now(dt.UTC), run_data.bar_count),
        )
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
        t0 = time.time()
        result = engine.run(spec)
        metrics = result.metrics
        ret = metrics.total_return_pct if metrics else 0.0
        sharpe = metrics.sharpe if metrics and metrics.sharpe else 0.0
        print(
            f"  {label:<26} {len(result.trades):>5,} trades   return {ret:>8.2%}"
            f"   sharpe {sharpe:>6.2f}   [{time.time() - t0:.1f}s]"
        )
        return len(result.trades), ret, sharpe

    print("\nthe same run, differing only in the ranker:")
    base = run("DiversityAware (as before)", False)
    fit = run("OpportunityFitRanker", True)

    print()
    if base[0] == fit[0] and abs(base[1] - fit[1]) < 1e-9:
        print(
            "  IDENTICAL trades and return. Either the ranker is inert or this universe\n"
            "  never offers a choice -- a slate of one cannot be reordered. Check the\n"
            "  contested-slate count from exercise_opportunity_scoring.py before\n"
            "  concluding anything about the wiring."
        )
    else:
        print(
            f"  trades {base[0]:,} -> {fit[0]:,}   return {base[1]:.2%} -> {fit[1]:.2%}"
            f"   sharpe {base[2]:.2f} -> {fit[2]:.2f}"
        )
        print(
            "\n  The ranker is in the decision path and the decisions differ. **This is\n"
            "  not evidence that score 3 earns anything**: one universe, one window, one\n"
            "  in-sample run, no registration, no ledger spend. It is evidence that the\n"
            "  wiring is real, which is what was authorised."
        )
    _ = PortfolioState
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
