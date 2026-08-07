from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import (
    AssetClass,
    Bartimeframe,
    CorporateActionType,
    Exchange,
    FiscalPeriod,
    KnowledgeTimeSource,
    ListingStatus,
)
from tradeit.core.models import (
    CorporateAction,
    EarningsEvent,
    FundamentalFact,
    Instrument,
    OhlcvBar,
    SymbolMapping,
    UniverseMembership,
)
from tradeit.errors import DataError

UTC = dt.UTC
CLOSE = dt.datetime(2024, 3, 8, 21, 0, tzinfo=UTC)


def make_bar(**overrides) -> OhlcvBar:
    payload = {
        "instrument_id": 1,
        "timeframe": Bartimeframe.D1,
        "session_date": dt.date(2024, 3, 8),
        "event_time": CLOSE,
        "knowledge_time": CLOSE + dt.timedelta(minutes=20),
        "knowledge_source": KnowledgeTimeSource.VENDOR_INGEST,
        "open": Decimal("100"),
        "high": Decimal("105"),
        "low": Decimal("99"),
        "close": Decimal("104"),
        "volume": Decimal("1000000"),
    }
    payload.update(overrides)
    return OhlcvBar(**payload)


class TestBitemporalInvariant:
    def test_knowledge_before_event_is_rejected(self):
        with pytest.raises(DataError, match="cannot be known before"):
            make_bar(knowledge_time=CLOSE - dt.timedelta(hours=1))

    def test_period_end_as_filing_date_is_caught(self):
        """The classic fundamentals leak: stamping a filing with its period end."""
        period_end = dt.datetime(2023, 12, 31, 21, 0, tzinfo=UTC)
        with pytest.raises(DataError):
            FundamentalFact(
                instrument_id=1,
                metric="revenue",
                fiscal_period=FiscalPeriod.Q4,
                fiscal_year=2023,
                period_end=period_end.date(),
                event_time=period_end,
                knowledge_time=period_end - dt.timedelta(seconds=1),
                knowledge_source=KnowledgeTimeSource.ESTIMATED,
                value=Decimal("1000"),
            )

    def test_corporate_action_may_be_announced_before_its_ex_date(self):
        ex_date = dt.date(2024, 6, 3)
        action = CorporateAction(
            instrument_id=1,
            action_type=CorporateActionType.SPLIT,
            ex_date=ex_date,
            event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
            knowledge_time=dt.datetime(2024, 5, 1, 20, 0, tzinfo=UTC),
            knowledge_source=KnowledgeTimeSource.REPORTED,
            ratio=Decimal(2),
        )
        assert action.knowledge_time < action.event_time

    def test_earnings_date_may_be_announced_in_advance(self):
        scheduled = dt.date(2024, 5, 9)
        event = EarningsEvent(
            instrument_id=1,
            scheduled_date=scheduled,
            fiscal_period=FiscalPeriod.Q1,
            fiscal_year=2024,
            event_time=dt.datetime.combine(scheduled, dt.time(21, 0), tzinfo=UTC),
            knowledge_time=dt.datetime(2024, 4, 20, 13, 0, tzinfo=UTC),
            knowledge_source=KnowledgeTimeSource.REPORTED,
            is_confirmed=True,
        )
        assert not event.has_reported

    def test_naive_timestamps_are_rejected(self):
        with pytest.raises(DataError, match="timezone-aware"):
            make_bar(knowledge_time=dt.datetime(2024, 3, 8, 21, 20))

    def test_visibility_matches_knowledge_time(self):
        bar = make_bar()
        assert not bar.is_visible_at(CLOSE + dt.timedelta(minutes=19))
        assert bar.is_visible_at(CLOSE + dt.timedelta(minutes=21))


class TestOhlcConsistency:
    def test_high_below_close_is_rejected(self):
        with pytest.raises(DataError, match="high"):
            make_bar(high=Decimal("103"), close=Decimal("104"))

    def test_low_above_open_is_rejected(self):
        with pytest.raises(DataError, match="low"):
            make_bar(low=Decimal("101"), open=Decimal("100"))

    def test_vwap_outside_range_is_rejected(self):
        with pytest.raises(DataError, match="vwap"):
            make_bar(vwap=Decimal("110"))

    def test_negative_volume_is_rejected(self):
        with pytest.raises(ValueError):
            make_bar(volume=Decimal("-1"))

    def test_zero_price_is_rejected(self):
        with pytest.raises(ValueError):
            make_bar(low=Decimal("0"))

    def test_dollar_volume_uses_vwap_when_present(self):
        bar = make_bar(vwap=Decimal("102"), volume=Decimal("1000"))
        assert bar.dollar_volume == Decimal("102000.000000")

    def test_dollar_volume_falls_back_to_typical_price(self):
        bar = make_bar(volume=Decimal("1000"))  # (105 + 99 + 104) / 3
        assert bar.dollar_volume == Decimal("102666.667000")

    def test_bars_are_immutable(self):
        bar = make_bar()
        with pytest.raises(Exception):
            bar.close = Decimal("200")


class TestIdentityAndLifecycle:
    def test_active_instrument_cannot_have_delisted_date(self):
        with pytest.raises(DataError, match="active instrument"):
            Instrument(
                instrument_id=1,
                primary_exchange=Exchange.XNYS,
                asset_class=AssetClass.COMMON_STOCK,
                name="Test",
                delisted_date=dt.date(2020, 1, 1),
            )

    def test_delisted_instrument_requires_a_date(self):
        with pytest.raises(DataError, match="requires a delisted_date"):
            Instrument(
                instrument_id=1,
                primary_exchange=Exchange.XNYS,
                asset_class=AssetClass.COMMON_STOCK,
                name="Test",
                listing_status=ListingStatus.BANKRUPT,
            )

    def test_symbol_mapping_interval_is_half_open(self):
        mapping = SymbolMapping(
            instrument_id=1,
            ticker="ABC",
            valid_from=dt.date(2020, 1, 1),
            valid_to=dt.date(2022, 1, 1),
        )
        assert mapping.covers(dt.date(2020, 1, 1))
        assert mapping.covers(dt.date(2021, 12, 31))
        assert not mapping.covers(dt.date(2022, 1, 1))
        assert not mapping.covers(dt.date(2019, 12, 31))

    def test_open_ended_membership_cannot_have_exit_reason(self):
        with pytest.raises(DataError, match="exit_reason"):
            UniverseMembership(
                universe="us_equity",
                instrument_id=1,
                valid_from=dt.date(2020, 1, 1),
                exit_reason=ListingStatus.MERGED,
            )

    def test_empty_interval_is_rejected(self):
        with pytest.raises(DataError, match="empty"):
            SymbolMapping(
                instrument_id=1,
                ticker="ABC",
                valid_from=dt.date(2022, 1, 1),
                valid_to=dt.date(2022, 1, 1),
            )


class TestCorporateActionSanity:
    def _action(self, **overrides) -> CorporateAction:
        payload = {
            "instrument_id": 1,
            "action_type": CorporateActionType.SPLIT,
            "ex_date": dt.date(2024, 6, 3),
            "event_time": dt.datetime(2024, 6, 3, 13, 30, tzinfo=UTC),
            "knowledge_time": dt.datetime(2024, 5, 1, 20, 0, tzinfo=UTC),
            "knowledge_source": KnowledgeTimeSource.REPORTED,
            "ratio": Decimal(2),
        }
        payload.update(overrides)
        return CorporateAction(**payload)

    def test_split_with_unit_ratio_is_rejected(self):
        with pytest.raises(DataError, match="not a split"):
            self._action(ratio=Decimal(1))

    def test_dividend_needs_an_amount(self):
        with pytest.raises(DataError, match="non-zero cash_amount"):
            self._action(
                action_type=CorporateActionType.CASH_DIVIDEND,
                ratio=Decimal(1),
                cash_amount=Decimal(0),
            )

    def test_symbol_change_needs_new_ticker(self):
        with pytest.raises(DataError, match="new_ticker"):
            self._action(action_type=CorporateActionType.SYMBOL_CHANGE, ratio=Decimal(1))
