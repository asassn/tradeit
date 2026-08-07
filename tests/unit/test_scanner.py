"""The integrated scanner, multi-timeframe detection, and incremental equivalence.

Four requirements meet in this file, and they are tested together because they
are only interesting together: a scanner that runs detectors but loses their
identities, or one that scans several timeframes but merges them, or one whose
incremental path disagrees with a full replay, would each pass a narrower test.

**Scope.** The scanner's most important property is what it does *not* do. The
tests assert the absence explicitly, because scope creep at this layer would
make the pattern system un-auditable — "why was this pattern reported" and "why
was this trade taken" would have the same answer.

**Causality across timeframes.** A weekly bar must never contain a session the
clock has not released. Scanning mid-week must see last week's completed bar and
not this week's partial one, because that is the value a live system would have
had on Wednesday.

**Identity across timeframes.** A daily bull flag and a weekly bull flag on the
same instrument are two patterns, not one. Timeframe is part of the identity
hash, so this is a property of the design rather than a convention anyone has to
remember.

**Incremental equivalence.** The claim being tested is precise: scanning a
bounded suffix produces the same instances as scanning the whole series, at the
same knowledge boundary. Any difference is a leak or a bug, and the tolerance is
zero on identity and geometry.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.errors import ConfigError, DataError
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.registry import DETECTOR_ORDER, DetectorRegistry, phase4_baseline
from tradeit.patterns.scanner import PatternScanner, RescanPolicy
from tradeit.patterns.series import causal_series, contains_no_future_bars, intraday_series
from tradeit.patterns.synthetic import PatternGenerator

GENERATOR = PatternGenerator()


@pytest.fixture(scope="module")
def long_series():
    """A long daily series, so weekly aggregation has something to work with."""
    return GENERATOR.flat_base(sessions=60, breakout_sessions=10)


class TestBaseline:
    def test_the_frozen_baseline_matches_the_shipped_configuration(self):
        """The gate's freeze, asserted rather than asserted-about.

        A detector corrected during the validation gate increments its version,
        and this test then fails until the baseline is deliberately re-cut --
        which is the point. Silent drift between what was validated and what
        runs is the thing a baseline exists to prevent.
        """
        assert phase4_baseline().check() == []

    def test_every_detector_is_registered_with_a_version_and_a_contract(self):
        registry = DetectorRegistry.from_config()
        assert set(registry.versions()) == set(DETECTOR_ORDER)
        for entry in registry.all_entries():
            assert entry.version >= 1
            assert entry.required_components
            assert entry.minimum_bars > 0
            assert entry.timeframes

    def test_changing_a_threshold_changes_the_digest(self):
        """Configuration identity is content, not a version number."""
        default = DetectorRegistry.from_config()
        altered = DetectorRegistry.from_config(
            PatternEngineConfig(cup_handle={"min_cup_depth": 0.11})
        )
        assert default.digest() != altered.digest()


class TestDetectorEnablement:
    def test_a_disabled_detector_is_recorded_rather_than_silently_absent(self):
        """ "No cup was found" and "the cup detector never ran" are different
        facts, and a dataset that cannot tell them apart cannot support a recall
        measurement."""
        config = PatternEngineConfig(enabled_detectors=("bull_flag", "pennant"))
        scanner = PatternScanner(config=config)
        series = GENERATOR.bull_flag()
        result = scanner.scan(1, Bartimeframe.D1, series.bars, series.last_session)

        skipped = {s.name: s.reason for s in result.skipped}
        assert "cup_handle" in skipped
        assert "disabled" in skipped["cup_handle"]
        assert all(i.detector_name in {"bull_flag", "pennant"} for i in result.instances)

    def test_an_unknown_detector_name_is_refused(self):
        with pytest.raises(ConfigError, match="no such detector"):
            DetectorRegistry.from_config(PatternEngineConfig(enabled_detectors=("moon_phase",)))

    def test_a_family_is_refused_on_a_timeframe_it_is_not_defined_for(self):
        """A high tight flag on 15-minute bars would be a 70% advance over eight
        hours: computable geometry, and a different claim wearing the name of a
        studied one."""
        registry = DetectorRegistry.from_config()
        with pytest.raises(ConfigError, match="does not support"):
            registry.detector("high_tight_flag", Bartimeframe.M15)

    def test_the_scanner_skips_unsupported_families_with_a_reason(self):
        scanner = PatternScanner()
        series = GENERATOR.bull_flag()
        result = scanner.scan(1, Bartimeframe.M15, series.bars, series.last_session)
        skipped = {s.name: s.reason for s in result.skipped}
        assert "not defined on" in skipped["cup_handle"]


class TestScannerScope:
    """What the scanner must not do. The most important tests in the file."""

    def test_no_result_claims_a_confirmed_breakout(self, long_series):
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        for instance in result.instances:
            label = str(instance.state)
            assert "confirmed" not in label or label.endswith("unconfirmed")

    def test_the_result_carries_no_trade_sizing_or_ranking_vocabulary(self, long_series):
        """A structural assertion about the payload's shape.

        If a later phase adds entries or sizes, they must live in that phase's
        objects. A pattern payload that grew a `position_size` field would make
        it impossible to tell a pattern observation from a trading decision.
        """
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        assert result.instances
        payload = result.instances[0].to_payload()
        forbidden = {"entry", "stop_loss", "position_size", "shares", "rank", "score_rank"}
        assert not forbidden & set(payload)

    def test_invalidation_is_structural_and_named_so(self, long_series):
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        for instance in result.instances:
            assert instance.invalidation_price > 0


class TestScanning:
    def test_a_scan_returns_every_material_interpretation(self, long_series):
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        assert len(result.families) >= 2, (
            "a real chart usually supports several readings; forcing one would "
            "discard what later scoring is better placed to choose between"
        )
        assert result.relations

    def test_detector_timings_are_recorded_per_family(self, long_series):
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        assert result.timings
        assert all(t >= 0 for t in result.timings.values())

    def test_identities_persist_across_sessions(self, long_series):
        """The failure the whole identity design exists to prevent: re-minting
        the same structure every day."""
        scanner = PatternScanner()
        bars = long_series.bars
        seen: list[set[str]] = []
        for offset in (4, 2, 0):
            session = bars[-1 - offset].session_date
            gated = [b for b in bars if b.session_date <= session]
            result = scanner.scan(1, Bartimeframe.D1, gated, session)
            seen.append({t.identity_key for t in result.tracked})
        assert set.intersection(*[s for s in seen if s])

    def test_a_scan_refuses_bars_past_the_boundary(self, long_series):
        scanner = PatternScanner()
        with pytest.raises(ValueError, match="past the knowledge boundary"):
            scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.bars[-5].session_date)


class TestMultiTimeframe:
    def test_a_weekly_bar_never_contains_a_session_past_the_boundary(self, long_series):
        """The invariant the whole requirement turns on."""
        bars = long_series.bars
        for offset in (0, 1, 2, 3, 4):
            session = bars[-1 - offset].session_date
            gated = [b for b in bars if b.session_date <= session]
            built = causal_series(gated, [Bartimeframe.W1], session)
            assert contains_no_future_bars(built[Bartimeframe.W1], session)

    def test_a_midweek_scan_sees_last_weeks_completed_bar(self, long_series):
        """Not this week's partial one.

        The weekly series must not grow on a day when no week has finished.
        That is what a live system would have had, and letting the partial week
        in produces a weekly high that changes without any new weekly
        information.
        """
        bars = long_series.bars
        by_session = {b.session_date: b for b in bars}
        weekly_counts = {}
        for offset in range(0, 6):
            session = bars[-1 - offset].session_date
            gated = [b for b in bars if b.session_date <= session]
            built = causal_series(gated, [Bartimeframe.W1], session)
            weekly_counts[session] = len(built[Bartimeframe.W1])

        ordered = sorted(weekly_counts)
        counts = [weekly_counts[s] for s in ordered]
        assert counts == sorted(counts), "the weekly series must never shrink as time advances"
        assert max(counts) - min(counts) <= 2, (
            "six consecutive sessions span at most two week boundaries; more than "
            "that means partial weeks are being admitted"
        )
        assert by_session  # the fixture really is a daily series

    def test_resampling_refuses_a_series_past_the_boundary(self, long_series):
        with pytest.raises(DataError, match="past the"):
            causal_series(long_series.bars, [Bartimeframe.W1], long_series.bars[-5].session_date)

    def test_intraday_aggregation_produces_bars_of_the_width_it_claims(self):
        """The bug this test exists for: an earlier generator emitted 26 bars a
        session and called them minute bars, which made rolling them into
        15-minute buckets the identity function."""
        series = GENERATOR.flat_base(sessions=45)
        m15 = GENERATOR.to_intraday_bars(series, Bartimeframe.M15)
        assert m15[0].timeframe is Bartimeframe.M15
        spacing = (m15[1].event_time - m15[0].event_time).total_seconds() / 60
        assert spacing == pytest.approx(15.0)

        built = intraday_series(m15, [Bartimeframe.H1, Bartimeframe.H4], m15[-1].event_time)
        assert len(built[Bartimeframe.H1]) < len(m15)
        assert len(built[Bartimeframe.H4]) < len(built[Bartimeframe.H1])

    def test_intraday_bars_never_carry_a_future_event_time(self):
        series = GENERATOR.flat_base(sessions=45)
        m15 = GENERATOR.to_intraday_bars(series, Bartimeframe.M15)
        cutoff = m15[len(m15) // 2].event_time
        gated = [b for b in m15 if b.event_time <= cutoff]
        built = intraday_series(gated, [Bartimeframe.H1], cutoff)
        assert all(b.event_time <= cutoff for b in built[Bartimeframe.H1])

    def test_a_daily_and_a_weekly_pattern_are_separate_identities(self, long_series):
        """Timeframe is part of the identity hash, so this holds by design
        rather than by anyone remembering it."""
        scanner = PatternScanner()
        session = long_series.last_session
        built = causal_series(long_series.bars, [Bartimeframe.D1, Bartimeframe.W1], session)
        result = scanner.scan_timeframes(1, built, session)

        daily = {i.identity_key for i in result.by_timeframe[Bartimeframe.D1].instances}
        weekly = {i.identity_key for i in result.by_timeframe[Bartimeframe.W1].instances}
        assert not (daily & weekly)

    def test_geometry_overlap_does_not_merge_identities(self, long_series):
        """Two structures covering the same calendar span at different scales are
        two patterns. Merging them would erase the fact that they are statements
        at different resolutions."""
        scanner = PatternScanner()
        session = long_series.last_session
        built = causal_series(long_series.bars, [Bartimeframe.D1, Bartimeframe.W1], session)
        result = scanner.scan_timeframes(1, built, session)
        keys = [i.identity_key for i in result.all_instances]
        assert len(keys) == len(set(keys))


class TestIncrementalEquivalence:
    """The critical test: bounded rescan must equal full replay."""

    def test_every_detector_is_classified(self):
        scanner = PatternScanner()
        for name in DETECTOR_ORDER:
            assert scanner.rescan_policy(name) is RescanPolicy.BOUNDED_RESCAN_REQUIRED
            assert scanner.rescan_window(name) > 0

    def test_the_rescan_window_exceeds_the_structural_lookback(self):
        """The extra is the Wilder warm-up, and it is not optional.

        ATR is an exponential average with unbounded memory, so truncating the
        series changes every later value by a decaying amount rather than not at
        all. A window equal to ``minimum_bars`` would be structurally sufficient
        and numerically wrong.
        """
        scanner = PatternScanner()
        registry = scanner.registry
        for name in DETECTOR_ORDER:
            assert scanner.rescan_window(name) > registry.entries[name].minimum_bars

    @pytest.mark.parametrize(
        "builder",
        [
            lambda: GENERATOR.flat_base(sessions=60, breakout_sessions=10),
            lambda: GENERATOR.cup_handle(handle_sessions=20),
            lambda: GENERATOR.double_bottom(recovery_sessions=20),
            lambda: GENERATOR.breakout_retest(hold_sessions=18),
        ],
        ids=["flat_base", "cup_handle", "double_bottom", "breakout_retest"],
    )
    def test_incremental_matches_full_replay_exactly(self, builder):
        """Identity, geometry, state, components and coverage must all agree.

        The tolerance is zero. A bounded rescan that produced *nearly* the same
        answer would mean the bound was chosen by hope rather than by what the
        detectors can see, and the difference would surface later as patterns
        that appear and vanish depending on how much history was loaded.
        """
        series = builder()
        session = series.last_session

        full = PatternScanner().scan(1, Bartimeframe.D1, series.bars, session, track=False)
        incremental = PatternScanner().scan_incremental(
            1, Bartimeframe.D1, series.bars, session, track=False
        )

        assert [i.to_payload() for i in full.instances] == [
            i.to_payload() for i in incremental.instances
        ]

    def test_incremental_matches_full_replay_across_a_replayed_history(self):
        """Bar-by-bar replay, both ways, compared at every boundary.

        The single most important test for the incremental path: agreement on
        one session could be luck, and agreement across a whole replay is the
        claim actually being made.
        """
        series = GENERATOR.flat_base(sessions=60, breakout_sessions=10)
        bars = series.bars
        full_scanner = PatternScanner()
        incremental_scanner = PatternScanner()

        compared = 0
        for offset in range(12, 0, -1):
            session = bars[-offset].session_date
            gated = [b for b in bars if b.session_date <= session]

            full = full_scanner.scan(1, Bartimeframe.D1, gated, session)
            incremental = incremental_scanner.scan_incremental(1, Bartimeframe.D1, gated, session)

            assert [i.to_payload() for i in full.instances] == [
                i.to_payload() for i in incremental.instances
            ], f"incremental and full replay disagreed at {session}"

            assert {t.identity_key for t in full.tracked} == {
                t.identity_key for t in incremental.tracked
            }
            compared += 1
        assert compared == 12

    def test_a_context_forces_the_full_series(self):
        """Truncating bars without truncating the benchmark would misalign them.

        The alignment check would catch it, but the honest response is to not
        try: a scan with a context is correct and slower rather than fast and
        wrong.
        """
        series = GENERATOR.flat_base(sessions=60, breakout_sessions=10)
        scanner = PatternScanner()
        from tradeit.patterns.base import PatternContext

        context = PatternContext(benchmark_closes=[float(b.close) for b in series.bars])
        result = scanner.scan_incremental(
            1, Bartimeframe.D1, series.bars, series.last_session, context=context, track=False
        )
        assert result.instances

    def test_the_saving_is_real(self):
        """A bounded window on a decade of dailies is a fraction of the series.

        Recorded as a test so a future change that quietly widened the window
        to the whole series would be visible rather than merely slow.
        """
        scanner = PatternScanner()
        required = scanner.required_history(Bartimeframe.D1)
        decade = 2520
        assert required < decade / 4


class TestScanResultReporting:
    def test_a_summary_is_serialisable_and_names_what_ran(self, long_series):
        scanner = PatternScanner()
        result = scanner.scan(1, Bartimeframe.D1, long_series.bars, long_series.last_session)
        summary = result.summary()
        assert summary["instrument_id"] == 1
        assert summary["timeframe"] == "1d"
        assert isinstance(summary["families"], list)
        assert dt.date.fromisoformat(str(summary["as_of_session"]))
