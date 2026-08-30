"""What an acquisition provider is, and where the seam between vendors sits.

The existing :mod:`tradeit.data.provider` protocols return *domain models* —
``OhlcvBar``, ``CorporateAction``. That is the right shape for the platform and
the wrong shape for acquisition, because turning a vendor row into an
``OhlcvBar`` throws away everything the model has no field for: the adjusted
prices, the split factor, the vendor's own dividend column. Those are precisely
what a package must retain so the project's adjustment methodology can be
verified rather than trusted.

So an acquisition provider works one level lower. It returns **raw response
bytes plus decoded rows**, and normalization into the package format is a
separate step owned by the adapter.

**The provider drives the plan.** An earlier version of this interface had the
runner decide what to fetch — metadata, then prices, one symbol at a time —
which was Tiingo's shape wearing a general name. Twelve Data batches symbols
into one request, serves splits and dividends from dedicated endpoints, and
charges credits per symbol rather than per request. Rather than teach the runner
about both, a provider now answers two questions:

* :meth:`AcquisitionProvider.plan` — given symbols and a date range, what
  requests do you want issued, in what order?
* :meth:`AcquisitionProvider.normalize` — given one outcome, what canonical
  package rows does it become?

The runner fetches, caches, journals, accounts and reports. It no longer knows
what a vendor's endpoints look like, which is what makes "add a third provider"
a new file rather than a refactor.

**Credits and requests are different quantities.** Batching three symbols into
one HTTP call is one request and three credits on a per-symbol-priced API.
Conflating them would make a batch look free and lead straight to a quota wall
mid-download, so :class:`FetchOutcome` carries both and the journal records
both.

**Credentials never enter this layer's data.** A provider reads its key from the
environment, sends it in a header where the vendor allows one, and every URL
that reaches the journal, the cache sidecar or an error message goes through
:func:`~tradeit.acquisition.redaction.redact_url` first. There are tests.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tradeit.data.packages.spec import DatasetKind
from tradeit.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - breaks a normalize <-> base cycle
    from tradeit.acquisition.normalize import NormalizedRows


class AcquisitionDataset(StrEnum):
    """What an adapter can be asked to fetch.

    Deliberately not the same enum as :class:`~tradeit.data.packages.spec.DatasetKind`.
    A vendor request is not a package dataset: Tiingo's price endpoint answers
    ``DAILY_BARS``, ``SPLITS`` and ``DIVIDENDS`` in one response, while Twelve
    Data serves each from its own endpoint. One enum for both would force one
    vendor's layout onto the other.
    """

    #: Daily price history, including whatever adjustment and corporate-action
    #: columns the vendor carries inline.
    DAILY_PRICES = "daily_prices"
    #: Reference metadata: name, exchange, listing dates.
    SYMBOL_META = "symbol_meta"
    #: A combined corporate-actions endpoint, where a vendor has one.
    CORPORATE_ACTIONS = "corporate_actions"
    #: A dedicated splits endpoint.
    SPLITS = "splits"
    #: A dedicated dividends endpoint.
    DIVIDENDS = "dividends"
    #: The first date a vendor holds data for a symbol. Cheap, and the honest
    #: way to tell "this security did not exist yet" from "we failed to get it".
    EARLIEST_TIMESTAMP = "earliest_timestamp"


class CapabilitySupport(StrEnum):
    """Whether a provider can actually supply something, and how we know.

    The distinction that matters is between an endpoint returning nothing and an
    endpoint the subscription does not include. Both look like "no rows" at the
    HTTP layer, and treating the second as the first writes "this stock had no
    splits" into a package about a stock that split four times.
    """

    #: Exercised against the vendor and it worked.
    AVAILABLE = "available"
    #: The vendor answered, correctly, with nothing.
    EMPTY_VALID_RESPONSE = "empty_valid_response"
    #: The subscription does not include it. **Never means "no data exists".**
    NOT_AVAILABLE_ON_PLAN = "not_available_on_plan"
    #: The vendor errored in a way that is not a plan restriction.
    PROVIDER_ERROR = "provider_error"
    #: Not established either way. The honest default for anything untested.
    UNKNOWN = "unknown"

    @property
    def is_evidence_of_absence(self) -> bool:
        """Whether "no rows" may be read as "no such events happened"."""
        return self is CapabilitySupport.EMPTY_VALID_RESPONSE


class SymbolStatus(StrEnum):
    """Why a requested symbol did or did not produce data."""

    VALID = "valid"
    NOT_FOUND = "not_found"
    #: The vendor knows the symbol but holds no data over the requested window.
    UNAVAILABLE_HISTORICALLY = "unavailable_historically"
    PLAN_RESTRICTED = "plan_restricted"
    #: The vendor needs an exchange or mic_code to disambiguate.
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class FetchStatus(StrEnum):
    OK = "ok"
    #: The vendor answered, and the answer was empty. Not an error — a symbol
    #: that had not listed yet legitimately has no rows — but recorded, because
    #: a universe of empty responses looks like success otherwise.
    EMPTY = "empty"
    #: Served from the raw cache without a request. The resume path.
    CACHED = "cached"
    #: The vendor rejected the request: bad key, plan does not include this
    #: endpoint, unknown symbol.
    REJECTED = "rejected"
    #: No HTTP status at all — DNS, TLS, timeout, egress policy.
    UNREACHABLE = "unreachable"
    #: Rate limited past the retry budget. A minute-level wall; retrying later
    #: in the same run is reasonable.
    RATE_LIMITED = "rate_limited"
    #: The plan's credit allowance is spent. Distinct from RATE_LIMITED because
    #: the remedy is different: waiting minutes will not help, and the run
    #: should stop cleanly and be resumed after the allowance resets.
    QUOTA_EXHAUSTED = "quota_exhausted"
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
            FetchStatus.QUOTA_EXHAUSTED,
            FetchStatus.MALFORMED,
        )

    @property
    def stops_the_run(self) -> bool:
        """Whether continuing to issue requests is pointless."""
        return self is FetchStatus.QUOTA_EXHAUSTED


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """One unit of work: one or more symbols, one dataset, one date range.

    Multiple symbols because some vendors accept a comma-separated list in one
    call. The runner does not decide when to batch — the provider's
    :meth:`AcquisitionProvider.plan` does, since only it knows whether batching
    is permitted and what it costs.
    """

    dataset: AcquisitionDataset
    symbols: tuple[str, ...]
    start: dt.date | None = None
    end: dt.date | None = None

    def __post_init__(self) -> None:
        if isinstance(self.symbols, str):
            # A bare string is iterable, so it would silently become a request
            # for the symbols "S", "P" and "Y". Refusing is cheap; the failure
            # is a batch of single letters that 404 individually.
            raise ConfigError(
                f"symbols must be a tuple, not the string {self.symbols!r}. "
                "Use FetchRequest.one(dataset, symbol) for a single symbol."
            )
        if not self.symbols:
            raise ConfigError("a fetch request needs at least one symbol")

    @classmethod
    def one(
        cls,
        dataset: AcquisitionDataset,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> FetchRequest:
        return cls(dataset=dataset, symbols=(symbol,), start=start, end=end)

    @property
    def symbol(self) -> str:
        """The first symbol. Convenient for single-symbol requests."""
        return self.symbols[0]

    @property
    def is_batch(self) -> bool:
        return len(self.symbols) > 1

    @property
    def identity(self) -> str:
        """Stable name for the cache file and the journal.

        Includes the date range, because the same symbol fetched over a
        different window is a different response and reusing one for the other
        would be a silent truncation. A batch is named for its members, hashed
        past three so a forty-symbol batch does not become a filename no
        filesystem will accept.
        """
        span = (
            f"{self.start.isoformat()}_{self.end.isoformat()}"
            if self.start and self.end
            else "full"
        )
        cleaned = [s.replace("/", "-") for s in self.symbols]
        if len(cleaned) <= 3:
            head = "+".join(cleaned)
        else:
            import hashlib

            digest = hashlib.sha256("|".join(cleaned).encode()).hexdigest()[:10]
            head = f"batch{len(cleaned)}-{digest}"
        return f"{head}__{span}"


@dataclass(frozen=True, slots=True)
class CreditUsage:
    """What one response said about the plan's allowance.

    All fields optional: a vendor that does not report credits leaves them
    ``None``, and ``None`` must never be rendered as zero. "We do not know how
    many credits are left" and "no credits are left" are opposite facts.
    """

    used: int | None = None
    remaining: int | None = None
    #: Credits this request is understood to have cost. Derived from the
    #: provider's pricing model when the vendor does not report it, and marked
    #: as such by ``estimated``.
    charged: int | None = None
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What happened, and what came back.

    ``raw`` is the vendor's bytes verbatim. ``rows`` is the decoded form, which
    is a convenience for the adapter's own normalizer and is never what gets
    written to the raw cache.
    """

    request: FetchRequest
    status: FetchStatus
    #: Redacted. Never contains a credential.
    url: str = ""
    raw: bytes = b""
    rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    #: For a batch: which symbols the vendor actually answered for, and how each
    #: fared. A batch where one ticker is unknown must not discard the rest.
    per_symbol: Mapping[str, SymbolStatus] = field(default_factory=dict)
    credits: CreditUsage = field(default_factory=CreditUsage)
    http_status: int | None = None
    attempts: int = 1
    error: str = ""
    elapsed_s: float = 0.0
    #: Seconds this request spent waiting on a rate or credit limit. Recorded
    #: so a slow run can be explained without guessing.
    waited_s: float = 0.0

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def symbols_ok(self) -> list[str]:
        if not self.per_symbol:
            return list(self.request.symbols) if self.status.is_success else []
        return [s for s, state in self.per_symbol.items() if state is SymbolStatus.VALID]

    def symbols_failed(self) -> dict[str, SymbolStatus]:
        return {s: state for s, state in self.per_symbol.items() if state is not SymbolStatus.VALID}


@runtime_checkable
class AcquisitionProvider(Protocol):
    """A vendor adapter. Owns its endpoints, its batching and its normalization."""

    name: str
    #: Environment variable the credential is read from. Named here so the
    #: setup instructions and the error message cannot drift apart.
    credential_env: str
    #: Which package datasets this provider's responses can populate.
    produces: tuple[DatasetKind, ...]

    def has_credential(self) -> bool:
        """Whether a key is present. Never returns or logs the key itself."""

    def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]:
        """The requests to issue, in the order they should be issued."""

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        """Issue one request. Must not raise for a vendor-side failure."""

    def normalize(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        """Turn one successful outcome into canonical package rows."""

    def limitations(self) -> list[str]:
        """What this vendor cannot supply, for the manifest."""

    def missing_datasets(self) -> list[DatasetKind]:
        """Package datasets this provider cannot fill."""

    def capabilities(self) -> Mapping[str, CapabilitySupport]:
        """What is known to work, what is plan-restricted, what is untested."""

    def adjustment_policy(self) -> Any:
        """What the price columns this provider writes actually contain."""


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
    "CapabilitySupport",
    "CreditUsage",
    "FetchOutcome",
    "FetchRequest",
    "FetchStatus",
    "SymbolStatus",
    "available_providers",
    "get_provider_class",
    "register",
]
