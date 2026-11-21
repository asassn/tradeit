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
from collections.abc import Mapping
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from tradeit.backtesting.baselines import MovingAverageCross, base_rules
from tradeit.backtesting.corpus import CorpusSessionData, pairwise_correlations
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
from tradeit.risk.contextual import CorrelationRule, MatrixCorrelationSource
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.strategy.config import StrategyConfig

__all__ = ["PlanResult", "build_cycle", "plan_declared", "resolve_universe"]

#: **Resolution is per date, never per ticker** — `RESEARCH_01_DATA_DICTIONARY`
#: §0.2, and `valid_to` is **exclusive**, so the last day a security owns a
#: ticker is `valid_to - 1`.
_TICKERS = text(
    "select alias_value, security_id from symbol_aliases "
    "where alias_kind = 'ticker' and alias_value in :t "
    "  and valid_from <= :as_of and (valid_to is null or valid_to > :as_of) "
    "order by alias_value, valid_from"
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
    #: Holdings marked at a close older than the session being planned, as
    #: ``(ticker, session)``. **Carried, not refused** — the engine's rule, and
    #: its reasoning: a security that did not print today is carried at its last
    #: known price, which "is not a guess, it is the last thing anybody knew,
    #: and being unchanged from yesterday it cannot trigger a stop that
    #: yesterday did not". Reported per position, because a stale mark is a fact
    #: the reader should see rather than one the plan should hide.
    carried: tuple[tuple[str, dt.date], ...]
    #: Correlation pairs measured, and how many exceed the configured limit.
    #: **Coverage is reported because the rule fails closed**: an unmeasured
    #: pair refuses, so a plan that suddenly buys nothing is explicable from
    #: here rather than looking like a broken screen.
    correlation_pairs: int
    correlation_over_limit: int
    sectors_classified: int
    sessions_loaded: int
    bars_loaded: int
    seconds: float


def resolve_universe(
    session: Session, tickers: tuple[str, ...], as_of: dt.date
) -> tuple[dict[str, int], dict[str, str]]:
    """Resolve tickers **as of a date**, and refuse the ones that are ambiguous.

    Returns ``(resolved, unresolved)`` where ``unresolved`` maps a ticker to
    the reason it could not be used.

    **Resolution is per date, never per ticker.** That is
    `RESEARCH_01_DATA_DICTIONARY` §0.2, and this function previously ignored
    it: it took the earliest alias interval, which is deterministic but not
    correct. Measured 2026-11-19 over 2003-2009, that rule resolved ``DIS`` to
    the Walt Disney security whose prices begin in **2018**, so the name
    printed on 0 of 1,763 sessions — and because the backtest engine skips a
    whole session when any held name lacks a print, one such pick can suppress
    every decision in the run.

    **A ticker claimed by two securities on the same date is UNRESOLVED.**
    ``DIS`` is exactly that on 2015-11-27: two intervals overlap, one
    open-ended from 1990 and one running 1995 to 2019. Picking either would be
    inventing an identity, and `CLAUDE.md` is explicit that ambiguity beats
    fabricated certainty. ``valid_to`` is exclusive, so an interval ending on
    the as-of date does **not** cover it.
    """
    if not tickers:
        return {}, {}
    rows = session.execute(
        _TICKERS.bindparams(bindparam("t", value=list(tickers), expanding=True)),
        {"as_of": as_of.isoformat()},
    ).all()
    claims: dict[str, list[int]] = {}
    for ticker, security_id in rows:
        claims.setdefault(str(ticker), []).append(int(security_id))

    resolved: dict[str, int] = {}
    unresolved: dict[str, str] = {}
    for ticker in tickers:
        found = claims.get(ticker, [])
        if len(found) == 1:
            resolved[ticker] = found[0]
        elif not found:
            unresolved[ticker] = f"no security held this ticker on {as_of}"
        else:
            ids = ", ".join(str(i) for i in sorted(found))
            unresolved[ticker] = (
                f"{len(found)} securities claim this ticker on {as_of} ({ids}); "
                "picking one would invent an identity"
            )
    return resolved, unresolved


#: Controls the decision path applies **beyond the archive's set**.
#:
#: `base_rules` is frozen by what the published backtests ran, so it cannot
#: grow without changing what those results mean. This is where a control the
#: archive did not have is declared instead — and the plan reports the
#: difference on screen, because a reader comparing a plan against a scoreboard
#: number needs to know the two were not decided under the same constraints.
#:
#: `CorrelationRule` was added 2026-11-21 on the owner's approval, measured in
#: `scripts/measure_unarmed_rules.py`: 14 of 183 proposals refused at
#: correlations of 0.77 to 0.83 against a 0.75 limit, cutting max drawdown by
#: 0.55 pp over 2010-2019. It is the only one of the four never-installed rules
#: that is both installable and exercised.
BEYOND_THE_ARCHIVE = ("correlation_cluster",)


def build_cycle(
    config: StrategyConfig,
    sectors: dict[int, str],
    correlation: Mapping[tuple[int, int], float] | None = None,
) -> PortfolioCycle:
    """The cycle, ranked by **score 3** rather than by score 1.

    This is the swap that makes the system a portfolio manager rather than a
    stock picker. Every portfolio result this project has published was ranked
    by ``DiversityAwareRanker(correlation_weight=0.0,
    sector_penalty_per_holding=0.0, held_sectors={})`` — pure score order with
    the portfolio awareness switched off — and ``OpportunityFitRanker`` existed
    the whole time, called only by a demonstration script.
    """
    # **The archive's rule set, exactly.** This used to be a hand-written list
    # that happened to differ from `build_engine`'s in both directions — it
    # added `SectorExposureRule` and omitted `PositionSizeRule` — so the plan
    # showed a decision no backtest had validated, and the backtest validated a
    # decision the plan would not make.
    #
    # Sector concentration is still enforced, by `score_fit`'s
    # `sector_limit_reached` veto at the ranker. The engine-stage rule is the
    # second check, and installing it here alone would make the plan stricter
    # than the thing that measures it.
    # **Fails closed on an unmeasured pair**, deliberately: treating one as
    # uncorrelated produces a portfolio that passes the correlation limit
    # precisely because nobody measured it. With no matrix at all the rule is
    # left out rather than installed to refuse everything, and the plan reports
    # that it is absent.
    extra: tuple[RiskRule, ...] = (
        (
            CorrelationRule(
                config=config.risk, source=MatrixCorrelationSource(values=dict(correlation))
            ),
        )
        if correlation
        else ()
    )
    return PortfolioCycle(
        sizer=RiskBasedSizer(
            sizing=config.sizing,
            risk=config.risk,
            max_participation=Decimal("0.02"),
            costs=config.costs,
        ),
        engine=MostRestrictiveEngine(rule_set=(*base_rules(config), *extra), sizing=config.sizing),
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

    resolved, why_not = resolve_universe(session, declared.universe, declared.as_of)
    unresolved = tuple(f"{ticker} ({why})" for ticker, why in sorted(why_not.items()))
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

    # A holding that did not print today is carried at its last known close,
    # which is what `EventDrivenEngine._context` does and for the reason it
    # gives. Refusing the whole horizon over one quiet name — which this did
    # until 2026-11-19 — stalls every other position's stop for no gain.
    marks: dict[int, Decimal] = {}
    highs: dict[int, Decimal] = {}
    carried: list[tuple[str, dt.date]] = []
    before = [day for day in sessions if day <= declared.as_of]
    for holding in declared.holdings:
        instrument_id = resolved[holding.ticker]
        if instrument_id in bars:
            marks[instrument_id] = bars[instrument_id].close
            highs[instrument_id] = bars[instrument_id].high
            continue
        for day in reversed(before[:-1]):
            earlier = data.bars(day)
            if instrument_id in earlier:
                marks[instrument_id] = earlier[instrument_id].close
                highs[instrument_id] = earlier[instrument_id].high
                carried.append((holding.ticker, day))
                break
        else:
            # Never printed in the window at all. That is an identity problem,
            # not a quiet session, and there is no last known price to carry.
            raise DeclarationError(
                f"{declared.mandate}: {holding.ticker} has no print anywhere between "
                f"{declared.warmup_from} and {declared.as_of}, so it cannot be marked or "
                "stopped. Check the ticker resolves to the security you mean"
            )

    positions: list[PositionState] = []
    for holding in declared.holdings:
        instrument_id = resolved[holding.ticker]
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
        last_prices={p.instrument_id: marks[p.instrument_id] for p in positions},
    )
    held_sessions = {resolved[h.ticker]: h.sessions_held for h in declared.holdings}
    contexts = {
        p.instrument_id: StopContext(
            last_price=marks[p.instrument_id],
            # An approximation, and stated as one: the true high since entry
            # needs the whole holding period, which a declaration does not
            # carry. `EventDrivenEngine` tracks it exactly from its own lots.
            # Understating it only ever keeps a trailing stop lower, and the
            # trailing rung is reported unarmed anyway for want of an ATR.
            high_since_entry=max(highs[p.instrument_id], p.average_entry_price),
            sessions_held=held_sessions[p.instrument_id],
            partial_profit_taken=False,
            # None, deliberately, and reported as an absent control: the
            # ladder's trailing rung engages only on ATR, and this path has
            # none to give it.
            atr=None,
        )
        for p in portfolio.open_positions
    }

    # **Measured and supplied, not left to default.**
    #
    # `cycle.plan(..., correlation=None)` makes `OpportunityFitRanker._overlap`
    # return 0.0 for every pair, which pins the fit's `distinctness` component
    # at a constant 1.0 — a flat 0.20 contribution for every candidate, with
    # one of five components inert while the fit score is reported as though
    # all five were live. SIGNAL_SCOREBOARD §56 found exactly that in §55's
    # harness; this had reproduced it.
    config = StrategyConfig(name="ma_cross_baseline")
    correlation = pairwise_correlations(data, sessions, config.risk.correlation_lookback_sessions)
    limit = config.risk.max_correlation_for_new_position
    over_limit = sum(1 for value in correlation.values() if value > limit)

    plan = plan_session(
        mandate=declared.mandate,
        timeframe=Bartimeframe.D1,
        session_date=declared.as_of,
        cycle=build_cycle(config, sectors, correlation),
        portfolio=portfolio,
        candidates=scored,
        contexts=contexts,
        references={i: t for t, i in resolved.items()},
        correlation=dict(correlation),
    )
    return PlanResult(
        plan=plan,
        unresolved=unresolved,
        carried=tuple(carried),
        correlation_pairs=len(correlation),
        correlation_over_limit=over_limit,
        sectors_classified=len(sectors),
        sessions_loaded=len(sessions),
        bars_loaded=data.bar_count,
        seconds=(dt.datetime.now(dt.UTC) - started).total_seconds(),
    )
