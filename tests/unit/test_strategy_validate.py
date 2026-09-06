"""``DRAFT → VALIDATED``: does everything this strategy references exist?

A backtest of a strategy naming a detector nobody wrote does not fail — it
silently tests a smaller strategy, and reports a number for it. That is what
this gate exists to stop, and why an unproducible pattern is a defect rather
than a warning.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.portfolio.mandate import Mandate, eligible_timeframes
from tradeit.strategy.config import StrategyConfig
from tradeit.strategy.lifecycle import StrategyState, StrategyVersion
from tradeit.strategy.validate import (
    Defect,
    StrategyInvalid,
    producible_patterns,
    validate,
    validated,
)


def _config(**timeframes: object) -> StrategyConfig:
    base = {
        "base_timeframe": "1d",
        "enabled": ("1d", "1w"),
        "intraday_enabled": ("15m", "1h"),
        "intraday_base": "1m",
    }
    base.update(timeframes)
    return StrategyConfig(name="swing-breakout", timeframes=base)


def _defects(config: StrategyConfig, mandate: Mandate) -> list[Defect]:
    return [f.defect for f in validate(config, mandate).findings]


# -- the registries --------------------------------------------------------


def test_the_pattern_vocabulary_is_wider_than_the_detector_set() -> None:
    """Deliberately: the enum must stay able to interpret an old stored row
    whose family has no current detector. The gap is what makes
    UNPRODUCIBLE_PATTERN a distinct defect from UNKNOWN_PATTERN."""
    producible = producible_patterns()
    assert producible < set(PatternType)
    orphans = {p.value for p in PatternType} - {p.value for p in producible}
    assert orphans, "if every pattern type had a detector the distinction is vacuous"


def test_a_pattern_no_detector_emits_is_a_defect() -> None:
    """It would never fire, which is worse than an error because the run still
    produces a number."""
    orphan = next(p for p in PatternType if p not in producible_patterns())
    config = StrategyConfig(name="s", patterns={"enabled_patterns": (orphan.value,)})
    assert Defect.UNPRODUCIBLE_PATTERN in _defects(config, Mandate.SWING)


def test_a_pattern_outside_the_vocabulary_is_a_different_defect() -> None:
    config = StrategyConfig(name="s", patterns={"enabled_patterns": ("moon_phase",)})
    assert Defect.UNKNOWN_PATTERN in _defects(config, Mandate.SWING)


def test_enabling_no_patterns_is_a_defect() -> None:
    """Valid by every field constraint and unable ever to detect anything."""
    config = StrategyConfig(name="s", patterns={"enabled_patterns": ()})
    assert Defect.NOTHING_ENABLED in _defects(config, Mandate.SWING)


def test_an_unknown_timeframe_is_reported_not_raised() -> None:
    assert Defect.UNKNOWN_TIMEFRAME in _defects(_config(enabled=("1d", "3d")), Mandate.SWING)


# -- the mandate binding (§7) ----------------------------------------------


def test_a_strategy_may_select_within_its_mandate() -> None:
    report = validate(_config(), Mandate.SWING)
    assert report.valid, report.describe()


def test_a_strategy_may_not_select_outside_its_mandate() -> None:
    """Retirement's eligible set stops at the daily bar."""
    defects = _defects(_config(), Mandate.RETIREMENT)
    assert Defect.TIMEFRAME_OUTSIDE_MANDATE in defects


def test_four_hour_bars_fail_for_every_mandate() -> None:
    """§3.2 defines 4h and withholds its adoption, so no mandate admits it and
    a config enabling it is not ready — whatever else is right about it."""
    config = _config(intraday_enabled=("15m", "1h", "4h"))
    for mandate in Mandate:
        assert Defect.TIMEFRAME_OUTSIDE_MANDATE in _defects(config, mandate), mandate


def test_every_finding_names_what_the_mandate_would_have_accepted() -> None:
    """A refusal that does not say what was allowed cannot be acted on."""
    findings = validate(_config(), Mandate.RETIREMENT).findings
    outside = [f for f in findings if f.defect is Defect.TIMEFRAME_OUTSIDE_MANDATE]
    assert outside
    for frame in eligible_timeframes(Mandate.RETIREMENT):
        assert frame.value in outside[0].detail


# -- construction inputs are not selections --------------------------------


def test_a_construction_base_is_not_checked_against_the_mandate() -> None:
    """A swing strategy building 15-minute bars out of 1-minute bars is not
    making a 1-minute decision, and 1m is outside the swing mandate. Checking
    it there would forbid building 15-minute bars correctly."""
    assert Bartimeframe.M1 not in eligible_timeframes(Mandate.SWING)
    assert validate(_config(intraday_base="1m"), Mandate.SWING).valid


def test_each_base_is_paired_with_the_group_it_builds() -> None:
    """The daily base builds the daily-and-above series and is not required to
    build 15-minute bars; pooling the groups reported a false defect on the
    default config, which is why they are separate."""
    assert validate(_config(base_timeframe="1d", intraday_base="1m"), Mandate.SWING).valid


def test_a_base_coarser_than_what_it_must_build_is_a_defect() -> None:
    config = _config(base_timeframe="1w", enabled=("1d", "1w"))
    findings = validate(config, Mandate.SWING).findings
    coarse = [f for f in findings if f.defect is Defect.BASE_COARSER_THAN_TARGET]
    assert coarse and "1d" in coarse[0].detail


# -- the report, and the transition ----------------------------------------


def test_every_defect_is_reported_not_only_the_first() -> None:
    """A strategy author fixing one reference per round trip is how a gate
    becomes something people route around."""
    config = StrategyConfig(
        name="s",
        patterns={"enabled_patterns": ("moon_phase", "tarot")},
        timeframes={"enabled": ("1d", "3d"), "intraday_enabled": ("4h",)},
    )
    assert len(validate(config, Mandate.SWING).findings) >= 4


def test_a_valid_strategy_is_promoted_to_validated() -> None:
    version = StrategyVersion(
        strategy_name="swing-breakout", version=1, digest="a" * 16, mandate=Mandate.SWING
    )
    promoted = validated(version, _config())
    assert promoted.state is StrategyState.VALIDATED
    assert promoted.history[-1].citation


def test_an_invalid_strategy_is_not_promoted() -> None:
    version = StrategyVersion(
        strategy_name="retirement-hold", version=1, digest="b" * 16, mandate=Mandate.RETIREMENT
    )
    with pytest.raises(StrategyInvalid):
        validated(version, _config())


def test_the_mandate_comes_from_the_version_not_the_caller() -> None:
    """§7 makes the mandate part of the definition. Letting a caller pass a
    different one would make the check answer a question nobody asked."""
    version = StrategyVersion(
        strategy_name="retirement-hold", version=1, digest="c" * 16, mandate=Mandate.RETIREMENT
    )
    config = _config()
    assert validate(config, Mandate.SWING).valid
    with pytest.raises(StrategyInvalid) as raised:
        validated(version, config)
    assert raised.value.report.mandate is Mandate.RETIREMENT


# -- §2: a detector on a timeframe its family is not defined for ------------


def test_pattern_families_map_to_the_timeframes_they_are_defined_at() -> None:
    """`SUPPORTED_TIMEFRAMES` is keyed by *detector* and a detector is not a
    pattern type; the registry carries both deliberately. The mapping unions
    across the detectors that emit a family."""
    from tradeit.strategy.validate import timeframes_by_pattern

    mapping = timeframes_by_pattern()
    assert mapping[PatternType.CUP_WITH_HANDLE] == frozenset({Bartimeframe.D1, Bartimeframe.W1})
    assert Bartimeframe.M15 in mapping[PatternType.BULL_FLAG]


def test_a_pattern_defined_at_no_decided_timeframe_is_a_defect() -> None:
    """A cup is an accumulation process measured in months. Enabling it on a
    strategy that only decides intraday is producible, admitted, and unable to
    fire — the same class of silent failure as an unproducible pattern."""
    config = StrategyConfig(
        name="intraday-cup",
        patterns={"enabled_patterns": ("cup_with_handle",)},
        timeframes={
            "base_timeframe": "1h",
            "enabled": ("1h",),
            "intraday_enabled": ("15m",),
            "intraday_base": "1m",
        },
    )
    findings = [
        f
        for f in validate(config, Mandate.SWING).findings
        if f.defect is Defect.PATTERN_TIMEFRAME_MISMATCH
    ]
    assert findings
    assert "1d" in findings[0].detail and "15m" in findings[0].detail


def test_one_supported_timeframe_is_enough() -> None:
    """Requiring every family at every timeframe would refuse an ordinary
    strategy running cup-and-handle daily and bull flags intraday, which is
    correct practice rather than a defect."""
    config = StrategyConfig(
        name="mixed",
        patterns={"enabled_patterns": ("cup_with_handle", "bull_flag")},
        timeframes={
            "base_timeframe": "1d",
            "enabled": ("1d",),
            "intraday_enabled": ("15m", "1h"),
            "intraday_base": "1m",
        },
    )
    assert not [
        f
        for f in validate(config, Mandate.SWING).findings
        if f.defect is Defect.PATTERN_TIMEFRAME_MISMATCH
    ]


def test_the_cross_check_does_not_double_report_an_unknown_pattern() -> None:
    """A name outside the vocabulary is one defect, not two."""
    config = StrategyConfig(
        name="s",
        patterns={"enabled_patterns": ("moon_phase",)},
        timeframes={"enabled": ("1d",), "intraday_enabled": ()},
    )
    defects = [f.defect for f in validate(config, Mandate.SWING).findings]
    assert defects.count(Defect.UNKNOWN_PATTERN) == 1
    assert Defect.PATTERN_TIMEFRAME_MISMATCH not in defects
