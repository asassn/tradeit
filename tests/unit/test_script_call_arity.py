"""Scripts may be untyped, but they may not call things that do not exist.

`scripts/` is deliberately outside CI's type-check gate: it holds 113 modules
and 192 mypy findings, most of them strictness rather than defect, and cleaning
them is not on anyone's critical path.

**The cost of that exemption was measured on 2026-10-23.**
`scripts/backtest_research01.py` -- the primary single-run backtest harness --
raised `TypeError` before loading a bar, because `EventDrivenEngine` had made
two parameters required and seven of its eight callers were updated. The eighth
was not. Nothing complained: scripts are untyped in CI and no test constructs
the engine from there, so the harness that produced much of the archive sat
unrunnable and unnoticed.

So one error code is gated rather than the whole directory: **`call-arg`**,
which is the code that would have caught it, with the exact message
*"Missing positional arguments ... in call to EventDrivenEngine"*. There was
precisely one `call-arg` finding in all of `scripts/` when this was written, and
it was a false positive now scoped at its call site -- so the gate costs one
suppression and catches a class of defect that renders a script unrunnable.

**Why `call-arg` specifically, and not more.** It is the subset of type errors
that is not a matter of strictness: a call missing a required argument, or
passing one that does not exist, fails at run time for certain. The other 191
findings are about precision, and gating them would mean a cleanup nobody has
asked for. The question for adding a code here is *"does this error mean the
line cannot run?"*
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_no_script_calls_something_with_the_wrong_arguments() -> None:
    """Runs mypy over `scripts/` and fails only on `call-arg`.

    Asserts mypy actually ran, as well as what it found. A test that greps for
    an error string passes just as happily when the tool crashed and printed
    nothing, which would turn this guard into a decoration -- the same
    passes-for-the-wrong-reason shape that let the broken harness survive.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "scripts"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if "No module named mypy" in output:
        pytest.skip("mypy is not installed in this environment")

    assert "error:" in output or "Success" in output, (
        f"mypy produced no recognisable report, so this test proved nothing:\n{output[:2000]}"
    )

    offenders = [line for line in output.splitlines() if "[call-arg]" in line]
    assert not offenders, (
        "a script calls something with arguments it does not accept, which fails at "
        "run time rather than merely being imprecise:\n  " + "\n  ".join(offenders)
    )
