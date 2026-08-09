"""What a check is handed, and why each piece of it is pinned.

A validation result is only worth as much as the reader's ability to say what
produced it. So the context is not "a database session" — it is a session
*plus* every identifier needed to reproduce the run: which package snapshot,
which as-of instant, which universe, which configuration digests, which code
version.

The awkward part is deliberate. Constructing a context requires naming a
snapshot, and a snapshot is a row written by an import that verified its own
file digests. There is no path from "I have a database somewhere" to a
validation result, because that path is how a result ends up describing data
nobody can identify afterwards.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.data.packages.spec import DatasetKind
from tradeit.data.validation_universe import ValidationUniverse, default_universe
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import content_hash
from tradeit.storage import tables as t
from tradeit.validation.checks import CheckClock

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


@dataclass(slots=True)
class ValidationContext:
    """Everything a check may read, and everything the report must record."""

    session: Session
    package: t.DataPackage
    clock: CheckClock
    universe: ValidationUniverse = field(default_factory=default_universe)
    code_version: str = "unknown"
    #: Configuration digests for the layers under test, so a later run that
    #: differs can say whether the data changed or the settings did.
    config_digests: dict[str, str] = field(default_factory=dict)
    _datasets: frozenset[DatasetKind] | None = field(default=None, init=False)

    @property
    def as_of(self) -> dt.datetime:
        return self.clock.as_of

    @property
    def snapshot_id(self) -> str:
        return self.package.snapshot_id

    @property
    def datasets(self) -> frozenset[DatasetKind]:
        """Which datasets this snapshot actually imported.

        Read from ``data_package_files`` rather than from the manifest object,
        so a dataset declared but never successfully read is not counted as
        present.
        """
        if self._datasets is None:
            rows = self.session.scalars(
                select(t.DataPackageFile.dataset).where(
                    t.DataPackageFile.package_id == self.package.id
                )
            ).all()
            kinds = set()
            for name in rows:
                try:
                    kinds.add(DatasetKind(name))
                except ValueError:  # pragma: no cover - forward compatibility
                    continue
            self._datasets = frozenset(kinds)
        return self._datasets

    def missing(self, required: tuple[DatasetKind, ...]) -> tuple[DatasetKind, ...]:
        return tuple(kind for kind in required if kind not in self.datasets)

    def is_usable(self) -> tuple[bool, str]:
        """Whether this snapshot may be cited as evidence at all.

        Three disqualifications, each of which makes every number computed from
        the snapshot describe something other than the market:

        * a partial import — a row limit was in force, so the sample is
          whatever the first N lines happened to be;
        * an aborted import — the run stopped, so the coverage is unknown;
        * unverified digests — the bytes cannot be attributed to the package.

        An UNKNOWN adjustment policy is not on the list because it disqualifies
        specific checks rather than the whole snapshot; those checks say so
        themselves.
        """
        if self.package.aborted:
            return False, "the import aborted; its coverage is unknown"
        if self.package.partial:
            return False, (
                "the import was partial (a row limit was in force), so the sample is "
                "the first N lines of each file rather than a period"
            )
        if not self.package.digests_verified:
            return False, (
                "file digests were not verified, so the rows cannot be attributed "
                "to the package that claims them"
            )
        return True, ""

    def provenance(self) -> dict[str, Any]:
        """The block every report repeats verbatim."""
        return {
            "snapshot_id": self.package.snapshot_id,
            "package_name": self.package.name,
            "provider": self.package.provider,
            "manifest_digest": self.package.manifest_digest,
            "adjustment_policy": self.package.adjustment_policy,
            "export_date": self.package.export_date.isoformat(),
            "declared_coverage": [
                self.package.coverage_start.isoformat(),
                self.package.coverage_end.isoformat(),
            ],
            "observed_coverage": [
                self.package.observed_start.isoformat() if self.package.observed_start else None,
                self.package.observed_end.isoformat() if self.package.observed_end else None,
            ],
            "rows_imported": self.package.rows_imported,
            "rows_quarantined": self.package.rows_quarantined,
            "rows_estimated_knowledge_time": self.package.rows_estimated_knowledge_time,
            "as_of": self.as_of.isoformat(),
            "universe": self.universe.name,
            "universe_digest": self.universe_digest(),
            "universe_size": len(self.universe),
            "code_version": self.code_version,
            "config_digests": dict(sorted(self.config_digests.items())),
        }

    def universe_digest(self) -> str:
        """Identity of the roster a result was computed over.

        Comparing a reading over 85 names with one over 4,000 is comparing two
        different statistics, and the digest is what makes that detectable
        rather than invisible.
        """
        return content_hash(
            {
                "name": self.universe.name,
                "tickers": sorted(self.universe.tickers),
            }
        )


def load_context(
    session: Session,
    snapshot_id: str,
    *,
    as_of: dt.datetime | None = None,
    universe: ValidationUniverse | None = None,
    code_version: str = "unknown",
    config_digests: dict[str, str] | None = None,
) -> ValidationContext:
    """Resolve a snapshot id into a context, or say why it cannot be.

    ``as_of`` defaults to the package's export date at end of day. Defaulting to
    *now* would let the same snapshot produce different answers on different
    days; defaulting to the export date makes the run a function of the package.
    """
    package = session.scalar(select(t.DataPackage).where(t.DataPackage.snapshot_id == snapshot_id))
    if package is None:
        known = session.scalars(
            select(t.DataPackage.snapshot_id).order_by(t.DataPackage.imported_at.desc()).limit(10)
        ).all()
        raise ConfigError(
            f"no imported package with snapshot id {snapshot_id!r}. "
            + (f"Known snapshots: {list(known)}" if known else "No packages have been imported.")
        )
    moment = as_of or dt.datetime.combine(package.export_date, dt.time(23, 59), tzinfo=dt.UTC)
    return ValidationContext(
        session=session,
        package=package,
        clock=CheckClock(as_of=moment),
        universe=universe or default_universe(),
        code_version=code_version,
        config_digests=dict(config_digests or {}),
    )


__all__ = ["ValidationContext", "load_context"]
