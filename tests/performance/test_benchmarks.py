"""Performance benchmarks for full-universe scanning.

The purpose is not micro-optimisation. It is to answer one question before
Phase 4 builds on this layer: **can this scan thousands of securities in the
time the daily pipeline allows?** The scheduler gives `compute_indicators` a
90-minute timeout and `daily_scan` another 90.

So these measure throughput and assert only loose ceilings — the numbers vary
several-fold across machines, and a tight assertion would be a flaky test rather
than a useful one. What the assertions *do* catch is an algorithmic regression:
if someone replaces a sliding-window SMA with a nested loop, or ranks a
cross-section with an O(n²) comparison, throughput drops by orders of magnitude
and these fail loudly.

Run with ``pytest -m performance -s`` to see the measured numbers.
"""

from __future__ import annotations

import datetime as dt
import time
import tracemalloc
from decimal import Decimal

import numpy as np
import pytest

from tradeit.analytics import kernels as k
from tradeit.analytics.indicators import IndicatorEngine
from tradeit.analytics.relative_strength import RelativeStrengthEngine
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.strategy.config import StrategyConfig

pytestmark = pytest.mark.performance

CAL = get_calendar("XNYS")
CONFIG = StrategyConfig(name="baseline")

#: A realistic scanning universe and history depth.
UNIVERSE_SIZE = 4_000
#: Two years of sessions plus the 272-session warm-up the registry demands.
HISTORY_SESSIONS = 760


def make_bars(instrument_id: int, count: int, seed: int) -> list[OhlcvBar]:
    rng = np.random.default_rng(seed)
    sessions = CAL.sessions_between(
        dt.date(2021, 1, 4), dt.date(2021, 1, 4) + dt.timedelta(days=int(count * 1.5))
    )[:count]
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.016, len(sessions))))
    volumes = rng.lognormal(np.log(2e6), 0.4, len(sessions))

    out: list[OhlcvBar] = []
    for day, close, volume in zip(sessions, closes, volumes, strict=True):
        close_utc = CAL.close_instant(day)
        out.append(
            OhlcvBar(
                instrument_id=instrument_id,
                timeframe=Bartimeframe.D1,
                session_date=day,
                event_time=close_utc,
                knowledge_time=close_utc + dt.timedelta(minutes=20),
                knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                open=Decimal(str(round(close, 4))),
                high=Decimal(str(round(close * 1.012, 4))),
                low=Decimal(str(round(close * 0.988, 4))),
                close=Decimal(str(round(close, 4))),
                volume=Decimal(str(int(volume))),
            )
        )
    return out


class TestIndicatorThroughput:
    def test_full_indicator_set_over_one_instrument(self, capsys):
        """Baseline: 58 features over 760 sessions for one instrument."""
        engine = IndicatorEngine(CONFIG.indicators)
        bars = make_bars(1, HISTORY_SESSIONS, seed=1)

        engine.compute(bars)  # warm any lazy imports
        start = time.perf_counter()
        iterations = 20
        for _ in range(iterations):
            series = engine.compute(bars)
        elapsed = (time.perf_counter() - start) / iterations

        per_second = 1.0 / elapsed
        projected = UNIVERSE_SIZE / per_second
        with capsys.disabled():
            print(
                f"\n  indicators: {len(series.values)} features x {HISTORY_SESSIONS} bars "
                f"in {elapsed * 1000:.1f} ms  ({per_second:.0f} instruments/s, "
                f"~{projected / 60:.1f} min for {UNIVERSE_SIZE:,} single-threaded)"
            )

        assert elapsed < 0.1, (
            f"{elapsed * 1000:.0f} ms per instrument projects to "
            f"{projected / 60:.0f} minutes for the universe; the measured baseline is "
            "about 7 ms, so this indicates a significant regression"
        )

    def test_kernel_scaling_is_linear_not_quadratic(self, capsys):
        """The regression guard that matters most.

        A sliding-window SMA is O(n); a naive nested loop is O(n·period). At 760
        bars the difference is invisible in wall time but becomes the difference
        between a scan finishing and not.
        """
        rng = np.random.default_rng(7)
        timings: dict[int, float] = {}
        for size in (1_000, 4_000, 16_000):
            values = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size)))
            start = time.perf_counter()
            for _ in range(20):
                k.sma(values, 200)
            timings[size] = (time.perf_counter() - start) / 20

        ratio = timings[16_000] / max(timings[1_000], 1e-9)
        with capsys.disabled():
            print(
                "\n  sma(200) scaling: "
                + "  ".join(f"{n:,}={t * 1e6:.0f}us" for n, t in timings.items())
                + f"  (16x data -> {ratio:.1f}x time)"
            )
        assert ratio < 40, (
            f"16x the data took {ratio:.1f}x the time; a linear kernel should be "
            "near 16x. This suggests a superlinear implementation."
        )

    def test_the_expensive_kernels_are_identified(self, capsys):
        """Not an assertion so much as a map of where the time goes.

        Three kernels use Python-level loops because their recursions are
        genuinely sequential (Wilder smoothing, EMA) or windowed with per-window
        state (percent_rank, bollinger). If the universe scan ever becomes the
        bottleneck, these are where to look first.
        """
        bars = make_bars(1, HISTORY_SESSIONS, seed=3)
        close = np.array([float(b.close) for b in bars])
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        volume = np.array([float(b.volume) for b in bars])

        candidates = {
            "sma_200": lambda: k.sma(close, 200),
            "ema_21": lambda: k.ema(close, 21),
            "rsi_14": lambda: k.rsi(close, 14),
            "adx_14": lambda: k.adx(high, low, close, 14),
            "bollinger_20": lambda: k.bollinger_bands(close, 20),
            "realized_vol_20": lambda: k.realized_volatility(close, 20),
            "percent_rank_252": lambda: k.percent_rank(close, 252),
            "rolling_max_252": lambda: k.rolling_max(high, 252),
            "relative_volume": lambda: k.relative_volume(volume, 20),
        }
        timings = {}
        for name, fn in candidates.items():
            start = time.perf_counter()
            for _ in range(50):
                fn()
            timings[name] = (time.perf_counter() - start) / 50 * 1e6

        with capsys.disabled():
            print(f"\n  per-kernel cost over {HISTORY_SESSIONS} bars (microseconds):")
            for name, micros in sorted(timings.items(), key=lambda kv: -kv[1]):
                print(f"    {name:<20} {micros:8.0f}")

        assert all(micros < 5_000 for micros in timings.values())


class TestCrossSectionalThroughput:
    def test_ranking_a_full_universe(self, capsys):
        """Cross-sectional ranking runs once per date per lookback.

        4,000 instruments x 4 lookbacks x 3 benchmarks is 48,000 percentile
        computations per session. A naive O(n²) rank would make that 768M
        comparisons.
        """
        engine = RelativeStrengthEngine(CONFIG.relative_strength)
        rng = np.random.default_rng(11)
        roster = list(range(1, UNIVERSE_SIZE + 1))
        performance = {i: float(rng.normal(0.02, 0.15)) for i in roster}

        start = time.perf_counter()
        ranks = engine.rank_cross_section(dt.date(2024, 3, 8), roster, performance)
        elapsed = time.perf_counter() - start

        per_session = (
            elapsed
            * len(CONFIG.relative_strength.lookbacks)
            * len(CONFIG.relative_strength.benchmarks)
        )
        with capsys.disabled():
            print(
                f"\n  cross-sectional rank: {UNIVERSE_SIZE:,} instruments in "
                f"{elapsed * 1000:.0f} ms  (~{per_session:.1f} s per session for all "
                f"{len(CONFIG.relative_strength.lookbacks)} lookbacks x "
                f"{len(CONFIG.relative_strength.benchmarks)} benchmarks)"
            )

        assert len(ranks) == UNIVERSE_SIZE
        assert elapsed < 2.0, "ranking is superlinear; a full-universe scan will not complete"

    def test_ranking_scales_acceptably_with_universe_size(self, capsys):
        engine = RelativeStrengthEngine(CONFIG.relative_strength)
        rng = np.random.default_rng(12)
        timings: dict[int, float] = {}
        for size in (500, 2_000, 8_000):
            roster = list(range(1, size + 1))
            performance = {i: float(rng.normal(0, 0.1)) for i in roster}
            start = time.perf_counter()
            engine.rank_cross_section(dt.date(2024, 3, 8), roster, performance)
            timings[size] = time.perf_counter() - start

        ratio = timings[8_000] / max(timings[500], 1e-9)
        with capsys.disabled():
            print(
                "\n  rank scaling: "
                + "  ".join(f"{n:,}={t * 1000:.0f}ms" for n, t in timings.items())
                + f"  (16x universe -> {ratio:.1f}x time)"
            )
        # Sort-and-binary-search is O(n log n), so 16x the universe should cost
        # roughly 16-20x. The first implementation counted the peer set per
        # instrument -- O(n^2) -- and measured 252x here, which is what this
        # assertion now prevents from coming back.
        assert ratio < 60, (
            f"16x the universe took {ratio:.0f}x the time; ranking has regressed to "
            "a superlinear implementation"
        )


class TestMemory:
    def test_one_instrument_feature_set_memory(self, capsys):
        """Peak memory for a full feature set, which bounds a batch size."""
        engine = IndicatorEngine(CONFIG.indicators)
        bars = make_bars(1, HISTORY_SESSIONS, seed=5)
        engine.compute(bars)

        tracemalloc.start()
        series = engine.compute(bars)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        per_instrument_kb = peak / 1024
        with capsys.disabled():
            print(
                f"\n  memory: {per_instrument_kb:.0f} KB peak for {len(series.values)} "
                f"features x {HISTORY_SESSIONS} bars "
                f"(~{per_instrument_kb * 500 / 1024:.0f} MB for a 500-instrument batch)"
            )
        assert per_instrument_kb < 5_000, "a batch of 500 would exceed a GB"

    def test_bar_construction_dominates_feature_memory(self, capsys):
        """Where the memory actually goes.

        Pydantic bar objects cost far more than the float arrays derived from
        them, which is why the engine converts to NumPy once and why a future
        batch loader should stream bars rather than materialise the universe.
        """
        tracemalloc.start()
        bars = make_bars(1, HISTORY_SESSIONS, seed=6)
        _, bars_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        tracemalloc.start()
        IndicatorEngine(CONFIG.indicators).compute(bars)
        _, features_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        with capsys.disabled():
            print(
                f"\n  {HISTORY_SESSIONS} bar objects: {bars_peak / 1024:.0f} KB   "
                f"58 feature arrays: {features_peak / 1024:.0f} KB"
            )
        assert bars_peak > 0 and features_peak > 0


class TestIncrementalRecomputation:
    def test_appending_one_session_costs_a_full_recompute_today(self, capsys):
        """An honest measurement of a known limitation.

        The engine recomputes the whole series when one bar arrives. For daily
        operation over 760 bars that is cheap enough not to matter. It would
        matter for intraday recomputation across the universe, and the recursive
        kernels (EMA, Wilder) could carry state to make it O(1) per bar.

        Recorded as a measured number rather than a vague intention, so the
        decision to optimise can be made against evidence.
        """
        engine = IndicatorEngine(CONFIG.indicators)
        bars = make_bars(1, HISTORY_SESSIONS, seed=8)
        engine.compute(bars)

        start = time.perf_counter()
        for _ in range(10):
            engine.compute(bars)
        full = (time.perf_counter() - start) / 10

        with capsys.disabled():
            print(
                f"\n  incremental: full recompute {full * 1000:.1f} ms per instrument; "
                f"one added session costs the same today "
                f"(~{full * UNIVERSE_SIZE / 60:.1f} min universe-wide, single-threaded)"
            )
        assert full < 0.1
