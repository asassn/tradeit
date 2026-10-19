"""The recorder seam, with no storage behind it.

Split from :mod:`tradeit.observability.record` deliberately, and the reason is
the whole point of the seam: that module imports the ORM, and
`backtesting.engine` importing the protocol from there would pull SQLAlchemy
into the engine's import graph — undermining the storage-free property the
protocol exists to protect.

So the contract lives here, importing only the execution value types, and the
database implementation lives in ``record``. The engine depends on this module
and never on that one.

**The split alone was not enough**, and that is worth stating here too: a
submodule import executes the package ``__init__``, so while
``observability/__init__.py`` re-exported ``record`` this module still dragged
in 150 SQLAlchemy modules. The package now exports only the seam, and a test
asserts the engine's import graph stays clean — because the coupling is
invisible at the call site that causes it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tradeit.execution.base import Fill, OrderRequest

__all__ = ["DecisionRecorder", "NullRecorder", "RecordedDecision"]


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    """One session's decisions, as the engine saw them.

    A value rather than a set of arguments, so the engine hands over *what
    happened* and the recorder decides what to persist. The engine therefore
    needs no session, no table, and no knowledge of whether anything is being
    written at all.
    """

    session_date: dt.date
    portfolio_id: int
    scores: tuple[tuple[int, str, float, Mapping[str, object]], ...] = ()
    orders: tuple[OrderRequest, ...] = ()
    fills: tuple[tuple[str, Fill], ...] = ()
    #: Candidates the risk engine refused, with the rule that refused them.
    refused: tuple[tuple[int, str], ...] = ()


class DecisionRecorder(Protocol):
    """Where a run's decisions go, if anywhere.

    The default is :class:`NullRecorder`, which is not a stub for later: **a
    backtest over scripted bars should write nothing**, and most runs are that.
    """

    def record(self, decision: RecordedDecision) -> None: ...


@dataclass(frozen=True, slots=True)
class NullRecorder:
    """Records nothing, deliberately.

    A run with no recorder is not an unobservable run — it is a run whose
    decisions nobody asked to keep, which is the correct state for a unit test
    and for the thousands of Stage A scans this project does.
    """

    def record(self, decision: RecordedDecision) -> None:
        return None
