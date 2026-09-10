"""Tests for factor coverage.

The two that matter are the ones describing bugs nobody has written yet:
scoring an uncomputable factor as zero, and renormalising when coverage is
patchy. Both look neutral and neither is.
"""

from __future__ import annotations

import pytest

from tradeit.strategy.config import StrategyConfig
from tradeit.strategy.factors import (
    FactorCoverage,
    UncomputableFactor,
    coverage_from,
    drop_uniformly_unavailable,
    require_uniform_coverage,
)

WEIGHTS = StrategyConfig(name="baseline").scoring.normalised_weights()
ALL = tuple(WEIGHTS)
#: The exemplar is whichever weighted factor cannot be computed today.
#: It was ``sector_strength`` until that weight was dropped on measured
#: evidence; the invariants here are about coverage, not about which
#: factor happens to be missing.
UNCOMPUTABLE = "fundamental_quality"
WITHOUT_SECTOR = tuple(n for n in ALL if n != UNCOMPUTABLE)


def _coverage(**per_security: tuple[str, ...]) -> FactorCoverage:
    return coverage_from(WEIGHTS, {int(k.lstrip("s")): v for k, v in per_security.items()})


class TestUniformity:
    def test_full_coverage_passes(self) -> None:
        coverage = _coverage(s1=ALL, s2=ALL)
        assert coverage.is_uniform
        assert coverage.missing_somewhere == frozenset()
        require_uniform_coverage(coverage)

    def test_patchy_coverage_is_refused_before_anything_else(self) -> None:
        """No renormalisation can rescue it, so it is caught first."""
        coverage = _coverage(s1=ALL, s2=WITHOUT_SECTOR)
        assert not coverage.is_uniform
        with pytest.raises(UncomputableFactor, match="not uniform"):
            require_uniform_coverage(coverage)

    def test_the_message_explains_why_ranking_would_be_invalid(self) -> None:
        coverage = _coverage(s1=ALL, s2=WITHOUT_SECTOR)
        with pytest.raises(UncomputableFactor) as caught:
            require_uniform_coverage(coverage)
        assert "share a scale but not a meaning" in str(caught.value)

    def test_an_empty_group_is_trivially_uniform(self) -> None:
        assert coverage_from(WEIGHTS, {}).is_uniform


class TestUniformlyMissing:
    def test_a_factor_missing_everywhere_still_refuses_by_default(self) -> None:
        """Scoring it zero would tighten min_score_to_consider silently."""
        coverage = _coverage(s1=WITHOUT_SECTOR, s2=WITHOUT_SECTOR)
        assert coverage.is_uniform
        assert coverage.missing_everywhere == frozenset({UNCOMPUTABLE})
        with pytest.raises(UncomputableFactor, match=UNCOMPUTABLE):
            require_uniform_coverage(coverage)

    def test_the_message_names_the_threshold_it_would_distort(self) -> None:
        coverage = _coverage(s1=WITHOUT_SECTOR, s2=WITHOUT_SECTOR)
        with pytest.raises(UncomputableFactor) as caught:
            require_uniform_coverage(coverage)
        assert "min_score_to_consider" in str(caught.value)

    def test_dropping_is_explicit_and_renormalises(self) -> None:
        coverage = _coverage(s1=WITHOUT_SECTOR, s2=WITHOUT_SECTOR)
        weights, digest, dropped = drop_uniformly_unavailable(coverage)
        assert dropped == (UNCOMPUTABLE,)
        assert UNCOMPUTABLE not in weights
        assert sum(weights.values()) == pytest.approx(1.0)
        assert digest

    def test_the_reduced_weight_set_has_its_own_identity(self) -> None:
        """A five-factor score must not be comparable with a six-factor one."""
        five, five_digest, _ = drop_uniformly_unavailable(
            _coverage(s1=WITHOUT_SECTOR, s2=WITHOUT_SECTOR)
        )
        six, six_digest, dropped = drop_uniformly_unavailable(_coverage(s1=ALL, s2=ALL))
        assert dropped == ()
        assert five_digest != six_digest
        assert len(five) == len(six) - 1

    def test_dropping_a_patchy_factor_is_refused(self) -> None:
        with pytest.raises(UncomputableFactor, match="not uniform"):
            drop_uniformly_unavailable(_coverage(s1=ALL, s2=WITHOUT_SECTOR))

    def test_losing_every_factor_leaves_nothing_to_score_with(self) -> None:
        with pytest.raises(UncomputableFactor, match="nothing left to score"):
            drop_uniformly_unavailable(_coverage(s1=(), s2=()))


class TestReporting:
    def test_explain_separates_the_two_kinds_of_gap(self) -> None:
        """Missing everywhere and missing patchily are different problems.

        Only the first can be dropped by renormalising; the second means two
        securities were scored on different evidence, which no arithmetic
        rescues. The message has to tell them apart, so it needs two factors.
        """
        patchy = "breakout_confirmation"
        coverage = _coverage(
            s1=WITHOUT_SECTOR,
            s2=tuple(n for n in WITHOUT_SECTOR if n != patchy),
        )
        message = coverage.explain()
        assert f"unavailable for every security: {UNCOMPUTABLE}" in message
        assert f"available for some securities only: {patchy}" in message

    def test_full_coverage_says_so_plainly(self) -> None:
        assert "all 5 weighted factors available" in _coverage(s1=ALL).explain()

    def test_unknown_names_are_not_counted_as_coverage(self) -> None:
        """A pipeline producing a feature nobody weighted has not covered anything."""
        coverage = coverage_from(WEIGHTS, {1: ("something_else",)})
        assert coverage.missing_for(1) == frozenset(WEIGHTS)


def test_the_corpus_today_cannot_score_every_weighted_factor() -> None:
    """The situation this module was written for, stated as a test.

    ``fundamental_quality`` needs an engine no study has run against
    ``research-01``, so a scorer run today would meet exactly this coverage and
    must refuse rather than quietly score the factor zero.
    """
    coverage = _coverage(s1=WITHOUT_SECTOR, s2=WITHOUT_SECTOR, s3=WITHOUT_SECTOR)
    with pytest.raises(UncomputableFactor):
        require_uniform_coverage(coverage)
    weights, _, dropped = drop_uniformly_unavailable(coverage)
    assert dropped == (UNCOMPUTABLE,)
    # The remaining five carry the whole weight, renormalised -- not 0.90.
    assert sum(weights.values()) == pytest.approx(1.0)
