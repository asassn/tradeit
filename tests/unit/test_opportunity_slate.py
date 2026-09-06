"""A day-trade setup and a retirement position never rank against each other.

Phase 7 produces a number that sorts. Nothing until now stopped two numbers
produced for different horizons being sorted together, and the result would
have arrived looking like an ordinary ranked list — which is precisely why it
needs a refusal rather than a convention.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.opportunity.final import combine
from tradeit.opportunity.fit import PortfolioContext, VetoReason, score_fit
from tradeit.opportunity.slate import Contender, build_slate
from tradeit.portfolio.mandate import Mandate, PopulationNotAdmissible, UnitOfStudy


def _context(**over: object) -> PortfolioContext:
    base: dict[str, object] = {
        "equity": Decimal(100_000),
        "cash": Decimal(50_000),
        "open_positions": 2,
        "position_limit": 10,
        "sector_exposure": 0.1,
        "sector_limit": 0.3,
        "heat": 0.02,
        "heat_limit": 0.06,
    }
    base.update(over)
    return PortfolioContext(**base)  # type: ignore[arg-type]


def _contender(
    ref: str,
    standalone: float,
    *,
    mandate: Mandate = Mandate.SWING,
    timeframe: Bartimeframe = Bartimeframe.D1,
    vetoed: bool = False,
) -> Contender:
    context = _context(open_positions=10) if vetoed else _context()
    fit = score_fit(context)
    return Contender(
        reference=ref,
        mandate=mandate,
        timeframe=timeframe,
        score=combine(standalone, fit),
    )


def _unit(
    mandate: Mandate = Mandate.SWING, timeframe: Bartimeframe = Bartimeframe.D1
) -> UnitOfStudy:
    return UnitOfStudy.single(mandate, timeframe)


# -- the refusal -----------------------------------------------------------


def test_a_slate_of_one_mandate_builds() -> None:
    slate = build_slate([_contender("AAPL", 0.8), _contender("MSFT", 0.6)], _unit())
    assert slate.mandate is Mandate.SWING
    assert not slate.combined
    assert len(slate.contenders) == 2


def test_a_contender_from_another_mandate_is_refused() -> None:
    """The failure this module exists for: two horizons, one sorted list."""
    contenders = [
        _contender("AAPL", 0.8),
        _contender("SPY", 0.9, mandate=Mandate.DAY, timeframe=Bartimeframe.M5),
    ]
    with pytest.raises(PopulationNotAdmissible):
        build_slate(contenders, _unit())


def test_a_slate_declaring_two_mandates_is_refused_before_any_population_test() -> None:
    """Not a population that failed a test — a slate that should never have
    been proposed, so the message says that rather than reporting a mixture."""
    unit = UnitOfStudy(
        mandates=frozenset({Mandate.SWING, Mandate.DAY}),
        timeframes=frozenset({Bartimeframe.D1}),
    )
    with pytest.raises(ValueError) as raised:
        build_slate([_contender("AAPL", 0.8)], unit)
    assert "one mandate's capital" in str(raised.value)


def test_an_undeclared_timeframe_is_refused() -> None:
    contenders = [_contender("AAPL", 0.8, timeframe=Bartimeframe.H1)]
    with pytest.raises(PopulationNotAdmissible):
        build_slate(contenders, _unit())


def test_a_declared_two_timeframe_slate_is_permitted_and_stays_labelled() -> None:
    """The separation rule's escape clause: legal when declared in advance, and
    it must remain readable as mixed afterwards."""
    unit = UnitOfStudy(
        mandates=frozenset({Mandate.SWING}),
        timeframes=frozenset({Bartimeframe.D1, Bartimeframe.H1}),
    )
    slate = build_slate(
        [_contender("AAPL", 0.8), _contender("MSFT", 0.7, timeframe=Bartimeframe.H1)], unit
    )
    assert slate.combined
    assert "combined" in slate.describe()


def test_an_empty_slate_is_refused() -> None:
    """A clean ranking of nothing, reported as though it were a result."""
    with pytest.raises(PopulationNotAdmissible):
        build_slate([], _unit())


def test_a_timeframe_the_mandate_forbids_cannot_be_declared() -> None:
    with pytest.raises(Exception):
        UnitOfStudy.single(Mandate.RETIREMENT, Bartimeframe.M5)


# -- ranking ---------------------------------------------------------------


def test_ranked_orders_by_total_best_first() -> None:
    slate = build_slate(
        [_contender("LOW", 0.2), _contender("HIGH", 0.9), _contender("MID", 0.5)], _unit()
    )
    assert [c.reference for c in slate.ranked] == ["HIGH", "MID", "LOW"]


def test_ranked_includes_what_the_fit_refused() -> None:
    """ "Forbidden, and would have ranked first" is worth seeing; discarding it
    hides what a limit cost."""
    slate = build_slate([_contender("OK", 0.3), _contender("BEST", 0.99, vetoed=True)], _unit())
    assert slate.ranked[0].reference == "BEST"
    assert [c.reference for c in slate.refused] == ["BEST"]


def test_for_capital_never_contains_a_vetoed_candidate() -> None:
    """However high the total. Admissibility comes from the vetoes alone."""
    slate = build_slate([_contender("OK", 0.3), _contender("BEST", 0.99, vetoed=True)], _unit())
    assert [c.reference for c in slate.for_capital] == ["OK"]
    assert VetoReason.POSITION_LIMIT_REACHED in slate.refused[0].score.fit.vetoes


def test_for_capital_and_refused_partition_the_slate() -> None:
    slate = build_slate(
        [_contender("A", 0.7), _contender("B", 0.4, vetoed=True), _contender("C", 0.5)],
        _unit(),
    )
    assert len(slate.for_capital) + len(slate.refused) == len(slate.contenders)


def test_the_mandate_is_unambiguous_by_construction() -> None:
    """Not by convention: a slate spanning mandates cannot be built at all."""
    slate = build_slate([_contender("AAPL", 0.8)], _unit())
    assert slate.mandate is Mandate.SWING
