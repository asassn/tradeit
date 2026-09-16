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
from tradeit.research01.series import (
    PrintRow,
    SplitEvidence,
    admit_prints,
    split_evidence,
    split_reading,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SecuritySplitPriceVerdict,
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
        """The invariant is that two splits multiply, not the date they sit on.

        This bar was dated 2020-01-01 -- New Year's Day, when no US market
        opens -- and the series read empty once non-session bars began being
        excluded. Moved to the first actual session of 2020 rather than
        weakening the filter, because a bar on a closed day is the defect the
        filter exists for and a fixture is not evidence that one should be read.
        """
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2020, 1, 2), Decimal("100"))
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


class TestASplitAlreadyInsideRaw:
    """At 667 real splits the vendor's ``raw`` close was already split-adjusted
    and both read paths adjusted it again; Sharadar's printed close confirmed
    33 of 33 sampled. Each case stores three sessions of ``raw`` and ``total``
    either side of a recorded split on SPLIT_DAY and asks what each basis does.
    """

    BEFORE = (dt.date(2020, 8, 26), dt.date(2020, 8, 27), dt.date(2020, 8, 28))
    AFTER = (SPLIT_DAY, dt.date(2020, 9, 1), dt.date(2020, 9, 2))
    AS_OF = dt.datetime(2021, 1, 1, tzinfo=UTC)

    def _series(
        self,
        session: Session,
        *,
        raw: tuple[str, str],
        total: tuple[str, str] = ("50", "50"),
        ratio: str = "2",
    ) -> int:
        sid = _security(session).security_id
        for days, index in ((self.BEFORE, 0), (self.AFTER, 1)):
            for day in days:
                _bar(session, sid, day, Decimal(raw[index]))
                _bar(session, sid, day, Decimal(total[index]), basis="total")
        _split(session, sid, SPLIT_DAY, Decimal(ratio))
        session.flush()
        return sid

    def _evidence(self, session: Session, sid: int, ratio: str = "2") -> SplitEvidence:
        return split_evidence(session, sid, SPLIT_DAY, Decimal(ratio))

    def test_a_genuine_print_is_still_adjusted(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("100", "50"))
        assert self._evidence(db_session, sid) is SplitEvidence.IN_RAW
        first = price_series(db_session, sid, as_of=self.AS_OF)[0]
        assert first.close == Decimal("50")
        assert first.split_factor == 2

    def test_an_already_adjusted_raw_is_not_adjusted_twice(self, db_session: Session) -> None:
        """The bug: 50 read as 25, a halving that never happened."""
        sid = self._series(db_session, raw=("50", "50"))
        assert self._evidence(db_session, sid) is SplitEvidence.ALREADY_ADJUSTED
        first = price_series(db_session, sid, as_of=self.AS_OF)[0]
        assert first.close == Decimal("50")
        assert first.split_factor == 1
        assert known_splits(db_session, sid, as_of=self.AS_OF) == []

    def test_an_audit_still_sees_every_recorded_split(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("50", "50"))
        assert len(known_splits(db_session, sid, as_of=self.AS_OF, include_disputed=True)) == 1

    def test_a_flat_raw_against_a_moving_total_is_contradicted(self, db_session: Session) -> None:
        """BNCN's shape, 2005-11-16: ``raw`` flat, ``total`` up by the ratio.
        Sharadar found this cell 9 prints to 23 adjusted, so neither reading
        may be acted on."""
        sid = self._series(db_session, raw=("50", "50"), total=("25", "50"))
        assert self._evidence(db_session, sid) is SplitEvidence.CONTRADICTED

    def test_both_bases_jumping_is_contradicted(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("100", "50"), total=("100", "50"))
        assert self._evidence(db_session, sid) is SplitEvidence.CONTRADICTED

    def test_a_contradicted_split_withholds_the_prints_before_it(self, db_session: Session) -> None:
        """A 1-for-2 on record, a 2-for-1 in the prices: no factor can be stated,
        so the earlier prints are not served and the later ones are."""
        sid = self._series(db_session, raw=("100", "50"), ratio="0.5")
        assert self._evidence(db_session, sid, "0.5") is SplitEvidence.CONTRADICTED
        applied, floor = split_reading(db_session, sid, as_of=self.AS_OF)
        assert applied == [] and floor == SPLIT_DAY
        served = [b.session_date for b in price_series(db_session, sid, as_of=self.AS_OF)]
        assert served == list(self.AFTER)
        audited = price_series(db_session, sid, as_of=self.AS_OF, include_disputed=True)
        assert len(audited) == 6

    def test_too_few_sessions_is_no_evidence_and_applied_as_recorded(
        self, db_session: Session
    ) -> None:
        """The ordinary state for a short fixture, and for 1,275 real splits with
        prints missing on one side: nothing changes."""
        sid = _security(db_session).security_id
        _bar(db_session, sid, self.BEFORE[-1], Decimal("100"))
        _split(db_session, sid, SPLIT_DAY, Decimal(2))
        db_session.flush()
        assert self._evidence(db_session, sid) is SplitEvidence.NO_EVIDENCE
        assert price_series(db_session, sid, as_of=self.AS_OF)[0].close == Decimal("50")

    def _verdict(self, session: Session, sid: int, verdict: str) -> None:
        session.add(
            SecuritySplitPriceVerdict(
                security_id=sid,
                ex_date=SPLIT_DAY,
                verdict=verdict,
                decided_by="sharadar",
                compared_session=self.BEFORE[-1],
                stored_raw_close=Decimal("50"),
                vendor_printed_close=Decimal("100"),
                vendor_adjusted_close=Decimal("50"),
                knowledge_time=dt.datetime(2026, 9, 16, tzinfo=UTC),
                citation="sharadar stocks TEST 2020-08-28",
                source="test",
            )
        )
        session.flush()

    def test_a_recorded_verdict_settles_a_contradicted_split(self, db_session: Session) -> None:
        """A second vendor's printed close decides what the corpus's own prints
        cannot. 51 of the first 100 contradicted splits were settled this way."""
        sid = self._series(db_session, raw=("50", "50"), total=("25", "50"))
        assert self._evidence(db_session, sid) is SplitEvidence.CONTRADICTED
        self._verdict(db_session, sid, "already_adjusted")
        assert self._evidence(db_session, sid) is SplitEvidence.ALREADY_ADJUSTED
        applied, floor = split_reading(db_session, sid, as_of=self.AS_OF)
        assert applied == [] and floor is None
        assert len(price_series(db_session, sid, as_of=self.AS_OF)) == 6

    def test_a_verdict_of_in_raw_restores_the_adjustment(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("50", "50"), total=("25", "50"))
        self._verdict(db_session, sid, "in_raw")
        assert self._evidence(db_session, sid) is SplitEvidence.IN_RAW
        applied, floor = split_reading(db_session, sid, as_of=self.AS_OF)
        assert [s.ex_date for s in applied] == [SPLIT_DAY] and floor is None

    def test_a_verdict_never_overrides_the_prints_themselves(self, db_session: Session) -> None:
        """Consulted only where the prints contradict. The two acting shapes were
        right at 69 of 69 sampled splits, so a stored row cannot overturn them."""
        sid = self._series(db_session, raw=("100", "50"))
        self._verdict(db_session, sid, "already_adjusted")
        assert self._evidence(db_session, sid) is SplitEvidence.IN_RAW

    def test_the_latest_verdict_wins(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("50", "50"), total=("25", "50"))
        self._verdict(db_session, sid, "in_raw")
        db_session.add(
            SecuritySplitPriceVerdict(
                security_id=sid,
                ex_date=SPLIT_DAY,
                verdict="already_adjusted",
                decided_by="later-source",
                compared_session=self.BEFORE[-1],
                stored_raw_close=Decimal("50"),
                vendor_printed_close=Decimal("100"),
                vendor_adjusted_close=Decimal("50"),
                knowledge_time=dt.datetime(2026, 10, 1, tzinfo=UTC),
                citation="a later source disagreeing",
                source="test",
            )
        )
        db_session.flush()
        assert self._evidence(db_session, sid) is SplitEvidence.ALREADY_ADJUSTED

    def test_a_split_too_small_to_test_is_applied_as_recorded(self, db_session: Session) -> None:
        sid = self._series(db_session, raw=("50", "50"), ratio="1.03")
        assert self._evidence(db_session, sid, "1.03") is SplitEvidence.TOO_SMALL


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


class TestNonSessionBars:
    """3,930 bars arrived before the importer learned to refuse them.

    They are excluded on read and left in the table: the corpus bounds what it
    will read rather than destroying what it was sent, so the vendor's error
    stays visible to an audit and stops reaching a strategy.
    """

    def test_a_bar_on_a_market_holiday_is_not_returned(self, db_session: Session) -> None:
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2021, 7, 5), Decimal("10"))  # observed July 4th
        _bar(db_session, sid, dt.date(2021, 7, 6), Decimal("11"))
        db_session.flush()
        got = price_series(db_session, sid, as_of=dt.datetime(2022, 1, 1, tzinfo=UTC))
        assert [b.session_date for b in got] == [dt.date(2021, 7, 6)]

    def test_the_bar_is_excluded_not_deleted(self, db_session: Session) -> None:
        """The row must still be there for an audit to find."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2021, 7, 5), Decimal("10"))
        db_session.flush()
        price_series(db_session, sid, as_of=dt.datetime(2022, 1, 1, tzinfo=UTC))
        stored = db_session.scalars(
            select(SecurityPriceFact).where(SecurityPriceFact.security_id == sid)
        ).all()
        assert len(stored) == 1
        assert stored[0].session_date == dt.date(2021, 7, 5)

    def test_an_auditor_can_still_see_them(self, db_session: Session) -> None:
        """include_disputed already means "show me what was cut"; a caller
        auditing the cut needs the holiday bars in that view too."""
        security = _security(db_session)
        sid = security.security_id
        _bar(db_session, sid, dt.date(2021, 7, 5), Decimal("10"))
        db_session.flush()
        got = price_series(
            db_session, sid, as_of=dt.datetime(2022, 1, 1, tzinfo=UTC), include_disputed=True
        )
        assert [b.session_date for b in got] == [dt.date(2021, 7, 5)]

    def test_ordinary_sessions_are_untouched(self, db_session: Session) -> None:
        security = _security(db_session)
        sid = security.security_id
        for day in (dt.date(2021, 7, 6), dt.date(2021, 7, 7), dt.date(2021, 7, 8)):
            _bar(db_session, sid, day, Decimal("10"))
        db_session.flush()
        got = price_series(db_session, sid, as_of=dt.datetime(2022, 1, 1, tzinfo=UTC))
        assert len(got) == 3


# -- prints nobody traded -----------------------------------------------------

D = Decimal
#: Consecutive NYSE sessions; 2021-07-05 was the Independence Day holiday.
JULY = [
    dt.date(2021, 7, 6),
    dt.date(2021, 7, 7),
    dt.date(2021, 7, 8),
    dt.date(2021, 7, 9),
    dt.date(2021, 7, 12),
    dt.date(2021, 7, 13),
    dt.date(2021, 7, 14),
]


def _row(day: dt.date, close: str, volume: str, *, low: str | None = None) -> PrintRow:
    c = D(close)
    return (day, c, c, D(low) if low is not None else c, c, D(volume))


def _print(
    session: Session, sid: int, day: dt.date, close: str, volume: str, *, low: str | None = None
) -> None:
    moment = dt.datetime.combine(day, dt.time(20), tzinfo=UTC)
    c = D(close)
    session.add(
        SecurityPriceFact(
            security_id=sid,
            session_date=day,
            adjustment_basis="raw",
            event_time=moment,
            knowledge_time=moment,
            knowledge_time_basis="session_close",
            knowledge_source="test",
            open=c,
            high=c,
            low=D(low) if low is not None else c,
            close=c,
            volume=D(volume),
            volume_adjusted=False,
            source="test",
        )
    )


class TestAdmitPrints:
    """A zero-volume bar is served only as an exact copy of the last traded close.

    The rule was chosen against measurement, and each test below is one of the
    shapes that measurement found. Refusing every zero-volume bar was rejected
    because 637,214 of 640,333 zero-volume runs are followed by trading again,
    and the backtester would have read each one as a delisting.
    """

    def test_bars_that_traded_are_all_kept(self) -> None:
        rows = [_row(JULY[0], "10", "500"), _row(JULY[1], "11", "300")]
        assert admit_prints(rows, []) == (rows, 0)

    def test_a_carried_close_is_kept(self) -> None:
        """The 86.5% case: a quiet day on a thin stock, last price carried."""
        rows = [_row(JULY[0], "10", "500"), _row(JULY[1], "10", "0")]
        kept, refused = admit_prints(rows, [])
        assert kept == rows and refused == 0

    def test_a_long_quiet_run_is_kept_so_it_cannot_look_delisted(self) -> None:
        """The backtester retires a holding after ten sessions of silence."""
        start = dt.date(2021, 1, 4)
        rows = [_row(start, "10", "500")] + [
            _row(start + dt.timedelta(days=i), "10", "0") for i in range(1, 40)
        ]
        kept, refused = admit_prints(rows, [])
        assert len(kept) == 40 and refused == 0

    def test_a_new_price_on_no_volume_is_refused(self) -> None:
        rows = [_row(JULY[0], "10", "500"), _row(JULY[1], "12", "0")]
        kept, refused = admit_prints(rows, [])
        assert [r[0] for r in kept] == [JULY[0]] and refused == 1

    def test_an_untraded_intraday_range_is_refused(self) -> None:
        """Close equal to the anchor is not enough: a low nobody dealt at would
        trigger a stop that nothing justified."""
        rows = [_row(JULY[0], "10", "500"), _row(JULY[1], "10", "0", low="0.0001")]
        kept, refused = admit_prints(rows, [])
        assert [r[0] for r in kept] == [JULY[0]] and refused == 1

    def test_zero_volume_before_any_trade_is_refused(self) -> None:
        """No traded close exists to be carried."""
        rows = [_row(JULY[0], "10", "0"), _row(JULY[1], "10", "500")]
        kept, refused = admit_prints(rows, [])
        assert [r[0] for r in kept] == [JULY[1]] and refused == 1

    def test_security_4565_is_refused_in_its_entirety(self) -> None:
        """Every bar zero volume, alternating a sentinel and nonsense. Never traded."""
        rows = [
            _row(JULY[0], "0.0001", "0"),
            _row(JULY[1], "92000", "0"),
            _row(JULY[2], "0.0001", "0"),
            _row(JULY[3], "96000", "0"),
        ]
        assert admit_prints(rows, []) == ([], 4)

    def test_a_spike_is_refused_and_the_carry_after_it_is_judged_on_the_trade(self) -> None:
        """The anchor is the last TRADED close, never the last served bar."""
        rows = [
            _row(JULY[0], "10", "500"),
            _row(JULY[1], "0.0001", "0"),  # sentinel
            _row(JULY[2], "10", "0"),  # carries the trade, not the sentinel
            _row(JULY[3], "0.0001", "0"),  # carries the sentinel: refused
        ]
        kept, refused = admit_prints(rows, [])
        assert [r[0] for r in kept] == [JULY[0], JULY[2]] and refused == 2

    def test_a_carried_close_does_not_cross_a_split(self) -> None:
        """A raw pre-split close repeated after a 2-for-1 would double the mark."""
        rows = [
            _row(JULY[0], "100", "500"),
            _row(JULY[1], "100", "0"),  # before the split: a real carry
            _row(JULY[2], "100", "0"),  # the ex-date: the pre-split price, stale
            _row(JULY[3], "50", "800"),  # trading resumes post-split
            _row(JULY[4], "50", "0"),  # a real carry again
        ]
        kept, refused = admit_prints(rows, [JULY[2]])
        assert [r[0] for r in kept] == [JULY[0], JULY[1], JULY[3], JULY[4]]
        assert refused == 1

    def test_bars_out_of_order_are_an_error_not_a_guess(self) -> None:
        rows = [_row(JULY[1], "10", "500"), _row(JULY[0], "10", "0")]
        with pytest.raises(ValueError, match="session order"):
            admit_prints(rows, [])


class TestPriceSeriesRefusesWhatNobodyTraded:
    def test_a_zero_priced_bar_is_not_served(self, db_session: Session) -> None:
        """Existing behaviour that had no test of its own until now."""
        sid = _security(db_session).security_id
        _print(db_session, sid, JULY[0], "10", "500")
        _print(db_session, sid, JULY[1], "0", "110")
        db_session.flush()
        bars = price_series(db_session, sid, as_of=AS_OF)
        assert [b.session_date for b in bars] == [JULY[0]]

    def test_an_untraded_price_is_not_served_and_a_carried_one_is(
        self, db_session: Session
    ) -> None:
        sid = _security(db_session).security_id
        _print(db_session, sid, JULY[0], "10", "500")
        _print(db_session, sid, JULY[1], "92000", "0")
        _print(db_session, sid, JULY[2], "10", "0")
        db_session.flush()
        bars = price_series(db_session, sid, as_of=AS_OF)
        assert [b.session_date for b in bars] == [JULY[0], JULY[2]]
        # Served, but still visibly untraded: a caller transacting must check.
        assert bars[1].volume == 0

    def test_an_auditor_sees_every_bar(self, db_session: Session) -> None:
        sid = _security(db_session).security_id
        _print(db_session, sid, JULY[0], "10", "500")
        _print(db_session, sid, JULY[1], "92000", "0")
        _print(db_session, sid, JULY[2], "0", "110")
        db_session.flush()
        bars = price_series(db_session, sid, as_of=AS_OF, include_disputed=True)
        assert [b.session_date for b in bars] == JULY[:3]

    def test_the_split_reset_uses_the_splits_the_series_knows(self, db_session: Session) -> None:
        sid = _security(db_session).security_id
        _print(db_session, sid, JULY[0], "100", "500")
        _print(db_session, sid, JULY[1], "100", "0")
        _split(db_session, sid, JULY[1], Decimal("2"))
        _print(db_session, sid, JULY[2], "50", "800")
        db_session.flush()
        bars = price_series(db_session, sid, as_of=AS_OF)
        assert [b.session_date for b in bars] == [JULY[0], JULY[2]]
