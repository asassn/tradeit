"""Vendor rows to package rows, preserving what the domain model cannot hold.

This is where a Tiingo price row becomes rows in four package datasets, and the
decisions worth stating are all about what is *kept* rather than what is
computed.

**Raw prices are the package's prices.** Tiingo returns both `open` and
`adjOpen`. The package's `open` column is the unadjusted one, because that is
what this platform stores and because adjusted history is a moving target —
today's adjusted series for a stock that split last week differs from the one
anybody could have seen before the split (ADR-0005). The adjusted columns are
not discarded: they go to a sidecar file alongside the package, so the project's
adjustment methodology can be *checked* against the vendor's rather than merely
agreeing with it by construction.

**Corporate actions come from the vendor's own columns.** `splitFactor` and
`divCash` are the vendor stating what happened. They are not inferred from
differences between adjusted and unadjusted prices — that inference is exactly
the thing worth refusing, because it cannot tell a split from a large dividend
from a data error.

**A split factor of 1 and a dividend of 0 are not events.** They appear on every
row of a normal price file, and emitting them would produce a splits dataset
with one row per session per symbol, most of them saying nothing happened.

**Instrument identity is ours.** The vendor's ticker is not the key. A stable
`instrument_id` is assigned from the sorted symbol list and recorded in
`symbol_mappings`, so a ticker change later does not silently follow the ticker
to whichever company holds it now.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from tradeit.acquisition.reconstruct import SplitFactorConvention
from tradeit.data.packages.spec import DatasetKind

#: How Tiingo's ``splitFactor`` is read: it is the share-count multiplier
#: directly, so no conversion happens. Declared so that "which way round is this
#: vendor?" has an answer in code for every provider rather than for the two
#: that happened to be audited.
SPLIT_FACTOR_CONVENTION = SplitFactorConvention.SHARE_COUNT_MULTIPLIER

#: A split factor within this distance of 1.0 is "no split". Vendors emit
#: 1.0 on ordinary sessions and occasionally 0.9999999999 through float
#: round-tripping; neither is an event.
SPLIT_EPSILON = Decimal("0.000001")

#: Columns written to the adjusted-price sidecar. Not part of the package
#: contract — the package stores raw prices — but retained so the platform's
#: own adjustment can be verified against the vendor's rather than trusted.
ADJUSTED_COLUMNS: tuple[str, ...] = (
    "instrument_id",
    "ticker",
    "session_date",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
    "split_factor",
    "div_cash",
)


@dataclass(slots=True)
class NormalizedRows:
    """Canonical rows per package dataset, plus the vendor sidecar."""

    rows: dict[DatasetKind, list[dict[str, str]]] = field(default_factory=dict)
    adjusted: list[dict[str, str]] = field(default_factory=list)
    #: Problems found while normalizing, reported rather than repaired.
    findings: list[str] = field(default_factory=list)

    def add(self, dataset: DatasetKind, row: dict[str, str]) -> None:
        self.rows.setdefault(dataset, []).append(row)

    def extend(self, other: NormalizedRows) -> None:
        for dataset, rows in other.rows.items():
            self.rows.setdefault(dataset, []).extend(rows)
        self.adjusted.extend(other.adjusted)
        self.findings.extend(other.findings)

    def count(self, dataset: DatasetKind) -> int:
        return len(self.rows.get(dataset, []))

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.rows.values())


def canonical_bar_row(
    *,
    instrument_id: int,
    session: dt.date,
    open_: Decimal | None,
    high: Decimal | None,
    low: Decimal | None,
    close: Decimal | None,
    volume: Decimal | None,
) -> dict[str, str] | None:
    """One daily bar in the package's canonical columns, or ``None``.

    Shared by every adapter, so a vendor's field names cannot reach the package
    and two providers cannot disagree about what a bar row looks like. Returns
    ``None`` when any of the four prices is missing — a bar without all four is
    not a bar, and the caller reports it rather than writing a partial row.
    """
    prices = {"open": open_, "high": high, "low": low, "close": close}
    if any(value is None for value in prices.values()):
        return None
    row = {
        "instrument_id": str(instrument_id),
        "session_date": session.isoformat(),
        "volume": _plain(volume) if volume is not None else "0",
    }
    for name, value in prices.items():
        assert value is not None  # narrowed above
        row[name] = _plain(value)
    return row


def assign_instrument_ids(symbols: Sequence[str], *, start_id: int = 1) -> dict[str, int]:
    """Stable surrogate keys, assigned from the sorted symbol list.

    Sorted rather than input-ordered so that acquiring the same universe twice
    — in a different order, or after a retry — assigns the same ids. An id that
    moved between runs would make two packages of the same data
    non-comparable.
    """
    return {symbol: start_id + i for i, symbol in enumerate(sorted(set(symbols)))}


def normalize_metadata(
    ticker: str,
    instrument_id: int,
    row: Mapping[str, Any],
    *,
    default_start: dt.date,
) -> NormalizedRows:
    """One vendor metadata object into instrument and symbol-mapping rows."""
    out = NormalizedRows()
    name = _text(row.get("name")) or ticker
    exchange = _text(row.get("exchangeCode")) or "UNKNOWN"
    start = _date(row.get("startDate"))
    end = _date(row.get("endDate"))

    # `endDate` is the vendor's last observation, not a delisting notice: an
    # actively traded symbol also has one, and it is yesterday. Only treat it
    # as a delisting when it is meaningfully in the past, and say in the
    # manifest that this is an inference rather than a vendor statement.
    delisted = end if end is not None and end < default_start else None

    out.add(
        DatasetKind.INSTRUMENTS,
        {
            "instrument_id": str(instrument_id),
            "name": name,
            "primary_exchange": exchange[:8],
            "asset_class": _asset_class(ticker, row),
            "country": _text(row.get("country")) or "US",
            "currency": "USD",
            "first_trade_date": start.isoformat() if start else "",
            "listing_status": "delisted" if delisted else "active",
            "delisted_date": delisted.isoformat() if delisted else "",
        },
    )
    out.add(
        DatasetKind.SYMBOL_MAPPINGS,
        {
            "instrument_id": str(instrument_id),
            "ticker": ticker,
            "valid_from": (start or default_start).isoformat(),
            "valid_to": "",
        },
    )
    if delisted:
        out.add(
            DatasetKind.DELISTINGS,
            {
                "instrument_id": str(instrument_id),
                "delisted_date": delisted.isoformat(),
                "reason": "",
            },
        )
    return out


def normalize_prices(
    ticker: str,
    instrument_id: int,
    rows: Iterable[Mapping[str, Any]],
) -> NormalizedRows:
    """One vendor price response into bars, splits, dividends and the sidecar.

    Rows the vendor sent that cannot be read as a bar are **reported, not
    dropped silently**: the finding names the session and what was wrong, and
    the runner puts it in the acquisition report. Rows that read fine but look
    odd are left entirely alone — deciding whether a zero-volume session is a
    halt or a data error is the importer's job, and it already has a documented
    policy for it that this must not pre-empt.
    """
    out = NormalizedRows()
    seen_sessions: set[dt.date] = set()

    for row in rows:
        session = _date(row.get("date"))
        if session is None:
            out.findings.append(f"{ticker}: a row has no date and was not written")
            continue
        if session in seen_sessions:
            out.findings.append(
                f"{ticker}: duplicate row for {session}; the second was not written"
            )
            continue

        prices = {name: _decimal(row.get(name)) for name in ("open", "high", "low", "close")}
        volume = _decimal(row.get("volume"))
        if any(value is None for value in prices.values()):
            missing = sorted(k for k, v in prices.items() if v is None)
            out.findings.append(f"{ticker} {session}: missing {missing}; row not written")
            continue

        seen_sessions.add(session)
        out.add(
            DatasetKind.DAILY_BARS,
            {
                "instrument_id": str(instrument_id),
                "session_date": session.isoformat(),
                "open": _plain(prices["open"]),
                "high": _plain(prices["high"]),
                "low": _plain(prices["low"]),
                "close": _plain(prices["close"]),
                "volume": _plain(volume) if volume is not None else "0",
            },
        )

        split = _decimal(row.get("splitFactor"))
        if split is not None and abs(split - 1) > SPLIT_EPSILON:
            if split <= 0:
                out.findings.append(
                    f"{ticker} {session}: splitFactor={split} is not positive; "
                    "not written as a split"
                )
            else:
                out.add(
                    DatasetKind.SPLITS,
                    {
                        "instrument_id": str(instrument_id),
                        "ex_date": session.isoformat(),
                        # Tiingo's `splitFactor` is already the share-count
                        # multiplier — 2.0 on the ex-date of a 2-for-1 — so this
                        # passes through unchanged. Declared rather than assumed,
                        # because the same field name means the reciprocal at
                        # another vendor and that mistake is invisible in the
                        # output.
                        "ratio": _plain(split),
                        "source_provider": "tiingo",
                        "vendor_factor": _plain(split),
                        "vendor_convention": str(SPLIT_FACTOR_CONVENTION),
                    },
                )

        dividend = _decimal(row.get("divCash"))
        if dividend is not None and dividend > 0:
            out.add(
                DatasetKind.DIVIDENDS,
                {
                    "instrument_id": str(instrument_id),
                    "ex_date": session.isoformat(),
                    "cash_amount": _plain(dividend),
                },
            )

        out.adjusted.append(
            {
                "instrument_id": str(instrument_id),
                "ticker": ticker,
                "session_date": session.isoformat(),
                "adj_open": _plain(_decimal(row.get("adjOpen"))),
                "adj_high": _plain(_decimal(row.get("adjHigh"))),
                "adj_low": _plain(_decimal(row.get("adjLow"))),
                "adj_close": _plain(_decimal(row.get("adjClose"))),
                "adj_volume": _plain(_decimal(row.get("adjVolume"))),
                "split_factor": _plain(split),
                "div_cash": _plain(dividend),
            }
        )

    return out


def _asset_class(ticker: str, row: Mapping[str, Any]) -> str:
    """Best available classification, defaulting honestly.

    Tiingo's metadata does not carry a security type, so an ETF and a common
    share are indistinguishable here. Rather than guess from the ticker — which
    would be a heuristic dressed as a fact — everything defaults to
    ``common_stock`` and the manifest records that asset classes are not
    vendor-supplied.
    """
    declared = _text(row.get("assetType")) or _text(row.get("asset_class"))
    return declared.lower().replace(" ", "_") if declared else "common_stock"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date(value: Any) -> dt.date | None:
    text = _text(value)
    if not text:
        return None
    # Tiingo timestamps look like 2020-01-02T00:00:00.000Z; the date is the
    # part before the T and the time component is always midnight UTC, so
    # parsing the whole thing and discarding the time would add a timezone
    # question to a field that does not have one.
    head = text.split("T")[0]
    try:
        return dt.date.fromisoformat(head)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    """Parse through ``str`` so a vendor float never becomes binary noise."""
    text = _text(value)
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _plain(value: Decimal | None) -> str:
    """Render without exponent notation, which the importer would refuse."""
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "ADJUSTED_COLUMNS",
    "SPLIT_EPSILON",
    "NormalizedRows",
    "assign_instrument_ids",
    "canonical_bar_row",
    "normalize_metadata",
    "normalize_prices",
]
