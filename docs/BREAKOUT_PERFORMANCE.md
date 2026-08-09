# Breakout monitoring performance

Measured by `tests/performance/test_breakout_benchmarks.py`, marked
`performance` and excluded from the default run:

    pytest tests/performance/test_breakout_benchmarks.py -m performance -s

Figures below are from a single machine and will vary. What should not vary is
the **shape**: linear in instruments, flat in history length.

## The operational shape

Breakout monitoring is not a scan. It runs over the handful of patterns that are
actually live, once per new bar. Item 44 states the requirement directly:

> Incremental evaluation should be the normal operational path. Do not rescan
> years of history just to decide whether today's price crossed resistance.

So the headline number is the per-instrument incremental cost, and the
benchmarks are built to make an accidental full rescan visible rather than fast.

## One session across a universe

Three active patterns per instrument, which is generous for a filtered set.

| Instruments | Projected | Per instrument |
| --- | --- | --- |
| 100 | 0.06 s | 0.59 ms |
| 500 | 0.31 s | 0.61 ms |
| 1,000 | 0.57 s | 0.57 ms |
| 4,000 | **2.4 s** | 0.60 ms |

The 1,000 and 4,000 figures are extrapolated from a measured 120-instrument
constant. The extrapolation is stated rather than hidden, and the linearity it
rests on is **asserted** rather than assumed: `test_the_pass_is_linear_in_instruments`
compares per-instrument cost at 40 and 160 and fails if the larger run costs
more than twice as much per instrument. If the monitor ever became super-linear —
a lookup over all events per instrument, say — every projected figure here would
be wrong in the same direction, and the assertion is what catches that.

A nightly pass over 4,000 names is therefore **seconds**, against roughly 4.5
minutes for the Phase 4 pattern scan it follows. That ratio is the expected one:
the scan searches, the monitor checks.

## Per-session cost is flat in history length

| Series length | Per session |
| --- | --- |
| 60 bars | 0.255 ms |
| 90 bars | 0.240 ms |
| 120 bars | 0.233 ms |

Flat, as it must be. A monitor that re-derived the boundary or re-measured the
approach from the whole series would show cost rising with the series; what rises
here is nothing. This is the direct measurement of item 44's requirement, and it
is a test rather than a claim: the benchmark fails if the 120-bar cost exceeds
three times the 60-bar cost.

## Stage costs

| Stage | Cost |
| --- | --- |
| create an event | 93 µs |
| advance one session | 212 µs |
| a full event lifecycle (48 sessions) | 16.2 ms |
| persist one event with 42 observations | 16.1 ms |
| compute the config digest | 82 µs |

Event creation is bookkeeping plus a content hash, and it is asserted to be
cheaper than advancing — if creation ever dominated, the monitor would be paying
to mint events it immediately expires.

The config digest is computed **once per engine**, not per event. A digest
recomputed on the hot path would put a full Pydantic model dump into every
evaluation, which is the kind of cost that hides in a profile as "pydantic".

## Memory

60 instruments × 3 patterns held **0.19 MB**, about **3.2 KB per event**. The
measurement snapshots after the bars are built and retained, so what is reported
is the monitor's own contribution — the events and their observation histories —
rather than the harness's series.

Projecting to a live universe: 4,000 instruments × 3 live patterns × 3.2 KB is
roughly **38 MB** of resident event state, which is not a constraint. The
constraint is storage, below.

## Storage

Per event: one `breakout_events` row (~45 columns, three JSON blobs) plus one
`breakout_observations` row per session observed.

The observation table is the term that grows. An event observed from first
approach to resolution produces 20–50 rows; the clean synthetic scenario produces
42. At a projected 4,000 instruments × 3 live patterns × a mean 1.7 attempts,
with perhaps 5% of boundaries producing an event that reaches `CLOSED_ABOVE` in
any given month:

- **event rows**: order 20,000 per year;
- **observation rows**: order 600,000–1,000,000 per year.

Both are small against the Phase 4 estimates (~10.5 M pattern observations per
year, ~200 k relationship edges per day). Breakout monitoring is not the storage
problem; the pattern relationship graph is.

`breakout_relationships` is capped by construction — the ontology has five edge
types and the monitor derives none automatically — so it does not repeat the
Phase 4 relationship growth.

## What is not measured

- **Real-market throughput.** Every number here comes from synthetic series with
  a synthetic weekday calendar. Real data has gaps, halts, splits and thin names,
  and the ingestion cost of those is Phase 1's problem rather than this one's.
- **Concurrency.** The monitor is single-threaded and holds its events in a dict.
  Nothing here says what happens under parallel instrument sharding, which is the
  obvious scaling axis if the universe grows an order of magnitude.
- **Database round-trips at scale.** Persistence is benchmarked against SQLite
  in-process. The PostgreSQL path is exercised for correctness in
  `tests/integration` and not for throughput.
