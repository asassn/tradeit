"""§5's states, and the promotions the platform must refuse.

The sentence these tests exist to enforce is *"no strategy becomes LIVE because
a backtest was profitable"*. That is easy to write and easy to erode: one
convenience transition from ``BACKTESTING`` to ``LIVE`` for a demo, and the
four claims in between are gone. :func:`test_no_path_from_backtest_to_live_in_one_move`
and the reachability walk below are the guards.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.portfolio.mandate import Mandate
from tradeit.strategy.lifecycle import (
    LADDER,
    IllegalTransition,
    LiveTradingNotAuthorised,
    PromotionWithoutEvidence,
    StrategyState,
    StrategyVersion,
    is_promotion,
    may_follow,
    successors,
)

NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC)


def _version(state: StrategyState = StrategyState.DRAFT) -> StrategyVersion:
    return StrategyVersion(
        strategy_name="swing-breakout",
        version=1,
        digest="a" * 16,
        mandate=Mandate.SWING,
        state=state,
    )


def _climb(to: StrategyState) -> StrategyVersion:
    """Walk the ladder honestly, one rung at a time, up to ``to``."""
    version = _version()
    for rung in LADDER[1 : LADDER.index(to) + 1]:
        version = version.transition(rung, citation=f"run/{rung.value}", now=NOW)
    return version


# -- the ladder ------------------------------------------------------------


def test_paused_and_retired_are_not_rungs() -> None:
    """They are not degrees of earned evidence; putting them on the ladder
    would make "promotion to paused" expressible, which means nothing."""
    assert StrategyState.PAUSED not in LADDER
    assert StrategyState.RETIRED not in LADDER
    assert set(LADDER) | {StrategyState.PAUSED, StrategyState.RETIRED} == set(StrategyState)


def test_promotion_is_exactly_one_rung() -> None:
    assert may_follow(StrategyState.DRAFT, StrategyState.VALIDATED) is None
    why = may_follow(StrategyState.DRAFT, StrategyState.BACKTESTING)
    assert why is not None and "one rung at a time" in why


def test_no_path_from_backtest_to_live_in_one_move() -> None:
    """The sentence the whole state machine exists for."""
    why = may_follow(StrategyState.BACKTESTING, StrategyState.LIVE)
    assert why is not None
    assert StrategyState.LIVE not in successors(StrategyState.BACKTESTING)


def test_live_is_reachable_only_through_every_intervening_claim() -> None:
    """Walk every state and confirm the only way into LIVE is from the rung
    immediately below it, or by resuming a pause."""
    into_live = {s for s in StrategyState if may_follow(s, StrategyState.LIVE) is None}
    assert into_live == {StrategyState.ELIGIBLE_FOR_CAPITAL, StrategyState.PAUSED}


def test_demotion_may_skip_any_distance() -> None:
    """A contaminated backtest invalidates everything built on it, so the fall
    is a single legitimate move rather than six."""
    assert may_follow(StrategyState.LIVE, StrategyState.DRAFT) is None
    assert may_follow(StrategyState.PAPER_TRADING, StrategyState.VALIDATED) is None


def test_demotion_is_not_a_promotion() -> None:
    assert not is_promotion(StrategyState.LIVE, StrategyState.DRAFT)
    assert is_promotion(StrategyState.DRAFT, StrategyState.VALIDATED)


def test_only_a_live_version_can_be_paused() -> None:
    assert may_follow(StrategyState.LIVE, StrategyState.PAUSED) is None
    why = may_follow(StrategyState.PAPER_TRADING, StrategyState.PAUSED)
    assert why is not None and "nothing else is running" in why


def test_resuming_a_pause_is_not_a_promotion() -> None:
    """It reclaims a rung the version already held; requiring fresh evidence
    for it would be theatre."""
    assert not is_promotion(StrategyState.PAUSED, StrategyState.LIVE)


def test_a_paused_version_may_be_demoted_rather_than_resumed() -> None:
    assert may_follow(StrategyState.PAUSED, StrategyState.DRAFT) is None


def test_retirement_is_reachable_from_everywhere_and_is_final() -> None:
    for state in StrategyState:
        if state is StrategyState.RETIRED:
            continue
        assert may_follow(state, StrategyState.RETIRED) is None
    assert successors(StrategyState.RETIRED) == ()


def test_a_state_cannot_transition_to_itself() -> None:
    for state in StrategyState:
        assert may_follow(state, state) is not None


# -- evidence --------------------------------------------------------------


def test_promotion_without_a_citation_is_refused() -> None:
    with pytest.raises(PromotionWithoutEvidence):
        _version().transition(StrategyState.VALIDATED, now=NOW)


def test_demotion_needs_no_citation() -> None:
    """Refusing to record a discovered flaw until paperwork exists would leave
    a known-bad version sitting at its old rung."""
    live = _climb(StrategyState.ELIGIBLE_FOR_CAPITAL)
    dropped = live.transition(StrategyState.DRAFT, now=NOW)
    assert dropped.state is StrategyState.DRAFT


def test_history_records_every_move_in_order() -> None:
    version = _climb(StrategyState.PAPER_TRADING)
    assert [e.to for e in version.history] == list(LADDER[1:5])
    assert version.evidence_for(StrategyState.BACKTESTING)[0].citation == "run/backtesting"


def test_has_reached_survives_a_demotion() -> None:
    """A version demoted from paper trading is not the same object as one that
    was never tested, and a comparison that cannot tell them apart treats a
    failure as a fresh start."""
    version = _climb(StrategyState.PAPER_TRADING).transition(StrategyState.DRAFT, now=NOW)
    assert version.state is StrategyState.DRAFT
    assert version.has_reached is StrategyState.PAPER_TRADING


def test_transition_returns_a_new_version_and_does_not_mutate() -> None:
    before = _version()
    after = before.transition(StrategyState.VALIDATED, citation="registry check", now=NOW)
    assert before.state is StrategyState.DRAFT
    assert after.state is StrategyState.VALIDATED
    assert before.history == ()


# -- editing ---------------------------------------------------------------


def test_an_edit_creates_a_new_version_at_draft_with_no_history() -> None:
    parent = _climb(StrategyState.PAPER_TRADING)
    child = parent.edit(digest="b" * 16)
    assert child.version == parent.version + 1
    assert child.state is StrategyState.DRAFT
    assert child.history == ()
    assert child.parent_digest == parent.digest


def test_an_edit_does_not_inherit_the_parents_evidence() -> None:
    """The parent's backtest was run on the parent's parameters. Carrying it
    forward is how a tuned threshold acquires a claim nobody tested."""
    parent = _climb(StrategyState.ELIGIBLE_FOR_CAPITAL)
    child = parent.edit(digest="c" * 16)
    assert child.has_reached is StrategyState.DRAFT
    assert child.evidence_for(StrategyState.BACKTESTING) == ()


def test_an_edit_that_changes_nothing_is_refused() -> None:
    with pytest.raises(ValueError):
        _version().edit(digest="a" * 16)


def test_an_edit_keeps_the_mandate() -> None:
    """§7: a version belongs to exactly one mandate. Editing parameters does
    not turn a swing strategy into a day strategy."""
    child = _version().edit(digest="d" * 16)
    assert child.mandate is Mandate.SWING


# -- the live interlock ----------------------------------------------------


def test_live_is_refused_without_an_authorisation() -> None:
    version = _climb(StrategyState.ELIGIBLE_FOR_CAPITAL)
    with pytest.raises(LiveTradingNotAuthorised):
        version.transition(StrategyState.LIVE, citation="paper track/2026", now=NOW)


def test_the_live_refusal_survives_a_perfect_record() -> None:
    """Every rung earned, every citation present, and it still refuses. The
    interlock is not a function of evidence."""
    version = _climb(StrategyState.ELIGIBLE_FOR_CAPITAL)
    assert len(version.history) == 5
    with pytest.raises(LiveTradingNotAuthorised):
        version.transition(StrategyState.LIVE, citation="flawless", now=NOW)


def test_live_authorisation_cannot_be_built_from_a_non_live_settings() -> None:
    from tradeit.config import Settings
    from tradeit.strategy.lifecycle import LiveAuthorisation

    with pytest.raises(LiveTradingNotAuthorised):
        LiveAuthorisation(Settings())


def test_illegal_transition_reports_both_states() -> None:
    with pytest.raises(IllegalTransition) as raised:
        _version().transition(StrategyState.LIVE, citation="x", now=NOW)
    assert raised.value.frm is StrategyState.DRAFT
    assert raised.value.to is StrategyState.LIVE
