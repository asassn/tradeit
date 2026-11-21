"""Planning a declared portfolio against a corpus.

Built on a synthetic database rather than the real 73 GB file, so these run
everywhere — but each rule they assert was chosen because of what the real
corpus or the real engine does.

The property that matters most here is the one this module got wrong first:
**a holding that did not print today is carried, not refused.**
``EventDrivenEngine._context`` has carried one at its last known close from the
beginning, and says why — that price "is not a guess, it is the last thing
anybody knew, and being unchanged from yesterday it cannot trigger a stop that
yesterday did not". ``plan_declared`` instead raised and refused the entire
horizon, which stalls every *other* position's stop over one quiet name.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.core.enums import KnowledgeTimeSource
from tradeit.decide import DeclarationError, DeclaredHolding, DeclaredPortfolio, plan_declared
from tradeit.portfolio.mandate import Mandate
from tradeit.research01.pit import KnowledgeTimeBasis
from tradeit.storage.tables import Issuer, Sector, Security, SecurityPriceFact, SymbolAlias

UTC = dt.UTC
WARM = dt.date(2024, 1, 1)
AS_OF = dt.date(2024, 3, 1)


def _close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(21), tzinfo=UTC)


def _listed(session: Session, ticker: str, sector: str = "Manufacturing") -> int:
    issuer = Issuer(display_name=f"{ticker} Inc", source="test")
    session.add(issuer)
    session.flush()
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    sid = int(security.security_id)
    session.add(
        SymbolAlias(
            security_id=sid,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=dt.date(2000, 1, 1),
            valid_to=None,
            knowledge_source="test",
            knowledge_time=_close(dt.date(2000, 1, 1)),
            source="test",
        )
    )
    session.add(
        Sector(
            instrument_id=sid,
            scheme="SIC",
            sector=sector,
            valid_from=dt.date(2000, 1, 1),
            valid_to=None,
            ingested_at=_close(dt.date(2000, 1, 1)),
            source="test",
        )
    )
    session.flush()
    return sid


def _bars(session: Session, sid: int, days: list[dt.date], close: str = "100") -> None:
    for day in days:
        session.add(
            SecurityPriceFact(
                security_id=sid,
                session_date=day,
                adjustment_basis="raw",
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal(1_000_000),
                event_time=_close(day),
                knowledge_time=_close(day),
                knowledge_time_basis=KnowledgeTimeBasis.SESSION_CLOSE,
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                source="test",
            )
        )
    session.flush()


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _declared(**overrides: object) -> DeclaredPortfolio:
    payload: dict[str, object] = {
        "mandate": Mandate.SWING,
        "equity": Decimal(100_000),
        "cash": Decimal(50_000),
        "universe": ("QUIET", "LOUD"),
        "warmup_from": WARM,
        "as_of": AS_OF,
        "holdings": (),
        "score_lower": 0.0,
        "score_upper": 0.5,
        # Short windows: the point is the carry, not a crossover.
        "fast": 2,
        "slow": 5,
        "stop_pct": Decimal("0.08"),
    }
    payload.update(overrides)
    return DeclaredPortfolio(**payload)  # type: ignore[arg-type]


def _holding(ticker: str, **overrides: object) -> DeclaredHolding:
    payload: dict[str, object] = {
        "ticker": ticker,
        "quantity": Decimal(100),
        "entry_price": Decimal(100),
        "stop_price": Decimal(90),
        "sessions_held": 5,
        "opened_on": dt.date(2024, 1, 2),
    }
    payload.update(overrides)
    return DeclaredHolding(**payload)  # type: ignore[arg-type]


@pytest.fixture
def corpus(db_session: Session) -> dict[str, int]:
    """Two names. ``LOUD`` prints every weekday; ``QUIET`` stops two days early."""
    days = _weekdays(WARM, AS_OF)
    loud = _listed(db_session, "LOUD")
    quiet = _listed(db_session, "QUIET")
    _bars(db_session, loud, days, close="100")
    _bars(db_session, quiet, days[:-2], close="110")
    return {"LOUD": loud, "QUIET": quiet}


LONG_WARM = dt.date(2023, 1, 2)
LONG_AS_OF = dt.date(2024, 3, 1)


@pytest.fixture
def long_corpus(db_session: Session) -> dict[str, int]:
    """Over 300 weekdays, so the 63-session correlation floor is cleared.

    The two series move differently on purpose: a flat series has zero standard
    deviation and `pairwise_correlations` drops the pair rather than dividing
    by it, so a fixture of constant prices would measure nothing and the test
    would pass for the wrong reason.
    """
    days = _weekdays(LONG_WARM, LONG_AS_OF)
    walk = _listed(db_session, "WALK")
    drift = _listed(db_session, "DRIFT", sector="Services")
    for index, day in enumerate(days):
        _bars(db_session, walk, [day], close=str(100 + (index % 7)))
        _bars(db_session, drift, [day], close=str(100 + (index % 5)))
    return {"WALK": walk, "DRIFT": drift}


def test_a_quiet_holding_is_carried_at_its_last_close(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**The engine's rule, now this module's too.**

    The whole horizon used to be refused over one name with no print. Every
    other position's stop went unmanaged as a result, which is precisely the
    failure the refusal was meant to avoid.
    """
    result = plan_declared(
        db_session, _declared(holdings=(_holding("QUIET", entry_price=Decimal(110)),))
    )

    assert len(result.carried) == 1
    ticker, session_date = result.carried[0]
    assert ticker == "QUIET"
    assert session_date < AS_OF, "marked at an earlier session, and it says which"

    (held,) = result.plan.holdings
    assert held.last_price == Decimal(110), "the last price anybody quoted"
    assert held.open_risk == Decimal(100) * (Decimal(110) - Decimal(90))


def test_a_holding_that_printed_today_is_not_reported_as_carried(
    db_session: Session, corpus: dict[str, int]
) -> None:
    result = plan_declared(db_session, _declared(holdings=(_holding("LOUD"),)))

    assert result.carried == ()
    assert result.plan.holdings[0].last_price == Decimal(100)


def test_a_quiet_name_does_not_stall_the_other_positions(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """The reason the refusal was wrong: one quiet name blocked everything."""
    result = plan_declared(
        db_session,
        _declared(
            holdings=(_holding("QUIET", entry_price=Decimal(110)), _holding("LOUD")),
        ),
    )

    assert {h.reference for h in result.plan.holdings} == {"QUIET", "LOUD"}
    assert [t for t, _ in result.carried] == ["QUIET"]


def test_a_holding_that_never_printed_is_refused(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**No last known price exists, so there is nothing honest to carry.**

    That is an identity problem — the ticker resolves to a security with no
    prices in this window — not a quiet session, and the two want different
    answers.
    """
    ghost = _listed(db_session, "GHOST")
    assert ghost  # listed, with an alias and a sector, and no bars at all

    with pytest.raises(DeclarationError, match="no print anywhere"):
        plan_declared(
            db_session,
            _declared(universe=("LOUD", "GHOST"), holdings=(_holding("GHOST"),)),
        )


def test_an_unresolvable_as_of_names_the_last_session(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """A date the corpus has no session for is refused, naming the nearest."""
    with pytest.raises(DeclarationError, match="not a trading session"):
        plan_declared(db_session, _declared(as_of=dt.date(2024, 3, 2)))  # a Saturday


def test_the_plan_reports_what_it_loaded(db_session: Session, corpus: dict[str, int]) -> None:
    """A plan whose inputs are invisible cannot be checked."""
    result = plan_declared(db_session, _declared())

    assert result.sessions_loaded > 0
    assert result.bars_loaded > 0
    assert result.sectors_classified == 2
    assert result.seconds >= 0


def test_a_mandate_whose_corpus_does_not_exist_declines(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """Day's primary corpus is intraday-01, and a Daily slate would mislabel."""
    with pytest.raises(DeclarationError, match="declines to be scored"):
        plan_declared(db_session, _declared(mandate=Mandate.DAY))


# ---------------------------------------------------------------------------
# The plan and the backtest must install the same rules
#
# Measured 2026-11-19, they did not, and they differed in BOTH directions: the
# backtest installed `PositionSizeRule` and the plan did not, while the plan
# installed `SectorExposureRule` and the backtest did not. A plan showing a
# decision the backtest never validated, and a backtest validating a decision
# the plan would not make, is two systems wearing one name.
# ---------------------------------------------------------------------------


def test_the_plan_is_the_archives_rules_plus_only_what_is_declared() -> None:
    """One source of truth, and every departure from it named.

    The archive's rule set is frozen by what it ran — `build_engine`'s own
    docstring requires every scoreboard result to stay reproducible. The plan
    may apply *more* than the archive did, and when it does the extra control
    must be in `BEYOND_THE_ARCHIVE` so a reader comparing a plan against a
    scoreboard number knows the two were not decided under the same
    constraints. What is forbidden is an undeclared difference.
    """
    from tradeit.backtesting.baselines import base_rules
    from tradeit.decide.runner import BEYOND_THE_ARCHIVE, build_cycle
    from tradeit.strategy.config import StrategyConfig

    config = StrategyConfig(name="ma_cross_baseline")
    archived = {rule.name for rule in base_rules(config)}
    planned = {rule.name for rule in build_cycle(config, {}, {(1, 2): 0.5}).engine.rules}

    assert archived <= planned, f"the plan dropped an archived rule: {archived - planned}"
    assert planned - archived == set(BEYOND_THE_ARCHIVE), (
        "an undeclared difference between what the dashboard decides with and what "
        f"the backtest validated: {planned - archived} vs {set(BEYOND_THE_ARCHIVE)}"
    )


def test_without_a_correlation_matrix_the_rule_is_left_out_not_installed() -> None:
    """**It fails closed**, so installing it blind would refuse everything.

    An unmeasured pair rejects, by design — treating one as uncorrelated gives a
    portfolio that passes the correlation limit precisely because nobody
    measured it. With no matrix the honest move is to leave the rule out and
    report it absent, not to install it and watch the plan buy nothing.
    """
    from tradeit.decide.runner import build_cycle
    from tradeit.strategy.config import StrategyConfig

    config = StrategyConfig(name="ma_cross_baseline")
    names = {rule.name for rule in build_cycle(config, {}, None).engine.rules}

    assert "correlation_cluster" not in names


def test_sector_is_still_enforced_even_though_its_engine_rule_is_absent(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**Absent from the engine is not absent from the decision.**

    `score_fit` vetoes `sector_limit_reached` at the ranker, before any sizing.
    What the engine-stage rule would add is a re-check of a holding that grew
    into the cap by a price move rather than by a new entry, and the reported
    absence has to say that rather than implying sector is unconstrained.
    """
    result = plan_declared(db_session, _declared())
    absent = {control.name: control.reason for control in result.plan.absent}

    assert "SectorExposureRule" in absent
    assert "sector_limit_reached" in absent["SectorExposureRule"]
    assert "grew into the cap" in absent["SectorExposureRule"]
    assert "PositionSizeRule" not in absent, "it is installed now, so it must not be listed"


# ---------------------------------------------------------------------------
# The fit's `distinctness` component must not be inert
#
# `cycle.plan(..., correlation=None)` makes `_overlap` return 0.0 for every
# pair, pinning `distinctness` at a constant 1.0 — a flat 0.20 contribution for
# every candidate while the fit score is reported as though all five components
# were live. SIGNAL_SCOREBOARD §56 found exactly that in §55's harness, and this
# module had reproduced it.
# ---------------------------------------------------------------------------


def test_a_window_too_short_to_measure_correlation_gets_no_correlation_control(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**And says so**, rather than appearing to be protected.

    `pairwise_correlations` needs half the lookback (63 of 126 sessions) before
    it will report a pair, because a correlation from a handful of points is
    noise wearing a number. This fixture spans 45 sessions, so nothing is
    measured — and the honest result is zero pairs, the rule left out, and the
    absence on the screen. Installing it on an empty matrix would refuse every
    entry after the first, which is the fail-closed behaviour working and
    looking exactly like a broken dashboard.
    """
    result = plan_declared(db_session, _declared())

    assert result.correlation_pairs == 0
    assert "correlation_cluster" not in {control.name for control in result.plan.absent}, (
        "the absent list names the CLASS, not the rule's runtime name"
    )
    assert "CorrelationRule" in {control.name for control in result.plan.absent}


def test_a_long_enough_window_measures_pairs_and_installs_the_rule(
    db_session: Session, long_corpus: dict[str, int]
) -> None:
    """Coverage is reported because the rule fails closed.

    A plan that suddenly buys nothing has to be explicable from the numbers
    beside it rather than looking like a broken screen.
    """
    result = plan_declared(
        db_session,
        _declared(universe=("WALK", "DRIFT"), warmup_from=LONG_WARM, as_of=LONG_AS_OF),
    )

    assert result.correlation_pairs == 1, "two names make one pair"
    assert result.correlation_over_limit <= result.correlation_pairs
    assert "CorrelationRule" not in {control.name for control in result.plan.absent}


def test_overlap_reaches_the_fit_so_distinctness_is_not_a_constant() -> None:
    """The property, asserted on the ranker rather than through the corpus.

    Two candidates, one of them strongly correlated with the single holding.
    With a matrix supplied their fit scores must differ; with `None` they are
    identical, which is the inert-component bug.
    """
    import datetime as dt
    from decimal import Decimal

    from tradeit.core.enums import PositionStatus, Side, SignalDirection
    from tradeit.opportunity import OpportunityFitRanker
    from tradeit.portfolio.base import AllocationCandidate, PortfolioState, PositionState
    from tradeit.portfolio.sizing import SizingDecision
    from tradeit.strategy.base import OpportunityScore, ScoreComponent
    from tradeit.strategy.config import StrategyConfig

    day = dt.date(2024, 3, 1)

    def candidate(instrument_id: int) -> AllocationCandidate:
        return AllocationCandidate(
            score=OpportunityScore(
                instrument_id=instrument_id,
                session_date=day,
                direction=SignalDirection.LONG,
                total=0.5,
                components=(
                    ScoreComponent(name="setup", raw_value=0.5, normalised=0.5, weight=1.0),
                ),
                feature_set_digest="t",
                strategy_config_digest="t",
            ),
            sizing=SizingDecision(
                instrument_id=instrument_id,
                quantity=Decimal(10),
                entry_price=Decimal(100),
                stop_price=Decimal(90),
                risk_per_share=Decimal(10),
                risk_amount=Decimal(100),
                risk_fraction=Decimal("0.001"),
                notional=Decimal(1000),
                binding_constraint="risk",
            ),
        )

    held = PositionState(
        position_id=9,
        portfolio_id=1,
        instrument_id=9,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(10),
        average_entry_price=Decimal(100),
        stop_price=Decimal(90),
        opened_on=day,
        initial_stop_price=Decimal(90),
    )
    portfolio = PortfolioState(
        portfolio_id=1,
        as_of=dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC),
        cash=Decimal(100_000),
        equity=Decimal(100_000),
        positions=(held,),
        last_prices={9: Decimal(100)},
    )
    config = StrategyConfig(name="ma_cross_baseline")
    ranker = OpportunityFitRanker(risk=config.risk, sizing=config.sizing, sectors={})
    candidates = [candidate(1), candidate(2)]

    # Instrument 1 moves with the holding; instrument 2 does not.
    with_matrix = ranker.assess(candidates, portfolio, {(1, 9): 0.9, (2, 9): 0.0})
    scores = {entry.instrument_id: entry.fit.score for entry in with_matrix.ranked}
    assert scores[1] < scores[2], "the correlated candidate must fit worse"

    without = ranker.assess(candidates, portfolio, None)
    flat = {entry.instrument_id: entry.fit.score for entry in without.ranked}
    assert flat[1] == flat[2], (
        "with no matrix both score identically — which is the inert "
        "`distinctness` component this test exists to keep out of the plan"
    )


def test_the_measured_matrix_actually_reaches_the_fit(
    db_session: Session, long_corpus: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Computing it and not passing it is the whole bug.**

    A mutant that measured the correlations, reported the coverage, installed
    the rule — and then handed `correlation=None` to the fit — passed every
    other test in this module. The fit's `distinctness` would be a constant 1.0
    for every candidate while the coverage line said 1 pair measured, which is
    worse than not measuring at all: the screen would assert a protection that
    was not applied.

    So this watches the hand-off directly. White-box, deliberately, because the
    defect lives in the wiring rather than in either side of it.
    """
    import tradeit.decide.runner as runner

    seen: dict[str, object] = {}
    original = runner.plan_session

    def spy(**kwargs: object) -> object:
        seen.update(kwargs)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "plan_session", spy)
    result = plan_declared(
        db_session,
        _declared(universe=("WALK", "DRIFT"), warmup_from=LONG_WARM, as_of=LONG_AS_OF),
    )

    passed = seen.get("correlation")
    assert passed, "the fit was handed no correlation, so `distinctness` is inert"
    assert isinstance(passed, dict)
    assert len(passed) == result.correlation_pairs, (
        "the coverage reported on screen must be the matrix the fit actually used"
    )
