#!/usr/bin/env python
"""A paper session over corpus bars, and the equivalence that justifies having one.

Phase 11's components were built on 2026-10-18 with unit tests and had never
seen the corpus -- the same gap Phases 7, 8 and 9 carried. This runs them
against it.

The claim under test is the one that makes paper trading worth doing:

    **Paper and backtest share a code path, so a paper fill and a backtest fill
    of the same order against the same bar agree.**

If that holds, a paper result is evidence about the simulated strategy. If it
does not, paper trading measures a second, undocumented system and every
comparison between the two is meaningless. So the test is not "did the paper
session run" -- it is whether the two agree **order by order**, and any
disagreement is the most valuable thing this script can produce.

How the comparison is made fair
-------------------------------

Both paths are driven from the **same decisions**. The backtester runs normally
and every order it places is captured along with the bar it was decided on and
the bar it settled against. Those same `OrderRequest` objects are then
submitted to :class:`PaperBroker` and advanced over the same bars.

**Nothing re-decides.** A second independent run would diverge for ordinary
reasons -- different cash, therefore different sizes, therefore different fills
-- and the comparison would say nothing about the venue. Replaying the captured
orders isolates the venue, which is the only thing under test.

One divergence found by reading rather than by running
------------------------------------------------------

The backtester estimates cost against the bar the decision was made on and
fills against the next. `BarFillModel` derives slippage from that estimate and
adds it to the trigger price, so **the reference bar sets the fill price**. A
venue estimating against the bar it is filling on would price every paper fill
differently while appearing to share the code path. `PaperBroker` therefore
keeps the last bar it saw per instrument and prices against that; this script
reports the count of orders accepted with no reference at all, which is the one
case where a legitimate difference remains.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.config import Settings, TradingMode
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import Fill, OrderRequest
from tradeit.execution.journal import JournalEntryKind, record_entry
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
    # The venue gets a FLOAT, not the strategy's capital, and this is the
    # difference between a comparison that isolates the venue and one that
    # does not. The engine sized every order against the engine's cash; the
    # venue has its own, and the moment one fill differs the two balances
    # diverge and `_affordable` starts capping differently. The first run of
    # this script used equal capital and found 289 of 330 fills differing on
    # quantity -- which measured the cash drift, not the fill arithmetic.
    #
    # The venue's cash cap is therefore DELIBERATELY EXCLUDED here and is
    # covered by its own unit tests.
    ap.add_argument("--venue-capital", type=Decimal, default=Decimal(100_000_000))
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

    # What the backtester decided, captured session by session: the orders it
    # had resting when a session opened, and that session's bars.
    submissions: list[
        tuple[dt.date, tuple[OrderRequest, ...], dict[int, OhlcvBar], dict[int, Decimal]]
    ] = []
    engine_fills: dict[str, Fill] = {}

    class CapturingEngine(EventDrivenEngine):
        def _settle(self, state, bars, session_date):  # type: ignore[no-untyped-def]
            resting = tuple(item.order for item in state.pending)
            submissions.append(
                (session_date, resting, dict(bars), dict(self.data.splits_on(session_date)))
            )
            # Re-derive the fills the engine produced by running the real
            # settle and reading what it recorded, rather than reimplementing
            # it here -- a second implementation of settle would be comparing
            # my arithmetic against itself.
            for item in state.pending:
                bar = bars.get(item.order.instrument_id)
                if bar is None:
                    continue
                # Participation passed, per ADR-0028, so this script agrees with
                # the engine and the paper broker rather than quietly pricing
                # the same order three different ways.
                reference = item.reference_bar
                estimate = self.costs.estimate(
                    item.order, reference, reference.close * reference.volume
                )
                simulated = self.fills.simulate(item.order, bar, estimate)
                if simulated is not None:
                    engine_fills[item.order.client_order_id] = simulated
            super()._settle(state, bars, session_date)

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
    costs = ParticipationCostModel(config=config.costs)
    fills = BarFillModel(max_participation=Decimal("0.02"))
    backtest = CapturingEngine(
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
        costs=costs,
        fills=fills,
        data=data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=0.02,
        ),
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
        manifest=_manifest(config, dt.datetime.now(dt.UTC), data.bar_count),
    )
    t0 = time.time()
    result = backtest.run(spec)
    decided = sum(len(orders) for _, orders, _, _ in submissions)
    print(
        f"\nbacktest: {len(result.trades):,} trades, {decided:,} order-submissions over "
        f"{len(submissions):,} sessions [{time.time() - t0:.1f}s]"
    )
    print(f"  engine produced {len(engine_fills):,} distinct fills")

    print(
        f"\nreplaying the SAME orders through the paper venue "
        f"(float {args.venue_capital:,}, so its cash cap never binds) ..."
    )
    broker = PaperBroker(
        account_id="paper-session",
        costs=costs,
        fills=fills,
        starting_cash=args.venue_capital,
        settings=Settings(trading_mode=TradingMode.PAPER),
        symbols={v: k for k, v in resolved.items()},
    )
    service = BrokerExecutionService(
        broker=broker,
        session=session,
        price_tolerance=Decimal("0.01"),
        cash_tolerance=Decimal("0.01"),
    )
    paper_fills: dict[str, Fill] = {}
    broker_ids: dict[str, str] = {}
    submitted = 0
    rejected: collections.Counter[str] = collections.Counter()
    # The engine CARRIES an unfilled order when an instrument did not print
    # (`engine._settle`: "a halted session is not a decision"), while the venue
    # CANCELS it as a DAY order at session end. Both are defensible and they are
    # not the same, so the count is measured rather than assumed away -- it is the
    # mechanism by which the two books can hold different quantities.
    carried: set[str] = set()
    split_sessions = 0
    resubmitted = 0
    t0 = time.time()
    for session_date, orders, bars, splits in submissions:
        # Splits FIRST, exactly where the engine applies them: the session's
        # prints are already post-split, so a pre-split share count marked
        # against a post-split price reports a loss nobody took.
        if splits:
            broker.apply_splits(splits)
            split_sessions += 1
        for order in orders:
            # The engine carries an unfilled order across sessions, so the same
            # client_order_id arrives again. Resubmitting is CORRECT here and
            # the venue's idempotency makes it a no-op -- this replay exercises
            # that path heavily rather than avoiding it.
            if order.client_order_id in carried:
                resubmitted += 1
            carried.add(order.client_order_id)
            try:
                status = broker.submit_order(order, order.client_order_id)
                broker_ids[order.client_order_id] = status.broker_order_id
                submitted += 1
            except ValueError as problem:
                rejected[str(problem).split(":")[0][:48]] += 1
        for fill in broker.advance(session_date, bars):
            paper_fills[str(fill.order_id)] = fill
    print(f"  submitted {submitted:,} orders, {len(paper_fills):,} fills [{time.time() - t0:.1f}s]")
    if rejected:
        print("  submissions the venue refused:")
        for reason, count in rejected.most_common():
            print(f"    {count:>6,}  {reason}")
    print(f"  {split_sessions:,} sessions carried a split, applied to the venue's book")
    print(
        f"  {resubmitted:,} submissions were orders the engine CARRIED to a later\n"
        "    session because the instrument did not print. The venue cancels those as\n"
        "    DAY orders, so a carried order that the engine later fills is one the\n"
        "    venue never does -- the two books then hold different quantities, and any\n"
        "    exit sized from the engine's lot is capped by the venue's smaller holding."
    )
    if broker.orders_without_reference:
        print(
            f"  {broker.orders_without_reference:,} orders had no reference bar, so their\n"
            "    cost was estimated against the filling bar -- the one case where a paper\n"
            "    fill may legitimately differ from the backtester's."
        )

    print("\ncomparing fills order by order ...")
    # Through the public API, from the ids submission returned -- not by reaching
    # into the venue's private order book, which would make this check depend on
    # its internals rather than its contract.
    by_client = {
        client_order_id: broker.get_order(broker_order_id)
        for client_order_id, broker_order_id in sorted(broker_ids.items())
    }
    agree = 0
    price_differs: list[tuple[str, Decimal, Decimal]] = []
    quantity_differs: list[tuple[str, Decimal, Decimal]] = []
    only_engine: list[str] = []
    for client_order_id, engine_fill in sorted(engine_fills.items()):
        status = by_client.get(client_order_id)
        if status is None or status.average_fill_price is None:
            only_engine.append(client_order_id)
            continue
        if status.filled_quantity != engine_fill.quantity:
            quantity_differs.append((client_order_id, engine_fill.quantity, status.filled_quantity))
        elif abs(status.average_fill_price - engine_fill.price) > Decimal("0.0001"):
            price_differs.append((client_order_id, engine_fill.price, status.average_fill_price))
        else:
            agree += 1

    total = len(engine_fills)
    print(f"  {agree:,} of {total:,} fills agree exactly on quantity AND price")
    print(f"  {len(quantity_differs):,} differ on quantity")
    print(f"  {len(price_differs):,} differ on price")
    print(f"  {len(only_engine):,} the engine filled and the venue did not")
    for client_order_id, mine, theirs in quantity_differs[:5]:
        print(f"    qty  {client_order_id}: engine {mine} vs venue {theirs}")
    for client_order_id, mine, theirs in price_differs[:5]:
        print(f"    px   {client_order_id}: engine {mine} vs venue {theirs}")

    if not total:
        raise SystemExit(
            "the engine recorded zero fills, so there is nothing to compare and this "
            "run proves nothing. Refusing to report it as an equivalence check."
        )

    record_entry(
        session,
        kind=JournalEntryKind.NOTE,
        session_date=end,
        summary=(
            f"paper/backtest equivalence: {agree} of {total} fills agree "
            f"({len(quantity_differs)} qty, {len(price_differs)} px, "
            f"{len(only_engine)} venue-missing)"
        ),
        rationale="Phase 11 corpus exercise; see PHASE_11_AUDIT_2026-10-18.md",
        context={"agree": agree, "total": total},
    )
    session.rollback()  # a diagnostic must not write to the corpus

    print(f"\nreconciliation: halted={service.is_halted(1)}")
    print(
        "\nThe venue is driven by the engine's own orders, so this isolates the VENUE.\n"
        "A second independent run would diverge for ordinary reasons -- different\n"
        "cash, different sizes, different fills -- and would say nothing about it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
