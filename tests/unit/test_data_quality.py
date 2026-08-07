"""Data-quality diagnostics, the fill policy, and the real provider adapters.

The tests that matter most here are the ones asserting a check does *not* fire.
A diagnostic that flags everything is indistinguishable from no diagnostic at
all, and on real market data the tempting checks -- "reject anything that moved
more than 50%" -- delete precisely the sessions a breakout system exists to
find.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.calendar import get_calendar
from tradeit.core.enums import (
    Bartimeframe,
    CorporateActionType,
    DataQualityFlag,
    KnowledgeTimeSource,
)
from tradeit.core.models import CorporateAction, OhlcvBar, SymbolMapping
from tradeit.data import quality
from tradeit.data.provider import MarketDataProvider
from tradeit.data.providers.http import (
    HttpTransport,
    ProviderUnreachableError,
    ResponseCache,
    _redact,
)
from tradeit.data.providers.stooq import StooqProvider
from tradeit.data.providers.tiingo import TiingoProvider, build_symbol_map
from tradeit.data.validation_universe import UniverseCategory, default_universe
from tradeit.errors import DataError, ProviderError

CALENDAR = get_calendar("XNYS")
UTC = dt.UTC


def bar(
    session: str | dt.date,
    close: float,
    *,
    instrument_id: int = 1,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000,
) -> OhlcvBar:
    day = dt.date.fromisoformat(session) if isinstance(session, str) else session
    close_time = CALENDAR.close_instant(day)
    o = close if open_ is None else open_
    return OhlcvBar(
        instrument_id=instrument_id,
        timeframe=Bartimeframe.D1,
        session_date=day,
        event_time=close_time,
        knowledge_time=close_time + dt.timedelta(minutes=20),
        knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        open=Decimal(str(o)),
        high=Decimal(str(max(o, close) if high is None else high)),
        low=Decimal(str(min(o, close) if low is None else low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


def broken_bar(session: str, **fields) -> OhlcvBar:
    """A bar that ``OhlcvBar`` would refuse to construct.

    ``model_construct`` bypasses validation deliberately. The domain model is
    the *first* gate and rejects impossible rows outright -- a fact these tests
    incidentally prove, since the naive version of them could not even build the
    input. The quality checks are the second gate, and they exist because rows
    also arrive from places the model never sees: a database read of data
    ingested by an older version, a raw vendor payload being triaged before
    construction, a batch loaded by a migration. A check that is unreachable
    today must still be correct the day it becomes reachable.
    """
    day = dt.date.fromisoformat(session)
    close_time = (
        CALENDAR.close_instant(day)
        if CALENDAR.is_session(day)
        else dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC)
    )
    defaults = {
        "instrument_id": 1,
        "timeframe": Bartimeframe.D1,
        "session_date": day,
        "event_time": close_time,
        "knowledge_time": close_time + dt.timedelta(minutes=20),
        "knowledge_source": KnowledgeTimeSource.VENDOR_INGEST,
        "open": Decimal("100"),
        "high": Decimal("100"),
        "low": Decimal("100"),
        "close": Decimal("100"),
        "volume": Decimal("1000"),
        "trade_count": None,
        "vwap": None,
        "quality": DataQualityFlag.OK,
    }
    return OhlcvBar.model_construct(**{**defaults, **fields})


def series(prices: list[float], start: str = "2024-01-02", **kwargs) -> list[OhlcvBar]:
    sessions = CALENDAR.sessions_between(
        dt.date.fromisoformat(start), dt.date.fromisoformat(start) + dt.timedelta(days=400)
    )
    return [bar(s, p, **kwargs) for s, p in zip(sessions, prices, strict=False)]


class TestStructuralChecks:
    def test_an_exact_replay_is_not_a_problem(self):
        """Re-running an ingest must be free. It is how a backfill is retried."""
        one = bar("2024-01-02", 100.0)
        findings = quality.check_duplicates([one, one])
        assert [f.severity for f in findings] == [quality.Severity.INFO]

    def test_conflicting_bars_for_one_session_are_rejected(self):
        """Whichever wins would be arbitrary, and the choice changes results."""
        findings = quality.check_duplicates([bar("2024-01-02", 100.0), bar("2024-01-02", 101.0)])
        assert findings[0].severity is quality.Severity.REJECT

    def test_the_domain_model_refuses_an_impossible_bar_outright(self):
        """The first gate. Nothing downstream ever sees this row."""
        with pytest.raises(DataError):
            bar("2024-01-02", 100.0, open_=99.0, high=101.0, low=99.5)

    def test_a_close_outside_its_own_range_is_rejected(self):
        broken = broken_bar(
            "2024-01-02", open=Decimal("99"), high=Decimal("101"), low=Decimal("99.5")
        )
        findings = quality.check_ohlc_consistency([broken])
        assert findings[0].severity is quality.Severity.REJECT
        assert findings[0].flag is DataQualityFlag.INCONSISTENT_OHLC

    def test_high_below_low_is_rejected(self):
        broken = broken_bar("2024-01-02", high=Decimal("99"), low=Decimal("101"))
        assert quality.check_ohlc_consistency([broken])

    def test_a_normal_bar_passes_every_structural_check(self):
        """The control. If this fires, the checks are useless."""
        ordinary = bar("2024-01-02", 100.0, open_=99.0, high=101.0, low=98.5)
        assert not quality.check_ohlc_consistency([ordinary])
        assert not quality.check_prices_positive([ordinary])
        assert not quality.check_duplicates([ordinary])

    def test_zero_price_is_rejected(self):
        zeroed = broken_bar(
            "2024-01-02",
            open=Decimal("0"),
            high=Decimal("0"),
            low=Decimal("0"),
            close=Decimal("0"),
        )
        findings = quality.check_prices_positive([zeroed])
        assert findings[0].severity is quality.Severity.REJECT
        assert "placeholder" in findings[0].message

    def test_negative_volume_is_rejected(self):
        findings = quality.check_volume([broken_bar("2024-01-02", volume=Decimal("-5"))])
        assert findings[0].severity is quality.Severity.REJECT

    def test_a_single_zero_volume_session_is_not_a_finding(self):
        """A halted or untouched session is real. One of them is not a pattern."""
        bars = series([100.0, 101.0, 102.0])
        bars[1] = bar(bars[1].session_date, 101.0, volume=0)
        assert not [
            f for f in quality.check_volume(bars) if f.check is quality.CheckId.ZERO_VOLUME_RUN
        ]

    def test_a_run_of_zero_volume_sessions_is_flagged(self):
        bars = [
            bar(b.session_date, 100.0, volume=0 if i < 4 else 1_000)
            for i, b in enumerate(series([100.0] * 8))
        ]
        findings = [
            f for f in quality.check_volume(bars) if f.check is quality.CheckId.ZERO_VOLUME_RUN
        ]
        assert findings[0].details["sessions"] == 4
        assert findings[0].severity is quality.Severity.SUSPECT

    def test_a_zero_volume_run_at_the_end_of_the_series_is_still_caught(self):
        """The off-by-one a sentinel-free loop invites."""
        bars = [
            bar(b.session_date, 100.0, volume=1_000 if i < 3 else 0)
            for i, b in enumerate(series([100.0] * 8))
        ]
        findings = [
            f for f in quality.check_volume(bars) if f.check is quality.CheckId.ZERO_VOLUME_RUN
        ]
        assert findings and findings[0].details["sessions"] == 5


class TestTimestamps:
    def test_knowing_a_price_before_it_happened_is_rejected(self):
        """The single most damaging row a vendor can send."""
        close_time = CALENDAR.close_instant(dt.date(2024, 1, 2))
        leaking = broken_bar("2024-01-02", knowledge_time=close_time - dt.timedelta(hours=6))
        findings = quality.check_timestamps([leaking], now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert findings[0].severity is quality.Severity.REJECT
        assert "before it existed" in findings[0].message

    def test_a_future_bar_is_rejected(self):
        findings = quality.check_timestamps(
            [bar("2024-06-03", 100.0)], now=dt.datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert any("future" in f.message for f in findings)

    def test_an_ordinary_bar_produces_nothing(self):
        assert not quality.check_timestamps(
            [bar("2024-01-02", 100.0)], now=dt.datetime(2025, 1, 1, tzinfo=UTC)
        )


class TestCalendar:
    def test_a_missing_session_is_reported(self):
        bars = series([100.0] * 10)
        without = [b for b in bars if b is not bars[4]]
        findings = quality.check_calendar(without, CALENDAR)
        assert [f.session_date for f in findings] == [bars[4].session_date]
        assert "not fillable" in findings[0].message

    def test_a_bar_on_a_holiday_is_reported(self):
        """Either the vendor is wrong or our calendar is. The second is worse."""
        bars = series([100.0] * 5)
        july_fourth = dt.date(2024, 7, 4)
        assert not CALENDAR.is_session(july_fourth)
        bars.append(bar(dt.date(2024, 7, 3), 100.0))
        forged = broken_bar(
            "2024-07-04",
            event_time=dt.datetime(2024, 7, 4, 21, tzinfo=UTC),
            knowledge_time=dt.datetime(2024, 7, 4, 21, 20, tzinfo=UTC),
        )
        findings = quality.check_calendar([*bars, forged], CALENDAR)
        unexpected = [f for f in findings if f.check is quality.CheckId.UNEXPECTED_SESSION]
        assert [f.session_date for f in unexpected] == [july_fourth]

    def test_an_ipo_is_not_missing_its_pre_listing_history(self):
        """Without listing dates, every IPO looks like a catastrophic gap."""
        bars = series([100.0] * 10, start="2024-03-01")
        findings = quality.check_calendar(bars, CALENDAR, first_trade_date=bars[0].session_date)
        assert not findings

    def test_a_complete_series_produces_nothing(self):
        assert not quality.check_calendar(series([100.0] * 30), CALENDAR)


class TestFillPolicy:
    def test_price_bars_are_never_filled(self):
        """The rule the brief asks to be explicit about."""
        policy = quality.fill_policy()
        assert policy["price_bars"].startswith("NEVER")
        assert "manufactur" in policy["price_bars"] or "invents" in policy["price_bars"]

    def test_every_dataset_that_could_be_filled_has_a_stated_rule(self):
        policy = quality.fill_policy()
        for dataset in (
            "price_bars",
            "indicator_values",
            "corporate_actions",
            "benchmark_series",
            "fundamental_facts",
            "sector_classification",
            "intraday_aggregation",
        ):
            assert policy[dataset]

    def test_carrying_a_fundamental_forward_is_distinguished_from_filling(self):
        """Two different operations that look alike and are not.

        A fact stays current until superseded; a price does not.
        """
        policy = quality.fill_policy()
        assert "different from filling" in policy["fundamental_facts"]
        assert policy["fundamental_facts"].startswith("Carried forward")


class TestCorporateActions:
    def _split(self, ex_date: dt.date, ratio: str) -> CorporateAction:
        open_time = CALENDAR.session(ex_date).open_utc
        return CorporateAction(
            instrument_id=1,
            action_type=CorporateActionType.SPLIT,
            ex_date=ex_date,
            ratio=Decimal(ratio),
            event_time=open_time,
            knowledge_time=open_time,
            knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
        )

    def test_a_split_sized_jump_with_no_action_is_reported_not_repaired(self):
        """Inferring the split would rewrite genuine crashes into adjustments."""
        bars = series([100.0, 100.0, 50.0, 50.0])
        findings = quality.check_corporate_actions(bars, [])
        unexplained = [f for f in findings if f.check is quality.CheckId.UNEXPLAINED_JUMP]
        assert unexplained
        assert unexplained[0].severity is quality.Severity.SUSPECT
        assert unexplained[0].details["implied_ratio"] == pytest.approx(2.0)
        assert "never repaired" in unexplained[0].message

    def test_a_jump_explained_by_a_recorded_split_is_silent(self):
        bars = series([100.0, 100.0, 50.0, 50.0])
        action = self._split(bars[2].session_date, "2")
        findings = quality.check_corporate_actions(bars, [action])
        assert not findings

    def test_a_recorded_split_with_no_price_move_means_prices_are_pre_adjusted(self):
        """Applying the adjustment again would halve the series twice."""
        bars = series([100.0, 100.0, 100.5, 100.0])
        action = self._split(bars[2].session_date, "2")
        findings = quality.check_corporate_actions(bars, [action])
        assert findings[0].check is quality.CheckId.ACTION_WITHOUT_JUMP
        assert "already adjusted" in findings[0].message

    def test_a_reverse_split_is_as_visible_as_a_forward_one(self):
        """Ratio 0.1 and ratio 10 are equally large events."""
        bars = series([10.0, 10.0, 100.0, 100.0])
        action = self._split(bars[2].session_date, "0.1")
        assert not quality.check_corporate_actions(bars, [action])

    def test_a_small_dividend_does_not_look_like_a_missing_split(self):
        bars = series([100.0, 100.0, 99.5, 99.0])
        assert not quality.check_corporate_actions(bars, [])

    def test_a_genuine_collapse_is_flagged_but_survives(self):
        """SVB, Enron and Lehman all printed moves in this range and were real.

        The finding is SUSPECT, never REJECT: quarantining these would delete
        the most instructive rows in the dataset.
        """
        bars = series([100.0, 100.0, 8.0])
        findings = quality.check_corporate_actions(bars, [])
        assert all(f.severity is quality.Severity.SUSPECT for f in findings)
        assert findings[0].details["split_magnitude"] is True


class TestStaleAndExtreme:
    def test_a_repeated_bar_run_is_flagged(self):
        bars = series([100.0] * 8)
        findings = quality.check_stale_prices(bars)
        assert findings[0].flag is DataQualityFlag.STALE_REPEAT
        assert findings[0].details["sessions"] == 8

    def test_a_short_quiet_run_is_not_flagged(self):
        assert not quality.check_stale_prices(series([100.0, 100.0, 100.0, 101.0]))

    def test_a_stale_run_ending_the_series_is_caught(self):
        bars = series([101.0, 102.0, *([100.0] * 6)])
        assert quality.check_stale_prices(bars)

    def test_an_extreme_move_is_suspect_not_rejected(self):
        findings = quality.check_extreme_moves(series([100.0, 240.0]))
        assert findings[0].severity is quality.Severity.SUSPECT

    def test_ordinary_volatility_is_not_flagged(self):
        assert not quality.check_extreme_moves(series([100.0, 103.0, 99.0, 105.0, 97.0]))


class TestSymbolMappings:
    def test_one_ticker_two_instruments_overlapping_is_rejected(self):
        """The identity trap: a lookup returns an arbitrary row."""
        findings = quality.check_symbol_mappings(
            [
                SymbolMapping(instrument_id=1, ticker="GOOG", valid_from=dt.date(2004, 8, 19)),
                SymbolMapping(instrument_id=2, ticker="GOOG", valid_from=dt.date(2014, 4, 3)),
            ]
        )
        assert findings[0].severity is quality.Severity.REJECT

    def test_a_clean_handover_is_not_a_conflict(self):
        assert not quality.check_symbol_mappings(
            [
                SymbolMapping(
                    instrument_id=1,
                    ticker="GOOG",
                    valid_from=dt.date(2004, 8, 19),
                    valid_to=dt.date(2014, 4, 2),
                ),
                SymbolMapping(instrument_id=2, ticker="GOOG", valid_from=dt.date(2014, 4, 3)),
            ]
        )

    def test_an_unterminated_symbol_change_is_caught(self):
        """FB -> META with no valid_to extends the old ticker forever."""
        findings = quality.check_symbol_mappings(
            [
                SymbolMapping(instrument_id=1, ticker="FB", valid_from=dt.date(2012, 5, 18)),
                SymbolMapping(instrument_id=1, ticker="META", valid_from=dt.date(2022, 6, 9)),
            ]
        )
        assert findings[0].severity is quality.Severity.REJECT
        assert "valid_to was never set" in findings[0].message

    def test_a_properly_terminated_symbol_change_is_clean(self):
        assert not quality.check_symbol_mappings(
            [
                SymbolMapping(
                    instrument_id=1,
                    ticker="FB",
                    valid_from=dt.date(2012, 5, 18),
                    valid_to=dt.date(2022, 6, 8),
                ),
                SymbolMapping(instrument_id=1, ticker="META", valid_from=dt.date(2022, 6, 9)),
            ]
        )


class TestTheReport:
    def test_only_rejections_quarantine(self):
        bars = [
            *series([100.0] * 5),
            broken_bar("2024-01-09", high=Decimal("98"), low=Decimal("101")),
        ]
        report = quality.run_all(bars, now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert report.quarantine_keys() == {(1, dt.date(2024, 1, 9))}

    def test_suspect_rows_carry_a_flag_and_stay(self):
        bars = series([100.0, 240.0])
        report = quality.run_all(bars, now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert not report.rejected
        assert DataQualityFlag.SUSPECT_PRICE_SPIKE in report.flags_for(1, bars[1].session_date)

    def test_a_clean_batch_produces_nothing(self):
        report = quality.run_all(
            series([100.0, 101.0, 100.5, 102.0, 101.0, 103.0]),
            calendar=CALENDAR,
            now=dt.datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert report.summary()["findings"] == 0

    def test_moves_are_measured_within_an_instrument_not_across_the_batch(self):
        """A $500 name following a $5 name is not a 100x move."""
        cheap = series([5.0, 5.1, 5.0])
        rich = series([500.0, 505.0, 500.0], instrument_id=2)
        report = quality.run_all([*cheap, *rich], now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert report.summary()["findings"] == 0

    def test_silent_checks_are_reported(self):
        """A check that never fires is more likely broken than vigilant."""
        report = quality.run_all(series([100.0] * 3), now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert "ohlc_inconsistent" in report.silent_checks()

    def test_the_summary_separates_severities(self):
        bars = [
            *series([100.0, 240.0, 240.0]),
            broken_bar("2024-01-08", high=Decimal("48"), low=Decimal("51")),
        ]
        report = quality.run_all(bars, now=dt.datetime(2025, 1, 1, tzinfo=UTC))
        summary = report.summary()
        assert summary["reject"] >= 1
        assert summary["suspect"] >= 1
        assert summary["bars_examined"] == len(bars)


class TestValidationUniverse:
    def test_it_contains_the_hard_cases(self):
        universe = default_universe()
        assert len(universe) >= 50
        assert "LEH" in universe.tickers, "the canonical survivorship case"
        assert "GME" in universe.tickers, "the canonical false-positive case"

    def test_it_carries_a_control_group(self):
        """Without controls, a check that flags everything looks vigilant."""
        assert len(default_universe().controls) >= 10

    def test_every_delisted_name_has_a_last_trade_date(self):
        universe = default_universe()
        for instrument in universe.of_category(UniverseCategory.DELISTED):
            assert instrument.last_trade_date is not None, instrument.ticker

    def test_a_survivorship_unsafe_provider_loses_named_tests(self):
        """A count would not convey that losing LEH removes the whole test."""
        gap = default_universe().coverage_gap(supplies_delisted=False)
        assert "LEH" in gap and "BSC" in gap

    def test_the_symbol_map_is_reproducible_from_the_roster(self):
        first = default_universe().symbol_map()
        second = default_universe().symbol_map()
        assert first == second

    def test_stress_cases_outnumber_nothing_useful(self):
        universe = default_universe()
        assert len(universe.stress_cases) > len(universe.controls)

    def test_the_difficulty_index_names_what_each_case_tests(self):
        index = default_universe().difficulty_index()
        assert "bankruptcy" in index
        assert "multiple_splits" in index


class TestProviderAdapters:
    def test_both_adapters_satisfy_the_vendor_neutral_protocol(self):
        assert isinstance(StooqProvider({1: "AAPL"}), MarketDataProvider)
        assert isinstance(TiingoProvider({1: "AAPL"}, token="x"), MarketDataProvider)

    def test_stooq_is_honest_about_not_being_backtest_grade(self):
        provider = StooqProvider({1: "AAPL"})
        assert not provider.capabilities.backtest_grade
        assert not provider.capabilities.survivorship_safe
        assert any("adjusted" in c for c in provider.limitations())

    def test_tiingo_supplies_raw_prices_and_delisted_names(self):
        """The two properties that make it the recommendation."""
        provider = TiingoProvider({1: "AAPL"}, token="x")
        assert provider.capabilities.supplies_unadjusted_prices
        assert provider.capabilities.supplies_delisted_instruments

    def test_tiingo_still_is_not_backtest_grade_and_says_why(self):
        """It does not stamp a publication instant, so ours is a rule."""
        provider = TiingoProvider({1: "AAPL"}, token="x")
        assert not provider.capabilities.backtest_grade
        assert any("knowledge_time is estimated" in c for c in provider.limitations())
        assert any("index constituents" in c for c in provider.limitations())

    def test_an_unmapped_instrument_fails_before_any_request(self):
        with pytest.raises(ProviderError, match="no Stooq symbol"):
            list(
                StooqProvider({1: "AAPL"}).fetch_bars(99, dt.date(2024, 1, 1), dt.date(2024, 2, 1))
            )

    def test_intraday_is_refused_rather_than_silently_returning_daily(self):
        with pytest.raises(ProviderError, match="daily"):
            list(
                StooqProvider({1: "AAPL"}).fetch_bars(
                    1, dt.date(2024, 1, 1), dt.date(2024, 2, 1), Bartimeframe.H1
                )
            )

    def test_a_missing_token_is_an_auth_error_not_a_network_error(self):
        """Otherwise a typo looks like a firewall problem for an afternoon."""
        provider = TiingoProvider({1: "AAPL"}, token="")
        with pytest.raises(ProviderError, match="no Tiingo token"):
            list(provider.fetch_bars(1, dt.date(2024, 1, 1), dt.date(2024, 2, 1)))

    def test_symbol_map_is_sorted_and_stable(self):
        assert build_symbol_map(["MSFT", "aapl", "AAPL"]) == {1: "AAPL", 2: "MSFT"}


class TestTransport:
    def test_api_keys_never_reach_a_log_or_a_cache_sidecar(self):
        redacted = _redact("https://api.example.com/x?token=SECRET&startDate=2024-01-01")
        assert "SECRET" not in redacted
        assert "startDate=2024-01-01" in redacted

    def test_a_cache_key_changes_when_the_request_does(self):
        a = ResponseCache.key("https://x/y?a=1")
        b = ResponseCache.key("https://x/y?a=2")
        assert a != b

    def test_a_rotating_key_does_not_invalidate_cached_price_history(self):
        """Header values are excluded from the key deliberately."""
        a = ResponseCache.key("https://x/y", {"Authorization": "Token one"})
        b = ResponseCache.key("https://x/y", {"Authorization": "Token two"})
        assert a == b

    def test_a_cached_body_is_returned_without_a_request(self, tmp_path):
        cache = ResponseCache(root=tmp_path)
        key = ResponseCache.key("https://x/y", {"User-Agent": "tradeit/0.3"})
        cache.put(key, "https://x/y", b"cached")
        transport = HttpTransport(cache=cache, headers={})
        # No network is reachable in this environment; a hit proves it did not try.
        assert transport.get("https://x/y") == b"cached"

    def test_the_cache_manifest_records_what_was_fetched(self, tmp_path):
        cache = ResponseCache(root=tmp_path)
        cache.put(ResponseCache.key("https://x/y"), "https://x/y?token=SECRET", b"body")
        manifest = cache.manifest()
        assert manifest[0]["bytes"] == 4
        assert "SECRET" not in manifest[0]["url"]
        assert manifest[0]["sha256"]

    @pytest.mark.network
    def test_an_unreachable_host_is_diagnosed_as_such(self):
        """Distinguishes a blocked network from a rejected credential.

        Marked so it does not run by default -- it makes a real connection
        attempt, and its result depends on the machine's egress policy rather
        than on this repository.
        """
        transport = HttpTransport(max_attempts=1, timeout_s=5.0)
        with pytest.raises(ProviderUnreachableError, match="never saw the request"):
            transport.get("https://stooq.com/")
