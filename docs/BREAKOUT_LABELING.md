# Breakout labelling protocol

Infrastructure only. No breakout event has been labelled by a human, no model is
trained on this, and none should be until humans have labelled real examples.

Extends the Phase 4 pattern-labelling framework with one addition that is not
cosmetic: **every label carries an explicit knowledge horizon**. Reasoning:
[ADR-0024](adr/0024-labels-carry-a-knowledge-horizon.md).

## The vocabulary

| Label | Means | Minimum hindsight |
| --- | --- | --- |
| `VALID_BREAKOUT` | a genuine, convincing break of a real level | 0 sessions |
| `WEAK_BREAKOUT` | a real break, but marginal — barely clear, thin, unconvincing | 0 |
| `FALSE_BREAKOUT` | price cleared the level and returned inside it | 3 |
| `SUCCESSFUL_RETEST` | price returned to the level and it held | 5 |
| `FAILED_RETEST` | price returned to the level and broke it | 5 |
| `AMBIGUOUS` | the reviewer genuinely cannot tell — data, not an absence of data | 0 |
| `INSUFFICIENT_EVIDENCE` | the example cannot be judged; retire it | 0 |

`AMBIGUOUS` and `INSUFFICIENT_EVIDENCE` are separate because they have different
consequences. An ambiguous label is a fact about a hard example and belongs in
the dataset. An unjudgeable one — bad bars, a gap in the series, a level that
makes no sense — means the *example* is broken and should be retired rather than
sent to another reviewer.

**None of these is a profitability judgement.** The two that sound closest —
`VALID_BREAKOUT` and `FALSE_BREAKOUT` — are about whether the level was genuinely
cleared and whether price held it, not about what a position would have returned.

## Reviewer instructions

1. **You are shown a fixed window.** The queue sets it; do not seek more. A
   reviewer who scrolls further has produced a label whose stored horizon
   misdescribes it, and the corpus has no way to detect that.
2. **You are never asked whether the stock made money.** There is no field for
   it and no label for it. If you find yourself reasoning about the eventual
   return, you have left the task.
3. **Judge the break, not the setup.** Whether the pattern was any good is Phase
   4's question and has its own labelling task. Here the question is what price
   did at the level.
4. **Abstain rather than guess.** `AMBIGUOUS` is a real answer. So is
   `INSUFFICIENT_EVIDENCE` when the example itself is unusable.
5. **You may change your mind.** A re-review is a new revision, not an
   overwrite. Your earlier opinion stays, because a reviewer who reverses is
   telling us the example is hard.

## What is stored

Per label: the instrument, the breakout session, the **knowledge horizon
session**, the timeframe, the reviewer, the revision, the label, the reviewer's
confidence, free-text comments — and the engine's own reading pinned at
labelling time: state, breakout quality, confirmation score, coverage, profile,
config digest and scorer version.

Pinned rather than looked up later, so agreement is computed against what the
engine said at labelling time rather than against whatever it says when the query
runs — which, after a scorer version bump, is a different engine.

`breakout_labels` has a check constraint that the horizon is not before the
labelled session, and a unique constraint on
`(instrument, session, timeframe, reviewer, revision)` so a re-review must be a
new row.

## Selecting examples

`BreakoutReviewQueue` offers six strategies. The default is `FUTURE_BLIND` and
the others exist to be combined with it, never to replace it.

| Strategy | Selects on |
| --- | --- |
| `FUTURE_BLIND` | the breakout session's own reading, stratified by quality band |
| `RANDOM` | uniform, seeded — the unbiased baseline |
| `STRATIFIED_QUALITY` | quality bands, so the corpus is not all high scorers |
| `STRATIFIED_STATE` | terminal states |
| `DISAGREEMENT` | score and outcome disagree — most informative, most biased |
| `CONTESTED` | reviewers disagreed on a previous pass |

**`FUTURE_BLIND` is deliberately not stratified by outcome state.** Balancing the
corpus across confirmed and failed events would use the outcome to decide which
examples a human sees, which is item 43's forbidden bias wearing a respectable
hat. `DISAGREEMENT` and `STRATIFIED_STATE` *do* use the outcome, which is why
they must be used alongside a blind sample rather than instead of one — and why
each queue item records the strategy that selected it, so an analysis can
partition on it.

The queue, not the reviewer, sets `hindsight_sessions`. The horizon is a property
of the sampling design.

## Agreement

`BreakoutLabelService.agreement()` reports examples, multiply-reviewed examples,
unanimous, contested and abstentions, over **latest revisions only**. Counting
every revision would let one indecisive reviewer's three opinions look like three
reviewers disagreeing.

Inter-reviewer agreement is what turns a set of opinions into a measurement, and
it is the number that decides whether the labelled corpus is worth anything. Two
reviewers agreeing 55% of the time on `VALID` versus `WEAK` would mean the
distinction is not operationalisable, and that is a finding worth having before a
model is trained on it.

## The real-market protocol

**Execute once real historical data can be ingested.** Real-market access is
still blocked, so what follows is a plan, not a result.

### Sampling frame (item 49)

Stratify across, and record the stratum on every example:

| Dimension | Strata |
| --- | --- |
| Market regime | bull trending, bull choppy, neutral, bear choppy, bear trending |
| Volatility regime | low, normal, high |
| Event context | earnings gap, no earnings nearby, unknown |
| Relative strength | high RS, low RS |
| Sector | leaders, laggards, across all sectors present |
| Market cap | micro, small, mid, large |
| Breakout character | textbook, messy, failure, whipsaw, retest, multiple attempts |

Minimum 40 examples per regime × character cell, which puts the target in the
low thousands. Two independent reviewers on every example, three on any example
where the first two disagree.

### Sampling rules

- **Draw the frame before looking at outcomes.** Select instrument-sessions by
  the engine's own state and frozen quality, never by what price did afterwards.
- **Do not select positive examples because they later rallied.** The corpus must
  contain breakouts that worked and breakouts that did not, in whatever
  proportion the frame produces.
- **Fix the hindsight window per label class** and record it. A `FALSE_BREAKOUT`
  label needs a window; a `VALID_BREAKOUT` label must not be given one.
- **Include the delisted and the acquired.** Sampling today's active universe
  reintroduces survivorship bias at the labelling stage, after the storage layer
  spent Phase 1 preventing it.
- **Blind the reviewer to the engine's score** where the workflow allows.
  Agreement measured against a score the reviewer could see is agreement with the
  score.

### What the labelled corpus is for

Measuring whether the engine's structural judgements match a competent human's —
nothing else. It is not a training set for a profitability model, it is not a
backtest, and it cannot become either without a separate outcome dataset with its
own knowledge horizon.

### What it cannot settle

Whether confirmed breakouts are profitable. That needs forward returns, a
walk-forward harness and out-of-sample discipline, all of which are Phase 9's.
