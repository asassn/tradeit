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

import json
from pathlib import Path
from typing import Any

import pytest

from tradeit.edgar.control_evidence import (
    DEFAULT_EVIDENCE_PATH,
    SCHEMA_VERSION,
    load_control_evidence,
    resolve_controls,
)
from tradeit.edgar.controls import CONTROL_UNIVERSE
from tradeit.edgar.identity import MappingStatus
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


def test_the_shipped_file_contains_no_cik_at_all() -> None:
    """sec.gov is unreachable here, so not one CIK may have been written."""
    loaded = load_control_evidence(DEFAULT_EVIDENCE_PATH)
    for control in loaded.controls.values():
        for mapping in control.mappings:
            assert mapping.cik is None, f"{control.control_id} carries a CIK that was not verified"
            assert mapping.status is MappingStatus.UNRESOLVED


def test_the_shipped_gm_record_models_the_identity_break() -> None:
    gm = load_control_evidence(DEFAULT_EVIDENCE_PATH).controls["GM"]
    assert gm.identity_break is True
    assert {m.issuer_label for m in gm.mappings} == {"old_gm", "new_gm"}
