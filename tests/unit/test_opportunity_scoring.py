"""Phase 7: the three scores, and the separation that is the point.

The property under test throughout: **a great stock the portfolio already
effectively owns three times over is not a great trade.** A system that cannot
express that is a stock picker, however good its scoring.

The specific failure guarded against is arithmetic. `AllocationCandidate`
computes `total * (1 - correlation_penalty)`, which can only move a number
toward zero -- so a strong enough setup survives any penalty short of total.
Every hard portfolio limit has the opposite shape: a sector cap is not a
preference that a better setup can outbid.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradeit.opportunity import (
    PortfolioContext,
    VetoReason,
    combine,
    score_fit,
)


def _context(**overrides: object) -> PortfolioContext:
    base: dict[str, object] = {
        "equity": Decimal(100_000),
        "cash": Decimal(50_000),
        "open_positions": 2,
        "position_limit": 10,
        "sector_exposure": 0.10,
        "sector_limit": 0.30,
        "heat": 0.02,
        "heat_limit": 0.06,
        "existing_weight": 0.0,
        "max_weight": 0.10,
        "overlap": 0.0,
        "required_cash": Decimal(5_000),
    }
    base.update(overrides)
    return PortfolioContext(**base)  # type: ignore[arg-type]


class TestAVetoIsNotAPenalty:
    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"sector_exposure": 0.30}, VetoReason.SECTOR_LIMIT_REACHED),
            ({"open_positions": 10}, VetoReason.POSITION_LIMIT_REACHED),
            ({"heat": 0.06}, VetoReason.HEAT_LIMIT_EXCEEDED),
            ({"existing_weight": 0.10}, VetoReason.ALREADY_AT_FULL_SIZE),
            ({"required_cash": Decimal(999_999)}, VetoReason.INSUFFICIENT_CASH),
        ],
    )
    def test_each_hard_limit_vetoes(self, overrides: dict, expected: VetoReason) -> None:
        fit = score_fit(_context(**overrides))
        assert expected in fit.vetoes
        assert fit.admissible is False

    def test_a_perfect_setup_cannot_overcome_a_veto(self) -> None:
        """The whole point of the phase, in one assertion.

        A standalone score of 1.0 -- the best possible setup -- against a
        portfolio already at its sector cap. Under `total * (1 - penalty)` this
        would still rank near the top. Here it is simply not permitted.
        """
        fit = score_fit(_context(sector_exposure=0.30))
        final = combine(1.0, fit)
        assert final.admissible is False
        assert final.may_authorise is False

    def test_admissibility_ignores_the_standalone_score_entirely(self) -> None:
        vetoed = score_fit(_context(heat=0.99))
        assert combine(0.0, vetoed).admissible is combine(1.0, vetoed).admissible is False

    def test_an_inadmissible_candidate_still_gets_an_honest_total(self) -> None:
        """ "Forbidden but would have ranked first" is worth seeing.

        Zeroing it would hide what the limit cost, and a trade blocked only by
        a cap is worth revisiting when that cap frees up.
        """
        final = combine(1.0, score_fit(_context(sector_exposure=0.30)))
        assert final.total > 0.0
        assert final.admissible is False


class TestFitRanksBeyondItsVetoes:
    def test_a_crowded_sector_fits_worse_than_an_empty_one(self) -> None:
        crowded = score_fit(_context(sector_exposure=0.29))
        empty = score_fit(_context(sector_exposure=0.0))
        assert empty.score > crowded.score
        # Neither is vetoed; this is ranking, not permission.
        assert crowded.admissible and empty.admissible

    def test_overlap_reduces_fit_without_forbidding(self) -> None:
        """Another way to own what we already own is a worse trade, not an
        impossible one."""
        distinct = score_fit(_context(overlap=0.0))
        duplicate = score_fit(_context(overlap=0.9))
        assert distinct.score > duplicate.score
        assert duplicate.admissible is True

    def test_components_reconcile_with_the_score(self) -> None:
        """Same guard as score 1: a late adjustment applied without being
        recorded leaves an explanation that does not add up."""
        assert score_fit(_context()).reconciles

    def test_the_explanation_is_ordered_by_influence(self) -> None:
        fit = score_fit(_context(sector_exposure=0.29, overlap=0.9))
        contributions = [abs(v) for _, v in fit.explain()]
        assert contributions == sorted(contributions, reverse=True)


class TestCombination:
    def test_neither_input_can_be_ignored(self) -> None:
        """At weight 1.0 the portfolio is ignored; at 0.0 the setup is. Both are
        failures of the thing this phase exists to build, so the default sits
        between them."""
        from tradeit.opportunity.final import DEFAULT_STANDALONE_WEIGHT

        assert 0.0 < DEFAULT_STANDALONE_WEIGHT < 1.0

    def test_fit_moves_the_ranking_even_when_the_setup_is_identical(self) -> None:
        good = combine(0.8, score_fit(_context(sector_exposure=0.0, overlap=0.0)))
        poor = combine(0.8, score_fit(_context(sector_exposure=0.29, overlap=0.9)))
        assert good.total > poor.total
        assert good.standalone == poor.standalone

    def test_an_out_of_range_weight_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            combine(0.5, score_fit(_context()), standalone_weight=1.5)

    def test_the_explanation_carries_the_vetoes_and_the_breakdown(self) -> None:
        explained = combine(0.9, score_fit(_context(sector_exposure=0.30))).explain()
        assert explained["admissible"] is False
        assert "sector_limit_reached" in explained["vetoes"]
        assert explained["fit_components"]
