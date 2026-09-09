"""Tests for diversity-aware allocation ranking.

Most of these are built so that ranking by raw score alone gives a *different*
answer from the one asserted. A ranking test where the score order and the
correct order agree proves only that sorting works.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import (
    AllocationCandidate,
    AllocationRanker,
    PortfolioState,
    PositionState,
    PositionStatus,
    Side,
    SizingDecision,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _candidate(
    instrument_id: int,
    total: float,
    *,
    sector: str | None = None,
    rejected: bool = False,
) -> AllocationCandidate:
    score = OpportunityScore(
        instrument_id=instrument_id,
        session_date=dt.date(2024, 6, 3),
        direction=SignalDirection.LONG,
        total=total,
        components=(ScoreComponent(name="trend", raw_value=total, normalised=total, weight=1.0),),
        feature_set_digest="fs",
        strategy_config_digest="sc",
    )
    sizing = SizingDecision(
        instrument_id=instrument_id,
        quantity=Decimal(0) if rejected else Decimal(100),
        entry_price=Decimal(100),
        stop_price=Decimal(95),
        risk_per_share=Decimal(5),
        risk_amount=Decimal(0) if rejected else Decimal(500),
        risk_fraction=Decimal(0),
        notional=Decimal(0) if rejected else Decimal(10000),
        binding_constraint="risk_per_trade",
        rejected=rejected,
        rejection_reason="no stop" if rejected else None,
    )
    return AllocationCandidate(score=score, sizing=sizing, sector=sector)


def _portfolio(*instrument_ids: int, equity: str = "100000") -> PortfolioState:
    positions = tuple(
        PositionState(
            position_id=i,
            portfolio_id=1,
            instrument_id=i,
            side=Side.LONG,
            status=PositionStatus.OPEN,
            quantity=Decimal(100),
            average_entry_price=Decimal(100),
            stop_price=Decimal(95),
            opened_on=dt.date(2024, 5, 1),
        )
        for i in instrument_ids
    )
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(equity),
        equity=Decimal(equity),
        positions=positions,
        last_prices={i: Decimal(100) for i in instrument_ids},
    )


def _ids(ranked: list[AllocationCandidate]) -> list[int]:
    return [c.score.instrument_id for c in ranked]


def _ranker(
    *,
    correlation_weight: float = 0.0,
    sector_penalty: float = 0.0,
    held_sectors: dict[int, str] | None = None,
) -> DiversityAwareRanker:
    return DiversityAwareRanker(
        correlation_weight=correlation_weight,
        sector_penalty_per_holding=sector_penalty,
        held_sectors=held_sectors or {},
    )


def test_satisfies_the_allocation_ranker_protocol() -> None:
    ranker: AllocationRanker = _ranker()
    assert ranker.name == "diversity_aware"


def test_ranks_by_score_when_nothing_differentiates_the_candidates() -> None:
    ranked = _ranker().rank(
        [_candidate(1, 0.4), _candidate(2, 0.9), _candidate(3, 0.6)],
        _portfolio(),
    )
    assert _ids(ranked) == [2, 3, 1]


class TestSectorCrowding:
    def test_a_second_name_from_one_sector_yields_to_a_lower_scoring_diversifier(self) -> None:
        ranked = _ranker(sector_penalty=0.20).rank(
            [
                _candidate(1, 1.00, sector="Energy"),
                _candidate(2, 0.95, sector="Energy"),
                _candidate(3, 0.90, sector="Financials"),
            ],
            _portfolio(),
        )
        # By raw score alone this would be 1, 2, 3.
        assert _ids(ranked) == [1, 3, 2]

    def test_crowding_is_charged_against_what_is_already_held(self) -> None:
        """Not only against what is already selected from this slate."""
        ranked = _ranker(
            sector_penalty=0.20,
            held_sectors={50: "Energy", 51: "Energy"},
        ).rank(
            [_candidate(1, 1.00, sector="Energy"), _candidate(2, 0.90, sector="Financials")],
            _portfolio(50, 51),
        )
        assert _ids(ranked) == [2, 1]

    def test_without_held_sectors_the_ranker_sees_only_the_slate(self) -> None:
        """The reason the field is required rather than defaulted to empty."""
        ranked = _ranker(sector_penalty=0.20).rank(
            [_candidate(1, 1.00, sector="Energy"), _candidate(2, 0.90, sector="Financials")],
            _portfolio(50, 51),
        )
        assert _ids(ranked) == [1, 2]

    def test_an_unattributable_sector_is_not_pooled_into_a_penalty_group(self) -> None:
        ranked = _ranker(sector_penalty=0.50).rank(
            [_candidate(1, 1.00), _candidate(2, 0.95), _candidate(3, 0.90)],
            _portfolio(),
        )
        assert _ids(ranked) == [1, 2, 3]
        assert all(c.correlation_penalty == 0.0 for c in ranked)

    def test_the_penalty_cannot_exceed_the_whole_score(self) -> None:
        ranked = _ranker(
            sector_penalty=0.60,
            held_sectors={50: "Energy", 51: "Energy", 52: "Energy"},
        ).rank([_candidate(1, 1.00, sector="Energy")], _portfolio(50, 51, 52))
        assert ranked[0].correlation_penalty == 1.0
        assert ranked[0].adjusted_score == 0.0


class TestCorrelation:
    def test_a_measured_low_correlation_beats_a_higher_unmeasured_score(self) -> None:
        """Unknown is charged as though it were perfectly correlated."""
        ranked = _ranker(correlation_weight=0.5).rank(
            [_candidate(1, 1.00), _candidate(2, 0.90), _candidate(3, 0.95)],
            _portfolio(),
            correlation={(1, 2): 0.1},
        )
        assert _ids(ranked) == [1, 2, 3]

    def test_no_correlation_source_at_all_applies_no_correlation_penalty(self) -> None:
        """An absent source is different from a source that omits the pair."""
        ranked = _ranker(correlation_weight=0.5).rank(
            [_candidate(1, 1.00), _candidate(2, 0.90), _candidate(3, 0.95)],
            _portfolio(),
            correlation=None,
        )
        assert _ids(ranked) == [1, 3, 2]

    def test_correlation_is_charged_against_held_names(self) -> None:
        ranked = _ranker(correlation_weight=0.5).rank(
            [_candidate(1, 1.00), _candidate(2, 0.90)],
            _portfolio(50),
            correlation={(1, 50): 0.95, (2, 50): 0.05, (1, 2): 0.05},
        )
        assert _ids(ranked) == [2, 1]


class TestIneligibleCandidates:
    def test_a_refused_sizing_ranks_last_and_is_not_dropped(self) -> None:
        ranked = _ranker().rank(
            [_candidate(1, 0.99, rejected=True), _candidate(2, 0.10)],
            _portfolio(),
        )
        assert _ids(ranked) == [2, 1]
        assert len(ranked) == 2

    def test_a_non_positive_score_is_excluded_rather_than_penalised(self) -> None:
        """Penalising a negative score would raise it: (1 - p) shrinks magnitude."""
        ranked = _ranker(sector_penalty=0.5, held_sectors={50: "Energy"}).rank(
            [_candidate(1, -2.0, sector="Energy"), _candidate(2, 0.10, sector="Energy")],
            _portfolio(50),
        )
        assert _ids(ranked) == [2, 1]
        assert ranked[1].correlation_penalty == 0.0


class TestDeterminism:
    def test_the_same_slate_ranks_identically_twice(self) -> None:
        ranker = _ranker(correlation_weight=0.5, sector_penalty=0.2)
        slate = [
            _candidate(1, 0.9, sector="Energy"),
            _candidate(2, 0.9, sector="Energy"),
            _candidate(3, 0.9, sector="Financials"),
        ]
        first = _ids(ranker.rank(slate, _portfolio(), correlation={}))
        second = _ids(ranker.rank(slate, _portfolio(), correlation={}))
        assert first == second

    def test_ties_break_on_the_lower_instrument_id(self) -> None:
        ranked = _ranker().rank(
            [_candidate(9, 0.5), _candidate(2, 0.5), _candidate(5, 0.5)],
            _portfolio(),
        )
        assert _ids(ranked) == [2, 5, 9]


def test_the_recorded_penalty_explains_the_ordering() -> None:
    """The ranking must be auditable, not merely correct."""
    ranked = _ranker(sector_penalty=0.20).rank(
        [
            _candidate(1, 1.00, sector="Energy"),
            _candidate(2, 0.95, sector="Energy"),
            _candidate(3, 0.90, sector="Financials"),
        ],
        _portfolio(),
    )
    by_id = {c.score.instrument_id: c for c in ranked}
    assert by_id[1].correlation_penalty == 0.0
    assert by_id[3].correlation_penalty == 0.0
    assert by_id[2].correlation_penalty == 0.20
    assert by_id[2].adjusted_score < by_id[3].adjusted_score
