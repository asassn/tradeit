"""Automated data-quality diagnostics.

Every check here answers one question: *would a human looking at this row
believe it?* Vendors ship bad data constantly — duplicated sessions after a
backfill, a decimal point in the wrong place, a split they forgot to apply, a
close outside the day's range, a stale price repeated through a halt. None of it
announces itself. It arrives as a number, gets averaged into an indicator, and
comes out the other end as a signal.

Three design commitments, each learned the expensive way:

**A finding is a hypothesis, not a verdict.** Every diagnostic here produces
false positives on real data, and the false positives are not noise — they are
the most interesting sessions in the market. GME on 2021-01-27 trips the
price-spike check, the volume check and the volatility check simultaneously,
and every one of those bars is correct. So findings carry a
:class:`Severity`, and only ``REJECT`` removes data from the pipeline.

**Suspicion is recorded, not acted on.** A ``SUSPECT`` finding attaches a
quality flag to the row and lets it through. The alternative — dropping
anything unusual — systematically deletes exactly the market conditions a
breakout system exists to trade, which is a far worse bias than the occasional
bad tick.

**Nothing is dropped silently.** Rejected rows go to ``quarantined_rows`` with
the payload and the reason, so a gap in a price series is always explainable.

The checks are deliberately independent and pure: each takes what it needs and
returns findings. That makes them individually testable, and it means a check
that turns out to be wrong can be removed without disturbing the others.

**On the overlap with model validation.** Several structural checks here --
OHLC ordering, positive prices, ``knowledge_time >= event_time`` -- duplicate
invariants that :class:`~tradeit.core.models.OhlcvBar` already enforces at
construction. That is deliberate, and the duplication is not dead code. The
model is the gate for rows built *through* it; these checks are the gate for
rows that arrive another way: a database read of data ingested by an older
version of the schema, a raw vendor payload being triaged before construction,
a batch loaded by a migration. A check that is currently unreachable must still
be correct on the day it becomes reachable, and the tests construct their
inputs with ``model_construct`` precisely to keep it honest.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Any

from tradeit.core.calendar import TradingCalendar
from tradeit.core.enums import CorporateActionType, DataQualityFlag
from tradeit.core.models import CorporateAction, OhlcvBar, SymbolMapping


class Severity(StrEnum):
    """What should happen to the row."""

    #: The row is impossible. Cannot be stored: a negative price or a high
    #: below its low is not a market event, it is a broken record.
    REJECT = "reject"
    #: The row is possible but unusual. Stored with a quality flag so a
    #: consumer can decide, and so the pattern is visible in aggregate.
    SUSPECT = "suspect"
    #: Worth knowing, affects no row. Gaps around a known halt, for example.
    INFO = "info"

    @property
    def quarantines(self) -> bool:
        return self is Severity.REJECT


class CheckId(StrEnum):
    DUPLICATE_BAR = "duplicate_bar"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    NON_POSITIVE_PRICE = "non_positive_price"
    IMPOSSIBLE_VOLUME = "impossible_volume"
    MISSING_SESSION = "missing_session"
    UNEXPECTED_SESSION = "unexpected_session"
    EXTREME_PRICE_JUMP = "extreme_price_jump"
    UNEXPLAINED_JUMP = "unexplained_jump"
    ACTION_WITHOUT_JUMP = "action_without_jump"
    TIMESTAMP_ANOMALY = "timestamp_anomaly"
    SYMBOL_MAPPING_CONFLICT = "symbol_mapping_conflict"
    STALE_PRICE = "stale_price"
    ZERO_VOLUME_RUN = "zero_volume_run"


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnostic result, addressed to a specific row or date."""

    check: CheckId
    severity: Severity
    instrument_id: int | None
    session_date: dt.date | None
    message: str
    #: Structured evidence. Kept separate from ``message`` so a report can
    #: aggregate on the numbers rather than parse prose.
    details: Mapping[str, Any] = field(default_factory=dict)
    #: The quality flag to attach when the severity is SUSPECT.
    flag: DataQualityFlag | None = None

    def __str__(self) -> str:
        where = f"{self.instrument_id}" if self.instrument_id is not None else "-"
        when = self.session_date.isoformat() if self.session_date else "-"
        return f"[{self.severity}] {self.check} {where}@{when}: {self.message}"


@dataclass(slots=True)
class QualityThresholds:
    """Tuning knobs, with the reasoning attached.

    These are detection thresholds, not strategy parameters. They are here
    rather than in ``StrategyConfig`` because changing them changes what the
    system *believes about its data*, not what it trades -- and a threshold
    that quietly varied per strategy would make two strategies disagree about
    whether a bar exists.
    """

    #: A one-session move beyond this is flagged for inspection. 50% is well
    #: outside anything a liquid equity does without news, and comfortably
    #: inside what a mis-applied 2:1 split produces.
    extreme_move_pct: float = 0.50
    #: Above this, an unexplained move is more likely a missing corporate
    #: action than a real one. A 3:1 split is a -66.7% move.
    split_suspect_pct: float = 0.60
    #: A corporate action of at least this ratio should be visible in the raw
    #: price series. Below it, the move is inside normal daily noise.
    action_visible_ratio: float = 1.25
    #: How close the observed move must be to the action's implied move. Wide,
    #: because the ex-date move also contains the day's genuine return.
    action_match_tolerance: float = 0.25
    #: Identical closes for this many consecutive sessions suggests a stale
    #: feed rather than a quiet market. Very thin securities genuinely do this,
    #: which is why it is SUSPECT and not REJECT.
    stale_price_sessions: int = 5
    #: Consecutive zero-volume sessions before the listing looks dormant.
    zero_volume_sessions: int = 3
    #: A volume above this multiple of the trailing median is flagged. Squeeze
    #: names exceed it legitimately and often.
    volume_spike_multiple: float = 50.0
    #: Volume beyond this is not a market event. US consolidated tape has never
    #: printed a single-session share volume near this for one security.
    absurd_volume: Decimal = Decimal("100_000_000_000")


# ---------------------------------------------------------------------------
# Structural checks: the row itself
# ---------------------------------------------------------------------------


def check_duplicates(bars: Sequence[OhlcvBar]) -> list[Finding]:
    """Two bars for the same instrument, timeframe and session.

    Distinguishes an exact replay from a genuine conflict. A replay is
    idempotent and harmless — the ingest layer's ``ON CONFLICT DO NOTHING``
    absorbs it. Two *different* bars for one session is a real problem, because
    whichever one wins is arbitrary and the choice changes the backtest.
    """
    grouped: dict[tuple[int, str, dt.date], list[OhlcvBar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.instrument_id, str(bar.timeframe), bar.session_date)].append(bar)

    findings: list[Finding] = []
    for (instrument_id, timeframe, session), group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        signatures = {(b.open, b.high, b.low, b.close, b.volume) for b in group}
        identical = len(signatures) == 1
        findings.append(
            Finding(
                check=CheckId.DUPLICATE_BAR,
                severity=Severity.INFO if identical else Severity.REJECT,
                instrument_id=instrument_id,
                session_date=session,
                message=(
                    f"{len(group)} identical {timeframe} bars (harmless replay)"
                    if identical
                    else f"{len(group)} conflicting {timeframe} bars for one session; "
                    "whichever is stored would be an arbitrary choice"
                ),
                details={"count": len(group), "distinct": len(signatures)},
            )
        )
    return findings


def check_ohlc_consistency(bars: Iterable[OhlcvBar]) -> list[Finding]:
    """High must be the highest and low the lowest. No exceptions, ever.

    Unlike most checks here this one has no legitimate counterexample, which is
    why it rejects. A bar whose close sits outside its own range is not an
    unusual market condition; it is a broken record, and any indicator reading
    it produces a number with no meaning.
    """
    findings: list[Finding] = []
    for bar in bars:
        problems: list[str] = []
        if bar.high < bar.low:
            problems.append(f"high {bar.high} below low {bar.low}")
        for name, value in (("open", bar.open), ("close", bar.close)):
            if value > bar.high:
                problems.append(f"{name} {value} above high {bar.high}")
            if value < bar.low:
                problems.append(f"{name} {value} below low {bar.low}")
        if problems:
            findings.append(
                Finding(
                    check=CheckId.OHLC_INCONSISTENT,
                    severity=Severity.REJECT,
                    instrument_id=bar.instrument_id,
                    session_date=bar.session_date,
                    message="; ".join(problems),
                    details={"problems": problems},
                    flag=DataQualityFlag.INCONSISTENT_OHLC,
                )
            )
    return findings


def check_prices_positive(bars: Iterable[OhlcvBar]) -> list[Finding]:
    """Equity prices are strictly positive.

    Note the scope. Futures and some commodity contracts genuinely printed
    negative in April 2020, and USO is in the validation universe precisely to
    exercise that. This check applies to equity bars, where a zero or negative
    price is a vendor placeholder — usually a suspended listing rendered as 0.
    """
    findings: list[Finding] = []
    for bar in bars:
        bad = {
            name: value
            for name, value in (
                ("open", bar.open),
                ("high", bar.high),
                ("low", bar.low),
                ("close", bar.close),
            )
            if value <= 0
        }
        if bad:
            findings.append(
                Finding(
                    check=CheckId.NON_POSITIVE_PRICE,
                    severity=Severity.REJECT,
                    instrument_id=bar.instrument_id,
                    session_date=bar.session_date,
                    message=(
                        f"non-positive price(s): {bad}; usually a vendor placeholder "
                        "for a suspended or unpriced session"
                    ),
                    details={"fields": {k: str(v) for k, v in bad.items()}},
                )
            )
    return findings


def check_volume(
    bars: Sequence[OhlcvBar], thresholds: QualityThresholds | None = None
) -> list[Finding]:
    """Negative volume is impossible; zero is meaningful; huge is suspicious.

    Zero volume is deliberately *not* rejected. A halted session, a dormant
    thin listing and a holiday half-session all legitimately print zero, and
    treating those as absent data would put a hole in the series exactly where
    something interesting happened. It is flagged instead, and a *run* of zeros
    is reported separately because that pattern means dormancy rather than a
    single quiet day.
    """
    limits = thresholds or QualityThresholds()
    findings: list[Finding] = []

    by_instrument: dict[int, list[OhlcvBar]] = defaultdict(list)
    for bar in bars:
        by_instrument[bar.instrument_id].append(bar)

    for instrument_id, series in sorted(by_instrument.items()):
        series = sorted(series, key=lambda b: b.session_date)
        volumes = [b.volume for b in series]

        for bar in series:
            if bar.volume < 0:
                findings.append(
                    Finding(
                        check=CheckId.IMPOSSIBLE_VOLUME,
                        severity=Severity.REJECT,
                        instrument_id=instrument_id,
                        session_date=bar.session_date,
                        message=f"negative volume {bar.volume}",
                    )
                )
            elif bar.volume > limits.absurd_volume:
                findings.append(
                    Finding(
                        check=CheckId.IMPOSSIBLE_VOLUME,
                        severity=Severity.REJECT,
                        instrument_id=instrument_id,
                        session_date=bar.session_date,
                        message=(
                            f"volume {bar.volume} exceeds any single-session share volume "
                            "ever printed on the US tape; almost certainly a unit error"
                        ),
                    )
                )

        # A run of zeros, not each individual zero: one quiet session is not a
        # finding, and reporting each of them would bury everything else.
        run_start: dt.date | None = None
        run = 0

        def close_run(
            start: dt.date | None, length: int, instrument_id: int = instrument_id
        ) -> None:
            if length >= limits.zero_volume_sessions and start is not None:
                findings.append(
                    Finding(
                        check=CheckId.ZERO_VOLUME_RUN,
                        severity=Severity.SUSPECT,
                        instrument_id=instrument_id,
                        session_date=start,
                        message=(
                            f"{length} consecutive sessions with zero volume from "
                            f"{start}; the listing is dormant, halted, or the feed "
                            "is not reporting volume for it"
                        ),
                        details={"sessions": length},
                        flag=DataQualityFlag.SUSPECT_ZERO_VOLUME,
                    )
                )

        for bar in series:
            if bar.volume == 0:
                run_start = run_start or bar.session_date
                run += 1
                continue
            close_run(run_start, run)
            run_start, run = None, 0
        close_run(run_start, run)  # a run that reaches the end of the series

        median = _median(sorted(v for v in volumes if v > 0))
        if median is None:
            continue
        for bar in series:
            if bar.volume > median * Decimal(str(limits.volume_spike_multiple)):
                findings.append(
                    Finding(
                        check=CheckId.IMPOSSIBLE_VOLUME,
                        severity=Severity.SUSPECT,
                        instrument_id=instrument_id,
                        session_date=bar.session_date,
                        message=(
                            f"volume {bar.volume} is {float(bar.volume / median):.0f}x the "
                            f"median {median}; real during a squeeze, wrong if the vendor "
                            "changed units"
                        ),
                        details={"volume": str(bar.volume), "median": str(median)},
                    )
                )
    return findings


def check_timestamps(bars: Iterable[OhlcvBar], *, now: dt.datetime) -> list[Finding]:
    """The bitemporal invariants, checked at the door.

    ``knowledge_time < event_time`` is the one that matters most: it says the
    system knew a price before it happened. Pydantic already refuses naive
    datetimes, so this catches the ordering and horizon problems that a type
    cannot.
    """
    findings: list[Finding] = []
    for bar in bars:
        if bar.knowledge_time < bar.event_time:
            findings.append(
                Finding(
                    check=CheckId.TIMESTAMP_ANOMALY,
                    severity=Severity.REJECT,
                    instrument_id=bar.instrument_id,
                    session_date=bar.session_date,
                    message=(
                        f"knowledge_time {bar.knowledge_time.isoformat()} precedes "
                        f"event_time {bar.event_time.isoformat()}: the row claims the "
                        "price was knowable before it existed"
                    ),
                )
            )
        if bar.event_time > now:
            findings.append(
                Finding(
                    check=CheckId.TIMESTAMP_ANOMALY,
                    severity=Severity.REJECT,
                    instrument_id=bar.instrument_id,
                    session_date=bar.session_date,
                    message=f"event_time {bar.event_time.isoformat()} is in the future",
                )
            )
        if (
            bar.event_time.date() != bar.session_date
            and abs((bar.event_time.date() - bar.session_date).days) > 1
        ):
            # One day of slack: a UTC close instant legitimately lands on the
            # next calendar day for some exchanges.
            findings.append(
                Finding(
                    check=CheckId.TIMESTAMP_ANOMALY,
                    severity=Severity.SUSPECT,
                    instrument_id=bar.instrument_id,
                    session_date=bar.session_date,
                    message=(
                        f"event_time {bar.event_time.date()} is more than a day from "
                        f"session_date {bar.session_date}"
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Calendar checks: the sessions that should and should not be there
# ---------------------------------------------------------------------------


def check_calendar(
    bars: Sequence[OhlcvBar],
    calendar: TradingCalendar,
    *,
    first_trade_date: dt.date | None = None,
    last_trade_date: dt.date | None = None,
) -> list[Finding]:
    """Compare the delivered sessions against the exchange calendar.

    Two directions, and they mean opposite things:

    * **A missing session** — the calendar says the exchange traded, the vendor
      has no bar. Real causes: a trading halt lasting all day, a security
      suspended pending news, or a vendor gap. All three are worth knowing and
      none of them may be filled in. See :func:`fill_policy`.
    * **An unexpected session** — the vendor has a bar on a day the calendar
      calls a holiday. That is either a vendor error or a hole in our calendar,
      and the second is worse because it silently shifts every session-count.

    ``first_trade_date`` and ``last_trade_date`` bound the expectation: a
    security that had not listed yet is not missing sessions, and neither is one
    that has delisted. Omitting them would report an IPO's entire pre-listing
    history as a gap.
    """
    if not bars:
        return []

    series = sorted(bars, key=lambda b: b.session_date)
    have = {b.session_date for b in series}
    start = max(series[0].session_date, first_trade_date or series[0].session_date)
    end = min(series[-1].session_date, last_trade_date or series[-1].session_date)

    findings: list[Finding] = []
    instrument_id = series[0].instrument_id

    expected = set(calendar.sessions_between(start, end))
    for session in sorted(expected - have):
        findings.append(
            Finding(
                check=CheckId.MISSING_SESSION,
                severity=Severity.SUSPECT,
                instrument_id=instrument_id,
                session_date=session,
                message=(
                    f"{calendar.name} traded on {session} but no bar was delivered; "
                    "a full-day halt, a suspension, or a vendor gap -- not fillable"
                ),
            )
        )
    for session in sorted(have - expected):
        if session < start or session > end:
            continue
        findings.append(
            Finding(
                check=CheckId.UNEXPECTED_SESSION,
                severity=Severity.SUSPECT,
                instrument_id=instrument_id,
                session_date=session,
                message=(
                    f"a bar exists for {session}, which {calendar.name} does not list as "
                    "a session; either the vendor is wrong or our calendar is, and the "
                    "second silently shifts every session count"
                ),
            )
        )
    return findings


def fill_policy() -> dict[str, str]:
    """When a missing value may and may not be filled. The rule, in one place.

    This is a correctness boundary, not a style preference. Forward-filling a
    price bar manufactures market activity that did not occur: it creates a
    session with a real close and zero true volume, which then becomes a data
    point in every average, a candidate for a breakout, and a tradable date in a
    backtest. The resulting fill price is a price at which nobody could have
    traded.
    """
    return {
        "price_bars": (
            "NEVER filled. A missing session stays missing. Forward-filling invents "
            "a bar nobody could have traded, and every downstream average, breakout "
            "and backtest fill inherits the fiction."
        ),
        "indicator_values": (
            "NEVER filled. A null indicator carries a null_behaviour saying why "
            "(warm-up, missing input, not applicable). Imputing a value destroys "
            "that distinction and turns missing data into a signal."
        ),
        "corporate_actions": (
            "NEVER inferred from prices. A jump without an action is reported as an "
            "unexplained jump, not repaired by inventing a split -- inventing one "
            "would rewrite genuine crashes into clean adjustments."
        ),
        "benchmark_series": (
            "NEVER filled, and a missing benchmark session propagates: relative "
            "strength on a date the benchmark did not trade is undefined, not zero."
        ),
        "fundamental_facts": (
            "Carried forward, which is different from filling. The most recent "
            "fact visible to the clock remains the current fact until superseded -- "
            "that is what as-filed data means, not an interpolation."
        ),
        "sector_classification": (
            "Carried forward from the last effective date at or before the as-of "
            "date. Never back-filled from the current classification (ADR-0011)."
        ),
        "intraday_aggregation": (
            "A partial period is excluded, never padded. An incomplete daily bar "
            "built from a half-session of intraday data is not a daily bar."
        ),
    }


# ---------------------------------------------------------------------------
# Corporate-action checks: do the prices and the actions agree?
# ---------------------------------------------------------------------------


def check_corporate_actions(
    bars: Sequence[OhlcvBar],
    actions: Sequence[CorporateAction],
    thresholds: QualityThresholds | None = None,
) -> list[Finding]:
    """Cross-examine the price series against the action series.

    Two failures, opposite in shape and both silent:

    **A jump with no action.** A -50% overnight move with no recorded split is
    either a genuine catastrophe or a split the vendor failed to report. The
    system cannot tell them apart from prices alone, and must not try: inferring
    the split would rewrite real crashes into clean adjustments, which is how a
    backtest ends up unable to lose money.

    **An action with no jump.** A recorded 2:1 split with no corresponding move
    in the *raw* series means the prices were already adjusted. Under ADR-0005
    that is serious — applying the adjustment again would halve the series a
    second time.

    Operates on raw, unadjusted prices. Run against an adjusted feed, the second
    check fires on every action, which is itself the correct finding.
    """
    limits = thresholds or QualityThresholds()
    if len(bars) < 2:
        return []

    series = sorted(bars, key=lambda b: b.session_date)
    instrument_id = series[0].instrument_id
    by_ex_date: dict[dt.date, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        by_ex_date[action.ex_date].append(action)

    findings: list[Finding] = []
    for previous, current in pairwise(series):
        if previous.close <= 0:
            continue
        move = float(current.close / previous.close) - 1.0
        session_actions = by_ex_date.get(current.session_date, [])
        material = [
            a
            for a in session_actions
            if a.action_type is CorporateActionType.SPLIT
            and _ratio_magnitude(a.ratio) >= limits.action_visible_ratio
        ]

        if abs(move) >= limits.extreme_move_pct and not session_actions:
            # Severity stays SUSPECT at every magnitude, deliberately. A -90%
            # session is *more* likely to be a missing split than a -50% one,
            # but "more likely" is not "certain" -- Enron, Lehman and SVB all
            # printed moves in that range that were entirely real. Rejecting
            # them would delete the most instructive rows in the dataset.
            likely_split = abs(move) >= limits.split_suspect_pct
            findings.append(
                Finding(
                    check=CheckId.UNEXPLAINED_JUMP,
                    severity=Severity.SUSPECT,
                    instrument_id=instrument_id,
                    session_date=current.session_date,
                    message=(
                        f"{move:+.1%} overnight with no corporate action on file"
                        + (
                            f", which a {_implied_ratio(move)}:1 split would explain exactly"
                            if likely_split
                            else ""
                        )
                        + ". Reported, never repaired -- inferring a split here would "
                        "rewrite genuine crashes into clean adjustments"
                    ),
                    details={
                        "move": round(move, 6),
                        "implied_ratio": _implied_ratio(move),
                        "split_magnitude": likely_split,
                    },
                    flag=DataQualityFlag.SUSPECT_PRICE_SPIKE,
                )
            )

        for action in material:
            expected = 1.0 / float(action.ratio) - 1.0
            if abs(move - expected) > limits.action_match_tolerance:
                findings.append(
                    Finding(
                        check=CheckId.ACTION_WITHOUT_JUMP,
                        severity=Severity.SUSPECT,
                        instrument_id=instrument_id,
                        session_date=current.session_date,
                        message=(
                            f"a {action.ratio}:1 split implies a {expected:+.1%} move in "
                            f"raw prices but the series moved {move:+.1%}; the prices are "
                            "probably already adjusted, and adjusting again would apply "
                            "the split twice"
                        ),
                        details={"expected": round(expected, 6), "observed": round(move, 6)},
                    )
                )
    return findings


def check_extreme_moves(
    bars: Sequence[OhlcvBar], thresholds: QualityThresholds | None = None
) -> list[Finding]:
    """Single-session moves outside normal equity behaviour.

    Separate from the corporate-action cross-check because it applies to
    *adjusted* series too, where a jump can no longer be explained by a missing
    split and is therefore either real news or a bad tick.
    """
    limits = thresholds or QualityThresholds()
    series = sorted(bars, key=lambda b: b.session_date)
    findings: list[Finding] = []
    for previous, current in pairwise(series):
        if previous.close <= 0:
            continue
        move = float(current.close / previous.close) - 1.0
        if abs(move) >= limits.extreme_move_pct:
            findings.append(
                Finding(
                    check=CheckId.EXTREME_PRICE_JUMP,
                    severity=Severity.SUSPECT,
                    instrument_id=current.instrument_id,
                    session_date=current.session_date,
                    message=f"{move:+.1%} single-session move",
                    details={"move": round(move, 6)},
                    flag=DataQualityFlag.SUSPECT_PRICE_SPIKE,
                )
            )
    return findings


def check_stale_prices(
    bars: Sequence[OhlcvBar], thresholds: QualityThresholds | None = None
) -> list[Finding]:
    """Identical closes repeated across sessions.

    A genuinely thin security does this, which is why it is SUSPECT. What makes
    it worth catching is the failure mode where a vendor repeats the last known
    price through a halt or a data outage: the series looks continuous, realised
    volatility collapses to zero, and a volatility-regime model concludes the
    market has gone quiet when in fact the feed has gone dark.
    """
    limits = thresholds or QualityThresholds()
    series = sorted(bars, key=lambda b: b.session_date)
    findings: list[Finding] = []

    run = 1
    run_start = series[0].session_date if series else None
    for previous, current in pairwise(series):
        identical = (
            current.close == previous.close
            and current.open == previous.open
            and current.high == previous.high
            and current.low == previous.low
        )
        if identical:
            run += 1
            continue
        if run >= limits.stale_price_sessions and run_start is not None:
            findings.append(_stale_finding(previous.instrument_id, run_start, run))
        run, run_start = 1, current.session_date

    if run >= limits.stale_price_sessions and run_start is not None and series:
        findings.append(_stale_finding(series[-1].instrument_id, run_start, run))
    return findings


def _stale_finding(instrument_id: int, start: dt.date, run: int) -> Finding:
    return Finding(
        check=CheckId.STALE_PRICE,
        severity=Severity.SUSPECT,
        instrument_id=instrument_id,
        session_date=start,
        message=(
            f"{run} consecutive sessions with an identical OHLC bar from {start}; "
            "thin trading, a halt, or a feed repeating its last known price -- the "
            "third makes realised volatility read zero while the market is moving"
        ),
        details={"sessions": run},
        flag=DataQualityFlag.STALE_REPEAT,
    )


# ---------------------------------------------------------------------------
# Identity checks
# ---------------------------------------------------------------------------


def check_symbol_mappings(mappings: Sequence[SymbolMapping]) -> list[Finding]:
    """Overlapping ticker intervals: the identity trap, caught early.

    Two failures, both of which corrupt history rather than crash:

    * **One ticker, two instruments, overlapping dates.** Ambiguous by
      construction — a lookup by ticker returns whichever row the database
      happened to order first, and the answer can change between runs.
    * **One instrument, two tickers, overlapping dates.** A security cannot
      trade under two symbols on one exchange at once; usually a symbol change
      whose end date was never set, which silently extends the old ticker
      forever.

    PostgreSQL enforces the first with an exclusion constraint. This check
    exists so the problem is found at ingestion, with the offending rows named,
    rather than as a constraint violation with a row id.
    """
    findings: list[Finding] = []

    by_ticker: dict[str, list[SymbolMapping]] = defaultdict(list)
    by_instrument: dict[int, list[SymbolMapping]] = defaultdict(list)
    for mapping in mappings:
        by_ticker[mapping.ticker].append(mapping)
        by_instrument[mapping.instrument_id].append(mapping)

    for ticker, group in sorted(by_ticker.items()):
        for a, b in _overlapping_pairs(group):
            if a.instrument_id == b.instrument_id:
                continue
            findings.append(
                Finding(
                    check=CheckId.SYMBOL_MAPPING_CONFLICT,
                    severity=Severity.REJECT,
                    instrument_id=a.instrument_id,
                    session_date=max(a.valid_from, b.valid_from),
                    message=(
                        f"ticker {ticker!r} maps to instruments {a.instrument_id} and "
                        f"{b.instrument_id} over overlapping dates; a lookup by ticker "
                        "would return an arbitrary one"
                    ),
                    details={"instruments": sorted((a.instrument_id, b.instrument_id))},
                )
            )

    for instrument_id, group in sorted(by_instrument.items()):
        for a, b in _overlapping_pairs(group):
            if a.ticker == b.ticker:
                continue
            findings.append(
                Finding(
                    check=CheckId.SYMBOL_MAPPING_CONFLICT,
                    severity=Severity.REJECT,
                    instrument_id=instrument_id,
                    session_date=max(a.valid_from, b.valid_from),
                    message=(
                        f"instrument {instrument_id} carries tickers {a.ticker!r} and "
                        f"{b.ticker!r} over overlapping dates; usually a symbol change "
                        "whose valid_to was never set"
                    ),
                    details={"tickers": sorted((a.ticker, b.ticker))},
                )
            )
    return findings


def _overlapping_pairs(
    mappings: Sequence[SymbolMapping],
) -> list[tuple[SymbolMapping, SymbolMapping]]:
    out: list[tuple[SymbolMapping, SymbolMapping]] = []
    ordered = sorted(mappings, key=lambda m: (m.valid_from, m.valid_to or dt.date.max))
    for i, a in enumerate(ordered):
        a_end = a.valid_to or dt.date.max
        for b in ordered[i + 1 :]:
            if b.valid_from > a_end:
                break  # ordered by start, so nothing later can overlap either
            out.append((a, b))
    return out


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Everything the diagnostics found, and what happens as a result."""

    findings: tuple[Finding, ...]
    bars_examined: int = 0
    instruments_examined: int = 0

    def of_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    @property
    def rejected(self) -> list[Finding]:
        """Findings that quarantine their row."""
        return self.of_severity(Severity.REJECT)

    @property
    def suspect(self) -> list[Finding]:
        return self.of_severity(Severity.SUSPECT)

    def quarantine_keys(self) -> set[tuple[int | None, dt.date | None]]:
        """The (instrument, session) pairs that must not reach the feature layer."""
        return {(f.instrument_id, f.session_date) for f in self.rejected}

    def flags_for(self, instrument_id: int, session: dt.date) -> set[DataQualityFlag]:
        """Quality flags to attach to one stored bar."""
        return {
            f.flag
            for f in self.findings
            if f.flag is not None and f.instrument_id == instrument_id and f.session_date == session
        }

    def by_check(self) -> dict[str, int]:
        return dict(sorted(Counter(str(f.check) for f in self.findings).items()))

    def by_instrument(self) -> dict[int, int]:
        counts = Counter(f.instrument_id for f in self.findings if f.instrument_id is not None)
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def summary(self) -> dict[str, Any]:
        return {
            "bars_examined": self.bars_examined,
            "instruments_examined": self.instruments_examined,
            "findings": len(self.findings),
            "reject": len(self.rejected),
            "suspect": len(self.suspect),
            "info": len(self.of_severity(Severity.INFO)),
            "by_check": self.by_check(),
        }

    def silent_checks(self) -> list[str]:
        """Checks that produced nothing.

        Reported because a check that never fires on a deliberately hostile
        universe is more likely broken than vindicated. On the Phase 3
        validation roster, several of these *should* fire.
        """
        fired = {str(f.check) for f in self.findings}
        return sorted(str(c) for c in CheckId if str(c) not in fired)


def run_all(
    bars: Sequence[OhlcvBar],
    *,
    calendar: TradingCalendar | None = None,
    actions: Sequence[CorporateAction] = (),
    mappings: Sequence[SymbolMapping] = (),
    now: dt.datetime | None = None,
    thresholds: QualityThresholds | None = None,
    listing_dates: Mapping[int, tuple[dt.date | None, dt.date | None]] | None = None,
) -> QualityReport:
    """Run every diagnostic over a batch and collect the findings.

    Per-instrument checks are run per instrument rather than over the whole
    batch, because a "50% move" between the last bar of one security and the
    first of the next is not a move at all.
    """
    limits = thresholds or QualityThresholds()
    moment = now or dt.datetime.now(dt.UTC)

    findings: list[Finding] = [
        *check_duplicates(bars),
        *check_ohlc_consistency(bars),
        *check_prices_positive(bars),
        *check_volume(bars, limits),
        *check_timestamps(bars, now=moment),
        *check_symbol_mappings(mappings),
    ]

    by_instrument: dict[int, list[OhlcvBar]] = defaultdict(list)
    for bar in bars:
        by_instrument[bar.instrument_id].append(bar)
    actions_by_instrument: dict[int, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        actions_by_instrument[action.instrument_id].append(action)

    for instrument_id, series in sorted(by_instrument.items()):
        findings.extend(check_extreme_moves(series, limits))
        findings.extend(check_stale_prices(series, limits))
        findings.extend(
            check_corporate_actions(series, actions_by_instrument.get(instrument_id, []), limits)
        )
        if calendar is not None:
            first, last = (listing_dates or {}).get(instrument_id, (None, None))
            findings.extend(
                check_calendar(series, calendar, first_trade_date=first, last_trade_date=last)
            )

    return QualityReport(
        findings=tuple(findings),
        bars_examined=len(bars),
        instruments_examined=len(by_instrument),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _ratio_magnitude(ratio: Decimal) -> float:
    """How far a split ratio is from 1, in either direction.

    A 1-for-10 reverse split has ratio 0.1 and is just as visible as a 10-for-1
    with ratio 10, so magnitude has to be symmetric under inversion.
    """
    value = float(ratio)
    if value <= 0:
        return 0.0
    return max(value, 1.0 / value)


def _implied_ratio(move: float) -> float | None:
    """The split ratio that would explain this move, for the report only.

    Emphatically not used to correct anything -- it is the number a human needs
    to decide whether "-66.6%" is Enron or a missing 3:1.
    """
    if move <= -1.0:
        return None
    return round(1.0 / (1.0 + move), 4)
