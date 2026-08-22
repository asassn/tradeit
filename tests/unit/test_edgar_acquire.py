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
