"""Whether each absolute refusal can actually fire.

A risk control that cannot fire is not a risk control — it is a comment that
passes its tests. ``VetoReason`` declares five refusals and
:func:`tradeit.opportunity.fit.score_fit` implements all five, so a test that
only exercises the function finds nothing wrong. **The question worth asking is
whether the value that triggers each one can arise from the code that produces
it**, and for one of the five the answer is no.

``VetoReason.INSUFFICIENT_CASH`` fires on ``required_cash > cash``. In the one
path that supplies it, ``required_cash`` is
:attr:`SizingDecision.notional` from :class:`RiskBasedSizer` — which already
caps quantity at ``cash / entry_price`` under
:data:`BindingConstraint.CASH` and then *floors* it. So the notional can equal
cash but never exceed it, and the veto is **unreachable by construction** on
that path.

That is recorded here rather than worked around, for two reasons. Writing a
test that reaches it by hand-building a ``PortfolioContext`` would prove the
``if`` statement works while leaving the real question untouched. And deleting
the veto would be wrong: a different sizer, a multi-leg order, or a cash
reservation taken between sizing and fit would all make it reachable, and the
refusal should already be there when that happens.

**The pin below fails if the cash cap is ever removed** — which is the moment
somebody needs to know the veto came alive, rather than discovering later that
it had been dead the whole time.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import SignalDirection
from tradeit.opportunity import PortfolioContext, VetoReason, score_fit
from tradeit.portfolio.base import PortfolioState
from tradeit.portfolio.sizing import BindingConstraint, RiskBasedSizer
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

SESSION = dt.date(2015, 6, 1)

#: A context that triggers nothing, so each case below varies one field and the
#: veto it produces is attributable to that field alone.
PERMISSIVE = PortfolioContext(
    equity=Decimal(100_000),
    cash=Decimal(50_000),
    open_positions=2,
    position_limit=12,
    sector_exposure=0.05,
    sector_limit=0.30,
    heat=0.01,
    heat_limit=0.06,
    existing_weight=0.0,
    max_weight=0.20,
    overlap=0.0,
    required_cash=Decimal(5_000),
)


def test_the_permissive_context_vetoes_nothing() -> None:
    """Otherwise every case below would pass for the wrong reason."""
    assert score_fit(PERMISSIVE).vetoes == ()
    assert score_fit(PERMISSIVE).admissible


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("existing_weight", 0.20, VetoReason.ALREADY_AT_FULL_SIZE),
        ("open_positions", 12, VetoReason.POSITION_LIMIT_REACHED),
        ("sector_exposure", 0.30, VetoReason.SECTOR_LIMIT_REACHED),
        ("heat", 0.06, VetoReason.HEAT_LIMIT_EXCEEDED),
        ("required_cash", Decimal(50_001), VetoReason.INSUFFICIENT_CASH),
    ],
)
def test_each_veto_fires_at_its_limit(field: str, value: object, expected: VetoReason) -> None:
    """Every refusal is reachable through ``score_fit``, and fires **at** the limit.

    At, not past: each test sets the field to exactly the limit. A veto written
    with ``>`` where the limit is inclusive would admit a trade that takes the
    portfolio precisely to its cap, which is the off-by-one a cap exists to
    prevent.
    """
    context = dataclasses.replace(PERMISSIVE, **{field: value})

    assert score_fit(context).vetoes == (expected,)
    assert not score_fit(context).admissible


def test_a_vetoed_candidate_still_gets_an_honest_fit_number() -> None:
    """ "Forbidden" and "fits badly" are different findings and both are reported."""
    blocked_only = dataclasses.replace(PERMISSIVE, sector_exposure=0.30)
    fit = score_fit(blocked_only)

    assert fit.vetoes == (VetoReason.SECTOR_LIMIT_REACHED,)
    assert fit.score > 0.0, "a trade blocked only by a cap is worth revisiting when it frees"
    assert fit.reconciles


def test_all_five_vetoes_can_fire_at_once() -> None:
    """Nothing short-circuits: a reader sees every binding limit, not the first."""
    context = PortfolioContext(
        equity=Decimal(100_000),
        cash=Decimal(1_000),
        open_positions=12,
        position_limit=12,
        sector_exposure=0.40,
        sector_limit=0.30,
        heat=0.09,
        heat_limit=0.06,
        existing_weight=0.25,
        max_weight=0.20,
        overlap=0.9,
        required_cash=Decimal(5_000),
    )

    assert set(score_fit(context).vetoes) == set(VetoReason)


# ---------------------------------------------------------------------------
# The pin: INSUFFICIENT_CASH cannot fire on the sizer's own notional
# ---------------------------------------------------------------------------


def _score(total: float = 0.5) -> OpportunityScore:
    return OpportunityScore(
        instrument_id=1,
        session_date=SESSION,
        direction=SignalDirection.LONG,
        total=total,
        components=(ScoreComponent(name="setup", raw_value=total, normalised=total, weight=1.0),),
        feature_set_digest="test",
        strategy_config_digest="test",
    )


def _portfolio(cash: Decimal, equity: Decimal) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(SESSION, dt.time(), tzinfo=dt.UTC),
        cash=cash,
        equity=equity,
        positions=(),
        last_prices={},
    )


def _sizer() -> RiskBasedSizer:
    config = StrategyConfig(name="ma_cross_baseline")
    return RiskBasedSizer(sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02"))


#: Each case is chosen so the **cash** cap is the one that binds, except the
#: last, which is a control where another cap binds first. Every notional
#: clears the 500.0 minimum, because a case the sizer refuses outright proves
#: nothing about the cash cap and a skip would make this test vacuous.
@pytest.mark.parametrize(
    ("cash", "equity", "entry", "stop", "cash_bound"),
    [
        # Cash binds exactly: 20 shares at 50 is the whole 1,000.
        (Decimal(1_000), Decimal(1_000_000), Decimal(50), Decimal(49), True),
        # **The equality case, which is the heart of the claim**: notional
        # lands on cash to the penny, and the veto needs strictly greater.
        (Decimal(2_500), Decimal(500_000), Decimal(25), Decimal(23), True),
        # Flooring leaves slack: 9.99 shares becomes 9, so 900 of 999.
        (Decimal(999), Decimal(100_000), Decimal(100), Decimal(95), True),
        # A fractional cash balance, where the floor leaves 0.56 unspent.
        (Decimal("1234.56"), Decimal(2_000_000), Decimal("12.34"), Decimal("12.00"), True),
        # Control: risk binds well below cash, so the cap is not even consulted.
        (Decimal(50_000), Decimal(100_000), Decimal(50), Decimal(25), False),
    ],
)
def test_the_sizer_never_proposes_more_cash_than_the_portfolio_has(
    cash: Decimal, equity: Decimal, entry: Decimal, stop: Decimal, cash_bound: bool
) -> None:
    """**This is why ``INSUFFICIENT_CASH`` is unreachable on this path.**

    ``BindingConstraint.CASH`` caps quantity at ``cash / entry_price`` and the
    quantity is then floored, so the notional can equal available cash and
    never exceed it. The veto needs ``required_cash > cash``.

    If this test starts failing, the cash cap has changed and the veto has come
    alive — which is a change worth noticing deliberately rather than a
    regression.
    """
    decision = _sizer().size(_score(), entry, stop, _portfolio(cash, equity))

    assert not decision.rejected, (
        f"this case must produce a real size to say anything: {decision.rejection_reason}"
    )
    assert (decision.binding_constraint == BindingConstraint.CASH) is cash_bound, (
        f"expected cash_bound={cash_bound}, bound on {decision.binding_constraint}"
    )
    assert decision.notional <= cash, (
        f"notional {decision.notional} exceeds cash {cash}; "
        f"binding constraint was {decision.binding_constraint}"
    )

    # And therefore the fit cannot veto it for cash, which is the real claim.
    context = dataclasses.replace(PERMISSIVE, cash=cash, required_cash=decision.notional)
    assert VetoReason.INSUFFICIENT_CASH not in score_fit(context).vetoes


def test_a_notional_that_exactly_equals_cash_is_still_admissible() -> None:
    """The boundary the veto turns on, stated on its own.

    ``required_cash > cash`` rather than ``>=``, and the sizer can land exactly
    on cash. If the comparison were ever tightened to ``>=``, a fully-invested
    but entirely fundable trade would be refused — so the boundary is pinned
    here and not left implicit in the parametrised case above.
    """
    decision = _sizer().size(
        _score(), Decimal(25), Decimal(23), _portfolio(Decimal(2_500), Decimal(500_000))
    )

    assert decision.notional == Decimal(2_500), "this case must sit exactly on cash"
    context = dataclasses.replace(PERMISSIVE, cash=Decimal(2_500), required_cash=decision.notional)
    assert score_fit(context).vetoes == ()


def test_the_cash_cap_is_still_one_of_the_sizer_constraints() -> None:
    """A second guard on the same claim, in case the cap is renamed rather than removed.

    The test above infers the cap from behaviour; this one asserts the
    constraint still exists by name, so a rename that preserved the arithmetic
    does not quietly invalidate the reasoning recorded in this module.
    """
    assert BindingConstraint.CASH == "available_cash"

    # Cash binding is observable: tiny cash against a large risk budget.
    decision = _sizer().size(
        _score(), Decimal(50), Decimal(49), _portfolio(Decimal(500), Decimal(1_000_000))
    )
    assert decision.binding_constraint == BindingConstraint.CASH
