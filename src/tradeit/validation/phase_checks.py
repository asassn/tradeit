"""Phase 3, 4 and 5 behaviour, measured on real data instead of synthetic.

Everything in these three layers was built and characterised on generated
series. That is not a criticism of the characterisation — it found three real
defects — but a random walk is not a market, and the properties that matter
here are exactly the ones a generator cannot test: does an indicator behave the
same on a real gap-riddled series as on a clean one, does a detector fire at a
rate anyone would consider plausible, does a boundary drawn on real resistance
behave like a boundary drawn on noise.

**What these checks measure, and what they do not.** They measure *structure*
and *self-consistency*: rates, distributions, determinism, prefix-consistency,
causality. They do not measure whether any of it makes money. Nothing here
computes a future return, and :func:`assert_no_performance_claims` runs over
every result the runner collects. The distinction matters because the moment a
hit rate exists, thresholds start getting tuned against it, and the phase order
exists to stop exactly that.

**Descriptive results are PASS.** A check reporting "cup_with_handle fired 1.4
times per instrument-decade" is not judging that number — nobody has a
defensible prior for what it should be, and inventing one here would be the
tuning this gate forbids. It PASSes and reports. Only a *structural* violation
— a non-deterministic recomputation, a pattern confirmed before its own
evidence, a production breakout on an untagged boundary — is a FAIL.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from sqlalchemy import func, select

from tradeit.analytics.indicators import IndicatorEngine
from tradeit.breakouts.eligibility import PRODUCTION_PATTERN_STATES
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.data.packages.spec import DatasetKind
from tradeit.storage import tables as tbl
from tradeit.strategy.config import IndicatorConfig
from tradeit.validation.checks import CheckResult, CheckStatus, Phase, blocked
from tradeit.validation.context import ValidationContext
from tradeit.validation.scale import DEFAULT_SCALE_FACTORS, scale_invariance_report

#: Instruments sampled for the expensive recompute checks. The whole universe
#: would be more thorough and would also make a validation run take long enough
#: that people stop running it, which is a worse failure than a smaller sample.
SAMPLE_INSTRUMENTS = 20

#: Instruments sampled for the rescaling sweep. Smaller than SAMPLE_INSTRUMENTS
#: because each instrument is computed once per factor, and the property being
#: checked is structural rather than statistical — it either holds on real data
#: or it does not, and eight instruments across six factors is enough to say.
SCALE_SAMPLE_INSTRUMENTS = 8

#: Below this the warm-up regions dominate and the comparison is mostly NaN.
MIN_BARS_FOR_SCALE_CHECK = 260

#: Where a prefix-consistency check cuts the series. Two thirds through: far
#: enough in that warm-up is long past, far enough from the end that the
#: remaining tail is a real test.
PREFIX_FRACTION = 2 / 3


class _Check:
    def __init__(
        self,
        *,
        check_id: str,
        title: str,
        phase: Phase,
        requires: tuple[DatasetKind, ...] = (),
    ) -> None:
        self.check_id = check_id
        self.title = title
        self.phase = phase
        self.requires = requires


def _load_series(
    context: ValidationContext, limit: int = SAMPLE_INSTRUMENTS
) -> dict[int, list[OhlcvBar]]:
    """Point-in-time daily bars for a sample of instruments.

    Bounded by the context's as-of clock, like every other read in the platform.
    A validation harness that read around the clock would be validating a system
    nobody runs.
    """
    session = context.session
    ids = session.scalars(
        select(tbl.OhlcvBar.instrument_id)
        .where(tbl.OhlcvBar.timeframe == str(Bartimeframe.D1))
        .group_by(tbl.OhlcvBar.instrument_id)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    out: dict[int, list[OhlcvBar]] = {}
    for instrument_id in ids:
        rows = session.scalars(
            select(tbl.OhlcvBar)
            .where(
                tbl.OhlcvBar.instrument_id == instrument_id,
                tbl.OhlcvBar.timeframe == str(Bartimeframe.D1),
                tbl.OhlcvBar.knowledge_time <= context.as_of,
            )
            .order_by(tbl.OhlcvBar.session_date)
        ).all()
        bars = [
            OhlcvBar(
                instrument_id=row.instrument_id,
                timeframe=Bartimeframe(row.timeframe),
                session_date=row.session_date,
                event_time=row.event_time,
                knowledge_time=row.knowledge_time,
                knowledge_source=row.knowledge_source,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                trade_count=row.trade_count,
                vwap=row.vwap,
            )
            for row in rows
        ]
        if bars:
            out[instrument_id] = bars
    return out


def _same(left: Any, right: Any) -> bool:
    """Array equality that treats NaN as a value rather than as inequality.

    Indicator warm-up regions are NaN, and ``nan != nan``, so a plain
    comparison would report every deterministic series as non-deterministic.
    """
    if left.shape != right.shape:
        return False
    return bool(np.array_equal(left, right, equal_nan=True))


def _first_difference(left: Any, right: Any) -> int | None:
    if left.shape != right.shape:
        return None
    both_nan = np.isnan(left) & np.isnan(right)
    differs = ~(both_nan | (left == right))
    hits = np.flatnonzero(differs)
    return int(hits[0]) if hits.size else None


# ---------------------------------------------------------------------------
# Phase 3
# ---------------------------------------------------------------------------


class IndicatorDeterminism(_Check):
    """Does computing the same indicator twice give the same answer?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="phase3.indicator_determinism",
            title="Indicators are deterministic on real series",
            phase=Phase.PHASE_3,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        series = _load_series(context)
        if not series:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no daily series available at the as-of instant",
            )
        engine = IndicatorEngine(IndicatorConfig())
        mismatches: list[str] = []
        checked = 0
        for instrument_id, bars in series.items():
            first = engine.compute(bars, instrument_id=instrument_id)
            second = engine.compute(bars, instrument_id=instrument_id)
            for name in first.names:
                checked += 1
                if not _same(first.values[name], second.values[name]):
                    mismatches.append(f"instrument={instrument_id} {name}")
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if not mismatches else CheckStatus.FAIL,
            summary=(
                f"{checked} indicator series over {len(series)} real instruments reproduce exactly"
                if not mismatches
                else f"{len(mismatches)} indicator series did not reproduce"
            ),
            evidence={"instruments": len(series), "series_compared": checked},
            examples=tuple(mismatches[:5]),
        )


class IndicatorPrefixConsistency(_Check):
    """Does yesterday's answer change when tomorrow's bar arrives?

    The property that makes an indicator usable in a backtest at all: the value
    computed at session *n* must not depend on sessions after *n*. A generator
    cannot really test this because a random walk has no structure to leak;
    real series with gaps, halts and splits are where it breaks.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase3.indicator_prefix_consistency",
            title="Indicator values do not change when later bars arrive",
            phase=Phase.PHASE_3,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        series = _load_series(context)
        if not series:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no daily series available at the as-of instant",
            )
        engine = IndicatorEngine(IndicatorConfig())
        warmup = engine.warmup_periods
        drifted: list[str] = []
        compared = 0
        usable = 0
        for instrument_id, bars in series.items():
            cut = int(len(bars) * PREFIX_FRACTION)
            if cut <= warmup:
                continue
            usable += 1
            whole = engine.compute(bars, instrument_id=instrument_id)
            prefix = engine.compute(bars[:cut], instrument_id=instrument_id)
            for name in prefix.names:
                compared += 1
                if not _same(whole.values[name][:cut], prefix.values[name]):
                    drifted.append(
                        f"instrument={instrument_id} {name} at index "
                        f"{_first_difference(whole.values[name][:cut], prefix.values[name])}"
                    )
        if usable == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary=(
                    f"no series is long enough to cut past the {warmup}-session warm-up; "
                    "prefix consistency is untestable on this snapshot"
                ),
                evidence={"warmup_periods": warmup},
            )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if not drifted else CheckStatus.FAIL,
            summary=(
                f"{compared} prefix comparisons over {usable} real series agree exactly"
                if not drifted
                else (
                    f"{len(drifted)} indicator series change retroactively when later "
                    "bars arrive; every result computed over them reads the future"
                )
            ),
            evidence={
                "instruments": usable,
                "series_compared": compared,
                "warmup_periods": warmup,
            },
            examples=tuple(drifted[:5]),
        )


class IndicatorWarmup(_Check):
    """Is a value there when the registry says it will be?

    The direction matters and is easy to get backwards. A series defined
    *earlier* than its declared warm-up is harmless: the declaration is
    conservative — MACD declares one warm-up for the whole family, using the
    signal line's — and a consumer respecting it merely ignores a few usable
    bars. A series still undefined *after* its declared warm-up is the real
    defect: the registry promised a value and there is none, so a consumer
    indexing at the warm-up boundary gets NaN where it expected a number.

    Both are reported. Only the second fails.

    **One bar of slack, and why.** Running this check surfaced that
    ``warmup_periods`` does not mean quite the same thing across the registry.
    For a windowed aggregate — ``sma_20`` — it is the count of values consumed,
    so the first defined index is 19. For a lagged difference — ``roc_20``,
    ``momentum_20`` — it is the lag itself, and the first defined index is 20,
    because the calculation needs both ends of the window. Neither reading
    permits a lookahead, so this is a documentation inconsistency rather than a
    defect, and the check accepts either. It is recorded here rather than
    papered over: a reader who assumes one convention holds everywhere will be
    off by one, and off-by-one at a warm-up boundary is how a NaN reaches a
    comparison.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase3.indicator_warmup",
            title="Indicators are defined by the warm-up they declare",
            phase=Phase.PHASE_3,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        series = _load_series(context, limit=5)
        if not series:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no daily series available at the as-of instant",
            )
        engine = IndicatorEngine(IndicatorConfig())
        registry = engine.registry
        late: list[str] = []
        early: list[str] = []
        checked = 0
        for instrument_id, bars in series.items():
            computed = engine.compute(bars, instrument_id=instrument_id)
            for name in computed.names:
                definition = registry.get(name)
                if definition is None or definition.warmup_periods < 1:
                    continue
                values = computed.values[name]
                defined = np.flatnonzero(~np.isnan(values))
                if defined.size == 0 or values.size <= definition.warmup_periods:
                    continue
                checked += 1
                first = int(defined[0])
                if first > definition.warmup_periods:
                    late.append(
                        f"instrument={instrument_id} {name} declares a "
                        f"{definition.warmup_periods}-session warm-up but is still "
                        f"undefined at index {definition.warmup_periods}; "
                        f"first value at {first}"
                    )
                elif first < definition.warmup_periods - 1:
                    early.append(
                        f"instrument={instrument_id} {name} declares "
                        f"{definition.warmup_periods} but is defined from index {first}"
                    )

        if late:
            status = CheckStatus.FAIL
            summary = (
                f"{len(late)} series are still undefined more than one bar after the "
                "warm-up they declare. A consumer that skips exactly warmup_periods "
                "bars and starts reading gets NaN where the registry promised a number."
            )
        elif early:
            status = CheckStatus.WARN
            summary = (
                f"every declared warm-up is honoured within a bar; {len(early)} of "
                f"{checked} series are defined earlier than declared. That is the safe "
                "direction — a conservative declaration wastes a few bars, it does not "
                "leak the future — and it is listed so nobody assumes the two agree."
            )
        else:
            status = CheckStatus.PASS
            summary = (
                f"{checked} series across {len(series)} real instruments become defined "
                "within a bar of their declared warm-up"
            )

        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=summary,
            evidence={
                "instruments": len(series),
                "series_checked": checked,
                "defined_later_than_declared": len(late),
                "defined_earlier_than_declared": len(early),
            },
            examples=tuple((late or early)[:5]),
        )


# ---------------------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------------------


class PatternDetectionRate(_Check):
    """How often do the detectors fire on real data?

    Descriptive, and deliberately not judged. Nobody has a defensible prior for
    how many cup-with-handles a decade of a large-cap contains, and inventing
    one here would be tuning a threshold against the validation set — the thing
    the phase order exists to prevent. The number's value is as a baseline: a
    later change that halves it has something to explain.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.detection_rate",
            title="Pattern detection rates per detector on real data",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        total = session.scalar(select(func.count()).select_from(tbl.Pattern)) or 0
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary=(
                    "no patterns have been persisted for this snapshot; run the scanner "
                    "before the Phase 4 checks"
                ),
            )
        by_detector = Counter(row.detector_name for row in session.scalars(select(tbl.Pattern)))
        instrument_years = _instrument_years(context)
        rates = {
            name: round(count / instrument_years, 4) if instrument_years else None
            for name, count in sorted(by_detector.items())
        }
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS,
            summary=(
                f"{total:,} patterns across {len(by_detector)} detectors over "
                f"{instrument_years:,.0f} instrument-years"
            ),
            evidence={
                "patterns": total,
                "instrument_years": round(instrument_years, 2),
                "per_instrument_year": rates,
                "counts": dict(sorted(by_detector.items())),
            },
        )


class PatternCausality(_Check):
    """Is any pattern confirmed before the geometry that confirms it?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.causality",
            title="No pattern is confirmed before its own evidence",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        total = session.scalar(select(func.count()).select_from(tbl.Pattern)) or 0
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no patterns have been persisted for this snapshot",
            )
        # A pattern may not be detected before the geometry that defines it has
        # finished forming. first_detected_session earlier than
        # structural_end_date means the detector saw a shape whose last bar had
        # not printed yet.
        acausal = tbl.Pattern.first_detected_session < tbl.Pattern.structural_end_date
        offenders = session.scalars(select(tbl.Pattern).where(acausal).limit(5)).all()
        count = session.scalar(select(func.count()).select_from(tbl.Pattern).where(acausal)) or 0
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if count == 0 else CheckStatus.FAIL,
            summary=(
                f"all {total:,} patterns are first detected on or after their structure completes"
                if count == 0
                else f"{count:,} patterns were detected before their own structure finished forming"
            ),
            evidence={"patterns": total, "acausal": count},
            examples=tuple(
                f"pattern={row.identity_key} detected={row.first_detected_session} "
                f"structure_ends={row.structural_end_date}"
                for row in offenders
            ),
        )


# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------


class BreakoutStateDistribution(_Check):
    """Where do breakout attempts end up? Descriptive."""

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.state_distribution",
            title="Distribution of terminal breakout states on real data",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        total = session.scalar(select(func.count()).select_from(tbl.BreakoutEvent)) or 0
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this snapshot",
            )
        events = list(session.scalars(select(tbl.BreakoutEvent)))
        states = Counter(e.state for e in events)
        paths = Counter(e.confirmed_path for e in events if e.confirmed_path)
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS,
            summary=(
                f"{total:,} breakout attempts; "
                f"{states.get('confirmed', 0) + states.get('retest_confirmed', 0):,} "
                "reached a confirmed state"
            ),
            evidence={
                "events": total,
                "states": dict(sorted(states.items())),
                "confirmation_paths": dict(sorted(paths.items())),
            },
        )


class BreakoutBoundaryProvenance(_Check):
    """Is every production breakout drawn on a structural boundary?

    The mechanism from ADR-0025, checked where it finally matters. A production
    dataset containing manual or untagged boundaries is a dataset whose rates
    describe a mixture nobody chose.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.boundary_provenance",
            title="Production breakout events carry a structural boundary tag",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        total = session.scalar(select(func.count()).select_from(tbl.BreakoutEvent)) or 0
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this snapshot",
            )
        kinds = Counter(e.boundary_kind for e in session.scalars(select(tbl.BreakoutEvent)))
        non_structural = total - kinds.get("structural_pattern_boundary", 0)
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if non_structural == 0 else CheckStatus.FAIL,
            summary=(
                f"all {total:,} events are tagged structural_pattern_boundary"
                if non_structural == 0
                else (
                    f"{non_structural:,} of {total:,} events are drawn on non-structural "
                    "boundaries. Any rate over this population is a mixture of levels "
                    "derived from confirmed geometry and levels somebody typed in."
                )
            ),
            evidence={"events": total, "by_kind": dict(sorted(kinds.items()))},
        )


class BreakoutQualityFrozen(_Check):
    """Does a breakout quality score exist only where a breakout does?

    Quality is computed once, on the session of the first qualifying close, and
    never recomputed (ADR-0021). The schema does not store *when* it was
    computed — freezing is enforced in the engine, not the database — so what is
    checkable here is the observable consequence: a non-zero quality on an event
    that never had a qualifying close would mean a score was produced by
    something other than a breakout, which is the only way the freeze can be
    visibly broken from outside the engine.

    Stated plainly because it matters: this is weaker than the unit tests, which
    assert the freeze directly. It is here to catch a persistence-layer
    regression, not to re-prove the engine.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.quality_frozen",
            title="Breakout quality exists only where a qualifying close does",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        events = list(session.scalars(select(tbl.BreakoutEvent)))
        if not events:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this snapshot",
            )
        offenders = [
            e for e in events if e.breakout_quality > 0 and e.first_qualifying_close_session is None
        ]
        scored = sum(1 for e in events if e.breakout_quality > 0)
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if not offenders else CheckStatus.FAIL,
            summary=(
                f"all {scored:,} scored events of {len(events):,} have a qualifying close"
                if not offenders
                else (
                    f"{len(offenders):,} events carry a breakout quality without ever "
                    "having produced a qualifying close"
                )
            ),
            evidence={"events": len(events), "scored": scored, "unbacked": len(offenders)},
            examples=tuple(
                f"event={e.event_key} attempt={e.attempt_number} quality={e.breakout_quality}"
                for e in offenders[:5]
            ),
        )


class BreakoutMonitorFloor(_Check):
    """Were only production-eligible pattern states monitored?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.monitor_floor",
            title="Only production-eligible pattern states produced breakout events",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        rows = session.execute(
            select(tbl.BreakoutEvent.pattern_key, tbl.Pattern.state)
            .join(tbl.Pattern, tbl.Pattern.identity_key == tbl.BreakoutEvent.pattern_key)
            .limit(5000)
        ).all()
        if not rows:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events joined to a pattern for this snapshot",
            )
        allowed = {str(s) for s in PRODUCTION_PATTERN_STATES}
        offenders = [(key, state) for key, state in rows if state not in allowed]
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if not offenders else CheckStatus.FAIL,
            summary=(
                f"all {len(rows):,} joined events come from {sorted(allowed)}"
                if not offenders
                else (
                    f"{len(offenders):,} events come from pattern states outside the "
                    f"production floor {sorted(allowed)}; every rate computed from this "
                    "dataset describes the monitor rather than the market"
                )
            ),
            evidence={"joined_events": len(rows), "allowed_states": sorted(allowed)},
            examples=tuple(f"pattern={k} state={s}" for k, s in offenders[:5]),
        )


def _instrument_years(context: ValidationContext) -> float:
    rows = context.session.execute(
        select(
            func.min(tbl.OhlcvBar.session_date),
            func.max(tbl.OhlcvBar.session_date),
            func.count(func.distinct(tbl.OhlcvBar.instrument_id)),
        ).where(tbl.OhlcvBar.timeframe == str(Bartimeframe.D1))
    ).one()
    first, last, instruments = rows
    if not first or not last or not instruments:
        return 0.0
    span = (last - first).days / 365.25
    return float(span * instruments)


class IndicatorScaleInvariance(_Check):
    """Do the analytics declared scale-invariant actually survive a rescaling?

    This is the check that licenses using the instruments whose raw price series
    could not be recovered. A split adjustment is a rescaling of the price
    series, so a feature whose answer is unchanged when every price is
    multiplied by a positive constant gives the same answer on adjusted prices
    as on raw ones.

    Run against the snapshot's **own bars** rather than a fixture, because the
    property is about this data: a feature can be invariant on a smooth
    synthetic ramp and not on a series with gaps, halts and near-zero prices.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase3.scale_invariance",
            title="Scale invariance: do the declared invariants hold on this data?",
            phase=Phase.PHASE_3,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        missing = context.missing(self.requires)
        if missing:
            return blocked(self, missing)
        series = _load_series(context, limit=SCALE_SAMPLE_INSTRUMENTS)
        if not series:
            return blocked(self, self.requires, "no daily bars are present")

        engine = IndicatorEngine(IndicatorConfig())

        def values_for(bars: list[OhlcvBar]) -> dict[str, Any]:
            return engine.compute(bars, instrument_id=bars[0].instrument_id).values

        violations: list[str] = []
        unclassified: set[str] = set()
        checked = 0
        for instrument_id, bars in sorted(series.items()):
            if len(bars) < MIN_BARS_FOR_SCALE_CHECK:
                continue
            checked += 1
            for factor in DEFAULT_SCALE_FACTORS:
                report = scale_invariance_report(values_for, bars, factor)
                unclassified.update(report.unclassified)
                violations.extend(
                    f"instrument {instrument_id} feature {item.name} moved by "
                    f"{item.max_relative_difference:.3g} at factor {factor}"
                    for item in report.violations
                )
        if not checked:
            return blocked(
                self,
                self.requires,
                f"no instrument has the {MIN_BARS_FOR_SCALE_CHECK} bars this check needs",
            )

        capabilities = context.capabilities
        evidence = {
            "instruments_checked": checked,
            "factors": list(DEFAULT_SCALE_FACTORS),
            "violations": len(violations),
            "unclassified_features": len(unclassified),
            # Both sample sizes, because a reader will otherwise assume one.
            "instruments_price_eligible": len(capabilities.price_eligible),
            "instruments_raw_verified": len(capabilities.raw_verified),
        }
        if violations or unclassified:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.FAIL,
                summary=(
                    f"{len(violations)} declared-invariant feature reading(s) moved under "
                    f"rescaling and {len(unclassified)} feature(s) have no classification. "
                    "Analytics may NOT be run on instruments without a verified raw price "
                    "series until this is resolved."
                ),
                evidence=evidence,
                examples=tuple(sorted(violations)[:5] + sorted(unclassified)[:5]),
            )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS,
            summary=(
                f"every feature declared scale-invariant returned the identical answer "
                f"over {checked} instrument(s) at {len(DEFAULT_SCALE_FACTORS)} rescaling "
                "factors, so those analytics are valid on split-adjusted prices without a "
                "verified raw series. Scale-SENSITIVE analytics are not, and are limited "
                f"to the {len(capabilities.raw_verified)} instrument(s) that have one."
            ),
            evidence=evidence,
        )


def phase_checks() -> list[Any]:
    """Every phase check, grouped by the layer it interrogates."""
    return [
        IndicatorWarmup(),
        IndicatorDeterminism(),
        IndicatorPrefixConsistency(),
        IndicatorScaleInvariance(),
        PatternDetectionRate(),
        PatternCausality(),
        BreakoutStateDistribution(),
        BreakoutBoundaryProvenance(),
        BreakoutQualityFrozen(),
        BreakoutMonitorFloor(),
    ]


__all__ = [
    "MIN_BARS_FOR_SCALE_CHECK",
    "PREFIX_FRACTION",
    "SAMPLE_INSTRUMENTS",
    "SCALE_SAMPLE_INSTRUMENTS",
    "BreakoutBoundaryProvenance",
    "BreakoutMonitorFloor",
    "BreakoutQualityFrozen",
    "BreakoutStateDistribution",
    "IndicatorDeterminism",
    "IndicatorPrefixConsistency",
    "IndicatorScaleInvariance",
    "IndicatorWarmup",
    "PatternCausality",
    "PatternDetectionRate",
    "phase_checks",
]
