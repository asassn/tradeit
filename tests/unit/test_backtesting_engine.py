"""Tests for the event-driven engine.

``TestNoLookAhead`` is the file's reason for existing. Every other property
here can be wrong and produce a merely inaccurate backtest; a look-ahead leak
produces a *profitable* one, which is the failure that gets acted on.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.core.enums import (
    ArtifactKind,
    Bartimeframe,
    ExitReason,
    KnowledgeTimeSource,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import GrossExposureRule, MaxPositionsRule, PortfolioHeatRule
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection
from tradeit.strategy.config import CostConfig, ExitConfig, RiskConfig, SizingConfig

UTC = dt.UTC
START = dt.date(2024, 1, 2)
KNOWN = dt.datetime(2024, 1, 2, 21, tzinfo=UTC)


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="bt-test",
        as_of=dt.datetime(2024, 12, 31, tzinfo=UTC),
        strategy_config=ArtifactVersion.of(
            ArtifactKind.STRATEGY_CONFIG, "baseline", {"risk": 0.005}
        ),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "eod", {"bars": 1}),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=dt.datetime(2024, 12, 31, tzinfo=UTC),
    )


def _bar(
    instrument_id: int,
    session: dt.date,
    *,
    open_: str,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
    volume: str = "10000000",
) -> OhlcvBar:
    o = Decimal(open_)
    c = Decimal(close) if close else o
    h = Decimal(high) if high else max(o, c)
    ll = Decimal(low) if low else min(o, c)
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=session,
        open=o,
        high=h,
        low=ll,
        close=c,
        volume=Decimal(volume),
        event_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_time=dt.datetime.combine(session, dt.time(21), tzinfo=UTC),
        knowledge_source=KnowledgeTimeSource.SYNTHETIC,
    )


def _candidate(instrument_id: int, entry: str, stop: str) -> EntryCandidate:
    score = OpportunityScore(
        instrument_id=instrument_id,
        session_date=START,
        direction=SignalDirection.LONG,
        total=1.0,
        components=(ScoreComponent(name="t", raw_value=1.0, normalised=1.0, weight=1.0),),
        feature_set_digest="fs",
        strategy_config_digest="sc",
    )
    return EntryCandidate(
        score=score,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        sector="Energy",
    )


@dataclass
class _Script:
    """A scripted SessionData: exactly the bars and candidates a test needs."""

    bars_by_session: dict[dt.date, dict[int, OhlcvBar]]
    candidates_by_session: dict[dt.date, list[EntryCandidate]] = field(default_factory=dict)

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]:
        return sorted(d for d in self.bars_by_session if start <= d <= end)

    def bars(self, session_date: dt.date) -> Mapping[int, OhlcvBar]:
        return self.bars_by_session.get(session_date, {})

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]:
        return self.candidates_by_session.get(session_date, [])


def _engine(data: _Script, *, exits: ExitConfig | None = None) -> EventDrivenEngine:
    sizing = SizingConfig()
    risk = RiskConfig()
    return EventDrivenEngine(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(sizing=sizing, risk=risk, max_participation=Decimal("0.05")),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=risk),
                    MaxPositionsRule(config=risk),
                    GrossExposureRule(config=risk),
                ),
                sizing=sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=exits or ExitConfig()),
        ),
        costs=ParticipationCostModel(config=CostConfig()),
        fills=BarFillModel(max_participation=Decimal("0.10")),
        data=data,
        analyzer=StandardPerformanceAnalyzer(annualisation_factor=252, risk_free_rate=0.0),
        manifest=_manifest(),
    )


def _spec(days: int = 5) -> BacktestSpec:
    return BacktestSpec(
        name="t",
        start=START,
        end=START + dt.timedelta(days=days),
        universe="test",
        initial_capital=Decimal(100000),
        strategy_config_digest="sc",
        cost_model="participation",
        fill_model="bar",
    )


def _days(n: int) -> list[dt.date]:
    return [START + dt.timedelta(days=i) for i in range(n)]


class TestNoLookAhead:
    def test_a_decision_on_one_session_fills_on_the_next(self) -> None:
        """The rule the whole module is built around."""
        days = _days(4)
        # Decision price 100 on day 0; day 1 opens at a wildly different 150.
        bars = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {7: _bar(7, days[1], open_="150", close="150")},
            days[2]: {7: _bar(7, days[2], open_="150", close="150")},
            days[3]: {7: _bar(7, days[3], open_="150", close="150")},
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.trades
        # Filled at day 1's open, not day 0's close.
        assert result.trades[0].entry_price >= Decimal(150)
        assert result.trades[0].entry_date == days[1]

    def test_a_candidate_on_the_final_session_never_trades(self) -> None:
        """There is no next session to execute against."""
        days = _days(3)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        data = _Script(bars, {days[-1]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.trades == ()

    def test_the_engine_refuses_a_window_too_short_to_execute_in(self) -> None:
        days = _days(1)
        data = _Script({days[0]: {7: _bar(7, days[0], open_="100")}})
        with pytest.raises(ValueError, match="at least two"):
            _engine(data).run(_spec())


class TestTheLoop:
    def test_an_empty_run_still_produces_a_curve(self) -> None:
        days = _days(4)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        result = _engine(_Script(bars)).run(_spec())
        assert len(result.equity_curve) == 4
        assert all(equity == Decimal(100000) for _, equity in result.equity_curve)
        assert result.metrics is not None
        assert result.metrics.trade_count == 0

    def test_equity_follows_the_position_not_just_the_cash(self) -> None:
        days = _days(4)
        bars = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {7: _bar(7, days[1], open_="100", close="100")},
            days[2]: {7: _bar(7, days[2], open_="100", close="120")},
            days[3]: {7: _bar(7, days[3], open_="120", close="120")},
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        marks = dict(result.equity_curve)
        assert marks[days[2]] > marks[days[1]]

    def test_everything_open_at_the_end_is_closed_and_labelled(self) -> None:
        """A last-day winner was not earned, and the exit reason says so."""
        days = _days(4)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.trades
        assert result.trades[-1].exit_reason == str(ExitReason.BACKTEST_END)

    def test_a_halted_session_carries_the_order_rather_than_cancelling_it(self) -> None:
        days = _days(5)
        bars = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {},  # no print
            days[2]: {7: _bar(7, days[2], open_="100", close="100")},
            days[3]: {7: _bar(7, days[3], open_="100", close="100")},
            days[4]: {7: _bar(7, days[4], open_="100", close="100")},
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec(days=6))
        assert result.trades
        assert result.trades[0].entry_date == days[2]


class TestCostsAreCharged:
    def test_a_round_trip_at_a_flat_price_loses_money(self) -> None:
        """If it broke even, no cost was applied anywhere."""
        days = _days(4)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.trades
        trade = result.trades[0]
        assert trade.costs > 0
        assert trade.net_pnl < trade.gross_pnl

    def test_the_final_equity_reflects_the_costs_paid(self) -> None:
        days = _days(4)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.equity_curve[-1][1] < Decimal(100000)


class TestExits:
    def test_a_broken_stop_exits_the_position(self) -> None:
        days = _days(5)
        bars = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {7: _bar(7, days[1], open_="100", close="100")},
            days[2]: {7: _bar(7, days[2], open_="100", low="90", close="90")},
            days[3]: {7: _bar(7, days[3], open_="90", close="90")},
            days[4]: {7: _bar(7, days[4], open_="90", close="90")},
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec(days=6))
        reasons = {trade.exit_reason for trade in result.trades}
        assert str(ExitReason.STOP_LOSS) in reasons

    def test_each_exit_is_its_own_trade_row(self) -> None:
        """A scaled-out position leaves for several reasons; averaging loses them."""
        days = _days(6)
        # Runs to 2R, taking a partial, then to the end.
        closes = ["100", "100", "115", "120", "120", "120"]
        bars = {
            day: {7: _bar(7, day, open_=close, close=close)}
            for day, close in zip(days, closes, strict=True)
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec(days=7))
        reasons = [trade.exit_reason for trade in result.trades]
        assert str(ExitReason.PARTIAL_PROFIT) in reasons
        assert str(ExitReason.BACKTEST_END) in reasons
        assert len(result.trades) >= 2


class TestResultIntegrity:
    def test_the_manifest_is_the_one_supplied(self) -> None:
        """Not one the engine minted, which would always validate and never replay."""
        days = _days(3)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        manifest = _manifest()
        engine = _engine(_Script(bars))
        engine = type(engine)(
            cycle=engine.cycle,
            costs=engine.costs,
            fills=engine.fills,
            data=engine.data,
            analyzer=engine.analyzer,
            manifest=manifest,
        )
        assert engine.run(_spec()).manifest is manifest

    def test_a_thin_result_is_not_trustworthy(self) -> None:
        days = _days(4)
        bars = {d: {7: _bar(7, d, open_="100", close="100")} for d in days}
        result = _engine(_Script(bars)).run(_spec())
        assert not result.trustworthy  # fewer than 30 trades

    def test_r_multiples_are_measured_against_the_entry_stop(self) -> None:
        days = _days(4)
        closes = ["100", "100", "110", "110"]
        bars = {
            day: {7: _bar(7, day, open_=close, close=close)}
            for day, close in zip(days, closes, strict=True)
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data).run(_spec())
        assert result.trades
        assert result.trades[-1].r_multiple is not None
        assert result.trades[-1].r_multiple > 0
