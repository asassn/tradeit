"""Phase 12 — the system explaining what it did.

> A live trading system that cannot explain what it did is not acceptable.

**This package deliberately exports only the seam.** `base` holds
:class:`~tradeit.observability.base.RecordedDecision`,
:class:`~tradeit.observability.base.DecisionRecorder` and
:class:`~tradeit.observability.base.NullRecorder`, and imports nothing from
storage. The writers live in ``record`` and are imported from there explicitly:

```python
from tradeit.observability import DecisionRecorder      # the seam
from tradeit.observability.record import DatabaseRecorder  # the implementation
```

**That asymmetry is the point and was arrived at the hard way.** `base` was
split out of `record` so `backtesting.engine` could depend on the protocol
without pulling the ORM into its import graph — and the split achieved nothing
at first, because a submodule import executes the package ``__init__``, and this
file re-exported ``record``. Importing `observability.base` therefore dragged in
150 SQLAlchemy modules exactly as before. Measured, not assumed.

So the rule: **nothing in this file may import ``record``.** A convenience
re-export here silently re-couples the engine to the database, and the coupling
is invisible at the call site that causes it.
"""

from tradeit.observability.base import (
    DecisionRecorder,
    NullRecorder,
    RecordedDecision,
)

__all__ = [
    "DecisionRecorder",
    "NullRecorder",
    "RecordedDecision",
]
