"""The counts the documentation quotes, checked against the code that defines them.

Four documents drifted apart on two numbers before this existed: the schema was
described as 41, 49 and 57 tables in different places, and the indicator count
appeared as 26 and 58. Every one of those numbers had been correct when it was
written, which is exactly why nobody noticed -- documentation rots silently, and
a reader has no way to tell a stale number from a current one.

So the numbers are pinned to their sources rather than to each other. These
tests fail when the code changes and the prose does not, which is the only
moment the discrepancy is cheap to fix.

They deliberately do **not** assert that a particular number appears; they
assert that whatever the document says matches what the code contains. A
docstring cannot satisfy them by coincidence.

Two later checks extend the same idea past counts, because DATA_MODEL turned out
to be wrong in ways a count cannot detect: it inventoried 22 of 57 tables, and
two of its diagrams described a schema that had been deliberately replaced two
phases earlier. So the *set* of documented tables is pinned to the ORM, and
every field named in a diagram must exist on the table it claims to describe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tradeit.analytics.indicators import IndicatorEngine
from tradeit.storage.tables import Base
from tradeit.strategy.config import StrategyConfig

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "config" / "strategies" / "baseline.toml"


def _table_count() -> int:
    """The authority on how many tables exist: the ORM metadata itself."""
    return len(Base.metadata.tables)


def _indicator_count() -> int:
    """The authority on how many indicators exist, for the shipped config.

    Configuration-dependent by design -- a config declaring more periods
    registers more features -- so the baseline is named explicitly rather than
    treated as a fixed property of the code.
    """
    config = StrategyConfig.from_toml(BASELINE)
    return len(IndicatorEngine(config.indicators).registry.names())


def test_the_orm_defines_the_table_count_the_docs_quote() -> None:
    """Every current-tense table count in the docs matches the metadata.

    Phase reports are excluded on purpose: PHASE_02 saying the 41-table schema
    applied cleanly is a record of what was true then, and rewriting a phase
    report to match today would falsify history rather than correct it.
    """
    expected = _table_count()
    assert expected > 0

    claims = {
        REPO / "README.md": r"\*\*A (\d+)-table schema\*\*",
        REPO / "docs" / "DATA_MODEL.md": r"\*\*(\d+) tables are defined\*\*",
    }
    for path, pattern in claims.items():
        found = re.search(pattern, path.read_text(encoding="utf-8"))
        assert found, f"{path.name} no longer states a table count in the expected form"
        assert int(found.group(1)) == expected, (
            f"{path.name} says {found.group(1)} tables; the ORM defines {expected}"
        )


def _domain_inventories() -> dict[str, list[str]]:
    """Table names listed in DATA_MODEL's per-domain inventories, by domain.

    An inventory row is ``| `table_name` | what it is |``. Scoped to sections
    titled "Domain ..." on purpose: the Partitioning section also opens rows
    with a table name, and counting those would let a table be "documented" by
    appearing in a sizing note.
    """
    doc = (REPO / "docs" / "DATA_MODEL.md").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for section in re.split(r"^## ", doc, flags=re.M)[1:]:
        title = section.split("\n", 1)[0].strip()
        if not title.startswith("Domain "):
            continue
        out[title] = re.findall(r"^\| `([a-z_]+)` \|", section, re.M)
    return out


def test_every_table_is_inventoried_in_exactly_one_domain() -> None:
    """DATA_MODEL's inventory is the schema -- no more, no less, no duplicates.

    This replaced a weaker check that read a stated coverage figure out of the
    document ("names 22 of those 57") and compared it to how many table names
    appeared in the prose. That was the right assertion while the document was
    knowingly incomplete: it stopped the gap from being papered over. It is the
    wrong one now that every table is covered, because a *count* can stay
    correct while the wrong tables are documented -- add one, drop another, and
    22 is still 22.

    So the invariant is stated over the set rather than its size. A table added
    to the ORM and not to a domain fails here; so does an inventory entry for a
    table that no longer exists, and so does one table listed under two domains,
    which is how a reader ends up with two different accounts of what it is for.
    """
    inventories = _domain_inventories()
    assert inventories, "DATA_MODEL.md no longer has per-domain inventory tables"

    listed = [name for names in inventories.values() for name in names]
    orm = set(Base.metadata.tables)

    duplicated = sorted({n for n in listed if listed.count(n) > 1})
    assert not duplicated, f"listed under more than one domain: {duplicated}"
    assert not set(listed) - orm, f"inventoried but not in the ORM: {sorted(set(listed) - orm)}"
    assert not orm - set(listed), f"defined but not inventoried: {sorted(orm - set(listed))}"
    # Belt and braces: the two set comparisons above are satisfied by an empty
    # document if the ORM is ever empty, and the count check would not be.
    assert len(listed) == _table_count()


def test_no_diagram_names_a_field_that_does_not_exist() -> None:
    """Mermaid entity blocks are checked against the columns they claim to show.

    A stale diagram is a worse failure than a missing one, and this document
    carried two for several phases: the ``PATTERNS`` and ``BREAKOUT_EVENTS``
    blocks showed the Phase 2 draft -- ``status``, ``pivot_price``,
    ``stop_price``, ``volume_ratio`` -- long after Phase 4 and Phase 5 replaced
    both designs. Nothing marked them as historical, so a reader had no way to
    tell them from current schema.

    Only field *names* are checked. Types and the quoted comments are prose and
    deliberately not pinned; the failure being prevented is a column that is not
    there at all.
    """
    doc = (REPO / "docs" / "DATA_MODEL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^    ([A-Z_]+) \{\n(.*?)^    \}", doc, re.S | re.M)
    assert blocks, "DATA_MODEL.md no longer contains mermaid entity blocks"

    problems: list[str] = []
    for entity, body in blocks:
        table = Base.metadata.tables.get(entity.lower())
        if table is None:
            problems.append(f"{entity} is drawn as an entity but is not a table")
            continue
        columns = set(table.columns.keys())
        for line in body.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] not in columns:
                problems.append(f"{entity.lower()}.{parts[1]} does not exist")
    assert not problems, "; ".join(problems)


def test_the_feature_registry_defines_the_indicator_count_the_docs_quote() -> None:
    """Every current-tense indicator count matches the baseline registry."""
    expected = _indicator_count()
    assert expected > 0

    claims = {
        REPO / "README.md": r"(\d+) indicators under the shipped",
        REPO / "docs" / "ROADMAP.md": r"depends on: (\d+) causal\s*\n?indicators",
        # The document writes the arithmetic with a real multiplication sign;
        # it is escaped here rather than pasted, so the pattern stays
        # unambiguous to a reader without changing what it matches.
        REPO / "docs" / "DATA_MODEL.md": "\\u00d7 (\\d+) indicators \\u00d7 252 sessions",
    }
    for path, pattern in claims.items():
        found = re.search(pattern, path.read_text(encoding="utf-8"))
        assert found, f"{path.name} no longer states an indicator count in the expected form"
        assert int(found.group(1)) == expected, (
            f"{path.name} says {found.group(1)} indicators; the registry declares {expected}"
        )


@pytest.mark.parametrize("stale", [41, 49, 26])
def test_a_superseded_count_cannot_return_as_a_current_claim(stale: int) -> None:
    """The three numbers that were actually wrong, kept out of current prose.

    Each remains legitimate as history -- 41 is what Phase 2 validated -- so the
    check is scoped to the current-tense forms the other tests read, not to the
    digits appearing anywhere.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    data_model = (REPO / "docs" / "DATA_MODEL.md").read_text(encoding="utf-8")

    assert f"**A {stale}-table schema**" not in readme
    assert f"**{stale} tables are defined**" not in data_model
    assert f"{stale} indicators under the shipped" not in readme
