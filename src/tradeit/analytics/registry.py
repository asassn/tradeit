"""Formal registry of analytical features.

Every feature the platform computes is declared here with the metadata needed
to reproduce, debug, and version it. The registry exists because a feature
value is meaningless without knowing how it was made: ``rs_score`` computed
against SPY over 120 sessions is a different feature from ``rs_score`` against
QQQ over 250, and if both are stored under one name the resulting dataset is
unusable for backtesting and actively dangerous for model training.

Three things the registry provides that a naming convention cannot:

**A content-addressed feature-set digest.** The set of active feature
definitions hashes to one digest, stored on every `indicator_values` row. A
changed definition produces a different digest, so recomputed values live
alongside the old ones instead of silently overwriting them — and a backtest
can pin the definitions it ran against (ADR-0007).

**Machine-checkable availability rules.** ``warmup_periods`` and
``availability`` state when a feature is computable at all. Downstream code asks
the registry rather than each engine, and a feature that is unavailable is
distinguishable from one that is legitimately null.

**Null semantics.** ``null_behaviour`` says what a missing value *means*. This
is the field most often omitted and most often needed: a 200-day average that is
null because the instrument listed forty days ago is a different fact from one
that is null because the vendor dropped a session, and imputing zero for either
turns missing data into a strong signal.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tradeit.core.enums import ArtifactKind, Bartimeframe
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import ArtifactVersion, content_hash


class FeatureKind(StrEnum):
    """What kind of thing a feature is, which determines how it may be used."""

    #: Computed from one instrument's own history. Safe to compute in isolation.
    TIME_SERIES = "time_series"
    #: Depends on the whole universe on one date. Leaks across instruments if
    #: the universe is not point-in-time.
    CROSS_SECTIONAL = "cross_sectional"
    #: A property of the market, computed once per date and shared.
    MARKET_LEVEL = "market_level"
    #: A property of a sector, computed from its point-in-time members.
    SECTOR_LEVEL = "sector_level"


class NullBehaviour(StrEnum):
    """What a missing value means. Never impute without consulting this."""

    #: Insufficient history. Will resolve as the instrument ages.
    WARMUP = "warmup"
    #: Genuinely undefined for this instrument (e.g. sector rank, no sector).
    NOT_APPLICABLE = "not_applicable"
    #: Input data was missing or failed quality checks.
    MISSING_INPUT = "missing_input"
    #: The feature is defined but the value could not be computed (e.g. a
    #: division by a zero denominator that is itself meaningful).
    UNDEFINED = "undefined"


class OutputType(StrEnum):
    RATIO = "ratio"
    PRICE = "price"
    PERCENT = "percent"
    SCORE_0_100 = "score_0_100"
    PERCENTILE_0_1 = "percentile_0_1"
    COUNT = "count"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    CURRENCY = "currency"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Everything needed to reproduce, interpret and version one feature."""

    name: str
    version: int
    description: str
    kind: FeatureKind
    timeframe: Bartimeframe
    output_type: OutputType
    null_behaviour: NullBehaviour
    #: Datasets this feature reads. Used to determine which ingestion runs must
    #: have completed before it can be computed, and which data-quality issues
    #: invalidate it.
    input_datasets: tuple[str, ...]
    #: Bars of history required before the first defined value.
    warmup_periods: int
    #: The parameters that define this feature instance. Two specs with the same
    #: name and different parameters are different features and hash differently.
    parameters: dict[str, object] = field(default_factory=dict)
    #: Free-text statement of when the value becomes knowable, beyond the
    #: universal ``knowledge_time <= as_of`` rule.
    availability: str = "available at the close of its session, after ingestion"
    #: Bumped when the calculation changes without the parameters changing --
    #: a bug fix, a different seeding rule. Forces a new digest so corrected
    #: values do not silently mix with the old ones.
    calculation_version: int = 1
    lookback_sessions: int | None = None
    depends_on: tuple[str, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        if self.warmup_periods < 0:
            raise ConfigError(f"{self.name}: warmup_periods cannot be negative")
        if not self.input_datasets:
            raise ConfigError(f"{self.name}: a feature must declare its input datasets")
        if self.version < 1 or self.calculation_version < 1:
            raise ConfigError(f"{self.name}: versions start at 1")

    @property
    def qualified_name(self) -> str:
        """``rs_score_spy_120@v1`` — unique across versions and parameters."""
        return f"{self.name}@v{self.version}.{self.calculation_version}"

    @property
    def digest(self) -> str:
        """Identity of this single feature definition."""
        return content_hash(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        """Canonical form. Excludes ``description`` -- prose is not a definition."""
        return {
            "name": self.name,
            "version": self.version,
            "calculation_version": self.calculation_version,
            "kind": str(self.kind),
            "timeframe": str(self.timeframe),
            "output_type": str(self.output_type),
            "null_behaviour": str(self.null_behaviour),
            "input_datasets": sorted(self.input_datasets),
            "warmup_periods": self.warmup_periods,
            "lookback_sessions": self.lookback_sessions,
            "parameters": dict(sorted(self.parameters.items())),
            "depends_on": sorted(self.depends_on),
        }

    def is_available(self, sessions_of_history: int) -> bool:
        return sessions_of_history >= self.warmup_periods


class FeatureRegistry:
    """The catalogue of active feature definitions.

    Ordinarily built once per run from the strategy configuration, hashed, and
    the digest recorded on every value it produces.
    """

    def __init__(self, specs: Iterable[FeatureSpec] = ()) -> None:
        self._specs: dict[str, FeatureSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        if spec.name in self._specs:
            existing = self._specs[spec.name]
            raise ConfigError(
                f"feature {spec.name!r} is already registered "
                f"({existing.qualified_name}); use a distinct name or bump the version "
                "rather than shadowing it"
            )
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> FeatureSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ConfigError(f"unknown feature {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def all(self) -> list[FeatureSpec]:
        return [self._specs[name] for name in self.names()]

    def of_kind(self, kind: FeatureKind) -> list[FeatureSpec]:
        return [s for s in self.all() if s.kind is kind]

    def for_timeframe(self, timeframe: Bartimeframe) -> list[FeatureSpec]:
        return [s for s in self.all() if s.timeframe is timeframe]

    def active(self) -> list[FeatureSpec]:
        return [s for s in self.all() if not s.deprecated]

    @property
    def max_warmup(self) -> int:
        """History needed before every active feature is defined.

        The number a backtest must skip before it starts trading. Beginning on
        day one with half the features null does not measure the strategy.
        """
        return max((s.warmup_periods for s in self.active()), default=0)

    def required_datasets(self) -> list[str]:
        out: set[str] = set()
        for spec in self.active():
            out.update(spec.input_datasets)
        return sorted(out)

    def validate(self) -> None:
        """Check the registry is internally consistent.

        Catches a dependency on a feature that does not exist, a cycle, and a
        feature whose warm-up is shorter than something it depends on -- the
        last of which produces values computed from NaN inputs and looks like a
        data problem rather than a definition problem.
        """
        for spec in self.all():
            for dependency in spec.depends_on:
                if dependency not in self._specs:
                    raise ConfigError(
                        f"feature {spec.name!r} depends on unknown feature {dependency!r}"
                    )

        visiting: set[str] = set()
        done: set[str] = set()

        def visit(name: str, trail: tuple[str, ...]) -> None:
            if name in done:
                return
            if name in visiting:
                raise ConfigError(f"feature dependency cycle: {' -> '.join([*trail, name])}")
            visiting.add(name)
            for dependency in self._specs[name].depends_on:
                visit(dependency, (*trail, name))
            visiting.discard(name)
            done.add(name)

        for spec in self.all():
            visit(spec.name, ())

        for spec in self.all():
            for dependency in spec.depends_on:
                upstream = self._specs[dependency]
                if spec.warmup_periods < upstream.warmup_periods:
                    raise ConfigError(
                        f"feature {spec.name!r} warms up in {spec.warmup_periods} sessions "
                        f"but depends on {dependency!r} which needs {upstream.warmup_periods}; "
                        "it would compute from undefined inputs"
                    )

    # -- versioning ----------------------------------------------------------

    def to_payload(self) -> dict[str, object]:
        return {"features": [spec.to_payload() for spec in self.active()]}

    @property
    def digest(self) -> str:
        """One hash for the whole active feature set."""
        return content_hash(self.to_payload())

    def version(
        self, name: str = "feature_set", created_at: dt.datetime | None = None
    ) -> ArtifactVersion:
        return ArtifactVersion.of(ArtifactKind.FEATURE_SET, name, self.to_payload(), created_at)

    def describe(self) -> list[dict[str, object]]:
        """Tabular summary for documentation and the API."""
        return [
            {
                "name": spec.name,
                "version": spec.qualified_name,
                "kind": str(spec.kind),
                "timeframe": str(spec.timeframe),
                "output": str(spec.output_type),
                "warmup": spec.warmup_periods,
                "nulls": str(spec.null_behaviour),
                "inputs": ", ".join(sorted(spec.input_datasets)),
                "description": spec.description,
            }
            for spec in self.active()
        ]

    def diff(self, other: FeatureRegistry) -> dict[str, list[str]]:
        """What changed between two registries. For reviewing a feature change."""
        mine, theirs = set(self._specs), set(other._specs)
        changed = [
            name
            for name in sorted(mine & theirs)
            if self._specs[name].digest != other._specs[name].digest
        ]
        return {
            "added": sorted(theirs - mine),
            "removed": sorted(mine - theirs),
            "changed": changed,
        }


def merge(registries: Sequence[FeatureRegistry]) -> FeatureRegistry:
    """Combine registries from several engines into the run's feature set."""
    merged = FeatureRegistry()
    for registry in registries:
        for spec in registry.all():
            merged.register(spec)
    merged.validate()
    return merged
