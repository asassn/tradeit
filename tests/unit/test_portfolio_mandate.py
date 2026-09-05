"""The mandate table, and the separation rule stated so it can be checked.

These tests exist because ``docs/MULTI_TIMEFRAME_MANDATES.md`` §4 makes claims
that are easy to state and easy to erode: that ``4h`` is in no mandate, that a
mandate refuses timeframes it does not admit, and that no population may pool
mandates or timeframes it did not declare. Each is pinned here so the erosion
shows up as a failure rather than as a plausible-looking number.
"""

from __future__ import annotations

import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.patterns.registry import SUPPORTED_TIMEFRAMES
from tradeit.portfolio.mandate import (
    MANDATES,
    Corpus,
    Mandate,
    Observation,
    PopulationNotAdmissible,
    PopulationViolation,
    Role,
    UnitOfStudy,
    admits,
    check_population,
    eligible_timeframes,
    require_population,
    require_timeframe,
    spec,
)


def test_every_mandate_is_specified() -> None:
    assert set(MANDATES) == set(Mandate)
    for mandate, entry in MANDATES.items():
        assert entry.mandate is mandate


def test_four_hour_bars_are_admitted_by_no_mandate() -> None:
    """§3.2 defines ``4h`` semantics and withholds adoption.

    The tension is real and deliberate: detector families *do* support ``4h``,
    so this cannot be inferred from the registry and has to be asserted.
    """
    for mandate in Mandate:
        assert not admits(mandate, Bartimeframe.H4)
    supports_h4 = {n for n, tfs in SUPPORTED_TIMEFRAMES.items() if Bartimeframe.H4 in tfs}
    assert supports_h4, "if no detector supports 4h the exclusion above is vacuous"


def test_hierarchy_timeframes_are_all_eligible_in_their_own_mandate() -> None:
    """A mandate that names a timeframe it would refuse is self-contradictory."""
    for mandate, entry in MANDATES.items():
        for timeframe in entry.hierarchy.all_timeframes():
            assert entry.admits(timeframe), f"{mandate.value} names {timeframe.value}"


def test_eligible_sets_run_fine_to_coarse() -> None:
    """Declaration order in :class:`Bartimeframe` is ascending by duration."""
    order = list(Bartimeframe)
    for mandate in Mandate:
        frames = eligible_timeframes(mandate)
        assert list(frames) == sorted(frames, key=order.index)


def test_the_three_mandates_are_not_the_same_population() -> None:
    """Day and Retirement overlap only at the daily bar, and nowhere finer."""
    day = set(eligible_timeframes(Mandate.DAY))
    retirement = set(eligible_timeframes(Mandate.RETIREMENT))
    assert day & retirement == {Bartimeframe.D1}


def test_a_timeframe_can_hold_two_roles_in_one_mandate() -> None:
    """Swing's daily bar is both the fine end of context and the coarse end of
    setup; a single-role answer would force a choice the hierarchy does not
    make."""
    roles = spec(Mandate.SWING).hierarchy.roles_of(Bartimeframe.D1)
    assert set(roles) == {Role.CONTEXT, Role.SETUP}


def test_the_same_timeframe_means_different_things_across_mandates() -> None:
    """``1d`` is Swing's setup and Retirement's trigger. A timeframe alone
    never identifies a role, which is why there is no global role table."""
    assert Role.SETUP in spec(Mandate.SWING).hierarchy.roles_of(Bartimeframe.D1)
    assert Role.TRIGGER in spec(Mandate.RETIREMENT).hierarchy.roles_of(Bartimeframe.D1)


def test_eligible_may_exceed_the_hierarchy() -> None:
    """``30m`` is eligible for Day without appearing in its hierarchy: the
    hierarchy is where the mandate ordinarily looks, eligibility is what it is
    permitted to look at, and collapsing them would forbid legitimate work."""
    entry = spec(Mandate.DAY)
    assert entry.admits(Bartimeframe.M30)
    assert Bartimeframe.M30 not in entry.hierarchy.all_timeframes()


def test_the_day_mandate_reads_the_intraday_corpus() -> None:
    assert spec(Mandate.DAY).primary_corpus is Corpus.INTRADAY_01
    assert spec(Mandate.RETIREMENT).primary_corpus is Corpus.RESEARCH_01


def test_swing_treats_intraday_as_supporting_not_primary() -> None:
    """Entries only. Making it a second primary corpus would license swing
    research on a survivorship-biased sample."""
    entry = spec(Mandate.SWING)
    assert entry.primary_corpus is Corpus.RESEARCH_01
    assert entry.supporting_corpora == (Corpus.INTRADAY_01,)


def test_require_timeframe_refuses_rather_than_returning() -> None:
    assert require_timeframe(Mandate.SWING, Bartimeframe.D1) is Bartimeframe.D1
    with pytest.raises(Exception) as raised:
        require_timeframe(Mandate.RETIREMENT, Bartimeframe.M5)
    assert "retirement" in str(raised.value)
    assert "5m" in str(raised.value)


# -- the separation rule ---------------------------------------------------


def _swing_daily(n: int) -> list[Observation]:
    return [Observation(Mandate.SWING, Bartimeframe.D1)] * n


def test_a_single_mandate_single_timeframe_population_is_admissible() -> None:
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    report = check_population(_swing_daily(40), unit)
    assert report.admissible
    assert not report.combined
    assert report.total == 40


def test_mixing_mandates_is_refused() -> None:
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    population = [*_swing_daily(40), Observation(Mandate.RETIREMENT, Bartimeframe.D1)]
    report = check_population(population, unit)
    assert not report.admissible
    assert [f.violation for f in report.findings] == [PopulationViolation.UNDECLARED_MANDATE]
    assert report.findings[0].count == 1


def test_mixing_timeframes_is_refused() -> None:
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    population = [*_swing_daily(40), *[Observation(Mandate.SWING, Bartimeframe.H1)] * 7]
    report = check_population(population, unit)
    assert not report.admissible
    assert [f.violation for f in report.findings] == [PopulationViolation.UNDECLARED_TIMEFRAME]
    assert report.findings[0].count == 7


def test_a_declared_combination_is_permitted_and_stays_labelled() -> None:
    """The rule's escape clause, and the condition on it: a mixed population is
    allowed when the mixture is the unit of study, and it must remain readable
    as mixed afterwards or the escape becomes a laundering route."""
    unit = UnitOfStudy(
        mandates=frozenset({Mandate.SWING}),
        timeframes=frozenset({Bartimeframe.D1, Bartimeframe.H1}),
    )
    population = [*_swing_daily(40), *[Observation(Mandate.SWING, Bartimeframe.H1)] * 7]
    report = check_population(population, unit)
    assert report.admissible
    assert report.combined
    assert "combined" in report.describe()


def test_a_declaration_the_mandate_would_refuse_is_itself_refused() -> None:
    """Declaring the pairing does not make it legitimate; the mandate's
    eligibility is checked after the declaration, not instead of it."""
    unit = UnitOfStudy(
        mandates=frozenset({Mandate.RETIREMENT}), timeframes=frozenset({Bartimeframe.M5})
    )
    report = check_population([Observation(Mandate.RETIREMENT, Bartimeframe.M5)], unit)
    assert not report.admissible
    assert report.findings[0].violation is PopulationViolation.INELIGIBLE_TIMEFRAME


def test_an_empty_population_is_refused_rather_than_passing_vacuously() -> None:
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    report = check_population([], unit)
    assert not report.admissible
    assert PopulationViolation.EMPTY_POPULATION in {f.violation for f in report.findings}


def test_an_empty_declaration_is_refused() -> None:
    unit = UnitOfStudy(mandates=frozenset(), timeframes=frozenset({Bartimeframe.D1}))
    report = check_population(_swing_daily(5), unit)
    assert not report.admissible
    assert PopulationViolation.EMPTY_DECLARATION in {f.violation for f in report.findings}


def test_unit_of_study_single_refuses_an_ineligible_pairing_at_construction() -> None:
    with pytest.raises(Exception):
        UnitOfStudy.single(Mandate.RETIREMENT, Bartimeframe.M1)


def test_require_population_raises_with_the_report_attached() -> None:
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    population = [*_swing_daily(3), Observation(Mandate.DAY, Bartimeframe.M5)]
    with pytest.raises(PopulationNotAdmissible) as raised:
        require_population(population, unit)
    assert raised.value.report.findings
    assert not raised.value.report.admissible


def test_the_report_records_what_was_present_not_only_what_failed() -> None:
    """A refusal that does not say what the population actually contained
    cannot be acted on."""
    unit = UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
    population = [*_swing_daily(4), *[Observation(Mandate.DAY, Bartimeframe.M5)] * 2]
    report = check_population(population, unit)
    assert report.present == {
        (Mandate.SWING, Bartimeframe.D1): 4,
        (Mandate.DAY, Bartimeframe.M5): 2,
    }
