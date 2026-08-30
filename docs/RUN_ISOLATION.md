# A scan run is an isolation boundary

Adding `scan_run_id` to `patterns` and `breakout_events` would have recorded
*which run inserted a row*. That is attribution, and on its own it would have
been worse than nothing — provenance that reads as authoritative and is false.
This document records why, and what was done instead.

## Why attribution alone fails

Both repositories look a row up before writing it:

```python
PatternRepository._find(identity_key, detector_version)
BreakoutRepository._find(event_key)
```

Both keys are content hashes of the instrument, timeframe and structure. Two
scans of the same snapshot under the same configuration therefore derive
**byte-identical keys**. With the constraints global, the second scan finds the
first scan's row and takes the `_advance` path: no duplicate, no error, and one
row afterwards holding run A's insert-time values (`first_detected_session`,
`first_detected_at`, `structure_known_through`) and run B's advance-time values
(`state`, `quality`, `structural_end_date`). A `scan_run_id` naming run A would
have been attached to a row run B had rewritten.

Nothing else could have separated them either: `data_snapshot_digest`,
`config_digest` and `detector_version` are identical across two runs of one
snapshot under one build.

## Question 1: why not `run_manifest_id`?

`patterns.run_manifest_id` and `breakout_events.run_manifest_id` already exist
and are exactly this kind of provenance anchor. They are not usable here, and
the reason is in `run_manifests` itself:

```python
strategy_config_digest: Mapped[str] = mapped_column(
    ForeignKey("artifact_versions.digest"), nullable=False
)
```

A `RunManifest` is the anchor for a **decision-producing** run — a backtest, a
paper session — and it requires a strategy configuration. A scan produces
observations, not decisions, and has no strategy. Writing one would mean
inventing a digest, and an invented digest is worse than an absent row: it makes
an unreproducible run look reproducible.

That is why `scan_runs` exists as a separate table, and it is the right anchor
for scan-derived rows. Neither `run_manifest_id` column is set by the scan path
today — `grep` across `patterns/`, `breakouts/` and `scanning/` returns nothing —
so the FK is live for other run kinds and simply unused by scans.

## Question 2: must two corpora coexist?

Yes. A scan is an experimental boundary. Without simultaneous, independently
reproducible corpora you cannot re-derive a snapshot under a changed detector
while keeping the validated corpus, cannot compare two derivations, and cannot
attribute any row. "Delete the old one first" is not isolation; it is a
convention that fails the first time somebody forgets.

## Question 3: what was made run-scoped

| | change |
|---|---|
| pattern lookup | `_find` filters on `scan_run_id` |
| breakout lookup | `_find` filters on `scan_run_id` |
| pattern uniqueness | `(scan_run_id, identity_key, detector_version)` |
| breakout identity | `(scan_run_id, event_key)` |
| breakout attempt | `(scan_run_id, instrument_id, timeframe, pattern_key, attempt_number)` |
| observations | attributed through the immutable FK chain; no new column |
| validation | reads exactly one named run, or BLOCKS |
| `monitor_floor` join | the run is in the **join condition**, not only a filter |

That last one is easy to miss and would have silently broken the fix:
`phase5.monitor_floor` joins events to patterns on `identity_key ==
pattern_key`. Since identity keys are content hashes, joining on the key alone
would let a run B event match a run A pattern and report a state belonging to a
different corpus. The join now also requires
`Pattern.scan_run_id == BreakoutEvent.scan_run_id`.

## Question 4: deriving the uniqueness tuples

Not adopted blindly. `(scan_run_id, identity_key, detector_version)` keeps
`detector_version` because the existing model treats two versions of a detector
as producing *different measurements of the same structure* that must coexist —
that is what `by_detector_version` is for — and the run does not subsume it.

For breakouts the schema carries **two** constraints, and scoping only the
obvious one would have left the hole open:

- `uq_breakout_event_identity (event_key)` — the identity hash;
- `uq_breakout_attempt (instrument_id, timeframe, pattern_key, attempt_number)`
  — the *attempt*, keyed directly rather than through a hash.

Separating event keys alone would still have let run B's attempt 2 on a boundary
collide with run A's attempt 2. Both are scoped.

**Observations deliberately get no column.**
`pattern_observations.pattern_id` and `breakout_observations.event_id` are
`ON DELETE CASCADE` foreign keys to rows that are now run-scoped, so an
observation's run is a property of a chain that cannot be re-pointed. A run
column on the child would be a second source of truth able to disagree with the
first, and their existing `(parent_id, session_date)` uniqueness is already
run-scoped through the parent.

## Question 5: resume semantics

Unchanged, and now tested against the scoping:

- **Rerunning an interrupted run** finds its own rows, because the lookup filter
  matches the run doing the lookup. Resume is still tracked by `scan_progress`
  per `scan_run_id`.
- **Rerunning a completed run** is idempotent — including under `--force`,
  which rescans a completed instrument in place.
- **A different scan id** creates an isolated corpus and touches nothing.

## Question 7: legacy and manual rows

`scan_run_id` is **nullable**, and the column cannot be `NOT NULL` without
breaking three legitimate kinds of row: hand-made labels and the patterns they
reference, test fixtures, and anything written before the column existed.

What the schema cannot express, the repository does. Both repositories take the
scan run as a constructor argument; the scanner always supplies one, and an
integration test asserts no scanner-written row has a NULL run.

**No backfill.** Existing rows keep NULL, because they were produced by a build
whose lookups were global — attributing them to a run would be a guess, and a
guessed provenance is exactly the failure this work exists to prevent. The
intended path for the `diag-02` corpus is `scripts/clear_scan_output.py`.

`NULLS NOT DISTINCT` on all three constraints keeps unscoped rows behaving
exactly as they did before. Without it PostgreSQL treats every NULL as distinct,
so two legacy rows sharing an identity key would stop conflicting — a silent
weakening of a constraint that has held since the schema was written.

## What validation does now

`--scan-id` names the corpus. It is optional when the snapshot has exactly one
completed run, and required when it has more: two completed runs and no name is
a question only the operator can answer, not a tie to be broken by recency. The
Phase 4/5 checks return **BLOCKED** rather than aggregating, because a
population assembled from two runs is one that never existed.

A snapshot with **no** scan ledger at all is not blocked. The unscoped corpus is
a real corpus and a coherent selection, so the checks read it — that is what
keeps a label-only or fixture database checkable.
