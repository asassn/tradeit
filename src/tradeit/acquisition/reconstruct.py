"""Reconstructing raw exchange prices from split-adjusted ones.

Twelve Data documents that its daily, weekly and monthly prices are adjusted for
stock splits. This platform stores *raw* prints, because today's adjusted series
for a stock that split last week differs from the series anybody could have seen
before the split (ADR-0005). So the question is whether the raw series can be
recovered.

**The arithmetic is exact.** A split-adjusted price is the raw print divided by
the product of every split ratio that took effect *after* that session. Invert
it:

    raw_price(d)  = adjusted_price(d) * product(ratio_i for ex_date_i > d)
    raw_volume(d) = adjusted_volume(d) / product(ratio_i for ex_date_i > d)

A 2-for-1 split (ratio 2) turns a $100 pre-split print into a $50 adjusted
figure; multiplying by 2 returns $100. Volume moves the other way, because the
share count doubled.

**The arithmetic being exact is not the same as the answer being right**, and
this module is careful about the difference. Three things can invalidate it, and
only the first is detectable from the data:

1. **An incomplete split history.** Every price before a missing split is wrong
   by that split's factor. If the splits dataset was unavailable on the plan, we
   know we do not know, and :class:`ReconstructionResult` says so. If it was
   available and merely empty, that is weaker evidence than it looks — a vendor
   that serves an empty list for a stock that split is indistinguishable here
   from one that is right.
2. **Rounding.** Vendor adjusted prices are rounded before we see them.
   Multiplying by a large factor re-inflates that rounding: a half-cent of error
   on a 10-for-1 split becomes five cents. The result records the factor so the
   magnitude is recoverable, and :data:`HIGH_FACTOR_THRESHOLD` marks rows where
   it stops being negligible.
3. **An adjustment we did not model.** The vendor documents splits only, and
   that is a documentation claim rather than something verified here. If daily
   prices were also dividend-adjusted, this reconstruction would be confidently
   wrong and nothing in the data would reveal it.

So the output is **DERIVED, and labelled as such everywhere it appears**. It is
never written into the package's price columns and the manifest never calls it
raw. It goes to a sidecar carrying, per row: the vendor's original value, the
factor applied, the reconstructed value, which splits produced the factor, and
the algorithm version. Somebody who later disagrees with the method can redo it
from the recorded inputs rather than re-downloading.

**Dividend adjustments are not split adjustments** and are not modelled here.
Applying a dividend adjustment as though it were a split would corrupt the ratio
for every prior session, so the dividend dataset is deliberately not an input.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

#: Bumped whenever the arithmetic or its inputs change. Recorded on every
#: reconstructed row, so a sidecar produced by an older version is identifiable
#: rather than silently mixed with a newer one.
RECONSTRUCTION_ALGORITHM_VERSION = "split-inverse-1"

#: The label these values carry. Never "raw", never the vendor's name.
RECONSTRUCTION_LABEL = "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"

#: Above this cumulative factor, rounding in the vendor's adjusted price is
#: magnified enough to matter at the cent level. Flagged per row rather than
#: refused: the value is still the best available, and the reader should know.
HIGH_FACTOR_THRESHOLD = Decimal(4)

#: Prices are carried at this precision through the inversion. Wider than any
#: vendor quotes, so the quantisation happens once at the end rather than
#: compounding through the product.
PRICE_PLACES = Decimal("0.000001")


class ReconstructionQuality(StrEnum):
    """How much the reconstruction can be relied on."""

    #: Splits were available and applied. Arithmetically exact given a complete
    #: history, which is an assumption rather than a fact.
    APPLIED = "applied"
    #: Splits were available and the vendor reported none. The series is then
    #: identical to the adjusted one — correct if the vendor is right, and
    #: indistinguishable from a vendor that simply lost the record.
    NO_SPLITS_REPORTED = "no_splits_reported"
    #: The splits dataset could not be read — plan restriction or error. **No
    #: reconstruction was attempted.** The adjusted values stand and the
    #: limitation is reported.
    NOT_ATTEMPTED_NO_SPLIT_DATA = "not_attempted_no_split_data"

    @property
    def is_reliable(self) -> bool:
        """Whether the output differs from the input in a way we can defend."""
        return self is ReconstructionQuality.APPLIED


@dataclass(frozen=True, slots=True)
class SplitEvent:
    """One split, normalized, with the vendor's own numbers kept.

    ``ratio`` is the multiplier on the **share count**: 4 for a 4-for-1, 0.1 for
    a 1-for-10 reverse split. Vendors express this at least three ways and
    getting it backwards inverts every price before the event, so the parsing
    lives in the adapter that knows the vendor's convention and this type takes
    the settled number.

    ``numerator`` and ``denominator`` are retained rather than discarded once
    the ratio is derived. A ratio of 0.1 could be 1-for-10 or 2-for-20, and when
    somebody later disputes the direction, the vendor's own pair is what the
    argument gets settled against.

    ``announced_at`` is almost always ``None`` and that is deliberate. An
    effective date is not an announcement date, and a split history that carries
    only the former cannot support announcement-time causality research. Leaving
    it empty says so; inventing it would not.
    """

    ex_date: dt.date
    ratio: Decimal
    source: str = ""
    numerator: int | None = None
    denominator: int | None = None
    split_type: str = ""
    announced_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError(f"split ratio {self.ratio} is not positive")

    @property
    def is_reverse(self) -> bool:
        return self.ratio < 1

    @property
    def describe(self) -> str:
        if self.numerator and self.denominator:
            return f"{self.numerator}-for-{self.denominator}"
        return f"ratio {self.ratio}"


@dataclass(slots=True)
class ReconstructedRow:
    """One session, with everything needed to check the arithmetic."""

    session_date: dt.date
    field_name: str
    vendor_value: Decimal
    factor: Decimal
    reconstructed_value: Decimal
    splits_applied: tuple[dt.date, ...]
    algorithm: str = RECONSTRUCTION_ALGORITHM_VERSION
    label: str = RECONSTRUCTION_LABEL

    @property
    def factor_is_large(self) -> bool:
        return self.factor >= HIGH_FACTOR_THRESHOLD or self.factor <= (1 / HIGH_FACTOR_THRESHOLD)


@dataclass(slots=True)
class ReconstructionResult:
    """The outcome for one symbol.

    ``price_provider`` and ``split_provider`` are separate fields because they
    are separate facts. When the prices came from Twelve Data and the splits
    from FMP, the reconstructed series was supplied by neither, and a consumer
    that assumed otherwise would be attributing one vendor's numbers to the
    other's record.
    """

    symbol: str
    quality: ReconstructionQuality
    rows: list[dict[str, str]] = field(default_factory=list)
    splits_used: tuple[SplitEvent, ...] = ()
    sessions_changed: int = 0
    sessions_total: int = 0
    high_factor_sessions: int = 0
    note: str = ""
    price_provider: str = ""
    split_provider: str = ""
    #: Consistency problems found while reconstructing. Reported, never
    #: silently resolved — each one needs a person.
    conflicts: list[str] = field(default_factory=list)
    #: Things a reader should know that nobody has to act on. A split outside
    #: the package's window belongs here: it is correct behaviour, and filing
    #: it as a conflict would bury the ones that are not.
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.quality is ReconstructionQuality.NOT_ATTEMPTED_NO_SPLIT_DATA:
            return (
                f"{self.symbol}: raw reconstruction not attempted — {self.note}. The "
                "package keeps the vendor's split-adjusted prices and says so."
            )
        if self.quality is ReconstructionQuality.NO_SPLITS_REPORTED:
            return (
                f"{self.symbol}: the vendor reports no splits, so the adjusted and raw "
                "series coincide. That is only as good as the vendor's split record."
            )
        provenance = (
            f" [prices {self.price_provider} + splits {self.split_provider}]"
            if self.price_provider and self.split_provider
            else ""
        )
        return (
            f"{self.symbol}: {self.sessions_changed:,} of {self.sessions_total:,} sessions "
            f"reconstructed across {len(self.splits_used)} split(s){provenance}"
            + (
                f"; {self.high_factor_sessions:,} carry a factor large enough that "
                "vendor rounding is magnified above a cent"
                if self.high_factor_sessions
                else ""
            )
            + (f"; {len(self.conflicts)} consistency finding(s)" if self.conflicts else "")
        )


def cumulative_factor(
    session: dt.date, splits: Sequence[SplitEvent]
) -> tuple[Decimal, tuple[dt.date, ...]]:
    """The product of every split ratio effective strictly after ``session``.

    Strictly after: a split's ex-date is the first session that trades on the
    new basis, so that session's print is already post-split and must not be
    multiplied by its own ratio.
    """
    factor = Decimal(1)
    applied: list[dt.date] = []
    for split in splits:
        if split.ex_date > session:
            factor *= split.ratio
            applied.append(split.ex_date)
    return factor, tuple(applied)


def check_split_consistency(
    symbol: str,
    splits: Sequence[SplitEvent],
    coverage: tuple[dt.date, dt.date] | None,
) -> tuple[list[str], list[str]]:
    """Problems in a split schedule, reported rather than resolved.

    Returns ``(conflicts, notes)``, and the split between the two is the point.
    A conflict is something a person has to decide about: silently dropping a
    duplicate, or averaging two conflicting same-day ratios, would produce a
    reconstruction that looks clean and is wrong in a way nothing downstream
    could detect. A note is something a reader needs told but nobody has to act
    on — a split outside the package's window is *correct* behaviour, and
    filing it as a conflict would bury the ones that are not.
    """
    findings: list[str] = []
    notes: list[str] = []
    by_date: dict[dt.date, list[SplitEvent]] = {}
    for split in splits:
        by_date.setdefault(split.ex_date, []).append(split)

    for ex_date, events in sorted(by_date.items()):
        if len(events) == 1:
            continue
        ratios = {event.ratio for event in events}
        if len(ratios) == 1:
            findings.append(
                f"{symbol}: {len(events)} identical split records on {ex_date} "
                f"({events[0].describe}). Duplicates were NOT merged; a repeated "
                "record may mean the vendor listed one event twice, or that two "
                "genuinely happened."
            )
        else:
            findings.append(
                f"{symbol}: CONFLICTING split records on {ex_date} — ratios "
                f"{sorted(str(r) for r in ratios)}. Not resolved automatically; "
                "reconstruction over this symbol applies all of them and will be "
                "wrong if only one is real."
            )

    for split in splits:
        if split.ratio > _IMPLAUSIBLE_RATIO or split.ratio < (1 / _IMPLAUSIBLE_RATIO):
            findings.append(
                f"{symbol}: split on {split.ex_date} has ratio {split.ratio} "
                f"({split.describe}), beyond anything a real corporate action "
                "produces. Treat as a vendor data error."
            )
        if coverage is not None and not (coverage[0] <= split.ex_date <= coverage[1]):
            # Not an error: a 2005 split is simply outside a package that starts
            # in 2010, and correctly affects nothing in it. Worth saying, because
            # "the split list has five entries and only two changed anything" is
            # otherwise a puzzle — but a note rather than a conflict, because
            # nobody has to do anything about it.
            notes.append(
                f"{symbol}: split on {split.ex_date} ({split.describe}) falls outside "
                f"the price coverage {coverage[0]}..{coverage[1]} and affects no row "
                "in this package."
            )
    return findings, notes


def reconstruct_symbol(
    symbol: str,
    bars: Sequence[Mapping[str, str]],
    splits: Sequence[SplitEvent],
    *,
    splits_available: bool,
    unavailable_reason: str = "",
    price_provider: str = "",
    split_provider: str = "",
) -> ReconstructionResult:
    """Invert the vendor's split adjustment for one symbol's daily bars.

    ``bars`` are the canonical rows already written for the package — string
    values, canonical column names — so the sidecar lines up row for row with
    what the importer will read.
    """
    if not splits_available:
        return ReconstructionResult(
            symbol=symbol,
            quality=ReconstructionQuality.NOT_ATTEMPTED_NO_SPLIT_DATA,
            sessions_total=len(bars),
            note=unavailable_reason or "the splits dataset was not readable",
            price_provider=price_provider,
            split_provider=split_provider,
        )

    ordered = sorted(splits, key=lambda s: s.ex_date)
    if not ordered:
        return ReconstructionResult(
            symbol=symbol,
            quality=ReconstructionQuality.NO_SPLITS_REPORTED,
            sessions_total=len(bars),
            price_provider=price_provider,
            split_provider=split_provider,
        )

    coverage = (
        (
            dt.date.fromisoformat(bars[0]["session_date"]),
            dt.date.fromisoformat(bars[-1]["session_date"]),
        )
        if bars
        else None
    )
    conflicts, notes = check_split_consistency(symbol, ordered, coverage)
    result = ReconstructionResult(
        symbol=symbol,
        quality=ReconstructionQuality.APPLIED,
        splits_used=tuple(ordered),
        sessions_total=len(bars),
        price_provider=price_provider,
        split_provider=split_provider,
        conflicts=conflicts,
        notes=notes,
    )

    for bar in bars:
        session = dt.date.fromisoformat(bar["session_date"])
        factor, applied = cumulative_factor(session, ordered)
        if factor == 1:
            continue
        result.sessions_changed += 1
        large = factor >= HIGH_FACTOR_THRESHOLD or factor <= (1 / HIGH_FACTOR_THRESHOLD)
        if large:
            result.high_factor_sessions += 1

        row: dict[str, str] = {
            "instrument_id": bar.get("instrument_id", ""),
            "symbol": symbol,
            "session_date": bar["session_date"],
            "factor": _plain(factor),
            "splits_applied": ";".join(d.isoformat() for d in applied),
            # Both providers on every row. The reconstructed series was supplied
            # by neither of them, and a consumer reading one field would
            # otherwise attribute one vendor's numbers to the other's record.
            "price_provider": price_provider,
            "split_provider": split_provider,
            "algorithm": RECONSTRUCTION_ALGORITHM_VERSION,
            "label": RECONSTRUCTION_LABEL,
            "rounding_magnified": "true" if large else "false",
        }
        for name in ("open", "high", "low", "close"):
            text = bar.get(name, "")
            if not text:
                continue
            vendor = Decimal(text)
            row[f"vendor_{name}"] = text
            row[f"reconstructed_{name}"] = _plain((vendor * factor).quantize(PRICE_PLACES))
        if any(
            Decimal(row[f"reconstructed_{name}"]) <= 0
            for name in ("open", "high", "low", "close")
            if f"reconstructed_{name}" in row
        ):
            result.conflicts.append(
                f"{symbol} {bar['session_date']}: reconstruction produced a "
                "non-positive price. The split schedule and the price series "
                "disagree; the row is written and flagged rather than dropped."
            )

        volume_text = bar.get("volume", "")
        if volume_text:
            vendor_volume = Decimal(volume_text)
            row["vendor_volume"] = volume_text
            # Volume moves opposite to price: a 2-for-1 doubles the share count,
            # so the adjusted volume is larger than the print and dividing
            # recovers it. Quantised to whole shares because a fractional share
            # count is not a thing an exchange ever printed.
            row["reconstructed_volume"] = _plain((vendor_volume / factor).quantize(Decimal(1)))
        result.rows.append(row)

    return result


#: Columns of the reconstruction sidecar, in a fixed order so two runs over the
#: same data produce byte-identical files.
RECONSTRUCTION_COLUMNS: tuple[str, ...] = (
    "instrument_id",
    "symbol",
    "session_date",
    "factor",
    "splits_applied",
    "vendor_open",
    "reconstructed_open",
    "vendor_high",
    "reconstructed_high",
    "vendor_low",
    "reconstructed_low",
    "vendor_close",
    "reconstructed_close",
    "vendor_volume",
    "reconstructed_volume",
    "rounding_magnified",
    "price_provider",
    "split_provider",
    "algorithm",
    "label",
)

#: A ratio beyond this in either direction is not a corporate action, it is a
#: data error. A 1000-for-1 split does not happen; a decimal point does.
_IMPLAUSIBLE_RATIO = Decimal(1000)


def _plain(value: Decimal) -> str:
    """Render without exponent notation, which the importer would refuse."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "HIGH_FACTOR_THRESHOLD",
    "RECONSTRUCTION_ALGORITHM_VERSION",
    "RECONSTRUCTION_COLUMNS",
    "RECONSTRUCTION_LABEL",
    "ReconstructedRow",
    "ReconstructionQuality",
    "ReconstructionResult",
    "SplitEvent",
    "check_split_consistency",
    "cumulative_factor",
    "reconstruct_symbol",
]
