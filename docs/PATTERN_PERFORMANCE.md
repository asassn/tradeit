# Pattern System Performance

Measured cost of the integrated pattern system, the operational budget it is held
to, and the storage growth it implies.

All figures from `tests/performance/test_integrated_benchmarks.py`, run on the
development container (Linux, Python 3.11). Absolute numbers vary with hardware;
the **scaling behaviour** is what transfers.

---

## 1. Headline

| Measure | Value |
| --- | ---: |
| Full 12-detector daily scan | **~70 ms per instrument** |
| Projected 4,000-name daily scan | **~4.5 minutes** |
| Projected 1,000-name daily scan | ~68 s |
| Projected 500-name daily scan | ~34 s |
| Weekly + daily configuration | ~121 ms per instrument |
| Incremental vs full scan | **1.31× faster** |
| Peak memory, scanning cost | +3.7 MB over the bars held |

A nightly US-equity scan is comfortably inside any reasonable batch window.
Performance is **not** the constraint on this system, and no optimisation work is
warranted on these numbers.

---

## 2. Scaling is linear

| Universe | Elapsed | Per instrument |
| ---: | ---: | ---: |
| 25 | 1.73 s | 69.0 ms |
| 50 | 3.51 s | 70.3 ms |
| 100 | 7.08 s | 70.8 ms |

Per-instrument cost varies by under 3% across a 4× change in universe size. The
extrapolation to 4,000 names rests on this, and the linearity is **asserted by
test** rather than assumed — a regression that introduced cross-instrument state
would break the assertion before it broke anything else.

### The extrapolation is stated, not disguised

A literal 4,000-name run with full history is expensive and would tell us nothing
the linearity check has not established. So the benchmark measures 100 names,
asserts linearity across three sizes, and multiplies. An extrapolation whose basis
is written down can be argued with; one presented as a measurement cannot.

---

## 3. No detector dominates

Across 30 instruments, 2.12 s of detector time:

| Detector | Share |
| --- | ---: |
| `breakout_retest` | 11.3% |
| `vcp` | 10.7% |
| `tight_consolidation` | 8.8% |
| `bull_flag` | 8.8% |
| `flat_base` | 8.6% |
| `double_bottom` | 8.5% |
| `ascending_triangle` | 7.6% |
| `base_on_base` | 7.4% |
| `cup_handle` | 7.4% |
| `pennant` | 7.1% |
| `high_tight_flag` | 7.0% |
| `inverse_head_shoulders` | 6.9% |

The spread is 11.3% to 6.9% — remarkably flat, and expected: every detector pays
the same shared cost for ATR and pivot confirmation, and their own discovery is a
small fraction on top. The breakout retest is highest because it enumerates
anchors and rebuilds a resistance cluster for each.

The benchmark asserts no detector exceeds 60% of the total. That bound is not
about speed: a detector that dominated would usually mean an accidental
quadratic, and this is the cheapest place to notice one.

---

## 4. No quadratic behaviour

Per-bar cost across series lengths:

| Series length | Per bar |
| ---: | ---: |
| 300 bars | 246.6 µs |
| 600 bars | 238.6 µs |
| 1,200 bars | 255.2 µs |

Flat to within 4% across a 4× change. An O(n²) detector would show a rising
per-bar cost, and the test asserts a ratio under 1.5× between the shortest and
longest series.

This matters beyond speed: a per-bar cost that rose with series length would mean
a detector is scanning history its contract claims to have bounded, which is a
**correctness** signal about its declared lookback.

---

## 5. Incremental scanning

Every detector is `BOUNDED_RESCAN_REQUIRED`. Neither extreme applies:

- **Not incremental-safe (O(1) update).** A new bar can confirm a pivot several
  sessions back, and a newly confirmed pivot can change where a structure
  *starts*. Yesterday's answer is not a valid prefix of today's.
- **Not full-rescan.** Every detector's discovery is bounded by its declared
  lookback. Bars older than that cannot affect the result — except through the
  indicator warm-up.

The warm-up is the subtlety. ATR is Wilder-smoothed: an exponential average with
**unbounded memory**, so truncating the series changes every later value by a
decaying amount rather than not at all. The rescan window is therefore
`minimum_bars + 6 × atr_period`. Six multiples leaves a residual influence of
about `exp(-6)` ≈ 0.25%, three orders of magnitude below any threshold a detector
tests.

| Detector | Warm-up | Rescan window |
| --- | ---: | ---: |
| `bull_flag` | 43 | 127 |
| `vcp` | 92 | 176 |
| `flat_base` | 97 | 181 |
| `tight_consolidation` | 152 | 236 |

Required history for a full daily scan: **236 bars** — under a quarter of a
decade of dailies, which is where the 1.31× saving comes from.

### Equivalence with full replay

Asserted at **zero tolerance** on identity, geometry, state, components, coverage
and invalidation, across a twelve-session bar-by-bar replay run both ways.

A bounded rescan that produced *nearly* the same answer would mean the bound was
chosen by hope, and the difference would surface later as patterns appearing and
vanishing depending on how much history happened to be loaded.

**One deliberate exception.** When a `PatternContext` is supplied, the incremental
path falls back to the full series. Truncating bars without truncating the
benchmark would misalign them; the alignment check would catch it, but the honest
response is not to try. Correct and slower beats fast and wrong.

---

## 6. Memory

| Measurement | Value |
| --- | ---: |
| 50 series held | 27.3 MB |
| After scanning them | 31.0 MB |
| **Scanning cost** | **+3.7 MB** |

Memory is dominated by the **bar series**, not by the pattern output. The
operational consequence is concrete: a production scan should **stream**
instruments rather than materialise a universe. Doing so keeps peak memory at
roughly one instrument's series plus the tracked pattern set.

> An earlier version of this benchmark claimed peak memory was bounded per
> instrument. It is not, and that test could not have shown it either way — the
> harness held every series up front, so what it measured was the harness. The
> corrected test measures the scan's own contribution, which is the number that
> was wanted.

---

## 7. Storage growth

Measured at ~10.4 pattern instances per instrument per daily scan.

| Universe | Live identities | Observations/day | Observations/year |
| ---: | ---: | ---: | ---: |
| 500 | ~5,200 | ~5,200 | ~1.3 M |
| 1,000 | ~10,400 | ~10,400 | ~2.6 M |
| 4,000 | ~41,500 | ~41,500 | ~10.5 M |

### Relationship rows

Relationship derivation is **quadratic in instances per instrument per session**.
Measured at roughly 5 edges per instance, so a 4,000-name daily scan implies on
the order of **200,000 relationship rows per day**, ~50 M per year.

This is the one number that could become a problem, and it is worth stating the
options before it does rather than after:

1. **Persist only cross-timeframe and cross-family edges.** Same-family
   `OVERLAPS` edges between near-duplicate readings are the bulk of the volume and
   the least informative.
2. **Persist edges only for patterns above the reporting floor.** Derivation stays
   complete in memory; storage keeps what a consumer would query.
3. **Recompute rather than store.** Derivation is a pure function of the session's
   instances, so edges are reproducible from the observation history. Storage
   becomes a cache.

Option 3 is the most attractive and the most reversible, but none has been
implemented: the right time to choose is when a real universe produces a real
number, and choosing now would be optimising against an estimate.

### What is stored per observation

Component scores are stored **per observation**, not only currently, because *"why
did this decay?"* is answerable from the component history and unanswerable from
the composite alone. That is the largest single contributor to observation row
size and it is deliberate.

Full geometry is stored on the `patterns` row, not per observation — sufficient
to redraw a pattern without recomputing it, and recomputation against a longer
series would draw a different pattern and call it the same one.

---

## 8. The operational budget

**Target:** a nightly scan of a few thousand US equities on daily bars, finishing
inside the window between the close and the next pre-market.

**Status:** met with a wide margin. ~4.5 minutes of compute against a window of
several hours.

The benchmark asserts a 4-hour ceiling. That bound is deliberately loose: it
exists to catch an order-of-magnitude regression, not to pin a number that varies
with hardware. Tightening it would produce a test that fails when the CI runner
is busy, which trains people to ignore it.

### If the budget were ever missed

The dominant costs in order, so the analysis does not have to start from scratch:

1. **Shared per-instrument setup** — ATR and pivot confirmation, paid once per
   instrument per timeframe regardless of how many detectors run. Disabling
   detectors saves less than expected for this reason.
2. **Relationship derivation** — quadratic in instances per session, and the
   fastest-growing term.
3. **Persistence writes** — not measured here (the benchmark tracks in memory);
   a real deployment should measure this separately, since it is the only part
   that leaves the process.

**Causal correctness is not negotiable for speed.** The rescan window could be
shortened and the scan would get faster and wrong; the provisional tail could be
used for structure and every detector would find more; neither is available.
