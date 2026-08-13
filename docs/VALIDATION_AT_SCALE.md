# Why the gate fell over on the full universe

The `full-01` validation reported a statement timeout in `phase5.monitor_floor`
and then `InFailedSqlTransaction` in six further checks, four of them Phase 4.
This records what was actually wrong, measured rather than guessed.

## 1. There was one root error, not two

The report groups by phase; the harness does not run in that order.

```
15. phase4.detection_rate      PASS
16. phase4.causality           PASS
17. phase5.state_distribution  PASS
18. phase5.boundary_provenance PASS
19. phase5.quality_frozen      PASS
20. phase5.monitor_floor       <-- QueryCanceled: statement timeout
21. phase4.score_distribution  <-- InFailedSqlTransaction
22. phase4.identity_stability  <-- InFailedSqlTransaction
23. phase4.identity_churn      <-- InFailedSqlTransaction
24. phase4.concentration       <-- InFailedSqlTransaction
25. phase5.lifecycle           <-- InFailedSqlTransaction
26. phase5.causality           <-- InFailedSqlTransaction
```

`all_checks()` is `[*data_checks(), *phase_checks(), *scan_checks()]`, and the
four Phase 4 checks that failed live in `scan_checks()` — after every Phase 5
check in `phase_checks()`. **There is no separate Phase 4 root exception.** All
six are cascade from check 20, and the phase grouping in the report is what made
them look like they came first.

## 2. The timeout: a hashed SubPlan falling off its cliff

`phase5.monitor_floor` asked for

```sql
pattern_key NOT IN (SELECT identity_key FROM patterns WHERE scan_run_id = ...)
```

`NOT IN` over a subquery can never be planned as an anti-join — the NULL
semantics forbid it — so PostgreSQL compiles it to a `SubPlan`. That is *fast
while the subquery result fits in `work_mem`*, because the SubPlan is hashed,
which is exactly why this survived every smaller corpus including `diag-02`.
Past that it degrades to a non-hashed SubPlan re-evaluated per row.

Measured on a 60,000-key fixture, crossing that one cliff:

| `work_mem` | plan | estimated cost | result |
|---|---|---|---|
| 4MB | `hashed SubPlan` | 7,654 | 0.07 s |
| 64kB | `SubPlan` | **53,634,079** | timeout |

The full-universe corpus had 272,537 keys. `NOT EXISTS` has no such cliff — it
plans as a parallel hash anti join and stays there under memory pressure.

**Measured on a full-scale corpus (272,537 patterns / 341,093 events):**
60 s timeout → **163 ms**.

## 3. The second, quieter problem: ORM materialisation

Three checks built one fully-populated ORM instance per breakout event to
compute a tally:

```python
events = list(session.scalars(select(tbl.BreakoutEvent).where(run.events())))
states = Counter(e.state for e in events)
```

341,093 instances: **23.8 s and ~1.9 GB resident**, for two numbers the database
produces in one grouped pass. `phase4.detection_rate` did the same over 272,537
patterns to read one attribute. `phase5.lifecycle` pulled 2,046,558 observation
rows into Python to ask a question about *pairs of states*, of which there are
fewer than two hundred.

## 4. What changed

| check | before | after |
|---|---|---|
| `phase4.detection_rate` | 15.16 s | 0.06 s |
| `phase5.state_distribution` | 0.27 s | 0.11 s |
| `phase5.monitor_floor` | **timeout** | 7.23 s |
| `phase5.lifecycle` | 7.41 s | 0.74 s |
| **total, 12 checks** | never finished | **14.16 s** |
| **peak RSS** | ~2 GB | **304 MB** |

The semantics are untouched. `phase5.lifecycle` still tests every recorded
transition — it groups first and asks `is_legal` once per distinct pair, which
is the same question the same number of times that matters. `monitor_floor`
still reads the pattern's state *as of the session the event opened*, from the
observation log, with the run in the join condition; the `DISTINCT ON`-style
correlated subquery returns exactly what the Python bisect returned.

One real bug surfaced while rewriting: `quality_frozen` counted its offenders
from a list that is now `LIMIT 5`, which would have capped any finding at five.
It counts in SQL and samples separately.

## 5. Transactional isolation

A failed statement leaves PostgreSQL's transaction aborted, so catching the
exception was never enough — every later statement on the same transaction
raises `InFailedSqlTransaction`. Each check now runs inside its own `SAVEPOINT`:

```python
savepoint = context.session.begin_nested()
try:
    result = check.run(context)
except Exception as error:
    savepoint.rollback()
    result = errored(check, error)
else:
    savepoint.commit()
```

`ROLLBACK TO SAVEPOINT` discards exactly the failing check's work and leaves the
transaction usable, which is safe because every check is read-only. The failing
check is still reported as ERROR, and now carries its own duration, so a timeout
is visible as a slow check rather than only a failed one.

Fixing the runner also fixed a bug it was hiding: the success path rebuilt
`CheckResult` field by field and silently dropped `detail`, which is where
`phase4.identity_churn` puts its per-detector table. It uses
`dataclasses.replace` now.

## 6. Indexes: measured, and none added

The access patterns were checked against `EXPLAIN (ANALYZE, BUFFERS)` on the
full-scale corpus rather than indexed on principle.

| access pattern | index | verdict |
|---|---|---|
| patterns by `(scan_run_id, identity_key)` | `uq_pattern_identity` prefix | present; planner prefers a hash join at this selectivity, which is correct |
| breakout events by `(scan_run_id, pattern_key)` | none | not needed — the join is a hash join, no seek |
| breakout events by `scan_run_id` | `ix_breakout_scan_run` | present |
| breakout observations via event/run | `uq_breakout_observation`, pkey | semi-join, 296 ms |
| pattern observations via pattern/run | `uq_pattern_observation` | semi-join, 182 ms; also serves the as-of lookup, which walks it backwards |

The `IN (SELECT ...)` predicates on the observation tables were *left alone* on
the evidence: PostgreSQL turns those into semi-joins and they run in under
300 ms. Rewriting them would have been churn justified by a pattern-match
rather than a measurement.

**No index was added.** Every one the rewritten queries need already existed.

## 7. The regression test

`tests/integration/test_validation_at_scale.py`, marked `performance`.

A 60,000-pattern fixture under the default 4MB `work_mem` runs the *fast*
`NOT IN` plan and would pass with the defect fully intact. The fixture therefore
pins `work_mem` to 64kB, which puts it on the far side of the same cliff the
real corpus fell off, and one test *asserts the old shape still times out
there* — so if a future change makes it fast again, the module says its own
guarantees have stopped meaning anything rather than passing quietly.
