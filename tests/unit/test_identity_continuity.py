"""When is a detection the same structure, and when is it a new one?

The diagnostic scan produced 37,483 re-minted identities from 39,218 — one
``double_bottom`` was minted 209 times, on consecutive trading days, for a
structure with a single fixed start date. The cause was not a lifecycle rule
being too eager. It was that the tracker could not find its own live identity:

* a detector emits the same content hash every session;
* after one re-mint the tracker holds that structure under a *suffixed* key;
* the lookup went straight into ``_open`` with the detector's key, missed, and
  fell through to the re-mint branch — every session, forever.

So these pin the seven semantics questions the fix had to answer, and the
answers are deliberately narrow: one index, one rule about stillbirth, and not
a single threshold moved.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.patterns.base import Boundary, PatternGeometry, PatternInstance, PatternState
from tradeit.patterns.tracking import PatternTracker

CALENDAR = get_calendar()
SESSIONS = CALENDAR.sessions_between(dt.date(2024, 1, 2), dt.date(2024, 12, 31))
START = dt.date(2023, 11, 1)


def instance(
    session: dt.date,
    state: PatternState = PatternState.MATURE,
    *,
    start: dt.date = START,
    resistance: float = 12.0,
    support: float = 10.0,
    invalidation: float = 9.5,
    quality: float = 70.0,
) -> PatternInstance:
    return PatternInstance(
        instrument_id=1,
        pattern_type=PatternType.DOUBLE_BOTTOM,
        timeframe=Bartimeframe.D1,
        state=state,
        geometry=PatternGeometry(
            start_date=start,
            end_date=session,
            resistance=Boundary(
                kind="resistance", method="swing", level=resistance, anchor_date=session
            ),
            support=Boundary(kind="support", method="swing", level=support, anchor_date=start),
        ),
        as_of_session=session,
        knowledge_time=dt.datetime.combine(session, dt.time(21), tzinfo=dt.UTC),
        quality=quality,
        invalidation_price=invalidation,
        detector_name="double_bottom",
    )


def identities(tracker: PatternTracker) -> list[str]:
    return [t.identity_key for t in (*tracker.open_patterns(), *tracker.closed_patterns())]


class TestOneStructureKeepsOneIdentity:
    """Question 1: when is a detection the same economic structure?

    Answer: when the tracker is still holding a live identity for that
    detector key, and the state change is a legal edge. Nothing else.
    """

    def test_a_continuously_detected_structure_is_never_re_minted(self) -> None:
        tracker = PatternTracker()
        for session in SESSIONS[:40]:
            tracker.observe([instance(session)], session, closes={1: 11.0})
        assert len(identities(tracker)) == 1
        (tracked,) = tracker.open_patterns()
        assert len(tracked.history) == 40

    def test_an_evolving_structure_keeps_its_identity(self) -> None:
        """Question 3: which geometric changes are allowed?

        The end date extends as the structure keeps forming, and the boundaries
        and quality are re-measured. The *start* cannot move — a different start
        is a different content hash, so it is a different structure by
        construction rather than by policy.
        """
        tracker = PatternTracker()
        for step, session in enumerate(SESSIONS[:30]):
            tracker.observe(
                [instance(session, resistance=12.0 + step * 0.05, quality=60.0 + step)],
                session,
                closes={1: 11.0},
            )
        assert len(identities(tracker)) == 1
        (tracked,) = tracker.open_patterns()
        assert tracked.current.geometry.end_date == SESSIONS[29]
        assert tracked.current.geometry.start_date == START

    def test_a_structure_that_survives_a_re_mint_is_found_again_afterwards(self) -> None:
        """The exact 209-times pathology, in miniature.

        Terminate a life, let the structure be re-detected — which mints a
        second identity under a suffixed key — then keep detecting it. Before
        the index, every one of those later sessions minted another identity.
        """
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        # Break support: the first life ends INVALIDATED.
        tracker.observe([], SESSIONS[1], closes={1: 8.0})
        assert tracker.open_patterns() == []

        # The structure is detected again, healthy: a genuine second life.
        for session in SESSIONS[2:25]:
            tracker.observe([instance(session)], session, closes={1: 11.0})

        keys = identities(tracker)
        assert len(keys) == 2, f"expected one terminated life and one live one, got {keys}"
        live = tracker.open_patterns()
        assert len(live) == 1
        assert len(live[0].history) == 23


class TestATerminatedStructureStaysTerminated:
    """Question 4: what permanently ends an identity?"""

    @pytest.mark.parametrize(
        ("close", "expected"),
        [(8.0, PatternState.INVALIDATED), (11.0, PatternState.MATURE)],
    )
    def test_a_close_below_invalidation_ends_it(self, close: float, expected: PatternState) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        tracker.observe([], SESSIONS[1], closes={1: close})
        states = [t.state for t in (*tracker.open_patterns(), *tracker.closed_patterns())]
        assert states == [expected]

    def test_a_terminated_identity_is_never_reopened(self) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        tracker.observe([], SESSIONS[1], closes={1: 8.0})
        (dead,) = tracker.closed_patterns()
        assert dead.state is PatternState.INVALIDATED

        tracker.observe([instance(SESSIONS[2])], SESSIONS[2], closes={1: 11.0})
        still_dead = [t for t in tracker.closed_patterns() if t.identity_key == dead.identity_key]
        assert len(still_dead) == 1
        assert still_dead[0].state is PatternState.INVALIDATED
        assert still_dead[0].last_seen == dead.last_seen
        assert tracker.open_patterns()[0].identity_key != dead.identity_key

    def test_a_life_cannot_begin_already_over(self) -> None:
        """The second rule, and the 10,024 identities it removes.

        A detection arriving in a terminal state, for a structure whose previous
        life has already terminated, records nothing the predecessor does not:
        terminal states are absorbing, so the identity would hold one
        observation, never transition, and retire on the same session. The
        outcome is already on file under the identity that lived it.
        """
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        tracker.observe([], SESSIONS[1], closes={1: 8.0})
        before = len(identities(tracker))

        for session in SESSIONS[2:20]:
            tracker.observe([instance(session, PatternState.INVALIDATED)], session, closes={1: 8.0})
        assert len(identities(tracker)) == before, (
            "a dead structure that stays visible must not mint one identity per session"
        )

    def test_the_very_first_sighting_of_a_dead_structure_is_still_recorded(self) -> None:
        """The rule is about *re*-minting, not about refusing to record."""
        tracker = PatternTracker()
        tracker.observe(
            [instance(SESSIONS[0], PatternState.INVALIDATED)], SESSIONS[0], closes={1: 8.0}
        )
        assert len(identities(tracker)) == 1


class TestAbsenceAndResumption:
    """Question 2: how long may it vanish and still be the same structure?

    Unchanged, and deliberately: ``grace_sessions = 3`` and
    ``resolution_carry_sessions = 10`` are exactly where they were. The fix
    operates on the *matching* path for structures that are being detected, so
    it can never extend a pattern's life past the existing rules.
    """

    def test_a_short_absence_does_not_re_mint(self) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        for session in SESSIONS[1:4]:  # three sessions unseen, inside the grace
            tracker.observe([], session, closes={1: 11.0})
        tracker.observe([instance(SESSIONS[4])], SESSIONS[4], closes={1: 11.0})

        assert len(identities(tracker)) == 1
        (tracked,) = tracker.open_patterns()
        assert tracked.first_seen == SESSIONS[0]

    def test_an_absence_past_the_grace_period_ends_the_identity(self) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        for session in SESSIONS[1:8]:
            tracker.observe([], session, closes={1: 11.0})
        assert tracker.open_patterns() == []
        (dead,) = tracker.closed_patterns()
        assert dead.state is PatternState.EXPIRED

        tracker.observe([instance(SESSIONS[8])], SESSIONS[8], closes={1: 11.0})
        assert len(identities(tracker)) == 2

    def test_the_grace_period_is_the_documented_one(self) -> None:
        # Pins the number so a change to it is a deliberate edit rather than a
        # side effect of this work.
        assert PatternTracker().grace_sessions == 3
        assert PatternTracker().resolution_carry_sessions == 10


class TestSeparateSetupsAreNotMerged:
    """Question 5: how are genuinely separate structures kept apart?"""

    def test_a_different_structural_start_is_a_different_identity(self) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        tracker.observe(
            [instance(SESSIONS[1], start=dt.date(2024, 1, 3))], SESSIONS[1], closes={1: 11.0}
        )
        assert len(identities(tracker)) == 2

    def test_a_later_setup_after_a_completed_one_gets_its_own_identity(self) -> None:
        tracker = PatternTracker()
        tracker.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        tracker.observe([], SESSIONS[1], closes={1: 8.0})  # invalidated
        for session in SESSIONS[2:6]:
            tracker.observe([instance(session)], session, closes={1: 11.0})

        keys = identities(tracker)
        assert len(keys) == 2
        first, second = tracker.closed_patterns()[0], tracker.open_patterns()[0]
        assert first.identity_key != second.identity_key
        assert second.first_seen == SESSIONS[2]
        assert first.state is PatternState.INVALIDATED

    def test_the_index_only_ever_names_a_life_the_tracker_holds_open(self) -> None:
        """The property that makes merging impossible.

        ``_live`` is written when an identity is created and removed when it
        terminates, so it can only resolve to a life the tracker is holding
        open. Two structures cannot be merged through it, because a second one
        can only be minted once the first is no longer live.
        """
        tracker = PatternTracker()
        for session in SESSIONS[:12]:
            tracker.observe([instance(session)], session, closes={1: 11.0})
            open_keys = {t.identity_key for t in tracker.open_patterns()}
            assert set(tracker._live.values()) <= open_keys
        tracker.observe([], SESSIONS[12], closes={1: 8.0})
        assert tracker._live == {}


class TestNoIllegalTransitionIsIntroduced:
    def test_an_illegal_edge_still_forks_rather_than_being_forced(self) -> None:
        """Question 1's other half, and the 550 the fix deliberately leaves.

        BROKEN_OUT_UNCONFIRMED may only go to itself, EXPIRED or INVALIDATED.
        A detection proposing MATURE is a different structure sharing a start
        date, and it still gets its own identity — the index resolves *which*
        identity to advance, never whether the edge is legal.
        """
        tracker = PatternTracker()
        tracker.observe(
            [instance(SESSIONS[0], PatternState.BROKEN_OUT_UNCONFIRMED)],
            SESSIONS[0],
            closes={1: 13.0},
        )
        tracker.observe([instance(SESSIONS[1], PatternState.MATURE)], SESSIONS[1], closes={1: 11.0})

        keys = identities(tracker)
        assert len(keys) == 2
        (retired,) = tracker.closed_patterns()
        assert retired.state.is_terminal
        (live,) = tracker.open_patterns()
        assert live.state is PatternState.MATURE

    def test_every_recorded_transition_is_a_legal_edge(self) -> None:
        """`StateTransition.__post_init__` checks each edge, so a walk that
        completes at all has produced only legal ones. This exercises a long
        mixed walk to make that a claim about this code path."""
        tracker = PatternTracker()
        states = [
            PatternState.FORMING,
            PatternState.MATURE,
            PatternState.NEAR_BREAKOUT,
            PatternState.MATURE,
            PatternState.BROKEN_OUT_UNCONFIRMED,
        ]
        for step, session in enumerate(SESSIONS[:60]):
            tracker.observe(
                [instance(session, states[step % len(states)])], session, closes={1: 11.0}
            )
        seen = [
            (t.from_state, t.to_state)
            for tracked in (*tracker.open_patterns(), *tracker.closed_patterns())
            for t in tracked.history
        ]
        assert seen  # construction would have raised on an illegal edge


class TestThe209RemintPathology:
    """The shape that produced 209 identities for one structure.

    A life terminates, the structure is re-detected and keeps being detected.
    Before the index, each of those later sessions minted another identity
    because the detector's key no longer resolved to anything open. This
    reproduces the shape and bounds the result.
    """

    @staticmethod
    def walk(sessions: list[dt.date]) -> PatternTracker:
        tracker = PatternTracker()
        for step, session in enumerate(sessions):
            # A death every 40 sessions, then continuous re-detection. Each
            # death is a genuine second life; each run between them is one.
            if step and step % 40 == 0:
                tracker.observe([], session, closes={1: 8.0})
                continue
            tracker.observe([instance(session)], session, closes={1: 11.0})
        return tracker

    def test_one_identity_per_life_not_one_per_session(self) -> None:
        sessions = SESSIONS[:200]
        tracker = self.walk(sessions)
        keys = identities(tracker)
        deaths = sum(1 for step in range(len(sessions)) if step and step % 40 == 0)

        # One identity per completed life, plus the one still running.
        assert len(keys) == deaths + 1, f"{len(keys)} identities for {deaths + 1} lives: {keys}"
        assert len(keys) <= 6
        # And the pathology's own metric: identities sharing one base hash.
        bases = {key.split(":")[0] for key in keys}
        assert len(bases) == 1, "the corpus is one structure, so one base hash"
        assert len(keys) / len(bases) <= 6, (
            "identities per base is the 209-times metric; it must stay near the "
            "number of real lives"
        )

    def test_each_life_accumulates_its_own_sessions(self) -> None:
        tracker = self.walk(SESSIONS[:200])
        counts = sorted(
            len(t.history) for t in (*tracker.open_patterns(), *tracker.closed_patterns())
        )
        # Before the fix every identity held one or two observations; a life
        # spanning forty sessions must now hold them.
        assert max(counts) >= 30, counts
        assert sum(counts) >= 190, "no session may be silently dropped"


class TestAStructuralBreakForcesANewIdentity:
    """Question 6, at the level this module owns.

    The scanner builds a fresh `PatternScanner`, `PatternTracker` and
    `BreakoutMonitor` per analytical episode, so nothing crosses a structural
    break by construction. `TestAStructuralBreakResetsEverything` in
    `tests/integration/test_snapshot_scan.py` proves that over a real 140-session
    gap; this guards the property the construction relies on — that two trackers
    share no identity state at all, including the new index.
    """

    def test_two_trackers_share_no_identity_state(self) -> None:
        first = PatternTracker()
        for session in SESSIONS[:5]:
            first.observe([instance(session)], session, closes={1: 11.0})
        assert first._live and first._open

        second = PatternTracker()
        assert second._live == {}
        assert second._open == {}
        assert second._retired == set()
        assert second.closed_patterns() == []

    def test_the_same_structure_in_a_second_episode_is_a_separate_identity(self) -> None:
        """What a per-episode tracker means for one instrument's history."""
        before = PatternTracker()
        before.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})
        after = PatternTracker()
        after.observe([instance(SESSIONS[0])], SESSIONS[0], closes={1: 11.0})

        # The keys are equal — identity is a content hash and the fixture feeds
        # the same structure — but they are *different objects with separate
        # histories*, and the scanner persists them under different episodes.
        assert before.open_patterns()[0] is not after.open_patterns()[0]
        assert before._live is not after._live
