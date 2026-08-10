"""Twelve Data, exercised against recorded response shapes.

**No real network call happened inside this suite, and none is claimed.** The
project owner verified the live API from their own machine; everything here runs
against deterministic fixtures shaped like the vendor's documented responses,
which is how it should be tested regardless — a suite that hit the vendor would
burn credits on every CI run and fail during their outages.

Three groups matter more than the rest:

* **The split-adjusted declaration.** Labelling these prices raw would put a
  history nobody could have seen into the package, and the resulting series is
  indistinguishable from a real one by inspection. Several tests exist purely to
  make that regression loud.
* **Credits versus requests.** Batching cuts round trips and not quota. A test
  asserts the two numbers diverge, because code that conflated them would pass
  every other test here.
* **Plan restriction versus absence.** "Your plan does not include splits" and
  "this stock never split" arrive looking alike and mean opposite things.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tradeit.acquisition.base import (
    AcquisitionDataset,
    CapabilitySupport,
    CreditUsage,
    FetchRequest,
    FetchStatus,
    SymbolStatus,
    available_providers,
)
from tradeit.acquisition.credits import CreditLedger
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.reconstruct import (
    RECONSTRUCTION_LABEL,
    ReconstructionQuality,
    SplitEvent,
    cumulative_factor,
    reconstruct_symbol,
)
from tradeit.acquisition.redaction import credential_hint, redact_text, redact_url
from tradeit.acquisition.runner import (
    AcquisitionOptions,
    AcquisitionRunner,
    PackageStatus,
)
from tradeit.acquisition.twelvedata import (
    DEFAULT_BATCH_SIZE,
    END_DATE_PROBE_DAYS,
    ENDPOINTS,
    MAX_OUTPUTSIZE,
    TwelveDataAcquisition,
    read_credit_headers,
)
from tradeit.core.calendar import get_calendar
from tradeit.data.packages.database import DatabaseSink
from tradeit.data.packages.importer import ImportOptions, PackageImporter
from tradeit.data.packages.manifest import WORKSPACE_DIRNAME, load_manifest
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.data.providers.http import ProviderUnreachableError

SECRET = "TWELVEDATASECRET99887766"
START = dt.date(2010, 1, 1)
END = dt.date(2011, 3, 31)


# ---------------------------------------------------------------------------
# Fixtures shaped like the vendor's documented responses
# ---------------------------------------------------------------------------


def values(count: int = 120, *, split_at: int | None = 60) -> list[dict[str, str]]:
    """Daily rows in Twelve Data's shape: string values, `datetime` key."""
    rows: list[dict[str, str]] = []
    day = dt.date(2010, 1, 4)
    price = 200.0
    written = 0
    while written < count:
        if day.weekday() < 5:
            price *= 1.002
            if written == split_at:
                price /= 2
            rows.append(
                {
                    "datetime": day.isoformat(),
                    "open": f"{price * 0.99:.2f}",
                    "high": f"{price * 1.01:.2f}",
                    "low": f"{price * 0.98:.2f}",
                    "close": f"{price:.2f}",
                    "volume": str(1_000_000 + written),
                }
            )
            written += 1
        day += dt.timedelta(days=1)
    return rows


def series_block(symbol: str, rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "meta": {
            "symbol": symbol,
            "interval": "1day",
            "currency": "USD",
            "exchange": "NASDAQ",
            "type": "Common Stock",
            "exchange_timezone": "America/New_York",
        },
        "values": values() if rows is None else rows,
        "status": "ok",
    }


SPLIT_PAYLOAD = {"splits": [{"date": "2010-06-24", "from_factor": 1, "to_factor": 2}]}
DIVIDEND_PAYLOAD = {"dividends": [{"ex_date": "2010-03-15", "amount": "0.55"}]}


class FakeTransport:
    """Answers like Twelve Data, including its 200-with-an-error-body habit."""

    def __init__(
        self,
        *,
        rows: list[dict[str, str]] | None = None,
        symbol_errors: dict[str, dict[str, Any]] | None = None,
        splits: dict[str, Any] | None = SPLIT_PAYLOAD,
        dividends: dict[str, Any] | None = DIVIDEND_PAYLOAD,
        raise_on: dict[str, Exception] | None = None,
        body_override: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.rows = rows
        self.symbol_errors = symbol_errors or {}
        self.splits = splits
        self.dividends = dividends
        self.raise_on = raise_on or {}
        self.body_override = body_override
        self.headers = headers or {}
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        for fragment, error in self.raise_on.items():
            if fragment in url:
                raise error
        if self.body_override is not None:
            return self.body_override

        symbols = url.split("symbol=")[1].split("&")[0].split(",")
        if "/time_series" in url:
            if len(symbols) == 1:
                symbol = symbols[0]
                if symbol in self.symbol_errors:
                    return json.dumps(self.symbol_errors[symbol]).encode()
                return json.dumps(series_block(symbol, self.rows)).encode()
            payload = {s: self.symbol_errors.get(s, series_block(s, self.rows)) for s in symbols}
            return json.dumps(payload).encode()
        if "/splits" in url:
            if self.splits is None:
                return json.dumps(
                    {
                        "code": 403,
                        "message": "/splits is available with the Grow plan and above",
                        "status": "error",
                    }
                ).encode()
            return json.dumps(self.splits).encode()
        if "/dividends" in url:
            if self.dividends is None:
                return json.dumps(
                    {
                        "code": 403,
                        "message": "/dividends requires a subscription upgrade",
                        "status": "error",
                    }
                ).encode()
            return json.dumps(self.dividends).encode()
        return b"{}"

    @property
    def price_calls(self) -> list[str]:
        return [c for c in self.calls if "/time_series" in c]


class _CalendarTransport:
    """Serves one bar per real trading session, with a chosen end_date semantic.

    Two subclasses differ in one comparison operator, which is the whole
    question the 2025-12-31 investigation turned on.
    """

    inclusive: bool

    def __init__(self, *, last_session: dt.date) -> None:
        self.last_session = last_session
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        symbol = url.split("symbol=")[1].split("&")[0]
        if "/time_series" not in url:
            # The free plan this project runs on refuses the corporate-action
            # endpoints. Answering them with a price payload would feed every
            # bar into the split normalizer and bury the report in findings,
            # which is a fixture bug that once looked like a product one.
            return json.dumps(
                {
                    "code": 403,
                    "message": "/splits is available with the Grow plan and above",
                    "status": "error",
                }
            ).encode()
        start = dt.date.fromisoformat(url.split("start_date=")[1].split("&")[0])
        asked = dt.date.fromisoformat(url.split("end_date=")[1].split("&")[0])
        calendar = get_calendar()
        rows = []
        cursor = start
        while cursor <= min(
            asked if self.inclusive else asked - dt.timedelta(days=1), self.last_session
        ):
            if calendar.is_session(cursor):
                rows.append(
                    {
                        "datetime": cursor.isoformat(),
                        "open": "100.00",
                        "high": "101.00",
                        "low": "99.00",
                        "close": "100.50",
                        "volume": "1000000",
                    }
                )
            cursor += dt.timedelta(days=1)
        return json.dumps(
            {
                "meta": {"symbol": symbol, "exchange": "NASDAQ", "type": "Common Stock"},
                "values": rows,
                "status": "ok",
            }
        ).encode()


class ExclusiveEndDateTransport(_CalendarTransport):
    """``end_date`` excludes its own day. What the live run behaved like."""

    inclusive = False


class InclusiveEndDateTransport(_CalendarTransport):
    """``end_date`` includes its own day. What the docs may or may not say."""

    inclusive = True


def _sessions_written(output: Path) -> list[dt.date]:
    """Session dates actually in the package, read back off disk."""
    path = output / "daily_bars.csv.gz"
    if not path.exists():
        return []
    text = gzip.decompress(path.read_bytes()).decode()
    return sorted(
        dt.date.fromisoformat(row["session_date"]) for row in csv.DictReader(text.splitlines())
    )


def make_provider(transport: FakeTransport | None = None, **kwargs: Any) -> TwelveDataAcquisition:
    kwargs.setdefault("batch_size", 4)
    # A large allowance so the credit ledger never actually sleeps in tests.
    kwargs.setdefault("credits_per_minute", 100_000)
    return TwelveDataAcquisition(token=SECRET, transport=transport or FakeTransport(), **kwargs)


def run_acquisition(
    output: Path,
    symbols: list[str],
    transport: FakeTransport | None = None,
    provider: TwelveDataAcquisition | None = None,
    **kwargs: Any,
) -> Any:
    provider = provider or make_provider(transport)
    options = AcquisitionOptions(start=START, end=END, max_wait_s=0, **kwargs)
    return AcquisitionRunner(provider, symbols, output, options).run()


# ---------------------------------------------------------------------------
# Registration and credentials
# ---------------------------------------------------------------------------


class TestRegistrationAndCredentials:
    def test_twelve_data_is_registered_alongside_tiingo(self) -> None:
        assert available_providers() == ["eodhd", "tiingo", "twelve_data"]

    def test_the_env_var_is_the_one_the_docs_promise(self) -> None:
        assert TwelveDataAcquisition.credential_env == "TWELVE_DATA_API_KEY"

    def test_readiness_follows_the_environment(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        assert TwelveDataAcquisition().has_credential() is False
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "abc")
        assert TwelveDataAcquisition().has_credential() is True

    def test_the_key_is_in_the_url_and_is_always_redacted(self, tmp_path: Path) -> None:
        """Twelve Data authenticates by query parameter, so this is the
        load-bearing case rather than a defensive one."""
        transport = FakeTransport()
        run_acquisition(tmp_path / "pkg", ["SPY"], transport)
        assert transport.calls
        assert any(SECRET in call for call in transport.calls), "fixture assumption"
        for path in (tmp_path / "pkg").rglob("*"):
            if not path.is_file():
                continue
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            assert SECRET.encode() not in blob, f"{path} contains the API key"

    def test_the_journal_records_a_redacted_url(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        assert journal.entries
        for entry in journal.entries:
            assert SECRET not in entry.url
            assert "apikey=REDACTED" in entry.url or not entry.url

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.twelvedata.com/time_series?symbol=SPY&apikey=SEKRIT",
            "https://x/y?api_key=SEKRIT&z=1",
            "https://x/y?token=SEKRIT",
        ],
    )
    def test_every_credential_parameter_spelling_is_redacted(self, url: str) -> None:
        assert "SEKRIT" not in redact_url(url)

    def test_redaction_also_scrubs_a_key_echoed_in_a_body(self) -> None:
        # A URL-shaped regex cannot catch a vendor that repeats the key inside
        # a JSON error message; the literal match does.
        assert "SEKRIT" not in redact_text('{"message": "bad key SEKRIT"}', "SEKRIT")

    def test_the_hint_reveals_almost_nothing(self) -> None:
        assert SECRET not in credential_hint(SECRET)
        assert credential_hint("") == "not set"

    def test_no_key_means_no_request(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        provider = TwelveDataAcquisition(token="", transport=transport)
        report = AcquisitionRunner(
            provider, ["SPY"], tmp_path / "pkg", AcquisitionOptions(start=START, end=END)
        ).run()
        assert not transport.calls
        assert report.status is PackageStatus.INVALID
        assert any("TWELVE_DATA_API_KEY" in p for p in report.problems)


# ---------------------------------------------------------------------------
# The split-adjustment declaration
# ---------------------------------------------------------------------------


class TestAdjustmentRepresentation:
    def test_the_provider_declares_split_adjusted(self) -> None:
        assert make_provider().adjustment_policy() is AdjustmentPolicyDeclaration.SPLIT_ADJUSTED

    def test_the_manifest_says_split_adjusted_not_raw(self, tmp_path: Path) -> None:
        """The regression that would be invisible afterwards.

        A package whose prices are split-adjusted but declared raw produces a
        price history nobody could have seen, and it looks perfectly plausible.
        """
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        assert manifest.adjustment_policy is AdjustmentPolicyDeclaration.SPLIT_ADJUSTED
        assert manifest.adjustment_policy is not AdjustmentPolicyDeclaration.RAW_UNADJUSTED

    def test_the_limitation_is_stated_first_and_in_capitals(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        report = run_acquisition(output, ["SPY"])
        assert "SPLIT-ADJUSTED" in report.limitations[0]
        manifest = load_manifest(output / "manifest.toml")
        assert any("SPLIT-ADJUSTED" in item for item in manifest.known_limitations)

    def test_the_validation_harness_warns_rather_than_passing_silently(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        from tradeit.validation.checks import CheckStatus
        from tradeit.validation.context import load_context
        from tradeit.validation.data_checks import AdjustmentDeclared

        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        sink = DatabaseSink(session=db_session, manifest=manifest, source_path=output)
        report = PackageImporter(manifest, output, options=ImportOptions(), sink=sink).run()
        package = sink.finalise(report)
        db_session.commit()

        context = load_context(db_session, package.snapshot_id)
        result = AdjustmentDeclared().run(context)
        assert result.status is CheckStatus.WARN
        assert "corporate-action checks" in result.summary

    def test_tiingo_still_declares_raw(self) -> None:
        # Adding a split-adjusted vendor must not change what the raw one says.
        from tradeit.acquisition.tiingo import TiingoAcquisition

        assert (
            TiingoAcquisition(token="x").adjustment_policy()
            is AdjustmentPolicyDeclaration.RAW_UNADJUSTED
        )


# ---------------------------------------------------------------------------
# Raw reconstruction
# ---------------------------------------------------------------------------


class TestReconstruction:
    def test_the_factor_excludes_a_split_on_the_session_itself(self) -> None:
        # An ex-date is the first session trading on the new basis, so that
        # session's print is already post-split.
        splits = [SplitEvent(dt.date(2020, 6, 1), Decimal(2))]
        assert cumulative_factor(dt.date(2020, 6, 1), splits)[0] == 1
        assert cumulative_factor(dt.date(2020, 5, 29), splits)[0] == 2

    def test_multiple_splits_compound(self) -> None:
        splits = [
            SplitEvent(dt.date(2015, 1, 1), Decimal(2)),
            SplitEvent(dt.date(2020, 1, 1), Decimal(4)),
        ]
        assert cumulative_factor(dt.date(2010, 1, 1), splits)[0] == 8
        assert cumulative_factor(dt.date(2017, 1, 1), splits)[0] == 4

    def test_a_reverse_split_scales_the_other_way(self) -> None:
        splits = [SplitEvent(dt.date(2020, 1, 1), Decimal("0.1"))]
        factor, _ = cumulative_factor(dt.date(2019, 1, 1), splits)
        assert factor == Decimal("0.1")

    def test_prices_multiply_and_volume_divides(self) -> None:
        bars = [
            {
                "instrument_id": "1",
                "session_date": "2019-01-02",
                "open": "50",
                "high": "51",
                "low": "49",
                "close": "50",
                "volume": "2000",
            }
        ]
        result = reconstruct_symbol(
            "X", bars, [SplitEvent(dt.date(2020, 1, 1), Decimal(2))], splits_available=True
        )
        assert result.quality is ReconstructionQuality.APPLIED
        row = result.rows[0]
        assert row["reconstructed_close"] == "100"
        assert row["reconstructed_volume"] == "1000"
        assert row["vendor_close"] == "50"

    def test_every_reconstructed_row_carries_its_provenance(self) -> None:
        bars = [
            {
                "instrument_id": "1",
                "session_date": "2019-01-02",
                "open": "50",
                "high": "51",
                "low": "49",
                "close": "50",
                "volume": "2000",
            }
        ]
        result = reconstruct_symbol(
            "X", bars, [SplitEvent(dt.date(2020, 1, 1), Decimal(2))], splits_available=True
        )
        row = result.rows[0]
        assert row["label"] == RECONSTRUCTION_LABEL
        assert row["algorithm"]
        assert row["factor"] == "2"
        assert row["splits_applied"] == "2020-01-01"

    def test_nothing_is_reconstructed_without_split_data(self) -> None:
        """A plan restriction must not silently produce "no splits, so raw
        equals adjusted"."""
        result = reconstruct_symbol(
            "X",
            [{"instrument_id": "1", "session_date": "2019-01-02", "close": "50"}],
            [],
            splits_available=False,
            unavailable_reason="the splits endpoint is not on this plan",
        )
        assert result.quality is ReconstructionQuality.NOT_ATTEMPTED_NO_SPLIT_DATA
        assert result.rows == []
        assert "not attempted" in result.summary()

    def test_no_splits_reported_is_distinct_from_no_split_data(self) -> None:
        result = reconstruct_symbol(
            "X",
            [{"instrument_id": "1", "session_date": "2019-01-02", "close": "50"}],
            [],
            splits_available=True,
        )
        assert result.quality is ReconstructionQuality.NO_SPLITS_REPORTED
        assert not result.quality.is_reliable

    def test_a_large_factor_is_flagged_for_rounding(self) -> None:
        bars = [
            {
                "instrument_id": "1",
                "session_date": "2019-01-02",
                "open": "5",
                "high": "5",
                "low": "5",
                "close": "5",
                "volume": "10",
            }
        ]
        result = reconstruct_symbol(
            "X", bars, [SplitEvent(dt.date(2020, 1, 1), Decimal(10))], splits_available=True
        )
        assert result.rows[0]["rounding_magnified"] == "true"
        assert result.high_factor_sessions == 1

    def test_the_sidecar_is_written_and_is_not_a_package_dataset(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        sidecar = output / WORKSPACE_DIRNAME / "reconstructed_raw_prices.csv.gz"
        assert sidecar.exists()
        text = gzip.decompress(sidecar.read_bytes()).decode()
        assert RECONSTRUCTION_LABEL in text
        manifest = load_manifest(output / "manifest.toml")
        assert sidecar.name not in {f.path for f in manifest.files}

    def test_the_package_prices_remain_the_vendors(self, tmp_path: Path) -> None:
        # Reconstruction is derived. It never becomes the package's prices.
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        bars = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        first_data_line = bars.strip().splitlines()[1]
        assert first_data_line.split(",")[0] == "1"

    def test_a_raw_price_provider_gets_no_reconstruction(self, tmp_path: Path) -> None:
        from tests.unit.test_acquisition import run_acquisition as run_tiingo

        report = run_tiingo(tmp_path / "tiingo", ["SPY"])
        assert report.reconstruction == []


# ---------------------------------------------------------------------------
# Batching, credits and quota
# ---------------------------------------------------------------------------


class TestBatchingAndCredits:
    def test_batching_reduces_requests_but_not_credits(self, tmp_path: Path) -> None:
        """The distinction the whole ledger exists for."""
        transport = FakeTransport()
        provider = make_provider(transport, batch_size=4, fetch_corporate_actions=False)
        report = run_acquisition(tmp_path / "pkg", ["A", "B", "C", "D"], provider=provider)
        assert len(transport.price_calls) == 1
        assert report.ledger.requests == 1
        assert report.ledger.credits_charged == 4

    def test_a_batch_is_one_url_with_a_comma_separated_symbol_list(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        provider = make_provider(transport, batch_size=3, fetch_corporate_actions=False)
        run_acquisition(tmp_path / "pkg", ["A", "B", "C"], provider=provider)
        assert "symbol=A,B,C" in transport.price_calls[0]

    def test_a_bare_string_of_symbols_is_refused(self) -> None:
        # Iterable, so it would silently become a request for "S", "P", "Y".
        from tradeit.errors import ConfigError

        with pytest.raises(ConfigError, match="must be a tuple"):
            FetchRequest(AcquisitionDataset.DAILY_PRICES, "SPY")  # type: ignore[arg-type]

    def test_a_batch_identity_stays_a_usable_filename(self) -> None:
        many = FetchRequest(
            AcquisitionDataset.DAILY_PRICES, tuple(f"SYM{i}" for i in range(40)), START, END
        )
        assert len(many.identity) < 80
        assert "batch40-" in many.identity

    def test_the_default_batch_size_is_defensible_and_configurable(self) -> None:
        # Not the maximum: a failed batch is redone in full, and one bad ticker
        # in a huge batch is harder to attribute.
        assert 1 < DEFAULT_BATCH_SIZE <= 16
        assert make_provider(batch_size=2).batch_size == 2

    def test_credits_are_estimated_when_the_vendor_does_not_report(self, tmp_path: Path) -> None:
        report = run_acquisition(tmp_path / "pkg", ["SPY"])
        assert report.ledger.credits_estimated > 0
        assert "estimated from the pricing model" in "\n".join(report.ledger.render())

    def test_a_reported_remaining_figure_beats_our_own_pacing(self) -> None:
        ledger = CreditLedger(per_minute_allowance=8)
        ledger.record(CreditUsage(remaining=50, charged=1))
        assert ledger.should_pause(1) == 0.0
        ledger.record(CreditUsage(remaining=2, charged=1))
        assert ledger.should_pause(1) > 0

    def test_an_unreported_remaining_figure_is_not_zero(self) -> None:
        # "We do not know how many credits are left" and "no credits are left"
        # are opposite facts.
        ledger = CreditLedger()
        assert ledger.credits_remaining is None
        assert "not reported" in "\n".join(ledger.render())

    def test_the_ledger_paces_itself_when_the_vendor_is_silent(self) -> None:
        ledger = CreditLedger(per_minute_allowance=4)
        ledger.record(CreditUsage(charged=4, estimated=True))
        assert ledger.should_pause(1) > 0

    def test_credit_headers_are_read_where_present(self) -> None:
        usage = read_credit_headers({"api-credits-used": "3", "api-credits-left": "97"})
        assert usage.used == 3
        assert usage.remaining == 97
        assert read_credit_headers({}).remaining is None

    def test_the_projection_uses_measured_cost_not_documentation(self, tmp_path: Path) -> None:
        report = run_acquisition(
            tmp_path / "pkg",
            ["SPY", "NOPE"],
            FakeTransport(
                symbol_errors={
                    "NOPE": {"code": 404, "message": "symbol not found", "status": "error"}
                }
            ),
        )
        rendered = report.render()
        assert "Measured cost" in rendered
        assert "credits per completed symbol" in rendered

    def test_no_wall_clock_estimate_is_offered(self, tmp_path: Path) -> None:
        rendered = run_acquisition(tmp_path / "pkg", ["SPY"]).render()
        for phrase in ("minutes remaining", "eta", "time remaining"):
            assert phrase not in rendered.lower()


class TestQuota:
    def _quota_transport(self, message: str) -> FakeTransport:
        return FakeTransport(
            body_override=json.dumps({"code": 429, "message": message, "status": "error"}).encode()
        )

    def test_a_daily_quota_message_stops_the_run_cleanly(self, tmp_path: Path) -> None:
        transport = self._quota_transport("You have run out of API credits for the current day")
        report = run_acquisition(tmp_path / "pkg", ["A", "B", "C"], transport)
        assert report.quota_stopped
        assert report.status is PackageStatus.INVALID or not report.successful
        # It stopped rather than grinding through every remaining symbol.
        assert len(transport.calls) == 1

    def test_a_minute_level_message_is_not_the_daily_wall(self, tmp_path: Path) -> None:
        provider = make_provider(
            self._quota_transport("You have run out of API credits for the current minute"),
            fetch_corporate_actions=False,
        )
        outcome = provider.fetch(
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, "SPY", START, END)
        )
        assert outcome.status is FetchStatus.RATE_LIMITED
        assert not outcome.status.stops_the_run

    def test_a_quota_stop_leaves_an_importable_package(self, tmp_path: Path) -> None:
        """Quota exhaustion is not corruption. What arrived is real."""

        class HalfwayTransport(FakeTransport):
            def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
                if len([c for c in self.calls if "/time_series" in c]) >= 1:
                    self.calls.append(url)
                    return json.dumps(
                        {
                            "code": 429,
                            "message": "API credits limit reached for the day",
                            "status": "error",
                        }
                    ).encode()
                return super().get(url, headers=headers)

        output = tmp_path / "pkg"
        provider = make_provider(HalfwayTransport(), batch_size=1, fetch_corporate_actions=False)
        report = run_acquisition(output, ["SPY", "AAPL"], provider=provider)
        assert report.status is PackageStatus.INCOMPLETE_QUOTA
        assert report.status.snapshot_ready
        assert not report.status.is_complete
        assert report.successful
        assert "run the SAME command again" in report.render()

    def test_a_quota_stopped_run_resumes_without_re_downloading(self, tmp_path: Path) -> None:
        class HalfwayTransport(FakeTransport):
            fail_after = 1

            def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
                if len([c for c in self.calls if "/time_series" in c]) >= self.fail_after:
                    self.calls.append(url)
                    return json.dumps(
                        {"code": 429, "message": "daily limit", "status": "error"}
                    ).encode()
                return super().get(url, headers=headers)

        output = tmp_path / "pkg"
        first = HalfwayTransport()
        run_acquisition(
            output,
            ["SPY", "AAPL"],
            provider=make_provider(first, batch_size=1, fetch_corporate_actions=False),
        )

        second = FakeTransport()
        report = run_acquisition(
            output,
            ["SPY", "AAPL"],
            provider=make_provider(second, batch_size=1, fetch_corporate_actions=False),
        )
        requested = {c.split("symbol=")[1].split("&")[0] for c in second.price_calls}
        assert "SPY" not in requested, "a resume re-downloaded what it already had"
        assert report.status.is_complete


# ---------------------------------------------------------------------------
# Partial failures and symbol classification
# ---------------------------------------------------------------------------


class TestPartialFailures:
    def test_one_bad_ticker_does_not_destroy_the_batch(self, tmp_path: Path) -> None:
        transport = FakeTransport(
            symbol_errors={"NOPE": {"code": 404, "message": "symbol not found", "status": "error"}}
        )
        report = run_acquisition(tmp_path / "pkg", ["SPY", "NOPE", "AAPL"], transport)
        assert {s.symbol for s in report.successful} == {"SPY", "AAPL"}
        assert [s.symbol for s in report.failed] == ["NOPE"]
        assert report.status is PackageStatus.VALID_WITH_WARNINGS

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"code": 404, "message": "symbol not found"}, SymbolStatus.NOT_FOUND),
            (
                {"code": 403, "message": "available with the Grow plan"},
                SymbolStatus.PLAN_RESTRICTED,
            ),
            (
                {"code": 400, "message": "symbol is ambiguous, specify exchange"},
                SymbolStatus.AMBIGUOUS,
            ),
        ],
    )
    def test_symbol_errors_are_classified_not_lumped_together(
        self, payload: dict[str, Any], expected: SymbolStatus
    ) -> None:
        """Not every provider error is a missing ticker."""
        provider = make_provider(FakeTransport(symbol_errors={"X": {**payload, "status": "error"}}))
        outcome = provider.fetch(
            FetchRequest(AcquisitionDataset.DAILY_PRICES, ("X", "SPY"), START, END)
        )
        assert outcome.per_symbol["X"] is expected
        assert outcome.per_symbol["SPY"] is SymbolStatus.VALID

    def test_an_empty_series_is_unavailable_historically_not_missing(self) -> None:
        provider = make_provider(FakeTransport(rows=[]))
        outcome = provider.fetch(
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, "NEW", START, END)
        )
        assert outcome.per_symbol["NEW"] is SymbolStatus.UNAVAILABLE_HISTORICALLY

    def test_the_failed_symbol_is_named_in_the_manifest(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(
            output,
            ["SPY", "NOPE"],
            FakeTransport(
                symbol_errors={"NOPE": {"code": 404, "message": "not found", "status": "error"}}
            ),
        )
        manifest = load_manifest(output / "manifest.toml")
        assert any("NOPE" in item for item in manifest.known_limitations)

    def test_a_network_failure_is_retryable_and_a_rejection_is_not(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(
            output,
            ["SPY"],
            FakeTransport(raise_on={"time_series": ProviderUnreachableError("no route")}),
        )
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        assert "SPY" in {r.symbol for r in journal.failed_requests()}


# ---------------------------------------------------------------------------
# Corporate actions and plan availability
# ---------------------------------------------------------------------------


class TestCorporateActions:
    def test_splits_and_dividends_come_from_dedicated_endpoints(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        run_acquisition(tmp_path / "pkg", ["SPY"], transport)
        assert any("/splits" in c for c in transport.calls)
        assert any("/dividends" in c for c in transport.calls)

    def test_a_split_becomes_a_canonical_row(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        report = run_acquisition(output, ["SPY"])
        assert report.rows[str(DatasetKind.SPLITS)] == 1
        text = (output / "splits.csv").read_text()
        assert "ex_date" in text and "ratio" in text
        # SPLIT_PAYLOAD is from_factor=1, to_factor=2. Under this vendor's
        # convention that is a price factor of 2, i.e. a 1-for-2 reverse split,
        # so the canonical share-count multiplier is 0.5.
        assert "2010-06-24,0.5" in text.replace(" ", "")

    def test_a_dividend_maps_to_cash_amount_not_amount(self, tmp_path: Path) -> None:
        """The canonical column is `cash_amount`. Twelve Data calls it `amount`.

        Reusing the vendor's name here is exactly the bug that quarantined every
        dividend row in the Tiingo path.
        """
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        header = (output / "dividends.csv").read_text().splitlines()[0]
        assert "cash_amount" in header
        assert header.split(",") == ["instrument_id", "ex_date", "cash_amount"]

    def test_a_plan_restriction_is_not_recorded_as_no_events(self, tmp_path: Path) -> None:
        """The distinction that would otherwise write a falsehood."""
        output = tmp_path / "pkg"
        provider = make_provider(FakeTransport(splits=None))
        report = run_acquisition(output, ["SPY"], provider=provider)
        assert provider.support[AcquisitionDataset.SPLITS] is (
            CapabilitySupport.NOT_AVAILABLE_ON_PLAN
        )
        text = " ".join(report.limitations)
        assert "not available on this subscription" in text
        assert "NOT about whether these securities had such events" in text

    def test_a_plan_restriction_blocks_reconstruction_rather_than_faking_it(
        self, tmp_path: Path
    ) -> None:
        report = run_acquisition(
            tmp_path / "pkg", ["SPY"], provider=make_provider(FakeTransport(splits=None))
        )
        assert all(
            item.quality is ReconstructionQuality.NOT_ATTEMPTED_NO_SPLIT_DATA
            for item in report.reconstruction
        )

    def test_an_empty_valid_response_is_evidence_of_absence(self) -> None:
        assert CapabilitySupport.EMPTY_VALID_RESPONSE.is_evidence_of_absence
        assert not CapabilitySupport.NOT_AVAILABLE_ON_PLAN.is_evidence_of_absence
        assert not CapabilitySupport.UNKNOWN.is_evidence_of_absence

    def test_untested_capabilities_are_unknown_not_claimed(self) -> None:
        capabilities = make_provider().capabilities()
        assert capabilities["intraday_ohlcv"] is CapabilitySupport.UNKNOWN
        assert capabilities["fundamentals"] is CapabilitySupport.UNKNOWN
        assert capabilities["daily_ohlcv"] is CapabilitySupport.UNKNOWN

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            # from/to, observed against a live account: Apple's 4-for-1 came
            # back as 0.25, and Apple's share count did not fall to a quarter.
            ({"from_factor": 4, "to_factor": 1}, Decimal(4)),
            ({"from_factor": 7, "to_factor": 1}, Decimal(7)),
            ({"from_factor": 1, "to_factor": 10}, Decimal("0.1")),
            # A spelled-out ratio carries its own direction and is read as
            # written, whatever the vendor's factor convention is.
            ({"factor": "3:1"}, Decimal(3)),
            ({"factor": "1:5"}, Decimal("0.2")),
            # A bare decimal has no direction of its own, so the provider's
            # declared convention settles it: 0.25 is a 4-for-1 here.
            ({"factor": "0.25"}, Decimal(4)),
            ({"factor": "8"}, Decimal("0.125")),
        ],
    )
    def test_every_split_notation_resolves_to_a_share_count_multiplier(
        self, payload: dict[str, Any], expected: Decimal
    ) -> None:
        # Getting the direction wrong inverts every price before the event.
        from tradeit.acquisition.twelvedata import _split_ratio

        assert _split_ratio(payload) == expected

    @pytest.mark.parametrize(
        ("payload", "share_count", "vendor_value"),
        [
            ({"from_factor": 7, "to_factor": 1}, Decimal(7), Decimal("0.142857142857")),
            ({"from_factor": 4, "to_factor": 1}, Decimal(4), Decimal("0.25")),
        ],
    )
    def test_the_real_apple_factors_normalize_to_the_real_apple_splits(
        self,
        payload: dict[str, Any],
        share_count: Decimal,
        vendor_value: Decimal,
    ) -> None:
        """The live smoke test's actual numbers, pinned.

        Twelve Data served Apple's 2014 7-for-1 as 0.142857142857... and its
        2020 4-for-1 as 0.25. Under the previous reading those became the
        share-count multipliers, which would have multiplied every pre-2014
        adjusted price by 1/28 instead of 28.
        """
        from tradeit.acquisition.reconstruct import ratios_agree
        from tradeit.acquisition.twelvedata import _split_ratio, _vendor_factor

        normalized = _split_ratio(payload)
        assert normalized is not None
        assert ratios_agree(normalized, share_count)
        # And what the vendor said is kept, not thrown away.
        observed = _vendor_factor(payload)
        assert observed is not None
        assert ratios_agree(observed, vendor_value)

    def test_the_convention_is_declared_not_inferred_from_the_value(self) -> None:
        """0.25 is a 4-for-1's price factor and a 1-for-4's share factor. The
        number cannot settle which, so a declaration does."""
        from tradeit.acquisition.reconstruct import SplitFactorConvention
        from tradeit.acquisition.twelvedata import SPLIT_FACTOR_CONVENTION

        assert SPLIT_FACTOR_CONVENTION is SplitFactorConvention.PRICE_ADJUSTMENT_MULTIPLIER

    def test_the_split_row_keeps_the_vendors_own_number_beside_the_canonical_one(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        rows = list(csv.DictReader((output / "splits.csv").read_text().splitlines()))
        assert rows
        row = rows[0]
        # SPLIT_PAYLOAD is from_factor=1, to_factor=2 -> a 1-for-2 reverse split
        # under this vendor's convention.
        assert row["ratio"] == "0.5"
        assert row["vendor_factor"] == "2"
        assert row["vendor_convention"] == "price_adjustment_multiplier"
        assert row["source_provider"] == "twelve_data"


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


class TestResponseHandling:
    def test_rows_are_sorted_chronologically_regardless_of_response_order(
        self, tmp_path: Path
    ) -> None:
        """Twelve Data defaults to newest-first, and the pipeline requires the
        opposite. Sorting here rather than trusting a query parameter."""
        reversed_rows = list(reversed(values(count=20, split_at=None)))
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"], FakeTransport(rows=reversed_rows))
        text = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        dates = [line.split(",")[1] for line in text.strip().splitlines()[1:]]
        assert dates == sorted(dates)

    def test_a_duplicate_timestamp_is_flagged_not_written_twice(self, tmp_path: Path) -> None:
        rows = values(count=5, split_at=None)
        report = run_acquisition(tmp_path / "pkg", ["SPY"], FakeTransport(rows=[*rows, rows[0]]))
        assert report.rows[str(DatasetKind.DAILY_BARS)] == 5
        assert any("duplicate" in f for f in report.findings)

    def test_a_missing_volume_does_not_lose_the_bar(self, tmp_path: Path) -> None:
        rows = values(count=3, split_at=None)
        for row in rows:
            row.pop("volume")
        report = run_acquisition(tmp_path / "pkg", ["SPY"], FakeTransport(rows=rows))
        assert report.rows[str(DatasetKind.DAILY_BARS)] == 3

    def test_a_row_missing_a_price_is_reported_not_written(self, tmp_path: Path) -> None:
        rows = values(count=3, split_at=None)
        rows[1].pop("high")
        report = run_acquisition(tmp_path / "pkg", ["SPY"], FakeTransport(rows=rows))
        assert report.rows[str(DatasetKind.DAILY_BARS)] == 2
        assert any("incomplete OHLC" in f for f in report.findings)

    def test_a_corrupted_response_is_malformed_not_empty(self) -> None:
        provider = make_provider(FakeTransport(body_override=b"{not json"))
        outcome = provider.fetch(
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, "SPY", START, END)
        )
        assert outcome.status is FetchStatus.MALFORMED

    def test_an_api_error_payload_with_http_200_is_not_success(self) -> None:
        # Twelve Data answers 200 with an error body. Trusting the HTTP status
        # would record "ok, zero rows".
        provider = make_provider(
            FakeTransport(
                body_override=json.dumps(
                    {"code": 401, "message": "invalid api key", "status": "error"}
                ).encode()
            )
        )
        outcome = provider.fetch(
            FetchRequest.one(AcquisitionDataset.DAILY_PRICES, "SPY", START, END)
        )
        assert outcome.status is FetchStatus.REJECTED
        assert "401" in outcome.error

    def test_a_daily_datetime_is_taken_as_a_session_date_not_converted(
        self, tmp_path: Path
    ) -> None:
        """A timezone conversion here would move a bar into the wrong session."""
        rows = [
            {
                "datetime": "2010-01-04",
                "open": "1",
                "high": "1",
                "low": "1",
                "close": "1",
                "volume": "1",
            }
        ]
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"], FakeTransport(rows=rows))
        text = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        assert "2010-01-04" in text
        assert "2010-01-03" not in text

    def test_a_timestamped_datetime_keeps_its_date(self) -> None:
        from tradeit.acquisition.twelvedata import _session_date

        assert _session_date("2010-01-04 09:30:00") == dt.date(2010, 1, 4)
        assert _session_date("2010-01-04T00:00:00.000Z") == dt.date(2010, 1, 4)


class TestCoverageVerification:
    def test_a_short_series_is_reported_rather_than_assumed_complete(self, tmp_path: Path) -> None:
        report = run_acquisition(
            tmp_path / "pkg", ["SPY"], FakeTransport(rows=values(count=10, split_at=None))
        )
        # Ten bars from 2010-01-04 against a window ending 2011-03-31: hundreds
        # of real sessions are missing at the end and the report says so.
        assert any("session(s) short" in f for f in report.findings)
        # The *start* is complete. 2010-01-01 is New Year's Day and the first
        # session on or after it is 2010-01-04, which is exactly what arrived.
        # The old check called this a shortfall; that was the false positive.
        assert not any("short at the start" in f for f in report.findings)

    def test_a_response_at_the_output_cap_warns_about_truncation(self, tmp_path: Path) -> None:
        provider = make_provider(FakeTransport(), fetch_corporate_actions=False)
        provider.coverage["SPY"] = (START, END, MAX_OUTPUTSIZE)
        findings = provider.coverage_findings(START, END)
        assert any("response cap" in f for f in findings)

    def test_explicit_dates_are_sent_rather_than_relying_on_outputsize(self) -> None:
        transport = FakeTransport()
        provider = make_provider(transport, fetch_corporate_actions=False)
        provider.fetch(FetchRequest.one(AcquisitionDataset.DAILY_PRICES, "SPY", START, END))
        url = transport.calls[0]
        assert f"start_date={START.isoformat()}" in url
        # One day past the requested end, deliberately. See END_DATE_PROBE_DAYS
        # and the trim tests below: the extra day is discarded before anything
        # is written, so this cannot leak a session nobody asked for.
        probe = END + dt.timedelta(days=END_DATE_PROBE_DAYS)
        assert f"end_date={probe.isoformat()}" in url

    def test_the_requested_final_session_is_not_lost_to_an_exclusive_end_date(
        self, tmp_path: Path
    ) -> None:
        """The 2025-12-31 bug, pinned.

        A live run asked for 2010-01-01..2025-12-31 and got a last session of
        2025-12-30 for two independent symbols. 2025-12-31 was a US trading
        session. This transport reproduces that behaviour — it treats end_date
        as **exclusive** — and the requested final session must still reach the
        package.
        """
        last = dt.date(2025, 12, 31)
        transport = ExclusiveEndDateTransport(last_session=last)
        provider = make_provider(transport, fetch_corporate_actions=False)
        options = AcquisitionOptions(start=dt.date(2025, 12, 1), end=last, max_wait_s=0)
        output = tmp_path / "pkg"
        AcquisitionRunner(provider, ["SPY"], output, options).run()

        sessions = _sessions_written(output)
        assert sessions, "no bars were written at all"
        assert sessions[-1] == last, (
            f"the requested final session {last} is missing; the package stops at "
            f"{sessions[-1]}. An exclusive end_date dropped it."
        )

    def test_the_probe_day_never_reaches_the_package(self, tmp_path: Path) -> None:
        """The other half of the fix, and the half that makes it safe.

        This transport treats end_date as **inclusive**, so asking for one day
        beyond the window returns a bar the operator did not request. It must be
        discarded: keeping it would be a silent one-day lookahead, which is the
        exact failure the rest of the platform exists to prevent.
        """
        requested_end = dt.date(2025, 12, 30)
        transport = InclusiveEndDateTransport(last_session=dt.date(2025, 12, 31))
        provider = make_provider(transport, fetch_corporate_actions=False)
        options = AcquisitionOptions(start=dt.date(2025, 12, 1), end=requested_end, max_wait_s=0)
        output = tmp_path / "pkg"
        report = AcquisitionRunner(provider, ["SPY"], output, options).run()

        sessions = _sessions_written(output)
        assert sessions[-1] == requested_end
        assert dt.date(2025, 12, 31) not in sessions
        assert any("were discarded" in f for f in report.findings)

    def test_both_end_date_semantics_produce_the_same_package(self, tmp_path: Path) -> None:
        """Why the fix is safe without the vendor's documentation.

        The probe plus the trim gives identical output whether end_date turns
        out to be inclusive or exclusive, so implementing it costs nothing if
        the diagnosis is wrong.
        """
        requested_end = dt.date(2025, 12, 31)
        options = AcquisitionOptions(start=dt.date(2025, 12, 1), end=requested_end, max_wait_s=0)
        written = []
        for index, transport in enumerate(
            (
                ExclusiveEndDateTransport(last_session=requested_end),
                InclusiveEndDateTransport(last_session=requested_end),
            )
        ):
            output = tmp_path / f"pkg{index}"
            provider = make_provider(transport, fetch_corporate_actions=False)
            AcquisitionRunner(provider, ["SPY"], output, options).run()
            written.append(_sessions_written(output))
        assert written[0] == written[1]
        assert written[0][-1] == requested_end

    # -- the start of the range, measured the same way as the end -------------

    def test_a_holiday_start_date_is_not_reported_as_missing_sessions(self) -> None:
        """The 2010-01-01 case, which is what the real run actually requested.

        New Year's Day is a market holiday and the 2nd and 3rd were a weekend,
        so a request from 2010-01-01 whose first bar is 2010-01-04 is complete.
        The old check called that a shortfall and offered two explanations, both
        wrong: the security had listed, and the provider's history did not begin
        later.
        """
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["AAPL"] = (dt.date(2010, 1, 4), dt.date(2025, 12, 31), 4020)
        findings = provider.coverage_findings(dt.date(2010, 1, 1), dt.date(2025, 12, 31))
        assert not any("short at the start" in f for f in findings)
        assert not any("had not listed" in f for f in findings)

    def test_a_weekend_start_date_is_not_reported_as_missing_sessions(self) -> None:
        saturday = dt.date(2026, 1, 3)
        monday = dt.date(2026, 1, 5)
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (monday, monday, 1)
        findings = provider.coverage_findings(saturday, monday)
        assert not any("short at the start" in f for f in findings)

    def test_a_trading_day_start_that_is_met_exactly_is_silent(self) -> None:
        session = dt.date(2010, 1, 4)
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (session, session, 1)
        findings = provider.coverage_findings(session, session)
        assert findings == []

    def test_a_genuinely_missing_first_session_is_still_reported(self) -> None:
        """Do not hide truncated coverage. 2010-01-04 was a session and it is
        absent, so the package really is short."""
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (dt.date(2010, 1, 11), dt.date(2010, 3, 31), 55)
        findings = provider.coverage_findings(dt.date(2010, 1, 1), dt.date(2010, 3, 31))
        short = [f for f in findings if "short at the start" in f]
        assert len(short) == 1
        assert "2010-01-04" in short[0]
        # Five sessions in the week of the 4th precede the 11th.
        assert "5 session(s) short" in short[0]

    def test_an_ipo_after_the_requested_range_is_reported_as_short(self) -> None:
        """Indistinguishable in the data from a provider whose history starts
        late, and the message says both possibilities rather than picking."""
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["NEWCO"] = (dt.date(2024, 6, 3), dt.date(2025, 12, 31), 400)
        findings = provider.coverage_findings(dt.date(2010, 1, 1), dt.date(2025, 12, 31))
        short = [f for f in findings if "short at the start" in f]
        assert len(short) == 1
        assert "had not listed" in short[0]
        assert "history begins later" in short[0]

    def test_a_provider_history_beginning_late_is_reported_as_short(self) -> None:
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (dt.date(2015, 1, 2), dt.date(2025, 12, 31), 2760)
        findings = provider.coverage_findings(dt.date(2010, 1, 1), dt.date(2025, 12, 31))
        assert any("short at the start" in f for f in findings)

    def test_the_real_run_window_produces_no_coverage_complaint_at_all(self) -> None:
        """2010-01-01 to 2025-12-31, the exact window of the live smoke test,
        against the exact coverage it returned."""
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["AAPL"] = (dt.date(2010, 1, 4), dt.date(2025, 12, 31), 4024)
        provider.coverage["NVDA"] = (dt.date(2010, 1, 4), dt.date(2025, 12, 31), 4024)
        findings = provider.coverage_findings(dt.date(2010, 1, 1), dt.date(2025, 12, 31))
        assert not any("short" in f for f in findings)
        assert not any("had not listed" in f for f in findings)

    def test_a_weekend_end_date_is_not_reported_as_missing_sessions(self) -> None:
        """The check that used to cry wolf on every well-formed package.

        A request ending on a Saturday is complete when it ends on the Friday.
        Reporting that as a shortfall trained the reader to skip the line, which
        is how the genuinely missing 2025-12-31 nearly went unnoticed.
        """
        saturday = dt.date(2026, 1, 3)
        friday = dt.date(2026, 1, 2)
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (dt.date(2026, 1, 2), friday, 1)
        findings = provider.coverage_findings(dt.date(2026, 1, 2), saturday)
        assert not any("session(s) short" in f for f in findings)

    def test_a_genuinely_missing_session_is_reported_as_such(self) -> None:
        provider = make_provider(fetch_corporate_actions=False)
        provider.coverage["SPY"] = (dt.date(2025, 12, 1), dt.date(2025, 12, 30), 20)
        findings = provider.coverage_findings(dt.date(2025, 12, 1), dt.date(2025, 12, 31))
        assert any("session(s) short" in f for f in findings)
        assert any("NOT a weekend or holiday" in f for f in findings)

    def test_only_documented_endpoints_are_reachable(self) -> None:
        assert set(ENDPOINTS.values()) == {
            "time_series",
            "splits",
            "dividends",
            "earliest_timestamp",
        }
        provider = make_provider()
        outcome = provider.fetch(FetchRequest.one(AcquisitionDataset.CORPORATE_ACTIONS, "SPY"))
        assert outcome.status is FetchStatus.REJECTED
        assert "no endpoint for" in outcome.error


# ---------------------------------------------------------------------------
# Cross-provider: the package must carry no vendor-specific assumption
# ---------------------------------------------------------------------------


class TestCrossProviderArchitecture:
    def test_the_twelve_data_package_imports_cleanly(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        output = tmp_path / "pkg"
        acquired = run_acquisition(output, ["SPY", "AAPL"])
        assert acquired.status.snapshot_ready

        manifest = load_manifest(output / "manifest.toml")
        sink = DatabaseSink(session=db_session, manifest=manifest, source_path=output)
        report = PackageImporter(manifest, output, options=ImportOptions(), sink=sink).run()
        package = sink.finalise(report)
        db_session.commit()

        assert not report.aborted
        assert report.rows_quarantined == 0
        assert report.rows_written == report.rows_read
        assert package.snapshot_id

    def test_both_providers_produce_the_same_column_contract(self, tmp_path: Path) -> None:
        """The architecture test: no vendor's field names reach the package."""
        from tests.unit.test_acquisition import run_acquisition as run_tiingo

        twelve = tmp_path / "twelve"
        tiingo = tmp_path / "tiingo"
        run_acquisition(twelve, ["SPY"])
        run_tiingo(tiingo, ["SPY"])

        def header(root: Path, name: str) -> list[str]:
            path = root / name
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            return blob.decode().splitlines()[0].split(",")

        assert header(twelve, "daily_bars.csv.gz") == header(tiingo, "daily_bars.csv.gz")
        assert header(twelve, "splits.csv") == header(tiingo, "splits.csv")
        assert header(twelve, "dividends.csv") == header(tiingo, "dividends.csv")

    def test_no_vendor_field_name_appears_in_a_package_file(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        vendor_names = ("divCash", "splitFactor", "adjClose", "to_factor", "from_factor")
        for path in output.iterdir():
            if not path.is_file():
                continue
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            text = blob.decode()
            for name in vendor_names:
                assert name not in text, f"{name} leaked into {path.name}"

    def test_the_importer_needs_no_column_mapping_from_either_provider(
        self, tmp_path: Path
    ) -> None:
        # Both adapters write canonical names, so the manifest carries no
        # per-vendor mapping and the importer has no provider branch.
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        assert all(not file.columns for file in manifest.files)

    def test_the_runner_holds_no_reference_to_any_vendor(self) -> None:
        from tradeit.acquisition import runner

        source = Path(runner.__file__).read_text()
        for vendor in ("tiingo", "twelvedata", "twelve_data", "eodhd"):
            assert vendor.lower() not in source.lower(), (
                f"{vendor} is named in the runner; the provider seam has leaked"
            )

    def test_tiingo_still_acquires_after_the_interface_change(self, tmp_path: Path) -> None:
        from tests.unit.test_acquisition import run_acquisition as run_tiingo

        report = run_tiingo(tmp_path / "pkg", ["SPY", "AAPL"])
        assert report.status is PackageStatus.VALID
        assert report.rows[str(DatasetKind.DAILY_BARS)] > 0
