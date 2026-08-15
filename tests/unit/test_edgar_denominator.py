"""The EDGAR denominator, and the two rules it must not be able to break.

The rules under test, both of which were corrections to an earlier design:

1. **Filing cessation is a candidate, not a death.** It never carries a
   lifecycle date, and :func:`assert_cessation_undated` fails loudly if one is
   ever attached.
2. **The top survivorship grade is unreachable** until a threshold is chosen
   from a real denominator. Passing all thirty controls is necessary and not
   sufficient.

Both are the kind of rule that erodes silently under later edits, so each has a
test whose failure is unambiguous.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pytest

from tradeit.cli_edgar import cmd_audit_paths
from tradeit.edgar.controls import CONTROL_UNIVERSE, unverified
from tradeit.edgar.denominator import (
    RESEARCH_GRADE_THRESHOLD,
    Classification,
    CoverageBounds,
    Denominator,
    SurvivorshipClass,
    classify_corpus,
)
from tradeit.edgar.evidence import (
    EvidenceStrength,
    EvidenceType,
    FormRole,
    LifecycleEvidence,
    LifecycleScope,
    classify_form,
)
from tradeit.edgar.identity import (
    MappingCandidate,
    MappingEvidence,
    MappingStatus,
    SecurityMapping,
    resolve_mapping,
)
from tradeit.edgar.index import (
    EDGAR_FIRST_QUARTER,
    IndexLayout,
    IndexQuarter,
    SkipReason,
    _assert_path_verbatim,
    accession_from_path,
    parse_full_index,
    parse_index,
    quarters,
)
from tradeit.edgar.lifecycle import (
    ExitResolution,
    assert_cessation_undated,
    build_timelines,
    resolve_exit,
)
from tradeit.edgar.pipeline import evidence_from_rows
from tradeit.errors import ConfigError, DataError

# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

#: Authentic ``master.idx`` shape: pipe-delimited, CIK first.
INDEX_BODY = """\
Description:           Master Index of EDGAR Dissemination Feed by CIK
Last Data Received:    December 31, 1998
Comments:              webmaster@sec.gov

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
320193|APPLE INC|10-K|1998-12-23|edgar/data/320193/0000320193-98-000110.txt
1000045|PETS COM INC|S-1|2000-01-20|edgar/data/1000045/0001000045-00-000001.txt
1000045|PETS COM INC|15-12G|2001-02-14|edgar/data/1000045/0001000045-01-000004.txt
garbage line with no pipes
55|SHORT|ROW
"""

#: Authentic 1994 Q3 ``form.idx`` shape: FIXED-WIDTH, form type first. The
#: column order differs from master.idx, which is the defect this fixture
#: exists to keep fixed. Rows are real-format samples.
#
# The header labels are NOT where the data columns are: "CIK" starts at 62 in
# the header while the CIK values sit at 58, and "Date Filed" starts at 74 while
# the dates sit at 68. That is the authentic shape, and it is why header-offset
# slicing failed on ~93% of the real 1994 Q3 rows. Reproduced deliberately so
# the regression cannot come back.
_FORM_IDX_LINES = [
    "Description:           Quarterly Index of EDGAR Dissemination Feed by Form Type",
    "Last Data Received:    September 30, 1994",
    "Comments:              webmaster@sec.gov",
    "Anonymous FTP:         ftp://ftp.sec.gov/edgar/",
    "",
    "Form Type   Company Name                                      "
    "CIK       Date Filed   File Name",
    "-" * 100,
    # Authentic rows reported from the real file, with the data columns offset
    # from the header labels exactly as SEC ships them.
    "10-C        3COM CORP                                     738076    "
    "1994-08-24  edgar/data/738076/0000738076-94-000018.txt",
    "10-C        ARDEN GROUP INC                               225051    "
    "1994-09-28  edgar/data/225051/0000912057-94-003253.txt",
    "10-K        AMERICAN TELEPHONE & TELEGRAPH CO             5907      "
    "1994-09-15  edgar/data/5907/0000005907-94-000012.txt",
    "SC 13D      GENERAL MOTORS CORP                           40730     "
    "1994-07-05  edgar/data/40730/0000040730-94-000003.txt",
    "DEF 14A     SMALL CO INC                                  7         "
    "1994-08-01  edgar/data/7/0000000007-94-000001.txt",
    "8-A12B      A VERY LONG COMPANY NAME THAT OVERFLOWS ITS COLUMN WIDTH  "
    "1234567   1994-09-30  edgar/data/1234567/0001234567-94-000009.txt",
    "this row is not a filing at all",
]
FORM_IDX_BODY = "\n".join(_FORM_IDX_LINES) + "\n"


def test_index_spine_starts_at_1994q3() -> None:
    assert EDGAR_FIRST_QUARTER == (1994, 3)
    IndexQuarter(1994, 3)
    with pytest.raises(DataError, match="1994"):
        IndexQuarter(1994, 2)
    with pytest.raises(DataError, match="1994"):
        IndexQuarter(1993, 1)


def test_quarters_enumerates_inclusive_and_rejects_reversed() -> None:
    got = list(quarters(IndexQuarter(1994, 3), IndexQuarter(1995, 2)))
    assert [q.label for q in got] == ["1994-QTR3", "1994-QTR4", "1995-QTR1", "1995-QTR2"]
    with pytest.raises(DataError, match="precedes"):
        list(quarters(IndexQuarter(1996, 1), IndexQuarter(1995, 1)))


def test_master_idx_pipe_layout_parses() -> None:
    """master.idx: pipe-delimited, CIK first."""
    parsed = parse_index(INDEX_BODY, quarter_label="1998-QTR4")
    assert parsed.header is not None
    assert parsed.header.layout is IndexLayout.PIPE
    assert parsed.header.fields == ("cik", "company_name", "form_type", "filed_at", "path")
    assert len(parsed.rows) == 3
    assert parsed.rows[0].cik == 320193
    assert parsed.rows[0].form_type == "10-K"
    assert parsed.rows[0].filed_at == dt.date(1998, 12, 23)
    assert parsed.rows[0].accession == "0000320193-98-000110"
    assert parsed.rows[0].index_quarter == "1998-QTR4"
    # The two junk lines are skipped, with accounting rather than silently.
    assert parsed.skipped == 2
    assert parsed.data_lines_seen == 5


# ---------------------------------------------------------------------------
# form.idx is FIXED-WIDTH with a different column order. Regression fixture for
# the 1994 Q3 defect: the parser assumed master.idx's pipe layout, so every
# authentic row was skipped and the file reported zero filings.
# ---------------------------------------------------------------------------


def test_authentic_1994q3_form_idx_first_row() -> None:
    parsed = parse_index(FORM_IDX_BODY, quarter_label="1994Q3")
    assert parsed.header is not None
    assert parsed.header.layout is IndexLayout.FIXED
    assert parsed.header.fields == ("form_type", "company_name", "cik", "filed_at", "path")

    first = parsed.rows[0]
    assert first.form_type == "10-C"
    assert first.company_name == "3COM CORP"
    assert first.cik == 738076
    assert first.filed_at == dt.date(1994, 8, 24)
    assert first.path == "edgar/data/738076/0000738076-94-000018.txt"
    assert first.accession == "0000738076-94-000018"
    assert first.index_quarter == "1994Q3"


def test_form_idx_preserves_spaces_in_company_names_and_form_types() -> None:
    """Whitespace splitting would destroy 'SC 13D' and 'GENERAL MOTORS CORP'."""
    rows = {r.cik: r for r in parse_index(FORM_IDX_BODY, quarter_label="1994Q3").rows}
    assert rows[5907].company_name == "AMERICAN TELEPHONE & TELEGRAPH CO"
    assert rows[40730].form_type == "SC 13D"
    assert rows[40730].company_name == "GENERAL MOTORS CORP"
    assert rows[7].form_type == "DEF 14A"


def test_form_idx_arden_group_row() -> None:
    """The second real skipped sample from the 1994 Q3 file."""
    rows = {r.cik: r for r in parse_index(FORM_IDX_BODY, quarter_label="1994Q3").rows}
    arden = rows[225051]
    assert arden.form_type == "10-C"
    assert arden.company_name == "ARDEN GROUP INC"
    assert arden.filed_at == dt.date(1994, 9, 28)
    assert arden.path == "edgar/data/225051/0000912057-94-003253.txt"
    # The accession's prefix differs from the CIK -- a filing agent submitted it.
    assert arden.accession == "0000912057-94-003253"


def test_form_idx_handles_varying_cik_lengths() -> None:
    ciks = {r.cik for r in parse_index(FORM_IDX_BODY, quarter_label="1994Q3").rows}
    assert {7, 5907, 40730, 225051, 738076, 1234567} <= ciks


def test_data_columns_offset_from_header_labels_still_parse() -> None:
    """The exact 93%-failure condition, pinned.

    In the authentic file the CIK values sit several characters left of where the
    'CIK' header label starts. Header-offset slicing therefore produced a CIK
    field that was sometimes digits and a date field that was garbage -- and the
    row was dropped. Right-anchored parsing is immune to it.
    """
    parsed = parse_index(FORM_IDX_BODY, quarter_label="1994Q3")
    header = parsed.header
    assert header is not None
    bounds = dict(zip(header.fields, header.offsets, strict=True))
    line = next(line for line in _FORM_IDX_LINES if line.startswith("10-C        3COM"))

    # Slicing at the header's labels recovers neither field: the CIK slice picks
    # up a fragment of the CIK plus part of the date, and the date slice is
    # garbage. Sometimes such a fragment is all digits, which is what made the
    # old code so hard to catch -- the CIK check passed, the misaligned date then
    # failed, and the row was dropped without the fallback ever being consulted.
    assert line[bounds["cik"] : bounds["filed_at"]].strip() != "738076"
    assert line[bounds["filed_at"] : bounds["path"]].strip() != "1994-08-24"

    # Right-anchored parsing is immune to all of it.
    assert any(r.cik == 738076 for r in parsed.rows)


def test_form_idx_recovers_rows_that_overflow_their_columns() -> None:
    """A company name long enough to run past every column boundary."""
    parsed = parse_index(FORM_IDX_BODY, quarter_label="1994Q3")
    overflow = next(r for r in parsed.rows if r.cik == 1234567)
    assert overflow.form_type == "8-A12B"
    assert overflow.company_name == "A VERY LONG COMPANY NAME THAT OVERFLOWS ITS COLUMN WIDTH"
    assert overflow.filed_at == dt.date(1994, 9, 30)


def test_form_idx_malformed_row_is_skipped_with_accounting() -> None:
    parsed = parse_index(FORM_IDX_BODY, quarter_label="1994Q3")
    assert len(parsed.rows) == 6
    assert parsed.skipped == 1
    assert parsed.data_lines_seen == 7
    assert parsed.skip_ratio < 0.20
    # Skips carry a reason and a sample, not just a count.
    assert sum(parsed.skip_reasons.values()) == 1
    assert parsed.skip_samples


def test_authentic_form_idx_parses_essentially_everything() -> None:
    """The acceptance condition the real file failed: a near-total parse rate."""
    parsed = parse_index(FORM_IDX_BODY, quarter_label="1994Q3", strict=False)
    filings = parsed.data_lines_seen - 1  # one deliberate junk line
    assert len(parsed.rows) == filings
    assert parsed.skip_ratio <= 1 / parsed.data_lines_seen + 1e-9


# ---------------------------------------------------------------------------
# skip-reason diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            "10-K        ACME CORP                     NOTADIGIT  "
            "1994-08-24  edgar/data/1/0000000001-94-000001.txt",
            SkipReason.INVALID_CIK,
        ),
        (
            "10-K        ACME CORP                     123        "
            "not-a-date  edgar/data/1/0000000001-94-000001.txt",
            SkipReason.INVALID_DATE,
        ),
        (
            "10-K        ACME CORP                     123        1994-08-24  nopathhere",
            SkipReason.MISSING_PATH,
        ),
        ("tooshort", SkipReason.FIXED_WIDTH_SLICE_FAILURE),
        (
            "SOLOTOKEN 123 1994-08-24 edgar/data/1/0000000001-94-000001.txt",
            SkipReason.FREE_TEXT_SPLIT_FAILURE,
        ),
    ],
)
def test_skip_reasons_are_attributed_specifically(row: str, expected: SkipReason) -> None:
    header = (
        "Form Type   Company Name                                      "
        "CIK       Date Filed   File Name\n" + "-" * 100 + "\n"
    )
    parsed = parse_index(header + row + "\n", quarter_label="1994Q3", strict=False)
    assert parsed.rows == []
    assert parsed.skip_reasons == {str(expected): 1}
    assert parsed.skip_samples[str(expected)]


def test_company_name_colliding_with_cik_is_recovered_from_the_path() -> None:
    """A name wide enough to eat its padding leaves one token: '...L P5011'.

    The CIK is recoverable from ``edgar/data/<cik>/``, so the boundary is found
    on evidence rather than by guessing where a name ends -- a guess that a
    company called "ACME 2000" would defeat.
    """
    header = (
        "Form Type   Company Name                                      "
        "CIK       Date Filed   File Name\n" + "-" * 100 + "\n"
    )
    row = (
        "10-K405     HOLDINGS MACHINES MOTORS & TELEGRAPH GENERAL L P5011      "
        "1994-09-17  edgar/data/5011/0000005011-94-000123.txt\n"
    )
    parsed = parse_index(header + row, quarter_label="1994Q3")
    assert len(parsed.rows) == 1
    recovered = parsed.rows[0]
    assert recovered.cik == 5011
    assert recovered.form_type == "10-K405"
    assert recovered.company_name == "HOLDINGS MACHINES MOTORS & TELEGRAPH GENERAL L P"


def test_collision_recovery_refuses_when_the_path_disagrees() -> None:
    """Without corroboration it stays a skip -- no boundary is invented."""
    header = (
        "Form Type   Company Name                                      "
        "CIK       Date Filed   File Name\n" + "-" * 100 + "\n"
    )
    row = "10-K        ACME 2000 INC9999    1994-09-17  edgar/data/12345/0000012345-94-000001.txt\n"
    parsed = parse_index(header + row, quarter_label="1994Q3", strict=False)
    assert parsed.rows == []
    assert parsed.skip_reasons == {str(SkipReason.INVALID_CIK): 1}


def test_path_cik_differs_from_the_accession_prefix() -> None:
    """ARDEN's filing agent submitted it; the path CIK is the filer's, not the agent's."""
    rows = {r.cik: r for r in parse_index(FORM_IDX_BODY, quarter_label="1994Q3").rows}
    arden = rows[225051]
    assert arden.accession.startswith("0000912057")
    assert "edgar/data/225051/" in arden.path


# ---------------------------------------------------------------------------
# the path is the raw File Name field, byte for byte
#
# A wrong path silently produces a wrong accession, which produces a citation
# that resolves to a DIFFERENT filing -- worse than no citation, because it
# looks checkable. These pin the invariant rather than the mechanism, because
# the mechanism is exactly what a future edit would change.
# ---------------------------------------------------------------------------


def test_authentic_2002_10k_row_filing_agent_prefix_differs_from_registrant_cik() -> None:
    """CIK 1100683, accession prefix 0000891618. The two must not be confused."""
    header = (
        "Form Type   Company Name                                      "
        "CIK       Date Filed   File Name\n" + "-" * 100 + "\n"
    )
    row = (
        "10-K        IPET HOLDINGS INC                             1100683   "
        "2002-03-29  edgar/data/1100683/0000891618-02-001559.txt\n"
    )
    parsed = parse_index(header + row, quarter_label="2002-QTR1")
    assert len(parsed.rows) == 1
    got = parsed.rows[0]
    assert got.cik == 1100683
    assert got.form_type == "10-K"
    assert got.company_name == "IPET HOLDINGS INC"
    assert got.filed_at == dt.date(2002, 3, 29)
    assert got.path == "edgar/data/1100683/0000891618-02-001559.txt"
    assert got.accession == "0000891618-02-001559"
    # The registrant CIK must never leak into the accession, and vice versa.
    assert "1100683" not in got.accession.split("-")[0]


@pytest.mark.parametrize(
    ("cik", "path", "expected_accession"),
    [
        # IPET: three different filing agents across one registrant's history.
        (1100683, "edgar/data/1100683/0001095811-00-004383.txt", "0001095811-00-004383"),
        (1100683, "edgar/data/1100683/0000891618-01-000054.txt", "0000891618-01-000054"),
        (1100683, "edgar/data/1100683/0000950134-05-011306.txt", "0000950134-05-011306"),
        # Old GM: registrant CIK far from the agent prefix.
        (40730, "edgar/data/40730/0001193125-09-045144.txt", "0001193125-09-045144"),
        # An agent filing for itself -- prefix and registrant genuinely equal.
        (1095811, "edgar/data/1095811/0001095811-02-001559.txt", "0001095811-02-001559"),
    ],
)
def test_agent_prefix_never_synthesised_from_registrant_cik(
    cik: int, path: str, expected_accession: str
) -> None:
    header = (
        "Form Type   Company Name                                      "
        "CIK       Date Filed   File Name\n" + "-" * 100 + "\n"
    )
    row = f"{'8-K':<12}{'SOME REGISTRANT INC':<46}{cik:<10}{'2002-03-29':<12}{path}\n"
    got = parse_index(header + row, quarter_label="2002-QTR1").rows[0]
    assert got.cik == cik
    assert got.path == path
    assert got.accession == expected_accession


def test_accession_is_always_derivable_from_the_emitted_path() -> None:
    """accession == accession_from_path(path), for every row of every fixture."""
    for body, label in (
        (FORM_IDX_BODY, "1994Q3"),
        (INDEX_BODY, "1998-QTR4"),
    ):
        for row in parse_index(body, quarter_label=label, strict=False).rows:
            assert row.accession == accession_from_path(row.path)


def test_every_parsed_path_appears_verbatim_in_its_source_line() -> None:
    for body, label in ((FORM_IDX_BODY, "1994Q3"), (INDEX_BODY, "1998-QTR4")):
        lines = body.splitlines()
        for row in parse_index(body, quarter_label=label, strict=False).rows:
            assert any(row.path in line for line in lines)


def test_a_reconstructed_path_is_rejected_not_emitted() -> None:
    """The guard itself: if any future edit produced a path that is not in the
    raw line, parsing must fail loudly rather than emit a citation pointing at
    a different filing."""
    with pytest.raises(DataError, match="does not appear verbatim"):
        _assert_path_verbatim(
            "edgar/data/1100683/0001095811-02-001559.txt",
            "10-K  IPET HOLDINGS INC  1100683  2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
        )


def test_summary_reports_the_diagnostic_fields() -> None:
    summary = parse_index(FORM_IDX_BODY, quarter_label="1994Q3").summary()
    for key in (
        "candidate_rows",
        "parsed_rows",
        "skipped_rows",
        "skip_rate",
        "skip_reason_counts",
        "split_rules",
    ):
        assert key in summary


def test_later_era_form_idx_layout_also_parses() -> None:
    """Modern form.idx uses wider padding. The header drives the offsets, so
    nothing is hard-coded to the 1994 column positions."""
    body = (
        "Description:           Quarterly Index\n"
        "\n"
        "Form Type    Company Name                                             "
        "     CIK        Date Filed  File Name\n" + "-" * 120 + "\n"
        "10-K         APPLE INC                                                "
        "     320193     2023-11-03  edgar/data/320193/0000320193-23-000106.txt\n"
    )
    rows = parse_full_index(body, quarter_label="2023-QTR4")
    assert len(rows) == 1
    assert rows[0].form_type == "10-K"
    assert rows[0].company_name == "APPLE INC"
    assert rows[0].cik == 320193
    assert rows[0].filed_at == dt.date(2023, 11, 3)


# ---------------------------------------------------------------------------
# The guard: a format mismatch must never look like an empty quarter
# ---------------------------------------------------------------------------


def test_format_mismatch_raises_instead_of_reporting_zero_filings() -> None:
    """The exact 1994 Q3 failure. Data lines present, none parseable -> loud."""
    body = (
        "Form Type   Company Name        CIK       Date Filed   File Name\n" + "-" * 70 + "\n"
        "these lines are data but match no known layout at all\n"
        "neither does this one, nor the one after it\n"
        "and this third one keeps the count above zero\n"
    )
    with pytest.raises(DataError, match="format mismatch, not an empty quarter"):
        parse_index(body, quarter_label="1994Q3")


def test_unrecognised_header_raises_rather_than_returning_nothing() -> None:
    body = "COL A|COL B|COL C\n" + "-" * 40 + "\nx|y|z\n"
    with pytest.raises(DataError, match="no recognisable EDGAR index header"):
        parse_index(body, quarter_label="1994Q3")


def test_a_genuinely_empty_file_is_not_an_error() -> None:
    """No header and no data is an empty file -- a coverage fact, not a defect."""
    parsed = parse_index("", quarter_label="1994Q3")
    assert parsed.rows == []
    assert parsed.data_lines_seen == 0


def test_high_skip_rate_over_a_large_file_raises() -> None:
    """A few corrupt rows are tolerated; a drifted layout is not."""
    header = "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 60 + "\n"
    good = "".join(
        f"{i}|CO {i}|10-K|2001-03-01|edgar/data/{i}/{i:010d}-01-000001.txt\n" for i in range(1, 101)
    )
    bad = "".join(f"junk row {i}\n" for i in range(40))
    with pytest.raises(DataError, match="above the 10% tolerance"):
        parse_index(header + good + bad, quarter_label="2001-QTR1")


def test_strict_can_be_disabled_for_diagnosis() -> None:
    body = (
        "Form Type   Company Name        CIK       Date Filed   File Name\n" + "-" * 70 + "\n"
        "unparseable line\n"
    )
    parsed = parse_index(body, quarter_label="1994Q3", strict=False)
    assert parsed.rows == []
    assert parsed.skipped == 1


def test_parse_full_index_is_eager_so_the_guard_actually_fires() -> None:
    """A generator would defer the format check to iteration -- i.e. to nobody."""
    body = (
        "Form Type   Company Name        CIK       Date Filed   File Name\n" + "-" * 70 + "\n"
        "unparseable line one\n"
        "unparseable line two\n"
    )
    with pytest.raises(DataError):
        parse_full_index(body, quarter_label="1994Q3")


def test_accession_absent_is_empty_not_invented() -> None:
    assert accession_from_path("edgar/data/1/no-accession-here.txt") == ""


# ---------------------------------------------------------------------------
# evidence: four lifecycles, and what the index cannot tell us
# ---------------------------------------------------------------------------


def test_form_25_is_exchange_listing_not_reporting() -> None:
    signal = classify_form("25", dt.date(2010, 5, 1))
    assert signal.scope is LifecycleScope.EXCHANGE_LISTING
    assert signal.evidence_type is EvidenceType.CONFIRMED_EXCHANGE_DELISTING


def test_form_15_is_reporting_not_delisting() -> None:
    """A Form 15 ends a reporting duty. It does not say the shares stopped trading."""
    signal = classify_form("15-12G", dt.date(2001, 2, 14))
    assert signal.scope is LifecycleScope.SEC_REPORTING
    assert signal.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert signal.evidence_type is not EvidenceType.CONFIRMED_EXCHANGE_DELISTING


def test_8k_from_the_index_proves_nothing_without_the_document() -> None:
    """The full-index carries no item numbers, so an 8-K is just an 8-K."""
    signal = classify_form("8-K", dt.date(2001, 12, 3))
    assert signal.role is FormRole.EXIT_CANDIDATE
    assert signal.evidence_type is None
    assert signal.strength is EvidenceStrength.NONE
    assert signal.requires_document_text is True


def test_pre_2005_form_25_is_annotated_with_its_regime() -> None:
    early = classify_form("25", dt.date(1999, 6, 1))
    late = classify_form("25", dt.date(2015, 6, 1))
    assert "sparse" in early.note
    assert "sparse" not in late.note


def test_s4_is_a_pointer_not_a_confirmation() -> None:
    signal = classify_form("S-4", dt.date(1999, 3, 1))
    assert signal.role is FormRole.TRANSACTION_POINTER
    assert signal.evidence_type is None


def test_index_evidence_never_claims_an_effective_date() -> None:
    evidence = LifecycleEvidence.from_index_row(
        cik=1,
        company_name="X",
        form_type="25",
        filed_at=dt.date(2010, 1, 4),
        accession="0000000001-10-000001",
        source_path="edgar/data/1/x.txt",
        index_quarter="2010-QTR1",
    )
    assert evidence.evidence_date == dt.date(2010, 1, 4)
    # A Form 25's effective date is set by rule some days after filing. The
    # index does not carry it, so it stays None rather than borrowing the
    # filing date and inventing precision.
    assert evidence.effective_date is None
    assert evidence.security_class_known is False


# ---------------------------------------------------------------------------
# lifecycle: cessation is a candidate
# ---------------------------------------------------------------------------


def _ev(cik: int, form: str, filed: dt.date) -> LifecycleEvidence:
    return LifecycleEvidence.from_index_row(
        cik=cik,
        company_name=f"ISSUER {cik}",
        form_type=form,
        filed_at=filed,
        accession=f"{cik:010d}-{filed.year % 100:02d}-000001",
        source_path=f"edgar/data/{cik}/x.txt",
        index_quarter=f"{filed.year}-QTR{(filed.month - 1) // 3 + 1}",
    )


def test_cessation_yields_a_candidate_with_no_date() -> None:
    timeline = build_timelines(
        [
            _ev(10, "10-K", dt.date(1999, 3, 1)),
            _ev(10, "10-Q", dt.date(1999, 8, 1)),
            _ev(10, "10-K", dt.date(2000, 3, 1)),
        ]
    )[10]
    resolution = resolve_exit(timeline, as_of=dt.date(2004, 1, 1))
    assert resolution.evidence_type is EvidenceType.POSSIBLE_EXIT_FILING_CESSATION
    assert resolution.strength is EvidenceStrength.CESSATION_ONLY
    # The whole correction, in two assertions.
    assert resolution.evidence_date is None
    assert resolution.effective_date is None
    # The last filing is recorded as context, explicitly not as a death date.
    assert resolution.last_periodic == dt.date(2000, 3, 1)


def test_recent_silence_is_not_yet_even_a_candidate() -> None:
    timeline = build_timelines([_ev(11, "10-K", dt.date(2025, 3, 1))])[11]
    resolution = resolve_exit(timeline, as_of=dt.date(2026, 1, 1))
    assert resolution.evidence_type is EvidenceType.UNRESOLVED_EXIT


def test_resumption_cancels_the_candidate() -> None:
    """Delinquency that later reverses must not read as a death."""
    timeline = build_timelines(
        [
            _ev(12, "10-K", dt.date(2000, 3, 1)),
            _ev(12, "10-K", dt.date(2004, 3, 1)),
        ]
    )[12]
    resolution = resolve_exit(timeline, as_of=dt.date(2005, 1, 1))
    assert resolution.evidence_type is EvidenceType.UNRESOLVED_EXIT


def test_direct_evidence_beats_cessation() -> None:
    timeline = build_timelines(
        [
            _ev(13, "10-K", dt.date(2000, 3, 1)),
            _ev(13, "15-12G", dt.date(2001, 2, 14)),
        ]
    )[13]
    resolution = resolve_exit(timeline, as_of=dt.date(2010, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert resolution.strength is EvidenceStrength.FORM_DIRECT
    assert resolution.evidence_date == dt.date(2001, 2, 14)
    assert resolution.supporting[0].accession


def test_extinguishment_is_derived_from_two_scopes_never_one() -> None:
    only_delisting = build_timelines([_ev(14, "25", dt.date(2010, 5, 3))])[14]
    assert (
        resolve_exit(only_delisting, as_of=dt.date(2020, 1, 1)).evidence_type
        is EvidenceType.CONFIRMED_EXCHANGE_DELISTING
    )

    both = build_timelines(
        [_ev(15, "25", dt.date(2010, 5, 3)), _ev(15, "15-12B", dt.date(2010, 5, 20))]
    )[15]
    resolution = resolve_exit(both, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
    # Derived, and labelled as derived so it cannot be mistaken for a filing's claim.
    assert resolution.strength is EvidenceStrength.FORM_INFERRED
    assert resolution.scopes == frozenset(
        {LifecycleScope.EXCHANGE_LISTING, LifecycleScope.SEC_REPORTING}
    )


def test_guard_catches_a_dated_cessation() -> None:
    bad = ExitResolution(
        cik=99,
        company_name="X",
        evidence_type=EvidenceType.POSSIBLE_EXIT_FILING_CESSATION,
        strength=EvidenceStrength.CESSATION_ONLY,
        scopes=frozenset(),
        evidence_date=dt.date(2001, 1, 1),
        effective_date=None,
        supporting=(),
    )
    with pytest.raises(DataError, match="candidate signal"):
        assert_cessation_undated([bad])


def test_denominator_refuses_to_be_built_from_a_dated_cessation() -> None:
    bad = ExitResolution(
        cik=99,
        company_name="X",
        evidence_type=EvidenceType.POSSIBLE_EXIT_FILING_CESSATION,
        strength=EvidenceStrength.CESSATION_ONLY,
        scopes=frozenset(),
        evidence_date=None,
        effective_date=dt.date(2001, 1, 1),
        supporting=(),
    )
    with pytest.raises(DataError):
        Denominator(resolutions=[bad])


# ---------------------------------------------------------------------------
# identity: never fabricate
# ---------------------------------------------------------------------------


def test_name_match_can_never_resolve() -> None:
    mapping = resolve_mapping(1, [MappingCandidate("ACME", MappingEvidence.NAME_MATCH)])
    assert mapping.status is MappingStatus.AMBIGUOUS
    assert mapping.ticker is None


def test_full_text_search_alone_can_never_resolve() -> None:
    mapping = resolve_mapping(1, [MappingCandidate("ACME", MappingEvidence.FULL_TEXT_SEARCH)])
    assert mapping.status is MappingStatus.AMBIGUOUS


def test_no_evidence_is_unresolved_not_absent() -> None:
    mapping = resolve_mapping(1, [])
    assert mapping.status is MappingStatus.UNRESOLVED
    assert mapping.counts_in_numerator is False


def test_conflicting_candidates_are_ambiguous() -> None:
    mapping = resolve_mapping(
        1,
        [
            MappingCandidate("AAA", MappingEvidence.SEC_COMPANY_TICKERS),
            MappingCandidate("BBB", MappingEvidence.SEC_COMPANY_TICKERS),
        ],
    )
    assert mapping.status is MappingStatus.AMBIGUOUS
    assert mapping.ticker is None


def test_stronger_evidence_wins() -> None:
    mapping = resolve_mapping(
        1,
        [
            MappingCandidate("WRONG", MappingEvidence.NAME_MATCH),
            MappingCandidate("RIGHT", MappingEvidence.SEC_COMPANY_TICKERS),
        ],
    )
    assert mapping.status is MappingStatus.RESOLVED
    assert mapping.ticker == "RIGHT"


def test_manual_verified_requires_a_citation() -> None:
    with pytest.raises(ConfigError, match="citation"):
        SecurityMapping(cik=1, ticker="AAA", status=MappingStatus.MANUAL_VERIFIED, citation="")
    ok = SecurityMapping(
        cik=1,
        ticker="AAA",
        status=MappingStatus.MANUAL_VERIFIED,
        citation="accession 0000000001-99-000001",
    )
    assert ok.counts_in_numerator


def test_manual_candidate_without_citation_is_refused_not_promoted() -> None:
    mapping = resolve_mapping(
        1, [MappingCandidate("AAA", MappingEvidence.MANUAL_FILING_CITATION, citation="")]
    )
    assert mapping.status is MappingStatus.AMBIGUOUS


# ---------------------------------------------------------------------------
# denominator aggregation
# ---------------------------------------------------------------------------


def _denominator() -> Denominator:
    evidence = [
        # confirmed delisting + deregistration -> extinguished
        _ev(1, "8-A12B", dt.date(1996, 1, 5)),
        _ev(1, "10-K", dt.date(1999, 3, 1)),
        _ev(1, "25", dt.date(2010, 5, 3)),
        _ev(1, "15-12B", dt.date(2010, 5, 20)),
        # deregistration only
        _ev(2, "8-A12B", dt.date(1999, 6, 1)),
        _ev(2, "10-K", dt.date(2000, 3, 1)),
        _ev(2, "15-12G", dt.date(2001, 2, 14)),
        # cessation candidate, undated
        _ev(3, "8-A12B", dt.date(1999, 11, 1)),
        _ev(3, "10-K", dt.date(2000, 3, 1)),
        # still filing
        _ev(4, "8-A12B", dt.date(1995, 1, 3)),
        _ev(4, "10-K", dt.date(2025, 3, 1)),
    ]
    timelines = build_timelines(evidence)
    resolutions = [resolve_exit(t, as_of=dt.date(2026, 1, 1)) for t in timelines.values()]
    return Denominator(
        resolutions=resolutions,
        timelines=timelines,
        mappings={
            1: SecurityMapping(
                1, "AAA", MappingStatus.RESOLVED, MappingEvidence.FILING_DOCUMENT_TEXT
            ),
            2: SecurityMapping(2, None, MappingStatus.UNRESOLVED),
            3: SecurityMapping(3, None, MappingStatus.AMBIGUOUS),
        },
    )


def test_counts_split_by_evidence_type_and_strength() -> None:
    denominator = _denominator()
    by_type = denominator.counts_by_evidence_type()
    assert by_type[str(EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED)] == 1
    assert by_type[str(EvidenceType.CONFIRMED_REGISTRATION_TERMINATION)] == 1
    assert by_type[str(EvidenceType.POSSIBLE_EXIT_FILING_CESSATION)] == 1
    assert by_type[str(EvidenceType.UNRESOLVED_EXIT)] == 1

    by_strength = denominator.counts_by_strength()
    assert by_strength[str(EvidenceStrength.CESSATION_ONLY)] == 1
    assert by_strength[str(EvidenceStrength.FORM_DIRECT)] == 1
    assert by_strength[str(EvidenceStrength.FORM_INFERRED)] == 1


def test_undated_exits_are_counted_and_not_placed_in_a_year() -> None:
    denominator = _denominator()
    years = denominator.counts_by_year()
    assert sum(years.values()) == 2  # the two confirmed ones
    assert denominator.undated_exits() == 2  # cessation + unresolved


def test_coverage_reports_two_bounds_that_differ() -> None:
    denominator = _denominator()
    bounds = denominator.coverage(["AAA"])
    assert bounds.matched_numerator == 1
    assert bounds.resolved_denominator == 1
    assert bounds.full_denominator == 4
    assert bounds.matched_coverage == pytest.approx(1.0)
    assert bounds.bounded_coverage == pytest.approx(0.25)
    # The gap is the identity-mapping uncertainty, reported rather than hidden.
    assert bounds.uncertainty == pytest.approx(0.75)


def test_empty_denominator_returns_none_not_zero() -> None:
    bounds = CoverageBounds(matched_numerator=0, resolved_denominator=0, full_denominator=0)
    assert bounds.matched_coverage is None
    assert bounds.bounded_coverage is None


def test_evidence_from_rows_drops_irrelevant_forms() -> None:
    rows = list(parse_full_index(INDEX_BODY, quarter_label="1998-QTR4"))
    kept = evidence_from_rows(rows)
    # 10-K (periodic) and 15-12G (direct) survive; S-1 is not lifecycle evidence.
    assert {e.form_type for e in kept} == {"10-K", "15-12G"}


# ---------------------------------------------------------------------------
# classification: the top grade is unreachable for now
# ---------------------------------------------------------------------------


def test_research_grade_threshold_is_deliberately_unset() -> None:
    assert RESEARCH_GRADE_THRESHOLD is None


def test_perfect_controls_and_high_coverage_still_do_not_grant_research_grade() -> None:
    """Thirty controls are a stress test, not proof of global completeness."""
    bounds = CoverageBounds(matched_numerator=95, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(bounds, controls_passed=30, controls_total=30)
    assert result.assigned is not SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE
    assert result.assigned is SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED
    assert "deliberately unset" in result.reason


def test_research_grade_becomes_reachable_only_when_a_threshold_is_chosen() -> None:
    bounds = CoverageBounds(matched_numerator=95, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(
        bounds, controls_passed=30, controls_total=30, research_grade_threshold=0.9
    )
    assert result.assigned is SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE


def test_failing_controls_blocks_research_grade_even_with_a_threshold() -> None:
    bounds = CoverageBounds(matched_numerator=99, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(
        bounds, controls_passed=29, controls_total=30, research_grade_threshold=0.5
    )
    assert result.assigned is SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED


@pytest.mark.parametrize(
    ("matched", "expected"),
    [
        (60, SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED),
        (30, SurvivorshipClass.PARTIALLY_SURVIVORSHIP_CORRECTED),
        (10, SurvivorshipClass.SURVIVOR_BIASED),
    ],
)
def test_lower_classes_are_assigned_from_the_pessimistic_bound(
    matched: int, expected: SurvivorshipClass
) -> None:
    bounds = CoverageBounds(
        matched_numerator=matched, resolved_denominator=matched, full_denominator=100
    )
    result = classify_corpus(bounds, controls_passed=30, controls_total=30)
    assert result.assigned is expected


def test_classification_of_an_empty_denominator_is_a_refusal() -> None:
    bounds = CoverageBounds(0, 0, 0)
    result: Classification = classify_corpus(bounds, controls_passed=0, controls_total=30)
    assert result.assigned is None


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------


def test_control_universe_is_thirty_securities() -> None:
    assert len(CONTROL_UNIVERSE) == 30
    assert len({c.ticker for c in CONTROL_UNIVERSE}) == 30


def test_no_control_carries_a_fabricated_cik() -> None:
    """sec.gov is unreachable here, so every CIK must still be absent."""
    assert all(c.mapping.cik is None for c in CONTROL_UNIVERSE)
    assert all(c.mapping.status is not MappingStatus.MANUAL_VERIFIED for c in CONTROL_UNIVERSE)


def test_milestone_0b_reports_itself_incomplete() -> None:
    assert len(unverified()) == 30


def test_the_fixture_covers_the_failure_modes_it_claims_to() -> None:
    classes = " ".join(c.control_class for c in CONTROL_UNIVERSE)
    for needed in ("ticker reuse", "reverse split", "short-lived", "peak acquisition"):
        assert needed in classes
    reuse = [c for c in CONTROL_UNIVERSE if "ticker reuse" in c.control_class]
    assert len(reuse) == 3
    short_lived = [c for c in CONTROL_UNIVERSE if "short-lived" in c.control_class]
    assert len(short_lived) == 4


# ---------------------------------------------------------------------------
# audit-paths accounting
# ---------------------------------------------------------------------------


def _write_index(root: Path, quarter: str, rows: list[str]) -> None:
    target = root / quarter.split("-")[0] / quarter.split("-")[1]
    target.mkdir(parents=True)
    header = [
        "Form Type   Company Name" + " " * 39 + "CIK         Date Filed  File Name",
        "-" * 100,
    ]
    (target / "form.idx").write_text("\n".join(header + rows) + "\n", encoding="latin-1")


def test_audit_counts_occurrences_not_distinct_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A File Name on two index lines is not a missing row.

    The audit once accumulated raw paths into a set and compared that against a
    list of parsed rows, which manufactures a shortfall proportional to the
    number of legitimately repeated File Names. Occurrences, distinct values and
    duplicates are three different numbers and are now reported as three.
    """
    _write_index(
        tmp_path,
        "2002-QTR1",
        [
            "10-K        IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
            "10-K405     IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
            "8-K         SOME OTHER CO" + " " * 38 + "1234567     2002-02-01  "
            "edgar/data/1234567/0000000000-02-000001.txt",
        ],
    )
    args = argparse.Namespace(index_root=str(tmp_path))
    assert cmd_audit_paths(args) == 0
    out = capsys.readouterr().out

    assert "raw File Name OCCURRENCES : 3" in out
    assert "raw File Name DISTINCT    : 2" in out
    assert "duplicate File Names    : 1" in out
    assert "rows parsed               : 3" in out
    assert "paths matching raw exactly: 3" in out
    assert "PATH MISMATCHES           : 0" in out
    # The duplicate is attributed to a quarter and shown with its raw lines, so
    # the reason a File Name repeats is visible rather than asserted.
    assert "2002-QTR1  1" in out
    assert out.count("edgar/data/1100683/0000891618-02-001559.txt") >= 3
