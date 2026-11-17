"""Valuation evidence: what a company costs, what it produces, and no verdict.

Phase 10 asks for *"a short human-readable verdict when browsing or selecting
a name, backed by multiples, the security's own valuation history, and
sector-relative comparison"*, under the condition that **the callout is never
shipped without the reasoning that produced it**.

**This package builds the reasoning and does not print the verdict.** That is
a decision, taken 2026-11-17 and recorded in
`docs/adr/0036-valuation-is-a-panel-not-a-verdict.md`, after building the
reasoning made the verdict look unsupportable: a multiple has no absolute
meaning without growth and risk, valuation is horizon-dependent so one label
across three horizons would be the global buy/sell number the architecture
exists to prevent, and the four fundamental signals measured on this corpus
have all failed. :func:`~tradeit.valuation.panel.panel_for` reports a free
cash flow yield on enterprise value, where it sits in the security's own
history, what growth the price implies at several required returns, and
whether cheapness arrives with deterioration.

An earlier revision of this package carried a ``ValuationVerdict`` that always
answered ``UNRESOLVED`` behind an ``UNAPPROVED`` threshold. The panel needs no
such gate: every number in it is measured or supplied by the caller, so there
is nothing left to approve.
"""

from __future__ import annotations

from tradeit.valuation.cashflow import (
    EnterpriseValue,
    FreeCashFlow,
    enterprise_value,
    free_cash_flow,
    implied_growth,
)
from tradeit.valuation.multiples import (
    Multiple,
    PriceBar,
    Unresolved,
    price_to_book,
    price_to_earnings,
    rank_within,
)
from tradeit.valuation.panel import (
    ImpliedGrowth,
    QualityFlag,
    ValuationPanel,
    panel_for,
)

__all__ = [
    "EnterpriseValue",
    "FreeCashFlow",
    "ImpliedGrowth",
    "Multiple",
    "PriceBar",
    "QualityFlag",
    "Unresolved",
    "ValuationPanel",
    "enterprise_value",
    "free_cash_flow",
    "implied_growth",
    "panel_for",
    "price_to_book",
    "price_to_earnings",
    "rank_within",
]
