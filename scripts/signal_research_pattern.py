#!/usr/bin/env python
"""``pattern_quality`` measured by its real detector, not by distance-from-SMA.

Every prior test of this factor used a *proxy*. ``dist_from_sma_50`` and
``dist_from_sma_200`` are not what ``pattern_quality`` means -- they are cheap
price kernels that were standing in for a detector nobody had run over the
corpus. Both flipped sign, and §12 of the scoreboard declined to zero the factor
on that basis precisely because **punishing a factor for its substitute's
failure is not evidence about the factor.**

This runs the actual thing: twelve enabled D1 detectors -- VCP, cup-and-handle,
bull flag, flat base, ascending triangle, pennant, high tight flag, double
bottom, inverse head and shoulders, base-on-base, tight consolidation,
breakout-retest -- over a trailing window at each sampled session, and takes the
quality of the best **live** structure found -- see :data:`ACTIONABLE`.

Two questions, kept apart
-------------------------

They are different claims and collapsing them is how a filter gets credited with
a grader's job:

* **Does a pattern's quality grade?** Among sessions where a detector fires, do
  higher-quality structures earn more? *This is the one that licenses a weight*,
  because ``pattern_quality`` enters the score as a continuous number, not as a
  flag.
* **Does a pattern's presence predict?** Do sessions with any pattern beat
  sessions with none? A real finding if true, but it would license a **gate**,
  not a weight -- the same distinction §11 drew for ``breakout_confirmation``.

The second is reported separately and its trials are counted separately, so a
failure of the first cannot be quietly replaced by a success of the second.

Why adjusted prices are admissible here, which needed checking
--------------------------------------------------------------

The proxy study rested on every signal being **scale-invariant**: a split
multiplies every bar in the window by one factor, and a ratio does not notice.
Pattern detectors are a harder case, because they are not all ratios.

Two facts make it hold, and both were verified rather than assumed:

* Every detector threshold is a **ratio or a session count** -- retracement
  depth, ATR contraction, channel slope, touch counts, consolidation length.
  There is no price-level test anywhere in the detector configuration.
* The one apparent exception, ``high_tight_flag``'s ``min_dollar_volume`` floor,
  is invariant too, because :func:`price_series` scales volume by the *same*
  split factor it divides price by. Dollar volume is therefore unchanged by an
  adjustment, and the detector sees the turnover a trader saw.

That floor is a fixed nominal $5m across a decade, which is a different
criticism -- it drifts with inflation and market size -- and is recorded here
rather than corrected silently, because detector thresholds are not this
script's to move.

**What is not claimed.** This measures the detectors as configured today against
a corpus whose survivorship gate still reads ``SURVIVOR_BIASED``. It can find
that the factor does not grade. It cannot prove that it does.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.overfitting import expected_max_of_normals
from tradeit.core.enums import Bartimeframe
from tradeit.patterns.base import PatternState
from tradeit.patterns.scanner import PatternScanner
from tradeit.research01.series import price_series
from tradeit.signals.study import (
    Observation,
    Orientation,
    PromotionRule,
    SignalStudy,
    StudyTarget,
    TargetKind,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import CostConfig, StrategyConfig

#: The states in which a structure is a live candidate, taken from the code's
#: own semantics rather than from anything measured here.
#:
#: ``FORMING`` is excluded because :class:`PatternState` says of it, in as many
#: words, *"Recorded, not actionable."* ``INVALIDATED`` and ``EXPIRED`` are
#: excluded because they are terminal -- a structure that already failed cannot
#: supply the quality of a candidate being considered today, and letting it do
#: so was a defect in this script's first draft, found in a six-security pilot.
#:
#: This matters more than it looks. The pilot found a **median of nine and a
#: maximum of eighteen concurrent structures** on a single security, so the
#: unfiltered maximum was an order statistic over a crowd that included dead
#: patterns, and it read near 80 almost always. A factor that reads 80 for
#: everything cannot rank anything.
ACTIONABLE = (
    PatternState.MATURE,
    PatternState.NEAR_BREAKOUT,
    PatternState.BROKEN_OUT_UNCONFIRMED,
)

#: Declared before the run, in ``pattern_prereg.txt``. POSITIVE because
#: ``ScoringConfig`` already asserts that higher quality is a better candidate;
#: measuring against that assertion is the test. Deriving the direction from
#: this data would cost two trials instead of one and would make the prior
#: unfalsifiable.
DECLARED_ORIENTATION = Orientation.POSITIVE


@dataclass(slots=True, frozen=True)
class DetectorBar:
    """An :class:`AdjustedBar` wearing the two fields the detectors also read.

    The detectors take ``Sequence[OhlcvBar]``, but they only ever touch OHLCV,
    ``session_date``, ``instrument_id`` and ``timeframe`` -- so a full pydantic
    ``OhlcvBar`` per bar would buy validation we do not need at a cost we would
    pay a hundred thousand times. This carries exactly the read surface.

    It is a **narrowing, not a widening**: nothing here invents a value. The
    security id and the timeframe are facts of the query, and the prices come
    through untouched from :func:`price_series` with its adjudication, revision
    and zero-price rules already applied.

    ``knowledge_time`` is the session's close instant, and that is the corpus's
    own recorded value rather than a convenient stand-in. Measured on
    ``security_price_facts`` before relying on it: of 35,428,493 rows at
    ``adjustment_basis='raw'``, **35,426,528 carry
    ``knowledge_time_basis='session_close'``** and 1,965 (0.006%) carry
    ``delivery_unestablished``; and the number of ``(security, session)`` pairs
    holding more than one raw revision is **zero**.

    That measurement is what licenses reading the whole decade at a single
    as-of instead of re-querying at every scan point. **A raw bar is never
    revised here**, so the two reads return the same bars -- the expensive
    version would not be more point-in-time, only slower. The fully-adjusted
    ``total`` rows, whose knowledge time is delivery day in 2026, are excluded
    by ``price_series`` itself and never reach a detector.
    """

    instrument_id: int
    timeframe: Bartimeframe
    session_date: dt.date
    knowledge_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class Sampled:
    """One scan point: what the detectors saw, and what happened next."""

    security_id: int
    session_date: dt.date
    quality: float | None
    family: str
    #: Every structure the scan returned, live or dead. Kept because the count
    #: is the selectivity measurement, and selectivity is the finding.
    instances: int
    actionable: int
    forward: dict[int, float]


def _universe(path: str, start: str, end: str, min_bars: int, cap: int) -> list[int]:
    """Identical construction to the proxy runs, so the results are comparable."""
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


def _scan_security(
    scanner: PatternScanner,
    session: Session,
    security_id: int,
    start: dt.date,
    end: dt.date,
    as_of: dt.datetime,
    history: int,
    stride: int,
    horizons: Sequence[int],
) -> tuple[list[Sampled], int, float]:
    """Walk one security's decade, scanning every ``stride`` sessions.

    The window handed to the scanner ends at the sampled session and never
    extends past it. The detectors enforce that boundary themselves -- passing a
    longer slice would be caught rather than silently believed -- but slicing
    here keeps the cost bounded, which is the only reason the run finishes.
    """
    bars = price_series(session, security_id, as_of=as_of, start=start, end=end)
    longest = max(horizons)
    if len(bars) < history + longest + 1:
        return [], 0, 0.0

    closes = np.array([float(b.close) for b in bars])
    shaped = [
        DetectorBar(
            instrument_id=security_id,
            timeframe=Bartimeframe.D1,
            session_date=b.session_date,
            knowledge_time=dt.datetime.combine(b.session_date, dt.time(21), tzinfo=dt.UTC),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
    ]
    out: list[Sampled] = []
    scans = 0
    for i in range(history - 1, len(bars) - longest, stride):
        if closes[i] <= 0:
            continue
        window = shaped[i - history + 1 : i + 1]
        result = scanner.scan(
            security_id,
            Bartimeframe.D1,
            window,
            bars[i].session_date,
            track=False,
        )
        scans += 1
        live = [p for p in result.instances if p.state in ACTIONABLE]
        best = max(live, key=lambda p: p.quality, default=None)
        forward = {h: float(closes[i + h] / closes[i] - 1.0) for h in horizons}
        out.append(
            Sampled(
                security_id=security_id,
                session_date=bars[i].session_date,
                quality=None if best is None else float(best.quality),
                family="" if best is None else str(best.pattern_type),
                instances=len(result.instances),
                actionable=len(live),
                forward=forward,
            )
        )
    return out, scans, float(np.median(closes))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", required=True)
    ap.add_argument("--start", default="2000-01-03")
    ap.add_argument("--end", default="2009-12-31")
    ap.add_argument("--cap", type=int, default=100, help="securities per arm")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--horizons", default="21,63")
    ap.add_argument("--stride", type=int, default=21)
    ap.add_argument("--min-observations", type=int, default=500)
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--quantile", type=float, default=0.2)
    ap.add_argument("--trials", type=int, default=24, help="ledger size including this run")
    ap.add_argument("--out", default=None, help="write the raw scan points here")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument(
        "--from-csv",
        default=None,
        help="comma-separated scan-point CSVs to pool and analyse, skipping the "
        "scan entirely. The shards are analysed together rather than averaged: "
        "a quantile is a cross-sectional cut and taking it per shard would rank "
        "each eighth of the universe against itself.",
    )
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    horizons = [int(h) for h in args.horizons.split(",")]
    as_of = dt.datetime.combine(end, dt.time(21), tzinfo=dt.UTC)

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    costs: CostConfig = StrategyConfig(name="baseline").costs
    rule = PromotionRule(
        min_observations=args.min_observations,
        min_abs_t_statistic=args.min_t,
        quantile_fraction=args.quantile,
    )

    if args.from_csv:
        points, prices = _load_points(args.from_csv.split(","), horizons)
        print(f"pooled {len(points):,} scan points from {len(args.from_csv.split(',')):,} shards")
        used = len({p.security_id for p in points})
        return _analyse(points, prices, used, horizons, args, rule, costs)

    scanner = PatternScanner()
    history = scanner.required_history(Bartimeframe.D1)
    enabled = [n for n, e in scanner.registry.entries.items() if e.enabled]
    print(f"{len(enabled)} detectors on D1, trailing window {history} bars, stride {args.stride}")

    universe = _universe(args.spans, args.start, args.end, args.min_bars, args.cap)
    if args.shards > 1:
        universe = [s for n, s in enumerate(universe) if n % args.shards == args.shard]
        print(f"  shard {args.shard}/{args.shards}: {len(universe):,} securities")

    t0 = time.time()
    points: list[Sampled] = []
    prices: list[float] = []
    used = scans = 0
    for n, security_id in enumerate(universe, 1):
        got, count, median_close = _scan_security(
            scanner, session, security_id, start, end, as_of, history, args.stride, horizons
        )
        if count:
            used += 1
            prices.append(median_close)
        scans += count
        points.extend(got)
        if n % 25 == 0 or n == len(universe):
            rate = scans / max(1e-9, time.time() - t0)
            print(
                f"  {n}/{len(universe)} securities  {scans:,} scans  "
                f"{rate:.0f} scans/s  [{time.time() - t0:.0f}s]"
            )

    return _analyse(points, prices, used, horizons, args, rule, costs)


def _load_points(
    paths: Sequence[str], horizons: Sequence[int]
) -> tuple[list[Sampled], list[float]]:
    """Re-read shard CSVs and pool them into one cross-section.

    Pooled rather than averaged. A quantile spread is a **cross-sectional**
    cut, so computing one per shard and averaging would rank each eighth of
    the universe against itself and then combine eight different definitions
    of `top quintile`. The shards are a way of splitting the *scanning* cost,
    not a partition of the study.
    """
    points: list[Sampled] = []
    prices: list[float] = []
    for path in paths:
        with open(path.strip()) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                points.append(
                    Sampled(
                        security_id=int(row["security_id"]),
                        session_date=dt.date.fromisoformat(row["session_date"]),
                        quality=float(row["quality"]) if row["quality"] else None,
                        family=row["family"],
                        instances=int(row["instances"]),
                        actionable=int(row["actionable"]),
                        forward={h: float(row[str(h)]) for h in horizons},
                    )
                )
        # Per-security median closes, written beside each shard rather than
        # squeezed into the point rows. They are a different shape of fact --
        # one per security, not one per scan -- and a column repeating the same
        # value down thousands of rows invites somebody to average it.
        sidecar = pathlib.Path(path.strip()).with_suffix(".prices.txt")
        if sidecar.exists():
            prices.extend(float(line) for line in sidecar.read_text().split() if line)
    return points, prices


def _analyse(
    points: list[Sampled],
    prices: list[float],
    used: int,
    horizons: Sequence[int],
    args: argparse.Namespace,
    rule: PromotionRule,
    costs: CostConfig,
) -> int:
    """Everything downstream of the scan, so a pooled re-read takes the same path."""
    fired = [p for p in points if p.quality is not None]
    print(
        f"\n{used:,} securities scanned; {len(points):,} scan points; "
        f"{len(fired):,} had a live pattern ({len(fired) / max(1, len(points)):.1%})"
    )
    if points:
        # Selectivity, reported before any return is looked at. A suite that
        # finds a structure on nearly every name on nearly every day is not
        # ranking candidates, whatever its quality numbers say.
        allc = np.array([p.instances for p in points], dtype=float)
        livec = np.array([p.actionable for p in points], dtype=float)
        print(
            f"  structures per scan: med {np.median(allc):.0f} "
            f"(max {allc.max():.0f});  of those live: med {np.median(livec):.0f} "
            f"(max {livec.max():.0f})"
        )
        print(f"  any structure at all fired on {float((allc > 0).mean()):.1%} of scan points")
    if fired:
        families: dict[str, int] = {}
        for p in fired:
            families[p.family] = families.get(p.family, 0) + 1
        top = sorted(families.items(), key=lambda kv: -kv[1])[:6]
        print("  best-pattern families: " + ", ".join(f"{k} {v:,}" for k, v in top))
        q = np.array([p.quality for p in fired], dtype=float)
        print(
            f"  quality: min {q.min():.0f}  p25 {np.percentile(q, 25):.0f}  "
            f"med {np.median(q):.0f}  p75 {np.percentile(q, 75):.0f}  max {q.max():.0f}"
        )

    if args.out:
        with open(args.out, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "security_id",
                    "session_date",
                    "quality",
                    "family",
                    "instances",
                    "actionable",
                    *horizons,
                ]
            )
            for p in points:
                writer.writerow(
                    [
                        p.security_id,
                        p.session_date.isoformat(),
                        "" if p.quality is None else f"{p.quality:.6f}",
                        p.family,
                        p.instances,
                        p.actionable,
                        *[f"{p.forward[h]:.8f}" for h in horizons],
                    ]
                )
        sidecar = pathlib.Path(args.out).with_suffix(".prices.txt")
        sidecar.write_text("\n".join(f"{v:.6f}" for v in prices))
        print(f"  raw scan points -> {args.out}  (median closes -> {sidecar.name})")

    # Cost modelling needs a share price, because commission per share and a
    # spread quoted in cents mean different things on a $4 stock and a $40 one.
    # Median of per-security medians, matching the proxy runs exactly so the
    # net-of-cost columns are on the same footing.
    median_price = float(np.median(prices)) if prices else 1.0
    print(f"  median price ${median_price:,.0f}")
    hurdle = expected_max_of_normals(args.trials)
    print(f"\nmultiple-testing hurdle at {args.trials} trials: |t| > {hurdle:.2f}")

    for horizon in horizons:
        print(f"\n=== horizon {horizon} sessions ===")

        # Question 1 -- does quality GRADE? Only the sessions where a detector
        # fired; a session with no pattern has no quality to rank.
        graded = SignalStudy(
            name="pattern_quality_best",
            target=StudyTarget(
                kind=TargetKind.FORWARD_RETURN,
                horizon_sessions=horizon,
                description=f"{horizon}-session forward total return",
            ),
            orientation=DECLARED_ORIENTATION,
            observations=tuple(
                Observation(
                    session_date=p.session_date,
                    instrument_id=p.security_id,
                    signal=float(p.quality or 0.0),
                    outcome=p.forward[horizon],
                )
                for p in fired
            ),
            sampling_stride_sessions=args.stride,
        )

        # Question 2 -- does PRESENCE predict? Every scan point, signal 1/0.
        # A separate claim, separately counted, and it would license a gate
        # rather than a weight even if it held.
        present = SignalStudy(
            name="pattern_present",
            target=graded.target,
            orientation=DECLARED_ORIENTATION,
            observations=tuple(
                Observation(
                    session_date=p.session_date,
                    instrument_id=p.security_id,
                    signal=0.0 if p.quality is None else 1.0,
                    outcome=p.forward[horizon],
                )
                for p in points
            ),
            sampling_stride_sessions=args.stride,
        )

        header = (
            f"  {'signal':<22}{'n':>8}{'IC':>8}{'t':>7}{'sp t':>7}"
            f"{'mean sp':>10}{'med sp':>9}{'net/yr':>9}  verdict"
        )
        print(header)
        for study in (graded, present):
            verdict, reason = study.verdict(rule, costs, average_price=median_price)
            ic = study.information_coefficient()
            t_stat = study.t_statistic()
            spread_t = study.spread_t_statistic(args.quantile)
            mean_spread = study.quantile_spread(args.quantile)
            median_spread = study.robust_quantile_spread(args.quantile)
            net = study.net_annual_spread(args.quantile, costs, average_price=median_price)
            print(
                f"  {study.name:<22}{study.count:>8,}"
                f"{'' if ic is None else f'{ic:>8.4f}'}"
                f"{'' if t_stat is None else f'{t_stat:>7.2f}'}"
                f"{'' if spread_t is None else f'{spread_t:>7.2f}'}"
                f"{'' if mean_spread is None else f'{mean_spread:>10.2%}'}"
                f"{'' if median_spread is None else f'{median_spread:>9.2%}'}"
                f"{'' if net is None else f'{net:>9.1%}'}  {verdict}"
            )
            print(f"    {reason}")

        # Presence measured the way a gate would actually be judged: the two
        # populations' returns side by side, not a correlation against a flag.
        with_p = np.array([p.forward[horizon] for p in fired])
        without = np.array([p.forward[horizon] for p in points if p.quality is None])
        if len(with_p) and len(without):
            print(
                f"    with a pattern  n {len(with_p):>7,}  mean {with_p.mean():+.2%}  "
                f"median {np.median(with_p):+.2%}"
            )
            print(
                f"    without one     n {len(without):>7,}  mean {without.mean():+.2%}  "
                f"median {np.median(without):+.2%}"
            )

    print(
        "\nEvidence, not a change. Detector thresholds and scoring weights are both\n"
        "strategy parameters; this run informs a decision and does not make one.\n"
        "And the corpus gate still reads SURVIVOR_BIASED -- this universe keeps the\n"
        "companies that failed, which is necessary and not sufficient."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
