"""A declared portfolio, and everything it refuses to guess.

Nothing in this repository writes a ``portfolios`` or ``positions`` row, so a
plan must get its portfolio from somewhere. Reading it from the empty tables
would produce a confident plan for an empty portfolio — indistinguishable from
a portfolio that genuinely holds nothing, which is the failure
``tradeit status`` had to be fixed for. So the operator declares it, and the
loader's whole job is to refuse a declaration that would need a guess.

**Every refusal below exists because the default would decide something
material while appearing to decide nothing.** A defaulted stop invents the
position's size, because risk-based sizing derives shares from the
entry-to-stop distance. A defaulted session count invents the holding's age and
suppresses the time stop that should close it. A defaulted score range invents
the mixture that ``standalone_weight`` claims to describe.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradeit.decide import DeclarationError, load_declaration
from tradeit.portfolio.mandate import Mandate


def _holding(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ticker": "IBM",
        "quantity": "400",
        "entry_price": "138.46",
        "stop_price": "110.00",
        "sessions_held": 20,
        "opened_on": "2015-09-01",
    }
    payload.update(overrides)
    return payload


def _portfolio(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mandate": "swing",
        "equity": "100000",
        "cash": "40000",
        "universe": ["IBM", "TXN", "AAPL"],
        "warmup_from": "2014-01-02",
        "as_of": "2015-11-27",
        "holdings": [_holding()],
        "score_range": {"lower": 0.0, "upper": 0.5},
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, *portfolios: dict[str, Any]) -> Path:
    path = tmp_path / "declaration.json"
    path.write_text(json.dumps({"portfolios": list(portfolios)}))
    return path


def test_a_complete_declaration_loads(tmp_path: Path) -> None:
    (declared,) = load_declaration(_write(tmp_path, _portfolio()))

    assert declared.mandate is Mandate.SWING
    assert declared.equity == Decimal(100_000)
    assert declared.cash == Decimal(40_000)
    assert declared.universe == ("IBM", "TXN", "AAPL")
    assert declared.as_of == dt.date(2015, 11, 27)
    assert declared.score_lower == 0.0
    assert declared.score_upper == 0.5
    holding = declared.holdings[0]
    assert holding.stop_price == Decimal("110.00")
    assert holding.sessions_held == 20


def test_each_horizon_is_declared_separately(tmp_path: Path) -> None:
    """Three portfolios, never collapsed — a ticker may differ by horizon."""
    path = _write(
        tmp_path,
        _portfolio(mandate="swing"),
        _portfolio(mandate="retirement", holdings=[]),
    )

    declared = load_declaration(path)

    assert [p.mandate for p in declared] == [Mandate.SWING, Mandate.RETIREMENT]


def test_the_same_mandate_declared_twice_is_refused(tmp_path: Path) -> None:
    """Which is current is a question the file must answer, not the loader."""
    path = _write(tmp_path, _portfolio(), _portfolio(cash="1000"))

    with pytest.raises(DeclarationError, match="more than once"):
        load_declaration(path)


# -- what it refuses to guess ----------------------------------------------


def test_a_holding_without_a_stop_is_refused(tmp_path: Path) -> None:
    broken = _holding()
    del broken["stop_price"]
    path = _write(tmp_path, _portfolio(holdings=[broken]))

    with pytest.raises(DeclarationError, match="stop_price is required"):
        load_declaration(path)


def test_a_holding_without_a_session_count_is_refused(tmp_path: Path) -> None:
    broken = _holding()
    del broken["sessions_held"]
    path = _write(tmp_path, _portfolio(holdings=[broken]))

    with pytest.raises(DeclarationError, match="sessions_held is required"):
        load_declaration(path)


@pytest.mark.parametrize("field", ["equity", "cash", "warmup_from", "as_of"])
def test_a_portfolio_missing_a_required_field_is_refused(tmp_path: Path, field: str) -> None:
    broken = _portfolio()
    del broken[field]
    path = _write(tmp_path, broken)

    with pytest.raises(DeclarationError, match=f"{field} is required"):
        load_declaration(path)


def test_a_missing_score_range_is_refused(tmp_path: Path) -> None:
    """The baseline's total is an unbounded spread, not a score."""
    path = _write(tmp_path, _portfolio(score_range={"lower": 0.0}))

    with pytest.raises(DeclarationError, match="score_range needs lower and upper"):
        load_declaration(path)


def test_an_inverted_score_range_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(score_range={"lower": 0.6, "upper": 0.2}))

    with pytest.raises(DeclarationError, match="score_upper must exceed"):
        load_declaration(path)


def test_an_empty_universe_is_refused(tmp_path: Path) -> None:
    """Loading every security would scan 71 million rows."""
    path = _write(tmp_path, _portfolio(universe=[]))

    with pytest.raises(DeclarationError, match="a universe is required"):
        load_declaration(path)


def test_a_holding_outside_the_universe_is_refused(tmp_path: Path) -> None:
    """It would have no prices to mark against, so no stop could be managed."""
    path = _write(tmp_path, _portfolio(holdings=[_holding(ticker="GOOG")]))

    with pytest.raises(DeclarationError, match="GOOG"):
        load_declaration(path)


def test_the_same_ticker_held_twice_is_refused(tmp_path: Path) -> None:
    """Two rows for one holding would split the pyramid count.

    The cycle merges an add into the existing position at a weighted-average
    entry for exactly this reason: counting entries is how the pyramid limit
    works, and two rows would let a third add through.
    """
    path = _write(tmp_path, _portfolio(holdings=[_holding(), _holding(quantity="100")]))

    with pytest.raises(DeclarationError, match="held twice"):
        load_declaration(path)


def test_an_as_of_before_the_warmup_is_refused(tmp_path: Path) -> None:
    """A crossover needs its slow window before it can cross."""
    path = _write(tmp_path, _portfolio(warmup_from="2015-11-27", as_of="2014-01-02"))

    with pytest.raises(DeclarationError, match="must be after warmup_from"):
        load_declaration(path)


def test_a_non_positive_equity_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(equity="0"))

    with pytest.raises(DeclarationError, match="equity must be positive"):
        load_declaration(path)


def test_negative_cash_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(cash="-1"))

    with pytest.raises(DeclarationError, match="cash cannot be negative"):
        load_declaration(path)


def test_a_fast_window_at_or_above_the_slow_one_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(fast=200, slow=50))

    with pytest.raises(DeclarationError, match="must be below slow"):
        load_declaration(path)


def test_an_unknown_mandate_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(mandate="scalping"))

    with pytest.raises(DeclarationError, match="unknown mandate"):
        load_declaration(path)


def test_a_non_numeric_amount_names_the_field(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(equity="lots"))

    with pytest.raises(DeclarationError, match="equity is not a number"):
        load_declaration(path)


def test_a_bad_date_names_the_field(tmp_path: Path) -> None:
    path = _write(tmp_path, _portfolio(as_of="last Tuesday"))

    with pytest.raises(DeclarationError, match="as_of is not an ISO date"):
        load_declaration(path)


# -- file-level problems ---------------------------------------------------


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(DeclarationError, match="no declaration at"):
        load_declaration(tmp_path / "absent.json")


def test_invalid_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "declaration.json"
    path.write_text("{not json")

    with pytest.raises(DeclarationError, match="not valid JSON"):
        load_declaration(path)


def test_a_file_without_a_portfolios_list_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "declaration.json"
    path.write_text(json.dumps({"swing": {}}))

    with pytest.raises(DeclarationError, match="'portfolios' list"):
        load_declaration(path)


def test_an_empty_portfolios_list_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "declaration.json"
    path.write_text(json.dumps({"portfolios": []}))

    with pytest.raises(DeclarationError, match="non-empty list"):
        load_declaration(path)


# ---------------------------------------------------------------------------
# A quiet holding is carried, not refused
#
# `EventDrivenEngine._context` carries a security that did not print today at
# its last known close, and says why: that "is not a guess -- it is the last
# thing anybody knew, and being unchanged from yesterday it cannot trigger a
# stop that yesterday did not". `plan_declared` refused the whole horizon in
# that case until 2026-11-19, which stalls every other position's stop over one
# quiet name. These pin the corrected behaviour at the declaration boundary;
# the carry itself is exercised against the corpus.
# ---------------------------------------------------------------------------


def test_a_declaration_may_hold_a_name_that_is_quiet_on_the_as_of_date(
    tmp_path: Path,
) -> None:
    """The loader does not know what printed; it must not pre-judge it.

    Whether a holding has a bar on the as-of date is a fact about the corpus,
    resolved by `plan_declared`. The declaration is valid either way, and a
    loader that refused here would make a quiet session a malformed file.
    """
    (declared,) = load_declaration(_write(tmp_path, _portfolio()))

    assert declared.holdings[0].ticker == "IBM"
    assert declared.as_of == dt.date(2015, 11, 27)
