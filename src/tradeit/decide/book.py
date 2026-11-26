"""Read a recorded book for planning, refusing what the record cannot vouch for.

Until 2026-11-24 nothing wrote a ``positions`` row, so ``tradeit plan`` read its
holdings from a JSON file the operator edited. ``scripts/paper_live_shape.py
--record`` now writes the book at every close, and this is the other end of
that wire: the plan for the next session starts from what the record says the
portfolio holds, rather than from what somebody last typed.

**What the record gives that a declaration could not.** The true high since
entry, sessions held and partial-profit flag — the three ladder fields a broker
cannot report. A declared holding carried none of them, so the plan
*approximated* the high from one bar and said so. From a book there is nothing
to approximate, and a position whose ladder was never recorded is **refused**,
exactly as `LiveShapedSession._adopt` refuses to resume it: a trailing stop
planned from a reset high is looser than the one in force.

**The plan advances the book by exactly one session, the way the driver does.**
`LiveShapedSession.run_session` increments ``sessions_held`` and raises the high
to the close *before* deciding, so the book written at the close of session D
holds the ladder as it stood when D was decided. Planning D+1 applies one more
step. Planning D again would re-decide a session already decided; planning
D+3 would skip two sessions in which no stop was checked. Both are refused
rather than reported, because each produces a plausible plan about a book that
does not exist.

**Orders in flight at the last close refuse the plan.** Since 2026-11-26 the
paper session writes its orders, so the record says which are still resting.
Each one fills at the planned session's bars *before* the driver decides, and
this plan does not simulate a fill — so planning over one would propose, for
example, selling a position the venue has already sold that morning. The
refusal names the orders and says to run the paper session instead.

A record written before then holds no orders at all, and for it the plan
cannot tell "nothing in flight" from "nothing recorded". Such a plan carries
:data:`IN_FLIGHT_CAVEAT` rather than a confidence it has no basis for.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.decide.declaration import BookRef, DeclarationError
from tradeit.execution.live_shape import LadderState
from tradeit.observability.portfolio_record import (
    RecordedOrder,
    last_recorded_session,
    load_ladder_state,
    load_open_orders,
    load_portfolio_state,
    portfolio_named,
    recorded_peak_equity,
)
from tradeit.portfolio.base import PortfolioState
from tradeit.storage.tables import Order

__all__ = ["IN_FLIGHT_CAVEAT", "RecordedBook", "open_record", "read_book"]

#: Carried by a plan whose record holds no orders. See the module docstring.
IN_FLIGHT_CAVEAT = (
    "This record holds no orders, so whether any was in flight at the last recorded "
    "close is unknown. One that was would fill before this session decides, and this "
    "plan does not see it."
)


@dataclass(frozen=True, slots=True)
class RecordedBook:
    """A portfolio as the record left it at the close of its last session."""

    portfolio_id: int
    name: str
    mode: str
    #: The last session with a snapshot. The plan must be for the session
    #: immediately after it.
    last_session: dt.date
    #: Cash and equity as recorded at that close; positions still open.
    state: PortfolioState
    ladder: dict[int, LadderState]
    peak_equity: Decimal | None
    #: Orders the record says are still resting at the venue.
    in_flight: tuple[RecordedOrder, ...]
    #: False for a record written before orders were recorded, which cannot
    #: say whether anything was in flight.
    orders_recorded: bool


def open_record(ref: BookRef) -> Session:
    """Open the record **read-only**, refusing a file that is not there.

    Checked before connecting because SQLite creates an empty database on
    connect: a mistyped path would otherwise produce "no such portfolio" about
    a file this call had just made, and leave it behind.
    """
    path = ref.record.expanduser().resolve()
    if not path.is_file():
        raise DeclarationError(f"no record at {path}, so the book {ref.portfolio!r} cannot be read")
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    return sessionmaker(bind=engine)()


def read_book(record: Session, name: str) -> RecordedBook:
    """The named book as last recorded, or a refusal that says what is missing.

    Three refusals, each because the alternative is a plan built on an
    invention:

    * **no portfolio of that name** — a typo must not plan an empty book;
    * **a portfolio with no snapshot** — "never recorded" and "holds nothing"
      lead to opposite decisions, and only the second is a state to plan from;
    * **an open position without its ladder or its stop** — see the module
      docstring.
    """
    row = portfolio_named(record, name)
    if row is None:
        raise DeclarationError(
            f"the record holds no portfolio named {name!r}; planning an empty book under "
            "that name would look exactly like a portfolio that holds nothing"
        )
    portfolio_id = int(row.id)
    last = last_recorded_session(record, portfolio_id)
    if last is None:
        raise DeclarationError(
            f"portfolio {name!r} has never been recorded at a close, which is a different "
            "fact from holding nothing and cannot be planned as though it were"
        )
    state = load_portfolio_state(
        record,
        portfolio_id,
        # The snapshot's own as_of is the close it describes; asking for the
        # end of that day finds it whatever hour the writer stamped.
        as_of=dt.datetime.combine(last, dt.time(23, 59, 59), tzinfo=dt.UTC),
    )
    if state is None:  # pragma: no cover - last_recorded_session found a snapshot
        raise DeclarationError(f"portfolio {name!r}: no snapshot readable at {last}")
    ladder = load_ladder_state(record, portfolio_id)
    for position in state.open_positions:
        if position.stop_price is None or position.initial_stop_price is None:
            raise DeclarationError(
                f"portfolio {name!r}: instrument {position.instrument_id} is open with no "
                "stop recorded, so its risk is undefined and it cannot be summed into heat"
            )
        if position.instrument_id not in ladder:
            raise DeclarationError(
                f"portfolio {name!r}: instrument {position.instrument_id} is open with no "
                "high since entry recorded. A trailing stop planned from a reset high is "
                "looser than the one in force, so it is refused rather than defaulted"
            )
    try:
        in_flight = load_open_orders(record, portfolio_id)
    except ValueError as exc:
        raise DeclarationError(f"portfolio {name!r}: {exc}") from exc
    order_rows = record.execute(
        select(func.count()).select_from(Order).where(Order.portfolio_id == portfolio_id)
    ).scalar_one()
    return RecordedBook(
        portfolio_id=portfolio_id,
        name=name,
        mode=str(row.mode),
        last_session=last,
        state=state,
        ladder=ladder,
        peak_equity=recorded_peak_equity(record, portfolio_id),
        in_flight=in_flight,
        orders_recorded=bool(order_rows),
    )
