"""Which scoring factors can actually be computed, and what to do when one cannot.

``ScoringConfig`` declares five weighted factors, and **two of them --
``breakout_confirmation`` and ``fundamental_quality`` -- cannot be produced on
``research-01`` today**, because the engines they need have never been run
against it. That is 39% of the declared weight resting on numbers nobody can
compute.

The module was written when ``sector_strength`` was the example: it carried
0.10 and the corpus held no sector classification at all. Both halves of that
have since changed -- the classification was built, the factor was measured on
two decades, it came out significant in *both* directions, and the weight was
removed. The situation it was written for outlived the example, which is the
usual way of these things.

Nothing is currently wrong, because no concrete scorer exists — the weights are
declared, hashed into the configuration digest, and consumed by nothing. This
module exists so that the obvious bug cannot be written when one is.

The bug it prevents
-------------------

A scorer that meets an uncomputable factor has three tempting options and two
of them are wrong.

**Scoring it zero** looks neutral and is not. Every security gets the same
zero, so the *ranking* is unaffected — but the total is now bounded by 0.90
rather than 1.00, and ``min_score_to_consider`` is an absolute threshold. A
0.60 cut against a 0.90 ceiling is a materially stricter filter than the one
anybody configured, and nothing in the output says so.

**Renormalising silently** fixes the ceiling and breaks something worse. If
sector data exists for some securities and not others, their scores are
computed from different factor sets — and **cross-sectional ranking of scores
built from different factors is not a ranking at all.** It is a comparison of
two different measurements that happen to share a scale.

**Refusing** is the third option and it is the default here.

The rule
--------

A score may be computed only when every weighted factor is available for
*every* security in the group being ranked. :class:`FactorCoverage` reports
what was available; :func:`require_uniform_coverage` refuses anything else.

The escape hatch is deliberate, explicit, and records itself: a caller may
drop unavailable factors, but only the ones unavailable for **all** securities,
and the resulting score carries the reduced factor set in its digest so it can
never be compared with a full one by accident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import content_hash

__all__ = [
    "FactorCoverage",
    "UncomputableFactor",
    "coverage_from",
    "drop_uniformly_unavailable",
    "require_uniform_coverage",
]


class UncomputableFactor(ConfigError):
    """A weighted factor could not be produced for at least one security."""


@dataclass(frozen=True, slots=True)
class FactorCoverage:
    """Which weighted factors were available, for which securities.

    ``available`` maps each security to the factors that could be computed for
    it. A factor absent from a security's set is one the data could not
    support — not one that scored zero.
    """

    weights: Mapping[str, float]
    available: Mapping[int, frozenset[str]]

    @property
    def weighted_factors(self) -> frozenset[str]:
        return frozenset(self.weights)

    @property
    def securities(self) -> tuple[int, ...]:
        return tuple(sorted(self.available))

    def missing_for(self, instrument_id: int) -> frozenset[str]:
        return self.weighted_factors - self.available.get(instrument_id, frozenset())

    @property
    def missing_everywhere(self) -> frozenset[str]:
        """Factors no security in the group could produce.

        These are the only ones it can be defensible to drop: dropping a
        factor that *some* securities have would score them on different
        evidence and then rank them against each other.
        """
        if not self.available:
            return self.weighted_factors
        return frozenset.intersection(
            *(self.missing_for(instrument_id) for instrument_id in self.available)
        )

    @property
    def missing_somewhere(self) -> frozenset[str]:
        return (
            frozenset().union(
                *(self.missing_for(instrument_id) for instrument_id in self.available)
            )
            if self.available
            else self.weighted_factors
        )

    @property
    def is_uniform(self) -> bool:
        """Whether every security has exactly the same factors available.

        The precondition for comparing scores at all.
        """
        if not self.available:
            return True
        first = next(iter(self.available.values()))
        return all(factors == first for factors in self.available.values())

    def explain(self) -> str:
        if not self.missing_somewhere:
            return f"all {len(self.weights)} weighted factors available"
        everywhere = sorted(self.missing_everywhere)
        patchy = sorted(self.missing_somewhere - self.missing_everywhere)
        parts = []
        if everywhere:
            parts.append(f"unavailable for every security: {', '.join(everywhere)}")
        if patchy:
            parts.append(f"available for some securities only: {', '.join(patchy)}")
        return "; ".join(parts)


def require_uniform_coverage(coverage: FactorCoverage) -> None:
    """Raise unless every security can produce the same factors.

    Patchy coverage is refused before the uniform-but-incomplete case, because
    it is the one no renormalisation can rescue.
    """
    if not coverage.is_uniform:
        patchy = sorted(coverage.missing_somewhere - coverage.missing_everywhere)
        raise UncomputableFactor(
            f"factor coverage is not uniform across the group: {', '.join(patchy)} "
            "can be computed for some securities and not others. Scores built from "
            "different factor sets share a scale but not a meaning, and ranking "
            "them against each other is not a ranking"
        )
    if coverage.missing_everywhere:
        raise UncomputableFactor(
            f"{', '.join(sorted(coverage.missing_everywhere))} cannot be computed "
            "for any security in this group. Scoring them zero would leave the "
            "total bounded below 1.0 while min_score_to_consider stayed an "
            "absolute threshold, which silently tightens the filter. Drop them "
            "explicitly with drop_uniformly_unavailable if that is intended"
        )


def drop_uniformly_unavailable(
    coverage: FactorCoverage,
) -> tuple[dict[str, float], str, tuple[str, ...]]:
    """Renormalise over the factors every security has, and say which went.

    Returns the reduced weights, a digest of the factor set they cover, and the
    dropped names. **The digest is the point.** A score computed from five
    factors must not be comparable with one computed from six just because both
    are numbers between 0 and 1, and carrying the factor set into the identity
    is what stops that.
    """
    if not coverage.is_uniform:
        require_uniform_coverage(coverage)  # raises with the better message
    dropped = tuple(sorted(coverage.missing_everywhere))
    kept = {
        name: weight
        for name, weight in coverage.weights.items()
        if name not in coverage.missing_everywhere
    }
    if not kept:
        raise UncomputableFactor(
            "every weighted factor is unavailable; there is nothing left to score with"
        )
    total = sum(kept.values())
    if total <= 0:
        raise UncomputableFactor("the surviving factors carry no weight between them")
    normalised = {name: weight / total for name, weight in sorted(kept.items())}
    return normalised, content_hash(normalised), dropped


def coverage_from(
    weights: Mapping[str, float],
    computed: Mapping[int, Sequence[str]],
) -> FactorCoverage:
    """Build coverage from what a feature pipeline actually produced."""
    return FactorCoverage(
        weights=dict(weights),
        available={
            instrument_id: frozenset(names) & frozenset(weights)
            for instrument_id, names in computed.items()
        },
    )
