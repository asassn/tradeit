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

``REGIME_BREAK``
    The price level and the traded volume both change by a large factor at one
    session boundary, with no split to explain it. What lies either side is not
    the same tradable thing. This is the last resort and the only rule that can
    reach a series with **no dormancy at all** -- the shape that defeated every
    other rule, because a vendor that never stops emitting rows leaves no hole
    to find.

**Absence of evidence closes nothing.** A series whose overrun no rule explains
returns ``UNRESOLVED`` and is left exactly as it is. That is a real answer here
in the same way ``UNRESOLVED`` is a real answer in the identity layer.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.storage.tables import SymbolAlias

__all__ = [
    "Adjudication",
    "BoundaryEvidence",
    "RegimeBreak",
    "Verdict",
    "adjudicate_series",
    "close_alias_interval",
    "detect_regime_break",
]


class BoundaryEvidence(StrEnum):
    """What a verdict rests on. Several may apply; each is recorded."""

    NO_OVERLAP = "no_overlap"
    DORMANCY = "dormancy"
    REGIME_BREAK = "regime_break"
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
    #: Price level and traded volume both break by a large factor at one date,
    #: with no split behind it. Deliberately **not** called a splice: the cause
    #: may be a ticker changing hands, but it may equally be the vendor stitching
    #: two sources or re-denominating a quote. What the evidence supports is that
    #: the two sides are not the same tradable thing -- which is what the cut
    #: needs -- and not a claim about which company each side is.
    REGIME_BREAK = "regime_break"
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

#: Sessions either side of a candidate break whose medians are compared. Three
#: months: long enough that one bad print cannot move a median, short enough to
#: place the break rather than smear it across a year.
REGIME_WINDOW = 60

#: How large the smaller of the two ratios -- price level, traded volume -- must
#: be before a break is called.
#:
#: **Chosen from a null test, not from taste.** Run inside each registrant's own
#: lifetime, where one company is there by construction, the detector scores at
#: most 49.6 across all 768 coherent series in the corpus: 9.6% of them reach 3,
#: 1.8% reach 5, one reaches 25, and **none reaches 50**. Fifty is the smallest
#: round threshold with no false positive in that population. Nought out of 768
#: bounds the false-positive rate below roughly 0.4% -- it does not establish
#: zero, and this comment exists so nobody later reads it as though it did.
REGIME_BREAK_THRESHOLD = 50.0

#: A split changes price and volume by design, so a break within a few sessions
#: of one is the corporate action and not a handover.
REGIME_SPLIT_EXCLUSION_DAYS = 5

#: How long after the anchor a resumption must fall before it can be read as a
#: different occupant. A vendor coverage hole that merely straddles the last
#: filing and resumes months later is a hole, not a handover.
RESUMPTION_GRACE_DAYS = 365


@dataclass(frozen=True, slots=True)
class RegimeBreak:
    """One session boundary at which the series stops being the same thing.

    ``score`` is the **smaller** of the price-level ratio and the volume ratio,
    and taking the smaller is the whole design. A penny stock's price triples
    routinely and an illiquid quote's volume jumps from nothing to something all
    the time; a series where *both* move by fifty times at one date is not one
    security behaving oddly. Requiring the weaker of the two to clear the bar
    means a large move in either alone establishes nothing.
    """

    session_date: dt.date
    score: float
    median_close_before: float
    median_close_after: float
    median_volume_before: float
    median_volume_after: float

    @property
    def summary(self) -> str:
        return (
            f"price level {self.median_close_before:g} -> {self.median_close_after:g} and "
            f"volume {self.median_volume_before:g} -> {self.median_volume_after:g} "
            f"across {self.session_date} (score {self.score:.1f}, no split within "
            f"{REGIME_SPLIT_EXCLUSION_DAYS} days)"
        )


def _ratio(before: float, after: float) -> float:
    return max(after / before, before / after) if before > 0 and after > 0 else 0.0


def detect_regime_break(
    bars: Sequence[tuple[dt.date, float, float]],
    *,
    after: dt.date | None,
    split_ex_dates: Collection[dt.date] = (),
    window: int = REGIME_WINDOW,
    threshold: float = REGIME_BREAK_THRESHOLD,
) -> RegimeBreak | None:
    """The strongest level-and-liquidity break at or after ``after``, if any.

    ``bars`` are ``(session_date, close, volume)`` in date order, closes
    positive. Medians are used rather than means throughout: one erroneous print
    in a thin series moves a mean and does not move a median, and these are the
    thinnest series in the corpus.

    Candidates start at ``after`` itself, because a ticker that changed hands the
    moment its registrant went quiet leaves no break *inside* the later run to
    find -- the break is at the anchor. Restricting the scan to interior points
    would miss exactly the cleanest case.
    """
    if len(bars) < 2 * window:
        return None
    excluded = dt.timedelta(days=REGIME_SPLIT_EXCLUSION_DAYS)
    scored: list[tuple[float, int]] = []
    for index in range(window, len(bars) - window + 1):
        if after is not None and bars[index][0] < after:
            continue
        before = bars[index - window : index]
        following = bars[index : index + window]
        close_ratio = _ratio(_median(b[1] for b in before), _median(b[1] for b in following))
        # Volumes are legitimately zero, so the ratio is taken on 1 + volume:
        # nothing-to-something is a real regime change and must not divide by
        # zero, while 0 -> 1 share must not read as infinite.
        volume_ratio = _ratio(
            1 + _median(b[2] for b in before), 1 + _median(b[2] for b in following)
        )
        scored.append((min(close_ratio, volume_ratio), index))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    if best_score < threshold:
        return None

    # **Windows detect; the adjacent bar locates.** A median over a window that
    # straddles a step keeps returning the majority side, so every candidate
    # from half a window before the break to half a window after it scores
    # identically. Taking the first of that plateau cuts up to sixty sessions
    # early and the last cuts them late; the discontinuity itself is the only
    # thing in the plateau that says where the break actually is.
    plateau = [index for score, index in scored if score >= best_score]
    index = max(plateau, key=lambda i: _ratio(bars[i - 1][1], bars[i][1]))

    # A split anywhere in the plateau explains the whole step, so the break is
    # refused outright rather than relocated to the plateau's edge -- which is
    # what relocating would amount to.
    span_lo, span_hi = bars[min(plateau)][0], bars[max(plateau)][0]
    if any(span_lo - excluded <= ex <= span_hi + excluded for ex in split_ex_dates):
        return None

    before = bars[index - window : index]
    following = bars[index : index + window]
    return RegimeBreak(
        session_date=bars[index][0],
        score=best_score,
        median_close_before=_median(b[1] for b in before),
        median_close_after=_median(b[1] for b in following),
        median_volume_before=_median(b[2] for b in before),
        median_volume_after=_median(b[2] for b in following),
    )


def _median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


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
            Verdict.REGIME_BREAK,
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
    regime_break: RegimeBreak | None = None,
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

    # 4. No hole to find, because the vendor never stopped emitting rows. Ask
    #    instead whether what it emitted stayed the same thing.
    if regime_break is not None and regime_break.session_date > anchor:
        successor = [d for d in ordered if d >= regime_break.session_date]
        registrant = [d for d in ordered if d < regime_break.session_date]
        if registrant and successor:
            return Adjudication(
                Verdict.REGIME_BREAK,
                registrant[-1],
                evidence=(BoundaryEvidence.REGIME_BREAK, *corroboration),
                kept_bars=len(registrant),
                dropped_bars=len(successor),
                note=regime_break.summary,
            )

    # 5. A reused symbol with no structural break: known wrong, not repairable.
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


def close_alias_interval(
    session: Session, security_id: int, boundary: dt.date, citation: str
) -> int:
    """Close this security's ticker intervals at ``boundary``. Returns how many moved.

    **The writer lives beside the rules rather than in a script** because two
    scripts now record boundaries -- the structural adjudication and the EDGAR
    successor search -- and a second copy of this is a second place for the
    invariants below to be forgotten. Which of them is right would then be
    decided by whichever ran last.

    Three refusals, each protecting something:

    * an interval already closed at or before ``boundary`` is **left alone**, so
      the earliest established close wins and re-running never widens a cut;
    * a boundary at or before ``valid_from`` is refused rather than written,
      because ``ck_alias_interval`` requires ``valid_to > valid_from`` and an
      empty interval is not a claim anybody can act on;
    * the citation is **appended**, never replacing what is there. The existing
      clause is the filing sentence that made the binding and is still true;
      overwriting it would destroy the provenance of the binding in order to
      record the provenance of its end.
    """
    moved = 0
    for alias in session.scalars(
        select(SymbolAlias).where(
            SymbolAlias.security_id == security_id,
            SymbolAlias.alias_kind == "ticker",
        )
    ).all():
        if alias.valid_to is not None and alias.valid_to <= boundary:
            continue
        if boundary <= alias.valid_from:
            continue
        alias.valid_to = boundary
        alias.citation = f"{alias.citation} | {citation}"
        moved += 1
    return moved
