"""Controlled breakout scenarios, and the adversarial ones.

Synthetic series are the only corpus Phase 5 has: real-market validation is
still blocked, and ADR-0018's limits apply here exactly as they did to the
pattern detectors. What synthetic data can establish is that the engine responds
to the thing it claims to respond to, and that the response is monotone in the
parameter that is varied. What it *cannot* establish is any statement about the
market, and no number produced from this module should be read as one.

**Every scenario is a parameterisation of one builder.** The alternative — a
hand-written series per scenario — produces corpora whose differences are
accidental. Here a "low-volume breakout" differs from a "clean breakout" in
exactly one field, so a difference in the engine's output is attributable to
that field and to nothing else. That is what makes the perturbation and
monotonicity tests meaningful rather than decorative.

**The boundary is constructed, not detected.** Each scenario returns the level
it built the series around. Feeding the engine a detector's opinion of where
resistance is would conflate two questions — did the detector find the level,
and did the engine judge the break correctly — and a failure in either would
look like a failure in both.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

import numpy as np

from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar

UTC = dt.UTC
_START = dt.date(2021, 1, 4)


@dataclass(frozen=True, slots=True)
class BreakoutSpec:
    """Control parameters for one synthetic breakout.

    Every field is a knob the tests turn one at a time. Defaults describe a
    clean, unambiguous breakout: a real prior advance, a tidy base, a decisive
    close through the level on heavy volume, and orderly follow-through.
    """

    #: Sessions of prior advance before the base forms.
    advance_sessions: int = 40
    advance_pct: float = 0.30
    #: The base the level is drawn across.
    base_sessions: int = 35
    base_depth: float = 0.07
    #: Resistance level, in price units.
    level: float = 100.0

    #: The breakout bar.
    breakout_close_above: float = 0.025
    breakout_volume_multiple: float = 2.2
    #: Where the close sits in the breakout bar's range, 0 (low) to 1 (high).
    breakout_close_location: float = 0.92
    breakout_range_multiple: float = 1.8
    #: Opening gap through the level, as a fraction of the previous close.
    gap_pct: float = 0.0

    #: What happens afterwards.
    after_sessions: int = 12
    #: Per-session drift after the breakout, as a fraction.
    follow_through_drift: float = 0.004
    #: When positive, price pulls back to this fraction below the level.
    retest_depth: float = 0.0
    retest_start: int = 3
    retest_sessions: int = 4
    retest_volume_multiple: float = 0.5
    retest_recovers: bool = True
    #: When true the breakout bar closes back below the level: a rejection.
    reject_breakout: bool = False
    #: Upper wick on the breakout bar, as a fraction of its range.
    breakout_upper_wick: float = 0.0

    base_volume: float = 1_000_000.0
    daily_noise: float = 0.005
    seed: int = 0

    @property
    def total_sessions(self) -> int:
        return self.advance_sessions + self.base_sessions + 1 + self.after_sessions


@dataclass(frozen=True, slots=True)
class GeneratedBreakout:
    """A synthetic series plus the facts the tests need about it."""

    name: str
    bars: tuple[OhlcvBar, ...]
    level: float
    #: Index of the intended breakout bar, or ``None`` when the scenario has no
    #: breakout by construction.
    breakout_index: int | None
    spec: BreakoutSpec
    #: What the scenario is for, reproduced in the characterisation report.
    intent: str = ""
    #: Whether the scenario is a negative: the engine confirming here would be
    #: a false positive rather than a legitimate alternative reading.
    adversarial: bool = False

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def breakout_session(self) -> dt.date | None:
        if self.breakout_index is None:
            return None
        return self.bars[self.breakout_index].session_date

    def prefix(self, index: int) -> GeneratedBreakout:
        """The same scenario truncated, for prefix-consistency testing."""
        return replace(
            self,
            bars=self.bars[: index + 1],
            breakout_index=(
                self.breakout_index
                if self.breakout_index is not None and self.breakout_index <= index
                else None
            ),
        )


def _sessions(count: int, start: dt.date = _START) -> list[dt.date]:
    """Weekday sessions. No holiday calendar: the engine never reads the date
    for anything but ordering and identity, so a synthetic calendar that skips
    weekends is sufficient and a fabricated holiday schedule would only add a
    way to be subtly wrong."""
    out: list[dt.date] = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _price(value: float) -> Decimal:
    return Decimal(f"{max(value, 0.01):.4f}")


def _bar(
    session: dt.date,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    instrument_id: int = 1,
) -> OhlcvBar:
    high = max(high, open_, close)
    low = min(low, open_, close)
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=session,
        open=_price(open_),
        high=_price(high),
        low=_price(low),
        close=_price(close),
        volume=Decimal(f"{max(volume, 1.0):.0f}"),
        event_time=dt.datetime.combine(session, dt.time(21, 0), tzinfo=UTC),
        knowledge_time=dt.datetime.combine(session, dt.time(21, 30), tzinfo=UTC),
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


class BreakoutGenerator:
    """Builds the scenario corpus.

    Stateless apart from the instrument id, so two calls with the same spec and
    seed produce byte-identical series. A surprising characterisation result has
    to be reproducible rather than re-argued.
    """

    def __init__(self, instrument_id: int = 1) -> None:
        self.instrument_id = instrument_id

    # -- the one builder -----------------------------------------------------

    def build(self, spec: BreakoutSpec, *, name: str, intent: str = "") -> GeneratedBreakout:
        rng = np.random.default_rng(spec.seed)
        sessions = _sessions(spec.total_sessions)
        level = spec.level

        closes: list[float] = []
        volumes: list[float] = []

        # 1. prior advance into the base
        start = level * (1.0 - spec.advance_pct)
        for i in range(spec.advance_sessions):
            fraction = (i + 1) / spec.advance_sessions
            closes.append(start + (level - start) * fraction)
            volumes.append(spec.base_volume * float(rng.uniform(0.9, 1.3)))

        # 2. the base: oscillates below the level, touching it a few times
        depth = level * spec.base_depth
        for i in range(spec.base_sessions):
            phase = 2.0 * np.pi * i / max(spec.base_sessions / 3.0, 1.0)
            value = level - depth * 0.5 * (1.0 - float(np.cos(phase)))
            closes.append(min(value, level * 0.999))
            volumes.append(spec.base_volume * float(rng.uniform(0.55, 0.85)))

        breakout_index = len(closes)

        # 3. the breakout bar
        if spec.reject_breakout:
            breakout_close = level * (1.0 - 0.004)
        else:
            breakout_close = level * (1.0 + spec.breakout_close_above)
        closes.append(breakout_close)
        volumes.append(spec.base_volume * spec.breakout_volume_multiple)

        # 4. afterwards. A rejected attempt falls away from the level rather
        # than drifting back through it: a scenario that rejects and then
        # recovers within two sessions is testing recovery, not rejection, and
        # would silently stop being the negative it is named for.
        after_start = breakout_close
        drift = (
            -abs(spec.follow_through_drift) if spec.reject_breakout else spec.follow_through_drift
        )
        for i in range(spec.after_sessions):
            closes.append(after_start * (1.0 + drift * (i + 1)))
            volumes.append(spec.base_volume * float(rng.uniform(0.7, 1.1)))

        # 5. the retest, carved into the post-breakout leg
        if spec.retest_depth > 0 and spec.after_sessions > spec.retest_start:
            trough = level * (1.0 - spec.retest_depth)
            first = breakout_index + 1 + spec.retest_start
            last = min(len(closes) - 1, first + spec.retest_sessions - 1)
            span = max(last - first, 1)
            for offset, index in enumerate(range(first, last + 1)):
                fraction = offset / span
                closes[index] = breakout_close + (trough - breakout_close) * min(
                    1.0, fraction * 2.0
                )
                volumes[index] = spec.base_volume * spec.retest_volume_multiple
            if spec.retest_recovers:
                for index in range(last + 1, len(closes)):
                    step = index - last
                    closes[index] = level * (1.0 + 0.004 * step)
            else:
                for index in range(last + 1, len(closes)):
                    step = index - last
                    closes[index] = trough * (1.0 - 0.006 * step)

        bars = self._to_bars(sessions, closes, volumes, spec, breakout_index, rng)
        return GeneratedBreakout(
            name=name,
            bars=tuple(bars),
            level=level,
            breakout_index=None if spec.reject_breakout else breakout_index,
            spec=spec,
            intent=intent,
        )

    def _to_bars(
        self,
        sessions: Sequence[dt.date],
        closes: Sequence[float],
        volumes: Sequence[float],
        spec: BreakoutSpec,
        breakout_index: int,
        rng: np.random.Generator,
    ) -> list[OhlcvBar]:
        bars: list[OhlcvBar] = []
        for i, (session, close, volume) in enumerate(zip(sessions, closes, volumes, strict=False)):
            previous = closes[i - 1] if i else close
            noise = float(rng.normal(0.0, spec.daily_noise)) * close

            if i == breakout_index:
                span = close * spec.daily_noise * 4.0 * spec.breakout_range_multiple
                open_ = previous * (1.0 + spec.gap_pct)
                if spec.reject_breakout:
                    # A rejection bar is built from its own geometry rather than
                    # by widening the normal one: the normal bar's low sits well
                    # below its close, which would place the close in the middle
                    # of the range and stop the scenario being a rejection at
                    # all — the negative would quietly become a neutral.
                    high = max(spec.level * 1.02, open_)
                    low = close - span * 0.15
                else:
                    low = close - span * spec.breakout_close_location
                    high = close + span * (1.0 - spec.breakout_close_location)
                    if spec.breakout_upper_wick > 0:
                        high = low + (close - low) / max(1e-6, 1.0 - spec.breakout_upper_wick)
            else:
                open_ = previous + noise * 0.4
                high = max(open_, close) + abs(noise) * 0.9
                low = min(open_, close) - abs(noise) * 0.9

            bars.append(_bar(session, open_, high, low, close, volume, self.instrument_id))
        return bars

    # -- the seventeen controlled scenarios ----------------------------------

    def clean_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(seed=seed),
            name="clean_breakout",
            intent="a decisive close through the level on heavy volume near the high",
        )

    def weak_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                breakout_close_above=0.003,
                breakout_volume_multiple=1.0,
                breakout_close_location=0.45,
                breakout_range_multiple=0.9,
                follow_through_drift=0.0005,
                seed=seed,
            ),
            name="weak_breakout",
            intent="barely clears the zone, average volume, mid-range close",
        )

    def low_volume_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(breakout_volume_multiple=0.7, seed=seed),
            name="low_volume_breakout",
            intent="identical geometry to the clean case, volume below average",
        )

    def high_volume_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(breakout_volume_multiple=4.5, seed=seed),
            name="high_volume_breakout",
            intent="identical geometry, very heavy volume",
        )

    def wick_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                breakout_upper_wick=0.6,
                breakout_close_location=0.3,
                seed=seed,
            ),
            name="wick_breakout",
            intent="traded far above the level and closed near the middle of the bar",
        )

    def gap_breakout(self, *, seed: int = 0, gap: float = 0.04) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(gap_pct=gap, breakout_close_above=gap + 0.01, seed=seed),
            name="gap_breakout",
            intent="opens through the level and holds; strong and extended at once",
        )

    def extreme_extension(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                gap_pct=0.14,
                breakout_close_above=0.16,
                breakout_volume_multiple=5.0,
                seed=seed,
            ),
            name="extreme_extension",
            intent="a very large gap far above the level: strong momentum, poor entry",
        )

    def immediate_rejection(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(reject_breakout=True, after_sessions=8, seed=seed),
            name="immediate_rejection",
            intent="clears the level intraday and closes back below it",
        )

    def strong_follow_through(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(follow_through_drift=0.012, after_sessions=10, seed=seed),
            name="strong_follow_through",
            intent="clean break followed by sustained progress",
        )

    def successful_retest(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                after_sessions=18,
                retest_depth=0.004,
                retest_start=2,
                retest_sessions=3,
                retest_recovers=True,
                follow_through_drift=0.002,
                seed=seed,
            ),
            name="successful_retest",
            intent="breaks out, pulls back into the zone, holds and recovers",
        )

    def failed_retest(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                after_sessions=18,
                retest_depth=0.05,
                retest_start=2,
                retest_sessions=4,
                retest_recovers=False,
                follow_through_drift=0.002,
                seed=seed,
            ),
            name="failed_retest",
            intent="breaks out, pulls back through the level and keeps going",
        )

    def false_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                breakout_close_above=0.012,
                after_sessions=10,
                retest_depth=0.07,
                retest_start=1,
                retest_sessions=3,
                retest_recovers=False,
                seed=seed,
            ),
            name="false_breakout",
            intent="closes above and is back inside the base within days",
        )

    def delayed_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(base_sessions=70, seed=seed),
            name="delayed_breakout",
            intent="a long approach before the same clean break",
        )

    def no_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        spec = BreakoutSpec(base_sessions=60, after_sessions=0, seed=seed)
        generated = self.build(spec, name="no_breakout", intent="price never clears the level")
        trimmed = generated.bars[: spec.advance_sessions + spec.base_sessions]
        return replace(generated, bars=trimmed, breakout_index=None)

    def high_volatility_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(daily_noise=0.022, base_depth=0.14, seed=seed),
            name="high_volatility_breakout",
            intent="the same break in a name whose daily range is four times larger",
        )

    def low_volatility_breakout(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(daily_noise=0.0015, base_depth=0.02, seed=seed),
            name="low_volatility_breakout",
            intent="the same break in a very quiet name",
        )

    def multiple_attempts(self, *, seed: int = 0) -> GeneratedBreakout:
        """Three goes at the same level: reject, reject, then clear it.

        Built by splicing rather than parameterised, because the point is the
        *sequence* — and the monitor's attempt numbering is what the scenario
        exists to exercise.
        """
        rng = np.random.default_rng(seed)
        spec = BreakoutSpec(seed=seed, after_sessions=10)
        base = self.build(spec, name="multiple_attempts")
        level = spec.level
        bars = list(base.bars[: spec.advance_sessions + spec.base_sessions])

        sessions = _sessions(len(bars) + 24)[len(bars) :]
        cursor = 0

        def push(close: float, high: float, low: float, volume: float) -> None:
            nonlocal cursor
            previous = float(bars[-1].close)
            bars.append(
                _bar(
                    sessions[cursor],
                    previous,
                    high,
                    low,
                    close,
                    volume,
                    self.instrument_id,
                )
            )
            cursor += 1

        for _ in range(2):  # two rejections
            push(level * 0.995, level * 1.018, level * 0.99, spec.base_volume * 1.6)
            for _ in range(4):
                push(
                    level * float(rng.uniform(0.955, 0.985)),
                    level * 0.99,
                    level * 0.95,
                    spec.base_volume * 0.7,
                )
        push(level * 1.03, level * 1.035, level * 0.998, spec.base_volume * 2.4)
        breakout_index = len(bars) - 1
        for i in range(5):
            close = level * (1.03 + 0.005 * (i + 1))
            push(close, close * 1.004, close * 0.995, spec.base_volume)

        return GeneratedBreakout(
            name="multiple_attempts",
            bars=tuple(bars),
            level=level,
            breakout_index=breakout_index,
            spec=spec,
            intent="two rejections at the level, then a clean third attempt",
        )

    # -- the thirteen adversarial scenarios ----------------------------------

    def random_level_crosses(self, *, seed: int = 0) -> GeneratedBreakout:
        """A random walk with a level drawn through the middle of it.

        The purest negative: nothing about this series is a breakout, and every
        cross of the level is arithmetic.
        """
        return self._noise_scenario(
            seed=seed,
            name="random_level_crosses",
            intent="a random walk crossing an arbitrary level",
            drift=0.0,
            noise=0.014,
            volume_shape="flat",
        )

    def noise_around_level(self, *, seed: int = 0) -> GeneratedBreakout:
        return self._noise_scenario(
            seed=seed,
            name="noise_around_level",
            intent="price oscillating tightly around the level with no structure",
            drift=0.0,
            noise=0.006,
            volume_shape="flat",
        )

    def broad_volatile_range(self, *, seed: int = 0) -> GeneratedBreakout:
        return self._noise_scenario(
            seed=seed,
            name="broad_volatile_range",
            intent="wide directionless swings through the level",
            drift=0.0,
            noise=0.032,
            volume_shape="erratic",
        )

    def repeated_whipsaw(self, *, seed: int = 0) -> GeneratedBreakout:
        return self._noise_scenario(
            seed=seed,
            name="repeated_whipsaw",
            intent="alternating closes either side of the level",
            drift=0.0,
            noise=0.018,
            volume_shape="erratic",
            alternate=True,
        )

    def low_volume_drift(self, *, seed: int = 0) -> GeneratedBreakout:
        return self._noise_scenario(
            seed=seed,
            name="low_volume_drift",
            intent="price drifts above the level on collapsing volume",
            drift=0.0012,
            noise=0.004,
            volume_shape="declining",
        )

    def liquidity_spike(self, *, seed: int = 0) -> GeneratedBreakout:
        return self._noise_scenario(
            seed=seed,
            name="liquidity_spike",
            intent="an isolated volume spike with no price follow-through",
            drift=0.0,
            noise=0.008,
            volume_shape="spike",
        )

    def one_tick_penetration(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                breakout_close_above=0.0002,
                breakout_volume_multiple=0.9,
                breakout_close_location=0.5,
                follow_through_drift=0.0,
                seed=seed,
            ),
            name="one_tick_penetration",
            intent="a close a hair above the level, inside any sane tolerance zone",
        )

    def intraday_break_close_below(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(reject_breakout=True, breakout_volume_multiple=1.9, seed=seed),
            name="intraday_break_close_below",
            intent="high clears the level, close does not",
        )

    def gap_and_fade(self, *, seed: int = 0) -> GeneratedBreakout:
        return self.build(
            BreakoutSpec(
                gap_pct=0.06,
                reject_breakout=True,
                breakout_volume_multiple=3.0,
                after_sessions=8,
                follow_through_drift=-0.008,
                seed=seed,
            ),
            name="gap_and_fade",
            intent="gaps above the level and closes below it on heavy volume",
        )

    def poor_confidence_boundary(self, *, seed: int = 0) -> GeneratedBreakout:
        """A clean break of a level nothing supports.

        The series is the clean scenario; what makes it adversarial is that the
        test attaches a one-touch, low-confidence, unattached boundary. The
        engine should produce a real breakout with visibly lower *confidence*,
        which is exactly the distinction item 41 asks for.
        """
        return replace(
            self.build(
                BreakoutSpec(seed=seed),
                name="poor_confidence_boundary",
                intent="clean geometry against a level defined by a single bar",
            ),
            adversarial=True,
        )

    def no_prior_resistance(self, *, seed: int = 0) -> GeneratedBreakout:
        """An uninterrupted advance with a level placed arbitrarily in it."""
        return self._noise_scenario(
            seed=seed,
            name="no_prior_resistance",
            intent="a trend with no consolidation; the level was never tested",
            drift=0.004,
            noise=0.006,
            volume_shape="flat",
        )

    def already_invalidated(self, *, seed: int = 0) -> GeneratedBreakout:
        """A clean break the caller declares structurally invalidated.

        The series is ordinary; the adversarial part is the caller's flag, which
        must send the event terminal rather than being ignored because the price
        action looks fine.
        """
        return replace(
            self.build(
                BreakoutSpec(seed=seed),
                name="already_invalidated",
                intent="clean break of a pattern the pattern layer has invalidated",
            ),
            adversarial=True,
        )

    def future_data_trap(self, *, seed: int = 0) -> GeneratedBreakout:
        """A weak breakout followed by an enormous later advance.

        The trap: any implementation that lets later bars inform the breakout
        bar's score will rate this highly. A correct one rates the breakout bar
        on the breakout bar, and the frozen quality must equal the quality
        computed from the prefix that ends there.
        """
        return replace(
            self.build(
                BreakoutSpec(
                    breakout_close_above=0.004,
                    breakout_volume_multiple=0.8,
                    breakout_close_location=0.4,
                    after_sessions=20,
                    follow_through_drift=0.035,
                    seed=seed,
                ),
                name="future_data_trap",
                intent="a poor breakout bar followed by a very strong advance",
            ),
            adversarial=True,
        )

    # -- noise construction --------------------------------------------------

    def _noise_scenario(
        self,
        *,
        seed: int,
        name: str,
        intent: str,
        drift: float,
        noise: float,
        volume_shape: str,
        alternate: bool = False,
        sessions: int = 120,
        level: float = 100.0,
    ) -> GeneratedBreakout:
        rng = np.random.default_rng(seed + 9_000)
        dates = _sessions(sessions)
        closes = [level]
        for i in range(1, sessions):
            if alternate:
                sign = 1.0 if i % 2 else -1.0
                closes.append(level * (1.0 + sign * noise * float(rng.uniform(0.5, 1.5))))
            else:
                step = float(rng.normal(drift, noise))
                closes.append(max(closes[-1] * (1.0 + step), level * 0.2))

        base_volume = 1_000_000.0
        volumes: list[float] = []
        for i in range(sessions):
            if volume_shape == "declining":
                volumes.append(base_volume * max(0.2, 1.0 - i / sessions))
            elif volume_shape == "erratic":
                volumes.append(base_volume * float(rng.uniform(0.3, 3.0)))
            elif volume_shape == "spike":
                volumes.append(base_volume * (6.0 if i == sessions - 5 else 0.8))
            else:
                volumes.append(base_volume * float(rng.uniform(0.8, 1.2)))

        bars: list[OhlcvBar] = []
        for i, (session, close, volume) in enumerate(zip(dates, closes, volumes, strict=False)):
            previous = closes[i - 1] if i else close
            span = abs(float(rng.normal(0.0, noise))) * close + close * 0.001
            bars.append(
                _bar(
                    session,
                    previous,
                    max(previous, close) + span,
                    min(previous, close) - span,
                    close,
                    volume,
                    self.instrument_id,
                )
            )

        return GeneratedBreakout(
            name=name,
            bars=tuple(bars),
            level=level,
            breakout_index=None,
            spec=BreakoutSpec(level=level, seed=seed),
            intent=intent,
            adversarial=True,
        )


#: The controlled corpus (item 33). Seventeen scenarios, each a named method.
CONTROLLED_SCENARIOS: tuple[str, ...] = (
    "clean_breakout",
    "weak_breakout",
    "low_volume_breakout",
    "high_volume_breakout",
    "wick_breakout",
    "gap_breakout",
    "extreme_extension",
    "immediate_rejection",
    "strong_follow_through",
    "successful_retest",
    "failed_retest",
    "false_breakout",
    "delayed_breakout",
    "no_breakout",
    "high_volatility_breakout",
    "low_volatility_breakout",
    "multiple_attempts",
)

#: The adversarial corpus (item 34). Thirteen scenarios.
ADVERSARIAL_SCENARIOS: tuple[str, ...] = (
    "random_level_crosses",
    "noise_around_level",
    "broad_volatile_range",
    "repeated_whipsaw",
    "low_volume_drift",
    "liquidity_spike",
    "one_tick_penetration",
    "intraday_break_close_below",
    "gap_and_fade",
    "poor_confidence_boundary",
    "no_prior_resistance",
    "already_invalidated",
    "future_data_trap",
)


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named scenario and how to build it, for the harness to iterate."""

    name: str
    build: Callable[[int], GeneratedBreakout]
    adversarial: bool
    intent: str = ""
    #: Extra instructions the harness must honour, keyed by name. Used for the
    #: two scenarios whose adversarial content is in the *caller's* inputs
    #: rather than in the price series.
    directives: frozenset[str] = field(default_factory=frozenset)


def _binder(method: Callable[..., GeneratedBreakout]) -> Callable[[int], GeneratedBreakout]:
    """Bind a generator method to the seed-only signature the harness expects."""

    def build(seed: int) -> GeneratedBreakout:
        return method(seed=seed)

    return build


def all_scenarios(generator: BreakoutGenerator | None = None) -> list[Scenario]:
    """Every scenario in the corpus, controlled first then adversarial."""
    gen = generator or BreakoutGenerator()
    out: list[Scenario] = []
    for name in CONTROLLED_SCENARIOS:
        method = getattr(gen, name)
        sample = method(seed=0)
        out.append(Scenario(name, _binder(method), False, sample.intent))
    for name in ADVERSARIAL_SCENARIOS:
        method = getattr(gen, name)
        sample = method(seed=0)
        directives: set[str] = set()
        if name == "poor_confidence_boundary":
            directives.add("weak_boundary")
        if name == "already_invalidated":
            directives.add("invalidate_pattern")
        if name == "future_data_trap":
            # The trap is that the *first* breakout bar is weak and what follows
            # is strong. Re-opening a later attempt would let the harness score
            # a genuinely strong bar from the middle of the advance, which is
            # a legitimate breakout and not the thing this scenario tests.
            directives.add("single_attempt")
        out.append(
            Scenario(
                name,
                _binder(method),
                True,
                sample.intent,
                frozenset(directives),
            )
        )
    return out


__all__ = [
    "ADVERSARIAL_SCENARIOS",
    "CONTROLLED_SCENARIOS",
    "BreakoutGenerator",
    "BreakoutSpec",
    "GeneratedBreakout",
    "Scenario",
    "all_scenarios",
]
