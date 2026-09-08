"""The ticker an insider filing states in a field rather than a sentence.

Every fixture below is the shape of a real filing sampled 2026-09-07. The three
guards each come from one of the first eight documents opened, which is why
they exist at all.
"""

from __future__ import annotations

import pytest

from tradeit.research01.insider import InsiderVerdict, read_insider_symbol


def _doc(cik: str, name: str, symbol: str) -> str:
    return (
        "<ownershipDocument><issuer>"
        f"<issuerCik>{cik}</issuerCik>"
        f"<issuerName>{name}</issuerName>"
        f"<issuerTradingSymbol>{symbol}</issuerTradingSymbol>"
        "</issuer></ownershipDocument>"
    )


def test_a_matching_filing_binds_its_symbol() -> None:
    got = read_insider_symbol(
        _doc("0000913072", "MERRILL MERCHANTS BANCSHARES INC", "MERB"), cik=913072
    )
    assert got.verdict is InsiderVerdict.SYMBOL
    assert got.symbol == "MERB"
    assert got.binds


def test_the_leading_zeros_of_the_cik_do_not_matter() -> None:
    """EDGAR pads to ten digits in the document and not in the index."""
    assert read_insider_symbol(_doc("0000320193", "Apple Inc.", "AAPL"), cik=320193).binds


def test_a_filing_naming_another_issuer_binds_nothing() -> None:
    """Sampled: a filing indexed under CIK 1085359 whose own issuerCik named
    Brookfield Property REIT. A filing appearing under a CIK is not a statement
    by that registrant — binding on the index alone would have attributed GGP
    to the wrong company."""
    got = read_insider_symbol(
        _doc("0001496048", "Brookfield Property REIT Inc.", "GGP"), cik=1085359
    )
    assert got.verdict is InsiderVerdict.CIK_MISMATCH
    assert got.symbol is None
    assert not got.binds


def test_the_mismatched_symbol_is_still_reported_for_the_record() -> None:
    """Refused as a binding, kept as an observation: an auditor asking why GGP
    was not bound needs to see that GGP is what the filing said."""
    got = read_insider_symbol(_doc("0001496048", "Brookfield", "GGP"), cik=1085359)
    assert got.stated == "GGP"


@pytest.mark.parametrize("stated", ["NONE", "none", "N/A", "NIL", "NOT APPLICABLE", "", "-"])
def test_none_is_a_statement_not_a_ticker(stated: str) -> None:
    """Three of the first eight filings said NONE. Read as a ticker it produces
    the symbol NONE; read correctly it is positive evidence of no listed
    security."""
    got = read_insider_symbol(_doc("0000851724", "GEODYNE ENERGY INCOME LTD", stated), cik=851724)
    assert got.verdict is InsiderVerdict.NO_TRADING_SYMBOL
    assert got.symbol is None


def test_a_venue_suffix_is_stripped() -> None:
    """MAJR.OB is MAJR on the OTC Bulletin Board."""
    got = read_insider_symbol(
        _doc("0001009779", "MAJOR AUTOMOTIVE COMPANIES INC", "MAJR.OB"), cik=1009779
    )
    assert got.symbol == "MAJR"
    assert got.stated == "MAJR.OB", "the filing's own rendering is kept for the citation"


@pytest.mark.parametrize("symbol", ["BRK.A", "BRK.B", "BF.A", "CRD.B"])
def test_a_share_class_suffix_is_never_stripped(symbol: str) -> None:
    """The guard that matters most. A rule stripping any trailing dot-suffix
    would turn BRK.A into BRK and merge two classes that trade three orders of
    magnitude apart."""
    got = read_insider_symbol(_doc("0001067983", "BERKSHIRE HATHAWAY INC", symbol), cik=1067983)
    assert got.symbol == symbol


def test_a_document_without_an_issuer_block_says_nothing() -> None:
    got = read_insider_symbol("<ownershipDocument></ownershipDocument>", cik=1)
    assert got.verdict is InsiderVerdict.UNREADABLE
    assert not got.binds


def test_a_missing_symbol_field_is_unreadable_not_absent() -> None:
    """No field is not the same claim as a field saying NONE, and collapsing
    them would turn a parse failure into evidence."""
    doc = "<issuer><issuerCik>0000000042</issuerCik><issuerName>X</issuerName></issuer>"
    got = read_insider_symbol(doc, cik=42)
    assert got.verdict is InsiderVerdict.UNREADABLE


# -- what a blocklist could not catch --------------------------------------


@pytest.mark.parametrize("stated", ["[NONE]", "(none)", "'NONE'", '"None"', "  NONE  ", "[N/A]"])
def test_decoration_around_none_does_not_make_it_a_ticker(stated: str) -> None:
    """Found in the 100-registrant stage test: a filer wrote `[NONE]`, the
    literal set did not contain it, and it bound a ticker of that name. A
    blocklist that must anticipate every rendering of "none" is one that will
    be beaten — so the wrapper is stripped and the value is judged by shape."""
    got = read_insider_symbol(
        _doc("0001258669", "MAN GLENWOOD LEXINGTON TEI LLC", stated), cik=1258669
    )
    assert got.verdict is InsiderVerdict.NO_TRADING_SYMBOL
    assert got.symbol is None


@pytest.mark.parametrize(("stated", "expected"), [('"AAPL"', "AAPL"), ("(MERB)", "MERB")])
def test_decoration_around_a_real_symbol_is_stripped(stated: str, expected: str) -> None:
    got = read_insider_symbol(_doc("0000000042", "X", stated), cik=42)
    assert got.symbol == expected


@pytest.mark.parametrize("stated", ["TOOLONGSYMBOL", "12345", "A B C", "COMMON STOCK"])
def test_a_value_that_is_not_ticker_shaped_is_unreadable(stated: str) -> None:
    """Reported as unreadable rather than as "no trading symbol": a value we
    cannot parse is not the filer telling us the issuer has none, and
    collapsing the two would turn a parse failure into evidence."""
    got = read_insider_symbol(_doc("0000000042", "X", stated), cik=42)
    assert got.verdict is InsiderVerdict.UNREADABLE


def test_the_shape_check_still_admits_a_class_suffix() -> None:
    """The shape must not undo the guard it sits next to."""
    assert (
        read_insider_symbol(_doc("0001067983", "BERKSHIRE", "BRK.A"), cik=1067983).symbol == "BRK.A"
    )
