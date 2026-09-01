"""Proposing ticker to CIK candidates, and refuting the ones that cannot be true.

**The problem.** EDGAR holds 90,548 registrants keyed by CIK and carries no
tickers. EODHD holds 32,907 delisted US common stocks keyed by ticker and
carries no CIKs. Nothing in either joins them, and the two cheap shortcuts are
both measured dead ends: `company_tickers.json` covers **0.9%** of delisted
names (it lists current filers), and the vendor's ISIN is present on **31%** of
delisted rows and none of the four verified controls.

**What this module does, and what it refuses to do.** It proposes candidates by
normalised company name and then tries to *destroy* them. A surviving candidate
is still only a candidate: ``MappingEvidence.NAME_MATCH`` is already ranked
weakest in this codebase and already declared insufficient alone, and nothing
here promotes it.

> **A matching company name establishes nothing.** Two firms called "Pacific
> Enterprises" are not one firm. The value of a name match is that it *narrows*
> 90,548 registrants to a handful, which is worth doing only if the narrowing is
> then attacked.

**Refutation is the useful half.** A candidate whose trading span lies entirely
outside its CIK's filing span cannot be that registrant: a company files around
the period it trades, so disjoint spans are a contradiction rather than a weak
signal. Refutation is *sound* where confirmation is not — it can only remove,
and removing a wrong candidate costs nothing and cannot manufacture identity.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Candidate",
    "CandidateStatus",
    "normalise_company_name",
    "propose_candidates",
    "refute_by_span",
]

#: Corporate-form words carry no discriminating power and differ between
#: sources for the same company: EDGAR writes "TJX COMPANIES INC /DE/" where a
#: vendor writes "TJX Companies, Inc.". Stripped so the comparison is about the
#: distinctive part of the name and not about punctuation and legal suffixes.
#: EDGAR appends the state of incorporation as ``/DE/``, ``/NY/``, ``/MD/``.
#: It must be stripped BEFORE punctuation, or the slashes become spaces and the
#: state code survives as a word: "TJX COMPANIES INC /DE/" normalised to
#: "TJX DE" and failed to match the vendor's "TJX Companies, Inc." -> "TJX".
#: Found by a test rather than by reading, which is why the test uses a real
#: EDGAR name rather than an invented one.
_STATE_SUFFIX = re.compile(r"/[A-Z]{2}(/|$)")

_NOISE = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|COMPANIES|LTD|LIMITED|LLC|LP|PLC|"
    r"HOLDINGS?|GROUP|TRUST|FUND|THE|NEW|COM|COMMON|SA|NV|AG|CLASS [A-Z])\b"
)


class CandidateStatus(StrEnum):
    """What is known about a proposed pairing. None of these is identity."""

    #: One CIK carries this normalised name. A lead, nothing more.
    CANDIDATE = "candidate"
    #: Several CIKs carry it. Kept rather than dropped: silence would look like
    #: "no such company" when the truth is "more than one".
    AMBIGUOUS = "ambiguous"
    #: The trading span lies wholly outside the filing span. Cannot be true.
    REFUTED_BY_SPAN = "refuted_by_span"


@dataclass(frozen=True, slots=True)
class Candidate:
    ticker: str
    vendor_name: str
    cik: int | None
    edgar_name: str
    status: CandidateStatus
    #: How many CIKs shared the name. >1 is what AMBIGUOUS means.
    cik_count: int = 1
    note: str = ""


def normalise_company_name(name: str) -> str:
    """Upper-case, strip punctuation and corporate-form words, collapse spaces.

    Deliberately lossy and deliberately *not* fuzzy. An edit-distance match
    would pair "AMERICAN AIRLINES" with "AMERICAN AIRWAYS" and produce
    confident nonsense; exact comparison after normalisation either matches or
    does not, and a reader can see why.
    """
    s = _STATE_SUFFIX.sub(" ", name.upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _NOISE.sub(" ", s)
    return " ".join(s.split())


def propose_candidates(
    vendor_rows: list[dict[str, str]], edgar_names: dict[str, list[int]]
) -> list[Candidate]:
    """Pair vendor symbols to CIKs by normalised name. Proposals only."""
    out: list[Candidate] = []
    for row in vendor_rows:
        ticker = str(row.get("Code", "")).strip()
        vendor_name = str(row.get("Name", "")).strip()
        if not ticker or not vendor_name:
            continue
        key = normalise_company_name(vendor_name)
        ciks = edgar_names.get(key) or []
        if not ciks:
            continue
        if len(ciks) > 1:
            out.append(
                Candidate(
                    ticker=ticker,
                    vendor_name=vendor_name,
                    cik=None,
                    edgar_name=key,
                    status=CandidateStatus.AMBIGUOUS,
                    cik_count=len(ciks),
                    note=f"{len(ciks)} registrants share this normalised name",
                )
            )
            continue
        out.append(
            Candidate(
                ticker=ticker,
                vendor_name=vendor_name,
                cik=ciks[0],
                edgar_name=key,
                status=CandidateStatus.CANDIDATE,
            )
        )
    return out


def refute_by_span(
    candidate: Candidate,
    *,
    trading: tuple[dt.date, dt.date] | None,
    filing: tuple[dt.date, dt.date] | None,
) -> Candidate:
    """Refute a candidate whose trading and filing spans cannot both be true.

    A registrant files around the period its security trades -- before it, to
    register; during it; and often after, to deregister. So **overlap proves
    nothing** and is not treated as corroboration. Disjointness, however, is a
    contradiction: a company cannot have been trading in 1999 if EDGAR shows it
    filing only from 2008.

    Returns the candidate unchanged when either span is unknown. **An absent
    span is not evidence against a pairing**, and treating it as such would
    quietly refute every company whose data we simply have not fetched.
    """
    if candidate.status is not CandidateStatus.CANDIDATE:
        return candidate
    if trading is None or filing is None:
        return candidate
    if trading[0] <= filing[1] and filing[0] <= trading[1]:
        return candidate
    return Candidate(
        ticker=candidate.ticker,
        vendor_name=candidate.vendor_name,
        cik=candidate.cik,
        edgar_name=candidate.edgar_name,
        status=CandidateStatus.REFUTED_BY_SPAN,
        cik_count=candidate.cik_count,
        note=(
            f"traded {trading[0]}..{trading[1]} but CIK {candidate.cik} "
            f"filed {filing[0]}..{filing[1]}; disjoint"
        ),
    )
