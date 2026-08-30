"""Pattern relationships, and the human-labelling infrastructure.

Two subjects in one file because they share a concern the rest of the system
does not: **both record claims made by somebody at a moment**, and both are
worthless if that record can be rewritten. A relationship derived on Friday did
not exist on Monday. A reviewer's opinion is evidence, and evidence that gets
overwritten when they change their mind is not evidence.

So the tests here are mostly about *what cannot be lost*: edges carry the
boundary they were derived under, labels accumulate revisions rather than
replacing them, and neither can be produced from information the moment did not
have.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tradeit.core.enums import AssetClass, Bartimeframe, Exchange, PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.detectors import BullFlagDetector, FlatBaseDetector
from tradeit.patterns.labeling import (
    HumanLabel,
    LabelRequest,
    LabelService,
    QueueStrategy,
    ReviewQueue,
)
from tradeit.patterns.persistence import PatternRepository
from tradeit.patterns.relationships import (
    RelationshipKind,
    classify,
    derive_relationships,
    derived_from,
    span_overlap,
    superseded_by,
)
from tradeit.patterns.scanner import PatternScanner
from tradeit.patterns.series import causal_series
from tradeit.patterns.synthetic import PatternGenerator
from tradeit.patterns.tracking import PatternTracker
from tradeit.storage import tables

GENERATOR = PatternGenerator()


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
def instances():
    """A daily scan that produces several overlapping readings."""
    series = GENERATOR.flat_base(sessions=60, breakout_sessions=10)
    scanner = PatternScanner()
    result = scanner.scan(1, Bartimeframe.D1, series.bars, series.last_session, track=False)
    return result.instances


class TestTaxonomy:
    def test_the_vocabulary_is_six_edges(self):
        """Small on purpose. Every extra edge is an argument waiting to happen
        at the point of use, and an edge nobody can classify confidently is an
        edge nobody should trust."""
        assert len(list(RelationshipKind)) == 6

    def test_directional_edges_pair_up_and_symmetric_ones_do_not(self):
        assert RelationshipKind.CONTAINS.inverse is RelationshipKind.NESTED_IN
        assert RelationshipKind.NESTED_IN.inverse is RelationshipKind.CONTAINS
        assert not RelationshipKind.CONTAINS.is_symmetric
        assert RelationshipKind.OVERLAPS.is_symmetric
        assert RelationshipKind.RELATED_TO.is_symmetric

    def test_overlap_is_reported_from_both_sides(self):
        """The asymmetry is the information.

        (1.0, 0.2) is a small structure wholly inside a large one; (0.6, 0.6) is
        two structures of similar size sharing half their bars. One number
        cannot distinguish them, so two are returned.
        """
        series = GENERATOR.flat_base(sessions=60)
        detector = FlatBaseDetector()
        found = detector.detect(series.bars, series.last_session)
        flag = BullFlagDetector().detect(series.bars, series.last_session)
        if not found or not flag:
            pytest.skip("this series did not produce two families to compare")
        first, second = span_overlap(found[0], flag[0])
        assert 0.0 <= first <= 1.0
        assert 0.0 <= second <= 1.0


class TestDerivation:
    def test_two_readings_of_one_geometry_are_related_not_nested(self, instances):
        """The distinction the competing-pattern work exists to preserve.

        A window that is a flat base and a bull flag is one piece of chart with
        two names. Calling that containment would say one structure holds the
        other, which is a different and wrong claim.
        """
        relations = derive_relationships(instances, instances[0].as_of_session)
        related = [r for r in relations if r.kind is RelationshipKind.RELATED_TO]
        assert related
        for relation in related:
            assert relation.overlap > 0.8

    def test_edges_carry_the_boundary_they_were_derived_under(self, instances):
        as_of = instances[0].as_of_session
        for relation in derive_relationships(instances, as_of):
            assert relation.as_of == as_of

    def test_an_edge_cannot_be_derived_from_a_future_instance(self, instances):
        """The retroactive-rewriting guard.

        An edge derived under a boundary the instances postdate would be an edge
        backdated to a session that could not have produced it.
        """
        as_of = instances[0].as_of_session - dt.timedelta(days=30)
        with pytest.raises(ConfigError, match="past the"):
            derive_relationships(instances, as_of)

    def test_derivation_is_reproducible(self, instances):
        as_of = instances[0].as_of_session
        first = [r.to_payload() for r in derive_relationships(instances, as_of)]
        second = [r.to_payload() for r in derive_relationships(instances, as_of)]
        assert first == second

    def test_one_direction_is_emitted_and_the_inverse_is_derivable(self, instances):
        """Storing both would create two rows that can disagree after an edit."""
        as_of = instances[0].as_of_session
        relations = derive_relationships(instances, as_of)
        pairs = {(r.from_key, r.to_key) for r in relations}
        for relation in relations:
            assert (relation.to_key, relation.from_key) not in pairs
            inverse = relation.inverted()
            assert inverse.from_key == relation.to_key
            assert inverse.kind is relation.kind.inverse

    def test_instances_on_different_instruments_never_relate(self, instances):
        from dataclasses import replace

        moved = replace(instances[0], instrument_id=99)
        assert classify(instances[0], moved) is None


class TestCrossTimeframeRelationships:
    def test_a_weekly_structure_contains_a_daily_one(self):
        """The canonical multi-timeframe case, and the reason containment is
        directional: coarser contains finer, never the reverse."""
        # Long enough that the weekly series clears the detectors' warm-up: a
        # weekly bull flag needs 43 weekly bars, which is 215 daily sessions
        # before anything can be found at all.
        series = GENERATOR.flat_base(sessions=150, breakout_sessions=10)
        scanner = PatternScanner()
        built = causal_series(series.bars, [Bartimeframe.D1, Bartimeframe.W1], series.last_session)
        result = scanner.scan_timeframes(1, built, series.last_session, track=False)

        weekly = result.by_timeframe[Bartimeframe.W1].instances
        daily = result.by_timeframe[Bartimeframe.D1].instances
        assert weekly and daily

        kind = classify(weekly[0], daily[0])
        assert kind in {RelationshipKind.CONTAINS, RelationshipKind.NESTED_IN}
        assert any(
            r.kind in {RelationshipKind.CONTAINS, RelationshipKind.NESTED_IN}
            for r in result.cross_relations
        )

    def test_cross_timeframe_edges_are_reported_separately(self):
        """A weekly structure containing a daily one is a fact about two scans,
        and no single-timeframe scan can see it."""
        series = GENERATOR.flat_base(sessions=150, breakout_sessions=10)
        scanner = PatternScanner()
        built = causal_series(series.bars, [Bartimeframe.D1, Bartimeframe.W1], series.last_session)
        result = scanner.scan_timeframes(1, built, series.last_session, track=False)
        assert result.cross_relations
        within = {
            (r.from_key, r.to_key) for scan in result.by_timeframe.values() for r in scan.relations
        }
        for relation in result.cross_relations:
            assert (relation.from_key, relation.to_key) not in within


class TestExplicitEdges:
    def test_derivation_edges_are_declared_rather_than_inferred(self, instances):
        """Only the detector that built a composite knows which structures
        justified it; geometry cannot reconstruct that."""
        composite, *parts = instances
        edges = derived_from(composite, parts[:2], composite.as_of_session)
        assert all(e.kind is RelationshipKind.DERIVED_FROM for e in edges)
        assert {e.to_key for e in edges} == {p.identity_key for p in parts[:2]}

    def test_supersession_is_a_lifecycle_event_not_a_geometric_one(self):
        edge = superseded_by("aaa", "bbb", dt.date(2024, 1, 5))
        assert edge.kind is RelationshipKind.SUPERSEDED_BY
        with pytest.raises(ConfigError, match="cannot supersede itself"):
            superseded_by("aaa", "aaa", dt.date(2024, 1, 5))

    def test_geometry_never_infers_supersession_or_derivation(self, instances):
        as_of = instances[0].as_of_session
        kinds = {r.kind for r in derive_relationships(instances, as_of)}
        assert RelationshipKind.SUPERSEDED_BY not in kinds
        assert RelationshipKind.DERIVED_FROM not in kinds


class TestRelationshipPersistence:
    def test_an_edge_stores_its_boundary(self, session):
        repository = PatternRepository(session)
        tracker = _replay()
        rows = [repository.save(t) for t in tracker.open_patterns() + tracker.closed_patterns()]
        assert len(rows) >= 2

        edge = repository.relate(
            rows[0],
            rows[1],
            str(RelationshipKind.RELATED_TO),
            as_of_session=dt.date(2023, 5, 1),
            note="two readings of one window",
        )
        assert edge.as_of_session == dt.date(2023, 5, 1)

    def test_an_unknown_relationship_is_refused(self, session):
        repository = PatternRepository(session)
        tracker = _replay()
        rows = [repository.save(t) for t in tracker.open_patterns() + tracker.closed_patterns()]
        assert len(rows) >= 2
        with pytest.raises(Exception, match="unknown pattern relationship"):
            repository.relate(rows[0], rows[1], "causes")


class TestLabelVocabulary:
    def test_five_labels_and_the_two_that_look_redundant_are_not(self):
        """ABSTAIN and INSUFFICIENT_EVIDENCE differ in what happens next.

        An abstention is reassigned -- the example is fine, this reviewer is not
        the one to judge it. An unjudgeable example is retired -- no reviewer can
        judge it, and leaving it in the queue wastes everyone's time. Merging
        them would mean either re-reviewing dead examples forever or silently
        discarding examples a second reviewer could have handled.
        """
        assert len(list(HumanLabel)) == 5
        assert HumanLabel.ABSTAIN.needs_reassignment
        assert not HumanLabel.ABSTAIN.retires_example
        assert HumanLabel.INSUFFICIENT_EVIDENCE.retires_example
        assert not HumanLabel.INSUFFICIENT_EVIDENCE.needs_reassignment

    def test_only_judgements_count_toward_agreement(self):
        judgements = {label for label in HumanLabel if label.is_judgement}
        assert judgements == {
            HumanLabel.POSITIVE,
            HumanLabel.NEGATIVE,
            HumanLabel.AMBIGUOUS,
        }

    def test_a_quality_rating_cannot_accompany_a_negative(self):
        """ "This is not a cup, quality 70" has no reading."""
        with pytest.raises(ConfigError, match="no reading"):
            LabelRequest(
                instrument_id=1,
                as_of_session=dt.date(2024, 1, 5),
                timeframe=Bartimeframe.D1,
                pattern_type=PatternType.CUP_WITH_HANDLE,
                reviewer="alex",
                label=HumanLabel.NEGATIVE,
                human_quality=70.0,
            )

    def test_an_anonymous_opinion_is_refused(self):
        with pytest.raises(ConfigError, match="not evidence"):
            LabelRequest(
                instrument_id=1,
                as_of_session=dt.date(2024, 1, 5),
                timeframe=Bartimeframe.D1,
                pattern_type=PatternType.CUP_WITH_HANDLE,
                reviewer="   ",
                label=HumanLabel.POSITIVE,
            )


class TestLabelService:
    def test_a_re_review_is_a_new_revision_rather_than_an_overwrite(self, session):
        """A reviewer changing their mind is itself a fact about how hard the
        example is, and the earlier opinion is what makes it visible."""
        service = LabelService(session)
        base = dict(
            instrument_id=1,
            as_of_session=dt.date(2024, 1, 5),
            timeframe=Bartimeframe.D1,
            pattern_type=PatternType.CUP_WITH_HANDLE,
            reviewer="alex",
        )
        service.record(LabelRequest(**base, label=HumanLabel.POSITIVE, human_quality=80.0))
        service.record(LabelRequest(**base, label=HumanLabel.AMBIGUOUS, human_quality=55.0))

        rows = service.for_example(
            1, dt.date(2024, 1, 5), Bartimeframe.D1, PatternType.CUP_WITH_HANDLE
        )
        assert [r.revision for r in rows] == [1, 2]
        assert [r.label for r in rows] == ["positive", "ambiguous"]

        latest = service.latest_per_reviewer(
            1, dt.date(2024, 1, 5), Bartimeframe.D1, PatternType.CUP_WITH_HANDLE
        )
        assert latest["alex"].revision == 2

    def test_the_detectors_prediction_is_pinned_at_labelling_time(self, session):
        """Agreement computed against whatever the detector does today is
        agreement with a different detector."""
        series = GENERATOR.cup_handle()
        instance = _cup(series)
        service = LabelService(session)
        row = service.record(
            LabelRequest.from_instance(
                instance,
                reviewer="alex",
                label=HumanLabel.POSITIVE,
                human_quality=90.0,
                config_digest="abc123",
            )
        )
        assert row.detector_name == "cup_handle"
        assert row.detector_version == instance.detector_version
        assert row.detector_quality == pytest.approx(instance.quality)
        assert row.detector_coverage == pytest.approx(instance.evidence_coverage)
        assert row.config_digest == "abc123"

    def test_multiple_reviewers_are_the_point_rather_than_an_edge_case(self, session):
        service = LabelService(session)
        base = dict(
            instrument_id=1,
            as_of_session=dt.date(2024, 1, 5),
            timeframe=Bartimeframe.D1,
            pattern_type=PatternType.CUP_WITH_HANDLE,
        )
        service.record(LabelRequest(**base, reviewer="alex", label=HumanLabel.POSITIVE))
        service.record(LabelRequest(**base, reviewer="sam", label=HumanLabel.NEGATIVE))
        service.record(LabelRequest(**base, reviewer="kim", label=HumanLabel.ABSTAIN))

        agreement = service.agreement()
        assert agreement.judged == 1
        assert agreement.unanimous == 0
        assert agreement.contested

    def test_an_abstention_is_not_a_disagreement(self, session):
        service = LabelService(session)
        base = dict(
            instrument_id=1,
            as_of_session=dt.date(2024, 2, 1),
            timeframe=Bartimeframe.D1,
            pattern_type=PatternType.BULL_FLAG,
        )
        service.record(LabelRequest(**base, reviewer="alex", label=HumanLabel.POSITIVE))
        service.record(LabelRequest(**base, reviewer="sam", label=HumanLabel.POSITIVE))
        service.record(LabelRequest(**base, reviewer="kim", label=HumanLabel.ABSTAIN))

        agreement = service.agreement(PatternType.BULL_FLAG)
        assert agreement.unanimity == 1.0


class TestReviewQueue:
    def test_every_strategy_returns_items_with_a_stated_reason(self, session):
        repository = PatternRepository(session)
        tracker = _replay()
        for tracked in tracker.open_patterns() + tracker.closed_patterns():
            repository.save(tracked)
        session.flush()

        queue = ReviewQueue(session)
        for strategy in QueueStrategy:
            items = queue.select(strategy, limit=5)
            for item in items:
                assert item.reason
                assert item.strategy is strategy

    def test_random_selection_is_reproducible(self, session):
        """A labelling run that cannot be reproduced cannot be audited for
        selection bias, which is the one thing it most needs to be audited for."""
        repository = PatternRepository(session)
        tracker = _replay()
        for tracked in tracker.open_patterns() + tracker.closed_patterns():
            repository.save(tracked)
        session.flush()

        queue = ReviewQueue(session)
        first = queue.select(QueueStrategy.RANDOM, limit=4, seed=7)
        second = queue.select(QueueStrategy.RANDOM, limit=4, seed=7)
        assert [i.pattern_id for i in first] == [i.pattern_id for i in second]

    def test_terminal_structures_are_selectable(self, session):
        """Under-sampled by every other strategy, and the population a
        false-positive rate has to be computed from."""
        repository = PatternRepository(session)
        tracker = _replay()
        for tracked in tracker.open_patterns() + tracker.closed_patterns():
            repository.save(tracked)
        session.flush()

        items = ReviewQueue(session).select(QueueStrategy.TERMINAL, limit=10)
        assert all("terminal" in i.reason for i in items)

    def test_the_random_strategy_exists_and_is_never_the_only_one_omitted(self):
        """The only strategy that yields an unbiased estimate of anything.

        A queue offering nothing but detector candidates produces a corpus that
        can measure precision and nothing else.
        """
        assert QueueStrategy.RANDOM in set(QueueStrategy)
        assert len(list(QueueStrategy)) >= 9


def _replay() -> PatternTracker:
    """Replay a scan day by day so several distinct identities accumulate.

    Uses the full scanner rather than one detector: the queue and relationship
    tests need more than one family present, and a single-family replay
    produced one pattern and made the tests skip.
    """
    series = GENERATOR.flat_base(sessions=60, breakout_sessions=10)
    scanner = PatternScanner()
    for index in range(len(series.bars) - 15, len(series.bars)):
        bars = series.bars[: index + 1]
        day = bars[-1].session_date
        scanner.scan(1, Bartimeframe.D1, bars, day)
    return scanner.tracker


def _cup(series):
    from tradeit.patterns.detectors import CupHandleDetector

    found = CupHandleDetector().detect(series.bars, series.last_session)
    assert found
    return found[0]
