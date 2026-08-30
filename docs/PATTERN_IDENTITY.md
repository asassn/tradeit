# Pattern identity: when is a detection the same structure?

A detector is stateless. It re-measures from scratch every session and emits a
content hash of *(instrument, timeframe, pattern type, structural start)*.
`PatternTracker` is the memory that re-detection lacks: it decides whether
today's detection continues yesterday's structure or begins a new one.

This document states that decision precisely, because getting it wrong is
expensive in a way that is invisible in aggregate. The diagnostic scan produced
**37,483 re-minted identities out of 39,218** — one `double_bottom` minted 209
times, on consecutive trading days, for a structure with a single fixed start
date.

## The seven rules

### 1. When is a detection the same economic structure?

When **the tracker is holding a live identity for that detector key**, and the
state the detection reports is a legal edge from the state that identity holds.

Both halves are necessary and neither is new. What was missing was the ability
to answer the first: the live identity may be filed under a *suffixed* key
(`<hash>:<session>`) that the detector does not know about, so looking the
detection up directly in the open set missed it and fell through to the re-mint
branch — every session, for as long as the structure kept being detected.
`_live` is a one-entry-per-structure index from the detector's key to the
tracked key currently alive for it.

If no live identity exists, the detection begins a new one (subject to rule 4's
stillbirth clause).

### 2. How long may it disappear and still be the same structure?

**Unchanged**, and deliberately so:

| | sessions | applies to |
|---|---|---|
| `grace_sessions` | 3 | a structure that is simply not re-detected |
| `resolution_carry_sessions` | 10 | one already in `BROKEN_OUT_UNCONFIRMED` |

Past its window the identity is declared `EXPIRED` and closed. A later
detection is then a genuinely new life and gets a new identity.

Neither number was touched. The continuity fix operates only on the *matching*
path — for structures that **are** being detected — so it cannot extend any
pattern's life beyond the rules that already existed.

### 3. What geometric changes are allowed while retaining identity?

| may change | may not change |
|---|---|
| `end_date` (extends as the structure keeps forming) | `start_date` |
| resistance / support levels (re-measurement) | instrument, timeframe, pattern type |
| quality, evidence coverage, confidence | |
| invalidation price | |

The start date is immutable twice over: it is an input to the content hash, so a
different start is a different key by construction, and
`PatternRepository._advance` refuses to persist a moved structural start
outright.

`structure_known_through` freezes what was measured at first detection, so a
re-measurement can never be mistaken for foresight.

### 4. What permanently terminates an identity?

Reaching `INVALIDATED` or `EXPIRED`. Both are absorbing — the state machine has
no edge out of either. Five routes in:

| route | reason | state |
|---|---|---|
| close below the stored invalidation level | `support_broken` | INVALIDATED |
| a detection that itself arrives terminal | `detected` | INVALIDATED |
| not re-detected past `grace_sessions` | `lost` | EXPIRED |
| carried past `resolution_carry_sessions` after breaking out | `timed_out` | EXPIRED |
| a detection proposing an edge the lifecycle lacks (rule 1) | `superseded` | EXPIRED |

A terminated identity is never reopened, never merged into, and never has
another observation appended.

**One clause added: a life cannot begin already over.** A detection arriving in
a terminal state, for a structure whose previous life has already terminated,
does not mint a new identity. It would hold exactly one observation, have no
legal transition available, and be retired on the same session — 10,024 of them
on the diagnostic corpus, one per session for as long as the dead geometry
stayed visible. The outcome is already on file under the identity that lived it.

The *first ever* sighting of an already-dead structure is still recorded: the
rule is about re-minting, not about refusing to observe.

### 5. How are genuinely separate setups kept from merging?

Three independent barriers:

1. **A different structural start is a different key.** Two setups that begin on
   different dates cannot collide, whatever else they share.
2. **`_live` only ever names a life the tracker is holding open.** It is written
   when an identity is created and removed when it terminates, so a second
   structure can only be minted once the first is no longer live — and if the
   first *is* live, it is the same structure by every property identity is
   defined on. This is asserted as a loop invariant in the tests.
3. **The legality check still forks.** A detection proposing an impossible edge
   still retires the incumbent and mints a new identity. The index resolves
   *which* identity to advance; it never decides whether the edge is legal.

The residual case — two structures on one instrument, same type, same timeframe,
beginning on the same date, separated by a termination — is exactly the case the
suffixed re-mint exists to represent, and it still does.

### 6. How does a structural break force a new identity?

By construction, above the tracker. `SnapshotScanner` segments an instrument's
sessions into analytical episodes and builds **a fresh `PatternScanner`,
`PatternTracker` and `BreakoutMonitor` per episode**, with a hard bar floor at
the episode start. No identity, no `_live` entry, no retired key and no
indicator history crosses an episode boundary, so a structure on the far side of
a 536-session gap cannot continue one from before it — ticker text alone never
establishes economic continuity across separate listing histories.

### 7. How does this interact with `grace_sessions = 3`?

It does not change it, and cannot. `grace_sessions` governs `_age_unseen`, which
runs over identities that were **not** in this session's detections. The
continuity fix only affects how a detection that *is* present is matched to an
existing identity. The two code paths are disjoint: a pattern that vanishes for
four sessions still expires on the fourth, exactly as before.

## Why not "stop terminating a structure that is still being detected"?

That was the option this investigation opened with, and the measurements ruled
it out. Terminations are not the cause:

- **72.6% of re-mints overlap their predecessor** — the successor's first
  detection *precedes* the predecessor's last observation, so the previous life
  was still open when the new identity was minted. No termination rule could
  have produced that.
- The 209-identity base shows first-detection dates on **consecutive trading
  days** while earlier lives were still being carried.

Loosening termination would have merged genuinely separate lives to work around
a lookup defect, and left the defect in place. The fix is a corrected lookup
plus one clause about stillbirth, and no threshold moved.
