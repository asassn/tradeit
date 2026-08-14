"""The EDGAR denominator, and the two rules it must not be able to break.

The rules under test, both of which were corrections to an earlier design:

1. **Filing cessation is a candidate, not a death.** It never carries a
   lifecycle date, and :func:`assert_cessation_undated` fails loudly if one is
   ever attached.
2. **The top survivorship grade is unreachable** until a threshold is chosen
   from a real denominator. Passing all thirty controls is necessary and not
   sufficient.

Both are the kind of rule that erodes silently under later edits, so each has a
test whose failure is unambiguous.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.edgar.controls import CONTROL_UNIVERSE, unverified
from tradeit.edgar.denominator import (
    RESEARCH_GRADE_THRESHOLD,
    Classification,
    CoverageBounds,
    Denominator,
    SurvivorshipClass,
    classify_corpus,
)
from tradeit.edgar.evidence import (
    EvidenceStrength,
    EvidenceType,
    FormRole,
    LifecycleEvidence,
    LifecycleScope,
    classify_form,
)
from tradeit.edgar.identity import (
    MappingCandidate,
    MappingEvidence,
    MappingStatus,
    SecurityMapping,
    resolve_mapping,
)
from tradeit.edgar.index import (
    EDGAR_FIRST_QUARTER,
    IndexQuarter,
    accession_from_path,
    parse_full_index,
    quarters,
)
from tradeit.edgar.lifecycle import (
    ExitResolution,
    assert_cessation_undated,
    build_timelines,
    resolve_exit,
)
from tradeit.edgar.pipeline import evidence_from_rows
from tradeit.errors import ConfigError, DataError

# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

INDEX_BODY = """\
Description:           Master Index of EDGAR Dissemination Feed by Form Type
Form Type|Company Name|CIK|Date Filed|File Name
--------------------------------------------------------------------------------
320193|APPLE INC|10-K|1998-12-23|edgar/data/320193/0000320193-98-000110.txt
1000045|PETS COM INC|S-1|2000-01-20|edgar/data/1000045/0001000045-00-000001.txt
1000045|PETS COM INC|15-12G|2001-02-14|edgar/data/1000045/0001000045-01-000004.txt
garbage line with no pipes
55|SHORT|ROW
"""


def test_index_spine_starts_at_1994q3() -> None:
    assert EDGAR_FIRST_QUARTER == (1994, 3)
    IndexQuarter(1994, 3)
    with pytest.raises(DataError, match="1994"):
        IndexQuarter(1994, 2)
    with pytest.raises(DataError, match="1994"):
        IndexQuarter(1993, 1)


def test_quarters_enumerates_inclusive_and_rejects_reversed() -> None:
    got = list(quarters(IndexQuarter(1994, 3), IndexQuarter(1995, 2)))
    assert [q.label for q in got] == ["1994-QTR3", "1994-QTR4", "1995-QTR1", "1995-QTR2"]
    with pytest.raises(DataError, match="precedes"):
        list(quarters(IndexQuarter(1996, 1), IndexQuarter(1995, 1)))


def test_parse_skips_preamble_and_malformed_lines() -> None:
    rows = list(parse_full_index(INDEX_BODY, quarter_label="1998-QTR4"))
    assert len(rows) == 3
    assert rows[0].cik == 320193
    assert rows[0].form_type == "10-K"
    assert rows[0].filed_at == dt.date(1998, 12, 23)
    assert rows[0].accession == "0000320193-98-000110"
    assert rows[0].index_quarter == "1998-QTR4"


def test_accession_absent_is_empty_not_invented() -> None:
    assert accession_from_path("edgar/data/1/no-accession-here.txt") == ""


# ---------------------------------------------------------------------------
# evidence: four lifecycles, and what the index cannot tell us
# ---------------------------------------------------------------------------


def test_form_25_is_exchange_listing_not_reporting() -> None:
    signal = classify_form("25", dt.date(2010, 5, 1))
    assert signal.scope is LifecycleScope.EXCHANGE_LISTING
    assert signal.evidence_type is EvidenceType.CONFIRMED_EXCHANGE_DELISTING


def test_form_15_is_reporting_not_delisting() -> None:
    """A Form 15 ends a reporting duty. It does not say the shares stopped trading."""
    signal = classify_form("15-12G", dt.date(2001, 2, 14))
    assert signal.scope is LifecycleScope.SEC_REPORTING
    assert signal.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert signal.evidence_type is not EvidenceType.CONFIRMED_EXCHANGE_DELISTING


def test_8k_from_the_index_proves_nothing_without_the_document() -> None:
    """The full-index carries no item numbers, so an 8-K is just an 8-K."""
    signal = classify_form("8-K", dt.date(2001, 12, 3))
    assert signal.role is FormRole.EXIT_CANDIDATE
    assert signal.evidence_type is None
    assert signal.strength is EvidenceStrength.NONE
    assert signal.requires_document_text is True


def test_pre_2005_form_25_is_annotated_with_its_regime() -> None:
    early = classify_form("25", dt.date(1999, 6, 1))
    late = classify_form("25", dt.date(2015, 6, 1))
    assert "sparse" in early.note
    assert "sparse" not in late.note


def test_s4_is_a_pointer_not_a_confirmation() -> None:
    signal = classify_form("S-4", dt.date(1999, 3, 1))
    assert signal.role is FormRole.TRANSACTION_POINTER
    assert signal.evidence_type is None


def test_index_evidence_never_claims_an_effective_date() -> None:
    evidence = LifecycleEvidence.from_index_row(
        cik=1,
        company_name="X",
        form_type="25",
        filed_at=dt.date(2010, 1, 4),
        accession="0000000001-10-000001",
        source_path="edgar/data/1/x.txt",
        index_quarter="2010-QTR1",
    )
    assert evidence.evidence_date == dt.date(2010, 1, 4)
    # A Form 25's effective date is set by rule some days after filing. The
    # index does not carry it, so it stays None rather than borrowing the
    # filing date and inventing precision.
    assert evidence.effective_date is None
    assert evidence.security_class_known is False


# ---------------------------------------------------------------------------
# lifecycle: cessation is a candidate
# ---------------------------------------------------------------------------


def _ev(cik: int, form: str, filed: dt.date) -> LifecycleEvidence:
    return LifecycleEvidence.from_index_row(
        cik=cik,
        company_name=f"ISSUER {cik}",
        form_type=form,
        filed_at=filed,
        accession=f"{cik:010d}-{filed.year % 100:02d}-000001",
        source_path=f"edgar/data/{cik}/x.txt",
        index_quarter=f"{filed.year}-QTR{(filed.month - 1) // 3 + 1}",
    )


def test_cessation_yields_a_candidate_with_no_date() -> None:
    timeline = build_timelines(
        [
            _ev(10, "10-K", dt.date(1999, 3, 1)),
            _ev(10, "10-Q", dt.date(1999, 8, 1)),
            _ev(10, "10-K", dt.date(2000, 3, 1)),
        ]
    )[10]
    resolution = resolve_exit(timeline, as_of=dt.date(2004, 1, 1))
    assert resolution.evidence_type is EvidenceType.POSSIBLE_EXIT_FILING_CESSATION
    assert resolution.strength is EvidenceStrength.CESSATION_ONLY
    # The whole correction, in two assertions.
    assert resolution.evidence_date is None
    assert resolution.effective_date is None
    # The last filing is recorded as context, explicitly not as a death date.
    assert resolution.last_periodic == dt.date(2000, 3, 1)


def test_recent_silence_is_not_yet_even_a_candidate() -> None:
    timeline = build_timelines([_ev(11, "10-K", dt.date(2025, 3, 1))])[11]
    resolution = resolve_exit(timeline, as_of=dt.date(2026, 1, 1))
    assert resolution.evidence_type is EvidenceType.UNRESOLVED_EXIT


def test_resumption_cancels_the_candidate() -> None:
    """Delinquency that later reverses must not read as a death."""
    timeline = build_timelines(
        [
            _ev(12, "10-K", dt.date(2000, 3, 1)),
            _ev(12, "10-K", dt.date(2004, 3, 1)),
        ]
    )[12]
    resolution = resolve_exit(timeline, as_of=dt.date(2005, 1, 1))
    assert resolution.evidence_type is EvidenceType.UNRESOLVED_EXIT


def test_direct_evidence_beats_cessation() -> None:
    timeline = build_timelines(
        [
            _ev(13, "10-K", dt.date(2000, 3, 1)),
            _ev(13, "15-12G", dt.date(2001, 2, 14)),
        ]
    )[13]
    resolution = resolve_exit(timeline, as_of=dt.date(2010, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_REGISTRATION_TERMINATION
    assert resolution.strength is EvidenceStrength.FORM_DIRECT
    assert resolution.evidence_date == dt.date(2001, 2, 14)
    assert resolution.supporting[0].accession


def test_extinguishment_is_derived_from_two_scopes_never_one() -> None:
    only_delisting = build_timelines([_ev(14, "25", dt.date(2010, 5, 3))])[14]
    assert (
        resolve_exit(only_delisting, as_of=dt.date(2020, 1, 1)).evidence_type
        is EvidenceType.CONFIRMED_EXCHANGE_DELISTING
    )

    both = build_timelines(
        [_ev(15, "25", dt.date(2010, 5, 3)), _ev(15, "15-12B", dt.date(2010, 5, 20))]
    )[15]
    resolution = resolve_exit(both, as_of=dt.date(2020, 1, 1))
    assert resolution.evidence_type is EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED
    # Derived, and labelled as derived so it cannot be mistaken for a filing's claim.
    assert resolution.strength is EvidenceStrength.FORM_INFERRED
    assert resolution.scopes == frozenset(
        {LifecycleScope.EXCHANGE_LISTING, LifecycleScope.SEC_REPORTING}
    )


def test_guard_catches_a_dated_cessation() -> None:
    bad = ExitResolution(
        cik=99,
        company_name="X",
        evidence_type=EvidenceType.POSSIBLE_EXIT_FILING_CESSATION,
        strength=EvidenceStrength.CESSATION_ONLY,
        scopes=frozenset(),
        evidence_date=dt.date(2001, 1, 1),
        effective_date=None,
        supporting=(),
    )
    with pytest.raises(DataError, match="candidate signal"):
        assert_cessation_undated([bad])


def test_denominator_refuses_to_be_built_from_a_dated_cessation() -> None:
    bad = ExitResolution(
        cik=99,
        company_name="X",
        evidence_type=EvidenceType.POSSIBLE_EXIT_FILING_CESSATION,
        strength=EvidenceStrength.CESSATION_ONLY,
        scopes=frozenset(),
        evidence_date=None,
        effective_date=dt.date(2001, 1, 1),
        supporting=(),
    )
    with pytest.raises(DataError):
        Denominator(resolutions=[bad])


# ---------------------------------------------------------------------------
# identity: never fabricate
# ---------------------------------------------------------------------------


def test_name_match_can_never_resolve() -> None:
    mapping = resolve_mapping(1, [MappingCandidate("ACME", MappingEvidence.NAME_MATCH)])
    assert mapping.status is MappingStatus.AMBIGUOUS
    assert mapping.ticker is None


def test_full_text_search_alone_can_never_resolve() -> None:
    mapping = resolve_mapping(1, [MappingCandidate("ACME", MappingEvidence.FULL_TEXT_SEARCH)])
    assert mapping.status is MappingStatus.AMBIGUOUS


def test_no_evidence_is_unresolved_not_absent() -> None:
    mapping = resolve_mapping(1, [])
    assert mapping.status is MappingStatus.UNRESOLVED
    assert mapping.counts_in_numerator is False


def test_conflicting_candidates_are_ambiguous() -> None:
    mapping = resolve_mapping(
        1,
        [
            MappingCandidate("AAA", MappingEvidence.SEC_COMPANY_TICKERS),
            MappingCandidate("BBB", MappingEvidence.SEC_COMPANY_TICKERS),
        ],
    )
    assert mapping.status is MappingStatus.AMBIGUOUS
    assert mapping.ticker is None


def test_stronger_evidence_wins() -> None:
    mapping = resolve_mapping(
        1,
        [
            MappingCandidate("WRONG", MappingEvidence.NAME_MATCH),
            MappingCandidate("RIGHT", MappingEvidence.SEC_COMPANY_TICKERS),
        ],
    )
    assert mapping.status is MappingStatus.RESOLVED
    assert mapping.ticker == "RIGHT"


def test_manual_verified_requires_a_citation() -> None:
    with pytest.raises(ConfigError, match="citation"):
        SecurityMapping(cik=1, ticker="AAA", status=MappingStatus.MANUAL_VERIFIED, citation="")
    ok = SecurityMapping(
        cik=1,
        ticker="AAA",
        status=MappingStatus.MANUAL_VERIFIED,
        citation="accession 0000000001-99-000001",
    )
    assert ok.counts_in_numerator


def test_manual_candidate_without_citation_is_refused_not_promoted() -> None:
    mapping = resolve_mapping(
        1, [MappingCandidate("AAA", MappingEvidence.MANUAL_FILING_CITATION, citation="")]
    )
    assert mapping.status is MappingStatus.AMBIGUOUS


# ---------------------------------------------------------------------------
# denominator aggregation
# ---------------------------------------------------------------------------


def _denominator() -> Denominator:
    evidence = [
        # confirmed delisting + deregistration -> extinguished
        _ev(1, "8-A12B", dt.date(1996, 1, 5)),
        _ev(1, "10-K", dt.date(1999, 3, 1)),
        _ev(1, "25", dt.date(2010, 5, 3)),
        _ev(1, "15-12B", dt.date(2010, 5, 20)),
        # deregistration only
        _ev(2, "8-A12B", dt.date(1999, 6, 1)),
        _ev(2, "10-K", dt.date(2000, 3, 1)),
        _ev(2, "15-12G", dt.date(2001, 2, 14)),
        # cessation candidate, undated
        _ev(3, "8-A12B", dt.date(1999, 11, 1)),
        _ev(3, "10-K", dt.date(2000, 3, 1)),
        # still filing
        _ev(4, "8-A12B", dt.date(1995, 1, 3)),
        _ev(4, "10-K", dt.date(2025, 3, 1)),
    ]
    timelines = build_timelines(evidence)
    resolutions = [resolve_exit(t, as_of=dt.date(2026, 1, 1)) for t in timelines.values()]
    return Denominator(
        resolutions=resolutions,
        timelines=timelines,
        mappings={
            1: SecurityMapping(
                1, "AAA", MappingStatus.RESOLVED, MappingEvidence.FILING_DOCUMENT_TEXT
            ),
            2: SecurityMapping(2, None, MappingStatus.UNRESOLVED),
            3: SecurityMapping(3, None, MappingStatus.AMBIGUOUS),
        },
    )


def test_counts_split_by_evidence_type_and_strength() -> None:
    denominator = _denominator()
    by_type = denominator.counts_by_evidence_type()
    assert by_type[str(EvidenceType.CONFIRMED_SECURITY_EXTINGUISHED)] == 1
    assert by_type[str(EvidenceType.CONFIRMED_REGISTRATION_TERMINATION)] == 1
    assert by_type[str(EvidenceType.POSSIBLE_EXIT_FILING_CESSATION)] == 1
    assert by_type[str(EvidenceType.UNRESOLVED_EXIT)] == 1

    by_strength = denominator.counts_by_strength()
    assert by_strength[str(EvidenceStrength.CESSATION_ONLY)] == 1
    assert by_strength[str(EvidenceStrength.FORM_DIRECT)] == 1
    assert by_strength[str(EvidenceStrength.FORM_INFERRED)] == 1


def test_undated_exits_are_counted_and_not_placed_in_a_year() -> None:
    denominator = _denominator()
    years = denominator.counts_by_year()
    assert sum(years.values()) == 2  # the two confirmed ones
    assert denominator.undated_exits() == 2  # cessation + unresolved


def test_coverage_reports_two_bounds_that_differ() -> None:
    denominator = _denominator()
    bounds = denominator.coverage(["AAA"])
    assert bounds.matched_numerator == 1
    assert bounds.resolved_denominator == 1
    assert bounds.full_denominator == 4
    assert bounds.matched_coverage == pytest.approx(1.0)
    assert bounds.bounded_coverage == pytest.approx(0.25)
    # The gap is the identity-mapping uncertainty, reported rather than hidden.
    assert bounds.uncertainty == pytest.approx(0.75)


def test_empty_denominator_returns_none_not_zero() -> None:
    bounds = CoverageBounds(matched_numerator=0, resolved_denominator=0, full_denominator=0)
    assert bounds.matched_coverage is None
    assert bounds.bounded_coverage is None


def test_evidence_from_rows_drops_irrelevant_forms() -> None:
    rows = list(parse_full_index(INDEX_BODY, quarter_label="1998-QTR4"))
    kept = evidence_from_rows(rows)
    # 10-K (periodic) and 15-12G (direct) survive; S-1 is not lifecycle evidence.
    assert {e.form_type for e in kept} == {"10-K", "15-12G"}


# ---------------------------------------------------------------------------
# classification: the top grade is unreachable for now
# ---------------------------------------------------------------------------


def test_research_grade_threshold_is_deliberately_unset() -> None:
    assert RESEARCH_GRADE_THRESHOLD is None


def test_perfect_controls_and_high_coverage_still_do_not_grant_research_grade() -> None:
    """Thirty controls are a stress test, not proof of global completeness."""
    bounds = CoverageBounds(matched_numerator=95, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(bounds, controls_passed=30, controls_total=30)
    assert result.assigned is not SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE
    assert result.assigned is SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED
    assert "deliberately unset" in result.reason


def test_research_grade_becomes_reachable_only_when_a_threshold_is_chosen() -> None:
    bounds = CoverageBounds(matched_numerator=95, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(
        bounds, controls_passed=30, controls_total=30, research_grade_threshold=0.9
    )
    assert result.assigned is SurvivorshipClass.SURVIVORSHIP_SAFE_RESEARCH_GRADE


def test_failing_controls_blocks_research_grade_even_with_a_threshold() -> None:
    bounds = CoverageBounds(matched_numerator=99, resolved_denominator=100, full_denominator=100)
    result = classify_corpus(
        bounds, controls_passed=29, controls_total=30, research_grade_threshold=0.5
    )
    assert result.assigned is SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED


@pytest.mark.parametrize(
    ("matched", "expected"),
    [
        (60, SurvivorshipClass.MATERIALLY_SURVIVORSHIP_CORRECTED),
        (30, SurvivorshipClass.PARTIALLY_SURVIVORSHIP_CORRECTED),
        (10, SurvivorshipClass.SURVIVOR_BIASED),
    ],
)
def test_lower_classes_are_assigned_from_the_pessimistic_bound(
    matched: int, expected: SurvivorshipClass
) -> None:
    bounds = CoverageBounds(
        matched_numerator=matched, resolved_denominator=matched, full_denominator=100
    )
    result = classify_corpus(bounds, controls_passed=30, controls_total=30)
    assert result.assigned is expected


def test_classification_of_an_empty_denominator_is_a_refusal() -> None:
    bounds = CoverageBounds(0, 0, 0)
    result: Classification = classify_corpus(bounds, controls_passed=0, controls_total=30)
    assert result.assigned is None


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------


def test_control_universe_is_thirty_securities() -> None:
    assert len(CONTROL_UNIVERSE) == 30
    assert len({c.ticker for c in CONTROL_UNIVERSE}) == 30


def test_no_control_carries_a_fabricated_cik() -> None:
    """sec.gov is unreachable here, so every CIK must still be absent."""
    assert all(c.mapping.cik is None for c in CONTROL_UNIVERSE)
    assert all(c.mapping.status is not MappingStatus.MANUAL_VERIFIED for c in CONTROL_UNIVERSE)


def test_milestone_0b_reports_itself_incomplete() -> None:
    assert len(unverified()) == 30


def test_the_fixture_covers_the_failure_modes_it_claims_to() -> None:
    classes = " ".join(c.control_class for c in CONTROL_UNIVERSE)
    for needed in ("ticker reuse", "reverse split", "short-lived", "peak acquisition"):
        assert needed in classes
    reuse = [c for c in CONTROL_UNIVERSE if "ticker reuse" in c.control_class]
    assert len(reuse) == 3
    short_lived = [c for c in CONTROL_UNIVERSE if "short-lived" in c.control_class]
    assert len(short_lived) == 4
