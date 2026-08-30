"""Content-addressed versioning for anything that affects a decision.

The requirement is that every historical signal be reproducible exactly as it
existed at that moment. Reproducing a decision means pinning five things:

1. **When** it was made — the ``as_of`` instant (Phase 1's ``AsOfClock``).
2. **What the rules were** — the strategy configuration.
3. **What data was visible** — bounded by ``as_of``, but also by *which* vendor
   and which ingestion runs had completed.
4. **How features were computed** — the feature-set definition and its code.
5. **Which model scored it**, if any.

Sequential version numbers do not achieve this. `v3` tells you nothing about
whether two runs used the same rules, and nothing stops someone editing `v3` in
place. So versions here are **content hashes**: the identity of a configuration
*is* its content. Two runs with the same hash provably used the same rules;
changing a single threshold produces a different hash and therefore a different
identity, with no discipline required from anyone.

The canonicalisation matters. JSON with sorted keys, no insignificant
whitespace, and decimals rendered as strings — so that `0.30`, `0.3` and
`Decimal("0.30")` do not produce three different hashes for one configuration.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tradeit.core.enums import ArtifactKind
from tradeit.errors import ConfigError

#: Length of the short hash used in filenames, log lines and UI labels. Full
#: hashes are stored; short ones are for humans. 12 hex chars is ~48 bits, which
#: is comfortable for the thousands of configurations a project accumulates.
SHORT_HASH_LENGTH = 12


def canonical_json(payload: Any) -> str:
    """Render a payload to the one string that represents it.

    Deterministic across processes and Python versions: keys sorted, no
    whitespace padding, non-ASCII preserved rather than escaped, and Decimals
    rendered via ``str`` so numeric formatting cannot change the hash.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise ConfigError(
        f"{type(value).__name__} is not canonically serialisable; a configuration "
        "value whose serialisation is ambiguous cannot be version-hashed"
    )


def content_hash(payload: Any) -> str:
    """SHA-256 of the canonical rendering. The identity of a configuration."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def short_hash(full: str) -> str:
    return full[:SHORT_HASH_LENGTH]


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    """An immutable, content-addressed reference to something reproducible.

    ``kind`` distinguishes the four things that must be pinned together;
    ``name`` is the human label (``"breakout_v2"``); ``digest`` is the identity.
    Two artifacts with the same digest are the same artifact regardless of name,
    which is how a renamed-but-unchanged strategy is recognised as unchanged.
    """

    kind: ArtifactKind
    name: str
    digest: str
    created_at: dt.datetime
    payload: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def of(
        cls,
        kind: ArtifactKind,
        name: str,
        payload: dict[str, Any],
        created_at: dt.datetime | None = None,
    ) -> ArtifactVersion:
        return cls(
            kind=kind,
            name=name,
            digest=content_hash(payload),
            created_at=created_at or dt.datetime.now(tz=dt.UTC),
            payload=payload,
        )

    @property
    def short(self) -> str:
        return short_hash(self.digest)

    @property
    def label(self) -> str:
        """What appears in logs and UI: ``breakout_v2@a1b2c3d4e5f6``."""
        return f"{self.name}@{self.short}"

    def matches(self, other: ArtifactVersion) -> bool:
        return self.kind is other.kind and self.digest == other.digest


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Everything needed to replay one decision-producing run.

    Stored alongside every scan, backtest and paper-trading session. If a
    manifest is complete, the run can be reproduced; if reproducing it yields a
    different answer, either the code changed (``code_version``) or something
    that should have been pinned was not — and that gap is itself the finding.

    ``data_snapshot`` deserves explanation. Bounding reads by ``as_of`` is not
    quite sufficient for reproducibility: if an ingestion run backfills a
    revision *after* a scan, replaying that scan at the same ``as_of`` now sees
    a row it did not see before — legitimately, because that row's
    ``knowledge_time`` is before ``as_of``, but it had not been loaded yet. The
    snapshot pins the maximum ingestion-run id per dataset, which makes replay
    exact rather than merely honest.
    """

    run_id: str
    as_of: dt.datetime
    strategy_config: ArtifactVersion
    data_snapshot: ArtifactVersion
    feature_set: ArtifactVersion | None
    model: ArtifactVersion | None
    code_version: str
    created_at: dt.datetime

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ConfigError("RunManifest.as_of must be timezone-aware")
        expected = {
            "strategy_config": ArtifactKind.STRATEGY_CONFIG,
            "data_snapshot": ArtifactKind.DATA_SNAPSHOT,
            "feature_set": ArtifactKind.FEATURE_SET,
            "model": ArtifactKind.MODEL,
        }
        for attribute, kind in expected.items():
            artifact = getattr(self, attribute)
            if artifact is not None and artifact.kind is not kind:
                raise ConfigError(f"{attribute} must be a {kind} artifact, got {artifact.kind}")

    @property
    def digest(self) -> str:
        """One hash identifying the entire reproducible context."""
        return content_hash(
            {
                "as_of": self.as_of.isoformat(),
                "strategy_config": self.strategy_config.digest,
                "data_snapshot": self.data_snapshot.digest,
                "feature_set": self.feature_set.digest if self.feature_set else None,
                "model": self.model.digest if self.model else None,
                "code_version": self.code_version,
            }
        )

    def describe(self) -> dict[str, str | None]:
        """Flat summary for logs, reports and the API."""
        return {
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "manifest_digest": short_hash(self.digest),
            "strategy_config": self.strategy_config.label,
            "data_snapshot": self.data_snapshot.label,
            "feature_set": self.feature_set.label if self.feature_set else None,
            "model": self.model.label if self.model else None,
            "code_version": self.code_version,
        }

    def reproduces(self, other: RunManifest) -> bool:
        """Whether two runs had identical reproducible context.

        Ignores ``run_id`` and ``created_at`` — the same decision made twice is
        the same decision.
        """
        return self.digest == other.digest
