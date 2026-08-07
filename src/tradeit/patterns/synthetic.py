"""Controlled synthetic pattern generation.

Hand-written fixtures are the usual way to test a pattern detector and they are
not enough. A fixture proves the detector finds *that* flag. It cannot answer
the questions that matter: does the score fall smoothly as retracement
deepens? does noise degrade it gradually or catastrophically? does the pattern
invalidate at the level it says it will? Those need a knob, not an example.

So every structural parameter here is a knob: flagpole magnitude and duration,
retracement depth, consolidation slope and length, noise amplitude, volume
contraction, ATR contraction, breakout presence. Perturbation tests turn one
knob and assert the score moves the right way, which is a far stronger claim
than any fixture supports.

**What this is not.** It is not a market model and produces no evidence about
real-world accuracy. A detector that scores 95 on a synthetic flag has been
shown to be internally consistent, nothing more — the generator draws what the
detector is looking for, so agreement is close to tautological. The real-data
validation in ``docs/PHASE_03_GATE.md`` §12 remains open and this does not
close any part of it. What synthetic data *can* prove is the properties above,
and those are properties of the code rather than of markets.

Everything is a pure function of the seed, so a failing property test
reproduces exactly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError

UTC = dt.UTC


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """Shared shape parameters for any generated series."""

    instrument_id: int = 1
    start_date: dt.date = dt.date(2023, 1, 3)
    start_price: float = 100.0
    #: Sessions of quiet history before the pattern begins. Detectors need a
    #: volume baseline and an ATR warm-up, and a series that starts at the
    #: flagpole gives them neither.
    lead_in_sessions: int = 60
    lead_in_drift: float = 0.0005
    #: Daily standard deviation of the lead-in.
    base_volatility: float = 0.012
    base_volume: float = 1_000_000.0
    #: Fraction of the daily range the close sits within, on average. Higher
    #: means stronger closes.
    seed: int = 20260807


@dataclass(frozen=True, slots=True)
class BullFlagSpec:
    """Every dimension of a bull flag, independently controllable.

    The defaults describe a textbook flag: a brisk 25% advance over 12 sessions
    on expanding volume, then a 9-session drift that gives back a third of it on
    contracting range and drying volume.
    """

    pole_gain: float = 0.25
    pole_sessions: int = 12
    #: Fraction of the pole given back during the consolidation.
    retracement: float = 0.33
    flag_sessions: int = 9
    #: Consolidation drift, as a fraction of price per session. Negative drifts
    #: down, which is the textbook shape.
    flag_slope: float = -0.002
    #: Volume during the consolidation, relative to the flagpole average.
    volume_contraction: float = 0.55
    #: Volume during the flagpole, relative to the lead-in baseline.
    pole_volume_expansion: float = 2.0
    #: Late-consolidation range over early-consolidation range.
    range_contraction: float = 0.55
    #: Multiplier on random daily noise inside the consolidation. Zero produces
    #: a geometrically perfect flag; 3.0 produces something barely recognisable.
    noise: float = 1.0
    #: Fraction of the pole delivered as a single overnight gap.
    gap_share: float = 0.0
    #: Sessions of breakout to append after the consolidation. Zero leaves the
    #: pattern unresolved.
    breakout_sessions: int = 0
    breakout_strength: float = 0.04
    #: When set, the consolidation ends by breaking *down* through support by
    #: this fraction -- the invalidation case.
    breakdown: float = 0.0

    def __post_init__(self) -> None:
        if self.pole_sessions < 2:
            raise ConfigError("a flagpole needs at least two sessions")
        if self.flag_sessions < 2:
            raise ConfigError("a consolidation needs at least two sessions")
        if not 0.0 <= self.retracement < 1.5:
            raise ConfigError("retracement must be in [0, 1.5)")
        if not 0.0 <= self.gap_share <= 1.0:
            raise ConfigError("gap_share must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class VcpSpec:
    """A base built from explicit contraction legs.

    ``depths`` is the sequence the pattern *is*. Passing (0.18, 0.10, 0.05)
    draws the textbook three-leg VCP; passing (0.18, 0.05, 0.12) draws a base
    that ends tight without being a staircase, which is the case that separates
    a detector measuring progression from one measuring net change.
    """

    prior_gain: float = 0.35
    prior_sessions: int = 55
    depths: tuple[float, ...] = (0.18, 0.10, 0.05)
    leg_sessions: tuple[int, ...] = (14, 9, 6)
    #: Volume through each leg, relative to the pre-base baseline.
    volume_ratios: tuple[float, ...] = (0.9, 0.7, 0.45)
    noise: float = 0.6
    breakout_sessions: int = 0

    def __post_init__(self) -> None:
        if len(self.depths) < 2:
            raise ConfigError("a VCP needs at least two contractions")
        if len(self.leg_sessions) != len(self.depths):
            raise ConfigError("leg_sessions must match depths")
        if len(self.volume_ratios) != len(self.depths):
            raise ConfigError("volume_ratios must match depths")


@dataclass(frozen=True, slots=True)
class GeneratedSeries:
    """Bars plus the ground truth used to draw them.

    ``truth`` carries the indices the generator *intended*, which is what makes
    precision and recall measurable: a detector that finds a flag 20 sessions
    from where one was drawn has not found that flag.
    """

    bars: list[OhlcvBar]
    truth: dict[str, object] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def last_session(self) -> dt.date:
        return self.bars[-1].session_date

    def closes(self) -> np.ndarray:
        return np.array([float(b.close) for b in self.bars])

    def slice_to(self, index: int) -> GeneratedSeries:
        """A prefix, for causality testing."""
        return GeneratedSeries(self.bars[: index + 1], dict(self.truth))


class PatternGenerator:
    """Builds bar series with known structure.

    Deterministic given a seed. The OHLC construction guarantees
    ``low <= open, close <= high`` by clipping rather than by luck, so a
    generated bar always satisfies the domain model's invariants -- otherwise
    the generator would spend its time producing test data that fails
    validation for reasons unrelated to the pattern.
    """

    def __init__(self, spec: SeriesSpec | None = None) -> None:
        self.spec = spec or SeriesSpec()
        self.calendar = get_calendar("XNYS")

    # -- primitives ----------------------------------------------------------

    def _sessions(self, count: int) -> list[dt.date]:
        out: list[dt.date] = []
        cursor = self.spec.start_date
        while len(out) < count:
            if self.calendar.is_session(cursor):
                out.append(cursor)
            cursor += dt.timedelta(days=1)
        return out

    def _bars_from(
        self,
        closes: np.ndarray,
        volumes: np.ndarray,
        ranges: np.ndarray,
        *,
        opens: np.ndarray | None = None,
        close_position: float = 0.6,
        gaps: np.ndarray | None = None,
    ) -> list[OhlcvBar]:
        """Assemble valid OHLCV bars from a close/volume/range description.

        ``close_position`` places the close within the day's range; 0.5 is the
        middle, higher is a strong close. Used to give flagpole sessions
        genuine closing strength rather than leaving it to noise.

        ``gaps`` opens a session away from the previous close. Without it every
        open equals the prior close and the series contains no overnight gaps at
        all -- which silently disconnected the ``gap_share`` knob, so a spec
        asking for a 70% gap-driven flagpole produced a perfectly continuous
        advance and the detector's gap measurement correctly reported zero.
        """
        n = len(closes)
        sessions = self._sessions(n)
        if opens is None:
            previous = np.concatenate([[closes[0]], closes[:-1]])
            opens = previous * (1.0 + gaps) if gaps is not None else previous

        bars: list[OhlcvBar] = []
        for i in range(n):
            close = float(closes[i])
            open_ = float(opens[i])
            span = max(float(ranges[i]), close * 0.001)

            low = min(open_, close) - span * (1.0 - close_position)
            high = max(open_, close) + span * close_position
            low = min(low, open_, close)
            high = max(high, open_, close)
            if low <= 0:
                low = min(open_, close) * 0.99

            session = sessions[i]
            close_time = self.calendar.close_instant(session)
            bars.append(
                OhlcvBar(
                    instrument_id=self.spec.instrument_id,
                    timeframe=Bartimeframe.D1,
                    session_date=session,
                    event_time=close_time,
                    knowledge_time=close_time + dt.timedelta(minutes=20),
                    knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                    open=_price(open_),
                    high=_price(high),
                    low=_price(low),
                    close=_price(close),
                    volume=Decimal(str(int(max(1.0, volumes[i])))),
                )
            )
        return bars

    # -- series --------------------------------------------------------------

    def random_walk(
        self, sessions: int, *, drift: float = 0.0, volatility: float | None = None, seed: int = 0
    ) -> GeneratedSeries:
        """A series with no pattern in it. The primary negative control."""
        rng = np.random.default_rng(self.spec.seed + seed)
        vol = self.spec.base_volatility if volatility is None else volatility
        returns = rng.normal(drift, vol, sessions)
        closes = self.spec.start_price * np.exp(np.cumsum(returns))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.3, sessions)
        ranges = closes * np.abs(rng.normal(vol * 1.4, vol * 0.4, sessions))
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": None},
        )

    def bull_flag(self, spec: BullFlagSpec | None = None, *, seed: int = 0) -> GeneratedSeries:
        """A bull flag with every dimension under control."""
        flag = spec or BullFlagSpec()
        rng = np.random.default_rng(self.spec.seed + seed)
        base = self.spec

        # -- lead-in
        lead_returns = rng.normal(base.lead_in_drift, base.base_volatility, base.lead_in_sessions)
        lead_closes = base.start_price * np.exp(np.cumsum(lead_returns))
        lead_volumes = rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions)
        lead_ranges = lead_closes * np.abs(
            rng.normal(
                base.base_volatility * 1.4, base.base_volatility * 0.3, base.lead_in_sessions
            )
        )

        pole_start_price = float(lead_closes[-1])

        # -- flagpole: a steady advance, optionally with one dominant gap
        gapped = flag.pole_gain * flag.gap_share
        stepped = flag.pole_gain - gapped
        per_session = (1.0 + stepped) ** (1.0 / flag.pole_sessions) - 1.0
        pole_returns = rng.normal(per_session, base.base_volatility * 0.5, flag.pole_sessions)
        pole_returns = np.maximum(pole_returns, -base.base_volatility)
        if gapped > 0:
            pole_returns[0] += gapped

        pole_closes = pole_start_price * np.exp(np.cumsum(np.log1p(pole_returns)))
        pole_gaps = np.zeros(flag.pole_sessions)
        if gapped > 0:
            pole_gaps[0] = gapped
        pole_volumes = rng.lognormal(
            np.log(base.base_volume * flag.pole_volume_expansion), 0.2, flag.pole_sessions
        )
        pole_ranges = pole_closes * np.abs(
            rng.normal(base.base_volatility * 1.8, base.base_volatility * 0.3, flag.pole_sessions)
        )

        pole_high = float(pole_closes.max())
        pole_height = pole_high - pole_start_price

        # -- consolidation: a linear drift to the retracement target, plus
        #    noise whose amplitude contracts through the window
        target_low = pole_high - pole_height * flag.retracement
        drift = np.linspace(0.0, 1.0, flag.flag_sessions)
        path = pole_high + (target_low - pole_high) * drift
        path = path * (1.0 + flag.flag_slope * np.arange(flag.flag_sessions))

        noise_scale = np.linspace(
            base.base_volatility, base.base_volatility * flag.range_contraction, flag.flag_sessions
        )
        flag_closes = path * (1.0 + rng.normal(0.0, noise_scale * flag.noise, flag.flag_sessions))
        flag_closes = np.maximum(flag_closes, pole_start_price * 0.5)

        flag_volumes = rng.lognormal(
            np.log(base.base_volume * flag.pole_volume_expansion * flag.volume_contraction),
            0.2,
            flag.flag_sessions,
        )
        # Noise widens the bars as well as jittering the closes. Without this the
        # knob changed the path and left the range structure untouched, so a
        # 3.0-noise flag measured the same volatility contraction as a 0.4 one
        # and scored identically.
        flag_ranges = flag_closes * noise_scale * 1.5 * max(0.35, flag.noise)

        if flag.breakdown > 0:
            # Drive the final sessions decisively below the consolidation low.
            breach = np.linspace(0.0, flag.breakdown, max(2, flag.flag_sessions // 3))
            flag_closes[-len(breach) :] = float(flag_closes.min()) * (1.0 - breach)

        # -- optional breakout
        parts_closes = [lead_closes, pole_closes, flag_closes]
        parts_volumes = [lead_volumes, pole_volumes, flag_volumes]
        parts_ranges = [lead_ranges, pole_ranges, flag_ranges]

        if flag.breakout_sessions > 0:
            launch = float(flag_closes[-1])
            step = flag.breakout_strength
            breakout_closes = launch * np.cumprod(
                1.0 + rng.normal(step, base.base_volatility * 0.5, flag.breakout_sessions)
            )
            breakout_closes = np.maximum(breakout_closes, pole_high * 1.005)
            parts_closes.append(breakout_closes)
            parts_volumes.append(
                rng.lognormal(
                    np.log(base.base_volume * flag.pole_volume_expansion * 1.5),
                    0.2,
                    flag.breakout_sessions,
                )
            )
            parts_ranges.append(breakout_closes * base.base_volatility * 2.0)

        closes = np.concatenate(parts_closes)
        volumes = np.concatenate(parts_volumes)
        ranges = np.concatenate(parts_ranges)

        gaps = np.concatenate(
            [
                np.zeros(base.lead_in_sessions),
                pole_gaps,
                np.zeros(len(closes) - base.lead_in_sessions - flag.pole_sessions),
            ]
        )
        bars = self._bars_from(closes, volumes, ranges, close_position=0.65, gaps=gaps)

        pole_start_index = base.lead_in_sessions - 1
        pole_end_index = base.lead_in_sessions + flag.pole_sessions - 1
        flag_end_index = pole_end_index + flag.flag_sessions

        return GeneratedSeries(
            bars,
            {
                "pattern": "bull_flag",
                "pole_start_index": pole_start_index,
                "pole_end_index": pole_end_index,
                "flag_start_index": pole_end_index + 1,
                "flag_end_index": flag_end_index,
                "pole_gain": flag.pole_gain,
                "retracement": flag.retracement,
                "expected_resistance": pole_high,
            },
        )

    # -- negative controls ---------------------------------------------------

    def vcp(self, spec: VcpSpec | None = None, *, seed: int = 0) -> GeneratedSeries:
        """A base of explicit contraction legs after an advance.

        Each leg runs from the base high down by its stated depth and back to
        near the high, so the *pivot* stays roughly constant while the pullbacks
        shrink -- which is what a VCP looks like and what distinguishes it from
        a descending wedge.
        """
        vcp = spec or VcpSpec()
        rng = np.random.default_rng(self.spec.seed + seed + 12211)
        base = self.spec

        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        advance_rate = (1.0 + vcp.prior_gain) ** (1.0 / vcp.prior_sessions) - 1.0
        advance = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(advance_rate, base.base_volatility * 0.6, vcp.prior_sessions)
        )
        pivot = float(advance.max())

        segments: list[np.ndarray] = [lead, advance]
        volumes: list[np.ndarray] = [
            rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
            rng.lognormal(np.log(base.base_volume * 1.8), 0.2, vcp.prior_sessions),
        ]
        ranges: list[np.ndarray] = [
            lead * base.base_volatility * 1.3,
            advance * base.base_volatility * 1.7,
        ]

        for depth, sessions, volume_ratio in zip(
            vcp.depths, vcp.leg_sessions, vcp.volume_ratios, strict=True
        ):
            half = max(2, sessions // 2)
            down = np.linspace(pivot, pivot * (1.0 - depth), half)
            up = np.linspace(pivot * (1.0 - depth), pivot * 0.995, sessions - half)
            leg = np.concatenate([down, up])
            leg = leg * (1.0 + rng.normal(0.0, base.base_volatility * vcp.noise, len(leg)))
            segments.append(leg)
            volumes.append(rng.lognormal(np.log(base.base_volume * volume_ratio), 0.2, len(leg)))
            # Bar ranges shrink with the leg depth, so volatility contracts with
            # the structure rather than independently of it.
            ranges.append(leg * max(0.004, depth * 0.28))

        if vcp.breakout_sessions > 0:
            launch = float(segments[-1][-1])
            run = launch * np.cumprod(
                1.0 + rng.normal(0.03, base.base_volatility, vcp.breakout_sessions)
            )
            run = np.maximum(run, pivot * 1.01)
            segments.append(run)
            volumes.append(
                rng.lognormal(np.log(base.base_volume * 2.5), 0.2, vcp.breakout_sessions)
            )
            ranges.append(run * base.base_volatility * 2.0)

        closes = np.concatenate(segments)
        return GeneratedSeries(
            self._bars_from(
                closes, np.concatenate(volumes), np.concatenate(ranges), close_position=0.55
            ),
            {
                "pattern": "vcp",
                "contractions": len(vcp.depths),
                "depths": vcp.depths,
                "expected_pivot": pivot,
                "base_start_index": base.lead_in_sessions + vcp.prior_sessions,
            },
        )

    def descending_wedge(self, *, seed: int = 0) -> GeneratedSeries:
        """Tightening legs whose *highs* fall too. Not a VCP.

        The negative that matters most for this detector: the pullbacks shrink,
        so a detector scoring only depth progression sees a textbook VCP. What
        is missing is that the pivot is falling -- the security is not coiling
        beneath a level, it is grinding down.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 13313)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0005, base.base_volatility, base.lead_in_sessions))
        )
        level = float(lead[-1])
        segments = [lead]
        for depth, sessions in ((0.16, 12), (0.09, 9), (0.05, 6)):
            level *= 0.94  # each leg starts lower than the last
            half = max(2, sessions // 2)
            leg = np.concatenate(
                [
                    np.linspace(level, level * (1 - depth), half),
                    np.linspace(level * (1 - depth), level * 0.98, sessions - half),
                ]
            )
            segments.append(leg * (1.0 + rng.normal(0.0, base.base_volatility * 0.6, len(leg))))
        closes = np.concatenate(segments)
        volumes = rng.lognormal(np.log(base.base_volume), 0.3, len(closes))
        ranges = closes * base.base_volatility
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.45),
            {"pattern": "descending_wedge"},
        )

    def flat_base(
        self,
        *,
        seed: int = 0,
        prior_gain: float = 0.30,
        depth: float = 0.08,
        sessions: int = 35,
        slope: float = 0.0,
        breakout_sessions: int = 0,
    ) -> GeneratedSeries:
        """An advance, then a shallow horizontal range."""
        rng = np.random.default_rng(self.spec.seed + seed + 14411)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        prior_sessions = 55
        rate = (1.0 + prior_gain) ** (1.0 / prior_sessions) - 1.0
        advance = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(rate, base.base_volatility * 0.6, prior_sessions)
        )
        ceiling = float(advance.max())

        # Oscillate inside a band whose width is the stated depth, with the
        # highs clustered at the ceiling so a resistance level actually forms.
        oscillation = np.sin(np.linspace(0, 3.5 * np.pi, sessions)) * (depth / 2)
        path = ceiling * (1.0 - depth / 2 + oscillation)
        path = path * (1.0 + slope * np.arange(sessions))
        band = path * (1.0 + rng.normal(0.0, depth * 0.12, sessions))

        segments = [lead, advance, band]
        volumes = [
            rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
            rng.lognormal(np.log(base.base_volume * 1.7), 0.2, prior_sessions),
            rng.lognormal(np.log(base.base_volume * 0.7), 0.2, sessions),
        ]
        ranges = [
            lead * base.base_volatility * 1.3,
            advance * base.base_volatility * 1.6,
            band * max(0.004, depth * 0.2),
        ]

        if breakout_sessions > 0:
            run = float(band[-1]) * np.cumprod(
                1.0 + rng.normal(0.025, base.base_volatility, breakout_sessions)
            )
            run = np.maximum(run, ceiling * 1.01)
            segments.append(run)
            volumes.append(rng.lognormal(np.log(base.base_volume * 2.2), 0.2, breakout_sessions))
            ranges.append(run * base.base_volatility * 2.0)

        closes = np.concatenate(segments)
        return GeneratedSeries(
            self._bars_from(
                closes, np.concatenate(volumes), np.concatenate(ranges), close_position=0.5
            ),
            {
                "pattern": "flat_base",
                "expected_ceiling": ceiling,
                "depth": depth,
                "base_start_index": base.lead_in_sessions + prior_sessions,
            },
        )

    def base_without_prior_trend(self, *, seed: int = 0) -> GeneratedSeries:
        """The same shallow horizontal range with no advance before it.

        The critical flat-base negative: identical geometry, missing context.
        """
        return self.flat_base(seed=seed + 300, prior_gain=0.0)

    def ascending_triangle(
        self,
        *,
        seed: int = 0,
        sessions: int = 40,
        depth: float = 0.12,
        touches: int = 4,
        rising: bool = True,
    ) -> GeneratedSeries:
        """A flat ceiling with lows that climb toward it.

        ``rising=False`` draws the rectangle: same ceiling, flat lows. The
        negative that separates an ascending triangle from a range.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 15511)
        base = self.spec
        # An advance *into* the ceiling, not a drift along it. Without the
        # advance the lead-in trades at the ceiling level throughout, so the
        # detector's resistance cluster reaches back through the whole series
        # and the "rising lows" it measures are lead-in noise.
        quiet = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions - 30))
        )
        advance = float(quiet[-1]) * np.cumprod(
            1.0 + rng.normal(0.008, base.base_volatility * 0.6, 30)
        )
        lead = np.concatenate([quiet, advance])
        ceiling = float(lead[-1])

        # Each cycle runs down to a low and back to the ceiling. The lows climb
        # when `rising`, so the boundaries converge.
        per_cycle = max(4, sessions // touches)
        path: list[float] = []
        for cycle in range(touches):
            fraction = cycle / max(1, touches - 1)
            low = ceiling * (1.0 - depth * (1.0 - 0.75 * fraction if rising else 1.0))
            half = per_cycle // 2
            path.extend(np.linspace(ceiling, low, half))
            path.extend(np.linspace(low, ceiling * 0.998, per_cycle - half))
        band = np.array(path) * (1.0 + rng.normal(0.0, base.base_volatility * 0.4, len(path)))

        closes = np.concatenate([lead, band])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 0.8), 0.2, len(band)),
            ]
        )
        ranges = closes * base.base_volatility * 1.1
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {
                "pattern": "ascending_triangle" if rising else "rectangle",
                "expected_ceiling": ceiling,
                "touches": touches,
            },
        )

    def rectangle(self, *, seed: int = 0) -> GeneratedSeries:
        """A flat ceiling with flat lows. Not ascending."""
        return self.ascending_triangle(seed=seed + 400, rising=False)

    def base_on_base(
        self,
        *,
        seed: int = 0,
        prior_gain: float = 0.30,
        first_sessions: int = 25,
        second_sessions: int = 22,
        first_depth: float = 0.12,
        second_depth: float = 0.08,
        ceiling_advance: float = 0.03,
        low_advance: float = 0.04,
        gap_sessions: int = 3,
    ) -> GeneratedSeries:
        """An advance, a base, almost no progress, a second base.

        ``ceiling_advance`` is the parameter the family turns on. Small is
        base-on-base; large draws a stair-step, which is a different and more
        common structure.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 21111)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        prior_sessions = 45
        rate = (1.0 + prior_gain) ** (1.0 / prior_sessions) - 1.0
        advance = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(rate, base.base_volatility * 0.6, prior_sessions)
        )
        first_ceiling = float(advance.max())
        second_ceiling = first_ceiling * (1.0 + ceiling_advance)

        def band(ceiling: float, depth: float, sessions: int, cycles: float) -> np.ndarray:
            """Oscillate under a ceiling so the highs actually cluster there."""
            wave = np.sin(np.linspace(0, cycles * np.pi, sessions))
            path = ceiling * (1.0 - depth / 2 + wave * depth / 2)
            return path * (1.0 + rng.normal(0.0, depth * 0.12, sessions))

        first = band(first_ceiling, first_depth, first_sessions, 3.5)
        # The transition: price clears the first ceiling, then settles into the
        # second base. Without it the two bases are one long range.
        bridge = np.linspace(float(first[-1]), second_ceiling, gap_sessions + 1)[1:]
        second = band(second_ceiling, second_depth, second_sessions, 3.5)
        # The second base's floor is lifted by ``low_advance`` relative to where
        # its own depth would put it. An earlier draft clamped the whole band to
        # the first ceiling, which pinned the floor and silently disconnected
        # ``second_depth`` -- the tightening knob moved nothing and the detector
        # correctly reported no change.
        second = second + second_ceiling * low_advance * (second_ceiling - second) / max(
            second_ceiling * second_depth, 1e-9
        )

        segments = [lead, advance, first, bridge, second]
        volumes = [
            rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
            rng.lognormal(np.log(base.base_volume * 1.7), 0.2, prior_sessions),
            rng.lognormal(np.log(base.base_volume * 0.85), 0.2, first_sessions),
            rng.lognormal(np.log(base.base_volume * 1.1), 0.2, gap_sessions),
            rng.lognormal(np.log(base.base_volume * 0.6), 0.2, second_sessions),
        ]
        closes = np.concatenate(segments)
        ranges = closes * base.base_volatility * 1.1
        return GeneratedSeries(
            self._bars_from(closes, np.concatenate(volumes), ranges, close_position=0.5),
            {
                "pattern": "base_on_base",
                "first_ceiling": first_ceiling,
                "second_ceiling": second_ceiling,
                "ceiling_advance": ceiling_advance,
                "first_base_index": base.lead_in_sessions + prior_sessions,
            },
        )

    def stair_step_bases(self, *, seed: int = 0) -> GeneratedSeries:
        """Two bases separated by a real advance. Not base-on-base."""
        return self.base_on_base(seed=seed + 1000, ceiling_advance=0.30, gap_sessions=12)

    def descending_bases(self, *, seed: int = 0) -> GeneratedSeries:
        """A second base built below the first. Ground given, not held."""
        return self.base_on_base(seed=seed + 1010, ceiling_advance=-0.12, low_advance=-0.12)

    def single_long_base(self, *, seed: int = 0) -> GeneratedSeries:
        """One continuous range of the same total length.

        The negative that matters: a long base does not become base-on-base
        because a line can be drawn through its middle.
        """
        return self.flat_base(seed=seed + 1020, sessions=50, depth=0.10)

    def tight_consolidation(
        self,
        *,
        seed: int = 0,
        prior_gain: float = 0.25,
        reference_depth: float = 0.14,
        reference_sessions: int = 30,
        sessions: int = 9,
        depth: float = 0.035,
        volume_ratio: float = 0.5,
    ) -> GeneratedSeries:
        """An advance, an ordinary-volatility stretch, then a genuinely tight window.

        ``reference_depth`` is what the tight window is tight *against*. Setting
        it equal to ``depth`` draws a chronically quiet stock, which is the
        family's defining negative: nothing contracted, so nothing happened.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 22211)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        # Long enough that the detector's prior-trend lookback, which must span
        # the reference window before it reaches the advance, has an advance to
        # reach.
        prior_sessions = 65
        rate = (1.0 + prior_gain) ** (1.0 / prior_sessions) - 1.0
        advance = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(rate, base.base_volatility * 0.6, prior_sessions)
        )
        top = float(advance[-1])

        # The reference ends at a *peak*, so a confirmed swing high sits exactly
        # where the tight window begins. A tight consolidation starts at a
        # turning point in reality, and the detector anchors on confirmed pivots
        # rather than searching window lengths -- a reference that ends
        # mid-oscillation would leave the nearest pivot several sessions back
        # and the measured window would span part of the wider range.
        reference = top * (
            1.0
            - reference_depth / 2
            + np.sin(np.linspace(0, 2.5 * np.pi, reference_sessions)) * reference_depth / 2
        )
        reference = reference * (1.0 + rng.normal(0.0, reference_depth * 0.15, reference_sessions))

        anchor = float(reference[-1])
        window = anchor * (
            1.0 - depth / 2 - np.sin(np.linspace(0, 2.0 * np.pi, sessions)) * depth / 2
        )
        window = window * (1.0 + rng.normal(0.0, depth * 0.10, sessions))

        closes = np.concatenate([lead, advance, reference, window])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 1.6), 0.2, prior_sessions),
                rng.lognormal(np.log(base.base_volume), 0.2, reference_sessions),
                rng.lognormal(np.log(base.base_volume * volume_ratio), 0.15, sessions),
            ]
        )
        # Bar ranges follow the segment they belong to, so ATR contracts with
        # the window rather than staying flat while the closes narrow.
        ranges = np.concatenate(
            [
                lead * base.base_volatility * 1.3,
                advance * base.base_volatility * 1.5,
                reference * reference_depth * 0.30,
                window * depth * 0.30,
            ]
        )
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {
                "pattern": "tight_consolidation",
                "depth": depth,
                "reference_depth": reference_depth,
                "window_start_index": base.lead_in_sessions + prior_sessions + reference_sessions,
            },
        )

    def chronically_quiet(self, *, seed: int = 0) -> GeneratedSeries:
        """A stock that is always this quiet. Contracts against nothing.

        The tight-consolidation family's defining negative and the reason every
        measurement in that detector is relative rather than absolute.
        """
        return self.tight_consolidation(seed=seed + 1100, reference_depth=0.04, depth=0.035)

    def tight_after_decline(self, *, seed: int = 0) -> GeneratedSeries:
        """The same tight window with a decline behind it instead of an advance."""
        return self.tight_consolidation(seed=seed + 1110, prior_gain=-0.20)

    def breakout_retest(
        self,
        *,
        seed: int = 0,
        base_sessions: int = 55,
        base_depth: float = 0.10,
        break_strength: float = 0.07,
        break_sessions: int = 6,
        retest_sessions: int = 6,
        retest_overshoot: float = 0.0,
        hold_sessions: int = 8,
        hold: bool = True,
    ) -> GeneratedSeries:
        """A level, a break above it, a pullback to it, and a hold or a failure.

        ``retest_overshoot`` pushes the pullback *through* the level;
        ``hold=False`` keeps price below it afterwards, which is the failure
        case the detector must report as INVALIDATED rather than hide.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 23311)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        # Above every lead-in high, not merely above the last lead-in close. A
        # lead-in that wandered higher than the base leaves the series' real
        # resistance behind the base rather than at its ceiling, and the
        # detector -- correctly -- finds that one instead.
        ceiling = float(np.max(lead)) * 1.03
        # Enough cycles that the ceiling is genuinely tested several times. Two
        # touches is a coincidence, and the detector requires three across a
        # span for exactly that reason -- a base drawn with fewer produces no
        # level at all, which is the correct answer to a level that was never
        # established.
        wave = np.sin(np.linspace(0, 8.5 * np.pi, base_sessions))
        band = ceiling * (1.0 - base_depth / 2 + wave * base_depth / 2)
        band = band * (1.0 + rng.normal(0.0, base_depth * 0.10, base_sessions))

        peak = ceiling * (1.0 + break_strength)
        run = self._leg(float(band[-1]), peak, break_sessions, rng)
        retest_to = ceiling * (1.0 - retest_overshoot)
        pull = self._leg(peak, retest_to, retest_sessions, rng)
        if hold:
            after = self._leg(retest_to, peak, hold_sessions, rng)
        else:
            # A genuine turn at the level -- so a confirmed swing low forms and
            # the retest is real -- followed by the breakdown. Without the
            # bounce there is no turning point, and "price fell through and kept
            # falling" is a failed breakout rather than a failed retest.
            bounce = max(3, hold_sessions // 3)
            after = np.concatenate(
                [
                    self._leg(retest_to, retest_to * 1.03, bounce, rng),
                    self._leg(retest_to * 1.03, ceiling * 0.90, hold_sessions - bounce, rng),
                ]
            )

        closes = np.concatenate([lead, band, run, pull, after])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 0.8), 0.2, base_sessions),
                rng.lognormal(np.log(base.base_volume * 2.4), 0.2, break_sessions),
                rng.lognormal(np.log(base.base_volume * 0.7), 0.2, retest_sessions),
                rng.lognormal(np.log(base.base_volume * 1.2), 0.2, hold_sessions),
            ]
        )
        ranges = closes * base.base_volatility * 1.2
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.55),
            {
                "pattern": "breakout_retest",
                "level": ceiling,
                "break_strength": break_strength,
                "held": hold,
            },
        )

    def breakout_no_retest(self, *, seed: int = 0) -> GeneratedSeries:
        """A break that ran away and never came back. Real, and not this pattern."""
        return self.breakout_retest(seed=seed + 1200, retest_overshoot=-0.12)

    def failed_retest(self, *, seed: int = 0) -> GeneratedSeries:
        """A genuine retest that then breaks down.

        The pullback reaches the level -- so the structure is real and must be
        *found* -- and price then loses it, which is what the state machine is
        for. A series that fell straight through to ten percent below would be a
        failed breakout, which is a different structure and not this test.
        """
        return self.breakout_retest(seed=seed + 1210, retest_overshoot=0.01, hold=False)

    def pennant(
        self,
        *,
        seed: int = 0,
        impulse_gain: float = 0.22,
        impulse_sessions: int = 9,
        sessions: int = 9,
        convergence: float = 0.35,
        symmetric: bool = True,
    ) -> GeneratedSeries:
        """A sharp impulse then a converging wedge.

        ``symmetric=False`` holds the lower boundary flat while the upper falls,
        which is a descending wedge -- the negative that separates a pennant
        from every other converging structure.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 16611)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        rate = (1.0 + impulse_gain) ** (1.0 / impulse_sessions) - 1.0
        impulse = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(rate, base.base_volatility * 0.5, impulse_sessions)
        )
        peak = float(impulse.max())

        # Both boundaries close on the midpoint; alternate between them so the
        # bars actually touch each side.
        opening = peak * 0.09
        widths = np.linspace(opening, opening * convergence, sessions)
        midpoint = peak * 0.97
        offsets = np.array([(1 if i % 2 == 0 else -1) for i in range(sessions)])
        if not symmetric:
            # Lower boundary held flat: only the highs come down.
            offsets = np.where(offsets > 0, offsets, 0.0)
            wedge = midpoint - opening / 2 + widths * (offsets + 0.5)
        else:
            wedge = midpoint + offsets * widths / 2
        wedge = wedge * (1.0 + rng.normal(0.0, base.base_volatility * 0.3, sessions))

        closes = np.concatenate([lead, impulse, wedge])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 2.2), 0.2, impulse_sessions),
                rng.lognormal(np.log(base.base_volume * 0.6), 0.2, sessions),
            ]
        )
        ranges = np.concatenate(
            [
                lead * base.base_volatility * 1.3,
                impulse * base.base_volatility * 1.8,
                wedge * widths / peak * 0.6,
            ]
        )
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.55),
            {
                "pattern": "pennant" if symmetric else "descending_wedge_pennant",
                "impulse_gain": impulse_gain,
                "expected_peak": peak,
            },
        )

    def parallel_flag_channel(self, *, seed: int = 0) -> GeneratedSeries:
        """An impulse then a *parallel* channel. A flag, explicitly not a pennant."""
        return self.pennant(seed=seed + 500, convergence=0.95)

    def cup_handle(
        self,
        *,
        seed: int = 0,
        prior_gain: float = 0.35,
        cup_sessions: int = 60,
        depth: float = 0.22,
        roundness: float = 0.5,
        handle_sessions: int = 10,
        handle_depth_ratio: float = 0.20,
        right_rim_shortfall: float = 0.0,
    ) -> GeneratedSeries:
        """An advance, a rounded decline and recovery, then a shallow handle.

        ``roundness`` is the exponent on the bottom shape and is the whole
        argument of the cup-versus-V distinction. Below 1 the trough is broad
        and price spends sessions near the low; above 1 it narrows toward a
        point. The detector measures time-near-low rather than fitting a curve,
        so this knob moves exactly the quantity under test.

        ``right_rim_shortfall`` leaves the recovery short of the left rim --
        an incomplete recovery, which is the rim-symmetry negative.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 17711)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        prior_sessions = 50
        rate = (1.0 + prior_gain) ** (1.0 / prior_sessions) - 1.0
        advance = float(lead[-1]) * np.cumprod(
            1.0 + rng.normal(rate, base.base_volatility * 0.6, prior_sessions)
        )
        rim = float(advance.max())

        # sin(pi*u) is 0 at both rims and 1 at the midpoint; the exponent
        # controls how much time is spent at the bottom.
        u = np.linspace(0.0, 1.0, cup_sessions)
        shape = np.sin(np.pi * u) ** roundness
        recovery_ceiling = 1.0 - right_rim_shortfall * u  # tilts the right rim down
        cup = rim * (1.0 - depth * shape) * recovery_ceiling
        cup = cup * (1.0 + rng.normal(0.0, base.base_volatility * 0.35, cup_sessions))

        right_rim = float(cup[-1])
        handle_depth = depth * handle_depth_ratio
        drift = np.linspace(0.0, 1.0, handle_sessions)
        handle = right_rim * (1.0 - handle_depth * np.sin(np.pi * drift) ** 0.8)
        handle = handle * (1.0 + rng.normal(0.0, base.base_volatility * 0.3, handle_sessions))

        closes = np.concatenate([lead, advance, cup, handle])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 1.6), 0.2, prior_sessions),
                # Volume dries up into the cup low and returns on the right side.
                rng.lognormal(np.log(base.base_volume), 0.22, cup_sessions)
                * (0.55 + 0.45 * np.abs(u - 0.5) * 2),
                rng.lognormal(np.log(base.base_volume * 0.55), 0.2, handle_sessions),
            ]
        )
        ranges = np.concatenate(
            [
                lead * base.base_volatility * 1.3,
                advance * base.base_volatility * 1.5,
                cup * base.base_volatility * 1.2,
                handle * base.base_volatility * 0.8,
            ]
        )
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {
                "pattern": "cup_handle",
                "expected_rim": rim,
                "depth": depth,
                "roundness": roundness,
                "cup_start_index": base.lead_in_sessions + prior_sessions,
            },
        )

    def v_bottom(self, *, seed: int = 0) -> GeneratedSeries:
        """The same decline and recovery, reversed at a point rather than rounded.

        The cup's defining negative: identical depth, duration and rims, and no
        time spent at the low.
        """
        return self.cup_handle(seed=seed + 600, roundness=4.0)

    def cup_without_handle(self, *, seed: int = 0) -> GeneratedSeries:
        """A rounded base that runs straight back to the rim. Not a cup *and handle*."""
        return self.cup_handle(seed=seed + 610, handle_sessions=2, handle_depth_ratio=0.01)

    def incomplete_recovery(self, *, seed: int = 0) -> GeneratedSeries:
        """A cup whose right rim finishes well below the left."""
        return self.cup_handle(seed=seed + 620, right_rim_shortfall=0.18)

    def high_tight_flag(
        self,
        *,
        seed: int = 0,
        advance: float = 1.10,
        advance_sessions: int = 30,
        pause_sessions: int = 10,
        pause_depth: float = 0.12,
        gap_share: float = 0.0,
    ) -> GeneratedSeries:
        """A near-vertical advance and a shallow pause.

        ``advance`` defaults above the detector's 70% floor and ``pause_depth``
        below its 25% ceiling. Lowering ``advance`` to ordinary-flag territory is
        how the tests check the detector has not diluted its own definition.

        ``gap_share`` delivers a fraction of the advance in a single overnight
        gap, which is a repricing rather than accumulation and should show up in
        ``advance_consistency`` rather than in the headline magnitude.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 18811)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        start = float(lead[-1])
        target = start * (1.0 + advance)

        continuous = (1.0 + advance) ** (1.0 - gap_share)
        rate = continuous ** (1.0 / advance_sessions) - 1.0
        steps = 1.0 + rng.normal(rate, base.base_volatility * 0.5, advance_sessions)
        gaps = np.zeros(advance_sessions)
        if gap_share > 0:
            jump = (1.0 + advance) ** gap_share
            at = advance_sessions // 2
            steps[at] *= jump
            gaps[at] = jump - 1.0
        run = start * np.cumprod(steps)
        # Land on the stated advance regardless of the noise draw, so the test's
        # magnitude assertion is about the spec rather than about the seed.
        run = run * (target / float(run[-1]))
        peak = float(run.max())

        # A shallow drift down and sideways, never breaching the stated depth.
        u = np.linspace(0.0, 1.0, pause_sessions)
        pause = peak * (1.0 - pause_depth * np.sin(np.pi * u) ** 0.7)
        pause = pause * (1.0 + rng.normal(0.0, base.base_volatility * 0.4, pause_sessions))
        pause = np.minimum(pause, peak * 0.999)
        pause = np.maximum(pause, peak * (1.0 - pause_depth * 0.98))

        closes = np.concatenate([lead, run, pause])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                rng.lognormal(np.log(base.base_volume * 4.0), 0.25, advance_sessions),
                rng.lognormal(np.log(base.base_volume * 0.9), 0.2, pause_sessions),
            ]
        )
        ranges = np.concatenate(
            [
                lead * base.base_volatility * 1.3,
                run * base.base_volatility * 2.4,
                pause * base.base_volatility * 1.0,
            ]
        )
        all_gaps = np.concatenate([np.zeros(base.lead_in_sessions), gaps, np.zeros(pause_sessions)])
        return GeneratedSeries(
            self._bars_from(
                closes,
                volumes,
                ranges,
                close_position=0.7,
                gaps=all_gaps,
            ),
            {
                "pattern": "high_tight_flag",
                "advance": advance,
                "expected_peak": peak,
                "pause_depth": pause_depth,
                "advance_start_index": base.lead_in_sessions,
            },
        )

    def ordinary_bull_flag_for_htf(self, *, seed: int = 0) -> GeneratedSeries:
        """A perfectly ordinary bull flag: 25% advance, 10% pause.

        The high tight flag's primary negative. A detector that fires here has
        absorbed the bull flag universe and become a rename rather than a
        distinct family.
        """
        return self.high_tight_flag(seed=seed + 700, advance=0.25, advance_sessions=22)

    def slow_double(self, *, seed: int = 0) -> GeneratedSeries:
        """A 100% advance taken slowly. Magnitude without thrust: a trend, not a flag."""
        return self.high_tight_flag(seed=seed + 710, advance=1.00, advance_sessions=120)

    def deep_pause_after_thrust(self, *, seed: int = 0) -> GeneratedSeries:
        """A genuine thrust followed by a 40% correction. No longer tight."""
        return self.high_tight_flag(seed=seed + 720, pause_depth=0.40, pause_sessions=18)

    # -- reversal structures -------------------------------------------------

    def _leg(
        self,
        start: float,
        end: float,
        sessions: int,
        rng: np.random.Generator,
        wobble: float = 0.35,
    ) -> np.ndarray:
        """A price path from ``start`` to ``end`` with noise that lands on ``end``.

        Reversal structures are built from legs rather than from a closed-form
        shape, because what defines them is a *sequence* of turning points and
        the generator has to place those points exactly where the test says
        they are.
        """
        path = np.linspace(start, end, sessions + 1)[1:]
        noise = rng.normal(0.0, self.spec.base_volatility * wobble, sessions)
        noise[-1] = 0.0  # land on the stated turning point
        return path * (1.0 + noise)

    def double_bottom(
        self,
        *,
        seed: int = 0,
        prior_decline: float = 0.28,
        decline_sessions: int = 35,
        separation: int = 30,
        rally: float = 0.16,
        undercut: float = 0.02,
        recovery_sessions: int = 12,
        second_low_volume: float = 0.6,
    ) -> GeneratedSeries:
        """A decline, a low, a rally, a second low at the same level, a recovery.

        ``undercut`` places the second low *below* the first, which is the
        constructive case. Negative values place it above.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 19911)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        peak = float(lead[-1])
        low1 = peak * (1.0 - prior_decline)
        middle = low1 * (1.0 + rally)
        low2 = low1 * (1.0 - undercut)

        up = separation // 2
        down = separation - up
        decline = self._leg(peak, low1, decline_sessions, rng)
        first_rally = self._leg(low1, middle, up, rng)
        second_fall = self._leg(middle, low2, down, rng)
        recovery = self._leg(low2, middle * 0.99, recovery_sessions, rng)

        closes = np.concatenate([lead, decline, first_rally, second_fall, recovery])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                # Heaviest into the first low: that is the capitulation.
                rng.lognormal(np.log(base.base_volume * 1.8), 0.25, decline_sessions),
                rng.lognormal(np.log(base.base_volume * 0.9), 0.2, up),
                rng.lognormal(np.log(base.base_volume * second_low_volume), 0.2, down),
                rng.lognormal(np.log(base.base_volume * 1.1), 0.2, recovery_sessions),
            ]
        )
        ranges = closes * base.base_volatility * 1.4
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {
                "pattern": "double_bottom",
                "prior_decline": prior_decline,
                "first_low": low1,
                "second_low": low2,
                "neckline": middle,
                "first_low_index": base.lead_in_sessions + decline_sessions - 1,
            },
        )

    def descending_double_low(self, *, seed: int = 0) -> GeneratedSeries:
        """A second low well below the first. A continued decline, not a bottom."""
        return self.double_bottom(seed=seed + 800, undercut=0.18)

    def range_double_low(self, *, seed: int = 0) -> GeneratedSeries:
        """Two lows at the same level with nothing to reverse.

        The double bottom's defining negative: identical geometry, no prior
        decline. A range is not a bottom.
        """
        return self.double_bottom(seed=seed + 810, prior_decline=0.02)

    def single_low_with_noise(self, *, seed: int = 0) -> GeneratedSeries:
        """One low with a 2% wobble in the middle. Not two lows."""
        return self.double_bottom(seed=seed + 820, rally=0.02)

    def inverse_head_shoulders(
        self,
        *,
        seed: int = 0,
        prior_decline: float = 0.24,
        decline_sessions: int = 30,
        prominence: float = 0.12,
        shoulder_asymmetry: float = 0.0,
        neckline_slope: float = 0.0,
        half_sessions: int = 22,
        timing_skew: float = 1.0,
        recovery_sessions: int = 10,
    ) -> GeneratedSeries:
        """Left shoulder, head, right shoulder, with a neckline through the rallies.

        ``prominence`` is how far the head sits below the shallower shoulder.
        ``shoulder_asymmetry`` makes the right shoulder that fraction shallower
        **below the neckline**, which is how the detector measures shoulder
        depth and is the only definition under which the parameter stays
        meaningful once ``neckline_slope`` tilts the line: expressed as a
        fraction of price instead, a 25% asymmetry lifts the right shoulder
        above its own neckline and the structure stops containing a third low
        at all.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 20011)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        peak = float(lead[-1])
        left_shoulder = peak * (1.0 - prior_decline)
        neck_left = left_shoulder * 1.10
        neck_right = neck_left * (1.0 + neckline_slope)
        left_depth = neck_left - left_shoulder
        right_shoulder = neck_right - left_depth * (1.0 - shoulder_asymmetry)
        head = min(left_shoulder, right_shoulder) * (1.0 - prominence)

        first = max(4, int(half_sessions * timing_skew))
        second = half_sessions
        legs = [
            self._leg(peak, left_shoulder, decline_sessions, rng),
            self._leg(left_shoulder, neck_left, first // 2, rng),
            self._leg(neck_left, head, first - first // 2, rng),
            self._leg(head, neck_right, second // 2, rng),
            self._leg(neck_right, right_shoulder, second - second // 2, rng),
            self._leg(right_shoulder, neck_right * 0.98, recovery_sessions, rng),
        ]
        volume_scales = (1.6, 1.0, 2.0, 1.0, 0.55, 0.9)
        closes = np.concatenate([lead, *legs])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                *(
                    rng.lognormal(np.log(base.base_volume * scale), 0.2, len(leg))
                    for scale, leg in zip(volume_scales, legs, strict=True)
                ),
            ]
        )
        ranges = closes * base.base_volatility * 1.4
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {
                "pattern": "inverse_head_shoulders",
                "left_shoulder": left_shoulder,
                "head": head,
                "right_shoulder": right_shoulder,
                "prominence": prominence,
                "left_shoulder_index": base.lead_in_sessions + decline_sessions - 1,
            },
        )

    def triple_bottom(self, *, seed: int = 0) -> GeneratedSeries:
        """Three lows at the same level. No head, so not this pattern."""
        return self.inverse_head_shoulders(seed=seed + 900, prominence=0.01)

    def head_and_shoulders_top(self, *, seed: int = 0) -> GeneratedSeries:
        """The bearish mirror: three highs with the middle one highest.

        The adversarial case for a bottom detector. Whatever it reports here it
        must not report as a strong bullish reversal.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 20111)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0005, base.base_volatility, base.lead_in_sessions))
        )
        trough = float(lead[-1])
        shoulder = trough * 1.22
        head_price = trough * 1.38
        neck = trough * 1.02
        legs = [
            self._leg(trough, shoulder, 18, rng),
            self._leg(shoulder, neck, 12, rng),
            self._leg(neck, head_price, 18, rng),
            self._leg(head_price, neck, 14, rng),
            self._leg(neck, shoulder, 14, rng),
            self._leg(shoulder, neck * 0.95, 12, rng),
        ]
        closes = np.concatenate([lead, *legs])
        volumes = rng.lognormal(np.log(base.base_volume), 0.25, len(closes))
        ranges = closes * base.base_volatility * 1.4
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "head_and_shoulders_top"},
        )

    def bear_flag(self, *, seed: int = 0) -> GeneratedSeries:
        """A downward pole with an upward drift. Must never score as bullish.

        The mirror image is the most instructive negative: every volume and
        volatility characteristic of a good bull flag is present, and only the
        direction differs.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 991)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0005, base.base_volatility, base.lead_in_sessions))
        )
        pole = float(lead[-1]) * np.exp(
            np.cumsum(np.log1p(rng.normal(-0.022, base.base_volatility * 0.5, 12)))
        )
        drift = float(pole[-1]) * (1.0 + np.linspace(0.0, 0.08, 9))
        closes = np.concatenate([lead, pole, drift])
        volumes = rng.lognormal(np.log(base.base_volume), 0.3, len(closes))
        ranges = closes * base.base_volatility * 1.5
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.35),
            {"pattern": "bear_flag"},
        )

    def falling_knife(self, *, seed: int = 0) -> GeneratedSeries:
        """A sustained decline. No bullish structure exists here."""
        rng = np.random.default_rng(self.spec.seed + seed + 313)
        n = self.spec.lead_in_sessions + 40
        closes = self.spec.start_price * np.exp(
            np.cumsum(rng.normal(-0.012, self.spec.base_volatility * 1.5, n))
        )
        volumes = rng.lognormal(np.log(self.spec.base_volume * 1.4), 0.35, n)
        ranges = closes * self.spec.base_volatility * 2.2
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.3),
            {"pattern": "falling_knife"},
        )

    def single_day_spike(self, *, seed: int = 0) -> GeneratedSeries:
        """One enormous session, then nothing.

        A one-day gap is not a flagpole. The advance has no duration, no
        accumulation and no structure, and a detector that scores it well has
        confused magnitude with quality.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 77)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        spike = float(lead[-1]) * 1.35
        after = spike * (1.0 + rng.normal(0.0, base.base_volatility * 0.6, 12))
        closes = np.concatenate([lead, [spike], after])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                [base.base_volume * 12],
                rng.lognormal(np.log(base.base_volume * 1.5), 0.25, 12),
            ]
        )
        ranges = closes * base.base_volatility * 1.5
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges), {"pattern": "single_day_spike"}
        )

    def gap_and_fade(self, *, seed: int = 0) -> GeneratedSeries:
        """A post-earnings gap that gives everything back.

        The consolidation-shaped trap: it looks like a pause because the range
        narrows, but price is bleeding back through the gap rather than holding
        above it.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 451)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        gap = float(lead[-1]) * 1.18
        fade = (
            gap
            * np.linspace(1.0, 0.86, 14)
            * (1.0 + rng.normal(0.0, base.base_volatility * 0.5, 14))
        )
        closes = np.concatenate([lead, [gap], fade])
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                [base.base_volume * 8],
                rng.lognormal(np.log(base.base_volume * 2.0), 0.25, 14),
            ]
        )
        ranges = closes * base.base_volatility * 1.6
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.35),
            {"pattern": "gap_and_fade"},
        )

    def broad_volatile_range(self, *, seed: int = 0) -> GeneratedSeries:
        """A wide, noisy sideways range. Not a consolidation.

        The difference between a base and a range is compression, and this has
        none: the amplitude is constant throughout.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 1201)
        n = self.spec.lead_in_sessions + 50
        oscillation = np.sin(np.linspace(0, 6 * np.pi, n)) * 0.14
        closes = self.spec.start_price * (1.0 + oscillation + rng.normal(0.0, 0.03, n))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.4, n)
        ranges = closes * 0.045
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "broad_volatile_range"},
        )

    def parabolic(self, *, seed: int = 0) -> GeneratedSeries:
        """An accelerating advance with no pause. Overextension, not a flag."""
        rng = np.random.default_rng(self.spec.seed + seed + 1777)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0005, base.base_volatility, base.lead_in_sessions))
        )
        acceleration = np.linspace(0.01, 0.06, 25)
        run = float(lead[-1]) * np.cumprod(1.0 + acceleration)
        closes = np.concatenate([lead, run])
        volumes = rng.lognormal(np.log(base.base_volume * 3), 0.3, len(closes))
        ranges = closes * base.base_volatility * 2.5
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.7),
            {"pattern": "parabolic"},
        )

    def mean_reverting(self, *, seed: int = 0, strength: float = 0.08) -> GeneratedSeries:
        """An Ornstein-Uhlenbeck process: pulled back toward its own mean.

        A distinct negative from a random walk. Mean reversion manufactures
        repeated tests of the same levels, which is exactly what makes a
        detector see resistance and support where there is only a restoring
        force. Any pattern engine that keys on "price touched this level three
        times" finds structure here.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 3301)
        n = self.spec.lead_in_sessions + 80
        level = np.log(self.spec.start_price)
        path = np.empty(n)
        value = level
        for i in range(n):
            value += strength * (level - value) + rng.normal(0.0, self.spec.base_volatility)
            path[i] = value
        closes = np.exp(path)
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.3, n)
        ranges = closes * self.spec.base_volatility * 1.3
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "mean_reverting"},
        )

    def autocorrelated_noise(self, *, seed: int = 0, phi: float = 0.45) -> GeneratedSeries:
        """Returns with positive serial correlation and no structure.

        The most instructive negative in the corpus. Autocorrelated returns
        produce runs -- streaks of up days and down days -- which look exactly
        like impulse legs and pullbacks without any of the market behaviour a
        flag is supposed to represent. A detector fooled here is keying on
        momentum persistence alone.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 4409)
        n = self.spec.lead_in_sessions + 80
        shocks = rng.normal(0.0, self.spec.base_volatility, n)
        returns = np.empty(n)
        previous = 0.0
        for i in range(n):
            previous = phi * previous + shocks[i]
            returns[i] = previous
        closes = self.spec.start_price * np.exp(np.cumsum(returns))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.3, n)
        ranges = closes * self.spec.base_volatility * 1.4
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "autocorrelated_noise"},
        )

    def regime_switching(self, *, seed: int = 0) -> GeneratedSeries:
        """Alternating quiet and violent regimes with no directional structure.

        Tests whether volatility *contraction* is being measured or merely
        volatility *level*. A detector that rewards the quiet half without
        reference to what preceded it scores this well.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 5501)
        segments = []
        volatilities = [0.006, 0.030, 0.008, 0.026, 0.010]
        for i, vol in enumerate(volatilities):
            segments.append(rng.normal(0.0002 * (-1) ** i, vol, 28))
        returns = np.concatenate(segments)
        closes = self.spec.start_price * np.exp(np.cumsum(returns))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.4, len(closes))
        ranges = closes * np.repeat(volatilities, 28) * 1.5
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "regime_switching"},
        )

    def quiet_drift(self, *, seed: int = 0) -> GeneratedSeries:
        """A low-volatility sideways stretch with no prior advance.

        The negative that separates a *base* from a *flat patch*. The geometry
        is genuinely tight and genuinely clean; what is missing is the context
        that makes tightness meaningful, which is why prior trend is a primitive
        rather than an afterthought.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 6607)
        n = self.spec.lead_in_sessions + 60
        closes = self.spec.start_price * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
        volumes = rng.lognormal(np.log(self.spec.base_volume * 0.6), 0.2, n)
        ranges = closes * 0.006
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "quiet_drift"},
        )

    def choppy_range(self, *, seed: int = 0) -> GeneratedSeries:
        """Rapid alternation inside a band. Noise with a ceiling and a floor."""
        rng = np.random.default_rng(self.spec.seed + seed + 7717)
        n = self.spec.lead_in_sessions + 70
        oscillation = np.sin(np.linspace(0, 14 * np.pi, n)) * 0.06
        closes = self.spec.start_price * (1.0 + oscillation + rng.normal(0.0, 0.02, n))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.5, n)
        ranges = closes * 0.03
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "choppy_range"},
        )

    def broadening_formation(self, *, seed: int = 0) -> GeneratedSeries:
        """Expanding range: the exact inverse of every contraction pattern.

        A detector that measures net range change rather than the *direction*
        of that change could score this as compression, since the window ends
        near where it began.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 8821)
        n = self.spec.lead_in_sessions + 60
        amplitude = np.linspace(0.01, 0.16, n)
        oscillation = np.sin(np.linspace(0, 8 * np.pi, n)) * amplitude
        closes = self.spec.start_price * (1.0 + oscillation + rng.normal(0.0, 0.01, n))
        volumes = rng.lognormal(np.log(self.spec.base_volume), 0.45, n)
        ranges = closes * amplitude * 0.8
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "broadening_formation"},
        )

    def deep_pullback(self, *, seed: int = 0) -> GeneratedSeries:
        """A real advance given almost entirely back.

        The borderline case that matters most, because everything about it is
        right except the one thing that is not: an 85% retracement is not a
        pause, it is the advance being reversed.
        """
        return self.bull_flag(BullFlagSpec(retracement=0.85, flag_sessions=12), seed=seed + 90)

    def failed_base(self, *, seed: int = 0) -> GeneratedSeries:
        """A sound-looking consolidation that breaks down out of it."""
        return self.bull_flag(
            BullFlagSpec(retracement=0.4, flag_sessions=14, breakdown=0.28), seed=seed + 91
        )

    def earnings_gap(self, *, seed: int = 0) -> GeneratedSeries:
        """A single overnight repricing followed by a tight range.

        Distinct from `gap_and_fade`: price *holds* the gap here rather than
        bleeding back. The structure is genuinely tight and the advance
        genuinely happened -- in one session, with no accumulation, which is the
        whole question.
        """
        rng = np.random.default_rng(self.spec.seed + seed + 9931)
        base = self.spec
        lead = base.start_price * np.exp(
            np.cumsum(rng.normal(0.0, base.base_volatility, base.lead_in_sessions))
        )
        gap_level = float(lead[-1]) * 1.22
        after = gap_level * (1.0 + rng.normal(0.0, 0.008, 16))
        closes = np.concatenate([lead, [gap_level], after])
        gaps = np.zeros(len(closes))
        gaps[base.lead_in_sessions] = 0.22
        volumes = np.concatenate(
            [
                rng.lognormal(np.log(base.base_volume), 0.25, base.lead_in_sessions),
                [base.base_volume * 10],
                rng.lognormal(np.log(base.base_volume * 1.3), 0.25, 16),
            ]
        )
        ranges = closes * base.base_volatility
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.55, gaps=gaps),
            {"pattern": "earnings_gap"},
        )

    def trending_walk(self, *, seed: int = 0, drift: float = 0.0018) -> GeneratedSeries:
        """A random walk with drift. Structure-free but directional.

        Separated from the plain walk because drift alone manufactures impulse
        legs, and a detector's false-positive rate on trending noise is a
        different number from its rate on flat noise.
        """
        return self.random_walk(140, drift=drift, seed=seed + 200)

    def low_volume_noise(self, *, seed: int = 0) -> GeneratedSeries:
        """A thin, drifting security with sporadic volume."""
        rng = np.random.default_rng(self.spec.seed + seed + 2003)
        n = self.spec.lead_in_sessions + 30
        closes = self.spec.start_price * np.exp(np.cumsum(rng.normal(0.0, 0.008, n)))
        volumes = rng.lognormal(np.log(4_000), 1.2, n)
        ranges = closes * 0.01
        return GeneratedSeries(
            self._bars_from(closes, volumes, ranges, close_position=0.5),
            {"pattern": "low_volume_noise"},
        )


def _price(value: float) -> Decimal:
    """Round to a cent, the way a real tape does."""
    return Decimal(f"{max(value, 0.01):.4f}")
