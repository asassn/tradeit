"""The reading guide has to live in the file, not beside it.

A markdown document helps a reader who was handed it. These tests assert that
someone who opens the database with nothing else finds the same guidance, and
that it cannot silently fall out of step with the views it describes.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tradeit.research01.views import README, VIEWS, create_readme, prepare_corpus


@pytest.fixture
def prepared(db_session: Session) -> Session:
    prepare_corpus(db_session, start=dt.date(2020, 1, 1), end=dt.date(2020, 12, 31))
    return db_session


def test_prepare_corpus_builds_both_halves(db_session: Session) -> None:
    sessions, entries = prepare_corpus(
        db_session, start=dt.date(2020, 1, 1), end=dt.date(2020, 12, 31)
    )
    assert sessions > 200  # a year of trading days
    assert entries == len(README)
    names = {
        n
        for (n,) in db_session.execute(
            text(
                "select name from sqlite_master where name in ('corpus_readme','trading_sessions')"
            )
        ).all()
    }
    assert names == {"corpus_readme", "trading_sessions"}


def test_the_guidance_is_readable_with_one_query(prepared: Session) -> None:
    """What a tool with no other context would run."""
    rows = prepared.execute(
        text("select topic, applies_to, guidance from corpus_readme order by ordinal")
    ).all()
    assert len(rows) == len(README)
    assert all(topic and applies_to and guidance for topic, applies_to, guidance in rows)


def test_the_first_entry_orients_a_reader_who_stops_there(prepared: Session) -> None:
    topic, _, guidance = prepared.execute(
        text("select topic, applies_to, guidance from corpus_readme order by ordinal limit 1")
    ).one()
    assert topic == "start here"
    assert "v_" in guidance


def test_the_survivorship_warning_is_present_and_early(prepared: Session) -> None:
    """The standing rule that no result here is evidence of profitability."""
    ordinal, guidance = prepared.execute(
        text("select ordinal, guidance from corpus_readme where topic like '%profitability%'")
    ).one()
    assert ordinal <= 2
    assert "SURVIVOR_BIASED" in guidance


def test_every_view_the_module_builds_is_mentioned_somewhere(prepared: Session) -> None:
    """A view a reader is never told to prefer does not help them."""
    guidance = " ".join(
        g for (g,) in prepared.execute(text("select guidance from corpus_readme")).all()
    )
    for name in VIEWS:
        assert name in guidance, f"{name} is built but never mentioned in corpus_readme"


def test_rebuilding_replaces_rather_than_appends(prepared: Session) -> None:
    """Generated output: a stale row surviving is the failure it exists to stop."""
    create_readme(prepared)
    create_readme(prepared)
    (count,) = prepared.execute(text("select count(*) from corpus_readme")).one()
    assert count == len(README)


def test_topics_are_unique(prepared: Session) -> None:
    topics = [t for (t,) in prepared.execute(text("select topic from corpus_readme")).all()]
    assert len(topics) == len(set(topics))


def test_each_entry_names_a_real_table_or_view(prepared: Session) -> None:
    """applies_to must be something a reader can actually go and look at."""
    objects = {
        n
        for (n,) in prepared.execute(
            text("select name from sqlite_master where type in ('table','view')")
        ).all()
    }
    objects.add("sqlite_master")
    for (applies_to,) in prepared.execute(text("select applies_to from corpus_readme")).all():
        assert applies_to in objects, f"corpus_readme points at {applies_to}, which is not here"
