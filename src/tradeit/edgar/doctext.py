"""Two facts read from filing text, because the header alone was wrong once.

**Why document text at all.** :mod:`tradeit.edgar.eightk` reads item numbers
from an 8-K's header, which is the filer's own declaration and right nearly
always. Nearly: General Instrument's 8-K of 1999-04-02 declares items ``3, 7``
-- *bankruptcy or receivership* -- in its header, and its text says **"Item 2.
ACQUISITION OR DISPOSITION OF ASSETS"**. General Instrument was acquired by
Motorola nine months later. The filing agent mis-keyed the header, and a
classifier trusting it booked an acquisition at a premium as a total loss.

So for the one finding where an error costs the most, the text must agree.

**The second fact is on Form 15**, which certifies the *approximate number of
holders of record* of the class being deregistered. After a merger that number
is the acquirer -- one -- or none at all; a registrant going dark because it is
small certifies its remaining holders. Measured on seven died-arm securities
before relying on it: Dal-Tile "ZERO", Lamar Capital "0", Eagle Bancshares "1",
Network Six "One (1)", Workgroup Technology "One Shareholder" -- all bought --
against DSI Toys "25", in Chapter 11 that year.

**Neither function concludes anything about a company.** They report what one
document says; ``exit_cause`` decides what that is evidence of. And nothing a
program reads here is ``MANUAL_VERIFIED``.
"""

from __future__ import annotations

import html
import re

__all__ = ["bankruptcy_heading_present", "form15_holders_of_record", "plain_text"]

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

#: "Item 1.03 Bankruptcy or Receivership" (2004 scheme) and "Item 3. Bankruptcy
#: or Receivership" (legacy) -- as filers actually write them, which the first
#: version of this pattern did not allow for. Measured on the first three
#: headers it "contradicted", two were real bankruptcies it misread:
#:
#: * Kentucky Electric Steel, 2003: "Item 3. Bankruptcy **and** Receivership";
#: * Federal-Mogul, 2007: "Item 1.03 **and Item 8.01**. Bankruptcy or
#:   Receivership and Other Events".
#:
#: So the connective may be *or*, *and* or *&*, and the item number may head a
#: **list** -- "Item 1.03 and Item 8.01" -- joined by *and*, a comma or *&*.
#: Any *other* ``item N`` between number and title is a different item, and
#: blocks the match: "Item 3. Not applicable. Item 5. Other Events ... no
#: bankruptcy or receivership" is not a heading.
_BANKRUPTCY = re.compile(
    r"item\s*(?:1\s*\.\s*03|3)\b"
    r"(?:\s*(?:,|and|&)\s*item\s*\d+(?:\s*\.\s*\d+)?)*"
    r"(?:(?!item\s*\d).){0,40}?"
    r"bankruptcy\s*(?:or|and|&)\s*receivership",
    re.IGNORECASE,
)

_HOLDERS = re.compile(
    r"approximate\s+number\s+of\s+holders\s+of\s+record.{0,80}?date\s*:?\s*(.{0,40})",
    re.IGNORECASE,
)

_WORDS = {
    "zero": 0,
    "none": 0,
    "no": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def plain_text(document: str) -> str:
    """Tags stripped, entities decoded, whitespace collapsed."""
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", document))).strip()


def _without_header(submission: str) -> str:
    """The submission from its first ``<DOCUMENT>`` on.

    A full ``.txt`` submission begins with a copy of the SGML header, which for
    an 8-K reads "ITEM INFORMATION: Bankruptcy or receivership". Checking the
    header against the document is only a check if the header is not also
    being read as the document.
    """
    start = submission.find("<DOCUMENT>")
    return submission if start < 0 else submission[start:]


def bankruptcy_heading_present(document: str) -> bool:
    """Does the filing's own text carry a bankruptcy-or-receivership item heading?"""
    return bool(_BANKRUPTCY.search(plain_text(_without_header(document))))


def form15_holders_of_record(document: str) -> int | None:
    """The holder count a Form 15 certifies, or ``None`` if it cannot be read.

    Digits and small numbers in words are both real -- filers write "0",
    "ZERO", "One (1)" and "One Shareholder". Anything else, including the
    common "less than 300", is ``None``: a bound is not a count, and guessing
    one would make a small registrant going dark look like a merger.
    """
    match = _HOLDERS.search(plain_text(document))
    if match is None:
        return None
    tail = match.group(1).strip(" .:-_\u2013\u2014").lower()
    digits = re.match(r"(\d[\d,]*)", tail)
    if digits:
        return int(digits.group(1).replace(",", ""))
    word = re.match(r"([a-z]+)", tail)
    if word and word.group(1) in _WORDS:
        # "no" only counts when it is "no holders", not "not applicable".
        if word.group(1) == "no" and not tail.startswith("no holder"):
            return None
        return _WORDS[word.group(1)]
    return None
