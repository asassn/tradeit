#!/usr/bin/env python
"""Query views that encode the corpus's read conventions.

**Why these exist.** `RESEARCH_01_DATA_DICTIONARY.md` §0 lists six ways this
corpus returns a confident wrong answer, and a dictionary helps a *reader* while
doing nothing for a *tool*. These views put the conventions in the database, so
``select * from v_prices`` is correct by construction: one row per session,
adjusted, with disputed and non-session bars already excluded.

Nothing is deleted, altered or moved. A view is a saved query; if one turns out
unhelpful, dropping it costs nothing and the evidence underneath is untouched.

**The one thing a view cannot do, stated here because it would otherwise become
a seventh trap.** A view takes no ``as_of`` argument, so it cannot be
point-in-time. ``v_prices`` returns *the current belief* -- the latest
``knowledge_time`` for each session. 327,924 keys in this corpus carry more than
one revision, so this is a real difference and not a technicality. **Anything
studying what was knowable on a past date must use**
``tradeit.research01.series.price_series``, which takes ``as_of`` and is the
supported point-in-time path. The views serve the simple case; they do not
replace the library.

``trading_sessions`` is a materialised table rather than a view because the
exchange calendar is not in SQL and cannot be. It is rebuilt from
:class:`~tradeit.core.calendar.TradingCalendar` on every run of this script.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01.views import VIEWS, create_views
from tradeit.storage.session import install_sqlite_busy_timeout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    if args.dry_run:
        for name in VIEWS:
            print(f"  would create {name}")
        return 0
    count = create_views(session)
    print(f"trading_sessions written: {count:,}")
    for name in VIEWS:
        print(f"  created {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
