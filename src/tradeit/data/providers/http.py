"""Shared HTTP transport for vendor adapters.

Three concerns that every real adapter has and that are worth solving once:

**The raw payload is cached verbatim, before parsing.** Not for speed —
for reproducibility. A vendor's history is not immutable: symbols get remapped,
prices get corrected, and a series fetched today may differ from the same
series fetched last month. Keeping the bytes we actually parsed means a
disagreement between two runs can be settled by looking, rather than argued
about. The cache key includes the full request, so a changed parameter is a
different cache entry rather than a stale hit.

**Failures are diagnosed, not just raised.** A vendor adapter fails for at
least four distinguishable reasons — no credential, bad credential, rate limit,
and network egress blocked — and telling the operator "request failed" when the
answer is "your firewall does not allow this host" wastes an afternoon. The
distinction is real in this project's own environment: the validation gate
found every market-data host refused at the CONNECT stage by proxy policy,
which is not a subscription problem and is not fixed by buying one.

**Rate limits are respected by construction.** Free tiers are strict, and the
punishment for exceeding them is usually a silent IP ban rather than a 429.

Uses ``urllib`` rather than ``requests`` deliberately: one fewer dependency for
a job that is a GET with a timeout, and it honours ``HTTPS_PROXY`` and the
system CA bundle without configuration.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from tradeit.errors import ProviderError

log = structlog.get_logger(__name__)

#: Sent so vendors can identify us. Several free tiers reject the default
#: urllib agent outright.
USER_AGENT = "tradeit/0.3 (quantitative research; contact via repository)"


class ProviderUnreachableError(ProviderError):
    """The vendor could not be contacted at all.

    Distinct from a vendor *rejecting* a request. This one means the bytes never
    left the machine, or never got past a proxy — which no credential fixes.
    """


class ProviderAuthError(ProviderError):
    """The vendor rejected our credential, or we had none to send.

    Carries the HTTP status and the vendor's own response body where there was
    one, because "401 or 403" is not enough to tell a wrong key from a plan that
    does not include the endpoint, and those lead to opposite conclusions about
    whether the data exists.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: bytes = b"") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderEntitlementError(ProviderError):
    """The vendor understood the request and the plan does not cover it.

    Raised for **HTTP 402 Payment Required**, which is not ambiguous the way a
    403 is: the request was understood, the credential was accepted, and the
    answer is that this account is not entitled to the data. That is a fact
    about a subscription, never about whether the underlying corporate actions
    happened, and it is **not retryable** — re-asking a question the vendor has
    answered "not on your plan" burns quota against a certainty.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: bytes = b"") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderRateLimitError(ProviderError):
    """The vendor asked us to slow down."""


@dataclass(slots=True)
class ResponseCache:
    """Verbatim on-disk cache of vendor responses.

    Stores the payload alongside a small sidecar recording the URL, the fetch
    instant and the digest of the body. The sidecar is what makes a cached file
    evidence rather than an anonymous blob: two runs that disagree can compare
    digests and fetch times instead of guessing whether the vendor revised
    something.
    """

    root: Path
    #: ``None`` means cached entries never expire. Correct for historical daily
    #: bars, which do not change; wrong for a quote endpoint.
    ttl: dt.timedelta | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    @staticmethod
    def key(url: str, headers: dict[str, str] | None = None) -> str:
        """Hash the whole request, so a changed parameter cannot hit a stale entry.

        Header *names* participate but values do not: an API key rotating must
        not invalidate a cache of public price history, and must not be written
        into a filename.
        """
        material = json.dumps(
            {"url": url, "headers": sorted((headers or {}).keys())}, sort_keys=True
        )
        return hashlib.sha256(material.encode()).hexdigest()[:32]

    def _paths(self, key: str) -> tuple[Path, Path]:
        shard = self.root / key[:2]
        return shard / f"{key}.body", shard / f"{key}.json"

    def get(self, key: str) -> bytes | None:
        body_path, meta_path = self._paths(key)
        if not body_path.exists() or not meta_path.exists():
            return None
        if self.ttl is not None:
            meta = json.loads(meta_path.read_text())
            fetched = dt.datetime.fromisoformat(meta["fetched_at"])
            if dt.datetime.now(dt.UTC) - fetched > self.ttl:
                return None
        return body_path.read_bytes()

    def put(self, key: str, url: str, body: bytes) -> None:
        body_path, meta_path = self._paths(key)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta_path.write_text(
            json.dumps(
                {
                    "url": _redact(url),
                    "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                },
                indent=2,
            )
        )

    def manifest(self) -> list[dict[str, Any]]:
        """Every cached response, for a reproducibility appendix."""
        return sorted(
            (json.loads(p.read_text()) for p in self.root.glob("*/*.json")),
            key=lambda m: str(m.get("url", "")),
        )


def _redact(url: str) -> str:
    """Strip credentials from a URL before it is written anywhere.

    Vendors put API keys in query strings. A cache sidecar, a log line and an
    exception message are all places a key must not end up.
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.query:
        return url
    secret_names = {"token", "apikey", "api_key", "apiKey", "key", "auth", "password"}
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    cleaned = [(k, "REDACTED" if k in secret_names else v) for k, v in pairs]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(cleaned)))


@dataclass(slots=True)
class HttpTransport:
    """A GET with retries, rate limiting, caching and honest failures."""

    cache: ResponseCache | None = None
    timeout_s: float = 30.0
    max_attempts: int = 4
    #: Minimum seconds between requests. Derived from a vendor's stated limit
    #: by the adapter, not guessed here.
    min_interval_s: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)
    _last_request: float = field(default=0.0, init=False)

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        merged = {"User-Agent": USER_AGENT, **self.headers, **(headers or {})}

        cache_key = ResponseCache.key(url, merged) if self.cache else ""
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                log.debug("http.cache_hit", url=_redact(url))
                return cached

        body = self._fetch(url, merged)
        if self.cache is not None:
            self.cache.put(cache_key, url, body)
        return body

    def _throttle(self) -> None:
        if self.min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)

    def _fetch(self, url: str, headers: dict[str, str]) -> bytes:
        safe = _redact(url)
        last: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            request = urllib.request.Request(url, headers=headers)
            try:
                self._last_request = time.monotonic()
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return bytes(response.read())

            except urllib.error.HTTPError as exc:
                # A status code is the vendor answering. Retrying a 401 forever
                # is how a typo in a key becomes an IP ban.
                #
                # The body is read once and passed on. Without it an adapter
                # cannot tell "your key is wrong" from "your plan lacks this",
                # and it is the vendor's own message that separates them.
                body = _read_error_body(exc)
                if exc.code == 402:
                    # Payment Required. Unlike a 403 this is unambiguous: the
                    # request was understood and the account is not entitled.
                    raise ProviderEntitlementError(
                        f"{safe} returned 402 Payment Required: this account's plan does "
                        "not include what was requested. Not retried -- the answer will "
                        "not change until the subscription does.",
                        status_code=402,
                        body=body,
                    ) from exc
                if exc.code in (401, 403):
                    raise ProviderAuthError(
                        f"{safe} returned {exc.code}: the vendor rejected the request. "
                        "Check the API key and the plan's entitlements -- a free tier "
                        "commonly returns 403 for endpoints it does not include.",
                        status_code=exc.code,
                        body=body,
                    ) from exc
                if exc.code == 429:
                    retry_after = float(exc.headers.get("Retry-After") or 0) or 2**attempt
                    if attempt == self.max_attempts:
                        raise ProviderRateLimitError(
                            f"{safe} rate-limited after {attempt} attempts"
                        ) from exc
                    log.warning("http.rate_limited", url=safe, sleeping=retry_after)
                    time.sleep(retry_after)
                    last = exc
                    continue
                if 500 <= exc.code < 600 and attempt < self.max_attempts:
                    time.sleep(2**attempt)
                    last = exc
                    continue
                raise ProviderError(f"{safe} returned HTTP {exc.code}: {exc.reason}") from exc

            except urllib.error.URLError as exc:
                # No status code at all: DNS, TLS, timeout, or a proxy refusing
                # CONNECT. Worth separating from a vendor rejection because the
                # remedy is completely different.
                last = exc
                if attempt < self.max_attempts:
                    time.sleep(2**attempt)
                    continue
                raise ProviderUnreachableError(
                    f"could not reach {safe} after {attempt} attempts: {exc.reason}. "
                    "No HTTP status was returned, so the vendor never saw the request. "
                    "Typical causes are DNS failure, TLS interception, or an egress "
                    "policy refusing CONNECT to this host -- none of which is fixed by "
                    "a credential or a subscription."
                ) from exc

        raise ProviderUnreachableError(f"could not reach {safe}: {last}")


def _read_error_body(exc: urllib.error.HTTPError, limit: int = 4096) -> bytes:
    """The vendor's own message from an error response, best effort.

    Bounded and never raising: this runs on a path that is already failing, and
    a second failure while reading the explanation would replace a diagnosable
    error with an undiagnosable one.
    """
    try:
        return bytes(exc.read()[:limit])
    except Exception:  # pragma: no cover - defensive; the body is a bonus
        return b""


def reachability_report(hosts: list[str], transport: HttpTransport | None = None) -> dict[str, str]:
    """Probe hosts and classify each outcome.

    Written for the operator staring at an empty database. It distinguishes
    "the vendor said no" from "nothing left this machine", which are the two
    findings that lead to opposite actions.
    """
    probe = transport or HttpTransport(max_attempts=1, timeout_s=8.0)
    out: dict[str, str] = {}
    for host in hosts:
        try:
            probe.get(f"https://{host}/")
            out[host] = "reachable"
        except ProviderEntitlementError:
            out[host] = "reachable (not included in this plan)"
        except ProviderAuthError:
            out[host] = "reachable (credential rejected)"
        except ProviderRateLimitError:
            out[host] = "reachable (rate-limited)"
        except ProviderUnreachableError as exc:
            out[host] = f"unreachable: {exc}"
        except ProviderError as exc:
            out[host] = f"reachable (HTTP error): {exc}"
    return out
