"""Twelve Data acquisition adapter.

Four things about this vendor drive the design, and each is the reason for a
piece of machinery that Tiingo did not need.

**Daily prices are split-adjusted.** Twelve Data documents that daily, weekly
and monthly series are adjusted for stock splits, while intraday is not. So this
adapter declares :attr:`AdjustmentPolicyDeclaration.SPLIT_ADJUSTED` and the
manifest says so. Labelling these as raw exchange prints would be the single
most damaging thing this file could do: the series looks perfectly plausible
either way, and every pattern drawn on it would be drawn on a history nobody
could have seen. Raw values are *reconstructed* separately, into a sidecar,
labelled DERIVED — see :mod:`tradeit.acquisition.reconstruct`.

**The key goes in the query string.** Twelve Data authenticates with an
``apikey`` parameter, not a header. Every URL this adapter produces therefore
contains a secret, and every one of them passes through
:func:`~tradeit.acquisition.redaction.redact_url` before it can reach a journal
line, a cache sidecar, a log or an exception message.

**Batching saves requests, not credits.** ``/time_series`` accepts a
comma-separated symbol list, and charges per symbol. Batching is still worth
doing — one connection instead of eight, one round trip instead of eight — but
the credit ledger counts symbols, and :class:`CreditLedger` is what keeps the
two from being confused.

**Corporate actions come from dedicated endpoints, and may not be on the plan.**
``/splits`` and ``/dividends`` are separate calls. A subscription that does not
include them answers in a way that is easy to mistake for "this security had no
splits", which would write a falsehood into a package. Every such response is
classified — :class:`~tradeit.acquisition.base.CapabilitySupport` — and a plan
restriction is never recorded as an absence of events.

Endpoints used, all documented: ``/time_series``, ``/splits``, ``/dividends``,
``/earliest_timestamp``. Nothing is guessed; an endpoint this adapter has not
been told about is simply not called.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    CreditUsage,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    SymbolStatus,
    register,
)
from tradeit.acquisition.normalize import NormalizedRows, canonical_bar_row
from tradeit.acquisition.reconstruct import (
    SplitEvent,
    SplitFactorConvention,
    to_share_count_multiplier,
)
from tradeit.acquisition.redaction import credential_hint, redact_text, redact_url
from tradeit.core.calendar import TradingCalendar, get_calendar
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.data.providers.http import (
    HttpTransport,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from tradeit.errors import DataError, ProviderError

BASE_URL = "https://api.twelvedata.com"

#: Endpoint path per dataset. Explicit, so an unlisted dataset cannot be reached
#: by string-building a plausible-looking path.
ENDPOINTS: Mapping[AcquisitionDataset, str] = {
    AcquisitionDataset.DAILY_PRICES: "time_series",
    AcquisitionDataset.SPLITS: "splits",
    AcquisitionDataset.DIVIDENDS: "dividends",
    AcquisitionDataset.EARLIEST_TIMESTAMP: "earliest_timestamp",
}

#: Response headers Twelve Data uses to report the plan's allowance. Read where
#: present; never assumed.
CREDITS_USED_HEADER = "api-credits-used"
CREDITS_LEFT_HEADER = "api-credits-left"

#: Symbols per ``/time_series`` call by default.
#:
#: Eight rather than the maximum. The tradeoffs pull in opposite directions:
#: larger batches cut round trips, and larger batches make one bad ticker
#: harder to attribute and one failed request more expensive to redo. Eight
#: keeps a retry cheap, keeps a 90-symbol universe at a dozen calls, and keeps
#: each response small enough to read when something looks wrong. Configurable,
#: because the right answer depends on a plan nobody here can see.
DEFAULT_BATCH_SIZE = 8

#: Credits ``/time_series`` costs per symbol. Used only when the vendor does not
#: report actual consumption, and marked estimated when it is.
CREDITS_PER_SYMBOL = 1

#: Twelve Data's documented ceiling on rows per response. The daily range this
#: project asks for sits inside it, but "inside it" is a claim about arithmetic
#: rather than about what arrived, so coverage is verified per symbol.
MAX_OUTPUTSIZE = 5000

#: Free-plan credits per minute. Overridable; a reported figure always wins.
DEFAULT_CREDITS_PER_MINUTE = 8

#: How this vendor's split factors are read. See :func:`_split_ratio` for the
#: evidence and for what went wrong before this was declared explicitly.
SPLIT_FACTOR_CONVENTION = SplitFactorConvention.PRICE_ADJUSTMENT_MULTIPLIER

#: Days added to the requested ``end_date`` before asking, then trimmed away.
#:
#: A live run requesting 2010-01-01..2025-12-31 came back with a last session of
#: 2025-12-30 for two independent symbols, and 2025-12-31 was a US trading
#: session. The simplest explanation is that ``end_date`` is exclusive, or is
#: read as the instant ``2025-12-31T00:00:00`` and compared with ``<``.
#:
#: **The fix is safe whether or not that explanation is right**, which is why it
#: is implemented despite the vendor's documentation being unreadable from this
#: environment. Asking for one extra calendar day and then discarding every bar
#: after the requested end produces byte-identical output under an inclusive
#: ``end_date`` and recovers the missing session under an exclusive one. The
#: trim is what makes it safe: without it, this would be a silent one-day
#: lookahead, which is the exact failure the rest of the platform is built to
#: prevent.
END_DATE_PROBE_DAYS = 1


@register
class TwelveDataAcquisition:
    """Daily prices, splits and dividends from Twelve Data."""

    name = "twelve_data"
    credential_env = "TWELVE_DATA_API_KEY"

    produces: tuple[DatasetKind, ...] = (
        DatasetKind.INSTRUMENTS,
        DatasetKind.SYMBOL_MAPPINGS,
        DatasetKind.DAILY_BARS,
        DatasetKind.SPLITS,
        DatasetKind.DIVIDENDS,
    )
    implemented = True
    credits_per_minute = DEFAULT_CREDITS_PER_MINUTE

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: HttpTransport | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        rate_limit_per_minute: int | None = None,
        credits_per_minute: int | None = None,
        timeout_s: float = 30.0,
        max_attempts: int = 4,
        fetch_corporate_actions: bool = True,
    ) -> None:
        self._token = token or os.environ.get(self.credential_env, "").strip()
        self.batch_size = max(1, batch_size)
        self.credits_per_minute = (
            credits_per_minute or rate_limit_per_minute or (DEFAULT_CREDITS_PER_MINUTE)
        )
        self.fetch_corporate_actions = fetch_corporate_actions
        self.transport = transport or HttpTransport(
            cache=None,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            # Pacing is the credit ledger's job: it knows the vendor's reported
            # allowance, which a fixed interval cannot. A small floor remains so
            # a burst of cached-miss requests does not arrive as one volley.
            min_interval_s=0.2,
        )
        #: Per-dataset support, discovered as the run proceeds. Everything
        #: starts UNKNOWN because nothing has been exercised yet, and UNKNOWN is
        #: the honest state for an untested capability.
        self.support: dict[AcquisitionDataset, CapabilitySupport] = {
            AcquisitionDataset.DAILY_PRICES: CapabilitySupport.UNKNOWN,
            AcquisitionDataset.SPLITS: CapabilitySupport.UNKNOWN,
            AcquisitionDataset.DIVIDENDS: CapabilitySupport.UNKNOWN,
        }
        #: Split events per symbol, kept so the reconstruction step can use the
        #: vendor's authoritative record rather than inferring from prices.
        self.splits_by_symbol: dict[str, list[SplitEvent]] = {}
        #: Per-symbol coverage as actually returned, for the truncation check.
        self.coverage: dict[str, tuple[dt.date, dt.date, int]] = {}

    # -- credentials ---------------------------------------------------------

    def has_credential(self) -> bool:
        return bool(self._token)

    def credential_hint(self) -> str:
        return credential_hint(self._token)

    # -- declared capabilities ------------------------------------------------

    def adjustment_policy(self) -> AdjustmentPolicyDeclaration:
        """Split-adjusted, per the vendor's own documentation.

        Not a judgement call and not configurable. Declaring these prices raw
        would put a history nobody could have seen into the package's price
        columns, and the resulting series is indistinguishable from a real one
        by inspection.
        """
        return AdjustmentPolicyDeclaration.SPLIT_ADJUSTED

    def capabilities(self) -> Mapping[str, CapabilitySupport]:
        """What this adapter can supply, with untested things marked UNKNOWN."""
        return {
            "daily_ohlcv": self.support[AcquisitionDataset.DAILY_PRICES],
            "splits": self.support[AcquisitionDataset.SPLITS],
            "dividends": self.support[AcquisitionDataset.DIVIDENDS],
            # Never exercised by this adapter. Claiming support for something
            # untested is how a package ends up silently missing a dataset
            # somebody believed was there.
            "intraday_ohlcv": CapabilitySupport.UNKNOWN,
            "reference_data": CapabilitySupport.UNKNOWN,
            "delisted_securities": CapabilitySupport.UNKNOWN,
            "historical_symbol_mapping": CapabilitySupport.UNKNOWN,
            "historical_universe_membership": CapabilitySupport.NOT_AVAILABLE_ON_PLAN,
            "corporate_action_knowledge_timestamps": CapabilitySupport.UNKNOWN,
            "filing_timestamps": CapabilitySupport.NOT_AVAILABLE_ON_PLAN,
            "fundamentals": CapabilitySupport.UNKNOWN,
        }

    def missing_datasets(self) -> list[DatasetKind]:
        return [kind for kind in DatasetKind if kind not in self.produces]

    def limitations(self) -> list[str]:
        """Copied verbatim into the manifest."""
        out = [
            "DAILY PRICES ARE SPLIT-ADJUSTED, not raw exchange prints. Twelve Data "
            "documents that daily, weekly and monthly series are adjusted for stock "
            "splits. The package declares split_adjusted and the corporate-action "
            "checks, which exist to find the artefacts adjustment removes, cannot "
            "run against it",
            "raw prices are reconstructed into a DERIVED sidecar by inverting the "
            "split adjustment, never written into the package's price columns, and "
            "never described as vendor raw data",
            "no per-bar publication timestamp: knowledge_time is derived from the "
            "session close by a rule and marked ESTIMATED rather than REPORTED",
            "no historical index constituents; survivorship bias is reduced by "
            "including delisted names where the vendor has them, not solved",
            "daily bars only. Intraday is a separate entitlement and is deliberately "
            "not part of the first empirical package",
        ]
        for dataset in (AcquisitionDataset.SPLITS, AcquisitionDataset.DIVIDENDS):
            state = self.support[dataset]
            if state is CapabilitySupport.NOT_AVAILABLE_ON_PLAN:
                out.append(
                    f"the {dataset} endpoint is not available on this subscription. "
                    f"The package therefore contains no {dataset} rows, and that "
                    "absence is a statement about the plan, NOT about whether these "
                    "securities had such events"
                )
            elif state is CapabilitySupport.PROVIDER_ERROR:
                out.append(
                    f"the {dataset} endpoint errored during acquisition; its absence "
                    "from this package is unexplained and must not be read as an "
                    "absence of events"
                )
            elif state is CapabilitySupport.UNKNOWN:
                out.append(f"the {dataset} endpoint was not exercised; availability is UNKNOWN")
        return out

    # -- planning ------------------------------------------------------------

    def plan(self, symbols: Sequence[str], start: dt.date, end: dt.date) -> list[FetchRequest]:
        """Price batches first, then corporate actions per symbol.

        Prices first because they are the package: if the run stops at a quota
        wall, stopping with prices and no splits leaves something importable,
        while the reverse leaves nothing. Corporate actions are per-symbol
        because those endpoints take one symbol.
        """
        ordered = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        requests: list[FetchRequest] = []
        for index in range(0, len(ordered), self.batch_size):
            chunk = tuple(ordered[index : index + self.batch_size])
            requests.append(
                FetchRequest(
                    dataset=AcquisitionDataset.DAILY_PRICES,
                    symbols=chunk,
                    start=start,
                    end=end,
                )
            )
        if self.fetch_corporate_actions:
            for dataset in (AcquisitionDataset.SPLITS, AcquisitionDataset.DIVIDENDS):
                requests.extend(
                    FetchRequest(dataset=dataset, symbols=(symbol,), start=start, end=end)
                    for symbol in ordered
                )
        return requests

    def credits_for(self, request: FetchRequest) -> int:
        """What this request is expected to cost. Per symbol, not per call."""
        return len(request.symbols) * CREDITS_PER_SYMBOL

    # -- fetching ------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        endpoint = ENDPOINTS.get(request.dataset)
        if endpoint is None:
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error=(
                    f"the Twelve Data adapter has no endpoint for {request.dataset}; "
                    f"it knows {[str(d) for d in ENDPOINTS]}"
                ),
            )
        if not self._token:
            return FetchOutcome(
                request=request,
                status=FetchStatus.REJECTED,
                error=(
                    f"no API key: set {self.credential_env} in your environment. "
                    "Refusing to issue an unauthenticated request that would come "
                    "back 401 and look like a network problem."
                ),
            )

        url = self._url(endpoint, request)
        safe_url = redact_url(url)
        started = time.perf_counter()
        try:
            body = self.transport.get(url)
        except ProviderAuthError as error:
            return self._failure(request, safe_url, FetchStatus.REJECTED, error, started)
        except ProviderRateLimitError as error:
            return self._failure(request, safe_url, FetchStatus.RATE_LIMITED, error, started)
        except ProviderUnreachableError as error:
            return self._failure(request, safe_url, FetchStatus.UNREACHABLE, error, started)
        except ProviderError as error:
            return self._failure(request, safe_url, FetchStatus.REJECTED, error, started)

        elapsed = time.perf_counter() - started
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as error:
            return FetchOutcome(
                request=request,
                status=FetchStatus.MALFORMED,
                url=safe_url,
                raw=body,
                error=f"response was not JSON: {error}",
                elapsed_s=elapsed,
            )
        return self._interpret(request, safe_url, body, decoded, elapsed)

    def _url(self, endpoint: str, request: FetchRequest) -> str:
        params = [
            f"symbol={','.join(request.symbols)}",
            f"apikey={self._token}",
            "format=JSON",
        ]
        if request.dataset is AcquisitionDataset.DAILY_PRICES:
            params.insert(1, "interval=1day")
            # Explicit dates rather than outputsize: a deterministic window is
            # what makes two packages comparable, and outputsize silently caps.
            if request.start:
                params.append(f"start_date={request.start.isoformat()}")
            if request.end:
                # One day past what was asked for, then trimmed back in
                # `_normalize_prices`. See END_DATE_PROBE_DAYS: this recovers a
                # final session an exclusive end_date would drop, and changes
                # nothing at all if end_date turns out to be inclusive.
                probe = request.end + dt.timedelta(days=END_DATE_PROBE_DAYS)
                params.append(f"end_date={probe.isoformat()}")
            params.append(f"outputsize={MAX_OUTPUTSIZE}")
            params.append("order=ASC")
        elif request.dataset in (AcquisitionDataset.SPLITS, AcquisitionDataset.DIVIDENDS):
            if request.start:
                params.append(f"start_date={request.start.isoformat()}")
            if request.end:
                params.append(f"end_date={request.end.isoformat()}")
        return f"{BASE_URL}/{endpoint}?{'&'.join(params)}"

    def _failure(
        self,
        request: FetchRequest,
        safe_url: str,
        status: FetchStatus,
        error: Exception,
        started: float,
    ) -> FetchOutcome:
        message = redact_text(str(error), self._token)
        if status is FetchStatus.REJECTED and _looks_like_plan_restriction(message):
            self._note_support(request.dataset, CapabilitySupport.NOT_AVAILABLE_ON_PLAN)
        elif status is FetchStatus.REJECTED:
            self._note_support(request.dataset, CapabilitySupport.PROVIDER_ERROR)
        if _looks_like_daily_quota(message):
            status = FetchStatus.QUOTA_EXHAUSTED
        return FetchOutcome(
            request=request,
            status=status,
            url=safe_url,
            error=message,
            elapsed_s=time.perf_counter() - started,
        )

    # -- interpreting the payload --------------------------------------------

    def _interpret(
        self,
        request: FetchRequest,
        safe_url: str,
        body: bytes,
        decoded: Any,
        elapsed: float,
    ) -> FetchOutcome:
        """Turn a 200 response into a status.

        Twelve Data answers with HTTP 200 for several conditions that are not
        success — a bad key, a plan restriction, an exhausted quota — carrying
        the real outcome in a ``status``/``code`` body. A tool that trusted the
        HTTP status would record "ok, zero rows" for all of them.
        """
        credits = CreditUsage(charged=self.credits_for(request), estimated=True)

        if isinstance(decoded, dict) and decoded.get("status") == "error":
            code = _as_int(decoded.get("code"))
            message = redact_text(str(decoded.get("message", "")), self._token)
            status, support = _classify_error(code, message)
            self._note_support(request.dataset, support)
            return FetchOutcome(
                request=request,
                status=status,
                url=safe_url,
                raw=body,
                http_status=code,
                error=f"Twelve Data {code}: {message}" if code else message,
                credits=credits,
                elapsed_s=elapsed,
            )

        rows, per_symbol = self._rows_and_statuses(request, decoded)
        if rows:
            self._note_support(request.dataset, CapabilitySupport.AVAILABLE)
        else:
            self._note_support(request.dataset, CapabilitySupport.EMPTY_VALID_RESPONSE)
        return FetchOutcome(
            request=request,
            status=FetchStatus.OK if rows else FetchStatus.EMPTY,
            url=safe_url,
            raw=body,
            rows=rows,
            per_symbol=per_symbol,
            http_status=200,
            credits=credits,
            elapsed_s=elapsed,
        )

    def _rows_and_statuses(
        self, request: FetchRequest, decoded: Any
    ) -> tuple[tuple[dict[str, Any], ...], dict[str, SymbolStatus]]:
        """Flatten both response shapes into rows tagged with their symbol.

        A single-symbol request answers ``{"meta": ..., "values": [...]}``; a
        batch answers ``{"AAPL": {...}, "MSFT": {...}}``. Tagging each row with
        its symbol here means nothing downstream has to know which shape it
        came from — and means a batch where one member failed keeps the others,
        which is the whole reason to interpret per symbol rather than per call.
        """
        rows: list[dict[str, Any]] = []
        statuses: dict[str, SymbolStatus] = {}

        if not isinstance(decoded, dict):
            return (), {s: SymbolStatus.UNKNOWN for s in request.symbols}

        if "values" in decoded or "meta" in decoded:
            blocks = {request.symbol: decoded}
        elif request.dataset in (AcquisitionDataset.SPLITS, AcquisitionDataset.DIVIDENDS):
            # These endpoints answer with a bare list under a key, or with a
            # per-symbol mapping for a batch.
            blocks = (
                {request.symbol: decoded}
                if any(k in decoded for k in ("splits", "dividends"))
                else {k: v for k, v in decoded.items() if isinstance(v, dict)}
            )
        else:
            blocks = {k: v for k, v in decoded.items() if isinstance(v, dict)}

        for symbol, block in blocks.items():
            if not isinstance(block, dict):
                statuses[symbol] = SymbolStatus.UNKNOWN
                continue
            if block.get("status") == "error":
                code = _as_int(block.get("code"))
                message = str(block.get("message", ""))
                statuses[symbol] = _classify_symbol(code, message)
                continue
            values = _extract_values(block, request.dataset)
            if not values:
                statuses[symbol] = SymbolStatus.UNAVAILABLE_HISTORICALLY
                continue
            statuses[symbol] = SymbolStatus.VALID
            meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
            for value in values:
                if isinstance(value, dict):
                    rows.append({**value, "_symbol": symbol, "_meta": meta})

        for symbol in request.symbols:
            statuses.setdefault(symbol, SymbolStatus.NOT_FOUND)
        return tuple(rows), statuses

    def _note_support(self, dataset: AcquisitionDataset, support: CapabilitySupport) -> None:
        """Record what a response revealed, never downgrading a proven success.

        Once an endpoint has answered with data, a later empty response for a
        stock with no splits must not turn its capability back into "empty" for
        the whole package.
        """
        if dataset not in self.support:
            return
        current = self.support[dataset]
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
            self.support[dataset] = support

    # -- normalization -------------------------------------------------------

    def normalize(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        """Vendor rows into canonical package rows."""
        out = NormalizedRows()
        dataset = outcome.request.dataset
        if dataset is AcquisitionDataset.DAILY_PRICES:
            return self._normalize_prices(outcome, ids)
        if dataset is AcquisitionDataset.SPLITS:
            return self._normalize_splits(outcome, ids)
        if dataset is AcquisitionDataset.DIVIDENDS:
            return self._normalize_dividends(outcome, ids)
        return out

    def _normalize_prices(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        out = NormalizedRows()
        by_symbol: dict[str, list[Mapping[str, Any]]] = {}
        for row in outcome.rows:
            by_symbol.setdefault(str(row.get("_symbol", "")), []).append(row)

        # What the operator asked for, not what was sent to the vendor. The two
        # differ by END_DATE_PROBE_DAYS and this is the boundary that decides
        # what lands in the package.
        requested_end = outcome.request.end
        trimmed = 0

        for symbol, rows in by_symbol.items():
            instrument_id = ids.get(symbol)
            if instrument_id is None:
                continue
            seen: set[dt.date] = set()
            sessions: list[dt.date] = []
            # Sorted here rather than trusted from the response: the API's
            # default order is newest-first and the parameter that changes it is
            # one edit away from being dropped. The pipeline downstream requires
            # chronological order and says so by raising.
            for row in sorted(rows, key=lambda r: str(r.get("datetime", ""))):
                session = _session_date(row.get("datetime"))
                if session is None:
                    out.findings.append(
                        f"{symbol}: a row has no readable datetime and was not written"
                    )
                    continue
                if requested_end is not None and session > requested_end:
                    # The other half of END_DATE_PROBE_DAYS. The request asked
                    # for one day beyond the window to find out whether the
                    # vendor's end_date is exclusive; anything past the window
                    # is discarded here, so the package contains exactly the
                    # range that was asked for and no session the operator did
                    # not request can reach it.
                    trimmed += 1
                    continue
                if session in seen:
                    out.findings.append(
                        f"{symbol}: duplicate row for {session}; the second was not written"
                    )
                    continue
                bar = canonical_bar_row(
                    instrument_id=instrument_id,
                    session=session,
                    open_=_decimal(row.get("open")),
                    high=_decimal(row.get("high")),
                    low=_decimal(row.get("low")),
                    close=_decimal(row.get("close")),
                    volume=_decimal(row.get("volume")),
                )
                if bar is None:
                    out.findings.append(
                        f"{symbol} {session}: incomplete OHLC in the vendor row; not written"
                    )
                    continue
                seen.add(session)
                sessions.append(session)
                out.add(DatasetKind.DAILY_BARS, bar)

            if sessions:
                self.coverage[symbol] = (min(sessions), max(sessions), len(sessions))
                meta: Mapping[str, Any] = {}
                for candidate in rows:
                    block = candidate.get("_meta")
                    if isinstance(block, dict) and block:
                        meta = block
                        break
                out.extend(_instrument_rows(symbol, instrument_id, meta, min(sessions)))

        if trimmed:
            # Said out loud rather than done quietly: this is the line that
            # proves the probe day is being discarded, and its absence from a
            # report where end_date turned out to be inclusive is equally
            # informative.
            out.findings.append(
                f"{trimmed} bar(s) after the requested end {requested_end} were "
                f"discarded. The request asks for {END_DATE_PROBE_DAYS} day(s) beyond "
                "the window because this vendor's end_date appears to be exclusive; "
                "the package contains only the range that was requested."
            )
        return out

    def _normalize_splits(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        out = NormalizedRows()
        for row in outcome.rows:
            symbol = str(row.get("_symbol", ""))
            instrument_id = ids.get(symbol)
            ex_date = _session_date(row.get("date") or row.get("ex_date"))
            ratio = _split_ratio(row)
            if instrument_id is None or ex_date is None or ratio is None:
                out.findings.append(
                    f"{symbol}: a split row was unreadable "
                    f"(date={row.get('date')!r}, factor={row.get('factor')!r}) and was not written"
                )
                continue
            vendor_value = _vendor_factor(row)
            out.add(
                DatasetKind.SPLITS,
                {
                    "instrument_id": str(instrument_id),
                    "ex_date": ex_date.isoformat(),
                    # Canonical share-count multiplier, never the vendor's own
                    # number. The vendor's number is kept beside it.
                    "ratio": _plain(ratio),
                    "source_provider": self.name,
                    "vendor_factor": _plain(vendor_value) if vendor_value is not None else "",
                    "vendor_convention": str(SPLIT_FACTOR_CONVENTION),
                },
            )
            self.splits_by_symbol.setdefault(symbol, []).append(
                SplitEvent(
                    ex_date=ex_date,
                    ratio=ratio,
                    source="twelve_data/splits",
                    vendor_value=vendor_value,
                    vendor_convention=SPLIT_FACTOR_CONVENTION,
                )
            )
        return out

    def _normalize_dividends(self, outcome: FetchOutcome, ids: Mapping[str, int]) -> NormalizedRows:
        out = NormalizedRows()
        for row in outcome.rows:
            symbol = str(row.get("_symbol", ""))
            instrument_id = ids.get(symbol)
            ex_date = _session_date(row.get("ex_date") or row.get("date"))
            amount = _decimal(row.get("amount") or row.get("dividend"))
            if instrument_id is None or ex_date is None or amount is None:
                out.findings.append(f"{symbol}: a dividend row was unreadable and was not written")
                continue
            if amount <= 0:
                continue
            out.add(
                DatasetKind.DIVIDENDS,
                {
                    "instrument_id": str(instrument_id),
                    # The canonical column is `cash_amount`. Twelve Data calls it
                    # `amount`; Tiingo calls it `divCash`. Mapping to the contract
                    # here is the whole job of this function, and getting it wrong
                    # is how every dividend row in a package ends up quarantined.
                    "ex_date": ex_date.isoformat(),
                    "cash_amount": _plain(amount),
                },
            )
        return out

    # -- coverage verification ------------------------------------------------

    def coverage_findings(self, start: dt.date, end: dt.date) -> list[str]:
        """Whether each symbol actually returned the range that was asked for.

        Requirement rather than nicety: ``outputsize`` caps a response at 5000
        rows, and the check is not "is the arithmetic under the cap" but "is
        what arrived what was requested". A silently truncated series looks
        exactly like a security that listed late.

        **Both ends are measured against the exchange calendar, not against the
        calendar date.** A request for 2010-01-01 through a Saturday is complete
        when it runs 2010-01-04 to the Friday: 1 January is a market holiday, the
        2nd and 3rd were a weekend, and no vendor owes anybody a bar for a day
        the exchange was shut. A check that said otherwise would cry wolf on
        every well-formed package until nobody read it — which is how the
        genuinely missing 2025-12-31 session nearly went unnoticed.

        What survives is the check that matters: a first or last row that misses
        real *sessions* the request covered. Truncated coverage is still
        reported, and reported with the number of sessions lost.
        """
        out: list[str] = []
        calendar = get_calendar()
        expected_first = _first_session_on_or_after(calendar, start)
        expected_last = _last_session_on_or_before(calendar, end)
        for symbol, (first, last, count) in sorted(self.coverage.items()):
            if count >= MAX_OUTPUTSIZE:
                out.append(
                    f"{symbol}: returned {count:,} rows, at or above the {MAX_OUTPUTSIZE:,} "
                    "response cap. The series may be truncated; narrow the date range "
                    "and acquire in two passes"
                )
            if expected_first is not None and first > expected_first:
                missing = len(calendar.sessions_between(expected_first, first)) - 1
                out.append(
                    f"{symbol}: requested from {start}, whose first trading session is "
                    f"{expected_first}, but the vendor's earliest row is {first} — "
                    f"{missing} session(s) short at the start. Either the security had "
                    "not listed, or the provider's history begins later"
                )
            if expected_last is not None and last < expected_last:
                missing = len(calendar.sessions_between(last, expected_last)) - 1
                out.append(
                    f"{symbol}: requested through {end}, whose last trading session is "
                    f"{expected_last}, but the vendor's latest row is {last} — "
                    f"{missing} session(s) short. This is NOT a weekend or holiday; "
                    "the sessions are genuinely absent"
                )
        # A complete range produces **no line at all**, deliberately. Every
        # finding here reaches `AcquisitionReport.findings`, and a non-empty
        # findings list downgrades the package to VALID_WITH_WARNINGS. An
        # informational "this range is complete" per symbol would therefore mark
        # a perfect 91-symbol download as warned-about, which is both noise and
        # a lie about the package's state.
        return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

#: Body codes that mean the subscription does not cover the request rather than
#: the request being wrong.
_PLAN_CODES = frozenset({403})
_QUOTA_CODES = frozenset({429})
_NOT_FOUND_CODES = frozenset({400, 404})


def _classify_error(code: int | None, message: str) -> tuple[FetchStatus, CapabilitySupport]:
    lowered = message.lower()
    if code in _QUOTA_CODES or "run out of api credits" in lowered:
        if _looks_like_daily_quota(message):
            return FetchStatus.QUOTA_EXHAUSTED, CapabilitySupport.UNKNOWN
        return FetchStatus.RATE_LIMITED, CapabilitySupport.UNKNOWN
    if code in _PLAN_CODES or _looks_like_plan_restriction(message):
        return FetchStatus.REJECTED, CapabilitySupport.NOT_AVAILABLE_ON_PLAN
    if code == 401:
        return FetchStatus.REJECTED, CapabilitySupport.UNKNOWN
    if code in _NOT_FOUND_CODES:
        return FetchStatus.EMPTY, CapabilitySupport.PROVIDER_ERROR
    return FetchStatus.REJECTED, CapabilitySupport.PROVIDER_ERROR


def _classify_symbol(code: int | None, message: str) -> SymbolStatus:
    """Read the message before the code.

    Twelve Data returns 400 for several distinct conditions, so the code alone
    cannot separate "no such symbol" from "this symbol trades on four exchanges,
    say which". The message is the more specific signal and is consulted first;
    the code is the fallback.
    """
    lowered = message.lower()
    if _looks_like_plan_restriction(message):
        return SymbolStatus.PLAN_RESTRICTED
    if "ambiguous" in lowered or "specify exchange" in lowered:
        return SymbolStatus.AMBIGUOUS
    if "not found" in lowered or code in _NOT_FOUND_CODES:
        return SymbolStatus.NOT_FOUND
    return SymbolStatus.UNKNOWN


def _looks_like_plan_restriction(message: str) -> bool:
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "grow plan",
            "pro plan",
            "upgrade",
            "not available under your current plan",
            "is available with",
            "subscription",
        )
    )


#: Matches "daily", "per day", "for the current day", "limit reached for the
#: day". A word boundary rather than a substring so "Monday" and "delay" do not
#: count, and the whole word rather than a fixed phrase list because the vendor
#: words this several ways.
_DAILY_RE = re.compile(r"\b(daily|day)\b")


def _looks_like_daily_quota(message: str) -> bool:
    """Tell the day wall from the minute wall.

    The remedies differ by three orders of magnitude — wait a minute, or come
    back tomorrow — so guessing wrong either burns a day or spins pointlessly
    against a limit that will not move. Anything mentioning a day is treated as
    the day wall; a per-minute message says "minute" and matches nothing here.
    """
    return bool(_DAILY_RE.search(message.lower()))


def read_credit_headers(headers: Mapping[str, str]) -> CreditUsage:
    """Read the plan's allowance from response headers, where present."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    return CreditUsage(
        used=_as_int(lowered.get(CREDITS_USED_HEADER)),
        remaining=_as_int(lowered.get(CREDITS_LEFT_HEADER)),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _extract_values(block: Mapping[str, Any], dataset: AcquisitionDataset) -> list[Any]:
    for key in ("values", "splits", "dividends", "data"):
        candidate = block.get(key)
        if isinstance(candidate, list):
            return candidate
    return []


def _session_date(value: Any) -> dt.date | None:
    """The exchange session date, without inventing a time.

    Twelve Data returns ``2020-01-02`` for a daily bar. It is a session label,
    not an instant, and converting it through a timezone is how a bar lands in
    the wrong session. The date part is taken verbatim; anything after a space
    or a ``T`` is discarded rather than parsed.
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


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _first_session_on_or_after(calendar: TradingCalendar, day: dt.date) -> dt.date | None:
    """The trading session a request starting on ``day`` should reach.

    ``day`` itself when it is a session, otherwise the next one. The 2010-01-01
    case: New Year's Day is a market holiday and the 2nd and 3rd were a weekend,
    so a request from 2010-01-01 is complete when its first bar is 2010-01-04.

    ``None`` when no session is found within the calendar's search window, which
    is a reason to make no claim rather than to invent one.
    """
    if calendar.is_session(day):
        return day
    try:
        return calendar.next_session(day)
    except DataError:
        return None


def _last_session_on_or_before(calendar: TradingCalendar, day: dt.date) -> dt.date | None:
    """The trading session a request ending on ``day`` should reach.

    ``day`` itself when it is a session, otherwise the previous one. ``None``
    when no session is found within the calendar's search window, which is a
    reason to make no claim rather than to invent one.
    """
    if calendar.is_session(day):
        return day
    try:
        return calendar.previous_session(day)
    except DataError:
        return None


def _vendor_factor(row: Mapping[str, Any]) -> Decimal | None:
    """The single number the vendor sent, before normalization.

    Kept so a later disagreement about which way round Twelve Data measures is
    settled by looking at the package rather than by re-downloading it.
    """
    to_factor = _decimal(row.get("to_factor"))
    from_factor = _decimal(row.get("from_factor"))
    if to_factor is not None and from_factor is not None and from_factor != 0:
        return to_factor / from_factor
    raw = row.get("factor") or row.get("ratio") or row.get("split_factor")
    return _decimal(raw) if raw is not None else None


def _split_ratio(row: Mapping[str, Any]) -> Decimal | None:
    """Normalize a Twelve Data split into a canonical share-count multiplier.

    **This function had the direction backwards until a live smoke test caught
    it**, and the correction is worth writing down because the failure was
    silent and severe.

    For AAPL, Twelve Data's ``/splits`` produced ``0.142857142857…`` on
    2014-06-09 and ``0.25`` on 2020-08-31. Those are Apple's real 7-for-1 and
    4-for-1 splits, and 1/7 and 1/4 are the **reciprocals** of their share-count
    multipliers. Apple's share count went *up* sevenfold in 2014; no reading of
    the world makes 0.142857 the multiplier on its share count. Under the
    previous ``to_factor / from_factor`` reading, reconstructing raw prices from
    a Twelve Data schedule would have multiplied every pre-2014 adjusted price
    by 1/28 instead of 28 — turning a $90 print into $3.21 rather than $2,520 —
    and the resulting series would have looked like an ordinary penny stock.

    So Twelve Data's factors are the **price-adjustment** multiplier, or
    equivalently its ``from_factor``/``to_factor`` name the split "4:1" as
    ``from_factor=4, to_factor=1``. Those two narratives differ in wording and
    agree exactly in arithmetic — both give share count = ``from / to`` — so the
    normalization is settled even though which narrative is right is not.

    **What this rests on**, stated plainly: two split events on one security,
    observed on a live free-tier account, whose values are exact reciprocals of
    independently known corporate actions and of FMP's explicit
    numerator/denominator pair for the same dates. Twelve Data's own
    documentation could not be read from this build environment, whose egress
    policy blocks the vendor's host. The declaration is
    :data:`SPLIT_FACTOR_CONVENTION` so it is one line to change, and
    :func:`~tradeit.acquisition.reconstruct.are_reciprocal` reports loudly if a
    second source ever disagrees with it in exactly this way.
    """
    to_factor = _decimal(row.get("to_factor"))
    from_factor = _decimal(row.get("from_factor"))
    if to_factor is not None and from_factor is not None and to_factor != 0:
        # from/to, not to/from. See the docstring: observed as from_factor=4,
        # to_factor=1 for Apple's 4-for-1.
        return from_factor / to_factor

    raw = row.get("factor") or row.get("ratio") or row.get("split_factor")
    if raw is None:
        return None
    text = str(raw).strip()
    for separator in (":", "/", "-for-"):
        if separator in text:
            left, _, right = text.partition(separator)
            numerator = _decimal(left)
            denominator = _decimal(right)
            if numerator is None or denominator is None or denominator == 0:
                return None
            # "4:1" spells the split out in words rather than as a factor, and
            # means four new shares for one old whoever writes it.
            return numerator / denominator
    # A bare decimal carries no direction of its own, so the provider's declared
    # convention settles it rather than the value's magnitude. Reading 0.25 as
    # "a 1-for-4 reverse split" because it is less than one is exactly the
    # inference this refuses to make.
    bare = _decimal(text)
    if bare is None:
        return None
    return to_share_count_multiplier(bare, SPLIT_FACTOR_CONVENTION)


def _instrument_rows(
    symbol: str, instrument_id: int, meta: Mapping[str, Any], first_session: dt.date
) -> NormalizedRows:
    out = NormalizedRows()
    exchange = str(meta.get("exchange") or meta.get("mic_code") or "UNKNOWN")
    out.add(
        DatasetKind.INSTRUMENTS,
        {
            "instrument_id": str(instrument_id),
            "name": str(meta.get("name") or symbol),
            "primary_exchange": exchange[:8],
            # Twelve Data's meta carries a `type` such as "Common Stock" or
            # "ETF". Mapped where present; defaulted honestly where not, rather
            # than guessed from the ticker.
            "asset_class": _asset_class(meta.get("type")),
            "country": str(meta.get("country") or "US") or "US",
            "currency": str(meta.get("currency") or "USD") or "USD",
            "first_trade_date": "",
            "listing_status": "active",
            "delisted_date": "",
        },
    )
    out.add(
        DatasetKind.SYMBOL_MAPPINGS,
        {
            "instrument_id": str(instrument_id),
            "ticker": symbol,
            "valid_from": first_session.isoformat(),
            "valid_to": "",
        },
    )
    return out


def _asset_class(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    known = {
        "common_stock": "common_stock",
        "etf": "etf",
        "exchange_traded_fund": "etf",
        "american_depositary_receipt": "adr",
        "reit": "reit",
    }
    return known.get(text, "common_stock")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "BASE_URL",
    "CREDITS_PER_SYMBOL",
    "DEFAULT_BATCH_SIZE",
    "ENDPOINTS",
    "END_DATE_PROBE_DAYS",
    "MAX_OUTPUTSIZE",
    "SPLIT_FACTOR_CONVENTION",
    "TwelveDataAcquisition",
    "read_credit_headers",
]
