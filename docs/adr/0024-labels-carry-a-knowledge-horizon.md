# ADR-0024: Every breakout label carries an explicit knowledge horizon

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5

## Context

Item 42 asks for human labelling of breakout events with a seven-value
vocabulary, and item 43 constrains how the dataset may be built:

> The structural breakout-labeling dataset must be created based on information
> visible at the labeling AsOfClock. Do not select positive breakout examples
> solely because they later rallied. Do not label false breakouts solely because
> they later declined unless the label definition itself explicitly includes the
> subsequent confirmation/failure window known at the label's terminal
> timestamp. **Be precise about each label's knowledge horizon.**

The difficulty is that the vocabulary is not homogeneous. "Was this a valid break
of the level, or a weak one?" is a question about the breakout bar and needs no
hindsight at all. "Was this a false breakout?" is a claim about what happened
next and *cannot* be answered from the bar. Both are legitimate labels. What is
not legitimate is a corpus in which some rows were assigned from the breakout bar
and others from a month of hindsight, with no record of which — because those
rows encode different amounts of future information and nothing downstream can
separate them.

The Phase 4 pattern-labelling framework did not need this: every pattern label
is a judgement about a structure visible at the labelled session.

## Decision

**Every label declares two dates and a session count.**

- `as_of_session` — the breakout session being judged.
- `knowledge_horizon_session` — the last session the reviewer was shown.
- `sessions_of_hindsight` — the window in *trading* sessions.

The session count is supplied by the caller rather than derived from the two
dates, because this module has no trading calendar and deriving it from calendar
days would understate a window spanning a holiday week — precisely the kind of
quiet inaccuracy the horizon exists to prevent.

**`LABEL_HORIZONS` states, per label, the minimum window its definition
requires**, and a request that does not have it is refused at construction:

| Label | Minimum hindsight |
| --- | --- |
| `VALID_BREAKOUT` | 0 |
| `WEAK_BREAKOUT` | 0 |
| `FALSE_BREAKOUT` | 3 |
| `SUCCESSFUL_RETEST` | 5 |
| `FAILED_RETEST` | 5 |
| `AMBIGUOUS` | 0 |
| `INSUFFICIENT_EVIDENCE` | 0 |

A `FALSE_BREAKOUT` assigned with a horizon equal to the breakout session raises
with a message saying that nothing visible by that date could support the
judgement. The database carries the same rule as a check constraint on
`knowledge_horizon_session >= as_of_session`.

**No profitability question is asked, and there is no column for the answer.**
The vocabulary has no member for it, `breakout_labels` has no outcome column, and
a test asserts both. Outcome data attaches separately and later, with its own
horizon. Mixing it in here would make every downstream use of the structural
labels circular: a model trained on labels that encode returns has learned the
returns.

**The default queue strategy is `FUTURE_BLIND`.** Selection uses only the
breakout session's own reading — the frozen quality, the state at sampling time —
and is stratified by quality band. It is deliberately **not** stratified by
outcome state: balancing the corpus across confirmed and failed events would use
the outcome to decide which examples a human sees, which is item 43's bias
wearing a respectable hat. The queue, not the reviewer, sets
`hindsight_sessions`, so the horizon is a property of the sampling design rather
than of how far someone happened to scroll.

## Consequences

**Good.** The corpus can be partitioned by horizon, so an analysis that must not
use hindsight can restrict itself to the zero-horizon labels and know it has
done so. Agreement between reviewers is computable within a horizon rather than
across a mixture.

The labels remain honest about their own nature: `SUCCESSFUL_RETEST` is openly a
judgement made with five sessions of hindsight, which is what it has to be, and
the row says so.

**Costs.** Reviewers must be given a fixed window and the tooling must enforce
it; a reviewer who scrolls further has produced a label the stored horizon
misdescribes. That is a workflow requirement this ADR cannot enforce in code, and
`BREAKOUT_LABELING.md` states it as a reviewer instruction.

Two labels effectively cannot be assigned live. `SUCCESSFUL_RETEST` needs five
sessions, so a same-day review queue can only produce four of the seven values.

**Rejected.** *One global horizon for the whole corpus.* Simple, and it either
forbids the retest labels or grants five sessions of hindsight to labels that
should have none.

*Deriving the horizon from the label.* Circular: the horizon is supposed to
constrain what label is permissible, not follow from it.
