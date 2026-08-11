# Empirical validation remediation — status

Against snapshot `twelve_data-daily-e3ddc03209bb25b4` (293,811 bars, 78
instruments, 4 quarantined rows).

| # | Finding | Status |
|---|---|---|
| 1 | `phase3.scale_invariance` FAIL — 32 violations | **Fixed.** Two defects, one cause. `docs/SCALE_INVARIANCE_INVESTIGATION.md` |
| 2 | `data.session_continuity` WARN — instruments 13 and 49 | **Diagnostics built; needs one run against your database.** Below |
| 3 | `data.survivorship_coverage` FAIL — 10 of 11 absent | **Classified. Still FAILs.** `docs/SURVIVORSHIP_COVERAGE.md` |
| 4 | 4 quarantined rows | **Diagnostics built; needs one run against your database.** Below |
| 5 | Re-run the gate | **Snapshot can be revalidated as-is.** Below |

Phase 4 and Phase 5 remain unstarted, no profitability statistic exists, and
nothing here begins the scanner or Phase 6.

---

## 2 and 4 need the database, not more code

Both findings are about *specific rows in your PostgreSQL instance*. That
database is on your machine; it was never copied here, and inventing plausible
values for instruments 13 and 49 would be worse than saying so. What could be
built here is the machinery that answers the question when you run it, and that
is what was built.

### `data.session_continuity`

The check already bounds its expectation by `[first observed bar, last observed
bar]`, so **sessions before a listing and after a delisting were never counted
as missing** — instruments 13 and 49 have *interior* holes. What it could not
say was what shape they are, and that is the whole question: 536 missing
sessions as one 26-month block and 536 spread over sixteen years are the same
integer and different defects.

It now resolves each flagged instrument to its ticker and groups the missing
sessions into maximal contiguous runs, classifying the result:

| shape | reading |
|---|---|
| `structural_break` | one absence of ≥ 21 consecutive sessions — a suspension, a relisting, or a history spliced from two listings. A rolling window spanning it reads a series nobody could have traded. |
| `scattered_absences` | many short absences — thin liquidity, or a listing on a calendar this project does not model. |
| `mixed` | both, in quantity. |

Instrument 13 is missing 536 of 4,174 (12.8%) — far too much for holidays or
thin liquidity, so expect `structural_break` or `mixed`, with the run's exact
dates printed. Instrument 49's 31 of 1,394 (2.2%) sits just over the threshold
and is the shape a foreign calendar produces. **Neither prediction is a
finding**; run it and read the dates.

Nothing is interpolated. The check reports and does not repair.

### The four quarantined rows

`data.quarantine_rate` reported `4` and nothing else. Everything needed was
already stored — identifier, pipeline stage, source file, 1-based line number,
rejection reason, and the raw payload — and none of it was printed. It now
lists every rejected row while there are fewer than 25 of them, and states the
connection a reader would otherwise have to make alone: **a quarantined bar is
a session with no row, so those four also appear as gaps in
`session_continuity`. They are the same rows, not two findings.**

Four rows in 294,000 is 0.001%, so they cannot account for instrument 13's 536.

The stage tells you which kind of problem it is:

| stage | means | fix |
|---|---|---|
| `normalized` | the value could not be parsed | declare the vendor's date or decimal format |
| `validated` | the numbers contradict each other, e.g. `low > high` | the vendor's print is wrong |
| `point_in_time` | nothing said when the fact became knowable | the package needs a knowledge-time rule |

**No row is repaired automatically.** A `low > high` bar cannot be corrected
without inventing a price, and an invented price is indistinguishable from a
real one afterwards.

---

## 5 Re-running the gate

**The existing snapshot can be revalidated; it does not need rebuilding.** The
Phase 3 fixes are in the indicator kernels, which run at validation time over
the stored bars — the bars themselves were never wrong. Re-import only if you
want the `[acquisition]` record, which requires a fresh acquisition rather than
a fresh import.

```
tradeit validate --snapshot twelve_data-daily-e3ddc03209bb25b4
```

Expected changes on this snapshot:

- `phase3.scale_invariance` — **PASS**, from 32 violations to 0.
- `data.session_continuity` — still WARN, now with tickers, gap runs, dates and
  a shape for instruments 13 and 49.
- `data.quarantine_rate` — still WARN, now naming all four rows.
- `data.survivorship_coverage` — **still FAIL**, now with the eleven-row roster:
  four `outside_requested_window` and six `unresolved` (this snapshot predates
  the acquisition record, so the vendor's answer for those six was never
  written down).
- Phase 4 and Phase 5 — still SKIP. Nothing between import and validation runs
  the pattern scanner or the breakout engine over an imported snapshot. That
  step is not started and is not authorised.

To move the six `unresolved` into real categories, and to recover the four
pre-window controls, re-acquire with a start date early enough to include them:

```
tradeit-data data acquire --start 2004-01-01 --output <dir> ...
```

which writes the `[acquisition]` record on the way through.

`acquire` now prints a warning before spending a single credit when the
requested start date excludes a delisted control the universe declares — the
check that would have caught this before the first full download:

```
WARNING    : 4 requested security(ies) stopped trading before 2010-01-01
             ENRNQ    last traded 2004-01-01
             BSC      last traded 2008-05-30
             LEH      last traded 2008-09-17
             WAMUQ    last traded 2009-03-20
             ...
             To include them all, use --start 2004-01-01 or earlier.
```

It is a warning, not an error: a deliberately recent window is a legitimate
thing to ask for. The `--start` default remains `2010-01-01`.
