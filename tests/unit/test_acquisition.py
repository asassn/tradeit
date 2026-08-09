"""The acquisition tool, exercised without a network.

Every test here runs against a fake transport that answers like Tiingo. That is
not a compromise forced by the environment — it is how this should be tested
anyway, because a suite that hits a vendor is a suite that fails when the vendor
has an outage, burns rate limit on every CI run, and needs a credential to be
green.

The tests that matter most are the ones about **not leaking a credential** and
about **resuming**, because both are silent failures: a key in a journal line is
invisible until somebody shares the file, and a broken resume is invisible until
somebody re-downloads a decade of history over a hotel connection.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchRequest,
    FetchStatus,
    available_providers,
    get_provider_class,
)
from tradeit.acquisition.cache import RawCache
from tradeit.acquisition.eodhd import EodhdAcquisition
from tradeit.acquisition.journal import AcquisitionJournal
from tradeit.acquisition.normalize import assign_instrument_ids, normalize_prices
from tradeit.acquisition.runner import (
    AcquisitionOptions,
    AcquisitionRunner,
    PackageStatus,
    estimate_size,
)
from tradeit.acquisition.tiingo import TiingoAcquisition, redact_url
from tradeit.data.packages.database import DatabaseSink
from tradeit.data.packages.importer import ImportOptions, PackageImporter
from tradeit.data.packages.manifest import WORKSPACE_DIRNAME, load_manifest
from tradeit.data.packages.spec import DatasetKind
from tradeit.data.providers.http import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from tradeit.errors import ConfigError, ProviderError

#: A distinctive value, so a test can assert it appears nowhere on disk.
SECRET = "SUPERSECRETKEY0123456789"

START = dt.date(2010, 1, 1)
END = dt.date(2010, 6, 30)


def price_rows(
    count: int = 60, *, split_at: int | None = 30, div_at: int | None = 10
) -> list[dict]:
    rows: list[dict] = []
    day = dt.date(2010, 1, 4)
    price = 100.0
    written = 0
    while written < count:
        if day.weekday() < 5:
            price *= 1.002
            split = 2.0 if written == split_at else 1.0
            if written == split_at:
                price /= 2
            rows.append(
                {
                    "date": f"{day}T00:00:00.000Z",
                    "open": round(price * 0.99, 2),
                    "high": round(price * 1.01, 2),
                    "low": round(price * 0.98, 2),
                    "close": round(price, 2),
                    "volume": 1_000_000 + written,
                    "adjOpen": round(price * 0.99, 4),
                    "adjHigh": round(price * 1.01, 4),
                    "adjLow": round(price * 0.98, 4),
                    "adjClose": round(price, 4),
                    "adjVolume": 1_000_000 + written,
                    "divCash": 0.25 if written == div_at else 0.0,
                    "splitFactor": split,
                }
            )
            written += 1
        day += dt.timedelta(days=1)
    return rows


class FakeTransport:
    """Answers like Tiingo. Records calls; can be told to fail."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        fail_symbols: dict[str, Exception] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.rows = price_rows() if rows is None else rows
        self.fail_symbols = fail_symbols or {}
        self.metadata = metadata
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        self.headers.append(dict(headers or {}))
        symbol = url.split("/daily/")[1].split("/")[0].split("?")[0]
        failure = self.fail_symbols.get(symbol)
        if failure is not None:
            raise failure
        if "/prices" not in url:
            payload = self.metadata or {
                "ticker": symbol,
                "name": f"{symbol} Incorporated",
                "exchangeCode": "NASDAQ",
                "startDate": "2010-01-04",
                "endDate": "2026-08-08",
            }
            return json.dumps(payload).encode()
        return json.dumps(self.rows).encode()

    @property
    def price_calls(self) -> list[str]:
        return [c for c in self.calls if "/prices" in c]


def make_provider(transport: FakeTransport | None = None) -> TiingoAcquisition:
    return TiingoAcquisition(token=SECRET, transport=transport or FakeTransport())


def run_acquisition(
    output: Path,
    symbols: list[str],
    transport: FakeTransport | None = None,
    **kwargs: Any,
) -> Any:
    provider = make_provider(transport)
    options = AcquisitionOptions(start=START, end=END, **kwargs)
    return AcquisitionRunner(provider, symbols, output, options).run()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestCredentialSafety:
    def test_the_key_travels_in_a_header_not_the_url(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        run_acquisition(tmp_path / "pkg", ["SPY"], transport)
        assert transport.calls
        for url in transport.calls:
            assert SECRET not in url
        for headers in transport.headers:
            assert headers["Authorization"] == f"Token {SECRET}"

    def test_the_key_appears_nowhere_on_disk(self, tmp_path: Path) -> None:
        """The test that matters. A key in a journal line is invisible until
        somebody shares the file to ask for help with a failed download."""
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY", "AAPL"])
        written = [p for p in output.rglob("*") if p.is_file()]
        assert written
        for path in written:
            blob = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            assert SECRET.encode() not in blob, f"{path} contains the API key"

    def test_a_credential_shaped_query_parameter_is_redacted(self) -> None:
        url = "https://api.example.com/x?token=abc123&symbol=SPY&api_key=zzz"
        redacted = redact_url(url)
        assert "abc123" not in redacted
        assert "zzz" not in redacted
        assert "symbol=SPY" in redacted

    def test_the_credential_hint_reveals_almost_nothing(self) -> None:
        hint = make_provider().credential_hint()
        assert SECRET not in hint
        assert str(len(SECRET)) in hint

    def test_no_key_means_no_request_at_all(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        provider = TiingoAcquisition(token="", transport=transport)
        report = AcquisitionRunner(
            provider, ["SPY"], tmp_path / "pkg", AcquisitionOptions(start=START, end=END)
        ).run()
        assert not transport.calls
        assert report.status is PackageStatus.INVALID
        assert any("TIINGO_API_KEY" in p for p in report.problems)

    def test_the_env_var_name_is_the_one_the_docs_promise(self) -> None:
        assert TiingoAcquisition.credential_env == "TIINGO_API_KEY"
        assert TiingoAcquisition.credential_env_fallback == "TRADEIT_TIINGO_TOKEN"


# ---------------------------------------------------------------------------
# Raw preservation and resume
# ---------------------------------------------------------------------------


class TestRawCacheAndResume:
    def test_the_raw_response_is_kept_byte_for_byte(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        transport = FakeTransport()
        run_acquisition(output, ["SPY"], transport)
        raw = output / WORKSPACE_DIRNAME / "raw" / "tiingo" / "daily_prices"
        files = sorted(raw.glob("*.json"))
        bodies = [f for f in files if not f.name.endswith(".meta.json")]
        assert len(bodies) == 1
        assert json.loads(bodies[0].read_text()) == transport.rows

    def test_a_second_run_re_downloads_nothing(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        first = FakeTransport()
        run_acquisition(output, ["SPY", "AAPL"], first)
        assert len(first.price_calls) == 2

        second = FakeTransport()
        report = run_acquisition(output, ["SPY", "AAPL"], second)
        assert second.calls == [], "a resume re-downloaded data it already had"
        assert report.cached_requests == 4
        assert report.fetched_requests == 0
        assert report.status is PackageStatus.VALID

    def test_an_interrupted_run_resumes_from_where_it_stopped(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        # First attempt: one symbol works, one is unreachable.
        broken = FakeTransport(fail_symbols={"AAPL": ProviderUnreachableError("connection reset")})
        first = run_acquisition(output, ["SPY", "AAPL"], broken)
        assert len(first.successful) == 1
        assert len(first.failed) == 1

        # Second attempt: the network is back. SPY must not be requested again.
        healed = FakeTransport()
        second = run_acquisition(output, ["SPY", "AAPL"], healed)
        requested = {c.split("/daily/")[1].split("/")[0].split("?")[0] for c in healed.calls}
        assert requested == {"AAPL"}
        assert len(second.successful) == 2

    def test_force_refresh_re_downloads(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        transport = FakeTransport()
        run_acquisition(output, ["SPY"], transport, force_refresh=True)
        assert len(transport.price_calls) == 1

    def test_a_truncated_cache_entry_is_treated_as_absent(self, tmp_path: Path) -> None:
        # An interrupted write must not be mistaken for a complete download.
        cache = RawCache(tmp_path / "ws")
        request = FetchRequest(
            dataset=AcquisitionDataset.DAILY_PRICES, symbol="SPY", start=START, end=END
        )
        entry = cache.put("tiingo", request, b'[{"a": 1}]', url="https://example/x")
        assert cache.get("tiingo", request) is not None
        entry.path.write_bytes(b'[{"a": ')  # digest no longer matches
        assert cache.get("tiingo", request) is None

    def test_running_twice_does_not_duplicate_package_rows(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        first = run_acquisition(output, ["SPY", "AAPL"])
        second = run_acquisition(output, ["SPY", "AAPL"])
        assert first.rows == second.rows
        bars = output / "daily_bars.csv.gz"
        rows = gzip.decompress(bars.read_bytes()).decode().strip().splitlines()
        assert len(rows) - 1 == second.rows[str(DatasetKind.DAILY_BARS)]


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ProviderAuthError("bad key"), FetchStatus.REJECTED),
            (ProviderRateLimitError("slow down"), FetchStatus.RATE_LIMITED),
            (ProviderUnreachableError("no route"), FetchStatus.UNREACHABLE),
            (ProviderError("404 not found"), FetchStatus.REJECTED),
        ],
    )
    def test_transport_failures_become_statuses_not_exceptions(
        self, error: Exception, expected: FetchStatus
    ) -> None:
        provider = make_provider(FakeTransport(fail_symbols={"SPY": error}))
        outcome = provider.fetch(FetchRequest(AcquisitionDataset.DAILY_PRICES, "SPY", START, END))
        assert outcome.status is expected
        assert outcome.error

    def test_one_bad_symbol_does_not_invalidate_the_package(self, tmp_path: Path) -> None:
        report = run_acquisition(
            tmp_path / "pkg",
            ["SPY", "BADSYM", "AAPL"],
            FakeTransport(fail_symbols={"BADSYM": ProviderError("404")}),
        )
        assert report.status is PackageStatus.VALID_WITH_WARNINGS
        assert report.status.snapshot_ready
        assert [s.symbol for s in report.failed] == ["BADSYM"]

    def test_every_symbol_failing_is_invalid(self, tmp_path: Path) -> None:
        report = run_acquisition(
            tmp_path / "pkg",
            ["SPY", "AAPL"],
            FakeTransport(
                fail_symbols={
                    "SPY": ProviderError("404"),
                    "AAPL": ProviderError("404"),
                }
            ),
        )
        assert report.status is PackageStatus.INVALID
        assert not report.status.snapshot_ready

    def test_the_failed_symbols_are_named_in_the_manifest(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(
            output, ["SPY", "BADSYM"], FakeTransport(fail_symbols={"BADSYM": ProviderError("404")})
        )
        manifest = load_manifest(output / "manifest.toml")
        assert any("BADSYM" in item for item in manifest.known_limitations)

    def test_a_rejected_symbol_is_not_retried_automatically(self, tmp_path: Path) -> None:
        # Retrying a 403 forever is how a typo in a key becomes an IP ban.
        output = tmp_path / "pkg"
        run_acquisition(
            output, ["SPY", "NOPE"], FakeTransport(fail_symbols={"NOPE": ProviderAuthError("403")})
        )
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        assert "NOPE" in journal.rejected_symbols()
        assert "NOPE" not in {r.symbol for r in journal.failed_requests()}

    def test_a_retryable_failure_is_offered_for_retry(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(
            output,
            ["SPY", "FLAKY"],
            FakeTransport(fail_symbols={"FLAKY": ProviderUnreachableError("timeout")}),
        )
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        assert "FLAKY" in {r.symbol for r in journal.failed_requests()}


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


class TestJournal:
    def test_every_request_is_recorded(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY", "AAPL"])
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        assert len(journal.entries) == 4  # metadata + prices, twice

    def test_an_entry_carries_everything_needed_to_diagnose(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        journal = AcquisitionJournal(output / WORKSPACE_DIRNAME / "journal.jsonl")
        prices = next(e for e in journal.entries if e.dataset == "daily_prices")
        assert prices.provider == "tiingo"
        assert prices.symbol == "SPY"
        assert prices.requested_start == START.isoformat()
        assert prices.requested_end == END.isoformat()
        assert prices.requested_at
        assert prices.rows > 0
        assert prices.source_file
        assert len(prices.sha256) == 64
        assert prices.attempts >= 1

    def test_a_truncated_final_line_does_not_break_reading(self, tmp_path: Path) -> None:
        # The expected shape of an interrupted run, and the moment somebody
        # most needs to read the journal.
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        path = output / WORKSPACE_DIRNAME / "journal.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"provider": "tiingo", "dataset": ')
        assert len(AcquisitionJournal(path).entries) == 2


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_the_package_stores_raw_prices_not_adjusted(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        rows = [
            {
                "date": "2010-01-04T00:00:00.000Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
                "adjClose": 50.25,
                "divCash": 0.0,
                "splitFactor": 1.0,
            }
        ]
        run_acquisition(output, ["SPY"], FakeTransport(rows=rows))
        text = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        assert "100.5" in text
        assert "50.25" not in text

    def test_the_adjusted_columns_are_kept_in_a_sidecar(self, tmp_path: Path) -> None:
        # Not discarded: the platform's own adjustment must be checkable
        # against the vendor's rather than agreeing with it by construction.
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        sidecar = output / WORKSPACE_DIRNAME / "vendor_adjusted_prices.csv.gz"
        assert sidecar.exists()
        text = gzip.decompress(sidecar.read_bytes()).decode()
        assert "adj_close" in text
        assert "split_factor" in text

    def test_splits_come_from_the_vendors_column(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        report = run_acquisition(output, ["SPY"], FakeTransport(rows=price_rows(split_at=5)))
        assert report.rows[str(DatasetKind.SPLITS)] == 1
        text = (output / "splits.csv").read_text()
        assert "ratio" in text
        assert "2" in text

    def test_an_ordinary_session_is_not_a_split_or_a_dividend(self) -> None:
        rows = normalize_prices(
            "SPY",
            1,
            [
                {
                    "date": "2010-01-04",
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                    "splitFactor": 1.0,
                    "divCash": 0.0,
                }
            ],
        )
        assert rows.count(DatasetKind.SPLITS) == 0
        assert rows.count(DatasetKind.DIVIDENDS) == 0
        assert rows.count(DatasetKind.DAILY_BARS) == 1

    def test_a_row_with_no_date_is_reported_not_silently_dropped(self) -> None:
        rows = normalize_prices("SPY", 1, [{"open": 1, "high": 1, "low": 1, "close": 1}])
        assert rows.count(DatasetKind.DAILY_BARS) == 0
        assert any("no date" in f for f in rows.findings)

    def test_a_duplicate_session_is_reported(self) -> None:
        row = {
            "date": "2010-01-04",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        rows = normalize_prices("SPY", 1, [row, dict(row)])
        assert rows.count(DatasetKind.DAILY_BARS) == 1
        assert any("duplicate" in f for f in rows.findings)

    def test_instrument_ids_are_stable_across_runs_and_orderings(self) -> None:
        # An id that moved between runs would make two packages of the same
        # data non-comparable.
        assert assign_instrument_ids(["SPY", "AAPL"]) == assign_instrument_ids(["AAPL", "SPY"])

    def test_prices_are_not_written_in_exponent_notation(self, tmp_path: Path) -> None:
        # The importer refuses it, and a tiny price is exactly where a float
        # would produce one.
        output = tmp_path / "pkg"
        rows = [
            {
                "date": "2010-01-04",
                "open": 0.00001,
                "high": 0.00002,
                "low": 0.000009,
                "close": 0.00001,
                "volume": 10,
            }
        ]
        run_acquisition(output, ["PENNY"], FakeTransport(rows=rows))
        text = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        assert "e-" not in text.lower()


# ---------------------------------------------------------------------------
# The package, and whether it imports
# ---------------------------------------------------------------------------


class TestPackageOutput:
    def test_the_manifest_declares_every_written_file(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY", "AAPL"])
        manifest = load_manifest(output / "manifest.toml")
        declared = {f.path for f in manifest.files}
        on_disk = {p.name for p in output.iterdir() if p.is_file() and p.name != "manifest.toml"}
        assert declared == on_disk

    def test_the_manifest_hashes_match_the_files(self, tmp_path: Path) -> None:
        from tradeit.data.packages.manifest import verify_files

        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        assert verify_files(manifest, output) == []

    def test_coverage_is_observed_not_requested(self, tmp_path: Path) -> None:
        # The vendor's data starts 2010-01-04 whatever we asked for.
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        assert manifest.coverage.start == dt.date(2010, 1, 4)
        assert manifest.coverage.start != START

    def test_the_manifest_records_what_the_provider_cannot_supply(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        manifest = load_manifest(output / "manifest.toml")
        text = " ".join(manifest.known_limitations)
        assert "publication timestamp" in text
        assert "not acquired" in text

    def test_the_bar_file_is_byte_identical_across_runs(self, tmp_path: Path) -> None:
        # gzip stamps a timestamp by default, which would change every digest
        # for no reason and make two identical packages look different.
        first = tmp_path / "a"
        second = tmp_path / "b"
        run_acquisition(first, ["SPY"])
        run_acquisition(second, ["SPY"])
        assert (first / "daily_bars.csv.gz").read_bytes() == (
            second / "daily_bars.csv.gz"
        ).read_bytes()

    def test_the_acquired_package_imports_cleanly(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """The end-to-end claim: what acquisition writes, the importer accepts."""
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
        assert report.rows_written == sum(acquired.rows.values())
        assert package.snapshot_id
        assert not package.partial

    def test_the_workspace_does_not_make_the_package_unverifiable(self, tmp_path: Path) -> None:
        # The raw cache lives inside the package so one directory is the whole
        # handoff. That must not read as undeclared data files.
        from tradeit.data.packages.manifest import verify_files

        output = tmp_path / "pkg"
        run_acquisition(output, ["SPY"])
        assert (output / WORKSPACE_DIRNAME).is_dir()
        assert verify_files(load_manifest(output / "manifest.toml"), output) == []


# ---------------------------------------------------------------------------
# Provider pluggability
# ---------------------------------------------------------------------------


class TestProviderSeam:
    def test_both_providers_are_registered(self) -> None:
        assert available_providers() == ["eodhd", "tiingo"]

    def test_an_unknown_provider_names_the_ones_that_exist(self) -> None:
        with pytest.raises(ConfigError, match="tiingo"):
            get_provider_class("bloomberg")

    def test_the_eodhd_stub_refuses_clearly_rather_than_failing_obscurely(self) -> None:
        provider = EodhdAcquisition()
        outcome = provider.fetch(FetchRequest(AcquisitionDataset.DAILY_PRICES, "SPY"))
        assert outcome.status is FetchStatus.REJECTED
        assert "stub" in outcome.error
        assert "--provider tiingo" in outcome.error
        assert provider.implemented is False

    def test_the_stub_does_not_pretend_to_have_a_credential(self) -> None:
        assert EodhdAcquisition().has_credential() is False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def test_every_number_in_the_summary_comes_from_the_files(self, tmp_path: Path) -> None:
        output = tmp_path / "pkg"
        report = run_acquisition(output, ["SPY", "AAPL"])
        text = gzip.decompress((output / "daily_bars.csv.gz").read_bytes()).decode()
        actual_bars = len(text.strip().splitlines()) - 1
        assert report.rows[str(DatasetKind.DAILY_BARS)] == actual_bars
        assert f"{actual_bars:,}" in report.render()

    def test_the_summary_says_what_to_run_next(self, tmp_path: Path) -> None:
        report = run_acquisition(tmp_path / "pkg", ["SPY"])
        rendered = report.render()
        assert "tradeit data import" in rendered
        assert "Snapshot-ready       : YES" in rendered

    def test_a_failed_package_says_nothing_was_imported(self, tmp_path: Path) -> None:
        report = run_acquisition(
            tmp_path / "pkg", ["SPY"], FakeTransport(fail_symbols={"SPY": ProviderError("404")})
        )
        assert "Snapshot-ready       : NO" in report.render()
        assert "Nothing was imported" in report.render()

    def test_the_size_estimate_is_labelled_as_an_estimate(self) -> None:
        text = estimate_size(["SPY"] * 85, dt.date(2010, 1, 1), dt.date(2026, 1, 1))
        assert "estimate" in text.lower()
        assert "Actual sizes are reported" in text

    def test_the_report_payload_is_json_serialisable(self, tmp_path: Path) -> None:
        report = run_acquisition(tmp_path / "pkg", ["SPY"])
        assert json.loads(json.dumps(report.to_payload()))["status"]

    def test_an_end_before_the_start_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="before start"):
            AcquisitionOptions(start=dt.date(2020, 1, 1), end=dt.date(2019, 1, 1))
