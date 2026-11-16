"""The callout, and why it is still `UNRESOLVED` on complete evidence.

The roadmap wants *"undervalued / fairly valued / overvalued"* and attaches
the condition that **the callout is never shipped without the reasoning that
produced it**. The reasoning is what :mod:`tradeit.valuation.multiples` builds.
Turning it into one of three words needs something the reasoning cannot supply:
a decision about **how cheap counts as cheap**.

That is not an implementation detail and it is not derivable. The project's
rule for exactly this case is written down --- *"Do not alter denominator
methodology, detector thresholds, strategy parameters, backtesting assumptions
or trading logic without an explicit, scoped proposition"* --- and ADR-0031
established the way out where one exists: express the bar as a **test against
something already written**, so the number comes from the data rather than
being chosen. ADR-0034 is the case where that move was not available, and the
one remaining judgement went to the owner.

**This is an ADR-0034-shaped question, not an ADR-0031-shaped one.** There is
nothing already written that says a security in the cheapest fifth of its own
history is *undervalued*. A percentile cut is a statement about tolerable
false positives in a surface a person browses, and picking one here would be
fitting a bar to whatever the corpus happens to hold.

So the framework is complete and the label is withheld, using the mechanism
`tradeit.readiness` already has for a criterion whose number is still owed:
``ThresholdSource.UNAPPROVED``. The verdict reads ``UNRESOLVED`` and names what
it is waiting for, which puts the gap on the screen rather than filling it with
a number nobody chose.

**Why withholding is the safer failure.** A label is the most acted-upon thing
a research interface can display --- it is one word, it needs no
interpretation, and it will be read by somebody who did not build it. An
`UNRESOLVED` with its multiples visible beneath it is less satisfying and
cannot mislead. A provisional label, shipped with a note that the threshold is
tentative, would be read as the answer and the note would not survive its first
screenshot.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.readiness.criteria import ThresholdSource
from tradeit.research01.fundamental_signals import FundamentalHistory
from tradeit.valuation.multiples import (
    Multiple,
    PriceBar,
    Unresolved,
    price_to_book,
    price_to_earnings,
    rank_within,
)

__all__ = [
    "LABEL_THRESHOLD_SOURCE",
    "NOT_POINT_IN_TIME_BEFORE",
    "ValuationEvidence",
    "ValuationVerdict",
    "evidence_for",
]

#: Where the label's cut-off comes from. **Nobody has chosen one**, and this
#: being UNAPPROVED is what makes :attr:`ValuationEvidence.verdict` refuse.
LABEL_THRESHOLD_SOURCE = ThresholdSource.UNAPPROVED

#: Before roughly this year the corpus's fundamentals are **not**
#: point-in-time: `knowledge_time` begins in 2009 and the median gap from
#: `period_end` to `knowledge_time` is 1,574 days, with 97.6% exceeding 400,
#: because SEC's XBRL datasets restate history
#: (`RESEARCH_01_DATA_DICTIONARY.md`). A valuation history reaching back past
#: this is not what a reader could have seen at the time, and says so.
NOT_POINT_IN_TIME_BEFORE = dt.date(2013, 1, 1)


class ValuationVerdict(StrEnum):
    """The callout. Four values, and today only one of them is reachable."""

    UNDERVALUED = "undervalued"
    FAIRLY_VALUED = "fairly_valued"
    OVERVALUED = "overvalued"
    #: No label. Either the evidence does not support one, or --- the case
    #: today --- no threshold has been approved to turn evidence into a word.
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ValuationEvidence:
    """Everything the callout would rest on, and the callout's refusal.

    **There is no path from a number to a label that does not go through this
    object**, which is how the roadmap's *"never shipped without the reasoning
    that produced it"* is enforced rather than remembered: :attr:`verdict` is a
    property of the evidence, and nothing in this package exposes a function
    taking a percentile and returning a word.
    """

    security_id: int
    as_of: dt.date
    #: Successfully computed multiples.
    multiples: tuple[Multiple, ...]
    #: Multiples that could not be computed, each with its reason. Carried
    #: beside the successes rather than dropped: "we did not compute a P/E"
    #: and "this company loses money" are different facts and the second is
    #: the more useful one.
    unresolved: tuple[Unresolved, ...]
    #: Each multiple's rank in the security's own history, 0 to 1, lower being
    #: cheaper. Keyed by basis; absent where there is no history to rank in.
    own_history_rank: dict[str, float]
    #: How many past observations each rank was computed against.
    own_history_size: dict[str, int]
    caveats: tuple[str, ...]

    @property
    def verdict(self) -> ValuationVerdict:
        """Always :attr:`ValuationVerdict.UNRESOLVED` today, and deliberately.

        Not a stub. The evidence above may be complete and this still refuses,
        because the missing piece is an approval rather than a measurement.
        When a threshold is approved this becomes a comparison against it, and
        the branch that returns a label will be reachable only from here ---
        so it will never be reachable without the evidence beside it.
        """
        return ValuationVerdict.UNRESOLVED

    @property
    def owed(self) -> str | None:
        """What the verdict is waiting for, in one sentence, or ``None``.

        Displayed instead of the label. A reader who sees `UNRESOLVED` with no
        explanation concludes the data is missing; the data may be complete and
        the decision missing, and those call for different actions.
        """
        if LABEL_THRESHOLD_SOURCE is not ThresholdSource.UNAPPROVED:
            return None
        if not self.multiples:
            return (
                "no multiple could be computed, so there is nothing to label "
                "even once a threshold is approved"
            )
        return (
            "the multiples below are computed and the label is not: no approved "
            "threshold says how cheap counts as undervalued. Approving one is a "
            "product decision, not a measurement"
        )

    @property
    def explanations(self) -> tuple[str, ...]:
        """One checkable line per computed multiple."""
        return tuple(multiple.explanation for multiple in self.multiples)


def evidence_for(
    security_id: int,
    price: PriceBar,
    history: FundamentalHistory,
    on: dt.date,
    *,
    past_prices: Sequence[PriceBar] = (),
) -> ValuationEvidence:
    """Assemble one security's valuation evidence as of ``on``.

    ``past_prices`` are earlier sessions used to build the security's own
    valuation history: each is re-paired with the fundamentals that were usable
    *on that session*, so the history is a series of point-in-time multiples
    rather than today's fundamentals projected backwards. Doing it the cheap
    way --- today's earnings against last year's price --- would make every
    company that has grown look like it was once expensive, which is an
    artefact of the method and not a fact about the company.

    Sector-relative comparison is **not** here and is owed. It needs the
    sector population resolved as of ``on``, which is a corpus-wide read rather
    than a per-security one, and pretending a two-way comparison is a three-way
    one would overstate the evidence beneath a label this package already
    refuses to print.
    """
    computed: list[Multiple] = []
    refused: list[Unresolved] = []
    for result in (
        price_to_earnings(price, history, on),
        price_to_book(price, history, on),
    ):
        if isinstance(result, Multiple):
            computed.append(result)
        else:
            refused.append(result)

    ranks: dict[str, float] = {}
    sizes: dict[str, int] = {}
    for multiple in computed:
        population: list[float] = []
        for past in past_prices:
            if past.session_date >= on:
                continue
            earlier = (
                price_to_earnings(past, history, past.session_date)
                if multiple.basis == "price_to_earnings"
                else price_to_book(past, history, past.session_date)
            )
            if isinstance(earlier, Multiple):
                population.append(earlier.value)
        rank = rank_within(multiple.value, population)
        if rank is not None:
            ranks[multiple.basis] = rank
            sizes[multiple.basis] = len(population)

    caveats: list[str] = []
    if price.basis != "raw":
        caveats.append(
            f"the price is on the '{price.basis}' basis; a multiple needs the raw "
            "print, because an adjusted close is divided by a per-share figure "
            "reported in the share count of its own time"
        )
    earliest_used = min(
        (past.session_date for past in past_prices if past.session_date < on),
        default=None,
    )
    if earliest_used is not None and earliest_used < NOT_POINT_IN_TIME_BEFORE:
        caveats.append(
            f"the history reaches {earliest_used}, before roughly "
            f"{NOT_POINT_IN_TIME_BEFORE.year}, where this corpus's fundamentals are "
            "not point-in-time: SEC's XBRL datasets restate history, so those "
            "multiples are not what a reader could have seen at the time"
        )
    if not computed:
        caveats.append("no multiple resolved; the reasons are listed beside them")

    return ValuationEvidence(
        security_id=security_id,
        as_of=on,
        multiples=tuple(computed),
        unresolved=tuple(refused),
        own_history_rank=ranks,
        own_history_size=sizes,
        caveats=tuple(caveats),
    )
