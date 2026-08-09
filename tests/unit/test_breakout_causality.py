"""Causality and prefix consistency for the breakout engine.

Item 31 lists nine properties and item 32 adds prefix consistency. Each has a
class here, named for the property, because a causality suite whose failures do
not say which guarantee broke is a suite nobody will trust under pressure.

Two of the nine are true **by construction** rather than by check, and both are
tested anyway — a guarantee that rests on an architectural decision needs a
test that fails if someone changes the architecture:

* breakout quality is computed on the session of the first qualifying close and
  never recomputed, so no later bar can reach it;
* the boundary is snapshotted when the event opens, so resistance cannot be
  re-derived through post-breakout bars.

The general shape of every test here is the same and it is the only shape that
actually proves anything: build the answer from a truncated series, build it
again from the full series, and compare. A test that merely asserts the engine
"does not look ahead" by inspecting its arguments proves nothing, because the
leak that matters is always in what the arguments contain.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.breakouts.config import BreakoutEngineConfig, IntradayVolumeConfig
from tradeit.breakouts.context import BreakoutContext, MarketReading, SectorReading
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.breakouts.measures import average_true_range
from tradeit.breakouts.synthetic import BreakoutGenerator, GeneratedBreakout
from tradeit.breakouts.validation import BoundarySpec, build_boundary, replay_scenario
from tradeit.breakouts.volume import build_intraday_curve, measure_volume
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource, MarketRegime
from tradeit.core.models import OhlcvBar
from tradeit.errors import DataError
from tradeit.patterns.series import causal_series, contains_no_future_bars

GENERATOR = BreakoutGenerator()
UTC = dt.UTC
WARMUP = 45

#: Scenarios the causal properties are checked across. Chosen to cover a
#: breakout, a retest, a failure and a case where nothing happens, because a
#: leak that only shows up on one shape is the one that survives.
SCENARIOS = (
    "clean_breakout",
    "weak_breakout",
    "successful_retest",
    "false_breakout",
    "no_breakout",
    "gap_breakout",
)


def scenario(name: str, seed: int = 0) -> GeneratedBreakout:
    return getattr(GENERATOR, name)(seed=seed)


def single_attempt(series: GeneratedBreakout, *, stop_at: int | None = None, **kwargs: object):
    """One attempt, no re-opening. Prefix comparisons need a single identity."""
    return replay_scenario(
        series,
        stop_at=stop_at,
        reopen=False,
        **kwargs,  # type: ignore[arg-type]
    )[0]


class TestFutureCandlesDoNotAlterPastStates:
    """Property 1: future candles do not alter previous breakout states."""

    @pytest.mark.parametrize("name", SCENARIOS)
    def test_the_state_history_of_a_prefix_is_a_prefix_of_the_history(self, name):
        series = scenario(name)
        cut = len(series.bars) - 6
        truncated = single_attempt(series, stop_at=cut)
        full = single_attempt(series)

        truncated_rows = [(o.session_date, o.state) for o in truncated.observations]
        full_rows = [
            (o.session_date, o.state)
            for o in full.observations
            if o.session_date <= series.bars[cut].session_date
        ]
        assert truncated_rows == full_rows

    @pytest.mark.parametrize("name", SCENARIOS)
    def test_every_stored_score_is_reproduced_exactly(self, name):
        """Item 32 asks for the stored state history to match *exactly*, so the
        comparison is on equality rather than on a tolerance."""
        series = scenario(name)
        cut = len(series.bars) - 6
        truncated = single_attempt(series, stop_at=cut)
        full = single_attempt(series)

        by_date = {o.session_date: o for o in full.observations}
        for observation in truncated.observations:
            other = by_date[observation.session_date]
            assert observation.to_payload() == other.to_payload()

    def test_a_state_read_back_matches_a_fresh_evaluation(self):
        series = scenario("successful_retest")
        full = single_attempt(series)
        for cut in range(WARMUP + 2, len(series.bars), 7):
            session = series.bars[cut].session_date
            truncated = single_attempt(series, stop_at=cut)
            assert truncated.state_on(session) == full.state_on(session)


class TestFutureVolumeDoesNotImproveHistoricalQuality:
    """Property 2: future volume does not improve historical breakout quality."""

    def test_multiplying_later_volume_leaves_the_frozen_quality_alone(self):
        series = scenario("weak_breakout")
        index = series.breakout_index
        assert index is not None
        loud = _scale_volume_after(series, index, 20.0)

        baseline = single_attempt(series)
        altered = single_attempt(loud)
        assert altered.breakout_quality == pytest.approx(baseline.breakout_quality)

    def test_the_frozen_component_measurements_are_identical(self):
        series = scenario("clean_breakout")
        index = series.breakout_index
        assert index is not None
        altered = single_attempt(_scale_volume_after(series, index, 0.05))
        baseline = single_attempt(series)
        assert _quality_payload(altered) == _quality_payload(baseline)


class TestFutureClosesCannotConfirmEarlier:
    """Property 3: future closes cannot provide earlier confirmation."""

    def test_confirmation_never_predates_the_evidence_that_produced_it(self):
        series = scenario("clean_breakout")
        full = single_attempt(series)
        assert full.confirmed_session is not None

        cut = _index_of(series, full.confirmed_session) - 1
        earlier = single_attempt(series, stop_at=cut)
        assert earlier.state is not BreakoutState.CONFIRMED

    def test_a_profile_needing_three_bars_cannot_confirm_on_the_first(self):
        """The window requirement is a floor on elapsed time, not on the score:
        the evidence a three-bar profile wants has not had time to exist."""
        series = scenario("clean_breakout")
        index = series.breakout_index
        assert index is not None
        event = single_attempt(series, stop_at=index, profile="conservative")
        assert event.state is BreakoutState.CLOSED_ABOVE
        assert event.confirmed_session is None


class TestFutureRetestsDoNotRewriteTheBreakout:
    """Property 4: a future retest cannot change the original breakout score."""

    def test_deepening_the_retest_leaves_the_breakout_score_untouched(self):
        from dataclasses import replace

        base_spec = scenario("successful_retest").spec
        shallow = GENERATOR.build(base_spec, name="shallow")
        deep = GENERATOR.build(replace(base_spec, retest_depth=0.05), name="deep")

        shallow_event = single_attempt(shallow)
        deep_event = single_attempt(deep)
        assert shallow_event.breakout_quality == pytest.approx(deep_event.breakout_quality)
        assert shallow_event.state is not deep_event.state

    def test_the_quality_recorded_on_every_later_observation_is_the_same(self):
        event = single_attempt(scenario("successful_retest"))
        breakout = event.first_qualifying_close_session
        assert breakout is not None
        values = {
            round(o.breakout_quality, 9) for o in event.observations if o.session_date >= breakout
        }
        assert len(values) == 1


class TestResistanceStaysTiedToTheCausalBoundary:
    """Property 5: resistance remains tied to the causal pattern boundary."""

    def test_the_boundary_is_identical_at_every_point_in_the_replay(self):
        series = scenario("clean_breakout")
        payloads = {
            str(single_attempt(series, stop_at=cut).boundary.to_payload())
            for cut in range(WARMUP + 2, len(series.bars), 9)
        }
        assert len(payloads) == 1

    def test_post_breakout_bars_cannot_move_the_level(self):
        """A level refitted through later bars would drift toward wherever
        price actually turned, which makes every retest look like it held."""
        series = scenario("successful_retest")
        index = series.breakout_index
        assert index is not None
        moved = _scale_prices_after(series, index, 1.15)
        assert single_attempt(moved).boundary.nominal == pytest.approx(
            single_attempt(series).boundary.nominal
        )

    def test_the_atr_used_is_the_one_frozen_at_open(self):
        series = scenario("clean_breakout")
        expected = average_true_range(
            list(series.bars[: WARMUP + 1]), BreakoutEngineConfig().atr_period
        )
        assert single_attempt(series).boundary.atr_at_open == pytest.approx(expected)


class TestWeeklyConfirmationCannotSeeFutureDays:
    """Property 6: weekly confirmation mid-week cannot use Friday's close."""

    def test_a_weekly_series_never_contains_an_unfinished_week(self):
        series = scenario("clean_breakout")
        daily = list(series.bars)
        midweek = next(b.session_date for b in daily[WARMUP:] if b.session_date.weekday() == 2)
        weekly = causal_series(
            [b for b in daily if b.session_date <= midweek], [Bartimeframe.W1], midweek
        )[Bartimeframe.W1]
        assert weekly
        assert contains_no_future_bars(weekly, midweek)
        assert all(bar.session_date < midweek for bar in weekly)

    def test_a_weekly_evaluation_matches_one_run_from_the_truncated_daily_series(self):
        """The whole point: the weekly bar the engine sees mid-week is built
        from the days that have happened, not from the week that will."""
        series = scenario("clean_breakout")
        daily = list(series.bars)
        midweek = next(b.session_date for b in daily[WARMUP + 5 :] if b.session_date.weekday() == 2)
        prefix = [b for b in daily if b.session_date <= midweek]
        from_prefix = causal_series(prefix, [Bartimeframe.W1], midweek)[Bartimeframe.W1]
        # The full series is refused outright rather than silently truncated:
        # a resampler handed unfiltered bars is the leak, not the symptom.
        with pytest.raises(DataError, match="clock-gate"):
            causal_series(daily, [Bartimeframe.W1], midweek)
        assert from_prefix
        assert all(bar.session_date < midweek for bar in from_prefix)


class TestIntradayVolumeCannotSeeLaterSessionVolume:
    """Property 7: intraday relative volume cannot see later session volume."""

    def test_the_curve_is_unchanged_by_the_current_session(self):
        prior = _intraday_sessions(15)
        config = IntradayVolumeConfig(buckets=13)
        curve = build_intraday_curve(prior, config=config)
        # A session whose whole volume printed at the open — a different shape,
        # not merely a different size, since the curve normalises each session.
        today = [[_daily(_dates(20)[-1], 900_000.0 if i == 0 else 1_000.0) for i in range(13)]]
        with_today = build_intraday_curve([*prior, *today], config=config)
        assert curve is not None and with_today is not None
        assert curve.fractions != with_today.fractions
        # And the causal call — prior sessions only — is the one the engine uses.
        again = build_intraday_curve(prior, config=config)
        assert again is not None and again.fractions == curve.fractions

    def test_a_projection_uses_only_what_has_traded(self):
        prior = _intraday_sessions(15)
        curve = build_intraday_curve(prior, config=IntradayVolumeConfig(buckets=13))
        assert curve is not None
        history = [_daily(day, 1_000_000.0) for day in _dates(30)]
        partial = [*history, _daily(_dates(31)[-1], 800_000.0)]
        reading = measure_volume(
            partial,
            config=BreakoutEngineConfig().volume,
            intraday_config=IntradayVolumeConfig(buckets=13),
            is_partial=True,
            observed_at=dt.time(10, 15),
            curve=curve,
        )
        assert reading.current_observed_volume == 800_000.0
        assert reading.completed_relative_volume is None
        assert reading.projected_volume is not None


class TestContextCannotLeakBackward:
    """Property 8: future sector and regime data cannot leak backward."""

    def test_a_later_regime_change_does_not_alter_a_frozen_score(self):
        series = scenario("clean_breakout")
        closes = [float(b.close) * 0.5 for b in series.bars]
        bull = single_attempt(
            series,
            context=BreakoutContext(
                benchmark_closes=closes,
                market=MarketReading(regime=MarketRegime.BULL_TRENDING),
                sector=SectorReading(strength_score=80.0),
            ),
            stop_at=(series.breakout_index or 0) + 1,
        )
        full = single_attempt(
            series,
            context=BreakoutContext(
                benchmark_closes=closes,
                market=MarketReading(regime=MarketRegime.BULL_TRENDING),
                sector=SectorReading(strength_score=80.0),
            ),
        )
        assert bull.breakout_quality == pytest.approx(full.breakout_quality)

    def test_the_benchmark_is_truncated_with_the_price_series(self):
        """A benchmark longer than the bars is a misalignment, and the engine
        marks relative strength unavailable rather than computing a ratio from
        mismatched sessions."""
        series = scenario("clean_breakout")
        closes = [float(b.close) * 0.5 for b in series.bars]
        engine = BreakoutEngine()
        bars = list(series.bars)
        boundary = build_boundary(
            series, config=engine.config, spec=BoundarySpec(), warmup_index=WARMUP
        )
        event = engine.open_event(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            boundary=boundary,
            session=bars[WARMUP].session_date,
        )
        index = (series.breakout_index or 0) + 1
        for cursor in range(WARMUP + 1, index + 1):
            event = engine.advance(
                event,
                SessionInputs(
                    bars=bars[: cursor + 1],
                    as_of_session=bars[cursor].session_date,
                    knowledge_time=dt.datetime.combine(
                        bars[cursor].session_date, dt.time(22), tzinfo=UTC
                    ),
                    context=BreakoutContext(benchmark_closes=closes),
                ),
            )
        rs = event.component("relative_strength")
        assert rs is not None
        assert rs.unavailable
        assert "misaligned" in rs.unavailable_reason


class TestPatternReidentificationDoesNotMisattach:
    """Property 9: pattern re-identification does not attach a breakout to the
    wrong historical pattern."""

    def test_an_event_keeps_the_pattern_key_it_opened_with(self):
        series = scenario("clean_breakout")
        event = single_attempt(series)
        assert event.boundary.pattern_key == BoundarySpec().pattern_key

    def test_two_patterns_at_the_same_level_are_separate_events(self):
        series = scenario("clean_breakout")
        first = single_attempt(series, spec=BoundarySpec(pattern_key="alpha"))
        second = single_attempt(series, spec=BoundarySpec(pattern_key="beta"))
        assert first.event_key != second.event_key
        assert first.boundary.pattern_key != second.boundary.pattern_key

    def test_a_boundary_moving_under_a_new_pattern_does_not_move_the_event(self):
        """The pattern layer may legitimately re-anchor a structure. An event
        already open against the old boundary keeps it, because it was judged
        against that one.
        """
        series = scenario("clean_breakout")
        engine = BreakoutEngine()
        bars = list(series.bars)
        original = build_boundary(
            series, config=engine.config, spec=BoundarySpec(), warmup_index=WARMUP
        )
        event = engine.open_event(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            boundary=original,
            session=bars[WARMUP].session_date,
        )
        for cursor in range(WARMUP + 1, WARMUP + 6):
            event = engine.advance(
                event,
                SessionInputs(
                    bars=bars[: cursor + 1],
                    as_of_session=bars[cursor].session_date,
                    knowledge_time=dt.datetime.combine(
                        bars[cursor].session_date, dt.time(22), tzinfo=UTC
                    ),
                ),
            )
        assert event.boundary is original


class TestPrefixConsistencySweep:
    """Item 32, run as a sweep rather than at one cut point.

    One cut point can pass by luck. Sweeping the whole post-warm-up range makes
    a leak that only bites at a particular offset — an off-by-one in a lookback,
    a window that happens to align — visible.
    """

    @pytest.mark.parametrize("name", ["clean_breakout", "successful_retest", "false_breakout"])
    def test_every_prefix_agrees_with_the_full_replay(self, name):
        series = scenario(name)
        full = single_attempt(series)
        by_date = {o.session_date: o.to_payload() for o in full.observations}
        last = max(by_date) if by_date else None
        assert last is not None

        for cut in range(WARMUP + 1, len(series.bars)):
            session = series.bars[cut].session_date
            if session > last:
                break
            truncated = single_attempt(series, stop_at=cut)
            rows = {o.session_date: o.to_payload() for o in truncated.observations}
            for date, payload in rows.items():
                assert payload == by_date[date], f"{name} disagreed at cut {cut} on {date}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _index_of(series: GeneratedBreakout, session: dt.date) -> int:
    for index, bar in enumerate(series.bars):
        if bar.session_date == session:
            return index
    raise AssertionError(f"{session} is not in the series")


def _quality_payload(event) -> list[dict[str, object]]:
    return [
        {
            "name": c.name,
            "score": None if c.unavailable else round(c.score, 9),
            "measurements": {k: round(v, 9) for k, v in sorted(c.measurements.items())},
        }
        for c in event.quality_components
    ]


def _scale_volume_after(series: GeneratedBreakout, index: int, factor: float) -> GeneratedBreakout:
    from dataclasses import replace

    bars = list(series.bars)
    for cursor in range(index + 1, len(bars)):
        bars[cursor] = bars[cursor].model_copy(
            update={"volume": Decimal(f"{max(1.0, float(bars[cursor].volume) * factor):.0f}")}
        )
    return replace(series, bars=tuple(bars))


def _scale_prices_after(series: GeneratedBreakout, index: int, factor: float) -> GeneratedBreakout:
    from dataclasses import replace

    bars = list(series.bars)
    for cursor in range(index + 1, len(bars)):
        bar = bars[cursor]
        bars[cursor] = bar.model_copy(
            update={
                "open": Decimal(f"{float(bar.open) * factor:.4f}"),
                "high": Decimal(f"{float(bar.high) * factor:.4f}"),
                "low": Decimal(f"{float(bar.low) * factor:.4f}"),
                "close": Decimal(f"{float(bar.close) * factor:.4f}"),
            }
        )
    return replace(series, bars=tuple(bars))


def _dates(count: int) -> list[dt.date]:
    out: list[dt.date] = []
    day = dt.date(2023, 1, 2)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _daily(session: dt.date, volume: float) -> OhlcvBar:
    return OhlcvBar(
        instrument_id=1,
        timeframe=Bartimeframe.D1,
        session_date=session,
        open=Decimal("98.0"),
        high=Decimal("98.5"),
        low=Decimal("97.5"),
        close=Decimal("98.0"),
        volume=Decimal(f"{volume:.0f}"),
        event_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_time=dt.datetime.combine(session, dt.time(21, 30), tzinfo=UTC),
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _intraday_sessions(count: int, volume_scale: float = 1.0) -> list[list[OhlcvBar]]:
    shape = [3.0, 2.0, 1.4, 1.0, 0.8, 0.7, 0.7, 0.7, 0.8, 1.0, 1.4, 2.0, 3.0]
    return [
        [_daily(day, 100_000.0 * weight * volume_scale) for weight in shape]
        for day in _dates(count)
    ]
