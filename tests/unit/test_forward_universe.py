"""The vendor assignment is a pair of files, so it can be checked.

`ops/forward_daily.py` runs the capture once per vendor, and each security is
assigned to exactly one. The reason is `RESEARCH_01_DATA_DICTIONARY.md` §0.1a:
Tiingo declares `RAW_UNADJUSTED` and EODHD declares `UNKNOWN`, so a series fed
by whichever vendor answered first would be a price whose adjustment state
varies by day. These tests hold the properties that make the split meaningful
rather than decorative.

Deliberately no corpus: these run in CI, where `research01.sqlite` does not
exist. Per-date resolvability is enforced at run time by
`tradeit.forward.identity`, which has its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[2] / "ops"
FILES = ("forward_universe.txt", "forward_universe_eodhd.txt")


def _names(filename: str) -> list[str]:
    path = OPS / filename
    if not path.exists():
        return []
    return [n.strip() for n in path.read_text(encoding="utf-8").strip().split(",") if n.strip()]


def test_the_two_universes_are_disjoint() -> None:
    """**The invariant the split exists for.**

    A ticker in both files would be captured twice a day from two vendors with
    different adjustment bases, into one record, under one instrument id -- the
    exact pooling the assignment is meant to prevent, arrived at by accident
    rather than by decision.
    """
    tiingo, eodhd = (set(_names(f)) for f in FILES)
    overlap = tiingo & eodhd
    assert not overlap, (
        f"{sorted(overlap)} is assigned to both vendors; one security takes bars from "
        "one vendor, because Tiingo's basis is raw_unadjusted and EODHD's is unknown"
    )


@pytest.mark.parametrize("filename", FILES)
def test_a_universe_file_is_a_bare_comma_separated_line(filename: str) -> None:
    """`forward_daily.py` counts symbols as `text.count(",") + 1`.

    That arithmetic is wrong for a trailing comma, a blank entry or a stray
    newline inside the list, and it is used to report how many symbols a run
    asked about -- a number somebody will compare against the record.
    """
    path = OPS / filename
    if not path.exists():
        pytest.skip(f"{filename} absent, which is how a vendor is turned off")
    raw = path.read_text(encoding="utf-8")
    assert raw.strip(), f"{filename} is empty; remove it instead to turn the vendor off"
    assert "\n" not in raw.strip(), "one line, so the symbol count stays a comma count"
    names = _names(filename)
    assert raw.strip().count(",") + 1 == len(names), "a blank or doubled comma miscounts"
    assert names == [n.upper() for n in names], "upper case, as the vendors expect"
    assert len(names) == len(set(names)), "a duplicate would be asked about twice"


def test_at_least_one_vendor_has_a_universe() -> None:
    """Both files empty means the scheduled job captures nothing, silently."""
    assert any(_names(f) for f in FILES)
