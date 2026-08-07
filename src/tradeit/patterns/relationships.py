"""How two pattern instances relate.

**Six kinds, and the case for not having fewer.** The gate suggests six and
invites a simplification argument if fewer suffice. Six is right, but only just,
and the reasoning is worth recording because the temptation to add a seventh
will recur:

``CONTAINS`` / ``NESTED_IN``
    Inverses of one another, stored as a single directed edge each way at the
    caller's choice. Containment is the relationship that makes multi-timeframe
    analysis mean anything — a weekly VCP containing a daily bull flag is the
    canonical case, and it is *not* symmetric, so one edge type cannot express
    it.
``OVERLAPS``
    Two structures sharing a span where neither contains the other. Distinct
    from containment because the reading differs: partial overlap usually means
    two genuine structures that share bars, where containment usually means one
    structure seen at two scales.
``RELATED_TO``
    Alternative readings of *the same* geometry. A window that is a
    TIGHT_CONSOLIDATION at 78 and a BULL_FLAG at 74 is one piece of chart with
    two names, and this is the edge that says so without forcing a choice.
``SUPERSEDED_BY``
    One structure replaced another under a new identity. Directional and
    terminal-adjacent: the superseded pattern's history stops here.
``DERIVED_FROM``
    The child-of-a-composite case. A base-on-base instance is *derived from* the
    two bases it describes; they are not nested inside it in the geometric sense
    and they are not alternative readings of it. Without this edge the
    composite's provenance is unrecorded, and a reader cannot get from the
    composite back to the parts that justified it.

**Why not fewer.** Collapsing ``CONTAINS``/``NESTED_IN`` into ``OVERLAPS`` loses
direction, which is the only thing that distinguishes a weekly structure holding
a daily one from two structures sharing bars. Collapsing ``RELATED_TO`` into
``OVERLAPS`` loses the distinction between "two structures" and "two names for
one structure", which is precisely the distinction the competing-pattern work
exists to preserve. ``DERIVED_FROM`` could in principle be ``CONTAINS``, but a
composite that contains its parts geometrically *and* is defined by them is
doing two different things, and one edge cannot say which.

**Why not more.** Every additional edge type is an argument waiting to happen at
the point of use, and an edge nobody can classify confidently is an edge nobody
should trust. There is no ``PRECEDES``, no ``CONFIRMS``, no ``INVALIDATES`` —
the first is derivable from dates, and the other two are judgements this phase
does not make.

**Relationships are historically reproducible.** Every edge is derived from what
was visible at one ``as_of`` boundary, and carries that boundary. A relationship
discovered on Friday is not backdated to Monday: it did not exist on Monday, and
recording it as though it did would be the same retroactive rewriting the
observation history exists to prevent. :func:`derive_relationships` is a pure
function of the instances it is given, so replaying a session reproduces exactly
the edges that session produced.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.core.enums import Bartimeframe
from tradeit.errors import ConfigError
from tradeit.patterns.base import PatternInstance

#: How much of a structure's span must sit inside another's to count as
#: containment rather than overlap. High, because a partial overlap is a
#: genuinely different statement and blurring the two makes both useless.
CONTAINMENT_THRESHOLD = 0.80

#: Minimum shared fraction for OVERLAPS. Below this the two structures merely
#: happen to be on the same instrument, which is not a relationship.
OVERLAP_THRESHOLD = 0.25

#: How closely two spans must coincide to be called two readings of one
#: geometry rather than two structures. Both directions must exceed it.
SAME_GEOMETRY_THRESHOLD = 0.85


class RelationshipKind(StrEnum):
    """The six edges. Stored as strings, so a row is readable without code."""

    CONTAINS = "contains"
    NESTED_IN = "nested_in"
    OVERLAPS = "overlaps"
    RELATED_TO = "related_to"
    SUPERSEDED_BY = "superseded_by"
    DERIVED_FROM = "derived_from"

    @property
    def inverse(self) -> RelationshipKind:
        """The edge pointing the other way.

        ``OVERLAPS`` and ``RELATED_TO`` are their own inverses; the rest pair
        up. Exposed so a caller storing one direction can assert the other is
        implied rather than storing both and risking them disagreeing.
        """
        return _INVERSES[self]

    @property
    def is_symmetric(self) -> bool:
        return self.inverse is self


_INVERSES: dict[RelationshipKind, RelationshipKind] = {
    RelationshipKind.CONTAINS: RelationshipKind.NESTED_IN,
    RelationshipKind.NESTED_IN: RelationshipKind.CONTAINS,
    RelationshipKind.OVERLAPS: RelationshipKind.OVERLAPS,
    RelationshipKind.RELATED_TO: RelationshipKind.RELATED_TO,
    RelationshipKind.SUPERSEDED_BY: RelationshipKind.SUPERSEDED_BY,
    RelationshipKind.DERIVED_FROM: RelationshipKind.DERIVED_FROM,
}

#: Timeframe ordering, coarsest first. Used to decide which of two structures is
#: the container when both cover the same calendar span at different scales.
_TIMEFRAME_RANK: dict[Bartimeframe, int] = {
    Bartimeframe.MN1: 0,
    Bartimeframe.W1: 1,
    Bartimeframe.D1: 2,
    Bartimeframe.H4: 3,
    Bartimeframe.H1: 4,
    Bartimeframe.M30: 5,
    Bartimeframe.M15: 6,
    Bartimeframe.M5: 7,
    Bartimeframe.M1: 8,
}


@dataclass(frozen=True, slots=True)
class PatternRelation:
    """One derived edge, with the boundary it was derived under.

    ``as_of`` is not decoration. An edge is a claim about what was visible at a
    moment, and a stored edge without one cannot be distinguished from an edge
    someone backdated.
    """

    from_key: str
    to_key: str
    kind: RelationshipKind
    as_of: dt.date
    #: Why the edge exists, in one line, for a human reading the history.
    reason: str = ""
    #: Shared fraction of the smaller structure's span, where meaningful.
    overlap: float = 0.0

    def inverted(self) -> PatternRelation:
        return PatternRelation(
            from_key=self.to_key,
            to_key=self.from_key,
            kind=self.kind.inverse,
            as_of=self.as_of,
            reason=self.reason,
            overlap=self.overlap,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "from_key": self.from_key,
            "to_key": self.to_key,
            "kind": str(self.kind),
            "as_of": self.as_of.isoformat(),
            "reason": self.reason,
            "overlap": round(self.overlap, 4),
        }


def span_overlap(a: PatternInstance, b: PatternInstance) -> tuple[float, float]:
    """Fraction of each structure's calendar span shared with the other.

    Returned as a pair rather than a single number because the asymmetry *is*
    the information: (1.0, 0.2) is a small structure wholly inside a large one,
    and (0.6, 0.6) is two structures of similar size sharing half their bars.
    Those are different relationships and one number cannot distinguish them.
    """
    start = max(a.geometry.start_date, b.geometry.start_date)
    end = min(a.geometry.end_date, b.geometry.end_date)
    if end < start:
        return 0.0, 0.0

    shared = (end - start).days + 1
    a_span = (a.geometry.end_date - a.geometry.start_date).days + 1
    b_span = (b.geometry.end_date - b.geometry.start_date).days + 1
    return shared / a_span, shared / b_span


def _is_coarser(a: PatternInstance, b: PatternInstance) -> bool:
    return _TIMEFRAME_RANK.get(a.timeframe, 99) < _TIMEFRAME_RANK.get(b.timeframe, 99)


def classify(a: PatternInstance, b: PatternInstance) -> RelationshipKind | None:
    """The edge between two instances, or ``None`` if they are unrelated.

    The order of tests matters and encodes the taxonomy's priorities:

    1. **Different timeframes with real overlap → containment**, coarser
       containing finer. This is checked first because it is the case the
       multi-timeframe work exists for, and because two structures at different
       scales are never "alternative readings" of each other — they are
       statements at different resolutions.
    2. **Near-identical spans on one timeframe → RELATED_TO.** Two names for one
       piece of chart.
    3. **One span largely inside the other → containment.**
    4. **Partial overlap → OVERLAPS.**

    ``SUPERSEDED_BY`` and ``DERIVED_FROM`` are never inferred from geometry.
    Supersession is a lifecycle event the tracker decides, and derivation is a
    fact the composite detector knows and nothing else can reconstruct.
    """
    if a.instrument_id != b.instrument_id:
        return None

    a_shared, b_shared = span_overlap(a, b)
    if a_shared <= 0.0 and b_shared <= 0.0:
        return None

    if a.timeframe is not b.timeframe:
        if max(a_shared, b_shared) < OVERLAP_THRESHOLD:
            return None
        return RelationshipKind.CONTAINS if _is_coarser(a, b) else RelationshipKind.NESTED_IN

    if a_shared >= SAME_GEOMETRY_THRESHOLD and b_shared >= SAME_GEOMETRY_THRESHOLD:
        # Same geometry, two names. Only meaningful across families: the same
        # detector producing two near-identical readings is a duplicate, and
        # that is the tracker's problem rather than a relationship.
        return RelationshipKind.RELATED_TO if a.pattern_type is not b.pattern_type else None

    if b_shared >= CONTAINMENT_THRESHOLD:
        return RelationshipKind.CONTAINS
    if a_shared >= CONTAINMENT_THRESHOLD:
        return RelationshipKind.NESTED_IN
    if max(a_shared, b_shared) >= OVERLAP_THRESHOLD:
        return RelationshipKind.OVERLAPS
    return None


def derive_relationships(
    instances: Sequence[PatternInstance], as_of: dt.date
) -> list[PatternRelation]:
    """Every edge implied by one session's instances.

    A pure function of its input, which is what makes the relationship history
    reproducible: replaying a session's detections reproduces its edges exactly,
    and no edge can depend on a bar that session could not see.

    Emits one direction per pair. The inverse is implied and derivable via
    :meth:`PatternRelation.inverted`; storing both would create two rows that
    can disagree after an edit.
    """
    for instance in instances:
        if instance.as_of_session > as_of:
            raise ConfigError(
                f"instance is as of {instance.as_of_session}, past the {as_of} boundary "
                "the relationships are being derived under; an edge must not depend on "
                "a bar the session could not see"
            )

    relations: list[PatternRelation] = []
    for i, first in enumerate(instances):
        for second in instances[i + 1 :]:
            kind = classify(first, second)
            if kind is None:
                continue
            a_shared, b_shared = span_overlap(first, second)
            relations.append(
                PatternRelation(
                    from_key=first.identity_key,
                    to_key=second.identity_key,
                    kind=kind,
                    as_of=as_of,
                    reason=_describe(first, second, kind),
                    overlap=min(a_shared, b_shared),
                )
            )
    return relations


def _describe(a: PatternInstance, b: PatternInstance, kind: RelationshipKind) -> str:
    a_name = f"{a.pattern_type} on {a.timeframe}"
    b_name = f"{b.pattern_type} on {b.timeframe}"
    if kind is RelationshipKind.CONTAINS:
        return f"{a_name} spans {b_name}"
    if kind is RelationshipKind.NESTED_IN:
        return f"{a_name} sits inside {b_name}"
    if kind is RelationshipKind.RELATED_TO:
        return f"{a_name} and {b_name} are readings of the same geometry"
    return f"{a_name} and {b_name} share part of their span"


def derived_from(
    composite: PatternInstance, parts: Sequence[PatternInstance], as_of: dt.date
) -> list[PatternRelation]:
    """Edges from a composite structure to the parts that define it.

    Not inferable from geometry, which is why it is a separate entry point: only
    the detector that built the composite knows which structures justified it.
    Base-on-base is the case this exists for.
    """
    return [
        PatternRelation(
            from_key=composite.identity_key,
            to_key=part.identity_key,
            kind=RelationshipKind.DERIVED_FROM,
            as_of=as_of,
            reason=f"{composite.pattern_type} is defined by this {part.pattern_type}",
            overlap=min(*span_overlap(composite, part)),
        )
        for part in parts
    ]


def superseded_by(
    old_key: str, new_key: str, as_of: dt.date, *, reason: str = ""
) -> PatternRelation:
    """One structure replaced by another under a new identity."""
    if old_key == new_key:
        raise ConfigError("a pattern cannot supersede itself")
    return PatternRelation(
        from_key=old_key,
        to_key=new_key,
        kind=RelationshipKind.SUPERSEDED_BY,
        as_of=as_of,
        reason=reason or "absorbed by a larger structure",
    )


#: Values the persistence layer accepts. Kept here so the vocabulary has one
#: home rather than a string literal in a repository method.
STORED_RELATIONSHIPS: frozenset[str] = frozenset(str(k) for k in RelationshipKind)


__all__ = [
    "CONTAINMENT_THRESHOLD",
    "OVERLAP_THRESHOLD",
    "SAME_GEOMETRY_THRESHOLD",
    "STORED_RELATIONSHIPS",
    "PatternRelation",
    "RelationshipKind",
    "classify",
    "derive_relationships",
    "derived_from",
    "span_overlap",
    "superseded_by",
]
