# Phase 4 — Completion Report

Causal chart pattern recognition. Twelve detector families, validated against a
seeded synthetic corpus, with the real-market validation gate explicitly open.

**Goal of this gate:** *prove we understand the behaviour, limitations, causal
integrity, computational cost and interactions of the pattern-recognition system
we have built.*

---

## 1. Detector status

All twelve implemented, registered, contract-declared and characterised.

| # | Detector | Kind | Version | Warm-up | Min coverage | Timeframes |
| --: | --- | --- | --: | --: | --: | --- |
| 1 | `bull_flag` | continuation | v1 | 43 | 60% | 1w, 1d, 4h, 1h, 15m |
| 2 | `vcp` | continuation | v1 | 92 | 55% | 1w, 1d, 4h |
| 3 | `flat_base` | continuation | v1 | 97 | 55% | 1w, 1d, 4h |
| 4 | `ascending_triangle` | continuation | v1 | 45 | 55% | 1w, 1d, 4h, 1h, 15m |
| 5 | `pennant` | continuation | v1 | 45 | 60% | 1w, 1d, 4h, 1h, 15m |
| 6 | `cup_handle` | continuation | **v2** | 56 | 60% | 1w, 1d |
| 7 | `high_tight_flag` | continuation | v1 | 49 | 65% | 1w, 1d |
| 8 | `double_bottom` | reversal | **v2** | 85 | 60% | 1w, 1d, 4h |
| 9 | `inverse_head_shoulders` | reversal | **v2** | 97 | 60% | 1w, 1d, 4h |
| 10 | `base_on_base` | structural | **v2** | 101 | 65% | 1w, 1d |
| 11 | `tight_consolidation` | structural | v1 | 152 | 70% | 1w, 1d, 4h, 1h, 15m |
| 12 | `breakout_retest` | structural | **v2** | 109 | 60% | 1w, 1d, 4h, 1h, 15m |

Five families moved to v2 during the gate, all for the same defect (§14).

Contracts, required and optional components: `docs/PATTERN_VALIDATION.md` §
*Detector registry*. Conceptual overlap: `docs/PATTERN_METHODOLOGY.md` §2.

---

## 2. Infrastructure delivered

| Requirement | Where | Status |
| --- | --- | --- |
| Frozen validation baseline | `patterns/registry.py` | content-hashed, asserted by test |
| Pattern lifecycle | `patterns/lifecycle.py` | 6 states, legal-transition machine, family constraints |
| Persistence | `patterns/persistence.py`, migration `0005` | append-only observations, relationship provenance |
| Relationship taxonomy | `patterns/relationships.py` | 6 edges, causally derived, reproducible |
| Human labelling | `patterns/labeling.py` | 5 labels, revisions, agreement, 9 queue strategies |
| Integrated scanner | `patterns/scanner.py` | enable/disable, timeframe gating, tracking, relations |
| Multi-timeframe | `patterns/series.py` | causal resampling; weekly/daily/4h/1h/15m |
| Incremental scanning | `patterns/scanner.py` | bounded rescan, zero-tolerance equivalence |
| Characterisation harness | `patterns/validation.py` | 7 cohort kinds × 12 families |
| Competing-pattern matrix | `patterns/competition.py` | 16 cases |
| Stability harness | `patterns/perturbation.py` | 5 dimensions, cause classification |

---

## 3. Validation results

Full tables: **`docs/PATTERN_VALIDATION.md`** — generated at n=1000 per cohort,
174 cohorts, 174,000 detections.

### Positive recognition

Every family recognises its own structure. Textbook-cohort candidate rates run
92.7%–100%; ≥70 rates run 71.0%–100%.

The two lowest are worth stating rather than smoothing:

- **`bull_flag` textbook medians 74.3, with 0.8% reaching 80.** Its composite is
  capped by `resistance_quality`, which scores 25 because a 9-session flag has a
  single high and no cluster to test. That is structurally correct and it means
  bull-flag scores are **not comparable** with, say, cup scores — which is the
  cross-family calibration problem, stated concretely.
- **`base_on_base` textbook candidate rate is 92.7%.** Two ceilings inside the
  touch tolerance describe one base, and near that boundary noise decides. The
  intermittency is a property of the definition and has its own borderline
  cohort.

### Ordering holds

Across every family, medians fall monotonically from textbook → moderate →
borderline → invalid.

### Noise sensitivity, ranked

Aggregated over noise and adversarial cohorts (n≈8,000 per family):

| Detector | Candidate | ≥70 | ≥90 |
| --- | ---: | ---: | ---: |
| `flat_base` | 99.8% | **46.6%** | 0.8% |
| `double_bottom` | 28.1% | 21.5% | 4.2% |
| `breakout_retest` | 30.7% | 19.8% | 3.3% |
| `cup_handle` | 52.9% | 19.5% | 0.1% |
| `ascending_triangle` | 79.3% | 10.2% | 0.1% |
| `vcp` | 64.7% | 8.8% | 0.0% |
| `base_on_base` | 9.3% | 7.8% | 1.3% |
| `bull_flag` | 49.7% | 6.2% | 0.0% |
| `inverse_head_shoulders` | 9.9% | 1.8% | 0.1% |
| `pennant` | 25.9% | 0.2% | 0.0% |
| `tight_consolidation` | 8.2% | 0.2% | 0.0% |
| `high_tight_flag` | 0.2% | 0.1% | 0.0% |

**This corrects an earlier claim.** A narrow probe run before the corpus existed
reported the breakout retest as the noisiest family "by a wide margin". At n=1000
the **flat base is materially noisier** — 46.6% of noise series reaching 70,
against 19.8%. The claim has been corrected in the detector docstring, the test
that asserted it, and here. The earlier figure was not wrong about the retest; it
was wrong about the ranking, because it had not measured the alternative.

---

## 4. Candidate frequency versus quality frequency

The distinction the gate insists on, and it changes the reading of two families.

**`flat_base`**: 99.8% candidate — it finds *a* base in essentially every series.
Only 46.6% reach 70 and 0.8% reach 90. The discovery rule is permissive by
construction (single-anchor, gated on duration and swing availability) and the
*scoring* carries the discrimination: textbook median 89.2, quiet drift 75.8,
low-volatility walk 72.2, deep base 49.9.

**`breakout_retest`**: 30.7% candidate, 19.8% at ≥70. Decomposed by noise type it
fires **most on quiet noise** (73% candidate on low-volatility walks, 27.8% on
high-volatility ones) because a level needs price below it for 85% of its span,
which violent series do not oblige. It produces **nothing** on choppy or broad
volatile ranges.

Neither was changed. Both are cases where the *definition* is satisfiable by
chance, and lowering a rate by moving a threshold after measuring it would fit
the detector to its own corpus.

---

## 5. Cup and Handle versus the V-bottom

Analysed in `PATTERN_VALIDATION.md` § *Cup and Handle versus the V-bottom*, with
the full component table.

Exactly one component differs: **`bottom_roundness`**, separating by more than 35
points. Every other component is measuring an identical structure — the V-bottom
is generated with the same rims, depth, duration and handle — and returns an
identical number.

**The scoring reflects the stated definition.** The module documents roundness as
the part that matters most and measures it as time spent near the low rather than
by curve fitting, because fitting rewards charts that look like a picture of a cup
while this rewards charts where supply was absorbed.

**The limitation, stated:** pattern quality is multidimensional, and a structure
excellent in every dimension except one still receives a high composite. The gap
is what one component's weight is worth. Raising that weight until the V-bottom
fell below a line would be tuning a weight to move a number, and would also
degrade every genuine cup whose bottom is merely good.

**What would justify recalibration:** human labels and empirical outcomes.
Protocol in `PATTERN_LABELING.md` §5.

---

## 6. Competing-pattern matrix

16 cases, every enabled detector run over each. Full output in
`PATTERN_VALIDATION.md`.

Coexistence is the expected outcome (ADR-0019) and is not treated as confusion.
Three definitional exclusions are asserted by test:

- `high_tight_flag` does **not** fire on an ordinary bull flag.
- `base_on_base` does **not** fire on a stair-step.
- `double_bottom` does **not** fire on an inverse head and shoulders.

**Confusion cases recorded rather than corrected.** In several rows a family that
was not drawn outscores the one that was — the flat base above the ascending
triangle on a triangle series, for instance. That is the honest report that a
triangle with rising lows under a ceiling *is* a shallow base with a flat top.
The component rows distinguish them and the margin column says how reliable the
family label is.

---

## 7. Selection-bias results

`tests/unit/test_selection_bias.py` — 92 assertions, no skips.

- A **score-selecting twin** of each multi-anchor detector is built by subclassing
  and asserted to behave differently. Measured at `discover`, not `detect`, since
  that is where the rule applies.
- **Invariance under future bars**: a structural start never moves once later bars
  arrive, for all twelve.
- **Boundary provenance**: no structure starts inside the provisional tail; every
  boundary lands on a real session and carries an anchor date.
- **Regressions** for both historical leaks (flagpole end, VCP base start).

**Architectural property recorded:** three families — flat base, ascending
triangle, high tight flag — discover a single structure or none, so score
selection is impossible there by construction. The first two by rule; the high
tight flag in effect, because its magnitude gate collapses the candidate set. The
distinction is asserted, so relaxing the floor would reopen the failure mode
visibly.

---

## 8. Stability and monotonicity

`tests/unit/test_perturbation.py` — 63 assertions. Full table in
`PATTERN_VALIDATION.md`.

Under 0.1% price noise every family moves a **median of under 1.3 points**, the
largest being the ascending triangle at 1.27. Volume-only noise moves scores less
than price noise for every family, as it should when geometry is definition and
volume is evidence. Dropping three leading bars moves nothing at all.

**Across all sixty stability rows, zero large jumps are unexplained.** Every one
traces to a moved structural start, a re-identified key point, a state change, or
a pattern appearing — which is the result the framework was built to be able to
distinguish, rather than a result it was built to produce.

**A defect in the measurement instrument was found and fixed to get there.** The
first run reported three "unexplained" ~28-point jumps in the inverse head and
shoulders. Diagnosis showed the right shoulder had been re-identified nineteen
sessions earlier, with `shoulder_symmetry` moving 39.5 → 100 accordingly — a
genuine structural change the score was right to follow. The classifier compared
only the structural *start*, so it labelled real structure as noise. It now
compares every named key point, and the unexplained count fell to zero. An
instrument that mislabels structure as noise is worse than useless: it points
investigation at the wrong thing.

Monotonicity is imposed on eleven dimensions whose definitions imply a direction.
Three tests record where non-linearity is **deliberate**: prior decline and head
prominence peak in the middle, and a small undercut scores above an exact double,
because more damage is not more evidence.

---

## 9. Causality and history

- Causal pivot confirmation (ADR-0015): no structure defined from the provisional
  tail; state may read it, geometry may not.
- Identity stable across sessions and separate across timeframes (ADR-0016).
- Observations append-only; component scores stored per observation.
- Terminal states absorbing.
- Relationship edges carry the boundary they were derived under and refuse
  instances that postdate it.

---

## 10. Incremental versus full replay

Every detector is `BOUNDED_RESCAN_REQUIRED`. Rescan window is
`minimum_bars + 6 × atr_period` — the extra covering Wilder smoothing, whose
memory does not end.

**Equivalence asserted at zero tolerance** on identity, geometry, state,
components, coverage and invalidation, across a twelve-session bar-by-bar replay
run both ways, plus four single-boundary comparisons.

One documented exception: with a `PatternContext`, the incremental path falls back
to the full series, because truncating bars without truncating the benchmark
would misalign them.

Measured saving: **1.31×**.

---

## 11. Performance

Full detail in `docs/PATTERN_PERFORMANCE.md`.

| Measure | Value |
| --- | ---: |
| Full 12-detector daily scan | ~70 ms/instrument |
| Projected 4,000-name daily scan | **~4.5 min** |
| Weekly + daily | ~121 ms/instrument |
| Incremental saving | 1.31× |
| Scanning memory over bars held | +3.7 MB |
| Largest single detector share | 11.3% |
| Per-bar cost, 300 → 1,200 bars | flat within 4% |

Scaling is linear and asserted as such; the 4,000-name figure is an
**extrapolation with a stated basis**, not a measurement. No quadratic behaviour.
Performance is not the constraint on this system and no optimisation is warranted.

---

## 12. Storage

~10.4 instances per instrument per daily scan.

| Universe | Live identities | Observations/day | Observations/year |
| ---: | ---: | ---: | ---: |
| 500 | ~5,200 | ~5,200 | ~1.3 M |
| 1,000 | ~10,400 | ~10,400 | ~2.6 M |
| 4,000 | ~41,500 | ~41,500 | ~10.5 M |

**Relationship rows are the fastest-growing term** — quadratic in instances per
instrument per session, ~5 edges per instance, so ~200k rows/day at 4,000 names.
Three mitigation options are written down in `PATTERN_PERFORMANCE.md` §7 and
none is implemented, because choosing now would be optimising against an
estimate.

---

## 13. Known weaknesses by detector

From the worst negative cohort of each family. Where real-data validation should
look first.

| Detector | Weakest against | p99 | ≥70 |
| --- | --- | ---: | ---: |
| `flat_base` | regime switching | 94.0 | 31.5% |
| `double_bottom` | high-volatility walk | 97.7 | 65.6% |
| `breakout_retest` | quiet walk | 95.4 | 57.7% |
| `base_on_base` | trending walk | 94.7 | 40.4% |
| `cup_handle` | regime switching | 88.9 | 25.8% |
| `vcp` | regime switching | 85.6 | 45.0% |
| `ascending_triangle` | regime switching | 89.6 | 21.4% |
| `bull_flag` | single-day spike | 84.6 | 19.5% |
| `inverse_head_shoulders` | high-volatility walk | 86.3 | 8.8% |
| `tight_consolidation` | ordinary walk | 70.1 | 1.1% |
| `pennant` | single-day spike | 72.7 | 1.5% |
| `high_tight_flag` | high-volatility walk | 64.0 | 0.9% |

**The pattern in the pattern:** regime-switching noise is the hardest case for
four families, and high-volatility walks for three. Both produce genuine
structure by chance. The two families with the highest ≥70 rates on their worst
cohort — `double_bottom` at 65.6% and `breakout_retest` at 57.7% — should be
treated as weak evidence on their own until real labels exist.

---

## 14. Defects found and corrected during this gate

| # | Defect | Category | Correction | Version |
| --: | --- | --- | --- | --- |
| 1 | Five detectors permitted two structures to share an identity (38/74 cup instances collided) | implementation defect | deduplicate on structural start in the shared flow | 5 × v2 |
| 2 | Breakout retest anchored its structural start on the lookback window edge | definition inconsistency | anchor on the level's first touch | included above |
| 3 | Bull flag and VCP exposed `atr_period` only through config | definition inconsistency | added to the `Detector` protocol | none (no behaviour change) |
| 4 | Perturbation classifier compared only the structural start, mislabelling re-identified key points as unexplained noise | implementation defect (test instrument) | compare every named key point | n/a |
| 5 | VCP "non-monotone" cohort asserted structure inside the provisional tail | vacuous test | supply trailing bars; the causal lag is now documented | n/a |
| 6 | Base-on-base textbook cohort sat on the touch tolerance | vacuous test | move it; keep the boundary as its own cohort | n/a |
| 7 | Intraday generator emitted 26 bars/session labelled as minute bars, making aggregation tests the identity function | vacuous test | derive bar width from session length | n/a |
| 8 | Memory benchmark claimed per-instrument bounding it could not show | vacuous test | measure the scan's own contribution | n/a |
| 9 | "Noisiest family by a wide margin" claim unsupported at n=1000 | incorrect claim | corrected in three places | n/a |

**Why 1 and 2 are definitional rather than performance tuning:** no threshold
moved, no weight changed, no score was made better. What changed is that a
detector no longer emits two structures the persistence layer cannot tell apart,
and that a structural start is a structural fact rather than an artefact of
search depth.

Nothing was changed because an adversarial distribution looked unfavourable. The
flat base's 46.6% noise rate and the retest's 19.8% are both reported unmodified.

---

## 15. Tests, lint, types

| Check | Result |
| --- | --- |
| Test suite | **1,390 passed**, 2 skipped, 32 deselected |
| Coverage (`tradeit.patterns`) | 83–100% per module; 93% package-wide |
| Coverage (whole project) | 85% |
| `ruff check` | clean |
| `ruff format` | clean |
| `mypy --strict` | clean, 90 source files |
| Performance suite | 11 benchmarks, all passing |

The 2 skips are PostgreSQL-only integration tests.

---

## 16. Migrations and ADRs

**Migration `0005_phase4_gate`** — relationship provenance (`as_of_session`), the
five-value label vocabulary, label revisions, `detector_coverage`,
`config_digest`.

**New ADRs:**

| ADR | Subject |
| --- | --- |
| 0015 | A pivot is not knowable on the day it happens |
| 0016 | One structure is one identity, and its history is append-only |
| 0017 | Pattern quality and evidence coverage are two numbers |
| 0018 | Synthetic corpora prove code properties, never market accuracy |
| 0019 | One chart may carry several pattern readings at once |

Detector versioning was folded into ADR-0016 rather than given its own: a stored
pattern saying which definition produced it is the same decision as identity.

**Documentation:** `PATTERN_ARCHITECTURE.md`, `PATTERN_METHODOLOGY.md`,
`PATTERN_LIFECYCLE.md`, `PATTERN_RELATIONSHIPS.md`, `PATTERN_LABELING.md`,
`PATTERN_VALIDATION.md` (generated), `PATTERN_PERFORMANCE.md`, this report.

---

## 17. What remains open

### Real-market validation — **OPEN**

No claim in this phase is evidence about real-world precision, recall or
profitability. The corpus is synthetic, the generator draws what the detectors
look for, and market-data egress is blocked in this environment.

`PATTERN_LABELING.md` §5 specifies the protocol: stratified corpus targets, a
four-source sampling design with stated proportions, presentation rules, and the
rule that matters most — **future returns must never determine whether an example
enters the structural labelling dataset.**

### Cross-family calibration — **OPEN**

A cup's 88 and a bull flag's 74 are not comparable. The bull flag's composite is
structurally capped near 81; the cup's textbook median is 96.3. Establishing a
mapping requires the labelled corpus above. **Nothing downstream should compare
raw scores across families until it exists.**

### Phase 3 data gate — **OPEN**

Unchanged by this phase.

---

## 18. Exit criteria

| Criterion | Status |
| --- | :-: |
| All twelve detectors implemented | ✅ |
| All twelve adversarially characterised | ✅ n=1000 × 7 cohort kinds |
| Quality and coverage separate | ✅ ADR-0017 |
| Required structural inputs enforced | ✅ contracts |
| Historical identity and state history preserved | ✅ ADR-0016 |
| Selection-bias tests exist | ✅ 92 assertions |
| Perturbation/stability testing exists | ✅ 63 assertions, 5 dimensions |
| Competing-pattern behaviour characterised | ✅ 16 cases |
| Relationships work | ✅ 6 edges, causal, reproducible |
| Human labelling infrastructure works | ✅ 5 labels, 9 queue strategies |
| Multi-timeframe detection works causally | ✅ weekly/daily/4h/1h/15m |
| Integrated scanner works | ✅ |
| Incremental and full replay compared | ✅ zero tolerance |
| Integrated performance measured | ✅ |
| Storage implications understood | ✅ |
| Durable documentation exists | ✅ 8 documents, 6 ADRs |
| Tests / lint / types pass | ✅ |
| Real-market validation explicitly open | ✅ |

---

## 19. Recommendation for Phase 5

**Proceed to Phase 5 (Breakout Detection & Confirmation), with three conditions.**

**1. Treat pattern quality as one input among several, not a ranking.** The
cross-family calibration gap is real and measured. Phase 5 should consume
`(family, quality, coverage, state, components)` and apply its own per-family
logic, rather than sorting a mixed list by quality.

**2. Weight the two chance-satisfiable families lower until labels exist.**
`double_bottom` and `breakout_retest` reach ≥70 on 65.6% and 57.7% of their worst
noise cohorts. `flat_base` produces a candidate on essentially everything and
should be read through its score, never its presence.

**3. Do not let breakout confirmation reach back into pattern geometry.** The
temptation will be to re-anchor a pattern once a breakout confirms — a cleaner
resistance line, a tighter base. That is the retroactive-refinement leak this
phase spent the most effort preventing. Phase 5 should consume stored geometry
and add its own observations beside it.

### What Phase 5 needs that exists

Stored patterns with resistance levels, invalidation levels, states and
observation history; `BROKEN_OUT_UNCONFIRMED` as an explicit unresolved state;
volume and volatility primitives; the relationship graph; the scanner.

### What Phase 5 must build

Volume confirmation, follow-through measurement, failure detection, the
confirmed/unconfirmed distinction — and the vocabulary for it, which this phase
deliberately does not have.

---

**Phase 4 is complete. Phase 5 is not authorised and has not been started.**
