"""Ticker to CIK: proposals that are never identity, and refutation that is sound.

The asymmetry this module rests on:

* **Confirmation is unsound here.** A matching company name establishes nothing
  — two firms called "Pacific Enterprises" are not one firm — so a surviving
  candidate stays a candidate.
* **Refutation is sound.** A security trading in 1999 whose registrant filed
  only from 2008 cannot be that registrant. Disjoint spans are a contradiction,
  and removing a wrong pairing cannot manufacture identity.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.research01 import (
    Candidate,
    CandidateStatus,
    normalise_company_name,
    propose_candidates,
    refute_by_span,
)


class TestNormalisation:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("TJX COMPANIES INC /DE/", "TJX Companies, Inc."),
            ("ADOBE INC.", "Adobe Inc"),
            ("CONAGRA BRANDS INC", "ConAgra Brands, Inc."),
        ],
    )
    def test_source_spellings_of_one_company_converge(self, a: str, b: str) -> None:
        """EDGAR and a vendor write the same company differently."""
        assert normalise_company_name(a) == normalise_company_name(b)

    def test_distinct_companies_do_not_converge(self) -> None:
        """Normalisation is lossy; it must not be lossy enough to merge these."""
        assert normalise_company_name("AMERICAN AIRLINES") != normalise_company_name(
            "AMERICAN AIRWAYS"
        )

    def test_it_is_exact_after_normalising_not_fuzzy(self) -> None:
        """An edit-distance match would pair these and produce confident
        nonsense. Exact comparison either matches or does not, visibly."""
        assert normalise_company_name("MICROSTRATEGY") != normalise_company_name("MICROSTRATEGIES")


class TestProposal:
    def test_a_unique_name_becomes_a_candidate_and_not_a_mapping(self) -> None:
        out = propose_candidates([{"Code": "ETYS", "Name": "eToys Inc"}], {"ETOYS": [1071502]})
        assert len(out) == 1
        assert out[0].status is CandidateStatus.CANDIDATE
        assert out[0].cik == 1071502
        # The status vocabulary contains no member meaning "identified".
        assert CandidateStatus.CANDIDATE != "resolved"

    def test_a_shared_name_is_ambiguous_and_carries_no_cik(self) -> None:
        """Kept rather than dropped: silence would read as 'no such company'
        when the truth is 'more than one'."""
        out = propose_candidates(
            [{"Code": "PE", "Name": "Pacific Enterprises"}], {"PACIFIC ENTERPRISES": [1, 2, 3]}
        )
        assert out[0].status is CandidateStatus.AMBIGUOUS
        assert out[0].cik is None
        assert out[0].cik_count == 3

    def test_an_unmatched_name_produces_nothing(self) -> None:
        assert propose_candidates([{"Code": "ZZZZ", "Name": "Nobody Ltd"}], {}) == []


class TestRefutation:
    def _candidate(self) -> Candidate:
        return Candidate(
            ticker="ETYS",
            vendor_name="eToys Inc",
            cik=1071502,
            edgar_name="ETOYS",
            status=CandidateStatus.CANDIDATE,
        )

    def test_disjoint_spans_refute(self) -> None:
        out = refute_by_span(
            self._candidate(),
            trading=(dt.date(1999, 5, 20), dt.date(2001, 2, 26)),
            filing=(dt.date(2008, 1, 1), dt.date(2012, 1, 1)),
        )
        assert out.status is CandidateStatus.REFUTED_BY_SPAN
        assert "disjoint" in out.note

    def test_overlapping_spans_do_NOT_promote(self) -> None:
        """Overlap proves nothing and must not be read as corroboration.

        A registrant files before, during and after the period its security
        trades, so overlap is the expected shape for a *wrong* pairing too.
        """
        out = refute_by_span(
            self._candidate(),
            trading=(dt.date(1999, 5, 20), dt.date(2001, 2, 26)),
            filing=(dt.date(1998, 1, 1), dt.date(2003, 1, 1)),
        )
        assert out.status is CandidateStatus.CANDIDATE

    @pytest.mark.parametrize("missing", ["trading", "filing"])
    def test_an_absent_span_refutes_nothing(self, missing: str) -> None:
        """Otherwise every company whose data we simply have not fetched would
        be quietly refuted -- absence of evidence made into evidence."""
        spans = {
            "trading": (dt.date(1999, 5, 20), dt.date(2001, 2, 26)),
            "filing": (dt.date(1998, 1, 1), dt.date(2003, 1, 1)),
        }
        spans[missing] = None  # type: ignore[assignment]
        out = refute_by_span(self._candidate(), **spans)  # type: ignore[arg-type]
        assert out.status is CandidateStatus.CANDIDATE

    def test_touching_spans_are_not_disjoint(self) -> None:
        out = refute_by_span(
            self._candidate(),
            trading=(dt.date(1999, 1, 1), dt.date(2001, 1, 1)),
            filing=(dt.date(2001, 1, 1), dt.date(2005, 1, 1)),
        )
        assert out.status is CandidateStatus.CANDIDATE

    def test_an_ambiguous_candidate_is_left_alone(self) -> None:
        """Refuting one of several shared CIKs would leave the rest looking
        stronger than they are."""
        ambiguous = Candidate(
            ticker="PE",
            vendor_name="Pacific Enterprises",
            cik=None,
            edgar_name="PACIFIC ENTERPRISES",
            status=CandidateStatus.AMBIGUOUS,
            cik_count=3,
        )
        out = refute_by_span(
            ambiguous,
            trading=(dt.date(1999, 1, 1), dt.date(2000, 1, 1)),
            filing=(dt.date(2010, 1, 1), dt.date(2011, 1, 1)),
        )
        assert out.status is CandidateStatus.AMBIGUOUS
