"""Consistency rules, and the line between "flag it" and "quarantine it".

Two kinds of problem arrive in vendor data and they deserve opposite treatment.

A bar whose high is below its low is **not a bar**. Nothing downstream can use
it: an ATR computed from it is meaningless, a breakout boundary drawn through it
is fictional. It is quarantined, and the quarantine record says which fields
contradicted each other so an operator can take it back to the vendor.

A bar with zero volume is **a fact that might be true**. A halted stock, a thin
ETF, a holiday half-session — all produce genuine zero-volume bars. Dropping
them creates a gap that looks exactly like a missing file, so they are imported
with :attr:`~tradeit.core.enums.DataQualityFlag.SUSPECT_ZERO_VOLUME` and the
consumer decides. This is the rule already stated on ``DataQualityFlag`` and
this module is where it is enforced.

The dividing question is: *can any correct consumer use this row?* If yes, flag.
If no, quarantine. "It looks wrong to me" is not on the list, because that
judgement belongs to whoever is looking at the numbers, not to the importer.

**Series checks** — duplicates, inverted intervals, impossible jumps — need more
than one row, so they run as a second pass over the keys the first pass
collected. The memory cost is one key tuple per row, which is why
:attr:`SeriesCheckConfig.check_duplicates` can be turned off for an import too
large to hold them; turning it off is reported, never assumed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from tradeit.core.enums import DataQualityFlag
from tradeit.data.packages.spec import DatasetKind
from tradeit.data.packages.stages import NormalizedRecord

#: A close-to-close move beyond this multiple is flagged, never rejected. Real
#: markets do this — a biotech on trial results, a small cap on a buyout — and a
#: rule that discarded them would quietly delete the most informative sessions
#: in the sample. It is a flag precisely because it is often true.
DEFAULT_SPIKE_MULTIPLE = Decimal("4")


class ValidationFailure(Exception):
    """The row is not usable by any correct consumer. Quarantine it."""


@dataclass(frozen=True, slots=True)
class SeriesCheckConfig:
    """Which cross-row checks to run, and how loudly."""

    check_duplicates: bool = True
    check_ordering: bool = True
    spike_multiple: Decimal = DEFAULT_SPIKE_MULTIPLE
    #: Sessions dated after the export date. Almost always a timezone or a
    #: fiscal-vs-calendar mistake, and always worth stopping on, because a bar
    #: dated in the future defeats every point-in-time guarantee downstream.
    check_future_dates: bool = True


def validate_row(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    """Apply the dataset's row-level rules.

    Returns the non-fatal flags. Raises :class:`ValidationFailure` when the row
    cannot be repaired into something usable.
    """
    handler = _ROW_VALIDATORS.get(record.dataset)
    if handler is None:
        return ()
    return handler(record)


def _validate_bar(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    values = record.values
    try:
        open_, high, low, close = (
            values["open"],
            values["high"],
            values["low"],
            values["close"],
        )
    except KeyError as error:
        raise ValidationFailure(f"missing required OHLC field {error.args[0]!r}") from error

    for name in ("open", "high", "low", "close"):
        price = values[name]
        if price is None:
            raise ValidationFailure(f"{name} is null; a bar without all four prices is not a bar")
        if price <= 0:
            raise ValidationFailure(
                f"{name}={price} is not positive. Zero or negative prices are not "
                "tradeable levels; this is usually a placeholder for a missing quote."
            )

    if high < max(open_, close, low):
        raise ValidationFailure(
            f"high={high} is below one of open={open_}, close={close}, low={low}; "
            "the row contradicts itself and no consumer can use it"
        )
    if low > min(open_, close, high):
        raise ValidationFailure(
            f"low={low} is above one of open={open_}, close={close}, high={high}; "
            "the row contradicts itself and no consumer can use it"
        )

    flags: list[DataQualityFlag] = []
    volume = values.get("volume")
    if volume is not None:
        if volume < 0:
            raise ValidationFailure(f"volume={volume} is negative")
        if volume == 0:
            flags.append(DataQualityFlag.SUSPECT_ZERO_VOLUME)
    vwap = values.get("vwap")
    if vwap is not None and not (low <= vwap <= high):
        # Not fatal: some vendors compute VWAP across sessions or include
        # off-book prints. Unusable as a bar-internal reference, usable as a bar.
        flags.append(DataQualityFlag.INCONSISTENT_OHLC)
    if open_ == high == low == close:
        flags.append(DataQualityFlag.STALE_REPEAT)
    return tuple(flags)


def _validate_interval(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    start = record.get("valid_from")
    end = record.get("valid_to")
    if start is None:
        raise ValidationFailure("valid_from is required; an interval with no start is not one")
    if end is not None and end <= start:
        raise ValidationFailure(
            f"valid_to={end} is not after valid_from={start}. Intervals are "
            "half-open [from, to); an inverted or empty one silently excludes "
            "the security from every universe read."
        )
    return ()


def _validate_split(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    ratio = record.get("ratio")
    if ratio is None:
        raise ValidationFailure("ratio is required for a split")
    if ratio <= 0:
        raise ValidationFailure(
            f"ratio={ratio} is not positive. A split ratio multiplies the share "
            "count; zero or negative would make every adjusted price nonsense."
        )
    return ()


def _validate_dividend(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    amount = record.get("amount")
    if amount is None:
        raise ValidationFailure("amount is required for a dividend")
    if amount < 0:
        raise ValidationFailure(f"amount={amount} is negative")
    ex_date = record.get("ex_date")
    pay_date = record.get("pay_date")
    if ex_date and pay_date and pay_date < ex_date:
        raise ValidationFailure(
            f"pay_date={pay_date} precedes ex_date={ex_date}; the cash cannot be "
            "paid before the shares trade without it"
        )
    return ()


def _validate_fundamental(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    period_end = record.get("period_end")
    if period_end is None:
        raise ValidationFailure(
            "period_end is required. Without it the fact has no fiscal identity "
            "and no lag rule can place it in time."
        )
    start = record.get("period_start")
    if start is not None and start >= period_end:
        raise ValidationFailure(f"period_start={start} is not before period_end={period_end}")
    return ()


def _validate_earnings(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    scheduled = record.get("scheduled_date")
    if scheduled is None:
        raise ValidationFailure("scheduled_date is required for an earnings event")
    announced = record.get("announced_time")
    if announced is not None and announced.date() > scheduled:
        raise ValidationFailure(
            f"announced_time={announced.date()} is after scheduled_date={scheduled}. "
            "A date cannot be announced after the event it schedules; this is "
            "usually a vendor writing the report timestamp into the announcement "
            "column, which would make every proximity filter see the future."
        )
    return ()


def _validate_delisting(record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
    if record.get("delisted_date") is None and record.get("last_trade_date") is None:
        raise ValidationFailure(
            "a delisting needs a date. Without one the security silently stays "
            "in the universe forever, which is survivorship bias with extra steps."
        )
    return ()


_ROW_VALIDATORS = {
    DatasetKind.DAILY_BARS: _validate_bar,
    DatasetKind.INTRADAY_BARS: _validate_bar,
    DatasetKind.SYMBOL_MAPPINGS: _validate_interval,
    DatasetKind.UNIVERSE_MEMBERSHIP: _validate_interval,
    DatasetKind.SECTORS: _validate_interval,
    DatasetKind.SPLITS: _validate_split,
    DatasetKind.DIVIDENDS: _validate_dividend,
    DatasetKind.FUNDAMENTALS: _validate_fundamental,
    DatasetKind.EARNINGS: _validate_earnings,
    DatasetKind.DELISTINGS: _validate_delisting,
}


#: Which fields identify a row within its dataset, for duplicate detection.
SERIES_KEYS: dict[DatasetKind, tuple[str, ...]] = {
    DatasetKind.DAILY_BARS: ("instrument_id", "session_date"),
    DatasetKind.INTRADAY_BARS: ("instrument_id", "session_date", "bar_start"),
    DatasetKind.INSTRUMENTS: ("instrument_id",),
    DatasetKind.SYMBOL_MAPPINGS: ("instrument_id", "ticker", "valid_from"),
    DatasetKind.SPLITS: ("instrument_id", "ex_date"),
    DatasetKind.DIVIDENDS: ("instrument_id", "ex_date"),
    DatasetKind.FUNDAMENTALS: ("instrument_id", "metric", "period_end", "fiscal_period"),
    DatasetKind.EARNINGS: ("instrument_id", "scheduled_date", "fiscal_period"),
    DatasetKind.DELISTINGS: ("instrument_id",),
}


@dataclass(slots=True)
class SeriesState:
    """Running state for the cross-row checks of one dataset."""

    config: SeriesCheckConfig
    export_date: dt.date | None = None
    seen: set[tuple[object, ...]] | None = None
    last_close: dict[object, Decimal] | None = None

    def __post_init__(self) -> None:
        if self.config.check_duplicates and self.seen is None:
            self.seen = set()
        if self.last_close is None:
            self.last_close = {}

    def check(self, record: NormalizedRecord) -> tuple[DataQualityFlag, ...]:
        """Cross-row checks. Raises :class:`ValidationFailure` on a duplicate."""
        flags: list[DataQualityFlag] = []
        key_fields = SERIES_KEYS.get(record.dataset)
        if key_fields and self.seen is not None:
            key = (record.dataset, *(record.get(name) for name in key_fields))
            if key in self.seen:
                raise ValidationFailure(
                    f"duplicate row for {dict(zip(key_fields, key[1:], strict=True))}. "
                    "Two rows claiming the same identity make every aggregate "
                    "over this dataset wrong by an unknown amount."
                )
            self.seen.add(key)

        session = record.get("session_date") or record.get("ex_date") or record.get("period_end")
        if (
            self.config.check_future_dates
            and self.export_date is not None
            and isinstance(session, dt.date)
            and session > self.export_date
        ):
            raise ValidationFailure(
                f"dated {session}, after the package export date {self.export_date}. "
                "A row dated in the future defeats every point-in-time guarantee "
                "downstream; check for a timezone or fiscal-calendar mistake."
            )

        close = record.get("close")
        if (
            record.dataset is DatasetKind.DAILY_BARS
            and isinstance(close, Decimal)
            and self.last_close is not None
        ):
            instrument = record.get("instrument_id")
            previous = self.last_close.get(instrument)
            if previous is not None and previous > 0:
                ratio = close / previous
                if ratio > self.config.spike_multiple or ratio < (1 / self.config.spike_multiple):
                    flags.append(DataQualityFlag.SUSPECT_PRICE_SPIKE)
            self.last_close[instrument] = close
        return tuple(flags)


def combine(*groups: Iterable[DataQualityFlag]) -> tuple[DataQualityFlag, ...]:
    """Merge flag sets, dropping OK and preserving first-seen order."""
    seen: dict[DataQualityFlag, None] = {}
    for group in groups:
        for flag in group:
            if flag is not DataQualityFlag.OK:
                seen.setdefault(flag, None)
    return tuple(seen)


__all__ = [
    "DEFAULT_SPIKE_MULTIPLE",
    "SERIES_KEYS",
    "SeriesCheckConfig",
    "SeriesState",
    "ValidationFailure",
    "combine",
    "validate_row",
]
