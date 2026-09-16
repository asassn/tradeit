"""The ``research-01`` corpus: its point-in-time policy and its importer.

Deliberately a separate package from :mod:`tradeit.ingest`, which serves
``full-01``. The two corpora key on different identities -- ``instrument_id``
against ``security_id`` -- and the whole point of milestone 2 was to stop those
being one thing. A shared importer would have re-joined them at the first
convenient moment.
"""

from tradeit.research01.actions import VendorAction, import_corporate_actions
from tradeit.research01.adjudicate import (
    Adjudication,
    BoundaryEvidence,
    RegimeBreak,
    Verdict,
    adjudicate_series,
    detect_regime_break,
)
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
from tradeit.research01.series import (
    AdjustedBar,
    Coherence,
    SplitAdjustment,
    SplitEvidence,
    adjudicated_bound,
    adjudicated_window,
    known_splits,
    price_series,
    recorded_verdict,
    series_coherence,
    split_evidence,
    split_reading,
)
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
    "Adjudication",
    "AdjustedBar",
    "BackfillPlan",
    "BackfillProgress",
    "BackfillReport",
    "BoundaryEvidence",
    "Candidate",
    "CandidateStatus",
    "Coherence",
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
    "RegimeBreak",
    "RejectReason",
    "RejectedBar",
    "Resolution",
    "SeedReport",
    "SplitAdjustment",
    "SplitEvidence",
    "VendorAction",
    "VendorAliasReport",
    "VendorBar",
    "Verdict",
    "action_knowledge_time_for",
    "adjudicate_series",
    "adjudicated_bound",
    "adjudicated_window",
    "candidate_symbols",
    "confirm_ticker",
    "derive_vendor_aliases",
    "detect_regime_break",
    "import_corporate_actions",
    "import_filings",
    "import_filings_from_index",
    "import_fsds_quarter",
    "import_price_bars",
    "knowledge_time_for",
    "known_splits",
    "normalise_company_name",
    "parse_bars",
    "parse_dividends",
    "parse_splits",
    "price_series",
    "propose_candidates",
    "read_quarter",
    "reconcile_fundamentals_against_filings",
    "recorded_verdict",
    "refute_by_span",
    "resolve_issuer",
    "resolve_security",
    "run_backfill",
    "seed_identity",
    "series_coherence",
    "split_evidence",
    "split_reading",
]
