"""``/plan`` — what each portfolio should do today, and what it refused.

**This is the route the dashboard exists for**, and the one it did not have.
What was built first was a stock research browser: prices, fundamentals,
filings, provenance. Useful, and an answer to the wrong question. CLAUDE.md
says TradeIt behaves *"like a portfolio manager rather than a stock picker"*,
and that *"a great stock"* and *"a great trade for this portfolio right now"*
are different questions of which **only the second may authorise a trade**.
Every route written before this one answered the first.

**The refusals are the payload's reason for existing.** A screen that shows
only what a system would buy cannot be checked: it looks identical whether the
limits are working or absent. So each refusal carries the limits that bound it,
every one of them rather than the first, and the standing it would have had —
which is what makes a cap's cost legible instead of invisible.

**Absence is reported, not omitted.** Four controls exist in this codebase and
cannot fire as currently wired, and a plan that listed only what fired would be
indistinguishable from one where everything was armed. The audit is measured
from the cycle's own wiring, so it stops claiming an absence the moment one is
fixed.

A horizon that cannot be planned returns its **reason** rather than vanishing
from the response. Day declines because its primary corpus does not exist, and
a client that simply received two horizons instead of three would have no way
to tell that from a Day portfolio nobody had declared.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from tradeit.decide import (
    DeclarationError,
    DeclaredPortfolio,
    PlanResult,
    SessionPlan,
    load_declaration,
    plan_declared,
)

__all__ = ["plan_payload", "serialise_plan"]


def serialise_plan(plan: SessionPlan) -> dict[str, Any]:
    """One horizon's plan as JSON, with every figure a number the client formats."""
    return {
        "mandate": plan.mandate,
        "timeframe": plan.timeframe,
        "session_date": str(plan.session_date),
        "equity": float(plan.equity),
        "cash": float(plan.cash),
        "heat": plan.heat,
        "position_count": plan.position_count,
        "candidates_considered": plan.candidates_considered,
        "capital_deployed": float(plan.capital_deployed),
        "holdings": [
            {
                "instrument_id": holding.instrument_id,
                "reference": holding.reference,
                "quantity": float(holding.quantity),
                "average_entry_price": float(holding.average_entry_price),
                "last_price": float(holding.last_price),
                "stop_price": (
                    float(holding.stop_price) if holding.stop_price is not None else None
                ),
                "market_value": float(holding.market_value),
                "open_risk": float(holding.open_risk),
                "weight": holding.weight,
            }
            for holding in plan.holdings
        ],
        "would_buy": [_entry(entry) for entry in plan.would_buy],
        "declined": [_entry(entry) for entry in plan.declined],
        "would_sell": [
            {
                "instrument_id": exit_.instrument_id,
                "reference": exit_.reference,
                "reason": exit_.reason,
                "fraction": float(exit_.fraction),
                "detail": exit_.detail,
            }
            for exit_ in plan.exits
        ],
        "refused": [
            {
                "instrument_id": refusal.instrument_id,
                "reference": refusal.reference,
                # Every limit that bound it. Reporting only the first would make
                # a second limit look slack when it is also binding.
                "reasons": list(refusal.reasons),
                "ranker": refusal.ranker,
                "standing": refusal.standing,
                "detail": refusal.detail,
            }
            for refusal in plan.refusals
        ],
        "not_armed": [{"name": control.name, "reason": control.reason} for control in plan.absent],
        "caveats": list(plan.caveats),
    }


def _entry(entry: Any) -> dict[str, Any]:
    return {
        "instrument_id": entry.instrument_id,
        "reference": entry.reference,
        "quantity": float(entry.quantity),
        "entry_price": float(entry.entry_price),
        "stop_price": float(entry.stop_price),
        "notional": float(entry.notional),
        "risk_amount": float(entry.risk_amount),
        "risk_fraction": float(entry.risk_fraction),
        "binding_constraint": entry.binding_constraint,
        "weight": entry.weight,
        "standalone": entry.standalone,
        "accepted": entry.accepted,
        "declined_by": entry.declined_by,
        "detail": entry.detail,
    }


def _declared(declared: DeclaredPortfolio) -> dict[str, Any]:
    """What the operator stated, echoed back so the plan is auditable.

    A plan whose inputs are not visible beside it cannot be checked: "why is
    equity 100,000?" has no answer on screen unless the declaration travels
    with the result.
    """
    return {
        "mandate": str(declared.mandate),
        # Null when the book supplies them: the record's figures travel in
        # `loaded.book`, and a declared figure beside them would be a second
        # answer to the same question.
        "equity": None if declared.equity is None else float(declared.equity),
        "cash": None if declared.cash is None else float(declared.cash),
        "book": (
            None
            if declared.book is None
            else {"record": str(declared.book.record), "portfolio": declared.book.portfolio}
        ),
        "universe_size": len(declared.universe),
        "warmup_from": str(declared.warmup_from),
        "as_of": str(declared.as_of),
        "holdings_declared": len(declared.holdings),
        "score_range": {"lower": declared.score_lower, "upper": declared.score_upper},
        "fast": declared.fast,
        "slow": declared.slow,
        "stop_pct": float(declared.stop_pct),
        "note": declared.note,
    }


def _loaded(result: PlanResult) -> dict[str, Any]:
    return {
        # Reported, never dropped silently: a universe quietly smaller than the
        # one declared produces a plan about a portfolio nobody described.
        "unresolved": list(result.unresolved),
        # Holdings marked at an older close. Carried, not refused — and
        # reported, because a stale mark changes what the heat figure means.
        "carried": [{"reference": ticker, "session": str(day)} for ticker, day in result.carried],
        # The rule fails closed on an unmeasured pair, so coverage is what makes
        # a plan that refuses everything explicable rather than broken.
        "correlation_pairs": result.correlation_pairs,
        "correlation_over_limit": result.correlation_over_limit,
        "sectors_classified": result.sectors_classified,
        "sessions_loaded": result.sessions_loaded,
        "bars_loaded": result.bars_loaded,
        "seconds": round(result.seconds, 2),
        "book": _book(result),
    }


def _book(result: PlanResult) -> dict[str, Any] | None:
    """Which recorded book the holdings came from, or null for a declaration."""
    book = result.book
    if book is None:
        return None
    return {
        "name": book.name,
        "portfolio_id": book.portfolio_id,
        "mode": book.mode,
        "last_session": str(book.last_session),
        "recorded_equity": float(book.recorded_equity),
        "marked_equity": float(book.marked_equity),
        "peak_equity": None if book.peak_equity is None else float(book.peak_equity),
        "positions": book.positions,
    }


def plan_payload(session: Session, declaration: Path) -> dict[str, Any]:
    """Every declared horizon, planned, with its reason if it cannot be.

    The declaration is re-read on each call rather than cached at startup, so
    editing the file takes effect without restarting the service. It is one
    small JSON read against a plan that loads hundreds of sessions.
    """
    try:
        declared = load_declaration(declaration)
    except DeclarationError as exc:
        return {
            "available": False,
            "declaration": str(declaration),
            "note": f"declaration refused: {exc}",
            "portfolios": [],
        }

    portfolios: list[dict[str, Any]] = []
    for entry in declared:
        try:
            result = plan_declared(session, entry)
        except DeclarationError as exc:
            # The horizon stays in the response carrying its reason. A client
            # that received two horizons instead of three could not tell a
            # declining Day from a Day nobody declared.
            portfolios.append(
                {
                    "mandate": str(entry.mandate),
                    "available": False,
                    "note": str(exc),
                    "declared": _declared(entry),
                }
            )
            continue
        portfolios.append(
            {
                "mandate": str(entry.mandate),
                "available": True,
                "declared": _declared(entry),
                "loaded": _loaded(result),
                "plan": serialise_plan(result.plan),
            }
        )

    return {
        "available": True,
        "declaration": str(declaration),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "portfolios": portfolios,
    }
