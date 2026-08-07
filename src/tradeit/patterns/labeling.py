"""Human labelling: the vocabulary, the service, and the review queue.

Phase 4 builds the infrastructure and labels nothing. No model is trained on
this, and none should be until humans have labelled real examples — the point of
building it now is that the *sampling* discipline has to exist before the corpus
does, or the corpus will be built out of whatever was easy to find.

**The five labels, and why ABSTAIN and INSUFFICIENT_EVIDENCE are both kept.**

``POSITIVE``
    This is the pattern.
``NEGATIVE``
    This is not the pattern.
``AMBIGUOUS``
    The reviewer judged it and the honest judgement is "arguably". A real
    category: chart structure is genuinely continuous, and forcing a binary
    manufactures agreement that does not exist.
``ABSTAIN``
    The reviewer declined to judge. About the *reviewer* — outside their
    competence, or they recognise the instrument and do not trust themselves to
    be neutral.
``INSUFFICIENT_EVIDENCE``
    The example cannot be judged. About the *example* — not enough history
    before the structure, a data gap through the middle, a security too illiquid
    for the geometry to mean anything.

The gate invites collapsing the last two, and they look redundant until you ask
what happens next. An ABSTAIN item is **reassigned** — the example is fine, this
reviewer is not the one to judge it. An INSUFFICIENT_EVIDENCE item is
**removed** — no reviewer can judge it and leaving it in the queue wastes every
future reviewer's time. Different downstream action, so different label. Merging
them would mean either re-reviewing dead examples forever or silently discarding
examples a second reviewer could have handled.

**Append-only.** A reviewer's opinion is evidence, and evidence that gets
overwritten when someone changes their mind is not evidence. Re-reviews are new
rows; the unique constraint is on (instrument, session, timeframe, family,
reviewer, revision), so a reviewer may revise and both versions survive.
Inter-reviewer agreement — the thing that turns opinions into a measurement —
is computable only if every opinion is still there.

**What is pinned at labelling time.** The detector's own prediction, its
version, the configuration digest, and the evidence coverage. Without them,
agreement between human and detector is computed against whatever the detector
does *today*, which is a different detector.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.clock import utcnow
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.errors import ConfigError, DataError
from tradeit.patterns.base import PatternInstance
from tradeit.storage import tables


class HumanLabel(StrEnum):
    """What a reviewer concluded."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    AMBIGUOUS = "ambiguous"
    ABSTAIN = "abstain"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

    @property
    def is_judgement(self) -> bool:
        """Whether the reviewer actually rated the structure.

        The three that are: agreement statistics are computed over these and
        would be meaningless if they included the two that are not.
        """
        return self in {HumanLabel.POSITIVE, HumanLabel.NEGATIVE, HumanLabel.AMBIGUOUS}

    @property
    def needs_reassignment(self) -> bool:
        """ABSTAIN: another reviewer should see this one."""
        return self is HumanLabel.ABSTAIN

    @property
    def retires_example(self) -> bool:
        """INSUFFICIENT_EVIDENCE: nobody can judge it; stop queuing it."""
        return self is HumanLabel.INSUFFICIENT_EVIDENCE


@dataclass(frozen=True, slots=True)
class LabelRequest:
    """One reviewer's opinion of one example, ready to store.

    A dataclass rather than keyword arguments because the field list is long
    enough that positional confusion is a real risk, and because validation
    belongs with the data rather than scattered across call sites.
    """

    instrument_id: int
    as_of_session: dt.date
    timeframe: Bartimeframe
    pattern_type: PatternType
    reviewer: str
    label: HumanLabel

    #: 0-100, on the detector's scale so the two are comparable. Only
    #: meaningful for POSITIVE and AMBIGUOUS; a rating attached to NEGATIVE is
    #: refused rather than quietly stored, because "this is not a cup, quality
    #: 70" has no reading.
    human_quality: float | None = None
    reviewer_confidence: float | None = None
    annotated_pivots: dict[str, object] | None = None
    annotated_boundaries: dict[str, object] | None = None
    comments: str = ""

    #: What the detector said, pinned so agreement survives a detector change.
    detector_name: str | None = None
    detector_version: int | None = None
    detector_quality: float | None = None
    detector_state: str | None = None
    detector_coverage: float | None = None
    config_digest: str = ""
    pattern_id: int | None = None

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise ConfigError("a label needs a reviewer; an anonymous opinion is not evidence")
        for name, value in (
            ("human_quality", self.human_quality),
            ("reviewer_confidence", self.reviewer_confidence),
        ):
            if value is not None and not 0.0 <= value <= 100.0:
                raise ConfigError(f"{name} must be within 0-100, got {value}")
        if self.human_quality is not None and self.label in {
            HumanLabel.NEGATIVE,
            HumanLabel.ABSTAIN,
            HumanLabel.INSUFFICIENT_EVIDENCE,
        }:
            raise ConfigError(
                f"a quality rating cannot accompany {self.label}: "
                '"this is not the pattern, quality 70" has no reading'
            )

    @classmethod
    def from_instance(
        cls,
        instance: PatternInstance,
        *,
        reviewer: str,
        label: HumanLabel,
        config_digest: str = "",
        **overrides: object,
    ) -> LabelRequest:
        """Build a request that pins the detector's prediction automatically."""
        base: dict[str, object] = {
            "instrument_id": instance.instrument_id,
            "as_of_session": instance.as_of_session,
            "timeframe": instance.timeframe,
            "pattern_type": instance.pattern_type,
            "reviewer": reviewer,
            "label": label,
            "detector_name": instance.detector_name,
            "detector_version": instance.detector_version,
            "detector_quality": instance.quality,
            "detector_state": str(instance.state),
            "detector_coverage": instance.evidence_coverage,
            "config_digest": config_digest or instance.config_digest,
        }
        base.update(overrides)
        return cls(**base)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Agreement:
    """How a set of reviewers compared with each other and with the detector."""

    examples: int
    judged: int
    unanimous: int
    #: Fraction of multi-reviewer examples where every judgement matched.
    #: Computed over judgements only -- an ABSTAIN is not a disagreement.
    unanimity: float
    #: Examples where reviewers split. The ones worth looking at.
    contested: tuple[tuple[int, dt.date, str], ...]

    def summary(self) -> dict[str, object]:
        return {
            "examples": self.examples,
            "judged": self.judged,
            "unanimous": self.unanimous,
            "unanimity": round(self.unanimity, 4),
            "contested": len(self.contested),
        }


class LabelService:
    """Create and read human labels. Never updates one."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- writes --------------------------------------------------------------

    def record(self, request: LabelRequest) -> tables.PatternLabel:
        """Store one opinion.

        A repeat from the same reviewer becomes a new revision rather than an
        overwrite. Someone changing their mind is itself a fact about how hard
        the example is, and the previous opinion is what makes that visible.
        """
        revision = self._next_revision(request)
        row = tables.PatternLabel(
            instrument_id=request.instrument_id,
            as_of_session=request.as_of_session,
            timeframe=str(request.timeframe),
            pattern_type=str(request.pattern_type),
            reviewer=request.reviewer,
            revision=revision,
            label=str(request.label),
            human_quality=request.human_quality,
            reviewer_confidence=request.reviewer_confidence,
            annotated_pivots=request.annotated_pivots,
            annotated_boundaries=request.annotated_boundaries,
            comments=request.comments or None,
            detector_name=request.detector_name,
            detector_version=request.detector_version,
            detector_quality=request.detector_quality,
            detector_state=request.detector_state,
            detector_coverage=request.detector_coverage,
            config_digest=request.config_digest or None,
            pattern_id=request.pattern_id,
            labelled_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _next_revision(self, request: LabelRequest) -> int:
        highest = self.session.scalar(
            select(func.max(tables.PatternLabel.revision)).where(
                tables.PatternLabel.instrument_id == request.instrument_id,
                tables.PatternLabel.as_of_session == request.as_of_session,
                tables.PatternLabel.timeframe == str(request.timeframe),
                tables.PatternLabel.pattern_type == str(request.pattern_type),
                tables.PatternLabel.reviewer == request.reviewer,
            )
        )
        return int(highest or 0) + 1

    # -- reads ---------------------------------------------------------------

    def for_example(
        self,
        instrument_id: int,
        as_of_session: dt.date,
        timeframe: Bartimeframe,
        pattern_type: PatternType,
    ) -> list[tables.PatternLabel]:
        return list(
            self.session.scalars(
                select(tables.PatternLabel)
                .where(
                    tables.PatternLabel.instrument_id == instrument_id,
                    tables.PatternLabel.as_of_session == as_of_session,
                    tables.PatternLabel.timeframe == str(timeframe),
                    tables.PatternLabel.pattern_type == str(pattern_type),
                )
                .order_by(tables.PatternLabel.reviewer, tables.PatternLabel.revision)
            )
        )

    def latest_per_reviewer(
        self,
        instrument_id: int,
        as_of_session: dt.date,
        timeframe: Bartimeframe,
        pattern_type: PatternType,
    ) -> dict[str, tables.PatternLabel]:
        """Each reviewer's most recent revision.

        The read most consumers want, and deliberately built on top of the full
        history rather than replacing it: the earlier revisions are still there.
        """
        latest: dict[str, tables.PatternLabel] = {}
        for row in self.for_example(instrument_id, as_of_session, timeframe, pattern_type):
            current = latest.get(row.reviewer)
            if current is None or row.revision > current.revision:
                latest[row.reviewer] = row
        return latest

    def agreement(self, pattern_type: PatternType | None = None) -> Agreement:
        """Inter-reviewer agreement across every multi-reviewer example."""
        query = select(tables.PatternLabel)
        if pattern_type is not None:
            query = query.where(tables.PatternLabel.pattern_type == str(pattern_type))
        rows = list(self.session.scalars(query))

        by_example: dict[tuple[int, dt.date, str, str], dict[str, tables.PatternLabel]] = {}
        for row in rows:
            key = (row.instrument_id, row.as_of_session, row.timeframe, row.pattern_type)
            bucket = by_example.setdefault(key, {})
            current = bucket.get(row.reviewer)
            if current is None or row.revision > current.revision:
                bucket[row.reviewer] = row

        judged = 0
        unanimous = 0
        contested: list[tuple[int, dt.date, str]] = []
        for key, reviewers in by_example.items():
            opinions = {
                row.label for row in reviewers.values() if HumanLabel(row.label).is_judgement
            }
            if len(reviewers) < 2 or not opinions:
                continue
            judged += 1
            if len(opinions) == 1:
                unanimous += 1
            else:
                contested.append((key[0], key[1], key[3]))

        return Agreement(
            examples=len(by_example),
            judged=judged,
            unanimous=unanimous,
            unanimity=unanimous / judged if judged else 0.0,
            contested=tuple(contested),
        )


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


class QueueStrategy(StrEnum):
    """How to choose what a reviewer sees next.

    The strategies exist because *how a labelling corpus is sampled determines
    what it can be used for*, and a queue that only ever shows high-scoring
    candidates produces a corpus that can measure precision and nothing else.
    """

    #: Uniformly at random across stored patterns. The only strategy that yields
    #: an unbiased estimate of anything, and therefore never omitted.
    RANDOM = "random"
    #: Stratified across quality bands, so the middle of the distribution is
    #: represented rather than only the extremes.
    SCORE_BAND = "score_band"
    #: Near the detector's own decision boundaries, where labels are worth most.
    BORDERLINE = "borderline"
    #: Reviewers have already split on these.
    DISAGREEMENT = "disagreement"
    #: More than one family fired on the same geometry.
    COMPETING = "competing"
    #: High-scoring instances, for precision measurement.
    POSITIVE_CONTROL = "positive_control"
    #: Instances the detector rejected or scored near zero, for recall.
    NEGATIVE_CONTROL = "negative_control"
    #: Instances whose score rests on little of its intended evidence.
    LOW_COVERAGE = "low_coverage"
    #: Structures that failed. Under-sampled by every other strategy, and the
    #: population a false-positive rate has to be computed from.
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One example offered for review, with why it was chosen."""

    pattern_id: int
    instrument_id: int
    as_of_session: dt.date
    timeframe: str
    pattern_type: str
    detector_quality: float
    evidence_coverage: float
    state: str
    strategy: QueueStrategy
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "instrument_id": self.instrument_id,
            "as_of_session": self.as_of_session.isoformat(),
            "timeframe": self.timeframe,
            "pattern_type": self.pattern_type,
            "detector_quality": round(self.detector_quality, 2),
            "evidence_coverage": round(self.evidence_coverage, 1),
            "state": self.state,
            "strategy": str(self.strategy),
            "reason": self.reason,
        }


@dataclass(slots=True)
class ReviewQueue:
    """Selects examples for human review.

    A query service rather than a stored queue, deliberately. A queue table
    would need reconciling with the patterns it points at every time a scan
    runs, and the only state it would hold that labels do not already hold is
    "assigned but not yet reviewed" — which is a UI concern and can be added
    when there is a UI to need it.
    """

    session: Session
    #: Quality bands used by SCORE_BAND. Even-width, so no band is privileged.
    bands: tuple[tuple[float, float], ...] = (
        (0.0, 20.0),
        (20.0, 40.0),
        (40.0, 60.0),
        (60.0, 80.0),
        (80.0, 100.0),
    )

    def select(
        self,
        strategy: QueueStrategy,
        *,
        limit: int = 50,
        pattern_type: PatternType | None = None,
        seed: int = 0,
    ) -> list[QueueItem]:
        """Choose up to ``limit`` examples under one strategy.

        ``seed`` makes RANDOM reproducible. A labelling run that cannot be
        reproduced cannot be audited for selection bias, which is the one thing
        a labelling run most needs to be auditable for.
        """
        rows = self._candidates(pattern_type)
        if strategy is QueueStrategy.RANDOM:
            chosen = self._random(rows, limit, seed)
            reason = "uniform random sample"
        elif strategy is QueueStrategy.SCORE_BAND:
            chosen = self._stratified(rows, limit, seed)
            reason = "stratified across quality bands"
        elif strategy is QueueStrategy.BORDERLINE:
            chosen = sorted(rows, key=lambda r: abs(r.peak_quality - 60.0))[:limit]
            reason = "near the middle of the score distribution"
        elif strategy is QueueStrategy.POSITIVE_CONTROL:
            chosen = sorted(rows, key=lambda r: -r.peak_quality)[:limit]
            reason = "highest-scoring instances"
        elif strategy is QueueStrategy.NEGATIVE_CONTROL:
            chosen = sorted(rows, key=lambda r: r.peak_quality)[:limit]
            reason = "lowest-scoring instances"
        elif strategy is QueueStrategy.LOW_COVERAGE:
            chosen = sorted(rows, key=lambda r: r.evidence_coverage)[:limit]
            reason = "score rests on little of the intended evidence"
        elif strategy is QueueStrategy.TERMINAL:
            chosen = [r for r in rows if r.terminal_at is not None][:limit]
            reason = "structure reached a terminal state"
        elif strategy is QueueStrategy.COMPETING:
            chosen = self._competing(rows, limit)
            reason = "more than one family fired on this geometry"
        elif strategy is QueueStrategy.DISAGREEMENT:
            chosen = self._contested(rows, limit)
            reason = "reviewers have already split on this example"
        else:  # pragma: no cover - the enum is exhaustive
            raise DataError(f"unknown queue strategy {strategy!r}")

        return [self._item(row, strategy, reason) for row in chosen]

    # -- selection helpers ---------------------------------------------------

    def _candidates(self, pattern_type: PatternType | None) -> list[tables.Pattern]:
        query = select(tables.Pattern)
        if pattern_type is not None:
            query = query.where(tables.Pattern.pattern_type == str(pattern_type))
        return list(self.session.scalars(query.order_by(tables.Pattern.id)))

    def _random(
        self, rows: Sequence[tables.Pattern], limit: int, seed: int
    ) -> list[tables.Pattern]:
        picker = random.Random(seed)
        pool = list(rows)
        picker.shuffle(pool)
        return pool[:limit]

    def _stratified(
        self, rows: Sequence[tables.Pattern], limit: int, seed: int
    ) -> list[tables.Pattern]:
        picker = random.Random(seed)
        per_band = max(1, limit // len(self.bands))
        chosen: list[tables.Pattern] = []
        for low, high in self.bands:
            band = [
                r
                for r in rows
                if low <= r.peak_quality < high or (high == 100.0 and r.peak_quality == 100.0)
            ]
            picker.shuffle(band)
            chosen.extend(band[:per_band])
        return chosen[:limit]

    def _competing(self, rows: Sequence[tables.Pattern], limit: int) -> list[tables.Pattern]:
        """Patterns sharing an instrument and session with a different family."""
        by_slot: dict[tuple[int, dt.date], list[tables.Pattern]] = {}
        for row in rows:
            by_slot.setdefault((row.instrument_id, row.last_observed_session), []).append(row)
        chosen: list[tables.Pattern] = []
        for group in by_slot.values():
            if len({r.pattern_type for r in group}) > 1:
                chosen.extend(group)
        return chosen[:limit]

    def _contested(self, rows: Sequence[tables.Pattern], limit: int) -> list[tables.Pattern]:
        service = LabelService(self.session)
        contested = {
            (instrument, session, family)
            for instrument, session, family in service.agreement().contested
        }
        return [
            r
            for r in rows
            if (r.instrument_id, r.last_observed_session, r.pattern_type) in contested
        ][:limit]

    def _item(self, row: tables.Pattern, strategy: QueueStrategy, reason: str) -> QueueItem:
        return QueueItem(
            pattern_id=row.id,
            instrument_id=row.instrument_id,
            as_of_session=row.last_observed_session,
            timeframe=row.timeframe,
            pattern_type=row.pattern_type,
            detector_quality=row.peak_quality,
            evidence_coverage=row.evidence_coverage,
            state=row.state,
            strategy=strategy,
            reason=reason,
        )


__all__ = [
    "Agreement",
    "HumanLabel",
    "LabelRequest",
    "LabelService",
    "QueueItem",
    "QueueStrategy",
    "ReviewQueue",
]
