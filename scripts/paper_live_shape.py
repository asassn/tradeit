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

Stopping and resuming
---------------------

With ``--record`` the book is written down at the close of every session --
`positions` kept current, one `portfolio_snapshots` row and one
`risk_snapshots` row per session, committed each time so the process can die
at any point. With ``--resume`` the next run continues that book instead of
starting flat, which is what makes a paper run a *run* rather than a series of
unrelated days.

Two things are re-derived rather than resumed, and the distinction is the whole
design:

* **The stop ladder is not re-derivable**, so it is stored. A high since entry
  needs every bar of the holding period replayed, and a trailing stop resumed
  from a reset high is looser than the one that was in force. A position whose
  high was never recorded is **refused**, not defaulted.
* **The signal's own state is re-derivable**, so it is not stored. The moving
  averages are a function of prices alone, and a resumed run walks the sessions
  it has already recorded through the candidate source without deciding on
  them -- otherwise the first session back would see a crossing that never
  happened.
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
from tradeit.execution.live_shape import LadderState, LiveShapedSession
from tradeit.execution.paper import PaperBroker
from tradeit.execution.service import BrokerExecutionService
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.observability.portfolio_record import (
    last_recorded_session,
    load_ladder_state,
    load_portfolio_state,
    portfolio_named,
    record_portfolio,
    record_portfolio_snapshot,
    record_risk_snapshot,
    recorded_peak_equity,
    sync_positions,
)
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import PortfolioState
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
from tradeit.risk.snapshot import build_snapshot
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import Base
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
    ap.add_argument(
        "--record",
        default=None,
        help=(
            "sqlite file the BOOK is written to, separate from --db, which stays "
            "read-only. Omit to run without persisting, as every run before "
            "2026-11-24 did."
        ),
    )
    ap.add_argument(
        "--name",
        default="live-shape-paper",
        help="portfolio name within the record; how a run says which book it is",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="continue the recorded book rather than starting flat",
    )
    args = ap.parse_args()
    if args.resume and not args.record:
        ap.error("--resume needs --record: there is nothing to resume from without one")

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

    record: Session | None = None
    portfolio_id = 1
    peak: Decimal | None = None
    resume_from: PortfolioState | None = None
    ladder: dict[int, LadderState] = {}
    resume_after: dt.date | None = None
    if args.record:
        # `create_all` rather than a migration, matching `record_decisions.py`:
        # this is a scratch record an operator names, not the project database.
        # The three ladder columns are on the ORM and in migration 0022, so the
        # two agree.
        record_engine = install_sqlite_busy_timeout(
            create_engine(f"sqlite:///{args.record}", future=True)
        )
        Base.metadata.create_all(record_engine)
        record = sessionmaker(bind=record_engine, future=True)()
        row = portfolio_named(record, args.name)
        if row is None:
            row = record_portfolio(
                record,
                name=args.name,
                mode=TradingMode.PAPER,
                initial_capital=args.capital,
                inception_date=start,
                broker_account_ref="live-shape",
                strategy_id=None,
            )
            record.commit()
            print(f"record : {args.record} (portfolio {row.id} created, name {args.name!r})")
        else:
            print(f"record : {args.record} (portfolio {row.id} continued, name {args.name!r})")
        portfolio_id = int(row.id)
        # Read once, from the record, so it survives the restart. A peak that
        # reset on every restart would understate every later drawdown, and
        # understating drawdown is the direction that gets a strategy promoted.
        peak = recorded_peak_equity(record, portfolio_id)
        if not args.resume and last_recorded_session(record, portfolio_id) is not None:
            # Refused here rather than mid-run. Without this the first session
            # drives, decides, places orders and only then hits
            # `SnapshotExists` -- a correct refusal in a confusing place, with
            # a partly-driven run behind it.
            ap.error(
                f"{args.name!r} already has sessions recorded up to "
                f"{last_recorded_session(record, portfolio_id)}. Pass --resume to continue "
                "it, or --name something else to start a separate book; overwriting a "
                "recorded session is a contradiction, not an update"
            )
    if args.resume and record is not None:
        resume_after = last_recorded_session(record, portfolio_id)
        if resume_after is None:
            print("  nothing recorded for this portfolio yet, so this run starts flat")
        else:
            # Prices the record does not hold: a snapshot states cash and
            # equity as of its session, not what each holding last printed.
            prices = (
                {i: bar.close for i, bar in data.bars(resume_after).items()}
                if start <= resume_after <= end
                else {}
            )
            resume_from = load_portfolio_state(
                record,
                portfolio_id,
                as_of=dt.datetime.combine(resume_after, dt.time(20), tzinfo=dt.UTC),
                last_prices=prices,
            )
            ladder = load_ladder_state(record, portfolio_id)
            held = 0 if resume_from is None else len(resume_from.open_positions)
            print(
                f"  resuming after {resume_after}: {held} position(s), "
                f"{len(ladder)} with a recorded ladder"
            )

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
    if resume_from is not None:
        # The venue has no memory of its own, so without this the restored
        # book would reconcile against an empty one and halt on the first
        # session -- the simulation having forgotten, not a drift. See
        # `PaperBroker.restore` for what that costs: at the restart boundary
        # both books come from the same rows, so the first session back cannot
        # detect a drift incurred across the restart.
        broker.restore(
            cash=resume_from.cash,
            holdings={
                position.instrument_id: (position.quantity, position.average_entry_price)
                for position in resume_from.open_positions
            },
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
        portfolio_id=portfolio_id,
        initial_capital=args.capital,
        resume_from=resume_from,
        ladder_state=ladder,
    )

    sessions = list(data.sessions(start, end))
    if resume_after is not None:
        already = [day for day in sessions if day <= resume_after]
        sessions = [day for day in sessions if day > resume_after]
        # **Walked, not skipped.** The moving averages are a function of prices
        # alone and so are re-derivable -- but only by being shown the same
        # sessions in the same order. Jumping straight to the first new session
        # would leave `_was_above` empty and manufacture a crossing on it.
        # Nothing here decides: the candidates are discarded.
        for day in already:
            data.candidates(day)
        print(f"  replayed {len(already):,} recorded sessions through the signal, deciding nothing")
    print(f"\ndriving {len(sessions):,} sessions with the venue as the book ...")
    t0 = time.time()
    clean = 0
    orders = 0
    filled = 0
    halted_on: dt.date | None = None
    quiet = 0
    opened = updated = closed = 0
    written = 0
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
        if record is not None:
            # **Written even on a halted session, and even on a quiet one.**
            # The book at the moment of a halt is the evidence; and
            # `risk_snapshots` exists for the quiet periods, which is where it
            # answers "was that drawdown a control failure or a run of losses?"
            state = driver.state()
            peak = state.equity if peak is None else max(peak, state.equity)
            change = sync_positions(
                record,
                portfolio_id,
                state.open_positions,
                driver.ladder(),
                closed_on=session_date,
            )
            opened += change.opened
            updated += change.updated
            closed += change.closed
            record_portfolio_snapshot(record, state, session_date=session_date, peak_equity=peak)
            record_risk_snapshot(
                record,
                build_snapshot(
                    state,
                    config.risk,
                    session_date=session_date,
                    peak_equity=peak,
                    sectors=sectors,
                    strategy_config_digest=config.digest,
                ),
            )
            # Committed per session, not at the end. A run that writes once at
            # the end has not been shown to survive anything: the thing it has
            # to survive is being killed mid-run.
            record.commit()
            written += 1
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
    if record is not None:
        peak_text = "none recorded" if peak is None else f"{peak:,.2f}"
        print(
            f"\n  {written:,} sessions written to {args.record} as portfolio {portfolio_id}\n"
            f"    positions: {opened:,} opened, {updated:,} updated, {closed:,} closed\n"
            f"    peak equity {peak_text}"
        )
        print(
            f"    re-run with --record {args.record} --name {args.name} --resume to\n"
            "    continue this book from the session after the last one written."
        )

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
    if record is not None:
        record.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
