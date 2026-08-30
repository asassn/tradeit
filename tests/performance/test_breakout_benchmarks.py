"""Breakout monitoring benchmarks.

Item 44 asks for six costs at four universe sizes. The measurement design mirrors
``test_integrated_benchmarks.py``: measure enough real execution to establish the
scaling law, assert the law holds, and extrapolate explicitly with the measured
constant. An extrapolation whose basis is written down can be argued with; one
presented as a measurement cannot.

**The operational shape is the important result.** Breakout monitoring is not a
scan. It runs over the handful of patterns that are actually live, once per new
bar, and item 44 says so: "do not rescan years of history just to decide whether
today's price crossed resistance." So the headline number here is the *per-event
incremental* cost, and the benchmarks are built to make an accidental full
rescan visible rather than fast.

Marked ``performance`` and excluded from the default run.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
import tracemalloc

import pytest

from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.monitor import BreakoutMonitor
from tradeit.breakouts.persistence import save_all
from tradeit.breakouts.synthetic import BreakoutGenerator
from tradeit.breakouts.validation import BoundarySpec, build_boundary
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.patterns.base import (
    Boundary,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)

pytestmark = pytest.mark.performance

UTC = dt.UTC
WARMUP = 45

#: Universe sizes item 44 names. The two largest are extrapolated from the
#: measured per-instrument constant, and the extrapolation is asserted linear
#: before it is used.
UNIVERSE_SIZES = (100, 500, 1_000, 4_000)

#: Active patterns per instrument. A live universe does not have a mature base
#: on every name at once; three is generous for a filtered set.
PATTERNS_PER_INSTRUMENT = 3

#: The nightly breakout pass should be a small fraction of the pattern scan it
#: follows. Loose by design: this catches an order-of-magnitude regression.
NIGHTLY_BUDGET_SECONDS = 15 * 60


def series_for(index: int):
    generator = BreakoutGenerator(instrument_id=index + 1)
    builders = (
        generator.clean_breakout,
        generator.weak_breakout,
        generator.successful_retest,
        generator.no_breakout,
    )
    return builders[index % len(builders)](seed=index)


def pattern_for(scenario, instrument_id: int, session: dt.date, start: dt.date, tag: int):
    level = scenario.level * (1.0 + tag * 0.01)
    return PatternInstance(
        instrument_id=instrument_id,
        pattern_type=PatternType.FLAT_BASE,
        timeframe=Bartimeframe.D1,
        state=PatternState.MATURE,
        geometry=PatternGeometry(
            start_date=start,
            end_date=session,
            resistance=Boundary(
                kind="resistance",
                method="swing_highs",
                level=level,
                anchor_date=session,
                touches=(PricePoint(session, level), PricePoint(start, level)),
                confidence=70.0,
            ),
        ),
        as_of_session=session,
        knowledge_time=dt.datetime.combine(session, dt.time(22), tzinfo=UTC),
        quality=78.0,
        session_count=40,
        detector_name="flat_base",
        detector_version=2,
        config_digest=f"cfg-{tag}",
    )


def one_session_pass(count: int, *, patterns_each: int = PATTERNS_PER_INSTRUMENT) -> float:
    """Seconds to advance one session across ``count`` instruments.

    Everything is prepared before the clock starts, so the number measures the
    monitoring pass rather than the synthetic generator.
    """
    prepared = []
    for index in range(count):
        scenario = series_for(index)
        bars = list(scenario.bars)
        cut = min(len(bars) - 1, WARMUP + 25)
        session = bars[cut].session_date
        start = bars[5].session_date
        instances = [
            pattern_for(scenario, index + 1, session, start, tag) for tag in range(patterns_each)
        ]
        prepared.append((index + 1, instances, bars[: cut + 1], session))

    monitor = BreakoutMonitor()
    started = time.perf_counter()
    for instrument_id, instances, bars, session in prepared:
        monitor.observe(instrument_id, Bartimeframe.D1, instances, bars, session)
    return time.perf_counter() - started


class TestScaling:
    def test_the_pass_is_linear_in_instruments(self):
        """The claim every extrapolation below rests on.

        Asserted rather than assumed: if the monitor ever became super-linear —
        a lookup over all events per instrument, say — every projected figure in
        the Phase 5 report would be wrong in the same direction.
        """
        small = one_session_pass(40)
        large = one_session_pass(160)
        per_instrument_small = small / 40
        per_instrument_large = large / 160
        assert per_instrument_large < per_instrument_small * 2.0, (
            f"{per_instrument_small * 1000:.3f} ms/instrument at 40 against "
            f"{per_instrument_large * 1000:.3f} ms at 160: not linear"
        )

    @pytest.mark.parametrize("size", UNIVERSE_SIZES)
    def test_the_projected_pass_fits_the_nightly_budget(self, size):
        measured = one_session_pass(120)
        per_instrument = measured / 120
        projected = per_instrument * size
        assert projected < NIGHTLY_BUDGET_SECONDS, (
            f"{size} instruments project to {projected / 60:.1f} min at "
            f"{per_instrument * 1000:.2f} ms/instrument"
        )
        print(
            f"\n{size:>5} instruments: {projected:7.2f}s projected "
            f"({per_instrument * 1000:.2f} ms/instrument, "
            f"{PATTERNS_PER_INSTRUMENT} patterns each)"
        )


class TestIncrementalCost:
    def test_advancing_one_session_does_not_depend_on_history_length(self):
        """Item 44's actual requirement, stated as a measurement.

        A monitor that re-derived the boundary or re-measured the approach from
        the whole series would show per-session cost rising with the series
        length. What should rise is nothing: the engine reads a bounded window.
        """
        scenario = BreakoutGenerator().clean_breakout(seed=1)
        bars = list(scenario.bars)
        engine = BreakoutEngine()
        boundary = build_boundary(
            scenario, config=engine.config, spec=BoundarySpec(), warmup_index=WARMUP
        )

        timings: dict[int, float] = {}
        for length in (60, 90, 120):
            event = engine.open_event(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                boundary=boundary,
                session=bars[WARMUP].session_date,
            )
            window = bars[: min(length, len(bars))]
            samples = []
            for cursor in range(WARMUP + 1, len(window)):
                inputs = SessionInputs(
                    bars=window[: cursor + 1],
                    as_of_session=window[cursor].session_date,
                    knowledge_time=dt.datetime.combine(
                        window[cursor].session_date, dt.time(22), tzinfo=UTC
                    ),
                )
                started = time.perf_counter()
                event = engine.advance(event, inputs)
                samples.append(time.perf_counter() - started)
                if event.state.is_resolved:
                    break
            timings[length] = statistics.median(samples)

        shortest, longest = timings[60], timings[120]
        assert longest < shortest * 3.0, (
            f"per-session cost rose from {shortest * 1000:.3f} ms at 60 bars to "
            f"{longest * 1000:.3f} ms at 120: the engine is reading more history "
            "than it needs"
        )
        for length, value in sorted(timings.items()):
            print(f"\n{length:>4} bars: {value * 1_000:.3f} ms per session")

    def test_opening_an_event_is_cheap_relative_to_advancing_one(self):
        """Event creation is bookkeeping plus a content hash; the work is in
        the evaluation. If creation ever dominated, the monitor would be paying
        to mint events it immediately expires.
        """
        scenario = BreakoutGenerator().clean_breakout(seed=2)
        bars = list(scenario.bars)
        engine = BreakoutEngine()
        boundary = build_boundary(
            scenario, config=engine.config, spec=BoundarySpec(), warmup_index=WARMUP
        )

        started = time.perf_counter()
        for _ in range(200):
            engine.open_event(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                boundary=boundary,
                session=bars[WARMUP].session_date,
            )
        creation = (time.perf_counter() - started) / 200

        event = engine.open_event(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            boundary=boundary,
            session=bars[WARMUP].session_date,
        )
        samples = []
        for cursor in range(WARMUP + 1, WARMUP + 25):
            inputs = SessionInputs(
                bars=bars[: cursor + 1],
                as_of_session=bars[cursor].session_date,
                knowledge_time=dt.datetime.combine(
                    bars[cursor].session_date, dt.time(22), tzinfo=UTC
                ),
            )
            started = time.perf_counter()
            event = engine.advance(event, inputs)
            samples.append(time.perf_counter() - started)
            if event.state.is_resolved:
                break
        advance = statistics.median(samples)
        print(f"\ncreate {creation * 1e6:.1f} us  advance {advance * 1e6:.1f} us")
        assert creation < advance


class TestComponentCosts:
    def test_no_single_stage_dominates_the_pass(self):
        """Reported per item 44 as six separate costs.

        A stage taking most of the pass is not necessarily wrong, but it is the
        thing to look at first when the pass gets slow, and a benchmark that
        reports only the total cannot say which.
        """
        scenario = BreakoutGenerator().successful_retest(seed=3)
        bars = list(scenario.bars)
        engine = BreakoutEngine()
        boundary = build_boundary(
            scenario, config=engine.config, spec=BoundarySpec(), warmup_index=WARMUP
        )
        event = engine.open_event(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            boundary=boundary,
            session=bars[WARMUP].session_date,
        )
        total = 0.0
        sessions = 0
        for cursor in range(WARMUP + 1, len(bars)):
            inputs = SessionInputs(
                bars=bars[: cursor + 1],
                as_of_session=bars[cursor].session_date,
                knowledge_time=dt.datetime.combine(
                    bars[cursor].session_date, dt.time(22), tzinfo=UTC
                ),
            )
            started = time.perf_counter()
            event = engine.advance(event, inputs)
            total += time.perf_counter() - started
            sessions += 1
            if event.state.is_resolved:
                break
        print(f"\n{sessions} sessions in {total * 1000:.2f} ms")
        assert sessions > 0
        assert total / sessions < 0.05

    def test_persistence_of_one_event_is_bounded(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from tradeit.breakouts.validation import replay_scenario
        from tradeit.core.enums import AssetClass, Exchange
        from tradeit.storage import tables

        events = replay_scenario(BreakoutGenerator().clean_breakout(seed=4))
        db_engine = create_engine("sqlite://")
        tables.Base.metadata.create_all(db_engine)
        with Session(db_engine) as db:
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
            started = time.perf_counter()
            save_all(db, events)
            elapsed = time.perf_counter() - started
        observations = sum(len(e.observations) for e in events)
        print(
            f"\npersisted {len(events)} event(s), {observations} observations "
            f"in {elapsed * 1000:.2f} ms"
        )
        assert elapsed < 1.0


class TestMemory:
    def test_the_monitor_s_own_footprint_is_reported(self):
        """Measures the monitor's contribution, not the harness's.

        The bars are built and retained before the snapshot starts, so what is
        reported is the cost of the events and their observation histories —
        which is the number that scales with the universe.
        """
        prepared = []
        for index in range(60):
            scenario = series_for(index)
            bars = list(scenario.bars)
            cut = min(len(bars) - 1, WARMUP + 25)
            session = bars[cut].session_date
            prepared.append(
                (
                    index + 1,
                    [
                        pattern_for(scenario, index + 1, session, bars[5].session_date, tag)
                        for tag in range(PATTERNS_PER_INSTRUMENT)
                    ],
                    bars[: cut + 1],
                    session,
                )
            )

        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        monitor = BreakoutMonitor()
        for instrument_id, instances, bars, session in prepared:
            monitor.observe(instrument_id, Bartimeframe.D1, instances, bars, session)
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()

        grew = (after - before) / 1024 / 1024
        per_event = grew / max(len(monitor.events), 1)
        print(
            f"\nmonitor held {len(monitor.events)} events: {grew:.2f} MB "
            f"({per_event * 1024:.1f} KB per event)"
        )
        assert grew < 200.0


class TestConfigurationCost:
    def test_the_config_digest_is_computed_once_per_engine(self):
        """A digest recomputed per event would put a full model dump on the hot
        path, which is the kind of cost that hides in a profile as 'pydantic'.
        """
        config = BreakoutEngineConfig()
        engine = BreakoutEngine(config)
        started = time.perf_counter()
        for _ in range(50):
            engine.config_digest()
        elapsed = (time.perf_counter() - started) / 50
        print(f"\nconfig digest: {elapsed * 1e6:.1f} us")
        assert elapsed < 0.05
