"""Declared feature sets: what a consumer actually uses.

The registry is a *catalogue*. It answers "does this feature exist, how is it
defined, when is it computable". It does not, and must not, answer "which
features should this model read".

Conflating the two is one of the most expensive mistakes available in a
quantitative system, and it is silent. If a scorer or a model consumes
``registry.active()``, then adding one indicator to an engine changes that
model's input surface without anyone editing the model, without a review, and
without a version bump. The model's historical results stop being reproducible
because the thing they were produced by no longer exists. Worse, features get
consumed that were never intended for consumption at all: diagnostics, raw
inputs, intermediate quantities, and near-duplicate variants that quietly
triple the weight of one underlying idea.

So the rule this module enforces:

    **The existence of a feature in the registry does not entitle any consumer
    to read it. Consumption requires declaration.**

A :class:`FeatureSet` is that declaration — named, versioned, hashed, and
resolvable against a registry. :meth:`ResolvedFeatureSet.view` gives a consumer
a read surface that raises on anything undeclared, so the rule is enforced at
the point of access rather than trusted to discipline.

Nothing here selects, weights or optimises features. Choosing *which* features
belong in a set is a modelling decision for later phases; this module only
makes the choice explicit, auditable and pinned.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from tradeit.analytics.registry import FeatureRegistry, FeatureSpec
from tradeit.core.enums import ArtifactKind
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import ArtifactVersion, content_hash

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for typing
    from tradeit.analytics.eligibility import EligibilityPolicy


class FeatureAccessError(ConfigError):
    """A consumer read a feature it did not declare.

    Raised rather than returning ``None`` because a silent ``None`` for an
    undeclared feature is indistinguishable from a legitimate warm-up null, and
    a model that trains on that difference is training on a bug.
    """


class ConsumerKind(StrEnum):
    """What is doing the consuming.

    Recorded because the acceptable looseness differs sharply. A research
    notebook browsing everything is fine; a production scorer doing the same is
    an unversioned dependency on the entire catalogue.
    """

    #: Boolean/threshold filters that reduce the universe.
    SCREEN = "screen"
    #: Numerical opportunity scoring (Phase 7).
    SCORING = "scoring"
    #: Chart-pattern classification (Phase 4).
    PATTERN = "pattern"
    #: Breakout detection and confirmation (Phase 5).
    BREAKOUT = "breakout"
    #: Portfolio construction and risk (Phase 8).
    PORTFOLIO = "portfolio"
    #: A trained statistical model. The strictest case: its input vector is part
    #: of its identity, and changing it invalidates every result it produced.
    MODEL = "model"
    #: Human-facing exploration. Broad sets are legitimate here, and results
    #: from a research set may not be promoted without a narrower declaration.
    RESEARCH = "research"
    #: Operational monitoring. Never feeds a trading decision.
    DIAGNOSTIC = "diagnostic"

    @property
    def feeds_trading_decisions(self) -> bool:
        return self not in (ConsumerKind.RESEARCH, ConsumerKind.DIAGNOSTIC)


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """A consumer's declared inputs.

    Deliberately a plain list of names rather than a predicate or a pattern.
    ``"everything matching rs_*"`` reintroduces exactly the problem this
    module exists to prevent: the set would change meaning when a new ``rs_``
    feature is registered, and the change would not appear in any diff of this
    declaration.
    """

    name: str
    consumer: ConsumerKind
    version: int
    features: tuple[str, ...]
    description: str = ""
    #: Optional per-feature justification. Not enforced, but a set that feeds
    #: trading decisions and cannot say why it wants a feature is a set worth
    #: reviewing.
    rationale: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a feature set must be named")
        if self.version < 1:
            raise ConfigError(f"{self.name}: versions start at 1")
        if not self.features:
            raise ConfigError(
                f"{self.name}: declares no features; an empty declaration is not the same "
                "as 'use everything' and will not be treated as such"
            )
        duplicates = sorted({n for n in self.features if self.features.count(n) > 1})
        if duplicates:
            raise ConfigError(f"{self.name}: duplicate features declared: {duplicates}")
        unknown = sorted(set(self.rationale) - set(self.features))
        if unknown:
            raise ConfigError(f"{self.name}: rationale given for undeclared features: {unknown}")

    @property
    def qualified_name(self) -> str:
        return f"{self.name}@v{self.version}"

    def to_payload(self) -> dict[str, Any]:
        """Canonical form. Excludes prose, includes the declared names."""
        return {
            "name": self.name,
            "consumer": str(self.consumer),
            "version": self.version,
            "features": sorted(self.features),
        }

    @property
    def declaration_digest(self) -> str:
        """Identity of the declaration alone, ignoring feature definitions.

        Changes when the consumer changes its mind about which features it
        wants. Distinct from :attr:`ResolvedFeatureSet.digest`, which also
        changes when a declared feature's *definition* changes underneath it --
        both matter, and confusing them makes a recomputation look like a
        modelling change or vice versa.
        """
        return content_hash(self.to_payload())

    def resolve(self, registry: FeatureRegistry) -> ResolvedFeatureSet:
        """Bind the declaration to a registry, or explain why it cannot bind.

        Dependencies are pulled in transitively. A consumer that declares
        ``rs_percentile`` gets ``rs_score`` in its resolved registry whether it
        asked or not, because otherwise the resolved warm-up would understate
        what the feature genuinely needs.
        """
        missing = sorted(n for n in self.features if n not in registry)
        if missing:
            raise ConfigError(
                f"{self.qualified_name}: declares features absent from the registry: "
                f"{missing}; either the feature was renamed or the wrong registry was "
                "supplied -- resolving against a registry that lacks them would silently "
                "shrink the consumer's inputs"
            )

        deprecated = sorted(n for n in self.features if registry.get(n).deprecated)
        resolved = registry.subset(self.features, with_dependencies=True)
        implied = tuple(sorted(set(resolved.names()) - set(self.features)))
        return ResolvedFeatureSet(
            declaration=self,
            registry=resolved,
            implied_dependencies=implied,
            deprecated_features=tuple(deprecated),
        )


@dataclass(frozen=True, slots=True)
class ResolvedFeatureSet:
    """A declaration bound to concrete definitions.

    This is the object a consumer holds and a run manifest pins. It knows the
    exact definition of every feature it will read, so its digest changes if
    either the choice or the mathematics changes.
    """

    declaration: FeatureSet
    registry: FeatureRegistry
    #: Features present because something declared depends on them.
    implied_dependencies: tuple[str, ...] = ()
    #: Declared features whose definitions are marked deprecated. Not fatal --
    #: a backtest reproducing an old run legitimately needs them -- but a live
    #: consumer resting on one should be visible.
    deprecated_features: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.declaration.name

    @property
    def names(self) -> list[str]:
        """Every feature that will be computed, declared and implied alike."""
        return self.registry.names()

    @property
    def specs(self) -> list[FeatureSpec]:
        return self.registry.all()

    @property
    def warmup(self) -> int:
        """Sessions of history before *this consumer* can act.

        The number that belongs in an eligibility decision -- not
        ``registry.max_warmup``, which is the warm-up of the longest feature in
        the whole catalogue and makes every strategy wait for the slowest one
        regardless of whether it reads it.
        """
        return self.registry.max_warmup

    @property
    def required_datasets(self) -> list[str]:
        """Which ingestion runs must have completed before this set computes."""
        return self.registry.required_datasets()

    @property
    def digest(self) -> str:
        """Identity of choice *and* definition together."""
        return content_hash(
            {
                "declaration": self.declaration.to_payload(),
                "definitions": self.registry.to_payload(),
            }
        )

    def version(self, created_at: dt.datetime | None = None) -> ArtifactVersion:
        return ArtifactVersion.of(
            ArtifactKind.FEATURE_SET,
            self.declaration.qualified_name,
            {
                "declaration": self.declaration.to_payload(),
                "definitions": self.registry.to_payload(),
            },
            created_at,
        )

    def eligibility_policy(
        self, *, min_data_sessions: int, name: str | None = None
    ) -> EligibilityPolicy:
        """The eligibility policy implied by this set.

        The join between item 1 and item 2: readiness is per strategy precisely
        because the feature set is per strategy. A consumer reading six 20-day
        features is ready in twenty sessions and should not be made to wait a
        year for an ADX it never reads.
        """
        from tradeit.analytics.eligibility import EligibilityPolicy

        return EligibilityPolicy(
            min_data_sessions=min_data_sessions,
            required_features=self.specs,
            name=name or self.declaration.qualified_name,
        )

    # -- enforcement ---------------------------------------------------------

    def select(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Narrow a wide feature row to this set, refusing to guess.

        A missing declared feature raises rather than being dropped: a scorer
        silently operating on eight of its ten inputs produces a plausible
        number that is not the number it was validated on.
        """
        missing = sorted(n for n in self.names if n not in values)
        if missing:
            raise FeatureAccessError(
                f"{self.declaration.qualified_name}: declared features absent from the "
                f"supplied row: {missing}"
            )
        return {name: values[name] for name in self.names}

    def view(self, values: Mapping[str, Any]) -> FeatureView:
        """A read surface that raises on undeclared access."""
        return FeatureView(self, dict(values))

    def audit(self) -> dict[str, Any]:
        """Reviewable summary of what this consumer consumes and why."""
        undocumented = sorted(set(self.declaration.features) - set(self.declaration.rationale))
        return {
            "name": self.declaration.qualified_name,
            "consumer": str(self.declaration.consumer),
            "declared": len(self.declaration.features),
            "implied_dependencies": list(self.implied_dependencies),
            "deprecated": list(self.deprecated_features),
            "warmup_sessions": self.warmup,
            "required_datasets": self.required_datasets,
            "declaration_digest": self.declaration.declaration_digest,
            "digest": self.digest,
            "undocumented": undocumented
            if self.declaration.consumer.feeds_trading_decisions
            else [],
        }


@dataclass(slots=True)
class FeatureView:
    """A feature row restricted to one declaration.

    Reading an undeclared name raises. This is what turns the architectural
    rule into something a test can catch, rather than a convention that erodes
    the first time a deadline meets a tempting nearby feature.
    """

    feature_set: ResolvedFeatureSet
    _values: dict[str, Any]

    def __getitem__(self, name: str) -> Any:
        if name not in self.feature_set.names:
            raise FeatureAccessError(
                f"{self.feature_set.declaration.qualified_name} did not declare {name!r}; "
                f"add it to the declaration (and bump the version) rather than reading it "
                "opportunistically -- an undeclared input is an unversioned dependency"
            )
        try:
            return self._values[name]
        except KeyError:
            raise FeatureAccessError(
                f"{name!r} is declared but was not computed for this row"
            ) from None

    def get(self, name: str, default: Any = None) -> Any:
        """Like ``__getitem__`` but tolerant of a *computed* null.

        Still raises for an *undeclared* name: the point is to permit missing
        values, not undeclared inputs.
        """
        if name not in self.feature_set.names:
            raise FeatureAccessError(
                f"{self.feature_set.declaration.qualified_name} did not declare {name!r}"
            )
        return self._values.get(name, default)

    def __contains__(self, name: object) -> bool:
        return name in self.feature_set.names and name in self._values

    def __len__(self) -> int:
        return len(self.feature_set.names)

    def as_dict(self) -> dict[str, Any]:
        return {name: self._values.get(name) for name in self.feature_set.names}


class FeatureSetCatalogue:
    """Every declared feature set in the system.

    Exists so that "which consumers read this feature?" is answerable before
    changing a definition. Without it, a calculation change is reviewed against
    the engine that produces the feature rather than against everything that
    depends on it.
    """

    def __init__(self, sets: Iterable[FeatureSet] = ()) -> None:
        self._sets: dict[str, FeatureSet] = {}
        for declaration in sets:
            self.declare(declaration)

    def declare(self, declaration: FeatureSet) -> FeatureSet:
        key = declaration.qualified_name
        if key in self._sets:
            raise ConfigError(
                f"feature set {key} is already declared; bump the version rather than "
                "redefining a version that other artefacts may already reference"
            )
        self._sets[key] = declaration
        return declaration

    def get(self, qualified_name: str) -> FeatureSet:
        try:
            return self._sets[qualified_name]
        except KeyError:
            raise ConfigError(f"unknown feature set {qualified_name!r}") from None

    def __len__(self) -> int:
        return len(self._sets)

    def all(self) -> list[FeatureSet]:
        return [self._sets[k] for k in sorted(self._sets)]

    def consumers_of(self, feature_name: str) -> list[str]:
        """Which declarations would be affected by changing this feature."""
        return sorted(
            declaration.qualified_name
            for declaration in self.all()
            if feature_name in declaration.features
        )

    def unconsumed(self, registry: FeatureRegistry) -> list[str]:
        """Registry features no declaration reads.

        Not a defect list. Diagnostics and raw inputs are *meant* to be
        unconsumed, and a feature computed for research is legitimately here.
        It is a review aid: a feature nothing declares is one nobody has to
        keep working.
        """
        declared: set[str] = set()
        for declaration in self.all():
            declared.update(declaration.features)
        return [name for name in registry.names() if name not in declared]

    def resolve_all(self, registry: FeatureRegistry) -> dict[str, ResolvedFeatureSet]:
        return {
            declaration.qualified_name: declaration.resolve(registry) for declaration in self.all()
        }


def declare(
    name: str,
    *,
    consumer: ConsumerKind,
    features: Sequence[str],
    version: int = 1,
    description: str = "",
    rationale: Mapping[str, str] | None = None,
) -> FeatureSet:
    """Convenience constructor, so declarations read as declarations."""
    return FeatureSet(
        name=name,
        consumer=consumer,
        version=version,
        features=tuple(features),
        description=description,
        rationale=dict(rationale or {}),
    )
