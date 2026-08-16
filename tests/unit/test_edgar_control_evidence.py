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
    IssuerMapping,
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
