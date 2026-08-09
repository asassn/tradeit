"""Acquiring real market data on a machine that can reach the internet.

The empirical gate established that this build environment cannot reach any
market-data provider, and that the honest response was to finish the offline
infrastructure rather than fabricate a result. This package is the other half:
the tool the project owner runs on their own computer to produce a package that
`tradeit data import` accepts.

It is deliberately small. Rate limiting, retries, exponential backoff, timeouts,
and the distinction between "the vendor rejected this" and "the vendor was never
reached" all already exist in
:class:`~tradeit.data.providers.http.HttpTransport`, and the package format,
manifest, validator and importer already exist. What was missing was the wiring
between them, plus the three things a long-running download on somebody's laptop
needs: a raw cache so a rerun resumes, a journal so a failure is diagnosable,
and a report that says what actually arrived rather than what was requested.

Nothing here runs inside the restricted environment. The tests use recorded
fixtures and a fake transport; the first real network call happens on the
operator's machine, and `docs/LOCAL_DATA_ACQUISITION.md` walks through it.
"""

from tradeit.acquisition.base import (
    AcquisitionDataset,
    AcquisitionProvider,
    CapabilitySupport,
    CreditUsage,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    SymbolStatus,
    available_providers,
    get_provider_class,
)
from tradeit.acquisition.cache import RawCache
from tradeit.acquisition.credits import CreditLedger
from tradeit.acquisition.eodhd import EodhdAcquisition
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.normalize import assign_instrument_ids
from tradeit.acquisition.reconstruct import (
    RECONSTRUCTION_LABEL,
    ReconstructionQuality,
    reconstruct_symbol,
)
from tradeit.acquisition.redaction import credential_hint, redact_url
from tradeit.acquisition.runner import (
    ACQUISITION_TOOL_VERSION,
    AcquisitionOptions,
    AcquisitionReport,
    AcquisitionRunner,
    PackageStatus,
    estimate_size,
)
from tradeit.acquisition.tiingo import TiingoAcquisition
from tradeit.acquisition.twelvedata import TwelveDataAcquisition

__all__ = [
    "ACQUISITION_TOOL_VERSION",
    "RECONSTRUCTION_LABEL",
    "AcquisitionDataset",
    "AcquisitionJournal",
    "AcquisitionOptions",
    "AcquisitionProvider",
    "AcquisitionReport",
    "AcquisitionRunner",
    "CapabilitySupport",
    "CreditLedger",
    "CreditUsage",
    "EodhdAcquisition",
    "FetchOutcome",
    "FetchRequest",
    "FetchStatus",
    "PackageStatus",
    "RawCache",
    "ReconstructionQuality",
    "SymbolStatus",
    "TiingoAcquisition",
    "TwelveDataAcquisition",
    "assign_instrument_ids",
    "available_providers",
    "credential_hint",
    "estimate_size",
    "get_provider_class",
    "reconstruct_symbol",
    "redact_url",
]
