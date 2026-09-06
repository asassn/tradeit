"""Reading "there was never a ticker" off a cover page.

55.6% of resolver attempts end as ``not_established`` — the annual report was
fetched and read and named no symbol. That bucket conflates *"we could not
parse it"* with *"the registrant says it has no registered class"*, and the
second is a positive finding. These tests use cover-page text taken verbatim
from filings sampled 2026-09-06, because a synthetic cover page proves only
that the regex matches itself.
"""

from __future__ import annotations

import pytest

from tradeit.research01.registered_classes import (
    RegisteredClasses,
    read_registered_classes,
)

# CIK 917540, AMERICAN RESTAURANT GROUP HOLDINGS INC, 10-K405 filed 1997-04-14.
# Verbatim, whitespace-collapsed. Files because it has registered public debt.
DEBT_ONLY = (
    "Securities registered pursuant to Section 12(b) of the Act: Name of Each "
    "Exchange Title of Each Class on Which Registered ------------------- "
    "---------------------- None None Securities registered pursuant to Section "
    "12(g) of the Act: None Indicate by a check mark whether the registrant: (1) "
    "has filed all reports required to be filed by Section 13"
)

# CIK 786470, FIDELITY LEASING INCOME FUND III LP, 10-K filed 1998-03-27.
# Note "Section 12 (b)" with a space, and a 12(g) class that is not equity.
PARTNERSHIP = (
    "Securities registered pursuant to Section 12 (b) of the Act: Name of Each "
    "Exchange Title of Each Class on Which Registered None Not applicable "
    "Securities registered pursuant to Section 12 (g) of the Act: Limited "
    "Partnership Interests Title of Class Indicate by check mark whether the "
    "registrant (1) has filed all reports"
)

LISTED = (
    "Securities registered pursuant to Section 12(b) of the Act: Title of each "
    "class Trading Symbol(s) Name of each exchange on which registered Common "
    "Stock, $0.01 par value ABCD The Nasdaq Stock Market LLC Securities "
    "registered pursuant to Section 12(g) of the Act: None Indicate by check "
    "mark whether the registrant has filed"
)


def test_a_debt_only_issuer_registers_nothing() -> None:
    """Both sections None. The registrant is saying there is no ticker."""
    assert read_registered_classes(DEBT_ONLY) is RegisteredClasses.NONE_AT_ALL


def test_a_partnership_registers_units_under_12g_only() -> None:
    """Inconclusive by design, not negative — see the Nasdaq test below."""
    assert read_registered_classes(PARTNERSHIP) is RegisteredClasses.TWELVE_G_ONLY


def test_a_listed_registrant_is_read_as_listed() -> None:
    assert read_registered_classes(LISTED) is RegisteredClasses.EXCHANGE_LISTED


def test_the_header_tolerates_a_space_before_the_paren() -> None:
    """ "Section 12 (b)" occurs in real filings and is not a typo to normalise."""
    assert "Section 12 (b)" in PARTNERSHIP
    assert read_registered_classes(PARTNERSHIP) is not RegisteredClasses.UNDETERMINED


def test_twelve_g_only_is_never_read_as_having_no_ticker() -> None:
    """**The subtlety that makes a naive reading wrong.** Before 2006 the Nasdaq
    National Market was not an "exchange" for 12(b), so its companies registered
    under 12(g). Treating 12(b)-None as "no ticker" would delete Nasdaq's entire
    pre-2006 population from the denominator."""
    nasdaq_1998 = (
        "Securities registered pursuant to Section 12(b) of the Act: None "
        "Securities registered pursuant to Section 12(g) of the Act: Common "
        "Stock, par value $.01 per share Title of Class Indicate by check mark "
        "whether the registrant has filed all reports"
    )
    got = read_registered_classes(nasdaq_1998)
    assert got is RegisteredClasses.TWELVE_G_ONLY
    assert got is not RegisteredClasses.NONE_AT_ALL


@pytest.mark.parametrize("empty", ["None", "NONE", "Not applicable", "N/A", "NIL", "none."])
def test_the_ways_a_filing_writes_nothing(empty: str) -> None:
    text = (
        f"Securities registered pursuant to Section 12(b) of the Act: {empty} "
        f"Securities registered pursuant to Section 12(g) of the Act: {empty} "
        "Indicate by check mark whether the registrant"
    )
    assert read_registered_classes(text) is RegisteredClasses.NONE_AT_ALL


def test_column_captions_are_not_mistaken_for_a_class_name() -> None:
    """A 12(b) block is mostly furniture — captions and rules. Reading "Title of
    each class" as a security would report every filing as listed."""
    text = (
        "Securities registered pursuant to Section 12(b) of the Act: Title of "
        "each class Trading Symbol(s) Name of each exchange on which registered "
        "None None None Securities registered pursuant to Section 12(g) of the "
        "Act: None Indicate by check mark"
    )
    assert read_registered_classes(text) is RegisteredClasses.NONE_AT_ALL


def test_a_missing_cover_page_is_undetermined_not_negative() -> None:
    """Fail closed: no cover page says nothing either way, and reporting it as
    "no registered class" would invent a finding."""
    assert read_registered_classes("annual report of some company") is (
        RegisteredClasses.UNDETERMINED
    )


def test_a_truncated_cover_page_is_undetermined() -> None:
    """Without the checkbox paragraph the 12(g) block has no end, and taking the
    rest of the document would read the whole filing as a class name."""
    text = (
        "Securities registered pursuant to Section 12(b) of the Act: None "
        "Securities registered pursuant to Section 12(g) of the Act: None"
    )
    assert read_registered_classes(text) is RegisteredClasses.UNDETERMINED


def test_only_the_cover_region_is_read() -> None:
    """A 10-K names exchanges and classes throughout. Reading the whole document
    would find a class in every filing that has ever discussed one."""
    text = DEBT_ONLY + (
        " Item 5. Market for Registrant's Common Equity. The Common Stock is "
        'listed on the New York Stock Exchange under the symbol "XYZ".'
    )
    assert read_registered_classes(text) is RegisteredClasses.NONE_AT_ALL
