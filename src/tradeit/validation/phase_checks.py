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
from sqlalchemy import func, literal, select

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
from tradeit.validation.scope import RunSelection

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


def _unresolved(check: _Check, run: RunSelection) -> CheckResult:
    """BLOCKED, because the checks read one scan run and could not pick one.

    Never a silent aggregate. Two completed scans over one snapshot are two
    corpora, and a check that read both would describe a population that never
    existed — which is exactly the class of quiet wrongness this gate is built
    to refuse.
    """
    return CheckResult(
        check_id=check.check_id,
        title=check.title,
        phase=check.phase,
        status=CheckStatus.BLOCKED,
        summary=f"not run: {run.problem}",
        needs=("a single named scan run (--scan-id)",),
        examples=tuple(f"candidate: {name}" for name in run.candidates[:8]),
    )


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
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.Pattern).where(run.patterns())) or 0
        )
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary=(
                    "no patterns have been persisted for this scan run; run the scanner "
                    "before the Phase 4 checks"
                ),
            )
        # Grouped in SQL. Iterating `session.scalars(select(Pattern))` to read
        # one attribute built 272,537 fully-populated ORM instances at
        # full-universe scale — 15s and most of a gigabyte, for a tally.
        by_detector: Counter[str] = Counter()
        for name, count in session.execute(
            select(tbl.Pattern.detector_name, func.count())
            .where(run.patterns())
            .group_by(tbl.Pattern.detector_name)
        ):
            by_detector[str(name)] = int(count)
        scope = context.scope
        instrument_years = scope.instrument_years()
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
                f"{instrument_years:,.0f} instrument-years of *scanned* history "
                f"({len(scope.completed)} of {len(scope.snapshot)} instruments in the snapshot)"
            ),
            evidence={
                "patterns": total,
                "instrument_years": round(instrument_years, 2),
                "per_instrument_year": rates,
                "counts": dict(sorted(by_detector.items())),
                **scope.describe(),
            },
        )


class PatternCausality(_Check):
    """Is any pattern detected before the geometry that defines it had printed?

    **Which end date this compares against is the whole check.** A pattern row
    carries two, and the first version of this compared the wrong one:

    ``structural_end_date``
        The structure's end as most recently *re-measured*. It moves. A
        consolidation that keeps consolidating is a longer consolidation, so
        every re-detection extends it — legitimately, from bars that had printed
        by then.
    ``structure_known_through``
        The structure's end as measured **on the session it was first
        detected**. Frozen at insert.

    Comparing ``first_detected_session`` against the moving one measured the
    tracker's memory, not the detector's causality: it flagged 5,849 of 156,433
    patterns on the diagnostic scan, and re-running the detectors over exactly
    the bar prefix available on each failing session reproduced the geometry
    with an end date on the detection session every time. Not one used a bar
    that had not printed. The finding was real and the defect was in the
    provenance, not in the detectors.

    A pattern written before ``structure_known_through`` existed has NULL there
    and **cannot be assessed** — the value was never recorded and cannot be
    recovered, because only the current geometry was ever stored. Those rows
    block the check rather than passing it.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.causality",
            title="No pattern is detected before its own evidence had printed",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.Pattern).where(run.patterns())) or 0
        )
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no patterns have been persisted for this scan run",
            )

        unassessable = (
            session.scalar(
                select(func.count())
                .select_from(tbl.Pattern)
                .where(run.patterns(), tbl.Pattern.structure_known_through.is_(None))
            )
            or 0
        )
        acausal = tbl.Pattern.first_detected_session < tbl.Pattern.structure_known_through
        count = (
            session.scalar(
                select(func.count()).select_from(tbl.Pattern).where(run.patterns(), acausal)
            )
            or 0
        )
        offenders = session.scalars(
            select(tbl.Pattern).where(run.patterns(), acausal).limit(5)
        ).all()
        # How far the *eventual* extent runs past detection, reported so the
        # retrospective movement stays visible rather than becoming invisible
        # now that it no longer fails anything.
        retrospective = (
            session.scalar(
                select(func.count())
                .select_from(tbl.Pattern)
                .where(
                    run.patterns(),
                    tbl.Pattern.first_detected_session < tbl.Pattern.structural_end_date,
                )
            )
            or 0
        )

        evidence = {
            "patterns": total,
            "acausal": count,
            "unassessable": unassessable,
            "structure_extended_after_detection": retrospective,
        }
        if count:
            status, summary = (
                CheckStatus.FAIL,
                f"{count:,} patterns were detected before their own structure had printed",
            )
        elif unassessable:
            status, summary = (
                CheckStatus.BLOCKED,
                f"{unassessable:,} of {total:,} patterns predate the "
                "structure_known_through column and cannot be assessed; the value was "
                "never recorded and is not recoverable from what was stored",
            )
        else:
            status, summary = (
                CheckStatus.PASS,
                f"all {total:,} patterns were first detected on or after the last bar of "
                f"the structure they were measured from; {retrospective:,} were later "
                "re-measured as running further, which is re-measurement rather than "
                "foresight",
            )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=summary,
            evidence=evidence,
            needs=(
                ("a scan run by a build that records structure_known_through",)
                if status is CheckStatus.BLOCKED
                else ()
            ),
            examples=tuple(
                f"pattern={row.identity_key} detected={row.first_detected_session} "
                f"known_through={row.structure_known_through} "
                f"eventual_end={row.structural_end_date}"
                for row in offenders
            ),
        )


# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------


class BreakoutStateDistribution(_Check):
    """Where do breakout attempts end up? Descriptive.

    This and ``phase5.lifecycle``'s ``terminal_states`` are the two fields in
    the gate conditioned on price action *after* a break, so they are the two
    worth arguing about. The argument, and why they are statements about the
    state machine rather than about trading, is in
    :mod:`tradeit.validation.scan_checks`. In short: no position, no entry or
    exit price, no magnitude, no holding period — so nothing here can become a
    return without supplying all four, which is a later phase's job.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.state_distribution",
            title="Distribution of terminal breakout states on real data",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.BreakoutEvent).where(run.events()))
            or 0
        )
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this scan run",
            )
        # Grouped in SQL. `list(session.scalars(select(BreakoutEvent)))` built
        # 341,093 fully-populated ORM instances at full-universe scale: 23.8s
        # and roughly 1.9 GB of resident memory, to compute two tallies the
        # database can produce in one pass.
        states: Counter[str] = Counter()
        for state, count in session.execute(
            select(tbl.BreakoutEvent.state, func.count())
            .where(run.events())
            .group_by(tbl.BreakoutEvent.state)
        ):
            states[str(state)] = int(count)
        paths: Counter[str] = Counter()
        for path, count in session.execute(
            select(tbl.BreakoutEvent.confirmed_path, func.count())
            .where(run.events(), tbl.BreakoutEvent.confirmed_path != "")
            .group_by(tbl.BreakoutEvent.confirmed_path)
        ):
            paths[str(path)] = int(count)
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
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.BreakoutEvent).where(run.events()))
            or 0
        )
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this scan run",
            )
        kinds: Counter[str] = Counter()
        for kind, count in session.execute(
            select(tbl.BreakoutEvent.boundary_kind, func.count())
            .where(run.events())
            .group_by(tbl.BreakoutEvent.boundary_kind)
        ):
            kinds[str(kind)] = int(count)
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
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)
        # Two counts and up to five examples, all in SQL. The predicate is
        # unchanged; only the 341,093 ORM instances it used to be evaluated
        # over are gone.
        scored_predicate = tbl.BreakoutEvent.breakout_quality > 0
        unqualified = tbl.BreakoutEvent.first_qualifying_close_session.is_(None)
        total, scored, offending = session.execute(
            select(
                func.count(),
                func.count().filter(scored_predicate),
                func.count().filter(scored_predicate, unqualified),
            )
            .select_from(tbl.BreakoutEvent)
            .where(run.events())
        ).one()
        if total == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events have been persisted for this scan run",
            )
        offenders = list(
            session.scalars(
                select(tbl.BreakoutEvent)
                .where(run.events(), scored_predicate, unqualified)
                .limit(5)
            )
        )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            # `offending` is the counted total; `offenders` is a bounded sample
            # of it. Judging on the sample would silently cap the finding at
            # five however many there really were.
            status=CheckStatus.PASS if offending == 0 else CheckStatus.FAIL,
            summary=(
                f"all {scored:,} scored events of {total:,} have a qualifying close"
                if offending == 0
                else (
                    f"{offending:,} events carry a breakout quality without ever "
                    "having produced a qualifying close"
                )
            ),
            evidence={"events": total, "scored": scored, "unbacked": offending},
            examples=tuple(
                f"event={e.event_key} attempt={e.attempt_number} quality={e.breakout_quality}"
                for e in offenders
            ),
        )


class BreakoutMonitorFloor(_Check):
    """Were only production-eligible pattern states monitored?

    **As of the session the event opened, not as of now.** A pattern that was
    MATURE when its boundary was watched and EXPIRED four months later was
    monitored correctly; comparing against the pattern row's *current* state
    convicts it of the future. The point-in-time answer is in
    ``pattern_observations``, which exists precisely so a mutable current-state
    row never has to be asked a historical question.

    Reading the final state made this report 5,000 failures on the diagnostic
    scan — and 5,000 was the ``LIMIT``, so the number was a sample size wearing
    a finding's clothes. The limit is gone: a check that cannot afford to count
    its own population should say so, not truncate it.

    The failures were real, but they were not what they looked like. Two
    separate defects produced them, and only one was in this check:

    * the monitor keyed its events by ``PatternInstance.identity_key`` — the
      base content hash — rather than by the tracked identity, so every event on
      a re-minted identity was attributed to the *first* life of that structure,
      which had by then terminated. Fixed in
      :meth:`~tradeit.breakouts.monitor.BreakoutMonitor.monitorable`.
    * this check then compared that mis-attributed row's final state.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.monitor_floor",
            title="Only production-eligible pattern states produced breakout events",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return _unresolved(self, run)

        allowed = sorted(str(state) for state in PRODUCTION_PATTERN_STATES)

        # The pattern's state *as of the session the event opened*, from the
        # observation log, computed in SQL.
        #
        # This used to pull every joined event into Python and then every
        # observation of every pattern those events named, and rebuild the
        # as-of lookup with a bisect. On the full-universe corpus that is
        # 341,093 events against 1.36M observations, and it never finished:
        # the check died on the statement timeout and left the transaction
        # aborted, which took the six checks after it down with it.
        #
        # `DISTINCT ON` over the run-scoped join gives the same answer — the
        # latest observation at or before `opened_session` — as an index scan
        # per event against `uq_pattern_observation (pattern_id,
        # session_date)`, which a btree can walk backwards. Measured at
        # full-universe scale: 2.2s.
        latest = (
            select(tbl.PatternObservation.to_state)
            .where(
                tbl.PatternObservation.pattern_id == tbl.Pattern.id,
                tbl.PatternObservation.session_date <= tbl.BreakoutEvent.opened_session,
            )
            .order_by(tbl.PatternObservation.session_date.desc())
            .limit(1)
            .correlate(tbl.Pattern, tbl.BreakoutEvent)
            .scalar_subquery()
            .label("state_then")
        )
        joined = (
            select(
                tbl.BreakoutEvent.pattern_key.label("pattern_key"),
                tbl.BreakoutEvent.opened_session.label("opened_session"),
                tbl.Pattern.state.label("final_state"),
                latest,
            )
            .join(
                tbl.Pattern,
                # The run belongs in the join condition, not only in a filter.
                # Identity keys are content hashes, so two runs of one snapshot
                # derive the same ones — joining on the key alone would let a
                # Run B event match a Run A pattern and report a state that
                # belongs to a different corpus.
                (tbl.Pattern.identity_key == tbl.BreakoutEvent.pattern_key)
                & (tbl.Pattern.scan_run_id == tbl.BreakoutEvent.scan_run_id),
            )
            .where(run.events())
            .subquery("joined")
        )

        eligible_then = joined.c.state_then.in_(allowed)
        eligible_now = joined.c.final_state.in_(allowed)
        counts = session.execute(
            select(
                func.count().label("joined_events"),
                func.count().filter(joined.c.state_then.is_(None)).label("unobserved"),
                func.count()
                .filter(joined.c.state_then.is_not(None), ~eligible_then)
                .label("below_floor"),
                func.count().filter(eligible_then, ~eligible_now).label("eligible_then_terminal"),
            ).select_from(joined)
        ).one()

        if counts.joined_events == 0:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no breakout events joined to a pattern for this scan run",
            )

        # An anti-join, not `NOT IN`. This is the statement that timed out.
        #
        # `NOT IN` over a subquery can never be planned as an anti-join — the
        # NULL semantics forbid it — so PostgreSQL compiles it to a `SubPlan`.
        # That is fast *while the subquery result fits in `work_mem`*, because
        # the SubPlan is hashed, which is why it survived every smaller corpus.
        # Past that it degrades to a non-hashed SubPlan re-evaluated per row.
        # Measured on a 60,000-key fixture, crossing that one cliff moves the
        # planner's estimate from 7,654 to 53,634,079 and turns 0.07s into a
        # timeout. The full-universe corpus had 272,537 keys.
        #
        # `NOT EXISTS` has no such cliff: it plans as a parallel hash anti join
        # and stays there under memory pressure. Measured at full-universe
        # scale, 163ms.
        pattern_exists = (
            select(literal(1))
            .where(
                tbl.Pattern.identity_key == tbl.BreakoutEvent.pattern_key,
                tbl.Pattern.scan_run_id == tbl.BreakoutEvent.scan_run_id,
            )
            .exists()
        )
        orphans = (
            session.scalar(
                select(func.count())
                .select_from(tbl.BreakoutEvent)
                .where(run.events(), ~pattern_exists)
            )
            or 0
        )

        # Only now, and bounded: the offending rows themselves, for the report.
        offenders = tuple(
            f"pattern={row.pattern_key} opened={row.opened_session} state_then={row.state_then}"
            for row in session.execute(
                select(joined.c.pattern_key, joined.c.opened_session, joined.c.state_then)
                .where(joined.c.state_then.is_not(None), ~eligible_then)
                .limit(5)
            )
        )

        problems = counts.below_floor + counts.unobserved
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if problems == 0 else CheckStatus.FAIL,
            summary=(
                f"all {counts.joined_events:,} events were opened against a pattern that "
                f"was in {allowed} on the session the event opened"
                if problems == 0
                else (
                    f"{counts.below_floor:,} events were opened against a pattern outside "
                    f"the production floor {allowed} on that session, and "
                    f"{counts.unobserved:,} against a pattern with no observation on or "
                    "before it; every rate computed from this dataset describes the "
                    "monitor rather than the market"
                )
            ),
            evidence={
                "joined_events": counts.joined_events,
                "allowed_states": allowed,
                "below_floor_when_opened": counts.below_floor,
                "no_observation_at_opening": counts.unobserved,
                # Reported, never failed: these are the ones a final-state join
                # would have convicted. A non-zero count here is the check
                # working, not the monitor misbehaving.
                "eligible_then_terminal_now": counts.eligible_then_terminal,
                "events_with_no_pattern_row": orphans,
            },
            examples=offenders,
        )


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
