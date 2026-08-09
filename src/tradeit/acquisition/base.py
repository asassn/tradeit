"""What an acquisition provider is, and where the seam between vendors sits.

The existing :mod:`tradeit.data.provider` protocols return *domain models* —
``OhlcvBar``, ``CorporateAction``. That is the right shape for the platform and
the wrong shape for acquisition, because turning a vendor row into an
``OhlcvBar`` throws away everything the model has no field for: the adjusted
prices, the split factor, the vendor's own dividend column. Those are precisely
what a package must retain so the project's adjustment methodology can be
verified rather than trusted.

So an acquisition provider works one level lower. It returns **raw response
bytes plus decoded rows as dicts**, and normalization into the package format is
a separate step. Three consequences worth stating:

* The raw file written to the cache is the vendor's answer, byte for byte. It is
  never rewritten, and its SHA-256 goes in the manifest.
* A provider adapter is small: build a URL, issue the request, hand back what
  came out. Everything hard — rate limiting, retries, backoff, auth-versus-
  network failure — already lives in :class:`~tradeit.data.providers.http.HttpTransport`
  and is not reimplemented here.
* Adding EODHD or Sharadar later means writing one adapter, not touching the
  package format. That is the whole point of the seam.

**Credentials never enter this layer's data.** A provider reads its key from the
environment, puts it in a request header rather than a query string, and the
journal and cache record the URL with any credential-shaped parameter redacted.
There is a test for that.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from tradeit.data.packages.spec import DatasetKind
from tradeit.errors import ConfigError


class AcquisitionDataset(StrEnum):
    """What an adapter can be asked to fetch.

    Deliberately not the same enum as :class:`~tradeit.data.packages.spec.DatasetKind`.
    A vendor request is not a package dataset: Tiingo's price endpoint answers
    ``DAILY_BARS``, ``SPLITS`` and ``DIVIDENDS`` in one response, and pretending
    otherwise would mean three downloads of the same bytes.
    """

    #: One request per symbol returning the daily price history, including
    #: whatever adjustment and corporate-action columns the vendor carries.
    DAILY_PRICES = "daily_prices"
    #: Per-symbol reference metadata: name, exchange, listing dates.
    SYMBOL_META = "symbol_meta"
    #: A dedicated corporate-actions endpoint, where the vendor has one.
    CORPORATE_ACTIONS = "corporate_actions"

    @property
    def is_per_symbol(self) -> bool:
        return True


class FetchStatus(StrEnum):
    OK = "ok"
    #: The vendor answered, and the answer was empty. Not an error — a symbol
    #: that had not listed yet legitimately has no rows — but recorded, because
    #: an empty universe of empty responses looks like success otherwise.
    EMPTY = "empty"
    #: Served from the raw cache without a request. The resume path.
    CACHED = "cached"
    #: The vendor rejected the request: bad key, plan does not include this
    #: endpoint, unknown symbol.
    REJECTED = "rejected"
    #: No HTTP status at all — DNS, TLS, timeout, egress policy.
    UNREACHABLE = "unreachable"
    #: Rate limited past the retry budget.
    RATE_LIMITED = "rate_limited"
    #: The response arrived and could not be decoded.
    MALFORMED = "malformed"

    @property
    def is_success(self) -> bool:
        return self in (FetchStatus.OK, FetchStatus.EMPTY, FetchStatus.CACHED)

    @property
    def is_retryable(self) -> bool:
        """Whether re-running the command is likely to help.

        ``REJECTED`` is excluded on purpose: retrying a 403 forever is how a
        typo in an API key becomes an IP ban.
        """
        return self in (
            FetchStatus.UNREACHABLE,
            FetchStatus.RATE_LIMITED,
            FetchStatus.MALFORMED,
        )


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """One unit of work: one symbol, one dataset, one date range."""

    dataset: AcquisitionDataset
    symbol: str
    start: dt.date | None = None
    end: dt.date | None = None

    @property
    def identity(self) -> str:
        """Stable name for the cache file and the journal.

        Includes the date range, because the same symbol fetched over a
        different window is a different response and reusing one for the other
        would be a silent truncation.
        """
        span = (
            f"{self.start.isoformat()}_{self.end.isoformat()}"
            if self.start and self.end
            else "full"
        )
        return f"{self.symbol.replace('/', '-')}__{span}"


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What happened, and what came back.

    ``raw`` is the vendor's bytes verbatim. ``rows`` is the decoded form, which
    is a convenience for normalization and is never what gets written to the
    raw cache.
    """

    request: FetchRequest
    status: FetchStatus
    #: Redacted. Never contains a credential.
    url: str = ""
    raw: bytes = b""
    rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    http_status: int | None = None
    attempts: int = 1
    error: str = ""
    elapsed_s: float = 0.0

    @property
    def row_count(self) -> int:
        return len(self.rows)


@runtime_checkable
class AcquisitionProvider(Protocol):
    """A vendor adapter. Small by design."""

    name: str
    #: Environment variable the credential is read from. Named here so the
    #: setup instructions and the error message cannot drift apart.
    credential_env: str
    #: Datasets this adapter can actually fetch. Anything absent is reported as
    #: unavailable in the manifest rather than silently skipped.
    supported: tuple[AcquisitionDataset, ...]
    #: Which package datasets this provider's responses can populate.
    produces: tuple[DatasetKind, ...]
    #: Requests per minute the vendor permits, used to derive the throttle.
    rate_limit_per_minute: int

    def has_credential(self) -> bool:
        """Whether a key is present. Never returns or logs the key itself."""

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        """Issue one request. Must not raise for a vendor-side failure."""

    def limitations(self) -> list[str]:
        """What this vendor cannot supply, for the manifest."""


_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Register an acquisition adapter under its ``name``."""
    name = getattr(cls, "name", "")
    if not name:
        raise ConfigError(f"{cls!r} has no name and cannot be registered")
    _REGISTRY[name] = cls
    return cls


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider_class(name: str) -> type:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ConfigError(
            f"no acquisition provider named {name!r}. Available: {available_providers()}"
        ) from None


__all__ = [
    "AcquisitionDataset",
    "AcquisitionProvider",
    "FetchOutcome",
    "FetchRequest",
    "FetchStatus",
    "available_providers",
    "get_provider_class",
    "register",
]
