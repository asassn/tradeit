"""Offline data packages: the path real market data takes into this platform.

The gate this serves exists because everything through Phase 5 was built and
characterised on synthetic series. Network egress to market-data providers is
denied in this environment, so the only way real data arrives is as files a
person supplies — and files a person supplies have idiosyncratic column names,
mixed date formats, and a vendor's opinions baked into them.

The modules, in the order a row passes through them:

* :mod:`~tradeit.data.packages.spec` — what each dataset means, column by
  column, and which validations supplying it unlocks.
* :mod:`~tradeit.data.packages.manifest` — a package's self-description:
  digests, coverage, timezone, adjustment policy, column mapping.
* :mod:`~tradeit.data.packages.readers` — CSV/TSV/gzip/ZIP/Parquet, returning
  raw text.
* :mod:`~tradeit.data.packages.normalize` — text to types, recording every
  change it makes.
* :mod:`~tradeit.data.packages.validate` — consistency rules, and the line
  between flagging a row and quarantining it.
* :mod:`~tradeit.data.packages.pointintime` — when each fact became knowable,
  and how confident we are of that.
* :mod:`~tradeit.data.packages.stages` — the four stages as separate types.
* :mod:`~tradeit.data.packages.sinks` / :mod:`~tradeit.data.packages.database`
  — where dated rows land.
* :mod:`~tradeit.data.packages.importer` — the orchestration and the report.

Nothing here decides whether a security is worth trading. It decides what the
data says and when it said it.
"""

from tradeit.data.packages.importer import (
    ImportOptions,
    ImportReport,
    PackageImporter,
    import_package,
    inspect_package,
)
from tradeit.data.packages.manifest import (
    Coverage,
    DatasetFile,
    PackageManifest,
    build_manifest_template,
    load_manifest,
    verify_files,
)
from tradeit.data.packages.normalize import NormalizationPolicy
from tradeit.data.packages.pointintime import KnowledgeTimePolicy
from tradeit.data.packages.sinks import CollectingSink, CountingSink, JsonlSink, RecordSink
from tradeit.data.packages.spec import (
    DATASET_SPECS,
    AdjustmentPolicyDeclaration,
    DatasetKind,
    capabilities_for,
    describe_dataset,
)
from tradeit.data.packages.stages import QuarantineEntry, Stage
from tradeit.data.packages.validate import SeriesCheckConfig

__all__ = [
    "DATASET_SPECS",
    "AdjustmentPolicyDeclaration",
    "CollectingSink",
    "CountingSink",
    "Coverage",
    "DatasetFile",
    "DatasetKind",
    "ImportOptions",
    "ImportReport",
    "JsonlSink",
    "KnowledgeTimePolicy",
    "NormalizationPolicy",
    "PackageImporter",
    "PackageManifest",
    "QuarantineEntry",
    "RecordSink",
    "SeriesCheckConfig",
    "Stage",
    "build_manifest_template",
    "capabilities_for",
    "describe_dataset",
    "import_package",
    "inspect_package",
    "load_manifest",
    "verify_files",
]
