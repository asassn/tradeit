"""Which forms count as a registrant reporting, and which deliberately do not.

`PERIODIC_FORMS` is the input to the supersession rule: a confirming filing
that precedes the registrant's last periodic report may not supply the exit
date. That rule is only as wide as this set, so a family missing from it is a
family whose live registrants can be dated dead — which is exactly what
happened to 645 investment companies before this set was made
regulator-neutral (`EDGAR_DELISTING_DENOMINATOR.md` §7d).

The exclusions carry as much weight as the inclusions and are pinned
individually, because each is a claim about what a form means rather than an
oversight.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.edgar.evidence import PERIODIC_FORMS, FormRole, classify_form

WHEN = dt.date(2020, 6, 30)


def _role(form: str) -> FormRole:
    return classify_form(form, WHEN).role


# -- three regulators, three cadences --------------------------------------


@pytest.mark.parametrize("form", ["10-K", "10-K405", "10-KSB", "10-Q", "10-QSB"])
def test_domestic_exchange_act_reports_are_periodic(form: str) -> None:
    assert _role(form) is FormRole.PERIODIC


@pytest.mark.parametrize("form", ["20-F", "40-F"])
def test_foreign_private_issuer_reports_are_periodic(form: str) -> None:
    """A 20-F filer is not silent between annual reports; it is punctual."""
    assert _role(form) is FormRole.PERIODIC


@pytest.mark.parametrize("form", ["N-CSR", "N-CSRS", "N-CEN", "NPORT-P", "N-Q", "N-30D"])
def test_investment_company_reports_are_periodic(form: str) -> None:
    """Registered investment companies report under the Investment Company
    Act. Judging them by the Exchange Act cadence read 645 live funds as
    dormant."""
    assert _role(form) is FormRole.PERIODIC


def test_the_retired_fund_forms_are_kept() -> None:
    """The corpus starts in 1994. N-Q and N-30D are what funds filed before
    NPORT-P and N-CSR existed, so dropping them would blind the set to exactly
    the early window this project cares most about."""
    assert {"N-Q", "N-30D"} <= PERIODIC_FORMS


# -- the exclusions, each a claim ------------------------------------------


@pytest.mark.parametrize("form", ["N-8F", "N-8F/A", "N-8F NTC", "N-8F ORDR"])
def test_deregistration_applications_are_not_periodic(form: str) -> None:
    """N-8F is a fund's application to deregister. One filed after an exit
    date corroborates that exit; admitting it here would let a fund's own
    death certificate supersede its death."""
    assert _role(form) is not FormRole.PERIODIC


@pytest.mark.parametrize("form", ["NT 10-K", "NT 10-Q", "NT 11-K"])
def test_a_notice_of_late_filing_is_not_a_report(form: str) -> None:
    """A promise to report is not a report. Counting one would let a
    delinquent registrant look current indefinitely."""
    assert _role(form) is not FormRole.PERIODIC


def test_proxy_voting_records_are_not_periodic() -> None:
    """N-PX says how a fund voted, not how the fund stands, and can be filed
    while winding down. Its absence is not what cessation means."""
    assert _role("N-PX") is not FormRole.PERIODIC


@pytest.mark.parametrize("form", ["8-K", "6-K"])
def test_current_reports_are_not_periodic(form: str) -> None:
    """The foreign exclusion mirrors the domestic one: 6-K is to 20-F what
    8-K is to 10-K, and neither is a periodic report."""
    assert _role(form) is not FormRole.PERIODIC


# -- shape -----------------------------------------------------------------


def test_form_types_are_matched_case_and_whitespace_insensitively() -> None:
    assert _role("  n-csr  ") is FormRole.PERIODIC


def test_a_periodic_form_carries_no_evidence_strength() -> None:
    """Periodic reports feed cessation detection only. If one ever carried an
    evidence type, absence of filings would start dating deaths."""
    signal = classify_form("N-CSR", WHEN)
    assert signal.evidence_type is None
    assert not signal.requires_document_text


def test_every_periodic_form_classifies_as_periodic() -> None:
    """Guards the lookup order: PERIODIC_FORMS is consulted before the signal
    table, so a form added to both would silently take whichever wins."""
    for form in PERIODIC_FORMS:
        assert _role(form) is FormRole.PERIODIC, form


# -- the third regulator's exit forms --------------------------------------


def test_the_sec_order_granting_deregistration_confirms_an_exit() -> None:
    """N-8F ORDR is the Investment Company Act analogue of Form 15, and the
    completion of the regulator-neutral principle already applied to foreign
    private issuers via 15F-12B."""
    from tradeit.edgar.evidence import EvidenceStrength, EvidenceType, LifecycleScope

    signal = classify_form("N-8F ORDR", WHEN)
    assert signal.role is FormRole.EXIT_CONFIRMING
    assert signal.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert signal.strength is EvidenceStrength.FORM_DIRECT
    assert signal.scope is LifecycleScope.SEC_REPORTING


@pytest.mark.parametrize("form", ["N-8F", "N-8F/A", "N-8F NTC"])
def test_the_application_to_deregister_dates_nothing(form: str) -> None:
    """An application can be withdrawn or denied. Only the SEC's grant ends the
    registration, which is the distinction the signal table already draws
    between a filing that says something and one that asks for something."""
    signal = classify_form(form, WHEN)
    assert signal.role is FormRole.EXIT_CANDIDATE
    assert signal.evidence_type is None


def test_the_fund_exit_forms_are_not_periodic() -> None:
    """The two halves must not collide: N-8F is excluded from PERIODIC_FORMS so
    a fund's own death certificate cannot supersede its death, and recognised
    as an exit signal so the death is dated."""
    assert not {"N-8F", "N-8F ORDR", "N-8F/A", "N-8F NTC"} & PERIODIC_FORMS


def test_all_three_regulators_have_an_exit_form() -> None:
    """Domestic, foreign and investment-company deregistration each resolve to
    a confirmed registration termination. A gap here is what left 336 fund
    closures evidenced nowhere."""
    from tradeit.edgar.evidence import EvidenceType

    for form in ("15-12G", "15F-12G", "N-8F ORDR"):
        assert classify_form(form, WHEN).evidence_type is (
            EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
        ), form
