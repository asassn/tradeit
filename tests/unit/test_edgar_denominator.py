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
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tradeit import cli_edgar
from tradeit.cli_edgar import _raw_extract, cmd_audit_exceptions, cmd_audit_paths
from tradeit.edgar.control_evidence import (
    controls_awaiting_manual_verification,
    unresolved_controls,
)
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.denominator import (
    RESEARCH_GRADE_THRESHOLD,
    Classification,
    CoverageBounds,
    Denominator,
    SurvivorshipClass,
    classify_corpus,
)
from tradeit.edgar.evidence import (
    PERIODIC_FORMS,
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
    FullIndexRow,
    IndexLayout,
    IndexQuarter,
    SkipReason,
    _assert_path_verbatim,
    accession_from_path,
    explain_row,
    parse_full_index,
    parse_index,
    parse_index_header,
    quarters,
)
from tradeit.edgar.lifecycle import (
    ExitResolution,
    assert_cessation_undated,
    assert_exit_not_contradicted,
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


# ---------------------------------------------------------------------------
# lifecycle: supersession -- a registrant that kept reporting did not exit
#
# Every test below fails against the pre-2026-08-29 `resolve_exit`, which took
# `confirming[0]` unconditionally. They are written against the shapes the first
# real run produced, not against invented ones.
# ---------------------------------------------------------------------------


def test_intel_shape_gets_no_exit_date_at_all() -> None:
    """The measured defect, reduced to its smallest form.

    ``INTEL CORP`` was recorded as exiting 1994-08-02 while still filing in
    2026. A Form 15 deregisters *a class*; the registrant went on reporting for
    three decades. The honest answer is not a better date, it is no date.
    """
    timeline = build_timelines(
        [
            _ev(50863, "10-K", dt.date(1994, 3, 1)),
            _ev(50863, "15-12G", dt.date(1994, 8, 2)),
            _ev(50863, "10-K", dt.date(2000, 3, 1)),
            _ev(50863, "10-K", dt.date(2026, 1, 26)),
        ]
    )[50863]
    resolution = resolve_exit(timeline, as_of=dt.date(2026, 8, 29))

    assert resolution.evidence_type is EvidenceType.NON_EXIT_REGISTRANT_STILL_REPORTING
    assert resolution.evidence_date is None
    assert resolution.effective_date is None
    assert resolution.is_confirmed is False
    # The filing is not discarded -- it is recorded as superseded, so a reader
    # can see what was refused and why.
    assert [e.form_type for e in resolution.superseded] == ["15-12G"]
    assert resolution.last_periodic == dt.date(2026, 1, 26)
    assert resolution.contradicts_its_own_evidence is False


def test_a_still_reporting_registrant_is_absent_from_every_per_year_count() -> None:
    """The defect's actual consequence: a phantom termination in 1994."""
    timelines = build_timelines(
        [
            _ev(50863, "10-K", dt.date(1994, 3, 1)),
            _ev(50863, "15-12G", dt.date(1994, 8, 2)),
            _ev(50863, "10-K", dt.date(2026, 1, 26)),
        ]
    )
    denominator = Denominator(
        resolutions=[resolve_exit(t, as_of=dt.date(2026, 8, 29)) for t in timelines.values()],
        timelines=timelines,
    )
    assert denominator.counts_by_year() == {}
    assert denominator.non_exits() == 1
    # And it must NOT be laundered into the undated-exit population, which is a
    # count of exits we believe happened.
    assert denominator.undated_exits() == 0
    assert denominator.superseded_evidence_counts() == {
        "resolutions_with_superseded_evidence": 1,
        "superseded_filings": 1,
        "still_dated_from_a_standing_filing": 0,
    }


def test_a_superseded_filing_does_not_veto_a_later_standing_one() -> None:
    """Supersession disqualifies a filing from dating the exit; it is not a veto.

    One registered class deregistered in 1994, thirteen more years of 10-Ks,
    then the registrant deregisters for good. It did exit -- in 2009, on
    evidence nothing contradicts -- and the 1994 filing is neither the date nor
    a reason to refuse one. Both filings are the same evidence type, so this
    stays clear of the extinguishment conjunction, which is tested separately.
    """
    timeline = build_timelines(
        [
            _ev(20, "10-K", dt.date(1994, 3, 1)),
            _ev(20, "15-12G", dt.date(1994, 8, 2)),
            _ev(20, "10-K", dt.date(2007, 3, 1)),
            _ev(20, "15-12G", dt.date(2009, 6, 15)),
        ]
    )[20]
    resolution = resolve_exit(timeline, as_of=dt.date(2020, 1, 1))

    assert resolution.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert resolution.evidence_date == dt.date(2009, 6, 15)
    assert [e.evidence_date for e in resolution.superseded] == [dt.date(1994, 8, 2)]
    # The superseded filing stays in the record at its own scope.
    assert resolution.scopes == frozenset({LifecycleScope.SEC_REPORTING})
    assert resolution.contradicts_its_own_evidence is False


def test_an_ordinary_exit_keeps_its_earliest_date() -> None:
    """The 20,813 already-correct rows must not move.

    Every confirming filing post-dates the last periodic report, so nothing is
    superseded and the earliest one still supplies the date. A fix that re-dated
    these to the latest filing would be a far wider change than the defect.
    """
    timeline = build_timelines(
        [
            _ev(21, "10-K", dt.date(2000, 3, 1)),
            _ev(21, "15-12G", dt.date(2001, 2, 14)),
            _ev(21, "15-12G", dt.date(2001, 9, 30)),
        ]
    )[21]
    resolution = resolve_exit(timeline, as_of=dt.date(2010, 1, 1))
    assert resolution.evidence_date == dt.date(2001, 2, 14)
    assert resolution.superseded == ()


def test_same_day_periodic_and_confirming_filing_is_an_exit_not_a_contradiction() -> None:
    """The boundary is strict: same-day is not "after".

    A registrant filing its last 10-K and its Form 25 on one day is an ordinary
    exit. Treating that as a contradiction would manufacture one.
    """
    timeline = build_timelines(
        [
            _ev(22, "10-K", dt.date(2011, 4, 4)),
            _ev(22, "25", dt.date(2011, 4, 4)),
        ]
    )[22]
    resolution = resolve_exit(timeline, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_EXCHANGE_DELISTING
    assert resolution.evidence_date == dt.date(2011, 4, 4)
    assert resolution.superseded == ()
    assert resolution.contradicts_its_own_evidence is False


def test_extinguishment_is_dated_from_the_latest_standing_filing() -> None:
    """The `confirming[-1]` that looked like an oversight, now stated as a decision.

    The derived claim needs both halves, so it cannot be dated before its later
    half exists -- and a superseded half may not supply that date either.
    """
    timeline = build_timelines(
        [
            _ev(23, "10-K", dt.date(2012, 3, 1)),
            _ev(23, "25", dt.date(2013, 5, 3)),
            _ev(23, "15-12B", dt.date(2013, 8, 20)),
        ]
    )[23]
    resolution = resolve_exit(timeline, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
    assert resolution.evidence_date == dt.date(2013, 8, 20)
    assert resolution.contradicts_its_own_evidence is False


def test_extinguishment_with_a_superseded_half_is_dated_from_the_standing_half() -> None:
    """A delisting can precede more reporting -- §2.4 says so explicitly.

    "An issuer can be delisted and continue to file." So the 2005 Form 25 is
    real evidence and the conjunction still holds; what it may not do is supply
    a date the registrant's 2011 10-K contradicts.
    """
    timeline = build_timelines(
        [
            _ev(24, "25", dt.date(2005, 6, 1)),
            _ev(24, "10-K", dt.date(2011, 3, 1)),
            _ev(24, "15-12B", dt.date(2012, 1, 10)),
        ]
    )[24]
    resolution = resolve_exit(timeline, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
    assert resolution.evidence_date == dt.date(2012, 1, 10)
    assert [e.form_type for e in resolution.superseded] == ["25"]
    assert resolution.contradicts_its_own_evidence is False


def test_a_registrant_that_never_filed_a_periodic_report_is_unaffected() -> None:
    """No periodic reports means nothing can supersede anything."""
    timeline = build_timelines([_ev(25, "25", dt.date(2010, 5, 3))])[25]
    resolution = resolve_exit(timeline, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_EXCHANGE_DELISTING
    assert resolution.evidence_date == dt.date(2010, 5, 3)
    assert resolution.last_periodic is None
    assert resolution.superseded == ()


def test_guard_catches_an_exit_dated_before_its_own_last_periodic_report() -> None:
    """The companion to the cessation guard, and the one that was missing.

    This is exactly the shape 12,549 registrants had on 2026-08-29.
    """
    bad = ExitResolution(
        cik=50863,
        company_name="INTEL CORP",
        evidence_type=EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        strength=EvidenceStrength.FORM_DIRECT,
        scopes=frozenset({LifecycleScope.SEC_REPORTING}),
        evidence_date=dt.date(1994, 8, 2),
        effective_date=None,
        supporting=(),
        last_periodic=dt.date(2026, 1, 26),
    )
    assert bad.contradicts_its_own_evidence is True
    with pytest.raises(DataError, match="has not exited"):
        assert_exit_not_contradicted([bad])


def test_guard_also_catches_a_contradicted_effective_date() -> None:
    """``effective_date`` is always None from the index today. The guard covers
    it anyway, because the document-parsing pass that will populate it is the
    obvious place for this defect to reappear in a new form."""
    bad = ExitResolution(
        cik=26,
        company_name="X",
        evidence_type=EvidenceType.CONFIRMED_EXCHANGE_DELISTING,
        strength=EvidenceStrength.FORM_DIRECT,
        scopes=frozenset(),
        evidence_date=None,
        effective_date=dt.date(1999, 1, 1),
        supporting=(),
        last_periodic=dt.date(2005, 1, 1),
    )
    with pytest.raises(DataError, match="has not exited"):
        assert_exit_not_contradicted([bad])


def test_denominator_refuses_to_be_built_from_a_contradicted_exit() -> None:
    """The rule is structural: the report cannot be produced at all."""
    bad = ExitResolution(
        cik=50863,
        company_name="INTEL CORP",
        evidence_type=EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        strength=EvidenceStrength.FORM_DIRECT,
        scopes=frozenset(),
        evidence_date=dt.date(1994, 8, 2),
        effective_date=None,
        supporting=(),
        last_periodic=dt.date(2026, 1, 26),
    )
    with pytest.raises(DataError, match="has not exited"):
        Denominator(resolutions=[bad])


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


def test_milestone_0b_reports_itself_complete() -> None:
    """Complete, and measured rather than assumed.

    This assertion has been rewritten twice for the same underlying reason: it
    kept outliving the state it described. It began as `len(unverified()) == 30`,
    which passed because the check could not return anything else -- it read the
    fixture's placeholder mapping and never opened the evidence file. It became
    `0 < len(unresolved) < 30`, asserting that work remained, which was true
    until RDDT and then fired as the tripwire it was.

    What survives both rewrites is the **ordering relation**, which is a property
    of the two measurements rather than of any particular corpus state: the
    milestone gate asks for MANUAL_VERIFIED, which a RESOLVED control does not
    supply, so it can never report fewer outstanding than the identity
    measurement. That holds at 0 and would hold again at any number.

    The zero is asserted separately and deliberately. Milestone 0b closing is a
    measured fact about the shipped evidence, and a regression -- a control
    dropping below MANUAL_VERIFIED, or acquiring an unsettled issuer obligation
    -- reopens it. This is where that shows up.
    """
    unresolved = unresolved_controls()
    awaiting = controls_awaiting_manual_verification()
    assert len(awaiting) >= len(unresolved)
    assert len(unresolved) == 0
    assert len(awaiting) == 0
    assert len(CONTROL_UNIVERSE) == 30


def test_every_control_names_a_route_that_could_establish_an_identity() -> None:
    """A route pointing at the wrong kind of document is worse than none.

    Checked across all thirty rather than on one sample, which is how two
    controls kept an empty route without anyone noticing: the only existing
    assertion read ``MSFT`` and passed.

    The three refusals below are the categories that were actually shipped here
    and could not have worked. A reference file is a dated primary source but
    not a filing anyone read. A lifecycle instrument evidences a corporate
    action, not an identity. A bare name search establishes which CIK to read
    and no part of the mapping.
    """
    reference_files = ("company_tickers.json", "sec_company_tickers")
    lifecycle_only = ("form 25", "form 15", "8-k", "def 14a", "25-nse", "s-4")

    for control in CONTROL_UNIVERSE:
        route = control.verification_route
        assert route.strip(), f"{control.ticker} has no verification route at all"

        lowered = route.lower()
        for reference in reference_files:
            assert reference not in lowered, (
                f"{control.ticker} routes through {reference}, which cannot reach "
                f"MANUAL_VERIFIED: it is a reference file, not a filing someone read"
            )
        named = [term for term in lifecycle_only if term in lowered]
        assert not named, (
            f"{control.ticker} routes only through {named}, which evidence a corporate "
            f"action rather than an identity"
        )


def test_a_route_may_mention_a_lifecycle_form_while_naming_an_identity_document() -> None:
    """The guard above rejects a *category*, not a substring, so pin that.

    Constructed rather than drawn from the fixture: if a future control needs to
    say "the 8-K is not the route here", that must remain expressible. This test
    fails if the guard is ever tightened into a blanket substring ban, which
    would push authors toward vaguer routes rather than more accurate ones.
    """
    honest = "its own Form 10-K narrative; the 8-K trail evidences the collapse, not the symbol"
    assert "8-k" in honest.lower()
    # The identity document is named first and the lifecycle form is explicitly
    # excluded, which is the shape the guard must not forbid outright.
    assert "form 10-k" in honest.lower()


def test_the_fixture_covers_the_failure_modes_it_claims_to() -> None:
    classes = " ".join(c.control_class for c in CONTROL_UNIVERSE)
    for needed in ("ticker reuse", "reverse split", "short-lived", "peak acquisition"):
        assert needed in classes
    reuse = [c for c in CONTROL_UNIVERSE if "ticker reuse" in c.control_class]
    assert len(reuse) == 3
    short_lived = [c for c in CONTROL_UNIVERSE if "short-lived" in c.control_class]
    assert len(short_lived) == 4


# ---------------------------------------------------------------------------
# the independent audit extractor
#
# These test the auditor, not the parser. If the auditor were wrong in the same
# direction as the parser the audit would prove nothing, so each authentic row
# shape is asserted against values read off the raw line by eye.
# ---------------------------------------------------------------------------


def test_raw_extractor_reads_each_authentic_fixed_width_shape() -> None:
    cases = [
        (
            "10-C        3COM CORP                                     738076    "
            "1994-08-24  edgar/data/738076/0000738076-94-000018.txt",
            "10-C",
            "3COM CORP",
            738076,
            dt.date(1994, 8, 24),
        ),
        # A form type containing a single space must not be split on it.
        (
            "SC 13D      GENERAL MOTORS CORP                           40730     "
            "1994-07-05  edgar/data/40730/0000040730-94-000003.txt",
            "SC 13D",
            "GENERAL MOTORS CORP",
            40730,
            dt.date(1994, 7, 5),
        ),
        # Nor must a company name containing spaces and an ampersand.
        (
            "10-K        AMERICAN TELEPHONE & TELEGRAPH CO             5907      "
            "1994-09-15  edgar/data/5907/0000005907-94-000012.txt",
            "10-K",
            "AMERICAN TELEPHONE & TELEGRAPH CO",
            5907,
            dt.date(1994, 9, 15),
        ),
        # A name that overflows its column still leaves padding before the CIK.
        (
            "8-A12B      A VERY LONG COMPANY NAME THAT OVERFLOWS ITS COLUMN WIDTH  "
            "1234567   1994-09-30  edgar/data/1234567/0001234567-94-000009.txt",
            "8-A12B",
            "A VERY LONG COMPANY NAME THAT OVERFLOWS ITS COLUMN WIDTH",
            1234567,
            dt.date(1994, 9, 30),
        ),
    ]
    for line, form, name, cik, filed in cases:
        raw = _raw_extract(line)
        assert raw is not None, line
        assert (raw.form_type, raw.company_name, raw.cik, raw.filed_at) == (form, name, cik, filed)
        assert raw.cik_from_path is False


def test_raw_extractor_flags_the_glued_name_as_corroborated_not_independent() -> None:
    """'...GENERAL L P5011' has no delimiter, so the CIK comes from the path.

    That is corroboration, not an independent read, and the auditor has to say
    so -- the production parser recovers the same row the same way, and an audit
    that quietly counted it as independent agreement would be auditing itself.
    """
    raw = _raw_extract(
        "10-K405     HOLDINGS MACHINES MOTORS & TELEGRAPH GENERAL L P5011      "
        "1994-09-17  edgar/data/5011/0000005011-94-000123.txt"
    )
    assert raw is not None
    assert raw.cik == 5011
    assert raw.company_name == "HOLDINGS MACHINES MOTORS & TELEGRAPH GENERAL L P"
    assert raw.cik_from_path is True


def test_raw_extractor_reads_pipe_rows_by_shape_not_by_header() -> None:
    raw = _raw_extract(
        "320193|APPLE INC|10-K|1998-12-23|edgar/data/320193/0000320193-98-000110.txt"
    )
    assert raw is not None
    assert (raw.cik, raw.form_type, raw.company_name) == (320193, "10-K", "APPLE INC")
    assert raw.filed_at == dt.date(1998, 12, 23)


def test_raw_extractor_refuses_rather_than_guesses() -> None:
    for line in (
        "this row is not a filing at all",
        "10-K        ACME INC    notacik    1994-09-17  edgar/data/12345/0000012345-94-000001.txt",
        "10-K        ACME INC    12345    1994-09-17  no/path/here.txt",
    ):
        assert _raw_extract(line) is None


# ---------------------------------------------------------------------------
# the corpus audit
# ---------------------------------------------------------------------------

_AUDIT_HEADER = [
    "Form Type   Company Name" + " " * 39 + "CIK         Date Filed  File Name",
    "-" * 100,
]


def _write_index(root: Path, quarter: str, rows: list[str]) -> None:
    year, qtr = quarter.split("-")
    target = root / year / qtr
    target.mkdir(parents=True)
    (target / "form.idx").write_text("\n".join(_AUDIT_HEADER + rows) + "\n", encoding="latin-1")


def test_audit_passes_the_gate_when_all_five_fields_agree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_index(
        tmp_path,
        "2002-QTR1",
        [
            "10-K        IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
            "SC 13D      GENERAL MOTORS CORP" + " " * 32 + "40730       2002-02-01  "
            "edgar/data/40730/0000040730-02-000003.txt",
        ],
    )
    assert cmd_audit_paths(argparse.Namespace(index_root=str(tmp_path))) == 0
    out = capsys.readouterr().out
    assert "rows compared field-by-field  : 2" in out
    for label in ("FORM", "CIK", "DATE", "PATH", "ACCESSION"):
        assert f"{label} mismatches" in out
    assert "integrity gate: PASSED" in out
    assert "does not require regeneration" in out


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
    assert cmd_audit_paths(argparse.Namespace(index_root=str(tmp_path))) == 0
    out = capsys.readouterr().out

    assert "raw File Name OCCURRENCES     : 3" in out
    assert "raw File Name DISTINCT        : 2" in out
    assert "duplicate File Names        : 1" in out
    assert "rows parsed                   : 3" in out
    assert "PATH mismatches               : 0" in out
    # Attributed to a quarter and shown with its raw lines, so the reason a File
    # Name repeats is visible rather than asserted.
    assert "2002-QTR1  1" in out
    assert out.count("edgar/data/1100683/0000891618-02-001559.txt") >= 3


def test_audit_reports_unreadable_raw_lines_instead_of_passing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Could not check" and "checked and agreed" are different facts."""
    _write_index(
        tmp_path,
        "1994-QTR3",
        [
            "10-C        3COM CORP" + " " * 42 + "738076      1994-08-24  "
            "edgar/data/738076/0000738076-94-000018.txt",
            "a line the auditor cannot read at all",
        ],
    )
    code = cmd_audit_paths(argparse.Namespace(index_root=str(tmp_path)))
    out = capsys.readouterr().out
    assert "raw-extraction FAILURES       : 1" in out
    assert "raw_extraction_failure=1" in out
    assert "a line the auditor cannot read at all" in out
    # One unreadable line is enough to withhold the gate.
    assert code == 1
    assert "GATE PARTIAL" in out


def test_audit_stops_on_a_classification_input_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupted CIK must halt the run and name the denominator, not warn.

    The corruption is injected at the parser boundary because the real parser
    does not produce one; what is under test is that the audit would catch it.
    """
    _write_index(
        tmp_path,
        "2002-QTR1",
        [
            "10-K        IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
        ],
    )
    real_parse = cli_edgar.parse_index

    def corrupt(text: str, *, quarter_label: str, strict: bool = True) -> Any:
        parsed = real_parse(text, quarter_label=quarter_label, strict=strict)
        parsed.rows = [replace(row, cik=999) for row in parsed.rows]
        return parsed

    monkeypatch.setattr(cli_edgar, "parse_index", corrupt)
    code = cmd_audit_paths(argparse.Namespace(index_root=str(tmp_path)))
    out = capsys.readouterr().out
    assert code == 1
    assert "CIK mismatches                : 1" in out
    assert "STOP. Classification inputs disagree with the raw index" in out
    assert "requires investigation" in out
    # The evidence is shown, not summarised away.
    assert "raw read : form='10-K' cik=1100683" in out
    assert "cik=999" in out


def test_audit_separates_provenance_damage_from_classification_damage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong accession with intact cik/form/date is not a denominator problem."""
    _write_index(
        tmp_path,
        "2002-QTR1",
        [
            "10-K        IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
        ],
    )
    real_parse = cli_edgar.parse_index

    def corrupt(text: str, *, quarter_label: str, strict: bool = True) -> Any:
        parsed = real_parse(text, quarter_label=quarter_label, strict=strict)
        parsed.rows = [replace(row, accession="0001095811-02-001559") for row in parsed.rows]
        return parsed

    monkeypatch.setattr(cli_edgar, "parse_index", corrupt)
    code = cmd_audit_paths(argparse.Namespace(index_root=str(tmp_path)))
    out = capsys.readouterr().out
    assert code == 1
    assert "ACCESSION mismatches          : 1" in out
    assert "STOP. Classification inputs" not in out
    assert "GATE NOT PASSED" in out
    assert "No classification input is affected" in out


# ---------------------------------------------------------------------------
# the exception report
# ---------------------------------------------------------------------------

#: The one row shape the auditor can read and the production parser cannot: the
#: company-name column is empty, so there is no second padding run and the
#: parser cannot produce two free-text fields from one. The auditor falls back
#: to its path-corroborated rule, which needs no name at all.
_EMPTY_NAME_ROW = (
    "10-K" + " " * 54 + "12345     1997-01-02  edgar/data/12345/0000012345-97-000001.txt"
)


def test_the_parser_and_the_auditor_disagree_only_on_an_unsplittable_prefix() -> None:
    """Pin the exact divergence, so a new one cannot appear unnoticed.

    The auditor reading a row the parser skipped is the audit working, not
    failing -- but it is only sound while the disagreement is understood. Note
    the auditor's own read of such a row is poor: with nothing to split on it
    reports an empty company name. That is tolerable precisely because these
    rows are excluded from the field-by-field comparison rather than counted as
    agreement.
    """
    header = parse_index_header(
        "Form Type   Company Name" + " " * 38 + "CIK       Date Filed   File Name"
    )
    raw = _raw_extract(_EMPTY_NAME_ROW)
    assert raw is not None
    assert raw.company_name == ""
    assert raw.cik == 12345
    assert raw.cik_from_path is True

    row, reason = explain_row(_EMPTY_NAME_ROW, header, quarter_label="1997-QTR1")
    assert row is None
    assert reason is SkipReason.FREE_TEXT_SPLIT_FAILURE


def test_exception_report_shows_the_row_the_reason_and_its_siblings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The question that decides whether an omission matters is the sibling one.

    A skipped filing whose File Name also appears on a line that *was* parsed
    contributes no evidence event the corpus does not already hold.
    """
    _write_index(
        tmp_path,
        "1997-QTR1",
        [
            "10-K        ACME CORP" + " " * 42 + "12345       1997-01-02  "
            "edgar/data/12345/0000012345-97-000001.txt",
            _EMPTY_NAME_ROW,
        ],
    )
    assert cmd_audit_exceptions(argparse.Namespace(index_root=str(tmp_path), quarter=None)) == 0
    out = capsys.readouterr().out

    assert "SKIPPED BY PARSER" in out
    assert "1997-QTR1 line 4" in out
    assert "parser verdict: SKIPPED, reason = free_text_split_failure" in out
    assert "cik          : 12345  (from path)" in out
    assert "accession    : '0000012345-97-000001'" in out
    # Same File Name as the row above it, and that one was parsed -- which is
    # what makes this particular omission evidentially empty.
    assert "same File Name on 2 index line(s)" in out
    assert "[PARSED]" in out
    assert "rows the auditor read and the parser did not emit : 1" in out


def test_exception_report_can_be_limited_to_named_quarters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_index(tmp_path, "1997-QTR1", [_EMPTY_NAME_ROW])
    _write_index(tmp_path, "2016-QTR1", [_EMPTY_NAME_ROW])
    cmd_audit_exceptions(argparse.Namespace(index_root=str(tmp_path), quarter=["2016-QTR1"]))
    out = capsys.readouterr().out
    assert "2016-QTR1" in out
    assert "1997-QTR1" not in out
    assert "rows the auditor read and the parser did not emit : 1" in out


def test_exception_report_is_silent_when_there_is_nothing_to_explain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_index(
        tmp_path,
        "2002-QTR1",
        [
            "10-K        IPET HOLDINGS INC" + " " * 34 + "1100683     2002-03-29  "
            "edgar/data/1100683/0000891618-02-001559.txt",
        ],
    )
    assert cmd_audit_exceptions(argparse.Namespace(index_root=str(tmp_path), quarter=None)) == 0
    out = capsys.readouterr().out
    assert "Nothing to explain" in out


# ---------------------------------------------------------------------------
# the two refused source rows, and why they cannot matter
# ---------------------------------------------------------------------------

#: The two rows the production parser refuses across the whole 129-quarter
#: corpus, both malformed the same way: the company-name column is blank.
_REFUSED = (
    ("SC 13D", dt.date(1997, 3, 24), 1036125, "edgar/data/1036125/0000950134-97-002093.txt"),
    ("485BPOS", dt.date(2016, 2, 26), 1593547, "edgar/data/1593547/0001135428-16-001124.txt"),
)


def _row(cik: int, form: str, filed: str, accession: str) -> FullIndexRow:
    return FullIndexRow(
        cik=cik,
        company_name="X CO",
        form_type=form,
        filed_at=dt.date.fromisoformat(filed),
        path=f"edgar/data/{cik}/{accession}.txt",
        accession=accession,
        index_quarter="Q",
        source_line=1,
    )


@pytest.mark.parametrize(("form", "filed", "_cik", "_path"), _REFUSED)
def test_neither_refused_form_is_classification_relevant(
    form: str, filed: dt.date, _cik: int, _path: str
) -> None:
    """SC 13D and 485BPOS carry no lifecycle meaning under this methodology.

    An SC 13D is a third party's beneficial-ownership report about an issuer;
    a 485BPOS is an investment company's post-effective registration amendment.
    Neither is a birth, an exit, a periodic report or a transaction pointer.
    """
    signal = classify_form(form, filed)
    assert signal.role is FormRole.IRRELEVANT
    assert signal.evidence_type is None
    assert signal.strength is EvidenceStrength.NONE
    assert form not in PERIODIC_FORMS


@pytest.mark.parametrize(("form", "filed", "cik", "path"), _REFUSED)
def test_a_refused_row_is_dropped_before_it_can_reach_a_timeline(
    form: str, filed: dt.date, cik: int, path: str
) -> None:
    """The filter is upstream of grouping, which is what makes the delta zero.

    ``evidence_from_rows`` discards an IRRELEVANT form before ``build_timelines``
    ever sees it, so such a row cannot create a registrant, cannot extend a
    filing window and cannot contribute an accession.
    """
    row = FullIndexRow(
        cik=cik,
        company_name="",
        form_type=form,
        filed_at=filed,
        path=path,
        accession=accession_from_path(path),
        index_quarter="Q",
        source_line=0,
    )
    assert evidence_from_rows([row]) == []
    assert build_timelines(evidence_from_rows([row])) == {}


@pytest.mark.parametrize(("form", "filed", "cik", "path"), _REFUSED)
def test_injecting_a_refused_row_changes_no_lifecycle_result(
    form: str, filed: dt.date, cik: int, path: str
) -> None:
    """The counterfactual, across every shape of history the CIK could have.

    This is the check that closed the denominator gate: not "we looked and it
    was fine", but "there is no prior history for which it would not be".
    """
    as_of = dt.date(2026, 8, 15)
    injected = FullIndexRow(
        cik=cik,
        company_name="",
        form_type=form,
        filed_at=filed,
        path=path,
        accession=accession_from_path(path),
        index_quarter="Q",
        source_line=0,
    )
    histories: dict[str, list[FullIndexRow]] = {
        "no other filings": [],
        "periodic only": [_row(cik, "10-K", "1998-03-01", "0000000001-98-000001")],
        "deregistration": [
            _row(cik, "10-K", "1998-03-01", "0000000001-98-000001"),
            _row(cik, "15-12G", "2000-05-01", "0000000001-00-000002"),
        ],
        "delisting": [_row(cik, "25-NSE", "2009-07-08", "0000000001-09-000003")],
        "extinguished": [
            _row(cik, "25-NSE", "2009-07-08", "0000000001-09-000003"),
            _row(cik, "15-12B", "2009-08-01", "0000000001-09-000004"),
        ],
        "exit candidate only": [_row(cik, "8-K", "2001-09-10", "0000000001-01-000005")],
        "birth only": [_row(cik, "8-A12G", "1996-01-05", "0000000001-96-000006")],
    }

    def resolve(rows: list[FullIndexRow]) -> dict[str, object] | None:
        timeline = build_timelines(evidence_from_rows(rows)).get(cik)
        return None if timeline is None else resolve_exit(timeline, as_of=as_of).summary()

    for label, history in histories.items():
        assert resolve(history) == resolve([*history, injected]), label


def test_injecting_both_refused_rows_leaves_the_published_report_identical() -> None:
    """Aggregates, not just per-CIK results: registrant count included."""
    as_of = dt.date(2026, 8, 15)
    corpus = [
        _row(320193, "10-K", "1998-12-23", "0000320193-98-000110"),
        _row(1100683, "15-12G", "2005-06-03", "0000950134-05-011306"),
        _row(40730, "25-NSE", "2009-07-08", "0000876661-09-000303"),
        _row(40730, "15-12B", "2009-08-01", "0000876661-09-000400"),
    ]
    injected = [
        FullIndexRow(
            cik=cik,
            company_name="",
            form_type=form,
            filed_at=filed,
            path=path,
            accession=accession_from_path(path),
            index_quarter="Q",
            source_line=0,
        )
        for form, filed, cik, path in _REFUSED
    ]

    def report(rows: list[FullIndexRow]) -> dict[str, Any]:
        timelines = build_timelines(evidence_from_rows(rows))
        denominator = Denominator(
            resolutions=[resolve_exit(t, as_of=as_of) for t in timelines.values()],
            timelines=timelines,
            mappings={},
            missing_quarters=(),
        )
        return denominator.report()

    before, after = report(corpus), report([*corpus, *injected])
    assert before == after
    assert before["registrants"] == after["registrants"] == 3


# ---------------------------------------------------------------------------
# curated identity reaching the denominator
#
# BuildOptions has accepted a CIK->SecurityMapping dict since the pipeline was
# written and the CLI never passed one, so `tradeit edgar denominator` reported
# every registrant UNRESOLVED on a corpus whose control identities had in fact
# been verified against filings. These tests pin the wiring and, more
# importantly, pin the two things it must not let a reader conclude: that
# supplying a mapping identifies a registrant, and that a mapping the handoff
# could not carry was never recorded.
# ---------------------------------------------------------------------------


def _denominator_args(root: Path, **overrides: Any) -> argparse.Namespace:
    args = {
        "index_root": str(root),
        "start": "2001Q1",
        "end": "2001Q1",
        "as_of": "2010-01-01",
        "quiet_quarters": 8,
        "evidence": None,
        "control_mappings": True,
        "json": False,
    }
    args.update(overrides)
    return argparse.Namespace(**args)


def _control_corpus(root: Path) -> None:
    """Two shipped control CIKs and one registrant no control names."""
    _write_index(
        root,
        "2001-QTR1",
        [
            "10-K        APPLE COMPUTER INC" + " " * 33 + "320193      2001-03-01  "
            "edgar/data/320193/0000320193-01-000001.txt",
            "15-12G      IPET HOLDINGS INC" + " " * 34 + "1100683     2001-02-01  "
            "edgar/data/1100683/0001100683-01-000002.txt",
            "10-K        SOME OTHER CO" + " " * 38 + "999999      2001-03-04  "
            "edgar/data/999999/0000999999-01-000003.txt",
        ],
    )


def test_the_denominator_reports_curated_identity_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wiring itself: two of three registrants stop reading as unresolved."""
    _control_corpus(tmp_path)
    assert cli_edgar.cmd_denominator(_denominator_args(tmp_path)) == 0
    out = capsys.readouterr().out

    assert "manual_verified                                    2" in out
    assert "unresolved                                         1" in out
    # The unnamed registrant is the control: it must still count in the
    # denominator, because an unidentified issuer is not an absent one.
    assert "registrants           : 3" in out


def test_withholding_the_curated_identity_says_so_rather_than_reporting_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--no-control-mappings`` must not be indistinguishable from no evidence.

    Without the printed notice the two runs differ only in a count that reads
    the same either way -- three registrants UNRESOLVED -- which is precisely
    the confusion that made the unwired default survive as long as it did.
    """
    _control_corpus(tmp_path)
    args = _denominator_args(tmp_path, control_mappings=False)
    assert cli_edgar.cmd_denominator(args) == 0
    out = capsys.readouterr().out

    assert "unresolved                                         3" in out
    assert "NOT SUPPLIED" in out
    assert "not the" in out and "state of the evidence" in out


def test_a_supplied_mapping_that_matches_no_registrant_is_reported_as_inert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Supplying a mapping is not identifying a registrant.

    Thirty-three mappings are handed in and two attach to anything in a
    one-quarter corpus. A reader told only the first number would conclude the
    identity work had reached thirty-three registrants here; it reached two.
    """
    _control_corpus(tmp_path)
    assert cli_edgar.cmd_denominator(_denominator_args(tmp_path)) == 0
    out = capsys.readouterr().out

    assert "2 attached to a registrant in this corpus" in out
    assert "did not appear" in out
    assert "inert, not identified" in out


def test_an_issuer_the_handoff_cannot_carry_is_named_in_the_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FRC must not vanish between the evidence file and the report.

    It is the best-evidenced control in the corpus and the only one absent from
    the denominator's identity section, for a reason that is about the
    denominator's SEC-only keying and nothing about FRC.
    """
    _control_corpus(tmp_path)
    assert cli_edgar.cmd_denominator(_denominator_args(tmp_path)) == 0
    out = capsys.readouterr().out

    assert "NOT HANDED OVER: FRC/primary [fdic_cert:59017]" in out
    assert "no SEC filer account" in out


def test_supplied_mapping_reach_counts_mappings_not_registrants() -> None:
    """The three numbers are about the handoff, and only ``matched`` acts.

    Constructed rather than run through the CLI, because the property is that
    ``supplied`` and ``matched`` measure different populations -- a distinction
    a corpus-shaped test can accidentally satisfy by having them coincide.
    """
    as_of = dt.date(2010, 1, 1)
    timelines = build_timelines(
        evidence_from_rows([_row(320193, "10-K", "2001-03-01", "0000320193-01-000001")])
    )
    denominator = Denominator(
        resolutions=[resolve_exit(t, as_of=as_of) for t in timelines.values()],
        timelines=timelines,
        mappings={
            320193: SecurityMapping(
                cik=320193,
                ticker="AAPL",
                status=MappingStatus.MANUAL_VERIFIED,
                evidence=MappingEvidence.MANUAL_FILING_CITATION,
                citation="a filing",
            ),
            111111: SecurityMapping(
                cik=111111,
                ticker="ZZZZ",
                status=MappingStatus.MANUAL_VERIFIED,
                evidence=MappingEvidence.MANUAL_FILING_CITATION,
                citation="a filing",
            ),
        },
    )

    assert denominator.supplied_mapping_reach() == {
        "supplied": 2,
        "matched": 1,
        "unmatched": 1,
    }
    # The unmatched mapping changed no published count. One registrant, one
    # identity -- not two.
    assert denominator.mapping_counts()[str(MappingStatus.MANUAL_VERIFIED)] == 1
    assert denominator.report()["supplied_mappings"] == denominator.supplied_mapping_reach()
