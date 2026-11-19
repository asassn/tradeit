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
import json
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
        # The plan's declared portfolios. None is the real argparse default,
        # so a test that omitted it would exercise a Namespace the command can
        # never actually receive.
        "declaration": None,
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


# ---------------------------------------------------------------------------
# The declared portfolios
#
# A plan needs holdings, and nothing in this repository writes a `portfolios`
# or `positions` row, so the operator declares them in a file. It is validated
# before the server starts for the same reason the record is: a declaration
# that only refuses when the home page is opened reads as a broken dashboard
# rather than as a file with a missing stop in it.
# ---------------------------------------------------------------------------


def test_without_a_declaration_the_plan_is_reported_disabled(
    tmp_path: Path, served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    a_record = tmp_path / "record.sqlite"
    a_record.touch()

    assert cmd_dashboard(_args(record=str(a_record))) == 0

    assert "portfolio: none -- the plan is disabled" in capsys.readouterr().out


def test_a_missing_declaration_file_is_refused_before_serving(
    tmp_path: Path, served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    a_record = tmp_path / "record.sqlite"
    a_record.touch()

    assert cmd_dashboard(_args(record=str(a_record), declaration=str(tmp_path / "gone.json"))) == 2

    assert served == [], "nothing should be served"
    err = capsys.readouterr().err
    assert "declaration refused" in err
    assert "no declaration at" in err, "and it names the path that was tried"


def test_a_declaration_that_cannot_be_read_is_refused_before_serving(
    tmp_path: Path, served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    """**Refused at startup, not at the first request.**

    A missing stop is a fact about the file. Surfacing it only when somebody
    opens the page makes it look like the dashboard is broken.
    """
    a_record = tmp_path / "record.sqlite"
    a_record.touch()
    bad = tmp_path / "declaration.json"
    bad.write_text(json.dumps({"portfolios": [{"mandate": "swing"}]}))

    assert cmd_dashboard(_args(record=str(a_record), declaration=str(bad))) == 2

    assert served == []
    assert "declaration refused" in capsys.readouterr().err


def test_a_valid_declaration_names_the_horizons_it_will_plan(
    tmp_path: Path, served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    a_record = tmp_path / "record.sqlite"
    a_record.touch()
    good = tmp_path / "declaration.json"
    good.write_text(
        json.dumps(
            {
                "portfolios": [
                    {
                        "mandate": "swing",
                        "equity": "100000",
                        "cash": "40000",
                        "universe": ["IBM"],
                        "warmup_from": "2014-01-02",
                        "as_of": "2015-11-27",
                        "score_range": {"lower": 0.0, "upper": 0.5},
                        "holdings": [],
                    }
                ]
            }
        )
    )

    assert cmd_dashboard(_args(record=str(a_record), declaration=str(good))) == 0

    out = capsys.readouterr().out
    assert "portfolio:" in out
    assert "(swing)" in out, "which horizons will be planned is part of what is being served"
