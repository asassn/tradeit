# Pattern Recognition Architecture

How the pattern layer is built and why. Written for someone who has to change it
and needs to know which parts are load-bearing.

---

## 1. What this layer answers

One question: **does this structure resemble a valid bullish chart pattern, and
how good is it?**

Not "should we buy this?". Not "did the breakout work?". Not "how big a
position?". Those are later phases, and keeping them out is the reason the
pattern layer can be audited at all — otherwise "why was this pattern reported"
and "why was this trade taken" have the same answer and neither can be examined.

The layer emits **pattern instances**: a structure, its geometry, a component
breakdown, a composite quality score, an evidence-coverage figure, a lifecycle
state, and the knowledge boundary it was computed under.

---

## 2. The nine invariants

These are the decisions everything else is built on. Changing one is a change to
the architecture, not to a detector.

### 2.1 Causal pivot confirmation

A swing high is not knowable on the day it happens. It becomes knowable
`right_bars` sessions later, when enough subsequent bars exist to confirm no
higher high followed. So every pivot carries **two dates**: `session_date` (when
it occurred) and `confirmed_date` (when it became knowable).

The consequence that surprises people: the last `right_bars` sessions of any
series are a **provisional tail**. They may inform a pattern's *state* — price
closed below support today — but they may never *define* structure. A boundary
drawn from an unconfirmed pivot is a boundary that will move, and a pattern whose
boundaries move is not reproducible.

`DetectionInputs.structure_end` is this rule applied once, centrally, so no
detector has to remember it.

### 2.2 Structural selection, not score selection

**ADR-0014.** A detector must find structures from causal market geometry and
must never select among candidate windows by maximising its own quality score.

This was violated twice during construction — in the bull flag, which searched
(pole, flag) splits and let a tie-break decide; and in the VCP, which took the
later of two equal highs and truncated the base. Both looked like ordinary
"find the best fit" code. The rule now has a test suite
(`tests/unit/test_selection_bias.py`) that builds a deliberately score-selecting
twin of each detector and asserts the real one behaves differently.

Three families are **single-anchor**: their discovery rule names one structure or
none, so score selection is impossible there by construction. The rest enumerate
anchors and are covered by the leak probes.

### 2.3 Pattern identity

A bull flag detected on Monday and the same flag on Friday are **one pattern**.
Identity is a content hash of what does not change as a pattern evolves:
instrument, family, timeframe, and structural start.

It deliberately excludes the detector version (a bug fix should not fork every
open pattern), the end date, the state and the scores (all of which move).

> **Defect found during the completion gate.** Five detectors deduplicated
> discovery on a composite key — the cup on (left rim, right rim), the double
> bottom on (first low, second low) — which permitted two structures to share a
> start and therefore an identity. 38 of 74 cup instances collided. Deduplication
> now happens once, on the structural start, in the shared detect flow.

### 2.4 Append-only observation history

The `patterns` row holds current state; every previous belief lives in
`pattern_observations`, which is never updated. A pattern that was MATURE at
quality 88 on 40% coverage on Wednesday was exactly that on Wednesday, whatever
Thursday brought.

This is not fastidiousness. A detector's false-positive rate can only be computed
from a population that still contains its failures.

### 2.5 Quality and evidence coverage are separate

Two numbers, 0–100, deliberately **not multiplied**:

- **Quality** — how good is this structure, on the components that could be
  measured?
- **Coverage** — how much of the intended evidence was actually available?

A flag scoring 88 on complete evidence and one scoring 88 with no volume data and
no benchmark are different objects. Multiplying them into a single 70 would hide
which. A later phase may choose to combine them; this one refuses to decide for
it.

Coverage below a detector's declared floor forces the instance to stay FORMING
regardless of score, because a composite computed from a fifth of its intended
evidence is describing something other than the pattern.

### 2.6 Declared detector contracts

Each detector declares required inputs, required components, optional components,
warm-up bars and a minimum coverage. Written down because the alternative is that
requirements live implicitly in the order of early-return statements, where
nobody can review them and a refactor can quietly relax one.

**Required** components suppress the instance when absent. **Optional** ones
reduce coverage. The distinction is what stops a detector silently constructing a
pattern from insufficient structural evidence.

### 2.7 Terminal states are absorbing

INVALIDATED and EXPIRED have no outgoing edges. Allowing INVALIDATED → MATURE
would let a failed pattern quietly become a successful one under the same
identity — which does not merely lose information, it inverts it.

If genuinely new structure appears later, it is a **new pattern** with a new
identity, and the old one stays failed.

### 2.8 Shared measurement primitives, unshared judgement

`primitives.py` and `structure.py` measure. `detectors/*.py` judge.

One `measure_consolidation` produces a `ConsolidationShape`; a bull flag wants a
mild downward drift, a flat base wants none, and an ascending triangle wants
rising lows. One shared measurement, three different opinions about what is good.

`BaseDetector.discover` and `.score` are **abstract** precisely so a subclass
cannot accidentally inherit a flag's opinion of what makes a structure good.
Renaming one detector's output to serve another pattern is the specific
anti-pattern the design forbids.

### 2.9 Versioning at three levels

- **Detector version** — bumped when a scoring rule or structural definition
  changes. A stored pattern says which definition produced it.
- **Config digest** — a content hash. Two runs with the same hash provably used
  the same numbers.
- **Data snapshot digest** — which ingested data was visible.

A backtest pins all three. Silently recomputing history under a newer detector
and presenting the results as unchanged is the specific dishonesty they prevent.

---

## 3. Module map

```
tradeit/patterns/
├── base.py            PatternInstance, ComponentScore, Boundary, geometry,
│                      DetectorContract, the Detector protocol
├── swings.py          Causal pivot confirmation. The causality-critical module.
├── structure.py       Line fits, horizontal/sloped boundaries, consolidation shape
├── primitives.py      Impulse, pullback, contraction sequence, volume, volatility,
│                      relative strength, prior trend, duration
├── scoring.py         band/ramp/decay/step curves, weighted combination
├── config.py          Every number, with the reasoning for its default
├── lifecycle.py       Legal transitions, family constraints
├── tracking.py        Identity across sessions; carry-forward resolution
├── persistence.py     Repository over the append-only schema
├── relationships.py   The six-edge taxonomy and its derivation
├── registry.py        Which detectors exist, where they run, the frozen baseline
├── scanner.py         Orchestration; incremental and multi-timeframe scanning
├── series.py          Causal resampling on the way in
├── labeling.py        Human label vocabulary, service, review queue
├── synthetic.py       Seeded generators for every family and its negatives
├── validation.py      Cohort-based behavioural characterisation
├── competition.py     Competing-pattern matrix
├── perturbation.py    Score stability and monotonicity helpers
├── corpus.py          Score distributions over generated families
└── detectors/
    ├── _base.py       Shared detect flow; discover/score are abstract
    └── <twelve>.py    One per family
```

---

## 4. The detection flow

```
bars + as_of_session
   │
   ├─ boundary check ──────────── refuse bars past as_of
   ├─ ATR (Wilder)
   ├─ confirmed swing highs/lows ─ right_bars confirmation lag applied
   │
   ├─ discover(inputs) ─────────── structural anchors only (ADR-0014)
   ├─ dedupe by structural start ─ one structure per identity
   ├─ cap at max_candidates ────── bounded work, order preserved
   │
   ├─ score(inputs, structure) ─── component scores + evidence
   ├─ combine ─────────────────── weighted, unavailable excluded from denominator
   ├─ coverage ────────────────── available weight / total weight
   ├─ geometry / invalidation
   └─ classify_state ──────────── quality, coverage floor, distance to boundary
```

Everything above the `discover` line is shared. Everything below `discover` is
the detector's own opinion.

---

## 5. Where the layer stops

The scanner explicitly does **not**:

- confirm breakouts (it records that a line was crossed);
- generate entries, exits or stops;
- consult fundamentals;
- size positions;
- rank candidates against each other.

`tests/unit/test_scanner.py::TestScannerScope` asserts the absence, including
that a pattern payload carries no trade vocabulary. The check exists because
scope creep here is both easy and irreversible.

---

## 6. Reading the rest

- **`PATTERN_METHODOLOGY.md`** — what each of the twelve families claims, and the
  interpretation matrix showing where they overlap.
- **`PATTERN_LIFECYCLE.md`** — states, transitions, tracking, carry-forward.
- **`PATTERN_RELATIONSHIPS.md`** — the six edges and why not five or seven.
- **`PATTERN_LABELING.md`** — the human-label vocabulary and the real-market
  sampling protocol.
- **`PATTERN_VALIDATION.md`** — generated distributions, competing-pattern
  matrix, stability results, known weaknesses.
- **`PATTERN_PERFORMANCE.md`** — benchmarks, budget, storage growth.
- **`PHASE_04.md`** — the completion report.
