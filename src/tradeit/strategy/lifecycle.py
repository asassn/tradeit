"""Strategy version states, and the promotions the platform will refuse.

A strategy is not a thing that is good or bad; a **strategy version** is a thing
that has, or has not, earned a particular claim. ``docs/STRATEGY_BUILDER.md`` §5
names nine states and one sentence that is the reason they exist:

    No strategy becomes LIVE because a backtest was profitable.

Separating the states is how that sentence becomes enforceable rather than
aspirational. A profitable backtest moves a version from ``BACKTESTING`` to
``OUT_OF_SAMPLE_TESTING`` and no further, because the next four claims are about
data the backtest never saw.

Three rules, each of which rejected a simpler alternative
---------------------------------------------------------

**Promotion is one rung; demotion is any distance.** The asymmetry is the point.
Evidence accrues one claim at a time — passing out-of-sample says nothing about
paper trading — so a promotion that skipped a rung would be asserting a claim
nobody tested. A *demotion* is the opposite kind of fact: discovering that a
version's backtest was contaminated invalidates everything built on top of it,
so a fall from ``LIVE`` to ``DRAFT`` is a single legitimate move. Symmetric
transitions would have been simpler and would have made demotion too weak to
express what a discovered flaw actually means.

**Evidence is required to climb and never inherited.** Every promotion carries a
citation naming the run that earned it. An edit does not produce an edited
version, it produces a **new version at** ``DRAFT`` **with no evidence at all**
(:meth:`StrategyVersion.edit`) — because the parent's backtest was run on the
parent's parameters, and carrying its record forward is precisely how a tuned
parameter acquires an untested claim.

**``LIVE`` is refused by default and cannot be argued into.** It needs a
:class:`LiveAuthorisation`, which has no constructor that does not consult the
ADR-0004 interlock. The interlock itself is *not reimplemented here* — a rule
written down twice is two rules that will eventually disagree — so this module
asks :class:`tradeit.config.Settings` whether it passed and refuses when it
cannot get an answer.

What this deliberately does not do
----------------------------------

No thresholds, no comparison metric, no claim about what makes a version worth
promoting. §6 lists what will eventually be compared and says plainly that none
of it is computed yet; a state machine that decided *when* to promote would be
inventing exactly those thresholds. This decides only what may follow what.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from tradeit.portfolio.mandate import Mandate

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from tradeit.config import Settings

__all__ = [
    "LADDER",
    "Evidence",
    "IllegalTransition",
    "LiveAuthorisation",
    "LiveTradingNotAuthorised",
    "PromotionWithoutEvidence",
    "StrategyState",
    "StrategyVersion",
    "is_promotion",
    "may_follow",
    "successors",
]


class StrategyState(StrEnum):
    """§5's nine states, in the order a version would earn them."""

    DRAFT = "draft"
    VALIDATED = "validated"
    BACKTESTING = "backtesting"
    OUT_OF_SAMPLE_TESTING = "out_of_sample_testing"
    PAPER_TRADING = "paper_trading"
    ELIGIBLE_FOR_CAPITAL = "eligible_for_capital"
    LIVE = "live"
    PAUSED = "paused"
    RETIRED = "retired"


#: The rungs, in order. ``PAUSED`` and ``RETIRED`` are deliberately absent: they
#: are not degrees of earned evidence, they are things that happen to a version
#: regardless of how much it has. Putting them on the ladder would make
#: "promotion to paused" expressible, which is meaningless.
LADDER: tuple[StrategyState, ...] = (
    StrategyState.DRAFT,
    StrategyState.VALIDATED,
    StrategyState.BACKTESTING,
    StrategyState.OUT_OF_SAMPLE_TESTING,
    StrategyState.PAPER_TRADING,
    StrategyState.ELIGIBLE_FOR_CAPITAL,
    StrategyState.LIVE,
)

_RANK: Mapping[StrategyState, int] = {state: i for i, state in enumerate(LADDER)}


class IllegalTransition(ValueError):
    """A state change the lifecycle does not permit."""

    def __init__(self, frm: StrategyState, to: StrategyState, why: str) -> None:
        super().__init__(f"{frm.value} -> {to.value} is not permitted: {why}")
        self.frm = frm
        self.to = to


class PromotionWithoutEvidence(ValueError):
    """A version was promoted with nothing naming what earned it."""


class LiveTradingNotAuthorised(ValueError):
    """``LIVE`` was requested without the ADR-0004 interlock being satisfied."""


class LiveAuthorisation:
    """Proof that the ADR-0004 interlock was satisfied, as an object.

    There is no constructor that does not consult it. :class:`Settings` raises
    at construction when ``trading_mode=live`` without the authorisation file,
    so a ``Settings`` instance reporting ``is_live`` is itself the evidence —
    which is why this asks rather than re-checking the file.
    """

    __slots__ = ("granted_at",)

    def __init__(self, settings: Settings) -> None:
        if not settings.is_live:
            raise LiveTradingNotAuthorised(
                "settings.trading_mode is not live; live trading stays disabled. "
                "Completing every roadmap phase does not by itself authorise it."
            )
        self.granted_at = dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class Evidence:
    """What earned one transition. Recorded, never reconstructed."""

    #: What produced it — a backtest run id, a paper-trading track, a review.
    citation: str
    #: When the transition was made, not when the underlying run happened.
    recorded_at: dt.datetime
    frm: StrategyState
    to: StrategyState

    def describe(self) -> str:
        return f"{self.frm.value} -> {self.to.value}: {self.citation}"


def may_follow(frm: StrategyState, to: StrategyState) -> str | None:
    """``None`` when the transition is permitted, else why it is not.

    Returning the reason rather than a bool is what lets a refusal say
    something useful; ``IllegalTransition`` carries the same string.
    """
    if frm is to:
        return "a version cannot transition to the state it is already in"
    if frm is StrategyState.RETIRED:
        return "retirement is final; supersede a retired version with a new one"
    if to is StrategyState.RETIRED:
        return None
    if to is StrategyState.PAUSED:
        if frm is not StrategyState.LIVE:
            return "only a live version can be paused; nothing else is running"
        return None
    if frm is StrategyState.PAUSED:
        # Resuming returns to LIVE; anything lower is a demotion, and both are
        # legitimate. Climbing past LIVE is not expressible.
        return None if _RANK[to] <= _RANK[StrategyState.LIVE] else "unreachable"
    if _RANK[to] < _RANK[frm]:
        return None  # demotion, any distance
    if _RANK[to] == _RANK[frm] + 1:
        return None  # promotion, exactly one rung
    return (
        f"promotion is one rung at a time; {frm.value} is followed by "
        f"{LADDER[_RANK[frm] + 1].value}, and the claims in between were never tested"
    )


def successors(frm: StrategyState) -> tuple[StrategyState, ...]:
    """Every state legally reachable in one move. Useful for a UI, and for
    proving in a test that no shortcut to ``LIVE`` exists."""
    return tuple(state for state in StrategyState if may_follow(frm, state) is None)


def is_promotion(frm: StrategyState, to: StrategyState) -> bool:
    """A move *up* the ladder. Resuming a paused version is not one — it
    reclaims a rung it already held rather than earning a new claim."""
    if frm is StrategyState.PAUSED or to in (StrategyState.PAUSED, StrategyState.RETIRED):
        return False
    return _RANK[to] > _RANK[frm]


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """One version of one strategy: what it is, and what it has earned.

    ``digest`` is the identity — :class:`~tradeit.strategy.config.StrategyConfig`
    already hashes its own contents, so two versions with the same digest are
    the same definition and the name is a label. ``mandate`` is part of the
    definition rather than a property of the run: §7 is explicit that the three
    mandates are not one strategy with three holding periods, so a version
    belongs to exactly one.
    """

    strategy_name: str
    version: int
    digest: str
    mandate: Mandate
    state: StrategyState = StrategyState.DRAFT
    parent_digest: str | None = None
    history: tuple[Evidence, ...] = field(default_factory=tuple)

    def transition(
        self,
        to: StrategyState,
        *,
        citation: str = "",
        now: dt.datetime | None = None,
        live_authorisation: LiveAuthorisation | None = None,
    ) -> StrategyVersion:
        """Move to ``to``, or refuse and say why.

        Returns a new version rather than mutating: the history is the record,
        and a record you can edit in place is not one.
        """
        why = may_follow(self.state, to)
        if why is not None:
            raise IllegalTransition(self.state, to, why)
        if to is StrategyState.LIVE and live_authorisation is None:
            raise LiveTradingNotAuthorised(
                "promotion to live requires a LiveAuthorisation obtained from the "
                "ADR-0004 interlock; a passing backtest is not one"
            )
        if is_promotion(self.state, to) and not citation.strip():
            raise PromotionWithoutEvidence(
                f"{self.state.value} -> {to.value} needs a citation naming what "
                "earned it; a number that cannot be traced to evidence is worse "
                "than no number"
            )
        stamp = now or dt.datetime.now(dt.UTC)
        record = Evidence(citation=citation, recorded_at=stamp, frm=self.state, to=to)
        return replace(self, state=to, history=(*self.history, record))

    def edit(self, digest: str) -> StrategyVersion:
        """A changed definition is a new version at ``DRAFT`` with no history.

        Not a mutation and not an inheritance. The parent's evidence was earned
        by the parent's parameters, and carrying it forward is how a tuned
        threshold acquires a claim nobody tested.
        """
        if digest == self.digest:
            raise ValueError("the definition is unchanged; an edit must change the digest")
        return StrategyVersion(
            strategy_name=self.strategy_name,
            version=self.version + 1,
            digest=digest,
            mandate=self.mandate,
            state=StrategyState.DRAFT,
            parent_digest=self.digest,
            history=(),
        )

    @property
    def has_reached(self) -> StrategyState:
        """The highest rung this version ever held.

        Distinct from :attr:`state`, and the distinction matters: a version
        demoted from ``PAPER_TRADING`` back to ``DRAFT`` is not the same object
        as one that was never tested, and a comparison that cannot tell them
        apart will treat a failure as a fresh start.
        """
        rungs = [_RANK[self.state]] if self.state in _RANK else []
        rungs += [_RANK[e.to] for e in self.history if e.to in _RANK]
        return LADDER[max(rungs)] if rungs else StrategyState.DRAFT

    def evidence_for(self, state: StrategyState) -> Sequence[Evidence]:
        """Every recorded transition *into* ``state``, oldest first."""
        return tuple(e for e in self.history if e.to is state)
