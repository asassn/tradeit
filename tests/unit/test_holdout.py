"""The gate on the confirmation window, which is worth more than its code.

``src/tradeit/research01/holdout.py``. 2020-2025 can be spent once per
question. These tests exist because the obvious ways to write this gate are all
subtly useless: a boolean anybody can pass, a file that need not be committed,
or a committed file that can still be edited to match whatever the window
turned out to say.

The design window must stay unencumbered -- a gate that made ordinary 2010-2019
work ask for authorisation would be removed within a week, and then nothing
would guard the real thing.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from tradeit.research01.holdout import HOLDOUT_START, HoldoutRefused, authorise


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real git repository, because the gate asks git and nothing else."""
    monkeypatch.chdir(tmp_path)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    return tmp_path


def commit(repo: Path, name: str, body: str = "a registration") -> Path:
    path = repo / name
    path.write_text(body)
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, check=True)
    return path


def test_the_design_window_needs_no_authorisation() -> None:
    """The case that must stay frictionless, or the gate gets deleted."""
    assert authorise(dt.date(2019, 12, 31), None) is None
    assert authorise(HOLDOUT_START - dt.timedelta(days=1), None) is None


def test_the_window_is_refused_without_a_registration() -> None:
    with pytest.raises(HoldoutRefused, match="held out"):
        authorise(dt.date(2025, 12, 31), None)
    # The very first held-out session counts as touching it.
    with pytest.raises(HoldoutRefused):
        authorise(HOLDOUT_START, None)


def test_an_uncommitted_registration_is_refused(repo: Path) -> None:
    """Writing the registration after the run is the failure to prevent."""
    (repo / "draft.md").write_text("written afterwards")
    with pytest.raises(HoldoutRefused, match="not committed"):
        authorise(dt.date(2025, 12, 31), "draft.md")


def test_a_missing_registration_is_refused(repo: Path) -> None:
    with pytest.raises(HoldoutRefused, match="no such pre-registration"):
        authorise(dt.date(2025, 12, 31), "absent.md")


def test_a_committed_registration_returns_its_sha(repo: Path) -> None:
    commit(repo, "prereg.md")
    sha = authorise(dt.date(2025, 12, 31), "prereg.md")
    assert sha is not None and len(sha) == 40


def test_a_registration_edited_after_commit_is_refused(repo: Path) -> None:
    """The subtlest hole, and the one a naive gate leaves open.

    A committed file that is then edited can be edited to match whatever the
    window turned out to say, and the commit check alone would pass it.
    """
    path = commit(repo, "prereg.md")
    sha = authorise(dt.date(2025, 12, 31), "prereg.md")
    assert sha is not None
    path.write_text("now it predicts the answer")
    with pytest.raises(HoldoutRefused, match="uncommitted changes"):
        authorise(dt.date(2025, 12, 31), "prereg.md")


def test_an_empty_string_is_not_a_registration(repo: Path) -> None:
    """argparse defaults to '' and that must not read as authorisation."""
    with pytest.raises(HoldoutRefused, match="held out"):
        authorise(dt.date(2025, 12, 31), "")
