"""The identity key has a computable maximum, and the column must exceed it.

The first real scan produced ``671a05b24082ef63e6f16232:2026-07-23`` — 35
characters — for a column declared ``VARCHAR(32)``. SQLite ignores VARCHAR
lengths, so no test that ran against SQLite could have caught it, and none did.

These tests pin the *derivation* rather than the number, so that a change to
how keys are minted fails here rather than in PostgreSQL four thousand sessions
into a scan.
"""

from __future__ import annotations

import datetime as dt

from tradeit.storage.tables import IDENTITY_KEY_LENGTH

#: The exact key that overflowed, from the real AAPL scan.
FAILING_KEY = "671a05b24082ef63e6f16232:2026-07-23"

#: What a pattern identity is: ``content_hash(...)[:24]``.
HASH_LENGTH = 24

#: What the tracker appends when a structure is re-detected after its identity
#: terminated, or when ``_fork`` mints a new one: ``":YYYY-MM-DD"``.
SUFFIX_LENGTH = len(":") + len("2026-07-23")


class TestTheDerivedMaximum:
    def test_the_column_holds_a_re_minted_key_with_room_to_spare(self) -> None:
        assert HASH_LENGTH + SUFFIX_LENGTH == 35
        assert IDENTITY_KEY_LENGTH >= HASH_LENGTH + SUFFIX_LENGTH

    def test_the_column_would_hold_one_further_qualifier(self) -> None:
        """A migration is a worse thing to need than unused bytes.

        PostgreSQL stores ``varchar(n)`` as the actual string plus a length
        header, so the declared maximum costs nothing until it is used.
        """
        assert IDENTITY_KEY_LENGTH >= HASH_LENGTH + 2 * SUFFIX_LENGTH

    def test_the_exact_failing_key_fits(self) -> None:
        assert len(FAILING_KEY) == 35
        assert len(FAILING_KEY) <= IDENTITY_KEY_LENGTH

    def test_it_did_not_fit_before(self) -> None:
        # Guards the premise. If this ever stops being true the whole story
        # above is wrong and should be rewritten rather than quietly kept.
        assert len(FAILING_KEY) > 32


class TestTheRealCodePathStaysInsideIt:
    def test_a_key_minted_by_the_real_identity_code_is_the_declared_hash_length(
        self,
    ) -> None:
        from tradeit.core.enums import Bartimeframe, PatternType
        from tradeit.reproducibility.versioning import content_hash

        # The same payload `PatternInstance.identity_key` hashes.
        key = content_hash(
            {
                "instrument_id": 13,
                "pattern_type": str(PatternType.BULL_FLAG),
                "timeframe": str(Bartimeframe.D1),
                "start_date": dt.date(2026, 7, 23).isoformat(),
            }
        )[:HASH_LENGTH]
        assert len(key) == HASH_LENGTH
        assert len(f"{key}:{dt.date(2026, 7, 23).isoformat()}") <= IDENTITY_KEY_LENGTH

    def test_every_suffix_the_tracker_can_append_is_an_iso_date(self) -> None:
        """Both suffixing sites use ``session.isoformat()``.

        An ISO date is exactly ten characters for any year from 1000 to 9999,
        so the bound holds for every session this system can represent.
        """
        for day in (dt.date(1000, 1, 1), dt.date(2026, 7, 23), dt.date(9999, 12, 31)):
            assert len(day.isoformat()) == 10

    def test_truncating_to_the_old_width_would_have_merged_separate_lives(self) -> None:
        """Why widening was the fix and truncating would not have been.

        24 characters of hash plus a colon leaves seven of the ten-character
        date inside 32, so a cut key keeps ``:YYYY-MM`` and loses the day. Two
        structures re-minted in the *same month* then land on the same string —
        silently merging two separate lives, which is the exact failure the
        suffix was added to prevent.
        """
        base = FAILING_KEY[:HASH_LENGTH]
        third, twenty_third = f"{base}:2026-07-03", f"{base}:2026-07-23"
        assert third != twenty_third
        assert third[:32] == twenty_third[:32] == f"{base}:2026-07"
        assert len(third) <= IDENTITY_KEY_LENGTH
        assert len(twenty_third) <= IDENTITY_KEY_LENGTH

    def test_a_re_minted_key_never_collides_with_the_base_it_forked_from(self) -> None:
        base = FAILING_KEY[:HASH_LENGTH]
        assert FAILING_KEY.startswith(base) and base != FAILING_KEY
        assert len(base) < len(FAILING_KEY) <= IDENTITY_KEY_LENGTH
