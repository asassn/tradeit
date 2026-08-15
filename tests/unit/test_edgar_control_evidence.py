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
from pathlib import Path
from typing import Any

import pytest

from tradeit.edgar.control_evidence import (
    DEFAULT_EVIDENCE_PATH,
    SCHEMA_VERSION,
    FactDateSource,
    load_control_evidence,
    resolve_controls,
)
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.evidence import LifecycleScope
from tradeit.edgar.identity import MappingEvidence, MappingStatus
from tradeit.errors import ConfigError


def _write(tmp_path: Path, controls: list[dict[str, Any]], **top: Any) -> Path:
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "controls": controls}
    payload.update(top)
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
# the shipped file
# ---------------------------------------------------------------------------


def test_the_shipped_evidence_file_is_valid() -> None:
    loaded = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    assert set(loaded.controls) == {"AAPL", "IPET", "GM"}


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
    assert ipet.verified_on == dt.date(2026, 8, 14)


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
    and not the end of ticker validity. Each would need its own filing.
    """
    ipet = _shipped_ipet()
    # No exchange-listing fact was recorded, because none was evidenced.
    assert all(f.scope is not LifecycleScope.EXCHANGE_LISTING for f in ipet.lifecycle_facts)
    # And the wind-down date did not leak into generic ticker validity.
    assert ipet.valid_to is None
    wind_down = next(f for f in ipet.lifecycle_facts if f.date == dt.date(2000, 11, 7))
    note = wind_down.note.lower()
    for excluded in ("dissolution", "bankruptcy", "delisting", "extinguishment", "valid_to"):
        assert excluded in note


def test_shipped_ipet_invents_no_validity_dates() -> None:
    """Approval for quotation is not the date ticker validity began."""
    ipet = _shipped_ipet()
    assert ipet.valid_from is None
    assert ipet.valid_to is None


def test_shipped_ipet_records_exactly_one_lifecycle_fact() -> None:
    """Only what was evidenced. No delisting date was consulted, so none exists."""
    assert len(_shipped_ipet().lifecycle_facts) == 1


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
