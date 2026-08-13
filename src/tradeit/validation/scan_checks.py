"""What a real scan produced, interrogated. Machinery, never profitability.

These checks run only once :mod:`tradeit.scanning` has persisted something.
Before that they SKIP, and a SKIP is not a pass — that distinction is the whole
reason the gate has five statuses.

**What they are for.** Phase 4 and Phase 5 were tested against synthetic
corpora, where every input was built to contain the thing being looked for. The
first pass over sixteen years of real prices is where the failures that
synthetic data cannot express show up: a detector that fires on every third
session, one that never fires at all, identities that reset instead of
persisting, events concentrated on split dates, lifecycle transitions in an
order the state machine forbids.

**What they are emphatically not for.** No check here computes a forward
return, a win rate, or anything from which one could be assembled. A
distribution of terminal breakout states is a statement about the *engine*; the
same numbers weighted by what happened next would be a statement about the
strategy, and that belongs to a later phase run once against data nobody has
been tuning on. :func:`~tradeit.validation.checks.assert_no_performance_claims`
walks every result produced here.

**Most of them are descriptive on purpose.** Nobody has a defensible prior for
how many cup-with-handles a decade of a large-cap contains, and inventing one
would be tuning a threshold against the validation set. So a rate is reported
as a baseline, and only the structural invariants — causality, identity,
lifecycle legality — are allowed to fail.

**The one field worth arguing about is** ``terminal_states``. A tally of how
many breakout events ended in CONFIRMED versus FAILED_BREAKOUT is conditioned
on price action after the break, which is the closest thing in this module to
an outcome, and it is worth being explicit about why it stays.

It is a statement about the state machine, not about trading. No position is
opened, no entry or exit price exists, no magnitude is attached, and no
holding period is defined — so the numbers cannot be turned into a return, a
win rate or an expectancy without supplying all four, which is precisely the
work a later phase does once, against data nobody has been tuning on. What the
tally can show is machinery: an engine that never reaches a terminal state is
leaking events, and one that reaches only one of them has a broken predicate.
Removing it would hide that and prevent nothing, since the states themselves
are already in the database. It is reported and not judged, and no threshold
in this repository is set from it.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.breakouts.lifecycle import is_legal as is_legal_breakout_transition
from tradeit.data.packages.spec import DatasetKind
from tradeit.storage import tables as tbl
from tradeit.validation.checks import CheckResult, CheckStatus, Phase
from tradeit.validation.context import ValidationContext
from tradeit.validation.scope import RunSelection

__all__ = [
    "BreakoutCausality",
    "BreakoutLifecycle",
    "PatternConcentration",
    "PatternIdentityChurn",
    "PatternIdentityStability",
    "PatternScoreDistribution",
    "scan_checks",
]

#: A single ticker holding more than this share of all detections is reported.
#: Not a failure: a universe containing one volatile small-cap and seventy-seven
#: large-caps legitimately concentrates. It is a prompt to look, and the
#: threshold is set where "one name dominates" stops being arguable.
CONCENTRATION_SHARE = 0.25

#: Likewise for a single session date. Twelve detectors across 78 instruments
#: firing on one day is a market-wide event or a bug, and the two look identical
#: in a total.
DATE_CONCENTRATION_SHARE = 0.05

#: Below this, a pattern's identity is being re-minted rather than tracked: it
#: was observed on one session and never again. Some of that is real — a
#: structure that failed its tests the next day — but a corpus where most
#: patterns have a single observation means the tracker is not doing its job.
MIN_MEAN_OBSERVATIONS = 1.5


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

    def unresolved(self, run: RunSelection) -> CheckResult:
        """BLOCKED, because these checks read exactly one scan run.

        Never a silent aggregate: two completed scans over one snapshot are two
        corpora, and reporting them together describes a population that never
        existed.
        """
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.BLOCKED,
            summary=f"not run: {run.problem}",
            needs=("a single named scan run (--scan-id)",),
            examples=tuple(f"candidate: {name}" for name in run.candidates[:8]),
        )

    def skipped(self, summary: str) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.SKIPPED,
            summary=summary,
        )

    def result(
        self,
        status: CheckStatus,
        summary: str,
        *,
        evidence: dict[str, Any] | None = None,
        examples: tuple[str, ...] = (),
        detail: tuple[str, ...] = (),
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=summary,
            evidence=evidence or {},
            examples=examples,
            detail=detail,
        )


NO_PATTERNS = (
    "no patterns have been persisted for this snapshot; run `tradeit scan "
    "--snapshot <id>` before the Phase 4 checks"
)
NO_BREAKOUTS = (
    "no breakout events have been persisted for this snapshot; run `tradeit scan "
    "--snapshot <id>` before the Phase 5 checks"
)


# ---------------------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------------------


class PatternScoreDistribution(_Check):
    """What the detectors' own scores look like on data nobody curated.

    Descriptive. The failure it can express is narrow and worth having: a
    detector whose scores are all identical is not scoring, and one whose
    evidence coverage is uniformly 100% on real data is not noticing that
    evidence is missing.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.score_distribution",
            title="Quality and evidence-coverage distributions per detector",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        rows = context.session.execute(
            select(
                tbl.Pattern.detector_name,
                func.count(),
                func.min(tbl.Pattern.quality),
                func.avg(tbl.Pattern.quality),
                func.max(tbl.Pattern.quality),
                func.min(tbl.Pattern.evidence_coverage),
                func.avg(tbl.Pattern.evidence_coverage),
            )
            .where(run.patterns())
            .group_by(tbl.Pattern.detector_name)
        ).all()
        if not rows:
            return self.skipped(NO_PATTERNS)

        states = Counter(
            state
            for (state,) in context.session.execute(select(tbl.Pattern.state).where(run.patterns()))
        )
        degenerate: list[str] = []
        lines = [
            f"  {'detector':<24} {'n':>7}  {'quality min/avg/max':<26} coverage min/avg",
            f"  {'-' * 24} {'-' * 7}  {'-' * 26} {'-' * 16}",
        ]
        for name, count, qmin, qavg, qmax, cmin, cavg in sorted(rows):
            lines.append(
                f"  {name:<24} {count:>7,}  "
                f"{qmin:>7.1f} /{qavg:>7.1f} /{qmax:>7.1f}   "
                f"{cmin:>6.1f} /{cavg:>6.1f}"
            )
            if count >= 20 and qmax - qmin < 1e-9:
                degenerate.append(f"{name}: every one of {count:,} patterns scored {qmax:.4f}")

        status = CheckStatus.WARN if degenerate else CheckStatus.PASS
        total = sum(row[1] for row in rows)
        return self.result(
            status,
            (
                f"{total:,} patterns across {len(rows)} detectors; scores and coverage "
                "reported as a baseline, not judged"
                + (
                    f". {len(degenerate)} detector(s) produced a constant score"
                    if degenerate
                    else ""
                )
            ),
            evidence={
                "patterns": total,
                "detectors": len(rows),
                "states": dict(sorted(states.items())),
                "per_detector": {
                    name: {
                        "count": count,
                        "quality_min": round(qmin, 4),
                        "quality_mean": round(float(qavg), 4),
                        "quality_max": round(qmax, 4),
                        "coverage_min": round(cmin, 4),
                        "coverage_mean": round(float(cavg), 4),
                    }
                    for name, count, qmin, qavg, qmax, cmin, cavg in sorted(rows)
                },
            },
            examples=tuple(degenerate[:5]),
            detail=tuple(lines),
        )


class PatternIdentityStability(_Check):
    """Are identities persisting across sessions, or being re-minted daily?

    The failure this exists to catch is the one the tracker was written to
    prevent: a pattern observed once, forgotten, and re-detected tomorrow as a
    new identity. The database cannot tell that apart from twelve genuinely
    different structures, and a false-positive rate computed over re-minted
    identities is a rate over an inflated denominator.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.identity_stability",
            title="Pattern identities persist across sessions",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.Pattern).where(run.patterns())) or 0
        )
        if total == 0:
            return self.skipped(NO_PATTERNS)

        observations = (
            session.scalar(
                select(func.count())
                .select_from(tbl.PatternObservation)
                .where(run.pattern_observations())
            )
            or 0
        )
        single = (
            session.scalar(
                select(func.count()).select_from(
                    select(tbl.PatternObservation.pattern_id)
                    .where(run.pattern_observations())
                    .group_by(tbl.PatternObservation.pattern_id)
                    .having(func.count() == 1)
                    .subquery()
                )
            )
            or 0
        )
        # A forked identity carries the fork session appended to its key. High
        # fork counts mean detections keep proposing transitions the lifecycle
        # does not have, which is a detector disagreeing with the state machine
        # rather than a market event.
        forked = (
            session.scalar(
                select(func.count())
                .select_from(tbl.Pattern)
                .where(run.patterns(), tbl.Pattern.identity_key.like("%:____-__-__"))
            )
            or 0
        )
        mean = observations / total if total else 0.0
        status = CheckStatus.WARN if mean < MIN_MEAN_OBSERVATIONS else CheckStatus.PASS
        return self.result(
            status,
            (
                f"{observations:,} observations over {total:,} identities "
                f"({mean:.2f} per identity); {single:,} were seen on exactly one session"
                + (
                    ". Below the floor: identities are being re-minted rather than tracked"
                    if status is CheckStatus.WARN
                    else ""
                )
            ),
            evidence={
                "identities": total,
                "observations": observations,
                "mean_observations_per_identity": round(mean, 4),
                "single_observation_identities": single,
                "single_observation_share": round(single / total, 4) if total else 0.0,
                "forked_identities": forked,
                "floor": MIN_MEAN_OBSERVATIONS,
            },
        )


class PatternConcentration(_Check):
    """Is the corpus one instrument, or one day, wearing a costume?

    Two shapes of the same problem. A single ticker supplying most detections
    means the rates describe that ticker. A single session supplying an
    outsized share means something happened to the *data* on that date — a
    split the adjustment did not smooth, a bad vendor print — and the detectors
    all found the same artefact.

    Also reports the instruments that produced nothing, because a detector set
    silent on a quarter of the universe is a finding that no aggregate shows.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.concentration",
            title="Detections are not concentrated in one name or one session",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        total = (
            session.scalar(select(func.count()).select_from(tbl.Pattern).where(run.patterns())) or 0
        )
        if total == 0:
            return self.skipped(NO_PATTERNS)

        tickers = {
            row.instrument_id: row.ticker for row in session.scalars(select(tbl.SymbolMapping))
        }
        by_instrument: Counter[int] = Counter()
        for instrument_id, count in session.execute(
            select(tbl.Pattern.instrument_id, func.count())
            .where(run.patterns())
            .group_by(tbl.Pattern.instrument_id)
        ).all():
            by_instrument[int(instrument_id)] = int(count)
        # The *completed* scan universe, never the snapshot. `diag-01` covered
        # seven of seventy-eight instruments and this check duly reported
        # seventy-one as having produced no detections — true of the database,
        # false about the detectors, and the most expensive kind of wrong a
        # validation report can be.
        scope = context.scope
        scanned = set(scope.completed)
        silent = sorted(tickers.get(i, str(i)) for i in scanned if by_instrument.get(i, 0) == 0)

        by_date: Counter[dt.date] = Counter()
        for day, count in session.execute(
            select(tbl.Pattern.first_detected_session, func.count())
            .where(run.patterns())
            .group_by(tbl.Pattern.first_detected_session)
        ).all():
            by_date[day] = int(count)

        findings: list[str] = []
        for instrument_id, count in by_instrument.most_common(3):
            share = count / total
            if share > CONCENTRATION_SHARE:
                name = tickers.get(instrument_id, str(instrument_id))
                findings.append(f"{name} supplies {share:.1%} of all detections ({count:,})")
        for day, count in by_date.most_common(3):
            share = count / total
            if share > DATE_CONCENTRATION_SHARE:
                findings.append(
                    f"{day.isoformat()} supplies {share:.1%} of all first detections ({count:,})"
                )

        status = CheckStatus.WARN if findings or silent else CheckStatus.PASS
        return self.result(
            status,
            (
                f"{total:,} detections over {len(by_instrument)} of {len(scanned)} scanned "
                f"instruments; {len(silent)} produced none"
                + (
                    f" ({len(scope.unscanned)} further instruments in the snapshot were "
                    "never scanned and are excluded)"
                    if scope.unscanned
                    else ""
                )
                + (f". {len(findings)} concentration finding(s)" if findings else "")
            ),
            evidence={
                "patterns": total,
                "instruments_scanned": len(scanned),
                "instruments_with_detections": len(by_instrument),
                "instruments_with_none": len(silent),
                "silent_instruments": silent[:40],
                **scope.describe(),
                "top_instrument_share": (
                    round(by_instrument.most_common(1)[0][1] / total, 4) if by_instrument else 0.0
                ),
                "top_session_share": (
                    round(by_date.most_common(1)[0][1] / total, 4) if by_date else 0.0
                ),
            },
            examples=tuple(findings[:5]),
        )


class PatternIdentityChurn(_Check):
    """Why does one structure end up under many identities?

    ``phase4.identity_stability`` says *how many* — 95.2% of the diagnostic
    scan's 156,433 identities carried a re-mint suffix. It cannot say why, and
    the two possible answers call for opposite responses:

    *expected tracking semantics*
        a structure genuinely ends and a genuinely new one begins on the same
        instrument. Re-minting is then correct, and merging would be the bug.
    *identity fragmentation*
        one continuous structure is repeatedly torn into new identities by a
        rule about state, not about structure. Every per-identity rate is then
        computed over an inflated denominator.

    This reports the evidence that separates them: the state an identity held
    when it was superseded, what it was re-detected as, how long identities
    live, and how many identities share one base hash. A structure that is
    re-minted every session with consecutive dates is fragmentation; one
    re-minted once a year is a second life.

    Descriptive, and deliberately so. The remedy is a lifecycle decision, not a
    threshold, and nothing here may be tuned against a real-data result.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase4.identity_churn",
            title="Identity re-minting, by detector and by cause",
            phase=Phase.PHASE_4,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        rows = session.execute(
            select(
                tbl.Pattern.id,
                tbl.Pattern.detector_name,
                tbl.Pattern.identity_key,
                tbl.Pattern.first_detected_session,
                tbl.Pattern.last_observed_session,
            ).where(run.patterns())
        ).all()
        if not rows:
            return self.skipped(NO_PATTERNS)

        observations: dict[int, int] = {
            int(pattern_id): int(count)
            for pattern_id, count in session.execute(
                select(tbl.PatternObservation.pattern_id, func.count())
                .where(run.pattern_observations())
                .group_by(tbl.PatternObservation.pattern_id)
            )
        }

        per: dict[str, _ChurnRow] = {}
        bases: Counter[tuple[str, str]] = Counter()
        for pattern_id, detector, key, first, last in rows:
            entry = per.setdefault(detector, _ChurnRow())
            base = key.split(":")[0]
            entry.add(
                forked=":" in key,
                observations=observations.get(int(pattern_id), 0),
                lifespan=(last - first).days,
            )
            bases[(detector, base)] += 1

        # Why an identity ended: the tracker writes the cause onto the
        # superseding transition, so this is recorded fact rather than a guess.
        superseded = Counter(
            state
            for (state,) in session.execute(
                select(tbl.PatternObservation.from_state).where(
                    run.pattern_observations(),
                    tbl.PatternObservation.reason == "superseded",
                )
            )
        )
        redetected: Counter[str] = Counter()
        for (note,) in session.execute(
            select(tbl.PatternObservation.note).where(
                run.pattern_observations(),
                tbl.PatternObservation.note.like("identity ended%"),
            )
        ):
            match = _REDETECTED_AS.search(note or "")
            if match:
                redetected[match.group(1)] += 1
        new_life = (
            session.scalar(
                select(func.count())
                .select_from(tbl.PatternObservation)
                .where(run.pattern_observations(), tbl.PatternObservation.note.like("new life%"))
            )
            or 0
        )

        total = len(rows)
        forked = sum(entry.forked for entry in per.values())
        crowded = [(pair, count) for pair, count in bases.items() if count > 1]
        worst = max((count for _, count in crowded), default=0)

        header = (
            f"  {'detector':<24}{'ids':>8}{'forked':>9}{'fork%':>8}"
            f"{'obs med':>9}{'p90':>6}{'p99':>6}{'life med':>10}{'single%':>9}"
        )
        lines = [header, "  " + "-" * (len(header) - 2)]
        for detector in sorted(per):
            lines.append("  " + per[detector].render(detector))

        return self.result(
            CheckStatus.WARN if forked > total // 2 else CheckStatus.PASS,
            (
                f"{forked:,} of {total:,} identities ({forked / total:.1%}) carry a re-mint "
                f"suffix; {len(crowded):,} base hashes name more than one identity, the "
                f"most crowded naming {worst:,}"
            ),
            evidence={
                "identities": total,
                "reminted_identities": forked,
                "reminted_share": round(forked / total, 4),
                "bases_naming_more_than_one_identity": len(crowded),
                "most_identities_on_one_base": worst,
                "ended_from_state": dict(sorted(superseded.items())),
                "redetected_as": dict(sorted(redetected.items())),
                "remints_after_termination": new_life,
                "remints_on_an_illegal_transition": sum(superseded.values()),
            },
            detail=tuple(lines),
        )


#: The tracker's own wording, parsed back out rather than re-derived.
_REDETECTED_AS = re.compile(r"re-detected as (\w+)")


@dataclass(slots=True)
class _ChurnRow:
    """One detector's churn tallies. A row of the table, not a finding."""

    identities: int = 0
    forked: int = 0
    single: int = 0
    observations: list[int] = field(default_factory=list)
    lifespans: list[int] = field(default_factory=list)

    def add(self, *, forked: bool, observations: int, lifespan: int) -> None:
        self.identities += 1
        self.forked += forked
        self.single += observations <= 1
        self.observations.append(observations)
        self.lifespans.append(lifespan)

    def render(self, detector: str) -> str:
        return (
            f"{detector:<24}{self.identities:>8,}{self.forked:>9,}"
            f"{self.forked / self.identities:>8.1%}"
            f"{_quantile(self.observations, 0.50):>9.1f}"
            f"{_quantile(self.observations, 0.90):>6.0f}"
            f"{_quantile(self.observations, 0.99):>6.0f}"
            f"{_quantile(self.lifespans, 0.50):>10.0f}"
            f"{self.single / self.identities:>9.1%}"
        )


def _quantile(values: list[int], q: float) -> float:
    """Nearest-rank quantile. No interpolation: these are counts of sessions."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))])


# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------


class BreakoutLifecycle(_Check):
    """Do the recorded transitions obey the state machine that produced them?

    Every stored observation names the state it came from and the state it went
    to. The lifecycle module knows which of those transitions exist. One that
    does not means the engine, the persistence layer, or a replay put an event
    somewhere it cannot legally be, and everything measured downstream of that
    event is measured over a history that never happened.

    This one is allowed to fail, because it is not a matter of taste.

    **On the word this check used to use.** A state machine is a directed
    graph and its transitions are its edges, so the first version of this said
    so — and ``assert_no_performance_claims`` aborted a real validation run,
    because in a trading system "edge" is overwhelmingly read as *trading*
    edge, the single most forbidden quantity in this gate. The guard was right
    to be suspicious and the naming was wrong: nothing here is a graph-theory
    result, and "transition" says the same thing with no second reading. The
    vocabulary is now ``transition`` throughout, and
    :data:`~tradeit.validation.checks._STRUCTURAL_SENSES` covers the case
    where graph vocabulary is genuinely the clearest wording.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.lifecycle",
            title="Breakout transitions are legal and counted",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        rows = session.execute(
            select(
                tbl.BreakoutObservation.event_id,
                tbl.BreakoutObservation.session_date,
                tbl.BreakoutObservation.from_state,
                tbl.BreakoutObservation.to_state,
                tbl.BreakoutObservation.reason,
            )
            .where(run.breakout_observations())
            .order_by(tbl.BreakoutObservation.event_id, tbl.BreakoutObservation.session_date)
        ).all()
        if not rows:
            return self.skipped(NO_BREAKOUTS)

        transitions = Counter(f"{r.from_state or 'new'} -> {r.to_state}" for r in rows)
        reasons = Counter(r.reason for r in rows)
        illegal: list[str] = []
        for row in rows:
            if row.from_state is None:
                continue
            try:
                origin = BreakoutState(row.from_state)
                target = BreakoutState(row.to_state)
            except ValueError:  # pragma: no cover - a state from a newer build
                illegal.append(
                    f"event={row.event_id} {row.session_date}: unrecognised state "
                    f"{row.from_state} -> {row.to_state}"
                )
                continue
            if origin is not target and not is_legal_breakout_transition(origin, target):
                illegal.append(
                    f"event={row.event_id} {row.session_date}: {origin} -> {target} "
                    "is not a legal transition of the lifecycle"
                )

        events = (
            session.scalar(select(func.count()).select_from(tbl.BreakoutEvent).where(run.events()))
            or 0
        )
        terminal = Counter(
            state
            for (state,) in session.execute(select(tbl.BreakoutEvent.state).where(run.events()))
        )
        status = CheckStatus.PASS if not illegal else CheckStatus.FAIL
        return self.result(
            status,
            (
                f"{len(rows):,} observations across {events:,} events; every recorded "
                "transition is one the state machine allows"
                if not illegal
                else f"{len(illegal):,} of {len(rows):,} recorded transitions are not "
                "ones the state machine allows"
            ),
            evidence={
                "events": events,
                "observations": len(rows),
                "terminal_states": dict(sorted(terminal.items())),
                "state_transitions": dict(sorted(transitions.items())),
                "transition_reasons": dict(sorted(reasons.items())),
                "illegal_transitions": len(illegal),
            },
            examples=tuple(illegal[:5]),
        )


class BreakoutCausality(_Check):
    """No event may be observed before it opened, or twice on one session.

    Both are causality failures rather than data quirks. An observation dated
    before ``opened_session`` means the engine evaluated a session the event did
    not exist in; two observations for one session mean the same bar was
    evaluated twice and only one answer survived, which makes the history
    unreplayable.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="phase5.causality",
            title="Breakout observations follow their event's own clock",
            phase=Phase.PHASE_5,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        run = context.run
        if not run.is_resolved:
            return self.unresolved(run)
        total = (
            session.scalar(
                select(func.count())
                .select_from(tbl.BreakoutObservation)
                .where(run.breakout_observations())
            )
            or 0
        )
        if total == 0:
            return self.skipped(NO_BREAKOUTS)

        early = session.execute(
            select(
                tbl.BreakoutEvent.event_key,
                tbl.BreakoutEvent.opened_session,
                tbl.BreakoutObservation.session_date,
            )
            .join(
                tbl.BreakoutObservation,
                tbl.BreakoutObservation.event_id == tbl.BreakoutEvent.id,
            )
            .where(
                run.events(),
                tbl.BreakoutObservation.session_date < tbl.BreakoutEvent.opened_session,
            )
            .limit(5)
        ).all()
        early_count = (
            session.scalar(
                select(func.count())
                .select_from(tbl.BreakoutObservation)
                .join(
                    tbl.BreakoutEvent,
                    tbl.BreakoutObservation.event_id == tbl.BreakoutEvent.id,
                )
                .where(
                    run.events(),
                    tbl.BreakoutObservation.session_date < tbl.BreakoutEvent.opened_session,
                )
            )
            or 0
        )

        # The unique constraint makes a duplicate impossible to write, so a
        # non-zero count here would mean the schema is not what this build
        # thinks it is. Cheap to ask, and the answer is load-bearing.
        duplicated = (
            session.scalar(
                select(func.count()).select_from(
                    select(tbl.BreakoutObservation.event_id)
                    .where(run.breakout_observations())
                    .group_by(
                        tbl.BreakoutObservation.event_id,
                        tbl.BreakoutObservation.session_date,
                    )
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            or 0
        )
        problems = early_count + duplicated
        return self.result(
            CheckStatus.PASS if problems == 0 else CheckStatus.FAIL,
            (
                f"all {total:,} breakout observations fall on or after their event opened, "
                "one per session"
                if problems == 0
                else f"{early_count:,} observations predate their event and {duplicated:,} "
                "sessions carry more than one"
            ),
            evidence={
                "observations": total,
                "before_event_opened": early_count,
                "duplicated_sessions": duplicated,
            },
            examples=tuple(
                f"event={key} opened={opened} observed={observed}"
                for key, opened, observed in early
            ),
        )


def scan_checks() -> list[Any]:
    """The checks that only mean anything once a scan has run."""
    return [
        PatternScoreDistribution(),
        PatternIdentityStability(),
        PatternIdentityChurn(),
        PatternConcentration(),
        BreakoutLifecycle(),
        BreakoutCausality(),
    ]
