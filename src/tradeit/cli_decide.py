"""``tradeit plan`` — what one portfolio should do today, and what it refused.

This is the first command in the project that runs the **portfolio manager**
path rather than a research path. Everything it prints comes from
:func:`tradeit.decide.plan_session`, which composes the parts that already
existed and had never been connected.

Every number it needs is **declared, never invented**. The portfolio's equity,
cash and holdings are arguments; the scoring range is an argument, because the
moving-average baseline puts a raw spread fraction in
``OpportunityScore.total`` and blending that with a fit score at a declared
weight would otherwise describe a mixture that is not happening; and the stop
basis is an argument, because an invented stop is an invented position size.

**Sessions are walked in order exactly once.** The candidate source detects a
*crossover*, which is a transition between two sessions, and it carries the
state that defines one. Asking it for an earlier session after the walk has
advanced returns a confidently wrong answer — a counting pass over 2015 found
a candidate on 27 November and re-querying that date returned none, because
``_was_above`` had already been set by the rest of the year. So the walk
happens once and the session of interest is kept as it goes by.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.baselines import MovingAverageCross
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import Bartimeframe, PositionStatus, Side
from tradeit.decide import plan_session
from tradeit.opportunity import OpportunityFitRanker, normalise_total
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.mandate import Mandate
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopContext, StopLadder
from tradeit.risk.base import RiskRule
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.strategy.config import StrategyConfig

__all__ = ["add_decide_commands"]

DEFAULT_CORPUS = Path("research01.sqlite")

_TICKERS = text(
    "select alias_value, security_id from symbol_aliases "
    "where alias_kind = 'ticker' and alias_value in :t order by valid_from"
)
_SECTORS = text(
    "select instrument_id, sector from sectors where scheme = 'SIC' "
    "and valid_from <= :as_of and (valid_to is null or valid_to > :as_of)"
)


def _universe(session: Session, tickers: Sequence[str]) -> dict[str, int]:
    """Resolve tickers to security ids, earliest alias interval winning.

    A ticker that resolves to nothing is **reported, not silently dropped** —
    a universe quietly smaller than the one asked for produces a plan about a
    portfolio nobody described.
    """
    rows = session.execute(
        _TICKERS.bindparams(bindparam("t", value=list(tickers), expanding=True))
    ).all()
    found: dict[str, int] = {}
    for ticker, security_id in rows:
        found.setdefault(str(ticker), int(security_id))
    return found


def _holding(spec: str, resolved: dict[str, int]) -> PositionState:
    """Parse ``TICKER:QUANTITY:ENTRY:STOP`` into a declared open position.

    All four parts are required. A holding without a stop has undefined risk,
    cannot be sized, cannot be summed into portfolio heat, and is therefore not
    a position this system will hold — ``PositionState`` says so itself.
    """
    parts = spec.split(":")
    if len(parts) != 4:
        raise SystemExit(
            f"--hold expects TICKER:QUANTITY:ENTRY:STOP, got {spec!r}. "
            "All four are required: a holding with no stop has undefined risk."
        )
    ticker, quantity, entry, stop = parts
    if ticker not in resolved:
        raise SystemExit(
            f"--hold {ticker} is not in --tickers, so it has no prices to mark against"
        )
    instrument_id = resolved[ticker]
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        opened_on=dt.date(1970, 1, 1),
        initial_stop_price=Decimal(stop),
    )


def _cycle(config: StrategyConfig, sectors: dict[int, str]) -> PortfolioCycle:
    """The cycle, ranked by **score 3** rather than by score 1.

    This is the swap that makes the system a portfolio manager rather than a
    stock picker. Every portfolio result this project has published was ranked
    by ``DiversityAwareRanker(correlation_weight=0.0,
    sector_penalty_per_holding=0.0, held_sectors={})`` — pure score order with
    the portfolio awareness switched off.
    """
    rules: tuple[RiskRule, ...] = (
        PortfolioHeatRule(config=config.risk),
        MaxPositionsRule(config=config.risk),
        GrossExposureRule(config=config.risk),
        SectorExposureRule(config=config.risk, sectors=sectors),
    )
    return PortfolioCycle(
        sizer=RiskBasedSizer(
            sizing=config.sizing,
            risk=config.risk,
            max_participation=Decimal("0.02"),
            costs=config.costs,
        ),
        engine=MostRestrictiveEngine(rule_set=rules, sizing=config.sizing),
        ranker=OpportunityFitRanker(risk=config.risk, sizing=config.sizing, sectors=sectors),
        ladder=StopLadder(config=config.exits),
    )


def cmd_plan(args: argparse.Namespace) -> int:
    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"no corpus at {corpus_path}")
        return 1

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None

    session = sessionmaker(bind=create_engine(f"sqlite:///{corpus_path}"))()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    resolved = _universe(session, tickers)
    missing = sorted(set(tickers) - set(resolved))
    if not resolved:
        print(f"none of {len(tickers)} tickers resolved against {corpus_path}")
        return 1
    print(f"universe : {len(resolved)} of {len(tickers)} resolved")
    if missing:
        print(f"           unresolved, and therefore not considered: {', '.join(missing)}")

    ids = set(resolved.values())
    sectors = {
        int(i): str(s)
        for i, s in session.execute(_SECTORS, {"as_of": start.isoformat()}).all()
        if int(i) in ids
    }
    print(f"sectors  : {len(sectors)} classified — {sorted(set(sectors.values()))}")

    config = StrategyConfig(name="ma_cross_baseline")
    rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=Decimal(str(args.stop_pct)))
    data = CorpusSessionData(
        session=session,
        universe=tuple(sorted(ids)),
        start=start,
        end=end,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on

    sessions = list(data.sessions(start, end))
    if not sessions:
        print(f"no sessions between {start} and {end}")
        return 1
    target = as_of or sessions[-1]
    if target not in sessions:
        print(f"{target} is not a trading session in this window; last is {sessions[-1]}")
        return 1

    # One ordered walk, stopping at the session being planned. See the module
    # docstring: the rule detects a transition and cannot be re-queried.
    kept: list[EntryCandidate] = []
    for day in sessions:
        bars = data.bars(day)
        candidates = [c for c in data.candidates(day) if c.instrument_id in bars]
        if day == target:
            kept = candidates
            break
    bars = data.bars(target)
    print(f"sessions : {len(sessions)} loaded, {data.bar_count} bars, planning {target}")

    # The declared range, as ADR-0027 requires of the ranker: a raw MA spread
    # is not a score, and `combine` would otherwise be dominated by the fit.
    scored = [
        dataclasses.replace(
            candidate,
            score=normalise_total(candidate.score, lower=args.score_lower, upper=args.score_upper),
        )
        for candidate in kept
    ]

    positions = tuple(_holding(spec, resolved) for spec in (args.hold or []))
    unpriced = sorted(p.instrument_id for p in positions if p.instrument_id not in bars)
    if unpriced:
        names = ", ".join(next(t for t, i in resolved.items() if i == iid) for iid in unpriced)
        print(
            f"           {names} has no print on {target}, so its stop cannot be managed. "
            "The cycle refuses that case rather than guessing; drop it or pick another date."
        )
        return 1

    portfolio = PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(target, dt.time(21), tzinfo=dt.UTC),
        cash=Decimal(str(args.cash)),
        equity=Decimal(str(args.equity)),
        positions=positions,
        last_prices={p.instrument_id: bars[p.instrument_id].close for p in positions},
    )
    contexts = {
        p.instrument_id: StopContext(
            last_price=bars[p.instrument_id].close,
            high_since_entry=max(bars[p.instrument_id].high, p.average_entry_price),
            sessions_held=args.sessions_held,
            partial_profit_taken=False,
            # None, deliberately and reported: the trailing rung engages only on
            # ATR, and this command has no ATR to give it.
            atr=None,
        )
        for p in portfolio.open_positions
    }

    plan = plan_session(
        mandate=Mandate(args.mandate),
        timeframe=Bartimeframe.D1,
        session_date=target,
        cycle=_cycle(config, sectors),
        portfolio=portfolio,
        candidates=scored,
        contexts=contexts,
        references={i: t for t, i in resolved.items()},
    )
    print()
    print(plan.describe())
    return 0


def add_decide_commands(sub: Any) -> None:
    plan = sub.add_parser(
        "plan", help="what one portfolio should do on one session, and what it refused"
    )
    plan.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="read only")
    plan.add_argument("--mandate", default="swing", choices=[str(m) for m in Mandate])
    plan.add_argument("--tickers", required=True, help="comma separated")
    plan.add_argument("--start", required=True, help="ISO date; needs --slow sessions of warmup")
    plan.add_argument("--end", required=True, help="ISO date")
    plan.add_argument("--as-of", default=None, help="session to plan (default: last in window)")
    plan.add_argument("--equity", default="100000")
    plan.add_argument("--cash", default="100000")
    plan.add_argument(
        "--hold",
        action="append",
        metavar="TICKER:QTY:ENTRY:STOP",
        help="a declared open position; repeatable. All four parts required.",
    )
    plan.add_argument("--sessions-held", type=int, default=1, help="for the time stop")
    plan.add_argument("--fast", type=int, default=50)
    plan.add_argument("--slow", type=int, default=200)
    plan.add_argument("--stop-pct", default="0.08")
    plan.add_argument(
        "--score-lower",
        type=float,
        default=0.0,
        help="declared lower bound of the raw setup score",
    )
    plan.add_argument(
        "--score-upper",
        type=float,
        default=0.5,
        help="declared upper bound; a raw MA spread is not a score",
    )
    plan.set_defaults(func=cmd_plan)
