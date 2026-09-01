"""The ``research-01`` corpus: its point-in-time policy and its importer.

Deliberately a separate package from :mod:`tradeit.ingest`, which serves
``full-01``. The two corpora key on different identities -- ``instrument_id``
against ``security_id`` -- and the whole point of milestone 2 was to stop those
being one thing. A shared importer would have re-joined them at the first
convenient moment.
"""

from tradeit.research01.actions import VendorAction, import_corporate_actions
from tradeit.research01.backfill import (
    BackfillPlan,
    BackfillProgress,
    BackfillReport,
    run_backfill,
)
from tradeit.research01.confirm import Confirmation, candidate_symbols, confirm_ticker
from tradeit.research01.eodhd_client import (
    EodhdClient,
    HttpEodhdClient,
    parse_bars,
    parse_dividends,
    parse_splits,
)
from tradeit.research01.filings import (
    FilingRow,
    import_filings,
    import_filings_from_index,
    resolve_issuer,
)
from tradeit.research01.fsds import FsdsFact, FsdsSubmission, import_fsds_quarter, read_quarter
from tradeit.research01.importer import (
    Delivery,
    ImportResult,
    RejectedBar,
    RejectReason,
    Resolution,
    VendorBar,
    import_price_bars,
    resolve_security,
)
from tradeit.research01.pit import (
    KnowledgeTimeBasis,
    action_knowledge_time_for,
    knowledge_time_for,
)
from tradeit.research01.reconcile import (
    ReconciliationReport,
    reconcile_fundamentals_against_filings,
)
from tradeit.research01.seed import NotEstablished, SeedReport, seed_identity
from tradeit.research01.ticker_cik import (
    Candidate,
    CandidateStatus,
    normalise_company_name,
    propose_candidates,
    refute_by_span,
)
from tradeit.research01.vendor_aliases import (
    DerivedAlias,
    VendorAliasReport,
    derive_vendor_aliases,
)

__all__ = [
    "BackfillPlan",
    "BackfillProgress",
    "BackfillReport",
    "Candidate",
    "CandidateStatus",
    "Confirmation",
    "Delivery",
    "DerivedAlias",
    "EodhdClient",
    "FilingRow",
    "FsdsFact",
    "FsdsSubmission",
    "HttpEodhdClient",
    "ImportResult",
    "KnowledgeTimeBasis",
    "NotEstablished",
    "ReconciliationReport",
    "RejectReason",
    "RejectedBar",
    "Resolution",
    "SeedReport",
    "VendorAction",
    "VendorAliasReport",
    "VendorBar",
    "action_knowledge_time_for",
    "candidate_symbols",
    "confirm_ticker",
    "derive_vendor_aliases",
    "import_corporate_actions",
    "import_filings",
    "import_filings_from_index",
    "import_fsds_quarter",
    "import_price_bars",
    "knowledge_time_for",
    "normalise_company_name",
    "parse_bars",
    "parse_dividends",
    "parse_splits",
    "propose_candidates",
    "read_quarter",
    "reconcile_fundamentals_against_filings",
    "refute_by_span",
    "resolve_issuer",
    "resolve_security",
    "run_backfill",
    "seed_identity",
]
