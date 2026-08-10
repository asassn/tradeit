"""The ignore rules, enforced rather than asserted in a comment.

This file exists because of a specific, expensive mistake. An unanchored `data/`
line in `.gitignore` also matched `src/tradeit/data/` and silently kept the whole
data package — providers, registry, quality checks, validation universe, offline
importer — out of every commit. A fresh clone did not build and CI never linted
any of it. Nothing failed loudly; the repository simply had a hole in it.

The lesson is not "be careful with gitignore". It is that an ignore rule is a
piece of behaviour, and behaviour that matters gets a test. Every rule intended
to hide acquired market data is checked here against paths that must be hidden
**and** against source paths that must never be, using `git check-ignore` — the
same matcher git itself uses, rather than a re-implementation of its pattern
semantics that could agree with the comment and disagree with git.

The paths below need not exist. `git check-ignore` matches patterns against path
strings, so this suite makes no filesystem changes and works on a clean tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def is_ignored(path: str) -> bool:
    """Ask git, not a regex, whether it would ignore this path."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {result.stderr.decode()}")
    return result.returncode == 0


class TestAcquiredDataIsIgnored:
    """Market-data licences commonly forbid redistribution, and a full-universe
    package is hundreds of megabytes. Neither should reach a commit by accident.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "empirical-smoke/manifest.toml",
            "empirical-smoke-2/daily_bars.csv.gz",
            "empirical-smoke-fmp/splits.csv",
            "empirical-validation-2010-2026/manifest.toml",
            "empirical-data/daily_bars.csv.gz",
            "market-data/bars.csv",
            "packages/anything.csv",
        ],
    )
    def test_root_level_acquisition_output_is_ignored(self, path: str) -> None:
        assert is_ignored(path), f"{path} would be committed"

    @pytest.mark.parametrize(
        "path",
        [
            "empirical-smoke/_acquisition/journal.jsonl",
            "empirical-smoke/_acquisition/raw/fmp/splits/AAPL.json",
            "some-package/_acquisition/raw/twelve_data/time_series/SPY.json",
        ],
    )
    def test_the_acquisition_workspace_is_ignored_wherever_it_appears(self, path: str) -> None:
        """`_acquisition` is always vendor data and no source tree uses the
        name, so this one is deliberately not root-anchored."""
        assert is_ignored(path), f"{path} would be committed"


class TestSourceIsNotIgnored:
    """The half that would have caught the original defect.

    Each path below is either real source or a plausible future file whose name
    brushes against an ignore pattern. None of them may be hidden.
    """

    @pytest.mark.parametrize(
        "path",
        [
            # The exact directory the unanchored `data/` rule swallowed.
            "src/tradeit/data/__init__.py",
            "src/tradeit/data/packages/spec.py",
            "src/tradeit/data/providers/http.py",
            "src/tradeit/data/validation_universe.py",
            # Names that brush against `/empirical-*/` without being at the root.
            "src/tradeit/data/empirical_notes.py",
            "src/tradeit/validation/empirical-config.toml",
            "docs/empirical-guide.md",
            "tests/fixtures/empirical-sample.csv",
            "tests/unit/empirical-data/fixture.csv",
            # Fixtures and documents that are committed on purpose.
            "tests/unit/test_acquisition_fmp.py",
            "docs/LOCAL_DATA_ACQUISITION.md",
            "docs/VENDOR_SEMANTICS.md",
            "DATA_REQUIRED.md",
            # A package directory nested under source must not vanish either.
            "src/tradeit/packages/registry.py",
            "src/tradeit/market-data/loader.py",
        ],
    )
    def test_source_and_committed_fixtures_are_never_ignored(self, path: str) -> None:
        assert not is_ignored(path), (
            f"{path} is hidden by .gitignore. This is the failure mode that once "
            "kept the entire src/tradeit/data package out of every commit: a "
            "fresh clone did not build and nothing failed loudly."
        )

    def test_every_data_pattern_is_anchored_or_names_a_reserved_directory(self) -> None:
        """A structural check on the file itself, not on its effects.

        A future rule could be written unanchored and happen to miss every path
        the tests above enumerate. This catches the shape rather than the
        symptom: a pattern mentioning data must either start with `/` — root
        only — or name `_acquisition`, which no source tree uses.
        """
        risky = {"data", "packages", "market-data", "empirical"}
        offenders: list[str] = []
        for raw in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            stem = line.lstrip("/").rstrip("/").removeprefix("**/")
            if not any(stem.startswith(name) for name in risky):
                continue
            if line.startswith("/") or "_acquisition" in line:
                continue
            offenders.append(line)
        assert offenders == [], (
            f"unanchored data ignore pattern(s) {offenders}. Anchor them to the "
            "repository root with a leading `/`, or they will also match "
            "directories inside src/."
        )
