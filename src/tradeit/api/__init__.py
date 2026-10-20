"""Phase 10's read side.

Imports FastAPI, which is an **optional** dependency (`pip install -e .[api]`).
Nothing else in the package imports this one, so the research and backtesting
path stays installable and runnable without a web framework.
"""

from tradeit.api.app import create_app

__all__ = ["create_app"]
