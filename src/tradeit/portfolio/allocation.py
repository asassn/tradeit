"""Allocation ranking: choosing between candidates that are all individually good.

Ranking by score alone is stock picking with extra steps. The five highest
scores in a momentum screen during an energy rally are five energy names, and a
ranker that cannot see that hands the portfolio one bet wearing five tickers —
which the sector and correlation *limits* will then refuse one at a time,
leaving the system apparently working and buying nothing.

So selection is **greedy and marginal**: take the best candidate, then re-price
every remaining candidate against what has already been taken, and repeat. A
name's value depends on what is already in the basket, so it cannot be computed
once up front.

Two decisions worth stating, because the obvious alternative is worse in a way
that is hard to see afterwards:

**A missing correlation is treated as maximal, not as zero.** Scoring an
unmeasured pair as uncorrelated makes the ranker systematically prefer the
names nobody has measured — it would rank a thinly-covered small cap above a
well-measured one *because* of the missing data. The distinction the class
draws is between a correlation source that omits a pair (the pair is unknown
and penalised as if perfectly correlated) and no source at all (the term is not
applied, and the ranking says so through the recorded penalty).

**Ineligible candidates are ranked last, never dropped.** A candidate whose
sizing was refused, or whose score is not positive, cannot receive capital, but
removing it from the returned list would hide it from post-trade analysis.
``OpportunitySlate`` already draws this line — ``ranked`` includes vetoed
candidates and ``for_capital`` does not — and this follows it.

**The penalty coefficients have no defaults.** They are trading logic; a
default in a signature is a strategy parameter nobody agreed to. Neither does
``held_sectors``: without it the ranker can only see crowding *inside the
slate*, and would cheerfully offer a fourth bank to a portfolio already holding
three.

A deliberate difference from :class:`~tradeit.risk.contextual.SectorExposureRule`:
that rule pools every unclassified name into one bucket, because it *refuses*
and must fail closed. This one *orders*, and pooling here would push
unclassified names down the ranking as a group -- a penalty for thin sector
coverage rather than for concentration. An unattributable sector contributes no
crowding penalty; the hard limit still catches the concentration afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from tradeit.portfolio.base import AllocationCandidate, PortfolioState

__all__ = ["DiversityAwareRanker"]


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True, slots=True)
class DiversityAwareRanker:
    """Greedy selection that re-prices the remainder after every pick.

    ``correlation_weight`` scales the penalty applied for the worst correlation
    against an already-selected or already-held name.
    ``sector_penalty_per_holding`` is charged once for each prior name in the
    same sector, so the third bank costs twice what the second did.
    """

    correlation_weight: float
    sector_penalty_per_holding: float
    held_sectors: Mapping[int, str]
    name: str = "diversity_aware"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "correlation_weight": self.correlation_weight,
            "sector_penalty_per_holding": self.sector_penalty_per_holding,
            "classified_holdings": len(self.held_sectors),
        }

    def rank(
        self,
        candidates: Sequence[AllocationCandidate],
        portfolio: PortfolioState,
        correlation: dict[tuple[int, int], float] | None = None,
    ) -> list[AllocationCandidate]:
        eligible = [c for c in candidates if self._is_eligible(c)]
        ineligible = [c for c in candidates if not self._is_eligible(c)]

        # Names already held count against a candidate exactly as selected ones
        # do. A portfolio that is already three-quarters energy should not be
        # offered a fourth energy name just because the slate is empty.
        taken = [p.instrument_id for p in portfolio.open_positions]
        sector_counts: dict[str, int] = {}
        for instrument_id in taken:
            sector = self.held_sectors.get(instrument_id)
            if sector is not None:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

        ordered: list[AllocationCandidate] = []
        remaining = list(eligible)
        while remaining:
            priced = [
                replace(
                    candidate,
                    correlation_penalty=self._penalty(candidate, taken, sector_counts, correlation),
                )
                for candidate in remaining
            ]
            # Ties break on instrument id so a slate ranks identically twice.
            best = max(priced, key=lambda c: (c.adjusted_score, -c.score.instrument_id))
            ordered.append(best)
            taken.append(best.score.instrument_id)
            if best.sector is not None:
                sector_counts[best.sector] = sector_counts.get(best.sector, 0) + 1
            remaining = [c for c in remaining if c.score.instrument_id != best.score.instrument_id]

        return ordered + ineligible

    @staticmethod
    def _is_eligible(candidate: AllocationCandidate) -> bool:
        """Whether the candidate could receive capital at all.

        A non-positive score is excluded rather than penalised because
        ``adjusted_score`` multiplies by ``(1 - penalty)``: on a negative score
        that *raises* the value, so penalising a bad candidate would promote it.
        """
        return candidate.score.total > 0 and not candidate.sizing.rejected

    def _penalty(
        self,
        candidate: AllocationCandidate,
        taken: Sequence[int],
        sector_counts: Mapping[str, int],
        correlation: Mapping[tuple[int, int], float] | None,
    ) -> float:
        penalty = 0.0
        if candidate.sector is not None:
            penalty += self.sector_penalty_per_holding * sector_counts.get(candidate.sector, 0)
        instrument_id = candidate.score.instrument_id
        others = [other for other in taken if other != instrument_id]
        if correlation is not None and others:
            worst = max(
                # An omitted pair is unknown, and unknown is charged as though
                # it were perfectly correlated. See the module docstring.
                correlation.get(_pair(instrument_id, other), 1.0)
                for other in others
            )
            penalty += self.correlation_weight * max(worst, 0.0)
        return min(penalty, 1.0)
