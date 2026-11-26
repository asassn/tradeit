# A paper session that survives its own process — 2026-11-24

Phase 11 closed on **equivalence**: 1,457 of 1,457 replayed fills identical, and
later 2,516 of 2,516 sessions reconciled clean with the broker owning the book
([`PHASE_11_AUDIT_2026-10-18.md`](PHASE_11_AUDIT_2026-10-18.md)). Both are claims
about one process. Until today a paper session started flat in memory every time,
so a ten-year run was one process or it was nothing.

This document records the operation half: what was wired, what it measured, and
the defect the measurement found.

---

## The measurement

One universe (6 of 8 tickers resolved, all SIC-classified), 2012-01-03 to
2014-12-31, 754 sessions, run twice:

| | uninterrupted | stopped and resumed |
|---|---|---|
| sessions | 754 in one process | **610, then 144 in a second process** |
| reconciled clean | 754 of 754 | 610 of 610, then 144 of 144 |
| orders / fills | 20 / 20 | 13 / 13, then 7 / 7 |
| final equity | 100,944.67 | **100,944.67** |
| peak equity | 101,055.23 | **101,055.23** |
| `positions` rows | 10 | **10** |

**Every one of the 754 snapshots agrees, and all 10 position rows agree, to
within 4.837e-8.** That residual is not a logic difference: it is
`Numeric(18,6)`. The uninterrupted run's snapshots carry the in-memory `Decimal`;
the resumed run's cash came back *through* the column, truncated at the sixth
decimal, and the 144 post-restart sessions carry that truncation forward. A
restart therefore costs about five hundredths of a microdollar, permanently, and
nothing else.

The cut was deliberately placed mid-holding — 2014-06-06, three open positions,
all three with a recorded ladder. A cut on a flat book would have proved only
that cash round-trips.

## What is stored, and what is re-derived

The distinction is the whole design, and getting it backwards in either
direction is a defect.

**Stored, because it cannot be re-derived.** The stop ladder. A high since entry
needs every bar of the holding period replayed; `positions` holds none. A
trailing stop resumed from a reset high is *looser* than the one that was in
force, a time stop resumed from zero never fires, and a forgotten partial profit
can be taken twice — three silent loosenings of risk per restart. Migration
`0022_position_ladder_state` added the three columns nullable and backfilled
nothing, so **"nobody recorded this" stays distinguishable from a number**, and a
position whose high was never recorded is **refused**, not defaulted. The
caller's options are to re-derive it from prices or to decline to resume;
substituting the entry price is a third that looks like the first.

**Re-derived, because storing it would be a second source of truth.** The
signal's own state. The moving averages are a function of prices alone, so a
resumed run walks the sessions it has already recorded through the candidate
source *without deciding on them* — 610 of them, in order. Skipping them would
leave `MovingAverageCross._was_above` empty and manufacture a crossing on the
first session back.

**The peak equity is read back from the record**, not held in memory. A peak that
reset on every restart would understate every later drawdown, and understating
drawdown is the direction that gets a strategy promoted.

## The limitation, stated rather than buried

**At the restart boundary our book and the venue's come from the same recorded
rows.** A real broker's book survives our restart — that is the entire reason
reconciliation is worth running. A simulated venue has no memory, so without
`PaperBroker.restore` a restored book would reconcile against an empty one and
halt on the first session: the simulation having forgotten, reported as a drift.
The consequence is that **the first session after a restart cannot detect a drift
incurred across the restart.** Every session after it evolves the two
independently again and drift is detectable as before.

A paper venue cannot do better. The alternative is to give the simulation its own
persistence, which would be a second record claiming to be an independent
authority while still being written by this process.

---

## The defect the comparison found

Comparing the two records was supposed to confirm the wiring. It found a
pre-existing bug in `execution/live_shape.py` instead.

**Every re-entry in an instrument inherited the first entry's stop.**
`_pending_stops` was keyed by client order id and looked up by *prefix match* —
first hit, never removed. The first entry's key therefore matched every later
fill in that instrument, for the lifetime of the process.

Measured on the record above, instrument 1695 was entered four times:

| opened | entry price | initial stop written | distance | declared |
|---|---|---|---|---|
| 2013-04-05 | 75.5995 | 70.104 | 7.3% | 8% |
| 2013-11-14 | 77.8015 | 70.104 | 9.9% | 8% |
| 2014-05-05 | 78.9116 | 70.104 | 11.2% | 8% |
| 2014-10-23 | 76.4196 | 70.104 | 8.3% | 8% |

**The initial stop defines R.** Every risk figure derived from those positions —
heat, size, R multiple, the sizer's own cash and participation caps — was
computed against a number decided for a different trade at a different price, up
to eighteen months earlier.

**Only the restart exposed it.** A second process starts with `_pending_stops`
empty, so its first entry in an instrument got its own stop while the
long-running process did not. Two position rows disagreed, and only two, because
only two positions were opened after the cut.

This is the failure shape `CLAUDE.md` names: *754 of 754 sessions reconciled
clean and both runs agreed on final equity.* The expected answer arrived, and it
was not an answer to the question asked. It would not have been found by running
once, however long the run.

**The fix is two separate invariants**, and they are tested separately because a
mutant that broke only the second survived the test for the first:

1. **One pending stop per instrument, overwritten by the latest decision.** An
   entry is only submitted for an instrument we do not hold, so a second
   submission means the first has not filled and its stop is stale by
   definition.
2. **Consumed by the fill that uses it.** A fill arriving with no decision behind
   it must reach the fallback stop rather than inherit the last one recorded.
   With the stop merely read, the fallback becomes unreachable for any instrument
   ever entered — and it is the only thing standing between a forgotten order and
   a position with no defined risk.

### What this costs the earlier audit

`PHASE_11_AUDIT_2026-10-18.md` reports 2,516 of 2,516 sessions clean over
195 names, 2010–2019, with final equity 143,331.79. **Its reconciliation result
stands** — the defect never put our book out of step with the venue's, which is
what that run measured, and that is precisely why reconciliation did not catch
it. **Its equity figure does not**: 1,223 orders over ten years across 195 names
contain many re-entries, every one of which was stopped at a price decided for an
earlier trade. The run is not re-stated here; it is a historical record and is
marked with a pointer to this document instead.

Nothing archived outside that run is affected. `live_shape.py` is not on the
backtester's path, no recorded backtest, trial or scoreboard entry runs through
it, and `research-01`'s evidence rules are untouched.

---

## Still not done

* **No test covers the script.** The signal-replay rule — walk the recorded
  sessions through the candidate source, deciding nothing — lives in
  `scripts/paper_live_shape.py` and is held only by the run above, not by the
  suite. Scripts are untyped in CI by design, and
  `tests/unit/test_script_call_arity.py` catches a call that cannot run, not a
  rule that is wrong.
* **A restart is still operator-driven.** Nothing schedules it, and the one
  unattended agent in this system captures prices, not sessions.
* **`record_trade` remains unusable** by its own account until a closed position
  is written with its realised P&L; `sync_positions` closes the row and leaves
  the P&L to `executions`, which it does not read.

---

## Addendum, 2026-11-26 — the cut had nothing in flight

Running the same book **one process per session** for the 144 sessions after
2014-06-06 places 379 orders and fills none, where one process places 20 and
fills 20 ([`PLAN_FROM_BOOK_2026-11-26.md`](PLAN_FROM_BOOK_2026-11-26.md)). An
order decided at a close fills at the next session, the record holds no
orders, and `PaperBroker.restore` restores cash and holdings only — so every
order in flight at a restart is dropped. The measurement above is correct for
its cut, which had no order in flight; it does not establish that a run
stopped at an arbitrary close resumes as one run.

**Fixed the same day.** Orders are now recorded and restored. The same 144
sessions run as 144 processes reproduce the single run exactly: 20 of 20
orders, 10 of 10 positions, 754 snapshots within 4.8e-7, final equity
100,944.67 ([`PLAN_FROM_BOOK_2026-11-26.md`](PLAN_FROM_BOOK_2026-11-26.md)).
