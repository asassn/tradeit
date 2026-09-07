"""The acquisition helper, and the promotions it must never perform.

This tool exists to remove typing from a control investigation, not judgment.
The tests are therefore weighted towards what it must *refuse*: an ambiguous
registrant it does not resolve, a mismatched envelope it does not accept, a
first ``<DOCUMENT>`` it does not assume is the report, and an evidence file it
cannot write. A helper that quietly promoted a control would be worse than no
helper, because the record would look the same as one a person had checked.

No test reaches the network. The submissions below are synthetic, small and
built in-process, so the suite is deterministic and runs in an environment whose
egress policy denies sec.gov -- which is this one.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from tradeit.edgar.acquire import (
    USER_AGENT_ENV,
    AcquisitionReport,
    ExtractionStatus,
    Outcome,
    acquire_control_evidence,
    candidate_ciks,
    download_submission,
    extract_fund_trust_identity,
    extract_identity_evidence,
    filing_path,
    find_control,
    pick_primary_document,
    resolve_user_agent,
    strip_html,
)
from tradeit.edgar.index import FullIndexRow
from tradeit.edgar.submission import split_documents
from tradeit.errors import ConfigError

MSFT_CIK = 789019
MSFT_ACCESSION = "0001193125-26-323660"
MSFT_PATH = "edgar/data/789019/0001193125-26-323660.txt"

# The cover-page shape this extractor is written for, reduced to the parts that
# matter. Small enough to read, and checked in as code rather than as a 35 MB
# submission nobody would ever open.
COVER_PAGE_HTML = """
<html><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p>Securities registered pursuant to Section 12(b) of the Act:</p>
<table>
  <tr><td>Title of each class</td><td>Trading Symbol</td>
      <td>Name of each exchange on which registered</td></tr>
  <tr><td>Common stock, $0.00000625 par value per share</td><td>MSFT</td><td>Nasdaq</td></tr>
</table>
<p>Item 5. Market for Registrant&#8217;s Common Equity.</p>
<p>Our common stock is traded on the NASDAQ Stock Market under the symbol MSFT.</p>
</body></html>
"""


def _submission(
    *,
    accession: str = MSFT_ACCESSION,
    cik: int = MSFT_CIK,
    form: str = "10-K",
    filed: str = "20260729",
    company: str = "MICROSOFT CORP",
    documents: list[tuple[str, str, str]] | None = None,
) -> str:
    """A complete-submission envelope with an SGML header and documents."""
    docs = documents if documents is not None else [("10-K", "msft-20260630.htm", COVER_PAGE_HTML)]
    body = "".join(
        f"<DOCUMENT>\n<TYPE>{doc_type}\n<SEQUENCE>{i}\n<FILENAME>{name}\n"
        f"<DESCRIPTION>x\n<TEXT>\n{content}\n</TEXT>\n</DOCUMENT>\n"
        for i, (doc_type, name, content) in enumerate(docs, start=1)
    )
    return (
        "<SEC-DOCUMENT>\n<SEC-HEADER>\n"
        f"ACCESSION NUMBER:\t\t{accession}\n"
        f"CONFORMED SUBMISSION TYPE:\t{form}\n"
        "PUBLIC DOCUMENT COUNT:\t\t1\n"
        "CONFORMED PERIOD OF REPORT:\t20260630\n"
        f"FILED AS OF DATE:\t\t{filed}\n"
        "FILER:\n"
        "\tCOMPANY DATA:\n"
        f"\t\tCOMPANY CONFORMED NAME:\t\t\t{company}\n"
        f"\t\tCENTRAL INDEX KEY:\t\t\t{cik:010d}\n"
        "</SEC-HEADER>\n" + body + "</SEC-DOCUMENT>\n"
    )


def _index(tmp_path: Path, rows: list[tuple[int, str, str, str, str]]) -> Path:
    """Write a fixed-width form.idx the real parser accepts."""
    root = tmp_path / "full-index" / "2026" / "QTR3"
    root.mkdir(parents=True)
    header = (
        "Form Type   Company Name"
        + " " * 50
        + "CIK         Date Filed  File Name\n"
        + "-" * 120
        + "\n"
    )
    lines = [
        f"{form:<12}{name:<62}{cik:<12}{filed:<12}{path}" for cik, name, form, filed, path in rows
    ]
    (root / "form.idx").write_text(header + "\n".join(lines) + "\n", encoding="latin-1")
    return tmp_path / "full-index"


def _msft_index(tmp_path: Path) -> Path:
    return _index(
        tmp_path,
        [(MSFT_CIK, "MICROSOFT CORP", "10-K", "2026-07-29", MSFT_PATH)],
    )


def _write_filing(
    tmp_path: Path, text: str, cik: int = MSFT_CIK, accession: str = MSFT_ACCESSION
) -> Path:
    target = filing_path(tmp_path / "filings", cik, accession)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="latin-1")
    return target


def _run(tmp_path: Path, **kwargs: Any) -> AcquisitionReport:
    defaults: dict[str, Any] = {
        "index_root": tmp_path / "full-index",
        "filings_root": tmp_path / "filings",
        "offline": True,
    }
    defaults.update(kwargs)
    return acquire_control_evidence(kwargs.pop("ticker", "MSFT"), **defaults)


# ---------------------------------------------------------------------------
# 1-3. control lookup, and the absence of any remembered CIK
# ---------------------------------------------------------------------------


def test_a_known_control_is_found_with_its_metadata() -> None:
    control = find_control("msft")
    assert control.ticker == "MSFT"
    assert control.name == "Microsoft Corporation"
    assert control.verification_route


def test_an_unknown_control_is_rejected() -> None:
    with pytest.raises(ConfigError, match="not one of the 30 control securities"):
        find_control("NOTACONTROL")


def _acquire_source() -> str:
    return (Path(__file__).resolve().parents[2] / "src/tradeit/edgar/acquire.py").read_text()


def test_the_module_hard_codes_no_cik() -> None:
    """The failure this whole design exists to prevent.

    A CIK literal in acquisition source would be a remembered mapping wearing a
    tool's clothes. Every CIK must arrive from the index, from the submission
    header, or from a human passing --cik. A CIK is 1-10 digits; anything six or
    longer sitting in this file is the shape of one.
    """
    import re

    numbers = re.findall(r"\b\d{6,10}\b", _acquire_source())
    assert numbers == [], f"CIK-shaped literals in acquire.py: {numbers}"


# ---------------------------------------------------------------------------
# 4. ambiguity is reported, never resolved
# ---------------------------------------------------------------------------


def test_several_candidate_registrants_are_not_auto_selected(tmp_path: Path) -> None:
    _index(
        tmp_path,
        [
            (
                11111,
                "MICROSOFT CORP",
                "10-K",
                "2026-07-29",
                "edgar/data/11111/0000000001-26-000001.txt",
            ),
            (
                22222,
                "MICROSOFT LICENSING GP",
                "10-K",
                "2026-07-29",
                "edgar/data/22222/0000000002-26-000002.txt",
            ),
        ],
    )
    report = _run(tmp_path)
    assert report.outcome is Outcome.CIK_AMBIGUOUS
    assert report.cik is None
    assert len(report.candidates) == 2
    assert "does not make" in " ".join(report.problems)


def test_no_candidate_is_reported_as_absence(tmp_path: Path) -> None:
    _index(
        tmp_path,
        [
            (
                11111,
                "UNRELATED HOLDINGS INC",
                "10-K",
                "2026-07-29",
                "edgar/data/11111/0000000001-26-000001.txt",
            )
        ],
    )
    report = _run(tmp_path)
    assert report.outcome is Outcome.CIK_NOT_FOUND
    assert report.cik is None


def test_candidate_counting_spans_every_row_for_that_cik(tmp_path: Path) -> None:
    """A renamed registrant still filed the rows filed under its old name."""
    _index(
        tmp_path,
        [
            (
                11111,
                "MICROSOFT CORP",
                "10-K",
                "2026-07-29",
                "edgar/data/11111/0000000001-26-000001.txt",
            ),
            (
                11111,
                "RENAMED LATER INC",
                "8-K",
                "2026-08-01",
                "edgar/data/11111/0000000001-26-000002.txt",
            ),
        ],
    )
    candidates = candidate_ciks(find_control("MSFT"), tmp_path / "full-index")
    assert len(candidates) == 1
    assert candidates[0].filings == 2
    assert candidates[0].last_filed == dt.date(2026, 8, 1)


# ---------------------------------------------------------------------------
# 5. SEC identification is required, never defaulted in source
# ---------------------------------------------------------------------------


def test_a_missing_user_agent_fails_loudly() -> None:
    with pytest.raises(ConfigError, match="must identify the requester"):
        resolve_user_agent(None, env={})


def test_an_explicit_user_agent_wins_over_the_environment() -> None:
    assert resolve_user_agent("explicit", env={USER_AGENT_ENV: "from-env"}) == "explicit"


def test_the_environment_supplies_the_user_agent() -> None:
    assert resolve_user_agent(None, env={USER_AGENT_ENV: "TradeIt (a@b.c)"}) == "TradeIt (a@b.c)"


def test_a_blank_user_agent_is_not_an_identity() -> None:
    with pytest.raises(ConfigError):
        resolve_user_agent("   ", env={})


# ---------------------------------------------------------------------------
# 6-8. download semantics
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Stands in for HttpTransport. Never touches a socket."""

    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append((url, dict(headers or {})))
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return self.body


def _row() -> FullIndexRow:
    return FullIndexRow(
        cik=MSFT_CIK,
        company_name="MICROSOFT CORP",
        form_type="10-K",
        filed_at=dt.date(2026, 7, 29),
        path=MSFT_PATH,
        accession=MSFT_ACCESSION,
        index_quarter="2026-QTR3",
    )


def test_a_successful_download_publishes_the_filing_and_leaves_no_part(tmp_path: Path) -> None:
    transport = _FakeTransport(body=_submission().encode("latin-1"))
    target = download_submission(
        _row(),
        tmp_path / "filings",
        user_agent="TradeIt (a@b.c)",
        transport=transport,  # type: ignore[arg-type]
    )
    assert target.exists()
    assert target == filing_path(tmp_path / "filings", MSFT_CIK, MSFT_ACCESSION)
    assert list(target.parent.glob("*.part*")) == []
    assert transport.calls[0][1]["User-Agent"] == "TradeIt (a@b.c)"
    assert transport.calls[0][0].startswith("https://")


def test_a_failed_download_publishes_nothing(tmp_path: Path) -> None:
    """No HTML error page left sitting at a .txt path looking like a filing."""
    transport = _FakeTransport(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        download_submission(
            _row(),
            tmp_path / "filings",
            user_agent="ua",
            transport=transport,  # type: ignore[arg-type]
        )
    target = filing_path(tmp_path / "filings", MSFT_CIK, MSFT_ACCESSION)
    assert not target.exists()
    assert list(target.parent.glob("*.part*")) == []


def test_an_existing_filing_is_never_clobbered(tmp_path: Path) -> None:
    existing = _write_filing(tmp_path, _submission())
    original = existing.read_bytes()
    transport = _FakeTransport(body=b"different bytes entirely")
    download_submission(
        _row(),
        tmp_path / "filings",
        user_agent="ua",
        transport=transport,  # type: ignore[arg-type]
    )
    assert existing.read_bytes() == original


def test_an_already_downloaded_filing_is_reused_without_network(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission())
    report = _run(tmp_path, offline=True)
    assert report.outcome is Outcome.EXTRACTED
    assert report.downloaded is False


def test_a_missing_filing_in_offline_mode_stops_rather_than_fetching(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    report = _run(tmp_path, offline=True)
    assert report.outcome is Outcome.NOT_LOCAL
    assert "--offline" in " ".join(report.problems)


# ---------------------------------------------------------------------------
# 9-11. the envelope must be the one that was asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"cik": 999999}, "CIK 789019 absent"),
        ({"accession": "0000000000-00-000000"}, "ACCESSION NUMBER"),
        ({"form": "10-Q"}, "CONFORMED SUBMISSION TYPE"),
        ({"filed": "20260101"}, "FILED AS OF DATE"),
    ],
)
def test_a_contradicted_envelope_fails_closed(
    tmp_path: Path, kwargs: dict[str, Any], expected: str
) -> None:
    """The index row said one thing and the submission says another.

    Every one of these is a different filing wearing the right filename. None of
    them may be extracted from, because the extraction would be attributed to
    the accession that was requested.
    """
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission(**kwargs))
    report = _run(tmp_path, offline=True)
    assert report.outcome is Outcome.VALIDATION_FAILED
    assert report.evidence is None
    assert any(expected in p for p in report.problems)


def test_a_form_not_present_for_the_cik_is_reported(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    report = _run(tmp_path, offline=True, forms=("S-1",))
    assert report.outcome is Outcome.NO_FILING


# ---------------------------------------------------------------------------
# 12. the primary document is chosen by type, not position
# ---------------------------------------------------------------------------


def test_the_primary_document_is_not_simply_the_first(tmp_path: Path) -> None:
    text = _submission(
        documents=[
            ("GRAPHIC", "logo.jpg", "not the report"),
            ("EX-21.1", "subsidiaries.htm", "not the report"),
            ("10-K", "msft-20260630.htm", COVER_PAGE_HTML),
        ]
    )
    documents = split_documents(text)
    primary, reasons = pick_primary_document(documents, "10-K")
    assert primary is not None
    assert primary.filename == "msft-20260630.htm"
    assert reasons == ()


def test_no_document_of_the_validated_type_is_reported_not_guessed(tmp_path: Path) -> None:
    documents = split_documents(_submission(documents=[("EX-21.1", "s.htm", "x")]))
    primary, reasons = pick_primary_document(documents, "10-K")
    assert primary is None
    assert "no <DOCUMENT> has <TYPE>" in reasons[0]


def test_two_documents_of_the_same_type_are_not_chosen_between(tmp_path: Path) -> None:
    documents = split_documents(
        _submission(documents=[("10-K", "a.htm", "x"), ("10-K", "b.htm", "y")])
    )
    primary, reasons = pick_primary_document(documents, "10-K")
    assert primary is None
    assert "not choosing between them" in reasons[0]


def test_an_unpickable_primary_document_yields_review_needed(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission(documents=[("EX-21.1", "s.htm", "x")]))
    report = _run(tmp_path, offline=True)
    assert report.outcome is Outcome.REVIEW_NEEDED
    assert report.primary_document == ""


# ---------------------------------------------------------------------------
# 13-16. extraction reports; it does not conclude
# ---------------------------------------------------------------------------


def test_the_section_12b_table_is_extracted_with_its_columns_and_row() -> None:
    extract = extract_identity_evidence(COVER_PAGE_HTML)
    assert extract.status is ExtractionStatus.FOUND
    assert "Section 12(b)" in extract.section_12b_heading
    assert extract.section_12b_headers == (
        "Title of each class",
        "Trading Symbol",
        "Name of each exchange on which registered",
    )
    cells = extract.section_12b_rows[0].cells
    assert "Common stock, $0.00000625 par value per share" in cells
    assert "MSFT" in cells
    assert "Nasdaq" in cells


def test_prose_after_the_table_is_not_reported_as_a_registered_security() -> None:
    """The table ends where its rows stop being table-shaped.

    The Item 5 heading and the paragraph beneath it sit immediately after the
    cover-page table. Reported as a row they would read like a second
    registered class, which is a fabricated fact in the most dangerous place.
    """
    extract = extract_identity_evidence(COVER_PAGE_HTML)
    assert len(extract.section_12b_rows) == 1
    flattened = " ".join(c for r in extract.section_12b_rows for c in r.cells)
    assert "Item 5" not in flattened


# The layout that defeated the first row-boundary rule, from a real 10-K cover
# page. The 12(g) line and the check-box paragraphs sit in <p> elements after
# </table>, so they are cell breaks rather than row breaks and the whole block
# arrived as one SIX-cell segment -- wider than the table's three columns, and
# therefore accepted as a second registered security. The earlier synthetic
# fixture passed only because its post-table prose happened to be two cells.
CISCO_SHAPED_COVER_PAGE = """
<html><body>
<p>Securities registered pursuant to Section 12(b) of the Act:</p>
<table>
  <tr><td>Title of each class</td><td>Trading Symbol</td>
      <td>Name of each exchange on which registered</td></tr>
  <tr><td>Common Stock, par value $0.001 per share</td><td>CSCO</td>
      <td>The Nasdaq Stock Market LLC</td></tr>
</table>
<p>Securities registered pursuant to Section 12(g) of the Act: None</p>
<p>_______________________________________</p>
<p>Indicate by check mark if the registrant is a well-known seasoned issuer, as
defined in Rule 405 of the Securities Act. Yes No</p>
<p>Indicate by check mark if the registrant is not required to file reports
pursuant to Section 13 or Section 15(d) of the Act. Yes No</p>
<p>Indicate by check mark whether the registrant is a large accelerated filer, an
accelerated filer, a non-accelerated filer, a smaller reporting company, or an
emerging growth company. Yes No</p>
<p>(a) Cisco common stock is traded on the Nasdaq Global Select Market under the
symbol CSCO.</p>
</body></html>
"""


def test_a_following_statutory_heading_ends_the_section_12b_rows() -> None:
    """The defect the first real filing found, pinned.

    Only the common-stock row is a registered security here. The 12(g) line and
    everything under it is prose, and reporting it as a row would put a
    fabricated registered security in front of the person deciding whether the
    control is verified -- the worst possible place for one.
    """
    extract = extract_identity_evidence(CISCO_SHAPED_COVER_PAGE)

    assert extract.status is ExtractionStatus.FOUND
    assert len(extract.section_12b_rows) == 1

    cells = extract.section_12b_rows[0].cells
    assert cells == (
        "Common Stock, par value $0.001 per share",
        "CSCO",
        "The Nasdaq Stock Market LLC",
    )

    flattened = " ".join(c for r in extract.section_12b_rows for c in r.cells)
    assert "12(g)" not in flattened
    assert "Indicate by check mark" not in flattened
    assert "Yes No" not in flattened
    assert "___" not in flattened


def test_the_narrative_statement_survives_the_row_boundary_fix() -> None:
    """The two constructions are independent, and tightening one must not cost
    the other: the ticker sentence sits well past the terminated row region."""
    extract = extract_identity_evidence(CISCO_SHAPED_COVER_PAGE)
    statements = [s.text for s in extract.symbol_statements]
    assert any("under the symbol CSCO" in s for s in statements)
    assert any("Nasdaq Global Select Market" in s for s in statements)


def test_the_cisco_shape_reports_no_internal_sentinels() -> None:
    extract = extract_identity_evidence(CISCO_SHAPED_COVER_PAGE)
    reported = " ".join(
        [extract.section_12b_heading, *extract.section_12b_headers]
        + [c for r in extract.section_12b_rows for c in r.cells]
        + [s.text for s in extract.symbol_statements]
        + list(extract.notes)
    )
    for sentinel in ("␟", "␞", "␝"):
        assert sentinel not in reported


def test_the_cisco_shape_still_changes_no_control_status(tmp_path: Path) -> None:
    """The fix is to an extractor. It must not have loosened the safety rule."""
    from tradeit.edgar.control_evidence import DEFAULT_EVIDENCE_PATH

    before = DEFAULT_EVIDENCE_PATH.read_bytes()
    _msft_index(tmp_path)
    _write_filing(
        tmp_path,
        _submission(documents=[("10-K", "csco-20250726.htm", CISCO_SHAPED_COVER_PAGE)]),
    )
    report = _run(tmp_path, offline=True)

    assert DEFAULT_EVIDENCE_PATH.read_bytes() == before
    payload = report.summary()
    assert payload["control_status_changed"] is False
    assert "status" not in payload


def test_a_table_end_stops_collection_even_without_a_statutory_heading() -> None:
    """Boundary 2 alone, with the 12(g) heading removed.

    Each boundary has to hold on its own: a filing that omits the 12(g) line, or
    words it differently, still ends its table where the table ends.
    """
    html = CISCO_SHAPED_COVER_PAGE.replace(
        "<p>Securities registered pursuant to Section 12(g) of the Act: None</p>", ""
    )
    extract = extract_identity_evidence(html)
    assert len(extract.section_12b_rows) == 1
    assert extract.section_12b_rows[0].cells[1] == "CSCO"


def test_a_wide_block_of_prose_is_not_a_registered_security_row() -> None:
    """Boundary 3 alone: no </table> and no statutory heading to help.

    This is the shape that broke the width rule -- many cells, each a sentence.
    """
    html = """
    <p>Securities registered pursuant to Section 12(b) of the Act:</p>
    <p>Title of each class</p><p>Trading Symbol</p>
    <p>Name of each exchange on which registered</p>
    <p>Common Stock, par value $0.001 per share</p><p>CSCO</p>
    <p>The Nasdaq Stock Market LLC</p>
    <p>Indicate by check mark whether the registrant has submitted electronically
    every Interactive Data File required to be submitted pursuant to Rule 405 of
    Regulation S-T during the preceding 12 months.</p>
    <p>Indicate by check mark whether the registrant is a shell company as defined
    in Rule 12b-2 of the Exchange Act, which is a longer sentence than any cell.</p>
    <p>Yes No</p>
    """
    extract = extract_identity_evidence(html)
    flattened = " ".join(c for r in extract.section_12b_rows for c in r.cells)
    assert "Indicate by check mark" not in flattened
    assert "Interactive Data File" not in flattened


def test_no_internal_boundary_marker_reaches_reported_text() -> None:
    """A quotation must be the filing's words, not this module's scaffolding."""
    extract = extract_identity_evidence(COVER_PAGE_HTML)
    reported = " ".join(
        [extract.section_12b_heading, *extract.section_12b_headers]
        + [c for r in extract.section_12b_rows for c in r.cells]
        + [s.text for s in extract.symbol_statements]
    )
    assert "␟" not in reported
    assert "␞" not in reported


def test_the_narrative_symbol_statement_is_extracted_verbatim() -> None:
    extract = extract_identity_evidence(COVER_PAGE_HTML)
    statements = [s.text for s in extract.symbol_statements]
    assert any("under the symbol MSFT" in s for s in statements)
    assert any("NASDAQ Stock Market" in s for s in statements)


def test_two_renderings_of_an_exchange_are_both_preserved() -> None:
    """The table says Nasdaq and the sentence says NASDAQ Stock Market.

    Collapsing them would assert an equivalence the filing never states, and
    exchange names that differ by a word are routinely different venues.
    """
    extract = extract_identity_evidence(COVER_PAGE_HTML)
    table = " ".join(c for r in extract.section_12b_rows for c in r.cells)
    narrative = " ".join(s.text for s in extract.symbol_statements)
    assert "Nasdaq" in table
    assert "NASDAQ Stock Market" in narrative


def test_a_filing_with_no_identity_passages_is_incomplete_not_invented() -> None:
    extract = extract_identity_evidence("<html><body><p>Nothing relevant here.</p></body></html>")
    assert extract.status is ExtractionStatus.NOT_FOUND
    assert extract.section_12b_rows == ()
    assert extract.symbol_statements == ()
    assert extract.notes


def test_only_one_construction_is_reported_as_partial() -> None:
    html = "<p>Our common stock is traded on the NYSE under the symbol ZZZZ.</p>"
    extract = extract_identity_evidence(html)
    assert extract.status is ExtractionStatus.PARTIAL
    assert any("only one of the two" in n for n in extract.notes)


def test_a_missing_table_beneath_a_heading_is_noted(tmp_path: Path) -> None:
    html = "<p>Securities registered pursuant to Section 12(b) of the Act: None.</p>"
    extract = extract_identity_evidence(html)
    assert extract.status is not ExtractionStatus.FOUND
    assert extract.notes


def test_strip_html_keeps_cells_apart() -> None:
    assert "␟" in strip_html("<td>a</td><td>b</td>")


# ---------------------------------------------------------------------------
# 17-19. the local path, the report, and what it may never touch
# ---------------------------------------------------------------------------


def test_the_full_local_workflow_needs_no_network(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission())
    report = _run(tmp_path, offline=True)

    assert report.outcome is Outcome.EXTRACTED
    assert report.cik == MSFT_CIK
    assert report.accession == MSFT_ACCESSION
    assert report.form == "10-K"
    assert report.filed_at == dt.date(2026, 7, 29)
    assert report.conformed_company_name == "MICROSOFT CORP"
    assert report.primary_document == "msft-20260630.htm"
    assert report.primary_document_type == "10-K"
    assert report.source_path.endswith(f"{MSFT_ACCESSION}.txt")


def test_a_human_supplied_cik_skips_discovery(tmp_path: Path) -> None:
    """A person naming the CIK is not this module remembering one."""
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission())
    report = _run(tmp_path, offline=True, cik=MSFT_CIK)
    assert report.outcome is Outcome.EXTRACTED
    assert report.candidates == ()


def test_every_report_states_that_no_status_changed(tmp_path: Path) -> None:
    _msft_index(tmp_path)
    report = _run(tmp_path, offline=True)
    payload = report.summary()
    assert payload["control_status_changed"] is False
    assert "NO CONTROL STATUS WAS CHANGED" in payload["statement"]
    assert json.dumps(payload)


def test_the_report_carries_no_mapping_status_field(tmp_path: Path) -> None:
    """It cannot even express a promotion, which is the point.

    A report carrying a MappingStatus is one refactor away from being copied
    into the evidence file wholesale. The only place those words may appear is
    the fixed statement saying the tool did not assign them.
    """
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission())
    payload = _run(tmp_path, offline=True).summary()

    assert "status" not in payload
    assert "mapping_status" not in payload
    assert "evidence_type" not in payload

    without_disclaimer = dict(payload)
    without_disclaimer.pop("statement")
    serialised = json.dumps(without_disclaimer).lower()
    assert "manual_verified" not in serialised
    assert "resolved" not in serialised


def test_the_acquisition_layer_cannot_import_the_evidence_writer() -> None:
    """Structural, and checked against the parse tree rather than the prose.

    The docstrings here discuss control_evidence at length; what must not exist
    is an *import* of it. Reading the AST asks the question the runtime asks, so
    a future edit that reaches for the evidence writer fails this test even if
    it is spelled unusually.
    """
    import ast

    tree = ast.parse(_acquire_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not any("control_evidence" in name for name in imported), imported
    assert not any("identity" in name for name in imported), imported


def test_running_the_helper_leaves_the_evidence_file_untouched(tmp_path: Path) -> None:
    from tradeit.edgar.control_evidence import DEFAULT_EVIDENCE_PATH

    before = DEFAULT_EVIDENCE_PATH.read_bytes()
    _msft_index(tmp_path)
    _write_filing(tmp_path, _submission())
    _run(tmp_path, offline=True)
    assert DEFAULT_EVIDENCE_PATH.read_bytes() == before


def test_a_json_report_written_under_out_is_ignored_by_git(tmp_path: Path) -> None:
    """The report is a working artifact, so it belongs in the ignored out/."""
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "out/csco-acquire.json"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# the fund/trust prospectus family
#
# A trust has no Section 12(b) cover-page table, so the corporate extractor
# correctly finds nothing in one -- which is right and useless. Its identity is
# in a labelled legal name, a shorthand the filing defines, and sentences naming
# the listing exchange and the symbol together.
#
# The fixture below is modelled structurally on a real 485BPOS: the same
# constructions in the same order, with everything else omitted. What is being
# pinned is that those constructions are recognised, quoted as written, and NOT
# interpreted -- a "formerly known as" parenthetical especially.
# ---------------------------------------------------------------------------

TRUST_PROSPECTUS_HTML = """
<html><body>
<table>
  <tr><td>A.</td><td>Exact name of Trust:</td>
      <td>STATE STREET&reg; SPDR&reg; S&amp;P 500&reg; ETF TRUST</td></tr>
  <tr><td></td><td></td>
      <td>(formerly known as SPDR TRUST SERIES 1 prior to January 27, 2010 and
      SPDR&reg; S&amp;P 500&reg; ETF TRUST prior to January 26, 2026)</td></tr>
  <tr><td>B.</td><td>Name of Depositor:</td><td>PDR SERVICES LLC</td></tr>
</table>
<p>State Street&reg; SPDR&reg; S&amp;P 500&reg; ETF Trust</p>
<p>("SPY" or the "Trust")</p>
<p>(A Unit Investment Trust)</p>
<p>Principal U.S. Listing Exchange for State Street&reg; SPDR&reg; S&amp;P 500&reg; ETF Trust:</p>
<p>NYSE Arca, Inc. under the symbol "SPY"</p>
<p>The Sponsor of the Trust is a global asset management firm; the Trustee and the
Distributor are named in the Statement of Additional Information.</p>
<p>The Trust is an exchange-traded fund holding a portfolio of equity securities.
Units of the Trust are listed and traded, and investors may buy and sell Units
under the market symbol "SPY" throughout the trading day.</p>
<p>Individual Units of the Trust may be purchased and sold on NYSE Arca, Inc. (the
"Exchange"), under the market symbol "SPY".</p>
</body></html>
"""


def _trust_index(tmp_path: Path) -> Path:
    return _index(
        tmp_path,
        [
            (
                MSFT_CIK,
                "MICROSOFT CORP",
                "485BPOS",
                "2026-01-26",
                MSFT_PATH,
            )
        ],
    )


def _trust_submission(**kwargs: Any) -> str:
    defaults: dict[str, Any] = {
        "form": "485BPOS",
        "filed": "20260126",
        "documents": [("485BPOS", "d77353d485bpos.htm", TRUST_PROSPECTUS_HTML)],
    }
    defaults.update(kwargs)
    return _submission(**defaults)


def test_the_trust_family_is_detected_where_the_corporate_one_finds_nothing() -> None:
    extract = extract_identity_evidence(TRUST_PROSPECTUS_HTML)

    assert extract.status is ExtractionStatus.FOUND
    assert extract.families == ("fund_trust_listing",)
    assert extract.section_12b_rows == ()
    assert extract.symbol_statements == ()


def test_the_exact_legal_trust_name_is_preserved() -> None:
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    assert fund.exact_name == "STATE STREET® SPDR® S&P 500® ETF TRUST"
    # The parenthetical history is a separate field, not part of the name.
    assert "formerly known as" not in fund.exact_name


def test_former_names_are_reported_and_not_interpreted() -> None:
    """Three renderings of this trust's name exist in one sentence.

    They are reproduced as written. Nothing turns them into a rename, a validity
    window or a dated event, and the dates inside stay inside the quoted string
    rather than becoming values.
    """
    extract = extract_identity_evidence(TRUST_PROSPECTUS_HTML)
    fund = extract.fund_trust

    assert fund.former_names
    joined = " ".join(fund.former_names)
    assert "SPDR TRUST SERIES 1" in joined
    assert "SPDR® S&P 500® ETF TRUST" in joined
    assert "&reg;" not in joined
    assert "prior to January 27, 2010" in joined

    assert any("NOT been read as a rename" in n for n in extract.notes)
    payload = fund.summary()
    assert isinstance(payload["former_names"], list)
    assert all(isinstance(v, str) for v in payload["former_names"])
    assert "valid_from" not in payload
    assert "lifecycle" not in json.dumps(payload).lower()


def test_the_filing_defined_shorthand_is_preserved() -> None:
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    joined = " ".join(fund.shorthand_definitions)
    assert '"SPY" or the "Trust"' in joined
    # In the real layout the shorthand sits in its own block, without the name it
    # abbreviates. That is precisely why it is corroboration and not identity.
    assert not fund.has_legal_identity or fund.exact_name not in joined


def test_the_exchange_and_symbol_construction_is_extracted() -> None:
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    joined = " ".join(fund.listing_statements)
    assert "Principal U.S. Listing Exchange" in joined
    assert "NYSE Arca, Inc." in joined
    assert 'under the symbol "SPY"' in joined


def test_the_units_trading_statement_is_extracted_independently() -> None:
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    joined = " ".join(fund.trading_statements)
    assert "Individual Units of the Trust" in joined
    assert "purchased and sold on NYSE Arca, Inc." in joined
    assert 'under the market symbol "SPY"' in joined


def test_legal_identity_without_a_listing_construction_is_partial() -> None:
    """A name is not a listing. Requirement 10, and the honest half of it."""
    html = (
        "<p>Exact name of Trust: EXAMPLE INDEX TRUST</p>"
        '<p>Example Index Trust ("EXT" or the "Trust") is a unit investment trust '
        "holding a portfolio of equity securities.</p>"
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.exact_name == "EXAMPLE INDEX TRUST"
    assert extract.fund_trust.has_legal_identity
    assert not extract.fund_trust.has_listing_identity
    assert extract.status is ExtractionStatus.PARTIAL
    assert any("exchange and a symbol together" in n for n in extract.notes)


def test_listing_identity_without_legal_identity_is_partial() -> None:
    """Requirement 11. A shorthand names what the filing will say, not what it is."""
    html = (
        '<p>Example Index Trust ("EXT" or the "Trust") is a unit investment trust.</p>'
        "<p>Principal U.S. Listing Exchange for Example Index Trust: Example Exchange LLC "
        'under the symbol "EXT"</p>'
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.exact_name == ""
    assert extract.fund_trust.shorthand_definitions
    assert not extract.fund_trust.has_legal_identity
    assert extract.fund_trust.has_listing_identity
    assert extract.status is ExtractionStatus.PARTIAL


def test_legal_identity_plus_listing_identity_is_found() -> None:
    """Requirement 12: both halves, and only then."""
    html = (
        "<p>Exact name of Trust: EXAMPLE INDEX TRUST</p>"
        "<p>Principal U.S. Listing Exchange for Example Index Trust: Example Exchange LLC "
        'under the symbol "EXT"</p>'
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.is_complete
    assert extract.status is ExtractionStatus.FOUND


def test_a_label_with_no_value_is_not_legal_identity() -> None:
    """Requirement 2, and the exact shape of the first real failure.

    The helper reported "Exact name of Trust:" as the legal name because the
    pattern accepted whitespace as a value. A label states where a name would go.
    """
    html = (
        "<p>Exact name of Trust:</p>"
        "<p>Principal U.S. Listing Exchange for the Trust: Example Exchange LLC "
        'under the symbol "EXT"</p>'
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.exact_name == ""
    assert not extract.fund_trust.has_legal_identity
    assert extract.status is ExtractionStatus.PARTIAL


def test_a_units_statement_without_a_venue_is_not_listing_identity() -> None:
    """Requirement 9. A symbol with no place attached is a near-miss."""
    html = (
        "<p>Exact name of Trust: EXAMPLE INDEX TRUST</p>"
        "<p>Units of the Trust are bought and sold by investors throughout the day, "
        'and are quoted under the symbol "EXT" wherever they change hands.</p>'
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.trading_statements or extract.fund_trust.listing_statements
    assert not extract.fund_trust.has_listing_identity
    assert extract.status is ExtractionStatus.PARTIAL


def test_a_missing_symbol_is_partial_not_fabricated() -> None:
    """An exchange without a symbol names a venue, not a security."""
    html = TRUST_PROSPECTUS_HTML.replace('under the symbol "SPY"', "").replace(
        'under the market symbol "SPY"', ""
    )
    extract = extract_identity_evidence(html)

    assert extract.status is ExtractionStatus.PARTIAL
    assert not extract.fund_trust.has_listing_identity


def test_a_sponsor_is_never_substituted_for_the_trust_identity() -> None:
    """The legal name comes from an explicit label or from nowhere.

    A prospectus names a sponsor, a trustee and a distributor. Recording one of
    those as the security's identity would be a wrong mapping that reads like a
    right one, so removing the label must empty the field rather than fall back
    to some other capitalised name in the document.
    """
    html = TRUST_PROSPECTUS_HTML.replace("Exact name of Trust:", "Overview:")
    fund = extract_fund_trust_identity(html)

    assert fund.exact_name == ""
    assert "Sponsor" not in " ".join(
        [fund.exact_name, *fund.shorthand_definitions, *fund.listing_statements]
    )


def test_prose_merely_mentioning_a_ticker_is_not_listing_evidence() -> None:
    """The construction is the evidence, not the presence of three letters."""
    html = (
        "<p>SPY is widely held. Many investors discuss SPY. The Trust is large "
        "and SPY is often mentioned in the press.</p>"
    )
    extract = extract_identity_evidence(html)

    assert extract.fund_trust.listing_statements == ()
    assert extract.fund_trust.trading_statements == ()
    assert extract.status is ExtractionStatus.NOT_FOUND


def test_the_corporate_family_is_not_regressed_by_the_trust_family() -> None:
    """MSFT- and CSCO-shaped cover pages keep reporting exactly as before."""
    for html in (COVER_PAGE_HTML, CISCO_SHAPED_COVER_PAGE):
        extract = extract_identity_evidence(html)
        assert extract.status is ExtractionStatus.FOUND
        assert extract.families == ("corporate_section_12b",)
        assert extract.section_12b_rows
        assert extract.symbol_statements
        assert not extract.fund_trust.has_anything


def test_the_full_trust_workflow_runs_offline_and_reports_the_family(tmp_path: Path) -> None:
    _trust_index(tmp_path)
    _write_filing(tmp_path, _trust_submission())
    report = _run(tmp_path, offline=True, forms=("485BPOS",))

    assert report.outcome is Outcome.EXTRACTED
    assert report.form == "485BPOS"
    assert report.primary_document == "d77353d485bpos.htm"
    assert report.primary_document_type == "485BPOS"
    assert report.evidence is not None
    assert report.evidence.families == ("fund_trust_listing",)


def test_a_trust_submission_still_fails_closed_on_a_mismatched_envelope(
    tmp_path: Path,
) -> None:
    """The new family changes nothing about validation."""
    _trust_index(tmp_path)
    _write_filing(tmp_path, _trust_submission(cik=999999))
    report = _run(tmp_path, offline=True, forms=("485BPOS",))

    assert report.outcome is Outcome.VALIDATION_FAILED
    assert report.evidence is None


def test_a_malformed_trust_document_yields_no_evidence_rather_than_a_guess(
    tmp_path: Path,
) -> None:
    _trust_index(tmp_path)
    _write_filing(
        tmp_path,
        _trust_submission(documents=[("485BPOS", "d.htm", "<html><body></body></html>")]),
    )
    report = _run(tmp_path, offline=True, forms=("485BPOS",))

    assert report.outcome is Outcome.REVIEW_NEEDED
    assert report.evidence is not None
    assert report.evidence.status is ExtractionStatus.NOT_FOUND
    assert report.evidence.families == ()


def test_a_trust_report_carries_no_mapping_status_or_promotion_field(tmp_path: Path) -> None:
    _trust_index(tmp_path)
    _write_filing(tmp_path, _trust_submission())
    payload = _run(tmp_path, offline=True, forms=("485BPOS",)).summary()

    assert "status" not in payload
    assert payload["control_status_changed"] is False
    assert "NO CONTROL STATUS WAS CHANGED" in payload["statement"]

    without_disclaimer = dict(payload)
    without_disclaimer.pop("statement")
    serialised = json.dumps(without_disclaimer).lower()
    assert "manual_verified" not in serialised
    assert '"status": "resolved"' not in serialised


def test_a_trust_run_leaves_the_evidence_file_untouched(tmp_path: Path) -> None:
    from tradeit.edgar.control_evidence import DEFAULT_EVIDENCE_PATH

    before = DEFAULT_EVIDENCE_PATH.read_bytes()
    _trust_index(tmp_path)
    _write_filing(tmp_path, _trust_submission())
    _run(tmp_path, offline=True, forms=("485BPOS",))

    assert DEFAULT_EVIDENCE_PATH.read_bytes() == before


def test_the_trust_family_reports_no_internal_sentinels() -> None:
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    reported = " ".join(
        [
            fund.exact_name,
            *fund.former_names,
            *fund.shorthand_definitions,
            *fund.listing_statements,
            *fund.trading_statements,
        ]
    )
    for sentinel in ("␟", "␞", "␝"):
        assert sentinel not in reported


def test_overlapping_matches_are_not_counted_as_independent_corroboration() -> None:
    """Requirement 13, and the second half of the first real failure.

    Both matchers see the units sentence. Reporting it twice would show a
    reviewer two passages that look like mutual confirmation and are one passage
    counted twice. Overlap is resolved by source span, so each passage in the
    filing appears exactly once across both fields.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    statements = [*fund.listing_statements, *fund.trading_statements]

    assert len(statements) == len(set(statements))
    for i, first in enumerate(statements):
        for second in statements[i + 1 :]:
            assert first not in second
            assert second not in first

    # The specific listing construction survives under its own label rather than
    # being swallowed by a broader match, which is what hid it in the real run.
    assert any("Principal U.S. Listing Exchange" in s for s in fund.listing_statements)
    assert any("Individual Units of the Trust" in s for s in fund.trading_statements)


def test_html_entities_are_decoded_in_every_emitted_passage() -> None:
    """Requirement 5 and 20, from the real run.

    The first real output quoted a trust's former name as
    "SPDR &reg; S&P 500 &reg; ETF TRUST". Evidence a person is meant to read and
    cite must carry the filing's wording, not its markup.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    emitted = " ".join(
        [
            fund.exact_name,
            fund.exact_name_statement,
            *fund.former_names,
            *fund.shorthand_definitions,
            *fund.listing_statements,
            *fund.trading_statements,
        ]
    )

    assert "®" in emitted
    assert "&" in emitted  # S&P survived as an ampersand
    for entity in ("&reg;", "&amp;", "&nbsp;", "&#8217;", "&quot;"):
        assert entity not in emitted


def test_decoding_is_not_normalization() -> None:
    """Requirement 6. Characters change; words do not.

    Three renderings of this trust's name appear in the fixture and all three
    survive distinctly. Collapsing them into one canonical name would destroy the
    difference a human is being asked to adjudicate.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)

    assert fund.exact_name == "STATE STREET® SPDR® S&P 500® ETF TRUST"
    joined = " ".join([*fund.former_names, *fund.shorthand_definitions, *fund.listing_statements])
    assert "SPDR TRUST SERIES 1" in joined
    assert "SPDR® S&P 500® ETF TRUST" in joined
    assert "State Street® SPDR® S&P 500® ETF Trust" in joined


def test_the_legal_name_is_the_value_and_the_statement_keeps_the_label() -> None:
    """Requirement 1. The label is context; the value is the evidence."""
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)

    assert fund.exact_name == "STATE STREET® SPDR® S&P 500® ETF TRUST"
    assert not fund.exact_name.lower().startswith("exact name")
    assert fund.exact_name_statement.startswith("Exact name of Trust:")
    assert fund.exact_name in fund.exact_name_statement


def test_a_shorthand_alone_cannot_complete_the_family() -> None:
    """Requirement 8, stated directly against the predicate."""
    html = '<p>Example Index Trust ("EXT" or the "Trust") is a unit investment trust.</p>'
    fund = extract_fund_trust_identity(html)

    assert fund.shorthand_definitions
    assert not fund.has_legal_identity
    assert not fund.is_complete


def test_former_names_alone_cannot_complete_the_family() -> None:
    html = "<p>formerly known as EXAMPLE OLD TRUST prior to January 1, 2020</p>"
    fund = extract_fund_trust_identity(html)

    assert fund.former_names
    assert not fund.has_legal_identity
    assert not fund.is_complete


# ---------------------------------------------------------------------------
# bounded cross-cell association
#
# The real filing puts a label in one table cell and its value in another, and
# the same for the principal-listing heading. Flattened-text matching cannot see
# across that boundary without being allowed to run across the whole page, so the
# boundary is used as the structure it is: blocks, adjacency, and a stop at the
# next labelled field.
# ---------------------------------------------------------------------------


def test_the_legal_name_is_read_from_the_cell_beside_its_label() -> None:
    """Requirements 1 and 2. The label and value are in different cells."""
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    assert fund.exact_name == "STATE STREET® SPDR® S&P 500® ETF TRUST"


def test_the_label_itself_is_never_the_value() -> None:
    """Requirement 3, the first real defect, pinned against the real layout."""
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    assert "Exact name of" not in fund.exact_name
    assert not fund.exact_name.endswith(":")


def test_the_depositor_below_the_trust_field_is_never_captured() -> None:
    """Requirements 4 and 17.

    ``Name of Depositor: PDR SERVICES LLC`` is the next field in the same table.
    A depositor recorded as the security's identity is a wrong mapping that reads
    like a right one, and the next field's label is what stops the search.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    assert "DEPOSITOR" not in fund.exact_name.upper()
    assert "PDR SERVICES" not in fund.exact_name.upper()


def test_a_missing_value_does_not_scan_into_the_next_field() -> None:
    """Requirement 5. An absent value stays absent.

    With the trust's name removed, the very next content is the depositor field.
    Reaching its label ends the search, so nothing is reported rather than the
    wrong thing being reported confidently.
    """
    html = """
    <table>
      <tr><td>A.</td><td>Exact name of Trust:</td><td></td></tr>
      <tr><td>B.</td><td>Name of Depositor:</td><td>PDR SERVICES LLC</td></tr>
    </table>
    """
    fund = extract_fund_trust_identity(html)
    assert fund.exact_name == ""
    assert not fund.has_legal_identity


def test_the_principal_listing_heading_joins_its_adjacent_value() -> None:
    """Requirements 7, 8, 9 and 10.

    One construction, carrying which trust, which venue and which symbol. The
    punctuation in "NYSE Arca, Inc." is not a boundary -- treating a full stop as
    one is what cut this family's evidence in half previously.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    principal = [s for s in fund.listing_statements if "Principal U.S. Listing Exchange" in s]

    assert len(principal) == 1
    statement = principal[0]
    assert "State Street® SPDR® S&P 500® ETF Trust" in statement
    assert "NYSE Arca, Inc." in statement
    assert 'under the symbol "SPY"' in statement
    assert fund.has_listing_identity


def test_a_later_ticker_mention_cannot_complete_an_orphaned_heading() -> None:
    """Requirement 11. Adjacency is the association; distance is not."""
    html = """
    <p>Principal U.S. Listing Exchange for Example Index Trust:</p>
    <p>To be determined.</p>
    <p>Many investors follow EXT. The trading symbol EXT appears in the press.</p>
    <p>Elsewhere in this document, under the symbol "EXT", quotations may be found.</p>
    """
    fund = extract_fund_trust_identity(html)
    joined = " ".join(fund.listing_statements)
    assert "Principal U.S. Listing Exchange" not in joined


def test_units_statements_do_not_compensate_for_a_missing_principal_listing() -> None:
    """Requirement 16. Corroboration is not a substitute for the thing itself.

    Units language remains reported, but the family is complete only because a
    genuine legal name and a genuine listing construction were both found.
    """
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)

    assert fund.trading_statements
    assert fund.has_legal_identity
    assert fund.has_listing_identity

    # Remove the legal name and the family must fall back to PARTIAL even though
    # every units passage is still there.
    without_name = TRUST_PROSPECTUS_HTML.replace("Exact name of Trust:", "Overview:")
    reduced = extract_fund_trust_identity(without_name)
    assert reduced.trading_statements
    assert not reduced.has_legal_identity
    assert not reduced.is_complete


def test_the_real_layout_reaches_found_through_the_intended_constructions() -> None:
    """Requirement 14, and the shape the real filing actually has."""
    extract = extract_identity_evidence(TRUST_PROSPECTUS_HTML)

    assert extract.status is ExtractionStatus.FOUND
    assert extract.families == ("fund_trust_listing",)
    assert extract.fund_trust.exact_name
    assert any(
        "Principal U.S. Listing Exchange" in s for s in extract.fund_trust.listing_statements
    )


def test_the_real_layout_emits_no_sentinels_and_no_entities() -> None:
    """Requirement 22, across every field of the block-structured fixture."""
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)
    emitted = " ".join(
        [
            fund.exact_name,
            fund.exact_name_statement,
            *fund.former_names,
            *fund.shorthand_definitions,
            *fund.listing_statements,
            *fund.trading_statements,
        ]
    )
    for sentinel in ("␟", "␞", "␝"):
        assert sentinel not in emitted
    for entity in ("&reg;", "&amp;", "&nbsp;", "&#8217;"):
        assert entity not in emitted
    assert "®" in emitted


# ---------------------------------------------------------------------------
# the registration-statement pattern
#
# A second fund convention, structurally unlike the prospectus one. The legal
# name is a title with a parenthetical caption UNDER it and no colon anywhere,
# and the listing relationship is a table -- Fund | Principal U.S. Listing
# Exchange | Ticker -- rather than a sentence. Both escaped the extractor
# entirely: one pattern required a colon, the other required a sentence.
# ---------------------------------------------------------------------------

REGISTRATION_STATEMENT_HTML = """
<html><body>
<p>FORM N-1A</p>
<p>REGISTRATION STATEMENT UNDER THE SECURITIES ACT OF 1933</p>
<p>POST-EFFECTIVE AMENDMENT NO. 42</p>
<p>Example Index Trust SM, Series 1</p>
<p>(Exact Name of Registrant as Specified in Charter)</p>
<p>Example Advisers Ltd. &mdash; Investment Adviser to the Registrant</p>
<p>Example Trust Services LLC, Sponsor and Depositor</p>
<table>
  <tr><td>Fund</td><td>Principal U.S. Listing Exchange</td><td>Ticker</td></tr>
  <tr><td>Example Index Trust SM, Series 1</td>
      <td>The Example Stock Market LLC</td><td>EXT</td></tr>
</table>
<p>The Registrant was formerly known as Example Legacy Trust, Series 1 and before
that as Example Original Trust Series 1.</p>
<p>Shares of the Fund are bought and sold in the secondary market, where EXT is
quoted throughout the trading day.</p>
</body></html>
"""


def _registration_submission(**kwargs: Any) -> str:
    defaults: dict[str, Any] = {
        "form": "485BPOS",
        "filed": "20260126",
        "documents": [("485BPOS", "tm0000000d1_485bpos.htm", REGISTRATION_STATEMENT_HTML)],
    }
    defaults.update(kwargs)
    return _submission(**defaults)


def test_a_name_above_its_caption_is_the_legal_name() -> None:
    """Requirement 1. The registration convention labels the name ABOVE it."""
    fund = extract_fund_trust_identity(REGISTRATION_STATEMENT_HTML)
    assert fund.exact_name == "Example Index Trust SM, Series 1"
    assert fund.has_legal_identity


def test_a_name_beside_its_caption_is_also_found() -> None:
    """Requirement 2. The same wording appears in cell-pair layouts."""
    html = """
    <table><tr><td>(Exact Name of Registrant as Specified in Charter)</td>
        <td>Example Index Trust SM, Series 1</td></tr></table>
    """
    fund = extract_fund_trust_identity(html)
    assert fund.exact_name == "Example Index Trust SM, Series 1"


def test_the_caption_itself_is_never_the_legal_name() -> None:
    """Requirement 3."""
    fund = extract_fund_trust_identity(REGISTRATION_STATEMENT_HTML)
    assert "Exact Name of Registrant" not in fund.exact_name
    assert not fund.exact_name.startswith("(")


def test_an_adviser_or_sponsor_below_the_caption_is_never_captured() -> None:
    """Requirement 4, and this control's own recorded verification route.

    With the registrant's name removed the caption is orphaned, and the very next
    blocks name an adviser, a sponsor and a depositor. Any of them recorded as
    the security's identity would be a wrong mapping that reads like a right one.
    """
    html = REGISTRATION_STATEMENT_HTML.replace(
        "<p>Example Index Trust SM, Series 1</p>\n<p>(Exact Name", "<p>(Exact Name"
    )
    fund = extract_fund_trust_identity(html)

    for role in ("Adviser", "Sponsor", "Depositor", "Example Advisers", "Trust Services"):
        assert role not in fund.exact_name


def test_the_listing_table_headers_are_recognised() -> None:
    """Requirement 5."""
    fund = extract_fund_trust_identity(REGISTRATION_STATEMENT_HTML)
    assert len(fund.listing_rows) == 1


def test_the_listing_row_preserves_fund_exchange_and_ticker() -> None:
    """Requirements 6, 7 and 8. The filing joined these three; nothing inferred."""
    row = extract_fund_trust_identity(REGISTRATION_STATEMENT_HTML).listing_rows[0]

    assert row.fund == "Example Index Trust SM, Series 1"
    assert row.exchange == "The Example Stock Market LLC"
    assert row.ticker == "EXT"


def test_later_ticker_prose_cannot_repair_a_malformed_table() -> None:
    """Requirement 9. A missing header is a missing table, not a lookup key."""
    html = REGISTRATION_STATEMENT_HTML.replace(
        "<td>Principal U.S. Listing Exchange</td>", "<td>Venue</td>"
    )
    fund = extract_fund_trust_identity(html)
    assert fund.listing_rows == ()


def test_a_legal_name_and_one_listing_row_reach_found() -> None:
    """Requirement 10."""
    extract = extract_identity_evidence(REGISTRATION_STATEMENT_HTML)
    assert extract.status is ExtractionStatus.FOUND
    assert extract.families == ("fund_trust_listing",)


def test_a_legal_name_without_a_listing_row_stays_partial() -> None:
    """Requirement 11."""
    html = """
    <p>Example Index Trust SM, Series 1</p>
    <p>(Exact Name of Registrant as Specified in Charter)</p>
    """
    extract = extract_identity_evidence(html)
    assert extract.fund_trust.has_legal_identity
    assert not extract.fund_trust.has_listing_identity
    assert extract.status is ExtractionStatus.PARTIAL


def test_a_listing_row_without_a_legal_name_stays_partial() -> None:
    """Requirement 12."""
    html = """
    <table>
      <tr><td>Fund</td><td>Principal U.S. Listing Exchange</td><td>Ticker</td></tr>
      <tr><td>Example Index Trust</td><td>The Example Stock Market LLC</td><td>EXT</td></tr>
    </table>
    """
    extract = extract_identity_evidence(html)
    assert extract.fund_trust.listing_rows
    assert extract.fund_trust.has_listing_identity
    assert not extract.fund_trust.has_legal_identity
    assert extract.status is ExtractionStatus.PARTIAL


def test_several_listing_rows_are_exposed_and_never_auto_selected() -> None:
    """Requirement 13, the multi-series case.

    Every row is reported so a human can see the choice; none is picked, and the
    family does not reach complete on a table it cannot attribute. Selecting the
    row whose ticker resembles the requested control would be the tool making an
    identity judgment, which is the one thing it must not do.
    """
    html = """
    <p>Example Fund Family Trust</p>
    <p>(Exact Name of Registrant as Specified in Charter)</p>
    <table>
      <tr><td>Fund</td><td>Principal U.S. Listing Exchange</td><td>Ticker</td></tr>
      <tr><td>Example Growth Fund</td><td>The Example Stock Market LLC</td><td>EXG</td></tr>
      <tr><td>Example Value Fund</td><td>The Example Stock Market LLC</td><td>EXV</td></tr>
    </table>
    """
    extract = extract_identity_evidence(html)
    fund = extract.fund_trust

    assert len(fund.listing_rows) == 2
    assert {r.ticker for r in fund.listing_rows} == {"EXG", "EXV"}
    assert not fund.has_listing_identity
    assert extract.status is ExtractionStatus.PARTIAL
    assert any("has NOT been decided here" in n for n in extract.notes)


def test_historical_name_prose_creates_no_lifecycle_fact() -> None:
    """Requirement 14. The registration fixture carries two prior names."""
    extract = extract_identity_evidence(REGISTRATION_STATEMENT_HTML)
    payload = extract.fund_trust.summary()

    assert "lifecycle" not in json.dumps(payload).lower()
    assert "valid_from" not in payload
    assert "valid_to" not in payload
    assert all(isinstance(v, str) for v in payload["former_names"])


def test_the_prospectus_family_is_unchanged_by_the_registration_pattern() -> None:
    """Requirement 15. The SPY-shaped fixture reports exactly as before."""
    fund = extract_fund_trust_identity(TRUST_PROSPECTUS_HTML)

    assert fund.exact_name == "STATE STREET® SPDR® S&P 500® ETF TRUST"
    assert fund.listing_rows == ()
    assert any("Principal U.S. Listing Exchange" in s for s in fund.listing_statements)
    assert fund.is_complete


def test_the_corporate_family_is_unchanged_by_the_registration_pattern() -> None:
    """Requirement 16."""
    for html in (COVER_PAGE_HTML, CISCO_SHAPED_COVER_PAGE):
        extract = extract_identity_evidence(html)
        assert extract.status is ExtractionStatus.FOUND
        assert extract.families == ("corporate_section_12b",)
        assert not extract.fund_trust.has_anything


def test_a_registration_run_leaves_the_evidence_file_untouched(tmp_path: Path) -> None:
    """Requirements 17, 18 and 20."""
    from tradeit.edgar.control_evidence import DEFAULT_EVIDENCE_PATH

    before = DEFAULT_EVIDENCE_PATH.read_bytes()
    _trust_index(tmp_path)
    _write_filing(tmp_path, _registration_submission())
    report = _run(tmp_path, offline=True, forms=("485BPOS",))

    assert DEFAULT_EVIDENCE_PATH.read_bytes() == before

    payload = report.summary()
    assert "status" not in payload
    assert payload["control_status_changed"] is False

    fund = report.evidence.fund_trust if report.evidence else None
    assert fund is not None
    emitted = " ".join(
        [fund.exact_name, *[f"{r.fund} {r.exchange} {r.ticker}" for r in fund.listing_rows]]
    )
    for sentinel in ("␟", "␞", "␝"):
        assert sentinel not in emitted
    for entity in ("&mdash;", "&amp;", "&reg;"):
        assert entity not in emitted


def test_a_registration_submission_still_fails_closed_on_a_bad_envelope(
    tmp_path: Path,
) -> None:
    """Requirement 19."""
    _trust_index(tmp_path)
    _write_filing(tmp_path, _registration_submission(cik=999999))
    report = _run(tmp_path, offline=True, forms=("485BPOS",))

    assert report.outcome is Outcome.VALIDATION_FAILED
    assert report.evidence is None


class TestUserAgentFromEnvFile:
    """The gitignored ``.env`` is a second home for the operator's contact string.

    Same two places, same order, as the EODHD token: one thing to set up rather
    than two, and neither of them a committed file.
    """

    def test_it_is_read_from_the_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(f"EODHD_API_KEY=x\n{USER_AGENT_ENV}=TradeIt research (a@b.c)\n")
        assert resolve_user_agent(env_file=env_file) == "TradeIt research (a@b.c)"

    def test_an_empty_value_in_the_file_counts_as_absent(self, tmp_path: Path) -> None:
        """Copying the example file must not hand back a contact-shaped nothing."""
        env_file = tmp_path / ".env"
        env_file.write_text(f"{USER_AGENT_ENV}=\n")
        with pytest.raises(ConfigError):
            resolve_user_agent(env_file=env_file)

    def test_an_explicit_env_mapping_is_exhaustive_and_the_file_is_not_consulted(
        self, tmp_path: Path
    ) -> None:
        """Otherwise a test's outcome would depend on whether the machine
        running it happened to have a ``.env``."""
        env_file = tmp_path / ".env"
        env_file.write_text(f"{USER_AGENT_ENV}=TradeIt research (a@b.c)\n")
        with pytest.raises(ConfigError):
            resolve_user_agent(None, env={}, env_file=env_file)

    def test_the_environment_still_wins_over_the_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(f"{USER_AGENT_ENV}=from-file\n")
        assert (
            resolve_user_agent(None, env={USER_AGENT_ENV: "from-env"}, env_file=env_file)
            == "from-env"
        )

    def test_a_missing_file_is_not_an_error_it_is_an_absence(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            resolve_user_agent(env_file=tmp_path / "nope.env")


ALPHABET_COVER_HTML = """
<p>Securities registered pursuant to Section 12(b) of the Act:</p>
<table>
 <tr><td>Title of each class</td><td>Trading Symbol(s)</td>
     <td>Name of each exchange on which registered</td></tr>
 <tr><td>Class A Common Stock, $0.001 par value</td><td>GOOGL</td>
     <td>Nasdaq Stock Market LLC</td></tr>
 <tr><td>(Nasdaq Global Select Market)</td></tr>
 <tr><td>Class C Capital Stock, $0.001 par value</td><td>GOOG</td>
     <td>Nasdaq Stock Market LLC</td></tr>
 <tr><td>(Nasdaq Global Select Market)</td></tr>
</table>
<p>Securities registered pursuant to Section 12(g) of the Act: None</p>
"""


def test_a_one_cell_continuation_row_does_not_end_a_multi_class_table() -> None:
    """Alphabet's cover puts "(Nasdaq Global Select Market)" on its own row
    between Class A and Class C.

    Breaking on it dropped Class C, and with it the second security of every
    multi-class issuer — which is why Alphabet had no ticker in the corpus at
    all. Narrowness was never a structural boundary; the statutory heading, the
    table end and a prose cell are.
    """
    extract = extract_identity_evidence(ALPHABET_COVER_HTML)
    symbols = {cell for row in extract.section_12b_rows for cell in row.cells}
    assert "GOOGL" in symbols
    assert "GOOG" in symbols, "the class after the continuation row was dropped"
    titles = [row.cells[0] for row in extract.section_12b_rows]
    assert any("Class A" in t for t in titles)
    assert any("Class C" in t for t in titles)


def test_the_continuation_row_itself_is_not_reported_as_a_security() -> None:
    """Skipped, not collected: a one-cell parenthetical is not a registered
    class and must not become a row of its own."""
    extract = extract_identity_evidence(ALPHABET_COVER_HTML)
    for row in extract.section_12b_rows:
        assert row.cells != ("(Nasdaq Global Select Market)",)
        assert len(row.cells) >= 3


def test_the_statutory_heading_still_ends_the_table_after_the_fix() -> None:
    """The boundary that does the real work must survive the one that did not:
    12(g) begins where 12(b) ends, whatever the row widths in between."""
    extract = extract_identity_evidence(ALPHABET_COVER_HTML)
    joined = " ".join(cell for row in extract.section_12b_rows for cell in row.cells)
    assert "None" not in joined.split()
    assert "12(g)" not in joined
