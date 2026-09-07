"""Confirming a ticker from the filing's own words.

Every sentence below is **verbatim from a real dot-com-era 10-K** retrieved
during the yield measurement, not invented. That matters: the first version of
this extractor scanned whole sentences for capitalised tokens, which looked
reasonable and confirmed **0 of 40** real filings, because `Company's` yields
the token `S` and a rule refusing multi-symbol sentences then rejected
everything. Fixtures written from imagination would not have caught it.
"""

from __future__ import annotations

import pytest

from tradeit.edgar.acquire import EvidenceExtract, ExtractionStatus, Section12bRow, SymbolStatement
from tradeit.edgar.identity import MappingEvidence
from tradeit.research01.confirm import candidate_symbols, confirm_ticker

# Verbatim, from the filings named in each case. Wrapped with implicit
# concatenation rather than shortened: the rule is verbatim or not at all, and a
# line-length limit is not a reason to paraphrase a filing.
ABDR = (
    "The Company's Common Stock is traded under the symbol "
    '"ABDR" in the Nasdaq National Market System'
)
AMVC = (
    "Advanced Machine Vision Corporation's common stock is traded on the "
    "Nasdaq Stock Market under the symbol AMVC"
)
AK = (
    "our Common Stock became listed and began trading on the "
    'New York Stock Exchange under the symbol "AK"'
)
# Xplore Technologies Corp, 0001104659-07-061307, filed 2007-08-10. The symbol
# is XPL; TSX is the Toronto Stock Exchange. Kept verbatim because the whole
# point is that this reads like a binding sentence and is not one.
XPLORE = (
    "Xplore, whose common shares are listed for trading on the Toronto Stock "
    "Exchange under the symbol TSX: XPL, has offices in Austin Texas and "
    "Helsinki Finland"
)
# Silvermex Resources Inc, 0001062993-11-001823, filed 2011-05-03. The same
# defect wearing a hyphen: the first fix refused `TSX:` and let `TSX-V:`
# straight through, because the capture stops at the word boundary.
SILVERMEX = (
    "The Company's Common Shares were traded on the TSX Venture Exchange from "
    "February 29, 1980 to January 20, 2008 under the symbol TSX-V: GGC and "
    "began trading on the Toronto Stock Exchange on January 21, 2008"
)


def _extract(*statements: str, cells: tuple[str, ...] = ()) -> EvidenceExtract:
    return EvidenceExtract(
        status=ExtractionStatus.FOUND,
        symbol_statements=tuple(SymbolStatement(s) for s in statements),
        section_12b_rows=(Section12bRow(cells),) if cells else (),
    )


class TestExtractionIsBoundNotBroad:
    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [(ABDR, {"ABDR"}), (AMVC, {"AMVC"}), (AK, {"AK"})],
    )
    def test_only_the_bound_symbol_is_extracted(self, sentence: str, expected: set[str]) -> None:
        assert candidate_symbols(_extract(sentence)) == expected

    @pytest.mark.parametrize("sentence", [XPLORE, SILVERMEX])
    def test_a_venue_qualified_symbol_does_not_bind_the_venue(self, sentence: str) -> None:
        """`symbol TSX: XPL` and `symbol TSX-V: GGC` bind XPL and GGC.

        Asked whether this filing bound ``TSX``, the pattern said yes, and a
        successor search was one step from cutting seventeen years off a series
        on the strength of it. Caught by reading the citation rather than
        counting the hit. Twice: the first fix caught the colon form and let
        the hyphenated one straight through.
        """
        assert "TSX" not in candidate_symbols(_extract(sentence))

    def test_the_qualified_form_is_refused_rather_than_parsed(self) -> None:
        """Returning nothing is the answer that cannot be wrong.

        Capturing ``XPL`` out of it would be more useful and is a wider change
        than a defect of this shape warrants.
        """
        assert candidate_symbols(_extract(XPLORE)) == set()

    def test_the_venue_qualified_ticker_is_not_confirmed(self) -> None:
        assert (
            confirm_ticker(
                ticker="TSX",
                cik=1177845,
                accession="0001104659-07-061307",
                extract=_extract(XPLORE),
            )
            is None
        )

    def test_an_ordinary_binding_still_works_beside_a_colon(self) -> None:
        """The lookahead must refuse `symbol X: Y` without refusing `symbol: X`."""
        assert candidate_symbols(_extract("trading symbol: AAPL")) == {"AAPL"}
        assert candidate_symbols(_extract("under the symbol XPL on the TSX")) == {"XPL"}

    def test_a_colon_introducing_prose_still_binds(self) -> None:
        """The text is upper-cased before matching, so without the stopword
        narrowing "the" and a ticker are indistinguishable to the lookahead."""
        assert candidate_symbols(_extract("under the symbol ABC: the shares are quoted daily")) == {
            "ABC"
        }

    def test_the_stopword_alternation_is_word_bounded(self) -> None:
        """Without `\\b`, `A` matches the start of `ABC` and the refusal
        quietly stops working for every ticker beginning with a stopword."""
        assert candidate_symbols(_extract("under the symbol TSX: ABC")) == set()

    def test_the_possessive_s_that_broke_the_first_version_is_not_a_symbol(self) -> None:
        """`Company's` -> `S`. A whole-sentence scan returned it alongside the
        real symbol, and the multi-symbol refusal then rejected the filing."""
        assert "S" not in candidate_symbols(_extract(ABDR))


class TestConfirmation:
    def test_a_matching_ticker_is_confirmed_with_the_filing_s_own_words(self) -> None:
        c = confirm_ticker(
            ticker="ABDR", cik=1021080, accession="0000-00-000000", extract=_extract(ABDR)
        )
        assert c is not None
        assert (c.ticker, c.cik) == ("ABDR", 1021080)
        assert c.evidence is MappingEvidence.FILING_DOCUMENT_TEXT
        # Verbatim. A summary of a filing is not a filing.
        assert c.citation == ABDR

    def test_the_vendor_old_suffix_does_not_prevent_a_match(self) -> None:
        """EODHD writes `ABDR_old`; the filing says `ABDR`.

        **Reworked, not re-pinned.** This test used to assert
        ``c.ticker == "ABDR"`` -- it encoded the splice bug as the expected
        result and would have gone on passing while the corpus filled with the
        wrong company's prices. The invariant it was reaching for is that the
        vendor's disambiguator must not *block* a match. What gets stored is a
        separate question, pinned in
        ``TestTheStoredSymbolIsTheVendorsNotTheComparisonForm``.
        """
        c = confirm_ticker(ticker="ABDR_old", cik=1021080, accession="a", extract=_extract(ABDR))
        assert c is not None
        assert c.matched_as == "ABDR"
        assert c.ticker == "ABDR_OLD"

    def test_a_different_symbol_does_not_confirm(self) -> None:
        assert (
            confirm_ticker(ticker="WBVN", cik=1021080, accession="a", extract=_extract(ABDR))
            is None
        )

    def test_a_filing_naming_several_symbols_is_not_established(self) -> None:
        """Common stock, warrants and units name three symbols. Choosing the one
        we hoped for is confirmation bias with a citation attached."""
        assert (
            confirm_ticker(
                ticker="ABDR",
                cik=1021080,
                accession="a",
                extract=_extract(ABDR, 'the Warrants trade under the symbol "ABDRW"'),
            )
            is None
        )

    def test_no_symbol_statement_is_not_established(self) -> None:
        assert confirm_ticker(ticker="ABDR", cik=1, accession="a", extract=_extract()) is None

    def test_a_twelve_b_row_can_carry_the_symbol(self) -> None:
        c = confirm_ticker(
            ticker="AMVC",
            cik=795445,
            accession="a",
            extract=_extract(cells=("Common Stock", "symbol AMVC", "Nasdaq")),
        )
        assert c is not None and c.ticker == "AMVC"

    def test_confirmation_never_claims_manual_verification(self) -> None:
        """A program that reads a filing has not satisfied the rule that a
        PERSON read it. This grade is FILING_DOCUMENT_TEXT and nothing here can
        produce MANUAL_VERIFIED."""
        c = confirm_ticker(ticker="AK", cik=319120, accession="a", extract=_extract(AK))
        assert c is not None
        assert c.evidence is not MappingEvidence.MANUAL_FILING_CITATION
        assert c.evidence is MappingEvidence.FILING_DOCUMENT_TEXT


class TestTheStoredSymbolIsTheVendorsNotTheComparisonForm:
    """The bug that spliced 92 securities and 388,590 bars.

    An earlier version used one value for both jobs. `ABTC_old` -- a company
    that died -- was compared against a filing saying `ABTC` (correct) and then
    **stored** as `ABTC` (wrong). The backfill queried `ABTC.US`, which is a
    different, living company, and filed its prices under the dead registrant's
    CIK. The code written to prevent splices caused one.

    The two forms are now separate fields and this pins them apart.
    """

    @pytest.mark.parametrize(
        ("vendor", "compares_as"),
        [
            ("ABTC_old", "ABTC"),
            ("AVIR_old1", "AVIR"),
            ("AAP1", "AAP"),  # Amway Asia Pacific; AAP is Advance Auto Parts
            ("AED2", "AED"),  # Allied Domecq; AED is Aegon
            ("GM_old", "GM"),
            ("ETYS", "ETYS"),  # no disambiguator, unchanged
        ],
    )
    def test_the_comparison_form_strips_the_vendor_disambiguator(
        self, vendor: str, compares_as: str
    ) -> None:
        from tradeit.research01.confirm import comparison_form

        assert comparison_form(vendor) == compares_as

    def test_the_stored_ticker_keeps_the_disambiguator(self) -> None:
        """Querying the vendor with the stripped form fetches another company."""
        c = confirm_ticker(
            ticker="ABTC_old",
            cik=902476,
            accession="a",
            extract=_extract('the common stock trades under the symbol "ABTC"'),
        )
        assert c is not None
        assert c.ticker == "ABTC_OLD", "must query the vendor with ITS symbol"
        assert c.matched_as == "ABTC", "the filing used the plain form"

    def test_a_trailing_digit_is_a_disambiguator_not_part_of_the_symbol(self) -> None:
        """90.2% of trailing-digit tickers have a base that is a DIFFERENT
        company, so the digit means the same thing `_old` does."""
        c = confirm_ticker(
            ticker="AAP1",
            cik=914601,
            accession="a",
            extract=_extract('our common stock is traded under the symbol "AAP"'),
        )
        assert c is not None
        assert c.ticker == "AAP1"
        assert c.matched_as == "AAP"

    def test_a_transposition_still_does_not_confirm(self) -> None:
        """`FODG` against a filing saying `FOGD` is a different symbol, not a
        disambiguator. Loosening the comparison to catch near-misses would start
        confirming wrong companies, which is worse than confirming none."""
        assert (
            confirm_ticker(
                ticker="FODG",
                cik=1094323,
                accession="a",
                extract=_extract('trading under the symbol "FOGD"'),
            )
            is None
        )


# -- the construction where the security is named in the previous sentence --


class _Extract:
    """The minimum `candidate_symbols` reads: statements and 12(b) rows."""

    def __init__(self, *texts: str) -> None:
        self.symbol_statements = [type("S", (), {"text": t})() for t in texts]
        self.section_12b_rows: list[object] = []


def test_the_ticker_symbol_is_construction_yields_the_symbol() -> None:
    """Verbatim from CIK 4672's 1999 10-K.

    The Trading Symbol column was only added to SEC cover pages in 2019, so a
    pre-2019 registrant's symbol lives in Item 5 prose. Here the security is
    named in the *previous* sentence — "Common Stock is listed on the New York
    Stock Exchange." — which is exactly what the "common stock ... under the
    symbol" pattern cannot reach.
    """
    assert candidate_symbols(_Extract("Ticker Symbol is ABP")) == {"ABP"}


def test_the_copula_is_not_captured_as_the_symbol() -> None:
    """Without allowing for "is", the capture takes IS, `_STOPWORDS` discards
    it, and the real symbol two words later is never reached — a silent miss
    rather than a wrong answer, which is worse to find."""
    assert "IS" not in candidate_symbols(_Extract("The Ticker Symbol is ABP"))


def test_trading_symbol_is_admitted_on_the_same_ground() -> None:
    """Verbatim from CIK 3952's 2013 10-K. It says "stock", not "common
    stock", so the security-named pattern cannot reach it either; the
    specificity is carried by the two-word term of art instead."""
    text = "the Company's stock traded in the over-the-counter market under the trading symbol ADGI"
    assert "ADGI" in candidate_symbols(_Extract(text))


def test_a_bare_symbol_in_prose_is_still_not_a_ticker() -> None:
    """Bare "symbol" appears in trademark and typographic prose, and matching
    it was already refused once. Only the two-word terms are admitted."""
    got = candidate_symbols(_Extract("any logo or symbol authorized by the Sub-Adviser"))
    assert got == set()


def test_the_cover_table_header_is_still_refused() -> None:
    """ "Trading Symbol" is now a matched phrase, and the 12(b) header contains
    it. Reading the header as a binding produced the ticker NAME for seven
    registrants once already."""
    header = "Title of each class Trading Symbol Name of each exchange on which registered"
    assert candidate_symbols(_Extract(header)) == set()


def test_the_venue_qualified_refusals_survive() -> None:
    """`TSX: XPL` and `TSX-V: GGC` are venues followed by a foreign listing,
    not this registrant's symbol. Both were real false positives."""
    assert candidate_symbols(_Extract("The shares trade on the TSX: XPL")) == set()
    assert candidate_symbols(_Extract("Listed on the TSX-V: GGC exchange.")) == set()
