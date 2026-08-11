"""What each instrument's data can actually be used for.

A real universe acquisition does not produce one uniform dataset. The first full
run produced 78 instruments with price history, of which 34 got a verified split
schedule, 0 were answered "this security has no splits on record", and 44 were
refused with HTTP 402 — an entitlement answer, not an absence of corporate
actions. Those three groups support different claims, and a validation result
computed over "78 instruments" without saying which is a number nobody can
interpret.

**The failure this prevents is a specific one.** Absent per-instrument flags,
there are only two tempting options and both are wrong:

* Throw away the 44 instruments whose splits could not be verified. That
  discards two thirds of a legitimately acquired price history because a
  *second* vendor's billing plan is what it is — and every scale-invariant
  analytic works perfectly well on those bars.
* Keep all 78 and quietly treat them alike. Then a check that needs raw prices
  silently runs on 44 instruments whose raw series was never recoverable, and
  the result looks like it covers the universe.

So each instrument carries flags, the flags are recorded in the package, and
validation reports its sample sizes separately.

**These are claims about provenance, not about quality.** ``SPLIT_SCHEDULE_VERIFIED``
means a source that was able to answer did answer; it does not mean the schedule
is complete, and :mod:`tradeit.acquisition.reconstruct` is explicit that a split
a vendor does not hold is invisible. The flag says "we asked and got an answer",
which is the strongest thing the data supports.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InstrumentCapability(StrEnum):
    """What one instrument's data supports, as a set of independent flags."""

    #: Daily bars are present for this instrument. The floor for any empirical
    #: work at all, and true for every instrument the acquisition kept.
    PRICE_DATA_AVAILABLE = "PRICE_DATA_AVAILABLE"
    #: A corporate-action source was *able to answer* for this instrument —
    #: either with split records or with a valid empty response. Not a claim
    #: that the schedule is complete; a split the vendor does not hold is
    #: invisible and stays invisible.
    SPLIT_SCHEDULE_VERIFIED = "SPLIT_SCHEDULE_VERIFIED"
    #: The raw exchange series can be derived: the prices are split-adjusted and
    #: a verified schedule exists to invert the adjustment with, **or** the
    #: prices were raw to begin with and no derivation is needed.
    RAW_RECONSTRUCTION_AVAILABLE = "RAW_RECONSTRUCTION_AVAILABLE"
    #: The raw series cannot be derived, or cannot be shown to be complete. The
    #: reason is recorded per instrument; the commonest is an entitlement answer
    #: from the split source. **Not a defect in the price history**, and not a
    #: reason to discard it.
    RAW_RECONSTRUCTION_INCOMPLETE_OR_UNKNOWN = "RAW_RECONSTRUCTION_INCOMPLETE_OR_UNKNOWN"


@dataclass(frozen=True, slots=True)
class InstrumentCapabilityRecord:
    """One instrument's flags, with the reason attached.

    ``reason`` is not decoration. Six months after a run, "44 instruments lack a
    verified split schedule" is a fact nobody can act on, while "the split
    source answered HTTP 402 Payment Required for these 44" names both the cause
    and the remedy.
    """

    instrument_id: int
    ticker: str = ""
    flags: frozenset[InstrumentCapability] = field(default_factory=frozenset)
    reason: str = ""
    #: Who supplied the split schedule, where one was verified.
    split_provider: str = ""
    #: The source's own HTTP status, where it gave one. ``402`` is the
    #: difference between "not entitled" and "went wrong".
    http_status: int | None = None
    observed_at: dt.date | None = None

    def has(self, capability: InstrumentCapability) -> bool:
        return capability in self.flags

    @property
    def price_data_available(self) -> bool:
        return self.has(InstrumentCapability.PRICE_DATA_AVAILABLE)

    @property
    def split_schedule_verified(self) -> bool:
        return self.has(InstrumentCapability.SPLIT_SCHEDULE_VERIFIED)

    @property
    def raw_reconstruction_available(self) -> bool:
        return self.has(InstrumentCapability.RAW_RECONSTRUCTION_AVAILABLE)

    def to_payload(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "flags": sorted(str(f) for f in self.flags),
            "reason": self.reason,
            "split_provider": self.split_provider,
            "http_status": self.http_status,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> InstrumentCapabilityRecord:
        raw_flags = payload.get("flags") or ()
        flags: set[InstrumentCapability] = set()
        for name in raw_flags:
            try:
                flags.add(InstrumentCapability(str(name)))
            except ValueError:
                # A flag written by a newer build. Ignored rather than fatal:
                # this is provenance, and an unreadable extra should not stop a
                # validation run from reading the ones it does understand.
                continue
        observed = payload.get("observed_at")
        return cls(
            instrument_id=int(payload["instrument_id"]),
            ticker=str(payload.get("ticker") or ""),
            flags=frozenset(flags),
            reason=str(payload.get("reason") or ""),
            split_provider=str(payload.get("split_provider") or ""),
            http_status=(
                int(payload["http_status"]) if payload.get("http_status") is not None else None
            ),
            observed_at=dt.date.fromisoformat(str(observed)) if observed else None,
        )


@dataclass(frozen=True, slots=True)
class CapabilityIndex:
    """Every instrument's capabilities, with the questions validation asks.

    Deliberately answers "which instruments" rather than "how many". A check
    that needs raw prices must be able to *name* its sample, because a sample
    size without a roster cannot be reproduced.
    """

    records: tuple[InstrumentCapabilityRecord, ...] = ()

    @classmethod
    def from_payload(cls, payload: Iterable[Mapping[str, Any]] | None) -> CapabilityIndex:
        if not payload:
            return cls()
        out: list[InstrumentCapabilityRecord] = []
        for item in payload:
            try:
                out.append(InstrumentCapabilityRecord.from_payload(item))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(records=tuple(out))

    def to_payload(self) -> list[dict[str, Any]]:
        return [record.to_payload() for record in self.records]

    def __len__(self) -> int:
        return len(self.records)

    @property
    def is_empty(self) -> bool:
        """No record at all — which is not the same as "nothing is capable".

        A package written before capabilities existed, or by a tool that does
        not emit them, says nothing here. Validation must report that as
        *unknown* rather than as zero eligible instruments.
        """
        return not self.records

    def get(self, instrument_id: int) -> InstrumentCapabilityRecord | None:
        for record in self.records:
            if record.instrument_id == instrument_id:
                return record
        return None

    def with_capability(self, capability: InstrumentCapability) -> tuple[int, ...]:
        return tuple(r.instrument_id for r in self.records if r.has(capability))

    def tickers_with(self, capability: InstrumentCapability) -> tuple[str, ...]:
        return tuple(sorted(r.ticker for r in self.records if r.has(capability) and r.ticker))

    @property
    def price_eligible(self) -> tuple[int, ...]:
        """Instruments any scale-invariant analytic may run over."""
        return self.with_capability(InstrumentCapability.PRICE_DATA_AVAILABLE)

    @property
    def raw_verified(self) -> tuple[int, ...]:
        """Instruments whose raw series can be derived and shown to be derived."""
        return self.with_capability(InstrumentCapability.RAW_RECONSTRUCTION_AVAILABLE)

    def counts(self) -> dict[str, int]:
        return {
            str(capability): len(self.with_capability(capability))
            for capability in InstrumentCapability
        }

    def reasons(self) -> dict[str, int]:
        """Why instruments lack a raw reconstruction, tallied.

        One line per distinct cause rather than per instrument: "44 instances of the split
        source answered HTTP 402" is actionable where 44 identical lines are
        scrolled past.
        """
        tally: dict[str, int] = {}
        for record in self.records:
            if record.raw_reconstruction_available or not record.reason:
                continue
            tally[record.reason] = tally.get(record.reason, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))

    def render(self) -> list[str]:
        if self.is_empty:
            return [
                "  no per-instrument capability record in this package. Eligibility is "
                "UNKNOWN rather than zero; re-run enrichment to produce one."
            ]
        counts = self.counts()
        labels = (
            (InstrumentCapability.PRICE_DATA_AVAILABLE, "instruments with price data"),
            (InstrumentCapability.SPLIT_SCHEDULE_VERIFIED, "with a verified split schedule"),
            (InstrumentCapability.RAW_RECONSTRUCTION_AVAILABLE, "with raw reconstruction"),
            (
                InstrumentCapability.RAW_RECONSTRUCTION_INCOMPLETE_OR_UNKNOWN,
                "raw reconstruction incomplete or unknown",
            ),
        )
        lines = [f"  {label:<42}: {counts[str(capability)]}" for capability, label in labels]
        reasons = self.reasons()
        if reasons:
            lines.append("  reasons a raw reconstruction is unavailable:")
            lines += [f"    {count:>4}  {reason}" for reason, count in reasons.items()]
        return lines


__all__ = [
    "CapabilityIndex",
    "InstrumentCapability",
    "InstrumentCapabilityRecord",
]
