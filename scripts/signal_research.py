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
    Orientation,
    PromotionRule,
    SignalStudy,
    SignalVerdict,
    StudyTarget,
    TargetKind,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import CostConfig, LiquidityConfig, StrategyConfig


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
    #: Declared in advance from the published literature, not from this data.
    #:
    #: Momentum trends at 6-12 months and reverses at one; RSI mean-reverts;
    #: low volatility outperforms. **``dist_from_sma_50`` is declared POSITIVE
    #: and the corpus disagrees with it.** It is left that way on purpose: a
    #: prior that gets flipped whenever the data disagrees is not a prior, and
    #: the whole reason to declare a direction is to avoid paying for the free
    #: parameter that deriving one costs.
    orientation: Orientation


def _build_signals() -> tuple[SignalSpec, ...]:
    """Built as a function so each lambda's closure is explicit."""

    def mom(period: int) -> Callable[..., np.ndarray]:
        return lambda close, high, low, volume: rate_of_change(close, period)

    def dist_sma(period: int) -> Callable[..., np.ndarray]:
        return lambda close, high, low, volume: distance_from(close, sma(close, period))

    pos, neg, der = Orientation.POSITIVE, Orientation.NEGATIVE, Orientation.DERIVED
    return (
        # One-month reversal, not momentum: the classic short-horizon finding.
        SignalSpec("momentum_21", mom(21), 22, True, "relative_strength", neg),
        SignalSpec("momentum_126", mom(126), 127, True, "relative_strength", pos),
        SignalSpec("momentum_252", mom(252), 253, True, "relative_strength", pos),
        SignalSpec("dist_from_sma_50", dist_sma(50), 51, True, "pattern_quality", pos),
        SignalSpec("dist_from_sma_200", dist_sma(200), 201, True, "pattern_quality", pos),
        SignalSpec(
            "rsi_14",
            lambda close, high, low, volume: rsi(close, 14),
            15,
            True,
            "pattern_quality",
            neg,
        ),
        SignalSpec(
            "relative_volume_20",
            lambda close, high, low, volume: relative_volume(volume, 20),
            22,
            True,
            "volume_accumulation",
            der,
        ),
        SignalSpec(
            "atr_percent_14",
            lambda close, high, low, volume: atr_percent(high, low, close, 14),
            16,
            True,
            "volatility",
            neg,
        ),
        SignalSpec(
            "realized_vol_60",
            lambda close, high, low, volume: realized_volatility(close, 60),
            62,
            True,
            "volatility",
            neg,
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
    liquidity: LiquidityConfig | None,
) -> tuple[dict[str, list[Observation]], float, int, tuple[int, int]]:
    """Signal readings paired with the return that followed them.

    ``liquidity`` is applied **per observation, on trailing data**, not per
    security over its lifetime. That distinction is the whole point: filtering
    securities by their average liquidity across the window would select the
    ones that *became* liquid, which is a look-ahead of exactly the kind this
    study exists to avoid. Here a name contributes observations only for the
    stretches during which it was actually tradeable, which is also the only
    period anybody could have acted on it.

    It is a tradability requirement and not a survivorship device -- a company
    that was liquid in 2003 and failed in 2005 keeps its 2003 observations.
    But it does drop the tail of failures that decay below the price floor
    before they end, which biases in the flattering direction and is recorded
    here rather than discovered later.
    """
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
    offered = kept = 0

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
        adv = (
            sma(close * volume, liquidity.dollar_volume_lookback) if liquidity is not None else None
        )
        for i in range(warmup, len(bars) - horizon, stride):
            if close[i] <= 0:
                continue
            offered += 1
            if liquidity is not None:
                if not (liquidity.min_price <= close[i] <= liquidity.max_price):
                    continue
                assert adv is not None
                if not np.isfinite(adv[i]) or adv[i] < liquidity.min_avg_dollar_volume:
                    continue
            kept += 1
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
    usable = [r for r in forward if r > -1.0]
    mean_forward = float(np.expm1(np.mean(np.log1p(usable)))) if usable else 0.0
    median_price = float(np.median(prices)) if prices else 1.0
    print(f"  {used:,} securities had enough history; {len(forward):,} forward returns")
    if forward:
        tail = np.array(forward)
        print(
            f"  outcome tails: p1 {np.percentile(tail, 1):+.1%}  "
            f"p99 {np.percentile(tail, 99):+.1%}  max {tail.max():+.0%}"
        )
    if liquidity is not None:
        share = kept / offered if offered else 0.0
        print(
            f"  liquidity filter kept {kept:,} of {offered:,} candidate "
            f"observations ({share:.1%}) -- price >= ${liquidity.min_price:,.0f}, "
            f"{liquidity.dollar_volume_lookback}-day dollar volume >= "
            f"${liquidity.min_avg_dollar_volume:,.0f}"
        )
    return out, mean_forward, int(median_price), (offered, kept)


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
    ap.add_argument(
        "--liquidity",
        action="store_true",
        help="require each observation to be tradeable on trailing data",
    )
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--min-dollar-volume", type=float, default=None)
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

    liquidity: LiquidityConfig | None = None
    if args.liquidity:
        base = StrategyConfig(name="baseline").liquidity
        liquidity = base.model_copy(
            update={
                k: v
                for k, v in (
                    ("min_price", args.min_price),
                    ("min_avg_dollar_volume", args.min_dollar_volume),
                )
                if v is not None
            }
        )

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    specs = _build_signals()
    # A derived direction tests both signs, so it spends two trials.
    per_horizon = sum(2 if spec.orientation is Orientation.DERIVED else 1 for spec in specs)
    trials = per_horizon * len(horizons)
    derived = sum(1 for spec in specs if spec.orientation is Orientation.DERIVED)
    print(
        f"\n{len(specs)} signals x {len(horizons)} horizons = {trials} trials "
        f"({derived} signal(s) with a derived direction count double)"
    )
    print(
        f"multiple-testing hurdle at {trials} trials: "
        f"|t| > {expected_max_of_normals(trials):.2f} on top of the {args.min_t:.1f} floor\n"
    )

    for horizon in horizons:
        stride = horizon  # non-overlapping
        print(f"=== horizon {horizon} sessions (sampled every {stride}) ===")
        t0 = time.time()
        observations, mean_forward, median_price, _counts = _observations(
            session, universe, start, end, horizon, stride, liquidity
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
                orientation=spec.orientation,
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

        # Ranked by what survives, with unusable rows last rather than first:
        # a mean spread of +1519% sorted to the top of the previous run and was
        # an artefact of one observation.
        rows.sort(
            key=lambda r: (
                r[2] is not SignalVerdict.ECONOMICALLY_USEFUL,
                -abs(r[1].t_statistic() or 0.0),
            )
        )
        print(
            f"  {'signal':<20}{'dir':>4}{'n':>8}{'IC':>7}{'t':>7}{'sp t':>7}"
            f"{'mean sp':>9}{'med sp':>8}{'net/yr':>9}  verdict"
        )
        for spec, study, verdict, net, _ in rows:
            mean_spread = study.quantile_spread(args.quantile)
            median_spread = study.robust_quantile_spread(args.quantile)
            spread_t = study.spread_t_statistic(args.quantile)
            arrow = {1: "up", -1: "dn"}.get(study.sign or 0, "?")
            ic = study.information_coefficient()
            t_stat = study.t_statistic()
            print(
                f"  {spec.name:<20}{arrow:>4}{study.count:>8,}"
                f"{'' if ic is None else f'{ic:>7.3f}'}"
                f"{'' if t_stat is None else f'{t_stat:>7.2f}'}"
                f"{'' if spread_t is None else f'{spread_t:>7.2f}'}"
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
