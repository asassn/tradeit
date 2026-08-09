"""The invariant: breakout state is not trade eligibility.

Three properties, and all three are the kind that decay one helpful convenience
method at a time:

* no Phase 5 object exposes BUY/SELL semantics, an eligibility flag, a size or a
  stop — asserted by walking the object graph rather than by reading it;
* the evidence bundle carries every field a downstream stage needs, including
  explicit empty slots for the two Phase 5 cannot fill;
* the production monitor opens events only against detected structural
  boundaries, and a config that tried to widen that is refused at construction.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from tradeit.breakouts import base as breakout_base
from tradeit.breakouts.boundary import BoundaryKind, manual_boundary
from tradeit.breakouts.config import BreakoutEngineConfig, ToleranceConfig
from tradeit.breakouts.eligibility import (
    FORBIDDEN_DECISION_TERMS,
    PRODUCTION_PATTERN_STATES,
    EligibilityError,
    EvidenceBundle,
    check_monitor_eligibility,
)
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.breakouts.monitor import BreakoutMonitor
from tradeit.breakouts.synthetic import BreakoutGenerator
from tradeit.breakouts.validation import BoundarySpec, run_scenario
from tradeit.core.enums import Bartimeframe, MarketRegime, PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import (
    Boundary,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)

GENERATOR = BreakoutGenerator()
UTC = dt.UTC


@pytest.fixture(scope="module")
def confirmed():
    event = run_scenario(GENERATOR.clean_breakout(seed=0))
    assert event.state is BreakoutState.CONFIRMED
    return event


@pytest.fixture(scope="module")
def drifting():
    """A low-volume drift above an arbitrary level.

    The scenario that makes the invariant necessary rather than decorative: it
    reaches CONFIRMED on the synthetic corpus 96% of the time, at roughly half
    the breakout quality of a clean break.
    """
    return run_scenario(GENERATOR.low_volume_drift(seed=0))


def pattern_for(event, *, state: PatternState = PatternState.MATURE) -> PatternInstance:
    session = event.last_session
    start = event.opened_session
    level = event.boundary.nominal
    return PatternInstance(
        instrument_id=event.instrument_id,
        pattern_type=PatternType.FLAT_BASE,
        timeframe=Bartimeframe.D1,
        state=state,
        geometry=PatternGeometry(
            start_date=start,
            end_date=session,
            resistance=Boundary(
                kind="resistance",
                method="swing_highs",
                level=level,
                anchor_date=session,
                touches=(PricePoint(session, level),),
                confidence=70.0,
            ),
        ),
        as_of_session=session,
        knowledge_time=dt.datetime.combine(session, dt.time(22), tzinfo=UTC),
        quality=81.0,
        session_count=40,
        detector_name="flat_base",
        detector_version=2,
    )


class TestNoTradeSemantics:
    def test_no_phase_five_dataclass_field_implies_a_decision(self):
        """Walks every dataclass in the Phase 5 object graph.

        Cheap, and it is the drift that arrives as a helpful convenience: an
        ``is_tradable`` property looks harmless on the object that has all the
        numbers, and is a Phase 8 decision made four stages early.
        """
        suspects = [
            breakout_base.BreakoutEvent,
            breakout_base.BreakoutObservation,
            breakout_base.RetestRecord,
            EvidenceBundle,
        ]
        for cls in suspects:
            for name in [f.name for f in dataclasses.fields(cls)]:
                assert not any(term in name.lower() for term in FORBIDDEN_DECISION_TERMS), (
                    f"{cls.__name__}.{name} carries decision vocabulary"
                )

    def test_no_attribute_or_method_implies_a_decision(self):
        for cls in (breakout_base.BreakoutEvent, EvidenceBundle):
            for name in dir(cls):
                if name.startswith("_"):
                    continue
                assert not any(term in name.lower() for term in FORBIDDEN_DECISION_TERMS), (
                    f"{cls.__name__}.{name} carries decision vocabulary"
                )

    def test_no_state_name_implies_a_decision(self):
        for state in BreakoutState:
            assert not any(term in str(state) for term in FORBIDDEN_DECISION_TERMS)

    def test_the_rendered_bundle_says_what_it_is_not(self, confirmed):
        text = EvidenceBundle.of(confirmed).explain()
        assert "not a recommendation" in text
        assert "does not authorise a trade" in text

    def test_the_bundle_offers_no_aggregate(self):
        """A convenience total would become the decision by default."""
        names = {f.name for f in dataclasses.fields(EvidenceBundle)}
        assert not names & {"total", "score", "rank", "composite", "verdict"}


class TestEvidenceBundle:
    def test_every_required_field_is_present(self, confirmed):
        """The thirteen the gate names, plus the two Phase 5 cannot fill."""
        from tradeit.breakouts.eligibility import REQUIRED_EVIDENCE_FIELDS

        names = {f.name for f in dataclasses.fields(EvidenceBundle)}
        missing = sorted(set(REQUIRED_EVIDENCE_FIELDS) - names)
        assert not missing, missing

    def test_the_unfillable_slots_are_explicit_rather_than_absent(self, confirmed):
        bundle = EvidenceBundle.of(confirmed)
        assert bundle.fundamental_score is None
        assert bundle.portfolio_fit_score is None
        gaps = {gap.field_name for gap in bundle.missing_for_decision()}
        assert {"fundamental_score", "portfolio_fit_score"} <= gaps

    def test_a_gap_names_who_would_supply_it(self, confirmed):
        by_field = {g.field_name: g for g in EvidenceBundle.of(confirmed).missing_for_decision()}
        assert by_field["fundamental_score"].supplied_by == "Phase 6"
        assert by_field["portfolio_fit_score"].supplied_by == "Phase 7"

    def test_supplied_context_closes_its_gap(self, confirmed):
        bundle = EvidenceBundle.of(
            confirmed,
            relative_strength=71.0,
            sector_strength=64.0,
            market_regime=MarketRegime.BULL_TRENDING,
        )
        gaps = {gap.field_name for gap in bundle.missing_for_decision()}
        assert "relative_strength" not in gaps
        assert "market_regime" not in gaps

    def test_a_pattern_for_another_instrument_is_refused(self, confirmed):
        """A bundle describing two instruments would attribute one's structure
        to the other's price action."""
        other = dataclasses.replace(pattern_for(confirmed), instrument_id=99)
        with pytest.raises(ConfigError, match="two instruments"):
            EvidenceBundle.of(confirmed, other)

    def test_an_unattached_event_reports_zero_rather_than_a_plausible_number(self):
        scenario = GENERATOR.clean_breakout(seed=0)
        event = run_scenario(scenario, spec=BoundarySpec.weak())
        bundle = EvidenceBundle.of(event)
        assert bundle.pattern_quality == 0.0
        assert bundle.pattern_evidence_coverage == 0.0
        assert bundle.boundary_kind is BoundaryKind.OTHER
        assert not bundle.is_structurally_attached

    def test_the_pattern_contributes_its_own_numbers(self, confirmed):
        bundle = EvidenceBundle.of(confirmed, pattern_for(confirmed))
        assert bundle.pattern_quality == 81.0
        assert bundle.pattern_state is PatternState.MATURE
        assert bundle.pattern_evidence_coverage >= 0.0

    def test_the_weakest_number_is_a_diagnostic_not_a_decision(self, confirmed):
        name, value = EvidenceBundle.of(confirmed).weakest_score
        assert isinstance(name, str)
        assert 0.0 <= value <= 100.0

    def test_the_payload_lists_what_is_missing(self, confirmed):
        payload = EvidenceBundle.of(confirmed).to_payload()
        assert "fundamental_score" in payload["missing"]
        assert payload["breakout_state"] == str(confirmed.state)

    def test_an_out_of_range_score_is_refused(self, confirmed):
        base = EvidenceBundle.of(confirmed)
        with pytest.raises(ConfigError, match=r"outside \[0, 100\]"):
            dataclasses.replace(base, breakout_quality=140.0)


class TestConfirmedIsNotQuality:
    def test_a_drift_and_a_clean_break_reach_the_same_state(self, confirmed, drifting):
        """The finding that makes the invariant load-bearing.

        Both are CONFIRMED. A consumer reading state alone cannot tell them
        apart, and this test exists so that fact stays visible rather than being
        rediscovered by a downstream stage.
        """
        assert drifting.state is BreakoutState.CONFIRMED
        assert confirmed.state is BreakoutState.CONFIRMED

    def test_the_evidence_separates_them(self, confirmed, drifting):
        good = EvidenceBundle.of(confirmed)
        weak = EvidenceBundle.of(drifting)
        assert good.breakout_quality > weak.breakout_quality + 20

    def test_every_confirmed_event_still_carries_its_full_evidence(self, drifting):
        bundle = EvidenceBundle.of(drifting)
        for value in (
            bundle.breakout_quality,
            bundle.breakout_confirmation_score,
            bundle.breakout_evidence_coverage,
            bundle.breakout_confidence,
            bundle.boundary_confidence,
        ):
            assert 0.0 <= value <= 100.0


class TestBoundaryProvenance:
    def test_a_detected_boundary_is_tagged_structural(self):
        monitor = BreakoutMonitor()
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        session = bars[45].session_date
        instance = PatternInstance(
            instrument_id=1,
            pattern_type=PatternType.FLAT_BASE,
            timeframe=Bartimeframe.D1,
            state=PatternState.MATURE,
            geometry=PatternGeometry(
                start_date=bars[5].session_date,
                end_date=session,
                resistance=Boundary(
                    kind="resistance",
                    method="swing_highs",
                    level=scenario.level,
                    anchor_date=session,
                    touches=(PricePoint(session, scenario.level),),
                    confidence=70.0,
                ),
            ),
            as_of_session=session,
            knowledge_time=dt.datetime.combine(session, dt.time(22), tzinfo=UTC),
            quality=78.0,
            session_count=40,
            detector_name="flat_base",
            detector_version=2,
        )
        live, _ = monitor.monitorable([instance], as_of_session=session, bars=bars[:46])
        assert live
        assert live[0].boundary.kind is BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY
        assert live[0].boundary.is_production_eligible

    def test_a_manual_level_cannot_claim_the_structural_tag(self):
        """The one thing that must not be possible: a research level entering
        the production population indistinguishably."""
        with pytest.raises(ConfigError, match="reserved for boundaries derived"):
            manual_boundary(
                level=100.0,
                anchor_date=dt.date(2023, 6, 15),
                atr=2.0,
                config=ToleranceConfig(),
                kind=BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY,
            )

    def test_a_manual_level_is_supported_and_tagged(self):
        boundary = manual_boundary(
            level=100.0,
            anchor_date=dt.date(2023, 6, 15),
            atr=2.0,
            config=ToleranceConfig(),
        )
        assert boundary.kind is BoundaryKind.MANUAL_BOUNDARY
        assert boundary.kind.is_research_only
        assert not boundary.is_production_eligible
        assert boundary.confidence == 0.0
        assert boundary.touch_count == 0

    def test_an_experimental_level_is_a_separate_population(self):
        boundary = manual_boundary(
            level=100.0,
            anchor_date=dt.date(2023, 6, 15),
            atr=2.0,
            config=ToleranceConfig(),
            kind=BoundaryKind.EXPERIMENTAL_BOUNDARY,
        )
        assert boundary.kind is BoundaryKind.EXPERIMENTAL_BOUNDARY
        assert boundary.kind is not BoundaryKind.MANUAL_BOUNDARY

    def test_a_structural_tag_without_a_pattern_key_is_not_eligible(self):
        """Both conditions, not either. Trusting the tag alone would let one bad
        construction call put research levels into the production population."""
        from tradeit.breakouts.boundary import BreakoutBoundary

        boundary = BreakoutBoundary(
            nominal=100.0,
            anchor_date=dt.date(2023, 6, 15),
            tolerance_pct=0.002,
            confidence=70.0,
            kind=BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY,
        )
        assert not boundary.is_production_eligible

    def test_the_kind_reaches_the_payload(self, confirmed):
        assert confirmed.boundary.to_payload()["kind"] in {str(k) for k in BoundaryKind}

    def test_a_research_boundary_shows_up_as_a_named_gap(self):
        scenario = GENERATOR.clean_breakout(seed=0)
        event = run_scenario(scenario, spec=BoundarySpec.weak())
        gaps = {g.field_name for g in EvidenceBundle.of(event).missing_for_decision()}
        assert "structural provenance" in gaps


class TestMonitorEligibility:
    def test_the_production_floor_excludes_forming(self):
        assert PatternState.FORMING not in PRODUCTION_PATTERN_STATES

    def test_a_config_cannot_widen_past_the_floor(self):
        with pytest.raises(EligibilityError, match="production floor"):
            BreakoutMonitor(BreakoutEngineConfig(monitored_states=("mature", "forming")))

    def test_narrowing_is_permitted(self):
        monitor = BreakoutMonitor(BreakoutEngineConfig(monitored_states=("mature",)))
        assert monitor.config.monitored_states == ("mature",)

    def test_the_check_names_the_supported_alternative(self):
        try:
            check_monitor_eligibility(["forming"])
        except EligibilityError as error:
            assert "non-structural boundary" in str(error)
        else:  # pragma: no cover - the call above must raise
            raise AssertionError("forming was accepted")

    def test_the_default_config_sits_at_the_floor(self):
        assert set(BreakoutEngineConfig().monitored_states) == {
            str(state) for state in PRODUCTION_PATTERN_STATES
        }


class TestStorage:
    def test_the_boundary_kind_is_persisted(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from tradeit.breakouts.persistence import BreakoutRepository
        from tradeit.core.enums import AssetClass, Exchange
        from tradeit.storage import tables

        event = run_scenario(GENERATOR.clean_breakout(seed=0))
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
            row = BreakoutRepository(db).save(event)
            assert row.boundary_kind == str(event.boundary.kind)
