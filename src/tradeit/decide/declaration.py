"""A declared portfolio: what the operator says this horizon holds.

**Nothing in this repository writes a ``portfolios`` or ``positions`` row.**
The ORM classes exist and are created by ``Base.metadata.create_all``; they are
constructed nowhere outside ``tables.py``, and every run hardcodes
``portfolio_id=1`` pointing at a row that does not exist. So a plan needs its
portfolio from somewhere, and there are three places it could come from:

* **Storage.** Rejected: there is nothing there. Reading holdings from an empty
  table would produce a confident plan for an empty portfolio, which looks
  exactly like a portfolio that holds nothing — the failure
  ``tradeit status`` had to be fixed for, where every row read UNKNOWN because
  it was pointed at the wrong database without saying so.
* **Reconstruction from the decision record.** Defensible, and deferred: the
  record holds orders and executions, and ``reconstruct`` can walk them. But
  the record is of a *backtest* whose ranker had the portfolio awareness
  switched off, so its positions are the output of a decision process this
  work exists to replace. Planning today's trades against them would be
  reasoning from the thing being corrected.
* **A declaration the operator writes.** Chosen. It is honest about its own
  provenance, it is a file that can be versioned and diffed, and it is the only
  option that does not require inventing either a writer or a history.

**A holding without a stop is refused.** Risk-based sizing derives shares from
the entry-to-stop distance, portfolio heat is the sum of those distances, and
``PositionState`` states plainly that a position without a stop "has undefined
risk, cannot be sized, cannot be aggregated into portfolio heat, and is
therefore not a position this system will hold". Defaulting one would be
inventing the position's size and the portfolio's heat at once.

Every field is required for the same reason. There is no default equity, no
default cash, no default universe and no default as-of date, because each of
those would decide something material while appearing to decide nothing.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tradeit.portfolio.mandate import Mandate

__all__ = [
    "DeclarationError",
    "DeclaredHolding",
    "DeclaredPortfolio",
    "load_declaration",
]


class DeclarationError(ValueError):
    """A declaration that cannot be read without inventing something."""


@dataclass(frozen=True, slots=True)
class DeclaredHolding:
    """One open position, as the operator states it."""

    ticker: str
    quantity: Decimal
    entry_price: Decimal
    #: Required. See the module docstring: there is no honest default.
    stop_price: Decimal
    #: Sessions held, for the time stop. Required for the same reason — a
    #: default of zero would make every holding look freshly opened and
    #: suppress the time stop that should close it.
    sessions_held: int
    opened_on: dt.date


@dataclass(frozen=True, slots=True)
class DeclaredPortfolio:
    """One horizon's portfolio and the window to plan it over."""

    mandate: Mandate
    equity: Decimal
    cash: Decimal
    universe: tuple[str, ...]
    #: Where the candidate source starts accumulating history. A moving-average
    #: crossover needs its slow window before it can cross, so a warmup shorter
    #: than that produces a session with no candidates and no explanation.
    warmup_from: dt.date
    as_of: dt.date
    holdings: tuple[DeclaredHolding, ...]
    #: The declared bounds of the raw setup score. Required, because the
    #: baseline puts an unbounded moving-average spread in
    #: ``OpportunityScore.total`` and blending that with a fit score at a
    #: declared weight would otherwise describe a mixture that is not happening.
    score_lower: float
    score_upper: float
    fast: int
    slow: int
    stop_pct: Decimal
    note: str = ""

    def __post_init__(self) -> None:
        if self.equity <= 0:
            raise DeclarationError(f"{self.mandate}: equity must be positive, got {self.equity}")
        if self.cash < 0:
            raise DeclarationError(f"{self.mandate}: cash cannot be negative, got {self.cash}")
        if not self.universe:
            raise DeclarationError(
                f"{self.mandate}: a universe is required. Loading every security would scan "
                "71 million rows, because the price index leads with security_id"
            )
        if self.as_of <= self.warmup_from:
            raise DeclarationError(
                f"{self.mandate}: as_of {self.as_of} must be after warmup_from "
                f"{self.warmup_from}; the candidate source needs history before it can signal"
            )
        if self.score_upper <= self.score_lower:
            raise DeclarationError(
                f"{self.mandate}: score_upper must exceed score_lower, got "
                f"{self.score_lower} and {self.score_upper}"
            )
        if self.fast >= self.slow:
            raise DeclarationError(
                f"{self.mandate}: fast {self.fast} must be below slow {self.slow}"
            )
        held = [holding.ticker for holding in self.holdings]
        if len(set(held)) != len(held):
            raise DeclarationError(
                f"{self.mandate}: the same ticker is declared held twice. Two rows for one "
                "holding would split the pyramid count and let a further add through"
            )
        outside = sorted(set(held) - set(self.universe))
        if outside:
            raise DeclarationError(
                f"{self.mandate}: {', '.join(outside)} declared held but not in the universe, "
                "so there would be no prices to mark against and no stop could be managed"
            )


def _decimal(payload: dict[str, Any], key: str, where: str) -> Decimal:
    if key not in payload:
        raise DeclarationError(f"{where}: {key} is required and has no default")
    try:
        return Decimal(str(payload[key]))
    except (InvalidOperation, ValueError) as exc:
        raise DeclarationError(f"{where}: {key} is not a number: {payload[key]!r}") from exc


def _date(payload: dict[str, Any], key: str, where: str) -> dt.date:
    if key not in payload:
        raise DeclarationError(f"{where}: {key} is required and has no default")
    try:
        return dt.date.fromisoformat(str(payload[key]))
    except ValueError as exc:
        raise DeclarationError(f"{where}: {key} is not an ISO date: {payload[key]!r}") from exc


def _holding(payload: dict[str, Any], where: str) -> DeclaredHolding:
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise DeclarationError(f"{where}: a holding needs a ticker")
    spot = f"{where} holding {ticker}"
    if "stop_price" not in payload:
        raise DeclarationError(
            f"{spot}: stop_price is required. A position without a stop has undefined risk, "
            "cannot be sized and cannot be summed into portfolio heat — defaulting one would "
            "invent both the position's size and the portfolio's heat"
        )
    if "sessions_held" not in payload:
        raise DeclarationError(
            f"{spot}: sessions_held is required. Defaulting it to zero would make every "
            "holding look freshly opened and suppress the time stop that should close it"
        )
    return DeclaredHolding(
        ticker=ticker,
        quantity=_decimal(payload, "quantity", spot),
        entry_price=_decimal(payload, "entry_price", spot),
        stop_price=_decimal(payload, "stop_price", spot),
        sessions_held=int(payload["sessions_held"]),
        opened_on=_date(payload, "opened_on", spot),
    )


def _portfolio(payload: dict[str, Any]) -> DeclaredPortfolio:
    raw = str(payload.get("mandate", "")).strip().lower()
    try:
        mandate = Mandate(raw)
    except ValueError as exc:
        known = ", ".join(str(m) for m in Mandate)
        raise DeclarationError(f"unknown mandate {raw!r}; expected one of {known}") from exc
    where = f"mandate {mandate}"
    universe = tuple(
        dict.fromkeys(str(t).strip().upper() for t in payload.get("universe", []) if str(t).strip())
    )
    scores = payload.get("score_range") or {}
    if "lower" not in scores or "upper" not in scores:
        raise DeclarationError(
            f"{where}: score_range needs lower and upper. The baseline puts an unbounded "
            "moving-average spread in OpportunityScore.total, and blending that with a fit "
            "score at a declared weight would describe a mixture that is not happening"
        )
    return DeclaredPortfolio(
        mandate=mandate,
        equity=_decimal(payload, "equity", where),
        cash=_decimal(payload, "cash", where),
        universe=universe,
        warmup_from=_date(payload, "warmup_from", where),
        as_of=_date(payload, "as_of", where),
        holdings=tuple(_holding(h, where) for h in payload.get("holdings", [])),
        score_lower=float(scores["lower"]),
        score_upper=float(scores["upper"]),
        fast=int(payload.get("fast", 50)),
        slow=int(payload.get("slow", 200)),
        stop_pct=Decimal(str(payload.get("stop_pct", "0.08"))),
        note=str(payload.get("note", "")),
    )


def load_declaration(path: Path) -> tuple[DeclaredPortfolio, ...]:
    """Read a declaration file, refusing anything that would need a guess.

    Returns one :class:`DeclaredPortfolio` per horizon, in the file's order. A
    mandate declared twice is an error rather than a silent last-wins, because
    two declarations for one horizon is a question about which is current and
    nothing here can answer it.
    """
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise DeclarationError(f"no declaration at {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "portfolios" not in payload:
        raise DeclarationError(
            f"{path} must be an object with a 'portfolios' list, one entry per horizon"
        )
    entries = payload["portfolios"]
    if not isinstance(entries, list) or not entries:
        raise DeclarationError(f"{path}: 'portfolios' must be a non-empty list")

    portfolios = tuple(_portfolio(entry) for entry in entries)
    seen = [p.mandate for p in portfolios]
    duplicates = sorted({str(m) for m in seen if seen.count(m) > 1})
    if duplicates:
        raise DeclarationError(
            f"{path}: {', '.join(duplicates)} declared more than once. Which one is current "
            "is a question this file has to answer, not one the loader may choose"
        )
    return portfolios
