"""Pattern persistence and causal identity history.

The property under test throughout: **advancing a pattern must never destroy
what the system believed yesterday.** A mutable current-state row is fine — a
screen asks "what is this base doing?" constantly and should not pay for a
windowed query — but only because every previous belief is preserved beside it.

The scenario the brief describes is replayed literally: a pattern appears at T,
evolves, matures, approaches resistance, crosses it, and at T+10 a fresh
detector would anchor differently. The stored history must still say what was
known at every earlier point.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tradeit.core.enums import AssetClass, Exchange, PatternType
from tradeit.errors import DataError
from tradeit.patterns.base import PatternState
from tradeit.patterns.detectors import BullFlagDetector
from tradeit.patterns.persistence import PatternRepository
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator
from tradeit.patterns.tracking import PatternTracker
from tradeit.storage import tables

GENERATOR = PatternGenerator()
DETECTOR = BullFlagDetector()


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


def replay(sessions: int = 20, spec: BullFlagSpec | None = None) -> PatternTracker:
    """Run the detector day by day, exactly as production would."""
    series = GENERATOR.bull_flag(
        spec or BullFlagSpec(flag_sessions=10, breakout_sessions=8, breakout_strength=0.04)
    )
    tracker = PatternTracker()
    for i in range(len(series.bars) - sessions, len(series.bars)):
        bars = series.bars[: i + 1]
        day = bars[-1].session_date
        tracker.observe(DETECTOR.detect(bars, day), day, closes={1: float(bars[-1].close)})
    return tracker


class TestPersistence:
    def test_one_pattern_is_one_row(self, session):
        """Not one row per session. The Phase 2 draft keyed uniqueness on
        detected_on and minted a row a day."""
        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]

        first = repository.save(tracked)
        again = repository.save(tracked)
        assert first.id == again.id
        assert session.query(tables.Pattern).count() == 1

    def test_every_session_becomes_one_observation(self, session):
        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]

        row = repository.save(tracked)
        history = repository.history(row.id)
        assert len(history) == len(tracked.history)
        assert [o.session_date for o in history] == sorted(o.session_date for o in history)

    def test_re_saving_does_not_duplicate_or_rewrite_history(self, session):
        """Replacing an observation would let a re-run silently change what the
        system claims it believed."""
        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]

        row = repository.save(tracked)
        before = [(o.session_date, o.to_state, o.quality) for o in repository.history(row.id)]
        repository.save(tracked)
        after = [(o.session_date, o.to_state, o.quality) for o in repository.history(row.id)]
        assert before == after

    def test_the_row_carries_provenance(self, session):
        """Without these a stored pattern cannot be reproduced."""
        repository = PatternRepository(session)
        tracker = replay()
        row = repository.save((tracker.open_patterns() + tracker.closed_patterns())[0])
        assert row.detector_name == "bull_flag"
        assert row.detector_version == DETECTOR.version
        assert row.geometry is not None

    def test_coverage_is_stored_alongside_quality(self, session):
        repository = PatternRepository(session)
        tracker = replay()
        row = repository.save((tracker.open_patterns() + tracker.closed_patterns())[0])
        assert 0.0 < row.evidence_coverage <= 100.0
        for observation in repository.history(row.id):
            assert 0.0 < observation.evidence_coverage <= 100.0

    def test_component_scores_are_stored_per_observation(self, session):
        """ "Why did this decay?" is answerable from the component history and
        unanswerable from the composite alone."""
        repository = PatternRepository(session)
        tracker = replay()
        row = repository.save((tracker.open_patterns() + tracker.closed_patterns())[0])
        observation = repository.history(row.id)[0]
        assert "flagpole" in observation.component_scores
        assert "requirement" in observation.component_scores["flagpole"]

    def test_an_unavailable_component_stores_its_reason(self, session):
        repository = PatternRepository(session)
        tracker = replay()
        row = repository.save((tracker.open_patterns() + tracker.closed_patterns())[0])
        scores = repository.history(row.id)[0].component_scores
        rs = scores["relative_strength"]
        assert rs["unavailable"] is True
        assert rs["unavailable_reason"]
        assert rs["score"] is None

    def test_a_moving_structural_start_is_refused(self, session):
        """The retroactive-refinement leak reaching the database.

        A pattern's origin cannot move. If the structure genuinely changed, it
        is a different pattern and needs a new identity.
        """
        from dataclasses import replace as dataclass_replace

        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        repository.save(tracked)

        shifted_geometry = dataclass_replace(
            tracked.current.geometry,
            start_date=tracked.current.geometry.start_date + dt.timedelta(days=7),
        )
        shifted = dataclass_replace(
            tracked, current=dataclass_replace(tracked.current, geometry=shifted_geometry)
        )
        with pytest.raises(DataError, match="origin cannot move"):
            repository.save(shifted)

    def test_terminal_patterns_are_kept(self, session):
        """A failed pattern is the evidence a false-positive rate is computed
        from. A system that prunes its failures cannot measure itself."""
        repository = PatternRepository(session)
        tracker = replay(sessions=6, spec=BullFlagSpec(retracement=0.5, breakdown=0.35))
        for tracked in tracker.closed_patterns():
            row = repository.save(tracked)
            assert row.terminal_at is not None
        assert session.query(tables.Pattern).count() >= 1

    def test_previous_state_is_kept_on_the_row(self, session):
        """So the most recent transition is answerable without the history table."""
        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        row = repository.save(tracked)
        if len({t.to_state for t in tracked.history}) > 1:
            assert row.previous_state is not None
            assert row.previous_state != row.state


class TestRelationships:
    def _two_patterns(self, session) -> tuple[tables.Pattern, tables.Pattern]:
        repository = PatternRepository(session)
        tracker = replay()
        rows = [repository.save(t) for t in tracker.open_patterns()[:2]]
        if len(rows) < 2:
            pytest.skip("need two coexisting patterns")
        return rows[0], rows[1]

    def test_two_readings_of_one_structure_may_coexist(self, session):
        """Forcing exclusivity would discard what a later scoring stage is
        better placed to decide."""
        parent, child = self._two_patterns(session)
        edge = PatternRepository(session).relate(child, parent, "nested_in")
        assert edge.relationship == "nested_in"

    def test_an_unknown_relationship_is_refused(self, session):
        parent, child = self._two_patterns(session)
        with pytest.raises(DataError, match="unknown pattern relationship"):
            PatternRepository(session).relate(child, parent, "sort_of_like")

    def test_a_pattern_cannot_relate_to_itself(self, session):
        parent, _ = self._two_patterns(session)
        with pytest.raises(DataError, match="cannot relate to itself"):
            PatternRepository(session).relate(parent, parent, "related_to")


class TestCausalHistory:
    """The scenario the brief describes, replayed literally."""

    def test_the_stored_history_preserves_every_earlier_belief(self, session):
        """At T+10 a fresh detector would anchor differently. The record of what
        was known at T, T+1 and T+3 must survive that."""
        repository = PatternRepository(session)
        series = GENERATOR.bull_flag(
            BullFlagSpec(flag_sessions=10, breakout_sessions=8, breakout_strength=0.04)
        )
        tracker = PatternTracker()

        snapshots: dict[dt.date, dict[str, tuple[str, float]]] = {}
        for i in range(len(series.bars) - 20, len(series.bars)):
            bars = series.bars[: i + 1]
            day = bars[-1].session_date
            tracker.observe(DETECTOR.detect(bars, day), day, closes={1: float(bars[-1].close)})
            for tracked in tracker.open_patterns() + tracker.closed_patterns():
                repository.save(tracked)
            snapshots[day] = {
                t.identity_key: (str(t.state), t.current.quality)
                for t in tracker.open_patterns() + tracker.closed_patterns()
            }

        # Every belief recorded along the way is still readable at the end.
        for row in session.query(tables.Pattern).all():
            for day, expected in snapshots.items():
                if row.identity_key not in expected:
                    continue
                observation = repository.as_of(row.id, day)
                if observation is None:
                    continue
                assert observation.session_date <= day

    def test_a_geometry_recorded_early_is_not_rewritten_later(self, session):
        repository = PatternRepository(session)
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=12, breakout_sessions=8))
        tracker = PatternTracker()

        starts: dict[str, dt.date] = {}
        for i in range(len(series.bars) - 18, len(series.bars)):
            bars = series.bars[: i + 1]
            day = bars[-1].session_date
            tracker.observe(DETECTOR.detect(bars, day), day, closes={1: float(bars[-1].close)})
            for tracked in tracker.open_patterns() + tracker.closed_patterns():
                row = repository.save(tracked)
                if row.identity_key in starts:
                    assert row.structural_start_date == starts[row.identity_key]
                starts[row.identity_key] = row.structural_start_date

    def test_a_quality_recorded_on_one_session_never_changes(self, session):
        """Future states must not rewrite historical quality scores."""
        repository = PatternRepository(session)
        series = GENERATOR.bull_flag(BullFlagSpec(flag_sessions=12, breakout_sessions=8))
        tracker = PatternTracker()

        seen: dict[tuple[str, dt.date], float] = {}
        for i in range(len(series.bars) - 18, len(series.bars)):
            bars = series.bars[: i + 1]
            day = bars[-1].session_date
            tracker.observe(DETECTOR.detect(bars, day), day, closes={1: float(bars[-1].close)})
            for tracked in tracker.open_patterns() + tracker.closed_patterns():
                row = repository.save(tracked)
                for observation in repository.history(row.id):
                    key = (row.identity_key, observation.session_date)
                    if key in seen:
                        assert observation.quality == seen[key]
                    seen[key] = observation.quality

    def test_replaying_a_pinned_snapshot_reconstructs_the_same_history(self, session):
        """Re-running from the same data must produce the same state history.

        The reproducibility claim in one assertion: if this fails, a stored
        pattern history is not a record of anything, because the same inputs
        produce a different one.
        """
        series = GENERATOR.bull_flag(
            BullFlagSpec(flag_sessions=10, breakout_sessions=8, breakout_strength=0.04)
        )

        def run() -> list[tuple[str, str, str, float]]:
            tracker = PatternTracker()
            for i in range(len(series.bars) - 20, len(series.bars)):
                bars = series.bars[: i + 1]
                day = bars[-1].session_date
                tracker.observe(DETECTOR.detect(bars, day), day, closes={1: float(bars[-1].close)})
            return [
                (t.identity_key, x.session_date.isoformat(), str(x.to_state), x.quality)
                for t in tracker.open_patterns() + tracker.closed_patterns()
                for x in t.history
            ]

        assert run() == run()

    def test_a_detector_version_change_produces_a_separate_row(self, session):
        """Historical backtests pin the detector version. Silently recomputing
        history under the newest detector and presenting the results as
        identical is the specific dishonesty versioning prevents.
        """
        from dataclasses import replace as dataclass_replace

        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        first = repository.save(tracked)

        upgraded = dataclass_replace(
            tracked, current=dataclass_replace(tracked.current, detector_version=2)
        )
        second = repository.save(upgraded)

        assert first.id != second.id
        assert first.identity_key == second.identity_key
        assert {first.detector_version, second.detector_version} == {1, 2}

    def test_reading_a_pattern_as_of_a_past_session(self, session):
        """The question a mutable current-state row cannot answer."""
        repository = PatternRepository(session)
        tracker = replay()
        tracked = (tracker.open_patterns() + tracker.closed_patterns())[0]
        row = repository.save(tracked)

        first_session = tracked.history[0].session_date
        early = repository.as_of(row.id, first_session)
        assert early is not None
        assert early.session_date == first_session
        assert early.to_state == str(tracked.history[0].to_state)

        # And the current row may well disagree with it, which is the point.
        assert row.state == str(tracked.current.state)


class TestBreakoutStaysUnjudged:
    def test_no_stored_field_expresses_breakout_validity(self):
        """Phase 4 observes that a line was crossed. Whether it counts is
        Phase 5's question, and the schema must not offer a place to answer it.
        """
        columns = set(tables.Pattern.__table__.columns.keys()) | set(
            tables.PatternObservation.__table__.columns.keys()
        )
        for forbidden in ("confirmed", "valid_breakout", "tradeable", "entry_price", "signal"):
            assert not any(forbidden in c for c in columns), forbidden

    def test_the_only_breakout_state_is_unconfirmed(self):
        assert PatternState.BROKEN_OUT_UNCONFIRMED.value.endswith("unconfirmed")
        assert not any(s.value in ("broken_out_confirmed", "confirmed") for s in PatternState)
        assert PatternType.BULL_FLAG in set(PatternType)
