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

**Who filed is not who the filing is about.** A header is two levels deep. Role
labels sit at column zero, bare, with nothing after the colon; everything
belonging to a role is indented beneath it in nests -- ``COMPANY DATA``,
``OWNER DATA``, ``FILING VALUES``, ``FORMER COMPANY``. A submission by its own
registrant carries one ``FILER`` nest, and reading every ``CENTRAL INDEX KEY``
in the header as one flat list answers the question by accident. A notice filed
*by an exchange* *about* an issuer carries two nests, and the flat list then
says only "this CIK appears somewhere" -- a sentence equally true when the CIK
is the subject and when it is the transmitting party. It cannot tell them
apart, so it cannot support a conclusion about either.

:attr:`SubmissionHeader.entities` therefore reports the same CIKs *with their
roles*, and the flat tuples are left exactly as they were. The structured view
is a refinement of the flat one, never a lossy re-reading::

    tuple(e.cik for e in header.entities if e.cik is not None) == header.ciks

Three rules keep that view honest. A role's fields are bound **inside a single
nest** and never copied across one -- where a structure is unusual enough that
the binding is ambiguous, the ambiguity is recorded in
:attr:`SubmissionHeader.entity_ambiguities` and the fields are left unset
rather than guessed. An unfamiliar top-level label becomes a role **only if its
block is entity-shaped** (a ``... DATA`` nest holding a central index key), so
an arbitrary header section can never masquerade as an entity. And role names
are never merged: ``FILED BY`` does not become ``FILER``, for the same reason
``DEF`` never becomes ``DEF 14A``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

__all__ = [
    "KNOWN_ENTITY_ROLES",
    "SubmissionDocument",
    "SubmissionEntity",
    "SubmissionHeader",
    "cik_roles",
    "entities_for_role",
    "header_mismatches",
    "parse_submission_header",
    "split_documents",
    "unknown_roles",
]

#: The registrant nests EDGAR's textual header is known to use, spelled the way
#: the header displays them. Membership decides only whether an unfamiliar label
#: has to prove itself entity-shaped before it counts as a role -- it is not a
#: filter, and a role outside this set still reaches the caller with its name
#: intact. Callers that need to refuse an unrecognised structure ask
#: :func:`unknown_roles`; the parser itself never silently drops one.
KNOWN_ENTITY_ROLES = frozenset(
    {
        "FILER",
        "FILED BY",
        "SUBJECT COMPANY",
        "REPORTING OWNER",
        "ISSUER",
        "SERIAL COMPANY",
        "FILED FOR",
    }
)

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

#: A role label: column zero, bare, nothing after the colon. A top-level
#: key/value line such as ``ACCESSION NUMBER:`` carries a value and so is not
#: one; a nest label such as ``COMPANY DATA:`` is indented and so is not one.
_ROLE_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 \-_/]*):[ \t]*$")
#: A nest label inside a role block. Indented, bare, nothing after the colon.
_NEST_LINE = re.compile(r"^[ \t]+([A-Za-z][A-Za-z0-9 \-_/]*):[ \t]*$")
_KEY_VALUE = re.compile(r"^[ \t]*([A-Za-z][A-Za-z0-9 \-_/]*):[ \t]*(\S.*?)[ \t]*$")


def _collapse(value: str) -> str:
    """Strip the ends and collapse internal whitespace runs to one space."""
    return re.sub(r"\s+", " ", value.strip())


def _canonical_label(label: str) -> str:
    """One spelling for a header label, so a separator cannot hide a role.

    Case and whitespace are layout. So is the separator EDGAR chooses between
    the words of a label: the reporting-owner nest is emitted hyphenated, and a
    vocabulary written in display spelling would silently fail to recognise it.
    Collapsing ``-`` and ``_`` to a space is the same class of repair as
    collapsing a tab -- it changes how the label is *written*, never which label
    it is. The label as it actually appeared is kept on
    :attr:`SubmissionEntity.role_label` so the question stays answerable.
    """
    return _collapse(re.sub(r"[-_]", " ", label)).upper()


def _as_date(value: str | None) -> dt.date | None:
    if value is None:
        return None
    return dt.date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


@dataclass(frozen=True, slots=True)
class SubmissionEntity:
    """One party named by the header, together with the role it holds.

    Every field is read from **one** role/nest grouping. Nothing is carried in
    from a sibling block, because the whole point of the structure is that the
    blocks describe different parties.
    """

    #: The canonical role: ``FILER``, ``SUBJECT COMPANY``, ``FILED BY`` and so
    #: on. ``None`` means this central index key could not be bound to a role --
    #: it sat outside every role block, outside every ``... DATA`` nest, or
    #: under a top-level label that was not entity-shaped. ``None`` is a refusal
    #: to guess, never a default, and a caller asserting a role gets a failure
    #: rather than a plausible-looking answer.
    role: str | None
    #: The label exactly as the header wrote it, before canonicalisation.
    role_label: str | None
    cik: int | None
    company_name: str | None
    #: This block's ``FILING VALUES`` form type -- a *fourth* separate statement
    #: of what the submission is, alongside the index row, the envelope's
    #: ``CONFORMED SUBMISSION TYPE`` and each document's ``<TYPE>``. Recorded,
    #: never merged with any of them.
    form_type: str | None = None
    file_numbers: tuple[str, ...] = ()


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
    #: The same central index keys as :attr:`ciks`, in the same order, each with
    #: the role it holds. Additive: the flat tuples above keep their exact
    #: previous meaning, so no existing caller changes because this exists.
    entities: tuple[SubmissionEntity, ...] = ()
    #: Structures unusual enough that a binding could not be made safely. Each
    #: entry names what was left unset and why. A header that parses cleanly
    #: reports none.
    entity_ambiguities: tuple[str, ...] = ()


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


def _parse_entities(head: str) -> tuple[tuple[SubmissionEntity, ...], tuple[str, ...]]:
    """Split the header into role blocks and bind each party inside its own nest.

    One entity is emitted per ``CENTRAL INDEX KEY`` line, in source order, which
    is what makes the structured view a refinement of :attr:`ciks` rather than a
    second opinion about it.
    """
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in head.splitlines():
        role_line = _ROLE_LINE.match(line)
        if role_line:
            blocks.append((role_line.group(1), []))
        else:
            blocks[-1][1].append(line)

    entities: list[SubmissionEntity] = []
    notes: list[str] = []
    for label, lines in blocks:
        found: list[tuple[int, str | None, bool]] = []
        form_type: str | None = None
        file_numbers: list[str] = []
        nest: str | None = None
        pending_name: str | None = None
        for line in lines:
            nest_line = _NEST_LINE.match(line)
            if nest_line:
                nest = _canonical_label(nest_line.group(1))
                pending_name = None
                continue
            pair = _KEY_VALUE.match(line)
            if pair is None:
                continue
            key = _canonical_label(pair.group(1))
            value = _collapse(pair.group(2))
            # A ``... DATA`` nest is the only place a party is described. A name
            # or key anywhere else is not bound, so it cannot bleed onto a
            # neighbour.
            in_data = nest is not None and nest.endswith(" DATA")
            if key == "COMPANY CONFORMED NAME" and in_data:
                pending_name = value
            elif key == "CENTRAL INDEX KEY" and value.isdigit():
                found.append((int(value), pending_name if in_data else None, in_data))
                # Consumed: a second key in the same nest is a structure this
                # parser cannot attribute, and must not inherit the first name.
                pending_name = None
            elif key == "FORM TYPE" and nest == "FILING VALUES":
                form_type = value
            elif key == "SEC FILE NUMBER" and nest == "FILING VALUES":
                file_numbers.append(value)

        canonical = _canonical_label(label) if label is not None else None
        entity_shaped = any(in_data for _cik, _name, in_data in found)
        block_role: str | None
        if canonical is None:
            block_role = None
        elif canonical in KNOWN_ENTITY_ROLES:
            block_role = canonical
        elif entity_shaped:
            block_role = canonical
            notes.append(
                f"top-level label {canonical!r} is not a known entity role; accepted as one "
                f"because the block is entity-shaped"
            )
        else:
            block_role = None
            if found:
                notes.append(
                    f"top-level label {canonical!r} is not a known entity role and the block is "
                    f"not entity-shaped; its central index keys are left unattributed"
                )

        # FILING VALUES describe the block. With several parties in one block
        # there is no way to say which they describe, so they describe none.
        attributable = len(found) == 1 and block_role is not None
        if len(found) > 1 and (form_type is not None or file_numbers):
            notes.append(
                f"role block {canonical!r} carries FILING VALUES but names {len(found)} central "
                f"index keys; the form type and file numbers were not copied onto any of them"
            )
        for cik, name, in_data in found:
            if not in_data:
                notes.append(
                    f"CIK {cik} is not inside a ... DATA nest; its role and company name are "
                    f"left unbound rather than taken from the surrounding block"
                )
            entities.append(
                SubmissionEntity(
                    role=block_role if in_data else None,
                    role_label=label.strip() if label is not None else None,
                    cik=cik,
                    company_name=name,
                    form_type=form_type if attributable else None,
                    file_numbers=tuple(file_numbers) if attributable else (),
                )
            )
    return tuple(entities), tuple(notes)


def entities_for_role(header: SubmissionHeader, role: str) -> tuple[SubmissionEntity, ...]:
    """Every entity holding exactly ``role``.

    Exact equality on the canonical label -- never prefix, never substring. That
    is not pedantry: ``FILED BY`` *contains* ``FILED``, and a loose match would
    let the party who transmitted a notice answer a question asked about the
    party who filed it, in the same way a prefix match lets ``10-K/A`` answer
    for ``10-K``.
    """
    wanted = _canonical_label(role)
    return tuple(e for e in header.entities if e.role == wanted)


def cik_roles(header: SubmissionHeader, cik: int) -> tuple[str | None, ...]:
    """Every role this CIK holds in this header, in the order it appears.

    This is the instrument for a role-sensitive question, and its shape is
    deliberate: the natural assertion is equality against a frozen tuple, e.g.
    ``cik_roles(header, 806085) == ("SUBJECT COMPANY",)``. That fails if the CIK
    is the filer, fails if it is unattributed, and fails if it holds two roles.
    There is no way to write it that decays into "appears somewhere".
    """
    return tuple(e.role for e in header.entities if e.cik == cik)


def unknown_roles(header: SubmissionHeader) -> tuple[str, ...]:
    """Entity roles outside :data:`KNOWN_ENTITY_ROLES`, first appearance first.

    Only *entity* roles: a top-level header section that was never entity-shaped
    is not a role and is not reported here. A caller that must refuse a
    structure this project has not reasoned about stops when this is non-empty.
    """
    seen: list[str] = []
    for entity in header.entities:
        role = entity.role
        if role is not None and role not in KNOWN_ENTITY_ROLES and role not in seen:
            seen.append(role)
    return tuple(seen)


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
    entities, ambiguities = _parse_entities(head)
    return SubmissionHeader(
        submission_type=_collapse(submission_type) if submission_type else None,
        accession=accession.strip() if accession else None,
        filed_at=_as_date(_first(_FILED_AS_OF, head)),
        period_of_report=_as_date(_first(_PERIOD, head)),
        date_of_change=_as_date(_first(_DATE_OF_CHANGE, head)),
        ciks=tuple(int(c) for c in _CENTRAL_INDEX_KEY.findall(head)),
        company_names=tuple(_collapse(n) for n in _COMPANY_NAME.findall(head)),
        file_numbers=tuple(_collapse(n) for n in _FILE_NUMBER.findall(head)),
        entities=entities,
        entity_ambiguities=ambiguities,
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

    The CIK check asks whether the requested registrant appears **anywhere in
    the header**, not whether it is the only one: a co-registrant is normal and
    is not grounds for refusal. That is deliberately weak, and it is weaker than
    it sounds -- it does not ask in what *role* the CIK appears, so it is
    satisfied alike by a filing the registrant made and by a notice someone else
    filed about it. Ask :func:`cik_roles` when the role is the point. Which
    registrant a *sentence* is about remains a question for the body, and the
    header cannot answer it at all.
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
