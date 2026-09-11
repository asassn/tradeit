"""What an 8-K says it reports, read from its own SGML header.

**The gap this closes.** :mod:`tradeit.edgar.evidence` marks every 8-K signal
``requires_document_text``, because the quarterly full-index carries no item
numbers: an 8-K in the index is just "an 8-K", and a bankruptcy is
indistinguishable from a change of auditor. That is true of the index. It is
not true of the filing.

**Every 8-K's header lists its items**, in both numbering schemes. Measured on
sec.gov on 2026-09-11 before anything was built on it::

    2005 filing  <ITEMS>1.01  <ITEMS>8.01  <ITEMS>9.01
    2002 filing  <ITEMS>5     <ITEMS>7

The header is the filer's own declaration of what the filing reports, in about
850 bytes, beside a document that can run to megabytes. That is the evidence
this module reads -- and **it reads nothing else**: an item number says *which
kind* of event the filing reports, never whether it was the registrant's own
bankruptcy or a subsidiary's, and never the terms of a deal. A caller that
needs those must read the document.

**Two schemes, told apart by shape and date.** The August 2004 overhaul
replaced single-integer items with dotted ones. Item ``"3"`` before it and
``"1.03"`` after it both mean *bankruptcy or receivership*; item ``"1"`` before
and ``"5.01"`` after both mean *change in control*. A bare integer on a filing
dated after the overhaul is read as nothing at all, because guessing which
scheme a malformed header meant is how an auditor change becomes a bankruptcy.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum

from tradeit.edgar.evidence import EIGHT_K_ITEM_NUMBERING_FROM

__all__ = [
    "EightKHeader",
    "ItemMeaning",
    "item_meanings",
    "parse_8k_header",
]


class ItemMeaning(StrEnum):
    """The handful of 8-K items that bear on why a security stopped trading."""

    BANKRUPTCY = "bankruptcy_or_receivership"
    CHANGE_IN_CONTROL = "change_in_control"
    #: Completion of an acquisition **or disposition** of assets. On the
    #: acquirer's 8-K it means it bought something; on a target's it can mean
    #: it sold everything. Never sufficient alone -- see ``exit_cause``.
    ACQUISITION_OR_DISPOSITION_COMPLETED = "acquisition_or_disposition_completed"
    #: 2004 scheme only. Filed both when an exchange delists for cause and when
    #: a merger extinguishes the listing, so it separates nothing on its own.
    DELISTING_NOTICE = "delisting_notice"
    #: 2004 scheme only; typically the merger's conversion of the shares.
    SECURITY_RIGHTS_MODIFIED = "security_rights_modified"
    #: 2004 scheme only; a merger agreement is one, so is a credit line.
    MATERIAL_AGREEMENT = "material_agreement"


#: The August 2004 scheme.
_DOTTED: dict[str, ItemMeaning] = {
    "1.01": ItemMeaning.MATERIAL_AGREEMENT,
    "1.03": ItemMeaning.BANKRUPTCY,
    "2.01": ItemMeaning.ACQUISITION_OR_DISPOSITION_COMPLETED,
    "3.01": ItemMeaning.DELISTING_NOTICE,
    "3.03": ItemMeaning.SECURITY_RIGHTS_MODIFIED,
    "5.01": ItemMeaning.CHANGE_IN_CONTROL,
}

#: The scheme it replaced. Items 4-12 (auditor change, other events, director
#: resignations, exhibits, fiscal year, Regulation FD, ethics code, blackout,
#: results) say nothing about why trading stopped, so they map to nothing.
_LEGACY: dict[str, ItemMeaning] = {
    "1": ItemMeaning.CHANGE_IN_CONTROL,
    "2": ItemMeaning.ACQUISITION_OR_DISPOSITION_COMPLETED,
    "3": ItemMeaning.BANKRUPTCY,
}

_TAG = re.compile(r"^<([A-Z-]+)>(.*)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class EightKHeader:
    """The fields of an 8-K header this project uses, exactly as filed."""

    accession: str
    #: Every ``<FILER>`` block's CIK, in header order. Joint filings are real:
    #: Equity Office Properties Trust and its operating partnership filed their
    #: 8-Ks together, and reading only the first block refused 23 of the first
    #: 702 headers fetched as belonging to someone else. ``<SUBJECT-COMPANY>``
    #: CIKs are deliberately absent -- a filing *about* a company is not one
    #: *by* it.
    filer_ciks: tuple[int, ...]
    form_type: str
    filing_date: dt.date
    #: The date of the earliest event reported -- the filer's ``<PERIOD>``.
    period: dt.date | None
    #: Verbatim, in header order. ``"5"`` and ``"5.01"`` are not normalised
    #: into each other; which scheme a number belongs to is itself evidence.
    items: tuple[str, ...]

    @property
    def cik(self) -> int:
        """The first filer -- the one a single-filer header has."""
        return self.filer_ciks[0]

    def filed_by(self, cik: int) -> bool:
        return cik in self.filer_ciks


_FILER_BLOCK = re.compile(r"<FILER>(.*?)</FILER>", re.DOTALL)
_CIK = re.compile(r"^<CIK>(\d+)\s*$", re.MULTILINE)


def _date(value: str) -> dt.date | None:
    value = value.strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        return None


def parse_8k_header(body: str) -> EightKHeader | None:
    """Parse an 8-K's ``.hdr.sgml``, or return ``None`` if it is not one.

    ``None`` covers every reason to refuse: no ``<SEC-HEADER>`` (a throttle page
    arrives with HTTP 200), no accession, no filer CIK, no filing date, or a
    form type that is not an 8-K. **Only CIKs inside ``<FILER>`` blocks count**,
    and a caller must still check the one it asked about with
    :meth:`EightKHeader.filed_by`.
    """
    if "<SEC-HEADER>" not in body:
        return None
    first: dict[str, str] = {}
    items: list[str] = []
    for tag, value in _TAG.findall(body):
        value = value.strip()
        if tag == "ITEMS":
            if value:
                items.append(value)
        elif tag not in first:
            first[tag] = value
    form_type = first.get("TYPE", "")
    accession = first.get("ACCESSION-NUMBER", "")
    filed = _date(first.get("FILING-DATE", ""))
    filers = tuple(int(cik) for block in _FILER_BLOCK.findall(body) for cik in _CIK.findall(block))
    if not form_type.upper().startswith("8-K") or not accession or filed is None:
        return None
    if not filers:
        return None
    return EightKHeader(
        accession=accession,
        filer_ciks=filers,
        form_type=form_type,
        filing_date=filed,
        period=_date(first.get("PERIOD", "")),
        items=tuple(items),
    )


def item_meanings(header: EightKHeader) -> frozenset[ItemMeaning]:
    """What the listed items mean, under the scheme in force when it was filed.

    A dotted item is read under the 2004 scheme whenever it appears. A bare
    integer is read under the legacy scheme **only before the overhaul**; after
    it, a bare integer is refused rather than guessed at.
    """
    meanings: set[ItemMeaning] = set()
    legacy_allowed = header.filing_date < EIGHT_K_ITEM_NUMBERING_FROM
    for item in header.items:
        if "." in item:
            meaning = _DOTTED.get(item)
        elif legacy_allowed:
            meaning = _LEGACY.get(item)
        else:
            meaning = None
        if meaning is not None:
            meanings.add(meaning)
    return frozenset(meanings)
