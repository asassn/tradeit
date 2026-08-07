"""The integrated pattern scanner.

One orchestration layer that takes an instrument, a timeframe and a knowledge
boundary, and returns every material pattern interpretation. It runs the enabled
detectors, applies their contracts, folds the results into tracked identities,
derives relationships and — when given a repository — persists observations.

**What the scanner must not do**, stated here because this is the layer where
scope creep would be easiest and most damaging:

* It does not confirm breakouts. It records that a line was crossed and stops.
* It does not generate trades, entries, exits or stops.
* It does not consult fundamentals.
* It does not size positions.
* It does not rank candidates against each other.

All five are later phases' work. A scanner that did any of them would make the
pattern layer un-auditable, because "why was this pattern reported" and "why was
this trade taken" would have the same answer.

**Incremental scanning.** A nightly scan that rebuilds years of history for
every instrument is waste, but causal correctness outranks the saving, so the
incremental path is defined by what it is *safe* to omit rather than by how
little work can be done:

Every detector here is ``BOUNDED_RESCAN_REQUIRED``. None is incremental-safe in
the strict sense — an O(1) update from yesterday's state — and none needs a full
rescan either. The reason both extremes are wrong is worth stating:

* **Not O(1)**, because a new bar can confirm a pivot several sessions back
  (``right_bars`` sessions of confirmation lag), and a newly confirmed pivot can
  change where a structure *starts*. Yesterday's answer is not a valid prefix of
  today's.
* **Not a full rescan**, because every detector's discovery is bounded: it looks
  back at most a fixed number of sessions, declared as ``minimum_bars``. Bars
  older than that cannot affect the result except through the indicator warm-up.

The warm-up is the subtlety. ATR is Wilder-smoothed, which is an exponential
moving average with unbounded memory: truncating the series changes every later
ATR value by a decaying amount rather than not at all. So the rescan window is
``minimum_bars`` plus :data:`WARMUP_MULTIPLE` times the ATR period, chosen so
the residual difference is far below any threshold a detector tests. The
equivalence between incremental and full replay is asserted by test rather than
assumed, and the measured agreement is reported.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.patterns.base import PatternContext, PatternInstance
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.registry import DetectorRegistry
from tradeit.patterns.relationships import PatternRelation, derive_relationships
from tradeit.patterns.tracking import PatternTracker, TrackedPattern

#: Multiples of the ATR period added to a rescan window so Wilder smoothing has
#: converged. Wilder's alpha is 1/period, so after k*period samples the residual
#: influence of the truncated history is about exp(-k). Six gives ~0.25%, which
#: is three orders of magnitude below the tolerances any detector tests.
WARMUP_MULTIPLE = 6


class RescanPolicy(StrEnum):
    """How much history a detector needs re-examined when new bars arrive."""

    #: Yesterday's result plus the new bar. No detector here qualifies, and the
    #: value exists so the classification is a decision rather than an omission.
    INCREMENTAL_SAFE = "incremental_safe"
    #: A bounded suffix reproduces the full-series result. Every detector here.
    BOUNDED_RESCAN_REQUIRED = "bounded_rescan_required"
    #: The whole series must be re-examined. Nothing qualifies today; a detector
    #: with an unbounded lookback would.
    FULL_RESCAN_REQUIRED = "full_rescan_required"


@dataclass(frozen=True, slots=True)
class SkippedDetector:
    """A detector that did not run, and why.

    Recorded rather than silently omitted: "no cup was found" and "the cup
    detector never ran" are different facts, and a dataset that cannot tell them
    apart cannot support a recall measurement.
    """

    name: str
    reason: str


@dataclass(slots=True)
class ScanResult:
    """Everything one scan produced."""

    instrument_id: int
    timeframe: Bartimeframe
    as_of_session: dt.date
    instances: list[PatternInstance] = field(default_factory=list)
    tracked: list[TrackedPattern] = field(default_factory=list)
    relations: list[PatternRelation] = field(default_factory=list)
    skipped: list[SkippedDetector] = field(default_factory=list)
    #: Wall-clock seconds per detector. Present so the integrated benchmark can
    #: attribute cost without a separate instrumented run.
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def families(self) -> set[str]:
        return {str(i.pattern_type) for i in self.instances}

    def best(self) -> PatternInstance | None:
        return max(self.instances, key=lambda p: p.quality, default=None)

    def of_family(self, name: str) -> list[PatternInstance]:
        return [i for i in self.instances if i.detector_name == name]

    def summary(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "timeframe": str(self.timeframe),
            "as_of_session": self.as_of_session.isoformat(),
            "instances": len(self.instances),
            "families": sorted(self.families),
            "relations": len(self.relations),
            "skipped": {s.name: s.reason for s in self.skipped},
            "elapsed": round(sum(self.timings.values()), 4),
        }


@dataclass(slots=True)
class MultiTimeframeResult:
    """Scans across several timeframes, plus the edges between them.

    Cross-timeframe relationships are derived here rather than inside a single
    scan because they are the one thing no single-timeframe scan can see: a
    weekly VCP containing a daily bull flag is a fact about two scans.
    """

    instrument_id: int
    as_of_session: dt.date
    by_timeframe: dict[Bartimeframe, ScanResult] = field(default_factory=dict)
    cross_relations: list[PatternRelation] = field(default_factory=list)

    @property
    def all_instances(self) -> list[PatternInstance]:
        return [i for result in self.by_timeframe.values() for i in result.instances]

    def summary(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "as_of_session": self.as_of_session.isoformat(),
            "timeframes": {str(tf): result.summary() for tf, result in self.by_timeframe.items()},
            "cross_relations": [r.to_payload() for r in self.cross_relations],
        }


class PatternScanner:
    """Runs the enabled detectors and assembles the result.

    Holds a tracker so identities persist across sessions. A scanner without one
    would re-mint every pattern every day, which is the failure the whole
    identity design exists to prevent.
    """

    def __init__(
        self,
        registry: DetectorRegistry | None = None,
        *,
        config: PatternEngineConfig | None = None,
        tracker: PatternTracker | None = None,
    ) -> None:
        self.registry = registry or DetectorRegistry.from_config(config)
        self.tracker = tracker if tracker is not None else PatternTracker()

    # -- policy --------------------------------------------------------------

    def rescan_policy(self, name: str) -> RescanPolicy:
        """Every detector needs a bounded rescan. See the module docstring."""
        if name not in self.registry.entries:
            raise ConfigError(f"no detector named {name!r}")
        return RescanPolicy.BOUNDED_RESCAN_REQUIRED

    def rescan_window(self, name: str) -> int:
        """Bars of history a bounded rescan must include for this detector.

        ``minimum_bars`` covers the structural lookback; the warm-up term covers
        the Wilder smoothing whose memory does not end.
        """
        entry = self.registry.entries[name]
        detector = self.registry.detector(name, entry.timeframes[0])
        return entry.minimum_bars + WARMUP_MULTIPLE * detector.atr_period

    def required_history(self, timeframe: Bartimeframe = Bartimeframe.D1) -> int:
        """The longest rescan window across the detectors enabled here."""
        windows = [
            self.rescan_window(name)
            for name, entry in self.registry.entries.items()
            if entry.enabled and entry.supports(timeframe)
        ]
        return max(windows, default=0)

    # -- scanning ------------------------------------------------------------

    def scan(
        self,
        instrument_id: int,
        timeframe: Bartimeframe,
        bars: Sequence[OhlcvBar],
        as_of_session: dt.date,
        *,
        context: PatternContext | None = None,
        track: bool = True,
    ) -> ScanResult:
        """Run every enabled detector that supports this timeframe.

        The knowledge boundary is enforced by each detector, not here, so a
        detector added later cannot bypass it by being called differently.
        """
        result = ScanResult(
            instrument_id=instrument_id, timeframe=timeframe, as_of_session=as_of_session
        )

        for name, entry in self.registry.entries.items():
            if not entry.enabled:
                result.skipped.append(SkippedDetector(name, "disabled in configuration"))
                continue
            if not entry.supports(timeframe):
                result.skipped.append(
                    SkippedDetector(name, f"family is not defined on {timeframe}")
                )
                continue
            if len(bars) < entry.minimum_bars:
                result.skipped.append(
                    SkippedDetector(
                        name,
                        f"{len(bars)} bars supplied, {entry.minimum_bars} needed for warm-up",
                    )
                )
                continue

            detector = self.registry.detector(name, timeframe)
            started = time.perf_counter()
            found = detector.detect(bars, as_of_session, context=context)
            result.timings[name] = time.perf_counter() - started
            result.instances.extend(found)

        result.instances.sort(key=lambda p: (-p.quality, str(p.pattern_type)))
        result.relations = derive_relationships(result.instances, as_of_session)

        if track:
            closes = {instrument_id: float(bars[-1].close)} if bars else None
            result.tracked = self.tracker.observe(result.instances, as_of_session, closes=closes)
        return result

    def scan_incremental(
        self,
        instrument_id: int,
        timeframe: Bartimeframe,
        bars: Sequence[OhlcvBar],
        as_of_session: dt.date,
        *,
        context: PatternContext | None = None,
        track: bool = True,
    ) -> ScanResult:
        """Scan the bounded suffix that can still affect the answer.

        The saving is real -- a ten-year daily series is ~2,500 bars against a
        window of a few hundred -- and bounded by what the detectors can see
        rather than by how little work would be nice. A context is *not*
        truncated alongside the bars: the alignment check would fail, and
        silently re-slicing a benchmark series is how relative strength ends up
        computed from mismatched days. When a context is supplied the scan falls
        back to the full series, which is correct and slower rather than fast
        and wrong.
        """
        if context is not None:
            return self.scan(
                instrument_id, timeframe, bars, as_of_session, context=context, track=track
            )

        window = self.required_history(timeframe)
        suffix = bars[-window:] if window and len(bars) > window else bars
        return self.scan(instrument_id, timeframe, suffix, as_of_session, track=track)

    def scan_timeframes(
        self,
        instrument_id: int,
        series: Mapping[Bartimeframe, Sequence[OhlcvBar]],
        as_of_session: dt.date,
        *,
        track: bool = True,
    ) -> MultiTimeframeResult:
        """Scan several causal series and relate what they find.

        **The scanner never resamples.** Each timeframe's bars are supplied by
        the caller, already aggregated under the same clock. A scanner that
        resampled internally would be deciding for itself which bars form this
        week's candle, and a partial week aggregated from bars the clock has not
        released is the leak the whole timeframe module exists to prevent.
        """
        result = MultiTimeframeResult(instrument_id=instrument_id, as_of_session=as_of_session)
        for timeframe, bars in series.items():
            result.by_timeframe[timeframe] = self.scan(
                instrument_id, timeframe, bars, as_of_session, track=track
            )

        everything = result.all_instances
        within = {
            (r.from_key, r.to_key) for scan in result.by_timeframe.values() for r in scan.relations
        }
        result.cross_relations = [
            relation
            for relation in derive_relationships(everything, as_of_session)
            if (relation.from_key, relation.to_key) not in within
        ]
        return result

    # -- state ---------------------------------------------------------------

    def open_patterns(self) -> list[TrackedPattern]:
        return self.tracker.open_patterns()

    def closed_patterns(self) -> list[TrackedPattern]:
        return self.tracker.closed_patterns()

    def summary(self) -> dict[str, int]:
        return self.tracker.summary()


__all__ = [
    "WARMUP_MULTIPLE",
    "MultiTimeframeResult",
    "PatternScanner",
    "RescanPolicy",
    "ScanResult",
    "SkippedDetector",
]
