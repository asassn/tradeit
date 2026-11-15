"""Telling a real exit from a company that renamed and kept trading.

The fixtures are the **measured 2026-11-15 filing record**, not invented shapes:
ELME's `25-NSE` and `15-12G`, and MGPI's registrant filing as `SEMPREON, INC`
with no exit form at all. `FORWARD_EXIT_ADJUDICATION_2026-11-14.md` reached the
same verdicts by hand the day before, so these tests also pin that this agrees
with a person who read the documents.

**Each test was run against a deliberately broken copy and shown to fail**, and
the mutant is named in the test that caught it.
"""

from __future__ import annotations

import datetime as dt

from tradeit.forward.silence import (
    SilenceVerdict,
    classify_silence,
    classify_silences,
    index_through,
)

THROUGH = dt.date(2026, 11, 13)

#: ELME, exactly as the 2026 QTR4 index holds it.
ELME = [
    ("8-K", dt.date(2026, 10, 26), "Elme Communities"),
    ("25-NSE", dt.date(2026, 10, 26), "Elme Communities"),
    ("8-K", dt.date(2026, 11, 2), "Elme Communities"),
    ("15-12G", dt.date(2026, 11, 6), "Elme Communities"),
    ("8-K", dt.date(2026, 11, 6), "Elme Communities"),
]

#: MGPI, likewise. Note the rename lands **before** the last print.
MGPI = [
    ("4", dt.date(2026, 10, 1), "MGP INGREDIENTS INC"),
    ("8-K", dt.date(2026, 10, 15), "MGP INGREDIENTS INC"),
    ("10-Q", dt.date(2026, 10, 28), "MGP INGREDIENTS INC"),
    ("144", dt.date(2026, 11, 6), "SEMPREON, INC"),
    ("4", dt.date(2026, 11, 12), "SEMPREON, INC"),
]


def test_a_deregistration_near_the_stop_corroborates_the_exit() -> None:
    """ELME: a `25-NSE` and a `15-12G` around the last print.

    Mutant: drop `"15"` from `EXIT_FORM_PREFIXES`. Caught -- still caught by the
    `25`, so the mutant that actually bites is dropping both, which this also
    catches via the verdict.
    """
    finding = classify_silence("ELME", 104894, dt.date(2026, 11, 5), ELME, index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.EXIT_CORROBORATED
    assert any("15-12G" in item for item in finding.evidence)
    assert not finding.verdict.contradicts_an_exit


def test_a_registrant_that_renamed_and_kept_filing_is_not_an_exit() -> None:
    """**MGPI: the false delisting this module exists to prevent.**

    No Form 25, no Form 15, and the registrant files a Form 4 two days after the
    last print under a different name. Recording this silence as a delisting
    would put a company that is still trading into the survivorship record as
    dead -- the corpus's own defect, reproduced in the one record built to be
    free of it.

    Mutant: return `STILL_FILING` whenever anything was filed after the stop.
    Caught -- the verdict stops naming the rename, which is the actionable part.
    """
    finding = classify_silence("MGPI", 835011, dt.date(2026, 11, 10), MGPI, index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.RENAMED_AND_STILL_FILING
    assert finding.verdict.contradicts_an_exit
    assert "MGP INGREDIENTS INC" in finding.reason
    assert "SEMPREON, INC" in finding.reason


def test_the_rename_is_caught_even_though_it_precedes_the_last_print() -> None:
    """**The bug the first version of this shipped with, pinned.**

    The obvious test -- does the filer name after the stop differ from the name
    before it -- misses MGPI, because the registrant began filing as
    `SEMPREON, INC` on 2026-11-06 and the vendor kept printing MGPI bars until
    2026-11-10. The new name sits on *both* sides of the stop.

    A ticker follows a rename by days or weeks; it does not move the same
    morning. So any test anchored on the stop misses the ordinary case.

    Mutant: compare names across the stop instead of across the window. Caught
    -- MGPI degrades to `STILL_FILING` and the report stops saying why.
    """
    straddling = [f for f in MGPI if f[1] <= dt.date(2026, 11, 10)]
    assert {f[2] for f in straddling} == {"MGP INGREDIENTS INC", "SEMPREON, INC"}, (
        "the premise: both names appear before the stop"
    )

    finding = classify_silence("MGPI", 835011, dt.date(2026, 11, 10), MGPI, index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.RENAMED_AND_STILL_FILING


def test_an_index_older_than_the_silence_refuses_to_answer() -> None:
    """**Fail closed, and the reason this module was not possible before.**

    Measured 2026-11-15: the local index held filings only to 2026-08-13, so
    every forward silence fell in a three-month hole and the classifier returned
    "no exit found" -- a statement about our download schedule dressed up as a
    statement about the company.

    "The index cannot speak to this" and "the filings show no exit" are
    different answers and must never be returned as one.

    Mutant: drop the `index_through < stop` check. Caught -- ELME comes back
    `unresolved` with a reason blaming the company rather than the index, and
    MGPI comes back as a clean exit-free silence that a caller would read as a
    delisting.
    """
    stale = classify_silence(
        "ELME", 104894, dt.date(2026, 11, 5), ELME, index_through=dt.date(2026, 8, 13)
    )

    assert stale.verdict is SilenceVerdict.UNRESOLVED
    assert "2026-08-13" in stale.reason
    assert "refresh" in stale.reason


def test_unresolved_never_counts_as_evidence_the_company_is_alive() -> None:
    """Not knowing is not the same as knowing it is alive.

    A caller that treated them alike would reintroduce exactly the guess this
    removes.

    Mutant: include `UNRESOLVED` in `contradicts_an_exit`. Caught.
    """
    assert not SilenceVerdict.UNRESOLVED.contradicts_an_exit
    assert SilenceVerdict.STILL_FILING.contradicts_an_exit
    assert SilenceVerdict.RENAMED_AND_STILL_FILING.contradicts_an_exit


def test_an_exit_filing_wins_over_filings_that_follow_it() -> None:
    """**Order of the checks is the design, not an accident.**

    A dissolving company files on its way out: ELME filed two 8-Ks and its
    `15-12G` after its last print. A still-filing test running first would call
    a completed dissolution alive -- the opposite error, and just as wrong.

    Mutant: test `after` before `exits`. Caught.
    """
    finding = classify_silence("ELME", 104894, dt.date(2026, 11, 5), ELME, index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.EXIT_CORROBORATED, (
        "filings after the stop are what a dissolution looks like"
    )


def test_a_symbol_that_never_printed_says_so_rather_than_guessing() -> None:
    """ABAX and AUTO: in the universe, dead before it was written.

    Mutant: treat a `None` stop as "today". Caught.
    """
    finding = classify_silence("ABAX", 881890, None, [], index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.UNRESOLVED
    assert "never printed" in finding.reason


def test_a_symbol_with_no_cik_is_unresolved_not_assumed_dead() -> None:
    """Mutant: fall through to the filing tests with `cik=None`. Caught."""
    finding = classify_silence("ZZZZ", None, dt.date(2026, 11, 5), [], index_through=THROUGH)

    assert finding.verdict is SilenceVerdict.UNRESOLVED
    assert "no CIK" in finding.reason


def test_the_report_puts_the_dangerous_finding_first() -> None:
    """A report whose first line is the boring case is read to the second line.

    The silence that would corrupt the record -- one that is *not* an exit --
    must be read before the routine ones.

    Mutant: sort alphabetically. Caught -- ELME, the corroborated exit, leads
    and MGPI's false delisting is buried.
    """
    findings = classify_silences(
        {"ELME": (104894, dt.date(2026, 11, 5)), "MGPI": (835011, dt.date(2026, 11, 10))},
        {104894: ELME, 835011: MGPI},
        index_through=THROUGH,
    )

    assert [f.symbol for f in findings] == ["MGPI", "ELME"]
    assert findings[0].verdict.contradicts_an_exit


def test_index_age_is_measured_from_the_rows_not_a_header() -> None:
    """The header is the file's claim about itself, and one went three months
    stale without anyone noticing.

    Mutant: return a fixed recent date. Caught.
    """
    assert index_through(ELME) == dt.date(2026, 11, 6)
    assert index_through([]) is None
