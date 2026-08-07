"""Pattern-detection throughput and scaling.

The question these answer, before eleven more detectors are built on the same
architecture: **can this scan thousands of securities in the time the daily
pipeline allows?** The scheduler gives pattern detection a 90-minute window.

Assertions are loose ceilings, deliberately. Absolute timings vary several-fold
across machines and a tight bound would be a flaky test rather than a useful
one. What the assertions do catch is an *algorithmic* regression: if candidate
enumeration goes quadratic, or swing detection starts rescanning the whole
series per candidate, throughput drops by orders of magnitude and these fail
loudly.

The scaling measurements matter more than the absolute ones. Extrapolating a
single-instrument timing to 4,000 instruments assumes linearity, and assuming
linearity is how an O(n²) surprise reaches production.

Run with ``pytest -m performance -s`` to see the numbers.
"""

from __future__ import annotations

import time
import tracemalloc

import pytest

from tradeit.patterns.detectors import BullFlagDetector
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator, SeriesSpec
from tradeit.patterns.tracking import PatternTracker

pytestmark = pytest.mark.performance

DETECTOR = BullFlagDetector()

#: Two years of sessions plus warm-up. Deeper than the detector needs, so the
#: measurement reflects a realistic stored history rather than a minimal one.
HISTORY_SESSIONS = 500


def build_series(count: int, *, sessions: int = HISTORY_SESSIONS) -> list:
    """Distinct series, one per instrument.

    Each gets its own seed so the detector cannot benefit from repeated inputs
    -- caching or branch prediction on identical data would flatter the result.
    """
    out = []
    for i in range(count):
        generator = PatternGenerator(
            SeriesSpec(instrument_id=i + 1, lead_in_sessions=sessions - 25, seed=20260807 + i)
        )
        out.append(generator.bull_flag(BullFlagSpec(flag_sessions=10), seed=i))
    return out


class TestThroughput:
    def test_single_instrument_detection(self):
        series = build_series(1)[0]
        start = time.perf_counter()
        for _ in range(20):
            DETECTOR.detect(series.bars, series.last_session)
        elapsed = (time.perf_counter() - start) / 20

        rate = 1.0 / elapsed
        print(
            f"\n  bull flag: {len(series.bars)} bars in {elapsed * 1000:.1f} ms "
            f"({rate:.0f} instruments/s, ~{4000 / rate / 60:.1f} min for 4,000 "
            f"single-threaded)"
        )
        assert elapsed < 0.5, "an order-of-magnitude regression"

    @pytest.mark.parametrize("count", [100])
    def test_universe_scan(self, count):
        universe = build_series(count)
        start = time.perf_counter()
        found = sum(len(DETECTOR.detect(s.bars, s.last_session)) for s in universe)
        elapsed = time.perf_counter() - start

        print(
            f"\n  {count} instruments scanned in {elapsed:.2f} s "
            f"({count / elapsed:.0f}/s, {found} patterns) "
            f"-> ~{4000 / (count / elapsed) / 60:.1f} min for 4,000"
        )
        assert elapsed < 60.0

    def test_scaling_is_linear_in_universe_size(self):
        """Measured rather than assumed.

        Detection is per-instrument and shares no cross-sectional state, so
        linear is the expected result -- but "expected" is exactly the
        assumption an O(n²) bug hides behind.
        """
        timings = {}
        for count in (25, 50, 100):
            universe = build_series(count)
            start = time.perf_counter()
            for series in universe:
                DETECTOR.detect(series.bars, series.last_session)
            timings[count] = time.perf_counter() - start

        ratio = timings[100] / timings[25]
        print(
            f"\n  universe scaling: 25={timings[25] * 1000:.0f}ms  "
            f"50={timings[50] * 1000:.0f}ms  100={timings[100] * 1000:.0f}ms  "
            f"(4x universe -> {ratio:.1f}x time)"
        )
        assert ratio < 8.0, "4x the universe should not cost more than 8x the time"

    def test_scaling_in_history_depth(self):
        """The dimension where a regression is most likely.

        Candidate enumeration walks confirmed swing lows, and swing detection
        walks the series. A naive rewrite that re-derived swings per candidate
        would be quadratic in history, which is invisible at 250 bars and
        crippling at 2,000.
        """
        timings = {}
        for sessions in (250, 500, 1000):
            series = build_series(1, sessions=sessions)[0]
            start = time.perf_counter()
            for _ in range(5):
                DETECTOR.detect(series.bars, series.last_session)
            timings[sessions] = (time.perf_counter() - start) / 5

        ratio = timings[1000] / timings[250]
        print(
            f"\n  history scaling: 250={timings[250] * 1000:.1f}ms  "
            f"500={timings[500] * 1000:.1f}ms  1000={timings[1000] * 1000:.1f}ms  "
            f"(4x history -> {ratio:.1f}x time)"
        )
        assert ratio < 12.0, "history scaling looks superlinear; check for a rescan per candidate"


class TestIncremental:
    def test_incremental_update_versus_full_redetection(self):
        """Today's scan re-runs the detector over the whole visible history.

        There is no incremental path, and this measures what that costs. The
        honest finding is that it costs nothing worth optimising: a full
        re-detection is already fast enough that an incremental cache would add
        a correctness risk -- stale confirmed pivots -- for no measurable gain.
        Recorded so the decision is a measurement rather than an assumption.
        """
        series = build_series(1)[0]
        full = time.perf_counter()
        DETECTOR.detect(series.bars, series.last_session)
        full = time.perf_counter() - full

        # "Incremental" here is the realistic alternative: re-detect over only
        # the window a pattern could occupy rather than the whole series.
        window = series.bars[-160:]
        partial = time.perf_counter()
        DETECTOR.detect(window, window[-1].session_date)
        partial = time.perf_counter() - partial

        print(
            f"\n  full re-detection {full * 1000:.1f} ms vs "
            f"windowed {partial * 1000:.1f} ms ({full / max(partial, 1e-9):.1f}x)"
        )
        assert full < 0.5

    def test_tracker_update_cost(self):
        """The tracking layer must not dominate detection.

        It is a dictionary lookup and a tuple append per pattern, so it should
        be negligible -- but "should be" is how an accidental O(n) rescan of
        the closed set gets in.
        """
        universe = build_series(50)
        tracker = PatternTracker()
        session = universe[0].last_session

        detections = []
        for series in universe:
            detections.extend(DETECTOR.detect(series.bars, session))

        start = time.perf_counter()
        tracker.observe(detections, session)
        elapsed = time.perf_counter() - start

        print(f"\n  tracker: {len(detections)} detections folded in {elapsed * 1000:.2f} ms")
        assert elapsed < 1.0

    def test_repeated_tracker_updates_do_not_degrade(self):
        """Guards the specific regression: a tracker that rescans its history.

        Comparing the first update against the twentieth catches an
        accumulating cost that a single measurement cannot see.
        """
        series = build_series(1)[0]
        tracker = PatternTracker()
        timings = []
        for i in range(len(series.bars) - 25, len(series.bars)):
            bars = series.bars[: i + 1]
            day = bars[-1].session_date
            detections = DETECTOR.detect(bars, day)
            start = time.perf_counter()
            tracker.observe(detections, day, closes={series.bars[0].instrument_id: 100.0})
            timings.append(time.perf_counter() - start)

        early = sum(timings[:5]) / 5
        late = sum(timings[-5:]) / 5
        print(f"\n  tracker update: first 5 avg {early * 1e6:.0f}us, last 5 avg {late * 1e6:.0f}us")
        assert late < max(early * 20, 5e-3), "tracker cost grows with history"


class TestMemory:
    def test_detection_memory_is_bounded(self):
        series = build_series(1)[0]
        tracemalloc.start()
        DETECTOR.detect(series.bars, series.last_session)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print(
            f"\n  memory: {peak / 1024:.0f} KB peak for {len(series.bars)} bars "
            f"(~{peak * 500 / 1024 / 1024:.0f} MB for a 500-instrument batch)"
        )
        assert peak < 50 * 1024 * 1024

    def test_stored_geometry_is_small_enough_to_persist_per_session(self):
        """Geometry is stored per pattern rather than recomputed, so its size
        multiplies by universe times sessions. A megabyte per pattern would
        make the observation log impractical."""
        import json

        series = build_series(1)[0]
        found = DETECTOR.detect(series.bars, series.last_session)
        if not found:
            pytest.skip("no pattern to measure")
        payload = json.dumps(found[0].geometry.as_dict())
        print(f"\n  geometry payload: {len(payload)} bytes")
        assert len(payload) < 32_000
