#!/usr/bin/env python
"""A paper session where the venue is the book, not a replay of one.

The equivalence run of 2026-10-18 replayed the backtester's own orders through
the venue and found 1,457 of 1,457 fills identical. That proves the two produce
the same fill for the same order, and it **cannot** find a defect in the loop:
in what we believe we hold, in what we therefore decide next, or in whether a
drift is ever noticed. A replay has no loop -- the decisions were already made.

This runs :class:`~tradeit.execution.live_shape.LiveShapedSession`, where the
broker owns the book, our state is an independent model updated only from
reported fills, and reconciliation runs every session before anything is
decided.

What a disagreement would mean here
-----------------------------------

In the replay, a disagreement meant the venue's arithmetic differed from the
backtester's. Here it means **our model of the book has drifted from the
broker's**, which is the failure the halt exists for and the one a backtest
structurally cannot have, because there the book and the venue are the same
object.

So the measurement is: how many sessions reconcile clean, and if any do not,
what drifted and when. A run that halts early is not a failed run -- it is the
halt working, and the drift is the finding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.config import Settings, TradingMode
from tradeit.execution.live_shape import LiveShapedSession
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
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
    print(f"universe: {len(resolved)} of {len(tickers)} resolved, {len(sectors)} classified")

    rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
    data = CorpusSessionData(
        session=session,
        universe=tuple(sorted(resolved.values())),
        start=start,
        end=end,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on

    costs = ParticipationCostModel(config=config.costs)
    fills = BarFillModel(max_participation=Decimal("0.02"))
    broker = PaperBroker(
        account_id="live-shape",
        costs=costs,
        fills=fills,
        starting_cash=args.capital,
        settings=Settings(trading_mode=TradingMode.PAPER),
        symbols={v: k for k, v in resolved.items()},
    )
    service = BrokerExecutionService(
        broker=broker,
        session=session,
        # Looser than the unit tests': ten years of commission splits on
        # hundreds of fills accumulate more rounding than a two-fill test, and
        # a tolerance tight enough to halt on that would measure arithmetic
        # noise rather than drift. Stated because a tolerance nobody chose is a
        # tolerance nobody can defend.
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("1.00"),
    )
    driver = LiveShapedSession(
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
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        service=service,
        broker=broker,
        initial_capital=args.capital,
    )

    sessions = list(data.sessions(start, end))
    print(f"\ndriving {len(sessions):,} sessions with the venue as the book ...")
    t0 = time.time()
    clean = 0
    orders = 0
    filled = 0
    halted_on: dt.date | None = None
    quiet = 0
    for session_date in sessions:
        bars = data.bars(session_date)
        outcome = driver.run_session(
            session_date,
            bars,
            data.candidates(session_date),
            data.splits_on(session_date),
        )
        orders += outcome.orders_placed
        filled += len(outcome.fills)
        if outcome.discrepancies == 0:
            clean += 1
        if not outcome.fills and not outcome.orders_placed:
            quiet += 1
        if outcome.halted:
            halted_on = session_date
            print(f"\n  HALTED on {session_date} after {outcome.discrepancies} discrepancies:")
            for item in service.halt_reason(driver.portfolio_id):
                print(f"    {item.describe()}")
            break
    elapsed = time.time() - t0

    print(f"\n  {clean:,} of {len(sessions):,} sessions reconciled clean [{elapsed:.1f}s]")
    print(f"  {orders:,} orders placed, {filled:,} fills received")
    print(f"  {quiet:,} sessions did nothing at all")
    print(
        f"  {driver.premise_failed:,} entries gapped through the stop they were decided\n"
        "    with. The backtester REFUSES those outright; a live system cannot,\n"
        "    because the shares are already bought -- so the stop is placed below the\n"
        "    fill and the premise failure is counted."
    )
    print(f"  final equity {driver.equity:,.2f} from {args.capital:,}")
    print(f"  broker equity {broker.equity:,.2f}, positions {len(broker.list_positions())}")

    if halted_on is None and clean == len(sessions):
        print(
            "\n  Our model and the broker's book agreed on every session of a run where\n"
            "  the broker owned the book. That is the loop working, and it is a\n"
            "  different claim from the replay's: the replay showed identical fills for\n"
            "  identical orders, this shows our BELIEF about the book never drifted."
        )
    elif halted_on is not None:
        print(
            "\n  The halt fired. That is the control working, and the drift above is the\n"
            "  finding -- not a failed run."
        )
    session.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
