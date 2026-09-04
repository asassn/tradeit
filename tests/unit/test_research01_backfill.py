"""The bulk downloader, tested against real captured responses.

The fixtures in ``tests/fixtures/eodhd/`` were captured from the live API using
EODHD's public ``demo`` token, so the field names below are the vendor's rather
than ours. That is the point: ``acquisition/eodhd.py`` was left unfinished
precisely because fixtures invented from memory "would produce a green suite
that proves nothing".

Two traps the real data contains, both of which look like ordinary numbers:

* AAPL's ``close`` of 500.04 on 2020-08-27 has ``adjusted_close`` 121.15 —
  restated by a split that happened **four days later**.
* AAPL's 2020-02-07 dividend is ``value`` 0.1925 and ``unadjustedValue`` 0.77 —
  the same split applied backwards to the amount actually declared.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    BackfillPlan,
    BackfillProgress,
    parse_bars,
    parse_dividends,
    parse_splits,
    run_backfill,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eodhd"


def _fixture(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeClient:
    """Serves captured responses and counts calls. No network, no key."""

    def __init__(
        self,
        eod: list[dict[str, Any]],
        splits: list[dict[str, Any]],
        dividends: list[dict[str, Any]],
        fail_on: str = "",
    ) -> None:
        self._eod, self._splits, self._div, self._fail_on = eod, splits, dividends, fail_on
        self.calls = 0

    def _maybe_fail(self, symbol: str) -> None:
        self.calls += 1
        if symbol == self._fail_on:
            raise RuntimeError("vendor returned garbage")

    def eod(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        self._maybe_fail(symbol)
        return self._eod

    def splits(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        self.calls += 1
        return self._splits

    def dividends(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        self.calls += 1
        return self._div


class TestParsingRealResponses:
    def test_raw_and_adjusted_closes_become_separate_bars(self) -> None:
        """The adjusted close is a different series, not a better number."""
        bars = parse_bars("AAPL.US", _fixture("eod_aapl_2020_split"))
        first_day = [b for b in bars if b.session_date == dt.date(2020, 8, 27)]
        by_basis = {b.adjustment_basis: b for b in first_day}
        assert set(by_basis) == {"raw", "total"}
        assert by_basis["raw"].close == Decimal("500.04")
        assert by_basis["total"].close == Decimal("121.1519")
        # Four days before the split that produced the difference.
        assert by_basis["raw"].close > by_basis["total"].close * 3

    def test_a_dividend_takes_the_declared_amount_not_the_restated_one(self) -> None:
        """``value`` is the same dividend restated for a later split.

        Using it would inject a look-ahead through a field that looks like an
        ordinary number.
        """
        dividends = parse_dividends("AAPL.US", _fixture("div_aapl_2020"))
        february = next(d for d in dividends if d.ex_date == dt.date(2020, 2, 7))
        assert february.cash_amount == Decimal("0.77")
        assert february.cash_amount != Decimal("0.1925")

    def test_a_split_ratio_string_is_parsed(self) -> None:
        splits = parse_splits("AAPL.US", _fixture("splits_aapl_2020"))
        assert len(splits) == 1
        assert splits[0].ex_date == dt.date(2020, 8, 31)
        assert splits[0].ratio == Decimal("4")

    @pytest.mark.parametrize("bad", ["", "4", "4/0", "abc/def", "-4/1"])
    def test_a_malformed_ratio_is_dropped_not_defaulted_to_one(self, bad: str) -> None:
        """A split of 1 does nothing and passes every check downstream."""
        assert parse_splits("X.US", [{"date": "2020-08-31", "split": bad}]) == []

    def test_the_old_suffix_is_kept_so_two_companies_stay_two(self) -> None:
        """``GM`` and ``GM_old`` are different registrants, per EODHD's own
        convention. Stripping the suffix to make them match would splice
        exactly the pair the control universe exists to keep apart."""
        bars = parse_bars("GM_old.US", _fixture("eod_aapl_2020_split"))
        assert {b.ticker for b in bars} == {"GM_old"}
        assert parse_bars("GM.US", _fixture("eod_aapl_2020_split"))[0].ticker == "GM"


def _corpus(session: Session, ticker: str) -> Security:
    issuer = Issuer(display_name=ticker, source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value="320193",
            value_normalized="320193",
            role="primary",
            citation="t",
            source="test",
        )
    )
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    session.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=dt.date(2000, 1, 1),
            valid_to=None,
            knowledge_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            knowledge_source="test",
            source="test",
        )
    )
    session.flush()
    return security


def _plan(symbols: tuple[str, ...], budget: int = 100_000) -> BackfillPlan:
    return BackfillPlan(
        symbols=symbols,
        start=dt.date(2020, 1, 1),
        end=dt.date(2020, 12, 31),
        daily_call_budget=budget,
    )


def _client() -> FakeClient:
    return FakeClient(
        _fixture("eod_aapl_2020_split"), _fixture("splits_aapl_2020"), _fixture("div_aapl_2020")
    )


class TestDryRunCostsNothing:
    def test_it_issues_no_calls_and_writes_no_rows(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """The whole point: prove the wiring before a subscription exists."""
        _corpus(db_session, "AAPL")
        client = _client()
        report = run_backfill(
            db_session,
            client,
            _plan(("AAPL.US", "MSFT.US")),
            BackfillProgress(tmp_path / "p.json"),
            dry_run=True,
        )
        assert client.calls == 0
        assert db_session.scalars(select(SecurityPriceFact)).all() == []
        # And it still reports what a real run would consume.
        assert report.calls_issued == 6
        assert report.symbols_fetched == 2

    def test_a_plan_reports_whether_it_fits_in_a_day(self) -> None:
        assert _plan(tuple(f"S{i}.US" for i in range(10))).fits_in_one_day
        assert not _plan(tuple(f"S{i}.US" for i in range(10)), budget=5).fits_in_one_day


class TestResumability:
    def test_an_interrupted_run_resumes_without_refetching(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """A laptop closing on day three must not cost days one and two."""
        _corpus(db_session, "AAPL")
        checkpoint = tmp_path / "progress.json"

        first = run_backfill(
            db_session, _client(), _plan(("AAPL.US",)), BackfillProgress.load(checkpoint)
        )
        assert first.symbols_fetched == 1
        assert checkpoint.exists()

        client = _client()
        second = run_backfill(
            db_session, client, _plan(("AAPL.US",)), BackfillProgress.load(checkpoint)
        )
        assert second.skipped_already_done == 1
        assert second.symbols_fetched == 0
        assert client.calls == 0

    def test_the_checkpoint_survives_a_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "p.json"
        progress = BackfillProgress(path)
        progress.completed.add("AAPL.US")
        progress.calls_used = 3
        progress.save()
        assert BackfillProgress.load(path).completed == {"AAPL.US"}
        assert BackfillProgress.load(path).calls_used == 3


class TestBudgetIsRefusedNotThrottled:
    def test_the_run_stops_and_says_so(self, db_session: Session, tmp_path: Path) -> None:
        """The vendor set the limit; exceeding it is not ours to decide."""
        _corpus(db_session, "AAPL")
        report = run_backfill(
            db_session,
            _client(),
            _plan(("AAPL.US", "B.US", "C.US"), budget=3),
            BackfillProgress(tmp_path / "p.json"),
        )
        assert report.symbols_fetched == 1
        assert report.stopped_on_budget is True


class TestOneBadSymbolDoesNotCostTheMonth:
    def test_a_failure_is_recorded_and_the_run_continues(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _corpus(db_session, "AAPL")
        client = FakeClient(
            _fixture("eod_aapl_2020_split"),
            _fixture("splits_aapl_2020"),
            _fixture("div_aapl_2020"),
            fail_on="BAD.US",
        )
        report = run_backfill(
            db_session,
            client,
            _plan(("BAD.US", "AAPL.US")),
            BackfillProgress(tmp_path / "p.json"),
        )
        assert len(report.failures) == 1
        assert report.failures[0][0] == "BAD.US"
        assert report.symbols_fetched == 1
        assert report.bars.landed > 0

    def test_a_failed_symbol_is_not_marked_complete(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Otherwise a resume would skip the one symbol that actually needs it."""
        _corpus(db_session, "AAPL")
        checkpoint = tmp_path / "p.json"
        client = FakeClient([], [], [], fail_on="BAD.US")
        run_backfill(db_session, client, _plan(("BAD.US",)), BackfillProgress.load(checkpoint))
        assert BackfillProgress.load(checkpoint).completed == set()


class TestLandingUsesTheRealImporters:
    def test_bars_and_actions_land_through_identity_resolution(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _corpus(db_session, "AAPL")
        report = run_backfill(
            db_session, _client(), _plan(("AAPL.US",)), BackfillProgress(tmp_path / "p.json")
        )
        assert report.bars.landed > 0
        assert report.actions.landed > 0
        split = db_session.scalars(
            select(SecurityCorporateActionFact).where(
                SecurityCorporateActionFact.action_type == "split"
            )
        ).one()
        assert split.ratio == Decimal("4")

    def test_an_unseeded_symbol_lands_nothing_and_creates_nothing(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        report = run_backfill(
            db_session, _client(), _plan(("AAPL.US",)), BackfillProgress(tmp_path / "p.json")
        )
        assert report.bars.landed == 0
        assert len(report.bars.unresolved) > 0
        assert db_session.scalars(select(Security)).all() == []


class TestTokenResolution:
    """Where the key lives, and the ways it must not leak."""

    def test_the_environment_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tradeit.research01.eodhd_client import resolve_api_token

        monkeypatch.setenv("EODHD_API_KEY", "from-env")
        assert resolve_api_token() == "from-env"

    def test_eodhd_s_own_variable_name_is_accepted_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EODHD's docs and plugin say EODHD_API_TOKEN; this repo says
        EODHD_API_KEY. A key that works everywhere except here is a support
        question nobody should have to ask."""
        from tradeit.research01.eodhd_client import resolve_api_token

        monkeypatch.delenv("EODHD_API_KEY", raising=False)
        monkeypatch.setenv("EODHD_API_TOKEN", "from-their-name")
        assert resolve_api_token() == "from-their-name"

    def test_a_dotenv_file_is_read_and_quotes_are_stripped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from tradeit.research01.eodhd_client import resolve_api_token

        monkeypatch.delenv("EODHD_API_KEY", raising=False)
        monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
        env = tmp_path / ".env"
        env.write_text('# a comment\nTRADEIT_LOG_LEVEL=INFO\nEODHD_API_KEY="quoted-key"\n')
        assert resolve_api_token(env_file=env) == "quoted-key"

    def test_a_missing_token_names_the_variable_and_not_a_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The failure must be actionable without ever echoing a secret."""
        from tradeit.errors import DataError
        from tradeit.research01.eodhd_client import resolve_api_token

        monkeypatch.delenv("EODHD_API_KEY", raising=False)
        monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
        with pytest.raises(DataError) as caught:
            resolve_api_token(env_file=tmp_path / "absent.env")
        message = str(caught.value)
        assert "EODHD_API_KEY" in message
        assert "gitignored" in message

    def test_the_client_refuses_an_empty_token_rather_than_calling(self) -> None:
        """An unauthenticated call would return a 401 that reads like absent
        data. Refusing locally keeps the two distinguishable."""
        import datetime as _dt

        from tradeit.errors import DataError
        from tradeit.research01.eodhd_client import HttpEodhdClient

        with pytest.raises(DataError, match="refusing"):
            HttpEodhdClient(api_token="").eod("AAPL.US", _dt.date(2020, 1, 1), _dt.date(2020, 1, 2))


class TestSpanOverlapDetection:
    """The check that reported PASS on spans that plainly overlapped.

    The first version compared each span against a hardcoded 2009 date rather
    than against the other span, so the real GM/GM_old result -- GM_old to
    2011-03-31, GM from 2010-11-18 -- was reported as disjoint. It produced the
    expected-looking answer to a question it had not asked, which is the failure
    this repository is built to catch and had just committed.
    """

    @staticmethod
    def _overlaps(a: tuple[dt.date, dt.date], b: tuple[dt.date, dt.date]) -> bool:
        return a[0] <= b[1] and b[0] <= a[1]

    def test_the_real_gm_spans_are_detected_as_overlapping(self) -> None:
        old = (dt.date(1998, 1, 2), dt.date(2011, 3, 31))
        new = (dt.date(2010, 11, 18), dt.date(2026, 8, 31))
        assert self._overlaps(old, new) is True

    def test_a_hardcoded_break_date_would_have_missed_it(self) -> None:
        """Pins why the old logic failed, so it is not reintroduced."""
        old = (dt.date(1998, 1, 2), dt.date(2011, 3, 31))
        new = (dt.date(2010, 11, 18), dt.date(2026, 8, 31))
        legacy_break = dt.date(2009, 7, 1)
        legacy_verdict = old[1] >= legacy_break and new[0] <= legacy_break
        assert legacy_verdict is False  # the old check said "no overlap"
        assert self._overlaps(old, new) is True  # the truth

    def test_genuinely_disjoint_spans_are_not_flagged(self) -> None:
        assert (
            self._overlaps(
                (dt.date(2000, 1, 3), dt.date(2009, 6, 1)),
                (dt.date(2009, 9, 1), dt.date(2020, 1, 1)),
            )
            is False
        )

    def test_touching_spans_count_as_overlapping(self) -> None:
        """One shared day is still two securities claiming one ticker at once."""
        assert (
            self._overlaps(
                (dt.date(2000, 1, 3), dt.date(2009, 6, 1)),
                (dt.date(2009, 6, 1), dt.date(2020, 1, 1)),
            )
            is True
        )


class TestPerSymbolWindows:
    """A dead registrant's ticker is often held by somebody else today.

    Requesting its whole history returns the successor's bars too. Those would
    be rejected on the way in -- ``resolve_security`` resolves per bar date --
    but rejection is detection, and not asking is prevention.
    """

    def test_a_symbol_with_a_window_is_fetched_only_over_it(self) -> None:
        plan = BackfillPlan(
            symbols=("AKRX.US", "AAPL.US"),
            start=dt.date(1990, 1, 1),
            end=dt.date(2026, 9, 1),
            windows={"AKRX.US": (dt.date(1990, 1, 1), dt.date(2020, 5, 20))},
        )
        assert plan.window_for("AKRX.US") == (dt.date(1990, 1, 1), dt.date(2020, 5, 20))

    def test_a_symbol_without_one_uses_the_plan(self) -> None:
        plan = BackfillPlan(
            symbols=("AAPL.US",), start=dt.date(1990, 1, 1), end=dt.date(2026, 9, 1)
        )
        assert plan.window_for("AAPL.US") == (dt.date(1990, 1, 1), dt.date(2026, 9, 1))

    def test_the_client_is_asked_for_the_narrow_window(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        asked: list[tuple[str, dt.date, dt.date]] = []

        class Recording:
            def eod(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, object]]:
                asked.append((symbol, start, end))
                return []

            def splits(self, symbol: str, start: dt.date, end: dt.date) -> list[dict[str, object]]:
                return []

            def dividends(
                self, symbol: str, start: dt.date, end: dt.date
            ) -> list[dict[str, object]]:
                return []

        plan = BackfillPlan(
            symbols=("DEAD.US",),
            start=dt.date(1990, 1, 1),
            end=dt.date(2026, 9, 1),
            windows={"DEAD.US": (dt.date(1990, 1, 1), dt.date(2011, 3, 4))},
        )
        run_backfill(
            db_session,
            Recording(),  # type: ignore[arg-type]
            plan,
            BackfillProgress(path=tmp_path / "progress.json"),
        )
        assert asked == [("DEAD.US", dt.date(1990, 1, 1), dt.date(2011, 3, 4))]


class TestTheCheckpointCannotOutrunTheCommit:
    """494 symbols checkpointed, 472 with nothing behind them.

    ``run_backfill`` committed once at the end of the run while writing its
    checkpoint after every symbol, so a power cut rolled back every row and left
    a progress file claiming they were all done. A resume would have skipped
    each one. The same rule the FSDS importer learned: a side file and the
    corpus must not be able to disagree.
    """

    def test_each_symbol_is_committed_before_it_is_checkpointed(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        order: list[str] = []
        real_commit = db_session.commit

        def watched_commit() -> None:
            order.append("commit")
            real_commit()

        class Watched(BackfillProgress):
            def save(self) -> None:
                order.append("checkpoint")
                super().save()

        progress = Watched(path=tmp_path / "p.json")
        db_session.commit = watched_commit  # type: ignore[method-assign]

        run_backfill(
            db_session,
            FakeClient(eod=[], splits=[], dividends=[]),
            BackfillPlan(symbols=("DEAD.US",), start=dt.date(1990, 1, 1), end=dt.date(2011, 3, 4)),
            progress,
        )
        assert order == ["commit", "checkpoint"], order
