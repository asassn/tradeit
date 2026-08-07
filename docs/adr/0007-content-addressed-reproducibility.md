# ADR-0007: Content-addressed versioning and run manifests

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 2

## Context

The brief requires that every historical signal be reproducible exactly as it
existed at that moment. Phase 1's `AsOfClock` bounds *what data was visible*.
That is necessary and not sufficient — reproducing a decision also requires
knowing what rules ran, how features were computed, which model scored it, and
which code produced it.

The conventional answer is a version number on the strategy config. It fails in
practice for reasons that are all mundane:

- `v3` does not tell you whether two runs used the same rules.
- Nothing prevents editing `v3` in place.
- A parameter sweep generating 200 variants needs 200 identities, and manual
  numbering will not survive it.
- A renamed-but-unchanged strategy looks like a different one; an edited-but-
  same-named strategy looks like the same one. Both are backwards.

## Decision

**Identity is content.** Anything that affects a decision is identified by the
SHA-256 of its canonical serialisation.

Four artifact kinds are pinned together in a `RunManifest`, written by every
decision-producing run and referenced by every row it produced:

| Slot | Pins |
|---|---|
| `strategy_config` | thresholds, weights, limits |
| `data_snapshot` | max ingestion-run id per dataset |
| `feature_set` | which features, computed how |
| `model` | the scoring model, if any |

plus `as_of` and `code_version`.

Canonicalisation is the load-bearing detail: sorted keys, no insignificant
whitespace, Decimals rendered via `str`. Without it one configuration produces
several hashes depending on how it happened to be serialised, and the whole
scheme silently stops working.

`artifact_versions.digest` is the table's primary key. Inserting the same
configuration twice is a no-op; two rows can never disagree about what a digest
means.

### Why `data_snapshot` exists

This is the subtlest part and the piece most likely to be omitted.

Bounding reads by `as_of` is *almost* sufficient. But if a backfill lands after
a scan ran, replaying that scan at the same `as_of` sees rows the original did
not — legitimately, because those rows' `knowledge_time` precedes `as_of`; they
simply had not been loaded yet. The result is a replay that differs from the
original for a reason nobody can see.

Pinning the maximum `ingestion_run_id` per dataset closes it, making replay
exact rather than merely honest.

## Alternatives considered

**Sequential version numbers.** Familiar, and every failure mode above applies.

**Git commit hash for everything.** Covers code, not configuration or data.
Configurations change without commits during sweeps, and data changes without
either.

**Full database snapshot per run.** Perfectly reproducible and prohibitively
expensive; also answers "what did the database look like?" rather than "what did
this decision see?", which is the question that matters.

## Consequences

- Every derived row carries `run_manifest_id`. That is a foreign key on the hot
  write path and a storage cost; it is what makes any stored signal explainable.
- Editing a strategy's `description` deliberately does *not* change its hash.
  Prose is not a rule, and treating it as one would break the link between a
  live session and the backtest that validated it.
- Scoring weights are normalised before hashing, so proportionally identical
  weight sets are correctly recognised as the same strategy.
- If replaying a manifest produces a different answer, either the code changed
  or something that should have been pinned was not — and that gap is the
  finding.
- Cache keys can include the manifest digest, so entries expire on content
  change rather than on a guessed TTL.
