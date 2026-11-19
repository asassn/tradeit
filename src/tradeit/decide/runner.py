"""Turn a declared portfolio into a plan, over the corpus.

One place, because the command line and the web service must not drift. A
second copy of this wiring would be a second set of answers to *which ranker,
which rules, which declared score range* — and the whole point of the ranker
swap is that those answers are now consequential.

**Sessions are walked in order, exactly once.** The candidate source detects a
crossover, which is a transition between two sessions, and it carries the state
that defines one. Asking it for an earlier session after the walk has advanced
returns a confidently wrong answer: a counting pass over 2015 found a candidate
on 27 November, and re-querying that date returned none, because ``_was_above``
had already been set by the rest of the year. The walk therefore stops at the
session being planned and keeps that session's candidates as it passes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from tradeit.backtesting.baselines import MovingAverageCross
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import Bartimeframe, PositionStatus, Side
from tradeit.decide.declaration import DeclarationError, DeclaredPortfolio
from tradeit.decide.plan import AVAILABLE_CORPORA, SessionPlan, plan_session
from tradeit.opportunity import OpportunityFitRanker, normalise_total
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.mandate import spec as mandate_spec
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopContext, StopLadder
from tradeit.risk.base import RiskRule
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.strategy.config import StrategyConfig

__all__ = ["PlanResult", "build_cycle", "plan_declared", "resolve_universe"]

_TICKERS = text(
    "select alias_value, security_id from symbol_aliases "
    "where alias_kind = 'ticker' and alias_value in :t order by valid_from"
)
_SECTORS = text(
    "select instrument_id, sector from sectors where scheme = 'SIC' "
    "and valid_from <= :as_of and (valid_to is null or valid_to > :as_of)"
)


@dataclasses.dataclass(frozen=True, slots=True)
class PlanResult:
    """A plan and what it cost to produce, including what was lost on the way."""

    plan: SessionPlan
    #: Tickers that resolved to no security. **Reported, never dropped
    #: silently**: a universe quietly smaller than the one declared produces a
    #: plan about a portfolio nobody described. Five of twenty did not resolve
    #: in the first real run.
    unresolved: tuple[str, ...]
    sectors_classified: int
    sessions_loaded: int
    bars_loaded: int
    seconds: float


def resolve_universe(session: Session, tickers: tuple[str, ...]) -> dict[str, int]:
    """Resolve tickers to security ids, the **earliest** alias interval winning.

    Deterministic rather than whichever row the database returns first. One
    ticker can belong to two unrelated issuers across time, and an arbitrary
    choice would price one company while planning trades for another — the
    splice this corpus exists to prevent, arriving as a universe entry.
    """
    if not tickers:
        return {}
    rows = session.execute(
        _TICKERS.bindparams(bindparam("t", value=list(tickers), expanding=True))
    ).all()
    found: dict[str, int] = {}
    for ticker, security_id in rows:
        found.setdefault(str(ticker), int(security_id))
    return found


def build_cycle(config: StrategyConfig, sectors: dict[int, str]) -> PortfolioCycle:
    """The cycle, ranked by **score 3** rather than by score 1.

    This is the swap that makes the system a portfolio manager rather than a
    stock picker. Every portfolio result this project has published was ranked
    by ``DiversityAwareRanker(correlation_weight=0.0,
    sector_penalty_per_holding=0.0, held_sectors={})`` — pure score order with
    the portfolio awareness switched off — and ``OpportunityFitRanker`` existed
    the whole time, called only by a demonstration script.
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


def plan_declared(session: Session, declared: DeclaredPortfolio) -> PlanResult:
    """Plan one declared portfolio for its declared session.

    Raises :class:`DeclarationError` when the declaration cannot be honoured
    against this corpus — no ticker resolving, the as-of date not being a
    trading session, or a declared holding having no print that day. The last
    is refused rather than skipped: a held name with no price cannot have its
    stop managed, and the cycle says so itself rather than guessing.
    """
    started = dt.datetime.now(dt.UTC)

    # **A horizon whose primary corpus does not exist declines to be scored.**
    #
    # Day's primary corpus is `intraday-01`, which is deferred (ADR-0030). Daily
    # bars *are* in Day's eligible set, so a Daily slate would be admissible and
    # would produce a plausible plan — but Day's declared hierarchy is context
    # D1/H1, setup H1/M15, trigger M5/M1, so both its setup and trigger layers
    # would be entirely absent. The result would be a Daily swing plan wearing a
    # day-trading label, which is precisely the collapse of three horizons into
    # one that the architecture exists to prevent.
    spec = mandate_spec(declared.mandate)
    if spec.primary_corpus not in AVAILABLE_CORPORA:
        raise DeclarationError(
            f"{declared.mandate} declines to be scored: its primary corpus is "
            f"{spec.primary_corpus}, which does not exist (ADR-0030). Its setup and trigger "
            "layers would both be absent, so a Daily slate would be a swing plan wearing a "
            f"{declared.mandate} label"
        )

    resolved = resolve_universe(session, declared.universe)
    unresolved = tuple(sorted(set(declared.universe) - set(resolved)))
    if not resolved:
        raise DeclarationError(
            f"{declared.mandate}: none of {len(declared.universe)} tickers resolved against "
            "this corpus, so there is nothing to plan"
        )
    held_unresolved = sorted(h.ticker for h in declared.holdings if h.ticker not in resolved)
    if held_unresolved:
        raise DeclarationError(
            f"{declared.mandate}: {', '.join(held_unresolved)} is declared held but does not "
            "resolve against this corpus, so its stop could not be managed"
        )

    ids = set(resolved.values())
    sectors = {
        int(i): str(s)
        for i, s in session.execute(_SECTORS, {"as_of": declared.warmup_from.isoformat()}).all()
        if int(i) in ids
    }

    rule = MovingAverageCross(fast=declared.fast, slow=declared.slow, stop_pct=declared.stop_pct)
    data = CorpusSessionData(
        session=session,
        universe=tuple(sorted(ids)),
        start=declared.warmup_from,
        end=declared.as_of,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on

    sessions = list(data.sessions(declared.warmup_from, declared.as_of))
    if declared.as_of not in sessions:
        last = sessions[-1] if sessions else None
        raise DeclarationError(
            f"{declared.mandate}: {declared.as_of} is not a trading session in this corpus; "
            f"the last one on or before it is {last}"
        )

    # One ordered walk, stopping at the session being planned.
    kept: list[EntryCandidate] = []
    for day in sessions:
        bars = data.bars(day)
        candidates = [c for c in data.candidates(day) if c.instrument_id in bars]
        if day == declared.as_of:
            kept = candidates
            break
    bars = data.bars(declared.as_of)

    scored = [
        dataclasses.replace(
            candidate,
            score=normalise_total(
                candidate.score, lower=declared.score_lower, upper=declared.score_upper
            ),
        )
        for candidate in kept
    ]

    positions: list[PositionState] = []
    for holding in declared.holdings:
        instrument_id = resolved[holding.ticker]
        if instrument_id not in bars:
            raise DeclarationError(
                f"{declared.mandate}: {holding.ticker} has no print on {declared.as_of}, so its "
                "stop cannot be managed. The cycle refuses that case rather than guessing"
            )
        positions.append(
            PositionState(
                position_id=instrument_id,
                portfolio_id=1,
                instrument_id=instrument_id,
                side=Side.LONG,
                status=PositionStatus.OPEN,
                quantity=holding.quantity,
                average_entry_price=holding.entry_price,
                stop_price=holding.stop_price,
                opened_on=holding.opened_on,
                initial_stop_price=holding.stop_price,
            )
        )

    portfolio = PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(declared.as_of, dt.time(21), tzinfo=dt.UTC),
        cash=declared.cash,
        equity=declared.equity,
        positions=tuple(positions),
        last_prices={p.instrument_id: bars[p.instrument_id].close for p in positions},
    )
    held_sessions = {resolved[h.ticker]: h.sessions_held for h in declared.holdings}
    contexts = {
        p.instrument_id: StopContext(
            last_price=bars[p.instrument_id].close,
            high_since_entry=max(bars[p.instrument_id].high, p.average_entry_price),
            sessions_held=held_sessions[p.instrument_id],
            partial_profit_taken=False,
            # None, deliberately, and reported as an absent control: the
            # ladder's trailing rung engages only on ATR, and this path has
            # none to give it.
            atr=None,
        )
        for p in portfolio.open_positions
    }

    plan = plan_session(
        mandate=declared.mandate,
        timeframe=Bartimeframe.D1,
        session_date=declared.as_of,
        cycle=build_cycle(StrategyConfig(name="ma_cross_baseline"), sectors),
        portfolio=portfolio,
        candidates=scored,
        contexts=contexts,
        references={i: t for t, i in resolved.items()},
    )
    return PlanResult(
        plan=plan,
        unresolved=unresolved,
        sectors_classified=len(sectors),
        sessions_loaded=len(sessions),
        bars_loaded=data.bar_count,
        seconds=(dt.datetime.now(dt.UTC) - started).total_seconds(),
    )
