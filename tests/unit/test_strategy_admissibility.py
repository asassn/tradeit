"""Build the machinery on a survivor-biased corpus; do not believe its numbers.

The rule these tests enforce is written in two places already — ``§5`` of the
strategy builder and the platform's standing constraint on ``research-01`` —
and until now lived only in prose. The distinction that matters is *run* versus
*believe*: entering ``BACKTESTING`` on a biased corpus is the intended use of
that corpus, and leaving it upward is the claim it cannot support.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from tradeit.edgar.denominator import SurvivorshipClass
from tradeit.portfolio.mandate import Mandate
from tradeit.strategy.admissibility import (
    EvidenceNotPermitted,
    admissibility,
    claims_evidence,
    promote_on_corpus,
)
from tradeit.strategy.lifecycle import LADDER, StrategyState, StrategyVersion

NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC)
BIASED = SurvivorshipClass.SURVIVOR_BIASED


def _at(state: StrategyState) -> StrategyVersion:
    version = StrategyVersion(
        strategy_name="swing-breakout", version=1, digest="a" * 16, mandate=Mandate.SWING
    )
    for rung in LADDER[1 : LADDER.index(state) + 1]:
        version = version.transition(rung, citation=f"run/{rung.value}", now=NOW)
    return version


def test_a_survivor_biased_corpus_permits_no_evidence() -> None:
    verdict = admissibility(BIASED)
    assert not verdict.permits_evidence
    assert "failures are absent" in verdict.reason


def test_every_other_classification_permits_evidence() -> None:
    """The standing rule lifts when the gate says anything other than
    survivor-biased, and the three remaining classes are treated alike because
    no written rule distinguishes them."""
    for klass in SurvivorshipClass:
        if klass is BIASED:
            continue
        assert admissibility(klass).permits_evidence, klass


def test_entering_backtesting_is_not_a_claim_about_results() -> None:
    """It says the version runs, which is a fact about the code."""
    assert not claims_evidence(StrategyState.VALIDATED, StrategyState.BACKTESTING)


def test_every_rung_above_backtesting_is_a_claim() -> None:
    ladder = list(LADDER)
    for frm, to in itertools.pairwise(ladder):
        expected = ladder.index(to) >= ladder.index(StrategyState.OUT_OF_SAMPLE_TESTING)
        assert claims_evidence(frm, to) is expected, (frm, to)


def test_a_demotion_is_never_a_claim() -> None:
    """A biased corpus must not block recording that something went wrong."""
    assert not claims_evidence(StrategyState.PAPER_TRADING, StrategyState.DRAFT)


def test_machinery_may_be_built_and_run_on_a_biased_corpus() -> None:
    version = promote_on_corpus(
        _at(StrategyState.VALIDATED),
        StrategyState.BACKTESTING,
        citation="backtest/2026-09",
        classification=BIASED,
        now=NOW,
    )
    assert version.state is StrategyState.BACKTESTING


def test_its_numbers_may_not_be_believed() -> None:
    with pytest.raises(EvidenceNotPermitted) as raised:
        promote_on_corpus(
            _at(StrategyState.BACKTESTING),
            StrategyState.OUT_OF_SAMPLE_TESTING,
            citation="backtest/2026-09 returned 41% CAGR",
            classification=BIASED,
            now=NOW,
        )
    assert raised.value.admissibility.classification is BIASED


def test_a_profitable_result_does_not_change_the_answer() -> None:
    """The refusal is about the corpus, not about the number. A good result on
    a biased corpus means the failures are absent."""
    for citation in ("lost money", "41% CAGR, sharpe 2.9, no drawdown over 8%"):
        with pytest.raises(EvidenceNotPermitted):
            promote_on_corpus(
                _at(StrategyState.BACKTESTING),
                StrategyState.OUT_OF_SAMPLE_TESTING,
                citation=citation,
                classification=BIASED,
                now=NOW,
            )


def test_the_rule_lifts_when_the_gate_stops_saying_survivor_biased() -> None:
    version = promote_on_corpus(
        _at(StrategyState.BACKTESTING),
        StrategyState.OUT_OF_SAMPLE_TESTING,
        citation="backtest/2026-09",
        classification=SurvivorshipClass.PARTIALLY_SURVIVORSHIP_CORRECTED,
        now=NOW,
    )
    assert version.state is StrategyState.OUT_OF_SAMPLE_TESTING


def test_a_demotion_is_permitted_on_a_biased_corpus() -> None:
    version = promote_on_corpus(
        _at(StrategyState.PAPER_TRADING),
        StrategyState.DRAFT,
        citation="",
        classification=BIASED,
        now=NOW,
    )
    assert version.state is StrategyState.DRAFT


def test_the_corpus_refusal_comes_before_the_lifecycle_refusal() -> None:
    """A caller told "you skipped a rung" when the real obstacle is that
    nothing measured can be believed would go and fix the wrong thing."""
    with pytest.raises(EvidenceNotPermitted):
        promote_on_corpus(
            _at(StrategyState.BACKTESTING),
            StrategyState.LIVE,
            citation="skipping ahead",
            classification=BIASED,
            now=NOW,
        )


def test_live_still_needs_the_interlock_on_a_clean_corpus() -> None:
    """A corpus that permits evidence is not an authorisation to trade."""
    from tradeit.strategy.lifecycle import LiveTradingNotAuthorised

    with pytest.raises(LiveTradingNotAuthorised):
        promote_on_corpus(
            _at(StrategyState.ELIGIBLE_FOR_CAPITAL),
            StrategyState.LIVE,
            citation="paper track/2026",
            classification=SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE,
            now=NOW,
        )


def test_a_classification_carries_when_it_was_measured() -> None:
    """A classification is a measurement with an age, not a property; one taken
    before the last import may no longer hold."""
    verdict = admissibility(BIASED, measured_at=NOW)
    assert verdict.measured_at == NOW
