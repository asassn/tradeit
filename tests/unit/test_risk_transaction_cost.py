"""Tests for the transaction-cost guard, on at 1% by default since 2026-09-18.

The case the guard exists for is reproduced from the evidence, not invented.
In the stop-ladder registration's sample 2, security 9106 closed at $0.0001 on
2010-07-06; the sizer put 0.5% of $99,793.09 at risk against an 8% stop, which
is 62,370,683 shares, bound by ``risk_per_trade``. They filled on 2010-07-07 at
$0.000100065 for $311,853.42 of commission on a $6,241 position, and the
account went to minus $520,343. (The 20,582,325 shares quoted in the
registration's Amendment 2 are the 33% partial-profit leg of that trade record,
not the entry.) See docs/PROPOSAL_TRANSACTION_COST_GUARD_2026-09-18.md.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import build_engine
from tradeit.core.enums import (
    ArtifactKind,
    Bartimeframe,
    KnowledgeTimeSource,
    OrderSide,
    RiskDecision,
    RiskLimitType,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.simulation import ParticipationCostModel
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.portfolio.sizing import BindingConstraint, RiskBasedSizer
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.risk.base import RiskRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
    TransactionCostRule,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection
from tradeit.strategy.config import CostConfig, RiskConfig, SizingConfig, StrategyConfig

UTC = dt.UTC
AS_OF = dt.datetime(2010, 7, 6, 21, tzinfo=UTC)

#: The evidence: security 9106's entry in stop-ladder primary sample 2, as the
#: sizer saw it on the decision session and as it filled on the next.
PENNY_PRICE = Decimal("0.000100")
PENNY_STOP = Decimal("0.000092")
PENNY_EQUITY = Decimal("99793.092948860")
PENNY_QUANTITY = Decimal(62_370_683)
PENNY_FILL = Decimal("0.000100065")
#: The 20-session average dollar volume the sizer was handed. Sub-penny prints
#: trade in billions of shares, so the liquidity cap was nowhere near binding.
PENNY_ADV = Decimal("64840093584.29134357845573516")


def _proposal(quantity: Decimal, entry: Decimal) -> SizingDecision:
    stop_price = entry * Decimal("0.92")
    per_share = entry - stop_price
    return SizingDecision(
        instrument_id=9106,
        quantity=quantity,
        entry_price=entry,
        stop_price=stop_price,
        risk_per_share=per_share,
        risk_amount=quantity * per_share,
        risk_fraction=Decimal(0),
        notional=quantity * entry,
        binding_constraint=BindingConstraint.RISK,
    )


def _portfolio(equity: str | Decimal = "100000") -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(equity),
        equity=Decimal(equity),
        positions=(),
        last_prices={},
    )


def _rule(limit: float = 0.01, costs: CostConfig | None = None) -> TransactionCostRule:
    return TransactionCostRule(
        max_round_trip_cost_pct=limit,
        costs=ParticipationCostModel(config=costs or CostConfig()),
    )


def _candidate(instrument_id: int, entry: Decimal, adv: Decimal) -> EntryCandidate:
    return EntryCandidate(
        score=OpportunityScore(
            instrument_id=instrument_id,
            session_date=AS_OF.date(),
            direction=SignalDirection.LONG,
            total=1.0,
            components=(ScoreComponent(name="t", raw_value=1.0, normalised=1.0, weight=1.0),),
            feature_set_digest="fs",
            strategy_config_digest="sc",
        ),
        entry_price=entry,
        stop_price=entry * Decimal("0.92"),
        sector=None,
        average_dollar_volume=adv,
    )


class TestTransactionCostRule:
    def test_satisfies_the_risk_rule_protocol(self) -> None:
        assert isinstance(_rule(), RiskRule)
        assert _rule().limit_type is RiskLimitType.TRANSACTION_COST

    def test_refuses_the_ten_thousandth_of_a_dollar_entry(self) -> None:
        """The evidence case: commission fifty times the position."""
        assessment = _rule().evaluate(_proposal(PENNY_QUANTITY, PENNY_PRICE), _portfolio())
        assert assessment.decision is RiskDecision.REJECT
        # $311,853.42 a side, twice, on $6,237.07: about one hundred times notional.
        assert assessment.measured is not None and assessment.measured > 99
        assert "6237.07 notional" in assessment.reason
        assert "1.00% limit" in assessment.reason

    def test_refuses_it_at_any_threshold_the_config_admits(self) -> None:
        # le=1.0 is the loosest RiskConfig allows: cost equal to the position.
        assessment = _rule(1.0).evaluate(_proposal(PENNY_QUANTITY, PENNY_PRICE), _portfolio())
        assert assessment.decision is RiskDecision.REJECT

    def test_allows_an_ordinary_entry(self) -> None:
        # $6,250 of a $50 stock: 125 shares, the $1 minimum commission each way
        # and 6.5 bps of spread and slippage each way -- 0.162% round trip.
        assessment = _rule().evaluate(_proposal(Decimal(125), Decimal(50)), _portfolio())
        assert assessment.decision is RiskDecision.ALLOW
        assert assessment.measured == pytest.approx(2 / 6250 + 0.0013)

    def test_the_estimate_is_the_cost_models_own(self) -> None:
        """Not a second formula: a buy and a sell through ``estimate_at``."""
        costs = ParticipationCostModel(config=CostConfig())
        proposal = _proposal(Decimal(4000), Decimal("0.80"))
        expected = sum(
            costs.estimate_at(side=side, quantity=Decimal(4000), reference=Decimal("0.80")).total
            for side in (OrderSide.BUY, OrderSide.SELL)
        )
        assert _rule().round_trip_cost(proposal) == expected

    def test_follows_the_configured_costs(self) -> None:
        # At 5x spread and slippage the same $2 entry costs more, and a limit
        # between the two estimates separates them.
        proposal = _proposal(Decimal(3000), Decimal(2))
        base = _rule(0.008).evaluate(proposal, _portfolio())
        stress = _rule(0.008, CostConfig(spread_bps=15.0, slippage_bps=25.0)).evaluate(
            proposal, _portfolio()
        )
        assert base.decision is RiskDecision.ALLOW
        assert stress.decision is RiskDecision.REJECT

    def test_the_limit_itself_is_allowed(self) -> None:
        # No commission; 5 bps of half-spread plus 10 of slippage, twice: 0.30%.
        no_commission = CostConfig(
            commission_per_share=0.0, commission_minimum=0.0, spread_bps=10.0, slippage_bps=10.0
        )
        rule = _rule(0.003, no_commission)
        assessment = rule.evaluate(_proposal(Decimal(100), Decimal(10)), _portfolio())
        assert assessment.measured == pytest.approx(0.003)
        assert assessment.decision is RiskDecision.ALLOW

    def test_never_reduces(self) -> None:
        """A smaller order is never cheaper as a fraction, so no reduction helps."""
        for quantity in (Decimal(1), Decimal(100), Decimal(10_000), PENNY_QUANTITY):
            for price in (PENNY_PRICE, Decimal("0.3"), Decimal(40)):
                decision = _rule().evaluate(_proposal(quantity, price), _portfolio()).decision
                assert decision is not RiskDecision.ALLOW_REDUCED

    def test_the_commission_minimum_makes_a_small_order_dearer(self) -> None:
        # Two shares of a $100 stock: $2 of minimum commission on $200.
        assessment = _rule().evaluate(_proposal(Decimal(2), Decimal(100)), _portfolio())
        assert assessment.decision is RiskDecision.REJECT

    def test_a_zero_quantity_is_a_rejection_not_a_division(self) -> None:
        assessment = _rule().evaluate(_proposal(Decimal(0), Decimal(10)), _portfolio())
        assert assessment.decision is RiskDecision.REJECT


class TestTheEvidenceCaseThroughTheSizer:
    """What the platform does today, and what the guard changes."""

    def _sized(self) -> SizingDecision:
        config = StrategyConfig(name="defaults")
        sizer = RiskBasedSizer(
            sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02")
        )
        return sizer.size(
            _candidate(9106, PENNY_PRICE, PENNY_ADV).score,
            PENNY_PRICE,
            PENNY_STOP,
            _portfolio(PENNY_EQUITY),
            PENNY_ADV,
        )

    def _rules(self, risk: RiskConfig, sizing: SizingConfig) -> tuple[RiskRule, ...]:
        return (
            PortfolioHeatRule(config=risk),
            MaxPositionsRule(config=risk),
            PositionSizeRule(max_position_pct_of_equity=sizing.max_position_pct_of_equity),
            GrossExposureRule(config=risk),
        )

    def test_the_sizer_accepts_it_today(self) -> None:
        """The gap: ``min_position_notional`` limits notional, not cost."""
        sized = self._sized()
        assert not sized.rejected
        assert sized.quantity == PENNY_QUANTITY
        assert sized.binding_constraint == BindingConstraint.RISK
        assert sized.notional > Decimal(SizingConfig().min_position_notional)

    def test_the_default_rules_allow_it_today(self) -> None:
        risk, sizing = RiskConfig(), SizingConfig()
        verdict = MostRestrictiveEngine(rule_set=self._rules(risk, sizing), sizing=sizing).evaluate(
            self._sized(), _portfolio()
        )
        assert verdict.decision is RiskDecision.ALLOW

    def test_the_guard_refuses_it(self) -> None:
        risk, sizing = RiskConfig(), SizingConfig()
        rules = (*self._rules(risk, sizing), _rule(0.01))
        verdict = MostRestrictiveEngine(rule_set=rules, sizing=sizing).evaluate(
            self._sized(), _portfolio(PENNY_EQUITY)
        )
        assert verdict.decision is RiskDecision.REJECT
        assert verdict.binding_rule == "transaction_cost"
        assert verdict.final_quantity == 0


@dataclass
class _Script:
    bars_by_session: dict[dt.date, dict[int, OhlcvBar]]
    candidates_by_session: dict[dt.date, list[EntryCandidate]] = field(default_factory=dict)

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]:
        return sorted(d for d in self.bars_by_session if start <= d <= end)

    def bars(self, session_date: dt.date) -> Mapping[int, OhlcvBar]:
        return self.bars_by_session.get(session_date, {})

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]:
        return self.candidates_by_session.get(session_date, [])

    def splits_on(self, session_date: dt.date) -> Mapping[int, Decimal]:
        return {}


def _bar(session: dt.date, price: Decimal) -> OhlcvBar:
    return OhlcvBar(
        instrument_id=9106,
        timeframe=Bartimeframe.D1,
        session_date=session,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(10_000_000_000),
        event_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _run(config: StrategyConfig) -> tuple[Decimal, int]:
    """Lowest equity and trade count of a four-session run offered the penny.

    Decided at the $0.0001 close, filled next session at $0.000100065, as 9106 was.
    """
    days = [dt.date(2010, 7, 6) + dt.timedelta(days=i) for i in range(4)]
    data = _Script(
        {d: {9106: _bar(d, PENNY_PRICE if d == days[0] else PENNY_FILL)} for d in days},
        {days[0]: [_candidate(9106, PENNY_PRICE, PENNY_ADV)]},
    )
    now = dt.datetime(2026, 9, 18, tzinfo=UTC)
    manifest = RunManifest(
        run_id="costguard-test",
        as_of=now,
        strategy_config=config.version(now),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "script", {"bars": 4}, now),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=now,
    )
    engine = build_engine(
        config,
        data,
        manifest,
        participation=Decimal("0.02"),
        risk_free_rate=0.0,
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
    )
    result = engine.run(
        BacktestSpec(
            name="costguard",
            start=days[0],
            end=days[-1],
            universe="test",
            initial_capital=Decimal(100_000),
            strategy_config_digest=config.digest,
            cost_model="participation",
            fill_model="bar",
        )
    )
    return min(e for _, e in result.equity_curve), len(result.trades)


class TestTheAssembledEngine:
    def test_switched_off_the_platform_still_sinks_the_account(self) -> None:
        """What the default used to do, so the guard is shown to be the difference."""
        low, trades = _run(StrategyConfig(name="off", risk={"max_round_trip_cost_pct": None}))
        assert trades == 1
        assert low < 0

    def test_by_default_the_entry_is_refused_and_the_account_survives(self) -> None:
        low, trades = _run(StrategyConfig(name="defaults"))
        assert trades == 0
        assert low == Decimal(100_000)

    def test_the_rule_is_assembled_only_when_configured(self) -> None:
        def names(config: StrategyConfig) -> list[str]:
            engine = build_engine(
                config,
                _Script({}),
                RunManifest(
                    run_id="x",
                    as_of=AS_OF,
                    strategy_config=config.version(AS_OF),
                    data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "s", {}, AS_OF),
                    feature_set=None,
                    model=None,
                    code_version="test",
                    created_at=AS_OF,
                ),
                participation=Decimal("0.02"),
                risk_free_rate=0.0,
                delisting_after_sessions=10,
                delisting_recovery=Decimal(1),
            )
            return [rule.name for rule in engine.cycle.engine.rules]

        assert "transaction_cost" in names(StrategyConfig(name="defaults"))
        assert "transaction_cost" not in names(
            StrategyConfig(name="off", risk={"max_round_trip_cost_pct": None})
        )


class TestTheFlag:
    def test_is_on_at_one_percent_by_default(self) -> None:
        assert RiskConfig().max_round_trip_cost_pct == 0.01

    def test_switched_off_is_the_strategy_from_before_the_guard(self) -> None:
        """Off hashes as the field never existed, so a pre-guard digest still
        names the pre-guard behaviour."""
        payload = StrategyConfig(name="s", risk={"max_round_trip_cost_pct": None}).to_payload()
        assert "max_round_trip_cost_pct" not in payload["risk"]

    def test_on_is_a_different_strategy(self) -> None:
        on = StrategyConfig(name="s")
        off = StrategyConfig(name="s", risk={"max_round_trip_cost_pct": None})
        assert on.digest != off.digest
        assert on.differs_from(off) == ["risk.max_round_trip_cost_pct"]

    @pytest.mark.parametrize("bad", [0.0, -0.01, 1.5])
    def test_a_limit_outside_zero_to_one_is_refused(self, bad: float) -> None:
        with pytest.raises(ValueError):
            RiskConfig(max_round_trip_cost_pct=bad)
