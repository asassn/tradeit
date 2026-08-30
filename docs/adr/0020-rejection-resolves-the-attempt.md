# ADR-0020: A rejected breakout resolves the attempt; the next try is a new event

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5

## Context

Price runs into a resistance level, pushes through it intraday, and closes back
below. The Phase 5 brief calls this a rejection and adds: "A rejection may
remain non-terminal if the structural setup survives and another attempt is
possible. Define the rules carefully."

There are two ways to honour that, and they lead to very different datasets.

**Keep the event alive.** The rejected event walks back to `APPROACHING` and
waits for the next try. One event then spans the whole history of a pattern's
relationship with its level: three pushes, two rejections, one eventual break,
all inside one record.

**Close the attempt.** The event is resolved and the next qualifying close opens
a new one, carrying `attempt_number + 1`.

The first looks tidier and is quietly destructive. "How often is a breakout
attempt turned back?" has no denominator when attempts are not individually
countable — the same event contributes one row whether it was rejected once or
five times. Item 26 of the brief asks explicitly for the attempt history
(*"Pattern #1842, Attempt 1: Rejected, Attempt 2: Closed above, failed next day,
Attempt 3: Closed above with volume, confirmed"*), and that history cannot exist
if attempts share an identity.

There is a second cost. An event that spans three attempts has one breakout
quality score, one confirmation score and one set of components — which of the
three attempts do they describe? In practice they describe the last one, and
every earlier attempt's measurements are silently overwritten. That is the
failure the Phase 4 identity work exists to prevent, one layer up.

## Decision

**`REJECTED` is a resolved state with no path back to the pre-breakout states.**

`LEGAL_TRANSITIONS[REJECTED]` is exactly `{REJECTED, FAILED_BREAKOUT, EXPIRED}`.
There is no edge to `APPROACHING`, `TESTING_RESISTANCE`, `INTRADAY_BREAK` or
`CLOSED_ABOVE`, and `check_transition` raises with a message that names the
remedy rather than only the problem:

> a rejected attempt is resolved. The structural setup may well survive and be
> taken later — that is a new attempt, and it needs a new breakout event
> carrying attempt_number + 1, so that attempts remain countable.

The *setup* surviving is expressed by the monitor, not by the event. The monitor
keeps the boundary under observation and opens attempt N+1 when a later
qualifying close arrives; `attempt_number` is part of the event identity hash, so
each attempt has a distinct `event_key` and a distinct row.

`REJECTED` is separated from the terminal pair by `is_resolved` rather than
`is_terminal`: the attempt has nothing further to do, but the structure has not
failed, and a rejected attempt is not counted in any failure statistic.

**After `rejection.max_rejections` attempts the boundary has won.** The monitor
declines to open further attempts and closes the last rejected one as
`FAILED_BREAKOUT` with reason `REPEATED_REJECTION`. Going quiet instead would
leave a pattern in the monitored set forever, and — more importantly — would not
record the finding. Three attempts turned back at one level is a fact about that
level, not an absence of facts.

## Consequences

**Good.** Attempts are countable, so "how often does a first attempt fail and a
later one succeed?" is answerable from `attempts_for(pattern_key)`. Every attempt
carries its own frozen scores. A repeated-rejection outcome is recorded as a
failure of the thesis at that level rather than as silence.

**Costs.** More rows: a pattern that tests its level five times produces five
event records rather than one. At the measured rate — a mean of 1.7 attempts per
controlled scenario and up to 3.6 on a trending adversarial one — that is a
factor of roughly two on event volume, which the storage estimates in
`PHASE_05.md` account for.

The characterisation harness has to model the monitor's re-opening to produce
meaningful numbers. A harness that stopped at the first rejection would report a
clean breakout series as having no breakout in it about a third of the time,
because a base oscillating up to its level routinely turns one attempt back
before the real break. `replay_scenario` re-opens after `REJECTED` and `EXPIRED`
and not after `FAILED_BREAKOUT`, and says why.

**Rejected.** *Letting the rejection increment a counter on a single event.*
That keeps the identity stable and still loses the per-attempt scores, which is
the more damaging half of the problem.

*Making `REJECTED` terminal.* Simpler, and it would put every rejection into the
failure population — inflating the false-breakout rate with events where no
breakout ever occurred.
