#!/usr/bin/env python
"""The daily forward capture. Approved by the owner 2026-10-20.

**Why this is Python and not the shell script it replaces.** The first version
of this job was ``ops/forward_daily.sh``, and under ``launchd`` it failed with
``Operation not permitted`` before executing a single line. The cause is macOS
TCC, and the shape of it is worth recording because it is invisible from the
code and the obvious diagnoses are all wrong:

**TCC is granted per executable, not per user and not per directory.** A
``launchd`` agent in this session can traverse ``~/Documents`` and can ``stat``
files inside it, but ``/bin/bash``, ``head`` and ``ls`` cannot *read* a file
there -- ``cat pyproject.toml`` is denied, and so is listing the repository.
The framework interpreter at ``/Library/Frameworks/Python.framework`` already
holds a Documents grant, so it reads the same files without complaint. Measured
2026-10-21 with a throwaway agent, because every chain of reasoning about this
produced a different wrong answer:

===================================  ========  ==========
operation under ``~/Documents``      ``bash``  ``python``
===================================  ========  ==========
``cd`` into the repository           OK        OK
``stat`` a file                      OK        OK
list the repository                  DENIED    OK
read ``pyproject.toml``              DENIED    OK
read ``.env``                        DENIED    OK
read ``research01.sqlite``           DENIED    OK
===================================  ========  ==========

``.venv/bin/python`` is a symlink to that framework interpreter, which is why
``launchd`` can exec it even though the symlink itself lives under
``~/Documents`` -- and why the earlier probe's "the agent can read the repo"
was a false positive: that line used ``ls``, which only stats.

So **the shell is removed from the path entirely** rather than worked around.
There is no wrapper to grant access to, no copy of ``bash`` to register in
System Settings, and no system setting changed at all. The consequence for
anyone editing this file: **do not reintroduce a shell step.** A pipe to
``grep``, a ``$(cat ...)`` or a wrapper script will fail at run time with a
permission error that looks like a bug in the capture.

**A trailing window, not yesterday alone.** A laptop asleep at 22:10 misses its
run and ``launchd`` does not replay a missed ``StartCalendarInterval``. Asking
about the last ``LOOKBACK`` sessions every day means a missed day is recovered
by the next run that happens, with no catch-up logic and no state to go wrong:
the capture is idempotent on prices, so re-asking costs credits and writes
nothing new. A job that only ever asked about yesterday would lose a day
permanently every time the machine slept, and lose it silently.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
LOOKBACK = 7

#: **One vendor per security, never pooled.** Tiingo declares `RAW_UNADJUSTED`
#: and EODHD declares `UNKNOWN`; a series mixing the two bases would be a price
#: whose adjustment state depends on which vendor happened to answer that day,
#: which is the §0.1a defect reintroduced through the back door. The split is a
#: pair of committed files so the assignment is checkable, and
#: `tests/unit/test_forward_universe.py` asserts they stay disjoint.
VENDORS = (
    ("tiingo", "forward_universe.txt"),
    ("eodhd", "forward_universe_eodhd.txt"),
)

#: ``KEY=value``, and nothing else. The ``.env`` file is **parsed, never
#: sourced**: the shell version of this job executed it and died on a value
#: containing parentheses, which is shell syntax. Executing a config file is
#: also how a value containing ``$(...)`` becomes a command. Reading key=value
#: line by line makes the contents data, which is what they are.
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def load_env(path: Path) -> int:
    """Put ``.env`` into the environment. **Never logs or returns a value.**

    Returns the number of names set, which is safe to print; the values are
    credentials and the count is the only thing a log may say about them.
    An existing environment variable wins, so an operator can override one
    for a single manual run without editing the file.
    """
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip().strip("\"'")
        if not value or name in os.environ:
            continue
        os.environ[name] = value
        count += 1
    return count


def main() -> int:
    os.chdir(REPO)  # forward_capture.py puts a relative "src" on sys.path
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    print(f"=== forward capture {stamp} ===", flush=True)
    print(f"credentials loaded: {load_env(REPO / '.env')} names", flush=True)

    # Imported from its path rather than by name: `scripts/` is not a package
    # and must not become one just to be scheduled. Loaded once and called per
    # vendor, so the two runs share an interpreter but nothing else.
    spec = importlib.util.spec_from_file_location(
        "forward_capture", REPO / "scripts" / "forward_capture.py"
    )
    if spec is None or spec.loader is None:
        print("cannot load scripts/forward_capture.py", file=sys.stderr)
        return 2
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    worst = 0
    for source, filename in VENDORS:
        path = REPO / "ops" / filename
        universe = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if not universe:
            # Not an error. An empty or absent file is how a vendor is turned
            # off, and a vendor being off must not take the other one down.
            print(f"\n--- {source}: {filename} is empty or absent; skipping", flush=True)
            continue
        print(f"\n--- {source}: {universe.count(',') + 1} symbols", flush=True)
        sys.argv = [
            "forward_capture.py",
            "--source",
            source,
            "--tickers",
            universe,
            "--lookback",
            str(LOOKBACK),
            "--record",
            str(OUT / "forward.sqlite"),
            "--journal",
            str(OUT / "forward_journal.jsonl"),
        ]
        try:
            code = int(module.main())
        except SystemExit as exit_code:
            # `forward_capture` refuses by raising SystemExit -- a missing
            # credential, for instance. **One vendor's refusal must not cancel
            # the other's capture**, because a day not captured is gone and
            # half a day is worth more than none.
            code = int(exit_code.code or 1)
            print(f"{source}: {exit_code}", file=sys.stderr, flush=True)
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
