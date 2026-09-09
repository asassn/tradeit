#!/usr/bin/env python
"""Which of the scoring factors actually predict anything.

Every weight in ``ScoringConfig`` is a judgement nobody has measured. The
roadmap says so plainly -- Phase 7 ships "declared, transparent, **unvalidated**
weights" to be "replaced by measured ones later" -- and this is the measurement.

It produces evidence, not a change. Scoring weights are strategy parameters and
moving one needs an explicit decision; this script's job is to make that
decision an informed one.

What it does about the four ways a study like this lies to itself
-----------------------------------------------------------------

**Survivorship.** The universe is built from the prices: every security
tradeable at the start of the window, whether or not it lasted. A study run on
today's survivors has already been filtered by the outcome it is measuring.

**Overlapping observations.** Sampling is non-overlapping by default -- one
observation per security per horizon -- so consecutive observations share no
window and the t-statistic means what it says. The stride is passed to
:class:`SignalStudy` either way, so a denser sampling is corrected rather than
quietly believed.

**Costs.** Statistical detectability is reported separately from whether
anything survives spread, slippage, commission and the turnover the horizon
forces. Only the second decides the verdict.

**Multiple comparisons.** Every signal tested is a trial, and testing eight
signals across three horizons is twenty-four of them. The hurdle rises
accordingly, and the run prints how many trials it ran.

Why fully-adjusted prices are safe here, which is not obvious
--------------------------------------------------------------

Signals are computed on split-adjusted series, and a series adjusted for a
split that had not yet happened is exactly the kind of future information this
codebase exists to keep out. It is admissible here for a specific reason:
**every signal used is scale-invariant.**

A split after session T multiplies *every* bar before T by the same factor. A
ratio -- momentum, RSI, distance from a moving average, relative volume, ATR as
a percentage -- is unchanged by a uniform rescaling of its whole input window.
So for these signals the adjusted series and the series a trader actually saw
give identical values, and the shortcut costs nothing.

**It would not be admissible for a level-based signal** -- price above $10,
distance from a fixed dollar threshold, raw ATR -- and the runner refuses to
compute one, rather than leaving the reasoning as a comment nobody checks.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.analytics.kernels import (
    atr_percent,
    distance_from,
    rate_of_change,
    realized_volatility,
    relative_volume,
    rsi,
    sma,
)
from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.research01.series import price_series
from tradeit.signals.study import (
    Observation,
    PromotionRule,
    SignalStudy,
    SignalVerdict,
    StudyTarget,
    TargetKind,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import CostConfig, StrategyConfig


@dataclass(frozen=True)
class SignalSpec:
    """A signal, and the assertion that it is safe to compute this way.

    ``scale_invariant`` is not documentation. The runner refuses to use a spec
    that is not, because the whole argument for computing on adjusted prices
    rests on it.
    """

    name: str
    compute: Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], np.ndarray]
    warmup: int
    scale_invariant: bool
    maps_to: str


def _build_signals() -> tuple[SignalSpec, ...]:
    """Built as a function so each lambda's closure is explicit."""

    def mom(period: int) -> Callable[..., np.ndarray]:
        return lambda close, high, low, volume: rate_of_change(close, period)

    def dist_sma(period: int) -> Callable[..., np.ndarray]:
        return lambda close, high, low, volume: distance_from(close, sma(close, period))

    return (
        SignalSpec("momentum_21", mom(21), 22, True, "relative_strength"),
        SignalSpec("momentum_126", mom(126), 127, True, "relative_strength"),
        SignalSpec("momentum_252", mom(252), 253, True, "relative_strength"),
        SignalSpec("dist_from_sma_50", dist_sma(50), 51, True, "pattern_quality"),
        SignalSpec("dist_from_sma_200", dist_sma(200), 201, True, "pattern_quality"),
        SignalSpec(
            "rsi_14",
            lambda close, high, low, volume: rsi(close, 14),
            15,
            True,
            "pattern_quality",
        ),
        SignalSpec(
            "relative_volume_20",
            lambda close, high, low, volume: relative_volume(volume, 20),
            22,
            True,
            "volume_accumulation",
        ),
        SignalSpec(
            "atr_percent_14",
            lambda close, high, low, volume: atr_percent(high, low, close, 14),
            16,
            True,
            "volatility",
        ),
        SignalSpec(
            "realized_vol_60",
            lambda close, high, low, volume: realized_volatility(close, 60),
            62,
            True,
            "volatility",
        ),
    )


#: Signals every other candidate must beat, measured on identical terms.
#: Classic momentum and classic mean reversion -- the two things anybody would
#: try first. A signal that cannot beat them is not promoted for being newer.
NAIVE_BASELINES = ("momentum_252", "rsi_14")


def _universe(path: str, start: str, end: str, min_bars: int, cap: int) -> list[int]:
    with open(path) as handle:
        spans = [
            (int(sid), first, last, int(count)) for sid, first, last, count in csv.reader(handle)
        ]
    alive = [row for row in spans if row[1] <= start <= row[2] and row[3] >= min_bars]
    died = sorted(row[0] for row in alive if row[2] < end)
    survived = sorted(row[0] for row in alive if row[2] >= end)

    def thin(ids: list[int], limit: int) -> list[int]:
        if len(ids) <= limit:
            return ids
        step = len(ids) / limit
        return [ids[int(i * step)] for i in range(limit)]

    chosen = sorted(thin(survived, cap) + thin(died, cap))
    print(
        f"universe: {len(chosen):,} securities "
        f"({len(thin(survived, cap)):,} survived, {len(thin(died, cap)):,} died)"
    )
    return chosen


def _observations(
    session: Session,
    universe: Sequence[int],
    start: dt.date,
    end: dt.date,
    horizon: int,
    stride: int,
) -> tuple[dict[str, list[Observation]], float, int]:
    """Signal readings paired with the return that followed them."""
    specs = _build_signals()
    for spec in specs:
        if not spec.scale_invariant:
            raise ValueError(
                f"{spec.name} is not scale-invariant and cannot be computed on "
                "split-adjusted prices without leaking future splits"
            )
    out: dict[str, list[Observation]] = {spec.name: [] for spec in specs}
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)
    forward: list[float] = []
    prices: list[float] = []
    used = 0

    for security_id in universe:
        bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
        if len(bars) < horizon + 260:
            continue
        used += 1
        close = np.array([float(b.close) for b in bars])
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        volume = np.array([float(b.volume) for b in bars])
        prices.append(float(np.median(close)))

        computed = {spec.name: spec.compute(close, high, low, volume) for spec in specs}
        warmup = max(spec.warmup for spec in specs)
        for i in range(warmup, len(bars) - horizon, stride):
            if close[i] <= 0:
                continue
            outcome = float(close[i + horizon] / close[i] - 1.0)
            forward.append(outcome)
            for spec in specs:
                value = computed[spec.name][i]
                if not np.isfinite(value):
                    continue
                out[spec.name].append(
                    Observation(
                        session_date=bars[i].session_date,
                        instrument_id=security_id,
                        signal=float(value),
                        outcome=outcome,
                    )
                )
    # Geometric, not arithmetic. The arithmetic mean of cross-sectional returns
    # is dominated by the few names that went up several hundred percent, and
    # reports a "buy and hold" nobody could have earned: on this universe it
    # reads 21.9%/yr for 2000-2010, a decade the market spent flat.
    kept = [r for r in forward if r > -1.0]
    mean_forward = float(np.expm1(np.mean(np.log1p(kept)))) if kept else 0.0
    median_price = float(np.median(prices)) if prices else 1.0
    print(f"  {used:,} securities had enough history; {len(forward):,} forward returns")
    return out, mean_forward, int(median_price)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=200, help="securities per arm")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--horizons", default="21,63")
    ap.add_argument("--min-observations", type=int, default=500)
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--quantile", type=float, default=0.2)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    horizons = [int(h) for h in args.horizons.split(",")]
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    costs: CostConfig = StrategyConfig(name="baseline").costs
    rule = PromotionRule(
        min_observations=args.min_observations,
        min_abs_t_statistic=args.min_t,
        quantile_fraction=args.quantile,
    )

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    specs = _build_signals()
    trials = len(specs) * len(horizons)
    print(f"\n{len(specs)} signals x {len(horizons)} horizons = {trials} trials")
    print(
        f"multiple-testing hurdle at {trials} trials: "
        f"|t| > {expected_max_of_normals(trials):.2f} on top of the {args.min_t:.1f} floor\n"
    )

    for horizon in horizons:
        stride = horizon  # non-overlapping
        print(f"=== horizon {horizon} sessions (sampled every {stride}) ===")
        t0 = time.time()
        observations, mean_forward, median_price = _observations(
            session, universe, start, end, horizon, stride
        )
        # Context, not a gate. Buy-and-hold is long-only and a quantile spread
        # is long the top and short the bottom; requiring one to beat the other
        # compares different exposures. The gates are the naive signals below,
        # which are measured on identical terms. Compounded, not multiplied:
        # the geometric mean has to be compounded to annualise it.
        buy_and_hold = (1.0 + mean_forward) ** (252.0 / horizon) - 1.0
        print(
            f"  equal-weight buy-and-hold (long-only, context only): "
            f"{buy_and_hold:.2%}/yr  median price ${median_price}  "
            f"[{time.time() - t0:.0f}s]"
        )

        target = StudyTarget(
            kind=TargetKind.FORWARD_RETURN,
            horizon_sessions=horizon,
            description=f"{horizon}-session forward total return",
        )
        studies = {
            spec.name: SignalStudy(
                name=spec.name,
                target=target,
                observations=tuple(observations[spec.name]),
                sampling_stride_sessions=stride,
            )
            for spec in specs
        }
        nets = {
            name: study.net_annual_spread(args.quantile, costs, average_price=float(median_price))
            for name, study in studies.items()
        }
        # The roadmap's requirement made apples-to-apples: a candidate beats the
        # naive signals measured the same way, not a long-only hold.
        baselines = {
            f"naive_{name}": nets[name] for name in NAIVE_BASELINES if nets.get(name) is not None
        }
        shown = ", ".join(f"{k} {v:.1%}" for k, v in baselines.items()) or "none"
        print(f"  naive baselines to beat: {shown}")

        rows = []
        for spec in specs:
            study = studies[spec.name]
            # A baseline is not asked to beat itself.
            hurdles = {k: v for k, v in baselines.items() if k != f"naive_{spec.name}"}
            verdict, reason = study.verdict(
                rule,
                costs,
                average_price=float(median_price),
                baselines=hurdles,
            )
            rows.append((spec, study, verdict, nets[spec.name], reason))

        rows.sort(key=lambda r: (r[3] is None, -(r[3] or 0)))
        print(
            f"  {'signal':<20}{'maps to':<20}{'n':>7}{'IC':>7}{'t':>7}"
            f"{'mean sp':>9}{'med sp':>8}{'net/yr':>9}  verdict"
        )
        for spec, study, verdict, net, _ in rows:
            mean_spread = study.quantile_spread(args.quantile)
            median_spread = study.robust_quantile_spread(args.quantile)
            ic = study.information_coefficient()
            t_stat = study.t_statistic()
            print(
                f"  {spec.name:<20}{spec.maps_to:<20}{study.count:>7,}"
                f"{'' if ic is None else f'{ic:>7.3f}'}"
                f"{'' if t_stat is None else f'{t_stat:>7.2f}'}"
                f"{'' if mean_spread is None else f'{mean_spread:>9.2%}'}"
                f"{'' if median_spread is None else f'{median_spread:>8.2%}'}"
                f"{'' if net is None else f'{net:>9.1%}'}  {verdict}"
            )
        useful = [r for r in rows if r[2] is SignalVerdict.ECONOMICALLY_USEFUL]
        print(f"  -> {len(useful)} of {len(rows)} economically useful\n")

    print(
        "Evidence, not a change. Scoring weights are strategy parameters and moving\n"
        "one is a decision, not a consequence of this run. And no result here is\n"
        "proof of profitability: the corpus gate reads SURVIVOR_BIASED overall,\n"
        "though this universe deliberately includes the companies that failed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
