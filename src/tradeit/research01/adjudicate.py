"""Deciding, on evidence, where a price series stops belonging to its registrant.

**What this is for.** ``series.py`` measured that 9.2% of the corpus's series
end more than seven years after their registrant's last EDGAR filing, and
returned :class:`~tradeit.research01.series.Coherence` as a *report*: the shape
is wrong, and truncating on a heuristic would have discarded legitimate
post-delisting trading or hidden a splice instead of naming it. This module is
the next step and a different act. It asks what the evidence establishes about
each of those series, reaches a verdict, and where a boundary can be located it
records that boundary in the corpus with a citation.

**Filtering on a cited, recorded interval is not filtering on a heuristic.**
That distinction is the whole reason these are two modules.

The evidence, strongest first
-----------------------------

``NO_OVERLAP``
    The series holds no bar from the registrant's lifetime at all -- the first
    print postdates the last filing by years. Nothing here can belong to this
    registrant, so there is no boundary to find: the whole series is another
    company's. Four such series exist and one of them is a single bar.

``DORMANCY``
    The vendor has **no row whatsoever** for months, and then a substantial run
    resumes. This is stronger than it looks, because the vendor emits
    zero-volume rows for sessions in which a security did not trade -- 19.7% of
    the corpus is such rows. An absent row therefore means an absent *security*,
    not a quiet one. It is also the only evidence here that *locates* a
    boundary rather than merely establishing that one exists.

``REGISTRY_REUSE``
    ``company_tickers.json`` shows the plain ticker belonging to a different CIK
    today. Direct registry evidence that the symbol was re-issued -- but it
    dates nothing, so on its own it establishes contamination without saying
    where it starts. That combination has its own verdict rather than a guess.

    **And it is meaningless unless the series overruns.** A first version of
    this asked the registry question of every series and returned 36 corpus
    securities as contaminated -- none of which was among the 79 the measurement
    had flagged. Every one was a vendor ``_OLD`` symbol, for which "somebody
    else holds the plain ticker today" is true *by construction*: that is what
    the suffix means. The vendor had already separated those series correctly.
    An answer arrived, and it was not an answer to the question asked. Reuse is
    now consulted only where the series itself runs past the registrant.

``FILED_EXIT``
    A confirmed, dated exit from the EDGAR denominator. Corroborates a dormancy
    by explaining it: the vendor lost the series *because* the registrant left.
    It never supplies the boundary by itself -- a company can be delisted and go
    on trading over the counter, which is precisely the case this must not cut.

**Absence of evidence closes nothing.** A series whose overrun no rule explains
returns ``UNRESOLVED`` and is left exactly as it is. That is a real answer here
in the same way ``UNRESOLVED`` is a real answer in the identity layer.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Adjudication",
    "BoundaryEvidence",
    "Verdict",
    "adjudicate_series",
]


class BoundaryEvidence(StrEnum):
    """What a verdict rests on. Several may apply; each is recorded."""

    NO_OVERLAP = "no_overlap"
    DORMANCY = "dormancy"
    REGISTRY_REUSE = "registry_reuse"
    FILED_EXIT = "filed_exit"


class Verdict(StrEnum):
    """What the evidence establishes about one suspect series."""

    #: The series ends within the grace window of the registrant's last known
    #: activity. Nothing to adjudicate. **Not a certificate**: a splice wholly
    #: inside the registrant's own lifetime leaves no trace in the shape, and
    #: this verdict says only that the ending does not betray one.
    COHERENT = "coherent"
    #: No bar predates the registrant's exit era. The series is another
    #: company's in its entirety, not a splice of two.
    WHOLLY_MISATTRIBUTED = "wholly_misattributed"
    #: A dormancy separates the registrant's era from a substantial later run.
    #: The boundary is the last bar before the dormancy.
    SPLICE_LOCATED = "splice_located"
    #: A dormancy separates the registrant's era from a handful of prints.
    #: Same cut, different conclusion: a few stale rows, not a second company.
    TAIL_ARTEFACT = "tail_artefact"
    #: Another registrant demonstrably holds the symbol, but nothing dates the
    #: handover. The series is known to be wrong and cannot be repaired, which
    #: is worse than either of the located cases and is named separately so it
    #: cannot be mistaken for one.
    CONTAMINATED_BOUNDARY_UNKNOWN = "contaminated_boundary_unknown"
    #: The overrun is unexplained. Left intact, flagged, and not cut.
    UNRESOLVED = "unresolved"


#: Calendar days with no vendor row at all before the absence counts as the
#: security having left rather than having traded quietly. Six months. The
#: longest closure of the modern US market is four sessions (September 2001,
#: which shows up in this corpus as a seven-day hole and must never be read as
#: a boundary), and a no-trade session in an illiquid name arrives as a
#: zero-volume row rather than as nothing at all.
DORMANCY_DAYS = 180

#: Bars after the boundary below which the tail is a few stale prints rather
#: than a second company's trading history.
MIN_SECOND_RUN = 20

#: How long after the anchor a resumption must fall before it can be read as a
#: different occupant. A vendor coverage hole that merely straddles the last
#: filing and resumes months later is a hole, not a handover.
RESUMPTION_GRACE_DAYS = 365


@dataclass(frozen=True, slots=True)
class Adjudication:
    """The verdict on one series, with what it rests on and what it costs.

    ``boundary`` is the **last session that still belongs to the registrant**,
    so an interval closed at it is inclusive. ``None`` means no boundary was
    located, which is a different statement from "there is none".
    """

    verdict: Verdict
    boundary: dt.date | None
    evidence: tuple[BoundaryEvidence, ...] = ()
    kept_bars: int = 0
    dropped_bars: int = 0
    #: Free text, composed from the measurements. Never a paraphrase of a
    #: filing -- there is no filing behind a structural finding, and pretending
    #: otherwise would forge provenance.
    note: str = ""
    dormancy_days: int | None = field(default=None)

    @property
    def is_actionable(self) -> bool:
        """Whether a boundary can be written to the corpus.

        ``WHOLLY_MISATTRIBUTED`` is actionable *without* a boundary inside the
        series: the interval closes at the anchor and nothing survives.
        """
        return self.verdict in {
            Verdict.WHOLLY_MISATTRIBUTED,
            Verdict.SPLICE_LOCATED,
            Verdict.TAIL_ARTEFACT,
        }


def _anchor(last_filing: dt.date | None, filed_exit: dt.date | None) -> dt.date | None:
    """The earliest date at which the symbol could have passed to someone else.

    The **later** of the two known dates, never the earlier. A registrant that
    filed until 2005 demonstrably existed until 2005 whatever a Form 25 from
    2003 says, and anchoring on the earlier date would authorise cutting away
    trading the company really did.
    """
    dates = [d for d in (last_filing, filed_exit) if d is not None]
    return max(dates) if dates else None


def adjudicate_series(
    *,
    sessions: Sequence[dt.date],
    last_filing: dt.date | None,
    filed_exit: dt.date | None = None,
    registrant_cik: int | None = None,
    current_holder_cik: int | None = None,
    dormancy_days: int = DORMANCY_DAYS,
    min_second_run: int = MIN_SECOND_RUN,
) -> Adjudication:
    """Decide what one series' overrun is, on the evidence supplied.

    Pure: it reads no database and fetches nothing, so every threshold above is
    visible in one place and a test can drive any shape through it.
    """
    if not sessions:
        return Adjudication(Verdict.UNRESOLVED, None, note="no sessions")
    ordered = sorted(set(sessions))
    anchor = _anchor(last_filing, filed_exit)
    if anchor is None:
        # Without an anchor there is nothing to measure the overrun against.
        # Not coherent -- unknown, which is why it is not silently passed.
        return Adjudication(
            Verdict.UNRESOLVED,
            None,
            kept_bars=len(ordered),
            note="registrant filing span unknown; overrun cannot be measured",
        )

    reused = (
        current_holder_cik is not None
        and registrant_cik is not None
        and current_holder_cik != registrant_cik
    )
    corroboration: list[BoundaryEvidence] = []
    if filed_exit is not None:
        corroboration.append(BoundaryEvidence.FILED_EXIT)
    if reused:
        corroboration.append(BoundaryEvidence.REGISTRY_REUSE)

    grace = anchor + dt.timedelta(days=RESUMPTION_GRACE_DAYS)

    # 1. Nothing to adjudicate: the series ends where the registrant did. This
    #    gate comes first because every rule below reads a shape that only means
    #    something once the series has outlived its registrant.
    if ordered[-1] <= grace:
        return Adjudication(
            Verdict.COHERENT,
            None,
            kept_bars=len(ordered),
            note=f"series ends {ordered[-1]}, within the grace window of {anchor}",
        )

    # 2. Does anything here belong to the registrant at all?
    if ordered[0] > grace:
        return Adjudication(
            Verdict.WHOLLY_MISATTRIBUTED,
            None,
            evidence=(BoundaryEvidence.NO_OVERLAP, *corroboration),
            kept_bars=0,
            dropped_bars=len(ordered),
            note=(
                f"first session {ordered[0]} postdates the registrant's last known "
                f"activity {anchor} by {(ordered[0] - anchor).days / 365.25:.1f} years; "
                f"no bar in the series falls within the registrant's lifetime"
            ),
        )

    # 3. The earliest dormancy whose resumption lands after the grace window.
    #    Earliest, because everything after the first genuine break is suspect.
    for previous, following in itertools.pairwise(ordered):
        gap = (following - previous).days
        if gap < dormancy_days or following <= grace:
            continue
        after = [d for d in ordered if d >= following]
        kept = len(ordered) - len(after)
        verdict = Verdict.SPLICE_LOCATED if len(after) >= min_second_run else Verdict.TAIL_ARTEFACT
        return Adjudication(
            verdict,
            previous,
            evidence=(BoundaryEvidence.DORMANCY, *corroboration),
            kept_bars=kept,
            dropped_bars=len(after),
            dormancy_days=gap,
            note=(
                f"no vendor row for {gap} days between {previous} and {following}; "
                f"resumption is {(following - anchor).days / 365.25:.1f} years after the "
                f"registrant's last known activity {anchor}"
            ),
        )

    # 4. A reused symbol with no structural break: known wrong, not repairable.
    if reused:
        return Adjudication(
            Verdict.CONTAMINATED_BOUNDARY_UNKNOWN,
            None,
            evidence=tuple(corroboration),
            kept_bars=len(ordered),
            note=(
                f"CIK {current_holder_cik} holds this symbol today, not CIK {registrant_cik}, "
                f"but the series runs unbroken past {anchor} and nothing dates the handover"
            ),
        )

    return Adjudication(
        Verdict.UNRESOLVED,
        None,
        evidence=tuple(corroboration),
        kept_bars=len(ordered),
        note=(
            f"series runs to {ordered[-1]}, "
            f"{(ordered[-1] - anchor).days / 365.25:.1f} years past {anchor}, "
            f"with no dormancy of {dormancy_days} days or more to separate two occupants"
        ),
    )
