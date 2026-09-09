"""Tests for the event-driven engine.

``TestNoLookAhead`` is the file's reason for existing. Every other property
here can be wrong and produce a merely inaccurate backtest; a look-ahead leak
produces a *profitable* one, which is the failure that gets acted on.
"""

from __future__ import annotations

import dataclasses
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
    splits_by_session: dict[dt.date, dict[int, Decimal]] = field(default_factory=dict)

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]:
        return sorted(d for d in self.bars_by_session if start <= d <= end)

    def bars(self, session_date: dt.date) -> Mapping[int, OhlcvBar]:
        return self.bars_by_session.get(session_date, {})

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]:
        return self.candidates_by_session.get(session_date, [])

    def splits_on(self, session_date: dt.date) -> Mapping[int, Decimal]:
        return self.splits_by_session.get(session_date, {})


def _engine(
    data: _Script,
    *,
    exits: ExitConfig | None = None,
    delisting_after: int = 20,
    recovery: str = "1",
) -> EventDrivenEngine:
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
        delisting_after_sessions=delisting_after,
        delisting_recovery=Decimal(recovery),
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
        engine = dataclasses.replace(engine, manifest=manifest)
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


class TestCorporateActions:
    """A split changes the share count, not the value of the holding."""

    def _through_a_split(self, ratio: str | None) -> tuple[list, dict]:
        days = _days(6)
        # Price halves on day 3: that is a 2-for-1, not a 50% loss.
        closes = ["100", "100", "100", "50", "50", "50"]
        bars = {
            day: {7: _bar(7, day, open_=close, close=close)}
            for day, close in zip(days, closes, strict=True)
        }
        script = _Script(
            bars,
            {days[0]: [_candidate(7, "100", "80")]},
            {days[3]: {7: Decimal(ratio)}} if ratio else {},
        )
        result = _engine(script).run(_spec(days=7))
        return list(result.equity_curve), {t.exit_reason: t for t in result.trades}

    def test_a_two_for_one_leaves_equity_unchanged(self) -> None:
        curve, _ = self._through_a_split("2")
        before = dict(curve)[_days(6)[2]]
        after = dict(curve)[_days(6)[3]]
        assert after == pytest.approx(before, rel=Decimal("0.001"))

    def test_without_the_split_the_holding_appears_to_halve(self) -> None:
        """The failure this exists to prevent, shown at the level it bites.

        The account only drops about 1.25%, because the position is 2.5% of
        equity -- which is exactly why this bug survives casual inspection of
        an equity curve. The trade record is where it is unmistakable: a
        holding that cost the owner nothing books a 50% loss and a stop-out.
        """
        _, trades = self._through_a_split(None)
        assert str(ExitReason.STOP_LOSS) in trades
        stopped = trades[str(ExitReason.STOP_LOSS)]
        assert stopped.return_pct == pytest.approx(-0.5, abs=0.01)

    def test_with_the_split_the_account_does_not_move(self) -> None:
        curve, _ = self._through_a_split("2")
        before = dict(curve)[_days(6)[2]]
        after = dict(curve)[_days(6)[3]]
        assert abs(after - before) / before < Decimal("0.0001")

    def test_the_two_readings_differ_by_the_whole_position(self) -> None:
        """Same prices, same strategy; the only difference is knowing about the split."""
        with_split, _ = self._through_a_split("2")
        without, _ = self._through_a_split(None)
        assert dict(with_split)[_days(6)[5]] > dict(without)[_days(6)[5]]

    def test_the_stop_survives_the_split_intact(self) -> None:
        """A stop at 80 against a 100 entry is a stop at 40 against a 50 one."""
        _, trades = self._through_a_split("2")
        # The halved price is not a stop breach, so nothing exits early.
        assert set(trades) == {str(ExitReason.BACKTEST_END)}

    def test_a_split_does_not_manufacture_an_r_multiple(self) -> None:
        _, trades = self._through_a_split("2")
        trade = trades[str(ExitReason.BACKTEST_END)]
        assert trade.r_multiple is not None
        assert abs(trade.r_multiple) < 0.1  # the holding is flat, and says so


class TestDelisting:
    """A universe containing companies that failed holds positions that stop."""

    def _run(self, *, recovery: str, after: int = 3, silence: int = 6) -> object:
        days = _days(4 + silence)
        bars: dict[dt.date, dict[int, OhlcvBar]] = {
            day: {7: _bar(7, day, open_="100", close="100")} for day in days[:4]
        }
        # The company stops printing. Something else keeps the calendar moving.
        for day in days[4:]:
            bars[day] = {9: _bar(9, day, open_="10", close="10")}
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        return _engine(data, delisting_after=after, recovery=recovery).run(
            _spec(days=len(days) + 1)
        )

    def test_a_holding_that_stops_printing_is_closed(self) -> None:
        result = self._run(recovery="1")
        reasons = [t.exit_reason for t in result.trades]  # type: ignore[attr-defined]
        assert str(ExitReason.DELISTED_EXIT) in reasons

    def test_it_is_not_carried_to_the_end_of_the_run(self) -> None:
        """Occupying a slot and a share of heat no real holder still had."""
        result = self._run(recovery="1")
        reasons = [t.exit_reason for t in result.trades]  # type: ignore[attr-defined]
        assert str(ExitReason.BACKTEST_END) not in reasons

    def test_silence_shorter_than_the_threshold_does_not_close_it(self) -> None:
        result = self._run(recovery="1", after=99)
        reasons = [t.exit_reason for t in result.trades]  # type: ignore[attr-defined]
        assert str(ExitReason.DELISTED_EXIT) not in reasons
        assert str(ExitReason.BACKTEST_END) in reasons

    def test_the_recovery_assumption_decides_the_loss(self) -> None:
        """The most consequential number in a survivorship-honest backtest."""
        sold = self._run(recovery="1")
        wiped = self._run(recovery="0")
        sold_trade = next(
            t
            for t in sold.trades
            if t.exit_reason == str(ExitReason.DELISTED_EXIT)  # type: ignore[attr-defined]
        )
        wiped_trade = next(
            t
            for t in wiped.trades
            if t.exit_reason == str(ExitReason.DELISTED_EXIT)  # type: ignore[attr-defined]
        )
        assert sold_trade.exit_price == Decimal(100)
        assert wiped_trade.exit_price == Decimal(0)
        assert wiped_trade.net_pnl < sold_trade.net_pnl

    def test_a_total_loss_shows_up_in_the_equity_curve(self) -> None:
        wiped = self._run(recovery="0")
        curve = dict(wiped.equity_curve)  # type: ignore[attr-defined]
        days = sorted(curve)
        assert curve[days[-1]] < Decimal(100000)

    def test_a_quiet_name_does_not_stall_the_whole_session(self) -> None:
        """A stale mark is the last thing known, not a reason to stop deciding."""
        result = self._run(recovery="1", after=99)
        # Trading continued: the position was still managed to the end.
        assert result.equity_curve  # type: ignore[attr-defined]
        assert len(result.equity_curve) == 10  # type: ignore[attr-defined]


class TestGuardsRealDataForced:
    """Both of these were found by running on the corpus, not by design."""

    def test_an_entry_that_gaps_through_its_own_stop_is_abandoned(self) -> None:
        """The premise of the trade no longer holds, so it is not opened.

        Decide at 100 with a stop at 92; the market opens at 85. Opening and
        instantly stopping out would book a loss on a trade nobody would take,
        and the position would have a stop above its entry -- undefined risk
        and no meaningful R.
        """
        days = _days(4)
        bars = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {7: _bar(7, days[1], open_="85", low="84", close="86")},
            days[2]: {7: _bar(7, days[2], open_="86", close="86")},
            days[3]: {7: _bar(7, days[3], open_="86", close="86")},
        }
        data = _Script(bars, {days[0]: [_candidate(7, "100", "92")]})
        result = _engine(data).run(_spec())
        assert result.trades == ()
        assert any("gapped to or through" in w for w in result.warnings)

    def test_an_exit_is_not_filled_into_a_market_that_is_not_printing(self) -> None:
        """Selling at a price nobody quoted is the most flattering fill there is."""
        days = _days(8)
        bars: dict[dt.date, dict[int, OhlcvBar]] = {
            days[0]: {7: _bar(7, days[0], open_="100", close="100")},
            days[1]: {7: _bar(7, days[1], open_="100", close="100")},
            # A close below the stop, then silence: the stop is breached on a
            # mark that no longer has a market behind it.
            days[2]: {7: _bar(7, days[2], open_="100", low="90", close="90")},
        }
        for day in days[3:]:
            bars[day] = {9: _bar(9, day, open_="10", close="10")}
        data = _Script(bars, {days[0]: [_candidate(7, "100", "95")]})
        result = _engine(data, delisting_after=3, recovery="0.5").run(_spec(days=9))
        reasons = [t.exit_reason for t in result.trades]
        # It left as a delisting at the stated recovery, not as a clean stop
        # fill at a price that was never quoted.
        assert str(ExitReason.DELISTED_EXIT) in reasons
        delisted = next(t for t in result.trades if t.exit_reason == str(ExitReason.DELISTED_EXIT))
        assert delisted.exit_price == Decimal(45)  # 90 x 0.5
