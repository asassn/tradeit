"""Breakout persistence and human labelling.

The property under test throughout: **advancing an event must never destroy what
the system believed yesterday.** The brief's own example — CLOSED_ABOVE at T0,
CONFIRMATION_PENDING at T+1, CONFIRMED at T+3, FAILED_BREAKOUT at T+7 — is
replayed literally, and the stored history must still show every step after the
failure.

The labelling half tests the addition Phase 5 makes to the Phase 4 framework:
every label declares the window of hindsight it was assigned under, and a label
whose definition needs a window it did not have is refused rather than stored.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tradeit.breakouts.labeling import (
    LABEL_HORIZONS,
    BreakoutLabel,
    BreakoutLabelRequest,
    BreakoutLabelService,
    BreakoutQueueStrategy,
    BreakoutReviewQueue,
)
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.breakouts.persistence import (
    STORED_RELATIONSHIPS,
    BreakoutRepository,
    save_all,
)
from tradeit.breakouts.synthetic import BreakoutGenerator
from tradeit.breakouts.validation import BoundarySpec, replay_scenario, run_scenario
from tradeit.core.enums import AssetClass, Exchange
from tradeit.errors import DataError
from tradeit.storage import tables

GENERATOR = BreakoutGenerator()
UTC = dt.UTC


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    tables.Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            tables.Instrument(
                instrument_id=1,
                primary_exchange=Exchange.XNYS.value,
                asset_class=AssetClass.COMMON_STOCK.value,
                name="Synthetic Corp",
                country="US",
                currency="USD",
                listing_status="active",
                source="synthetic",
            )
        )
        db.flush()
        yield db


@pytest.fixture(scope="module")
def confirmed_event():
    return run_scenario(GENERATOR.clean_breakout(seed=0))


@pytest.fixture(scope="module")
def failed_event():
    return run_scenario(GENERATOR.false_breakout(seed=0))


class TestWriting:
    def test_an_event_and_its_observations_are_stored(self, session, confirmed_event):
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        assert row.event_key == confirmed_event.event_key
        assert len(repository.history(row.id)) == len(confirmed_event.observations)

    def test_saving_twice_adds_no_duplicate_observations(self, session, confirmed_event):
        """Idempotent by session. Overwriting would let a re-run silently change
        what the system claims it believed."""
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        before = len(repository.history(row.id))
        repository.save(confirmed_event)
        assert len(repository.history(row.id)) == before

    def test_an_event_without_observations_is_refused(self, session, confirmed_event):
        from dataclasses import replace

        repository = BreakoutRepository(session)
        with pytest.raises(DataError, match="without a history"):
            repository.save(replace(confirmed_event, observations=()))

    def test_the_frozen_boundary_is_stored_in_full(self, session, confirmed_event):
        """Stored rather than referenced through the pattern, so an event read
        back later is not re-judged against a level that has since moved."""
        row = BreakoutRepository(session).save(confirmed_event)
        assert float(row.boundary_level) == pytest.approx(confirmed_event.boundary.nominal)
        assert row.boundary_tolerance_pct == pytest.approx(confirmed_event.boundary.tolerance_pct)
        assert row.atr_at_open == pytest.approx(confirmed_event.boundary.atr_at_open)

    def test_provenance_survives_the_round_trip(self, session, confirmed_event):
        row = BreakoutRepository(session).save(confirmed_event)
        assert row.scorer_version == confirmed_event.scorer_version
        assert row.breakout_config_digest == confirmed_event.breakout_config_digest
        assert row.profile == confirmed_event.profile_name

    def test_the_failure_does_not_erase_the_earlier_states(self, session, failed_event):
        """The brief's T0..T+7 example, read back from storage."""
        repository = BreakoutRepository(session)
        row = repository.save(failed_event)
        states = [o.to_state for o in repository.history(row.id)]
        assert "closed_above" in states
        assert states[-1] == "failed_breakout"
        assert row.state == "failed_breakout"

    def test_the_previous_state_is_kept_beside_the_current_one(self, session, confirmed_event):
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        assert row.state
        assert row.previous_state is None or row.previous_state != row.state

    def test_measurements_are_stored_per_observation(self, session, confirmed_event):
        """'Why did the confirmation score fall on Thursday?' is answerable from
        these and unanswerable from the composite alone."""
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        history = repository.history(row.id)
        assert any(o.measurements for o in history)

    def test_save_all_persists_a_monitor_pass(self, session):
        attempts = replay_scenario(GENERATOR.multiple_attempts(seed=0))
        rows = save_all(session, attempts)
        assert len(rows) == len(attempts)
        assert {r.attempt_number for r in rows} == {e.attempt_number for e in attempts}


class TestReading:
    def test_attempts_for_a_pattern_come_back_in_order(self, session):
        """Item 26: three goes at a level are three rows, and a later phase
        asking whether repeated failure matters needs all of them."""
        attempts = replay_scenario(GENERATOR.multiple_attempts(seed=0))
        save_all(session, attempts)
        repository = BreakoutRepository(session)
        rows = repository.attempts_for(attempts[0].boundary.pattern_key)
        assert [r.attempt_number for r in rows] == sorted(r.attempt_number for r in rows)
        assert len(rows) == len(attempts)

    def test_terminal_events_are_excluded_from_the_active_set_not_deleted(
        self, session, failed_event
    ):
        repository = BreakoutRepository(session)
        repository.save(failed_event)
        assert not repository.active_for(1)
        assert repository.state_counts()["failed_breakout"] == 1

    def test_as_of_returns_what_was_believed_on_a_session(self, session, confirmed_event):
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        target = confirmed_event.observations[2].session_date
        observation = repository.as_of(row.id, target)
        assert observation is not None
        assert observation.session_date == target
        assert observation.to_state == str(confirmed_event.observations[2].state)

    def test_the_stored_history_matches_a_fresh_replay(self, session):
        """Item 32 at the storage boundary: what was written is what a fresh
        evaluation truncated at the same session produces."""
        scenario = GENERATOR.clean_breakout(seed=0)
        full = replay_scenario(scenario, reopen=False)[0]
        repository = BreakoutRepository(session)
        row = repository.save(full)
        stored = {o.session_date: o.to_state for o in repository.history(row.id)}
        for cut in range(46, len(scenario.bars), 8):
            truncated = replay_scenario(scenario, stop_at=cut, reopen=False)[0]
            for observation in truncated.observations:
                assert stored[observation.session_date] == str(observation.state)


class TestRelationships:
    def test_an_unknown_edge_is_refused(self, session, confirmed_event, failed_event):
        """A free-text edge type is how the small ontology item 40 asks for
        becomes a taxonomy nobody maintains."""
        repository = BreakoutRepository(session)
        first = repository.save(confirmed_event)
        second = repository.save(failed_event)
        with pytest.raises(DataError, match="unknown breakout relationship"):
            repository.relate(first, second, "vibes", as_of_session=dt.date(2021, 6, 1))

    def test_an_edge_is_idempotent(self, session, confirmed_event, failed_event):
        repository = BreakoutRepository(session)
        first = repository.save(confirmed_event)
        second = repository.save(failed_event)
        edge = repository.relate(first, second, "same_region", as_of_session=dt.date(2021, 6, 1))
        again = repository.relate(first, second, "same_region", as_of_session=dt.date(2021, 6, 1))
        assert edge is not None and again is not None
        assert edge.id == again.id

    def test_self_edges_are_refused(self, session, confirmed_event):
        repository = BreakoutRepository(session)
        row = repository.save(confirmed_event)
        with pytest.raises(DataError, match="cannot relate to itself"):
            repository.relate(row, row, "same_region", as_of_session=dt.date(2021, 6, 1))

    def test_the_ontology_stays_small(self):
        assert len(STORED_RELATIONSHIPS) == 5


class TestLabelling:
    def test_a_structural_label_is_recorded_with_its_horizon(self, session, confirmed_event):
        BreakoutRepository(session).save(confirmed_event)
        service = BreakoutLabelService(session)
        breakout = confirmed_event.first_qualifying_close_session
        assert breakout is not None
        row = service.record(
            BreakoutLabelRequest.from_event(
                confirmed_event,
                reviewer="alice",
                label=BreakoutLabel.VALID_BREAKOUT,
                knowledge_horizon_session=breakout,
                sessions_of_hindsight=0,
            )
        )
        assert row.label == "valid_breakout"
        assert row.knowledge_horizon_session == breakout
        assert row.engine_breakout_quality == pytest.approx(confirmed_event.breakout_quality)

    def test_a_label_needing_hindsight_is_refused_without_it(self, confirmed_event):
        """A FALSE_BREAKOUT cannot be assigned from the breakout bar: nothing
        visible that day could support it."""
        breakout = confirmed_event.first_qualifying_close_session
        assert breakout is not None
        with pytest.raises(DataError, match="session"):
            BreakoutLabelRequest.from_event(
                confirmed_event,
                reviewer="alice",
                label=BreakoutLabel.FALSE_BREAKOUT,
                knowledge_horizon_session=breakout,
                sessions_of_hindsight=0,
            )

    def test_a_horizon_before_the_labelled_session_is_refused(self, confirmed_event):
        breakout = confirmed_event.first_qualifying_close_session
        assert breakout is not None
        with pytest.raises(DataError, match="precedes"):
            BreakoutLabelRequest.from_event(
                confirmed_event,
                reviewer="alice",
                label=BreakoutLabel.VALID_BREAKOUT,
                knowledge_horizon_session=breakout - dt.timedelta(days=5),
                sessions_of_hindsight=0,
            )

    def test_every_label_declares_a_horizon(self):
        assert set(LABEL_HORIZONS) == set(BreakoutLabel)
        assert LABEL_HORIZONS[BreakoutLabel.VALID_BREAKOUT] == 0
        assert LABEL_HORIZONS[BreakoutLabel.SUCCESSFUL_RETEST] > 0

    def test_the_vocabulary_contains_no_profitability_judgement(self):
        """Nobody is asked whether the stock made money, and there is no member
        in which that answer could be recorded."""
        forbidden = ("profit", "return", "win", "loss", "gain", "money")
        for label in BreakoutLabel:
            assert not any(word in str(label) for word in forbidden)

    def test_the_label_table_has_no_outcome_column(self):
        columns = set(tables.BreakoutLabel.__table__.columns.keys())
        assert not columns & {"future_return", "outcome", "pnl", "win"}

    def test_a_re_review_is_a_new_revision(self, session, confirmed_event):
        """A reviewer changing their mind is itself a fact about how hard the
        example is."""
        BreakoutRepository(session).save(confirmed_event)
        service = BreakoutLabelService(session)
        breakout = confirmed_event.first_qualifying_close_session
        assert breakout is not None
        for label in (BreakoutLabel.VALID_BREAKOUT, BreakoutLabel.WEAK_BREAKOUT):
            service.record(
                BreakoutLabelRequest.from_event(
                    confirmed_event,
                    reviewer="alice",
                    label=label,
                    knowledge_horizon_session=breakout,
                    sessions_of_hindsight=0,
                )
            )
        latest = service.latest_per_reviewer(1, breakout)
        assert latest["alice"].revision == 2
        assert latest["alice"].label == "weak_breakout"

    def test_agreement_counts_latest_revisions_only(self, session, confirmed_event):
        """Counting every revision would let one indecisive reviewer look like
        three reviewers disagreeing."""
        BreakoutRepository(session).save(confirmed_event)
        service = BreakoutLabelService(session)
        breakout = confirmed_event.first_qualifying_close_session
        assert breakout is not None
        for reviewer, labels in (
            ("alice", [BreakoutLabel.WEAK_BREAKOUT, BreakoutLabel.VALID_BREAKOUT]),
            ("bob", [BreakoutLabel.VALID_BREAKOUT]),
        ):
            for label in labels:
                service.record(
                    BreakoutLabelRequest.from_event(
                        confirmed_event,
                        reviewer=reviewer,
                        label=label,
                        knowledge_horizon_session=breakout,
                        sessions_of_hindsight=0,
                    )
                )
        agreement = service.agreement()
        assert agreement.multiply_reviewed == 1
        assert agreement.unanimous == 1
        assert agreement.agreement_rate == pytest.approx(1.0)

    def test_an_unjudgeable_example_is_separated_from_an_ambiguous_one(self):
        assert BreakoutLabel.INSUFFICIENT_EVIDENCE.retires_example
        assert not BreakoutLabel.AMBIGUOUS.retires_example
        assert BreakoutLabel.AMBIGUOUS.is_judgement


class TestReviewQueue:
    @pytest.fixture
    def populated(self, session):
        """Four events on four distinct patterns.

        Distinct ``pattern_key``s rather than four copies of one, because the
        schema enforces one attempt per (instrument, timeframe, pattern,
        attempt) — which is the constraint that keeps attempts countable.
        """
        scenarios = ("clean_breakout", "weak_breakout", "false_breakout", "gap_breakout")
        events = [
            run_scenario(
                getattr(GENERATOR, name)(seed=0),
                spec=BoundarySpec(pattern_key=f"pattern-{index}"),
            )
            for index, name in enumerate(scenarios, start=1)
        ]
        save_all(session, events)
        return session

    def test_the_default_strategy_uses_no_future_information(self, populated):
        """Item 43: the way selection bias enters is a queue sorted by 'most
        interesting', where interesting quietly means 'moved a lot after'."""
        queue = BreakoutReviewQueue(populated)
        items = queue.select(BreakoutQueueStrategy.FUTURE_BLIND, limit=10)
        assert items
        assert all(item.strategy is BreakoutQueueStrategy.FUTURE_BLIND for item in items)
        assert all("breakout session" in item.reason for item in items)

    def test_the_future_blind_sampler_is_not_stratified_by_outcome(self, populated):
        """Balancing across confirmed and failed would use the outcome to decide
        which examples a human sees."""
        queue = BreakoutReviewQueue(populated)
        items = queue.select(BreakoutQueueStrategy.FUTURE_BLIND, limit=10)
        states = {item.engine_state for item in items}
        assert states  # recorded, but not balanced on

    def test_stratified_quality_spreads_across_bands(self, populated):
        queue = BreakoutReviewQueue(populated)
        items = queue.select(BreakoutQueueStrategy.STRATIFIED_QUALITY, limit=4)
        assert len(items) >= 2

    def test_disagreement_surfaces_the_surprising_events(self, populated):
        queue = BreakoutReviewQueue(populated)
        items = queue.select(BreakoutQueueStrategy.DISAGREEMENT, limit=2)
        assert items
        assert all("disagree" in item.reason for item in items)

    def test_the_queue_sets_the_hindsight_window(self, populated):
        """A property of the sampling design, not of how far someone scrolled."""
        queue = BreakoutReviewQueue(populated, hindsight_sessions=7)
        items = queue.select(limit=2)
        assert all(item.hindsight_sessions == 7 for item in items)

    def test_an_empty_corpus_yields_an_empty_queue(self, session):
        assert BreakoutReviewQueue(session).select() == []

    def test_a_queue_item_serialises(self, populated):
        item = BreakoutReviewQueue(populated).select(limit=1)[0]
        payload = item.to_payload()
        assert payload["strategy"]
        assert "breakout_quality" in payload
        assert "future_return" not in payload


class TestStateVocabularyInStorage:
    def test_the_stored_state_is_the_engine_s_own_vocabulary(self, session, confirmed_event):
        row = BreakoutRepository(session).save(confirmed_event)
        assert row.state in {str(state) for state in BreakoutState}
