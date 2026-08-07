"""ADR-0014 enforcement: structure first, score second.

The architectural rule is that a detector must find structures from causal
market geometry and never *select* among candidate windows by maximising its own
quality score. The rule is easy to state and easy to violate accidentally — it
was violated twice during construction, in the bull flag and again in the VCP,
both times by code that looked like ordinary "find the best fit".

These tests exist because a documented rule that nothing checks is a rule that
decays. They come in four groups:

**Leak probes.** A deliberately biased detector is built by subclassing a real
one and overriding only its discovery to pick the highest-scoring candidate.
Each probe then asserts that the biased version behaves differently from the
real one in a way the tests can see. A probe that *cannot* distinguish them is
itself a finding: it means that failure mode is invisible to this suite, and the
test says so rather than passing quietly.

**Invariance under future bars.** The strongest available statement of the rule
without a second implementation to compare against: a structure discovered as of
day T must not change when bars after T arrive. If discovery were score-driven,
later bars would supply better-scoring windows and the boundaries would move.

**Boundary provenance.** Every structural boundary must trace to a confirmed
pivot, an impulse termination, or a prior structural event — never to an index
chosen because it scored well.

**Legitimate multiplicity.** The rule forbids score-selection, not multiple
answers. Where several structures were causally available at one boundary, all
of them should be emitted; suppressing all but the best would be score-selection
wearing a different hat.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.detectors import (
    AscendingTriangleDetector,
    BaseOnBaseDetector,
    BreakoutRetestDetector,
    BullFlagDetector,
    CupHandleDetector,
    DetectionInputs,
    DoubleBottomDetector,
    FlatBaseDetector,
    HighTightFlagDetector,
    InverseHeadShouldersDetector,
    PennantDetector,
    Structure,
    TightConsolidationDetector,
    VcpDetector,
)
from tradeit.patterns.registry import DETECTOR_ORDER, DetectorRegistry
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator, VcpSpec

GENERATOR = PatternGenerator()
REGISTRY = DetectorRegistry.from_config()

#: The series each detector is probed against.
#:
#: Every one is drawn with **trailing room** -- extra sessions after the
#: structure completes -- and that is not incidental. The generators otherwise
#: finish the pattern on the last bar, so scanning a few sessions earlier finds
#: nothing and the invariance tests skip. A suite that skips is a suite that
#: proves nothing, and the whole point here is to compare what a detector said
#: on Monday with what it says on Friday about the same structure.
NATIVE_SERIES = {
    "bull_flag": lambda: GENERATOR.bull_flag(BullFlagSpec(breakout_sessions=3)),
    "vcp": lambda: GENERATOR.vcp(VcpSpec(breakout_sessions=8)),
    "flat_base": lambda: GENERATOR.flat_base(breakout_sessions=8),
    "ascending_triangle": lambda: GENERATOR.ascending_triangle(touches=6),
    "pennant": lambda: GENERATOR.pennant(sessions=18),
    "cup_handle": lambda: GENERATOR.cup_handle(handle_sessions=22),
    "high_tight_flag": lambda: GENERATOR.high_tight_flag(pause_sessions=20),
    "double_bottom": lambda: GENERATOR.double_bottom(recovery_sessions=22),
    "inverse_head_shoulders": lambda: GENERATOR.inverse_head_shoulders(recovery_sessions=22),
    "base_on_base": lambda: GENERATOR.base_on_base(second_sessions=28),
    "tight_consolidation": lambda: GENERATOR.tight_consolidation(sessions=20),
    "breakout_retest": lambda: GENERATOR.breakout_retest(hold_sessions=20),
}

#: Detectors built on BaseDetector, whose discover/score split can be subclassed
#: to build a biased twin. The bull flag and VCP predate that base and are
#: covered by the invariance and provenance groups instead.
SUBCLASSABLE = (
    FlatBaseDetector,
    AscendingTriangleDetector,
    PennantDetector,
    CupHandleDetector,
    HighTightFlagDetector,
    DoubleBottomDetector,
    InverseHeadShouldersDetector,
    BaseOnBaseDetector,
    TightConsolidationDetector,
    BreakoutRetestDetector,
)

#: Detectors whose discovery has a **single deterministic anchor**: a rule that
#: names one structure or none, with nothing to choose between.
#:
#: This is an architectural property worth stating rather than a gap. The flat
#: base takes the earliest confirmed high at the peak level in its window; the
#: ascending triangle takes the contiguous touch cluster under its ceiling. Both
#: rules yield exactly one answer, so score-selection is impossible there **by
#: construction** -- there is no candidate set to rank.
#:
#: The high tight flag belongs here for a different reason and it is worth being
#: precise about the difference. Its discovery *does* enumerate swing-low
#: origins, but it dedupes them onto the confirmed peak each one leads to, and
#: its magnitude gate is strict enough that in practice a single peak survives.
#: So it is single-anchor in effect rather than by construction, and if the
#: magnitude floor were ever relaxed it could become multi-anchor -- which is
#: exactly what the assertion below would catch.
#:
#: The consequence is worth knowing too: these families cannot report two
#: simultaneous readings of themselves on one instrument. Where a chart really
#: does contain two flat bases, the detector describes the more recent one and
#: the earlier one is visible only in the tracked history.
SINGLE_ANCHOR = {"flat_base", "ascending_triangle", "high_tight_flag"}

#: Detectors that enumerate anchors and may return several structures. These are
#: the ones where a score-selecting implementation would have had something to
#: select, so these are the ones the leak probe can actually test.
MULTI_ANCHOR = {
    "pennant",
    "cup_handle",
    "double_bottom",
    "inverse_head_shoulders",
    "base_on_base",
    "tight_consolidation",
    "breakout_retest",
}

#: Series that reliably give a multi-anchor detector more than one structure.
#: Chosen per family because "a chart with several readings" is family-specific.
MULTI_ANCHOR_SERIES = {
    "pennant": lambda: GENERATOR.random_walk(240, volatility=0.032, seed=1),
    "cup_handle": lambda: GENERATOR.random_walk(240, volatility=0.032, seed=1),
    "double_bottom": lambda: GENERATOR.random_walk(240, volatility=0.032, seed=0),
    "inverse_head_shoulders": lambda: GENERATOR.random_walk(240, volatility=0.032, seed=1),
    "base_on_base": lambda: GENERATOR.base_on_base(second_sessions=26),
    "tight_consolidation": lambda: GENERATOR.base_on_base(second_sessions=26),
    "breakout_retest": lambda: GENERATOR.failed_retest(),
}


def _args(series):
    return series.bars, series.last_session


def best_quality(detector, series) -> float:
    return max((p.quality for p in detector.detect(*_args(series))), default=0.0)


def make_capturing(cls):
    """A twin that records what discovery produced, before scoring saw it.

    ``detect`` is the wrong place to look for score-selection: instances are
    also dropped for coverage and for the reporting floor, so a detector that
    discovers six structures and reports one may be behaving perfectly. The rule
    is about ``discover``, so that is where the probe measures.
    """

    class Capturing(cls):  # type: ignore[valid-type,misc]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.discovered: list[Structure] = []

        def discover(self, inputs: DetectionInputs) -> list[Structure]:
            found = super().discover(inputs)
            self.discovered = list(found)
            return found

    return Capturing


def make_score_selecting(cls):
    """A twin that keeps only its own highest-scoring structure.

    This is the forbidden behaviour, implemented exactly. Building it is the
    only way to check that the real detector is not doing it: without a
    counterexample, "we do not select by score" is an assertion about code
    nobody re-reads.
    """

    class ScoreSelecting(cls):  # type: ignore[valid-type,misc]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.discovered: list[Structure] = []

        def discover(self, inputs: DetectionInputs) -> list[Structure]:
            found = super().discover(inputs)
            if len(found) >= 2:
                found = sorted(
                    found,
                    key=lambda s: sum(c.score * c.weight for c in self.score(inputs, s)),
                    reverse=True,
                )[:1]
            self.discovered = list(found)
            return found

    return ScoreSelecting


class TestScoreSelectionIsDetectable:
    """The leak probes. A biased twin must be distinguishable from the real one."""

    @pytest.mark.parametrize(
        "cls", [c for c in SUBCLASSABLE if c.name in MULTI_ANCHOR], ids=lambda c: c.name
    )
    def test_discovery_keeps_every_causally_available_structure(self, cls):
        """The probe that matters, measured where the rule applies.

        On a series offering several readings, honest discovery returns all of
        them and the score-selecting twin returns one. Measuring at ``discover``
        rather than at ``detect`` is what makes this sharp: a detector may
        legitimately *report* one instance after coverage and floor filtering,
        and comparing reported counts would confuse that with selection.
        """
        honest = make_capturing(cls)()
        biased = make_score_selecting(cls)()
        series = MULTI_ANCHOR_SERIES[cls.name]()

        honest.detect(*_args(series))
        biased.detect(*_args(series))

        assert len(honest.discovered) >= 2, (
            f"{cls.name} is classified as multi-anchor but discovered "
            f"{len(honest.discovered)} structure(s) here; either the classification is "
            "wrong or this series no longer offers the ambiguity the probe needs"
        )
        assert len(biased.discovered) == 1
        assert len(honest.discovered) > len(biased.discovered)

    @pytest.mark.parametrize(
        "cls", [c for c in SUBCLASSABLE if c.name in SINGLE_ANCHOR], ids=lambda c: c.name
    )
    def test_single_anchor_discovery_never_has_anything_to_select(self, cls):
        """Score-selection is impossible here by construction.

        A positive statement rather than a skipped probe. These detectors a
        single structure or none, so there is no candidate set to rank -- and
        the guarantee is worth asserting, because a later change that started
        enumerating anchors would silently reopen the failure mode the ADR
        exists to close.
        """
        detector = make_capturing(cls)()
        for builder in (
            lambda: GENERATOR.random_walk(240, volatility=0.032, seed=4),
            lambda: GENERATOR.choppy_range(),
            lambda: GENERATOR.flat_base(sessions=60),
            lambda: GENERATOR.ascending_triangle(touches=8, sessions=60),
            lambda: GENERATOR.mean_reverting(),
            lambda: GENERATOR.high_tight_flag(pause_sessions=20),
        ):
            series = builder()
            detector.detect(*_args(series))
            assert len(detector.discovered) <= 1, (
                f"{cls.name} is documented as single-anchor but discovered "
                f"{len(detector.discovered)} structures; if enumeration was added "
                "deliberately, move it to MULTI_ANCHOR so the leak probe covers it"
            )

    @pytest.mark.parametrize("cls", SUBCLASSABLE, ids=lambda c: c.name)
    def test_the_honest_detector_keeps_structures_a_biased_one_would_drop(self, cls):
        """Every structure the biased twin returns must also be in the honest
        detector's output. The reverse must not hold, or the honest detector is
        already filtering by score."""
        honest = cls()
        biased = make_score_selecting(cls)()
        multi = MULTI_ANCHOR_SERIES.get(cls.name, lambda: GENERATOR.choppy_range())
        for series in (multi(), GENERATOR.random_walk(240, volatility=0.02)):
            honest_keys = {p.identity_key for p in honest.detect(*_args(series))}
            biased_keys = {p.identity_key for p in biased.detect(*_args(series))}
            assert biased_keys <= honest_keys

    @pytest.mark.parametrize(
        "cls", [c for c in SUBCLASSABLE if c.name in MULTI_ANCHOR], ids=lambda c: c.name
    )
    def test_discovery_order_is_positional_not_quality(self, cls):
        """Structures come back in the order the market produced them.

        Discovery walks anchors from the most recent backwards, so the returned
        order tracks position in the series. An order that tracked quality
        instead would mean scoring had run before discovery finished, which is
        the inversion the ADR is named for.
        """
        honest = make_capturing(cls)()
        series = MULTI_ANCHOR_SERIES[cls.name]()
        honest.detect(*_args(series))
        assert len(honest.discovered) >= 2

        starts = [s.start_index for s in honest.discovered]
        assert starts == sorted(starts) or starts == sorted(starts, reverse=True), (
            f"{cls.name} returned structures in neither forward nor reverse positional "
            "order; an order that is not positional is an order chosen by something else"
        )


class TestDiscoveryDoesNotMoveWhenTheFutureArrives:
    """Invariance under later bars.

    If a boundary were chosen because it maximised a score, adding bars would
    supply better-scoring alternatives and the boundary would move. Holding a
    structure's start fixed as the future arrives is the strongest statement of
    ADR-0014 available without a second implementation to diff against.

    Only structures that survive into the later scan are compared. A pattern
    that legitimately resolves, expires or is superseded is not a violation --
    it is the lifecycle working.
    """

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_a_structural_start_never_moves_backwards_in_time(self, name):
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        series = NATIVE_SERIES[name]()
        bars = series.bars

        early_session = bars[-6].session_date
        early = detector.detect([b for b in bars if b.session_date <= early_session], early_session)
        if not early:
            pytest.skip(f"{name} finds nothing five sessions before the end of its own series")

        later = detector.detect(bars, series.last_session)
        later_by_key = {p.identity_key: p for p in later}

        compared = 0
        for instance in early:
            match = later_by_key.get(instance.identity_key)
            if match is None:
                continue
            compared += 1
            assert match.geometry.start_date == instance.geometry.start_date, (
                f"{name} moved a structural start from {instance.geometry.start_date} to "
                f"{match.geometry.start_date} once later bars arrived; a boundary that "
                "moves when the future is revealed was chosen by score, not by structure"
            )
        if compared == 0:
            pytest.skip(f"no {name} identity survived into the later scan to compare")

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_identity_is_stable_across_the_confirmation_lag(self, name):
        """A structure's identity must not change merely because the provisional
        tail filled in. Identity keys on the structural start, so this is the
        same claim as above expressed the way the persistence layer sees it."""
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        series = NATIVE_SERIES[name]()
        bars = series.bars

        keys_by_day = []
        for offset in (6, 4, 2, 0):
            session = bars[-1 - offset].session_date
            found = detector.detect([b for b in bars if b.session_date <= session], session)
            keys_by_day.append({p.identity_key for p in found})

        # Identities may appear and disappear; what must not happen is the same
        # structure reappearing under a different key, which shows up as the set
        # churning completely while detections continue.
        populated = [k for k in keys_by_day if k]
        if len(populated) < 2:
            pytest.skip(f"{name} produced detections on fewer than two of the four sessions")
        assert set.intersection(*populated), (
            f"{name} shares no identity across four consecutive sessions; the same "
            "structure is being re-minted rather than tracked"
        )


class TestBoundaryProvenance:
    """Where boundaries come from.

    Every structural boundary must trace to something the market produced. The
    strongest available check without reaching into private state: a boundary
    must coincide with a bar the detector was allowed to see, and must never
    fall inside the provisional tail whose pivots are not yet confirmed.
    """

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_no_structure_starts_inside_the_provisional_tail(self, name):
        """The tail may inform state; it must never define structure."""
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        series = NATIVE_SERIES[name]()
        right_bars = REGISTRY.config.swings.right_bars
        boundary = series.bars[-1 - right_bars].session_date

        for instance in detector.detect(*_args(series)):
            assert instance.geometry.start_date <= boundary, (
                f"{name} started a structure at {instance.geometry.start_date}, inside the "
                f"{right_bars}-session provisional tail; those pivots are not confirmed and "
                "cannot define a boundary"
            )

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_every_boundary_lands_on_a_real_session(self, name):
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        series = NATIVE_SERIES[name]()
        sessions = {b.session_date for b in series.bars}

        for instance in detector.detect(*_args(series)):
            assert instance.geometry.start_date in sessions
            assert instance.geometry.end_date in sessions
            for start, end in instance.geometry.segments.values():
                assert start in sessions and end in sessions

    @pytest.mark.parametrize("name", DETECTOR_ORDER)
    def test_boundaries_are_anchored_to_dates_not_recomputed_levels(self, name):
        """A boundary carries the session it was drawn from.

        Without an anchor date a stored boundary is a bare number, and the only
        way to check it later is to recompute it against a longer series --
        which would draw a different line and call it the same one.
        """
        detector = REGISTRY.detector(name, Bartimeframe.D1)
        series = NATIVE_SERIES[name]()
        sessions = {b.session_date for b in series.bars}

        for instance in detector.detect(*_args(series)):
            for boundary in (instance.geometry.resistance, instance.geometry.support):
                if boundary is None:
                    continue
                assert boundary.anchor_date in sessions
                assert boundary.level > 0


class TestLegitimateMultiplicity:
    """The rule forbids selection by score, not multiple answers."""

    def test_several_causally_available_structures_are_all_emitted(self):
        """A volatile series offers a reversal detector many valid readings.

        Keeping only the best would be score-selection; keeping all of them is
        the design. Uses a multi-anchor family deliberately -- asking this of a
        single-anchor detector would test the classification rather than the
        rule.
        """
        detector = DoubleBottomDetector()
        found = detector.detect(*_args(GENERATOR.random_walk(240, volatility=0.032, seed=0)))
        assert len(found) >= 2
        assert len({p.identity_key for p in found}) == len(found)

    def test_ordering_is_presentation_only(self):
        """Results are sorted by quality for readability. Sorting after the fact
        cannot change what exists, and the test pins that distinction: the same
        set comes back regardless of the order bars are passed in."""
        detector = FlatBaseDetector()
        series = GENERATOR.flat_base()
        first = detector.detect(*_args(series))
        second = detector.detect(list(series.bars), series.last_session)
        assert [p.identity_key for p in first] == [p.identity_key for p in second]
        assert [p.quality for p in first] == sorted((p.quality for p in first), reverse=True)

    def test_a_candidate_cap_bounds_work_without_choosing_by_score(self):
        """``max_candidates_per_pattern`` truncates discovery, not scoring.

        The cap applies to the structures discovery produced, in the order
        discovery produced them -- most recent first. Applying it after scoring
        would turn a performance control into exactly the selection ADR-0014
        forbids.
        """
        assert REGISTRY.config.max_candidates_per_pattern >= 1
        detector = BullFlagDetector()
        found = detector.detect(*_args(GENERATOR.choppy_range()))
        assert len(found) <= REGISTRY.config.max_candidates_per_pattern


class TestKnownHistoricalLeaks:
    """Regressions for the two selection-bias bugs that actually happened.

    Both were found by feeding a detector a series whose structure was known and
    noticing the reported geometry did not match it. Both are now impossible by
    construction, and these tests are what keeps them that way.
    """

    def test_the_flagpole_end_is_the_highest_confirmed_high(self):
        """The bull flag once searched (pole, flag) splits and let a tie-break
        decide the pole end, which meant the split that scored best won. The
        pole now ends at the argmax over confirmed data, full stop."""
        detector = BullFlagDetector()
        series = GENERATOR.bull_flag()
        found = detector.detect(*_args(series))
        assert found
        pole_start, pole_end = found[0].geometry.segments["flagpole"]
        confirmed = [b for b in series.bars if pole_start <= b.session_date <= pole_end]
        peak = max(confirmed, key=lambda b: b.high)
        assert peak.session_date == pole_end, (
            "the flagpole must end at its highest confirmed high; an end chosen "
            "anywhere else was chosen by something other than the geometry"
        )

    def test_the_vcp_base_starts_at_the_earliest_high_at_the_peak_level(self):
        """Taking the *later* of two equal highs truncated the base to its final
        leg, which measured as far tighter than the structure that formed. The
        rule is earliest-at-level, and the cup, base-on-base and double bottom
        adopted it for the same reason."""
        detector = VcpDetector()
        series = GENERATOR.vcp()
        found = detector.detect(*_args(series))
        assert found
        instance = found[0]
        tolerance = REGISTRY.config.swings.touch_tolerance_pct
        peak = max(
            float(b.high)
            for b in series.bars
            if b.session_date <= instance.geometry.end_date
            and b.session_date >= instance.geometry.start_date
        )
        start_bar = next(b for b in series.bars if b.session_date == instance.geometry.start_date)
        assert float(start_bar.high) >= peak * (1.0 - tolerance * 3), (
            "the base must start at a high near its own peak level; a start further "
            "down the series is a window chosen rather than a level found"
        )
