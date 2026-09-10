"""Tests for SIC extraction from filing headers.

Every shape asserted here was found in a real filing held locally, not
imagined. A parser written against the modern header alone loses the 1994-2000
filings entirely -- which are exactly the ones a survivorship study most needs.
"""

from __future__ import annotations

import pytest

from tradeit.edgar.sic import DIVISIONS, division_of, parse_sic_header

MODERN = "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tSERVICES-PREPACKAGED SOFTWARE [7372]\n"


class TestTheFiveHeaderShapes:
    def test_description_and_code(self) -> None:
        found = parse_sic_header(MODERN)
        assert found is not None
        assert found.code == 7372
        assert found.description == "SERVICES-PREPACKAGED SOFTWARE"
        assert found.division == "Services"

    def test_code_alone_in_brackets(self) -> None:
        """Seen in 1999-2000 filings."""
        found = parse_sic_header("STANDARD INDUSTRIAL CLASSIFICATION:\t [4813]\n")
        assert found is not None
        assert found.code == 4813
        assert found.description == ""

    def test_a_bare_code_with_no_brackets(self) -> None:
        """Seen in 1994 filings."""
        found = parse_sic_header("STANDARD INDUSTRIAL CLASSIFICATION:\t6211\n")
        assert found is not None
        assert found.code == 6211
        assert found.division == "Finance, Insurance and Real Estate"

    def test_a_header_whose_closing_bracket_wrapped_to_the_next_line(self) -> None:
        """The real shape: '[4813' then ']\\t\\tIRS NUMBER:' on the line below."""
        text = (
            "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\t [4813\n]\t\tIRS NUMBER:\t\t\t\t232259884\n"
        )
        found = parse_sic_header(text)
        assert found is not None
        assert found.code == 4813

    def test_an_empty_classification_is_absence_not_a_guess(self) -> None:
        assert parse_sic_header("STANDARD INDUSTRIAL CLASSIFICATION:\t []\n") is None

    def test_no_header_at_all(self) -> None:
        assert parse_sic_header("CONFORMED SUBMISSION TYPE:\t10-K\n") is None


class TestVerbatimDescriptions:
    def test_the_secs_own_wording_is_kept(self) -> None:
        """Not normalised. The wording is the evidence."""
        found = parse_sic_header(MODERN)
        assert found is not None
        assert found.description == "SERVICES-PREPACKAGED SOFTWARE"

    def test_a_description_containing_its_own_brackets_is_not_truncated(self) -> None:
        text = (
            "STANDARD INDUSTRIAL CLASSIFICATION:\t"
            "WHOLESALE-PETROLEUM & PETROLEUM PRODUCTS (NO BULK STATIONS) [5172]\n"
        )
        found = parse_sic_header(text)
        assert found is not None
        assert found.code == 5172
        assert found.description.endswith("(NO BULK STATIONS)")


class TestDivisions:
    @pytest.mark.parametrize(
        ("code", "division"),
        [
            (100, "Agriculture, Forestry and Fishing"),
            (1311, "Mining"),
            (1531, "Construction"),
            (2834, "Manufacturing"),
            (4813, "Transportation, Communications, Electric, Gas and Sanitary Services"),
            (5172, "Wholesale Trade"),
            (5990, "Retail Trade"),
            (6798, "Finance, Insurance and Real Estate"),
            (7372, "Services"),
            (9995, "Nonclassifiable Establishments"),
        ],
    )
    def test_known_codes_land_in_their_published_division(self, code: int, division: str) -> None:
        assert division_of(code) == division

    def test_a_reit_is_finance_not_real_estate_of_its_own(self) -> None:
        """6798 sits inside the Finance/Insurance/Real Estate division."""
        assert division_of(6798) == "Finance, Insurance and Real Estate"

    def test_an_unassigned_range_is_none_not_other(self) -> None:
        """1800-1999 and 9730-9899 are genuine gaps in the standard."""
        assert division_of(1900) is None
        assert division_of(9800) is None

    def test_a_code_in_no_division_is_not_returned_as_a_classification(self) -> None:
        assert parse_sic_header("STANDARD INDUSTRIAL CLASSIFICATION:\t1900\n") is None

    def test_the_divisions_do_not_overlap(self) -> None:
        for i, (low, high, _) in enumerate(DIVISIONS):
            assert low <= high
            for other_low, other_high, _ in DIVISIONS[i + 1 :]:
                assert high < other_low or other_high < low

    def test_the_divisions_are_in_ascending_order(self) -> None:
        lows = [low for low, _, _ in DIVISIONS]
        assert lows == sorted(lows)


class TestTheMajorGroup:
    def test_it_is_the_two_digit_prefix(self) -> None:
        found = parse_sic_header(MODERN)
        assert found is not None
        assert found.major_group == 73

    def test_a_three_digit_code_groups_correctly(self) -> None:
        found = parse_sic_header("STANDARD INDUSTRIAL CLASSIFICATION:\t100\n")
        assert found is not None
        assert found.major_group == 1


def test_only_the_header_should_be_searched() -> None:
    """The phrase appears in filing bodies that discuss SIC codes.

    The parser searches whatever it is given, so the caller passes a truncated
    document -- and this records why, because a body mention is not an
    assertion about the filer.
    """
    body = (
        "The registrant notes that its STANDARD INDUSTRIAL CLASSIFICATION: "
        "SOMETHING ELSE [1234] was previously reported in error."
    )
    found = parse_sic_header(body)
    assert found is not None and found.code == 1234  # it will match anything given
