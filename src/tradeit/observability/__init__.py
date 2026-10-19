"""Phase 12 — the system explaining what it did.

> A live trading system that cannot explain what it did is not acceptable.

`record` holds the writers for the four links of the decision chain that had
none: scores, orders, executions and trades. The other three already had one —
risk snapshots (`risk/snapshot.py`), the journal (`execution/journal.py`), and
the run manifest, which the engine takes and never constructs.

`reconstruct` walks a session and **names the links that hold nothing**, because
a reconstruction that cannot tell a quiet session from an unlogged one does not
satisfy the requirement.
"""

from tradeit.observability.record import (
    DecisionChain,
    reconstruct,
    record_execution,
    record_order,
    record_score,
    record_trade,
)

__all__ = [
    "DecisionChain",
    "reconstruct",
    "record_execution",
    "record_order",
    "record_score",
    "record_trade",
]
