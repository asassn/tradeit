"""The atlas report's guard-rail: a corpus defect must not become a number.

Written 2026-09-25, after an atlas ran with five of the eight jump-guard shards
-- the file list was built from a truncated ``ls | head`` -- and a placebo leg
returning **+460,516% over five sessions** carried a published edge to -44.30%.
The table printed it without complaint, and 19 of 27 cells turned out to be
contaminated once the check existed to ask.

Two lessons are encoded here, both of which cost a night of compute:

1. **A guard that depends on being handed every one of its own shards is not a
   guard.** The report now checks the numbers themselves, so an incomplete
   upstream filter is caught at the point the number would be believed.
2. **The check has to live on the path that actually runs.** It was added to
   the shared table builder while the terminal path kept its own copy of the
   loop, so the refusal never fired where anyone would see it. These tests
   drive the same entry point the CLI does.

Nothing here is about trading. It is about ``CLAUDE.md``'s first discipline:
a number that cannot be traced to evidence is worse than no number, because it
will be acted on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from pattern_atlas_report import (
    EXTREME_RETURN,
    extremes,
    group,
    table,
)

HORIZONS = (5, 10, 21, 63, 126)


def leg(trade: str, which: str, net: float, **over: str) -> dict[str, str]:
    row = {
        "trade_id": trade,
        "leg": which,
        "direction": "long",
        "pattern": "bull_flag",
        "arm": "closed_above",
        "regime": "uptrend",
        "vol_regime": "quiet",
        "attempt": "1",
        "security_id": "1",
        "entry_date": "2014-05-27",
        "final_state": "confirmed",
        "reached_1r_first": "1",
    }
    row.update({f"net_{h}": f"{net:.6f}" for h in HORIZONS})
    row.update(over)
    return row


def rows(count: int, *, poison: float | None = None) -> list[dict[str, str]]:
    """``count`` ordinary pairs, optionally with one poisoned placebo leg."""
    out: list[dict[str, str]] = []
    for i in range(count):
        out.append(leg(str(i), "rule", 0.02))
        bad = poison if (poison is not None and i == 0) else 0.01
        out.append(leg(str(i), "placebo", bad, security_id="3953"))
    return out


def built(data: list[dict[str, str]], minimum: int = 5) -> list[str]:
    return table(group(data, "trend", False, []), minimum, markdown=False)


def test_a_clean_table_reports_its_cell() -> None:
    """The control: without a defect the cell must actually print.

    A refusal rule that refused everything would pass every other test here
    and destroy the atlas.
    """
    output = "\n".join(built(rows(20)))
    assert "bull_flag" in output
    assert "REFUSED" not in output


def test_one_poisoned_leg_refuses_the_whole_cell() -> None:
    """The failure that happened, reduced to its smallest form."""
    output = "\n".join(built(rows(20, poison=4605.16)))
    assert "REFUSED" in output
    assert "3953" in output, "the refusal must name the security to investigate"
    # And it must NOT quietly print a mean that one print decided.
    printed = built(rows(20, poison=4605.16))
    assert not any(line.startswith("long / bull_flag") and "%" in line for line in printed)


def test_the_refusal_names_the_horizon_and_size() -> None:
    output = "\n".join(built(rows(20, poison=4605.16)))
    assert "beyond" in output
    assert "460,516%" in output or "+460,516%" in output


def test_a_defect_that_appears_only_at_a_late_horizon_is_caught() -> None:
    """The near-miss fix that would have passed every other test here.

    The break that started this was visible at ``net_5``, so a check written
    against ``net_5`` alone looks correct and is not: a price discontinuity at
    session 80 leaves the five- and twenty-one-session columns clean and
    destroys the six-month one. Every horizon is checked, and this is the test
    that says so.
    """
    data = rows(20)
    late = leg("0", "placebo", 0.01, security_id="3953")
    late["net_126"] = "88.0"
    data[1] = late
    output = "\n".join(built(data))
    assert "REFUSED" in output
    assert "3953" in output


def test_a_large_but_possible_trade_is_not_refused() -> None:
    """A real trade can triple. The bound is set well above that on purpose.

    If this ever starts failing, the threshold has been tightened into
    discarding evidence rather than defects, which is the opposite error.
    """
    assert not any("REFUSED" in line for line in built(rows(20, poison=2.5)))


def test_the_bound_is_symmetric() -> None:
    """A catastrophic negative print is as much a defect as a positive one."""
    assert any("REFUSED" in line for line in built(rows(20, poison=-9.0)))


def test_extremes_reports_every_offending_leg_not_just_the_first() -> None:
    data = rows(20)
    for i in (0, 1, 2):
        data[2 * i + 1] = leg(str(i), "placebo", 50.0, security_id=f"{900 + i}")
    pairs = group(data, "trend", False, [])[("long", "bull_flag", "closed_above", "uptrend", "all")]
    found = extremes(pairs, "net_5")
    assert len(found) == 3
    assert {f[0] for f in found} == {"900", "901", "902"}


def test_the_threshold_is_what_the_module_documents() -> None:
    """Pinning it: the bound is a documented constant, not a magic number.

    §0.10 records price steps with no recorded action; 4.0 sits above any
    plausible trade and below every defect seen so far.
    """
    assert EXTREME_RETURN == 4.0
    assert not extremes([(leg("0", "rule", 3.99), leg("0", "placebo", 0.0))], "net_5")
    assert extremes([(leg("0", "rule", 4.01), leg("0", "placebo", 0.0))], "net_5")


@pytest.mark.parametrize("markdown", [True, False])
def test_both_output_formats_refuse(markdown: bool) -> None:
    """The bug was that one of two output paths lacked the check."""
    output = "\n".join(table(group(rows(20, poison=4605.16), "trend", False, []), 5, markdown))
    assert "REFUSED" in output
