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
#:
#: ``split-inverse-2`` — the arithmetic is unchanged, but what reaches it is
#: not: vendor split factors are now normalized to a canonical share-count
#: multiplier through a **declared per-provider convention**, and the Twelve
#: Data adapter's convention was previously reciprocal to the truth. A sidecar
#: stamped ``split-inverse-1`` that was built from a Twelve Data split schedule
#: multiplied by 1/r where it should have multiplied by r. That is exactly what
#: this version string exists to make findable.
RECONSTRUCTION_ALGORITHM_VERSION = "split-inverse-2"

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


class SplitFactorConvention(StrEnum):
    """What a vendor's split number actually measures.

    This exists because two vendors handed us the same corporate action as
    ``7`` and ``0.142857142857…`` and a naive comparison called it a
    disagreement. It was not one. They are reciprocals, which means they are
    the *same split* described from opposite ends, and the fix is to normalize
    before comparing rather than to widen a tolerance until the complaint stops.

    **The number alone cannot tell you which convention it is.** ``0.25`` is the
    price-adjustment multiplier of a 4-for-1 forward split *and* the share-count
    multiplier of a 1-for-4 reverse split, and those are opposite events. So the
    convention is **declared per provider**, never inferred per value. A vendor
    whose convention is not established is :attr:`UNKNOWN` and its factors are
    refused rather than assumed.
    """

    #: The multiplier on the **share count**: 4 for a 4-for-1, 0.125 for a
    #: 1-for-8 reverse. This is the canonical form everything else derives from.
    SHARE_COUNT_MULTIPLIER = "share_count_multiplier"
    #: The multiplier applied to **historical prices** to produce the adjusted
    #: series: 0.25 for a 4-for-1. The reciprocal of the share count.
    PRICE_ADJUSTMENT_MULTIPLIER = "price_adjustment_multiplier"
    #: An explicit pair — 4 new shares for 1 old — which is the only
    #: unambiguous form, because the two numbers carry their own direction.
    NEW_OVER_OLD_SHARES = "new_over_old_shares"
    #: Not established. Factors are refused, not guessed.
    UNKNOWN = "unknown"

    @property
    def describe(self) -> str:
        return {
            SplitFactorConvention.SHARE_COUNT_MULTIPLIER: (
                "share-count multiplier (4 for a 4-for-1)"
            ),
            SplitFactorConvention.PRICE_ADJUSTMENT_MULTIPLIER: (
                "historical price-adjustment multiplier (0.25 for a 4-for-1)"
            ),
            SplitFactorConvention.NEW_OVER_OLD_SHARES: (
                "explicit new-for-old share pair (4-for-1)"
            ),
            SplitFactorConvention.UNKNOWN: "not established",
        }[self]


#: Relative tolerance for deciding two normalized ratios describe the same
#: split.
#:
#: Not zero, because a vendor that serves ``1/7`` as a truncated decimal cannot
#: round-trip to exactly 7. Not loose either: 1e-6 is nine orders of magnitude
#: below the gap between a 7-for-1 and a 6-for-1, so nothing this tolerance
#: merges is a real disagreement. A vendor truncating to fewer than about seven
#: significant digits will surface as a conflict rather than as agreement —
#: reported for a person, which is the safe direction.
RATIO_TOLERANCE = Decimal("1e-6")


def to_share_count_multiplier(value: Decimal, convention: SplitFactorConvention) -> Decimal:
    """Normalize one vendor's factor into the canonical share-count multiplier.

    Raises rather than guessing for :attr:`SplitFactorConvention.UNKNOWN`. An
    unknown convention applied as though it were the share count inverts every
    price before the event, and the resulting series looks perfectly plausible.
    """
    if value <= 0:
        raise ValueError(f"split factor {value} is not positive")
    if convention is SplitFactorConvention.UNKNOWN:
        raise ValueError(
            f"cannot normalize the split factor {value}: the provider's convention is "
            "not established. The same number is a 4-for-1 under one convention and a "
            "1-for-4 under the other, so this is refused rather than guessed."
        )
    if convention is SplitFactorConvention.PRICE_ADJUSTMENT_MULTIPLIER:
        return Decimal(1) / value
    return value


def ratios_agree(left: Decimal, right: Decimal, tolerance: Decimal = RATIO_TOLERANCE) -> bool:
    """Whether two canonical ratios describe the same split, within tolerance."""
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) / max(left, right) <= tolerance


def are_reciprocal(left: Decimal, right: Decimal, tolerance: Decimal = RATIO_TOLERANCE) -> bool:
    """Whether two ratios are reciprocals — the same split, described inversely.

    Used *after* normalization, where it should never fire. When it does, it
    means a provider's declared convention is wrong, which is worth saying
    precisely rather than reporting as an economic disagreement.
    """
    if left <= 0 or right <= 0:
        return False
    return ratios_agree(left, Decimal(1) / right, tolerance)


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
    """One split in canonical form, with the vendor's own numbers preserved.

    **One canonical quantity, four derived ones.** ``ratio`` is the multiplier
    on the **share count** — 4 for a 4-for-1, 0.1 for a 1-for-10 reverse — and
    every other multiplier anybody needs is a property derived from it, so no
    caller has to remember which way round a particular use runs:

    ======================================  =========  =====================
    Quantity                                4-for-1    1-for-8 reverse
    ======================================  =========  =====================
    ``economic_ratio``                      4          0.125
    ``price_adjustment_multiplier``         0.25       8
    ``volume_adjustment_multiplier``        4          0.125
    ``raw_price_reconstruction_multiplier`` 4          0.125
    ``raw_volume_reconstruction_multiplier`` 0.25      8
    ======================================  =========  =====================

    Read the first two rows together and the reason for the type is visible: a
    4-for-1 split has an economic ratio of ``4`` and a price-adjustment
    multiplier of ``0.25``, and a vendor that hands you one of those numbers
    without saying which has handed you something indistinguishable from a
    1-for-4 reverse split.

    ``vendor_value`` and ``vendor_convention`` keep what the vendor actually
    said, so a later disagreement about the normalization is settled by looking
    rather than by re-downloading.

    ``numerator`` and ``denominator`` are likewise retained rather than
    discarded once the ratio is derived. A ratio of 0.1 could be 1-for-10 or
    2-for-20, and the vendor's own pair is what an argument about direction gets
    settled against.

    ``announced_at`` is almost always ``None`` and that is deliberate. An
    effective date is not an announcement date, and a split history that carries
    only the former cannot support announcement-time causality research. Leaving
    it empty says so; inventing it would not.
    """

    ex_date: dt.date
    #: Canonical. Always the share-count multiplier, whatever the vendor sent.
    ratio: Decimal
    source: str = ""
    numerator: int | None = None
    denominator: int | None = None
    split_type: str = ""
    announced_at: dt.datetime | None = None
    #: Exactly what the vendor sent, before normalization. ``None`` when the
    #: vendor supplied an explicit share pair rather than a single factor.
    vendor_value: Decimal | None = None
    #: How that value was read. Declared by the provider adapter, never guessed
    #: from the number.
    vendor_convention: SplitFactorConvention = SplitFactorConvention.SHARE_COUNT_MULTIPLIER

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError(f"split ratio {self.ratio} is not positive")

    @classmethod
    def from_vendor(
        cls,
        *,
        ex_date: dt.date,
        value: Decimal,
        convention: SplitFactorConvention,
        source: str = "",
        numerator: int | None = None,
        denominator: int | None = None,
        split_type: str = "",
    ) -> SplitEvent:
        """Build a canonical event from a vendor factor and its declared convention.

        The one place a vendor number crosses into canonical semantics. Every
        adapter goes through it, so "which way round is this vendor?" is
        answered once per provider rather than at each use site.
        """
        return cls(
            ex_date=ex_date,
            ratio=to_share_count_multiplier(value, convention),
            source=source,
            numerator=numerator,
            denominator=denominator,
            split_type=split_type,
            vendor_value=value,
            vendor_convention=convention,
        )

    # -- the canonical quantity and everything derived from it ----------------

    @property
    def economic_ratio(self) -> Decimal:
        """What happened to the share count. The canonical fact."""
        return self.ratio

    @property
    def price_adjustment_multiplier(self) -> Decimal:
        """Raw historical price times this gives the vendor's adjusted price."""
        return Decimal(1) / self.ratio

    @property
    def volume_adjustment_multiplier(self) -> Decimal:
        """Raw historical volume times this gives the vendor's adjusted volume.

        The inverse of the price relationship, which is what keeps the money
        that changed hands unchanged by the adjustment.
        """
        return self.ratio

    @property
    def raw_price_reconstruction_multiplier(self) -> Decimal:
        """Adjusted price times this gives the raw exchange print."""
        return self.ratio

    @property
    def raw_volume_reconstruction_multiplier(self) -> Decimal:
        """Adjusted volume times this gives the raw printed volume."""
        return Decimal(1) / self.ratio

    # -- description ----------------------------------------------------------

    @property
    def is_reverse(self) -> bool:
        return self.ratio < 1

    @property
    def describe(self) -> str:
        if self.numerator and self.denominator:
            return f"{self.numerator}-for-{self.denominator}"
        return f"ratio {self.ratio}"

    @property
    def vendor_note(self) -> str:
        """What the vendor said, and how it was read. For findings and reports."""
        if self.vendor_value is None:
            return f"{self.source or 'source'} supplied {self.describe}"
        return (
            f"{self.source or 'source'} supplied {self.vendor_value} as a "
            f"{self.vendor_convention.describe}, normalized to a share-count "
            f"multiplier of {self.ratio}"
        )


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


@dataclass(frozen=True, slots=True)
class SplitCensus:
    """How a symbol's supplied splits divide up. Four numbers, not one.

    The report used to say "reconstructed across 5 split(s)" for an Apple
    package spanning 2010 to 2025. All five records are real; three of them are
    from 1987, 2000 and 2005 and touch nothing in that window. Reading the line,
    a person would reasonably conclude five splits were applied.

    The four counts are genuinely different quantities, and lumping them
    together loses the distinction that matters:

    * ``supplied`` — records the enrichment provider handed over, in total.
    * ``in_coverage`` — records whose ex-date falls inside the package's own
      first-to-last session window.
    * ``effective`` — records that changed at least one reconstructed row.
    * ``outside_coverage`` — the rest, split by side, because the two sides
      behave in opposite ways.

    ``in_coverage`` and ``effective`` are not the same count and neither is
    redundant. A split **after** the window's end is outside coverage and
    affects *every* row; a split **before** the window's start is outside
    coverage and affects *none*. Apple over 2010 to 2025 has two in coverage, both
    effective; NVIDIA the same. That is the sentence the report should have
    been printing.
    """

    supplied: int = 0
    in_coverage: int = 0
    effective: int = 0
    before_coverage: int = 0
    after_coverage: int = 0

    @property
    def outside_coverage(self) -> int:
        return self.before_coverage + self.after_coverage

    def render(self) -> str:
        parts = [
            f"{self.supplied} supplied",
            f"{self.in_coverage} inside price coverage",
            f"{self.effective} affecting at least one row",
        ]
        if self.outside_coverage:
            sides = []
            if self.before_coverage:
                sides.append(f"{self.before_coverage} before the window, affecting none")
            if self.after_coverage:
                sides.append(f"{self.after_coverage} after the window, affecting every row")
            parts.append(f"{self.outside_coverage} outside coverage ({'; '.join(sides)})")
        return ", ".join(parts)

    def to_payload(self) -> dict[str, int]:
        return {
            "supplied": self.supplied,
            "in_coverage": self.in_coverage,
            "effective": self.effective,
            "outside_coverage": self.outside_coverage,
            "before_coverage": self.before_coverage,
            "after_coverage": self.after_coverage,
        }


def take_census(
    splits: Sequence[SplitEvent],
    coverage: tuple[dt.date, dt.date] | None,
) -> SplitCensus:
    """Count a split schedule against a package's price coverage.

    ``effective`` is derived from the same strictly-after comparison the
    reconstruction itself uses, rather than being counted separately — a census
    that disagreed with the arithmetic it describes would be worse than none.
    """
    if coverage is None:
        return SplitCensus(supplied=len(splits))
    first, last = coverage
    in_coverage = sum(1 for s in splits if first <= s.ex_date <= last)
    before = sum(1 for s in splits if s.ex_date < first)
    after = sum(1 for s in splits if s.ex_date > last)
    # A split changes a row when at least one session precedes its ex-date,
    # which is exactly `cumulative_factor`'s condition applied to the earliest
    # session in the package.
    effective = sum(1 for s in splits if s.ex_date > first)
    return SplitCensus(
        supplied=len(splits),
        in_coverage=in_coverage,
        effective=effective,
        before_coverage=before,
        after_coverage=after,
    )


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
    #: How the supplied splits divide up. See :class:`SplitCensus` — the four
    #: numbers are genuinely different and "across 5 splits" said none of them.
    census: SplitCensus = field(default_factory=lambda: SplitCensus())

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
            f"reconstructed{provenance}; splits {self.census.render()}"
            + (
                f"; {self.high_factor_sessions:,} session(s) carry a factor large enough "
                "that vendor rounding is magnified above a cent"
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
        # Compared on the canonical share-count multiplier and with a tolerance,
        # never on the vendors' raw numbers. Two sources describing one split as
        # `7` and `0.142857142857…` agree about the world and disagree only
        # about which end to measure from; calling that a conflict is how a
        # report fills with noise and the real conflicts stop being read.
        first = events[0]
        if all(ratios_agree(first.ratio, event.ratio) for event in events[1:]):
            findings.append(
                f"{symbol}: {len(events)} equivalent split records on {ex_date} "
                f"({first.describe}). Duplicates were NOT merged; a repeated "
                "record may mean the vendor listed one event twice, or that two "
                "genuinely happened."
            )
        elif all(
            ratios_agree(first.ratio, event.ratio) or are_reciprocal(first.ratio, event.ratio)
            for event in events[1:]
        ):
            # Reciprocals *after* normalization mean a provider's declared
            # convention is wrong, not that the vendors disagree about the
            # event. Worth saying precisely, because the remedy is a one-line
            # declaration rather than an argument with a data vendor.
            notes.append(
                f"{symbol}: the split records on {ex_date} are RECIPROCALS of each "
                f"other ({', '.join(sorted(str(e.ratio) for e in events))}), which is "
                "one split described from opposite ends rather than two different "
                "splits. After canonical normalization this should not happen: it "
                "means a provider's declared split-factor convention is wrong. "
                + "; ".join(sorted(event.vendor_note for event in events))
            )
        else:
            findings.append(
                f"{symbol}: CONFLICTING split records on {ex_date} — share-count "
                f"multipliers {sorted(str(e.ratio) for e in events)}. These are not "
                "reciprocals and not equal, so they describe different events. Not "
                "resolved automatically; reconstruction over this symbol applies all "
                "of them and will be wrong if only one is real. "
                + "; ".join(sorted(event.vendor_note for event in events))
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
    census = take_census(ordered, coverage)
    result = ReconstructionResult(
        symbol=symbol,
        quality=ReconstructionQuality.APPLIED,
        splits_used=tuple(ordered),
        sessions_total=len(bars),
        price_provider=price_provider,
        split_provider=split_provider,
        conflicts=conflicts,
        notes=notes,
        census=census,
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
    "RATIO_TOLERANCE",
    "RECONSTRUCTION_ALGORITHM_VERSION",
    "RECONSTRUCTION_COLUMNS",
    "RECONSTRUCTION_LABEL",
    "ReconstructedRow",
    "ReconstructionQuality",
    "ReconstructionResult",
    "SplitCensus",
    "SplitEvent",
    "SplitFactorConvention",
    "are_reciprocal",
    "check_split_consistency",
    "cumulative_factor",
    "ratios_agree",
    "reconstruct_symbol",
    "take_census",
    "to_share_count_multiplier",
]
