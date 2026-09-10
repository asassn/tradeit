"""Standard Industrial Classification, read from the filing that carried it.

Every EDGAR filing's SGML header states the filer's SIC code as the SEC had it
*on the day of that filing*:

    STANDARD INDUSTRIAL CLASSIFICATION:  SERVICES-PREPACKAGED SOFTWARE [7372]

That makes SIC the one sector classification this project can obtain without a
vendor, and -- more importantly -- the one it can obtain **point-in-time**. A
company reclassified in 2015 has one SIC in its 2010 filings and another in its
2020 filings, and a backtest that used today's classification for a 2010
session would be using a fact nobody had.

What this module refuses to do
------------------------------

**It does not invent a sector taxonomy.** SIC divisions are a published
standard with fixed numeric ranges, and mapping a code to its division is
mechanical. Mapping a division to "Technology" or "Consumer Discretionary" is
not: those are vendor taxonomies, every vendor draws them differently, and a
home-made one would be a judgement wearing the appearance of a fact.

So a classification here carries three things: the **code**, the **description
exactly as the filing wrote it**, and the **official division**. Anything
finer-grained is a decision for whoever needs it, made visibly.

The description is preserved verbatim, per the standing rule about quoted text.
``SERVICES-PREPACKAGED SOFTWARE`` is not normalised to ``Prepackaged Software``:
the SEC's own wording is the evidence, and the day it changes that wording is a
day this project would want to see the change rather than have it smoothed
away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "DIVISIONS",
    "SicClassification",
    "division_of",
    "parse_sic_header",
]

#: The official SIC divisions, as inclusive code ranges.
#:
#: These are the published standard, not a choice made here. Codes above 9729
#: are the SEC's own "nonclassifiable" range, which it does use -- 6770 blank
#: checks and 9995 non-operating establishments both appear in EDGAR.
DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Agriculture, Forestry and Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation, Communications, Electric, Gas and Sanitary Services"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance, Insurance and Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9729, "Public Administration"),
    (9900, 9999, "Nonclassifiable Establishments"),
)

#: The header line, whatever is left of it. Everything after the colon up to
#: the end of the line is taken, and the code is picked out of that -- because
#: **four shapes occur in this corpus and only one of them is the modern one.**
#: Measured across the filings held locally:
#:
#: * ``SERVICES-PREPACKAGED SOFTWARE [7372]`` -- description and code, modern
#: * ``[4813]`` or `` [4813]`` -- code alone, no description, seen 1999-2000
#: * ``6211`` -- bare code, no brackets at all, seen in 1994 filings
#: * ``[4813`` with the ``]`` on the **next line** -- the header wraps, seen
#:   1996-2000. The closing bracket carries no information, so its absence on
#:   the line is not a reason to discard the code
#: * ``[]`` -- empty, no classification stated
#:
#: A parser written against the first shape alone silently loses the other
#: three, which are exactly the oldest filings and therefore exactly the ones a
#: survivorship study most needs.
_HEADER_LINE = re.compile(
    r"STANDARD\s+INDUSTRIAL\s+CLASSIFICATION:[ \t]*(?P<rest>[^\r\n]*)",
    re.IGNORECASE,
)

#: A bracketed code, or -- failing that -- a bare one. Bracketed is tried first
#: because a description can contain digits (``WHOLESALE-PETROLEUM & PETROLEUM
#: PRODUCTS (NO BULK STATIONS)`` does not, but ``RETAIL-EATING PLACES`` sits
#: beside codes that do), and the brackets are unambiguous where a loose number
#: is not.
_BRACKETED = re.compile(r"\[\s*(?P<code>\d{3,4})\s*\]")
#: The wrapped case: an opening bracket and a code, the closing bracket having
#: fallen onto the following line.
_BRACKET_OPEN = re.compile(r"\[\s*(?P<code>\d{3,4})\s*$")
_BARE = re.compile(r"^\s*(?P<code>\d{3,4})\s*$")


@dataclass(frozen=True, slots=True)
class SicClassification:
    """One filer's classification, as one filing stated it."""

    code: int
    description: str
    division: str

    @property
    def major_group(self) -> int:
        """The two-digit group, e.g. 73 for 7372. Coarser than the code."""
        return self.code // 100

    def __post_init__(self) -> None:
        if not 100 <= self.code <= 9999:
            raise ValueError(f"SIC code {self.code} is outside the 100-9999 range")


def division_of(code: int) -> str | None:
    """The official division for a code, or ``None`` if it falls in no range.

    ``None`` rather than a catch-all: the ranges have real gaps -- 1800-1999
    and 9730-9899 are unassigned -- and a code landing there is a fact about
    the filing worth seeing, not something to file under "other".
    """
    for low, high, name in DIVISIONS:
        if low <= code <= high:
            return name
    return None


def parse_sic_header(text: str) -> SicClassification | None:
    """Read the classification out of a filing's SGML header.

    Returns ``None`` when the header does not state one, which is a real case:
    of 331 filings held locally, 5 carry no SIC line at all.

    Only the header is searched by the caller passing a truncated document --
    the phrase can appear in the body of a filing that *discusses* SIC codes,
    and the header is the only place it is an assertion about this filer.
    """
    line = _HEADER_LINE.search(text)
    if line is None:
        return None
    rest = line.group("rest")

    bracketed = _BRACKETED.search(rest) or _BRACKET_OPEN.search(rest)
    if bracketed is not None:
        code_text = bracketed.group("code")
        description = rest[: bracketed.start()].strip()
    else:
        bare = _BARE.match(rest)
        if bare is None:
            # An empty ``[]``, or a line stating no code at all. A real case,
            # and reported as absence rather than guessed at.
            return None
        code_text = bare.group("code")
        description = ""

    code = int(code_text)
    if not 100 <= code <= 9999:
        return None
    division = division_of(code)
    if division is None:
        return None
    return SicClassification(
        code=code,
        # Verbatim, and empty when the filing gave none. See the module
        # docstring: an absent description is not an occasion to invent one
        # from the code, because the SEC's own wording is the evidence.
        description=description,
        division=division,
    )
