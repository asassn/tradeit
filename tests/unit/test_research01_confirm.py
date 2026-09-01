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

    def test_the_vendor_old_suffix_is_stripped_before_comparing(self) -> None:
        """EODHD writes `ABDR_old`; the filing says `ABDR`."""
        c = confirm_ticker(ticker="ABDR_old", cik=1021080, accession="a", extract=_extract(ABDR))
        assert c is not None and c.ticker == "ABDR"

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
