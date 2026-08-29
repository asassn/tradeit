"""End-to-end: index files on disk -> a published denominator report.

Kept separate from the analytical modules so that the analysis is testable
without a filesystem, and the wiring is testable without inventing evidence.

**Raw provenance survives every stage.** Each :class:`LifecycleEvidence` carries
its accession, filing date, CIK, form type, source path and originating index
quarter, and each :class:`~tradeit.edgar.lifecycle.ExitResolution` carries the
evidence rows it rests on. Any published count can be walked back to the filings
that produced it, which is the difference between a measurement and an
assertion.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tradeit.edgar.denominator import Denominator
from tradeit.edgar.evidence import FormRole, LifecycleEvidence, classify_form
from tradeit.edgar.identity import SecurityMapping
from tradeit.edgar.index import FullIndexRow, IndexQuarter, LocalFullIndexSource
from tradeit.edgar.lifecycle import (
    CESSATION_QUIET_QUARTERS,
    build_timelines,
    resolve_exit,
)

__all__ = ["BuildOptions", "build_denominator", "evidence_from_rows"]


@dataclass(slots=True)
class BuildOptions:
    index_root: Path
    start: IndexQuarter = field(default_factory=lambda: IndexQuarter(1994, 3))
    end: IndexQuarter = field(default_factory=lambda: IndexQuarter(2026, 2))
    #: The clock the cessation rule measures silence against. Pin it for a
    #: reproducible report; leaving it None makes the output depend on the day
    #: it was run, which is fine for exploration and not for a published number.
    as_of: dt.date | None = None
    quiet_quarters: int = CESSATION_QUIET_QUARTERS
    #: Curated CIK->ticker identity, keyed by CIK. Built from the control
    #: evidence file by
    #: :func:`~tradeit.edgar.control_evidence.security_mappings_by_cik`, which
    #: also reports the issuers a CIK-keyed dict cannot carry. Leaving this None
    #: is a valid measurement -- the corpus without curated identity -- and is
    #: not the same statement as "no identity has been established".
    mappings: dict[int, SecurityMapping] | None = None


def evidence_from_rows(rows: Iterable[FullIndexRow]) -> list[LifecycleEvidence]:
    """Keep only rows that say something about a lifecycle.

    Everything else -- SC 13G, 4, 424B, correspondence -- is dropped here rather
    than carried through the pipeline, because a denominator built over "every
    filing ever" is dominated by ownership reports and tells you nothing.
    """
    kept: list[LifecycleEvidence] = []
    for row in rows:
        signal = classify_form(row.form_type, row.filed_at)
        if signal.role is FormRole.IRRELEVANT:
            continue
        kept.append(
            LifecycleEvidence.from_index_row(
                cik=row.cik,
                company_name=row.company_name,
                form_type=row.form_type,
                filed_at=row.filed_at,
                accession=row.accession,
                source_path=row.path,
                index_quarter=row.index_quarter,
            )
        )
    return kept


def build_denominator(options: BuildOptions) -> Denominator:
    source = LocalFullIndexSource(root=options.index_root)
    rows = source.rows(options.start, options.end)
    evidence = evidence_from_rows(rows)
    timelines = build_timelines(evidence)

    as_of = options.as_of or dt.datetime.now(dt.UTC).date()
    resolutions = [
        resolve_exit(timeline, as_of=as_of, quiet_quarters=options.quiet_quarters)
        for timeline in timelines.values()
    ]
    return Denominator(
        resolutions=resolutions,
        timelines=timelines,
        mappings=dict(options.mappings or {}),
        missing_quarters=tuple(source.missing),
    )
