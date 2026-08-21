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

import json
import subprocess
from pathlib import Path

import pytest

from tradeit.cli_data import OUTPUT_DIRNAME, _write_json_report

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


class TestLocalOutputIsIgnored:
    """A diagnostic session must not leave untracked files next to the source.

    Eleven of them once did: scan-diagnostic.json, scan-full-01.json/.log,
    validation-report*.json and friends, all written into the repository root
    because the documented examples were bare filenames and the command was run
    from a clone. `/out/` is where they belong now.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "out/scan.json",
            "out/scan.log",
            "out/validation-report.json",
            "out/2026-08-21/scan-diagnostic-02.json",
        ],
    )
    def test_the_output_directory_is_ignored(self, path: str) -> None:
        assert is_ignored(path), f"{path} would be committed"

    def test_the_documented_directory_is_the_ignored_one(self) -> None:
        """The name the CLI advertises and the name git hides are one name."""
        assert is_ignored(f"{OUTPUT_DIRNAME}/anything.json")


class TestReportWriting:
    """`--json out/scan.json` must work before `out/` exists.

    A full scan runs for a long time and the payload lives only in memory until
    it is written. Failing with FileNotFoundError at that point discards exactly
    what the run was for, so the directory is created rather than required.
    """

    def test_a_missing_output_directory_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / OUTPUT_DIRNAME / "scan.json"
        assert not target.parent.exists()

        written = _write_json_report(str(target), {"checks": [], "usable": True})

        assert written == target
        assert json.loads(target.read_text(encoding="utf-8")) == {"checks": [], "usable": True}

    def test_a_bare_filename_still_writes_where_it_is_told(self, tmp_path: Path) -> None:
        """Existing invocations keep working: only the documented path changed."""
        target = tmp_path / "report.json"

        written = _write_json_report(str(target), {"ok": 1})

        assert written == target
        assert target.exists()


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
            # Names that brush against `/out/` without being at the root. `out`
            # is a short, common word, which is exactly why the rule is anchored.
            "src/tradeit/validation/out.py",
            "src/tradeit/scanning/out/writer.py",
            "docs/out-of-sample.md",
            "tests/fixtures/out/expected.json",
            # Fixtures and documents that are committed on purpose.
            "tests/unit/test_acquisition_fmp.py",
            "docs/LOCAL_DATA_ACQUISITION.md",
            "docs/VENDOR_SEMANTICS.md",
            "DATA_REQUIRED.md",
            # The curated identity evidence. A .json file, and the research
            # output rather than a rebuildable artifact -- a blanket `*.json`
            # rule to catch diagnostic output would hide it.
            "docs/research/control_identity_evidence.json",
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
        risky = {"data", "packages", "market-data", "empirical", "out"}
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

    def test_no_blanket_pattern_catches_diagnostic_output_by_name(self) -> None:
        """The tempting fix for stray reports, refused in code.

        A rule like `*.json` or `validation-*` would have swept up the eleven
        stray diagnostic files in one line -- and would also hide
        docs/research/control_identity_evidence.json, any future fixture named
        for a scan, and any document named for validation. The directory is
        ignored; the shapes of filenames are not.
        """
        banned = {"*.json", "*.log", "scan-*", "validation-*", "*.jsonl"}
        found = [
            line
            for raw in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if (line := raw.strip()) in banned
        ]
        assert found == [], (
            f"blanket ignore pattern(s) {found}. Diagnostic output is kept out of "
            "commits by writing it into the ignored /out/ directory, not by "
            "matching filenames -- a name-shaped rule cannot tell a rebuildable "
            "report from committed research evidence."
        )
