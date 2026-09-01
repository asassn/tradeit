"""Turning EODHD responses into research-01 rows.

**Built against verified shapes, not remembered ones.** ``acquisition/eodhd.py``
was deliberately left unfinished with the reason recorded: guessing a vendor's
URL shapes and then testing against fixtures built from the same guesses
"would produce a green suite that proves nothing and an adapter that fails on
first contact with the real API". The endpoints and field names here were read
off live responses using EODHD's own public ``demo`` token, and the fixtures in
``tests/fixtures/eodhd/`` are those responses.

Two traps the real data exposed, both of which would pass silently:

**1. ``adjusted_close`` is the vendor's adjusted series.** It carries the same
delivery-epoch problem as any vendor adjustment -- AAPL's ``close`` of 500.04 on
2020-08-27 appears as ``adjusted_close`` 121.15 because of a split that happened
*four days later*. Ingesting it as the price on that date would put the future
into a 2020 bar. It is imported as a separate ``adjustment_basis`` row, which
``pit.py`` then stamps at delivery.

**2. A dividend's ``value`` is adjusted; ``unadjustedValue`` is what was
declared.** AAPL's 2020-02-07 dividend is ``value`` 0.1925 and
``unadjustedValue`` 0.77 — the same 4:1 split applied backwards. **The declared
amount is the historical fact**, so ``unadjustedValue`` is what is imported.
Taking ``value`` would inject the identical look-ahead through a field that
looks like a plain number.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from tradeit.errors import DataError
from tradeit.research01.actions import VendorAction
from tradeit.research01.importer import VendorBar

__all__ = [
    "EodhdClient",
    "HttpEodhdClient",
    "parse_bars",
    "parse_dividends",
    "parse_splits",
    "resolve_api_token",
]

BASE = "https://eodhd.com/api"

#: Checked in order. ``EODHD_API_KEY`` is this repository's convention, already
#: used by ``acquisition/eodhd.py``; ``EODHD_API_TOKEN`` is the name EODHD's own
#: documentation and plugin use. Both are accepted because a key that works
#: everywhere except here is a support question nobody should have to ask.
_TOKEN_VARS = ("EODHD_API_KEY", "EODHD_API_TOKEN")


def resolve_api_token(*, env_file: Path | None = None) -> str:
    """Find the API token, or say exactly how to provide one.

    Looks in the environment first, then in a ``.env`` file at the repository
    root. ``.env`` is gitignored, which is the reason it is the recommended
    home: a token in a stray text file is one ``git add .`` away from being
    published, and a published key cannot be unpublished.

    **The value is never logged, printed or returned in an error.** The failure
    message names the variable and not its contents.
    """
    for name in _TOKEN_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value

    path = env_file or Path(__file__).resolve().parents[3] / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() in _TOKEN_VARS:
                return value.strip().strip("\"'")

    raise DataError(
        "No EODHD API token found. Put it in the .env file at the repository "
        "root as EODHD_API_KEY=... (that file is gitignored), or export "
        "EODHD_API_KEY in your shell. The token itself is never logged."
    )


class EodhdClient(Protocol):
    """What the backfill needs. Implemented by HTTP and by test fakes alike."""

    def eod(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]: ...

    def splits(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]: ...

    def dividends(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class HttpEodhdClient:
    """Live client. **Never logs or stores the token**, and never defaults one."""

    api_token: str
    timeout: int = 60
    calls: int = 0

    def _get(self, path: str, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        if not self.api_token:
            raise DataError("EODHD api_token is empty; refusing to issue an unauthenticated call")
        query = urllib.parse.urlencode(
            {
                "api_token": self.api_token,
                "fmt": "json",
                "from": start.isoformat(),
                "to": end.isoformat(),
            }
        )
        url = f"{BASE}/{path}/{urllib.parse.quote(symbol)}?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "TradeIt research"})
        self.calls += 1
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise DataError(f"{path}/{symbol}: expected a list, got {type(payload).__name__}")
        return payload

    def eod(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        return self._get("eod", symbol, start, end)

    def splits(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        return self._get("splits", symbol, start, end)

    def dividends(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        return self._get("div", symbol, start, end)


def _date(raw: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def _dec(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _ticker(symbol: str) -> str:
    """``GM.US`` -> ``GM``; ``GM_old.US`` -> ``GM_old``.

    **The suffix is kept.** EODHD marks a reused ticker's earlier holder with
    ``_old``, so ``GM`` and ``GM_old`` are two different companies. Stripping it
    to make them match would splice exactly the pair the control universe exists
    to keep apart.
    """
    return symbol.rsplit(".", 1)[0] if "." in symbol else symbol


def parse_bars(symbol: str, rows: Sequence[dict[str, Any]]) -> list[VendorBar]:
    """Raw bars from ``close``, and adjusted bars from ``adjusted_close``.

    Both are emitted, as separate ``adjustment_basis`` rows, because the
    relationship between them is what recovers a corporate action -- and because
    the adjusted one must be stamped at delivery rather than at the session.
    """
    out: list[VendorBar] = []
    for row in rows:
        session_date = _date(row.get("date"))
        close = _dec(row.get("close"))
        if session_date is None or close is None:
            continue
        o, h, low, volume = (
            _dec(row.get("open")),
            _dec(row.get("high")),
            _dec(row.get("low")),
            _dec(row.get("volume")),
        )
        if o is None or h is None or low is None or volume is None:
            continue
        out.append(
            VendorBar(
                ticker=_ticker(symbol),
                session_date=session_date,
                open=o,
                high=h,
                low=low,
                close=close,
                volume=volume,
                adjustment_basis="raw",
            )
        )
        adjusted = _dec(row.get("adjusted_close"))
        if adjusted is not None:
            # Only the close is adjusted in this feed; OHLC are not restated, so
            # a "total" bar carries the adjusted close in every price field
            # rather than mixing one adjusted number with three raw ones.
            out.append(
                VendorBar(
                    ticker=_ticker(symbol),
                    session_date=session_date,
                    open=adjusted,
                    high=adjusted,
                    low=adjusted,
                    close=adjusted,
                    volume=volume,
                    adjustment_basis="total",
                    volume_adjusted=False,
                )
            )
    return out


def parse_splits(symbol: str, rows: Sequence[dict[str, Any]]) -> list[VendorAction]:
    """``{"date": "2020-08-31", "split": "4.000000/1.000000"}`` -> ratio 4.

    A malformed or zero-denominator ratio is **dropped, not defaulted to 1**: a
    split of 1 is a split that does nothing and would pass every check
    downstream while silently failing to adjust anything.
    """
    out: list[VendorAction] = []
    for row in rows:
        ex_date = _date(row.get("date"))
        raw = str(row.get("split", ""))
        if ex_date is None or "/" not in raw:
            continue
        numerator, _, denominator = raw.partition("/")
        top, bottom = _dec(numerator), _dec(denominator)
        if top is None or bottom is None or bottom == 0 or top <= 0:
            continue
        out.append(
            VendorAction(
                ticker=_ticker(symbol),
                action_type="split",
                ex_date=ex_date,
                ratio=top / bottom,
            )
        )
    return out


def parse_dividends(symbol: str, rows: Sequence[dict[str, Any]]) -> list[VendorAction]:
    """Takes ``unadjustedValue`` -- the amount actually declared.

    ``value`` is the same dividend restated for later splits, and using it would
    inject a look-ahead through a field that looks like an ordinary number.
    A row carrying only ``value`` is dropped rather than silently substituted.
    """
    out: list[VendorAction] = []
    for row in rows:
        ex_date = _date(row.get("date"))
        declared = _dec(row.get("unadjustedValue"))
        if ex_date is None or declared is None:
            continue
        out.append(
            VendorAction(
                ticker=_ticker(symbol),
                action_type="cash_dividend",
                ex_date=ex_date,
                cash_amount=declared,
                currency=str(row.get("currency") or "USD"),
            )
        )
    return out
