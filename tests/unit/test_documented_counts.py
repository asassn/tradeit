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


def test_the_docs_do_not_claim_to_describe_more_tables_than_they_name() -> None:
    """DATA_MODEL states how many of the tables it actually covers.

    The header once read "49 tables in six domains. Every table is defined in
    tables.py", which implied completeness it did not have: 35 of the 57 are
    absent from the document entirely. Correcting only the count would have made
    that worse -- asserting coverage of 57 while naming 22 -- so the coverage
    figure is stated in the document and is checked here.
    """
    doc = (REPO / "docs" / "DATA_MODEL.md").read_text(encoding="utf-8")
    found = re.search(r"names (\d+) of those (\d+)", doc)
    assert found, "DATA_MODEL.md no longer states how many tables it covers"
    covered, total = int(found.group(1)), int(found.group(2))

    assert total == _table_count()
    # The caveat itself lists what is *missing*, so a table named there is not
    # thereby documented. Measuring without excluding it counted the gap as
    # coverage -- which this test caught when the caveat was first written.
    body = "\n".join(ln for ln in doc.split("\n") if not ln.lstrip().startswith(">"))
    named = {t for t in Base.metadata.tables if re.search(rf"\b{re.escape(t)}\b", body)}
    assert len(named) == covered, (
        f"DATA_MODEL.md claims to describe {covered} tables but mentions {len(named)}"
    )
    assert covered < total, (
        "the coverage caveat is now false because every table is documented -- "
        "delete the caveat rather than leaving a claim that understates the document"
    )


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
