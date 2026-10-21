"""The EODHD adapter, against responses the live API actually returned.

The stub this replaces refused to fetch for a stated reason: *"completing it
would mean guessing this vendor's URL shapes, parameter names, response fields
and error semantics from memory, and then writing tests against fixtures built
from the same guesses — which would produce a green suite that proves nothing."*

So the fixtures here are **captured from the real API**, with the key stripped
and that stripping verified at capture time. They are not hand-written, and the
field names in them are the field names the adapter reads.

What these tests are most concerned with is not that it works. It is that the
adapter does not overclaim: this vendor has two measured defects and an unknown
adjustment basis, and an adapter that reported itself as clean would be worse
than the stub it replaces.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from tradeit.acquisition.base import AcquisitionDataset, FetchRequest, FetchStatus
from tradeit.acquisition.eodhd import BASE_URL, EodhdAcquisition
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eodhd"
START, END = dt.date(2026, 10, 13), dt.date(2026, 10, 16)


class _StubTransport:
    """Returns a captured body, and records the URL it was asked for."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> bytes:
        self.urls.append(url)
        return self.body


def _adapter(fixture: str) -> tuple[EodhdAcquisition, _StubTransport]:
    transport = _StubTransport((FIXTURES / fixture).read_bytes())
    return EodhdAcquisition(token="test-token", transport=transport), transport


def _request(dataset: AcquisitionDataset, symbol: str = "AAPL") -> FetchRequest:
    return FetchRequest.one(dataset, symbol, START, END)


def test_the_adjustment_basis_is_unknown_not_raw() -> None:
    """**The most important assertion in this file.**

    EODHD serves `close` beside `adjusted_close`, so the obvious reading is
    that `close` is the exchange print. §0.1a measured that some EODHD `raw`
    closes are **already split-adjusted**, so applying the recorded split
    adjusts twice. A vendor that is usually raw is not raw, and this field
    exists because a wrong answer to it is invisible afterwards.
    """
    adapter, _ = _adapter("eod_aapl.json")
    assert adapter.adjustment_policy() is AdjustmentPolicyDeclaration.UNKNOWN
    assert adapter.adjustment_policy() is not AdjustmentPolicyDeclaration.RAW_UNADJUSTED


def test_the_measured_limitations_are_declared() -> None:
    """Both are from the vendor matrix and both travel with every result."""
    adapter, _ = _adapter("eod_aapl.json")
    text = " ".join(adapter.limitations()).lower()
    assert "one series per ticker" in text, "the reused-ticker limit is not declared"
    assert "incomplete" in text, "the corporate-action gap is not declared"
    assert "unknown" in text, "the adjustment basis is not declared"


def test_daily_prices_parse_from_a_real_response() -> None:
    adapter, _ = _adapter("eod_aapl.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    assert outcome.status is FetchStatus.OK
    assert len(outcome.rows) == 4
    assert set(outcome.rows[0]) >= {"date", "open", "high", "low", "close", "volume"}


def test_prices_normalise_through_the_shared_normaliser() -> None:
    """EODHD's field names match what `normalize_prices` reads, so no
    vendor-specific price parsing exists to drift."""
    adapter, _ = _adapter("eod_aapl.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    rows = adapter.normalize(outcome, {"AAPL": 7}).rows[DatasetKind.DAILY_BARS]
    assert len(rows) == 4
    assert rows[0]["instrument_id"] == "7"
    assert rows[0]["session_date"] == "2026-10-13"


def test_a_real_split_parses_to_its_ratio() -> None:
    """AAPL's 4-for-1 of 2020-08-31, as the vendor actually returned it."""
    adapter, _ = _adapter("splits_aapl_2020.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.CORPORATE_ACTIONS))
    normalized = adapter.normalize(outcome, {"AAPL": 7})
    rows = normalized.rows[DatasetKind.SPLITS]
    assert rows == [{"instrument_id": "7", "ex_date": "2020-08-31", "ratio": "4"}]
    assert not normalized.findings


def test_no_split_in_the_window_is_empty_not_an_error() -> None:
    """A security that did not split is not a failure to report one."""
    adapter, _ = _adapter("splits_empty.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.CORPORATE_ACTIONS))
    assert outcome.status is FetchStatus.EMPTY
    assert adapter.normalize(outcome, {"AAPL": 7}).rows == {}


@pytest.mark.parametrize(
    "raw",
    ["4.000000/0.000000", "not-a-ratio", "", "-2.0/1.0"],
)
def test_an_unreadable_split_is_reported_and_dropped_never_defaulted_to_one(
    raw: str,
) -> None:
    """A split silently read as 1 is a split that did not happen, and it leaves
    the price series uncorrected with nothing to show for it."""
    adapter, _ = _adapter("splits_empty.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.CORPORATE_ACTIONS))
    outcome = type(outcome)(
        request=outcome.request,
        status=FetchStatus.OK,
        rows=({"date": "2020-08-31", "split": raw},),
    )
    normalized = adapter.normalize(outcome, {"AAPL": 7})
    assert DatasetKind.SPLITS not in normalized.rows
    assert normalized.findings, f"{raw!r} was dropped without a finding"


def test_the_token_never_reaches_a_stored_url() -> None:
    """The URL is journalled and the journal gets shared."""
    adapter, transport = _adapter("eod_aapl.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    assert "test-token" in transport.urls[0], "the token must reach the vendor"
    assert "test-token" not in outcome.url, "the token reached the stored URL"
    assert "REDACTED" in outcome.url


def test_the_url_matches_the_shapes_the_research_client_already_uses() -> None:
    """Not guessed. `research01.eodhd_client` has used these paths against the
    live API for 854,718 bars."""
    from tradeit.research01.eodhd_client import BASE as RESEARCH_BASE

    assert BASE_URL == RESEARCH_BASE, "two constants for one base URL have drifted"
    adapter, transport = _adapter("eod_aapl.json")
    adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    assert transport.urls[0].startswith(f"{BASE_URL}/eod/AAPL?")
    assert "from=2026-10-13" in transport.urls[0]
    assert "to=2026-10-16" in transport.urls[0]


def test_planning_asks_for_prices_and_splits_separately() -> None:
    """EODHD serves corporate actions from dedicated endpoints rather than
    inline, which is the shape the stub existed to prove the interface
    accommodates."""
    adapter, _ = _adapter("eod_aapl.json")
    requests = adapter.plan(["AAPL", "MSFT"], START, END)
    datasets = {(r.symbol, r.dataset) for r in requests}
    assert ("AAPL", AcquisitionDataset.DAILY_PRICES) in datasets
    assert ("AAPL", AcquisitionDataset.CORPORATE_ACTIONS) in datasets
    assert len(requests) == 4


def test_an_unsupported_dataset_is_refused_clearly() -> None:
    adapter, _ = _adapter("eod_aapl.json")
    outcome = adapter.fetch(_request(AcquisitionDataset.SYMBOL_META))
    assert outcome.status is FetchStatus.REJECTED
    assert "not implemented" in str(outcome.error).lower()


def test_without_a_credential_it_refuses_rather_than_calling() -> None:
    transport = _StubTransport(b"[]")
    adapter = EodhdAcquisition(token="", transport=transport)
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    assert outcome.status is FetchStatus.REJECTED
    assert transport.urls == [], "it called the vendor with no credential"


def test_a_malformed_body_is_reported_as_malformed_not_empty() -> None:
    """ "The vendor sent something unparseable" and "the vendor has no data" are
    different facts."""
    adapter = EodhdAcquisition(token="t", transport=_StubTransport(b"<html>nope"))
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    assert outcome.status is FetchStatus.MALFORMED
    assert outcome.status is not FetchStatus.EMPTY


def test_the_fixtures_carry_no_credential() -> None:
    """Captured from the live API, so this is checked rather than assumed."""
    for path in FIXTURES.glob("*.json"):
        body = path.read_text()
        assert "api_token" not in body
        json.loads(body)


def test_the_raw_close_is_read_not_the_adjusted_one() -> None:
    """Uses the fixture captured for exactly this, and which I nearly ignored.

    `tests/fixtures/eodhd/README.md` says of `eod_aapl_2020_split.json`:
    *"AAPL's August 2020 4-for-1 split is chosen deliberately: it is the window
    where `close` and `adjusted_close` diverge sharply, so a test that confused
    them cannot pass quietly."*

    The fixture I captured myself has `close == adjusted_close` on every row, so
    a reader that took the adjusted column would have passed against it in
    silence. This one cannot: on 2020-08-27 the close is 500.04 and the
    adjusted close is 121.1519.

    **This does not make the basis RAW.** §0.1a's finding stands and
    `adjustment_policy()` is still UNKNOWN — what is pinned here is only that
    the adapter reads the column it claims to read.
    """
    transport = _StubTransport((FIXTURES / "eod_aapl_2020_split.json").read_bytes())
    adapter = EodhdAcquisition(token="t", transport=transport)
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    rows = adapter.normalize(outcome, {"AAPL": 7}).rows[DatasetKind.DAILY_BARS]

    first = next(row for row in rows if row["session_date"] == "2020-08-27")
    assert first["close"] == "500.04", "the adjusted close was read instead of the raw one"
    assert first["close"] != "121.1519"


def test_the_split_window_shows_the_price_dropping_fourfold() -> None:
    """The sanity check that the fixture is what it claims to be.

    A 4-for-1 on 2020-08-31 means the raw close falls from ~499 to ~129 across
    it. If a future recapture quietly replaced this fixture with an adjusted
    series, that step would vanish and the test above would stop meaning
    anything.
    """
    transport = _StubTransport((FIXTURES / "eod_aapl_2020_split.json").read_bytes())
    adapter = EodhdAcquisition(token="t", transport=transport)
    outcome = adapter.fetch(_request(AcquisitionDataset.DAILY_PRICES))
    rows = {
        row["session_date"]: float(row["close"])
        for row in adapter.normalize(outcome, {"AAPL": 7}).rows[DatasetKind.DAILY_BARS]
    }
    before, after = rows["2020-08-28"], rows["2020-08-31"]
    assert before / after == pytest.approx(4, abs=0.2), (
        "the raw series does not step across the split; this fixture may have been "
        "replaced with an adjusted one"
    )
