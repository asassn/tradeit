# Pattern Lifecycle

How a pattern is born, how it changes, and how it ends.

---

## 1. The states

| State | Meaning | Terminal | Screenable |
| --- | --- | :-: | :-: |
| `FORMING` | structure present, not yet established — low quality or insufficient coverage | no | no |
| `MATURE` | established structure, well clear of its boundary | no | yes |
| `NEAR_BREAKOUT` | price within the near-boundary band of resistance | no | yes |
| `BROKEN_OUT_UNCONFIRMED` | price closed above resistance. **A geometric observation only** | no | yes |
| `INVALIDATED` | price closed below the structural invalidation level | **yes** | no |
| `EXPIRED` | ran out of time without resolving | **yes** | no |

`BROKEN_OUT_UNCONFIRMED` is named at length on purpose. Phase 4 records that a
line was crossed. Whether that constitutes a *confirmed* breakout — volume,
follow-through, tradability — is Phase 5's question, and this package has no
vocabulary for answering it.

---

## 2. The transition machine

```
FORMING ──→ MATURE ──→ NEAR_BREAKOUT ──→ BROKEN_OUT_UNCONFIRMED
   │           │             │  ↑                  │
   │           │             └──┘ (drift back)     │
   └───────────┴─────────────┴────────────────────┴──→ INVALIDATED
   └───────────┴─────────────┴────────────────────┴──→ EXPIRED
```

Two edges are less obvious and both are deliberate:

**NEAR_BREAKOUT → MATURE is legal.** Price approaching resistance and drifting
back is ordinary hesitation, not failure. Forbidding it would force a pattern to
invalidate every time it paused.

**BROKEN_OUT_UNCONFIRMED → INVALIDATED is legal.** A structure can clear its
resistance and then break its support. Phase 4 does not judge whether the
breakout was valid; it does record that the structure subsequently failed.

### What is forbidden

**Any edge out of a terminal state.** INVALIDATED and EXPIRED are absorbing.

This matters more than it looks. Allowing INVALIDATED → MATURE would let a failed
pattern quietly become a successful one under the same identity — which does not
merely lose information, it inverts it. Every measurement of how often patterns
fail would be computed from a population that had erased its own failures.

If genuinely new structure appears later, it is a **new pattern** with a new
identity. `check_transition` says so in its error message, because the intuitive
fix ("just allow the edge") is the wrong one.

### Family constraints

A family may **narrow** the machine but never widen it.

`BREAKOUT_RETEST` excludes `NEAR_BREAKOUT`: a retest structure is observed only
after price has already moved through resistance, so it never occupies the
pre-breakout states. Emitting one as NEAR_BREAKOUT would describe a different
structure entirely.

---

## 3. Tracking: why re-detection is not enough

A pattern detected on Monday and the same pattern on Friday are one identity. But
running the detector every day and matching results has a specific, severe
failure:

**Re-detection loses a pattern at the exact moment it resolves.** A breakout is
re-detectable for only `right_bars` sessions. After that the new high is itself
confirmed, it re-anchors the flagpole, and the old consolidation is no longer the
most recent structure — so the detector returns nothing. A system that only
re-detects reports BROKEN_OUT_UNCONFIRMED for three sessions and then silently
forgets the pattern existed, which is precisely when downstream stages care most.

So `PatternTracker` keeps the record. Each session it:

1. matches fresh detections against open patterns by identity;
2. advances the ones it recognises;
3. **carries forward** the ones that have left the detector's view, resolving
   them against stored levels using today's close;
4. ages out the rest.

### What carry-forward may and may not do

It **may** update state, note that price has moved, and mark a pattern resolved
or expired.

It **may not** rewrite geometry from data the original detection could not see.
That is the same retroactive-refinement leak the swing module exists to prevent,
wearing different clothes: a stored pattern whose resistance quietly improves is
a pattern that can no longer be reproduced from the data that produced it.

The comparison is always between the pattern's **stored** boundaries — measured
when it was detected, from data visible then — and today's close. No geometry is
recomputed.

### Transition reasons

Recorded so a history reads as prose rather than as a state sequence:

`DETECTED`, `ADVANCED`, `CARRIED_FORWARD`, `SUPPORT_BROKEN`,
`RESISTANCE_CLEARED`, `TIMED_OUT`, `LOST`, `SUPERSEDED`.

The distinction between `LOST` and `RESISTANCE_CLEARED` is the one carry-forward
exists for. Without it, a flag that broke out just outside the re-detection window
is recorded as "lost" — the single most misleading outcome available, because the
pattern did not fail or fade, it did the thing it was being watched for.

---

## 4. History is append-only

`patterns` holds current state. `pattern_observations` holds every previous
belief and is never updated.

A pattern that was MATURE at quality 88 on 40% coverage on Wednesday was exactly
that on Wednesday. Component scores are stored per observation, not only
currently, because *"why did this decay?"* is answerable from the component
history and unanswerable from the composite alone.

The mutable current-state row exists only because a screen asks "what is this
base doing?" constantly and should not pay for a windowed query. It is safe
because nothing is lost beside it.

---

## 5. Supersession and nesting

Two structures can describe one thing at different scales, or one can absorb
another.

`merge_overlapping` is deliberately conservative: it only supersedes patterns of
the **same family** where one is largely contained in the other — the case where
two rows genuinely describe one structure rather than two readings of it.

Two *different* families on the same geometry are **not** merged. A window can be
a tight consolidation at 78 and a bull flag at 74, and forcing a single label
early discards information a later scoring stage is better placed to use. That
relationship is recorded as a `RELATED_TO` edge instead — see
`PATTERN_RELATIONSHIPS.md`.

---

## 6. Where state comes from

`classify_state` runs after scoring, and its order encodes the priorities:

1. **Invalidated?** Price below the stored invalidation level. Checked first
   because a broken structure is broken regardless of how good it looked.
2. **Below the quality floor?** FORMING.
3. **Below the coverage floor?** FORMING, with contradicting evidence saying why.
   A composite computed from a fifth of its intended evidence should not claim
   maturity.
4. **Past resistance?** BROKEN_OUT_UNCONFIRMED.
5. **Near resistance?** NEAR_BREAKOUT.
6. Otherwise MATURE.

Detectors may override this — the breakout retest does, because it begins life
already resolved — but the override must still produce a state its family
constraint permits.

### The provisional tail and state

State reads the **last bar**, including the provisional tail. Scoring reads only
confirmed structure. This is intentional and produces a result worth
understanding: a retest structure can show `hold_behaviour` in the sixties, with
one instance still measuring a close above its line, while the state says
INVALIDATED — because price lost the level in the final three sessions, which
inform state but cannot redraw geometry.

Measurement describes the window. State describes now.
