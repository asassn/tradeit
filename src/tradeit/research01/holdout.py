"""The key to the held-out window: a pre-registration that is already committed.

2020-2025 is the confirmation window. It can be spent once per question, and
the only thing standing between that and "one more look" is a refusal in every
script that could read it. Those refusals started as flat ``SystemExit`` calls,
one per script, each a single edit away from being deleted and none of them
recording *why* a read was allowed.

So the gate is here, once, and it asks for evidence rather than permission:
**the path of a pre-registration that git already has.** A registration written
after the window was read is not a registration, and a boolean flag records no
reason, so neither is accepted. The returned commit SHA is printed by the
caller into its own output, which makes the artifact say which registration
spent the window.

One implementation, deliberately. The same codebase has already shipped a
guard-rail into one of two copies of a printing loop, where it fired nowhere
anyone would look.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

#: The first session of the confirmation window. Everything at or after this
#: date is held out and needs a registration to read.
HOLDOUT_START = dt.date(2020, 1, 1)


class HoldoutRefused(Exception):
    """Raised when a read of the confirmation window is not authorised."""


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return done.stdout.strip()


def authorise(end: dt.date, prereg: str | None) -> str | None:
    """Permission to read up to ``end``, or an exception explaining why not.

    Returns ``None`` when the window is not touched at all -- the ordinary
    design-window case, which needs no authorisation and must not be made to
    ask for any. Otherwise returns the commit SHA that carries ``prereg``.

    Raises :class:`HoldoutRefused` when the window is touched and the
    registration is missing, absent from disk, uncommitted, or modified since
    its commit. The last of those matters most: a registration edited after it
    was committed can be edited to match whatever the window turned out to say.
    """
    if end < HOLDOUT_START:
        return None
    if not prereg:
        raise HoldoutRefused(
            f"{HOLDOUT_START.year}-2025 is held out. To spend it, pass the path of a "
            "committed pre-registration that authorises this exact test."
        )
    path = Path(prereg)
    if not path.is_file():
        raise HoldoutRefused(f"no such pre-registration: {path}")
    sha = _git("log", "-1", "--format=%H", "--", str(path))
    if not sha:
        raise HoldoutRefused(
            f"{path} is not committed. A registration written after the window was "
            "read is not a registration."
        )
    dirty = _git("status", "--porcelain", "--", str(path))
    if dirty:
        raise HoldoutRefused(
            f"{path} has uncommitted changes ({dirty}). A registration that can still "
            "be edited is not a registration."
        )
    return sha
