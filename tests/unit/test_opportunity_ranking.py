"""Score 3 in the allocation seat, authorised 2026-10-18.

The property under test is the one the whole phase exists for: **the ranking
must be able to prefer a worse setup that fits better, and must refuse a better
setup the portfolio cannot take.** A ranker that only ever reorders by score 1
has wired nothing.

Three failures are guarded specifically, each of which produces a plausible
number rather than an error:

* **Blending incommensurate scales.** ``combine`` is linear and
  ``OpportunityScore.total`` declares no range, so a raw spread fraction
  blended at a declared 0.6 weight yields a number the fit dominates entirely.
  The ranker refuses rather than normalising on the caller's behalf.
* **A veto that merely demotes.** A sector cap is not a preference a better
  setup can outbid, and a refusal expressed as a low number can be outbid.
* **An explanation that stops adding up.** ``normalise_total`` rescales a score,
  and a rescale that leaves the components behind breaks
  :attr:`OpportunityScore.reconciles` — the property that exists to catch
  exactly that.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import PositionStatus, Side, SignalDirection
from tradeit.opportunity import (
    OpportunityFitRanker,
    StandaloneScoreOutOfRange,
    normalise_total,
)
from tradeit.portfolio.base import (
    AllocationCandidate,
    PortfolioState,
    PositionState,
    SizingDecision,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

SESSION = dt.date(2015, 6, 1)


def _score(instrument_id: int, total: float) -> OpportunityScore:
    return OpportunityScore(
        instrument_id=instrument_id,
        session_date=SESSION,
        direction=SignalDirection.LONG,
        total=total,
        components=(ScoreComponent(name="setup", raw_value=total, normalised=total, weight=1.0),),
        feature_set_digest="test",
        strategy_config_digest="test",
    )


def _sizing(instrument_id: int, notional: Decimal = Decimal(5_000)) -> SizingDecision:
    return SizingDecision(
        instrument_id=instrument_id,
        quantity=Decimal(100),
        entry_price=Decimal(50),
        stop_price=Decimal(46),
        risk_per_share=Decimal(4),
        risk_amount=Decimal(400),
        risk_fraction=Decimal("0.004"),
        notional=notional,
        binding_constraint="risk",
    )


def _candidate(
    instrument_id: int, total: float, sector: str | None = None, notional: Decimal = Decimal(5_000)
) -> AllocationCandidate:
    return AllocationCandidate(
        score=_score(instrument_id, total),
        sizing=_sizing(instrument_id, notional),
        sector=sector,
    )


def _position(instrument_id: int, quantity: Decimal, price: Decimal) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=quantity,
        average_entry_price=price,
        stop_price=price * Decimal("0.92"),
        opened_on=SESSION,
    )


def _portfolio(
    positions: tuple[PositionState, ...] = (),
    cash: Decimal = Decimal(50_000),
    equity: Decimal = Decimal(100_000),
    last_prices: dict[int, Decimal] | None = None,
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(SESSION, dt.time(), tzinfo=dt.UTC),
        cash=cash,
        equity=equity,
        positions=positions,
        last_prices=last_prices or {},
    )


def _ranker(**overrides: object) -> OpportunityFitRanker:
    config = StrategyConfig(name="ma_cross_baseline")
    kwargs: dict[str, object] = {
        "risk": config.risk,
        "sizing": config.sizing,
        "sectors": {},
    }
    kwargs.update(overrides)
    return OpportunityFitRanker(**kwargs)  # type: ignore[arg-type]


def test_an_unbounded_standalone_total_is_refused_not_normalised() -> None:
    """The scale trap. 0.02 blended at weight 0.6 is dominated by the fit.

    Refusing is the point: a caller whose scorer is unbounded must normalise
    where the scale is understood and say so. Normalising here would silently
    make `standalone_weight` describe a mixture that is not happening.
    """
    ranker = _ranker()
    with pytest.raises(StandaloneScoreOutOfRange, match="outside"):
        ranker.rank([_candidate(1, 0.02 + 1.0)], _portfolio())


def test_a_negative_standalone_total_is_refused_too() -> None:
    ranker = _ranker()
    with pytest.raises(StandaloneScoreOutOfRange):
        ranker.rank([_candidate(1, -0.01)], _portfolio())


def test_a_worse_setup_that_fits_better_can_win() -> None:
    """The claim the phase exists for, as an executable assertion.

    Instrument 2 has the weaker setup and no sector exposure behind it;
    instrument 1 has the stronger setup and sits in a sector at 29% against a
    30% cap. Fit must be able to overturn the setup ranking.

    **The margins are deliberate, not tuned.** `sector_headroom` carries weight
    0.25, so a sector at 29/30 of its cap contributes 0.0083 against an empty
    sector's 0.25 -- a fit gap of 0.2417. At the default standalone weight of
    0.6 that gap can overturn a setup advantage of at most
    ``0.2417 * 0.4 / 0.6 = 0.161``. A first version of this test used a 0.30
    setup gap and failed, which is the ranker being right: **fit is a weighted
    input, not an override**, and a test that demanded otherwise would have been
    asserting a behaviour the design explicitly rejects.
    """
    sectors = {1: "Crowded", 2: "Empty", 9: "Crowded"}
    held = (_position(9, Decimal(580), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker(sectors=sectors)

    ranked = ranker.rank([_candidate(1, 0.90), _candidate(2, 0.80)], portfolio)

    assert [c.score.instrument_id for c in ranked] == [2, 1], (
        "the crowded sector's stronger setup must not outrank the empty sector's weaker one"
    )


def test_fit_cannot_override_a_setup_advantage_larger_than_its_weight_allows() -> None:
    """The boundary the test above establishes, asserted from the other side.

    Same portfolio, same sectors, a 0.30 setup gap instead of 0.10. Fit loses,
    and that is correct: `standalone_weight` is a declared blend and a ranking
    input that could always win would make it a lie.
    """
    sectors = {1: "Crowded", 2: "Empty", 9: "Crowded"}
    held = (_position(9, Decimal(580), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker(sectors=sectors)

    ranked = ranker.rank([_candidate(1, 0.90), _candidate(2, 0.60)], portfolio)

    assert [c.score.instrument_id for c in ranked] == [1, 2]


def test_a_lower_standalone_weight_lets_fit_overturn_the_same_gap() -> None:
    """And the weight is what decides it, which pins the blend to the parameter."""
    sectors = {1: "Crowded", 2: "Empty", 9: "Crowded"}
    held = (_position(9, Decimal(580), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})

    at_default = _ranker(sectors=sectors).rank(
        [_candidate(1, 0.90), _candidate(2, 0.60)], portfolio
    )
    fit_led = _ranker(sectors=sectors, standalone_weight=0.2).rank(
        [_candidate(1, 0.90), _candidate(2, 0.60)], portfolio
    )

    assert [c.score.instrument_id for c in at_default] == [1, 2]
    assert [c.score.instrument_id for c in fit_led] == [2, 1]


def test_the_setup_still_wins_when_fit_is_equal() -> None:
    """The converse, or the test above proves only that fit dominates always."""
    ranker = _ranker()
    ranked = ranker.rank([_candidate(1, 0.40), _candidate(2, 0.80)], _portfolio())
    assert [c.score.instrument_id for c in ranked] == [2, 1]


def test_a_veto_refuses_and_cannot_be_outbid() -> None:
    """A sector cap is not a preference. A perfect setup must still be refused."""
    sectors = {1: "Crowded", 9: "Crowded"}
    # 35% of equity already in Crowded, against a 30% cap.
    held = (_position(9, Decimal(700), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker(sectors=sectors)

    ranked = ranker.rank([_candidate(1, 1.00)], portfolio)

    assert ranked == (), "a 1.00 setup in a sector over its cap must be refused, not demoted"


def test_refuse_vetoed_false_keeps_it_but_still_sorts_it_last() -> None:
    """Measurement mode must not become a way to let a vetoed trade through."""
    sectors = {1: "Crowded", 2: "Empty", 9: "Crowded"}
    held = (_position(9, Decimal(700), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker(sectors=sectors, refuse_vetoed=False)

    ranked = ranker.rank([_candidate(1, 1.00), _candidate(2, 0.10)], portfolio)

    assert [c.score.instrument_id for c in ranked] == [2, 1]
    assert len(ranked) == 2


def test_an_unmeasured_correlation_pair_does_not_block_the_ranking() -> None:
    """The deliberate asymmetry with CorrelationRule, which fails closed.

    A ranker that refused to rank an unmeasured pair would decline to choose at
    all, and the caller has no such option.
    """
    held = (_position(9, Decimal(100), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker()
    ranked = ranker.rank([_candidate(1, 0.50)], portfolio, correlation={})
    assert [c.score.instrument_id for c in ranked] == [1]


def test_overlap_demotes_a_candidate_correlated_with_a_holding() -> None:
    held = (_position(9, Decimal(100), Decimal(50)),)
    portfolio = _portfolio(positions=held, last_prices={9: Decimal(50)})
    ranker = _ranker()
    correlation = {(1, 9): 0.95, (2, 9): 0.00}

    ranked = ranker.rank(
        [_candidate(1, 0.60), _candidate(2, 0.60)], portfolio, correlation=correlation
    )

    assert [c.score.instrument_id for c in ranked] == [2, 1], (
        "equal setups must be separated by overlap with what is already held"
    )


def test_an_empty_slate_ranks_to_nothing_without_touching_the_portfolio() -> None:
    assert _ranker().rank([], _portfolio()) == ()


def test_normalise_total_keeps_the_explanation_adding_up() -> None:
    """A rescale that leaves the components behind breaks `reconciles`."""
    score = _score(1, 0.02)
    rescaled = normalise_total(score, lower=0.0, upper=0.10)
    assert rescaled.total == pytest.approx(0.20)
    assert rescaled.reconciles, "components must still sum to the rescaled total"


def test_normalise_total_clamps_rather_than_raising_outside_its_declared_range() -> None:
    assert normalise_total(_score(1, 0.50), lower=0.0, upper=0.10).total == pytest.approx(1.0)
    assert normalise_total(_score(1, -0.50), lower=0.0, upper=0.10).total == pytest.approx(0.0)


def test_normalise_total_refuses_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="upper must exceed lower"):
        normalise_total(_score(1, 0.02), lower=0.10, upper=0.0)


def test_a_normalised_score_then_ranks_without_refusal() -> None:
    """The two halves compose: normalise, then rank. Otherwise the refusal above
    is a wall with no door."""
    raw = _candidate(1, 0.02)
    normalised = AllocationCandidate(
        score=normalise_total(raw.score, lower=0.0, upper=0.10),
        sizing=raw.sizing,
        sector=raw.sector,
    )
    ranked = _ranker().rank([normalised], _portfolio())
    assert [c.score.instrument_id for c in ranked] == [1]


def test_sector_exposures_agrees_with_the_risk_snapshot_copy() -> None:
    """The duplication in `ranking._sector_exposures` is pinned to its original.

    Four duplicated lines avoid a package cycle; an untested copy of them would
    silently drift into a second answer to the same question.
    """
    from tradeit.opportunity.ranking import _sector_exposures
    from tradeit.risk.snapshot import sector_exposures

    # TWO positions in sector A, deliberately. A first version used one per
    # sector and a mutant that replaced the accumulation `totals[s] = totals[s]
    # + value` with `totals[s] = value` passed every test -- the copy could
    # silently stop summing and nothing objected. Instrument 3 is unclassified
    # so the pooled bucket is covered too.
    sectors = {1: "A", 2: "B", 4: "A"}
    portfolio = _portfolio(
        positions=(
            _position(1, Decimal(100), Decimal(50)),
            _position(2, Decimal(200), Decimal(25)),
            _position(4, Decimal(150), Decimal(40)),
            _position(3, Decimal(80), Decimal(10)),
        ),
        last_prices={1: Decimal(60), 2: Decimal(20), 4: Decimal(45), 3: Decimal(12)},
    )
    mine = _sector_exposures(portfolio, sectors)
    assert mine == sector_exposures(portfolio, sectors)
    # And the sum is a real sum: A holds 100*60 + 150*45 = 12,750 of 100,000.
    assert mine["A"] == pytest.approx(0.1275)
    assert "UNCLASSIFIED" in mine


def test_parameters_record_what_the_ranking_was_run_with() -> None:
    """A ranked result whose settings are not recorded is not reproducible."""
    ranker = _ranker(sectors={1: "A"}, standalone_weight=0.75)
    assert ranker.name == "opportunity_fit"
    assert ranker.parameters["standalone_weight"] == 0.75
    assert ranker.parameters["classified_instruments"] == 1
    assert ranker.parameters["refuse_vetoed"] is True
