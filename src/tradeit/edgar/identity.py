"""CIK to ticker, with the four states -- and the refusal to invent the mapping.

EDGAR is CIK-centric. Old filings do not carry authoritative tickers, and the
SEC's own ``company_tickers.json`` covers only what is listed *today*, which is
exactly the population survivorship research is not about. So a large fraction
of pre-2009 registrants cannot be mapped, and the honest response is to say so
in a way that survives into every downstream number.

``UNRESOLVED`` is therefore a first-class state, not an error. A denominator
with 30% unresolved identity is still a valid denominator -- it simply reports
two coverage bounds instead of one, and the gap between them is the measurement
of its own uncertainty.

**The rule that does the work:** name matching may never on its own produce
``RESOLVED``. Two companies called "Acme Technologies Inc" thirty years apart
are not evidence, and a mapping built that way would fabricate exactly the thing
being measured. The strongest a name match can reach is ``AMBIGUOUS``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.errors import ConfigError

__all__ = [
    "MappingEvidence",
    "MappingStatus",
    "SecurityMapping",
    "mapping_counts",
    "resolve_mapping",
]


class MappingStatus(StrEnum):
    #: A human checked primary evidence and recorded a citation.
    MANUAL_VERIFIED = "manual_verified"
    #: One defensible mapping from a dated primary source.
    RESOLVED = "resolved"
    #: Several plausible mappings, or evidence too weak to choose.
    AMBIGUOUS = "ambiguous"
    #: No defensible mapping found. Counts in the denominator, not the numerator.
    UNRESOLVED = "unresolved"


class MappingEvidence(StrEnum):
    """Ranked strongest first. The rank is the tuple order below."""

    MANUAL_FILING_CITATION = "manual_filing_citation"
    SEC_COMPANY_TICKERS = "sec_company_tickers"
    FILING_DOCUMENT_TEXT = "filing_document_text"
    SUBMISSIONS_FORMER_NAMES = "submissions_former_names"
    FULL_TEXT_SEARCH = "full_text_search"
    #: Weakest. Can never on its own produce RESOLVED.
    NAME_MATCH = "name_match"


_RANK: tuple[MappingEvidence, ...] = (
    MappingEvidence.MANUAL_FILING_CITATION,
    MappingEvidence.SEC_COMPANY_TICKERS,
    MappingEvidence.FILING_DOCUMENT_TEXT,
    MappingEvidence.SUBMISSIONS_FORMER_NAMES,
    MappingEvidence.FULL_TEXT_SEARCH,
    MappingEvidence.NAME_MATCH,
)

#: Evidence that cannot, alone, establish a mapping.
_INSUFFICIENT_ALONE = frozenset({MappingEvidence.NAME_MATCH, MappingEvidence.FULL_TEXT_SEARCH})


@dataclass(frozen=True, slots=True)
class SecurityMapping:
    """One CIK's ticker attribution, with the reason it is believed."""

    cik: int | None
    ticker: str | None
    status: MappingStatus
    evidence: MappingEvidence | None = None
    #: Required for MANUAL_VERIFIED: accession, URL, or another checkable pointer.
    citation: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.status is MappingStatus.MANUAL_VERIFIED and not self.citation:
            raise ConfigError(
                "MANUAL_VERIFIED requires a citation; an unsourced manual mapping "
                "is a fabricated mapping with a better label"
            )
        if (
            self.status in {MappingStatus.RESOLVED, MappingStatus.MANUAL_VERIFIED}
            and not self.ticker
        ):
            raise ConfigError(f"status {self.status} requires a ticker")

    @property
    def counts_in_numerator(self) -> bool:
        """Only mappings good enough to match a vendor roster against."""
        return self.status in {MappingStatus.RESOLVED, MappingStatus.MANUAL_VERIFIED}


@dataclass(frozen=True, slots=True)
class MappingCandidate:
    ticker: str
    evidence: MappingEvidence
    citation: str = ""


def resolve_mapping(cik: int | None, candidates: Sequence[MappingCandidate]) -> SecurityMapping:
    """Choose a mapping, or decline to.

    - no candidates -> ``UNRESOLVED``
    - the best evidence is name-match or full-text-search -> ``AMBIGUOUS`` at
      most, whatever the count
    - several distinct tickers at the same best evidence rank -> ``AMBIGUOUS``
    - one candidate at sufficient evidence -> ``RESOLVED``
    - a manual citation -> ``MANUAL_VERIFIED``
    """
    if not candidates:
        return SecurityMapping(
            cik=cik,
            ticker=None,
            status=MappingStatus.UNRESOLVED,
            evidence=None,
            note="no mapping evidence found",
        )

    best_rank = min(_RANK.index(c.evidence) for c in candidates)
    best_evidence = _RANK[best_rank]
    best = [c for c in candidates if c.evidence is best_evidence]
    tickers = {c.ticker.strip().upper() for c in best}

    if best_evidence is MappingEvidence.MANUAL_FILING_CITATION:
        if len(tickers) > 1:
            return SecurityMapping(
                cik=cik,
                ticker=None,
                status=MappingStatus.AMBIGUOUS,
                evidence=best_evidence,
                note=f"conflicting manual citations: {sorted(tickers)}",
            )
        cited = next((c.citation for c in best if c.citation), "")
        if not cited:
            return SecurityMapping(
                cik=cik,
                ticker=None,
                status=MappingStatus.AMBIGUOUS,
                evidence=best_evidence,
                note="manual mapping offered without a citation; refused",
            )
        return SecurityMapping(
            cik=cik,
            ticker=tickers.pop(),
            status=MappingStatus.MANUAL_VERIFIED,
            evidence=best_evidence,
            citation=cited,
        )

    if best_evidence in _INSUFFICIENT_ALONE:
        return SecurityMapping(
            cik=cik,
            ticker=None,
            status=MappingStatus.AMBIGUOUS,
            evidence=best_evidence,
            note=(
                f"best available evidence is {best_evidence}, which cannot establish "
                f"a mapping on its own; candidates {sorted(tickers)}"
            ),
        )

    if len(tickers) > 1:
        return SecurityMapping(
            cik=cik,
            ticker=None,
            status=MappingStatus.AMBIGUOUS,
            evidence=best_evidence,
            note=f"several tickers at the same evidence rank: {sorted(tickers)}",
        )

    return SecurityMapping(
        cik=cik,
        ticker=tickers.pop(),
        status=MappingStatus.RESOLVED,
        evidence=best_evidence,
        citation=next((c.citation for c in best if c.citation), ""),
    )


def mapping_counts(mappings: Sequence[SecurityMapping]) -> dict[str, int]:
    counts = {str(status): 0 for status in MappingStatus}
    for mapping in mappings:
        counts[str(mapping.status)] += 1
    return counts
