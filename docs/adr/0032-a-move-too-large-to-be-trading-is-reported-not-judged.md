# ADR-0032: A move too large to be trading is reported, never judged

**Status:** accepted
**Date:** 2026-11-15
**Supersedes:** nothing. **Amends:** nothing. **Companion to**
[ADR-0031](0031-a-bar-is-a-test-not-a-number.md), whose method — *express the
bar as a test against something already written, so the number comes from the
data* — this applies outside the readiness framework for the first time.

## Context

`ELME_VENUE_BREAK_2026-11-15.md` measured three defects the capture agent cannot
see. ELME's shares stopped trading on the NYSE after 2026-10-23 and traded OTC
as `ELMES` from 2026-10-26. EODHD served both venues as **one continuous series
with no field distinguishing them**, a −97.6% single-session move at the join,
and then retroactively re-mapped the whole history to the new ticker so that the
nine OTC bars we hold under `instrument_id = 1700` are no longer served under
the ticker they were acquired under. Three of sixteen bars have since been
revised.

The capture returned `ok` sixteen times. Nothing failed, nothing was logged, and
**the break was found three weeks later because a person asked a question nobody
had scheduled.** That is the part worth fixing: the measurement was available on
2026-10-26 and no mechanism was looking.

Two obvious responses are both wrong.

**Quarantine the suspect bars.** This requires deciding which price is right,
which is the which-vendor-wins decision that `_write_bars`'s never-overwrite rule
deliberately defers. A detector that quarantined would take that decision by the
back door, and it would do so from the weakest possible evidence — a large
number.

**Name the cause.** A flag reading *"splice detected"* would be inventing a
fact. A splice, a venue change, a vendor re-mapping, a forward split, a reverse
split and a share consolidation all produce the same shape in a close series,
and nothing in the record distinguishes them. This is `CLAUDE.md`'s *never
answer by accident* exactly: the flag would be right about ELME for the wrong
reason, and wrong the first time it fired on a split.

## Decision

**The capture gains a report-only check that flags single-session price moves too
large for trading to explain, names no cause, writes nothing, and never changes
an exit code.**

Four properties, each with a test that was shown to fail without it:

1. **Report only.** It runs *after* the captures and reads what they wrote. It
   has no access to the write path, so it cannot pre-empt the which-vendor-wins
   decision even by mistake.
2. **No cause claimed.** The finding says the move is not a trading move and
   that the record does not explain it. Naming the mechanism is a person's job,
   as it was for ELME.
3. **Candidate explanations beside the finding, never instead of it.** A
   corporate action in the record dated on the later session is reported
   alongside, so a flagged 2:1 split is visibly a known split. It never
   suppresses the finding — a 2:1 split recorded beside a 42× move does not
   explain it, and that judgement is not the detector's. Fail closed.
4. **Never fails the job.** A report that failed the capture would turn a
   *correct observation* into a red run, and a job that goes red when it is
   working is a job whose output gets switched off.

### The threshold is derived from the record, not chosen

Measured 2026-11-15 over the whole forward record — 4,327 bars, 195 instruments,
4,132 consecutive session pairs, direction-free ratio `max(a/b, b/a)`:

| pair ratio | |
|---|---|
| median | 1.014 |
| 99th percentile | 1.143 |
| 99.99th percentile | 1.490 |
| **largest, ELME excluded** | **1.490** (−32.9%, a penny stock trading) |
| ELME at the venue break | **42.243** |

Two constraints fix the number, and **they do not conflict, so it is determined
rather than picked**:

1. It must sit **above every move trading produced** → floor 1.49.
2. It must sit **at or below the smallest ratio a corporate action produces**, or
   routine actions escape. The smallest in common use is 2:1 → ceiling 2.0.

`2` is the ceiling itself and the only round number in the window, so it is the
strictest value satisfying both. It sits 34% above the largest observed trading
move and a factor of 21 below the one known identity event. Every candidate from
1.5× to 10× fires on exactly one pair in the current record, so the choice inside
that range costs nothing today and constraint (2) is what binds.

**This is not a `ThresholdSource`.** Reusing the readiness enum was the obvious
move and is wrong: it exists to say whether a threshold may authorise a
promotion, and this one authorises nothing. Carrying it here would invite wiring
a report-only check into a readiness gate, which is the one thing it must not
become. The test asserts the derivation's *bounds*, not the number, so moving the
threshold out of `(1.49, 2.0]` fails until somebody re-derives it.

### The re-measurement condition

4,132 pairs over 17 sessions is a small sample and 1.490× is an observed maximum,
not a bound; a longer record will contain larger trading moves. **If a genuine
trading move ever exceeds the threshold, the answer is to re-measure and record
the new distribution — not to raise the bar until the flag goes quiet.** Raising
a detector's threshold to silence it is how a safeguard becomes decoration, which
is the failure mode `CLAUDE.md` describes for every other safeguard in this
codebase.

## Consequences

**What it buys.** The ELME shape is reported on the night it occurs rather than
three weeks later by accident. The measured rate is roughly one instrument a
month at the current universe size, so it will not become noise. The full scan
over the current record takes 0.42 s.

**What it does not buy.** It is blind to the other two ELME defects. A vendor
re-mapping with no price move leaves no trace in a close series, and a revision
to a bar we already hold is only visible by re-asking — which needs the
which-vendor-wins decision. Both remain open and are recorded as open in
`ELME_VENUE_BREAK_2026-11-15.md`.

**What it is honest about.** While nothing writes corporate actions to the
forward record — measured 2026-11-15: zero rows — *every* finding is unexplained,
and the report says so rather than letting "unexplained" read as a stronger claim
than it is.

## Alternatives rejected

| | why not |
|---|---|
| **A percentage threshold** | Not direction-free. A reverse split moves the price *up*, so a one-sided percentage test catches ELME and misses every consolidation — the same defect mirrored. |
| **Quarantine the bar** | Takes the which-vendor-wins decision from the weakest evidence available, a large number. |
| **Name the cause in the flag** | Inventing a fact. Right about ELME for the wrong reason, wrong on the first real split. |
| **Suppress a flag a recorded action explains** | Becomes a suppression. A recorded 2:1 split beside a 42× move would silence it. |
| **Block the capture on a finding** | Trades a session, which cannot be bought back, for a warning. |
| **Also flag a price on zero volume** | Real (§0.7, ELME's last NYSE bar) and already a documented read rule, but a *different question*. Two questions from one function is how a report becomes unread. |
| **A statistical threshold per instrument** | Needs a per-instrument volatility estimate from 17 sessions. The estimate would be noisier than the thing it gates, and it would quietly become a model. |
