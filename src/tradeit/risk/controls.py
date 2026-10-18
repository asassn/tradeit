"""The controls a strategy cannot reach, enforced where orders leave.

The roadmap lists these among the things that *"sit outside a strategy's reach
[…] enforced above the strategy, not configured by it"*: kill switch,
stale-data protection, duplicate-order protection, and execution
reconciliation. The fourth was built in Phase 11. These are the other three.

**Why they are not `RiskRule`s.** That protocol is explicit: rules are *"pure
functions of (proposal, state, config)"* that *"do not read the database, do
not consult wall-clock time, and do not mutate anything — which is what lets a
backtest evaluate the exact rules that would run live."* All three controls here
need precisely what that forbids. A kill switch is operator state. Stale-data
protection is a fact about when data arrived. Duplicate-order protection is a
fact about what has already been sent.

Forcing them into `RiskRule` would have meant weakening the one sentence that
makes the risk layer backtestable. So they are a separate layer, consulted at
the execution boundary by
:class:`~tradeit.execution.service.BrokerExecutionService` — which is also the
right place: a kill switch that only stopped *sizing* would let a resting order
fill after it was thrown.

**They take an explicit `as_of` and never read the clock.** Same reason the
rules don't: a control that consulted wall-clock time could not be replayed,
and a guard nobody can replay is a guard nobody can test.

What is deliberately not here
-----------------------------

**No values are inferred.** The roadmap says of these controls that *"their
values need approval and are not inferred"*. ``StaleDataGuard`` therefore takes
its tolerance as a required argument with no default: a staleness limit nobody
chose is exactly the number that will be wrong on the day it matters.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tradeit.execution.base import OrderRequest

__all__ = [
    "ControlOutcome",
    "ControlRefusal",
    "DuplicateOrderGuard",
    "KillSwitch",
    "PreTradeControls",
    "StaleDataGuard",
]


class ControlOutcome(StrEnum):
    """Why an order was refused before it reached a venue."""

    KILL_SWITCH = "kill_switch"
    STALE_DATA = "stale_data"
    DUPLICATE_ORDER = "duplicate_order"


@dataclass(frozen=True, slots=True)
class ControlRefusal:
    """One control's refusal, with the facts that produced it."""

    outcome: ControlOutcome
    reason: str
    detail: str = ""

    def describe(self) -> str:
        return f"{self.outcome}: {self.reason}{f' ({self.detail})' if self.detail else ''}"


@dataclass
class KillSwitch:
    """Stops all order placement, and is not a config field.

    Two ways to throw it, and both exist on purpose:

    * :meth:`engage` in-process, for a system that has detected its own
      problem.
    * A **file on disk**, for an operator who needs to stop a running system
      and cannot wait for it to ask. Checked on every call rather than cached,
      because a switch that was read at startup is not a switch.

    **Disengaging is deliberate and requires a reason.** A kill switch that
    cleared itself, or that could be cleared by the next thing that happened to
    succeed, would not have stopped anything -- it would have paused it. This
    mirrors the sticky halt in the execution service for the same reason.
    """

    #: Where an operator can throw the switch without touching the process.
    #: Deliberately not inside the repository: a control that lives in the
    #: working tree can be committed, reverted, or lost in a checkout.
    path: Path | None = None
    _engaged: str | None = field(default=None, init=False)

    def engage(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("engaging the kill switch requires a reason; it will be read later")
        self._engaged = reason

    def disengage(self, *, cleared_by: str, note: str) -> None:
        """Clear it, as an act somebody performed.

        Does **not** clear a switch thrown by file: that one is the operator's,
        and a process clearing it would be overruling the person who threw it.
        """
        if not cleared_by.strip() or not note.strip():
            raise ValueError("clearing the kill switch requires who cleared it and why")
        self._engaged = None

    @property
    def engaged_reason(self) -> str | None:
        """Why it is thrown, or None. Reads the file every time."""
        if self._engaged is not None:
            return self._engaged
        if self.path is not None and self.path.exists():
            try:
                text = self.path.read_text().strip()
            except OSError as problem:
                # An unreadable switch file is treated as THROWN. Fail closed:
                # the alternative is a control that stops working exactly when
                # the filesystem is in trouble.
                return f"kill-switch file at {self.path} could not be read: {problem}"
            return text or f"kill-switch file present at {self.path}"
        return None

    def check(self) -> ControlRefusal | None:
        reason = self.engaged_reason
        if reason is None:
            return None
        return ControlRefusal(
            outcome=ControlOutcome.KILL_SWITCH,
            reason="order placement is stopped",
            detail=reason,
        )


@dataclass(frozen=True, slots=True)
class StaleDataGuard:
    """Refuses to trade on data that has stopped arriving.

    ``max_age`` has **no default**. The roadmap says these values need approval
    and are not inferred, and a staleness tolerance nobody chose is the number
    that will be wrong on the day the feed dies quietly.

    Judged against an ``as_of`` the caller supplies rather than wall-clock time,
    so a backtest and a live session evaluate the same guard.
    """

    max_age: dt.timedelta

    def __post_init__(self) -> None:
        if self.max_age <= dt.timedelta(0):
            raise ValueError(
                f"max_age must be positive, got {self.max_age}; a non-positive tolerance "
                "refuses every order including those on fresh data"
            )

    def check(
        self, instrument_id: int, last_seen: Mapping[int, dt.datetime], as_of: dt.datetime
    ) -> ControlRefusal | None:
        stamp = last_seen.get(instrument_id)
        if stamp is None:
            # Never seen is the most stale a thing can be, and the easiest case
            # to treat as absent rather than refused.
            return ControlRefusal(
                outcome=ControlOutcome.STALE_DATA,
                reason=f"no data has ever been seen for instrument {instrument_id}",
            )
        age = as_of - stamp
        if age > self.max_age:
            return ControlRefusal(
                outcome=ControlOutcome.STALE_DATA,
                reason=f"instrument {instrument_id} data is {age} old",
                detail=f"tolerance {self.max_age}, last seen {stamp.isoformat()}",
            )
        return None


@dataclass
class DuplicateOrderGuard:
    """Refuses an order that has already been sent, or its economic twin.

    Two different protections, and the second is the one that matters:

    * **The same `client_order_id`** is a retry, and a venue's idempotency
      already makes that safe. Refused here anyway so the retry does not depend
      on every venue implementing it.
    * **A different id for the same instrument, side and session** is the
      dangerous case: a system that decides twice, or a caller that loses track
      of what it sent, doubling a position while every individual order looks
      correct. This is what a venue's idempotency key cannot catch, because the
      ids differ.
    """

    _sent: dict[str, tuple[int, str, dt.date]] = field(default_factory=dict, init=False)

    def record(self, order: OrderRequest, session_date: dt.date) -> None:
        self._sent[order.client_order_id] = (
            order.instrument_id,
            str(order.side),
            session_date,
        )

    def check(self, order: OrderRequest, session_date: dt.date) -> ControlRefusal | None:
        if order.client_order_id in self._sent:
            return ControlRefusal(
                outcome=ControlOutcome.DUPLICATE_ORDER,
                reason=f"client_order_id {order.client_order_id!r} has already been sent",
            )
        signature = (order.instrument_id, str(order.side), session_date)
        for existing_id, existing in self._sent.items():
            if existing == signature:
                return ControlRefusal(
                    outcome=ControlOutcome.DUPLICATE_ORDER,
                    reason=(
                        f"an order for instrument {order.instrument_id} {order.side} on "
                        f"{session_date} was already sent"
                    ),
                    detail=f"as {existing_id!r}, under a different id",
                )
        return None


@dataclass
class PreTradeControls:
    """Every control an order passes before it leaves, and all of them run.

    **All checks run even after the first refusal.** A caller that stops at the
    first one learns the kill switch is thrown and not that the data is also
    three days old -- and the second fact is the one that explains the first.
    Cheap here, and the difference between a log line and a diagnosis.
    """

    kill_switch: KillSwitch | None = None
    stale_data: StaleDataGuard | None = None
    duplicates: DuplicateOrderGuard | None = None

    def check(
        self,
        order: OrderRequest,
        *,
        session_date: dt.date,
        as_of: dt.datetime,
        last_seen: Mapping[int, dt.datetime] | None = None,
    ) -> tuple[ControlRefusal, ...]:
        refusals: list[ControlRefusal] = []
        if self.kill_switch is not None:
            refusal = self.kill_switch.check()
            if refusal is not None:
                refusals.append(refusal)
        if self.stale_data is not None:
            refusal = self.stale_data.check(order.instrument_id, last_seen or {}, as_of)
            if refusal is not None:
                refusals.append(refusal)
        if self.duplicates is not None:
            refusal = self.duplicates.check(order, session_date)
            if refusal is not None:
                refusals.append(refusal)
        return tuple(refusals)

    def record(self, order: OrderRequest, session_date: dt.date) -> None:
        """Remember a placed order, so the next identical one is caught.

        Called by the execution service **after** a successful submission, not
        before: an order that was refused by the venue was not sent, and
        recording it would block the legitimate retry.
        """
        if self.duplicates is not None:
            self.duplicates.record(order, session_date)

    @property
    def configured(self) -> Sequence[str]:
        """Which controls are actually installed.

        Exists so a run can report that it had none, rather than a reader
        assuming protection that was never wired.
        """
        return tuple(
            name
            for name, control in (
                ("kill_switch", self.kill_switch),
                ("stale_data", self.stale_data),
                ("duplicates", self.duplicates),
            )
            if control is not None
        )
