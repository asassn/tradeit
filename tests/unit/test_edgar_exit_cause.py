"""Why a security stopped trading: 8-K header items, and the classification on them.

The two headers below are verbatim from sec.gov (fetched 2026-09-11), trimmed
after the filer block. They are the measurement the parser was built on: item
numbers are in the header in both schemes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.edgar.doctext import bankruptcy_heading_present, form15_holders_of_record
from tradeit.edgar.eightk import EightKHeader, ItemMeaning, item_meanings, parse_8k_header
from tradeit.edgar.evidence import EvidenceStrength
from tradeit.edgar.exit_cause import ExitCause, classify_exit

HEADER_2005 = """<SEC-HEADER>0000897069-05-001532.hdr.sgml : 20050622
<ACCEPTANCE-DATETIME>20050622152749
<ACCESSION-NUMBER>0000897069-05-001532
<TYPE>8-K
<PUBLIC-DOCUMENT-COUNT>4
<PERIOD>20050620
<ITEMS>1.01
<ITEMS>8.01
<ITEMS>9.01
<FILING-DATE>20050622
<FILER>
<COMPANY-DATA>
<CONFORMED-NAME>INFODATA SYSTEMS INC
<CIK>0000050420
<ASSIGNED-SIC>7372
</COMPANY-DATA>
</FILER>
</SEC-HEADER>
"""

HEADER_2002 = """<SEC-HEADER>0000891020-02-000814.hdr.sgml : 20020531
<ACCEPTANCE-DATETIME>20020531161128
<ACCESSION-NUMBER>0000891020-02-000814
<TYPE>8-K
<PUBLIC-DOCUMENT-COUNT>3
<PERIOD>20020531
<ITEMS>5
<ITEMS>7
<FILING-DATE>20020531
<FILER>
<COMPANY-DATA>
<CONFORMED-NAME>ACKERLEY GROUP INC
<CIK>0000319120
</COMPANY-DATA>
</FILER>
</SEC-HEADER>
"""

STOP = dt.date(2006, 6, 30)


def _k(day: dt.date, *items: str, accession: str = "0000000000-06-000001") -> EightKHeader:
    return EightKHeader(
        accession=accession,
        filer_ciks=(1,),
        form_type="8-K",
        filing_date=day,
        period=day,
        items=tuple(items),
    )


def _days(n: int) -> dt.date:
    return STOP + dt.timedelta(days=n)


class TestParsingTheHeader:
    def test_the_2004_scheme_header(self) -> None:
        header = parse_8k_header(HEADER_2005)
        assert header is not None
        assert header.cik == 50420
        assert header.accession == "0000897069-05-001532"
        assert header.filing_date == dt.date(2005, 6, 22)
        assert header.period == dt.date(2005, 6, 20)
        assert header.items == ("1.01", "8.01", "9.01")

    def test_the_legacy_scheme_header(self) -> None:
        header = parse_8k_header(HEADER_2002)
        assert header is not None
        assert header.items == ("5", "7")
        assert header.cik == 319120

    def test_a_throttle_page_is_not_a_header(self) -> None:
        """sec.gov serves it with HTTP 200."""
        page = "<html><h1>Your Request Originates from an Undeclared Automated Tool</h1>"
        assert parse_8k_header(page) is None

    def test_a_header_for_another_form_is_refused(self) -> None:
        assert parse_8k_header(HEADER_2005.replace("<TYPE>8-K", "<TYPE>10-Q")) is None

    def test_a_joint_filing_belongs_to_every_filer(self) -> None:
        """EOP's 8-Ks named the trust first and the operating partnership second."""
        joint = HEADER_2005.replace(
            "</FILER>\n</SEC-HEADER>",
            "</FILER>\n<FILER>\n<COMPANY-DATA>\n<CIK>0001043866\n</COMPANY-DATA>\n"
            "</FILER>\n</SEC-HEADER>",
        )
        header = parse_8k_header(joint)
        assert header is not None
        assert header.filer_ciks == (50420, 1043866)
        assert header.filed_by(1043866) and header.filed_by(50420)
        assert not header.filed_by(1)

    def test_a_subject_company_is_not_a_filer(self) -> None:
        with_subject = HEADER_2005.replace(
            "</SEC-HEADER>",
            "<SUBJECT-COMPANY>\n<COMPANY-DATA>\n<CIK>0000000777\n</COMPANY-DATA>\n"
            "</SUBJECT-COMPANY>\n</SEC-HEADER>",
        )
        header = parse_8k_header(with_subject)
        assert header is not None and not header.filed_by(777)

    def test_a_header_without_a_filer_cik_is_refused(self) -> None:
        assert parse_8k_header(HEADER_2005.replace("<CIK>0000050420\n", "")) is None


class TestWhatTheItemsMean:
    def test_dotted_items(self) -> None:
        header = _k(STOP, "1.03", "2.01", "3.01", "3.03", "5.01", "8.01")
        assert item_meanings(header) == {
            ItemMeaning.BANKRUPTCY,
            ItemMeaning.ACQUISITION_OR_DISPOSITION_COMPLETED,
            ItemMeaning.DELISTING_NOTICE,
            ItemMeaning.SECURITY_RIGHTS_MODIFIED,
            ItemMeaning.CHANGE_IN_CONTROL,
        }

    def test_legacy_items_before_the_overhaul(self) -> None:
        header = _k(dt.date(2002, 5, 31), "1", "2", "3", "5", "7")
        assert item_meanings(header) == {
            ItemMeaning.CHANGE_IN_CONTROL,
            ItemMeaning.ACQUISITION_OR_DISPOSITION_COMPLETED,
            ItemMeaning.BANKRUPTCY,
        }

    def test_a_bare_integer_after_the_overhaul_means_nothing(self) -> None:
        """Guessing the scheme is how an auditor change becomes a bankruptcy."""
        assert item_meanings(_k(dt.date(2006, 1, 5), "3", "1")) == frozenset()

    def test_the_legacy_other_events_item_means_nothing(self) -> None:
        assert item_meanings(_k(dt.date(2002, 5, 31), "5", "7")) == frozenset()


class TestClassification:
    def test_a_bankruptcy_item_the_document_confirms(self) -> None:
        header = _k(_days(-40), "1.03", "9.01", accession="A")
        found = classify_exit(STOP, [("8-K", _days(-40))], [header], bankruptcy_text={"A": True})
        assert found.cause is ExitCause.BANKRUPT
        assert found.strength is EvidenceStrength.FORM_DIRECT

    def test_a_legacy_bankruptcy_item(self) -> None:
        stop = dt.date(2002, 3, 1)
        header = _k(dt.date(2002, 1, 15), "3", "7", accession="A")
        found = classify_exit(
            stop, [("8-K", dt.date(2002, 1, 15))], [header], bankruptcy_text={"A": True}
        )
        assert found.cause is ExitCause.BANKRUPT

    def test_a_header_the_document_contradicts_is_not_a_bankruptcy(self) -> None:
        """General Instrument, 1999: header items 3,7; text "Item 2. ACQUISITION"."""
        header = EightKHeader(
            accession="GI",
            filer_ciks=(1,),
            form_type="8-K",
            filing_date=dt.date(1999, 4, 2),
            period=None,
            items=("3", "7"),
        )
        stop = dt.date(2000, 1, 5)
        found = classify_exit(
            stop,
            [("8-K", dt.date(1999, 4, 2)), ("DEFM14A", dt.date(1999, 11, 1))],
            [header],
            bankruptcy_text={"GI": False},
        )
        assert found.cause is ExitCause.ACQUISITION_INDICATED
        assert any("document does not" in c for c in found.conflicts)

    def test_an_unread_document_leaves_the_bankruptcy_unverified(self) -> None:
        header = _k(_days(-40), "1.03", accession="A")
        found = classify_exit(STOP, [("8-K", _days(-40))], [header])
        assert found.cause is not ExitCause.BANKRUPT
        assert any("document unread" in c for c in found.conflicts)

    def test_bankruptcy_outranks_a_sale_and_keeps_the_conflict(self) -> None:
        """A section 363 sale looks like an acquisition and pays the equity nothing.

        Modelled as a 363 sale actually appears: the acquirer's deal
        communications (425) and a completion 8-K. It first used a merger proxy,
        which a 363 sale never has -- there is no shareholder vote -- and which
        now correctly means the shareholders *were* bought out.
        """
        filings = [("425", _days(-90)), ("8-K", _days(-200)), ("8-K", _days(5))]
        eightks = [_k(_days(-200), "1.03", accession="B"), _k(_days(5), "2.01", "5.01")]
        found = classify_exit(STOP, filings, eightks, bankruptcy_text={"B": True})
        assert found.cause is ExitCause.BANKRUPT
        assert any("425" in c for c in found.conflicts)
        assert any("2.01" in c for c in found.conflicts)

    def test_an_offer_to_shareholders_after_the_item_means_the_equity_was_bought(
        self,
    ) -> None:
        """Conning, 1999-2000: the parent's receivership, then MetLife's tender."""
        filings = [("8-K", _days(-240)), ("SC 14D9", _days(-40)), ("15-12G", _days(5))]
        found = classify_exit(
            STOP,
            filings,
            [_k(_days(-240), "1.03", accession="P")],
            bankruptcy_text={"P": True},
            form15_holders=[(_days(5), 1)],
        )
        assert found.cause is ExitCause.ACQUIRED
        assert any("may concern a parent" in c for c in found.conflicts)

    def test_a_deal_that_failed_before_the_bankruptcy_is_still_a_bankruptcy(self) -> None:
        """Edge Petroleum: the Chaparral merger collapsed in 2008, Chapter 11 in 2009."""
        filings = [("DEFM14A", _days(-300)), ("8-K", _days(-80))]
        found = classify_exit(
            STOP,
            filings,
            [_k(_days(-80), "1.03", accession="E")],
            bankruptcy_text={"E": True},
        )
        assert found.cause is ExitCause.BANKRUPT

    def test_a_proposal_and_a_completion_make_an_acquisition(self) -> None:
        filings = [("DEFM14A", _days(-120)), ("8-K", _days(3))]
        found = classify_exit(STOP, filings, [_k(_days(3), "2.01", "3.01", "5.01")])
        assert found.cause is ExitCause.ACQUIRED

    def test_a_proposal_alone_is_only_indicated(self) -> None:
        """A merger proxy is a proposal, not an outcome."""
        found = classify_exit(STOP, [("DEFM14A", _days(-120))], [])
        assert found.cause is ExitCause.ACQUISITION_INDICATED

    def test_a_completion_alone_is_only_indicated(self) -> None:
        """Item 2.01 on an acquirer's 8-K means it bought something."""
        found = classify_exit(STOP, [("8-K", _days(3))], [_k(_days(3), "2.01")])
        assert found.cause is ExitCause.ACQUISITION_INDICATED

    def test_a_proposal_filed_long_after_the_stop_cannot_explain_it(self) -> None:
        found = classify_exit(STOP, [("DEFM14A", _days(200))], [])
        assert found.cause is ExitCause.UNRESOLVED

    def test_going_private_counts_as_a_transaction(self) -> None:
        found = classify_exit(STOP, [("SC 13E3", _days(-60))], [])
        assert found.cause is ExitCause.ACQUISITION_INDICATED

    def test_a_registrant_that_kept_reporting_did_not_die(self) -> None:
        found = classify_exit(STOP, [("10-K", _days(400))], [])
        assert found.cause is ExitCause.KEPT_REPORTING

    def test_an_acquisition_beats_later_reporting(self) -> None:
        """An acquired company with public debt goes on filing 10-Ks."""
        filings = [("DEFM14A", _days(-100)), ("10-K", _days(400))]
        found = classify_exit(STOP, filings, [])
        assert found.cause is ExitCause.ACQUISITION_INDICATED

    def test_late_filing_notices_indicate_distress(self) -> None:
        found = classify_exit(STOP, [("NT 10-K", _days(-80)), ("15-12G", _days(20))], [])
        assert found.cause is ExitCause.DISTRESS_INDICATED

    def test_a_delisting_notice_without_a_transaction_indicates_distress(self) -> None:
        found = classify_exit(STOP, [("8-K", _days(-30))], [_k(_days(-30), "3.01")])
        assert found.cause is ExitCause.DISTRESS_INDICATED

    def test_a_delisting_notice_with_a_transaction_is_the_merger(self) -> None:
        filings = [("SC TO-T", _days(-50)), ("8-K", _days(-30))]
        found = classify_exit(STOP, filings, [_k(_days(-30), "3.01")])
        assert found.cause is ExitCause.ACQUISITION_INDICATED

    def test_a_form_15_alone_explains_nothing(self) -> None:
        found = classify_exit(STOP, [("15-12G", _days(30))], [])
        assert found.cause is ExitCause.DEREGISTERED_UNEXPLAINED

    def test_a_form_15_with_no_public_holders_is_an_extinguished_class(self) -> None:
        found = classify_exit(STOP, [("15-12G", _days(10))], [], form15_holders=[(_days(10), 0)])
        assert found.cause is ExitCause.EXTINGUISHED
        assert found.strength is EvidenceStrength.FORM_DIRECT

    def test_a_proposal_and_an_extinguished_class_make_an_acquisition(self) -> None:
        """Dal-Tile's merger documents were filed under Mohawk's CIK."""
        found = classify_exit(
            STOP,
            [("DEFM14A", _days(-60)), ("15-15D", _days(1))],
            [],
            form15_holders=[(_days(1), 1)],
        )
        assert found.cause is ExitCause.ACQUIRED

    def test_a_form_15_with_public_holders_left_explains_nothing(self) -> None:
        """DSI Toys certified 25 holders, the year it filed Chapter 11."""
        found = classify_exit(STOP, [("15-12G", _days(10))], [], form15_holders=[(_days(10), 25)])
        assert found.cause is ExitCause.DEREGISTERED_UNEXPLAINED

    def test_a_form_15_outside_the_window_is_not_read(self) -> None:
        """Westwood Homestead's 1998 Form 15 is not evidence about 2000."""
        found = classify_exit(STOP, [("15-12G", _days(10))], [], form15_holders=[(_days(-700), 0)])
        assert found.cause is ExitCause.DEREGISTERED_UNEXPLAINED

    def test_cancelled_shares_in_a_bankruptcy_are_still_a_bankruptcy(self) -> None:
        found = classify_exit(
            STOP,
            [("8-K", _days(-100)), ("15-12G", _days(20))],
            [_k(_days(-100), "1.03", accession="C")],
            bankruptcy_text={"C": True},
            form15_holders=[(_days(20), 0)],
        )
        assert found.cause is ExitCause.BANKRUPT

    def test_a_year_without_periodic_reports_is_flagged_not_reclassified(self) -> None:
        """SONICblue: Chapter 11 in 2003, equity cancelled in 2009."""
        silent = classify_exit(STOP, [("15-12G", _days(10))], [], form15_holders=[(_days(10), 0)])
        assert silent.cause is ExitCause.EXTINGUISHED
        assert any("no 10-K" in c for c in silent.conflicts)
        reporting = classify_exit(
            STOP,
            [("10-Q", _days(-50)), ("15-12G", _days(10))],
            [],
            form15_holders=[(_days(10), 0)],
        )
        assert not any("no 10-K" in c for c in reporting.conflicts)

    def test_nothing_is_unresolved(self) -> None:
        found = classify_exit(STOP, [("10-Q", _days(-60))], [])
        assert found.cause is ExitCause.UNRESOLVED
        assert found.strength is EvidenceStrength.NONE

    @pytest.mark.parametrize(("read", "expected"), [(2, True), (1, False)])
    def test_unread_8ks_are_counted_not_assumed_empty(self, read: int, expected: bool) -> None:
        filings = [("8-K", _days(-30)), ("8-K", _days(-10))]
        headers = [_k(_days(-30), "8.01"), _k(_days(-10), "8.01")][:read]
        found = classify_exit(STOP, filings, headers)
        assert (found.eightks_listed, found.eightks_read) == (2, read)
        assert found.fully_read is expected


class TestReadingTheDocuments:
    @pytest.mark.parametrize(
        "text",
        [
            "ITEM 1.03 BANKRUPTCY OR RECEIVERSHIP. On March 7, the Company filed",
            "Item 3. Bankruptcy or Receivership <br> On January 21, 2003",
            "<b>Item&nbsp;1.03</b> &#8211; Bankruptcy or Receivership",
            # Kentucky Electric Steel, 2003.
            "Item 3. Bankruptcy and Receivership On February 5, 2003, Kentucky",
            # Federal-Mogul, 2007.
            "Item 1.03 and Item 8.01. Bankruptcy or Receivership and Other Events.",
            # Webvan and Bio-Plexus, 2001.
            "Item 3. Bankruptcy. ---------- On July 13, 2001, Webvan Group",
            # Winstar, 2001: the filer's own typo.
            "ITEM 3. BANKRUPTCY OR RECEIVORSHIP A. On April 18, 2001",
            # Kmart, 2002: right title, wrong item number, and a petition.
            "Item 2. Bankruptcy or Receivership. On January 22, 2002, Kmart "
            "Corporation filed voluntary petitions for reorganization under "
            "Chapter 11 of the United States Bankruptcy Code",
            # Luminant, 2001: no title, then the petition.
            "Item 3. On December 7, 2001, Luminant Worldwide Corporation filed a "
            "voluntary petition for reorganization relief under Chapter 11",
        ],
    )
    def test_a_bankruptcy_heading_in_either_scheme(self, text: str) -> None:
        assert bankruptcy_heading_present(text)

    def test_merger_boilerplate_is_not_a_bankruptcy(self) -> None:
        """CFI ProServices' merger agreement, verbatim."""
        text = (
            "Item 2. Acquisition or Disposition of Assets ... enforceability may be "
            "subject to laws of general application relating to bankruptcy, insolvency "
            "and the relief of debtors"
        )
        assert not bankruptcy_heading_present(text)

    def test_a_petition_and_a_chapter_far_apart_are_two_claims(self) -> None:
        text = "A voluntary petition was discussed. " + ("x " * 300) + "See Chapter 11."
        assert not bankruptcy_heading_present(text)

    def test_general_instruments_text_is_not_a_bankruptcy(self) -> None:
        text = "Item 2. ACQUISITION OR DISPOSITION OF ASSETS  Item 7. FINANCIAL STATEMENTS"
        assert not bankruptcy_heading_present(text)

    def test_the_header_copy_is_not_read_as_the_document(self) -> None:
        """Otherwise the check would confirm the header by reading the header."""
        submission = (
            "<SEC-HEADER>ITEM INFORMATION: Bankruptcy or receivership\n</SEC-HEADER>\n"
            "<DOCUMENT><TYPE>8-K<TEXT>Item 2. ACQUISITION OR DISPOSITION OF ASSETS</DOCUMENT>"
        )
        assert not bankruptcy_heading_present(submission)

    def test_the_gap_may_not_cross_another_item(self) -> None:
        text = "Item 3. Not applicable. Item 5. Other Events. No bankruptcy or receivership."
        assert not bankruptcy_heading_present(text)

    def test_the_legacy_legal_proceedings_heading_is_not_a_bankruptcy(self) -> None:
        """Item 3 means legal proceedings in a 10-K; a title must say bankrupt."""
        assert not bankruptcy_heading_present("Item 3. Legal Proceedings. None.")

    def test_a_passing_mention_is_not_a_heading(self) -> None:
        assert not bankruptcy_heading_present("the risk of bankruptcy or receivership remains")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: ZERO",
                0,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: 0 Pursuant to",
                0,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: One (1) Pursuant",
                1,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: One Shareholder",
                1,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: 25 Pursuant",
                25,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: 1,204",
                1204,
            ),
            (
                "Approximate number of holders of record as of the certification "
                "or notice date: less than 300",
                None,
            ),
            ("A Form 15 with the line missing entirely", None),
        ],
    )
    def test_holders_of_record(self, text: str, expected: int | None) -> None:
        assert form15_holders_of_record(text) == expected
