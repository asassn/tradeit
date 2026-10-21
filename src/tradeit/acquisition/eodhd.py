"""EODHD adapter — implemented 2026-10-21, against shapes already proved here.

The stub this replaces named its own blocker precisely: *"completing it would
mean guessing this vendor's URL shapes, parameter names, response fields and
error semantics from memory […] The environment this was written in cannot
reach `eodhd.com` to check."*

**Neither half of that is true any more.** The URL shapes and response fields
are not guessed and were not re-derived: `tradeit.research01.eodhd_client` has
used them against the live API to load **854,718 bars** for the survivorship
backfill, and this adapter reuses the same paths and the same field names. Where
the stub listed five steps, four were already done elsewhere in the repository.

What is declared, and what is deliberately not
----------------------------------------------

**`adjustment_policy()` returns `UNKNOWN`, not `RAW_UNADJUSTED`**, and that is
the most important line in this file. EODHD serves `close` beside
`adjusted_close`, so the obvious reading is that `close` is the exchange print
— but `RESEARCH_01_DATA_DICTIONARY.md` §0.1a records, from measurement, that
**some EODHD `raw` closes are already split-adjusted**, so applying the recorded
split adjusts twice. 1,872 splits in this corpus have prints that contradict
each other or the ratio.

A vendor that is *usually* raw is not raw. `UNKNOWN` is the honest declaration
and it costs something real — it blocks corporate-action validation, by design.
Claiming `RAW_UNADJUSTED` would buy that validation back with a number nobody
checked.

**Splits and dividends come from dedicated endpoints**, not inline on price
rows the way Tiingo serves them. That is what the stub was written to prove the
interface could accommodate, and it can: a request names a *dataset*, not an
endpoint.

Two measured limitations travel with every result
--------------------------------------------------

Both from `PHASE_06_VENDOR_MATRIX.md`, measured rather than asserted:

* **One series per ticker — the most recent holder.** 59,925 delisted entries
  under 59,925 distinct codes, no duplicates, so an *earlier* holder of a reused
  ticker is not addressable at all. Asking for a dead registrant's symbol
  returns whichever company holds those letters now. Forward this is far less
  dangerous, because a ticker handover is observed as it happens rather than
  reconstructed — but it is why bars from this vendor should stay
  distinguishable from another's rather than pooled.
* **The corporate-action feed is materially incomplete.** 8,818 of 17,428
  priced securities carried no action at all, and `splits` for `THQI.US` across
  2011-2013 returns nothing for a company that split four times. Sharadar
  returned all four.

Neither is a reason to refuse the vendor. Both are reasons to record what it is.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from decimal import DivisionByZero as DecimalDivisionByZero
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    register,
)
from tradeit.acquisition.normalize import NormalizedRows, normalize_prices
from tradeit.acquisition.redaction import credential_hint, redact_text, redact_url
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.data.providers.http import (
    HttpTransport,
    ProviderRateLimitError,
    ProviderUnreachableError,
)

#: Endpoint paths, to be filled in against the vendor's documentation. Empty
#: rather than guessed: a plausible-looking wrong path produces a 404 that
#: reads like a missing symbol.
#: Base, matching `research01.eodhd_client.BASE`. One constant per repository
#: would be better; two that agree is the honest second-best, and a test pins
#: them together so they cannot drift apart silently.
BASE_URL = "https://eodhd.com/api"

#: The real paths, taken from the client that has used them against the live
#: API rather than from documentation read once.
ENDPOINTS: dict[AcquisitionDataset, str] = {
    AcquisitionDataset.DAILY_PRICES: "eod",
    AcquisitionDataset.CORPORATE_ACTIONS: "splits",
}

#: Kept so callers that matched on it still compile; the adapter no longer
#: returns it for a supported dataset.
NOT_IMPLEMENTED_MESSAGE = (
    "this dataset is not implemented for EODHD. Daily prices and splits are; "
    "see the module docstring for what this vendor is and is not."
)


@register
class EodhdAcquisition:
    """Daily prices and splits from EODHD. See the module docstring for the
    two measured limitations that travel with everything it returns."""

    name = "eodhd"
    credential_env = "EODHD_API_KEY"

    supported: tuple[AcquisitionDataset, ...] = (
        AcquisitionDataset.DAILY_PRICES,
        AcquisitionDataset.CORPORATE_ACTIONS,
    )
    produces: tuple[DatasetKind, ...] = (
        DatasetKind.DAILY_BARS,
        DatasetKind.SPLITS,
    )
    rate_limit_per_minute = 60

    implemented = True

    def __init__(
        self,
        token: str | None = None,
        *,
        transport: Any = None,
        rate_limit_per_minute: int | None = None,
        timeout_s: float = 30.0,
        max_attempts: int = 4,
    ) -> None:
        self._token = token or os.environ.get(self.credential_env, "").strip()
        limit = rate_limit_per_minute or self.rate_limit_per_minute
        self.rate_limit_per_minute = limit
        self.transport = transport or HttpTransport(
            cache=None,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            min_interval_s=60.0 / max(limit, 1),
        )

    # -- credentials ---------------------------------------------------------

    def has_credential(self) -> bool:
        return bool(self._token)

    def credential_hint(self) -> str:
        return credential_hint(self._token)

    # -- declared capabilities ------------------------------------------------

    def adjustment_policy(self) -> AdjustmentPolicyDeclaration:
        """UNKNOWN, and deliberately so. See the module docstring.

        §0.1a measured that some EODHD `raw` closes are already split-adjusted.
        A vendor that is usually raw is not raw, and this field exists because a
        wrong answer to it is invisible afterwards.
        """
        return AdjustmentPolicyDeclaration.UNKNOWN

    def capabilities(self) -> Mapping[str, CapabilitySupport]:
        return {
            "daily_ohlcv": CapabilitySupport.AVAILABLE,
            # AVAILABLE, because the endpoint works and returns real splits
            # -- and the incompleteness lives in `limitations()` instead,
            # because `CapabilitySupport` has no PARTIAL and inventing one
            # here would change an enum the whole matrix is graded against.
            # This matches how PHASE_06_VENDOR_MATRIX already records it:
            # present for some securities, and "presence is not completeness".
            "splits": CapabilitySupport.AVAILABLE,
            "dividends": CapabilitySupport.UNKNOWN,
        }

    def limitations(self) -> list[str]:
        return [
            "serves one series per ticker -- the most recent holder -- so an "
            "earlier holder of a reused ticker is not addressable",
            "the corporate-action feed is materially incomplete: 8,818 of 17,428 "
            "priced securities carried no action, measured 2026-09-16",
            "adjustment basis is UNKNOWN: some `raw` closes are already "
            "split-adjusted (RESEARCH_01_DATA_DICTIONARY.md §0.1a)",
        ]

    def missing_datasets(self) -> list[DatasetKind]:
        return [kind for kind in DatasetKind if kind not in self.produces]

    # -- planning ------------------------------------------------------------

    def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]:
        """One price request per symbol, and one split request per symbol.

        Separate requests because EODHD serves corporate actions from dedicated
        endpoints rather than inline -- which is exactly the shape the stub
        existed to prove the interface accommodates.
        """
        requests: list[FetchRequest] = []
        for raw in symbols:
            symbol = raw.strip().upper()
            if not symbol:
                continue
            for dataset in self.supported:
                requests.append(FetchRequest.one(dataset, symbol, start, end))
        return requests

    def credits_for(self, request: FetchRequest) -> int:
        return len(request.symbols)

    # -- fetching ------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        if request.dataset not in self.supported:
            return FetchOutcome(
                request=request, status=FetchStatus.REJECTED, error=NOT_IMPLEMENTED_MESSAGE
            )
        if not self.has_credential():
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error=f"no credential: set {self.credential_env}",
            )
        url = self._url(request)
        started = time.perf_counter()
        try:
            body = self.transport.get(url)
        except ProviderRateLimitError as error:
            return self._failure(request, url, FetchStatus.RATE_LIMITED, error, started)
        except ProviderUnreachableError as error:
            return self._failure(request, url, FetchStatus.UNREACHABLE, error, started)
        except Exception as error:
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
                error=redact_text(f"response was not JSON: {error}", self._token),
                elapsed_s=elapsed,
            )

        rows = (
            tuple(row for row in decoded if isinstance(row, dict))
            if isinstance(decoded, list)
            else ()
        )
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
            # redact_text as well as redact_url, for the reason the Tiingo
            # adapter gives: the URL regex catches `param=value`, and a vendor
            # echoing the key in prose inside an error body writes it in neither
            # shape. This string reaches the journal, which gets shared.
            error=redact_text(str(error), self._token),
            elapsed_s=time.perf_counter() - started,
        )

    def _url(self, request: FetchRequest) -> str:
        """The paths `research01.eodhd_client` has used against the live API.

        The token goes in the query string because that is what this vendor
        accepts; `redact_url` strips it from everything that is stored or
        logged, which is checked by a test rather than assumed.
        """
        path = ENDPOINTS[request.dataset]
        symbol = urllib.parse.quote(request.symbol.strip().upper())
        params = {"api_token": self._token, "fmt": "json"}
        if request.start:
            params["from"] = request.start.isoformat()
        if request.end:
            params["to"] = request.end.isoformat()
        return f"{BASE_URL}/{path}/{symbol}?{urllib.parse.urlencode(params)}"

    # -- normalising ----------------------------------------------------------

    def normalize(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        """Prices through the shared normaliser; splits through this vendor's shape.

        EODHD's price rows carry `date`, `open`, `high`, `low`, `close` and
        `volume` under exactly those names, so `normalize_prices` reads them
        unchanged -- it finds no `splitFactor` and emits no splits, which is
        correct here because they arrive from their own endpoint.
        """
        symbol = outcome.request.symbol
        instrument_id = ids.get(symbol)
        if instrument_id is None:
            return NormalizedRows()
        if outcome.request.dataset is AcquisitionDataset.DAILY_PRICES:
            return normalize_prices(symbol, instrument_id, outcome.rows)
        if outcome.request.dataset is AcquisitionDataset.CORPORATE_ACTIONS:
            return self._normalize_splits(symbol, instrument_id, outcome.rows)
        return NormalizedRows()

    @staticmethod
    def _normalize_splits(
        symbol: str, instrument_id: int, rows: Sequence[Mapping[str, Any]]
    ) -> NormalizedRows:
        """``{"date": "2020-08-31", "split": "4.000000/1.000000"}`` -> ratio 4.

        A malformed or zero-denominator ratio is **reported and dropped, never
        defaulted to 1**, matching `research01.eodhd_client.parse_splits`: a
        split silently read as 1 is a split that did not happen, and it would
        leave the price series uncorrected with nothing to show for it.
        """
        out = NormalizedRows()
        for row in rows:
            session = row.get("date")
            raw = str(row.get("split", "")).strip()
            if not session or "/" not in raw:
                out.findings.append(f"{symbol}: unreadable split {raw!r} on {session}")
                continue
            numerator, _, denominator = raw.partition("/")
            try:
                ratio = Decimal(numerator) / Decimal(denominator)
            except (InvalidOperation, ZeroDivisionError, DecimalDivisionByZero):
                out.findings.append(f"{symbol}: split {raw!r} on {session} is not a ratio")
                continue
            if ratio <= 0:
                out.findings.append(f"{symbol}: split ratio {ratio} on {session} is not positive")
                continue
            out.rows.setdefault(DatasetKind.SPLITS, []).append(
                {
                    "instrument_id": str(instrument_id),
                    "ex_date": str(session),
                    "ratio": format(ratio.normalize(), "f"),
                }
            )
        return out


__all__ = ["BASE_URL", "ENDPOINTS", "NOT_IMPLEMENTED_MESSAGE", "EodhdAcquisition"]
