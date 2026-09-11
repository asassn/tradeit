"""Why a security's prices stopped, as far as the registrant's own filings say.

**Why this exists.** The survivorship backtest in ``SIGNAL_SCOREBOARD.md`` §15
found that the bias it measures lies anywhere between about zero and 57
percentage points, and that the *delisting recovery assumption* decides where.
An acquisition pays the holder roughly the last price; a bankruptcy pays close
to nothing; the engine could not tell them apart, so recovery was a single
number the caller had to choose. This replaces the choice with evidence,
security by security, and says how strong each piece is.

**What it reads.** Two things, both from EDGAR, neither interpreted beyond what
it states:

* the full-index **form types and dates** -- a merger proxy, a tender offer, a
  going-private schedule, a late-filing notice, a Form 15 or 25;
* each 8-K's **header items** (:mod:`tradeit.edgar.eightk`) -- bankruptcy,
  change in control, completion of an acquisition or disposition.

**What it refuses to conclude.** A merger proxy is a proposal, not an outcome:
alone it makes an acquisition *indicated*, never established. A completion 8-K
alone is ambiguous -- item 2.01 on an acquirer's filing means it bought
something. Only the two together make ``ACQUIRED``. Stopping filing is a lead,
not a death, exactly as :mod:`tradeit.edgar.evidence` rules. And an 8-K whose
header was not read is *unread*, not *empty*: every finding carries how many of
its window's 8-Ks were actually seen, so "no bankruptcy found" can be told apart
from "did not look".

**Bankruptcy outranks acquisition.** An equity holder's recovery in a Chapter
11 is governed by the bankruptcy even when the assets are then sold -- a
section 363 sale looks like an acquisition in the filings and pays the
shareholders nothing. When both appear, the finding is ``BANKRUPT`` and the
conflict is recorded rather than resolved silently.

None of this is ``MANUAL_VERIFIED``, and nothing here pretends to be: a program
read these headers, and a person has not.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.edgar.eightk import EightKHeader, ItemMeaning, item_meanings
from tradeit.edgar.evidence import EXCHANGE_ACT_PERIODIC_FORMS, EvidenceStrength

__all__ = [
    "BANKRUPTCY_WINDOW",
    "COMPLETION_WINDOW",
    "DEREGISTRATION_WINDOW",
    "DISTRESS_WINDOW",
    "EIGHT_K_WINDOW",
    "EXTINGUISHED_HOLDERS",
    "POINTER_WINDOW",
    "REPORTING_AFTER",
    "ExitCause",
    "ExitCauseFinding",
    "classify_exit",
]


class ExitCause(StrEnum):
    """What the filings say ended the security's trading, strongest first."""

    #: An 8-K reporting bankruptcy or receivership near the stop.
    BANKRUPT = "bankrupt"
    #: A transaction pointer before the stop **and** evidence it closed at it --
    #: a completion 8-K, or a Form 15 certifying the class has no public holders.
    ACQUIRED = "acquired"
    #: One of the two, not both.
    ACQUISITION_INDICATED = "acquisition_indicated"
    #: A Form 15 certifying zero or one holder of record, with no transaction
    #: pointer and no bankruptcy. The public holders were converted or cashed
    #: out; the filings do not say by whom. Read only after bankruptcy has been
    #: ruled out, because a reorganisation plan cancels old shares too.
    EXTINGUISHED = "extinguished"
    #: The registrant filed periodic reports well after its prices stopped. The
    #: series ended; the company did not -- a ticker change, an exchange move,
    #: a drop to the pink sheets the corpus does not follow.
    KEPT_REPORTING = "kept_reporting"
    #: Late-filing notices, or a delisting notice with no transaction behind it.
    DISTRESS_INDICATED = "distress_indicated"
    #: A Form 15 or 25 and nothing that says why.
    DEREGISTERED_UNEXPLAINED = "deregistered_unexplained"
    UNRESOLVED = "unresolved"


#: Days relative to the stop, (before, after). Each is a research window, not a
#: detector threshold: it bounds where evidence is looked for and changes no
#: strategy. Stated as constants so a run can report how many findings move
#: when one is widened.
#:
#: Merger proxies and tender offers precede a close by weeks to months; eighteen
#: months is generous on purpose, and a proposal filed long after the prices
#: stopped cannot be what stopped them, hence only a month of grace after.
POINTER_WINDOW = (548, 30)
#: The completion 8-K is filed within days of the close; the index date can lag
#: the last print by a few weeks either way.
COMPLETION_WINDOW = (30, 60)
#: A Chapter 11 filer often keeps trading for months before the exchange delists
#: it, and one delisted first may file after -- both govern the holder.
BANKRUPTCY_WINDOW = (365, 180)
#: Late-filing notices are the classic precursor of a delisting for cause.
DISTRESS_WINDOW = (365, 90)
#: The union of the 8-K windows above: the headers a caller must fetch.
EIGHT_K_WINDOW = (365, 180)
#: A periodic report this long after the stop means the registrant went on.
REPORTING_AFTER = 180
#: Where a deregistration is looked for, and whose Form 15 is read.
DEREGISTRATION_WINDOW = (180, 365)
#: Zero or one: after a merger the only holder is the acquirer. Not a tuned
#: threshold -- any count above one means public holders remain.
EXTINGUISHED_HOLDERS = 1

_POINTERS = frozenset({"DEFM14A", "DEFM14C", "SC TO-T", "SC 14D9", "SC 13E3", "425"})
#: The pointers addressed to the *shareholders* -- a vote on a merger, a tender
#: for their shares, a going-private schedule. A 425 is excluded: it is deal
#: communication, often the acquirer's, and says nothing about who is paid.
#:
#: They matter because a bankruptcy item says what kind of proceeding a filing
#: reports, never **whose**: the legacy item 3 covers "the registrant or its
#: parent". Conning's 1999 8-Ks disclosed its parent GenAmerica's insurance
#: receivership; MetLife then tendered for Conning's shares in March 2000 and
#: the holders were paid. Nobody tenders for equity a bankruptcy has wiped out,
#: so an offer to shareholders *after* the bankruptcy item overrides it -- and
#: across 1,737 securities that happens exactly once, which is recorded rather
#: than assumed. An offer *before* the item is a deal that failed first: seven
#: of those, Edge Petroleum's collapsed Chaparral merger among them.
_SHAREHOLDER_OFFERS = frozenset({"DEFM14A", "DEFM14C", "SC TO-T", "SC 14D9", "SC 13E3"})
_LATE = frozenset({"NT 10-K", "NT 10-Q", "NT 10-K405", "NT 20-F"})
_DEREGISTRATION = frozenset(
    {"15-12B", "15-12G", "15-15D", "15F-12B", "15F-12G", "15F-15D", "25", "25-NSE"}
)
_COMPLETION = frozenset(
    {
        ItemMeaning.CHANGE_IN_CONTROL,
        ItemMeaning.ACQUISITION_OR_DISPOSITION_COMPLETED,
        ItemMeaning.SECURITY_RIGHTS_MODIFIED,
    }
)


@dataclass(frozen=True, slots=True)
class ExitCauseFinding:
    cause: ExitCause
    strength: EvidenceStrength
    #: Human-readable, each naming the form and date it rests on.
    evidence: tuple[str, ...]
    #: Evidence pointing another way, kept rather than discarded.
    conflicts: tuple[str, ...]
    #: 8-Ks the index lists inside :data:`EIGHT_K_WINDOW`, and how many of their
    #: headers were read. A finding with unread 8-Ks has not ruled out what they
    #: might have said.
    eightks_listed: int
    eightks_read: int

    @property
    def fully_read(self) -> bool:
        return self.eightks_read >= self.eightks_listed


def _within(day: dt.date, stop: dt.date, window: tuple[int, int]) -> bool:
    before, after = window
    return stop - dt.timedelta(days=before) <= day <= stop + dt.timedelta(days=after)


def classify_exit(
    stop: dt.date,
    filings: Iterable[tuple[str, dt.date]],
    eightks: Sequence[EightKHeader],
    *,
    bankruptcy_text: Mapping[str, bool] | None = None,
    form15_holders: Sequence[tuple[dt.date, int | None]] = (),
) -> ExitCauseFinding:
    """Classify one security whose prices stopped on ``stop``.

    ``filings`` is every (form type, filing date) the index holds for the
    registrant. ``eightks`` is the headers actually read -- already checked to
    belong to this registrant's CIK, which is the caller's job because only the
    caller knows which CIK it asked about.

    ``bankruptcy_text`` maps the accession of each 8-K whose header declares a
    bankruptcy item to whether the filing's text carries the heading too. **A
    bankruptcy header counts only when its document agrees**: one that
    disagrees is recorded as a conflict, and one whose document was not read is
    recorded as unverified. Neither makes the finding ``BANKRUPT``.

    ``form15_holders`` is (filing date, holders of record) for each Form 15 the
    caller read, ``None`` where the count could not be read.
    """
    texts = bankruptcy_text or {}
    listed = 0
    pointers: list[str] = []
    offers: list[dt.date] = []
    late: list[str] = []
    dereg: list[str] = []
    later_periodic: list[dt.date] = []
    recent_periodic = False
    for form, day in filings:
        form = form.strip().upper()
        if form in ("8-K", "8-K/A") and _within(day, stop, EIGHT_K_WINDOW):
            listed += 1
        if form in _POINTERS and _within(day, stop, POINTER_WINDOW):
            pointers.append(f"{form} {day}")
            if form in _SHAREHOLDER_OFFERS:
                offers.append(day)
        if form in _LATE and _within(day, stop, DISTRESS_WINDOW):
            late.append(f"{form} {day}")
        if form in _DEREGISTRATION and _within(day, stop, DEREGISTRATION_WINDOW):
            dereg.append(f"{form} {day}")
        if form in EXCHANGE_ACT_PERIODIC_FORMS and day > stop + dt.timedelta(days=REPORTING_AFTER):
            later_periodic.append(day)
        if form in EXCHANGE_ACT_PERIODIC_FORMS and _within(day, stop, (365, 0)):
            recent_periodic = True

    bankrupt: list[str] = []
    bankrupt_dates: list[dt.date] = []
    doubted: list[str] = []
    completion: list[str] = []
    delisting_notice: list[str] = []
    for header in eightks:
        meanings = item_meanings(header)
        label = f"8-K {header.filing_date} items {','.join(header.items)} ({header.accession})"
        if ItemMeaning.BANKRUPTCY in meanings and _within(
            header.filing_date, stop, BANKRUPTCY_WINDOW
        ):
            agrees = texts.get(header.accession)
            if agrees is True:
                bankrupt.append(label)
                bankrupt_dates.append(header.filing_date)
            elif agrees is False:
                doubted.append(f"header declares bankruptcy, document does not: {label}")
            else:
                doubted.append(f"header declares bankruptcy, document unread: {label}")
        if meanings & _COMPLETION and _within(header.filing_date, stop, COMPLETION_WINDOW):
            completion.append(label)
        if ItemMeaning.DELISTING_NOTICE in meanings and _within(
            header.filing_date, stop, DISTRESS_WINDOW
        ):
            delisting_notice.append(label)

    extinguished = [
        f"Form 15 {day} certifies {holders} holder(s) of record"
        for day, holders in form15_holders
        if holders is not None
        and holders <= EXTINGUISHED_HOLDERS
        and _within(day, stop, DEREGISTRATION_WINDOW)
    ]
    read = len(eightks)
    # A registrant in liquidation or a long Chapter 11 keeps filing 8-Ks and
    # stops filing 10-Ks. SONICblue filed for bankruptcy in 2003, traded for six
    # more years and had its equity cancelled in 2009; its bankruptcy 8-K is far
    # outside the window, and the Form 15 alone reads as an extinguished class.
    # The silence does not change the finding -- it marks it as doubtful.
    silence: tuple[str, ...] = (
        ()
        if recent_periodic
        else ("no 10-K, 10-Q, 20-F or 40-F in the year before the last price",)
    )

    def finding(
        cause: ExitCause,
        strength: EvidenceStrength,
        evidence: Sequence[str],
        conflicts: Sequence[str] = (),
    ) -> ExitCauseFinding:
        return ExitCauseFinding(
            cause=cause,
            strength=strength,
            evidence=tuple(evidence),
            conflicts=tuple(conflicts) + tuple(doubted) + silence,
            eightks_listed=listed,
            eightks_read=read,
        )

    if bankrupt and offers and max(offers) > min(bankrupt_dates):
        # An offer to the shareholders after the proceeding: whoever's
        # proceeding it was, the equity was bought. Judged on the acquisition
        # evidence below, with the bankruptcy item kept as a conflict.
        doubted.extend(
            f"bankruptcy item precedes an offer to shareholders "
            f"(may concern a parent or subsidiary): {label}"
            for label in bankrupt
        )
        bankrupt = []
    if bankrupt:
        # The filing states it; that is as direct as a header gets.
        return finding(
            ExitCause.BANKRUPT,
            EvidenceStrength.FORM_DIRECT,
            bankrupt,
            conflicts=[f"transaction pointer {p}" for p in pointers]
            + [f"completion {c}" for c in completion],
        )
    closed = completion + extinguished
    if pointers and closed:
        return finding(ExitCause.ACQUIRED, EvidenceStrength.FORM_INFERRED, pointers + closed)
    if pointers or completion:
        return finding(
            ExitCause.ACQUISITION_INDICATED,
            EvidenceStrength.FORM_INFERRED,
            pointers + closed,
        )
    if extinguished:
        # The form states the count; what it does not state is why.
        return finding(ExitCause.EXTINGUISHED, EvidenceStrength.FORM_DIRECT, extinguished)
    if later_periodic:
        first = min(later_periodic)
        return finding(
            ExitCause.KEPT_REPORTING,
            EvidenceStrength.FORM_INFERRED,
            [f"periodic report {first}, {(first - stop).days} days after the last price"],
        )
    if late or delisting_notice:
        return finding(
            ExitCause.DISTRESS_INDICATED,
            EvidenceStrength.FORM_INFERRED,
            late + delisting_notice,
        )
    if dereg:
        return finding(ExitCause.DEREGISTERED_UNEXPLAINED, EvidenceStrength.FORM_DIRECT, dereg)
    return finding(ExitCause.UNRESOLVED, EvidenceStrength.NONE, [])
