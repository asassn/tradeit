# The empirical scan: driving Phase 4 and Phase 5 over real bars

Phase 4's twelve detectors and Phase 5's breakout engine were implemented,
tested against synthetic corpora, and had **never been run over imported
market data**. The missing piece was never the detectors — it was the command
that drives them causally across a snapshot and persists what they produce, so
the Phase 4/5 gate checks had nothing to read and SKIPPED.

```
tradeit scan --snapshot <snapshot-id> [--symbols AAPL NVDA] [--scan-id my-scan]
             [--start ISO] [--end ISO] [--progress] [--json out.json]
```

---

## 1. What it guarantees

**Causality.** Bars arrive through `tradeit.scanning.feed.CausalFeed`. Each
session is evaluated at the instant its own bar became knowable — taken from the
snapshot's `knowledge_time`, floored at the exchange close — and the view handed
to a detector never contains a bar dated after the session under evaluation. A
test asserts that over every session of a real import, and a second asserts each
view is a strict prefix of the next.

The feed has two modes and *proves* which one it may use:

| mode | when |
|---|---|
| `per_session_read` | always correct: one point-in-time repository read per session |
| `verified_prefix` | one read, then prefixes — only after checking this instrument has no revised bars and no session knowable at or before the one preceding it |

The precondition is measured against the database, not inferred from the
importer's behaviour, and a test proves the two modes produce identical views on
a snapshot that qualifies. Inserting one revised bar flips the instrument to the
slow path.

**Identity.** One `PatternScanner` and one `BreakoutMonitor` per instrument,
alive for the whole walk. A fresh scanner per session would re-mint every
pattern daily and make breakout attempt numbering meaningless.

**Idempotency.** Everything for one instrument commits in a single transaction
together with its `scan_progress` row, so the ledger and the observations cannot
disagree. Re-running with the same `--scan-id` skips completed instruments; a
forced re-scan writes no duplicate rows, because both repositories are
idempotent by session. Both are tested by counting rows before and after.

**Provenance.** `scan_runs` records the snapshot, timeframe, as-of instant, code
version, and the pattern and breakout configuration digests.

**Observation, not recommendation.** The report states what the scan produces
and what it deliberately does not — no ranking, no selection, no forward
return — and a test asserts those lines are present. A `CONFIRMED` breakout is a
recorded observation about price behaviour.

---

## 2. Five defects the first real walk exposed

Every one of these was invisible to the synthetic corpora, and four of them
aborted the scan outright. They are listed in the order they surfaced.

### 2.1 A pattern aged on its own birth session (Phase 4 tracker)

`PatternTracker._fork` mints a new identity when a detection would move an
existing one along an edge that does not exist. It inserted the forked key into
`_open` **without adding it to `seen_keys`**, so `_age_unseen` — which runs
immediately afterwards — treated the newborn pattern as "not detected this
session" and resolved it from price against a zero-session gap.

Two consequences. The pattern's history recorded it as *carried forward* before
a single session had passed; and it carried two transitions on one date, which
`uq_pattern_observation (pattern_id, session_date)` forbids. So a tracker defect
surfaced as a database integrity error four layers away.

*Fixed:* the forked key is marked seen. A backstop in `PatternRepository`
collapses any remaining same-session pair into one net transition rather than
taking the whole run down with it.

### 2.2 Terminated identities re-minted under the same key (Phase 4 tracker)

When a detection arrived for a key that was not open, the tracker minted a fresh
`TrackedPattern` under that same key — including when the key had already
*terminated*. Over a four-year walk **one identity was re-minted 151 times**,
and persisting them wrote 151 separate lives into a single `patterns` row whose
history is an interleaving nothing can separate afterwards. Across one
instrument: 785 tracked objects under 261 distinct keys.

*Fixed:* a retired key is recorded, and a re-detection after termination gets a
new identity suffixed with the session — the same rule `_fork` already applied
to the sibling case. After the fix: 2,392 tracked objects, 2,392 distinct keys,
zero duplicates.

### 2.3 `breakout_retest` reported its boundaries the wrong way round (Phase 4)

The detector assigned `resistance = the broken level` and `support = the retest
low`. After a break that *held*, the retest low sits above the level, so the
pattern stored support above resistance and
`ck_pattern_support_below_resistance` refused it.

The crash is the smaller half. Phase 5 monitors `geometry.resistance`, so with
the old mapping the engine would have watched a boundary price was **already
above** — opening a spurious breakout event on the first session of every
retest, and inflating the breakout count with events that were artefacts of the
boundary choice.

*Fixed:* after a break, the level is support (which is also what `invalidation`
is derived from, so the two now agree), and resistance is the high of the
excursion — the price the move must clear to resume.

### 2.4 Incoherent boundary pairs from clustered swings (Phase 4, all detectors)

`structural_support` clusters swing lows and `structural_resistance` clusters
swing highs, each independently maximising cluster size. In a base that drifts
upward the late lows can sit *above* the early highs, and the pair then
describes no consolidation at all — while every measurement drawn from it
(depth, width, penetration, position in range) is a difference between two lines
in the wrong order. `flat_base` and `vcp` both produced these on real data.

*Fixed:* `PatternGeometry.boundaries_are_ordered` states the invariant once, and
the structure is **rejected** at detection with a reason rather than reaching the
database, which asserts the same thing and can only abort a run over it. The
guard is applied at all three instance-construction sites.

### 2.5 Daily bars are stamped at a fixed 16:00 New York (importer)

`KnowledgeTimePolicy.close_instant` places every daily bar at the configured
session close, ignoring early closes. On 2018-07-03 the exchange closed at 13:00
ET and the bar is stamped 16:00 ET — three hours after the fact existed.

**Not a lookahead risk**: the stamp is *later* than the truth, so nothing
becomes visible too early. It is a modelling inaccuracy, and it is left as it
is rather than silently changed, because the policy is venue-agnostic by design
(`timezone` and `session_close` are configurable for other exchanges) and
consulting this project's US calendar would be wrong elsewhere. Re-stamping
would also change `knowledge_time` for every existing snapshot on re-import,
which is the operator's decision.

The scanner no longer depends on the assumption: it derives each session's
evaluation instant from the stored `knowledge_time`.

---

## 3. Rehearsal, and what it does and does not show

Five instruments (`AAPL`, `NVDA`, `SPY`, plus a penny-priced and an illiquid
shape), 1,008 sessions each, 2018–2021.

| | |
|---|---|
| session evaluations | 5,040 |
| feed mode | `verified_prefix` for all five, precondition verified |
| patterns persisted | 2,991 |
| pattern observations | 48,005 |
| breakout events | 3,963 |
| breakout observations | 38,017 |
| resume, second run | 0 scanned, 5 resumed, **all four counts unchanged** |

**The detection rate is high and this rehearsal cannot say whether that is a
defect.** Eleven detectors produced ~8.5 detections per session evaluation —
`bull_flag` alone 1.66. But the fixture is a *random walk*: it contains no real
market structure, so anything found in it is noise by construction, and a high
rate on noise is a statement about the fixture as much as about the detectors.
Reading it as "the detectors over-fire" would be exactly the unfounded inference
this project exists to avoid.

What it does establish: the path runs end to end, every detector fires (so none
is silently disabled), the states span the lifecycle rather than collapsing to
one value, and resume is exactly idempotent. **The rate has to be re-measured on
the real snapshot before anything is concluded from it**, which is what
`phase4.score_distribution` and `phase4.concentration` are for.

**Throughput.** ~180 ms per session evaluation across twelve detectors on this
machine. The five-instrument rehearsal took ~18 minutes. Extrapolated, the full
78 × 4,174 universe is on the order of **16 hours** — feasible as an overnight
run and resumable if interrupted, but not something to launch casually. Scan the
diagnostic subset first.

---

## 4. The new gate checks

They SKIP until a scan has persisted something, and a SKIP is never a pass.

| check | reports | may fail? |
|---|---|---|
| `phase4.detection_rate` | detections per detector per instrument-year | no — descriptive |
| `phase4.score_distribution` | quality and coverage min/mean/max per detector, state distribution | WARN if a detector's score is constant |
| `phase4.identity_stability` | observations per identity, single-observation share, forked identities | WARN below 1.5 observations per identity |
| `phase4.concentration` | share held by the top ticker and top session; instruments with zero detections | WARN on concentration or silence |
| `phase4.causality` | patterns detected before their own structure completed | **yes** |
| `phase5.state_distribution` | terminal breakout states | no — descriptive |
| `phase5.lifecycle` | every recorded transition against `LEGAL_TRANSITIONS`; transition and reason tallies | **yes** |
| `phase5.causality` | observations predating their event; duplicated sessions | **yes** |
| `phase5.boundary_provenance`, `phase5.quality_frozen`, `phase5.monitor_floor` | as before | yes |

Only the structural invariants — causality, identity, lifecycle legality — are
allowed to fail. Nobody has a defensible prior for how many cup-with-handles a
decade of a large-cap contains, and inventing one here would be tuning a
threshold against the validation set.

**No check computes a forward return, a win rate, or anything from which one
could be assembled**, and `assert_no_performance_claims` walks every result.
