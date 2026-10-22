"""Ticker resolution is per date, and the tests say what goes wrong without it.

Each case here corresponds to something the previous resolver got wrong or
could not express. `scripts/backtest_research01._universe` asked
`where alias_value in (...) order by valid_from` and kept the first row per
ticker: no date, so the answer was always the *earliest* holder, and a ticker
with no current holder still came back with an id.
"""

from __future__ import annotations

import datetime as dt

from tradeit.forward.identity import TickerAlias, UnresolvedReason, resolve_as_of

# A reused ticker, in the shape the corpus actually holds `AUTO`: one company
# to 2013, another from 1997 to 2022. Note they overlap -- the corpus contains
# overlapping aliases, which is why ambiguity needs a name of its own.
EARLY = TickerAlias("AUTO", 1914, dt.date(1995, 6, 16), dt.date(2013, 5, 2))
LATE = TickerAlias("AUTO", 4535, dt.date(2013, 5, 2), dt.date(2022, 9, 13))
OPEN_ENDED = TickerAlias("AAPL", 1, dt.date(1990, 1, 1), None)


def test_a_single_holder_resolves() -> None:
    resolved, unresolved = resolve_as_of([OPEN_ENDED], ["AAPL"], as_of=dt.date(2026, 10, 22))
    assert resolved == {"AAPL": 1}
    assert unresolved == {}


def test_a_reused_ticker_resolves_to_the_holder_on_that_date() -> None:
    """**The case the old resolver always got wrong.**

    It returned the earliest holder whatever date you cared about, so a 2020
    question about `AUTO` was answered with a company that stopped holding the
    ticker in 2013. Asked on two different dates, the same input must give two
    different answers -- that is what "per date" means, and a resolver that
    passes the first assertion while failing the second is the old one.
    """
    aliases = [EARLY, LATE]
    early, _ = resolve_as_of(aliases, ["AUTO"], as_of=dt.date(2000, 1, 3))
    late, _ = resolve_as_of(aliases, ["AUTO"], as_of=dt.date(2020, 1, 2))
    assert early == {"AUTO": 1914}
    assert late == {"AUTO": 4535}


def test_valid_to_is_exclusive() -> None:
    """§0.2: the last day held is ``valid_to - 1``.

    On `valid_to` itself the security no longer holds the ticker. An inclusive
    reading hands back the wrong company for exactly one day per handover,
    which is the kind of error that never looks like an error.
    """
    held, _ = resolve_as_of([EARLY], ["AUTO"], as_of=dt.date(2013, 5, 1))
    assert held == {"AUTO": 1914}
    gone, reasons = resolve_as_of([EARLY], ["AUTO"], as_of=dt.date(2013, 5, 2))
    assert gone == {}
    assert reasons == {"AUTO": UnresolvedReason.NO_HOLDER_AT_DATE}


def test_a_ticker_nobody_holds_is_unresolved_not_defaulted() -> None:
    """`ABAX`'s shape: a ticker that ended in 2018, asked about in 2026.

    The old resolver returned `security_id` 3377 and the capture asked Tiingo
    about it daily, recording `empty` each time -- two false delisting claims
    per day, dated 2026, in the record built so survivorship cannot go wrong.
    """
    abax = TickerAlias("ABAX", 3377, dt.date(1996, 7, 29), dt.date(2018, 8, 11))
    resolved, unresolved = resolve_as_of([abax], ["ABAX"], as_of=dt.date(2026, 10, 22))
    assert resolved == {}
    assert unresolved == {"ABAX": UnresolvedReason.NO_HOLDER_AT_DATE}


def test_two_holders_on_one_date_is_ambiguous_not_a_coin_flip() -> None:
    """The corpus contradicts itself; `UNRESOLVED` is the honest answer.

    `AMBIGUOUS` is a separate reason from `NO_HOLDER_AT_DATE` because the two
    call for opposite responses: one means fix the universe, the other means
    fix the corpus.
    """
    # EARLY and LATE do not overlap -- LATE starts the day EARLY ends, which is
    # what `valid_to` being exclusive means -- so a genuine overlap has to be
    # constructed to reach this branch at all.
    overlap = TickerAlias("AUTO", 9999, dt.date(1990, 1, 1), None)
    resolved, unresolved = resolve_as_of([EARLY, overlap], ["AUTO"], as_of=dt.date(2000, 1, 3))
    assert resolved == {}
    assert unresolved == {"AUTO": UnresolvedReason.AMBIGUOUS}


def test_a_ticker_is_never_in_both_returns() -> None:
    """A caller reading only `resolved` must not be able to act on a guess."""
    aliases = [EARLY, LATE, OPEN_ENDED]
    tickers = ["AUTO", "AAPL", "NEVERHELD"]
    resolved, unresolved = resolve_as_of(aliases, tickers, as_of=dt.date(2026, 10, 22))
    assert set(resolved) & set(unresolved) == set()
    assert set(resolved) | set(unresolved) == set(tickers)


def test_an_unknown_ticker_gets_a_reason_rather_than_silence() -> None:
    _, unresolved = resolve_as_of([], ["NOTATICKER"], as_of=dt.date(2026, 10, 22))
    assert unresolved == {"NOTATICKER": UnresolvedReason.NO_HOLDER_AT_DATE}
