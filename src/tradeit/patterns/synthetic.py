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
