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
    paired_median,
    table,
    tie_share,
    win_share,
)

HORIZONS = (5, 10, 21, 63, 126)
KEY = ("long", "bull_flag", "closed_above", "uptrend", "all")


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


def test_one_poisoned_leg_withholds_the_mean_and_names_the_security() -> None:
    """The failure that happened, reduced to its smallest form.

    The invariant is *a contaminated mean never prints as a number*. It used to
    be enforced by dropping the whole row, which cost 22 of 45 cells to ten
    securities out of 5,782 -- discarding evidence to avoid a defect. Now the
    row survives and only the spoiled mean is withheld.
    """
    output = "\n".join(built(rows(20, poison=4605.16)))
    assert "WITHHELD" in output
    assert "3953" in output, "the note must name the security to investigate"
    row = next(line for line in built(rows(20, poison=4605.16)) if line.startswith("long /"))
    # The poisoned mean is gone; the robust statistics are still there.
    assert "+46051" not in row and "4605" not in row
    assert row.count("--") >= len(HORIZONS), "every spoiled horizon's mean is withheld"


def test_a_clean_cell_keeps_every_mean() -> None:
    """The control: withholding must not be the default.

    A rule that withheld everything would satisfy the test above and destroy
    the atlas.
    """
    row = next(line for line in built(rows(20)) if line.startswith("long /"))
    assert "--" not in row


def test_the_note_names_the_horizons_and_the_size() -> None:
    output = "\n".join(built(rows(20, poison=4605.16)))
    assert "mean withheld at" in output
    assert "+460,516%" in output


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
    assert "WITHHELD" in output
    assert "3953" in output
    row = next(line for line in built(data) if line.startswith("long /"))
    # Only the late horizon is spoiled, so the short ones must still print.
    assert row.count("--") == 1


def test_the_median_and_win_rate_survive_a_poisoned_leg() -> None:
    """Why the row is worth keeping at all.

    One leg at +460,516% moves a mean over twenty pairs by tens of thousands of
    percent. It moves the **median by nothing**, and it moves the **win rate by
    exactly one observation** -- because a defect can flip at most the pair it
    is in. Bounded is not the same as unaffected, and saying so is the point of
    reporting both beside the mean rather than in place of it.
    """
    clean = group(rows(20), "trend", False, [])[KEY]
    dirty = group(rows(20, poison=4605.16), "trend", False, [])[KEY]
    assert paired_median(clean, "net_126") == pytest.approx(
        paired_median(dirty, "net_126"), abs=1e-9
    )
    moved = abs((win_share(clean, "net_126") or 0) - (win_share(dirty, "net_126") or 0))
    assert moved == pytest.approx(1 / 20), "one bad leg may flip its own pair and no other"


def test_ties_are_excluded_from_the_win_rate_not_counted_as_losses() -> None:
    """The number that was nearly published as a result.

    A fifth of real pairs are exact ties -- both legs stopped at the same stop
    fraction, so the trade and its control return the identical number. Scoring
    those as losses turned a bull flag winning 40.3% against its placebo's
    38.6% into a reported "40.3%", which reads as a rule that loses money.
    """
    data = rows(10)
    for i in range(4):  # four pairs return exactly the same on both legs
        data[2 * i + 1] = leg(str(i), "placebo", 0.02, security_id="7")
    pairs = group(data, "trend", False, [])[KEY]
    assert tie_share(pairs, "net_126") == pytest.approx(0.4)
    # Six decided pairs, all won by the rule.
    assert win_share(pairs, "net_126") == pytest.approx(1.0)


def test_the_median_is_reported_but_lands_in_the_tie_block() -> None:
    """Documents why every real cell shows +0.00 and it is not a null."""
    data = rows(10)
    for i in range(6):
        data[2 * i + 1] = leg(str(i), "placebo", 0.02, security_id="7")
    pairs = group(data, "trend", False, [])[KEY]
    assert paired_median(pairs, "net_126") == pytest.approx(0.0)
    assert tie_share(pairs, "net_126") == pytest.approx(0.6)


def test_a_large_but_possible_trade_is_not_refused() -> None:
    """A real trade can triple. The bound is set well above that on purpose.

    If this ever starts failing, the threshold has been tightened into
    discarding evidence rather than defects, which is the opposite error.
    """
    assert not any("WITHHELD" in line for line in built(rows(20, poison=2.5)))


def test_the_bound_is_symmetric() -> None:
    """A catastrophic negative print is as much a defect as a positive one."""
    assert any("WITHHELD" in line for line in built(rows(20, poison=-9.0)))


def test_extremes_reports_every_offending_leg_not_just_the_first() -> None:
    data = rows(20)
    for i in (0, 1, 2):
        data[2 * i + 1] = leg(str(i), "placebo", 50.0, security_id=f"{900 + i}")
    pairs = group(data, "trend", False, [])[KEY]
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
def test_both_output_formats_withhold(markdown: bool) -> None:
    """The bug was that one of two output paths lacked the check."""
    output = "\n".join(table(group(rows(20, poison=4605.16), "trend", False, []), 5, markdown))
    assert "WITHHELD" in output
