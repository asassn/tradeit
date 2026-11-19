"""What one portfolio should do today, and what it refused to do.

This module exists because the platform's central distinction had never been
*run*. ``tradeit.opportunity`` separates three scores — the setup, the fit, and
the number that may authorise — and ``tradeit.portfolio`` decides and
constrains. Both were built, tested and measured beside the decision rather
than inside it: the one run that produced a stored decision record ranked with
``DiversityAwareRanker(correlation_weight=0.0, sector_penalty_per_holding=0.0,
held_sectors={})``, which is pure score order with the portfolio awareness
switched off, and never imported ``tradeit.opportunity`` at all.

So this composes the parts that already exist into the answer the system is
for: **not "is this a great stock" but "is this a great trade for this
portfolio right now"**, and when the answer is no, *which limit said so*.

Three choices worth recording, because each had a plausible alternative.

**The portfolio is declared, not fabricated.** ``PortfolioState`` is a value
object and its own docstring says a caller may construct one directly without a
database. Nothing in this repository writes a ``portfolios`` or ``positions``
row, so the alternative — reading the holdings from storage — would mean
inventing the writer first and then trusting the invention. A declared
portfolio is honest about where it came from; a persisted one that no code
maintains would not be.

**The cycle is the authority, and it is consulted once.**
:meth:`PortfolioCycle.plan` sizes twice internally, applies exits before
entries so released capital is redeployable, and measures every limit against
the provisional state as the slate progresses. Re-deriving any of that here
would produce a second opinion, and a plan whose refusals disagree with its
admissions is worse than one that reports fewer.

**A ``Slate`` is deliberately not built.** :func:`build_slate` needs a
``FinalOpportunityScore`` per contender, which this module does not hold: the
cycle computes them inside the ranker and reports refusals as strings, by
design, so that ``portfolio`` need not know ``opportunity``'s vocabulary.
Obtaining them here would mean ranking a second time against the same
portfolio — the duplication the paragraph above rejects. What the slate was
guarding is kept: ``UnitOfStudy.single`` enforces the mandate's eligible
timeframe and :func:`require_population` enforces the single population, so a
mixed or ineligible declaration still raises rather than being silently ranked.

**Nothing here may authorise a live trade.** This produces a plan, not an
order. The interlock in :mod:`tradeit.config` stands regardless of what this
prints.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.core.enums import Bartimeframe
from tradeit.portfolio.base import PortfolioState, RankerRefusal
from tradeit.portfolio.cycle import CyclePlan, EntryCandidate, PortfolioCycle
from tradeit.portfolio.mandate import (
    Corpus,
    Mandate,
    Observation,
    UnitOfStudy,
    require_population,
)
from tradeit.portfolio.mandate import spec as mandate_spec
from tradeit.portfolio.stops import StopContext

__all__ = [
    "AVAILABLE_CORPORA",
    "AbsentControl",
    "Holding",
    "ProposedEntry",
    "ProposedExit",
    "Refusal",
    "SessionPlan",
    "absent_controls",
    "caveats_for",
    "plan_session",
]


@dataclass(frozen=True, slots=True)
class Holding:
    """One open position, priced at today's close."""

    instrument_id: int
    reference: str
    quantity: Decimal
    average_entry_price: Decimal
    last_price: Decimal
    stop_price: Decimal | None
    market_value: Decimal
    #: What is lost if the stop is hit — the quantity that portfolio heat sums.
    open_risk: Decimal
    weight: float


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    """A trade the portfolio would open, with the constraint that sized it."""

    instrument_id: int
    reference: str
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    notional: Decimal
    risk_amount: Decimal
    risk_fraction: Decimal
    #: Which of the five caps decided the size. Reported because "we bought
    #: less than we wanted" and "we bought what we wanted" are different facts.
    binding_constraint: str
    weight: float
    #: The setup score, as the strategy produced it.
    standalone: float
    accepted: bool
    #: For a declined entry, the risk rule that rejected it — distinct from a
    #: :class:`Refusal`, which the ranker made before any sizing.
    declined_by: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProposedExit:
    """A position the stop ladder says to close, in whole or in part."""

    instrument_id: int
    reference: str
    reason: str
    fraction: Decimal
    detail: str


@dataclass(frozen=True, slots=True)
class Refusal:
    """A candidate the **ranker** refused before it was ever sized.

    Distinct from a declined :class:`ProposedEntry`, which reached the risk
    engine and was rejected there. Two mechanisms, two reports: collapsing them
    loses which control acted.
    """

    instrument_id: int
    reference: str
    reasons: tuple[str, ...]
    ranker: str
    #: Where it would have ranked had it been admissible — what the limit cost.
    standing: int
    detail: str


@dataclass(frozen=True, slots=True)
class AbsentControl:
    """A control that exists in code and **cannot fire as currently wired**.

    Reported rather than omitted. A risk control that cannot fire is not a risk
    control, and the difference between "this limit held" and "this limit was
    never consulted" is invisible on a screen that shows only what fired.
    """

    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """One horizon's decision for one session, with its evidence status.

    **This is a decision process running on real data, not a recommendation.**
    No signal in this repository has a validated portfolio edge — the best
    measured one, ``realized_vol_60``, passes a registered out-of-sample test
    as a *signal* and lost to a random portfolio in four of four samples as a
    *portfolio*. :attr:`caveats` carries that with every plan rather than
    leaving it in a document, because a number on a screen is acted on and a
    document is not.
    """

    mandate: str
    timeframe: str
    session_date: dt.date
    equity: Decimal
    cash: Decimal
    heat: float
    position_count: int
    holdings: tuple[Holding, ...]
    entries: tuple[ProposedEntry, ...]
    exits: tuple[ProposedExit, ...]
    refusals: tuple[Refusal, ...]
    absent: tuple[AbsentControl, ...]
    caveats: tuple[str, ...]
    candidates_considered: int

    @property
    def would_buy(self) -> tuple[ProposedEntry, ...]:
        return tuple(entry for entry in self.entries if entry.accepted)

    @property
    def declined(self) -> tuple[ProposedEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.accepted)

    @property
    def capital_deployed(self) -> Decimal:
        return sum((entry.notional for entry in self.would_buy), Decimal(0))

    def describe(self) -> str:
        """The plan as a person reads it, refusals included."""
        lines = [
            f"{self.mandate.upper()} {self.timeframe} · {self.session_date}",
            f"  equity {self.equity:,.2f} · cash {self.cash:,.2f} · "
            f"heat {self.heat:.2%} · {self.position_count} position(s)",
        ]
        lines.append(f"  HOLDS        {len(self.holdings)}")
        for holding in self.holdings:
            lines.append(
                f"    {holding.reference:<10} {holding.quantity} @ "
                f"{holding.average_entry_price} · now {holding.last_price} · "
                f"stop {holding.stop_price} · {holding.weight:.2%} of equity"
            )
        lines.append(f"  WOULD BUY    {len(self.would_buy)}")
        for entry in self.would_buy:
            lines.append(
                f"    {entry.reference:<10} {entry.quantity} @ {entry.entry_price} "
                f"stop {entry.stop_price} · risk {entry.risk_amount:,.2f} "
                f"({entry.risk_fraction:.2%}) · {entry.weight:.2%} of equity · "
                f"sized by {entry.binding_constraint} · setup {entry.standalone:.4f}"
            )
        lines.append(f"  WOULD SELL   {len(self.exits)}")
        for exit_ in self.exits:
            lines.append(
                f"    {exit_.reference:<10} {exit_.reason} {exit_.fraction:.0%} · {exit_.detail}"
            )
        lines.append(f"  REFUSED      {len(self.refusals)}")
        for refusal in self.refusals:
            lines.append(
                f"    {refusal.reference:<10} {', '.join(refusal.reasons)} "
                f"· would have ranked {refusal.standing} · {refusal.detail}"
            )
        lines.append(f"  DECLINED     {len(self.declined)}")
        for entry in self.declined:
            lines.append(f"    {entry.reference:<10} {entry.declined_by} · {entry.detail}")
        lines.append(f"  NOT ARMED    {len(self.absent)}")
        for control in self.absent:
            lines.append(f"    {control.name:<24} {control.reason}")
        for caveat in self.caveats:
            lines.append(f"  ! {caveat}")
        return "\n".join(lines)


#: Corpora this system actually has. ``intraday-01`` is deferred (ADR-0030) and
#: is **not** listed, which is what makes the layer reporting below measured
#: rather than asserted.
AVAILABLE_CORPORA: frozenset[Corpus] = frozenset({Corpus.RESEARCH_01})

#: Which corpus a timeframe has to come from. Daily and longer are in
#: ``research-01``; everything shorter needs ``intraday-01``.
_SUB_DAILY = frozenset(
    {
        Bartimeframe.M1,
        Bartimeframe.M5,
        Bartimeframe.M15,
        Bartimeframe.M30,
        Bartimeframe.H1,
        Bartimeframe.H4,
    }
)


def _corpus_for(timeframe: Bartimeframe) -> Corpus:
    return Corpus.INTRADAY_01 if timeframe in _SUB_DAILY else Corpus.RESEARCH_01


def _layers_absent(mandate: Mandate) -> tuple[str, ...]:
    """Which of a mandate's declared layers cannot be built, and why.

    **Measured from the mandate's own hierarchy**, not written out per mandate.
    The first version of this stated Swing's missing ``H1/M15`` trigger as a
    constant, and printed that sentence on a Retirement plan — whose trigger is
    ``D1`` from ``research-01`` and is entirely available. A caveat that is
    false is worse than no caveat, because it is the part of the screen a reader
    is meant to trust.
    """
    spec = mandate_spec(mandate)
    out: list[str] = []
    for role, timeframes in (
        ("context", spec.hierarchy.context),
        ("setup", spec.hierarchy.setup),
        ("trigger", spec.hierarchy.trigger),
    ):
        needed = {_corpus_for(timeframe) for timeframe in timeframes}
        missing = sorted(str(corpus) for corpus in needed - AVAILABLE_CORPORA)
        if not missing:
            continue
        names = "/".join(str(timeframe) for timeframe in timeframes)
        available = [t for t in timeframes if _corpus_for(t) in AVAILABLE_CORPORA]
        wholly = not available
        out.append(
            f"{mandate}'s declared {role} layer is {names}, which needs "
            f"{', '.join(missing)}. That corpus does not exist (ADR-0030), so the layer is "
            + (
                "entirely absent"
                if wholly
                else f"only partly available ({'/'.join(str(t) for t in available)})"
            )
            + " — reported rather than silently skipped."
        )
    return tuple(out)


#: Carried with every plan, whatever the horizon. Each is measured or recorded
#: elsewhere in this repository, and each changes what the numbers are worth.
UNIVERSAL_CAVEATS: tuple[str, ...] = (
    "No signal in this system has a validated portfolio edge. The best measured "
    "one (realized_vol_60) passes a registered out-of-sample test as a signal and "
    "lost to a random portfolio in 4 of 4 samples as a portfolio. The ranking "
    "below is a decision process running on real data, not a recommendation.",
    "Nothing in this path may place an order. The live-trading interlock in "
    "tradeit.config requires a machine-local authorisation file regardless.",
)


def caveats_for(mandate: Mandate) -> tuple[str, ...]:
    """The universal caveats plus whatever this horizon specifically lacks."""
    return (*UNIVERSAL_CAVEATS, *_layers_absent(mandate))


def absent_controls(
    cycle: PortfolioCycle, contexts: Mapping[int, StopContext]
) -> tuple[AbsentControl, ...]:
    """Controls that cannot fire, measured from the cycle rather than listed.

    Measured, because a hardcoded list rots the moment a rule is installed and
    would then assert an absence that had been fixed. Each check below reads
    the wiring it describes.
    """
    found: list[AbsentControl] = []

    installed = {type(rule).__name__ for rule in cycle.engine.rules}
    for name, why in (
        ("PyramidRule", "adds to an existing position are not limited"),
        ("CorrelationRule", "a candidate correlated with a holding is not blocked"),
        ("DrawdownHaltRule", "trading does not halt on a drawdown"),
        ("DailyLossRule", "a session's losses do not stop further entries"),
        ("PositionSizeRule", "a single position's share of equity is not capped by the engine"),
        ("SectorExposureRule", "sector concentration is not blocked by the engine"),
    ):
        if name not in installed:
            found.append(AbsentControl(name=name, reason=f"not installed — {why}"))

    if getattr(cycle.sizer, "costs", None) is None:
        found.append(
            AbsentControl(
                name="round_trip_cost",
                reason="the sizer has no cost model, so cost can never bind a size",
            )
        )

    if contexts and all(context.atr is None for context in contexts.values()):
        found.append(
            AbsentControl(
                name="trailing_stop",
                reason=(
                    "no ATR in any stop context, and the ladder's trailing rung "
                    "engages only when ATR is present — so the stop can reach "
                    "breakeven but never trail"
                ),
            )
        )

    found.append(
        AbsentControl(
            name="insufficient_cash_veto",
            reason=(
                "unreachable by construction: the sizer caps quantity at "
                "cash / entry_price and then floors it, so required cash can equal "
                "available cash but never exceed it"
            ),
        )
    )
    return tuple(found)


def plan_session(
    *,
    mandate: Mandate,
    timeframe: Bartimeframe,
    session_date: dt.date,
    cycle: PortfolioCycle,
    portfolio: PortfolioState,
    candidates: Sequence[EntryCandidate],
    contexts: Mapping[int, StopContext],
    references: Mapping[int, str] | None = None,
    correlation: dict[tuple[int, int], float] | None = None,
) -> SessionPlan:
    """Run the decision path for one horizon and one session.

    ``references`` maps instrument ids to tickers for display only; a missing
    one renders as the id rather than being dropped, because a holding with no
    label is still a holding.

    Raises :class:`TimeframeNotEligible` when the timeframe is outside the
    mandate's declared set, and ``PopulationNotAdmissible`` when the candidates
    do not form one admissible population. Both are checks the stored record
    never performed — its mandate label came from a default argument, so it was
    correct by accident rather than by verification.
    """
    # `UnitOfStudy.single` calls `require_timeframe` itself, so constructing the
    # unit *is* the eligibility check. An explicit call beside it was a second
    # call to the same function, and a mutation test proved no assertion could
    # tell the two apart — a guard whose removal nothing detects is redundant,
    # not defensive.
    unit = UnitOfStudy.single(mandate, timeframe)
    checked = timeframe
    if candidates:
        # Only when there are candidates. `require_population` refuses an empty
        # observation list, which is right for evidence — a population that
        # passes because it is empty is a vacuous result — but wrong here: a
        # session with no crossings is an ordinary outcome, not a malformed
        # study, and refusing it would make "nothing to do today" an error.
        require_population(
            [Observation(mandate=mandate, timeframe=checked) for _ in candidates], unit
        )

    plan = cycle.plan(portfolio, candidates, dict(contexts), correlation)
    labels = dict(references or {})

    def label(instrument_id: int) -> str:
        return labels.get(instrument_id, str(instrument_id))

    return SessionPlan(
        mandate=str(mandate),
        timeframe=str(checked),
        session_date=session_date,
        equity=portfolio.equity,
        cash=portfolio.cash,
        heat=float(portfolio.heat()),
        position_count=portfolio.position_count,
        holdings=_holdings(portfolio, label),
        entries=_entries(plan, portfolio, label),
        exits=_exits(plan, label),
        refusals=_refusals(plan.refusals, label),
        absent=absent_controls(cycle, contexts),
        caveats=caveats_for(mandate),
        candidates_considered=len(candidates),
    )


def _holdings(portfolio: PortfolioState, label: Callable[[int], str]) -> tuple[Holding, ...]:
    equity = portfolio.equity
    out: list[Holding] = []
    for position in portfolio.open_positions:
        price = portfolio.last_prices.get(position.instrument_id, position.average_entry_price)
        value = position.market_value(price)
        out.append(
            Holding(
                instrument_id=position.instrument_id,
                reference=label(position.instrument_id),
                quantity=position.quantity,
                average_entry_price=position.average_entry_price,
                last_price=price,
                stop_price=position.stop_price,
                market_value=value,
                open_risk=position.open_risk(price),
                weight=float(value / equity) if equity > 0 else 0.0,
            )
        )
    return tuple(out)


def _entries(
    plan: CyclePlan, portfolio: PortfolioState, label: Callable[[int], str]
) -> tuple[ProposedEntry, ...]:
    equity = portfolio.equity
    out: list[ProposedEntry] = []
    for entry in plan.entries:
        notional = entry.quantity * entry.candidate.entry_price
        out.append(
            ProposedEntry(
                instrument_id=entry.candidate.instrument_id,
                reference=label(entry.candidate.instrument_id),
                quantity=entry.quantity,
                entry_price=entry.candidate.entry_price,
                stop_price=entry.candidate.stop_price,
                notional=notional,
                risk_amount=entry.sizing.risk_amount,
                risk_fraction=entry.sizing.risk_fraction,
                binding_constraint=entry.sizing.binding_constraint,
                weight=float(notional / equity) if equity > 0 else 0.0,
                standalone=entry.candidate.score.total,
                accepted=entry.accepted,
                declined_by=None if entry.accepted else entry.verdict.binding_rule,
                detail=entry.explain(),
            )
        )
    return tuple(out)


def _exits(plan: CyclePlan, label: Callable[[int], str]) -> tuple[ProposedExit, ...]:
    return tuple(
        ProposedExit(
            instrument_id=signal.instrument_id,
            reference=label(signal.instrument_id),
            reason=str(signal.reason),
            fraction=signal.fraction,
            detail=signal.detail,
        )
        for signal in plan.exits
    )


def _refusals(
    refusals: Sequence[RankerRefusal], label: Callable[[int], str]
) -> tuple[Refusal, ...]:
    return tuple(
        Refusal(
            instrument_id=refusal.instrument_id,
            reference=label(refusal.instrument_id),
            reasons=refusal.reasons,
            ranker=refusal.ranker,
            standing=refusal.standing,
            detail=refusal.detail,
        )
        for refusal in refusals
    )


def with_references(plan: SessionPlan, references: Mapping[int, str]) -> SessionPlan:
    """Relabel a plan's rows, for a caller that resolves tickers after the fact."""
    return dataclasses.replace(
        plan,
        holdings=tuple(
            dataclasses.replace(h, reference=references.get(h.instrument_id, h.reference))
            for h in plan.holdings
        ),
        entries=tuple(
            dataclasses.replace(e, reference=references.get(e.instrument_id, e.reference))
            for e in plan.entries
        ),
        exits=tuple(
            dataclasses.replace(x, reference=references.get(x.instrument_id, x.reference))
            for x in plan.exits
        ),
        refusals=tuple(
            dataclasses.replace(r, reference=references.get(r.instrument_id, r.reference))
            for r in plan.refusals
        ),
    )
