"""Why a forward symbol stopped printing — exit, or still alive under a new name?

**The defect this exists to stop.** A security that stops printing is recorded
as silent, and silence is how the forward record observes a delisting. But a
company that *renames and changes ticker* also stops printing under the old
symbol — and recording that as an exit puts a **false delisting** into the one
record built to be survivorship-honest. That is the corpus's own defect,
reproduced forward, where it was supposed to be impossible.

It is not hypothetical. Measured 2026-11-15 on a record 17 sessions old, with
four silent symbols:

====  ====================================================  ==================
sym   what EDGAR says                                       what it means
====  ====================================================  ==================
ELME  ``25-NSE`` 2026-10-26, ``15-12G`` 2026-11-06           a real exit
MGPI  no 25, no 15; filer renamed; ``4`` filed 2026-11-12    **not an exit**
====  ====================================================  ==================

MGPI renamed and kept trading under another ticker.
`FORWARD_EXIT_ADJUDICATION_2026-11-14.md` reached the same conclusion by hand
the day before — *"do not record a survivorship exit for MGPI"* — after a long
document of manual filing reads. This is that reasoning made repeatable.

**Report only. It writes nothing and decides nothing.** A verdict here is a lead
for a person, and the adjudication that settles identity stays manual:
`MANUAL_VERIFIED` means a person read the filing, and
`tradeit.edgar.acquire` says so structurally.

Why it could not be automated before
------------------------------------

Not the algorithm — :mod:`tradeit.edgar.exit_cause` has classified exits from
EDGAR evidence for months. **The local index was three months stale and nothing
said so.** Measured 2026-11-15: 2026 QTR3 had been downloaded mid-quarter and
held filings only to 2026-08-13; QTR4 did not exist. Every forward silence falls
in that hole, so the classifier returned "no exit found" for MGPI — a confident
wrong answer produced by missing data rather than by evidence.

So **index age is checked first and the check fails closed.** If the newest
filing the index holds predates the silence, the verdict is ``UNRESOLVED`` and
the reason says so. *"The index cannot speak to this"* and *"the filings show no
exit"* are different answers and must never be returned as the same one — which
is the whole `UNRESOLVED`-is-first-class discipline, applied to the one place it
had quietly stopped applying.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "EXIT_FORM_PREFIXES",
    "EXIT_WINDOW",
    "SilenceFinding",
    "SilenceVerdict",
    "classify_silence",
]

#: Forms that certify a security leaving the public market. ``25`` and
#: ``25-NSE`` notify an exchange delisting; ``15``, ``15-12B``, ``15-12G`` and
#: ``15F-*`` certify deregistration. Matched by prefix because EDGAR renders
#: several variants and **quoted form types are never normalised** -- `10-K` is
#: not `10-K405`, and the same rule applies here.
EXIT_FORM_PREFIXES = ("25", "15")

#: How far from the stop an exit filing still counts, in days either side. A
#: Form 25 precedes the last print and a Form 15 follows it, so the window is
#: deliberately two-sided: ELME's ``25-NSE`` came 10 days before its last print
#: and its ``15-12G`` one day after.
EXIT_WINDOW = (45, 45)


class SilenceVerdict(StrEnum):
    """What the filings say about a symbol that stopped printing."""

    #: A Form 25 or Form 15 near the stop. The silence is a real exit.
    EXIT_CORROBORATED = "exit_corroborated"
    #: The registrant kept filing after the stop and **its name changed**. A
    #: strong lead that the ticker moved rather than the company ending.
    RENAMED_AND_STILL_FILING = "renamed_and_still_filing"
    #: The registrant kept filing after the stop under the same name. Not an
    #: exit either -- but the reason the prices stopped is not in these forms.
    STILL_FILING = "still_filing"
    #: The index cannot answer. **Never conflated with "no exit found".**
    UNRESOLVED = "unresolved"

    @property
    def contradicts_an_exit(self) -> bool:
        """Whether writing this silence into a corpus as a delisting would be wrong.

        ``UNRESOLVED`` is deliberately **not** included. Not knowing is not the
        same as knowing it is alive, and a caller that treated the two alike
        would reintroduce the guess this module exists to remove.
        """
        return self in (SilenceVerdict.RENAMED_AND_STILL_FILING, SilenceVerdict.STILL_FILING)


@dataclass(frozen=True, slots=True)
class SilenceFinding:
    """One silent symbol, and what the filing index says about it."""

    symbol: str
    cik: int | None
    stop: dt.date | None
    verdict: SilenceVerdict
    #: Why, in a sentence a person can act on. Always populated, including --
    #: especially -- for ``UNRESOLVED``, where *which* gap stopped the answer is
    #: the actionable part.
    reason: str
    evidence: tuple[str, ...] = ()

    def describe(self) -> str:
        head = f"{self.symbol:<8} {self.verdict:<26} {self.reason}"
        return "\n".join([head, *(f"           {item}" for item in self.evidence)])


def _is_exit_form(form: str) -> bool:
    bare = form.strip().upper()
    return any(
        bare == p or bare.startswith(f"{p}-") or bare.startswith(f"{p}F")
        for p in EXIT_FORM_PREFIXES
    )


def classify_silence(
    symbol: str,
    cik: int | None,
    stop: dt.date | None,
    filings: Sequence[tuple[str, dt.date, str]],
    *,
    index_through: dt.date | None,
    window: tuple[int, int] = EXIT_WINDOW,
) -> SilenceFinding:
    """Classify one symbol that stopped printing on ``stop``.

    ``filings`` is ``(form type, filed date, filer name)`` for the registrant,
    in any order. ``index_through`` is the newest filing date the index holds
    *overall* -- not for this registrant -- which is what says whether the index
    is new enough to be asked.

    **Checked in this order, and the order is the design:**

    1. No CIK, or no stop → ``UNRESOLVED``. Nothing can be said.
    2. ``index_through`` before the stop → ``UNRESOLVED``. The index predates
       the event; "no exit found" here would be a statement about our download
       schedule dressed up as a statement about the company.
    3. A Form 25 or 15 within the window → ``EXIT_CORROBORATED``. Checked
       **before** the still-filing tests, because a deregistering company files
       on the way out: ELME filed three 8-Ks and a ``15-12G`` after its last
       print, and a still-filing test that ran first would have called a
       completed dissolution alive.
    4. Filings after the stop → still filing, and **renamed** when the filer
       name differs across the stop.
    5. Otherwise ``UNRESOLVED``.
    """
    if cik is None:
        return SilenceFinding(
            symbol, cik, stop, SilenceVerdict.UNRESOLVED, "no CIK resolves for this symbol"
        )
    if stop is None:
        return SilenceFinding(
            symbol,
            cik,
            stop,
            SilenceVerdict.UNRESOLVED,
            "the symbol never printed, so there is no stop to explain",
        )
    if index_through is None or index_through < stop:
        held = index_through.isoformat() if index_through else "nothing"
        return SilenceFinding(
            symbol,
            cik,
            stop,
            SilenceVerdict.UNRESOLVED,
            f"the filing index reaches only {held}, before the silence on {stop}; "
            "refresh it before reading this as evidence of anything",
        )

    # No `before` list: the rename test compares across the whole window rather
    # than across the stop, for the reason recorded below.
    after = [f for f in filings if f[1] > stop]
    exits = [
        f
        for f in filings
        if _is_exit_form(f[0])
        and stop - dt.timedelta(days=window[0]) <= f[1] <= stop + dt.timedelta(days=window[1])
    ]
    if exits:
        return SilenceFinding(
            symbol,
            cik,
            stop,
            SilenceVerdict.EXIT_CORROBORATED,
            f"{len(exits)} exit filing(s) within {window[0]}/{window[1]} days of the stop",
            tuple(
                f"{form} filed {day} by {name}"
                for form, day, name in sorted(exits, key=lambda f: f[1])
            ),
        )
    if after:
        # **The rename is compared across the whole window, not across the
        # stop.** The obvious test -- does the name after the stop differ from
        # the name before it -- was the first version and it *missed MGPI*,
        # which is the case this module was written for. MGPI's registrant
        # began filing as `SEMPREON, INC` on 2026-11-06 and the vendor kept
        # printing MGPI bars until 2026-11-10, so the new name sat on both
        # sides of the stop and the straddle test saw no change.
        #
        # A ticker follows a rename by days or weeks; it does not change on the
        # same morning. Any test anchored on the stop therefore misses the
        # ordinary case, and the honest comparison is earliest name in the
        # window against latest.
        ordered = sorted(filings, key=lambda f: f[1])
        first_name, last_name = ordered[0][2].strip(), ordered[-1][2].strip()
        renamed = bool(first_name) and first_name.casefold() != last_name.casefold()
        evidence = tuple(
            f"{form} filed {day} by {name}"
            for form, day, name in sorted(after, key=lambda f: f[1])[-4:]
        )
        if renamed:
            return SilenceFinding(
                symbol,
                cik,
                stop,
                SilenceVerdict.RENAMED_AND_STILL_FILING,
                f"no exit filing, and the registrant's filings move from {first_name!r} "
                f"to {last_name!r} across this window: the ticker probably moved, so "
                "this silence is very likely NOT a delisting",
                evidence,
            )
        return SilenceFinding(
            symbol,
            cik,
            stop,
            SilenceVerdict.STILL_FILING,
            f"no exit filing and {len(after)} filing(s) after the stop: the registrant "
            "is alive, so this silence is not a delisting and its cause is elsewhere",
            evidence,
        )
    return SilenceFinding(
        symbol,
        cik,
        stop,
        SilenceVerdict.UNRESOLVED,
        "the index holds no filing after the stop and no exit filing near it",
    )


def classify_silences(
    silent: Mapping[str, tuple[int | None, dt.date | None]],
    filings_by_cik: Mapping[int, Sequence[tuple[str, dt.date, str]]],
    *,
    index_through: dt.date | None,
) -> list[SilenceFinding]:
    """Classify every silent symbol, worst-news first.

    Ordered so the finding that would corrupt the record -- a silence that is
    **not** an exit -- is read before the ones that are routine. A report whose
    first line is the boring case is a report read to the second line.
    """
    order = {
        SilenceVerdict.RENAMED_AND_STILL_FILING: 0,
        SilenceVerdict.STILL_FILING: 1,
        SilenceVerdict.UNRESOLVED: 2,
        SilenceVerdict.EXIT_CORROBORATED: 3,
    }
    findings = [
        classify_silence(
            symbol, cik, stop, list(filings_by_cik.get(cik or -1, ())), index_through=index_through
        )
        for symbol, (cik, stop) in silent.items()
    ]
    findings.sort(key=lambda f: (order[f.verdict], f.symbol))
    return findings


def index_through(rows: Iterable[tuple[str, dt.date, str]]) -> dt.date | None:
    """The newest filing date an index holds, measured rather than declared.

    Read from the rows themselves instead of the file's ``Last Data Received``
    header: the header is the vendor's claim about its own file, and this module
    exists because a claim like that went three months out of date without
    anyone noticing. ``None`` when there are no rows at all.
    """
    newest: dt.date | None = None
    for _form, day, _name in rows:
        if newest is None or day > newest:
            newest = day
    return newest
