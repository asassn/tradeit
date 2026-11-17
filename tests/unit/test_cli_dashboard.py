"""The wrapper that starts Phase 10's service.

`RUNBOOK.md` documented the start command and then explained, correctly, that
``create_app`` takes the record's DSN and has no default *"so a wrapper is
needed to pass one"*. The wrapper did not exist, so the documented command
raised ``TypeError`` and there was no way to start the service at all. These
tests hold the three properties that make the wrapper worth having rather than
a thin alias for ``uvicorn``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine

from tradeit.cli_dashboard import LOOPBACK, _sqlite_url, add_dashboard_commands, cmd_dashboard
from tradeit.storage.tables import Base


def _args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "record": "",
        "corpus": None,
        "forward": None,
        "host": LOOPBACK,
        "port": 8000,
        "log_level": "info",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what would have been served instead of serving it."""
    calls: list[dict[str, Any]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


@pytest.fixture
def a_record(tmp_path: Path) -> Path:
    path = tmp_path / "record.sqlite"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
    return path


def test_the_record_argument_is_required() -> None:
    """No default, inherited from `create_app`'s own reasoning: the record and
    the corpus are different databases and there is more than one record, so a
    default would serve whichever happened to be configured."""
    parser = argparse.ArgumentParser()
    add_dashboard_commands(parser.add_subparsers())
    with pytest.raises(SystemExit):
        parser.parse_args(["dashboard"])


def test_a_path_that_does_not_exist_is_refused_rather_than_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], served: list[dict[str, Any]]
) -> None:
    """**SQLite creates an empty database on connect**, so a mistyped path
    would otherwise start a service that runs cleanly and answers every route
    with nothing -- which reads exactly like a record in which nothing
    happened. That is the single most misleading thing this service can do,
    and it is checked before the server starts rather than at the first
    request.
    """
    missing = tmp_path / "typo.sqlite"
    assert cmd_dashboard(_args(record=str(missing))) == 2
    assert "no record at" in capsys.readouterr().err
    assert not missing.exists(), "a refused path must not leave a database behind"
    assert served == []


def test_an_existing_record_is_served(a_record: Path, served: list[dict[str, Any]]) -> None:
    """The companion to the test above, so neither can go vacuous: a wrapper
    that refused everything would pass that one on its own."""
    assert cmd_dashboard(_args(record=str(a_record))) == 0
    assert len(served) == 1
    assert served[0]["host"] == LOOPBACK


def test_a_full_dsn_is_passed_through_rather_than_treated_as_a_path(
    served: list[dict[str, Any]],
) -> None:
    """The record may be Postgres. A DSN run through `Path.exists` would be
    refused as a missing file."""
    assert cmd_dashboard(_args(record="sqlite:///:memory:")) == 0
    assert len(served) == 1


def test_binding_beyond_loopback_says_what_it_exposes(
    a_record: Path, capsys: pytest.CaptureFixture[str], served: list[dict[str, Any]]
) -> None:
    """The flag exists -- a wrapper that forbade it would be worked around by
    calling uvicorn directly -- but the default is loopback and anything else
    names what is being exposed. The service is read-only and cannot trade,
    and nothing authenticates it."""
    assert cmd_dashboard(_args(record=str(a_record), host="0.0.0.0")) == 0
    err = capsys.readouterr().err
    assert "0.0.0.0" in err
    assert "nothing authenticates it" in err
    assert served[0]["host"] == "0.0.0.0"


def test_the_loopback_default_raises_no_warning(
    a_record: Path, capsys: pytest.CaptureFixture[str], served: list[dict[str, Any]]
) -> None:
    """Or the warning is noise and stops being read."""
    assert cmd_dashboard(_args(record=str(a_record))) == 0
    assert "warning" not in capsys.readouterr().err


def test_a_relative_record_resolves_against_the_working_directory(tmp_path: Path) -> None:
    """`sqlite:///` with a relative path is relative to the working directory
    and `sqlite:////` with one is relative to the filesystem root, and the
    error when it is wrong never names the path it tried. Resolving first
    removes the distinction."""
    url = _sqlite_url(Path("record.sqlite"))
    assert url.startswith("sqlite:////")
    assert url.endswith("/record.sqlite")
    del tmp_path


def test_a_missing_web_server_explains_the_optional_extra(
    monkeypatch: pytest.MonkeyPatch, a_record: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The research path is deliberately runnable without a web framework, so
    the dependency is an optional extra and its absence is a normal state to
    land in rather than a broken install."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> Any:
        if name == "uvicorn":
            raise ModuleNotFoundError("No module named 'uvicorn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert cmd_dashboard(_args(record=str(a_record))) == 2
    assert '".[api]"' in capsys.readouterr().err


def test_the_command_is_registered_on_the_cli() -> None:
    """The defect this whole module fixes was that nothing could start the
    service. A wrapper nobody can reach would reproduce it exactly."""
    from tradeit.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dashboard"])  # required --record
    args = parser.parse_args(["dashboard", "--record", "x.sqlite"])
    assert args.func is cmd_dashboard
