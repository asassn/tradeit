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
    KNOWN_ENTITY_ROLES,
    SubmissionHeader,
    cik_roles,
    entities_for_role,
    header_mismatches,
    parse_submission_header,
    split_documents,
    unknown_roles,
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


# ---------------------------------------------------------------------------
# who filed it is not who it is about
#
# Every entity below is invented. Nothing here is evidence about any real
# registrant, and no filing was opened to write these fixtures: the structure
# under test is the header's shape, which is a property of the format rather
# than of any particular company.
# ---------------------------------------------------------------------------

ISSUER = ("EXAMPLE HOLDINGS INC", "0000111111", 111111)
EXCHANGE = ("EXAMPLE EXCHANGE LLC", "0000222222", 222222)
CO_REGISTRANT = ("EXAMPLE FUNDING LP", "0000333333", 333333)


def _nest(
    role: str,
    entity: tuple[str, str, int],
    *,
    data: str = "COMPANY DATA",
    form: str | None = None,
    file_number: str | None = None,
    indent: str = "\t",
    extra: str = "",
) -> str:
    """One role block, shaped the way EDGAR writes them: bare label, then nests."""
    name, cik, _number = entity
    lines = [
        f"{role}:",
        "",
        f"{indent}{data}:",
        f"{indent * 2}COMPANY CONFORMED NAME:\t\t\t{name}",
        f"{indent * 2}CENTRAL INDEX KEY:\t\t\t{cik}",
    ]
    if form is not None or file_number is not None:
        lines.append("")
        lines.append(f"{indent}FILING VALUES:")
        if form is not None:
            lines.append(f"{indent * 2}FORM TYPE:\t\t{form}")
        if file_number is not None:
            lines.append(f"{indent * 2}SEC FILE NUMBER:\t{file_number}")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _envelope(*nests: str, form: str = "25-NSE", top_level: str = "") -> str:
    accession = "0000222222-08-000395"
    filed = "20081015"
    return f"""<SEC-DOCUMENT>{accession}.txt : {filed}
<SEC-HEADER>{accession}.hdr.sgml : {filed}
ACCESSION NUMBER:\t\t{accession}
CONFORMED SUBMISSION TYPE:\t{form}
PUBLIC DOCUMENT COUNT:\t\t1
FILED AS OF DATE:\t\t{filed}
DATE AS OF CHANGE:\t\t{filed}
{top_level}
{"".join(nests)}
</SEC-HEADER>
"""


#: The shape that motivated all of this: one party files, another is the subject.
NOTICE = _envelope(
    _nest("SUBJECT COMPANY", ISSUER, form="25-NSE", file_number="001-09466"),
    _nest("FILED BY", EXCHANGE, form="25-NSE"),
)
NOTICE_REVERSED = _envelope(
    _nest("FILED BY", EXCHANGE, form="25-NSE"),
    _nest("SUBJECT COMPANY", ISSUER, form="25-NSE", file_number="001-09466"),
)
TWO_FILERS = _envelope(
    _nest("FILER", ISSUER, form="S-3"),
    _nest("FILER", CO_REGISTRANT, form="S-3"),
    form="S-3",
)
THREE_ROLES = _envelope(
    _nest("SUBJECT COMPANY", ISSUER),
    _nest("FILED BY", EXCHANGE),
    _nest("FILER", CO_REGISTRANT),
)
OWNERSHIP = _envelope(
    _nest("ISSUER", ISSUER),
    _nest("REPORTING-OWNER", CO_REGISTRANT, data="OWNER DATA"),
    form="4",
)
SPACE_INDENTED = _envelope(
    _nest("SUBJECT COMPANY", ISSUER, form="25-NSE", file_number="001-09466", indent="    "),
    _nest("FILED BY", EXCHANGE, form="25-NSE", indent="    "),
)
STRAY_KEY = _envelope(top_level="CENTRAL INDEX KEY:\t\t\t0000111111")
UNKNOWN_ENTITY_ROLE = _envelope(
    _nest("SUBJECT COMPANY", ISSUER),
    _nest("FILED ON BEHALF OF", EXCHANGE),
)
UNKNOWN_PLAIN_GROUP = _envelope(
    _nest("SUBJECT COMPANY", ISSUER),
    "NOTIFICATION:\n\n\tCENTRAL INDEX KEY:\t\t\t0000333333\n",
)
FORMER_NAME = _envelope(
    _nest(
        "SUBJECT COMPANY",
        ISSUER,
        extra=(
            "\tFORMER COMPANY:\n"
            "\t\tFORMER CONFORMED NAME:\t\t\tEXAMPLE HOLDINGS CORP\n"
            "\t\tDATE OF NAME CHANGE:\t\t\t19970101"
        ),
    ),
)
TWO_KEYS_ONE_BLOCK = _envelope(
    "FILER:\n"
    "\n"
    "\tCOMPANY DATA:\n"
    f"\t\tCOMPANY CONFORMED NAME:\t\t\t{ISSUER[0]}\n"
    f"\t\tCENTRAL INDEX KEY:\t\t\t{ISSUER[1]}\n"
    "\n"
    "\tCOMPANY DATA:\n"
    f"\t\tCOMPANY CONFORMED NAME:\t\t\t{CO_REGISTRANT[0]}\n"
    f"\t\tCENTRAL INDEX KEY:\t\t\t{CO_REGISTRANT[1]}\n"
    "\n"
    "\tFILING VALUES:\n"
    "\t\tFORM TYPE:\t\tS-3\n"
    "\t\tSEC FILE NUMBER:\t333-00001\n",
    form="S-3",
)

ALL_ROLE_FIXTURES = [
    NOTICE,
    NOTICE_REVERSED,
    TWO_FILERS,
    THREE_ROLES,
    OWNERSHIP,
    SPACE_INDENTED,
    STRAY_KEY,
    UNKNOWN_ENTITY_ROLE,
    UNKNOWN_PLAIN_GROUP,
    FORMER_NAME,
    TWO_KEYS_ONE_BLOCK,
    _submission(),
    _submission(documents=_DOCUMENTS),
]


def test_an_ordinary_filing_has_one_entity_holding_the_filer_role() -> None:
    header = parse_submission_header(_submission())
    assert len(header.entities) == 1
    (entity,) = header.entities
    assert entity.role == "FILER"
    assert entity.cik == 806085
    assert entity.company_name == "LEHMAN BROTHERS HOLDINGS INC"
    assert entity.form_type == "DEF 14A"
    assert entity.file_numbers == ("001-09466",)
    assert header.entity_ambiguities == ()


def test_the_flat_fields_mean_exactly_what_they_meant_before() -> None:
    """The new field is additive. No existing caller changes because it exists."""
    header = parse_submission_header(_submission())
    assert header.ciks == (806085,)
    assert header.company_names == ("LEHMAN BROTHERS HOLDINGS INC",)
    assert header.file_numbers == ("001-09466",)
    assert header.submission_type == "DEF 14A"
    assert header.filed_at == dt.date(2008, 3, 5)
    assert header_mismatches(header, **_expect()) == ()  # type: ignore[arg-type]


def test_a_notice_filed_by_one_party_about_another_keeps_them_apart() -> None:
    header = parse_submission_header(NOTICE)
    subjects = entities_for_role(header, "SUBJECT COMPANY")
    filers = entities_for_role(header, "FILED BY")
    assert [e.cik for e in subjects] == [ISSUER[2]]
    assert [e.cik for e in filers] == [EXCHANGE[2]]
    assert subjects[0].company_name == ISSUER[0]
    assert filers[0].company_name == EXCHANGE[0]
    assert subjects[0].file_numbers == ("001-09466",)
    assert filers[0].file_numbers == ()
    assert header.entity_ambiguities == ()


def test_each_party_holds_exactly_the_role_it_was_given() -> None:
    header = parse_submission_header(NOTICE)
    assert cik_roles(header, ISSUER[2]) == ("SUBJECT COMPANY",)
    assert cik_roles(header, EXCHANGE[2]) == ("FILED BY",)
    assert cik_roles(header, CO_REGISTRANT[2]) == ()


def test_the_flat_view_cannot_tell_the_subject_from_the_filer() -> None:
    """The defect this change exists to fix, pinned as a test.

    Both CIKs are in ``ciks``, in it equally, with nothing to separate them. Any
    check phrased as "is this CIK present" is satisfied by a notice the
    registrant did not file, about itself or about anyone else. The structured
    view is what makes the two answerable apart.
    """
    header = parse_submission_header(NOTICE)
    assert ISSUER[2] in header.ciks
    assert EXCHANGE[2] in header.ciks
    weak = header_mismatches(
        header,
        accession="0000222222-08-000395",
        form_type="25-NSE",
        filed_at=dt.date(2008, 10, 15),
        cik=ISSUER[2],
    )
    assert weak == ()
    subject_ciks = [e.cik for e in entities_for_role(header, "SUBJECT COMPANY")]
    filer_ciks = [e.cik for e in entities_for_role(header, "FILED BY")]
    assert EXCHANGE[2] not in subject_ciks
    assert ISSUER[2] not in filer_ciks
    assert cik_roles(header, ISSUER[2]) != cik_roles(header, EXCHANGE[2])


def test_block_order_carries_no_meaning() -> None:
    forward = parse_submission_header(NOTICE)
    backward = parse_submission_header(NOTICE_REVERSED)
    assert cik_roles(forward, ISSUER[2]) == cik_roles(backward, ISSUER[2])
    assert cik_roles(forward, EXCHANGE[2]) == cik_roles(backward, EXCHANGE[2])
    assert forward.ciks != backward.ciks


def test_repeated_role_blocks_yield_repeated_entities() -> None:
    header = parse_submission_header(TWO_FILERS)
    filers = entities_for_role(header, "FILER")
    assert [e.cik for e in filers] == [ISSUER[2], CO_REGISTRANT[2]]
    assert [e.company_name for e in filers] == [ISSUER[0], CO_REGISTRANT[0]]


def test_three_roles_in_one_header_stay_separate() -> None:
    header = parse_submission_header(THREE_ROLES)
    assert cik_roles(header, ISSUER[2]) == ("SUBJECT COMPANY",)
    assert cik_roles(header, EXCHANGE[2]) == ("FILED BY",)
    assert cik_roles(header, CO_REGISTRANT[2]) == ("FILER",)
    assert unknown_roles(header) == ()


def test_an_ownership_header_separates_the_issuer_from_the_reporting_owner() -> None:
    """The hyphenated label is the same role written a different way."""
    header = parse_submission_header(OWNERSHIP)
    assert cik_roles(header, ISSUER[2]) == ("ISSUER",)
    assert cik_roles(header, CO_REGISTRANT[2]) == ("REPORTING OWNER",)
    (owner,) = entities_for_role(header, "REPORTING OWNER")
    assert owner.role_label == "REPORTING-OWNER"
    assert owner.company_name == CO_REGISTRANT[0]
    assert unknown_roles(header) == ()


def test_a_key_outside_every_role_block_is_unattributed_not_guessed() -> None:
    header = parse_submission_header(STRAY_KEY)
    assert header.ciks == (ISSUER[2],)
    assert cik_roles(header, ISSUER[2]) == (None,)
    assert cik_roles(header, ISSUER[2]) != ("FILER",)
    assert header.entity_ambiguities


def test_an_unfamiliar_label_becomes_a_role_when_the_block_is_entity_shaped() -> None:
    header = parse_submission_header(UNKNOWN_ENTITY_ROLE)
    assert cik_roles(header, EXCHANGE[2]) == ("FILED ON BEHALF OF",)
    assert unknown_roles(header) == ("FILED ON BEHALF OF",)
    assert "FILED ON BEHALF OF" not in KNOWN_ENTITY_ROLES
    assert cik_roles(header, ISSUER[2]) == ("SUBJECT COMPANY",)


def test_an_arbitrary_header_section_is_not_an_entity_role() -> None:
    """Open recognition, not credulity: a bare label alone proves nothing."""
    header = parse_submission_header(UNKNOWN_PLAIN_GROUP)
    assert cik_roles(header, CO_REGISTRANT[2]) == (None,)
    assert unknown_roles(header) == ()
    assert header.entity_ambiguities


def test_a_former_company_nest_neither_adds_an_entity_nor_renames_one() -> None:
    header = parse_submission_header(FORMER_NAME)
    assert len(header.entities) == 1
    (entity,) = header.entities
    assert entity.company_name == ISSUER[0]
    assert entity.role == "SUBJECT COMPANY"


def test_space_indentation_parses_identically_to_tabs() -> None:
    tabs = parse_submission_header(NOTICE)
    spaces = parse_submission_header(SPACE_INDENTED)
    assert [(e.role, e.cik, e.company_name) for e in tabs.entities] == [
        (e.role, e.cik, e.company_name) for e in spaces.entities
    ]


@pytest.mark.parametrize("raw", ALL_ROLE_FIXTURES)
def test_the_structured_view_never_loses_or_reorders_a_key(raw: str) -> None:
    """The refinement invariant, over every fixture in this file.

    The structured view may say *more* than the flat one. It may never disagree
    about who is present, or a caller could be told a CIK is absent because its
    block was shaped unusually.
    """
    header = parse_submission_header(raw)
    assert tuple(e.cik for e in header.entities if e.cik is not None) == header.ciks


def test_role_matching_is_exact_and_not_a_prefix() -> None:
    """``FILED BY`` contains ``FILED`` and is not ``FILER``."""
    header = parse_submission_header(NOTICE)
    assert entities_for_role(header, "FILED") == ()
    assert entities_for_role(header, "FILER") == ()
    assert entities_for_role(header, "SUBJECT") == ()
    assert len(entities_for_role(header, "FILED BY")) == 1


def test_a_block_form_type_is_a_fourth_separate_statement() -> None:
    """Envelope says one thing, the block says another. Both survive."""
    raw = NOTICE.replace("FORM TYPE:\t\t25-NSE", "FORM TYPE:\t\t25", 1)
    header = parse_submission_header(raw)
    (subject,) = entities_for_role(header, "SUBJECT COMPANY")
    assert header.submission_type == "25-NSE"
    assert subject.form_type == "25"
    assert subject.form_type != header.submission_type


def test_filing_values_are_not_copied_onto_several_parties() -> None:
    """Ambiguity is reported, never resolved by duplication."""
    header = parse_submission_header(TWO_KEYS_ONE_BLOCK)
    assert [e.cik for e in header.entities] == [ISSUER[2], CO_REGISTRANT[2]]
    assert [e.company_name for e in header.entities] == [ISSUER[0], CO_REGISTRANT[0]]
    assert all(e.form_type is None for e in header.entities)
    assert all(e.file_numbers == () for e in header.entities)
    assert any("FILING VALUES" in note for note in header.entity_ambiguities)


def test_a_document_body_cannot_invent_an_entity() -> None:
    forged = _submission(documents=_DOCUMENTS).replace(
        "<TEXT>\n<html><body><p>Proxy Statement",
        "<TEXT>\nSUBJECT COMPANY:\n\n\tCOMPANY DATA:\n"
        "\t\tCENTRAL INDEX KEY:\t\t\t0000999999\n<html><body><p>Proxy Statement",
    )
    header = parse_submission_header(forged)
    assert [e.cik for e in header.entities] == [806085]
    assert cik_roles(header, 999999) == ()
