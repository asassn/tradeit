"""Tiingo acquisition adapter.

Thin on purpose. Rate limiting, retries, exponential backoff, timeouts and the
distinction between "the vendor rejected this" and "the vendor was never
reached" all already live in :class:`~tradeit.data.providers.http.HttpTransport`,
which Phase 1 built and which this reuses unchanged. What is left is: build a
URL, put the credential in a header, hand back the bytes.

**Two things this adapter does differently from
:class:`~tradeit.data.providers.tiingo.TiingoProvider`**, and both are the
reason it exists rather than calling that one:

*It returns the vendor's rows, not our domain models.* `TiingoProvider.fetch_bars`
produces `OhlcvBar`, which has no field for `adjClose`, `splitFactor` or
`divCash`. Those are exactly the columns a package must retain so the project's
adjustment methodology can be *verified* rather than trusted, so acquisition
works one level lower and normalization happens later.

*It never raises for a vendor-side failure.* A universe download of ninety
symbols must not die because one ticker 404s. Every failure becomes a
:class:`~tradeit.acquisition.base.FetchOutcome` with a status, which the journal
records and the summary counts.

**Corporate actions.** Tiingo delivers splits and dividends *inline* on the
daily price rows, as the `splitFactor` and `divCash` columns. That is an
authoritative vendor record — the vendor stating what the action was — and not
an inference from differences between adjusted and unadjusted prices, which is
the thing worth refusing to do. The adapter therefore reads them from the price
response rather than issuing a second request for data it already has. If a
plan exposes a dedicated corporate-actions endpoint, adding it is a new
:class:`~tradeit.acquisition.base.AcquisitionDataset` member and one method,
with no change to the package format.

**The credential.** Read from ``TIINGO_API_KEY``, falling back to
``TRADEIT_TIINGO_TOKEN`` for continuity with the Phase 1 provider. It travels in
an ``Authorization`` header, never in the query string, so it cannot reach a
cache sidecar, a journal line, a log or an exception message. Every URL this
module records is passed through :func:`redact_url` first.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    register,
)
from tradeit.data.packages.spec import DatasetKind
from tradeit.data.providers.http import (
    HttpTransport,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from tradeit.errors import ProviderError

BASE_URL = "https://api.tiingo.com/tiingo/daily"

#: Query parameters whose value must never be printed, whatever the vendor
#: calls them. Matched case-insensitively.
CREDENTIAL_PARAMS: tuple[str, ...] = ("token", "api_token", "apikey", "api_key", "key")

_CREDENTIAL_RE = re.compile(
    rf"(?i)\b({'|'.join(CREDENTIAL_PARAMS)})=[^&\s]*",
)


def redact_url(url: str) -> str:
    """Replace any credential-shaped query parameter's value with ``REDACTED``.

    Applied to every URL that reaches the journal, the cache sidecar or an
    error message. This adapter puts its key in a header and so should never
    produce a URL that needs redacting; the function runs anyway, because the
    cost is nothing and the failure it prevents is one careless edit away.
    """
    return _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=REDACTED", url)


@register
class TiingoAcquisition:
    """Daily prices and inline corporate actions from Tiingo."""

    name = "tiingo"
    credential_env = "TIINGO_API_KEY"
    #: Accepted as well, so an environment configured for the Phase 1 provider
    #: keeps working.
    credential_env_fallback = "TRADEIT_TIINGO_TOKEN"

    supported: tuple[AcquisitionDataset, ...] = (
        AcquisitionDataset.SYMBOL_META,
        AcquisitionDataset.DAILY_PRICES,
    )
    produces: tuple[DatasetKind, ...] = (
        DatasetKind.INSTRUMENTS,
        DatasetKind.SYMBOL_MAPPINGS,
        DatasetKind.DAILY_BARS,
        DatasetKind.SPLITS,
        DatasetKind.DIVIDENDS,
    )
    #: Tiingo's documented free-tier ceiling. Used to derive the throttle; a
    #: paid plan may permit more, and the value is a constructor argument.
    rate_limit_per_minute = 50

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: HttpTransport | None = None,
        rate_limit_per_minute: int | None = None,
        timeout_s: float = 30.0,
        max_attempts: int = 4,
    ) -> None:
        self._token = token or _read_credential()
        limit = rate_limit_per_minute or self.rate_limit_per_minute
        self.rate_limit_per_minute = limit
        self.transport = transport or HttpTransport(
            # No ResponseCache: the acquisition RawCache is the cache, and two
            # layers of caching over the same bytes means two places to look
            # when a stale response is suspected.
            cache=None,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            min_interval_s=60.0 / max(limit, 1),
        )

    # -- credentials ---------------------------------------------------------

    def has_credential(self) -> bool:
        return bool(self._token)

    def credential_hint(self) -> str:
        """A non-secret acknowledgement that a key is present.

        Length and last two characters only. Enough for a person to tell "the
        variable is set to something" from "the variable is empty", and useless
        to anyone who reads it over their shoulder.
        """
        if not self._token:
            return "not set"
        return f"set ({len(self._token)} chars, ends …{self._token[-2:]})"

    # -- fetching ------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        if request.dataset not in self.supported:
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error=(
                    f"the Tiingo adapter does not fetch {request.dataset}; "
                    f"it supports {[str(d) for d in self.supported]}"
                ),
            )
        if not self._token:
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error=(
                    f"no API key: set {self.credential_env} in your environment. "
                    "Refusing to issue an unauthenticated request that would come "
                    "back 403 and look like a network problem."
                ),
            )

        url = self._url(request)
        started = time.perf_counter()
        try:
            body = self.transport.get(url, headers={"Authorization": f"Token {self._token}"})
        except ProviderAuthError as error:
            return self._failure(request, url, FetchStatus.REJECTED, error, started)
        except ProviderRateLimitError as error:
            return self._failure(request, url, FetchStatus.RATE_LIMITED, error, started)
        except ProviderUnreachableError as error:
            return self._failure(request, url, FetchStatus.UNREACHABLE, error, started)
        except ProviderError as error:
            # A 4xx that is not auth: usually an unknown symbol. Rejected
            # rather than unreachable, because retrying will not help.
            return self._failure(request, url, FetchStatus.REJECTED, error, started)

        elapsed = time.perf_counter() - started
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as error:
            return FetchOutcome(
                request=request,
                status=FetchStatus.MALFORMED,
                url=redact_url(url),
                raw=body,
                error=f"response was not JSON: {error}",
                elapsed_s=elapsed,
            )

        rows = self._rows(decoded)
        return FetchOutcome(
            request=request,
            status=FetchStatus.OK if rows else FetchStatus.EMPTY,
            url=redact_url(url),
            raw=body,
            rows=rows,
            http_status=200,
            elapsed_s=elapsed,
        )

    def _failure(
        self,
        request: FetchRequest,
        url: str,
        status: FetchStatus,
        error: Exception,
        started: float,
    ) -> FetchOutcome:
        return FetchOutcome(
            request=request,
            status=status,
            url=redact_url(url),
            error=redact_url(str(error)),
            elapsed_s=time.perf_counter() - started,
        )

    def _url(self, request: FetchRequest) -> str:
        symbol = request.symbol.strip().upper()
        if request.dataset is AcquisitionDataset.SYMBOL_META:
            return f"{BASE_URL}/{symbol}"
        params = ["format=json"]
        if request.start:
            params.append(f"startDate={request.start.isoformat()}")
        if request.end:
            params.append(f"endDate={request.end.isoformat()}")
        return f"{BASE_URL}/{symbol}/prices?{'&'.join(params)}"

    @staticmethod
    def _rows(decoded: Any) -> tuple[dict[str, Any], ...]:
        """Normalize both response shapes to a row sequence.

        The prices endpoint answers with a list; the metadata endpoint answers
        with a single object. Wrapping the latter keeps every downstream step
        working in rows rather than branching on which endpoint it came from.
        """
        if isinstance(decoded, list):
            return tuple(row for row in decoded if isinstance(row, dict))
        if isinstance(decoded, dict):
            return (decoded,)
        return ()

    # -- honesty -------------------------------------------------------------

    def limitations(self) -> list[str]:
        """What this vendor cannot supply. Copied verbatim into the manifest."""
        return [
            "no per-bar publication timestamp: Tiingo does not stamp when a bar "
            "became available, so knowledge_time is derived from the session close "
            "by a rule and marked ESTIMATED rather than REPORTED",
            "corporate actions come from the price file's splitFactor and divCash "
            "columns. That covers splits, reverse splits and cash dividends. It "
            "does not cover spin-offs, rights issues or symbol changes, which are "
            "absent from this package rather than empty in it",
            "no historical index constituents: the package can include delisted "
            "securities but cannot reconstruct which of them were in an index on a "
            "given date, so survivorship bias is reduced rather than solved",
            "no delisting reason or delisting return; the metadata endpoint's end "
            "date is the only signal that a listing stopped",
            "daily bars only. Intraday requires a different Tiingo endpoint and a "
            "different entitlement, and is deliberately not part of the first "
            "empirical package",
        ]

    def missing_datasets(self) -> list[DatasetKind]:
        """Package datasets this provider cannot fill. Declared in the manifest."""
        return [kind for kind in DatasetKind if kind not in self.produces]


def _read_credential() -> str:
    for variable in (
        TiingoAcquisition.credential_env,
        TiingoAcquisition.credential_env_fallback,
    ):
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    return ""


__all__ = ["BASE_URL", "CREDENTIAL_PARAMS", "TiingoAcquisition", "redact_url"]
