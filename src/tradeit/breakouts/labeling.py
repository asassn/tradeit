"""Human labelling of breakout events.

Extends the Phase 4 labelling framework to breakouts, with one addition that is
not cosmetic: **every label carries an explicit knowledge horizon.**

The reason is item 43, and it is easy to get wrong in a way that is invisible
afterwards. Some of these labels are decidable from the breakout bar alone —
"was this a valid break of the level, or a weak one?" is a question about the
bar. Others are not: FALSE_BREAKOUT and FAILED_RETEST are claims about what
happened next, and a reviewer cannot make them without seeing a window after the
event.

That is legitimate, and it is *only* legitimate if the window is stated. A
dataset in which some labels were assigned from the breakout bar and others from
a month of hindsight, with no record of which, is a dataset whose labels
silently encode different amounts of future information. Every label here
therefore declares:

* ``as_of_session`` — the breakout session being judged;
* ``knowledge_horizon_session`` — the last session the reviewer was shown.

and :data:`LABEL_HORIZONS` states, per label, the minimum window its definition
requires. A FALSE_BREAKOUT assigned with a horizon equal to the breakout session
is refused, because nothing visible on that session could support it.

**Nobody is asked whether the stock made money.** There is no field for it, the
vocabulary has no member for it, and the reviewer instructions in
``docs/BREAKOUT_LABELING.md`` say so. Outcome data attaches separately and
later, with its own horizon; mixing it in here would make every downstream use
of the structural labels circular.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.breakouts.base import BreakoutEvent
from tradeit.core.clock import utcnow
from tradeit.errors import DataError
from tradeit.storage import tables


class BreakoutLabel(StrEnum):
    """What a reviewer concluded about a breakout event.

    Structural and confirmation judgements. None of these is a profitability
    judgement, and the two that sound closest — VALID_BREAKOUT and
    FALSE_BREAKOUT — are about whether the level was genuinely cleared and
    whether price held it, not about what the position would have returned.
    """

    #: A genuine, convincing break of a real level.
    VALID_BREAKOUT = "valid_breakout"
    #: A real break, but marginal: barely clear, thin, or unconvincing.
    WEAK_BREAKOUT = "weak_breakout"
    #: Price cleared the level and returned inside it; the break did not hold.
    FALSE_BREAKOUT = "false_breakout"
    #: Price returned to the level after breaking out and the level held.
    SUCCESSFUL_RETEST = "successful_retest"
    #: Price returned to the level and broke it.
    FAILED_RETEST = "failed_retest"
    #: The reviewer genuinely cannot tell. Data, not an absence of data.
    AMBIGUOUS = "ambiguous"
    #: The example cannot be judged: bad bars, a gap in the series, a level that
    #: makes no sense. Distinct from AMBIGUOUS, because this example should be
    #: retired rather than sent to another reviewer.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

    @property
    def is_judgement(self) -> bool:
        """Whether the reviewer actually reached a conclusion."""
        return self not in (BreakoutLabel.INSUFFICIENT_EVIDENCE,)

    @property
    def retires_example(self) -> bool:
        return self is BreakoutLabel.INSUFFICIENT_EVIDENCE

    @property
    def concerns_retest(self) -> bool:
        return self in (BreakoutLabel.SUCCESSFUL_RETEST, BreakoutLabel.FAILED_RETEST)


#: Minimum sessions of hindsight each label's *definition* requires.
#:
#: Not a policy about how much a reviewer should see — a statement about what
#: the label means. VALID_BREAKOUT and WEAK_BREAKOUT describe the breakout bar
#: and need none. FALSE_BREAKOUT describes what happened after it and cannot be
#: assigned from the bar itself; the same is true of both retest labels, more
#: so, because a retest takes time to occur at all.
LABEL_HORIZONS: Mapping[BreakoutLabel, int] = {
    BreakoutLabel.VALID_BREAKOUT: 0,
    BreakoutLabel.WEAK_BREAKOUT: 0,
    BreakoutLabel.FALSE_BREAKOUT: 3,
    BreakoutLabel.SUCCESSFUL_RETEST: 5,
    BreakoutLabel.FAILED_RETEST: 5,
    BreakoutLabel.AMBIGUOUS: 0,
    BreakoutLabel.INSUFFICIENT_EVIDENCE: 0,
}


@dataclass(frozen=True, slots=True)
class BreakoutLabelRequest:
    """One reviewer's opinion of one breakout event, with its horizon.

    ``sessions_of_hindsight`` is supplied by the caller rather than derived from
    the two dates, because the reviewer's window is counted in *trading*
    sessions and this module has no calendar. Deriving it from calendar days
    would understate a window spanning a holiday week, which is exactly the kind
    of quiet inaccuracy the horizon exists to prevent.
    """

    instrument_id: int
    as_of_session: dt.date
    knowledge_horizon_session: dt.date
    sessions_of_hindsight: int
    reviewer: str
    label: BreakoutLabel
    timeframe: str = "1d"
    reviewer_confidence: float | None = None
    comments: str = ""

    event_key: str = ""
    engine_state: str | None = None
    engine_breakout_quality: float | None = None
    engine_confirmation_score: float | None = None
    engine_coverage: float | None = None
    engine_profile: str | None = None
    breakout_config_digest: str | None = None
    scorer_version: int | None = None

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise DataError("a label needs a reviewer")
        if self.knowledge_horizon_session < self.as_of_session:
            raise DataError(
                f"knowledge horizon {self.knowledge_horizon_session} precedes the "
                f"labelled session {self.as_of_session}"
            )
        if self.sessions_of_hindsight < 0:
            raise DataError("sessions_of_hindsight cannot be negative")
        required = LABEL_HORIZONS[self.label]
        if self.sessions_of_hindsight < required:
            raise DataError(
                f"{self.label} needs at least {required} session(s) of hindsight and "
                f"was assigned with {self.sessions_of_hindsight}: nothing visible by "
                f"{self.knowledge_horizon_session} could support that judgement"
            )
        if self.reviewer_confidence is not None and not 0.0 <= self.reviewer_confidence <= 100.0:
            raise DataError("reviewer_confidence must be a percentage")

    @classmethod
    def from_event(
        cls,
        event: BreakoutEvent,
        *,
        reviewer: str,
        label: BreakoutLabel,
        knowledge_horizon_session: dt.date,
        sessions_of_hindsight: int,
        reviewer_confidence: float | None = None,
        comments: str = "",
    ) -> BreakoutLabelRequest:
        """Build a request with the engine's own reading pinned to it.

        Pinned rather than looked up later, so agreement is computed against
        what the engine said at labelling time rather than against whatever it
        says when the query runs — which, after a scorer version bump, is a
        different engine.
        """
        session = event.first_qualifying_close_session or event.opened_session
        return cls(
            instrument_id=event.instrument_id,
            as_of_session=session,
            knowledge_horizon_session=knowledge_horizon_session,
            sessions_of_hindsight=sessions_of_hindsight,
            reviewer=reviewer,
            label=label,
            timeframe=str(event.timeframe),
            reviewer_confidence=reviewer_confidence,
            comments=comments,
            event_key=event.event_key,
            engine_state=str(event.state),
            engine_breakout_quality=event.breakout_quality,
            engine_confirmation_score=event.confirmation_score,
            engine_coverage=event.evidence_coverage,
            engine_profile=event.profile_name,
            breakout_config_digest=event.breakout_config_digest,
            scorer_version=event.scorer_version,
        )


@dataclass(frozen=True, slots=True)
class BreakoutAgreement:
    """Inter-reviewer agreement over the labelled breakout corpus."""

    examples: int
    multiply_reviewed: int
    unanimous: int
    contested: int
    abstentions: int

    @property
    def agreement_rate(self) -> float | None:
        if self.multiply_reviewed == 0:
            return None
        return self.unanimous / self.multiply_reviewed

    def summary(self) -> dict[str, object]:
        return {
            "examples": self.examples,
            "multiply_reviewed": self.multiply_reviewed,
            "unanimous": self.unanimous,
            "contested": self.contested,
            "abstentions": self.abstentions,
            "agreement_rate": self.agreement_rate,
        }


class BreakoutLabelService:
    """Records and reads breakout labels. Append-only.

    A reviewer changing their mind writes a new revision; the earlier opinion
    stays. That is not bookkeeping fussiness — a reviewer who reverses is
    telling you the example is hard, and agreement computed after overwrites is
    computed over a population that erased its own disagreements.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, request: BreakoutLabelRequest) -> tables.BreakoutLabel:
        event_id = None
        if request.event_key:
            event_id = self.session.execute(
                select(tables.BreakoutEvent.id).where(
                    tables.BreakoutEvent.event_key == request.event_key
                )
            ).scalar_one_or_none()

        row = tables.BreakoutLabel(
            event_id=event_id,
            instrument_id=request.instrument_id,
            as_of_session=request.as_of_session,
            knowledge_horizon_session=request.knowledge_horizon_session,
            timeframe=request.timeframe,
            reviewer=request.reviewer,
            revision=self._next_revision(request),
            label=str(request.label),
            reviewer_confidence=request.reviewer_confidence,
            comments=request.comments or None,
            engine_state=request.engine_state,
            engine_breakout_quality=request.engine_breakout_quality,
            engine_confirmation_score=request.engine_confirmation_score,
            engine_coverage=request.engine_coverage,
            engine_profile=request.engine_profile,
            breakout_config_digest=request.breakout_config_digest,
            scorer_version=request.scorer_version,
            labelled_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _next_revision(self, request: BreakoutLabelRequest) -> int:
        highest = self.session.execute(
            select(func.max(tables.BreakoutLabel.revision)).where(
                tables.BreakoutLabel.instrument_id == request.instrument_id,
                tables.BreakoutLabel.as_of_session == request.as_of_session,
                tables.BreakoutLabel.timeframe == request.timeframe,
                tables.BreakoutLabel.reviewer == request.reviewer,
            )
        ).scalar()
        return 1 if highest is None else int(highest) + 1

    def latest_per_reviewer(
        self, instrument_id: int, as_of_session: dt.date, timeframe: str = "1d"
    ) -> dict[str, tables.BreakoutLabel]:
        rows = self.session.execute(
            select(tables.BreakoutLabel)
            .where(
                tables.BreakoutLabel.instrument_id == instrument_id,
                tables.BreakoutLabel.as_of_session == as_of_session,
                tables.BreakoutLabel.timeframe == timeframe,
            )
            .order_by(tables.BreakoutLabel.revision)
        ).scalars()
        latest: dict[str, tables.BreakoutLabel] = {}
        for row in rows:
            latest[row.reviewer] = row
        return latest

    def agreement(self) -> BreakoutAgreement:
        """Agreement across the labelled corpus, over latest revisions only.

        Counting every revision would let one indecisive reviewer's three
        opinions look like three reviewers disagreeing.
        """
        rows = list(
            self.session.execute(
                select(tables.BreakoutLabel).order_by(tables.BreakoutLabel.revision)
            ).scalars()
        )
        grouped: dict[tuple[int, dt.date, str], dict[str, str]] = {}
        for row in rows:
            key = (row.instrument_id, row.as_of_session, row.timeframe)
            grouped.setdefault(key, {})[row.reviewer] = row.label

        multiply = unanimous = contested = abstentions = 0
        for opinions in grouped.values():
            judgements = [label for label in opinions.values() if BreakoutLabel(label).is_judgement]
            abstentions += len(opinions) - len(judgements)
            if len(judgements) < 2:
                continue
            multiply += 1
            if len(set(judgements)) == 1:
                unanimous += 1
            else:
                contested += 1

        return BreakoutAgreement(
            examples=len(grouped),
            multiply_reviewed=multiply,
            unanimous=unanimous,
            contested=contested,
            abstentions=abstentions,
        )


class BreakoutQueueStrategy(StrEnum):
    """How to pick the next breakout events for a human to look at.

    ``FUTURE_BLIND`` is the default and the one that matters. Item 43 forbids
    selecting positive examples because they later rallied, and the way that
    happens in practice is not malice — it is a queue sorted by "most
    interesting", where interesting quietly means "moved a lot afterwards". This
    strategy samples on the engine's *state and score at the breakout*, which
    contains no future information at all.
    """

    #: Uniform over events, seeded. The unbiased baseline.
    RANDOM = "random"
    #: Stratified across breakout-quality bands, so the labelled corpus is not
    #: all high scorers.
    STRATIFIED_QUALITY = "stratified_quality"
    #: Stratified across the engine's terminal states, so confirmed, failed and
    #: expired events are all represented.
    STRATIFIED_STATE = "stratified_state"
    #: Events whose score and state disagree — high quality that failed, low
    #: quality that confirmed. The most informative and the most biased; use
    #: alongside a random sample, never instead of one.
    DISAGREEMENT = "disagreement"
    #: Events already reviewed once, where reviewers disagreed.
    CONTESTED = "contested"
    #: The explicit no-future-information sampler: selection uses only the
    #: breakout session's own reading.
    FUTURE_BLIND = "future_blind"


@dataclass(frozen=True, slots=True)
class BreakoutQueueItem:
    """One event queued for review, with why it was chosen."""

    event_id: int
    event_key: str
    instrument_id: int
    as_of_session: dt.date
    timeframe: str
    strategy: BreakoutQueueStrategy
    reason: str
    engine_state: str
    breakout_quality: float
    confirmation_score: float
    evidence_coverage: float
    #: Sessions of hindsight the reviewer may be shown. Set by the queue rather
    #: than by the reviewer, so the horizon is a property of the sampling design
    #: rather than of how far someone happened to scroll.
    hindsight_sessions: int

    def to_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_key": self.event_key,
            "instrument_id": self.instrument_id,
            "as_of_session": self.as_of_session.isoformat(),
            "timeframe": self.timeframe,
            "strategy": str(self.strategy),
            "reason": self.reason,
            "engine_state": self.engine_state,
            "breakout_quality": round(self.breakout_quality, 4),
            "confirmation_score": round(self.confirmation_score, 4),
            "evidence_coverage": round(self.evidence_coverage, 4),
            "hindsight_sessions": self.hindsight_sessions,
        }


class BreakoutReviewQueue:
    """Selects breakout events for human review.

    The whole class is a bias control. Whatever strategy is chosen, the
    selection reads only columns that were knowable at the breakout —
    ``breakout_quality`` is frozen there, ``state`` is read at the time of
    sampling and the item records it, and no query in this class touches a price
    after the event.
    """

    def __init__(self, session: Session, *, hindsight_sessions: int = 10) -> None:
        self.session = session
        self.hindsight_sessions = hindsight_sessions

    def select(
        self,
        strategy: BreakoutQueueStrategy = BreakoutQueueStrategy.FUTURE_BLIND,
        *,
        limit: int = 50,
        seed: int = 0,
    ) -> list[BreakoutQueueItem]:
        rows = list(
            self.session.execute(
                select(tables.BreakoutEvent).order_by(tables.BreakoutEvent.id)
            ).scalars()
        )
        if not rows:
            return []

        if strategy is BreakoutQueueStrategy.RANDOM:
            chosen = self._random(rows, limit, seed)
            reason = "uniform random sample"
        elif strategy is BreakoutQueueStrategy.STRATIFIED_QUALITY:
            chosen = self._stratified(rows, limit, key=lambda r: _band(r.breakout_quality))
            reason = "stratified across breakout-quality bands"
        elif strategy is BreakoutQueueStrategy.STRATIFIED_STATE:
            chosen = self._stratified(rows, limit, key=lambda r: r.state)
            reason = "stratified across engine states"
        elif strategy is BreakoutQueueStrategy.DISAGREEMENT:
            chosen = self._disagreement(rows, limit)
            reason = "engine score and outcome state disagree"
        elif strategy is BreakoutQueueStrategy.CONTESTED:
            chosen = self._contested(rows, limit)
            reason = "reviewers disagreed on a previous pass"
        else:
            chosen = self._future_blind(rows, limit, seed)
            reason = "sampled on the breakout session's own reading only"

        return [self._item(row, strategy, reason) for row in chosen]

    def _random(
        self, rows: Sequence[tables.BreakoutEvent], limit: int, seed: int
    ) -> list[tables.BreakoutEvent]:
        import random

        rng = random.Random(seed)
        pool = list(rows)
        rng.shuffle(pool)
        return pool[:limit]

    def _stratified(
        self,
        rows: Sequence[tables.BreakoutEvent],
        limit: int,
        *,
        key: object,
    ) -> list[tables.BreakoutEvent]:
        assert callable(key)
        buckets: dict[object, list[tables.BreakoutEvent]] = {}
        for row in rows:
            buckets.setdefault(key(row), []).append(row)
        out: list[tables.BreakoutEvent] = []
        while len(out) < limit and any(buckets.values()):
            for bucket in list(buckets.values()):
                if bucket and len(out) < limit:
                    out.append(bucket.pop(0))
        return out

    def _disagreement(
        self, rows: Sequence[tables.BreakoutEvent], limit: int
    ) -> list[tables.BreakoutEvent]:
        def surprise(row: tables.BreakoutEvent) -> float:
            failed = row.state in ("failed_breakout", "expired")
            return row.breakout_quality if failed else 100.0 - row.breakout_quality

        return sorted(rows, key=surprise, reverse=True)[:limit]

    def _contested(
        self, rows: Sequence[tables.BreakoutEvent], limit: int
    ) -> list[tables.BreakoutEvent]:
        counts = {
            key: value
            for key, value in self.session.execute(
                select(
                    tables.BreakoutLabel.event_id,
                    func.count(func.distinct(tables.BreakoutLabel.label)),
                ).group_by(tables.BreakoutLabel.event_id)
            )
        }
        contested = [row for row in rows if counts.get(row.id, 0) > 1]
        return contested[:limit]

    def _future_blind(
        self, rows: Sequence[tables.BreakoutEvent], limit: int, seed: int
    ) -> list[tables.BreakoutEvent]:
        """Sample on the breakout session's own reading, stratified by quality.

        Deliberately *not* stratified by outcome state. Balancing the corpus
        across confirmed and failed events would use the outcome to decide which
        examples a human sees, which is the selection bias item 43 forbids
        wearing a respectable hat.
        """
        broke_out = [row for row in rows if row.first_qualifying_close_session is not None]
        pool = broke_out or list(rows)
        return self._stratified(
            self._random(pool, len(pool), seed), limit, key=lambda r: _band(r.breakout_quality)
        )

    def _item(
        self,
        row: tables.BreakoutEvent,
        strategy: BreakoutQueueStrategy,
        reason: str,
    ) -> BreakoutQueueItem:
        return BreakoutQueueItem(
            event_id=row.id,
            event_key=row.event_key,
            instrument_id=row.instrument_id,
            as_of_session=row.first_qualifying_close_session or row.opened_session,
            timeframe=row.timeframe,
            strategy=strategy,
            reason=reason,
            engine_state=row.state,
            breakout_quality=row.breakout_quality,
            confirmation_score=row.confirmation_score,
            evidence_coverage=row.evidence_coverage,
            hindsight_sessions=self.hindsight_sessions,
        )


def _band(quality: float) -> str:
    if quality >= 85:
        return "85+"
    if quality >= 70:
        return "70-85"
    if quality >= 55:
        return "55-70"
    return "<55"


__all__ = [
    "LABEL_HORIZONS",
    "BreakoutAgreement",
    "BreakoutLabel",
    "BreakoutLabelRequest",
    "BreakoutLabelService",
    "BreakoutQueueItem",
    "BreakoutQueueStrategy",
    "BreakoutReviewQueue",
]
