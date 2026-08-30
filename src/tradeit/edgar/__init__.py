"""SEC EDGAR lifecycle evidence and the survivorship denominator.

Free, public domain, and independent of any price vendor. This package builds
the instrument that *measures* whether a vendor's delisted roster is complete,
rather than accepting the roster's own account of itself.

Six modules, in the order they run:

``index``
    Read the quarterly full-index. The spine starts at **1994 Q3**.
``submission``
    Read one downloaded submission: its SGML header and its documents, so a
    filing is checked against the index row that named it before it is read --
    and so *who filed it* stays distinct from *who it is about*.
``evidence``
    Decide what each filing proves, across four separate lifecycles.
``lifecycle``
    Resolve exits -- and refuse to turn filing cessation into a death date.
``identity``
    Map CIK to ticker in four states, with ``UNRESOLVED`` first-class and no
    fabricated mappings.
``denominator``
    Aggregate, publish two coverage bounds, and decline to award the top
    survivorship grade until a threshold has been chosen from real data.
"""

from tradeit.edgar.denominator import (
    Classification,
    CoverageBounds,
    Denominator,
    SurvivorshipClass,
    classify_corpus,
)
from tradeit.edgar.evidence import (
    EvidenceStrength,
    EvidenceType,
    LifecycleEvidence,
    LifecycleScope,
    classify_form,
)
from tradeit.edgar.identity import (
    MappingCandidate,
    MappingEvidence,
    MappingStatus,
    SecurityMapping,
    resolve_mapping,
)
from tradeit.edgar.index import (
    EDGAR_FIRST_QUARTER,
    FullIndexRow,
    IndexQuarter,
    LocalFullIndexSource,
    parse_full_index,
)
from tradeit.edgar.lifecycle import ExitResolution, build_timelines, resolve_exit
from tradeit.edgar.submission import (
    KNOWN_ENTITY_ROLES,
    SubmissionDocument,
    SubmissionEntity,
    SubmissionHeader,
    cik_roles,
    entities_for_role,
    header_mismatches,
    parse_submission_header,
    split_documents,
    unknown_roles,
)

__all__ = [
    "EDGAR_FIRST_QUARTER",
    "KNOWN_ENTITY_ROLES",
    "Classification",
    "CoverageBounds",
    "Denominator",
    "EvidenceStrength",
    "EvidenceType",
    "ExitResolution",
    "FullIndexRow",
    "IndexQuarter",
    "LifecycleEvidence",
    "LifecycleScope",
    "LocalFullIndexSource",
    "MappingCandidate",
    "MappingEvidence",
    "MappingStatus",
    "SecurityMapping",
    "SubmissionDocument",
    "SubmissionEntity",
    "SubmissionHeader",
    "SurvivorshipClass",
    "build_timelines",
    "cik_roles",
    "classify_corpus",
    "classify_form",
    "entities_for_role",
    "header_mismatches",
    "parse_full_index",
    "parse_submission_header",
    "resolve_exit",
    "resolve_mapping",
    "split_documents",
    "unknown_roles",
]
