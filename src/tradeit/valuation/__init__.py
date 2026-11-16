"""Valuation evidence: the multiples, and what they are cheap or dear against.

Phase 10 asks for *"a short human-readable verdict when browsing or selecting a
name, backed by multiples, the security's own valuation history, and
sector-relative comparison"*, and attaches a condition to it:

> **The callout is never shipped without the reasoning that produced it** --- a
> one-word label with no visible methodology is an opinion wearing a badge, and
> it would be acted on far more confidently than its evidence deserves.

This package builds the reasoning. **It does not produce the label**, and that
is a decision rather than an unfinished edge: see
:class:`~tradeit.valuation.evidence.ValuationEvidence`, which carries complete
evidence and still answers ``UNRESOLVED``, naming the approval it is waiting
for. The same machinery `tradeit.readiness` uses to declare a criterion whose
number is still owed --- ``ThresholdSource.UNAPPROVED`` --- is what holds the
gap open here, so it is visible on the screen instead of being filled with a
number nobody chose.
"""

from __future__ import annotations

from tradeit.valuation.evidence import (
    ValuationEvidence,
    ValuationVerdict,
    evidence_for,
)
from tradeit.valuation.multiples import (
    Multiple,
    PriceBar,
    Unresolved,
    price_to_book,
    price_to_earnings,
    rank_within,
)

__all__ = [
    "Multiple",
    "PriceBar",
    "Unresolved",
    "ValuationEvidence",
    "ValuationVerdict",
    "evidence_for",
    "price_to_book",
    "price_to_earnings",
    "rank_within",
]
