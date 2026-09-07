"""The research-01 importer: the point-in-time policy, and the splice property.

The property that matters most is stated as an inversion, because reading it the
natural way gets it backwards:

> **A continuous input series across a known identity break must produce a
> DISCONTINUOUS output.** A continuous result is the failure. It looks like
> complete coverage and is two companies spliced into one price history.

That is why ``GM`` is in the EODHD sample, and the synthetic two-issuer ticker
below pins the property before any real file arrives.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    Delivery,
    KnowledgeTimeBasis,
    RejectReason,
    Resolution,
    VendorBar,
    import_price_bars,
    knowledge_time_for,
    resolve_security,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityPriceFact,
    SymbolAlias,
)

UTC = dt.UTC
#: A real NYSE session, so the session-close branch is exercised on a real day.
SESSION = dt.date(2011, 4, 4)
DELIVERED = dt.datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
KT = dt.datetime(2026, 1, 1, tzinfo=UTC)


def _issuer(session: Session, name: str, cik: str) -> Issuer:
    issuer = Issuer(display_name=name, source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value=cik,
            value_normalized=str(int(cik)),
            role="primary",
            citation=f"test fixture {cik}",
            source="test",
        )
    )
    session.flush()
    return issuer


def _security(session: Session, issuer: Issuer) -> Security:
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return security


def _alias(
    session: Session,
    security: Security,
    ticker: str,
    valid_from: dt.date,
    valid_to: dt.date | None,
    knowledge_time: dt.datetime = KT,
) -> None:
    session.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=valid_from,
            valid_to=valid_to,
            knowledge_time=knowledge_time,
            knowledge_source="test",
            source="test",
        )
    )
    session.flush()


def _bar(ticker: str, day: dt.date, basis: str = "raw") -> VendorBar:
    return VendorBar(
        ticker=ticker,
        session_date=day,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("1000"),
        adjustment_basis=basis,
    )


class TestKnowledgeTimePolicy:
    """A backfilled fact's usable-from instant is not its delivery instant."""

    def test_a_raw_bar_is_knowable_at_the_session_close(self) -> None:
        kt, basis = knowledge_time_for(
            adjustment_basis="raw", session_date=SESSION, delivered_at=DELIVERED
        )
        assert basis is KnowledgeTimeBasis.SESSION_CLOSE
        # 1999 knowledge for a 2026 delivery: the print was public then.
        assert kt.date() == SESSION
        assert kt < DELIVERED

    @pytest.mark.parametrize("basis_name", ["split", "total"])
    def test_a_vendor_adjusted_bar_is_knowable_only_at_delivery(self, basis_name: str) -> None:
        """Its value embeds every action up to the moment it was computed.

        Not "adjusted prices are never historically knowable" -- a series
        adjusted as of 2005 from splits public by 2005 is point-in-time valid.
        What is invalid is a *vendor-delivered* one, whose adjustment epoch is
        the delivery date.
        """
        kt, basis = knowledge_time_for(
            adjustment_basis=basis_name, session_date=SESSION, delivered_at=DELIVERED
        )
        assert basis is KnowledgeTimeBasis.COMPUTED_AT_DELIVERY
        assert kt == DELIVERED

    def test_an_adjusted_bar_ignores_a_vendor_dissemination_stamp(self) -> None:
        """That stamp describes the print, not the adjustment. Accepting it
        would backdate a value computed today, which is the whole failure."""
        kt, basis = knowledge_time_for(
            adjustment_basis="split",
            session_date=SESSION,
            delivered_at=DELIVERED,
            disseminated_at=dt.datetime(2011, 4, 4, 20, tzinfo=UTC),
        )
        assert basis is KnowledgeTimeBasis.COMPUTED_AT_DELIVERY
        assert kt == DELIVERED

    def test_a_stated_dissemination_instant_wins_for_a_raw_bar(self) -> None:
        stamped = dt.datetime(2011, 4, 4, 20, 15, tzinfo=UTC)
        kt, basis = knowledge_time_for(
            adjustment_basis="raw",
            session_date=SESSION,
            delivered_at=DELIVERED,
            disseminated_at=stamped,
        )
        assert basis is KnowledgeTimeBasis.SOURCE_DISSEMINATED
        assert kt == stamped

    def test_a_raw_bar_on_a_non_session_falls_back_to_delivery(self) -> None:
        """**The proof that the basis column is not derivable from
        adjustment_basis.** This row is `raw`, exactly like an ordinary bar, and
        its knowledge_time is the delivery instant. Only the basis separates
        them, so nothing may 'simplify' the column away."""
        boxing_day_sunday = dt.date(2010, 12, 26)
        kt, basis = knowledge_time_for(
            adjustment_basis="raw", session_date=boxing_day_sunday, delivered_at=DELIVERED
        )
        assert basis is KnowledgeTimeBasis.DELIVERY_UNESTABLISHED
        assert kt == DELIVERED

    def test_the_fallback_is_invisible_to_an_earlier_as_of(self) -> None:
        """Fail closed: an unestablished bar cannot license a 2011 decision."""
        kt, _ = knowledge_time_for(
            adjustment_basis="raw", session_date=dt.date(2010, 12, 26), delivered_at=DELIVERED
        )
        assert kt > dt.datetime(2011, 1, 1, tzinfo=UTC)


class TestResolution:
    def test_an_unknown_ticker_resolves_to_nothing_and_creates_nothing(
        self, db_session: Session
    ) -> None:
        security_id, resolution = resolve_security(db_session, ticker="NOPE", on=SESSION)
        assert security_id is None
        assert resolution is Resolution.UNRESOLVED_NO_ALIAS
        assert db_session.scalars(select(Security)).all() == []

    def test_the_importer_never_mints_a_security(self, db_session: Session) -> None:
        result = import_price_bars(
            db_session, [_bar("GHOST", SESSION)], Delivery("eodhd", DELIVERED)
        )
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NO_ALIAS
        assert db_session.scalars(select(Security)).all() == []
        assert db_session.scalars(select(SymbolAlias)).all() == []
        assert result.summary()["unresolved_subjects"] == ["GHOST"]

    def test_two_securities_claiming_one_ticker_at_one_instant_is_ambiguous(
        self, db_session: Session
    ) -> None:
        """Load-bearing on SQLite, where ``EXCLUDE USING gist`` cannot exist.

        On PostgreSQL ``ex_alias_no_overlap`` refuses these rows outright; here
        nothing does, so the importer is the only thing standing between an
        overlapping pair and a silently-picked winner.
        """
        a = _security(db_session, _issuer(db_session, "A", "1"))
        b = _security(db_session, _issuer(db_session, "B", "2"))
        _alias(db_session, a, "DUP", dt.date(2000, 1, 1), None)
        _alias(db_session, b, "DUP", dt.date(2000, 1, 1), None)

        security_id, resolution = resolve_security(db_session, ticker="DUP", on=SESSION)
        assert security_id is None
        assert resolution is Resolution.UNRESOLVED_AMBIGUOUS

    def test_a_later_revision_supersedes_rather_than_competes(self, db_session: Session) -> None:
        """Overlapping intervals at *different* knowledge_times are a correction."""
        a = _security(db_session, _issuer(db_session, "A", "1"))
        b = _security(db_session, _issuer(db_session, "B", "2"))
        _alias(db_session, a, "REV", dt.date(2000, 1, 1), None, dt.datetime(2020, 1, 1, tzinfo=UTC))
        _alias(db_session, b, "REV", dt.date(2000, 1, 1), None, dt.datetime(2021, 1, 1, tzinfo=UTC))

        security_id, resolution = resolve_security(db_session, ticker="REV", on=SESSION)
        assert resolution is Resolution.RESOLVED
        assert security_id == b.security_id


class TestRoundTrip:
    """The milestone gate: what goes in comes back out, and twice is once."""

    def test_a_bar_round_trips_with_its_values_intact(self, db_session: Session) -> None:
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)

        result = import_price_bars(
            db_session, [_bar("ACME", SESSION)], Delivery("eodhd", DELIVERED, "acme.csv")
        )
        assert result.landed == 1

        stored = db_session.scalars(select(SecurityPriceFact)).one()
        assert stored.security_id == security.security_id
        assert stored.session_date == SESSION
        assert (stored.open, stored.high, stored.low, stored.close) == (
            Decimal("10"),
            Decimal("11"),
            Decimal("9"),
            Decimal("10.5"),
        )
        assert stored.adjustment_basis == "raw"
        assert stored.knowledge_time_basis == str(KnowledgeTimeBasis.SESSION_CLOSE)
        assert stored.knowledge_source == "eodhd"
        assert stored.knowledge_time >= stored.event_time

    def test_re_importing_the_same_delivery_changes_nothing(self, db_session: Session) -> None:
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        delivery = Delivery("eodhd", DELIVERED, "acme.csv")

        first = import_price_bars(db_session, [_bar("ACME", SESSION)], delivery)
        second = import_price_bars(db_session, [_bar("ACME", SESSION)], delivery)

        assert (first.landed, second.landed) == (1, 0)
        assert second.rejected[0].reason is RejectReason.DUPLICATE
        assert len(db_session.scalars(select(SecurityPriceFact)).all()) == 1

    def test_a_bar_dated_on_a_market_holiday_is_refused(self, db_session: Session) -> None:
        """3,930 bars in the corpus fall on days the US market was closed --
        July 4th, Thanksgiving, the 2025-01-09 day of mourning. The vendor emits
        them; a day with no trading has no price."""
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        delivery = Delivery("eodhd", DELIVERED, "acme.csv")

        result = import_price_bars(db_session, [_bar("ACME", dt.date(2025, 7, 4))], delivery)

        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NON_SESSION_DATE
        assert "not a trading session" in result.rejected[0].detail

    def test_a_holiday_bar_is_refused_rather_than_moved_to_a_session(
        self, db_session: Session
    ) -> None:
        """There is no session to move it to, and inventing one would put a
        fabricated observation into a corpus whose claim is that it does not
        fabricate."""
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        delivery = Delivery("eodhd", DELIVERED, "acme.csv")

        import_price_bars(db_session, [_bar("ACME", dt.date(2025, 12, 25))], delivery)

        assert db_session.scalars(select(SecurityPriceFact)).all() == []

    def test_an_ordinary_session_still_lands(self, db_session: Session) -> None:
        """The guard must not reject the trading days it exists to protect."""
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        delivery = Delivery("eodhd", DELIVERED, "acme.csv")

        result = import_price_bars(db_session, [_bar("ACME", dt.date(2025, 7, 7))], delivery)

        assert result.landed == 1

    def test_the_same_bar_twice_in_one_delivery_lands_once(self, db_session: Session) -> None:
        """The within-delivery duplicate, which the cross-delivery test above
        does not reach. The per-bar query caught it by seeing the flushed row;
        the cached key set has to add each insert as it goes, or a vendor that
        repeats a row would double it."""
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        delivery = Delivery("eodhd", DELIVERED, "acme.csv")

        result = import_price_bars(
            db_session, [_bar("ACME", SESSION), _bar("ACME", SESSION)], delivery
        )

        assert result.landed == 1
        assert [r.reason for r in result.rejected] == [RejectReason.DUPLICATE]
        assert len(db_session.scalars(select(SecurityPriceFact)).all()) == 1

    def test_the_three_bases_land_as_three_rows_with_different_knowledge_times(
        self, db_session: Session
    ) -> None:
        security = _security(db_session, _issuer(db_session, "ACME", "1"))
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)

        import_price_bars(
            db_session,
            [_bar("ACME", SESSION, b) for b in ("raw", "split", "total")],
            Delivery("eodhd", DELIVERED),
        )
        rows = db_session.scalars(select(SecurityPriceFact)).all()
        by_basis = {r.adjustment_basis: r for r in rows}
        assert set(by_basis) == {"raw", "split", "total"}
        # The raw print is usable in 2011; the adjusted ones only from delivery.
        assert by_basis["raw"].knowledge_time < by_basis["split"].knowledge_time
        assert by_basis["split"].knowledge_time == by_basis["total"].knowledge_time == DELIVERED


class TestNoSpliceAcrossAnIdentityBreak:
    """The GM property, pinned on a synthetic two-issuer ticker.

    Two unrelated issuers hold ``TWO`` either side of a break, with a deliberate
    gap between the intervals. The vendor file is one continuous run of bars,
    exactly as a spliced vendor series would arrive.
    """

    BREAK_OUT = dt.date(2009, 6, 1)
    BREAK_IN = dt.date(2009, 9, 1)

    def _corpus(self, db_session: Session) -> tuple[Security, Security]:
        old = _security(db_session, _issuer(db_session, "OLD CO", "40730"))
        new = _security(db_session, _issuer(db_session, "NEW CO", "1467858"))
        _alias(db_session, old, "TWO", dt.date(2000, 1, 3), self.BREAK_OUT)
        _alias(db_session, new, "TWO", self.BREAK_IN, None)
        return old, new

    def _continuous_input(self) -> list[VendorBar]:
        """One unbroken run of sessions spanning the break, as a vendor sends it."""
        days = [
            dt.date(2009, 5, 1),
            dt.date(2009, 5, 29),
            dt.date(2009, 7, 1),  # inside the gap
            dt.date(2009, 8, 3),  # inside the gap
            dt.date(2009, 9, 1),
            dt.date(2009, 10, 1),
        ]
        return [_bar("TWO", d) for d in days]

    def test_a_continuous_input_produces_a_discontinuous_output(self, db_session: Session) -> None:
        """The inversion. A single continuous series here would be the defect."""
        old, new = self._corpus(db_session)
        result = import_price_bars(
            db_session, self._continuous_input(), Delivery("eodhd", DELIVERED)
        )

        assert result.securities_touched == {old.security_id, new.security_id}
        rows = db_session.scalars(select(SecurityPriceFact)).all()
        by_security: dict[int, list[dt.date]] = {}
        for r in rows:
            by_security.setdefault(r.security_id, []).append(r.session_date)

        # Two series, not one.
        assert len(by_security) == 2
        # And neither spans the break: every old-co bar precedes it, every
        # new-co bar follows it.
        assert max(by_security[old.security_id]) < self.BREAK_OUT
        assert min(by_security[new.security_id]) >= self.BREAK_IN

    def test_no_single_security_spans_the_break(self, db_session: Session) -> None:
        old, new = self._corpus(db_session)
        import_price_bars(db_session, self._continuous_input(), Delivery("eodhd", DELIVERED))
        for security_id in (old.security_id, new.security_id):
            days = db_session.scalars(
                select(SecurityPriceFact.session_date).where(
                    SecurityPriceFact.security_id == security_id
                )
            ).all()
            spans = any(d < self.BREAK_OUT for d in days) and any(d >= self.BREAK_IN for d in days)
            assert not spans, f"security {security_id} spans the identity break"

    def test_gap_bars_are_unresolved_not_nearest_neighboured(self, db_session: Session) -> None:
        """The bars between the intervals belong to neither issuer.

        Attaching them to the closer side is the tempting repair and is exactly
        the splice in miniature: it would manufacture an attribution the
        evidence does not support.
        """
        self._corpus(db_session)
        result = import_price_bars(
            db_session, self._continuous_input(), Delivery("eodhd", DELIVERED)
        )

        gap = {dt.date(2009, 7, 1), dt.date(2009, 8, 3)}
        rejected_days = {r.payload.session_date for r in result.unresolved}
        assert rejected_days == gap
        assert all(r.reason is RejectReason.NO_ALIAS for r in result.unresolved)

        landed_days = set(db_session.scalars(select(SecurityPriceFact.session_date)).all())
        assert landed_days.isdisjoint(gap)
        assert result.landed == 4


class TestAliasKindIsActuallyHonoured:
    """A parameter accepted and ignored is worse than one that does not exist.

    `alias_kind` was added to `import_price_bars` and threaded no further,
    because a reformat had collapsed the inner call onto one line before the
    edit landed. The importer accepted `alias_kind="vendor_symbol"`, reported no
    error, and resolved against `"ticker"` regardless -- 16,336 bars rejected
    with the caller believing it had asked for something else.

    It failed CLOSED, which was luck rather than design: had the default been
    the weaker identity, the same bug would have silently attributed bars using
    vendor spans while the caller believed it was using curated evidence.
    """

    def _security_with_vendor_alias(self, session: Session) -> Security:
        issuer = Issuer(display_name="VENDOR ONLY", source="test")
        session.add(issuer)
        session.flush()
        security = Security(
            issuer_id=issuer.issuer_id,
            security_type="common_stock",
            currency="USD",
            source="test",
        )
        session.add(security)
        session.flush()
        session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="vendor_symbol",
                alias_value="ACME",
                valid_from=dt.date(2000, 1, 1),
                valid_to=None,
                knowledge_time=KT,
                knowledge_source="eodhd_symbol_span",
                source="test",
            )
        )
        session.flush()
        return security

    def test_the_default_does_not_see_a_vendor_alias(self, db_session: Session) -> None:
        """Curated identity is the default and must not silently widen."""
        self._security_with_vendor_alias(db_session)
        result = import_price_bars(
            db_session, [_bar("ACME", SESSION)], Delivery("eodhd", DELIVERED)
        )
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.NO_ALIAS

    def test_asking_for_vendor_symbol_resolves(self, db_session: Session) -> None:
        """And the weaker identity works only when explicitly requested."""
        security = self._security_with_vendor_alias(db_session)
        result = import_price_bars(
            db_session,
            [_bar("ACME", SESSION)],
            Delivery("eodhd", DELIVERED),
            alias_kind="vendor_symbol",
        )
        assert result.landed == 1
        assert result.securities_touched == {security.security_id}

    def test_a_ticker_alias_is_not_reachable_by_asking_for_vendor_symbol(
        self, db_session: Session
    ) -> None:
        """The two kinds are separate claims, not fallbacks for each other."""
        issuer = Issuer(display_name="CURATED", source="test")
        db_session.add(issuer)
        db_session.flush()
        security = Security(
            issuer_id=issuer.issuer_id,
            security_type="common_stock",
            currency="USD",
            source="test",
        )
        db_session.add(security)
        db_session.flush()
        db_session.add(
            SymbolAlias(
                security_id=security.security_id,
                alias_kind="ticker",
                alias_value="ACME",
                valid_from=dt.date(2000, 1, 1),
                valid_to=None,
                knowledge_time=KT,
                knowledge_source="test",
                source="test",
            )
        )
        db_session.flush()

        result = import_price_bars(
            db_session,
            [_bar("ACME", SESSION)],
            Delivery("eodhd", DELIVERED),
            alias_kind="vendor_symbol",
        )
        assert result.landed == 0


class TestAnIncoherentBarIsRefusedNotRepaired:
    """`open=high=low=0` with a non-zero close, from EODHD, on a thin delisted name.

    Measured on the first real fetch of the dead cohort: it killed the job
    through `ck_security_price_high` after 65 symbols, losing the rest of a
    paid run. The constraints were right; raising was the wrong way to say so.
    """

    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        # Values chosen to trip ONE constraint each. The checks run in the
        # order the table declares them, so a high of 0 reports "below low"
        # rather than "below open" -- the first violation, not every one.
        [
            ("high", Decimal("10.2"), "below open"),
            ("low", Decimal("10.2"), "above open"),
            ("high", Decimal("0"), "below low"),
            ("volume", Decimal("-1"), "negative volume"),
        ],
    )
    def test_it_is_rejected_by_name_and_the_run_continues(
        self, db_session: Session, field: str, value: Decimal, fragment: str
    ) -> None:
        _alias(
            db_session,
            _security(db_session, _issuer(db_session, "Dead Co", "1")),
            "DEAD",
            dt.date(1990, 1, 1),
            None,
        )
        good = _bar("DEAD", dt.date(2011, 10, 10))
        bad = replace(_bar("DEAD", dt.date(2011, 10, 11)), **{field: value})
        result = import_price_bars(db_session, [bad, good], Delivery("eodhd", DELIVERED))

        assert result.landed == 1
        reasons = [(r.reason, r.detail) for r in result.rejected]
        assert any(r is RejectReason.INCOHERENT_BAR for r, _ in reasons)
        assert any(fragment in detail for _, detail in reasons)

    def test_the_high_is_never_raised_to_make_the_bar_fit(self, db_session: Session) -> None:
        """Setting the high to the close would invent a price nobody printed."""
        _alias(
            db_session,
            _security(db_session, _issuer(db_session, "Dead Co", "1")),
            "DEAD",
            dt.date(1990, 1, 1),
            None,
        )
        bad = replace(
            _bar("DEAD", dt.date(2011, 10, 11)),
            open=Decimal("0"),
            high=Decimal("0"),
            low=Decimal("0"),
            close=Decimal("0.85"),
        )
        assert import_price_bars(db_session, [bad], Delivery("eodhd", DELIVERED)).landed == 0
        assert db_session.scalars(select(SecurityPriceFact.id)).all() == []


# -- the cached resolver must not become a second rule ----------------------


def test_resolver_agrees_with_resolve_security(db_session: Session) -> None:
    """Two implementations of one rule that can disagree are two rules.

    ``AliasResolver`` hoists the query out of the per-bar loop; it must not
    change a single answer. Every alias shape that matters is exercised: a
    plain interval, an open-ended one, a reused ticker with disjoint
    intervals, an ambiguous overlap, and a revision that supersedes.
    """
    from tradeit.research01.importer import AliasResolver

    issuer = Issuer(display_name="X", source="test")
    db_session.add(issuer)
    db_session.flush()
    ids = []
    for _ in range(3):
        security = Security(issuer_id=issuer.issuer_id, security_type="common_stock", source="test")
        db_session.add(security)
        db_session.flush()
        ids.append(security.security_id)

    early = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    later = dt.datetime(2021, 1, 1, tzinfo=dt.UTC)
    rows = [
        # REUSE: disjoint intervals on one ticker
        ("REUSE", ids[0], dt.date(1995, 1, 1), dt.date(2000, 1, 1), early),
        ("REUSE", ids[1], dt.date(2005, 1, 1), dt.date(2010, 1, 1), early),
        # OPEN: no closing bound
        ("OPEN", ids[0], dt.date(1998, 1, 1), None, early),
        # AMBIG: two securities, same interval, same knowledge_time
        ("AMBIG", ids[0], dt.date(2000, 1, 1), dt.date(2010, 1, 1), early),
        ("AMBIG", ids[1], dt.date(2000, 1, 1), dt.date(2010, 1, 1), early),
        # REVISED: a later belief supersedes an earlier one
        ("REVISED", ids[0], dt.date(2000, 1, 1), dt.date(2010, 1, 1), early),
        ("REVISED", ids[2], dt.date(2000, 1, 1), dt.date(2010, 1, 1), later),
    ]
    for value, security_id, valid_from, valid_to, known in rows:
        db_session.add(
            SymbolAlias(
                security_id=security_id,
                alias_kind="ticker",
                alias_value=value,
                valid_from=valid_from,
                valid_to=valid_to,
                knowledge_time=known,
                knowledge_source="test",
                source="test",
            )
        )
    db_session.flush()

    resolver = AliasResolver(db_session)
    probes = [
        dt.date(1994, 6, 1),
        dt.date(1996, 1, 1),
        dt.date(1999, 12, 31),
        dt.date(2000, 1, 1),
        dt.date(2002, 1, 1),
        dt.date(2007, 1, 1),
        dt.date(2010, 1, 1),
        dt.date(2026, 1, 1),
    ]
    compared = 0
    for ticker in ("REUSE", "OPEN", "AMBIG", "REVISED", "ABSENT"):
        for on in probes:
            direct = resolve_security(db_session, ticker=ticker, on=on)
            cached = resolver.resolve(ticker, on)
            assert cached == direct, f"{ticker} on {on}: {cached} != {direct}"
            compared += 1
    assert compared == 40, "the comparison must actually run, not pass vacuously"


def test_resolver_reads_each_ticker_once(db_session: Session) -> None:
    """The whole point. If it re-queried per call it would be a slower
    resolve_security wearing a cache's name."""
    from tradeit.research01.importer import AliasResolver

    resolver = AliasResolver(db_session)
    resolver.resolve("ANY", dt.date(2004, 1, 1))
    resolver.resolve("ANY", dt.date(2005, 1, 1))
    assert list(resolver._cache) == ["ANY"]
