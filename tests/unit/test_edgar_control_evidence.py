"""The curated control-identity evidence file, and what it refuses to accept.

The layer exists so that verifying a control is a *record* rather than a source
edit. Its job is therefore to be strict: a malformed provenance record is worse
than none, because it looks like provenance.

The identity rules themselves are not restated here -- each record is validated
by constructing a :class:`SecurityMapping`, so ``MANUAL_VERIFIED`` needing a
citation is enforced by the same code that enforces it everywhere else. What is
tested here is the file-level discipline: no duplicates, no unknown controls, no
promotion on name resemblance, and -- the one that motivated the whole design --
no silent merging of two issuers that shared a ticker.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tradeit.edgar.control_evidence import (
    DEFAULT_EVIDENCE_PATH,
    SCHEMA_VERSION,
    ControlEvidence,
    FactDateSource,
    IssuerMapping,
    controls_awaiting_manual_verification,
    load_control_evidence,
    resolve_controls,
    unresolved_controls,
)
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.evidence import LifecycleScope
from tradeit.edgar.identity import MappingEvidence, MappingStatus
from tradeit.errors import ConfigError


def _write(tmp_path: Path, controls: list[dict[str, Any]], **top: Any) -> Path:
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "controls": controls}
    payload.update(top)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _verified_mapping(**overrides: Any) -> dict[str, Any]:
    record = {
        "issuer_label": "primary",
        "cik": 320193,
        "ticker": "AAPL",
        "status": "manual_verified",
        "evidence": "sec_company_tickers",
        "citation": "https://www.sec.gov/files/company_tickers.json retrieved 2026-08-14",
        "verified_on": "2026-08-14",
    }
    record.update(overrides)
    return record


def _unresolved_mapping(**overrides: Any) -> dict[str, Any]:
    record = {
        "issuer_label": "primary",
        "cik": None,
        "ticker": None,
        "status": "unresolved",
        "unresolved_reason": "no primary source consulted yet",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def test_valid_file_loads(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "control_id": "AAPL",
                "expected_company": "Apple Inc.",
                "mappings": [_verified_mapping()],
            }
        ],
    )
    loaded = load_control_evidence(path)
    assert loaded.schema_version == SCHEMA_VERSION
    mapping = loaded.controls["AAPL"].mappings[0]
    assert mapping.cik == 320193
    assert mapping.status is MappingStatus.MANUAL_VERIFIED


def test_absent_file_is_empty_not_an_error(tmp_path: Path) -> None:
    """A project that has not started verifying yet is a valid state."""
    loaded = load_control_evidence(tmp_path / "nothing.json")
    assert loaded.controls == {}


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, [], schema_version=99)
    with pytest.raises(ConfigError, match="schema_version"):
        load_control_evidence(path)


def test_malformed_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_control_evidence(path)


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def test_manual_verified_without_citation_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(citation="")]}],
    )
    with pytest.raises(ConfigError, match="citation"):
        load_control_evidence(path)


def test_resolved_without_ticker_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(status="resolved", ticker=None)]}],
    )
    with pytest.raises(ConfigError, match="requires a ticker"):
        load_control_evidence(path)


def test_resolved_without_cik_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(status="resolved", cik=None)]}],
    )
    with pytest.raises(ConfigError, match="requires a cik"):
        load_control_evidence(path)


def test_name_match_cannot_promote_a_mapping(tmp_path: Path) -> None:
    """A company name resembling the control's is not evidence."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "AAPL",
                "mappings": [_verified_mapping(status="resolved", evidence="name_match")],
            }
        ],
    )
    with pytest.raises(ConfigError, match="cannot establish a mapping on its own"):
        load_control_evidence(path)


def test_full_text_search_alone_cannot_promote_a_mapping(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "control_id": "AAPL",
                "mappings": [_verified_mapping(status="resolved", evidence="full_text_search")],
            }
        ],
    )
    with pytest.raises(ConfigError, match="cannot establish a mapping on its own"):
        load_control_evidence(path)


@pytest.mark.parametrize("bad", [0, -1, 10_000_000_000, "320193", True])
def test_invalid_ciks_are_refused(tmp_path: Path, bad: Any) -> None:
    path = _write(tmp_path, [{"control_id": "AAPL", "mappings": [_verified_mapping(cik=bad)]}])
    with pytest.raises(ConfigError, match="cik"):
        load_control_evidence(path)


def test_unknown_control_id_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"control_id": "NOTACONTROL", "mappings": [_unresolved_mapping()]}])
    with pytest.raises(ConfigError, match="unknown control_id"):
        load_control_evidence(path)


def test_duplicate_control_is_refused(tmp_path: Path) -> None:
    record = {"control_id": "AAPL", "mappings": [_verified_mapping()]}
    path = _write(tmp_path, [record, record])
    with pytest.raises(ConfigError, match="duplicate control_id"):
        load_control_evidence(path)


def test_duplicate_issuer_label_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": True,
                "mappings": [_unresolved_mapping(), _unresolved_mapping()],
            }
        ],
    )
    with pytest.raises(ConfigError, match="duplicate issuer_label"):
        load_control_evidence(path)


def test_two_issuers_sharing_a_cik_is_refused(tmp_path: Path) -> None:
    """Conflicting mappings: the same registrant cannot be two issuers."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": True,
                "mappings": [
                    _verified_mapping(issuer_label="old_gm", cik=40730, ticker="GM"),
                    _verified_mapping(issuer_label="new_gm", cik=40730, ticker="GM"),
                ],
            }
        ],
    )
    with pytest.raises(ConfigError, match="same cik appears on two issuers"):
        load_control_evidence(path)


def test_unresolved_without_a_reason_is_refused(tmp_path: Path) -> None:
    """'Unresolved' with no reason is an omission wearing a status."""
    path = _write(
        tmp_path,
        [{"control_id": "IPET", "mappings": [_unresolved_mapping(unresolved_reason="")]}],
    )
    with pytest.raises(ConfigError, match="unresolved_reason"):
        load_control_evidence(path)


def test_reversed_validity_window_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "control_id": "AAPL",
                "mappings": [_verified_mapping(valid_from="2010-01-01", valid_to="2009-01-01")],
            }
        ],
    )
    with pytest.raises(ConfigError, match="valid_to precedes valid_from"):
        load_control_evidence(path)


# ---------------------------------------------------------------------------
# the identity break -- the case that shaped the model
# ---------------------------------------------------------------------------


def test_several_issuers_require_an_explicit_identity_break(tmp_path: Path) -> None:
    """Two issuers sharing a ticker must SAY so; silence would merge them."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": False,
                "mappings": [
                    _unresolved_mapping(issuer_label="old_gm"),
                    _unresolved_mapping(issuer_label="new_gm"),
                ],
            }
        ],
    )
    with pytest.raises(ConfigError, match="identity_break is false"):
        load_control_evidence(path)


def test_identity_break_with_one_issuer_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"control_id": "GM", "identity_break": True, "mappings": [_unresolved_mapping()]}],
    )
    with pytest.raises(ConfigError, match="only one issuer"):
        load_control_evidence(path)


def test_two_issuers_are_kept_apart_with_their_own_ciks_and_windows(tmp_path: Path) -> None:
    """The GM case, modelled rather than collapsed."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": True,
                "mappings": [
                    _verified_mapping(
                        issuer_label="old_gm",
                        cik=40730,
                        ticker="GM",
                        valid_to="2009-06-01",
                        scope_notes="General Motors Corporation",
                    ),
                    _verified_mapping(
                        issuer_label="new_gm",
                        cik=1467858,
                        ticker="GM",
                        valid_from="2010-11-18",
                        scope_notes="General Motors Company",
                    ),
                ],
            }
        ],
    )
    control = load_control_evidence(path).controls["GM"]
    assert control.identity_break is True
    assert len(control.mappings) == 2
    assert {m.cik for m in control.mappings} == {40730, 1467858}
    assert {m.ticker for m in control.mappings} == {"GM"}
    # Same ticker, different registrants, non-overlapping windows.
    old, new = control.mappings
    assert old.valid_to is not None
    assert new.valid_from is not None
    assert old.valid_to < new.valid_from


def test_a_controls_status_is_its_weakest_issuer(tmp_path: Path) -> None:
    """A half-mapped identity break is exactly what produces a spliced series."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": True,
                "mappings": [
                    _verified_mapping(issuer_label="new_gm", cik=1467858, ticker="GM"),
                    _unresolved_mapping(issuer_label="old_gm"),
                ],
            }
        ],
    )
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    assert resolved["GM"].status is MappingStatus.UNRESOLVED
    assert (
        "unresolved" in resolved["GM"].unresolved_reason.lower() or resolved["GM"].unresolved_reason
    )


# ---------------------------------------------------------------------------
# joining to the fixture
# ---------------------------------------------------------------------------


def test_controls_without_a_record_are_unresolved_with_a_reason(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"control_id": "AAPL", "mappings": [_verified_mapping()]}])
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    assert len(resolved) == len(CONTROL_UNIVERSE)
    assert resolved["AAPL"].status is MappingStatus.MANUAL_VERIFIED
    assert resolved["MSFT"].status is MappingStatus.UNRESOLVED
    assert "no evidence record" in resolved["MSFT"].unresolved_reason


def test_an_ordinary_survivor_reaches_manual_verified(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"control_id": "AAPL", "mappings": [_verified_mapping()]}])
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    aapl = resolved["AAPL"]
    assert aapl.status is MappingStatus.MANUAL_VERIFIED
    assert aapl.mappings[0].citation
    assert aapl.mappings[0].counts_in_numerator


def test_a_historical_failure_may_stay_unresolved_honestly(tmp_path: Path) -> None:
    """Pets.com: EDGAR gives the CIK; nothing free gives the 2000-era ticker."""
    path = _write(
        tmp_path,
        [
            {
                "control_id": "IPET",
                "mappings": [
                    _unresolved_mapping(
                        unresolved_reason=(
                            "registrant located in EDGAR but no filing consulted states the "
                            "trading symbol; EDGAR full-text search does not reach 2000"
                        )
                    )
                ],
            }
        ],
    )
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    ipet = resolved["IPET"]
    assert ipet.status is MappingStatus.UNRESOLVED
    assert "full-text search does not reach 2000" in ipet.unresolved_reason
    assert ipet.mappings[0].counts_in_numerator is False


# ---------------------------------------------------------------------------
# the two control measurements
#
# They answer different questions and must not be conflated:
#
#   unresolved_controls()                     -- is this control's identity known?
#                                                RESOLVED and MANUAL_VERIFIED clear it
#   controls_awaiting_manual_verification()   -- Milestone 0b: "30 controls to
#                                                MANUAL_VERIFIED". Only MANUAL_VERIFIED
#                                                clears it; RESOLVED does not
#
# The second is strictly harder, so its count is never lower. Reporting the
# easier number against the milestone's wording would let 0b be declared complete
# on evidence the milestone does not ask for.
#
# Both replaced `controls.unverified()`, which read `ControlSecurity.mapping` --
# a placeholder pinned at UNRESOLVED so no CIK is ever written into source. It
# reported thirty outstanding no matter what had been verified, and would have
# gone on reporting thirty after the last control was confirmed.
# ---------------------------------------------------------------------------


def _measure(path: Path) -> tuple[set[str], set[str]]:
    """(unresolved identities, controls still needing MANUAL_VERIFIED)."""
    evidence = load_control_evidence(path)
    return (
        {c.control.ticker for c in unresolved_controls(evidence)},
        {c.control.ticker for c in controls_awaiting_manual_verification(evidence)},
    )


def test_a_control_with_no_evidence_fails_both_measurements(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"control_id": "AAPL", "mappings": [_verified_mapping()]}])
    unresolved, awaiting = _measure(path)
    assert "MSFT" in unresolved
    assert "MSFT" in awaiting


def test_a_record_that_resolves_nothing_fails_both_measurements(tmp_path: Path) -> None:
    """The existence of a record is not evidence. Only its content is.

    An UNRESOLVED mapping must say why it is unresolved, and saying so does not
    discharge either measurement -- otherwise both could be cleared by writing
    thirty honest admissions of ignorance.
    """
    path = _write(tmp_path, [{"control_id": "IPET", "mappings": [_unresolved_mapping()]}])
    unresolved, awaiting = _measure(path)
    assert "IPET" in unresolved
    assert "IPET" in awaiting
    assert len(unresolved) == len(CONTROL_UNIVERSE)
    assert len(awaiting) == len(CONTROL_UNIVERSE)


def test_resolved_clears_identity_but_not_milestone_0b(tmp_path: Path) -> None:
    """The distinction this pair of measurements exists for.

    AAPL is RESOLVED on purpose -- one defensible mapping from the SEC ticker
    file -- so its identity is known. Milestone 0b asks for MANUAL_VERIFIED: a
    person who read a filing and cited it. No ticker reference file supplies
    that, so a RESOLVED control is identified and still outstanding for 0b.
    """
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(status="resolved")]}],
    )
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    assert resolved["AAPL"].status is MappingStatus.RESOLVED
    assert resolved["AAPL"].counts_in_numerator is True
    assert resolved["AAPL"].is_manually_verified is False

    unresolved, awaiting = _measure(path)
    assert "AAPL" not in unresolved
    assert "AAPL" in awaiting


def test_manual_verified_clears_both_measurements(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"control_id": "AAPL", "mappings": [_verified_mapping()]}])
    resolved = {r.control.ticker: r for r in resolve_controls(load_control_evidence(path))}
    assert resolved["AAPL"].status is MappingStatus.MANUAL_VERIFIED
    assert resolved["AAPL"].is_manually_verified is True

    unresolved, awaiting = _measure(path)
    assert "AAPL" not in unresolved
    assert "AAPL" not in awaiting


def test_a_half_mapped_identity_break_fails_both_measurements(tmp_path: Path) -> None:
    """One verified issuer and one unverified one is not a verified control.

    This is the case the whole evidence layer exists for: the unverified half is
    the segment that would splice a price series.
    """
    path = _write(
        tmp_path,
        [
            {
                "control_id": "GM",
                "identity_break": True,
                "mappings": [
                    _verified_mapping(issuer_label="new_gm", cik=1467858, ticker="GM"),
                    _unresolved_mapping(issuer_label="old_gm"),
                ],
            }
        ],
    )
    unresolved, awaiting = _measure(path)
    assert "GM" in unresolved
    assert "GM" in awaiting


def _universe_at(status: str) -> list[dict[str, Any]]:
    return [
        {"control_id": c.ticker, "mappings": [_verified_mapping(ticker=c.ticker, status=status)]}
        for c in CONTROL_UNIVERSE
    ]


def test_an_all_resolved_universe_knows_every_identity_and_does_not_finish_0b(
    tmp_path: Path,
) -> None:
    """The state that would be mistaken for completion by a single count."""
    path = _write(tmp_path, _universe_at("resolved"))
    evidence = load_control_evidence(path)

    assert unresolved_controls(evidence) == ()
    awaiting = controls_awaiting_manual_verification(evidence)
    assert len(awaiting) == len(CONTROL_UNIVERSE)


def test_an_all_manual_verified_universe_completes_milestone_0b(tmp_path: Path) -> None:
    """The only state that ends 0b: 30/30 MANUAL_VERIFIED."""
    path = _write(tmp_path, _universe_at("manual_verified"))
    evidence = load_control_evidence(path)

    assert unresolved_controls(evidence) == ()
    assert controls_awaiting_manual_verification(evidence) == ()
    assert all(r.is_manually_verified for r in resolve_controls(evidence))


def test_the_milestone_gate_is_never_easier_than_the_identity_measurement(
    tmp_path: Path,
) -> None:
    """A structural property, over a deliberately mixed file.

    Every unresolved identity is also awaiting manual verification, so the 0b
    count can never come in below the unresolved count. If that inverts, the two
    predicates have drifted apart.
    """
    path = _write(
        tmp_path,
        [
            {"control_id": "AAPL", "mappings": [_verified_mapping(status="resolved")]},
            {"control_id": "MSFT", "mappings": [_verified_mapping(ticker="MSFT")]},
            {"control_id": "IPET", "mappings": [_unresolved_mapping()]},
        ],
    )
    unresolved, awaiting = _measure(path)
    assert unresolved <= awaiting
    assert len(awaiting) >= len(unresolved)


def test_malformed_evidence_cannot_satisfy_either_measurement(tmp_path: Path) -> None:
    """A file that does not load can never report zero of anything.

    The failure mode worth refusing is a measurement that treats an unreadable or
    invalid evidence file as "nothing outstanding" -- silence read as success.
    Loading raises, so neither measurement is reached with a hollow answer.
    """
    path = _write(
        tmp_path,
        [
            {
                "control_id": c.ticker,
                # A CIK with a status that cannot carry one, and no reason given.
                "mappings": [_verified_mapping(ticker=c.ticker, status="unresolved")],
            }
            for c in CONTROL_UNIVERSE
        ],
    )
    with pytest.raises(ConfigError):
        unresolved_controls(load_control_evidence(path))
    with pytest.raises(ConfigError):
        controls_awaiting_manual_verification(load_control_evidence(path))


def test_an_evidence_file_that_is_not_json_cannot_satisfy_either_measurement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        unresolved_controls(load_control_evidence(path))
    with pytest.raises(ConfigError):
        controls_awaiting_manual_verification(load_control_evidence(path))


def test_the_shipped_measurements_agree_with_the_resolution() -> None:
    """Relationships rather than constants, so this needs no edit per control.

    The counts themselves move every time a control is verified; what must hold
    whatever they are is that each measurement equals its own predicate over the
    resolution, and that neither has reached zero.
    """
    evidence = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    resolved = resolve_controls(evidence)
    unresolved = unresolved_controls(evidence)
    awaiting = controls_awaiting_manual_verification(evidence)

    assert len(resolved) == len(CONTROL_UNIVERSE)
    assert len(unresolved) == sum(1 for r in resolved if not r.counts_in_numerator)
    assert len(awaiting) == sum(1 for r in resolved if not r.is_manually_verified)
    assert {c.control.ticker for c in unresolved} <= {c.control.ticker for c in awaiting}
    assert unresolved, "every identity is resolved; the roadmap needs updating"
    assert awaiting, "Milestone 0b is complete; the roadmap needs updating"

    identified = {r.control.ticker for r in resolved if r.counts_in_numerator}
    assert identified == set(evidence.controls), (
        "every shipped record currently resolves; if that stops being true this "
        "assertion is the place to record which record does not"
    )


def test_the_shipped_state_measures_9_unresolved_and_11_awaiting_verification() -> None:
    """The current shipped snapshot, deliberately hard-coded.

    Every other test here is written as a relationship so it survives the next
    control being verified. This one is the exception on purpose: it is what the
    roadmap's status line quotes, so the two are pinned together and verifying a
    control fails this test until the roadmap is updated with it. It has now done
    exactly that for MSFT, CSCO, AMZN, SPY, QQQ, ETYS, WBVN, KOOP, MPPP, WCOM,
    EXDS, PSIX, GCTY and BCST in turn, which is the behaviour rather than a
    nuisance.
    """
    evidence = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    resolved = resolve_controls(evidence)

    assert len(resolved) == 30
    assert len(unresolved_controls(evidence)) == 9
    assert len(controls_awaiting_manual_verification(evidence)) == 11
    assert sum(1 for r in resolved if r.counts_in_numerator) == 21
    assert sum(1 for r in resolved if r.is_manually_verified) == 19
    assert sum(1 for r in resolved if r.status is MappingStatus.RESOLVED) == 2


# ---------------------------------------------------------------------------
# the shipped file
# ---------------------------------------------------------------------------


def test_the_shipped_evidence_file_is_valid() -> None:
    """Every recorded control is a real one, and none is silently dropped.

    The roster is asserted as a superset rather than an exact set: it grows by
    one every time a control is verified, and a test that has to be edited for
    each addition trains people to edit it without reading it. What must not
    happen is a control disappearing or an unknown id appearing, and both of
    those this catches.
    """
    loaded = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    verified_pilots = {"AAPL", "IPET", "GM", "BEL"}
    assert verified_pilots <= set(loaded.controls)
    assert set(loaded.controls) <= {c.ticker for c in CONTROL_UNIVERSE}


# ---------------------------------------------------------------------------
# the shipped GCTY lifecycle facts
#
# Two filings, two dates, two scopes, and the whole point is that they stay
# apart. An acquisition an acquirer says it completed, and a registration
# certification the target filed five days later, are not one event -- and
# neither of them is the day the ticker stopped being valid, which nothing
# inspected establishes.
# ---------------------------------------------------------------------------


def _shipped_gcty() -> Any:
    return load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GCTY"]


def test_shipped_gcty_carries_the_acquisition_completion_on_1999_05_28() -> None:
    """Issuer scope, body text, and the date the sentence names.

    The Yahoo 8-K was *filed* 1999-06-02 and *states* a completion of
    1999-05-28. Recording the filing date here would be recording the wrong one
    of the two dates the document carries.
    """
    mapping = _shipped_gcty().mappings[0]
    facts = [f for f in mapping.lifecycle_facts if f.scope is LifecycleScope.ISSUER]
    assert len(facts) == 1
    fact = facts[0]
    assert fact.date == dt.date(1999, 5, 28)
    assert fact.date_source is FactDateSource.BODY_TEXT
    assert "completed the acquisition of GeoCities" in fact.citation
    assert "0001047469-99-022911" in fact.citation
    assert "1011006" in fact.citation  # the acquirer's CIK, whose filing this is


def test_shipped_gcty_acquisition_fact_claims_only_completion() -> None:
    """No silent upgrade to a stronger proposition than the filing states.

    "Completed the acquisition" is what the 8-K says. "Merger effective",
    "consummated", "ceased to exist" and "stopped trading" are each a different
    claim, and none of them is in evidence.
    """
    mapping = _shipped_gcty().mappings[0]
    fact = next(f for f in mapping.lifecycle_facts if f.scope is LifecycleScope.ISSUER)
    for upgrade in ("merger effective", "consummated", "ceased to exist", "stopped trading"):
        assert upgrade not in fact.fact.lower(), upgrade


def test_shipped_gcty_carries_the_form_15_reporting_fact_on_1999_06_02() -> None:
    """SEC-reporting scope, header field, mirroring the IPET Form 15 precedent."""
    mapping = _shipped_gcty().mappings[0]
    facts = [f for f in mapping.lifecycle_facts if f.scope is LifecycleScope.SEC_REPORTING]
    assert len(facts) == 1
    fact = facts[0]
    assert fact.date == dt.date(1999, 6, 2)
    assert fact.date_source is FactDateSource.HEADER_FIELD
    assert "0001047469-99-022856" in fact.citation
    assert "COMMON STOCK, $0.001 PAR VALUE" in fact.fact
    assert "12g-4(a)(1)(i)" in fact.fact


def test_shipped_gcty_form_15_is_not_restated_as_a_delisting() -> None:
    """Termination of registration and removal from listing are different things.

    The scope carries most of this -- ``sec_reporting`` is not
    ``exchange_listing`` -- but the prose must not undo it either.
    """
    mapping = _shipped_gcty().mappings[0]
    fact = next(f for f in mapping.lifecycle_facts if f.scope is LifecycleScope.SEC_REPORTING)
    for wrong in ("delisted", "last trading", "ticker termination"):
        assert wrong not in fact.fact.lower(), wrong
    assert not any(f.scope is LifecycleScope.EXCHANGE_LISTING for f in mapping.lifecycle_facts)


def test_the_two_shipped_gcty_dates_stay_distinct_propositions() -> None:
    """The failure this pair exists to prevent is collapsing them into one.

    Both filings reached EDGAR on 1999-06-02, which is exactly what makes the
    completion date easy to lose. Two facts, two scopes, two dates, five days
    apart.
    """
    facts = _shipped_gcty().mappings[0].lifecycle_facts
    assert len(facts) == 2
    dates = {f.scope: f.date for f in facts}
    assert dates[LifecycleScope.ISSUER] == dt.date(1999, 5, 28)
    assert dates[LifecycleScope.SEC_REPORTING] == dt.date(1999, 6, 2)
    assert dates[LifecycleScope.ISSUER] != dates[LifecycleScope.SEC_REPORTING]


def test_shipped_gcty_lifecycle_facts_assign_no_last_trading_date() -> None:
    """No fact may date the end of the series, because nothing establishes it.

    Every mention of an ending in this record is a denial. The test reads each
    sentence containing one and requires it to be negated, which is stricter
    than checking the words are absent -- they are *supposed* to appear, as
    things explicitly not established.
    """
    control = _shipped_gcty()
    mapping = control.mappings[0]
    prose = " ".join(
        [control.notes, mapping.scope_notes]
        + [f.fact + " " + f.note for f in mapping.lifecycle_facts]
    )
    negations = ("does not", "not establish", "not derived", "unknown", "nothing is",
                 "never be restated", "not adjudicated", "not supplied", "no inference")
    for phrase in ("last gcty trading session", "cessation of quotation", "end-of-series"):
        sentences = [s for s in prose.replace("\n", " ").split(". ") if phrase in s.lower()]
        assert sentences, f"{phrase} should be addressed explicitly"
        for sentence in sentences:
            assert any(n in sentence.lower() for n in negations), sentence


def test_shipped_gcty_keeps_a_null_validity_window_and_one_issuer() -> None:
    """Identity is untouched by the lifecycle work.

    ``valid_from``/``valid_to`` mean generic ticker validity; ``LifecycleFact``
    exists so a lifecycle event is not overloaded onto them. Neither recorded
    date is a ticker-validity boundary, so both ends stay unknown.
    """
    control = _shipped_gcty()
    mapping = control.mappings[0]
    assert mapping.valid_from is None
    assert mapping.valid_to is None
    assert control.identity_break is False
    assert len(control.mappings) == 1
    assert mapping.status is MappingStatus.MANUAL_VERIFIED
    assert mapping.cik == 1062777
    assert mapping.ticker == "GCTY"


def test_no_yahoo_issuer_mapping_is_attached_to_gcty() -> None:
    """The series must end, not continue into the acquirer.

    Yahoo's acquisition is a lifecycle event about GeoCities. It is not a ticker
    mapping, and the acquirer is not a second issuer for this ticker -- that
    would be the splice the control exists to catch.
    """
    control = _shipped_gcty()
    assert [m.cik for m in control.mappings] == [1062777]
    assert [m.ticker for m in control.mappings] == ["GCTY"]
    # The acquirer's CIK may be cited as the source of a fact, never mapped.
    assert all(m.cik != 1011006 for m in control.mappings)
    assert all(m.ticker != "YHOO" for m in control.mappings)


def test_shipped_gcty_names_what_it_does_not_establish() -> None:
    """The open questions are enumerated rather than left to inference."""
    notes = _shipped_gcty().notes.lower()
    for open_question in (
        "last gcty trading session",
        "nasdaq delisting",
        "cessation of quotation",
        "ticker-validity end date",
        "effective time",
        "reuse of the ticker",
    ):
        assert open_question in notes, open_question


# ---------------------------------------------------------------------------
# verified_on: a human's calendar date, never a clock's
#
# The field records when a *person* reviewed the cited evidence. Deriving it from
# a clock answers a different question, and silently changes the recorded answer
# at whatever midnight that clock observes. These tests hold the authoring path
# clock-free, and hold the loader to storing what was supplied.
# ---------------------------------------------------------------------------


def _evidence_loader_source() -> str:
    import tradeit.edgar.control_evidence as module

    return Path(module.__file__).read_text(encoding="utf-8")


def test_the_evidence_loader_reads_no_clock() -> None:
    """Structural, and checked against the parse tree rather than the prose.

    Three records authored either side of a UTC midnight once disagreed about
    which day it was, because the authoring step consulted ``date -u``. Nothing
    in the loader did, and nothing in it may start: a clock here would make the
    stored date depend on when the file happened to be read.

    The AST is asked rather than the text so an unusual spelling still fails.
    """
    import ast

    tree = ast.parse(_evidence_loader_source())
    clock_names = {"now", "today", "utcnow", "fromtimestamp", "time", "monotonic"}
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in clock_names:
            found.append(func.attr)
        elif isinstance(func, ast.Name) and func.id in clock_names:
            found.append(func.id)

    assert not found, f"the evidence loader reached for a clock: {found}"


def test_the_evidence_loader_imports_no_clock_module() -> None:
    """``datetime`` is imported for the type; ``time`` has no business here."""
    import ast

    tree = ast.parse(_evidence_loader_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert "time" not in imported, imported
    assert "calendar" not in imported, imported


def test_verified_on_is_stored_exactly_as_supplied(tmp_path: Path) -> None:
    """Whatever the author wrote is what the record holds.

    Dates far from today in both directions round-trip unchanged, which is the
    behaviour that proves nothing clamps, defaults or second-guesses the supplied
    value against the machine's current date.
    """
    for supplied in ("1999-11-05", "2026-08-22", "2026-08-23", "2099-12-31"):
        path = _write(
            tmp_path,
            [{"control_id": "AAPL", "mappings": [_verified_mapping(verified_on=supplied)]}],
        )
        mapping = load_control_evidence(path).controls["AAPL"].mappings[0]
        assert mapping.verified_on == dt.date.fromisoformat(supplied)
        assert mapping.summary()["verified_on"] == supplied


def test_a_verified_on_the_machine_would_call_future_is_not_rejected(tmp_path: Path) -> None:
    """Deliberately permitted, and the reason is the point.

    A legitimate explicitly supplied review date must not depend on the reading
    machine's timezone or clock. An operator ahead of UTC can honestly record a
    date this container still thinks is tomorrow, and a rule comparing against
    "today" would reject a true statement for being read in the wrong place.

    The date is a fixed literal rather than ``today() + 1``: this test asserts
    that no clock governs the field, so consulting one to build the input would
    undercut the thing being asserted -- and would make the test's own meaning
    depend on when it runs.
    """
    ahead = "2099-12-31"
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(verified_on=ahead)]}],
    )
    mapping = load_control_evidence(path).controls["AAPL"].mappings[0]
    assert mapping.verified_on == dt.date(2099, 12, 31)
    assert mapping.status is MappingStatus.MANUAL_VERIFIED


@pytest.mark.parametrize("bad", ["", None, "2026-13-01", "22/08/2026", "today"])
def test_manual_verified_still_requires_a_real_iso_date(tmp_path: Path, bad: Any) -> None:
    """The existing requirement is unchanged by documenting the convention.

    An evidence-backed mapping needs a non-null, parseable ISO date. Leaving it
    out is the honest move when the review date is unknown -- and it costs the
    mapping its status, which is exactly the trade the convention intends.
    """
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(verified_on=bad)]}],
    )
    with pytest.raises(ConfigError, match="verified_on"):
        load_control_evidence(path)


def test_every_shipped_verified_on_is_an_explicit_date() -> None:
    """The corpus rule, asserted over what shipped.

    Not that the dates agree with each other or with any clock -- records written
    before the convention existed are left as they stand -- but that every
    evidence-backed mapping carries a real supplied date rather than a blank or a
    placeholder.
    """
    loaded = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    for control in loaded.controls.values():
        for mapping in control.mappings:
            if mapping.counts_in_numerator:
                assert isinstance(mapping.verified_on, dt.date)


# ---------------------------------------------------------------------------
# citation integrity: text must say what its source says
#
# A citation is presented as a verbatim quotation of a primary source, so a
# character that is in the record but not in the filing is a defect even when
# every field validates. The class caught here is double-escaping: text escaped
# once by a builder and once again by the serializer, which round-trips as valid
# JSON and reaches the operator's screen as ``(\"OTC\")``.
# ---------------------------------------------------------------------------


def test_ordinary_quotation_marks_in_a_citation_are_valid(tmp_path: Path) -> None:
    """The common case, and the one the safeguard must never break.

    Nearly every shipped citation quotes a filing, and 246 quotation marks are
    already in the corpus. If quoting cost anything, citations would stop being
    verbatim.
    """
    quoted = 'SEC Form 10-K, accession 0000320193-25-000001: "Common Stock, $0.00001 par value"'
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(citation=quoted)]}],
    )
    mapping = load_control_evidence(path).controls["AAPL"].mappings[0]
    assert mapping.citation == quoted
    assert "\\" not in mapping.citation


def test_the_etys_style_escaped_quote_is_refused(tmp_path: Path) -> None:
    """The exact defect: a backslash that survived into the runtime string.

    ``json.loads`` consumes JSON escaping, so ``\\"`` on disk *is* a plain quote
    in memory. A backslash still standing in front of a quote at runtime means
    the text was escaped twice, and the reader is looking at the serializer
    rather than at the filing.
    """
    over_escaped = 'approved for quotation on the Nasdaq National Market under \\"ETYS\\"'
    assert "\\" in over_escaped  # the artifact is in the loaded string, not the JSON syntax
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(citation=over_escaped)]}],
    )
    with pytest.raises(ConfigError, match="serialization artifact"):
        load_control_evidence(path)


def test_literal_newline_and_tab_escapes_are_refused(tmp_path: Path) -> None:
    """The same defect wearing a different escape.

    ``\\n`` written as two characters where a paragraph break was meant is the
    same over-escaping mistake, and reads as backslash-en in the diagnostic.
    """
    for artifact in ("first line\\nsecond line", "column\\tcolumn"):
        path = _write(
            tmp_path,
            [{"control_id": "AAPL", "mappings": [_verified_mapping(scope_notes=artifact)]}],
        )
        with pytest.raises(ConfigError, match="serialization artifact"):
            load_control_evidence(path)


def test_real_newlines_apostrophes_and_accessions_remain_valid(tmp_path: Path) -> None:
    """Ordinary prose is untouched.

    Real paragraph breaks, possessive apostrophes, hyphenated accession numbers,
    the registered-mark sign and the em dash all appear in the shipped corpus and
    none of them is an escape artifact.
    """
    prose = (
        "SEC Form 424B4, accession 0001047469-99-021620, filed 1999-05-20.\n\n"
        "The registrant's own offering document -- SPDR®, — and $1.00 included."
    )
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(scope_notes=prose)]}],
    )
    mapping = load_control_evidence(path).controls["AAPL"].mappings[0]
    assert mapping.scope_notes == prose


def test_the_check_reaches_lifecycle_facts_and_top_level_notes(tmp_path: Path) -> None:
    """Every string in the file, not the prose fields someone remembered to list.

    The shipped defect this safeguard was written for sat in *two* places -- a
    mapping citation and a lifecycle fact's citation -- so a check wired only to
    mapping citations would have caught half of it.
    """
    fact = {
        "scope": str(LifecycleScope.EXCHANGE_LISTING),
        "fact": "removed from listing",
        "date": "2001-04-23",
        "date_source": "body_text",
        "citation": 'the Over-the-Counter (\\"OTC\\") market',
    }
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(lifecycle_facts=[fact])]}],
    )
    with pytest.raises(ConfigError, match="serialization artifact"):
        load_control_evidence(path)

    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping()]}],
        notes='roster note with a stray \\"quote\\"',
    )
    with pytest.raises(ConfigError, match="serialization artifact"):
        load_control_evidence(path)


def test_the_escape_check_does_not_weaken_any_other_rule(tmp_path: Path) -> None:
    """Malformed evidence still fails closed under the existing semantics.

    The new walk runs before any field is interpreted, so the risk worth testing
    is that it somehow short-circuits the rules that follow it. A record that is
    clean of escape artifacts and still invalid must still be refused.
    """
    path = _write(
        tmp_path,
        [{"control_id": "AAPL", "mappings": [_verified_mapping(citation="")]}],
    )
    with pytest.raises(ConfigError, match="citation"):
        load_control_evidence(path)


def test_no_shipped_string_carries_a_serialization_artifact() -> None:
    """The corpus regression test, asserted over the file rather than the loader.

    ``load_control_evidence`` now refuses such a file outright, so this would pass
    vacuously if it only called the loader. It walks the parsed payload directly,
    so it keeps reporting *where* an artifact is if one is ever reintroduced, and
    it keeps holding if the loader check is ever relaxed.
    """
    payload = json.loads(DEFAULT_EVIDENCE_PATH.read_text(encoding="utf-8"))

    def walk(node: Any, path: str) -> list[tuple[str, str]]:
        if isinstance(node, str):
            found = re.search(r'\\["\\/bfnrtu]', node)
            return [(path, found.group(0))] if found else []
        if isinstance(node, dict):
            return [hit for k, v in node.items() for hit in walk(v, f"{path}.{k}")]
        if isinstance(node, list):
            return [hit for i, v in enumerate(node) for hit in walk(v, f"{path}[{i}]")]
        return []

    assert walk(payload, "evidence") == []


def test_shipped_tglo_quotes_the_filing_without_escape_artifacts() -> None:
    """The record the defect was actually found in, pinned.

    The 10-Q writes ``the Over-the-Counter ("OTC") market``. Both the mapping
    citation and the lifecycle fact's citation quote that sentence, and both once
    carried it double-escaped.
    """
    mapping = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["TGLO"].mappings[0]
    assert 'Over-the-Counter ("OTC") market' in mapping.citation
    assert 'Over-the-Counter ("OTC") market' in mapping.lifecycle_facts[0].citation
    assert "\\" not in mapping.citation
    assert "\\" not in mapping.lifecycle_facts[0].citation
    # The correction was presentation only: the identity it records is unchanged.
    assert mapping.cik == 1066684
    assert mapping.ticker == "TGLO"
    assert mapping.status is MappingStatus.MANUAL_VERIFIED


def test_every_shipped_cik_carries_a_citation() -> None:
    """A CIK without a citation is a CIK someone remembered.

    This replaces an earlier blanket "no CIK anywhere" assertion, which was true
    only until the first control was verified. The durable rule is not that CIKs
    are absent -- it is that no CIK exists without primary-source provenance.
    """
    loaded = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    for control in loaded.controls.values():
        for mapping in control.mappings:
            if mapping.cik is None:
                assert mapping.status is MappingStatus.UNRESOLVED
                assert mapping.unresolved_reason
            else:
                assert mapping.counts_in_numerator
                assert mapping.citation
                assert mapping.verified_on is not None


def test_shipped_aapl_is_resolved_from_the_sec_ticker_file() -> None:
    """RESOLVED, not MANUAL_VERIFIED: the SEC ticker file is primary evidence,
    but MANUAL_VERIFIED stays reserved for a human-checked filing citation."""
    aapl = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["AAPL"]
    assert aapl.identity_break is False
    assert len(aapl.mappings) == 1
    mapping = aapl.mappings[0]
    assert mapping.cik == 320193
    assert mapping.ticker == "AAPL"
    assert mapping.status is MappingStatus.RESOLVED
    assert mapping.status is not MappingStatus.MANUAL_VERIFIED
    assert mapping.evidence is MappingEvidence.SEC_COMPANY_TICKERS
    assert "company_tickers.json" in mapping.citation
    assert mapping.verified_on == dt.date(2026, 8, 14)


def test_shipped_aapl_invents_no_ticker_validity_dates() -> None:
    """company_tickers.json proves the current mapping and dates nothing.

    The filing spine corroborates that one registrant persisted across the
    Apple Computer -> Apple Inc. rename; it does not establish the dates on
    which the ticker AAPL was valid, so both ends stay null.
    """
    mapping = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["AAPL"].mappings[0]
    assert mapping.valid_from is None
    assert mapping.valid_to is None


# ---------------------------------------------------------------------------
# the shipped IPET record -- a 2000-era delisted issuer, ticker from the
# offering document because no ticker reference file will ever hold it
# ---------------------------------------------------------------------------


def _shipped_ipet() -> Any:
    return load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["IPET"].mappings[0]


def test_shipped_ipet_identity() -> None:
    ipet = _shipped_ipet()
    assert ipet.cik == 1100683
    assert ipet.ticker == "IPET"
    assert ipet.status is MappingStatus.MANUAL_VERIFIED
    assert ipet.verified_on == dt.date(2026, 8, 15)


def test_shipped_ipet_promotion_is_not_a_name_match() -> None:
    """'Pets.com, Inc.' resembling the control name is not what promoted it."""
    ipet = _shipped_ipet()
    assert ipet.evidence is MappingEvidence.MANUAL_FILING_CITATION
    assert ipet.evidence is not MappingEvidence.NAME_MATCH
    assert "0000891618-00-000749" in ipet.citation
    assert "IPET" in ipet.citation


def test_shipped_ipet_wind_down_is_issuer_scoped_and_cited() -> None:
    fact = next(f for f in _shipped_ipet().lifecycle_facts if f.date == dt.date(2000, 11, 7))
    assert fact.scope is LifecycleScope.ISSUER
    assert fact.date_source is FactDateSource.BODY_TEXT
    assert "0001095811-00-004383" in fact.citation
    assert "wind down" in fact.fact.lower()


def test_shipped_ipet_wind_down_is_not_corporate_death_or_delisting() -> None:
    """The single most important negative assertion in this record.

    An operational wind-down is not dissolution, not bankruptcy, not delisting
    and not the end of ticker validity. Each needs its own filing -- and each now
    has one, filed later and dated differently, which is the point: the delisting
    is 2001-01-18, not 2000-11-07, and nothing back-dated it.
    """
    ipet = _shipped_ipet()
    wind_down = next(f for f in ipet.lifecycle_facts if f.date == dt.date(2000, 11, 7))
    note = wind_down.note.lower()
    for excluded in ("dissolution", "bankruptcy", "delisting", "extinguishment", "valid_to"):
        assert excluded in note
    # The wind-down date leaked into nothing: not ticker validity, not the
    # delisting fact, not the dissolution fact.
    assert ipet.valid_from != dt.date(2000, 11, 7)
    assert ipet.valid_to != dt.date(2000, 11, 7)
    assert all(
        f.date != dt.date(2000, 11, 7)
        for f in ipet.lifecycle_facts
        if f.scope is not LifecycleScope.ISSUER
    )


def test_shipped_ipet_validity_dates_rest_on_symbol_evidence_not_the_delisting() -> None:
    """Both ends were null until a filing named the symbol and its dates.

    The prospectus proved the symbol was *approved for quotation*, which is not
    the date trading began, so valid_from stayed null through the first pass. The
    10-K states the trading period outright. valid_to coincides with the delisting
    but does not rest on it -- the same sentence names IPET through that date and
    IPETZ after it, which is a claim about the symbol.
    """
    ipet = _shipped_ipet()
    assert ipet.valid_from == dt.date(2000, 2, 11)
    assert ipet.valid_to == dt.date(2001, 1, 18)
    assert "0000891618-02-001559" in ipet.citation
    assert "under the symbol IPET" in ipet.citation


def test_shipped_ipet_records_seven_lifecycle_facts() -> None:
    """One per evidenced event, and no event without a filing behind it."""
    facts = _shipped_ipet().lifecycle_facts
    assert len(facts) == 7
    assert [f.date for f in facts] == sorted(f.date for f in facts)


# ---------------------------------------------------------------------------
# the shipped GM record -- ticker reuse across two registrants
# ---------------------------------------------------------------------------


def _shipped_gm() -> dict[str, Any]:
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    return {m.issuer_label: m for m in gm.mappings} | {"_control": gm}


def test_shipped_gm_has_exactly_two_issuers_and_declares_the_break() -> None:
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    assert gm.identity_break is True
    assert len(gm.mappings) == 2
    assert {m.issuer_label for m in gm.mappings} == {"old_gm", "new_gm"}


def test_shipped_gm_issuers_share_a_ticker_but_not_a_cik() -> None:
    """The whole point: one ticker, two registrants, never concatenated."""
    issuers = _shipped_gm()
    assert issuers["old_gm"].cik == 40730
    assert issuers["new_gm"].cik == 1467858
    assert issuers["old_gm"].cik != issuers["new_gm"].cik
    assert issuers["old_gm"].ticker == "GM"
    assert issuers["new_gm"].ticker == "GM"


def test_shipped_old_gm_is_manual_verified_from_a_filing_citation() -> None:
    old = _shipped_gm()["old_gm"]
    assert old.status is MappingStatus.MANUAL_VERIFIED
    assert old.evidence is MappingEvidence.MANUAL_FILING_CITATION
    assert old.citation
    assert "0001193125-09-045144" in old.citation
    assert old.verified_on == dt.date(2026, 8, 14)


def test_shipped_new_gm_is_resolved_from_the_sec_ticker_file() -> None:
    """Same precedent as AAPL: viewing the JSON by hand is not a filing citation."""
    new = _shipped_gm()["new_gm"]
    assert new.status is MappingStatus.RESOLVED
    assert new.status is not MappingStatus.MANUAL_VERIFIED
    assert new.evidence is MappingEvidence.SEC_COMPANY_TICKERS
    assert "company_tickers.json" in new.citation


def test_shipped_gm_invents_no_ticker_validity_dates() -> None:
    """The July 2009 removal is an exchange-listing fact, not generic validity."""
    issuers = _shipped_gm()
    for label in ("old_gm", "new_gm"):
        assert issuers[label].valid_from is None
        assert issuers[label].valid_to is None


def test_old_gm_records_the_nyse_common_stock_removal_as_a_scoped_fact() -> None:
    old = _shipped_gm()["old_gm"]
    removal = next(
        f
        for f in old.lifecycle_facts
        if f.date_source is FactDateSource.BODY_TEXT and f.scope is LifecycleScope.EXCHANGE_LISTING
    )
    assert removal.date == dt.date(2009, 7, 20)
    assert "0000876661-09-000303" in removal.citation


def test_old_gm_keeps_the_header_effectiveness_date_separate() -> None:
    """Header EFFECTIVENESS DATE and body removal date are different claims."""
    old = _shipped_gm()["old_gm"]
    header = next(f for f in old.lifecycle_facts if f.date_source is FactDateSource.HEADER_FIELD)
    body = next(f for f in old.lifecycle_facts if f.date_source is FactDateSource.BODY_TEXT)
    assert header.date == dt.date(2009, 7, 8)
    assert body.date == dt.date(2009, 7, 20)
    assert header.date != body.date


def test_the_debenture_form_25_is_never_cited_as_common_stock_evidence() -> None:
    """0000876661-09-000262 covers the Series D debentures, not the common stock.

    It may appear in a note that explains why it is excluded; it must never
    appear as the citation of a lifecycle fact or of a mapping.
    """
    old = _shipped_gm()["old_gm"]
    debenture = "0000876661-09-000262"
    assert debenture not in old.citation
    for fact in old.lifecycle_facts:
        assert debenture not in fact.citation


def test_gm_control_status_is_its_weakest_issuer() -> None:
    """MANUAL_VERIFIED + RESOLVED aggregates to RESOLVED, which is correct."""
    resolved = {
        r.control.ticker: r for r in resolve_controls(load_control_evidence(DEFAULT_EVIDENCE_PATH))
    }
    assert resolved["GM"].status is MappingStatus.RESOLVED


# ---------------------------------------------------------------------------
# lifecycle-fact validation
# ---------------------------------------------------------------------------


def _fact(**overrides: Any) -> dict[str, Any]:
    record = {
        "scope": "exchange_listing",
        "fact": "removed from listing",
        "date": "2009-07-20",
        "date_source": "body_text",
        "citation": "accession 0000876661-09-000303",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"date_source": None}, "date_source is required"),
        ({"citation": ""}, "citation is required"),
        ({"scope": None}, "scope is required"),
        ({"fact": ""}, "fact text is required"),
        ({"date": None}, "date is required"),
        ({"scope": "not_a_scope"}, "unknown scope"),
        ({"date_source": "guessed"}, "unknown date_source"),
    ],
)
def test_lifecycle_fact_validation(tmp_path: Path, override: dict[str, Any], match: str) -> None:
    path = _write(
        tmp_path,
        [
            {
                "control_id": "AAPL",
                "mappings": [_verified_mapping(lifecycle_facts=[_fact(**override)])],
            }
        ],
    )
    with pytest.raises(ConfigError, match=match):
        load_control_evidence(path)


def test_the_shipped_gm_record_models_the_identity_break() -> None:
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    assert gm.identity_break is True
    assert {m.issuer_label for m in gm.mappings} == {"old_gm", "new_gm"}


# ---------------------------------------------------------------------------
# the finalized IPET pilot -- the seven things that must not silently change
#
# Each of these encodes a distinction that was argued for and could be lost by a
# plausible-looking future edit. A rename quietly becoming an identity break, a
# ticker window quietly becoming an issuer lifespan, a second quotation symbol
# quietly merging into the first, a header date quietly becoming a legal date --
# every one of those reads as tidying up.
# ---------------------------------------------------------------------------


def test_ipet_rename_does_not_create_an_identity_break() -> None:
    """Pets.com -> IPET Holdings is one registrant, so one mapping."""
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["IPET"]
    assert control.identity_break is False
    assert len(control.mappings) == 1
    assert control.mappings[0].cik == 1100683

    rename = next(f for f in control.mappings[0].lifecycle_facts if "name change" in f.fact.lower())
    assert rename.scope is LifecycleScope.ISSUER
    assert rename.date == dt.date(2001, 1, 16)
    assert "no merger, reorganization or successor issuer" in rename.fact.lower()


def test_a_second_ipet_mapping_on_the_same_cik_is_refused(tmp_path: Path) -> None:
    """The model blocks the wrong representation rather than trusting judgement.

    Representing the IPETZ quotation symbol as a second issuer is the mistake
    this record exists to avoid, and it is unavailable: two mappings sharing a
    CIK is a load error, so it cannot be done by accident or by conviction.
    """
    path = _write(
        tmp_path,
        [
            {
                "control_id": "IPET",
                "expected_company": "Pets.com, Inc.",
                "identity_break": True,
                "mappings": [
                    _verified_mapping(issuer_label="primary", cik=1100683, ticker="IPET"),
                    _verified_mapping(issuer_label="otc", cik=1100683, ticker="IPETZ"),
                ],
            }
        ],
    )
    with pytest.raises(ConfigError, match="the same cik appears on two issuers"):
        load_control_evidence(path)


def test_ipet_ticker_window_is_not_issuer_existence() -> None:
    """valid_to is 2001-01-18 and the issuer demonstrably outlived it."""
    ipet = _shipped_ipet()
    assert ipet.valid_to == dt.date(2001, 1, 18)

    later = [f for f in ipet.lifecycle_facts if f.date > ipet.valid_to]
    assert later, "the record must show the issuer alive after the ticker window closed"
    assert any(f.scope is LifecycleScope.SEC_REPORTING for f in later)

    dissolution = next(
        f for f in ipet.lifecycle_facts if "certificate of dissolution" in f.fact.lower()
    )
    assert "NOT CORPORATE EXTINCTION" in dissolution.note
    notes = ipet.scope_notes.lower()
    assert "not issuer existence" in notes
    assert "not security-class extinction" in notes


def test_ipet_and_ipetz_are_never_merged_into_one_ticker() -> None:
    """A successor quotation symbol is not a second identity, and not a listing."""
    ipet = _shipped_ipet()
    assert ipet.ticker == "IPET"
    assert "IPETZ" in ipet.scope_notes
    # IPETZ appears nowhere as a dated fact, because no date for it is evidenced.
    assert all("IPETZ" not in f.fact for f in ipet.lifecycle_facts)
    # And is never scoped as an exchange listing: OTC services quote, not list.
    listings = [f for f in ipet.lifecycle_facts if f.scope is LifecycleScope.EXCHANGE_LISTING]
    assert all("IPETZ" not in f.fact and "IPETZ" not in f.note for f in listings)
    assert "not scoped exchange_listing" in ipet.scope_notes.lower()


def test_ipet_form_15_identifies_only_rule_12g_4_a_1_i() -> None:
    """The checked box is decoded from the raw Wingdings, not inferred."""
    form15 = next(
        f for f in _shipped_ipet().lifecycle_facts if f.scope is LifecycleScope.SEC_REPORTING
    )
    assert form15.date == dt.date(2005, 6, 3)
    assert "0000950134-05-011306" in form15.citation
    assert "12g-4(a)(1)(i)" in form15.fact
    assert "&#253;" in form15.note and "checked" in form15.note
    assert "&#168;" in form15.note and "empty" in form15.note
    assert "251 holders of record" in form15.fact
    assert "000-29387" in form15.fact
    # No other deregistration rule is claimed to be relied upon.
    for other in ("12h-3", "12g-4(a)(1)(ii)", "15d-6"):
        assert other not in form15.fact


def test_ipet_beyond_com_anomaly_is_retained_and_changes_no_identity() -> None:
    """An unexplained sentence is recorded as unexplained, not resolved away."""
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["IPET"]
    form15 = next(
        f for f in control.mappings[0].lifecycle_facts if f.scope is LifecycleScope.SEC_REPORTING
    )
    assert "Beyond.com Corporation has caused this certification/notice to be signed" in form15.note
    assert "RECORDED AND NOT RESOLVED" in form15.note
    assert "No explanation for the discrepancy is offered or invented" in form15.note
    # The competing primary fields are cited, so a reader can weigh it.
    for corroborant in ("IPET HOLDINGS INC", "1100683", "000-29387", "Richard G. Couch"):
        assert corroborant in form15.note
    # And identity is untouched.
    assert len(control.mappings) == 1
    assert control.identity_break is False
    assert control.mappings[0].cik == 1100683


def test_ipet_sgml_name_change_date_never_overrides_the_filing_body() -> None:
    """19991208 predates the IPO. It is preserved, and it is not used."""
    ipet = _shipped_ipet()
    rename = next(f for f in ipet.lifecycle_facts if "name change" in f.fact.lower())
    assert rename.date == dt.date(2001, 1, 16)
    assert "19991208" in rename.note
    assert "preserved as unexplained metadata" in rename.note
    # The bad value is nowhere a date, and no cause was invented for it.
    assert all(f.date != dt.date(1999, 12, 8) for f in ipet.lifecycle_facts)
    assert ipet.valid_from != dt.date(1999, 12, 8)


def test_ipet_form_15_dates_never_become_a_legal_termination_date() -> None:
    """Filing date, header field and the 90-day rule stay three separate things."""
    form15 = next(
        f for f in _shipped_ipet().lifecycle_facts if f.scope is LifecycleScope.SEC_REPORTING
    )
    assert form15.date_source is FactDateSource.HEADER_FIELD
    assert "EFFECTIVENESS DATE 20050603" in form15.note
    assert "is NOT equated with the legal effective date" in form15.note
    assert "remains unestablished" in form15.note
    # The derivable date is named as derivable and refused, never recorded.
    assert "2005-09-01 is derivable but conditional" in form15.note
    everything = "\n".join([f.fact + f.citation for f in _shipped_ipet().lifecycle_facts])
    assert "2005-09-01" not in everything


# ---------------------------------------------------------------------------
# the BEL pilot -- one continuing issuer whose ticker changed
#
# BEL exists in the fixture as the deliberate contrast to GM. Both controls end
# with one ticker attached to something other than what it started on, and the
# whole methodology turns on the two cases being different: GM is two
# registrants sharing a symbol, BEL is one registrant changing symbol. A model
# that treats "the ticker changed" as the trigger for identity_break gets one of
# them wrong, and it is not obvious from the outside which.
# ---------------------------------------------------------------------------


def _shipped_bel() -> IssuerMapping:
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["BEL"]
    assert len(control.mappings) == 1
    return control.mappings[0]


def test_bel_is_one_continuing_issuer_not_a_succession() -> None:
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["BEL"]
    assert control.identity_break is False
    assert len(control.mappings) == 1
    assert control.mappings[0].cik == 732712
    assert control.mappings[0].status is MappingStatus.MANUAL_VERIFIED
    assert "0000950134-00-005461" in control.mappings[0].citation


def test_bel_records_the_merger_direction_so_the_survivor_is_unambiguous() -> None:
    """A reverse triangular merger, and which side disappeared decides identity.

    Bell Atlantic's own subsidiary merged into GTE. Read carelessly -- "Bell
    Atlantic and GTE merged" -- this is the shape that invites a successor
    issuer that does not exist.
    """
    merger = next(
        f
        for f in _shipped_bel().lifecycle_facts
        if f.scope is LifecycleScope.ISSUER and "Beta Gamma" in f.fact
    )
    assert merger.date == dt.date(2000, 6, 30)
    assert "merged with and into GTE" in merger.fact
    assert "wholly owned subsidiary of Bell Atlantic" in merger.fact
    assert "remained the continuing parent issuer" in merger.fact
    assert "NOT an identity break" in merger.note


def test_bel_and_gm_are_the_two_proven_ends_of_the_ticker_question() -> None:
    """The distinction the control pair exists to prove, pinned as two facts.

    Both controls involve a ticker that ends up meaning something else. GM is
    two registrants sharing one symbol; BEL is one registrant changing symbol.
    These are assertions about **these two controls**, established from their
    filings -- deliberately not a general rule inferred from them. See
    :func:`test_cik_count_is_context_for_an_identity_break_not_its_definition`.
    """
    evidence = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    bel, gm = evidence.controls["BEL"], evidence.controls["GM"]

    # BEL: the 8-K names the merger direction, so continuity is evidenced.
    assert bel.identity_break is False
    assert {m.cik for m in bel.mappings} == {732712}
    assert len(bel.mappings) == 1

    # GM: two registrants, proven separately, each with its own citation.
    assert gm.identity_break is True
    assert len({m.cik for m in gm.mappings}) == 2
    assert len(gm.mappings) == 2


def test_a_ticker_change_alone_does_not_imply_an_identity_break() -> None:
    """BEL's symbol changed and its issuer did not, and the record says both."""
    bel = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["BEL"]
    listing = next(
        f for f in bel.mappings[0].lifecycle_facts if f.scope is LifecycleScope.EXCHANGE_LISTING
    )
    assert "new symbol" in listing.fact or "new symbol" in listing.note
    assert bel.identity_break is False


def test_a_shared_ticker_alone_does_not_imply_continuity() -> None:
    """The converse, which is the error full-01 actually made.

    Both GM issuers carry the ticker GM. Identical symbols across two
    registrants is precisely what a naive merge treats as one series.
    """
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    assert {m.ticker for m in gm.mappings} == {"GM"}
    assert gm.identity_break is True


def test_cik_count_is_context_for_an_identity_break_not_its_definition(
    tmp_path: Path,
) -> None:
    """``identity_break`` is a declared finding, never computed from CIK count.

    An earlier version of this file asserted
    ``identity_break == (len({m.cik for m in mappings}) > 1)`` across every
    shipped control. It passed, and it was wrong to assert: two controls are not
    a definition, and promoting an observation about them into a universal rule
    would let CIK arithmetic stand in for reading the legal history. A registrant
    that keeps its CIK through a genuine discontinuity would then be
    unrecordable *by rule* rather than *by evidence*.

    What the loader actually enforces is a consistency check on **mapping
    count** -- the declaration and the record must agree -- which is a different
    claim, and this test pins that difference.
    """
    two_issuers = [
        _verified_mapping(issuer_label="old", cik=40730, ticker="GM"),
        _verified_mapping(issuer_label="new", cik=1467858, ticker="GM"),
    ]

    # The declaration is read from the record, not derived from the CIKs.
    loaded = load_control_evidence(
        _write(
            tmp_path / "ok",
            [
                {
                    "control_id": "GM",
                    "expected_company": "General Motors",
                    "identity_break": True,
                    "mappings": two_issuers,
                }
            ],
        )
    )
    assert loaded.controls["GM"].identity_break is True

    # And what is enforced is agreement with the number of ISSUERS recorded --
    # the error names issuers, not CIKs.
    with pytest.raises(ConfigError, match="issuers but identity_break is false"):
        load_control_evidence(
            _write(
                tmp_path / "undeclared",
                [
                    {
                        "control_id": "GM",
                        "expected_company": "General Motors",
                        "identity_break": False,
                        "mappings": two_issuers,
                    }
                ],
            )
        )
    with pytest.raises(ConfigError, match="only one issuer is recorded"):
        load_control_evidence(
            _write(
                tmp_path / "overdeclared",
                [
                    {
                        "control_id": "BEL",
                        "expected_company": "Bell Atlantic Corporation",
                        "identity_break": True,
                        "mappings": [_verified_mapping(cik=732712, ticker="VZ")],
                    }
                ],
            )
        )


def test_bel_merger_date_and_vz_trading_date_cannot_collapse() -> None:
    """2000-06-30 and 2000-07-03 are different events three days apart."""
    facts = _shipped_bel().lifecycle_facts
    merger = next(f for f in facts if "Beta Gamma" in f.fact)
    listing = next(f for f in facts if f.scope is LifecycleScope.EXCHANGE_LISTING)

    assert merger.date == dt.date(2000, 6, 30)
    assert listing.date == dt.date(2000, 7, 3)
    assert merger.date != listing.date
    assert "NOT THE MERGER DATE" in listing.note

    # No listing fact is dated on the merger date, and no issuer fact is dated
    # on the trading date -- the two dates never cross scopes.
    assert all(
        f.date != dt.date(2000, 6, 30) for f in facts if f.scope is LifecycleScope.EXCHANGE_LISTING
    )
    assert all(f.date != dt.date(2000, 7, 3) for f in facts if f.scope is LifecycleScope.ISSUER)


def test_bel_never_claims_vz_traded_before_the_evidenced_date() -> None:
    """A prospective statement does not become generic ticker validity."""
    bel = _shipped_bel()
    assert bel.ticker == "BEL"
    assert bel.valid_from is None
    assert bel.valid_to is None

    listing = next(f for f in bel.lifecycle_facts if f.scope is LifecycleScope.EXCHANGE_LISTING)
    assert listing.date == dt.date(2000, 7, 3)
    assert "PROSPECTIVE STATEMENT" in listing.note
    # Nothing anywhere dates VZ earlier than the evidenced Monday.
    assert all(f.date <= dt.date(2000, 7, 3) or "VZ" not in f.fact for f in bel.lifecycle_facts)
    assert "IPET precedent" in bel.scope_notes


def test_bel_ticker_is_cited_not_assumed(tmp_path: Path) -> None:
    """'BEL' is in the mapping because a filing says so, not because we knew it.

    The record carried 'VZ' here for one commit, with the historical symbol
    recorded as an open gap, precisely because no inspected filing named it.
    The 1999 filing closed the gap by stating the NYSE symbol outright, and this
    test fails if the ticker is ever present without that citation behind it.
    """
    bel = _shipped_bel()
    assert bel.ticker == "BEL"
    assert bel.cik == 732712
    assert bel.status is MappingStatus.MANUAL_VERIFIED
    assert "0000950130-99-002148" in bel.citation
    assert "under the symbol 'BEL'" in bel.citation
    # The gap language from the previous draft is gone, not merely contradicted.
    assert "does NOT name the prior symbol" not in bel.scope_notes
    assert "background knowledge is not evidence" not in bel.scope_notes

    # And the grade itself cannot survive without a citation: MANUAL_VERIFIED
    # with the citation removed is a load error, not a silent downgrade.
    with pytest.raises(ConfigError, match="requires a citation"):
        load_control_evidence(
            _write(
                tmp_path,
                [
                    {
                        "control_id": "BEL",
                        "expected_company": "Bell Atlantic Corporation",
                        "mappings": [_verified_mapping(cik=732712, ticker="BEL", citation="")],
                    }
                ],
            )
        )


def test_bel_keeps_vz_as_a_dated_fact_rather_than_a_second_mapping() -> None:
    """Both symbols are recorded; only one of them is an issuer mapping.

    A symbol change on a continuing issuer is not a second identity. The model
    refuses the wrong shape twice over -- two mappings sharing a CIK is a load
    error, and any second mapping would force identity_break true -- so VZ is
    carried where it belongs, as a dated exchange_listing fact.
    """
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["BEL"]
    assert len(control.mappings) == 1
    assert control.mappings[0].ticker == "BEL"
    assert control.identity_break is False

    listing = next(
        f for f in control.mappings[0].lifecycle_facts if f.scope is LifecycleScope.EXCHANGE_LISTING
    )
    assert "VZ" in listing.fact
    assert listing.date == dt.date(2000, 7, 3)
    # VZ is not smuggled into the mapping by any route.
    assert control.mappings[0].ticker != "VZ"
    assert all(m.ticker != "VZ" for m in control.mappings)


def test_bel_invents_no_last_trading_day_for_the_old_symbol() -> None:
    """The Friday before is derivable from a calendar and is not evidence."""
    bel = _shipped_bel()
    assert bel.valid_to is None
    assert "arithmetic, not evidence" in bel.scope_notes
    # The two dates a careless reading would reach for appear on no fact and on
    # no validity boundary.
    for invented in (dt.date(2000, 6, 30), dt.date(2000, 7, 3)):
        assert bel.valid_from != invented
        assert bel.valid_to != invented


def test_bel_records_a_dba_and_invents_no_legal_name_change_date() -> None:
    dba = next(f for f in _shipped_bel().lifecycle_facts if "Doing Business As" in f.fact)
    assert dba.date == dt.date(2000, 6, 30)
    assert "A D/B/A IS NOT A LEGAL NAME CHANGE" in dba.note
    assert "not used as a legal effective date" in dba.note
    # No fact in the record claims a corporate-name-change effective date.
    assert all("name change" not in f.fact.lower() for f in _shipped_bel().lifecycle_facts)


def test_bel_share_issuance_is_not_a_new_class_or_an_extinguishment() -> None:
    """1.175 billion new shares into an existing class is dilution, not a birth."""
    issuance = next(
        f for f in _shipped_bel().lifecycle_facts if f.scope is LifecycleScope.SECURITY_CLASS
    )
    assert issuance.date == dt.date(2000, 6, 30)
    assert "1.175 billion" in issuance.fact
    assert "not the creation of a new class" in issuance.note
    assert "not the extinguishment of the old one" in issuance.note


# ---------------------------------------------------------------------------
# the TGLO pilot -- the negative control
#
# Every other control so far proves an event happened. This one proves several
# did not. TGLO carries the full set of ingredients for a false exit -- a
# restructured business, a Nasdaq delisting, a move to the OTC bulletin board,
# and a 598-day hole in the filing history -- and the correct output is no exit
# at all. These tests fail if any of those ingredients is ever promoted.
# ---------------------------------------------------------------------------


def _shipped_tglo() -> IssuerMapping:
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["TGLO"]
    assert len(control.mappings) == 1
    return control.mappings[0]


def test_tglo_maps_to_its_cik_and_ticker_from_cited_filings() -> None:
    """Both symbol eras are cited, and neither is assumed."""
    tglo = _shipped_tglo()
    assert tglo.cik == 1066684
    assert tglo.ticker == "TGLO"
    assert tglo.status is MappingStatus.MANUAL_VERIFIED
    assert tglo.evidence is MappingEvidence.MANUAL_FILING_CITATION
    # Nasdaq era: the FY2000 annual report names the symbol outright.
    assert "0000950130-01-001623" in tglo.citation
    assert "under the Symbol 'TGLO'" in tglo.citation
    # OTC era: the 10-Q names the same symbol on the new venue.
    assert "0000950130-01-501712" in tglo.citation


def test_tglo_delisting_is_dated_exactly_and_scoped_to_the_listing() -> None:
    facts = _shipped_tglo().lifecycle_facts
    assert len(facts) == 1
    delisting = facts[0]
    assert delisting.date == dt.date(2001, 4, 23)
    assert delisting.date_source is FactDateSource.BODY_TEXT
    assert delisting.scope is LifecycleScope.EXCHANGE_LISTING
    assert "0000950130-01-501712" in delisting.citation
    assert "$1 bid price" in delisting.fact or "$1 bid price" in delisting.citation


def test_tglo_delisting_is_not_an_issuer_exit() -> None:
    """The whole point of the control, asserted on the record itself."""
    delisting = _shipped_tglo().lifecycle_facts[0]
    note = delisting.note
    for excluded in (
        "NOT issuer extinction",
        "NOT bankruptcy",
        "NOT registration termination",
        "NOT security-class extinguishment",
        "NOT a survivorship exit",
    ):
        assert excluded in note
    # No fact of any other scope exists, because no other event was evidenced.
    scopes = {f.scope for f in _shipped_tglo().lifecycle_facts}
    assert scopes == {LifecycleScope.EXCHANGE_LISTING}


def test_tglo_otc_continuation_creates_no_second_issuer() -> None:
    """A change of quotation venue is not a change of identity."""
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["TGLO"]
    assert len(control.mappings) == 1
    assert control.identity_break is False
    assert control.mappings[0].ticker == "TGLO"
    assert "not a second security" in control.mappings[0].scope_notes


def test_tglo_ob_is_venue_notation_not_a_ticker_identity() -> None:
    """'.OB' is where the quote came from, not what the security is called."""
    tglo = _shipped_tglo()
    assert tglo.ticker == "TGLO"
    assert tglo.ticker != "TGLO.OB"
    # It is explained in the notes and asserted nowhere as an identity.
    assert "TGLO.OB" in tglo.scope_notes
    assert "venue notation" in tglo.scope_notes
    assert all("TGLO.OB" not in f.fact for f in tglo.lifecycle_facts)


def test_tglo_records_no_sec_reporting_event_from_the_interior_gap() -> None:
    """598 days of silence that later ends is not cessation, and never becomes it."""
    tglo = _shipped_tglo()
    assert all(f.scope is not LifecycleScope.SEC_REPORTING for f in tglo.lifecycle_facts)
    assert "INTERIOR gap" in tglo.scope_notes
    assert "not cessation" in tglo.scope_notes
    # The gap's endpoints appear on no fact and on no validity boundary.
    for endpoint in (dt.date(2003, 9, 23), dt.date(2005, 5, 13)):
        assert all(f.date != endpoint for f in tglo.lifecycle_facts)
        assert tglo.valid_from != endpoint
        assert tglo.valid_to != endpoint


def test_tglo_restructuring_is_not_promoted_to_extinction() -> None:
    """$41.3m of charges and a closed business line are not a closed company."""
    notes = _shipped_tglo().scope_notes
    assert "41.3 million" in notes
    assert "Seattle e-commerce" in notes
    for excluded in (
        "NOT a complete business shutdown",
        "NOT shell status",
        "NOT dissolution",
        "NOT deregistration",
        "NOT security extinguishment",
        "NOT an identity break",
    ):
        assert excluded in notes
    # And it is not a dated lifecycle fact, because no filing dates it.
    assert all("restructuring" not in f.fact.lower() for f in _shipped_tglo().lifecycle_facts)


def test_tglo_infers_no_identity_break_from_collapse_delisting_or_otc() -> None:
    control = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["TGLO"]
    assert control.identity_break is False
    assert len({m.cik for m in control.mappings}) == 1
    # Contrast: GM's break rests on two registrants, not on a bad year.
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    assert gm.identity_break is True
    assert len({m.cik for m in gm.mappings}) == 2


def test_tglo_leaves_no_partial_date_workaround_behind() -> None:
    """The exact day was found, so no month-precision hedge may remain.

    Before the 10-Q was inspected, the best available precision was 'April
    2001', which the schema cannot store. That pressure is gone and the record
    must not carry a residue of it.
    """
    tglo = _shipped_tglo()
    delisting = tglo.lifecycle_facts[0]
    assert delisting.date == dt.date(2001, 4, 23)
    for hedge in ("April 2001", "month precision", "approximately", "circa", "on or about"):
        assert hedge not in delisting.fact
        assert hedge not in delisting.note
    # The two boundary days a guess would have reached for are recorded nowhere.
    for guessed in (dt.date(2001, 4, 1), dt.date(2001, 4, 30)):
        assert all(f.date != guessed for f in tglo.lifecycle_facts)


def test_tglo_keeps_the_four_dates_of_the_delisting_apart() -> None:
    """Determination, appeal, delisting and filing are four different dates."""
    delisting = _shipped_tglo().lifecycle_facts[0]
    assert delisting.date == dt.date(2001, 4, 23)
    assert "only the last is recorded here" in delisting.note
    # Neither the FY2000 filing date nor the 10-Q filing date became the event.
    for filing_date in (dt.date(2001, 4, 2), dt.date(2001, 5, 15)):
        assert delisting.date != filing_date


def test_tglo_does_not_claim_a_continuous_filing_history() -> None:
    """The gap is disclosed where the history is described, not only elsewhere.

    An earlier draft opened the scope notes with "continuous from 1998 to the
    present", which is true of the issuer and false of the filing history, and
    a reader has no way to tell which was meant. The record now says which.
    """
    tglo = _shipped_tglo()
    assert "NOT literally continuous" in tglo.scope_notes
    assert "598-day interior periodic-filing gap" in tglo.scope_notes
    assert "2003-09-23" in tglo.scope_notes and "2005-05-13" in tglo.scope_notes
    # And the methodological point survives the rewording.
    assert "must not be treated as filing cessation" in tglo.scope_notes


def test_tglo_negative_findings_state_what_was_actually_searched() -> None:
    """A negative result is only as broad as the search behind it.

    The index search covered form types across all 360 filings; the manual
    search covered four filing bodies. Those are different scopes and the
    record names both rather than merging them into an unqualified "nothing
    establishes".
    """
    notes = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["TGLO"].notes
    assert "PROGRAMMATIC" in notes and "MANUAL" in notes
    assert "all 360 indexed filings" in notes
    assert "no evidence of an issuer discontinuity was found in the filings inspected" in notes
    # The limit of the claim is stated, not left for a reader to infer.
    assert "not a proof that no such filing exists anywhere" in notes
    assert "356 of the 360 filing bodies were not read" in notes
    # Each manually inspected accession is named so the scope is checkable.
    for accession in (
        "0000950130-01-001623",
        "0001015402-02-001340",
        "0000950130-01-501712",
        "0001144204-06-012657",
    ):
        assert accession in notes


# ---------------------------------------------------------------------------
# the ENE pilot -- succession where the equity survived
#
# ENE is the third shape of identity break. IPET renamed itself and stayed one
# issuer. BEL's own subsidiary disappeared and BEL survived. GM's registrant
# was replaced and the old equity was extinguished. ENE is none of those: the
# registrant itself merged out of existence, a new registrant survived under
# the same name and ticker, and every share converted one-for-one. The boolean
# that records the break cannot express that difference, so these tests pin
# the facts that carry it -- and pin the four dates, two conversion ratios and
# three unproved propositions that a later summary could collapse.
# ---------------------------------------------------------------------------


def _shipped_ene() -> ControlEvidence:
    return load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["ENE"]


def _ene_mapping(label: str) -> IssuerMapping:
    matches = [m for m in _shipped_ene().mappings if m.issuer_label == label]
    assert len(matches) == 1, f"expected exactly one {label!r} mapping"
    return matches[0]


def _ene_text() -> list[str]:
    """Every free-text field on the ENE record, for whole-record assertions."""
    control = _shipped_ene()
    out = [control.notes]
    for mapping in control.mappings:
        out += [mapping.citation, mapping.scope_notes, mapping.unresolved_reason]
        for fact in mapping.lifecycle_facts:
            out += [fact.fact, fact.citation, fact.note]
    return out


def test_ene_is_two_issuers_with_the_break_declared() -> None:
    """Two registrants, one ticker, and a break that does not mean what GM's means."""
    control = _shipped_ene()
    assert control.identity_break is True
    assert len(control.mappings) == 2
    assert {m.cik for m in control.mappings} == {72859, 1024401}
    assert {m.ticker for m in control.mappings} == {"ENE"}
    assert all(m.status is MappingStatus.MANUAL_VERIFIED for m in control.mappings)
    assert all(m.evidence is MappingEvidence.MANUAL_FILING_CITATION for m in control.mappings)

    # The boolean is identical to GM's; the records are not. ENE carries a
    # security-class conversion fact because the equity continued. GM carries
    # none because it did not. That difference lives in the facts, never in
    # the flag, and a consumer reading only the flag must not conclude either.
    ene_scopes = {f.scope for m in control.mappings for f in m.lifecycle_facts}
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    gm_scopes = {f.scope for m in gm.mappings for f in m.lifecycle_facts}
    assert gm.identity_break is control.identity_break is True
    assert LifecycleScope.SECURITY_CLASS in ene_scopes
    assert LifecycleScope.SECURITY_CLASS not in gm_scopes


def test_ene_delaware_predecessor_ceased_to_exist() -> None:
    """The registrant was the disappearing party, and the direction is recorded."""
    delaware = _ene_mapping("delaware")
    assert delaware.cik == 72859
    assert delaware.ticker == "ENE"
    assert "0000950129-96-002469" in delaware.citation

    facts = delaware.lifecycle_facts
    assert len(facts) == 1
    merger = facts[0]
    assert merger.scope is LifecycleScope.ISSUER
    assert merger.date == dt.date(1997, 7, 1)
    assert merger.date_source is FactDateSource.BODY_TEXT
    assert "merged with and into" in merger.fact
    assert "ceased to exist" in merger.fact
    assert "0001024401-97-000002" in merger.citation

    # Direction is the whole finding: BEL's registrant survived, this one did not.
    assert "inverse of BEL" in merger.note

    # The predecessor's ticker rests on the text, not on a header that names
    # two filers, and the record says so rather than letting a reader assume.
    assert "MULTIPLE FILER BLOCKS" in delaware.scope_notes
    assert "1024401" in delaware.scope_notes


def test_ene_oregon_successor_became_the_successor_issuer() -> None:
    """The successor's ticker is proved by its own filing, not carried across."""
    oregon = _ene_mapping("oregon")
    assert oregon.cik == 1024401
    assert oregon.ticker == "ENE"
    # A post-merger filing by this registrant, offering this registrant's stock.
    assert "0000950129-98-001874" in oregon.citation
    assert "trades under the symbol ENE" in oregon.citation
    assert "not carried across the merger by inference" in oregon.scope_notes

    succession = [
        f
        for f in oregon.lifecycle_facts
        if f.scope is LifecycleScope.ISSUER and f.date == dt.date(1997, 7, 1)
    ]
    assert len(succession) == 1
    assert "survived" in succession[0].fact
    assert "successor issuer" in succession[0].fact
    assert succession[0].date_source is FactDateSource.BODY_TEXT

    # The SGML name-change date contradicts the bodies and is kept as an
    # anomaly rather than silently adopted as the rename date.
    assert "19961008" in succession[0].note
    assert succession[0].date != dt.date(1996, 10, 8)


def test_ene_conversion_ratios_cannot_be_attributed_to_the_wrong_transaction() -> None:
    """Two mergers, two ratios, and no path by which they can swap."""
    oregon = _ene_mapping("oregon")
    conversions = [f for f in oregon.lifecycle_facts if f.scope is LifecycleScope.SECURITY_CLASS]
    assert len(conversions) == 1
    conversion = conversions[0]
    assert conversion.date == dt.date(1997, 7, 1)
    assert "one-for-one" in conversion.fact

    # The PGC ratio is named only to be excluded, and never inside the fact.
    assert "0.9825" not in conversion.fact
    assert "0.9825" in conversion.note
    assert "PGC Merger" in conversion.note
    assert "must never be attributed to the Reincorporation Merger" in conversion.note

    # And it reaches no other field of the record either.
    assert sum("0.9825" in text for text in _ene_text()) == 1


def test_ene_conversion_is_sourced_to_the_pos_am_body() -> None:
    """Post-effective operative text, in DOCUMENT 1, not an exhibit or a prospectus."""
    conversion = next(
        f
        for f in _ene_mapping("oregon").lifecycle_facts
        if f.scope is LifecycleScope.SECURITY_CLASS
    )
    assert "0000950129-97-002781" in conversion.citation
    assert "DOCUMENT 1" in conversion.citation
    assert "post-effective" in conversion.citation

    # The exhibits carry the effective date, not the conversion terms, and are
    # named as non-sources so neither is later cited for this fact.
    assert "EX-3.02" in conversion.note and "EX-3.03" in conversion.note
    assert "do NOT carry the one-for-one conversion statement" in conversion.note

    # The 1996 prospective registration is antecedent context, never the source.
    assert "0000950129-96-002469" not in conversion.citation
    assert "0000950129-96-002433" not in conversion.citation


def test_ene_chapter_11_is_dated_and_issuer_scoped() -> None:
    """A petition is an issuer event and stays one."""
    bankruptcies = [
        f for f in _ene_mapping("oregon").lifecycle_facts if f.date == dt.date(2001, 12, 2)
    ]
    assert len(bankruptcies) == 1
    chapter_11 = bankruptcies[0]
    assert chapter_11.scope is LifecycleScope.ISSUER
    assert chapter_11.date_source is FactDateSource.BODY_TEXT
    assert "0001024401-01-500046" in chapter_11.citation
    assert "Chapter 11" in chapter_11.fact

    for excluded in (
        "NOT a delisting",
        "NOT cessation of trading",
        "NOT ticker termination",
        "NOT security extinguishment",
        "NOT registration termination",
        "NOT issuer extinction",
    ):
        assert excluded in chapter_11.note

    # The subsidiaries filed later; their dates belong to them and never
    # become the parent's date.
    for subsidiary_day in (dt.date(2001, 12, 3), dt.date(2001, 12, 6)):
        assert all(f.date != subsidiary_day for f in _ene_mapping("oregon").lifecycle_facts)


def test_ene_records_no_listing_or_reporting_fact() -> None:
    """Nothing was proved about the listing or the registration, so nothing is recorded."""
    scopes = {f.scope for m in _shipped_ene().mappings for f in m.lifecycle_facts}
    assert scopes == {LifecycleScope.ISSUER, LifecycleScope.SECURITY_CLASS}
    assert LifecycleScope.EXCHANGE_LISTING not in scopes
    assert LifecycleScope.SEC_REPORTING not in scopes

    notes = _ene_mapping("oregon").scope_notes
    # The Form 25 absence cannot support a delisting inference in this era.
    assert "electronic Form 25 filing began 2005-04-24" in notes
    assert "Absence therefore proves nothing" in notes
    # A registrant that stops filing has not thereby done anything.
    assert "2005-11-29" in notes
    assert "NOT a registration termination" in notes

    # The control-level record refuses the headline the fixture invites.
    control_notes = _shipped_ene().notes
    assert "does NOT establish an NYSE delisting" in control_notes
    assert "large-cap disappearance" in control_notes
    assert "NOT proved by this record" in control_notes


def test_ene_asserts_no_validity_boundaries() -> None:
    """Every symbol statement is present-tense, so neither end is known."""
    for mapping in _shipped_ene().mappings:
        assert mapping.valid_from is None
        assert mapping.valid_to is None

    # In particular, no event date leaked into a generic ticker window: not the
    # merger, not the petition, not any filing date.
    fact_dates = {f.date for m in _shipped_ene().mappings for f in m.lifecycle_facts}
    assert fact_dates == {dt.date(1997, 7, 1), dt.date(2001, 12, 2)}
    for mapping in _shipped_ene().mappings:
        assert mapping.valid_from not in fact_dates
        assert mapping.valid_to not in fact_dates


def test_ene_co_registrant_creates_no_mapping() -> None:
    """A co-registrant on one filing is not an issuer under this control."""
    control = _shipped_ene()
    assert 924024 not in {m.cik for m in control.mappings}
    assert len(control.mappings) == 2

    # It is identified rather than ignored, so a later reader meets it here
    # instead of rediscovering an unexplained CIK in the header.
    conversion = next(
        f
        for f in _ene_mapping("oregon").lifecycle_facts
        if f.scope is LifecycleScope.SECURITY_CLASS
    )
    assert "924024" in conversion.note
    assert "ENRON CAPITAL RESOURCES LP" in conversion.note
    assert "No issuer mapping is created for it" in conversion.note


def test_ene_8k_citation_uses_the_filed_as_of_date() -> None:
    """The 8-K was filed 1997-07-16. Three other dates sit next to it.

    Its Date of Report and signature date are July 15, 1997, and the merger
    took effect July 1, 1997. Only the first may follow the word "filed", and
    an earlier draft of this record used the report date as the filing date.
    """
    citing = [text for text in _ene_text() if "0001024401-97-000002" in text]
    assert citing, "the 8-K is cited somewhere on the record"
    for text in citing:
        assert "filed 1997-07-16" in text
        assert "filed 1997-07-15" not in text

    # No field anywhere calls 1997-07-15 a filing date, in any phrasing.
    for text in _ene_text():
        assert re.search(r"[Ff]iled[^.]{0,40}1997-07-15", text) is None

    # The event the fact is dated on is the merger, not any of the three
    # dates the filing itself carries.
    merger_dates = {
        f.date
        for m in _shipped_ene().mappings
        for f in m.lifecycle_facts
        if "0001024401-97-000002" in f.citation
    }
    assert merger_dates == {dt.date(1997, 7, 1)}
