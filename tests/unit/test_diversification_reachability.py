"""Whether the diversification the ranker offers can reach a backtest at all.

`SIGNAL_SCOREBOARD.md` §54 records that **every portfolio-level result on the
scoreboard was produced with diversification switched off**, and names the
cause as one line in `build_engine` holding ``correlation_weight`` and
``sector_penalty_per_holding`` at 0.0. Read as a configuration decision, that
invites somebody to turn the numbers up and re-read the archive.

Measured 2026-11-24 (`docs/BREADTH_2026-11-24.md`), turning them up through
`build_engine`'s ``ranker=`` argument produced a result **identical to the
control on every arm, sample and delisting assumption**. Two separate reasons,
and this file pins both, because each is a silent no-op rather than an error:

* `EventDrivenEngine` calls ``cycle.plan`` with three arguments. `PortfolioCycle`
  accepts a fourth, ``correlation``, and passes it to the ranker, so every
  backtest ever run here ranked with ``correlation=None`` and
  ``correlation_weight`` multiplied nothing.
* ``_candidate`` in `backtesting/baselines.py` constructs every baseline
  candidate with ``sector=None``, and `DiversityAwareRanker._penalty` charges
  nothing to a candidate with no sector.

**These tests assert unreachability, which is an unusual thing to want.** They
exist so that wiring either path makes a test *fail* rather than quietly
changing what every archived number means. A failure here is not a regression:
it is the notice that §54's switch came alive, and the archive's comparability
has to be re-argued before the change lands.

Diversification's *substance* is not untested — §58 installed
`SectorExposureRule` through ``extra_rules`` across 64 arms and found a mean
change indistinguishable from zero. That is the risk-rule form, reachable and
measured. This file is about the ranker form only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from tradeit.backtesting.baselines import (
    BuyAndHold,
    EqualWeightBenchmark,
    MovingAverageCross,
    NaiveMomentumClassifier,
)
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.core.models import OhlcvBar
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import AllocationCandidate, PortfolioState
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection
from tradeit.strategy.config import RiskConfig, SizingConfig

from .test_backtesting_engine import START, _bar, _candidate, _engine, _Script, _spec


@dataclass
class _SpyRanker:
    """Records what the cycle hands it, and otherwise ranks by score.

    Deliberately not a `DiversityAwareRanker` subclass: the question is what
    arrives at the `AllocationRanker` boundary, and a subclass could answer it
    by inheriting behaviour the engine never exercises.
    """

    seen: list[object] = field(default_factory=list)
    name: str = "spy"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    def rank(
        self,
        candidates: Sequence[AllocationCandidate],
        portfolio: PortfolioState,
        correlation: dict[tuple[int, int], float] | None = None,
    ) -> list[AllocationCandidate]:
        self.seen.append(correlation)
        return sorted(candidates, key=lambda c: -c.score.total)


def _two_session_script() -> _Script:
    """Two candidates on day one, and bars for long enough to rank them."""
    days = [START + dt.timedelta(days=n) for n in range(4)]
    return _Script(
        bars_by_session={
            day: {
                1: _bar(1, day, open_="100", close="101"),
                2: _bar(2, day, open_="50", close="51"),
            }
            for day in days
        },
        candidates_by_session={
            START: [_candidate(1, "100", "92"), _candidate(2, "50", "46")],
        },
    )


def _scored(instrument_id: int, total: float, entry: str, stop: str) -> EntryCandidate:
    """A candidate with a chosen score, which `_candidate` cannot give (it pins 1.0)."""
    return EntryCandidate(
        score=OpportunityScore(
            instrument_id=instrument_id,
            session_date=START,
            direction=SignalDirection.LONG,
            total=total,
            components=(ScoreComponent(name="t", raw_value=total, normalised=total, weight=1.0),),
            feature_set_digest="fs",
            strategy_config_digest="sc",
        ),
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        sector=None,
    )


def _reordering_script() -> _Script:
    """One held name, one free slot, and two candidates a penalty would reorder.

    Instrument 1 is offered alone on the first session so it is held before the
    choice is made. On the second session 2 and 3 are offered: 2 scores higher
    but is the correlated one, 3 scores lower and is the diversifier. Their
    closes then diverge, so which one the book took is visible in the equity
    curve rather than only in the trade list.
    """
    days = [START + dt.timedelta(days=n) for n in range(7)]
    bars: dict[dt.date, dict[int, OhlcvBar]] = {}
    for index, day in enumerate(days):
        bars[day] = {
            1: _bar(1, day, open_="100", close="100"),
            # 2 falls, 3 rises: a book holding 2 and a book holding 3 cannot
            # have the same equity, so the assertion is on the outcome rather
            # than on an intermediate the ranker could change without effect.
            2: _bar(2, day, open_="50", close=str(50 - index)),
            3: _bar(3, day, open_="50", close=str(50 + index)),
        }
    return _Script(
        bars_by_session=bars,
        candidates_by_session={
            days[0]: [_scored(1, 1.0, "100", "92")],
            days[1]: [_scored(2, 0.9, "49", "45"), _scored(3, 0.5, "51", "47")],
        },
    )


def _one_slot_engine(data: _Script) -> EventDrivenEngine:
    """`_engine`, but the book may hold two positions and no more.

    The cap is what makes the ranking order decide an outcome: with room for
    everything, a reordered slate buys the same set in a different sequence and
    the equity curve cannot tell the difference.
    """
    engine = _engine(data)
    sizing = SizingConfig()
    risk = RiskConfig(max_positions=2)
    object.__setattr__(
        engine.cycle,
        "engine",
        MostRestrictiveEngine(
            rule_set=(
                PortfolioHeatRule(config=risk),
                MaxPositionsRule(config=risk),
                GrossExposureRule(config=risk),
            ),
            sizing=sizing,
        ),
    )
    return engine


class TestCorrelationCannotReachABacktest:
    """The engine does not pass a matrix, so the weight multiplies nothing."""

    def test_the_engine_hands_the_ranker_no_correlation_matrix(self) -> None:
        spy = _SpyRanker()
        engine = _engine(_two_session_script())
        object.__setattr__(engine.cycle, "ranker", spy)

        engine.run(_spec(days=3))

        # Non-empty first: an assertion that every element of an empty list is
        # None is the vacuous pass this whole file is written to avoid.
        assert spy.seen, "the ranker was never called, so this test proves nothing"
        assert all(matrix is None for matrix in spy.seen), (
            f"the engine passed a correlation matrix on "
            f"{sum(m is not None for m in spy.seen)} of {len(spy.seen)} calls; "
            "correlation weighting has become reachable from a backtest and every "
            "archived result's comparability needs re-arguing -- see this module's "
            "docstring and SIGNAL_SCOREBOARD §54"
        )

    def test_turning_the_correlation_weight_up_changes_no_backtest(self) -> None:
        """The same fact end to end, in a scenario where a live matrix must bite.

        **This test was vacuous when first written and the first version is
        worth recording.** It offered two equally scored candidates and asserted
        that weight 0.0 and 0.9 produced the same equity curve. They did -- and
        they still did with a correlation matrix wired into the engine, so the
        test passed against the very change it existed to catch. Two reasons,
        both properties of `DiversityAwareRanker._penalty`:

        * the **first** pick is never penalised, because ``taken`` is empty and
          there is nothing to be correlated with;
        * penalising a candidate that is already last does not reorder anything,
          and with capital for every candidate the order does not matter either.

        So the scenario below is built so that a live penalty must change *which
        name is held*: the book already holds one position, there is room for
        exactly one more, and the better-scoring of the two remaining candidates
        is the one correlated with what is held. Without a penalty the book takes
        the higher score; with one it takes the diversifier instead, and the two
        have different price paths.
        """
        curves = []
        for weight in (0.0, 0.9):
            engine = _one_slot_engine(_reordering_script())
            object.__setattr__(
                engine.cycle,
                "ranker",
                DiversityAwareRanker(
                    correlation_weight=weight,
                    sector_penalty_per_holding=0.0,
                    held_sectors={},
                ),
            )
            result = engine.run(_spec(days=6))
            curves.append(result.equity_curve)

        assert len(curves[0]) > 2, "the run produced no curve to compare"
        assert curves[0] == curves[1], (
            "a 0.9 correlation weight moved the equity curve, so it is being "
            "applied; §54's switch is live and the archive's results are no "
            "longer all produced the same way"
        )


class TestSectorPenaltiesCannotReachABaseline:
    """Every baseline candidate carries no sector, so the penalty charges nothing."""

    def test_no_baseline_offers_a_candidate_with_a_sector(self) -> None:
        day = START
        bars = {
            1: _bar(1, day, open_="100", close="104"),
            2: _bar(2, day, open_="50", close="52"),
        }
        sources = (
            BuyAndHold(stop_pct=Decimal("0.08")),
            EqualWeightBenchmark(cadence_sessions=1, stop_pct=Decimal("0.08")),
            NaiveMomentumClassifier(stop_pct=Decimal("0.08")),
            MovingAverageCross(fast=1, slow=2, stop_pct=Decimal("0.08")),
        )
        # `MovingAverageCross` fails closed without a split source rather than
        # keeping unrestated history (baselines.py:196). A no-split source is
        # the honest stand-in here: this test is about sectors, and a rule that
        # refused to run would be excluded from it by accident.
        for source in sources:
            if isinstance(source, MovingAverageCross):
                source.splits_on = lambda _session: {}
        offered = 0
        for source in sources:
            # Called twice: the momentum and crossover rules need a prior bar
            # before they will offer anything, and a source that offered nothing
            # would let this test pass without examining a single candidate.
            for session in (day, day + dt.timedelta(days=1)):
                shifted = {
                    i: _bar(i, session, open_=str(b.open), close=str(b.close * 2))
                    for i, b in bars.items()
                }
                for candidate in source(session, shifted):
                    offered += 1
                    assert candidate.sector is None, (
                        f"{type(source).__name__} offered a candidate with sector "
                        f"{candidate.sector!r}; sector penalties have become reachable "
                        "from the baseline arms and every floor and scoreboard number "
                        "produced through them needs re-arguing"
                    )
        assert offered, "no baseline offered any candidate, so this test proves nothing"

    def test_a_sector_penalty_charges_nothing_to_a_sectorless_candidate(self) -> None:
        """The other half: even a huge penalty cannot price a `None` sector.

        Pinned separately from the candidates themselves, because the two could
        decay independently -- a baseline could start supplying sectors, or the
        ranker could start charging for their absence.
        """
        ranker = DiversityAwareRanker(
            correlation_weight=0.0, sector_penalty_per_holding=10.0, held_sectors={1: "Energy"}
        )
        penalty = ranker._penalty(
            AllocationCandidate(
                score=_candidate(2, "50", "46").score,
                sizing=_sizing(),
                sector=None,
            ),
            taken=[1],
            sector_counts={"Energy": 3},
            correlation=None,
        )
        assert penalty == 0.0


def _sizing() -> object:
    from tradeit.portfolio.base import SizingDecision
    from tradeit.portfolio.sizing import BindingConstraint

    return SizingDecision(
        instrument_id=2,
        quantity=Decimal(10),
        entry_price=Decimal(50),
        stop_price=Decimal(46),
        risk_per_share=Decimal(4),
        risk_amount=Decimal(40),
        risk_fraction=0.0004,
        notional=Decimal(500),
        binding_constraint=BindingConstraint.RISK,
    )
