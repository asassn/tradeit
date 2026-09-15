"""Sharadar SEP rows into the vendor-neutral bar and action shapes.

**Why the raw bar is reconstructed, and why that is exact.** Sharadar's ``stocks``
table publishes ``open``/``high``/``low``/``close`` and ``volume`` adjusted for
splits, ``closeadj`` adjusted for splits and dividends, and ``closeunadj`` -- the
close actually printed. The printed OHLC is not published, but it is not lost:
every split-adjusted field on a session was divided by the same cumulative split
factor, so

    factor      = closeunadj / close
    raw price   = adjusted price x factor
    raw volume  = adjusted volume / factor

Measured 2026-09-14 before this was written, on THQ's 1-for-10 reverse split
of 2012-07-09: ``closeunadj`` 0.52 against ``close`` 5.20 the session before
(factor 0.1) and equal on the ex-date, with volume 46,670 before -- 466,700
printed. The factor steps only on split dates, so nothing about a later split
leaks backwards into a raw bar.

**Why Sharadar's raw and not EODHD's.** Test 5 of
``docs/PROPOSAL_SURVIVORSHIP_DATA_2026-09-14.md``: at a sample of 533 recorded
splits, the EODHD close this corpus stores as ``raw`` jumped by the split ratio
at 65%, did not jump at 14%, and was ambiguous at 21% -- so that feed's ``raw``
is sometimes already split-adjusted. Sharadar's ``closeunadj`` jumped at every
split checked. It is the one to trust for this basis.

**Dividends are not parsed.** Whether Sharadar's dividend ``value`` is the
amount declared or one restated for later splits has not been established, and
a restated amount is a look-ahead through an ordinary-looking number -- the
reason ``eodhd_client.parse_dividends`` takes ``unadjustedValue`` only. Until it
is measured, dividends stay out rather than being guessed at.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from tradeit.research01.actions import VendorAction
from tradeit.research01.importer import VendorBar

__all__ = ["parse_sep_bars", "parse_splits"]

_PRICE_PLACES = Decimal("0.000001")
_VOLUME_PLACES = Decimal("1")


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def parse_sep_bars(ticker: str, rows: Sequence[dict[str, Any]]) -> list[VendorBar]:
    """A ``raw`` bar reconstructed from ``closeunadj``, and a ``total`` bar.

    ``ticker`` is the symbol the bars are **labelled** with, which the caller
    chooses from curated identity; Sharadar's own uniquified symbol (``CVNS1``)
    would resolve to nothing. A row missing any field the reconstruction needs,
    or with a non-positive close, is dropped rather than repaired: a bar with an
    invented price is worse than a missing one because it looks usable.
    """
    out: list[VendorBar] = []
    for row in rows:
        session_date = _date(row.get("date"))
        o, h, low, close = (_dec(row.get(k)) for k in ("open", "high", "low", "close"))
        volume, unadj, total = (
            _dec(row.get("volume")),
            _dec(row.get("closeunadj")),
            _dec(row.get("closeadj")),
        )
        if session_date is None or None in (o, h, low, close, volume, unadj):
            continue
        assert o is not None and h is not None and low is not None and close is not None
        assert volume is not None and unadj is not None
        if close <= 0 or unadj <= 0:
            continue
        factor = unadj / close
        out.append(
            VendorBar(
                ticker=ticker,
                session_date=session_date,
                open=(o * factor).quantize(_PRICE_PLACES),
                high=(h * factor).quantize(_PRICE_PLACES),
                low=(low * factor).quantize(_PRICE_PLACES),
                close=unadj,
                volume=(volume / factor).quantize(_VOLUME_PLACES),
                adjustment_basis="raw",
            )
        )
        if total is not None and total > 0:
            # As eodhd_client does: only the close is fully adjusted, so the
            # total bar carries it in every price field rather than mixing bases.
            out.append(
                VendorBar(
                    ticker=ticker,
                    session_date=session_date,
                    open=total,
                    high=total,
                    low=total,
                    close=total,
                    volume=(volume / factor).quantize(_VOLUME_PLACES),
                    adjustment_basis="total",
                    volume_adjusted=False,
                )
            )
    return out


def parse_splits(ticker: str, rows: Sequence[dict[str, Any]]) -> list[VendorAction]:
    """``action == "split"`` rows. ``value`` is new shares per old: 1.5 is 3-for-2,
    0.1 is 1-for-10 -- the convention ``eodhd_client.parse_splits`` produces.

    Non-positive, missing or exactly-1 ratios are dropped, never defaulted: a
    split of 1 does nothing and would pass every downstream check.
    """
    out: list[VendorAction] = []
    for row in rows:
        if str(row.get("action", "")).lower() != "split":
            continue
        ex_date, ratio = _date(row.get("date")), _dec(row.get("value"))
        if ex_date is None or ratio is None or ratio <= 0 or ratio == 1:
            continue
        out.append(VendorAction(ticker=ticker, action_type="split", ex_date=ex_date, ratio=ratio))
    return out
