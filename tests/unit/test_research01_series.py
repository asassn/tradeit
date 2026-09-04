"""Reconstructing a price series as it could have been known on a given day.

`pit.py` established that a vendor-delivered adjusted series carries the
vendor's adjustment epoch and is unusable for historical work, and that the only
valid route is to derive our own from raw bars plus the actions known at the
instant being asked about. **That derivation did not exist**, so the corpus
offered a choice between raw bars that jump at every split and adjusted bars
that contain the future. This is it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    Coherence,
    adjudicated_bound,
    adjudicated_window,
    known_splits,
    price_series,
    series_coherence,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

UTC = dt.UTC
SPLIT_DAY = dt.date(2020, 8, 31)
AS_OF = dt.datetime(2026, 9, 1, tzinfo=UTC)


def _security(session: Session) -> Security:
    issuer = Issuer(display_name="TEST", source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value="1",
            value_normalized="1",
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
    return security


def _bar(session: Session, sid: int, day: dt.date, close: Decimal, basis: str = "raw") -> None:
    moment = dt.datetime.combine(day, dt.time(20), tzinfo=UTC)
    session.add(
        SecurityPriceFact(
            security_id=sid,
            session_date=day,
            adjustment_basis=basis,
            event_time=moment,
            knowledge_time=moment,
            knowledge_time_basis="session_close",
            knowledge_source="test",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal(100),
            volume_adjusted=False,
            source="test",
        )
    )


def _split(session: Session, sid: int, ex: dt.date, ratio: Decimal) -> None:
    moment = dt.datetime.combine(ex, dt.time(20), tzinfo=UTC)
    session.add(
        SecurityCorporateActionFact(
            security_id=sid,
            action_type="split",
            ex_date=ex,
            event_time=moment,
            knowledge_time=moment,
            knowledge_source="test",
            ratio=ratio,
            source="test",
        )
    )


class TestTheAdjustmentIsPointInTime:
    """The property the whole module exists for."""

    def _corpus(self, session: Session) -> int:
        security = _security(session)
        sid = security.security_id
        _bar(session, sid, dt.date(2020, 8, 27), Decimal("500.04"))
        _bar(session, sid, dt.date(2020, 9, 1), Decimal("124.81"))
        _split(session, sid, SPLIT_DAY, Decimal(4))
        session.flush()
        return sid

    def test_standing_after_the_split_the_earlier_bar_is_adjusted(
        self, db_session: Session
    ) -> None:
        """AAPL's real 4-for-1: 500.04 before becomes 125.01, comparable with
        the 124.81 that follows it."""
        sid = self._corpus(db_session)
        bars = price_series(db_session, sid, as_of=dt.datetime(2021, 1, 1, tzinfo=UTC))
        pre = next(b for b in bars if b.session_date == dt.date(2020, 8, 27))
        assert pre.close == Decimal("125.01")
        assert pre.raw_close == Decimal("500.04")
        assert pre.split_factor == 4

    def test_standing_before_the_split_it_does_not_exist(self, db_session: Session) -> None:
        """Applying it would put the future into the past."""
        sid = self._corpus(db_session)
        bars = price_series(db_session, sid, as_of=dt.datetime(2020, 8, 28, tzinfo=UTC))
        pre = next(b for b in bars if b.session_date == dt.date(2020, 8, 27))
        assert pre.close == Decimal("500.04")
        assert pre.split_factor == 1
        assert known_splits(db_session, sid, as_of=dt.datetime(2020, 8, 28, tzinfo=UTC)) == []

    def test_the_same_bar_reads_differently_from_two_vantage_points(
        self, db_session: Session
    ) -> None:
        """Not a bug -- the definition of point-in-time."""
        sid = self._corpus(db_session)
        after = price_series(db_session, sid, as_of=dt.datetime(2021, 1, 1, tzinfo=UTC))[0]
        before = price_series(db_session, sid, as_of=dt.datetime(2020, 8, 28, tzinfo=UTC))[0]
        assert after.session_date == before.session_date
        assert before.close == after.close * 4

    def test_a_bar_on_the_ex_date_is_not_adjusted(self, db_session: Session) -> None:
        """The split has already taken effect in that day's print. Adjusting it
        again would halve a price that was never doubled."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, SPLIT_DAY, Decimal("125.00"))
        _split(db_session, sid, SPLIT_DAY, Decimal(4))
        db_session.flush()
        bar = price_series(db_session, sid, as_of=dt.datetime(2021, 1, 1, tzinfo=UTC))[0]
        assert bar.close == Decimal("125.00")
        assert bar.split_factor == 1


class TestMechanics:
    def test_volume_moves_opposite_to_price(self, db_session: Session) -> None:
        """A split multiplies the share count. Adjusting price without volume
        silently breaks every turnover and liquidity measure."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2020, 8, 27), Decimal("500.00"))
        _split(db_session, sid, SPLIT_DAY, Decimal(4))
        db_session.flush()
        bar = price_series(db_session, sid, as_of=dt.datetime(2021, 1, 1, tzinfo=UTC))[0]
        assert bar.close == Decimal("125.00")
        assert bar.volume == Decimal(400)

    def test_a_reverse_split_multiplies_the_price(self, db_session: Session) -> None:
        """A 1-for-10 arrives as ratio 0.1; the earlier price must go UP."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2001, 5, 9), Decimal("2.86"))
        _split(db_session, sid, dt.date(2001, 5, 10), Decimal("0.1"))
        db_session.flush()
        bar = price_series(db_session, sid, as_of=dt.datetime(2002, 1, 1, tzinfo=UTC))[0]
        assert bar.close == Decimal("28.60")

    def test_successive_splits_compound(self, db_session: Session) -> None:
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2020, 1, 1), Decimal("100"))
        _split(db_session, sid, dt.date(2020, 6, 1), Decimal(2))
        _split(db_session, sid, dt.date(2021, 6, 1), Decimal(5))
        db_session.flush()
        bar = price_series(db_session, sid, as_of=dt.datetime(2022, 1, 1, tzinfo=UTC))[0]
        assert bar.split_factor == 10
        assert bar.close == Decimal(10)

    def test_vendor_adjusted_bars_are_never_read(self, db_session: Session) -> None:
        """Feeding a `total` bar through this would adjust an already adjusted
        number twice, with the vendor's delivery epoch still inside it."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2020, 8, 27), Decimal("121.15"), basis="total")
        db_session.flush()
        assert price_series(db_session, sid, as_of=dt.datetime(2021, 1, 1, tzinfo=UTC)) == []


class TestSeriesCoherence:
    """Measured on the corpus: 89.3% coherent, 9.2% more than seven years out.

    Bimodal rather than a tail, which is why the threshold is where it is.
    """

    @pytest.mark.parametrize(
        ("years_after", "expected"),
        [
            (0.0, Coherence.COHERENT),
            (0.5, Coherence.COHERENT),
            (2.0, Coherence.QUESTIONABLE),
            (10.0, Coherence.SUSPECT_TICKER_REUSE),
            (24.8, Coherence.SUSPECT_TICKER_REUSE),
        ],
    )
    def test_the_gap_decides_the_verdict(self, years_after: float, expected: Coherence) -> None:
        last_filing = dt.date(2001, 1, 1)
        verdict, gap = series_coherence(
            last_session=last_filing + dt.timedelta(days=round(years_after * 365.25)),
            last_filing=last_filing,
        )
        assert verdict is expected
        assert gap is not None

    def test_an_unknown_filing_span_is_not_coherent(self) -> None:
        """Absence of the comparison is not evidence that it would pass."""
        verdict, gap = series_coherence(last_session=dt.date(2020, 1, 1), last_filing=None)
        assert verdict is Coherence.UNKNOWN
        assert gap is None

    def test_it_reports_rather_than_filters(self) -> None:
        """Truncating here would discard legitimate post-delisting trading;
        dropping would hide a splice instead of naming it."""
        verdict, gap = series_coherence(
            last_session=dt.date(2026, 1, 1), last_filing=dt.date(2001, 1, 1)
        )
        assert verdict is Coherence.SUSPECT_TICKER_REUSE
        assert gap is not None and gap > 24


class TestAdjudicatedBound:
    """A recorded boundary is honoured; an unrecorded suspicion is not.

    The asymmetry with :class:`Coherence` above is the point of both. Coherence
    is a shape and reports; an adjudicated bound is an established, cited fact
    written into the alias interval, and reading the corpus as recorded is not
    the same act as filtering on a hunch.
    """

    def _bound_series(self, session: Session, bound: dt.date | None) -> Security:
        security = _security(session)
        for day in (dt.date(2000, 1, 3), dt.date(2000, 1, 4), dt.date(2018, 1, 3)):
            _bar(session, security.security_id, day, Decimal("10"))
        session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="ticker",
                alias_value="ZZZZ",
                valid_from=dt.date(1990, 1, 1),
                valid_to=bound,
                knowledge_time=dt.datetime(2026, 1, 1, tzinfo=UTC),
                knowledge_source="test",
                source="test",
            )
        )
        session.flush()
        return security

    def test_an_open_interval_returns_everything(self, db_session: Session) -> None:
        security = self._bound_series(db_session, None)
        bars = price_series(db_session, security.security_id, as_of=AS_OF)
        assert len(bars) == 3
        assert adjudicated_bound(db_session, security.security_id) is None

    def test_a_closed_interval_excludes_bars_beyond_it(self, db_session: Session) -> None:
        security = self._bound_series(db_session, dt.date(2000, 1, 5))
        bars = price_series(db_session, security.security_id, as_of=AS_OF)
        assert [b.session_date for b in bars] == [dt.date(2000, 1, 3), dt.date(2000, 1, 4)]

    def test_the_bound_is_EXCLUSIVE(self, db_session: Session) -> None:
        """Half-open ``[valid_from, valid_to)``, as every interval table here declares.

        ``ck_alias_interval`` and its four siblings require
        ``valid_to > valid_from``, and ``resolve_security`` -- which decides
        what may enter the corpus at all -- tests ``valid_to > on``. This read
        the same column as inclusive, so a bar on the boundary was admitted
        here and rejected there. The invariant is one column, one meaning.
        """
        security = self._bound_series(db_session, dt.date(2000, 1, 4))
        bars = price_series(db_session, security.security_id, as_of=AS_OF)
        assert [b.session_date for b in bars] == [dt.date(2000, 1, 3)]

    def test_include_disputed_returns_the_excluded_bars(self, db_session: Session) -> None:
        """An auditor must be able to see what the cut removed."""
        security = self._bound_series(db_session, dt.date(2000, 1, 5))
        bars = price_series(db_session, security.security_id, as_of=AS_OF, include_disputed=True)
        assert len(bars) == 3

    def test_a_vendor_symbol_alias_does_not_bound_the_series(self, db_session: Session) -> None:
        """Only the evidence-backed ``ticker`` kind carries an adjudicated claim.

        A vendor span is the vendor's own bookkeeping about a symbol; treating
        it as a boundary would let the vendor decide what our corpus believes.
        """
        security = _security(db_session)
        _bar(db_session, security.security_id, dt.date(2018, 1, 3), Decimal("10"))
        db_session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="vendor_symbol",
                alias_value="ZZZZ",
                valid_from=dt.date(1990, 1, 1),
                valid_to=dt.date(2000, 1, 4),
                knowledge_time=dt.datetime(2026, 1, 1, tzinfo=UTC),
                knowledge_source="eodhd_symbol_span",
                source="test",
            )
        )
        db_session.flush()
        assert adjudicated_bound(db_session, security.security_id) is None
        assert len(price_series(db_session, security.security_id, as_of=AS_OF)) == 1

    def test_the_earliest_close_wins_across_two_aliases(self, db_session: Session) -> None:
        security = self._bound_series(db_session, dt.date(2018, 1, 4))
        db_session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="ticker",
                alias_value="YYYY",
                valid_from=dt.date(1990, 1, 1),
                valid_to=dt.date(2000, 1, 4),
                knowledge_time=dt.datetime(2026, 1, 1, tzinfo=UTC),
                knowledge_source="test",
                source="test",
            )
        )
        db_session.flush()
        assert adjudicated_bound(db_session, security.security_id) == dt.date(2000, 1, 4)
        # Bars are 2000-01-03, 2000-01-04 and 2018-01-03; the earlier close is
        # exclusive of 2000-01-04, so only the first survives.
        assert len(price_series(db_session, security.security_id, as_of=AS_OF)) == 1

    def test_a_split_after_the_bound_does_not_adjust_the_kept_bars(
        self, db_session: Session
    ) -> None:
        """The half-fix that left ``ASCX``'s $18.00 reading as three trillion.

        Corporate actions were fetched under the same symbol as the prices, so a
        series holding two companies holds two companies' splits. Cutting only
        the bars left the successor's six compounding reverse splits still
        dividing the registrant's prices.
        """
        security = self._bound_series(db_session, dt.date(2000, 1, 5))
        _split(db_session, security.security_id, dt.date(2006, 10, 13), Decimal("0.01"))
        db_session.flush()
        assert known_splits(db_session, security.security_id, as_of=AS_OF) == []
        bars = price_series(db_session, security.security_id, as_of=AS_OF)
        assert all(bar.split_factor == 1 for bar in bars)
        assert bars[0].close == bars[0].raw_close

    def test_include_disputed_restores_the_successors_splits_too(self, db_session: Session) -> None:
        security = self._bound_series(db_session, dt.date(2000, 1, 5))
        _split(db_session, security.security_id, dt.date(2006, 10, 13), Decimal("0.01"))
        db_session.flush()
        assert (
            len(known_splits(db_session, security.security_id, as_of=AS_OF, include_disputed=True))
            == 1
        )
        bars = price_series(db_session, security.security_id, as_of=AS_OF, include_disputed=True)
        assert bars[0].split_factor == Decimal("0.01")

    def test_bars_before_the_interval_opens_are_excluded_too(self, db_session: Session) -> None:
        """A reused ticker has two neighbours, and the corpus met both.

        ``AAAB`` arrived carrying bars from 1999 to 2003 under a registrant that
        did not exist until 2011: the end bound stops the successor and does
        nothing about the predecessor.
        """
        security = self._bound_series(db_session, dt.date(2018, 1, 4))
        alias = db_session.scalars(
            select(SymbolAlias).where(SymbolAlias.security_id == security.security_id)
        ).one()
        alias.valid_from = dt.date(2000, 1, 4)
        db_session.flush()

        bars = price_series(db_session, security.security_id, as_of=AS_OF)
        assert [b.session_date for b in bars] == [dt.date(2000, 1, 4), dt.date(2018, 1, 3)]

    def test_the_window_is_the_narrowest_of_several_aliases(self, db_session: Session) -> None:
        security = self._bound_series(db_session, dt.date(2018, 1, 4))
        db_session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="ticker",
                alias_value="YYYY",
                valid_from=dt.date(2000, 1, 4),
                valid_to=dt.date(2001, 1, 1),
                knowledge_time=dt.datetime(2026, 1, 1, tzinfo=UTC),
                knowledge_source="test",
                source="test",
            )
        )
        db_session.flush()
        assert adjudicated_window(db_session, security.security_id) == (
            dt.date(2000, 1, 4),
            dt.date(2001, 1, 1),
        )

    def test_nothing_outside_the_window_is_deleted(self, db_session: Session) -> None:
        """The bars remain; the interval says which belong.

        Deleting them would destroy the record of the defect, which is the
        decision the splice adjudication already took and recorded.
        """
        security = self._bound_series(db_session, dt.date(2000, 1, 4))
        assert len(price_series(db_session, security.security_id, as_of=AS_OF)) == 1
        assert (
            len(price_series(db_session, security.security_id, as_of=AS_OF, include_disputed=True))
            == 3
        )
