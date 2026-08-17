"""Reading one submission's envelope, and refusing the wrong filing.

**The bug these tests exist to prevent.** A hand-written validation regex
captured the submission type with ``\\S+``, which stops at the first space, so
``DEF 14A`` arrived as ``DEF`` and the gate refused a filing that was exactly
the one requested. It survived unnoticed because every form validated before it
-- ``10-K``, ``10-Q``, ``8-A12B`` -- is a single token. Four of thirty-four
locally stored submissions carried the same truncation once it was looked for.

So the multi-word cases below are not decoration. Each of ``DEF 14A``,
``PRE 14A``, ``POS AM``, ``SC 13D`` and ``SC 13G`` fails under ``\\S+`` and must
pass here, and ``10-K`` guards the single-token path that always worked.

The second half of the file pins what the fix must NOT do: no alias, no prefix
acceptance, and no merging of the three separate statements a submission makes
about what it is.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.edgar.submission import (
    SubmissionHeader,
    header_mismatches,
    parse_submission_header,
    split_documents,
)

MULTI_WORD_FORMS = ["DEF 14A", "PRE 14A", "POS AM", "SC 13D", "SC 13G"]


def _submission(
    *,
    form: str = "DEF 14A",
    accession: str = "0001047469-08-002261",
    filed: str = "20080305",
    cik: str = "0000806085",
    name: str = "LEHMAN BROTHERS HOLDINGS INC",
    period: str = "20080415",
    extra_filers: str = "",
    documents: str = "",
) -> str:
    """A submission shaped like the real thing: tab-separated, padded, SGML."""
    return f"""<SEC-DOCUMENT>{accession}.txt : {filed}
<SEC-HEADER>{accession}.hdr.sgml : {filed}
<ACCEPTANCE-DATETIME>{filed}164357
ACCESSION NUMBER:\t\t{accession}
CONFORMED SUBMISSION TYPE:\t{form}
PUBLIC DOCUMENT COUNT:\t\t6
CONFORMED PERIOD OF REPORT:\t{period}
FILED AS OF DATE:\t\t{filed}
DATE AS OF CHANGE:\t\t{filed}

FILER:

\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\t{name}
\t\tCENTRAL INDEX KEY:\t\t\t{cik}
\t\tIRS NUMBER:\t\t\t\t133216325

\tFILING VALUES:
\t\tFORM TYPE:\t\t{form}
\t\tSEC FILE NUMBER:\t001-09466
{extra_filers}
</SEC-HEADER>
{documents}"""


_DOCUMENTS = """<DOCUMENT>
<TYPE>DEF 14A
<SEQUENCE>1
<FILENAME>a2183244zdef14a.htm
<DESCRIPTION>DEF 14A
<TEXT>
<html><body><p>Proxy Statement Pursuant to Section 14(a)</p></body></html>
</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>GRAPHIC
<SEQUENCE>2
<FILENAME>g393881.jpg
<DESCRIPTION>G393881.JPG
<TEXT>
begin 644 g393881.jpg
</TEXT>
</DOCUMENT>
"""


# ---------------------------------------------------------------------------
# the truncation bug itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", [*MULTI_WORD_FORMS, "10-K"])
def test_the_whole_submission_type_survives(form: str) -> None:
    """Every character of the form reaches the caller, space included."""
    header = parse_submission_header(_submission(form=form))
    assert header.submission_type == form


@pytest.mark.parametrize("form", MULTI_WORD_FORMS)
def test_a_first_token_reader_would_have_failed_these(form: str) -> None:
    """The cases are chosen because the old extractor mangles them.

    If this ever stops holding, the parametrisation has drifted onto forms that
    could not have caught the bug, and the suite is guarding nothing.
    """
    assert form.split()[0] != form
    header = parse_submission_header(_submission(form=form))
    assert header.submission_type != form.split()[0]
    assert header.submission_type == form


def test_tabs_and_padding_do_not_reach_the_value() -> None:
    """Real headers are tab-padded; the value is the form, not the layout."""
    header = parse_submission_header(_submission(form="DEF 14A"))
    assert header.submission_type == "DEF 14A"
    assert "\t" not in (header.submission_type or "")
    assert (header.submission_type or "").strip() == header.submission_type


def test_internal_whitespace_is_normalised_without_renaming() -> None:
    """A pathological separator collapses to one space and nothing else."""
    raw = _submission(form="DEF 14A").replace(
        "CONFORMED SUBMISSION TYPE:\tDEF 14A",
        "CONFORMED SUBMISSION TYPE:\tDEF   14A",
    )
    header = parse_submission_header(raw)
    assert header.submission_type == "DEF 14A"


# ---------------------------------------------------------------------------
# the rest of the envelope
# ---------------------------------------------------------------------------


def test_the_dates_stay_three_different_things() -> None:
    """Filed, period of report and date of change are not interchangeable."""
    header = parse_submission_header(_submission())
    assert header.filed_at == dt.date(2008, 3, 5)
    assert header.period_of_report == dt.date(2008, 4, 15)
    assert header.date_of_change == dt.date(2008, 3, 5)
    assert header.filed_at != header.period_of_report


def test_every_filer_block_is_kept() -> None:
    """A co-registrant is not the registrant, and dropping it hides that."""
    extra = """
FILER:

\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\tENRON CAPITAL RESOURCES LP
\t\tCENTRAL INDEX KEY:\t\t\t0000924024
"""
    header = parse_submission_header(_submission(extra_filers=extra))
    assert header.ciks == (806085, 924024)
    assert header.company_names == (
        "LEHMAN BROTHERS HOLDINGS INC",
        "ENRON CAPITAL RESOURCES LP",
    )


def test_zero_padded_ciks_compare_numerically() -> None:
    """``0000806085`` and ``806085`` are the same registrant."""
    header = parse_submission_header(_submission(cik="0000806085"))
    assert header.ciks == (806085,)
    assert 806085 in header.ciks


def test_a_header_only_string_parses() -> None:
    """Callers should not have to hold a whole 700kB submission to check it."""
    whole = _submission(documents=_DOCUMENTS)
    head_only = whole[: whole.find("<DOCUMENT>")]
    assert parse_submission_header(head_only) == parse_submission_header(whole)


def test_document_bodies_cannot_contribute_header_fields() -> None:
    """Text after the first <DOCUMENT> is not the envelope's word."""
    forged = _submission(documents=_DOCUMENTS).replace(
        "<TEXT>\n<html><body><p>Proxy Statement",
        "<TEXT>\nACCESSION NUMBER:\t\t9999999999-99-999999\n<html><body><p>Proxy Statement",
    )
    header = parse_submission_header(forged)
    assert header.accession == "0001047469-08-002261"


def test_missing_fields_are_none_rather_than_invented() -> None:
    header = parse_submission_header("<SEC-HEADER>nothing useful here\n")
    assert header.submission_type is None
    assert header.accession is None
    assert header.filed_at is None
    assert header.ciks == ()


# ---------------------------------------------------------------------------
# documents are not one flat envelope
# ---------------------------------------------------------------------------


def test_documents_are_separate_and_ordered() -> None:
    docs = split_documents(_submission(documents=_DOCUMENTS))
    assert len(docs) == 2
    assert [d.sequence for d in docs] == ["1", "2"]
    assert [d.document_type for d in docs] == ["DEF 14A", "GRAPHIC"]
    assert docs[0].filename == "a2183244zdef14a.htm"
    assert docs[1].description == "G393881.JPG"


def test_a_documents_own_type_also_survives_a_space() -> None:
    """The truncation bug applies to <TYPE> exactly as it did to the header."""
    docs = split_documents(_submission(documents=_DOCUMENTS))
    assert docs[0].document_type == "DEF 14A"
    assert docs[0].document_type.split()[0] == "DEF"


def test_the_three_statements_of_form_are_not_merged() -> None:
    """Index form, envelope type and document type are separate facts.

    Here the envelope says ``DEF 14A`` while document 1 says ``PRE 14A``. That
    disagreement is evidence about the filing, so both values must reach the
    caller unchanged rather than one being silently adopted for the other.
    """
    raw = _submission(documents=_DOCUMENTS).replace("<TYPE>DEF 14A", "<TYPE>PRE 14A")
    header = parse_submission_header(raw)
    docs = split_documents(raw)
    assert header.submission_type == "DEF 14A"
    assert docs[0].document_type == "PRE 14A"
    assert header.submission_type != docs[0].document_type


# ---------------------------------------------------------------------------
# acceptance: exact equality against the verified index row, and nothing looser
# ---------------------------------------------------------------------------


def _expect(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "accession": "0001047469-08-002261",
        "form_type": "DEF 14A",
        "filed_at": dt.date(2008, 3, 5),
        "cik": 806085,
    }
    base.update(overrides)
    return base


def test_the_real_filing_is_accepted() -> None:
    header = parse_submission_header(_submission())
    assert header_mismatches(header, **_expect()) == ()  # type: ignore[arg-type]


@pytest.mark.parametrize("form", MULTI_WORD_FORMS)
def test_multi_word_forms_are_accepted_against_their_index_row(form: str) -> None:
    """The end-to-end shape of the bug: index says X, envelope says X, accept."""
    header = parse_submission_header(_submission(form=form))
    assert header_mismatches(header, **_expect(form_type=form)) == ()  # type: ignore[arg-type]


def test_def_is_never_accepted_for_def_14a() -> None:
    """No alias. The truncated value is a DIFFERENT form and stays refused."""
    header = parse_submission_header(_submission(form="DEF"))
    problems = header_mismatches(header, **_expect(form_type="DEF 14A"))  # type: ignore[arg-type]
    assert len(problems) == 1
    assert "CONFORMED SUBMISSION TYPE" in problems[0]


@pytest.mark.parametrize(
    ("envelope", "expected"),
    [
        ("POS", "POS AM"),
        ("PRE", "PRE 14A"),
        ("SC", "SC 13D"),
        ("DEF 14A", "DEFA14A"),
        ("SC 13G", "SC 13D"),
    ],
)
def test_no_pair_of_distinct_forms_is_ever_equated(envelope: str, expected: str) -> None:
    header = parse_submission_header(_submission(form=envelope))
    assert header_mismatches(header, **_expect(form_type=expected))  # type: ignore[arg-type]


def test_an_amendment_is_not_accepted_for_the_original() -> None:
    """Exact equality, not prefix. ``10-K/A`` answers a different question."""
    header = parse_submission_header(_submission(form="10-K/A"))
    assert header_mismatches(header, **_expect(form_type="10-K"))  # type: ignore[arg-type]
    original = parse_submission_header(_submission(form="10-K"))
    assert header_mismatches(original, **_expect(form_type="10-K")) == ()  # type: ignore[arg-type]


def test_form_comparison_ignores_case_and_padding_only() -> None:
    """Whitespace and case are layout. Everything else is the form's identity."""
    header = parse_submission_header(_submission(form="def 14a"))
    assert header_mismatches(header, **_expect(form_type="DEF 14A")) == ()  # type: ignore[arg-type]


def test_each_wrong_field_is_reported_separately() -> None:
    header = parse_submission_header(
        _submission(form="10-K", accession="0000000000-00-000000", filed="20080306", cik="728586")
    )
    problems = header_mismatches(header, **_expect())  # type: ignore[arg-type]
    assert len(problems) == 4
    joined = " | ".join(problems)
    for token in (
        "ACCESSION NUMBER",
        "CONFORMED SUBMISSION TYPE",
        "FILED AS OF DATE",
        "CIK 806085",
    ):
        assert token in joined


def test_a_co_registrant_does_not_make_the_filing_wrong() -> None:
    """The requested CIK must be present, not alone."""
    extra = """
FILER:

\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\tSOME CO-REGISTRANT LP
\t\tCENTRAL INDEX KEY:\t\t\t0000924024
"""
    header = parse_submission_header(_submission(extra_filers=extra))
    assert header_mismatches(header, **_expect()) == ()  # type: ignore[arg-type]
    assert 924024 in header.ciks


def test_an_empty_header_is_refused_rather_than_defaulted() -> None:
    problems = header_mismatches(SubmissionHeader(None, None, None, None, None), **_expect())  # type: ignore[arg-type]
    assert len(problems) == 4
