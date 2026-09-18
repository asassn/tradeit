"""Volume on the served price's share basis -- DATA_DICTIONARY §0.9.

Every case asserts one invariant, because it is the only one a liquidity floor,
a participation limit or a turnover signal needs: **served price times served
volume is the money that traded**. Each fixture stores the vendor's numbers in
one of the shapes measured on the real corpus and checks the read restores it.

The worked example is AAPL on 2010-01-04: a true print of $214.01 on ~17.6M
shares, stored as $214.01 on 493.7M -- volume restated for the 2014 7:1 and the
2020 4:1 -- which the old read multiplied by seven again.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from tradeit.core.calendar import get_calendar
from tradeit.research01 import price_series
from tradeit.research01.series import VolumeBasis, volume_basis
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
)

UTC = dt.UTC
CAL = get_calendar()
EX = dt.date(2020, 8, 31)
BEFORE = CAL.sessions_between(dt.date(2020, 8, 10), dt.date(2020, 8, 28))[-12:]
AFTER = CAL.sessions_between(EX, dt.date(2020, 9, 20))[:12]
LATE = dt.datetime(2021, 6, 1, tzinfo=UTC)
EARLY = dt.datetime(2020, 8, 29, tzinfo=UTC)


_CIKS = iter(range(7, 10_000))


def _security(session: Session) -> int:
    cik = str(next(_CIKS))
    issuer = Issuer(display_name="VOL", source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value=cik,
            value_normalized=cik,
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
    return security.security_id


def _bar(
    session: Session,
    sid: int,
    day: dt.date,
    close: str,
    volume: str,
    *,
    basis: str = "raw",
    source: str = "eodhd-backfill",
) -> None:
    moment = dt.datetime.combine(day, dt.time(20), tzinfo=UTC)
    price = Decimal(close)
    session.add(
        SecurityPriceFact(
            security_id=sid,
            session_date=day,
            adjustment_basis=basis,
            event_time=moment,
            knowledge_time=moment,
            knowledge_time_basis="session_close",
            knowledge_source="test",
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal(volume),
            volume_adjusted=False,
            source=source,
        )
    )


def _split(session: Session, sid: int, ratio: str) -> None:
    moment = dt.datetime.combine(EX, dt.time(20), tzinfo=UTC)
    session.add(
        SecurityCorporateActionFact(
            security_id=sid,
            action_type="split",
            ex_date=EX,
            event_time=moment,
            knowledge_time=moment,
            knowledge_source="test",
            ratio=Decimal(ratio),
            source="test",
        )
    )


def _series(
    session: Session,
    *,
    raw: tuple[str, str],
    volume: tuple[str, str],
    total: tuple[str, str] | None = None,
    ratio: str = "2",
) -> int:
    """Twelve sessions either side of a split on ``EX``.

    ``total`` defaults to the adjusted close a genuine raw print implies, which is
    what makes :func:`split_evidence` read the price as the print.
    """
    sid = _security(session)
    if total is None:
        total = (raw[1], raw[1])
    for days, side in ((BEFORE, 0), (AFTER, 1)):
        for day in days:
            _bar(session, sid, day, raw[side], volume[side])
            _bar(session, sid, day, total[side], volume[side], basis="total")
    _split(session, sid, ratio)
    session.flush()
    return sid


def _money(bar_close: Decimal, bar_volume: Decimal) -> Decimal:
    return (bar_close * bar_volume).quantize(Decimal("0.01"))


class TestTheBasisIsRead:
    def test_volume_that_steps_with_the_split_is_the_print(self, db_session: Session) -> None:
        sid = _series(db_session, raw=("100", "50"), volume=("1000", "2000"))
        assert volume_basis(db_session, sid) == {"eodhd-backfill": VolumeBasis.RAW}

    def test_volume_that_runs_through_the_split_is_already_restated(
        self, db_session: Session
    ) -> None:
        """AAPL across 2014-06-09: the close falls 645.57 -> 93.70 and the volume
        runs 349.9M -> 301.7M. That shape."""
        sid = _series(db_session, raw=("100", "50"), volume=("2000", "2000"))
        assert volume_basis(db_session, sid) == {"eodhd-backfill": VolumeBasis.ADJUSTED}

    def test_a_security_without_splits_does_not_need_the_question(
        self, db_session: Session
    ) -> None:
        sid = _security(db_session)
        for day in (*BEFORE, *AFTER):
            _bar(db_session, sid, day, "10", "500")
        db_session.flush()
        bars = price_series(db_session, sid, as_of=LATE)
        assert {bar.volume_basis for bar in bars} == {VolumeBasis.NOT_NEEDED}
        assert all(bar.volume == Decimal(500) for bar in bars)

    def test_too_little_evidence_is_undetermined_and_served_as_before(
        self, db_session: Session
    ) -> None:
        """Fail visible: the old number, marked, so a floor can refuse it."""
        sid = _security(db_session)
        for day in BEFORE[-3:]:
            _bar(db_session, sid, day, "100", "1000")
            _bar(db_session, sid, day, "50", "1000", basis="total")
        for day in AFTER[:3]:
            _bar(db_session, sid, day, "50", "2000")
            _bar(db_session, sid, day, "50", "2000", basis="total")
        _split(db_session, sid, "2")
        db_session.flush()
        first = price_series(db_session, sid, as_of=LATE)[0]
        assert first.volume_basis is VolumeBasis.UNDETERMINED
        assert first.volume == Decimal(1000) * 2


class TestPriceTimesVolumeIsTheMoneyThatTraded:
    """True print before the split: $100 on 1,000 shares, $100,000."""

    TRUE = Decimal(100_000)

    def test_raw_volume(self, db_session: Session) -> None:
        sid = _series(db_session, raw=("100", "50"), volume=("1000", "2000"))
        first = price_series(db_session, sid, as_of=LATE)[0]
        assert first.volume_basis is VolumeBasis.RAW
        assert _money(first.close, first.volume) == self.TRUE

    def test_restated_volume_is_not_restated_twice(self, db_session: Session) -> None:
        """The defect: the old read served 4,000 here -- $200,000 of trading
        that never happened."""
        sid = _series(db_session, raw=("100", "50"), volume=("2000", "2000"))
        first = price_series(db_session, sid, as_of=LATE)[0]
        assert first.volume_basis is VolumeBasis.ADJUSTED
        assert first.volume == Decimal(2000)
        assert _money(first.close, first.volume) == self.TRUE

    def test_a_split_after_the_as_of_is_taken_back_out(self, db_session: Session) -> None:
        """The look-ahead. Read before the split was knowable, the price is the
        print and the volume must be the shares that traded -- not a number that
        already knows about a split the market had not heard of."""
        sid = _series(db_session, raw=("100", "50"), volume=("2000", "2000"))
        first = price_series(db_session, sid, as_of=EARLY)[0]
        assert first.close == Decimal(100)
        assert first.volume == Decimal(1000)
        assert _money(first.close, first.volume) == self.TRUE

    def test_a_reverse_split(self, db_session: Session) -> None:
        """A 1-for-10 on a distressed name. Restated volume divides by ten, which
        §0.9 found wrongly excludes such names from a liquidity floor -- 8,356
        observations on §26's panel, median 63-session return -6.25%."""
        sid = _series(db_session, raw=("1", "10"), volume=("10000", "10000"), ratio="0.1")
        first = price_series(db_session, sid, as_of=LATE)[0]
        assert first.volume_basis is VolumeBasis.ADJUSTED
        # True print: $1 on 100,000 shares.
        assert _money(first.close, first.volume) == Decimal(100_000)

    def test_a_price_the_vendor_already_restated(self, db_session: Session) -> None:
        """Stored close already halved (both bases flat), volume the print. The
        served price is on the post-split share count, so the volume must be too:
        1,000 shares at a true $100 serve as 2,000 at $50."""
        sid = _series(
            db_session,
            raw=("50", "50"),
            total=("50", "50"),
            volume=("1000", "2000"),
        )
        first = price_series(db_session, sid, as_of=LATE)[0]
        assert first.split_factor == 1
        assert first.volume == Decimal(2000)
        assert _money(first.close, first.volume) == self.TRUE


class TestEachVendorIsReadOnItsOwnBasis:
    EX2 = dt.date(2020, 11, 2)

    def test_two_vendors_on_one_security(self, db_session: Session) -> None:
        """One security, two 2-for-1 splits, each stretch supplied by a different
        vendor -- the way the Sharadar backfills extended EODHD series.

        True history: $100 on 1,000 shares, then $50 on 2,000 after the first
        split, then $25 on 4,000 after the second. Every session trades $100,000.

        EODHD covers the first split and restates volume for **both** splits it
        knew on delivery, so its volume reads 4,000 throughout. Sharadar covers
        the second and serves the print. The schema cannot hold two vendors' raw
        bars for one session at one instant, so they never overlap -- which is
        also how the real corpus looks.
        """
        before2 = CAL.sessions_between(dt.date(2020, 10, 10), dt.date(2020, 10, 30))[-12:]
        after2 = CAL.sessions_between(self.EX2, dt.date(2020, 11, 25))[:12]
        sid = _security(db_session)
        stretches = (
            (BEFORE, "100", "4000", "eodhd-backfill"),
            (AFTER, "50", "4000", "eodhd-backfill"),
            (before2, "50", "2000", "sharadar-backfill"),
            (after2, "25", "4000", "sharadar-backfill"),
        )
        for days, close, shares, source in stretches:
            for day in days:
                _bar(db_session, sid, day, close, shares, source=source)
                _bar(db_session, sid, day, "25", shares, basis="total", source=source)
        for ex in (EX, self.EX2):
            moment = dt.datetime.combine(ex, dt.time(20), tzinfo=UTC)
            db_session.add(
                SecurityCorporateActionFact(
                    security_id=sid,
                    action_type="split",
                    ex_date=ex,
                    event_time=moment,
                    knowledge_time=moment,
                    knowledge_source="test",
                    ratio=Decimal(2),
                    source="test",
                )
            )
        db_session.flush()

        assert volume_basis(db_session, sid) == {
            "eodhd-backfill": VolumeBasis.ADJUSTED,
            "sharadar-backfill": VolumeBasis.RAW,
        }
        bars = {bar.session_date: bar for bar in price_series(db_session, sid, as_of=LATE)}
        for day in (BEFORE[0], AFTER[0], before2[0], after2[0]):
            assert _money(bars[day].close, bars[day].volume) == Decimal(100_000), day


class TestBothReadPathsAgree:
    """``CorpusSessionData`` serves raw prints to the backtester and applies
    splits to holdings itself. Its volume must be the shares that traded at that
    raw price -- a participation limit resting on a restated number lets a
    company that later split absorb trades it never could have."""

    def _money_in_backtest(self, session: Session, sid: int, day: dt.date) -> Decimal:
        from tradeit.backtesting.corpus import CorpusSessionData

        data = CorpusSessionData(
            session=session,
            universe=(sid,),
            start=BEFORE[0],
            end=AFTER[-1],
            candidate_source=lambda d, b: [],
        )
        bar = data.bars(day)[sid]
        return _money(bar.close, bar.volume)

    def test_restated_volume_in_the_backtester(self, db_session: Session) -> None:
        sid = _series(db_session, raw=("100", "50"), volume=("2000", "2000"))
        assert self._money_in_backtest(db_session, sid, BEFORE[0]) == Decimal(100_000)

    def test_the_two_paths_report_the_same_money(self, db_session: Session) -> None:
        """They disagreed once, over zero-priced bars, and that was the worse
        outcome. Every shape above, both paths, one number."""
        shapes = (
            {"raw": ("100", "50"), "volume": ("1000", "2000")},
            {"raw": ("100", "50"), "volume": ("2000", "2000")},
            {"raw": ("1", "10"), "volume": ("10000", "10000"), "ratio": "0.1"},
        )
        for shape in shapes:
            sid = _series(db_session, **shape)  # type: ignore[arg-type]
            research = price_series(db_session, sid, as_of=LATE)[0]
            assert self._money_in_backtest(db_session, sid, BEFORE[0]) == _money(
                research.close, research.volume
            ), shape
