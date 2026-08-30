# ADR-0016: One structure is one identity, and its history is append-only

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

A bull flag detected on Monday and the same flag on Friday are one pattern. The
Phase 2 draft schema keyed pattern uniqueness on
`(instrument, type, start_date, detected_on)`, which minted a new row every day
the same structure was re-detected. Five days of a flag became five patterns,
and any question of the form "how often does this structure fail?" was
unanswerable because the denominator was counting sessions rather than
structures.

Two further problems compound it.

**Re-detection alone loses a pattern at the moment it resolves.** A breakout is
re-detectable for exactly `right_bars` sessions. After that the new high is
itself confirmed, it re-anchors the flagpole, and the old consolidation is no
longer the most recent structure — so the detector returns nothing. A system that
only re-detects reports BROKEN_OUT_UNCONFIRMED for three sessions and then
silently forgets the pattern existed, which is precisely when downstream stages
care most.

**Mutable state destroys the record.** If Friday's beliefs overwrite Monday's,
then "what did we think on Monday?" has no answer, and every measurement of
detector behaviour is computed from a population that has erased its own
history.

## Decision

### Identity is a content hash of what does not change

`identity_key = hash(instrument_id, pattern_type, timeframe, structural_start)`

Deliberately **excluded**: the end date, the state, the scores and the boundaries
(all of which move as the pattern evolves), and the **detector version** — a bug
fix must not fork every open pattern into a new identity.

Deliberately **included**: the timeframe. A daily bull flag and a weekly bull flag
on one instrument are two patterns, and geometry overlap does not merge them.

### Current state is mutable; history is append-only

`patterns` holds one row per identity with its current state. Every previous
belief lives in `pattern_observations`, which is never updated. Component scores
are stored per observation, because *"why did this decay?"* is answerable from
the component history and unanswerable from the composite alone.

### Terminal states are absorbing

INVALIDATED and EXPIRED have no outgoing edges. If genuinely new structure
appears later it is a **new identity**, and the old one stays failed.

### Carry-forward may resolve, never redraw

A tracker may update state and mark a pattern resolved or expired using today's
close against its **stored** boundaries. It may not recompute geometry from data
the original detection could not see.

### Three version levels are pinned

- **Detector version** — an integer, bumped when a scoring rule or structural
  definition changes.
- **Config digest** — a content hash (ADR-0007) of every number the run used.
- **Data snapshot digest** — which ingested data was visible.

The detector version is stored alongside the identity key in the uniqueness
constraint, so the same structure detected under two detector versions is two
rows. That is intentional: they are two claims, made by two different
definitions, and averaging them would be averaging incomparable things.

## Consequences

- **Identity must be collision-free by construction.** Found the hard way during
  the completion gate: five detectors deduplicated discovery on a composite key —
  the cup on (left rim, right rim), the double bottom on (first low, second low)
  — which permitted two structures to share a start and therefore an identity.
  38 of 74 cup instances collided. The tracker would have folded distinct
  structures into one history and the unique constraint would have rejected the
  second insert. Deduplication now happens once, on the structural start, in the
  shared detect flow.

- **Storage grows with observations, not with patterns.** ~10 identities per
  instrument per daily scan, one observation row each per session. A 4,000-name
  universe implies ~10.5 M observation rows a year. See
  `PATTERN_PERFORMANCE.md`.

- **A backtest can pin all three versions** and reproduce exactly what was
  believed. Silently recomputing history under a newer detector and presenting
  the results as unchanged is the specific dishonesty this prevents.

- **Testable.** `tests/unit/test_pattern_persistence.py` replays a pattern
  through its whole life and asserts every earlier belief survives;
  `tests/unit/test_scanner.py` asserts identity stability across sessions and
  separation across timeframes.

## Alternatives considered

**Key identity on the geometry hash.** Would fork the identity every time a
boundary moved by a cent, which is every session.

**Key identity on (instrument, type, detection date).** The Phase 2 draft. Counts
sessions, not structures.

**Store only the current state and reconstruct history by re-running detectors.**
Requires the detectors never to change, which is exactly what versioning exists
to allow.

**Bump the detector version on any code change.** Rejected: a refactor that
changes no output would fork every open pattern. The version tracks *behaviour*,
and the config digest catches numeric changes independently.
