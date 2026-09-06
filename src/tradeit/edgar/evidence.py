"""What an EDGAR filing actually proves about a security's life.

**The correction this module exists to enforce.** An earlier design treated
"the issuer stopped filing 10-Ks" as a delisting. It is not. A registrant that
stops filing may have been acquired, gone private, been liquidated, become a
non-reporting subsidiary, or simply fallen delinquent and resumed eighteen
months later. Those are different events with different dates and different
meanings for survivorship research, and collapsing them into one produces a
denominator that looks authoritative and is wrong in a direction nobody can see.

So cessation is a **candidate**, never a conclusion. It gets its own evidence
type, it never carries a lifecycle date, and it stays unresolved until something
else says what happened.

**Four lifecycles, deliberately not merged.** EDGAR is a filing archive, so what
it observes directly is the *SEC reporting* lifecycle. Survivorship research
needs the *exchange listing* and *security class* lifecycles, and those are
related to the reporting one but are not the same:

- an issuer can deregister while its shares keep trading over the counter;
- a class can be extinguished in a merger while the issuer keeps filing;
- an issuer can be delisted from an exchange and continue to file;
- an issuer can survive while every class it once listed has gone.

:class:`LifecycleScope` keeps them apart so that a Form 15 is never quietly read
as a delisting.

**What the quarterly full-index can and cannot tell us.** The index carries five
fields — CIK, company name, form type, filing date, and the document path. It
carries **no item numbers**, so an 8-K in the index is just "an 8-K"; whether it
reports a bankruptcy, a completed acquisition or a change of auditor is only
knowable by fetching and reading the document. Every signal that depends on 8-K
item semantics is therefore marked ``requires_document_text`` and contributes
nothing until the document is actually parsed. That is a limitation of the free
index, stated rather than papered over.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "EIGHT_K_ITEM_NUMBERING_FROM",
    "ELECTRONIC_FORM_25_FROM",
    "FORM_SIGNALS",
    "PERIODIC_FORMS",
    "EvidenceStrength",
    "EvidenceType",
    "FormRole",
    "FormSignal",
    "LifecycleEvidence",
    "LifecycleScope",
    "classify_form",
]


class LifecycleScope(StrEnum):
    """Which life a piece of evidence is about.

    Merging these is the single easiest way to build a denominator that is
    confidently wrong, because each is observable through different forms and
    each ends at a different time.
    """

    #: The legal entity and its operations. Deliberately spans both, because
    #: they separate: a company can wind down operations, lay off staff and sell
    #: its assets while continuing to exist legally for months or years. An
    #: issuer-scoped fact must therefore say in its own text *which* it is --
    #: "began an orderly wind-down" and "was dissolved" are both issuer-scope
    #: and are not the same event.
    ISSUER = "issuer"
    #: A class of securities: does this particular security still exist?
    SECURITY_CLASS = "security_class"
    #: Listing on an exchange: is the class still listed and traded there?
    EXCHANGE_LISTING = "exchange_listing"
    #: Duty to file with the SEC: is the registrant still reporting?
    SEC_REPORTING = "sec_reporting"


class EvidenceType(StrEnum):
    """What an exit is, as distinct from the fact that one seems to have happened.

    The first five are conclusions supported by a filing that says so. The last
    three are explicitly *not* conclusions, and the design turns on keeping them
    that way.

    :data:`NON_EXIT_REGISTRANT_STILL_REPORTING` is the newest and was added
    because the first real run produced its opposite. A registrant that files a
    Form 15 for one registered class and goes on filing 10-Ks for thirty years
    was being recorded as having exited on the date of that Form 15 -- so the
    corpus *invented* dead companies rather than omitting them. The filing is
    real and something did end; what did not end is the registrant, and the
    evidence type now says which. See
    :func:`~tradeit.edgar.lifecycle.assert_exit_not_contradicted`.
    """

    CONFIRMED_EXCHANGE_DELISTING = "confirmed_exchange_delisting"
    CONFIRMED_REGISTRATION_TERMINATION = "confirmed_registration_termination"
    CONFIRMED_ACQUISITION = "confirmed_acquisition"
    CONFIRMED_BANKRUPTCY = "confirmed_bankruptcy"
    CONFIRMED_SECURITY_EXTINGUISHED = "confirmed_security_extinguished"
    #: The registrant stopped filing. **This is a lead, not a death.**
    POSSIBLE_EXIT_FILING_CESSATION = "possible_exit_filing_cessation"
    #: A confirming filing exists, and the registrant filed a periodic report
    #: *after* it. **The registrant did not exit.** Something narrower did --
    #: the index names no security class, so it cannot say what -- and this
    #: carries no lifecycle date for the same reason cessation carries none.
    NON_EXIT_REGISTRANT_STILL_REPORTING = "non_exit_registrant_still_reporting"
    #: Something ended and the evidence does not say what.
    UNRESOLVED_EXIT = "unresolved_exit"


class EvidenceStrength(StrEnum):
    """How the conclusion was reached, published beside every count.

    A 2001 termination count that is 80% ``CESSATION_ONLY`` is a materially
    weaker claim than one that is 80% ``FORM_DIRECT``, and a denominator that
    reports a single number has thrown that away.
    """

    #: A form whose existence directly evidences the event.
    FORM_DIRECT = "form_direct"
    #: Derived by combining several forms, or requiring document text.
    FORM_INFERRED = "form_inferred"
    #: Only the absence of subsequent filings. Never sufficient on its own.
    CESSATION_ONLY = "cessation_only"
    NONE = "none"


class FormRole(StrEnum):
    """What role a form plays in a lifecycle reconstruction."""

    #: Evidences the beginning of a listing or a registered class.
    BIRTH = "birth"
    #: Directly evidences an exit.
    EXIT_CONFIRMING = "exit_confirming"
    #: Suggests an exit but needs document text or corroboration.
    EXIT_CANDIDATE = "exit_candidate"
    #: A routine periodic report; feeds cessation detection only.
    PERIODIC = "periodic"
    #: Points at a transaction without evidencing its completion.
    TRANSACTION_POINTER = "transaction_pointer"
    IRRELEVANT = "irrelevant"


#: 8-K item numbering (1.03 bankruptcy, 2.01 completion of acquisition, 3.01
#: delisting notice, 5.06 shell transaction) dates from the August 2004
#: overhaul. Before it, 8-K used a coarser scheme. CORROBORATED, not verified
#: against sec.gov in this environment.
EIGHT_K_ITEM_NUMBERING_FROM = dt.date(2004, 8, 23)

#: Form 25 became an electronically filed record under the amended Rule 12d2-2
#: regime in 2005. Before that it was largely filed on paper by the exchange
#: and is **not reliably present in EDGAR** — which is precisely the 1998-2002
#: window this project cares most about. CORROBORATED, not verified here.
ELECTRONIC_FORM_25_FROM = dt.date(2005, 4, 24)

#: Periodic reports whose absence is what "cessation" means. Nothing else.
#:
#: **The set is regulator-neutral by necessity, not by taste.** A registrant is
#: dormant only when *its own* form family goes quiet, and three families report
#: on three different forms: domestic issuers on ``10-K``/``10-Q``, foreign
#: private issuers on ``20-F``/``40-F``, and registered investment companies
#: under the Investment Company Act. Judging a fund by the Exchange Act cadence
#: reads a perfectly punctual filer as silent — measured 2026-09-05, that
#: mistake dated 645 exits before a periodic report the registrant had itself
#: filed, and the supersession rule could not catch them because the forms it
#: weighs did not include theirs. See ``EDGAR_DELISTING_DENOMINATOR.md`` §7d.
#:
#: Three fund-shaped families are deliberately **excluded**, and each exclusion
#: is a claim:
#:
#: * ``N-8F`` and its notice and order variants are the *application to
#:   deregister an investment company*. One filed after an exit date
#:   corroborates that exit; admitting it here would let a fund's own death
#:   certificate supersede its death.
#: * ``NT 10-K`` and ``NT 10-Q`` notify the SEC that a report will be late.
#:   A promise to report is not a report, and treating it as one would let a
#:   delinquent registrant look current.
#: * ``N-PX`` records how a fund voted proxies rather than how the fund itself
#:   stands, and can be filed while winding down. Its absence is not what
#:   cessation means.
PERIODIC_FORMS: frozenset[str] = frozenset(
    {
        # Domestic issuers, Exchange Act.
        "10-K",
        "10-K405",
        "10-KSB",
        "10-K/A",
        "10-Q",
        "10-QSB",
        "10-Q/A",
        # Foreign private issuers. 6-K is excluded for the same reason 8-K is:
        # a current report is not a periodic one.
        "20-F",
        "40-F",
        # Registered investment companies, Investment Company Act. N-30D and
        # N-Q are the retired predecessors of N-CSR and NPORT-P and are kept
        # because the corpus starts in 1994, when they were what funds filed.
        "N-CSR",
        "N-CSR/A",
        "N-CSRS",
        "N-CSRS/A",
        "N-CEN",
        "N-CEN/A",
        "NPORT-P",
        "NPORT-P/A",
        "N-Q",
        "N-Q/A",
        "N-30D",
    }
)


@dataclass(frozen=True, slots=True)
class FormSignal:
    """What one form type contributes, and what it does not."""

    form_type: str
    role: FormRole
    scope: LifecycleScope | None
    evidence_type: EvidenceType | None
    strength: EvidenceStrength
    #: True when the index alone is insufficient and the filing must be read.
    requires_document_text: bool
    note: str = ""


_SIGNALS: tuple[FormSignal, ...] = (
    # --- births -----------------------------------------------------------
    FormSignal(
        "8-A12B",
        FormRole.BIRTH,
        LifecycleScope.EXCHANGE_LISTING,
        None,
        EvidenceStrength.FORM_DIRECT,
        False,
        "registration of a class under section 12(b) — an exchange listing event",
    ),
    FormSignal(
        "8-A12G",
        FormRole.BIRTH,
        LifecycleScope.SECURITY_CLASS,
        None,
        EvidenceStrength.FORM_DIRECT,
        False,
        "registration under 12(g); not necessarily an exchange listing",
    ),
    # --- direct exits -----------------------------------------------------
    FormSignal(
        "25",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.EXCHANGE_LISTING,
        EvidenceType.CONFIRMED_EXCHANGE_DELISTING,
        EvidenceStrength.FORM_DIRECT,
        False,
        "removal from listing; the effective date is set by rule and is not the filing date",
    ),
    FormSignal(
        "25-NSE",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.EXCHANGE_LISTING,
        EvidenceType.CONFIRMED_EXCHANGE_DELISTING,
        EvidenceStrength.FORM_DIRECT,
        False,
        "exchange-filed notification of removal",
    ),
    FormSignal(
        "15-12B",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "termination of 12(b) registration; usually follows a Form 25",
    ),
    FormSignal(
        "15-12G",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "termination of 12(g) registration",
    ),
    FormSignal(
        "15-15D",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "suspension of the duty to file under 15(d)",
    ),
    FormSignal(
        "15F-12B",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "foreign private issuer deregistration",
    ),
    FormSignal(
        "15F-12G",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "foreign private issuer deregistration",
    ),
    # --- investment company deregistration, the third regulator ------------
    #
    # The Investment Company Act analogue of Form 15, and the completion of the
    # regulator-neutral principle already applied to periodic reporting and, on
    # the exit side, to foreign private issuers via 15F-12B above. Without it a
    # fund's closure is evidenced nowhere: measured 2026-09-05, 336 registrants
    # whose exits the widened PERIODIC_FORMS correctly un-dated had filed one of
    # these and would have carried no exit date at all. See §7d.
    #
    # The application and the order are deliberately different claims. An
    # application can be withdrawn or denied; 31 of the 336 have no order on
    # record. Only the SEC's grant ends the registration, which is the same
    # distinction this table already draws between a filing that says something
    # and one that asks for something.
    FormSignal(
        "N-8F ORDR",
        FormRole.EXIT_CONFIRMING,
        LifecycleScope.SEC_REPORTING,
        EvidenceType.CONFIRMED_REGISTRATION_TERMINATION,
        EvidenceStrength.FORM_DIRECT,
        False,
        "SEC order granting deregistration of a registered investment company "
        "under section 8(f); the grant, not the request",
    ),
    FormSignal(
        "N-8F",
        FormRole.EXIT_CANDIDATE,
        LifecycleScope.SEC_REPORTING,
        None,
        EvidenceStrength.NONE,
        False,
        "application to deregister an investment company; may be withdrawn or "
        "denied, so it dates nothing on its own",
    ),
    FormSignal(
        "N-8F/A",
        FormRole.EXIT_CANDIDATE,
        LifecycleScope.SEC_REPORTING,
        None,
        EvidenceStrength.NONE,
        False,
        "amended application to deregister; still an application",
    ),
    FormSignal(
        "N-8F NTC",
        FormRole.EXIT_CANDIDATE,
        LifecycleScope.SEC_REPORTING,
        None,
        EvidenceStrength.NONE,
        False,
        "notice that a deregistration application was filed; announces the "
        "request rather than its outcome",
    ),
    # --- candidates that need the document --------------------------------
    FormSignal(
        "8-K",
        FormRole.EXIT_CANDIDATE,
        None,
        None,
        EvidenceStrength.NONE,
        True,
        "the index carries no item numbers; bankruptcy (1.03), completed acquisition "
        "(2.01), delisting notice (3.01) and shell transactions (5.06) are "
        "indistinguishable from a change of auditor without reading the filing",
    ),
    # --- pointers ---------------------------------------------------------
    FormSignal(
        "S-4",
        FormRole.TRANSACTION_POINTER,
        None,
        None,
        EvidenceStrength.NONE,
        True,
        "registers securities for a proposed transaction and is filed by the "
        "acquirer; evidences neither completion nor which party disappears",
    ),
    FormSignal(
        "DEFM14A",
        FormRole.TRANSACTION_POINTER,
        None,
        None,
        EvidenceStrength.NONE,
        True,
        "merger proxy: a proposal, not an outcome",
    ),
    FormSignal(
        "SC 14D9",
        FormRole.TRANSACTION_POINTER,
        None,
        None,
        EvidenceStrength.NONE,
        True,
        "tender-offer response; the offer may fail",
    ),
)

FORM_SIGNALS: dict[str, FormSignal] = {signal.form_type: signal for signal in _SIGNALS}

_PERIODIC_SIGNAL = FormSignal(
    "",
    FormRole.PERIODIC,
    LifecycleScope.SEC_REPORTING,
    None,
    EvidenceStrength.NONE,
    False,
    "periodic report; contributes to cessation detection only",
)

_UNKNOWN_SIGNAL = FormSignal(
    "",
    FormRole.IRRELEVANT,
    None,
    None,
    EvidenceStrength.NONE,
    False,
    "form type not classified for lifecycle purposes",
)


def classify_form(form_type: str, filed_at: dt.date) -> FormSignal:
    """What this filing contributes, given when it was filed.

    ``filed_at`` matters because two regimes changed underneath this data.
    A Form 25 filed before :data:`ELECTRONIC_FORM_25_FROM` is still direct
    evidence *when present* — the caveat is that most of them are absent, which
    is a coverage fact about the era rather than a reason to distrust the ones
    that exist. The note is attached so the era shows up in the output.
    """
    key = form_type.strip().upper()
    if key in PERIODIC_FORMS:
        return FormSignal(
            key,
            _PERIODIC_SIGNAL.role,
            _PERIODIC_SIGNAL.scope,
            None,
            EvidenceStrength.NONE,
            False,
            _PERIODIC_SIGNAL.note,
        )
    signal = FORM_SIGNALS.get(key)
    if signal is None:
        return FormSignal(
            key,
            _UNKNOWN_SIGNAL.role,
            None,
            None,
            EvidenceStrength.NONE,
            False,
            _UNKNOWN_SIGNAL.note,
        )
    if key in {"25", "25-NSE"} and filed_at < ELECTRONIC_FORM_25_FROM:
        return FormSignal(
            signal.form_type,
            signal.role,
            signal.scope,
            signal.evidence_type,
            signal.strength,
            signal.requires_document_text,
            signal.note + "; pre-2005 regime — electronic Form 25 coverage is sparse",
        )
    if key == "8-K" and filed_at < EIGHT_K_ITEM_NUMBERING_FROM:
        return FormSignal(
            signal.form_type,
            signal.role,
            signal.scope,
            signal.evidence_type,
            signal.strength,
            signal.requires_document_text,
            signal.note + "; pre-2004 item scheme — text parsing differs",
        )
    return signal


@dataclass(frozen=True, slots=True)
class LifecycleEvidence:
    """One filing, with the provenance needed to defend any count built on it.

    ``evidence_date`` is the filing date and is always known. ``effective_date``
    is the date the *event* took effect and is ``None`` unless a document
    actually stated it — a Form 25's effective date is set by rule some days
    after filing, so treating the filing date as the delisting date would
    fabricate precision. Counts are reported against ``evidence_date`` and say
    so.
    """

    cik: int
    company_name: str
    form_type: str
    #: When the filing was submitted. Always known from the index.
    evidence_date: dt.date
    accession: str
    #: The index's own ``Filename`` column — where this came from.
    source_path: str
    #: Which quarterly index file supplied the row, e.g. ``"1998-QTR1"``.
    index_quarter: str
    scope: LifecycleScope | None
    evidence_type: EvidenceType | None
    strength: EvidenceStrength
    #: Only when a parsed document stated it. Never inferred from the filing date.
    effective_date: dt.date | None = None
    #: The index names no security class, so this is False for index-only rows.
    security_class_known: bool = False
    exchange: str | None = None
    requires_document_text: bool = False
    note: str = ""

    @classmethod
    def from_index_row(
        cls,
        *,
        cik: int,
        company_name: str,
        form_type: str,
        filed_at: dt.date,
        accession: str,
        source_path: str,
        index_quarter: str,
    ) -> LifecycleEvidence:
        signal = classify_form(form_type, filed_at)
        return cls(
            cik=cik,
            company_name=company_name,
            form_type=form_type.strip().upper(),
            evidence_date=filed_at,
            accession=accession,
            source_path=source_path,
            index_quarter=index_quarter,
            scope=signal.scope,
            evidence_type=signal.evidence_type,
            strength=signal.strength,
            effective_date=None,
            security_class_known=False,
            exchange=None,
            requires_document_text=signal.requires_document_text,
            note=signal.note,
        )

    @property
    def role(self) -> FormRole:
        return classify_form(self.form_type, self.evidence_date).role

    def summary(self) -> dict[str, object]:
        return {
            "cik": self.cik,
            "form_type": self.form_type,
            "evidence_date": self.evidence_date.isoformat(),
            "accession": self.accession,
            "scope": str(self.scope) if self.scope else None,
            "evidence_type": str(self.evidence_type) if self.evidence_type else None,
            "strength": str(self.strength),
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "security_class_known": self.security_class_known,
            "requires_document_text": self.requires_document_text,
            "index_quarter": self.index_quarter,
        }
