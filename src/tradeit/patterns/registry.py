"""The detector registry, and the Phase 4 validation baseline.

**Why a registry rather than a list of imports.** Three requirements meet here
and none of them is satisfiable by constructing detectors ad hoc at each call
site:

1. **Enable/disable per detector** (Phase 4 gate item 16). Not every strategy
   will want all twelve families, and a detector absent from a run must be
   absent from the *dataset* rather than silently skipped — otherwise a stored
   pattern set cannot be reproduced from its configuration digest alone.
2. **Supported timeframes per detector.** A high tight flag is a claim about an
   eight-week advance; running it on 15-minute bars produces a structure the
   family's literature has nothing to say about. The registry records where each
   family is meaningful, and the scanner refuses the rest rather than reporting
   numbers nobody has grounds to interpret.
3. **A frozen baseline.** The remaining Phase 4 validation work has to be run
   against a fixed set of detector versions and configuration content. Without
   a snapshot, "the adversarial distribution moved" is ambiguous between "the
   detector changed" and "the corpus changed", and the whole exercise stops
   being a measurement.

**The baseline is a content hash, not a version number.** Following ADR-0007:
the identity of a configuration *is* its content. ``BASELINE`` below pins the
twelve detector versions explicitly, and :func:`baseline_digest` hashes the
whole engine configuration, so a threshold edited anywhere produces a different
digest and the mismatch is visible immediately rather than after someone
notices the numbers moved.

**Timeframe support is deliberately conservative.** Where a family's structural
definition is stated in weeks (high tight flag, cup and handle, base-on-base),
intraday timeframes are excluded — not because the geometry cannot be computed
there, but because the definition would be a different one and reporting it
under the same name would misrepresent what is known about it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.detectors import (
    AscendingTriangleDetector,
    BaseDetector,
    BaseOnBaseDetector,
    BreakoutRetestDetector,
    BullFlagDetector,
    CupHandleDetector,
    DoubleBottomDetector,
    FlatBaseDetector,
    HighTightFlagDetector,
    InverseHeadShouldersDetector,
    PennantDetector,
    TightConsolidationDetector,
    VcpDetector,
)
from tradeit.reproducibility.versioning import content_hash, short_hash

#: Every detector class, keyed by the name the configuration uses.
DETECTOR_CLASSES: Mapping[str, Any] = {
    "bull_flag": BullFlagDetector,
    "vcp": VcpDetector,
    "flat_base": FlatBaseDetector,
    "ascending_triangle": AscendingTriangleDetector,
    "pennant": PennantDetector,
    "cup_handle": CupHandleDetector,
    "high_tight_flag": HighTightFlagDetector,
    "double_bottom": DoubleBottomDetector,
    "inverse_head_shoulders": InverseHeadShouldersDetector,
    "base_on_base": BaseOnBaseDetector,
    "tight_consolidation": TightConsolidationDetector,
    "breakout_retest": BreakoutRetestDetector,
}

#: The order the roadmap fixed. Preserved so reports, tables and iteration all
#: read the same way, and so a reader comparing two runs is comparing rows.
DETECTOR_ORDER: tuple[str, ...] = (
    "bull_flag",
    "vcp",
    "flat_base",
    "ascending_triangle",
    "pennant",
    "cup_handle",
    "high_tight_flag",
    "double_bottom",
    "inverse_head_shoulders",
    "base_on_base",
    "tight_consolidation",
    "breakout_retest",
)

_INTRADAY: tuple[Bartimeframe, ...] = (Bartimeframe.H4, Bartimeframe.H1, Bartimeframe.M15)
_SWING: tuple[Bartimeframe, ...] = (Bartimeframe.W1, Bartimeframe.D1)
_ALL: tuple[Bartimeframe, ...] = (*_SWING, *_INTRADAY)

#: Where each family's structural definition is meaningful.
#:
#: The short-horizon continuation and structural families run everywhere: a
#: flag, a pennant, a triangle, a tight range and a retest are all statements
#: about relative geometry that survive a change of bar size.
#:
#: The families excluded from intraday are excluded because their definitions
#: are stated in weeks and their thresholds were chosen against that. A "high
#: tight flag" on 15-minute bars would be a 70% advance over eight *hours*,
#: which is a different phenomenon wearing the name of a studied one. A cup is
#: an accumulation process measured in months. Base-on-base is a claim about
#: two multi-week bases. The geometry is computable; the *claim* is not the same
#: claim, and Phase 4 has no evidence about the intraday version.
SUPPORTED_TIMEFRAMES: Mapping[str, tuple[Bartimeframe, ...]] = {
    "bull_flag": _ALL,
    "vcp": (*_SWING, Bartimeframe.H4),
    "flat_base": (*_SWING, Bartimeframe.H4),
    "ascending_triangle": _ALL,
    "pennant": _ALL,
    "cup_handle": _SWING,
    "high_tight_flag": _SWING,
    "double_bottom": (*_SWING, Bartimeframe.H4),
    "inverse_head_shoulders": (*_SWING, Bartimeframe.H4),
    "base_on_base": _SWING,
    "tight_consolidation": _ALL,
    "breakout_retest": _ALL,
}

#: Family type, for the interpretation matrix and for readers.
FAMILY_KIND: Mapping[str, str] = {
    "bull_flag": "continuation",
    "vcp": "continuation",
    "flat_base": "continuation",
    "ascending_triangle": "continuation",
    "pennant": "continuation",
    "cup_handle": "continuation",
    "high_tight_flag": "continuation",
    "double_bottom": "reversal",
    "inverse_head_shoulders": "reversal",
    "base_on_base": "structural",
    "tight_consolidation": "structural",
    "breakout_retest": "structural",
}


@dataclass(frozen=True, slots=True)
class DetectorEntry:
    """One detector's registration: what it is and where it may run."""

    name: str
    pattern_type: PatternType
    version: int
    kind: str
    enabled: bool
    timeframes: tuple[Bartimeframe, ...]
    minimum_bars: int
    required_components: tuple[str, ...]
    optional_components: tuple[str, ...]
    minimum_evidence_coverage: float

    def supports(self, timeframe: Bartimeframe) -> bool:
        return timeframe in self.timeframes

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "pattern_type": str(self.pattern_type),
            "version": self.version,
            "kind": self.kind,
            "enabled": self.enabled,
            "timeframes": [str(t) for t in self.timeframes],
            "minimum_bars": self.minimum_bars,
            "required_components": list(self.required_components),
            "optional_components": list(self.optional_components),
            "minimum_evidence_coverage": self.minimum_evidence_coverage,
        }


@dataclass(slots=True)
class DetectorRegistry:
    """Constructed detectors plus their registration metadata.

    Built from a :class:`PatternEngineConfig`, so the set of detectors that runs
    and the numbers they run with come from one object with one digest.
    """

    config: PatternEngineConfig
    entries: dict[str, DetectorEntry] = field(default_factory=dict)
    _detectors: dict[tuple[str, Bartimeframe], BaseDetector] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: PatternEngineConfig | None = None) -> DetectorRegistry:
        engine = config or PatternEngineConfig()
        registry = cls(config=engine)
        enabled = set(engine.enabled_detectors)
        unknown = enabled - set(DETECTOR_CLASSES)
        if unknown:
            raise ConfigError(
                f"enabled_detectors names no such detector: {sorted(unknown)}; "
                f"known detectors are {sorted(DETECTOR_CLASSES)}"
            )
        for name in DETECTOR_ORDER:
            prototype = DETECTOR_CLASSES[name](engine)
            registry.entries[name] = DetectorEntry(
                name=name,
                pattern_type=prototype.pattern_type,
                version=prototype.version,
                kind=FAMILY_KIND[name],
                enabled=name in enabled,
                timeframes=SUPPORTED_TIMEFRAMES[name],
                minimum_bars=prototype.minimum_bars,
                required_components=prototype.contract.required_components,
                optional_components=prototype.contract.optional_components,
                minimum_evidence_coverage=prototype.contract.minimum_evidence_coverage,
            )
        return registry

    # -- access --------------------------------------------------------------

    def detector(self, name: str, timeframe: Bartimeframe = Bartimeframe.D1) -> BaseDetector:
        """A detector bound to a timeframe.

        Cached per (name, timeframe) because detectors are stateless and
        constructing twelve of them per instrument per timeframe is pure waste
        in a four-thousand-name scan.
        """
        entry = self.entries.get(name)
        if entry is None:
            raise ConfigError(f"no detector named {name!r}")
        if not entry.supports(timeframe):
            raise ConfigError(
                f"detector {name!r} does not support {timeframe}; its structural "
                f"definition is stated for {[str(t) for t in entry.timeframes]}, and "
                "running it elsewhere would report a number nobody has grounds to read"
            )
        key = (name, timeframe)
        cached = self._detectors.get(key)
        if cached is None:
            cached = DETECTOR_CLASSES[name](self.config, timeframe=timeframe)
            self._detectors[key] = cached
        return cached

    def enabled_for(self, timeframe: Bartimeframe) -> list[BaseDetector]:
        """Every enabled detector that supports this timeframe, in fixed order."""
        return [
            self.detector(name, timeframe)
            for name in DETECTOR_ORDER
            if self.entries[name].enabled and self.entries[name].supports(timeframe)
        ]

    def all_entries(self) -> list[DetectorEntry]:
        return [self.entries[name] for name in DETECTOR_ORDER]

    def versions(self) -> dict[str, int]:
        return {name: self.entries[name].version for name in DETECTOR_ORDER}

    # -- provenance ----------------------------------------------------------

    def digest(self) -> str:
        """Content hash of everything that affects what this registry produces."""
        return baseline_digest(self.config)

    def to_payload(self) -> dict[str, object]:
        return {
            "digest": self.digest(),
            "detectors": [entry.to_payload() for entry in self.all_entries()],
        }


def baseline_digest(config: PatternEngineConfig | None = None) -> str:
    """Content hash of the engine configuration plus detector versions.

    Both halves are needed. The configuration alone misses a detector whose
    *code* changed without its numbers moving; the versions alone miss a
    threshold edited in place. Together they are what a stored pattern needs in
    order to be reproducible.
    """
    engine = config or PatternEngineConfig()
    versions = {name: DETECTOR_CLASSES[name](engine).version for name in DETECTOR_ORDER}
    return content_hash({"config": engine.model_dump(mode="json"), "versions": versions})


@dataclass(frozen=True, slots=True)
class Baseline:
    """The frozen Phase 4 validation baseline.

    Recorded as data rather than prose so a test can assert against it. When a
    detector is corrected during the validation gate, its version is incremented
    *here* as well as in its config, and the mismatch between the two is what
    makes an unrecorded change fail loudly.
    """

    label: str
    versions: Mapping[str, int]
    digest: str

    def check(self, config: PatternEngineConfig | None = None) -> list[str]:
        """Differences between this baseline and a live configuration.

        Returns prose rather than raising, because the caller's response differs:
        a test fails, a report footnotes, and a deliberate revalidation run says
        so and carries on.
        """
        engine = config or PatternEngineConfig()
        problems: list[str] = []
        for name in DETECTOR_ORDER:
            live = DETECTOR_CLASSES[name](engine).version
            expected = self.versions.get(name)
            if expected is None:
                problems.append(f"{name}: absent from the baseline")
            elif live != expected:
                problems.append(f"{name}: baseline v{expected}, live v{live}")
        live_digest = baseline_digest(engine)
        if live_digest != self.digest:
            problems.append(
                f"engine digest {short_hash(live_digest)} does not match the baseline's "
                f"{short_hash(self.digest)}"
            )
        return problems


#: Detector versions frozen for the Phase 4 validation gate.
#:
#: Five families sit at v2. All five were corrected during the gate for the same
#: defect: pattern identity is a content hash of the structural start, but their
#: discovery deduplicated on a composite key -- the cup on (left rim, right rim),
#: the double bottom on (first low, second low), and so on -- which permitted two
#: structures to share a start and therefore an identity. More than half of the
#: cup detector's instances and three quarters of the inverse head and
#: shoulders' were colliding when it was measured.
#:
#: The correction is definitional rather than performance tuning: no threshold
#: moved, no weight changed, and no score was made better. What changed is that
#: a detector no longer emits two structures the persistence layer cannot tell
#: apart. The breakout retest also had its structural start moved from the
#: lookback window's edge -- an artefact of how far the search reached -- to the
#: level's first touch, which is a structural fact.
#:
#: A correction made *after* this baseline is cut increments the entry here, and
#: :meth:`Baseline.check` reports the drift until the baseline is re-cut
#: deliberately.
PHASE4_VERSIONS: Mapping[str, int] = {
    "bull_flag": 1,
    "vcp": 1,
    "flat_base": 1,
    "ascending_triangle": 1,
    "pennant": 1,
    "cup_handle": 2,
    "high_tight_flag": 1,
    "double_bottom": 2,
    "inverse_head_shoulders": 2,
    "base_on_base": 2,
    "tight_consolidation": 1,
    "breakout_retest": 2,
}


def phase4_baseline(config: PatternEngineConfig | None = None) -> Baseline:
    """The baseline for this gate, computed from the shipped defaults."""
    engine = config or PatternEngineConfig()
    return Baseline(
        label="phase-4-validation",
        versions=dict(PHASE4_VERSIONS),
        digest=baseline_digest(engine),
    )


def detector_names(
    only_enabled: bool = False, config: PatternEngineConfig | None = None
) -> list[str]:
    registry = DetectorRegistry.from_config(config)
    return [name for name in DETECTOR_ORDER if not only_enabled or registry.entries[name].enabled]


def timeframes_for(names: Iterable[str]) -> dict[str, tuple[Bartimeframe, ...]]:
    return {name: SUPPORTED_TIMEFRAMES[name] for name in names}


__all__ = [
    "DETECTOR_CLASSES",
    "DETECTOR_ORDER",
    "FAMILY_KIND",
    "PHASE4_VERSIONS",
    "SUPPORTED_TIMEFRAMES",
    "Baseline",
    "DetectorEntry",
    "DetectorRegistry",
    "baseline_digest",
    "detector_names",
    "phase4_baseline",
    "timeframes_for",
]
