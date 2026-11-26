"""The decision path, composed and run.

``tradeit.opportunity`` separates the three scores and ``tradeit.portfolio``
decides and constrains. This package is where they are put together and
actually executed for one horizon and one session — the step that had never
been taken, which is why no refusal in the stored decision record could name
the limit that caused it.
"""

from tradeit.decide.book import IN_FLIGHT_CAVEAT, RecordedBook, open_record, read_book
from tradeit.decide.declaration import (
    BookRef,
    DeclarationError,
    DeclaredHolding,
    DeclaredPortfolio,
    load_declaration,
)
from tradeit.decide.plan import (
    AbsentControl,
    Holding,
    ProposedEntry,
    ProposedExit,
    Refusal,
    SessionPlan,
    absent_controls,
    plan_session,
    with_references,
)
from tradeit.decide.runner import (
    BookSummary,
    PlanResult,
    build_cycle,
    plan_declared,
    resolve_universe,
)

__all__ = [
    "IN_FLIGHT_CAVEAT",
    "AbsentControl",
    "BookRef",
    "BookSummary",
    "DeclarationError",
    "DeclaredHolding",
    "DeclaredPortfolio",
    "Holding",
    "PlanResult",
    "ProposedEntry",
    "ProposedExit",
    "RecordedBook",
    "Refusal",
    "SessionPlan",
    "absent_controls",
    "build_cycle",
    "load_declaration",
    "open_record",
    "plan_declared",
    "plan_session",
    "read_book",
    "resolve_universe",
    "with_references",
]
