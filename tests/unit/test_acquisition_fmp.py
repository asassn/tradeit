"""FMP split enrichment, exercised against recorded response shapes.

**No real network call happened inside this suite, and none is claimed.** The
project owner verified ``/stable/splits`` from their own machine against a real
free account; everything here runs against deterministic fixtures shaped like
that endpoint's documented responses. A suite that hit the vendor would spend
the account's daily allowance on every CI run and fail during their outages.

What these tests are actually protecting, in rough order of how much damage the
regression would do:

* **A plan restriction is not an absence of splits.** The whole reason FMP is in
  this project is that Twelve Data answers "not on your plan" for ``/splits``,
  and treating that as "this stock never split" would write a false history into
  a package. Every classification path is pinned.
* **Direction.** A reverse split read as a forward one inverts every price
  before its ex-date, and the resulting series looks perfectly plausible.
* **The label.** The reconstructed series is derived from one vendor's adjusted
  bars and another's split schedule, so it was supplied by neither. Several
  tests exist purely to make it loud if it ever gets called a vendor price.
* **Splits outside coverage.** A 2005 split must change nothing in a package
  that starts in 2010, and the arithmetic that guarantees that is one comparison
  operator wide.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradeit import cli_data
from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    FetchStatus,
    available_providers,
)
from tradeit.acquisition.enrich import (
    ENRICHMENT_TOOL_VERSION,
    CorporateActionSource,
    EnrichmentOptions,
    EnrichmentStatus,
    PackageEnricher,
    available_sources,
    compare_schedules,
    get_source_class,
)
from tradeit.acquisition.fmp import (
    BASE_URL,
    DEFAULT_REQUESTS_PER_MINUTE,
    ENDPOINTS,
    FmpSplitSource,
    RequestPacer,
    normalize_splits,
)
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.reconstruct import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    RECONSTRUCTION_LABEL,
    ReconstructionQuality,
    SplitEvent,
)
from tradeit.acquisition.runner import AcquisitionOptions, AcquisitionRunner
from tradeit.acquisition.twelvedata import TwelveDataAcquisition
from tradeit.data.packages.manifest import WORKSPACE_DIRNAME, load_manifest
from tradeit.data.packages.spec import DATASET_SPECS, DatasetKind
from tradeit.data.providers.http import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from tradeit.errors import ConfigError

FMP_SECRET = "FMPSECRETKEY0123456789"
TD_SECRET = "TWELVEDATASECRET99887766"
START = dt.date(2010, 1, 1)
END = dt.date(2011, 3, 31)


# ---------------------------------------------------------------------------
# Fixtures shaped like the vendor's documented responses
# ---------------------------------------------------------------------------

#: Apple's real split history, in FMP's shape. Used for the "a 2005 split must
#: touch nothing in a 2010 package" test, which is the arithmetic this whole
#: module rests on.
AAPL_SPLITS: list[dict[str, Any]] = [
    {"symbol": "AAPL", "date": "2020-08-31", "numerator": 4, "denominator": 1},
    {"symbol": "AAPL", "date": "2014-06-09", "numerator": 7, "denominator": 1},
    {"symbol": "AAPL", "date": "2005-02-28", "numerator": 2, "denominator": 1},
    {"symbol": "AAPL", "date": "2000-06-21", "numerator": 2, "denominator": 1},
    {"symbol": "AAPL", "date": "1987-06-16", "numerator": 2, "denominator": 1},
]

NVDA_SPLITS: list[dict[str, Any]] = [
    {
        "symbol": "NVDA",
        "date": "2024-06-10",
        "numerator": 10,
        "denominator": 1,
        "splitType": "stock_split",
    },
    {
        "symbol": "NVDA",
        "date": "2021-07-20",
        "numerator": 4,
        "denominator": 1,
        "splitType": "stock_split",
    },
    {
        "symbol": "NVDA",
        "date": "2007-09-11",
        "numerator": 3,
        "denominator": 2,
        # A label this code has never seen. It must survive to the package
        # rather than be dropped by a whitelist written today.
        "splitType": "some_future_label",
    },
]

PLAN_ERROR = {
    "Error Message": (
        "Exclusive Endpoint: This endpoint is not available under your current "
        "subscription. Please upgrade your plan."
    )
}
DAILY_CAP_ERROR = {
    "Error Message": "Limit Reach . Please upgrade your plan or visit our documentation"
}
BAD_KEY_ERROR = {"Error Message": "Invalid API KEY. Please retry or visit our documentation"}

#: A 1-for-8 reverse split, for the direction tests. Written out because reading
#: it backwards inverts every price before its ex-date and the resulting series
#: looks perfectly plausible.
REVERSE_SPLIT: dict[str, Any] = {
    "symbol": "AAPL",
    "date": "2020-01-02",
    "numerator": 1,
    "denominator": 8,
}


class FakeFmpTransport:
    """Answers like FMP, including its 200-with-an-error-body habit."""

    def __init__(
        self,
        *,
        by_symbol: dict[str, Any] | None = None,
        default: Any = None,
        raise_on: dict[str, Exception] | None = None,
        body_override: bytes | None = None,
    ) -> None:
        self.by_symbol = by_symbol if by_symbol is not None else {"AAPL": AAPL_SPLITS}
        self.default = default if default is not None else []
        self.raise_on = raise_on or {}
        self.body_override = body_override
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        symbol = url.split("symbol=")[1].split("&")[0]
        error = self.raise_on.get(symbol) or self.raise_on.get("*")
        if error is not None:
            raise error
        if self.body_override is not None:
            return self.body_override
        return json.dumps(self.by_symbol.get(symbol, self.default)).encode()


def make_source(transport: FakeFmpTransport | None = None, **kwargs: Any) -> FmpSplitSource:
    # A very high self-imposed rate so the pacer never actually sleeps in tests.
    kwargs.setdefault("requests_per_minute", 600_000)
    return FmpSplitSource(token=FMP_SECRET, transport=transport or FakeFmpTransport(), **kwargs)


# -- a Twelve Data package to enrich ----------------------------------------


def bars(count: int = 60, *, symbol: str = "AAPL") -> list[dict[str, str]]:
    """Daily rows in Twelve Data's shape: string values, `datetime` key.

    Prices and volumes are chosen to divide exactly by the factors under test,
    so the dollar-volume invariance can be asserted as equality rather than
    "close enough", which would pass while hiding a real drift.
    """
    rows: list[dict[str, str]] = []
    day = dt.date(2010, 1, 4)
    written = 0
    while written < count:
        if day.weekday() < 5:
            rows.append(
                {
                    "datetime": day.isoformat(),
                    "open": "100.00",
                    "high": "112.00",
                    "low": "96.00",
                    "close": "108.00",
                    "volume": str(28_000_000 + written * 28),
                }
            )
            written += 1
        day += dt.timedelta(days=1)
    _ = symbol
    return rows


class FakeTwelveDataTransport:
    """Twelve Data with `/splits` refused by the plan — the real situation."""

    def __init__(self, *, splits_available: bool = False) -> None:
        self.splits_available = splits_available
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        symbols = url.split("symbol=")[1].split("&")[0].split(",")
        if "/time_series" in url:
            blocks = {
                s: {
                    "meta": {"symbol": s, "exchange": "NASDAQ", "type": "Common Stock"},
                    "values": bars(symbol=s),
                    "status": "ok",
                }
                for s in symbols
            }
            payload = blocks[symbols[0]] if len(symbols) == 1 else blocks
            return json.dumps(payload).encode()
        if "/splits" in url:
            if self.splits_available:
                return json.dumps(
                    {"splits": [{"date": "2014-06-09", "from_factor": 1, "to_factor": 7}]}
                ).encode()
            return json.dumps(
                {
                    "code": 403,
                    "message": "/splits is available with the Grow plan and above",
                    "status": "error",
                }
            ).encode()
        if "/dividends" in url:
            return json.dumps({"dividends": [{"ex_date": "2010-03-15", "amount": "0.55"}]}).encode()
        return b"{}"


def build_package(output: Path, symbols: list[str], *, splits_available: bool = False) -> Path:
    """A real acquisition run against fixtures, producing a real package."""
    provider = TwelveDataAcquisition(
        token=TD_SECRET,
        transport=FakeTwelveDataTransport(splits_available=splits_available),
        batch_size=4,
        credits_per_minute=100_000,
    )
    options = AcquisitionOptions(start=START, end=END, max_wait_s=0)
    AcquisitionRunner(provider, symbols, output, options).run()
    return output


def enrich(
    package: Path,
    transport: FakeFmpTransport | None = None,
    source: FmpSplitSource | None = None,
    **kwargs: Any,
) -> Any:
    return PackageEnricher(
        package, source or make_source(transport), EnrichmentOptions(max_wait_s=0, **kwargs)
    ).run()


def read_csv(path: Path) -> list[dict[str, str]]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


# ---------------------------------------------------------------------------
# Registration, scope and credentials
# ---------------------------------------------------------------------------


class TestRegistrationAndScope:
    def test_fmp_is_a_corporate_action_source(self) -> None:
        assert available_sources() == ["fmp"]
        assert get_source_class("fmp") is FmpSplitSource

    def test_fmp_is_NOT_a_price_provider(self) -> None:
        """The structural guarantee that FMP cannot become the OHLCV source.

        Not a convention and not a code review rule: a package whose prices
        quietly came from a different vendor than its manifest says is not
        detectable by inspection afterwards.
        """
        assert "fmp" not in available_providers()
        assert available_providers() == ["eodhd", "tiingo", "twelve_data"]
        assert not hasattr(FmpSplitSource, "plan")
        assert not hasattr(FmpSplitSource, "fetch")
        assert not hasattr(FmpSplitSource, "adjustment_policy")

    def test_it_satisfies_the_enrichment_protocol(self) -> None:
        assert isinstance(make_source(), CorporateActionSource)

    def test_the_env_var_is_the_one_the_docs_promise(self) -> None:
        assert FmpSplitSource.credential_env == "FMP_API_KEY"

    def test_readiness_follows_the_environment(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        assert FmpSplitSource().has_credential() is False
        monkeypatch.setenv("FMP_API_KEY", "abc")
        assert FmpSplitSource().has_credential() is True

    def test_only_the_verified_endpoint_is_known(self) -> None:
        """One endpoint, named. Nothing is string-built into existence."""
        assert list(ENDPOINTS) == [AcquisitionDataset.SPLITS]
        assert ENDPOINTS[AcquisitionDataset.SPLITS] == "/stable/splits"

    def test_the_url_is_the_documented_one(self) -> None:
        source = make_source()
        transport = source.transport
        source.lookup("AAPL")
        assert isinstance(transport, FakeFmpTransport)
        assert transport.calls == [
            f"{BASE_URL}/stable/splits?symbol=AAPL&apikey={FMP_SECRET}",
        ]

    @pytest.mark.parametrize(
        "forbidden",
        [
            "income-statement",
            "balance-sheet",
            "cash-flow",
            "ratios",
            "earnings",
            "analyst",
            "estimates",
            "historical-price",
            "insider",
            "institutional",
            "profile",
        ],
    )
    def test_no_other_fmp_endpoint_appears_anywhere_in_the_module(self, forbidden: str) -> None:
        """Scope is splits only, and the file itself is the evidence."""
        from tradeit.acquisition import fmp

        source_text = Path(fmp.__file__).read_text(encoding="utf-8")
        for line in source_text.splitlines():
            if "financialmodelingprep.com" in line or "/stable/" in line:
                assert forbidden not in line, line

    def test_no_credential_reaches_the_hint(self) -> None:
        hint = make_source().credential_hint()
        assert FMP_SECRET not in hint
        assert hint.startswith("set (")


# ---------------------------------------------------------------------------
# Normalizing the vendor's records
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_a_forward_split_becomes_a_share_count_multiplier(self) -> None:
        events, findings = normalize_splits("AAPL", [AAPL_SPLITS[0]])
        assert findings == []
        assert len(events) == 1
        event = events[0]
        assert event.ex_date == dt.date(2020, 8, 31)
        assert event.ratio == Decimal(4)
        assert event.is_reverse is False
        assert event.describe == "4-for-1"

    def test_the_vendors_own_numbers_survive_the_derived_ratio(self) -> None:
        """A ratio of 0.1 could be 1-for-10 or 2-for-20. The pair says which."""
        events, _ = normalize_splits(
            "X", [{"date": "2020-01-02", "numerator": 2, "denominator": 20}]
        )
        assert events[0].ratio == Decimal("0.1")
        assert events[0].numerator == 2
        assert events[0].denominator == 20
        assert events[0].describe == "2-for-20"

    def test_a_reverse_split_is_not_read_as_a_forward_one(self) -> None:
        events, findings = normalize_splits(
            "RVRS", [{"date": "2019-05-06", "numerator": 1, "denominator": 8}]
        )
        assert findings == []
        assert events[0].ratio == Decimal("0.125")
        assert events[0].is_reverse is True

    def test_events_come_back_in_date_order_whatever_the_vendor_sent(self) -> None:
        events, _ = normalize_splits("AAPL", AAPL_SPLITS)
        assert [e.ex_date for e in events] == sorted(e.ex_date for e in events)

    def test_an_unfamiliar_split_type_is_carried_not_dropped(self) -> None:
        """No whitelist. A label written today cannot silence tomorrow's data."""
        events, findings = normalize_splits("NVDA", NVDA_SPLITS)
        assert findings == []
        assert {e.split_type for e in events} == {"stock_split", "some_future_label"}

    def test_the_nvda_ten_for_one_normalizes_exactly(self) -> None:
        events, _ = normalize_splits("NVDA", NVDA_SPLITS)
        latest = next(e for e in events if e.ex_date == dt.date(2024, 6, 10))
        assert (latest.numerator, latest.denominator) == (10, 1)
        assert latest.ratio == Decimal(10)

    def test_an_announcement_time_is_never_invented(self) -> None:
        """An effective date is not an announcement date, and saying so by
        leaving the field empty is the only honest option available."""
        events, _ = normalize_splits("NVDA", NVDA_SPLITS)
        assert all(event.announced_at is None for event in events)

    def test_a_zero_denominator_is_refused_rather_than_coerced(self) -> None:
        events, findings = normalize_splits(
            "BAD", [{"date": "2020-01-02", "numerator": 4, "denominator": 0}]
        )
        assert events == ()
        assert any("denominator 0" in f for f in findings)

    def test_a_missing_numerator_is_refused_rather_than_assumed_to_be_one(self) -> None:
        events, findings = normalize_splits("BAD", [{"date": "2020-01-02", "denominator": 1}])
        assert events == ()
        assert any("not guessed" in f.lower() for f in findings)

    def test_a_fractional_numerator_is_refused_rather_than_truncated(self) -> None:
        events, findings = normalize_splits(
            "BAD", [{"date": "2020-01-02", "numerator": "2.5", "denominator": 1}]
        )
        assert events == ()
        assert findings

    def test_a_negative_fraction_is_refused(self) -> None:
        events, findings = normalize_splits(
            "BAD", [{"date": "2020-01-02", "numerator": -2, "denominator": 1}]
        )
        assert events == ()
        assert any("non-positive" in f for f in findings)

    def test_an_unreadable_date_is_refused(self) -> None:
        events, findings = normalize_splits(
            "BAD", [{"date": "not-a-date", "numerator": 2, "denominator": 1}]
        )
        assert events == ()
        assert any("unreadable date" in f for f in findings)

    def test_a_record_for_another_symbol_is_refused(self) -> None:
        """A split from a different security rewrites every price before it."""
        events, findings = normalize_splits(
            "AAPL", [{"symbol": "MSFT", "date": "2003-02-18", "numerator": 2, "denominator": 1}]
        )
        assert events == ()
        assert any("MSFT" in f for f in findings)

    def test_a_datetime_is_reduced_to_its_date_without_a_timezone_hop(self) -> None:
        events, _ = normalize_splits(
            "X", [{"date": "2020-08-31T00:00:00.000Z", "numerator": 4, "denominator": 1}]
        )
        assert events[0].ex_date == dt.date(2020, 8, 31)

    def test_duplicate_records_are_kept_and_reported_not_merged(self) -> None:
        """Two identical records may be one event listed twice, or two events.
        Silently collapsing them decides that question without evidence."""
        events, findings = normalize_splits("AAPL", [AAPL_SPLITS[0], AAPL_SPLITS[0]])
        assert len(events) == 2
        assert findings == []
        from tradeit.acquisition.reconstruct import check_split_consistency

        conflicts, notes = check_split_consistency("AAPL", events, None)
        assert any("identical split records" in f for f in conflicts)
        assert any("NOT merged" in f for f in conflicts)
        assert notes == []


# ---------------------------------------------------------------------------
# Classifying what came back
# ---------------------------------------------------------------------------


class TestClassification:
    def test_a_populated_response_is_available(self) -> None:
        lookup = make_source().lookup("AAPL")
        assert lookup.support is CapabilitySupport.AVAILABLE
        assert lookup.status is FetchStatus.OK
        assert len(lookup.splits) == 5
        assert lookup.is_usable is True

    def test_an_empty_response_is_evidence_of_absence_but_says_what_it_cannot_tell(
        self,
    ) -> None:
        source = make_source(FakeFmpTransport(by_symbol={}, default=[]))
        lookup = source.lookup("NOSPLITS")
        assert lookup.support is CapabilitySupport.EMPTY_VALID_RESPONSE
        assert lookup.support.is_evidence_of_absence is True
        assert lookup.status is FetchStatus.EMPTY
        assert lookup.splits == ()
        assert any("unrecognised ticker" in f for f in lookup.findings)

    def test_an_empty_response_is_never_silently_a_never_split_claim(self) -> None:
        source = make_source(FakeFmpTransport(by_symbol={}, default=[]))
        source.lookup("MADEUPTICKER")
        assert any("unrecognised ticker" in item for item in source.limitations())

    def test_a_plan_restriction_is_not_an_absence_of_splits(self) -> None:
        """The single most damaging confusion this module can make."""
        source = make_source(FakeFmpTransport(default=PLAN_ERROR, by_symbol={}))
        lookup = source.lookup("AAPL")
        assert lookup.support is CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        assert lookup.support.is_evidence_of_absence is False
        assert lookup.is_usable is False
        assert lookup.status is FetchStatus.REJECTED
        assert any("NOT about" in item for item in source.limitations())

    def test_a_bad_key_is_not_reported_as_a_plan_restriction(self) -> None:
        """A typo must not put "not available on this subscription" in a manifest."""
        source = make_source(FakeFmpTransport(default=BAD_KEY_ERROR, by_symbol={}))
        lookup = source.lookup("AAPL")
        assert lookup.support is CapabilitySupport.UNKNOWN
        assert lookup.support is not CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        assert lookup.is_usable is False

    def test_the_daily_cap_stops_the_pass_rather_than_spinning(self) -> None:
        source = make_source(FakeFmpTransport(default=DAILY_CAP_ERROR, by_symbol={}))
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.QUOTA_EXHAUSTED
        assert lookup.status.stops_the_run is True
        assert lookup.support is CapabilitySupport.UNKNOWN

    def test_a_rate_limit_is_a_delay_not_a_wall(self) -> None:
        source = make_source(
            FakeFmpTransport(raise_on={"*": ProviderRateLimitError("rate-limited, 3 attempts")})
        )
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.RATE_LIMITED
        assert lookup.status.stops_the_run is False
        assert lookup.status.is_retryable is True
        assert source.pacer.backoffs == 1

    def test_a_429_widens_the_self_imposed_gap(self) -> None:
        source = make_source(
            FakeFmpTransport(raise_on={"*": ProviderRateLimitError("slow down")}),
            requests_per_minute=60,
        )
        before = source.pacer.interval_s
        source.lookup("AAPL")
        assert source.pacer.interval_s > before

    def test_a_network_failure_says_nothing_about_splits(self) -> None:
        """No HTTP status means the vendor never saw the request."""
        source = make_source(
            FakeFmpTransport(raise_on={"*": ProviderUnreachableError("could not reach host")})
        )
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.UNREACHABLE
        assert lookup.support is CapabilitySupport.UNKNOWN
        assert lookup.is_usable is False

    def test_an_auth_failure_is_not_retried_into_a_ban(self) -> None:
        source = make_source(FakeFmpTransport(raise_on={"*": ProviderAuthError("401 rejected")}))
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.REJECTED
        assert lookup.status.is_retryable is False

    def test_malformed_json_is_not_an_absence_of_splits(self) -> None:
        source = make_source(FakeFmpTransport(body_override=b"<html>gateway timeout</html>"))
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.MALFORMED
        assert lookup.support is CapabilitySupport.PROVIDER_ERROR
        assert lookup.is_usable is False

    def test_an_unexpected_object_shape_is_not_an_absence_of_splits(self) -> None:
        source = make_source(FakeFmpTransport(default={"unexpected": "shape"}, by_symbol={}))
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.MALFORMED
        assert lookup.is_usable is False

    def test_a_proven_success_is_not_downgraded_by_a_later_empty(self) -> None:
        source = make_source(FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS}, default=[]))
        source.lookup("AAPL")
        source.lookup("NOSPLITS")
        assert source.support is CapabilitySupport.AVAILABLE

    def test_a_plan_restriction_does_override_a_prior_success(self) -> None:
        source = make_source(FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS, "X": PLAN_ERROR}))
        source.lookup("AAPL")
        source.lookup("X")
        assert source.support is CapabilitySupport.NOT_AVAILABLE_ON_PLAN

    def test_no_credential_refuses_before_issuing_a_request(self) -> None:
        transport = FakeFmpTransport()
        source = FmpSplitSource(token="", transport=transport)
        lookup = source.lookup("AAPL")
        assert lookup.status is FetchStatus.REJECTED
        assert transport.calls == []
        assert "FMP_API_KEY" in lookup.error


# ---------------------------------------------------------------------------
# Credential hygiene
# ---------------------------------------------------------------------------


class TestCredentialsNeverEscape:
    def test_the_key_is_in_the_url_and_is_always_redacted(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        transport = FakeFmpTransport()
        enrich(package, transport)
        assert any(FMP_SECRET in call for call in transport.calls), "fixture assumption"
        for path in package.rglob("*"):
            if not path.is_file():
                continue
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            assert FMP_SECRET.encode() not in blob, f"{path} contains the API key"
            assert TD_SECRET.encode() not in blob, f"{path} contains the API key"

    def test_the_journal_records_a_redacted_url(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        journal = AcquisitionJournal(package / WORKSPACE_DIRNAME / "journal.jsonl")
        fmp_entries = [e for e in journal.entries if e.provider == "fmp"]
        assert fmp_entries
        for entry in fmp_entries:
            assert FMP_SECRET not in entry.url
            assert "apikey=REDACTED" in entry.url

    def test_an_error_message_never_echoes_the_key(self) -> None:
        source = make_source(
            FakeFmpTransport(
                raise_on={"*": ProviderAuthError(f"rejected apikey={FMP_SECRET} outright")}
            )
        )
        lookup = source.lookup("AAPL")
        assert FMP_SECRET not in lookup.error
        assert "REDACTED" in lookup.error

    def test_the_key_never_reaches_the_manifest(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        text = (package / "manifest.toml").read_text(encoding="utf-8")
        assert FMP_SECRET not in text
        assert TD_SECRET not in text


# ---------------------------------------------------------------------------
# Enrichment over a real package
# ---------------------------------------------------------------------------


class TestEnrichment:
    def test_a_split_adjusted_package_gains_a_reconstruction(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        before = load_manifest(package / "manifest.toml")
        assert before.provenance is not None
        assert before.provenance.reconstruction_performed is False

        report = enrich(package)
        assert report.status is EnrichmentStatus.ENRICHED
        assert report.price_provider == "twelve_data"
        assert report.split_provider == "fmp"
        assert report.reconstruction
        assert report.reconstruction[0].quality is ReconstructionQuality.APPLIED

    def test_the_splits_dataset_keeps_the_vendors_numbers(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / "splits.csv")
        assert len(rows) == 5
        row = next(r for r in rows if r["ex_date"] == "2020-08-31")
        assert row["ratio"] == "4"
        assert row["numerator"] == "4"
        assert row["denominator"] == "1"
        assert row["source_provider"] == "fmp"

    def test_every_split_column_is_in_the_dataset_contract(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["NVDA"])
        enrich(package, FakeFmpTransport(by_symbol={"NVDA": NVDA_SPLITS}))
        known = {c.name for c in DATASET_SPECS[DatasetKind.SPLITS].columns}
        rows = read_csv(package / "splits.csv")
        assert set(rows[0]) <= known

    def test_no_announcement_time_column_is_written(self, tmp_path: Path) -> None:
        """The contract has the column. This source cannot fill it, and an
        effective date presented as an announcement would license research the
        data does not support."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / "splits.csv")
        assert "announcement_time" not in rows[0]

    def test_the_package_still_verifies_after_enrichment(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        report = enrich(package)
        assert report.problems == []
        manifest = load_manifest(package / "manifest.toml")
        from tradeit.data.packages.manifest import verify_files

        assert verify_files(manifest, package) == []

    def test_nothing_is_written_when_the_source_answers_for_nobody(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        before = (package / "manifest.toml").read_bytes()
        report = enrich(package, FakeFmpTransport(default=PLAN_ERROR, by_symbol={}))
        assert report.status is EnrichmentStatus.NOT_ENRICHED
        assert report.splits_written == 0
        assert (package / "manifest.toml").read_bytes() == before
        assert any("NOT about whether" in note for note in report.notes)

    def test_a_partial_answer_names_the_symbols_it_could_not_reach(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL", "NVDA"])
        report = enrich(
            package,
            FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS, "NVDA": PLAN_ERROR}),
        )
        assert report.status is EnrichmentStatus.PARTIAL
        assert [s.symbol for s in report.unenriched] == ["NVDA"]
        manifest = load_manifest(package / "manifest.toml")
        assert any(
            "NVDA" in item and "not a claim that they never split" in item
            for item in manifest.known_limitations
        )

    def test_a_missing_credential_leaves_the_package_untouched(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        package = build_package(tmp_path / "pkg", ["AAPL"])
        before = (package / "manifest.toml").read_bytes()
        report = PackageEnricher(package, FmpSplitSource(transport=FakeFmpTransport())).run()
        assert report.status is EnrichmentStatus.FAILED
        assert any("FMP_API_KEY" in p for p in report.problems)
        assert (package / "manifest.toml").read_bytes() == before

    def test_a_package_without_symbol_mappings_is_refused_not_guessed(self, tmp_path: Path) -> None:
        """Re-deriving instrument ids would attach one company's splits to
        another company's prices."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        manifest = load_manifest(package / "manifest.toml")
        stripped = manifest.model_copy(
            update={
                "files": tuple(
                    f for f in manifest.files if f.dataset is not DatasetKind.SYMBOL_MAPPINGS
                )
            }
        )
        from tradeit.data.packages.manifest import render_manifest

        (package / "manifest.toml").write_text(render_manifest(stripped), encoding="utf-8")
        report = enrich(package)
        assert report.status is EnrichmentStatus.FAILED
        assert any("symbol_mappings" in p for p in report.problems)

    def test_only_the_named_symbols_are_asked_about(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL", "NVDA"])
        transport = FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS, "NVDA": NVDA_SPLITS})
        enrich(package, transport, symbols=("AAPL",))
        assert len(transport.calls) == 1
        assert "symbol=AAPL" in transport.calls[0]

    def test_a_daily_cap_stops_the_pass_and_keeps_what_arrived(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL", "NVDA", "SPY"])
        report = enrich(
            package,
            FakeFmpTransport(
                by_symbol={"AAPL": AAPL_SPLITS, "NVDA": DAILY_CAP_ERROR, "SPY": NVDA_SPLITS}
            ),
        )
        assert report.quota_stopped is True
        # SPY was never asked about: the run stopped at NVDA.
        assert [s.symbol for s in report.symbols] == ["AAPL", "NVDA"]
        assert report.splits_written == 5
        assert any("allowance" in note or "Limit Reach" in note for note in report.notes)

    def test_no_reconstruct_writes_splits_and_no_sidecar(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        report = enrich(package, reconstruct=False)
        assert report.splits_written == 5
        assert report.reconstruction == []
        assert not (package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz").exists()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_a_second_pass_reads_the_cache_and_asks_nothing(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        transport = FakeFmpTransport()
        report = enrich(package, transport)
        assert transport.calls == []
        assert report.cached == 1
        assert report.requests == 0
        assert report.splits_written == 5

    def test_resuming_after_a_partial_pass_only_asks_for_what_is_missing(
        self, tmp_path: Path
    ) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL", "NVDA"])
        first = enrich(
            package,
            FakeFmpTransport(
                by_symbol={"AAPL": AAPL_SPLITS, "NVDA": DAILY_CAP_ERROR},
            ),
        )
        assert first.quota_stopped is True
        assert first.splits_written == 5

        transport = FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS, "NVDA": NVDA_SPLITS})
        second = enrich(package, transport)
        assert [c for c in transport.calls if "AAPL" in c] == []
        assert len(transport.calls) == 1 and "NVDA" in transport.calls[0]
        assert second.status is EnrichmentStatus.ENRICHED
        assert second.splits_written == 8

    def test_force_refresh_re_asks(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        transport = FakeFmpTransport()
        enrich(package, transport, force_refresh=True)
        assert len(transport.calls) == 1

    def test_a_cached_body_is_replayed_through_the_same_interpretation(self) -> None:
        source = make_source()
        live = source.lookup("AAPL")
        replayed = source.replay("AAPL", live.raw)
        assert replayed.splits == live.splits
        assert replayed.status is FetchStatus.CACHED
        assert replayed.status.is_success is True


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


class TestReconstructionArithmetic:
    def test_splits_before_the_package_window_change_nothing(self, tmp_path: Path) -> None:
        """A 2005 split must not be applied to prices that begin in 2010.

        The comparison is strictly-after, and this is the test that says so:
        Apple's 1987, 2000 and 2005 splits all precede every session in this
        package and must contribute nothing, while 2014 and 2020 both follow
        every session and must contribute 7 * 4 = 28.
        """
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        assert rows
        for row in rows:
            applied = row["splits_applied"].split(";")
            assert applied == ["2014-06-09", "2020-08-31"]
            assert "2005-02-28" not in row["splits_applied"]
            assert "2000-06-21" not in row["splits_applied"]
            assert "1987-06-16" not in row["splits_applied"]
            assert Decimal(row["factor"]) == Decimal(28)

    def test_out_of_coverage_splits_are_reported_rather_than_hidden(self, tmp_path: Path) -> None:
        """Five splits and only two affecting anything is otherwise a puzzle."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        report = enrich(package)
        assert any("1987-06-16" in item and "affects no row" in item for item in report.findings)
        # A note, not a conflict: nothing is wrong and nobody has to act.
        assert not any("affects no row" in item for item in report.conflicts)

    def test_prices_are_multiplied_and_volume_divided(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        row = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")[0]
        assert Decimal(row["vendor_close"]) == Decimal("108")
        assert Decimal(row["reconstructed_close"]) == Decimal("108") * 28
        assert Decimal(row["reconstructed_volume"]) == Decimal(row["vendor_volume"]) / 28

    def test_dollar_volume_is_invariant_under_the_reconstruction(self, tmp_path: Path) -> None:
        """Price and volume move in opposite directions by the same factor, so
        the money that changed hands is unchanged. The fixture's volumes are
        chosen to divide exactly, so this is equality rather than 'close'."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        assert rows
        for row in rows:
            vendor = Decimal(row["vendor_close"]) * Decimal(row["vendor_volume"])
            derived = Decimal(row["reconstructed_close"]) * Decimal(row["reconstructed_volume"])
            assert derived == vendor, row["session_date"]

    def test_dollar_volume_survives_a_reverse_split_too(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(
            package,
            FakeFmpTransport(by_symbol={"AAPL": [REVERSE_SPLIT]}),
        )
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        assert rows
        for row in rows:
            assert Decimal(row["factor"]) == Decimal("0.125")
            vendor = Decimal(row["vendor_close"]) * Decimal(row["vendor_volume"])
            derived = Decimal(row["reconstructed_close"]) * Decimal(row["reconstructed_volume"])
            assert derived == vendor, row["session_date"]

    def test_a_reverse_split_lowers_the_reconstructed_price(self, tmp_path: Path) -> None:
        """Direction, stated as an inequality so an inverted ratio is loud."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(
            package,
            FakeFmpTransport(by_symbol={"AAPL": [REVERSE_SPLIT]}),
        )
        row = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")[0]
        assert Decimal(row["reconstructed_close"]) < Decimal(row["vendor_close"])
        assert Decimal(row["reconstructed_volume"]) > Decimal(row["vendor_volume"])

    def test_multiple_splits_compound(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["NVDA"])
        enrich(package, FakeFmpTransport(by_symbol={"NVDA": NVDA_SPLITS}))
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        # 2007 precedes the window; 2021 and 2024 follow all of it.
        assert Decimal(rows[0]["factor"]) == Decimal(40)

    def test_no_splits_reported_means_no_sidecar_and_a_stated_caveat(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        report = enrich(package, FakeFmpTransport(by_symbol={}, default=[]))
        assert report.reconstruction[0].quality is ReconstructionQuality.NO_SPLITS_REPORTED
        assert "only as good as the vendor's split record" in report.reconstruction[0].summary()
        assert not (package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz").exists()

    def test_high_factors_are_flagged_because_rounding_is_magnified(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        assert all(row["rounding_magnified"] == "true" for row in rows)


# ---------------------------------------------------------------------------
# Cross-vendor labelling and provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_every_reconstructed_row_names_both_vendors(self, tmp_path: Path) -> None:
        """The value came from neither of them. One field would let a reader
        attribute one vendor's numbers to the other's record."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        for row in rows:
            assert row["price_provider"] == "twelve_data"
            assert row["split_provider"] == "fmp"

    def test_every_reconstructed_row_carries_the_derived_label(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        rows = read_csv(package / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz")
        for row in rows:
            assert row["label"] == RECONSTRUCTION_LABEL
            assert row["label"] == "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"
            assert row["algorithm"] == RECONSTRUCTION_ALGORITHM_VERSION

    @pytest.mark.parametrize("forbidden", ["RAW_VENDOR_PRICE", "raw_vendor_price"])
    def test_the_word_the_label_must_never_be(self, tmp_path: Path, forbidden: str) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        for path in package.rglob("*"):
            if not path.is_file():
                continue
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            assert forbidden.encode() not in blob, path

    def test_the_reconstruction_is_never_a_declared_package_file(self, tmp_path: Path) -> None:
        """It is DERIVED. The importer must not read it as prices."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        manifest = load_manifest(package / "manifest.toml")
        assert all("reconstructed" not in file.path for file in manifest.files)
        assert manifest.provenance is not None
        assert manifest.provenance.reconstruction_file.startswith(WORKSPACE_DIRNAME)

    def test_the_manifest_names_each_vendors_contribution(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        manifest = load_manifest(package / "manifest.toml")
        provenance = manifest.provenance
        assert provenance is not None
        assert provenance.price_provider == "twelve_data"
        assert provenance.price_representation == "split_adjusted"
        assert provenance.split_provider == "fmp"
        assert provenance.dividend_provider == "twelve_data"
        assert provenance.reconstruction_performed is True
        assert provenance.reconstruction_algorithm == RECONSTRUCTION_ALGORITHM_VERSION
        assert provenance.reconstruction_label == RECONSTRUCTION_LABEL

    def test_the_top_level_provider_still_names_the_price_source(self, tmp_path: Path) -> None:
        """Enrichment does not rebrand the package. Its prices are still Twelve
        Data's, and the snapshot id must not start pointing somewhere else."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        manifest = load_manifest(package / "manifest.toml")
        assert manifest.provider == "twelve_data"

    def test_the_splits_file_note_says_who_supplied_it(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        manifest = load_manifest(package / "manifest.toml")
        entry = manifest.files_for(DatasetKind.SPLITS)[0]
        assert "fmp" in entry.note
        assert "twelve_data" in entry.note

    def test_the_stale_not_attempted_limitation_is_replaced(self, tmp_path: Path) -> None:
        """A manifest saying both "splits are not on this plan" and
        "reconstruction used FMP records" would let a reader believe either."""
        package = build_package(tmp_path / "pkg", ["AAPL"])
        before = load_manifest(package / "manifest.toml").known_limitations
        assert any("not available on this subscription" in item for item in before)

        enrich(package)
        after = load_manifest(package / "manifest.toml").known_limitations
        assert not any(
            "the splits endpoint is not available on this subscription" in item for item in after
        )
        assert any("raw reconstruction performed using fmp" in item.lower() for item in after)
        assert any(
            "completeness of the raw reconstruction depends on fmp" in item.lower()
            for item in after
        )

    def test_the_manifest_keeps_the_announcement_time_caveat(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        limitations = load_manifest(package / "manifest.toml").known_limitations
        assert any("EFFECTIVE (ex-) date" in item for item in limitations)
        assert any("announcement-time" in item for item in limitations)

    def test_the_manifest_records_the_enrichment_tool_version(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        limitations = load_manifest(package / "manifest.toml").known_limitations
        assert any(ENRICHMENT_TOOL_VERSION in item for item in limitations)

    def test_the_provenance_table_round_trips_through_toml(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        raw = tomllib.loads((package / "manifest.toml").read_text(encoding="utf-8"))
        assert raw["provenance"]["split_provider"] == "fmp"
        # The classic TOML nesting bug: a top-level key emitted after a table
        # header silently becomes a member of it.
        assert "known_limitations" in raw
        assert "known_limitations" not in raw["coverage"]
        assert "known_limitations" not in raw["provenance"]


# ---------------------------------------------------------------------------
# Two vendors disagreeing
# ---------------------------------------------------------------------------


class TestConflicts:
    def test_agreement_produces_no_finding(self) -> None:
        shared = (SplitEvent(ex_date=dt.date(2014, 6, 9), ratio=Decimal(7), source="twelve_data"),)
        assert compare_schedules("AAPL", shared, shared, "fmp") == []

    def test_a_ratio_disagreement_is_reported_not_reconciled(self) -> None:
        primary = (SplitEvent(ex_date=dt.date(2014, 6, 9), ratio=Decimal(7), source="twelve_data"),)
        secondary = (SplitEvent(ex_date=dt.date(2014, 6, 9), ratio=Decimal(2), source="fmp"),)
        findings = compare_schedules("AAPL", primary, secondary, "fmp")
        assert len(findings) == 1
        assert "DISAGREE" in findings[0]
        assert "Not reconciled" in findings[0]

    def test_an_event_only_the_primary_has_is_reported_as_not_applied(self) -> None:
        primary = (SplitEvent(ex_date=dt.date(1999, 1, 4), ratio=Decimal(2), source="twelve_data"),)
        findings = compare_schedules("AAPL", primary, (), "fmp")
        assert "NOT applied" in findings[0]

    def test_an_event_only_the_secondary_has_is_reported(self) -> None:
        secondary = (SplitEvent(ex_date=dt.date(2020, 8, 31), ratio=Decimal(4), source="fmp"),)
        findings = compare_schedules("AAPL", (), secondary, "fmp")
        assert "fmp records a split" in findings[0]

    def test_a_disagreement_over_a_real_package_reaches_the_manifest(self, tmp_path: Path) -> None:
        # Twelve Data's fixture reports the 2014 7-for-1; FMP's reports it too,
        # plus four more. The 2014 record agrees, so the findings are about the
        # events only one source has.
        package = build_package(tmp_path / "pkg", ["AAPL"], splits_available=True)
        report = enrich(package)
        assert any("does not" in item for item in report.conflicts)
        limitations = load_manifest(package / "manifest.toml").known_limitations
        assert any("NOT resolved automatically" in item for item in limitations)

    def test_the_secondary_schedule_is_the_one_reconstruction_uses(self, tmp_path: Path) -> None:
        """Named on the command line, so used — and every difference reported."""
        package = build_package(tmp_path / "pkg", ["AAPL"], splits_available=True)
        enrich(package)
        rows = read_csv(package / "splits.csv")
        assert {row["source_provider"] for row in rows} == {"fmp"}
        assert len(rows) == 5


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


class TestPacing:
    def test_the_default_rate_is_documented_as_self_imposed(self) -> None:
        text = RequestPacer(requests_per_minute=DEFAULT_REQUESTS_PER_MINUTE).describe()
        assert "self-imposed" in text
        assert "not a limit quoted" in text

    def test_the_rate_is_configurable(self) -> None:
        assert RequestPacer(requests_per_minute=60).interval_s == pytest.approx(1.0)
        assert RequestPacer(requests_per_minute=6).interval_s == pytest.approx(10.0)

    def test_the_pacing_note_reaches_the_manifest(self, tmp_path: Path) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        enrich(package)
        limitations = load_manifest(package / "manifest.toml").known_limitations
        assert any("self-imposed pacing" in item for item in limitations)

    def test_backoff_is_capped_so_it_cannot_become_a_hang(self) -> None:
        pacer = RequestPacer(requests_per_minute=1)
        for _ in range(20):
            pacer.note_rate_limited()
        assert pacer.interval_s <= 30.0

    def test_the_first_request_does_not_wait(self) -> None:
        pacer = RequestPacer(requests_per_minute=1)
        assert pacer.wait_for_slot() == 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestSourceRegistry:
    def test_an_unknown_source_names_the_alternatives(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            get_source_class("nope")
        assert "fmp" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The operator-facing commands
# ---------------------------------------------------------------------------


class _CliFmp(FmpSplitSource):
    """The real adapter with a recorded transport, for the CLI tests."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            token=FMP_SECRET,
            transport=FakeFmpTransport(by_symbol={"AAPL": AAPL_SPLITS, "NVDA": NVDA_SPLITS}),
            requests_per_minute=600_000,
        )


class _CliTwelveData(TwelveDataAcquisition):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            token=TD_SECRET,
            transport=FakeTwelveDataTransport(),
            batch_size=4,
            credits_per_minute=100_000,
        )


class TestCommands:
    """``tradeit data enrich`` is the primitive; ``--split-provider`` is sugar.

    The design choice, since it is not obvious: enrichment is a pass over an
    *existing package directory*, and the ``acquire`` flag runs exactly that
    pass immediately afterwards. It is not a second implementation living inside
    acquisition. Price acquisition on a free plan spans days — the daily credit
    allowance runs out, the run stops cleanly, the operator resumes tomorrow —
    so a package sits usable-but-incomplete for a long time, and the split
    schedule for the symbols already downloaded has to be obtainable without
    re-downloading a single bar.
    """

    def _args(self, **kwargs: Any) -> argparse.Namespace:
        return argparse.Namespace(**kwargs)

    def test_enrich_runs_over_an_existing_package(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        monkeypatch.setattr(cli_data, "get_source_class", lambda name: _CliFmp)
        code = cli_data.cmd_enrich(
            self._args(
                package=str(package),
                source="fmp",
                symbols=None,
                split_rate_limit=None,
                force_refresh=False,
                no_reconstruct=False,
            )
        )
        assert code == 0
        assert "ENRICHED" in capsys.readouterr().out
        assert load_manifest(package / "manifest.toml").provenance.split_provider == "fmp"

    def test_enrich_writes_a_machine_readable_report(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        package = build_package(tmp_path / "pkg", ["AAPL"])
        monkeypatch.setattr(cli_data, "get_source_class", lambda name: _CliFmp)
        cli_data.cmd_enrich(
            self._args(
                package=str(package),
                source="fmp",
                symbols=None,
                split_rate_limit=None,
                force_refresh=False,
                no_reconstruct=False,
            )
        )
        capsys.readouterr()
        payload = json.loads(
            (package / WORKSPACE_DIRNAME / "enrichment_report.json").read_text(encoding="utf-8")
        )
        assert payload["split_provider"] == "fmp"
        assert payload["price_provider"] == "twelve_data"
        assert payload["reconstruction"][0]["label"] == RECONSTRUCTION_LABEL
        assert FMP_SECRET not in json.dumps(payload)

    def test_acquire_with_split_provider_runs_the_same_pass(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        monkeypatch.setattr(cli_data, "get_provider_class", lambda name: _CliTwelveData)
        monkeypatch.setattr(cli_data, "get_source_class", lambda name: _CliFmp)
        output = tmp_path / "pkg"
        code = cli_data.cmd_acquire(
            self._args(
                provider="twelve_data",
                symbols=["AAPL"],
                universe="validation",
                start="2010-01-01",
                end="2011-03-31",
                output=str(output),
                name=None,
                force_refresh=False,
                retry_failed=False,
                rate_limit=None,
                batch_size=None,
                estimate_only=False,
                split_provider="fmp",
                split_rate_limit=None,
            )
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "ACQUISITION SUMMARY" in out
        assert "ENRICHMENT SUMMARY" in out
        manifest = load_manifest(output / "manifest.toml")
        assert manifest.provider == "twelve_data"
        assert manifest.provenance is not None
        assert manifest.provenance.split_provider == "fmp"

    def test_acquire_without_split_provider_leaves_the_package_alone(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        monkeypatch.setattr(cli_data, "get_provider_class", lambda name: _CliTwelveData)
        output = tmp_path / "pkg"
        code = cli_data.cmd_acquire(
            self._args(
                provider="twelve_data",
                symbols=["AAPL"],
                universe="validation",
                start="2010-01-01",
                end="2011-03-31",
                output=str(output),
                name=None,
                force_refresh=False,
                retry_failed=False,
                rate_limit=None,
                batch_size=None,
                estimate_only=False,
                split_provider=None,
                split_rate_limit=None,
            )
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "ENRICHMENT SUMMARY" not in out
        assert not (output / "splits.csv").exists()

    def test_the_parser_offers_the_source_by_name(self) -> None:
        parser = argparse.ArgumentParser()
        cli_data.add_data_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["data", "enrich", "./pkg", "--source", "fmp"])
        assert args.source == "fmp"
        assert args.func is cli_data.cmd_enrich

    def test_fmp_is_refused_as_a_price_provider_by_the_registry(self) -> None:
        with pytest.raises(ConfigError):
            cli_data.get_provider_class("fmp")

    def test_providers_lists_sources_separately(self, capsys: Any) -> None:
        cli_data.cmd_providers(argparse.Namespace())
        out = capsys.readouterr().out
        assert "Price providers" in out
        assert "Corporate-action sources" in out
        assert "fmp" in out
        assert "cannot be used as --provider" in out
