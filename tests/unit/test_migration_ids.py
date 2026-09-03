"""Every migration's revision id must fit the column Alembic stores it in.

``alembic_version.version_num`` is a ``varchar(32)``. **SQLite does not enforce
a varchar length and PostgreSQL does**, so an over-long id passes the entire
local suite -- which runs on SQLite -- and then fails every PostgreSQL
integration test in CI, with an error naming string truncation rather than the
migration that caused it.

That happened with ``0016_fundamental_cross_section_index``, thirty-six
characters. ``0014_pit_basis_and_alias_overlap`` is thirty-two, which is to say
the convention had already reached the limit without anyone noticing it was one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

#: Alembic's own default for `alembic_version.version_num`.
MAX = 32

_REVISION = re.compile(r'^revision: str = "([^"]+)"', re.M)
_DOWN = re.compile(r'^down_revision: str \| None = "([^"]+)"', re.M)


def _migrations() -> list[Path]:
    found = sorted(p for p in VERSIONS.glob("*.py") if not p.name.startswith("__"))
    assert found, "no migrations found; the glob or the layout has moved"
    return found


@pytest.mark.parametrize("path", _migrations(), ids=lambda p: p.stem)
def test_migration_revision_ids_fit_the_column(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = _REVISION.search(text)
    assert found, f"{path.name} declares no revision id in the expected form"
    revision = found.group(1)
    assert len(revision) <= MAX, (
        f"{path.name}: revision id {revision!r} is {len(revision)} characters; "
        f"alembic_version.version_num holds {MAX}. SQLite will not object and "
        "PostgreSQL will."
    )
    down = _DOWN.search(text)
    if down:
        assert len(down.group(1)) <= MAX, (
            f"{path.name}: down_revision {down.group(1)!r} exceeds {MAX} characters"
        )
