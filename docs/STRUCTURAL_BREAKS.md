# Structural breaks: when analytical state resets, and when it does not

The first real snapshot contains a series that is **not one security**.

Under the ticker `BBBY` there are 3,638 bars running 2010-01-04 to 2026-08-07,
with a 536-session hole from 2023-07-05 to 2025-08-21. Bed Bath & Beyond
stopped trading on 2023-05-03. The bars after the hole belong to whatever later
took the symbol, and the vendor handed both to us under one `instrument_id`.

Two separate consequences follow, and both are fixed.

---

## 1. It silently passed a survivorship control

`data.survivorship_coverage` reported **1 of 11 covered**. The one was BBBY —
passed on the strength of a *different, surviving* company's prices. Coverage
was measured as "enough bars, recent enough", and a spliced series satisfies
both.

That is the exact substitution the module forbids in its own docstring, arrived
at without anyone deciding to do it. A control's series may now run no more than
`TICKER_REUSE_TOLERANCE_DAYS` (92) past its recorded last trade date:

```
BBBY  INSUFFICIENT_HISTORY
      series runs to 2026-08-07, 1192 days *after* the recorded last trade
      2023-05-03: the symbol has been reused and this history is not one
      security's
```

**Survivorship coverage on this snapshot is 0 of 11, not 1 of 11.** The honest
number is worse than the reported one, which is the direction that matters.

---

## 2. Nothing analytical may cross the break

An **analytical episode** is a maximal run of sessions with no structural break
inside it. Each episode is scanned with its own `PatternScanner`, its own
`PatternTracker`, its own `BreakoutMonitor`, and a **hard bar floor** at the
episode's first session.

The floor is the load-bearing part. Without it, on the first session after the
gap:

| what | would have happened |
|---|---|
| `sma_200`, `ema_*` | averaged prices from two companies |
| ATR, realised volatility, Bollinger width | inherited a range no participant experienced |
| confirmed pivots | a "swing low" from a different issuer's order book |
| pattern identities | a pre-break structure carried forward or re-detected after it |
| pattern lifecycle | a state transition spanning the boundary |
| breakout monitors | an attempt opened when the new listing crossed a dead company's level |
| retest state | a pullback measured to a level from a different security |

Because each episode starts a fresh tracker, a pre-break identity has **no
object on the far side to be advanced onto** — the impossibility is structural,
not probabilistic.

### Why a reset rather than a bridge

The architecture has no mechanism that could establish continuity here, and the
one it does have argues the other way. Identity in this system is the surrogate
`instrument_id`, and `symbol_mappings` exists precisely because **a ticker
string is not an identity**. A vendor's decision to reuse a symbol is not
evidence that two price series belong to one economic entity, and no amount of
point-in-time discipline turns a 26-month hole into a tradeable history.

If a data source later supplies a listing-level identifier that genuinely
proves continuity across a gap, `tradeit.scanning.episodes` is the seam to
relax — on that evidence, not by widening a threshold.

### No bars are manufactured

The gap stays a gap. An episode is a view over the sessions that exist, never
an interpolation over the ones that do not. There is a test.

---

## 3. Where the boundary sits, and why scattered gaps are different

`BREAK_SESSIONS = 21` — roughly a trading month — and it is **imported from**
`STRUCTURAL_RUN_SESSIONS`, the constant the continuity check already uses, so
the two can never drift: a gap the gate reports as structural is exactly a gap
the scanner resets on.

| shape | example in this snapshot | state |
|---|---|---|
| one run ≥ 21 sessions | BBBY: 536 sessions | **reset** |
| many short runs | NKLA: 31 sessions across 11 runs over 5 years (2.22%) | **preserved** |
| one session | each of the 4 quarantined bars | **preserved** |

**Scattered absences are absent observations inside a continuous listing, not a
discontinuity.** Resetting on them would be the opposite error and a worse one:
it would report every missing print as a new security, destroy identity
continuity for every thinly traded name, inflate the count of distinct patterns,
and make any rate computed over them meaningless. Brief absences are already
handled one layer down by the pattern tracker's grace period, which is the right
place for them.

The gap is measured in **trading sessions the exchange held**, not calendar
days. A Thanksgiving week spans many calendar days and few sessions; using the
calendar difference would reset every instrument several times a year.

---

## 4. The four quarantined rows

All four are internally impossible vendor prints, and all four stay quarantined
— repairing one would mean inventing a price, and an invented price is
indistinguishable from a real one afterwards.

| instrument | date | violation |
|---|---|---|
| 34 | 2023-06-05 | high 30.415 < open 30.46 |
| 60 | 2012-05-23 | high 62.78 < close 63.20 |
| 60 | 2021-05-05 | low 39.445 > open 39.060001 / close 39.78 |
| 60 | 2023-01-24 | low 56.26 > open 51.26 |

**None contributes to either reported continuity gap.** The gaps are on
instruments 13 (BBBY) and 49 (NKLA); the quarantines are on 34 and 60. Each
quarantine removes exactly one session from *its own* instrument's series — a
one-session hole, far below both the 2% flagging threshold and the 21-session
break threshold, so it neither raises a continuity finding nor resets an
episode.

Quarantine reports now print the ticker beside the surrogate id, list the
instruments affected, and state explicitly that a continuity finding for an
instrument *not* in that list has another cause.
