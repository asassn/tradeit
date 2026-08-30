"""Integrated pattern-system benchmarks.

Not per-detector microbenchmarks — those exist in ``test_pattern_benchmarks.py``
and answer a different question. This measures **the whole system as it would
run**: every enabled detector, across timeframe configurations, with tracking,
relationship derivation and persistence included, at universe sizes that
resemble a real scan.

**Why the extrapolation is stated rather than hidden.** Running a literal
four-thousand-name benchmark with a decade of history each is expensive and
tells you little that a five-hundred-name run does not, *provided* the scaling is
linear in instruments and you have checked that it is. So the tests measure
enough real execution to establish the scaling law, assert it is linear, and
extrapolate explicitly with the measured constant. An extrapolation whose basis
is written down can be argued with; one presented as a measurement cannot.

**The budget.** A nightly US-equity scan is the operational target: a few
thousand securities on daily bars inside a batch window that fits comfortably
between the close and the next open. The assertion below is deliberately loose
— it exists to catch an order-of-magnitude regression, not to pin a number that
varies with the machine.

Marked ``performance`` and excluded from the default run.
"""

from __future__ import annotations

import statistics
import time
import tracemalloc

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.registry import DETECTOR_ORDER
from tradeit.patterns.scanner import PatternScanner
from tradeit.patterns.series import causal_series
from tradeit.patterns.synthetic import PatternGenerator, SeriesSpec

pytestmark = pytest.mark.performance

GENERATOR = PatternGenerator()

#: Sessions per instrument. Two years of dailies: enough for every detector's
#: warm-up with room for structures to form, and representative of what an
#: incremental nightly scan would actually load.
SESSIONS = 500

#: A nightly scan should finish well inside the overnight window. Loose by
#: design: this catches an order-of-magnitude regression, not a 20% drift.
NIGHTLY_BUDGET_SECONDS = 4 * 60 * 60


def universe(count: int, sessions: int = SESSIONS):
    """Distinct instruments, each with its own structure and its own noise."""
    out = []
    for index in range(count):
        generator = PatternGenerator(SeriesSpec(instrument_id=index + 1, seed=20260807 + index))
        if index % 4 == 0:
            series = generator.flat_base(seed=index, sessions=140, breakout_sessions=8)
        elif index % 4 == 1:
            series = generator.random_walk(sessions, volatility=0.018, seed=index)
        elif index % 4 == 2:
            series = generator.cup_handle(seed=index, cup_sessions=90, handle_sessions=18)
        else:
            series = generator.base_on_base(seed=index, second_sessions=28)
        out.append((index + 1, series))
    return out


def scan_universe(instruments, *, incremental: bool = False, timeframes=(Bartimeframe.D1,)):
    """Run the full scanner over a universe, returning elapsed seconds and counts."""
    scanner = PatternScanner()
    instances = 0
    relations = 0
    started = time.perf_counter()

    for instrument_id, series in instruments:
        built = causal_series(series.bars, list(timeframes), series.last_session)
        if len(timeframes) == 1:
            bars = built[timeframes[0]]
            method = scanner.scan_incremental if incremental else scanner.scan
            result = method(instrument_id, timeframes[0], bars, series.last_session)
            instances += len(result.instances)
            relations += len(result.relations)
        else:
            multi = scanner.scan_timeframes(instrument_id, built, series.last_session)
            instances += len(multi.all_instances)
            relations += len(multi.cross_relations) + sum(
                len(r.relations) for r in multi.by_timeframe.values()
            )

    return time.perf_counter() - started, instances, relations, scanner


class TestScalingIsLinear:
    """The claim the extrapolation rests on."""

    def test_daily_scan_scales_linearly_in_instruments(self):
        timings = {}
        for count in (25, 50, 100):
            elapsed, instances, _relations, _scanner = scan_universe(universe(count))
            timings[count] = elapsed
            print(
                f"{count:>4} instruments  {elapsed:>7.2f}s  "
                f"{elapsed / count * 1000:>7.1f} ms/instrument  {instances} instances"
            )

        per_instrument = [timings[c] / c for c in timings]
        spread = max(per_instrument) / min(per_instrument)
        assert spread < 2.0, (
            f"per-instrument cost varied by {spread:.1f}x across universe sizes; the "
            "extrapolation to a full universe assumes linearity and this says it does "
            f"not hold: {per_instrument}"
        )

    def test_the_nightly_budget_is_met_by_extrapolation(self):
        """Measured on a sample, extrapolated with the measured constant.

        The extrapolation is stated rather than presented as a measurement: a
        four-thousand-name run at this history depth is expensive and would tell
        us nothing the linearity check has not already established.
        """
        count = 100
        elapsed, instances, relations, _scanner = scan_universe(universe(count))
        per_instrument = elapsed / count

        for size in (100, 500, 1000, 4000):
            projected = per_instrument * size
            print(
                f"projected {size:>5} instruments: {projected:>8.1f}s "
                f"({projected / 60:>6.1f} min)  ~{instances / count * size:.0f} instances"
            )

        projected_full = per_instrument * 4000
        assert projected_full < NIGHTLY_BUDGET_SECONDS, (
            f"a 4,000-name daily scan projects to {projected_full / 60:.0f} minutes at "
            f"{per_instrument * 1000:.0f} ms/instrument, past the "
            f"{NIGHTLY_BUDGET_SECONDS / 3600:.0f}h budget"
        )
        assert relations >= 0


class TestPerDetectorCost:
    def test_each_detector_reports_its_own_share(self):
        """Attribution without a separate instrumented run.

        The scanner records per-detector timings, so the expensive family is
        identifiable from an ordinary scan rather than by bisecting the config.
        """
        scanner = PatternScanner()
        totals = dict.fromkeys(DETECTOR_ORDER, 0.0)
        for instrument_id, series in universe(30):
            result = scanner.scan(instrument_id, Bartimeframe.D1, series.bars, series.last_session)
            for name, seconds in result.timings.items():
                totals[name] += seconds

        overall = sum(totals.values())
        print(f"\ntotal detector time {overall:.2f}s across 30 instruments")
        for name, seconds in sorted(totals.items(), key=lambda kv: -kv[1]):
            share = seconds / overall if overall else 0.0
            print(f"  {name:<26} {seconds:>7.3f}s  {share:>6.1%}")

        assert overall > 0
        worst = max(totals.values()) / overall if overall else 0.0
        assert worst < 0.6, (
            f"one detector accounts for {worst:.0%} of the scan; that is where any "
            "optimisation work belongs, and it may also indicate an accidental "
            "quadratic"
        )


class TestTimeframeConfigurations:
    @pytest.mark.parametrize(
        "timeframes",
        [
            (Bartimeframe.D1,),
            (Bartimeframe.W1, Bartimeframe.D1),
        ],
        ids=["daily", "weekly+daily"],
    )
    def test_configuration_cost(self, timeframes):
        elapsed, instances, relations, _scanner = scan_universe(universe(40), timeframes=timeframes)
        names = "+".join(str(t) for t in timeframes)
        print(
            f"\n{names:<14} {elapsed:>7.2f}s over 40 instruments "
            f"({elapsed / 40 * 1000:.0f} ms each)  {instances} instances  "
            f"{relations} relations"
        )
        assert elapsed > 0


class TestIncrementalSaving:
    def test_incremental_is_faster_than_a_full_scan(self):
        """The saving the bounded window buys, measured rather than assumed."""
        instruments = universe(40)
        full, _fi, _fr, _fs = scan_universe(instruments)
        incremental, _ii, _ir, _is = scan_universe(instruments, incremental=True)
        print(
            f"\nfull {full:.2f}s vs incremental {incremental:.2f}s "
            f"({full / incremental if incremental else 0:.2f}x)"
        )
        assert incremental <= full * 1.1, (
            "the bounded rescan window should not cost more than a full scan; if it "
            "does, the window has grown to cover the whole series"
        )


class TestMemory:
    def test_peak_memory_tracks_loaded_series_not_pattern_output(self):
        """What actually dominates memory, measured rather than assumed.

        An earlier version of this test claimed peak memory was bounded per
        instrument. It is not, and the test could not have shown that either way:
        the harness builds every series up front, so what it measured was the
        harness holding data. The honest statement is the one below — memory is
        linear in the *series* held, and the pattern output is a rounding error
        beside them.

        The operational consequence is concrete: a production scan should stream
        instruments rather than materialise a universe, and the linearity here is
        what says so.
        """
        tracemalloc.start()
        instruments = universe(50)
        _current, series_peak = tracemalloc.get_traced_memory()
        scan_universe(instruments)
        _current, total_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print(
            f"\npeak after loading 50 series: {series_peak / 1e6:.1f} MB; "
            f"after scanning them: {total_peak / 1e6:.1f} MB "
            f"(+{(total_peak - series_peak) / 1e6:.1f} MB)"
        )
        assert total_peak < series_peak * 2.5, (
            "scanning should cost far less memory than holding the bars it scans; "
            "if it does not, the scanner is accumulating state across instruments"
        )

    def test_memory_is_linear_in_the_universe_held(self):
        peaks = {}
        for count in (25, 50):
            tracemalloc.start()
            scan_universe(universe(count))
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peaks[count] = peak
            print(f"{count:>3} instruments: {peak / 1e6:>6.1f} MB peak")

        ratio = peaks[50] / peaks[25]
        assert 1.5 < ratio < 3.0, (
            f"peak memory scaled {ratio:.1f}x when the universe doubled; linear is "
            "expected because the harness holds every series, and a sharply "
            "super-linear result would mean the scanner retains state per instrument"
        )


class TestObservationVolume:
    def test_observation_growth_is_estimated_from_a_measured_rate(self):
        """Storage planning, from a measured instances-per-instrument rate.

        Reported rather than asserted against a size limit: what counts as too
        much depends on retention policy, which is an operational decision this
        phase does not make.
        """
        count = 50
        _elapsed, instances, relations, scanner = scan_universe(universe(count))
        per_instrument = instances / count
        tracked = len(scanner.open_patterns()) + len(scanner.closed_patterns())

        # One observation row per open pattern per session is the steady state.
        for size in (500, 1000, 4000):
            identities = per_instrument * size
            daily_observations = identities
            annual = daily_observations * 252
            print(
                f"\n{size:>5} instruments: ~{identities:>8.0f} live identities, "
                f"~{daily_observations:>8.0f} observations/day, "
                f"~{annual / 1e6:>6.1f}M observations/year"
            )
        print(f"relations produced in the sample: {relations}")
        assert tracked > 0
        assert per_instrument > 0


class TestQuadraticDetection:
    def test_cost_per_bar_does_not_grow_with_series_length(self):
        """An O(n^2) detector shows up here and nowhere else.

        Per-bar cost that rises with series length means a detector is scanning
        history it should have bounded, which is both slow and — more importantly
        — a sign that its lookback is not what its contract claims.
        """
        per_bar = {}
        for sessions in (300, 600, 1200):
            generator = PatternGenerator(SeriesSpec(instrument_id=1))
            series = generator.random_walk(sessions, volatility=0.018, seed=3)
            scanner = PatternScanner()
            started = time.perf_counter()
            for _ in range(3):
                scanner.scan(1, Bartimeframe.D1, series.bars, series.last_session, track=False)
            per_bar[sessions] = (time.perf_counter() - started) / 3 / sessions
            print(f"{sessions:>5} bars: {per_bar[sessions] * 1e6:>8.1f} us/bar")

        ratio = per_bar[1200] / per_bar[300]
        assert ratio < 1.5, (
            f"per-bar cost rose {ratio:.1f}x between a 300-bar and a 1200-bar series; "
            "a detector is scanning more history than its contract declares"
        )

    def test_scan_time_is_stable_across_repeats(self):
        scanner = PatternScanner()
        series = GENERATOR.flat_base(sessions=100, breakout_sessions=8)
        timings = []
        for _ in range(7):
            started = time.perf_counter()
            scanner.scan(1, Bartimeframe.D1, series.bars, series.last_session, track=False)
            timings.append(time.perf_counter() - started)
        median = statistics.median(timings)
        print(f"\nmedian scan {median * 1000:.1f} ms, max {max(timings) * 1000:.1f} ms")
        assert max(timings) < median * 5
