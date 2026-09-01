"""Landing corporate actions -- the dependency adjusted prices rest on.

Not an enrichment. ``pit.py`` establishes that a **vendor-delivered** adjusted
series is stamped at delivery and so is invisible to any earlier as-of, which
leaves exactly one route to an adjusted series valid at a past instant:
**derive it ourselves from raw bars plus the actions known at that instant.**
This table is what makes that possible, so it sits on the critical path for any
historical backtest rather than beside it.

Resolution is the same per-date question the price importer asks, for the same
reason: an action belongs to whichever security held the ticker on its ex-date,
and asking per date is what stops a reused ticker attaching one company's split
to another's price history.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01.importer import (
    Delivery,
    ImportResult,
    RejectedBar,
    RejectReason,
    Resolution,
    resolve_security,
)
from tradeit.research01.pit import action_knowledge_time_for
from tradeit.storage.tables import SecurityCorporateActionFact

__all__ = ["VendorAction", "import_corporate_actions"]

#: What each action type must carry. A row missing its own required value is
#: rejected rather than defaulted -- a split with an implied ratio of 1.0 is a
#: split that does nothing, and it would pass every check downstream.
_REQUIRES: dict[str, str] = {
    "split": "ratio",
    "reverse_split": "ratio",
    "cash_dividend": "cash_amount",
    "special_dividend": "cash_amount",
}


@dataclass(frozen=True, slots=True)
class VendorAction:
    """One corporate action as the vendor sent it, before identity is attached."""

    ticker: str
    action_type: str
    ex_date: dt.date
    ratio: Decimal | None = None
    cash_amount: Decimal | None = None
    currency: str = "USD"


def import_corporate_actions(
    session: Session,
    actions: list[VendorAction],
    delivery: Delivery,
    *,
    alias_kind: str = "ticker",
) -> ImportResult:
    """Land actions that resolve and are complete; report the rest.

    Idempotent on ``(security, type, ex_date, knowledge_time)``, so replaying a
    delivery inserts nothing.
    """
    result = ImportResult()
    for action in actions:
        security_id, resolution = resolve_security(
            session, ticker=action.ticker, on=action.ex_date, alias_kind=alias_kind
        )
        if resolution is Resolution.UNRESOLVED_NO_ALIAS:
            result.rejected.append(
                RejectedBar(
                    action,
                    RejectReason.NO_ALIAS,
                    action.ticker,
                    f"no security evidenced for {action.ticker!r} on {action.ex_date}",
                )
            )
            continue
        if resolution is Resolution.UNRESOLVED_AMBIGUOUS:
            result.rejected.append(
                RejectedBar(
                    action,
                    RejectReason.AMBIGUOUS_ALIAS,
                    action.ticker,
                    f"{action.ticker!r} claimed by more than one security on {action.ex_date}",
                )
            )
            continue

        required = _REQUIRES.get(action.action_type)
        if required is not None and getattr(action, required) is None:
            result.rejected.append(
                RejectedBar(
                    action,
                    RejectReason.INCOMPLETE,
                    action.ticker,
                    f"{action.action_type} carries no {required}",
                )
            )
            continue

        assert security_id is not None
        knowledge_time, _basis = action_knowledge_time_for(
            ex_date=action.ex_date, delivered_at=delivery.delivered_at
        )
        # `_basis` is computed and not stored: security_corporate_action_facts
        # has no knowledge_time_basis column, because unlike a price bar an
        # action has only two routes (session close, or delivery when the
        # ex-date is not a session) and the ex_date itself distinguishes them.
        # If a third route ever appears -- an announcement instant -- this
        # needs the column that security_price_facts already has.
        # The ex-date IS the event. Using it for both instants is what keeps
        # knowledge_time >= event_time true without backdating anything; see
        # action_knowledge_time_for for the announcement question this defers.
        event_time = knowledge_time

        if session.scalars(
            select(SecurityCorporateActionFact.id).where(
                SecurityCorporateActionFact.security_id == security_id,
                SecurityCorporateActionFact.action_type == action.action_type,
                SecurityCorporateActionFact.ex_date == action.ex_date,
                SecurityCorporateActionFact.knowledge_time == knowledge_time,
            )
        ).first():
            result.rejected.append(
                RejectedBar(
                    action,
                    RejectReason.DUPLICATE,
                    action.ticker,
                    "already present at this revision",
                )
            )
            continue

        session.add(
            SecurityCorporateActionFact(
                security_id=security_id,
                action_type=action.action_type,
                ex_date=action.ex_date,
                event_time=event_time,
                knowledge_time=knowledge_time,
                knowledge_source=delivery.vendor,
                ratio=action.ratio,
                cash_amount=action.cash_amount,
                currency=action.currency,
                source=delivery.filename or delivery.vendor,
            )
        )
        result.landed += 1
        result.securities_touched.add(security_id)
    session.flush()
    return result
