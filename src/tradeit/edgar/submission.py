"""Reading one EDGAR submission: its SGML header and its documents.

**Why this module exists.** Every checkpoint that downloads a filing has to
answer one question before reading a word of it -- *is this the document I
asked for?* That check was written by hand, as a fresh regex, each time. One of
those regexes was::

    re.search(r"CONFORMED SUBMISSION TYPE:\\s*(\\S+)", header)

``\\S+`` stops at the first space, so ``DEF 14A`` was captured as ``DEF`` and
compared, correctly and uselessly, against the index's ``DEF 14A``. The
validation gate then refused a filing that was exactly right. Four of
thirty-four locally stored submissions reproduced the same truncation --
``POS AM`` to ``POS``, ``PRE 14A`` to ``PRE``, ``DEF 14A`` to ``DEF`` -- and it
had gone unnoticed only because every form validated until then (``10-K``,
``10-Q``, ``8-A12B``) happens to be a single token.

The repository already knew this. :mod:`tradeit.edgar.index` parses the
quarterly index right-anchored precisely so that multi-word form types survive,
and the operator standard bans ``line.split()[n]`` for a form type by name. The
lesson was recorded and then re-broken through a different construct, which is
the argument for a shared, tested reader rather than a better ad-hoc regex.

**Three separate facts, deliberately not merged.** A submission carries three
different statements of what it is, and they are *different claims about
different things*:

- the **master-index form type**, from ``form.idx`` -- what EDGAR's catalogue
  says the submission is;
- the top-level SGML **CONFORMED SUBMISSION TYPE** -- what the submission's own
  envelope says it is;
- each document's **<TYPE>** -- what one file inside the envelope says it is.

They usually agree. When they do not, that disagreement is evidence about the
filing and must reach a human intact, so nothing here renames one to another.

**Whitespace is normalised; names never are.** ``submission_type`` is stripped
and internal whitespace runs collapse to single spaces, so a tab-separated
header reads as ``DEF 14A``. That is a whitespace repair, not a rename: no
alias table exists, ``DEF`` never becomes ``DEF 14A``, and
:func:`header_mismatches` compares by exact equality on the whole value.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

__all__ = [
    "SubmissionDocument",
    "SubmissionHeader",
    "header_mismatches",
    "parse_submission_header",
    "split_documents",
]

#: The header ends where the first document begins.
_HEADER_END = "<DOCUMENT>"

_ACCESSION = re.compile(r"^\s*ACCESSION NUMBER:\s*(\S+)\s*$", re.M)
#: Captures the REST OF THE LINE. Never ``\S+`` -- see the module docstring.
_SUBMISSION_TYPE = re.compile(r"^\s*CONFORMED SUBMISSION TYPE:\s*(\S.*?)\s*$", re.M)
_FILED_AS_OF = re.compile(r"^\s*FILED AS OF DATE:\s*(\d{8})\s*$", re.M)
_PERIOD = re.compile(r"^\s*CONFORMED PERIOD OF REPORT:\s*(\d{8})\s*$", re.M)
_DATE_OF_CHANGE = re.compile(r"^\s*DATE AS OF CHANGE:\s*(\d{8})\s*$", re.M)
_CENTRAL_INDEX_KEY = re.compile(r"^\s*CENTRAL INDEX KEY:\s*(\d+)\s*$", re.M)
_COMPANY_NAME = re.compile(r"^\s*COMPANY CONFORMED NAME:\s*(\S.*?)\s*$", re.M)
_FILE_NUMBER = re.compile(r"^\s*SEC FILE NUMBER:\s*(\S.*?)\s*$", re.M)

_DOCUMENT = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.S)


def _collapse(value: str) -> str:
    """Strip the ends and collapse internal whitespace runs to one space."""
    return re.sub(r"\s+", " ", value.strip())


def _as_date(value: str | None) -> dt.date | None:
    if value is None:
        return None
    return dt.date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


@dataclass(frozen=True, slots=True)
class SubmissionHeader:
    """The SGML envelope's own account of what this submission is."""

    #: Full ``CONFORMED SUBMISSION TYPE``, whitespace-normalised, never aliased.
    submission_type: str | None
    accession: str | None
    #: ``FILED AS OF DATE``. The only date that may be called the filing date.
    filed_at: dt.date | None
    #: ``CONFORMED PERIOD OF REPORT``. A period covered, not a filing date.
    period_of_report: dt.date | None
    #: ``DATE AS OF CHANGE``. Metadata about the record, not an event.
    date_of_change: dt.date | None
    #: Every ``CENTRAL INDEX KEY`` in the header, in order, as integers. A
    #: submission may carry several FILER blocks, and a co-registrant is not
    #: the registrant -- keeping all of them is what makes that checkable.
    ciks: tuple[int, ...] = ()
    company_names: tuple[str, ...] = ()
    file_numbers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmissionDocument:
    """One ``<DOCUMENT>`` inside the envelope, kept apart from its siblings."""

    sequence: str
    #: This document's own ``<TYPE>``. Not the submission type.
    document_type: str
    filename: str
    description: str
    #: The raw block, tags and all. Text extraction is the caller's business.
    raw: str = field(repr=False, default="")


def parse_submission_header(text: str) -> SubmissionHeader:
    """Read the SGML header of one submission.

    ``text`` may be the whole ``.txt`` submission or just its header; anything
    from the first ``<DOCUMENT>`` onward is ignored either way, so a document
    body cannot contribute a field the envelope did not state.
    """
    cut = text.find(_HEADER_END)
    head = text if cut < 0 else text[:cut]

    submission_type = _first(_SUBMISSION_TYPE, head)
    accession = _first(_ACCESSION, head)
    return SubmissionHeader(
        submission_type=_collapse(submission_type) if submission_type else None,
        accession=accession.strip() if accession else None,
        filed_at=_as_date(_first(_FILED_AS_OF, head)),
        period_of_report=_as_date(_first(_PERIOD, head)),
        date_of_change=_as_date(_first(_DATE_OF_CHANGE, head)),
        ciks=tuple(int(c) for c in _CENTRAL_INDEX_KEY.findall(head)),
        company_names=tuple(_collapse(n) for n in _COMPANY_NAME.findall(head)),
        file_numbers=tuple(_collapse(n) for n in _FILE_NUMBER.findall(head)),
    )


def split_documents(text: str) -> tuple[SubmissionDocument, ...]:
    """Every ``<DOCUMENT>`` in the submission, in order.

    A submission is not a flat envelope. An exhibit answers a different question
    than the primary document does, and a finding has to name which one it came
    from, so the blocks are never concatenated.
    """
    out: list[SubmissionDocument] = []
    for block in _DOCUMENT.findall(text):

        def tag(name: str, source: str = block) -> str:
            match = re.search(rf"^<{name}>(.*)$", source, re.M)
            return match.group(1).strip() if match else ""

        out.append(
            SubmissionDocument(
                sequence=tag("SEQUENCE"),
                document_type=tag("TYPE"),
                filename=tag("FILENAME"),
                description=tag("DESCRIPTION"),
                raw=block,
            )
        )
    return tuple(out)


def header_mismatches(
    header: SubmissionHeader,
    *,
    accession: str,
    form_type: str,
    filed_at: dt.date,
    cik: int,
) -> tuple[str, ...]:
    """Why this submission is **not** the one the index row described.

    Empty means it is. Each expectation comes from the verified index row, and
    the form is compared by **exact equality on the whole value** -- never by
    prefix, which would accept ``10-K/A`` for ``10-K``, and never through an
    alias, which would accept a different form outright.

    The CIK check asks whether the requested registrant appears among the
    header's FILER blocks, not whether it is the only one: a co-registrant is
    normal and is not grounds for refusal. Which registrant a *sentence* is
    about remains a question for the body, and the header cannot answer it.
    """
    problems: list[str] = []
    if header.accession != accession:
        problems.append(f"ACCESSION NUMBER is {header.accession!r}, expected {accession!r}")
    expected_form = _collapse(form_type).upper()
    actual_form = header.submission_type.upper() if header.submission_type else None
    if actual_form != expected_form:
        problems.append(
            f"CONFORMED SUBMISSION TYPE is {header.submission_type!r}, expected {form_type!r}"
        )
    if header.filed_at != filed_at:
        problems.append(f"FILED AS OF DATE is {header.filed_at}, expected {filed_at}")
    if cik not in header.ciks:
        problems.append(f"CIK {cik} absent; header CENTRAL INDEX KEYs are {list(header.ciks)}")
    return tuple(problems)
