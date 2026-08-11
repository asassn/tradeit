"""Checks on the data itself, before any analysis is asked to trust it.

These run first and several of them are gates rather than diagnostics: if the
point-in-time check fails, no Phase 3/4/5 result computed over the snapshot
means anything, because the analysis was reading facts before they existed.

Each check reports *numbers*, not verdicts about the vendor. "1,204 sessions
missing across 85 instruments, concentrated in 2008" is actionable; "data
quality is poor" is not.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.calendar import get_calendar
from tradeit.data.packages.spec import DatasetKind
from tradeit.storage import tables as t
from tradeit.validation.checks import CheckResult, CheckStatus, Phase
from tradeit.validation.context import ValidationContext
from tradeit.validation.continuity import analyse_series
from tradeit.validation.survivorship import ObservedSeries, classify_roster, render_roster

#: A quarantine rate above this is reported as a failure rather than a warning.
#: Chosen as "one row in twenty", which is well beyond what a clean vendor
#: export produces and well below the rate a column-mapping mistake produces —
#: the two cases this number has to separate.
MAX_ACCEPTABLE_QUARANTINE_RATE = 0.05

#: Overnight moves beyond this multiple are examined for an unrecorded split.
#: A 2-for-1 split shows as a 0.5x gap; the threshold sits between "a big day"
#: and "the price series changed units".
SPLIT_SUSPECT_RATIO = Decimal("1.9")

#: How many flagged series get their gap runs enumerated. The analysis costs one
#: query per series, so it is bounded by the number of *findings*; a snapshot
#: where every series is flagged is a calendar bug, and the first few will say so
#: as clearly as all of them would.
MAX_GAP_ANALYSES = 10

#: How many quarantined rows are named individually. Below this the rows are the
#: finding; above it the stage tally is, and a wall of near-identical lines
#: hides the one that differs.
MAX_QUARANTINE_ROWS = 25


class _Check:
    """Shared construction for the checks in this module.

    A plain class rather than a dataclass: several checks carry a tunable of
    their own, and a frozen slotted base would make adding one an exercise in
    ``object.__setattr__``.
    """

    def __init__(
        self,
        *,
        check_id: str,
        title: str,
        phase: Phase,
        requires: tuple[DatasetKind, ...] = (),
    ) -> None:
        self.check_id = check_id
        self.title = title
        self.phase = phase
        self.requires = requires

    def result(self, **kwargs: object) -> CheckResult:
        return CheckResult(check_id=self.check_id, title=self.title, phase=self.phase, **kwargs)  # type: ignore[arg-type]


class RowsPresent(_Check):
    """Is there anything here at all?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.rows_present",
            title="The snapshot contains imported rows",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        bars = context.session.scalar(select(func.count()).select_from(t.OhlcvBar)) or 0
        instruments = (
            context.session.scalar(select(func.count(func.distinct(t.OhlcvBar.instrument_id)))) or 0
        )
        status = CheckStatus.PASS if bars else CheckStatus.FAIL
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=(
                f"{bars:,} bars over {instruments:,} instruments"
                if bars
                else "no bars were imported; every downstream check is vacuous"
            ),
            evidence={"bars": bars, "instruments": instruments},
        )


class QuarantineRate(_Check):
    """How much of the export the importer could not use."""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.quarantine_rate",
            title="Quarantined rows are a small fraction of the export",
            phase=Phase.DATA,
            requires=(),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        package = context.package
        read = package.rows_read or 0
        rate = (package.rows_quarantined / read) if read else 0.0
        quarantined = list(
            context.session.scalars(
                select(t.QuarantinedRow)
                .where(t.QuarantinedRow.package_id == package.id)
                .order_by(t.QuarantinedRow.source_file, t.QuarantinedRow.line_number)
            )
        )
        by_stage = Counter(row.stage for row in quarantined)
        if rate > MAX_ACCEPTABLE_QUARANTINE_RATE:
            status = CheckStatus.FAIL
        elif package.rows_quarantined:
            status = CheckStatus.WARN
        else:
            status = CheckStatus.PASS
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=(
                f"{package.rows_quarantined:,} of {read:,} rows quarantined ({rate:.2%})"
                + (
                    "; a rate this high is usually a column mapping rather than dirty data"
                    if status is CheckStatus.FAIL
                    else ""
                )
            ),
            evidence={
                "rows_read": read,
                "rows_quarantined": package.rows_quarantined,
                "rate": round(rate, 6),
                "by_stage": dict(sorted(by_stage.items())),
                "rows": [_quarantine_payload(row) for row in quarantined[:MAX_QUARANTINE_ROWS]],
            },
            detail=tuple(_render_quarantine(quarantined)),
        )


def _quarantine_payload(row: t.QuarantinedRow) -> dict[str, Any]:
    return {
        "dataset": row.dataset,
        "identifier": row.identifier,
        "stage": row.stage,
        "source_file": row.source_file,
        "line_number": row.line_number,
        "reason": row.reason,
        "payload": row.payload[:400],
    }


def _render_quarantine(rows: list[t.QuarantinedRow]) -> list[str]:
    """Name the rejected rows while there are few enough to read.

    A count answers "how bad?" and nothing else. Four quarantined bars in a
    294,000-row import is a rate of 0.001% and still worth a minute of a
    person's attention, because the four are either four genuinely
    self-contradictory vendor prints or the first sign of a systematic
    misreading — and the only way to tell is to look at them. The identifier,
    the file and the line number are all stored; printing them costs nothing
    and saves the reader writing a query.

    Above :data:`MAX_QUARANTINE_ROWS` the individual rows stop being the story
    and the stage tally is the better summary, so the list is capped and says
    it was.
    """
    if not rows:
        return []
    lines = [
        "  quarantined rows (kept verbatim; they are absent from every downstream count)",
        f"  {'dataset':<14} {'identifier':<12} {'stage':<13} {'source':<22} reason",
        f"  {'-' * 14} {'-' * 12} {'-' * 13} {'-' * 22} {'-' * 24}",
    ]
    for row in rows[:MAX_QUARANTINE_ROWS]:
        where = f"{row.source_file or '?'}:{row.line_number if row.line_number else '?'}"
        lines.append(
            f"  {row.dataset:<14} {(row.identifier or '?'):<12} {row.stage:<13} "
            f"{where[:22]:<22} {row.reason[:70]}"
        )
    if len(rows) > MAX_QUARANTINE_ROWS:
        lines.append(
            f"  ... and {len(rows) - MAX_QUARANTINE_ROWS:,} more; at this volume read the "
            "stage tally rather than the rows"
        )
    lines += [
        "",
        "  A quarantined bar is a session with no row, so these also appear as gaps in",
        "  data.session_continuity. They are the same rows, not two separate findings.",
    ]
    return lines


class AdjustmentDeclared(_Check):
    """Do we know what the price columns contain?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.adjustment_declared",
            title="The price series declares its adjustment policy",
            phase=Phase.DATA,
            requires=(),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        policy = context.package.adjustment_policy
        if policy == "unknown":
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.FAIL,
                summary=(
                    "the package does not say whether its prices are raw or adjusted. "
                    "Pattern geometry drawn through an unknown adjustment is not "
                    "interpretable, and no result over this snapshot is citable."
                ),
                evidence={"adjustment_policy": policy},
            )
        status = CheckStatus.PASS if policy == "raw_unadjusted" else CheckStatus.WARN
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=(
                f"prices are declared {policy}"
                + (
                    ". Structure is comparable, but the corporate-action checks — "
                    "which exist to find the artefacts adjustment removes — cannot "
                    "run against an adjusted series."
                    if policy != "raw_unadjusted"
                    else ""
                )
            ),
            evidence={"adjustment_policy": policy},
        )


class KnowledgeTimeOrdering(_Check):
    """Does every fact become knowable at or after it happened?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.knowledge_time_ordering",
            title="knowledge_time never precedes event_time",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )

    #: Every bitemporal fact table. Listed rather than discovered, so adding a
    #: fact table without adding it here is a visible omission rather than a
    #: silently narrower check.
    TABLES: tuple[tuple[type[Any], str], ...] = (
        (t.OhlcvBar, "bar"),
        (t.CorporateAction, "action"),
        (t.FundamentalFact, "fundamental"),
        (t.EarningsEvent, "earnings"),
    )

    def run(self, context: ValidationContext) -> CheckResult:
        offenders = 0
        examples: list[str] = []
        for table, label in self.TABLES:
            broken = table.knowledge_time < table.event_time
            rows = context.session.scalars(select(table).where(broken).limit(5)).all()
            count = (
                context.session.scalar(select(func.count()).select_from(table).where(broken)) or 0
            )
            offenders += count
            examples.extend(
                f"{label} instrument={row.instrument_id} "
                f"knowledge={row.knowledge_time.isoformat()} event={row.event_time.isoformat()}"
                for row in rows
            )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if offenders == 0 else CheckStatus.FAIL,
            summary=(
                "every stored fact becomes knowable at or after it happened"
                if offenders == 0
                else f"{offenders:,} facts claim to have been knowable before they occurred"
            ),
            evidence={"offending_rows": offenders},
            examples=tuple(examples[:5]),
        )


class PointInTimeFundamentals(_Check):
    """The one that would invalidate everything downstream if it failed."""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.point_in_time_fundamentals",
            title="No fundamental fact is knowable on its own period end",
            phase=Phase.DATA,
            requires=(DatasetKind.FUNDAMENTALS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        total = session.scalar(select(func.count()).select_from(t.FundamentalFact)) or 0
        offenders = session.scalars(
            select(t.FundamentalFact)
            .where(func.date(t.FundamentalFact.knowledge_time) <= t.FundamentalFact.period_end)
            .limit(5)
        ).all()
        offending_count = (
            session.scalar(
                select(func.count())
                .select_from(t.FundamentalFact)
                .where(func.date(t.FundamentalFact.knowledge_time) <= t.FundamentalFact.period_end)
            )
            or 0
        )
        estimated = (
            session.scalar(
                select(func.count())
                .select_from(t.FundamentalFact)
                .where(t.FundamentalFact.knowledge_source == "estimated")
            )
            or 0
        )
        if offending_count:
            status = CheckStatus.FAIL
            summary = (
                f"{offending_count:,} of {total:,} fundamental facts are marked knowable "
                "on or before their own period end. A quarter ending 31 March was not "
                "knowable on 31 March; every result computed over this snapshot is "
                "reading the future."
            )
        elif estimated:
            status = CheckStatus.WARN
            summary = (
                f"no fact predates its period end, but {estimated:,} of {total:,} carry an "
                "ESTIMATED knowledge_time derived from a filing-deadline rule rather than "
                "an observed filing timestamp. Results resting on them measure the rule too."
            )
        else:
            status = CheckStatus.PASS
            summary = f"all {total:,} fundamental facts carry an observed publication timestamp"
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=summary,
            evidence={
                "facts": total,
                "knowable_at_or_before_period_end": offending_count,
                "estimated_knowledge_time": estimated,
            },
            examples=tuple(
                f"instrument={row.instrument_id} metric={row.metric} "
                f"period_end={row.period_end} knowledge={row.knowledge_time.date()}"
                for row in offenders
            ),
        )


class SessionContinuity(_Check):
    """Are there holes in the price series, and where?"""

    def __init__(self, max_gap_ratio: float = 0.02) -> None:
        super().__init__(
            check_id="data.session_continuity",
            title="Price series have no unexplained gaps",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )
        self.max_gap_ratio = max_gap_ratio

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        rows = session.execute(
            select(
                t.OhlcvBar.instrument_id,
                func.min(t.OhlcvBar.session_date),
                func.max(t.OhlcvBar.session_date),
                func.count(),
            )
            .where(t.OhlcvBar.timeframe == "1d")
            .group_by(t.OhlcvBar.instrument_id)
        ).all()
        if not rows:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="no daily bars to check for continuity",
            )

        # Sessions, not weekdays. The weekday count was a deliberate cheap upper
        # bound, and over a sixteen-year window it stops being cheap and starts
        # being wrong: roughly ten US market holidays a year is about 3.6% of
        # weekdays, above the 2% threshold, so **every** well-formed series in
        # the first real universe import was reported as gappy. A check that
        # fires on all 78 of 78 teaches the reader to skip it.
        #
        # The calendar is the exchange's, and this project's own. It does not
        # know which venue a vendor's instrument trades on — that is what the
        # EXCHANGES dataset is for and it is usually absent — so a genuine
        # foreign listing may still show a shortfall. That is a smaller and more
        # honest error than counting Thanksgiving as missing data.
        # Symbols, so a finding names a security rather than a surrogate key.
        # "instrument 13 is missing 536 sessions" cannot be acted on without a
        # second query that the reader has to think to run.
        tickers = {
            row.instrument_id: row.ticker for row in session.scalars(select(t.SymbolMapping)).all()
        }

        calendar = get_calendar()
        flagged: list[tuple[int, dt.date, dt.date, int, int]] = []
        for instrument_id, first, last, count in rows:
            expected = calendar.session_count(first, last)
            if expected and count < expected * (1 - self.max_gap_ratio):
                flagged.append((instrument_id, first, last, count, expected))

        # The shape of the holes, for the flagged series only. "Missing 536 of
        # 4,174" is the same integer whether it is one 26-month block or 536
        # scattered days, and those are different defects with different
        # responses — so the runs are computed rather than left to the reader
        # to guess at. Only for flagged series, so the extra query is bounded
        # by the number of findings rather than by the universe.
        flagged.sort(key=lambda row: row[4] - row[3], reverse=True)
        analyses = [
            analyse_series(
                instrument_id,
                tickers.get(instrument_id, ""),
                self._sessions_for(session, instrument_id),
                calendar.sessions_between(first, last),
            )
            for instrument_id, first, last, _count, _expected in flagged[:MAX_GAP_ANALYSES]
        ]
        broken = [a for a in analyses if a.shape.breaks_the_series]

        status = CheckStatus.PASS if not flagged else CheckStatus.WARN
        detail: list[str] = []
        for analysis in analyses:
            detail.extend(analysis.render())
        if len(flagged) > len(analyses):
            detail.append(
                f"  ... and {len(flagged) - len(analyses)} more flagged series; "
                "see the report payload"
            )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=(
                f"{len(rows)} series checked against the exchange calendar over the "
                f"interval each one actually traded; {len(flagged)} are missing more "
                f"than {self.max_gap_ratio:.0%} of their sessions"
                + (
                    f", of which {len(broken)} have a structural break rather than "
                    "scattered absences"
                    if broken
                    else ""
                )
            ),
            evidence={
                "series": len(rows),
                "series_with_gaps": len(flagged),
                "missing_sessions_total": sum(e - c for _, _, _, c, e in flagged),
                "structural_breaks": len(broken),
                "analysed": [a.to_payload() for a in analyses],
            },
            detail=tuple(detail),
        )

    @staticmethod
    def _sessions_for(session: Session, instrument_id: int) -> list[dt.date]:
        return list(
            session.scalars(
                select(t.OhlcvBar.session_date)
                .where(
                    t.OhlcvBar.instrument_id == instrument_id,
                    t.OhlcvBar.timeframe == "1d",
                )
                .order_by(t.OhlcvBar.session_date)
            )
        )


class UnexplainedPriceJumps(_Check):
    """Overnight moves that look like unrecorded splits."""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.unexplained_price_jumps",
            title="Large overnight jumps are explained by a corporate action",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        session = context.session
        if context.package.adjustment_policy != "raw_unadjusted":
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary=(
                    "prices are adjusted, so split artefacts have already been "
                    "removed and their absence proves nothing"
                ),
                evidence={"adjustment_policy": context.package.adjustment_policy},
            )

        actions: set[tuple[int, dt.date]] = {
            (row.instrument_id, row.ex_date) for row in session.scalars(select(t.CorporateAction))
        }
        suspects: list[str] = []
        checked = 0
        previous: dict[int, tuple[dt.date, Decimal]] = {}
        for bar in session.scalars(
            select(t.OhlcvBar)
            .where(t.OhlcvBar.timeframe == "1d")
            .order_by(t.OhlcvBar.instrument_id, t.OhlcvBar.session_date)
        ):
            last = previous.get(bar.instrument_id)
            previous[bar.instrument_id] = (bar.session_date, bar.close)
            if last is None or last[1] <= 0:
                continue
            checked += 1
            ratio = bar.close / last[1]
            big = ratio > SPLIT_SUSPECT_RATIO or ratio < (1 / SPLIT_SUSPECT_RATIO)
            if big and (bar.instrument_id, bar.session_date) not in actions:
                suspects.append(
                    f"instrument={bar.instrument_id} {last[0]}→{bar.session_date} ratio={ratio:.3f}"
                )

        has_actions = DatasetKind.SPLITS in context.datasets
        status = CheckStatus.WARN if (not has_actions or suspects) else CheckStatus.PASS
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=(
                f"{len(suspects)} overnight moves beyond {SPLIT_SUSPECT_RATIO}x have no "
                f"corporate action on the same date, out of {checked:,} transitions"
                + (
                    ". The package has no splits dataset, so none could be explained."
                    if not has_actions
                    else ""
                )
            ),
            evidence={
                "transitions_examined": checked,
                "unexplained_jumps": len(suspects),
                "splits_available": has_actions,
            },
            examples=tuple(suspects[:5]),
        )


class SurvivorshipCoverage(_Check):
    """Are the securities that stopped trading actually here?"""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.survivorship_coverage",
            title="Delisted securities are present, not silently absent",
            phase=Phase.DATA,
            requires=(DatasetKind.INSTRUMENTS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        expected = context.universe.delisted
        if not expected:
            return CheckResult(
                check_id=self.check_id,
                title=self.title,
                phase=self.phase,
                status=CheckStatus.SKIPPED,
                summary="the validation universe declares no delisted names",
            )
        present = {row.ticker for row in context.session.scalars(select(t.SymbolMapping))} or {
            row.name for row in context.session.scalars(select(t.Instrument))
        }
        record = context.acquisition
        # The requested start, not the observed one. A snapshot beginning
        # 2010-01-04 because that is where the request began and one beginning
        # there because the vendor had nothing earlier are the same row in
        # `data_packages`, and only the first explains a 2008 delisting's
        # absence. Falling back to declared coverage is a weaker but honest
        # approximation for packages written before the record existed.
        requested_start = record.requested_start or context.package.coverage_start
        requested_end = record.requested_end or context.package.coverage_end
        roster = classify_roster(
            expected,
            present,
            record,
            requested_start=requested_start,
            requested_end=requested_end,
            observed=self._observed_series(context),
        )
        uncovered = roster.uncovered

        # Classification changes the remedy, never the verdict. A control whose
        # usable history is not in the snapshot is not in the snapshot, whoever
        # is at fault, and every rate computed here is a rate over survivors.
        status = CheckStatus.PASS if not uncovered else CheckStatus.FAIL
        ours = [entry for entry in uncovered if entry.status.is_our_defect]
        headline = (
            f"all {len(expected)} survivorship controls are covered by usable history"
            if not uncovered
            else (
                f"{len(uncovered)} of {len(expected)} survivorship controls are not "
                "covered by usable historical data. Every rate computed over this "
                "snapshot is computed over survivors, which is the single most "
                "flattering mistake available."
                + (
                    f" {len(ours)} of them are missing for a reason on our side of the "
                    "vendor boundary, not the vendor's."
                    if ours
                    else ""
                )
            )
        )
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=status,
            summary=headline,
            evidence={
                "controls_expected": len(expected),
                "controls_covered": len(roster.covered),
                "controls_uncovered": len(uncovered),
                "requested_start": requested_start.isoformat(),
                "required_history_start": (
                    roster.required_history_start.isoformat()
                    if roster.required_history_start
                    else None
                ),
                "acquisition_outcomes_recorded": not record.is_empty,
                "by_status": roster.counts,
                "controls": roster.to_payload()["controls"],
            },
            detail=tuple(render_roster(roster)),
        )

    @staticmethod
    def _observed_series(context: ValidationContext) -> dict[str, ObservedSeries]:
        """What the snapshot actually holds, per ticker.

        Presence in ``symbol_mappings`` is not coverage: a ticker row with forty
        bars proves nothing about what a screen would have seen in 2008. The
        bars are what decides, so they are measured rather than assumed.
        """
        rows = context.session.execute(
            select(
                t.SymbolMapping.ticker,
                func.min(t.OhlcvBar.session_date),
                func.max(t.OhlcvBar.session_date),
                func.count(),
            )
            .join(t.OhlcvBar, t.OhlcvBar.instrument_id == t.SymbolMapping.instrument_id)
            .where(t.OhlcvBar.timeframe == "1d")
            .group_by(t.SymbolMapping.ticker)
        ).all()
        return {
            ticker: ObservedSeries(
                ticker=ticker, first_session=first, last_session=last, bars=int(count)
            )
            for ticker, first, last, count in rows
        }


class DuplicateFacts(_Check):
    """One row per identity, or every aggregate is wrong by an unknown amount."""

    def __init__(self) -> None:
        super().__init__(
            check_id="data.duplicate_facts",
            title="No two rows claim the same fact identity",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        grouped = (
            select(
                t.OhlcvBar.instrument_id,
                t.OhlcvBar.timeframe,
                t.OhlcvBar.session_date,
                func.count().label("n"),
            )
            .group_by(t.OhlcvBar.instrument_id, t.OhlcvBar.timeframe, t.OhlcvBar.session_date)
            .having(func.count() > 1)
            .subquery()
        )
        rows = context.session.execute(select(grouped).limit(5)).all()
        total = context.session.scalar(select(func.count()).select_from(grouped)) or 0
        # More than one row per (instrument, timeframe, session) is legitimate
        # when it is a vendor revision, which the schema distinguishes by
        # knowledge_time. So this reports rather than fails, and names the
        # distinction the reader has to make.
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            phase=self.phase,
            status=CheckStatus.PASS if total == 0 else CheckStatus.WARN,
            summary=(
                "every (instrument, timeframe, session) appears once"
                if total == 0
                else (
                    f"{total:,} (instrument, timeframe, session) keys have more than one row. "
                    "That is correct for a vendor revision and wrong for a duplicated "
                    "export; check whether their knowledge_times differ."
                )
            ),
            evidence={"duplicate_keys": total},
            examples=tuple(f"instrument={r[0]} {r[1]} {r[2]} rows={r[3]}" for r in rows),
        )


class InstrumentCapabilityCoverage(_Check):
    """How many instruments support which kind of claim.

    Reports **two sample sizes and never one**. The first real universe import
    has 78 instruments with price history and 34 with a verified split
    schedule; a result quoted over "78 instruments" that silently needed raw
    prices would be describing a sample it did not have.

    Never FAILs on a low raw-verified count. An entitlement answer from a
    corporate-action vendor is not a defect in the price data, and failing here
    would push the operator toward deleting good bars to make a check go green.
    """

    def __init__(self) -> None:
        super().__init__(
            check_id="data.instrument_capability",
            title="Per-instrument capability: what may each instrument be used for?",
            phase=Phase.DATA,
            requires=(DatasetKind.DAILY_BARS,),
        )

    def run(self, context: ValidationContext) -> CheckResult:
        index = context.capabilities
        if index.is_empty:
            return self.result(
                status=CheckStatus.WARN,
                summary=(
                    "this package carries no per-instrument capability record, so "
                    "eligibility is UNKNOWN rather than established. Checks that need "
                    "a verified raw price series cannot name their sample."
                ),
                evidence={"capability_recorded": False},
                examples=("re-run `tradeit data enrich <package> --source fmp` to produce one",),
            )

        price_eligible = len(index.price_eligible)
        raw_verified = len(index.raw_verified)
        reasons = index.reasons()
        summary = (
            f"{price_eligible} instrument(s) carry price data; {raw_verified} of them "
            "have a verified raw price series. Scale-invariant analytics may use all "
            f"{price_eligible}; absolute-price analytics may use {raw_verified}."
        )
        return self.result(
            status=CheckStatus.PASS if raw_verified else CheckStatus.WARN,
            summary=summary,
            evidence={
                "instruments_price_eligible": price_eligible,
                "instruments_raw_verified": raw_verified,
                **index.counts(),
            },
            examples=tuple(f"{count} x {reason}" for reason, count in reasons.items())[:5],
        )


def data_checks() -> list[_Check]:
    """Every data-layer check, in the order a reader should see them."""
    return [
        RowsPresent(),
        QuarantineRate(),
        AdjustmentDeclared(),
        InstrumentCapabilityCoverage(),
        KnowledgeTimeOrdering(),
        PointInTimeFundamentals(),
        DuplicateFacts(),
        SessionContinuity(),
        UnexplainedPriceJumps(),
        SurvivorshipCoverage(),
    ]


__all__ = [
    "MAX_ACCEPTABLE_QUARANTINE_RATE",
    "SPLIT_SUSPECT_RATIO",
    "AdjustmentDeclared",
    "DuplicateFacts",
    "InstrumentCapabilityCoverage",
    "KnowledgeTimeOrdering",
    "PointInTimeFundamentals",
    "QuarantineRate",
    "RowsPresent",
    "SessionContinuity",
    "SurvivorshipCoverage",
    "UnexplainedPriceJumps",
    "data_checks",
]
