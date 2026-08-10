"""Financial Modeling Prep, consulted for **historical stock splits only**.

This adapter exists to answer one question that the primary price provider could
not: *what splits does this symbol have on record?* Twelve Data supplies daily
bars that its documentation says are split-adjusted, so recovering the raw
exchange prints requires a split schedule — and on the plan this project holds,
Twelve Data's own ``/splits`` endpoint answers with a plan restriction. FMP
serves a split history on a free account, so the missing input is obtainable
from a second vendor.

**Scope is deliberately one endpoint.** ``/stable/splits`` is the only path this
module knows, and it is the one that was verified working against a real free
account before this file was written. Nothing else is called: not fundamentals,
not ratios, not earnings, not estimates, not statements, not prices, not
ownership. That is a scope decision rather than an oversight, and the endpoint
table is explicit so a plausible-looking path cannot be string-built into
existence.

**FMP must not become the price source.** It is not an
:class:`~tradeit.acquisition.base.AcquisitionProvider` and cannot be selected
with ``--provider``. It implements
:class:`~tradeit.acquisition.enrich.CorporateActionSource`, which has no way to
produce a bar. Making that structural rather than a convention is the point: a
package whose prices quietly came from a different vendor than its manifest says
is not detectable by inspection.

**An effective date is not an announcement date.** FMP's ``date`` field is the
ex-/effective date. When a split became *publicly knowable* is a different fact,
this endpoint does not carry it, and this adapter never invents one:
``announced_at`` stays ``None`` and the corporate-action knowledge-timestamp
capability is reported ``NOT_AVAILABLE_ON_PLAN``. Anyone wanting
announcement-time causality still cannot have it here.

**Empty is not proof.** ``[]`` from this endpoint is the same response an
unrecognised ticker produces. It is recorded as
:attr:`~tradeit.acquisition.base.CapabilitySupport.EMPTY_VALID_RESPONSE` — the
one state that may be read as evidence of absence — and a finding says plainly
what that response cannot distinguish.

**The rate limit here is self-imposed, not quoted.** FMP publishes a daily
request cap per plan; a per-minute figure for the free tier is not something this
file can cite, so :data:`DEFAULT_REQUESTS_PER_MINUTE` is a conservative choice
made by this project, is described as such wherever it is reported, and is
overridable. The vendor's own signals win over it: a 429 backs the pacer off, and
a daily-cap message stops the pass rather than retrying into a wall.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    FetchStatus,
)
from tradeit.acquisition.enrich import CorporateActionLookup, register_source
from tradeit.acquisition.reconstruct import SplitEvent
from tradeit.acquisition.redaction import credential_hint, redact_text, redact_url
from tradeit.data.providers.http import (
    HttpTransport,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from tradeit.errors import ProviderError

BASE_URL = "https://financialmodelingprep.com"

#: The only endpoint this adapter knows. Explicit rather than string-built, so a
#: plausible-looking path cannot be reached by accident, and so the scope of this
#: module is readable in one line.
ENDPOINTS: Mapping[AcquisitionDataset, str] = {
    AcquisitionDataset.SPLITS: "/stable/splits",
}

#: Requests per minute this adapter will issue by default.
#:
#: **A self-imposed floor, not a figure quoted from the vendor.** FMP documents a
#: daily request cap per plan; a per-minute rate for the free tier is not
#: something this file can cite, and inventing one would be exactly the kind of
#: confident guess the rest of this codebase refuses. Thirty is slow enough that
#: a hundred-symbol universe finishes in minutes and no reasonable per-minute
#: limit is threatened. Override it when you know your plan's real terms.
DEFAULT_REQUESTS_PER_MINUTE = 30

#: How far the pacer backs off after the vendor says 429, as a multiplier on the
#: current interval. The vendor's signal is authoritative over our guess, so the
#: response is to slow down rather than to keep the configured pace.
BACKOFF_FACTOR = 2.0

#: Ceiling on the self-imposed interval, so a burst of 429s cannot back the pass
#: off into an effective hang.
MAX_INTERVAL_S = 30.0


@dataclass(slots=True)
class RequestPacer:
    """A minimum gap between requests, widened when the vendor complains.

    Separate from :class:`~tradeit.acquisition.credits.CreditLedger` because the
    quantities are different. Twelve Data prices per *symbol* and reports credits
    in headers, so pacing there is arithmetic on a number the vendor supplies.
    Here there is no such number: only a request count, a daily cap the vendor
    documents per plan, and a per-minute rate nobody here can quote. So this
    paces on wall-clock and adapts to 429s, and says so rather than presenting a
    guess as a reading.
    """

    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    interval_s: float = field(init=False, default=0.0)
    requests: int = 0
    waited_s: float = 0.0
    backoffs: int = 0
    _last: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.requests_per_minute = max(1, int(self.requests_per_minute))
        self.interval_s = 60.0 / self.requests_per_minute

    def wait_for_slot(self, *, max_wait_s: float | None = None) -> float:
        """Sleep until the next request may be issued. Returns seconds slept."""
        if self._last == 0.0:
            self._last = time.monotonic()
            return 0.0
        delay = self.interval_s - (time.monotonic() - self._last)
        if delay <= 0:
            self._last = time.monotonic()
            return 0.0
        if max_wait_s is not None:
            delay = min(delay, max_wait_s)
        if delay > 0:
            time.sleep(delay)
            self.waited_s += delay
        self._last = time.monotonic()
        return delay

    def note_rate_limited(self) -> None:
        """The vendor pushed back. Widen the gap; never narrow it again here."""
        self.interval_s = min(self.interval_s * BACKOFF_FACTOR, MAX_INTERVAL_S)
        self.backoffs += 1

    def describe(self) -> str:
        return (
            f"self-imposed pacing at {self.requests_per_minute} requests/minute "
            f"({self.interval_s:.1f}s apart"
            + (f", widened by {self.backoffs} vendor 429(s)" if self.backoffs else "")
            + "). This is a conservative choice by this tool, not a limit quoted "
            "by the vendor."
        )


@register_source
class FmpSplitSource:
    """Historical stock splits from FMP's ``/stable/splits`` endpoint."""

    name = "fmp"
    credential_env = "FMP_API_KEY"
    dataset = AcquisitionDataset.SPLITS

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: HttpTransport | None = None,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        timeout_s: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self._token = token or os.environ.get(self.credential_env, "").strip()
        self.pacer = RequestPacer(requests_per_minute=requests_per_minute)
        self.transport = transport or HttpTransport(
            cache=None,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            # Pacing is the pacer's job. A small floor remains so a burst of
            # cache misses does not arrive as one volley.
            min_interval_s=0.2,
        )
        #: Discovered as the pass proceeds. UNKNOWN because nothing has been
        #: exercised yet, and UNKNOWN is the honest state for an untested
        #: capability.
        self.support: CapabilitySupport = CapabilitySupport.UNKNOWN
        #: Symbols whose answer was an empty list. Kept because "eleven symbols
        #: returned nothing" is a pattern worth seeing, and one empty response is
        #: not.
        self.empty_symbols: list[str] = []

    # -- credentials ---------------------------------------------------------

    def has_credential(self) -> bool:
        return bool(self._token)

    def credential_hint(self) -> str:
        return credential_hint(self._token)

    # -- declared capabilities -----------------------------------------------

    def capabilities(self) -> Mapping[str, CapabilitySupport]:
        return {
            "splits": self.support,
            # This adapter calls exactly one endpoint. Everything else is
            # UNKNOWN because it was never exercised, and claiming support for
            # something untested is how a package ends up silently missing a
            # dataset somebody believed was there.
            "dividends": CapabilitySupport.UNKNOWN,
            "daily_ohlcv": CapabilitySupport.UNKNOWN,
            "fundamentals": CapabilitySupport.UNKNOWN,
            # Not untested — unavailable. The endpoint carries an effective date
            # and no announcement timestamp, so this is a fact about the data
            # rather than a gap in our testing.
            "corporate_action_knowledge_timestamps": CapabilitySupport.NOT_AVAILABLE_ON_PLAN,
        }

    def limitations(self) -> list[str]:
        """Copied verbatim into the manifest."""
        out = [
            "FMP split records carry an EFFECTIVE (ex-) date only. That is not the "
            "date the split became publicly knowable, no announcement timestamp is "
            "available from this endpoint, and none was invented; "
            "announcement-time corporate-action causality is therefore NOT supported "
            "by this package",
            "FMP was consulted for historical stock splits ONLY. No price, dividend, "
            "fundamental, estimate, statement or ownership data in this package comes "
            "from FMP",
            self.pacer.describe(),
        ]
        if self.support is CapabilitySupport.NOT_AVAILABLE_ON_PLAN:
            out.append(
                "the FMP splits endpoint is not available on this subscription. The "
                "absence of split rows is a statement about the plan, NOT about "
                "whether these securities had splits"
            )
        elif self.support is CapabilitySupport.PROVIDER_ERROR:
            out.append(
                "the FMP splits endpoint errored during enrichment; the absence of "
                "split rows is unexplained and must not be read as an absence of events"
            )
        if self.empty_symbols:
            out.append(
                f"{len(self.empty_symbols)} symbol(s) returned an empty FMP split list: "
                + ", ".join(sorted(self.empty_symbols))
                + ". An empty list is also what an unrecognised ticker returns, so this "
                "is evidence of no recorded splits only to the extent the ticker is "
                "known good"
            )
        return out

    # -- pacing --------------------------------------------------------------

    def wait_for_slot(self, *, max_wait_s: float | None = None) -> float:
        return self.pacer.wait_for_slot(max_wait_s=max_wait_s)

    # -- fetching ------------------------------------------------------------

    def lookup(self, symbol: str) -> CorporateActionLookup:
        """Ask FMP for one symbol's split history."""
        ticker = symbol.strip().upper()
        if not self._token:
            return CorporateActionLookup(
                symbol=ticker,
                support=CapabilitySupport.UNKNOWN,
                status=FetchStatus.REJECTED,
                error=(
                    f"no API key: set {self.credential_env} in your environment. "
                    "Refusing to issue an unauthenticated request that would come "
                    "back 401 and look like a network problem."
                ),
            )

        url = self._url(ticker)
        safe_url = redact_url(url)
        started = time.perf_counter()
        try:
            body = self.transport.get(url)
        except ProviderAuthError as error:
            return self._failure(ticker, safe_url, error, started, auth=True)
        except ProviderRateLimitError as error:
            self.pacer.note_rate_limited()
            return self._failure(ticker, safe_url, error, started, rate_limited=True)
        except ProviderUnreachableError as error:
            # No HTTP status at all. The vendor never saw the request, so this
            # says nothing whatever about whether the symbol has splits.
            return CorporateActionLookup(
                symbol=ticker,
                support=CapabilitySupport.UNKNOWN,
                status=FetchStatus.UNREACHABLE,
                url=safe_url,
                error=redact_text(str(error), self._token),
                elapsed_s=time.perf_counter() - started,
            )
        except ProviderError as error:
            return self._failure(ticker, safe_url, error, started)

        return self._interpret(ticker, safe_url, body, time.perf_counter() - started)

    def replay(self, symbol: str, raw: bytes) -> CorporateActionLookup:
        """Re-interpret a cached body through the same path a live one takes."""
        return self._interpret(symbol.strip().upper(), "", raw, 0.0, cached=True)

    def _url(self, symbol: str) -> str:
        endpoint = ENDPOINTS[AcquisitionDataset.SPLITS]
        return f"{BASE_URL}{endpoint}?symbol={symbol}&apikey={self._token}"

    def _failure(
        self,
        symbol: str,
        safe_url: str,
        error: Exception,
        started: float,
        *,
        auth: bool = False,
        rate_limited: bool = False,
    ) -> CorporateActionLookup:
        message = redact_text(str(error), self._token)
        if rate_limited:
            status = (
                FetchStatus.QUOTA_EXHAUSTED
                if _looks_like_daily_cap(message)
                else FetchStatus.RATE_LIMITED
            )
            support = CapabilitySupport.UNKNOWN
        elif auth and _looks_like_plan_restriction(message):
            status, support = FetchStatus.REJECTED, CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        elif auth:
            # A 401/403 that does not name a plan is a credential problem, and
            # "your key is wrong" must not be recorded as "your plan lacks this".
            status, support = FetchStatus.REJECTED, CapabilitySupport.UNKNOWN
        else:
            status, support = FetchStatus.REJECTED, CapabilitySupport.PROVIDER_ERROR
        self._note_support(support)
        return CorporateActionLookup(
            symbol=symbol,
            support=support,
            status=status,
            url=safe_url,
            error=message,
            elapsed_s=time.perf_counter() - started,
        )

    # -- interpreting the payload --------------------------------------------

    def _interpret(
        self,
        symbol: str,
        safe_url: str,
        body: bytes,
        elapsed: float,
        *,
        cached: bool = False,
    ) -> CorporateActionLookup:
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as error:
            self._note_support(CapabilitySupport.PROVIDER_ERROR)
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.PROVIDER_ERROR,
                status=FetchStatus.MALFORMED,
                url=safe_url,
                raw=body,
                error=f"response was not JSON: {error}",
                elapsed_s=elapsed,
            )

        # FMP reports several non-success conditions in a 200 body under an
        # "Error Message" key. A tool that trusted the HTTP status would record
        # "ok, zero splits" for a plan restriction.
        if isinstance(decoded, dict):
            message = redact_text(
                str(decoded.get("Error Message") or decoded.get("error") or ""), self._token
            )
            if message:
                return self._error_body(symbol, safe_url, body, message, elapsed)
            self._note_support(CapabilitySupport.PROVIDER_ERROR)
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.PROVIDER_ERROR,
                status=FetchStatus.MALFORMED,
                url=safe_url,
                raw=body,
                error=(
                    "expected a JSON array of split records and got an object with "
                    f"keys {sorted(str(k) for k in decoded)}"
                ),
                elapsed_s=elapsed,
            )

        if not isinstance(decoded, list):
            self._note_support(CapabilitySupport.PROVIDER_ERROR)
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.PROVIDER_ERROR,
                status=FetchStatus.MALFORMED,
                url=safe_url,
                raw=body,
                error=f"expected a JSON array of split records and got {type(decoded).__name__}",
                elapsed_s=elapsed,
            )

        events, findings = normalize_splits(symbol, decoded)
        if not decoded:
            if symbol not in self.empty_symbols:
                self.empty_symbols.append(symbol)
            self._note_support(CapabilitySupport.EMPTY_VALID_RESPONSE)
            findings.append(
                f"{symbol}: FMP returned an empty split list. That is the vendor "
                "holding no split records — and is also exactly what an "
                "unrecognised ticker returns, so it is evidence that this "
                "security never split only to the extent the ticker is known good."
            )
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.EMPTY_VALID_RESPONSE,
                status=FetchStatus.CACHED if cached else FetchStatus.EMPTY,
                url=safe_url,
                raw=body,
                findings=tuple(findings),
                http_status=200,
                elapsed_s=elapsed,
            )

        self._note_support(CapabilitySupport.AVAILABLE)
        return CorporateActionLookup(
            symbol=symbol,
            support=CapabilitySupport.AVAILABLE,
            status=FetchStatus.CACHED if cached else FetchStatus.OK,
            splits=events,
            url=safe_url,
            raw=body,
            findings=tuple(findings),
            http_status=200,
            elapsed_s=elapsed,
        )

    def _error_body(
        self, symbol: str, safe_url: str, body: bytes, message: str, elapsed: float
    ) -> CorporateActionLookup:
        if _looks_like_daily_cap(message):
            self.pacer.note_rate_limited()
            self._note_support(CapabilitySupport.UNKNOWN)
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.UNKNOWN,
                status=FetchStatus.QUOTA_EXHAUSTED,
                url=safe_url,
                raw=body,
                error=f"FMP: {message}",
                elapsed_s=elapsed,
            )
        if _looks_like_plan_restriction(message):
            self._note_support(CapabilitySupport.NOT_AVAILABLE_ON_PLAN)
            return CorporateActionLookup(
                symbol=symbol,
                support=CapabilitySupport.NOT_AVAILABLE_ON_PLAN,
                status=FetchStatus.REJECTED,
                url=safe_url,
                raw=body,
                error=f"FMP: {message}",
                elapsed_s=elapsed,
            )
        support = (
            CapabilitySupport.UNKNOWN
            if _looks_like_bad_key(message)
            else CapabilitySupport.PROVIDER_ERROR
        )
        self._note_support(support)
        return CorporateActionLookup(
            symbol=symbol,
            support=support,
            status=FetchStatus.REJECTED,
            url=safe_url,
            raw=body,
            error=f"FMP: {message}",
            elapsed_s=elapsed,
        )

    def _note_support(self, support: CapabilitySupport) -> None:
        """Record what a response revealed, never downgrading a proven success.

        Once the endpoint has answered with data, a later empty response for a
        stock that never split must not turn the capability back into "empty"
        for the whole pass.
        """
        current = self.support
        if (
            current is CapabilitySupport.AVAILABLE
            and support is not CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        ):
            return
        if (
            current is CapabilitySupport.UNKNOWN
            or support is CapabilitySupport.AVAILABLE
            or support is CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        ):
            self.support = support


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

#: Keys FMP uses for the split fraction. Both spellings handled; nothing else is
#: coerced, because getting the direction wrong inverts every price before the
#: event and a silent fallback is how that happens.
_NUMERATOR_KEYS = ("numerator", "splitNumerator", "new")
_DENOMINATOR_KEYS = ("denominator", "splitDenominator", "old")


def normalize_splits(symbol: str, records: list[Any]) -> tuple[tuple[SplitEvent, ...], list[str]]:
    """FMP split records into canonical events, keeping the vendor's numbers.

    ``numerator`` and ``denominator`` are preserved rather than discarded once
    the ratio is derived: a ratio of ``0.1`` could be 1-for-10 or 2-for-20, and
    the pair is what an argument about direction gets settled against.

    Nothing here assumes a forward split. A 1-for-8 reverse split normalizes to
    a ratio of ``0.125`` and reconstructs correctly; treating every record as
    ``numerator/1`` would turn that into a factor of 1 and leave the prices
    untouched, silently.

    Unexpected fields — a ``splitType`` this code has never seen, an extra key —
    are carried through rather than matched against a hard-coded list. A vendor
    that starts labelling something "reverse_split" or "stock_dividend" should
    show up in the data, not be filtered out by a whitelist written today.
    """
    events: list[SplitEvent] = []
    findings: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            findings.append(f"{symbol}: split record {index} is not an object; not written")
            continue

        declared = str(record.get("symbol") or "").strip().upper()
        if declared and declared != symbol:
            # Asked about one ticker and told about another. Reported rather
            # than silently accepted: attaching another company's split to these
            # prices would rewrite every session before it.
            findings.append(
                f"{symbol}: FMP returned a record for {declared!r}; not written. "
                "A split from a different security would rewrite every price "
                "before its date."
            )
            continue

        ex_date = _date(record.get("date"))
        if ex_date is None:
            findings.append(
                f"{symbol}: split record {index} has an unreadable date "
                f"({record.get('date')!r}); not written"
            )
            continue

        numerator = _int(_first(record, _NUMERATOR_KEYS))
        denominator = _int(_first(record, _DENOMINATOR_KEYS))
        if numerator is None or denominator is None:
            findings.append(
                f"{symbol} {ex_date}: split record has no readable numerator/denominator "
                f"({record.get('numerator')!r}/{record.get('denominator')!r}); not written. "
                "The ratio is NOT guessed — a wrong direction inverts every price "
                "before this date."
            )
            continue
        if denominator == 0:
            findings.append(
                f"{symbol} {ex_date}: split record has denominator 0, which is not a "
                "ratio; not written"
            )
            continue
        if numerator <= 0 or denominator < 0:
            findings.append(
                f"{symbol} {ex_date}: split record has a non-positive fraction "
                f"{numerator}/{denominator}; not written"
            )
            continue

        ratio = Decimal(numerator) / Decimal(denominator)
        events.append(
            SplitEvent(
                ex_date=ex_date,
                ratio=ratio,
                source="fmp/stable/splits",
                numerator=numerator,
                denominator=denominator,
                split_type=str(record.get("splitType") or record.get("split_type") or "").strip(),
                # Never set. FMP's `date` is the effective date; when the split
                # became publicly knowable is a different fact this endpoint does
                # not carry, and inventing it would license research the data
                # cannot support.
                announced_at=None,
            )
        )

    events.sort(key=lambda e: e.ex_date)
    return tuple(events), findings


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

#: FMP's wording when a plan does not include an endpoint. Matched on phrases
#: rather than a status code because the same 403 also means "bad key", and the
#: two lead to opposite conclusions about whether the data exists.
_PLAN_PHRASES: tuple[str, ...] = (
    "exclusive endpoint",
    "special endpoint",
    "not available under your current subscription",
    "upgrade your plan",
    "premium",
    "legacy endpoint",
    "your subscription",
)

#: FMP's wording when the plan's *request allowance* is spent. Distinct from a
#: plan restriction: the endpoint is included, there are simply no requests left,
#: and the remedy is tomorrow rather than money.
_DAILY_PHRASES: tuple[str, ...] = ("limit reach", "daily limit", "rate limit exceeded")

_DAILY_RE = re.compile(r"\b(daily|day)\b")

_BAD_KEY_PHRASES: tuple[str, ...] = ("invalid api key", "invalid apikey", "unauthorized")


def _looks_like_plan_restriction(message: str) -> bool:
    lowered = message.lower()
    if any(phrase in lowered for phrase in _BAD_KEY_PHRASES):
        # A bad key is not a plan restriction, and recording it as one would put
        # "not available on this subscription" in a manifest over a typo.
        return False
    return any(phrase in lowered for phrase in _PLAN_PHRASES)


def _looks_like_bad_key(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _BAD_KEY_PHRASES)


def _looks_like_daily_cap(message: str) -> bool:
    """Tell the day wall from the minute wall.

    The remedies differ by three orders of magnitude — wait a minute, or come
    back tomorrow — so guessing wrong either burns a day or spins pointlessly
    against a limit that will not move. FMP's own daily-cap wording is matched
    explicitly; anything else mentioning a day is treated the same way, and a
    bare 429 with no such wording is left as the minute wall.
    """
    lowered = message.lower()
    if any(phrase in lowered for phrase in _DAILY_PHRASES):
        return True
    return bool(_DAILY_RE.search(lowered))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _date(value: Any) -> dt.date | None:
    """The effective date, taken verbatim.

    ``2020-08-31`` is a calendar label, not an instant. Converting it through a
    timezone is how a split lands on the wrong side of a session boundary, so
    anything after a space or a ``T`` is discarded rather than parsed.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    head = text.replace("T", " ").split(" ")[0]
    try:
        return dt.date.fromisoformat(head)
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    """Parse an integer count of shares, refusing anything fractional.

    A numerator of ``2.5`` is not a share count and is not silently truncated:
    truncating it would produce a plausible ratio from an implausible record.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


__all__ = [
    "BACKOFF_FACTOR",
    "BASE_URL",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "ENDPOINTS",
    "MAX_INTERVAL_S",
    "FmpSplitSource",
    "RequestPacer",
    "normalize_splits",
]
