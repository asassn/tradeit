# Phase 5 — Breakout detection and confirmation

**Status: complete.** Real-market empirical validation remains **explicitly
open**, exactly as it was at the end of Phase 4. Nothing in this phase has been
measured against a price series the platform did not generate, and no number in
it should be read as a statement about the market.

Phase 4 answered *what structural setup exists?* Phase 5 answers *what is price
doing relative to that setup's boundary, and how convincing is that behaviour?*
It does not answer *should we buy it?* — there is no field, enum member or
column in which that answer could be recorded, and the Phase 2 placeholder that
carried an `is_tradable` property was removed rather than left alongside.

---

## 1. Breakout architecture

`src/tradeit/breakouts/`, sixteen modules, documented in
[`BREAKOUT_ARCHITECTURE.md`](BREAKOUT_ARCHITECTURE.md). Imports run to
`tradeit.patterns`, `tradeit.analytics` and `tradeit.core` and to nothing
downstream.

Everything the engine does is a function of *(the event as it stood yesterday,
the bars up to and including today)*. That signature is the causality
architecture: there is no argument through which a future bar could arrive, and
the incremental path is not an optimisation of a batch path — it **is** the
path, with full replay defined as calling it repeatedly.

## 2. Breakout event identity model

A breakout event is one **attempt** at one boundary:

    event_key = hash(instrument_id, timeframe, pattern_identity, attempt_number)

`attempt_number` is in the key deliberately, so a pattern with three goes at a
level produces three records rather than one that overwrites itself. The event
carries the frozen boundary, the three timestamps item 1 asks for (first
approach, first penetration, first qualifying close), four scores, full
provenance across both layers, and an append-only observation log.

Unattached boundaries — a level not claimed by any detector — are permitted and
**marked**, because a breakout of a level nobody's detector claimed is a weaker
object and the dataset must be able to separate the two populations.

## 3. Breakout lifecycle / state machine

Thirteen states, constrained transitions, documented in
[`BREAKOUT_LIFECYCLE.md`](BREAKOUT_LIFECYCLE.md). Four decisions worth
defending, each defended there and in the ADRs:

- **`REJECTED` closes the attempt, not the setup.** The next try is a new event
  with `attempt_number + 1`, so attempts stay countable
  ([ADR-0020](adr/0020-rejection-resolves-the-attempt.md)).
- **`CONFIRMED` is not terminal.** A confirmed breakout can subsequently fail,
  and recording it otherwise would produce a confirmed population that erased
  its own failures.
- **`FAILED_BREAKOUT` and `EXPIRED` are both absorbing and different.** Merging
  them would inflate every failure rate with non-events.
- **A pullback that never reached the level was not a retest.** It returns to the
  confirmation track rather than being credited with a test that never happened.

Pattern invalidation resolves by where the event had got to: past
`CLOSED_ABOVE` it is `FAILED_BREAKOUT`, before it `EXPIRED`, both with reason
`PATTERN_INVALIDATED`, and the event stays attached to its pattern.

## 4. Boundary and tolerance methodology

Three prices, not one: **nominal**, a **zone** around it, and a **threshold** at
the top of the zone that a qualifying close must exceed. Tolerance is the larger
of a 15 bp floor and 0.10 × ATR, widened up to 1.6× for a low-confidence
boundary and capped at 2%.

The boundary is **snapshotted when the event opens** and never replaced,
including the ATR it was measured against
([ADR-0022](adr/0022-the-boundary-is-snapshotted-at-open.md)). A support line is
refused outright: measuring penetration above support yields individually
plausible numbers about nothing.

## 5. Initial breakout methodology

Four distinct events, separately reported because a bar can be several at once:
`touched`, `penetrated_intraday`, `closed_above`, `gapped_above`. The brief's own
example — high 0.3% above resistance closing below — is a named test.

Penetration is measured from the **threshold**, so clearing the ambiguity zone is
worth zero. `one_tick_penetration` never produces a qualifying close at any seed,
which is what the zone is for.

## 6. Breakout candle methodology

`close_location` (0.45), body fraction (0.25), upper wick (0.15), range
expansion (0.15). Close location carries most of the weight for a mechanical
reason: a bar that traded up through a level and closed at its low leaves every
buyer above the level underwater at the close.

A zero-range bar is handled explicitly rather than dividing by zero.

## 7. Volume confirmation methodology

[`BREAKOUT_VOLUME.md`](BREAKOUT_VOLUME.md). No universal threshold: the figure
that scores 100 is per family, ranging 1.4× (breakout retest) to 2.2× (high tight
flag), and families absent from the map use the shared 1.8×. Three outputs kept
separate because they can disagree. The averaging window excludes the bar being
measured.

## 8. Intraday volume methodology

Four quantities kept distinct all the way to the consumer:
`CURRENT_OBSERVED_VOLUME`, `TIME_NORMALIZED_VOLUME`, `PROJECTED_VOLUME`,
`COMPLETED_BAR_RELATIVE_VOLUME`. **A partial bar has no completed relative
volume** — the field is `None`, full stop, and a caller wanting an in-progress
figure must ask for the projection by name.

The time-of-day curve is built from **prior complete sessions only**, and any
score built on a projection records that fact and carries it as contradicting
evidence. Below 8% of the session elapsed, no projection is emitted, with the
reason stated.

## 9. Close confirmation methodology

A qualifying close exceeds the threshold, not the nominal level. `closes_above`
and `consecutive_closes` are tracked separately: "five of the last eight closed
above" and "the last five did" are different facts and only the second is
acceptance. Weekly evaluation mid-week sees the weeks that have finished and no
partial one.

## 10. Multi-candle methodology

Confirmation windows are configurable `(1, 2, 3, 5)` with **no privileged
value**. Profiles pick from them, and Phase 9 may find the choice matters more
than any threshold here.

The window is a **floor on elapsed time**, not on the score: a profile asking for
three bars cannot confirm on day one however good the first bar looked, because
the evidence it wants has not had time to exist.

## 11. Follow-through methodology

Progress (0.40), adverse excursion (0.25), range expansion (0.15), volume hold
(0.20), measured over bars **strictly after** the breakout bar. An empty window
returns `available=False` rather than zero — a breakout that has not had time to
follow through is not the same as one that has had time and failed to.

## 12. Retest methodology

[`BREAKOUT_RETEST.md`](BREAKOUT_RETEST.md). Evaluated against the frozen
original level; a level refitted through the pullback low would make every retest
hold. Three states meaning three different things. Recovery closes counted from
the low onward.

## 13. Rejection methodology

A rejection is a statement about **one bar**: price went through and came back.
Requires a penetration, a close below the level, and either a large upper wick or
a low close location. Heavier volume makes a rejection *more* significant — being
turned back on heavy volume means size was willing to sell there.

Kept apart from failure so a single wicky day cannot terminate an event the next
day resolves cleanly.

## 14. Failed-breakout methodology

Two unconditional rules (decisive close 1.2 ATR below the level; adverse
excursion 2.5 ATR from the breakout close) and two structural ones that apply
only when no retest is running (two consecutive closes below the zone; breaking
the breakout bar's low while closing below the zone).

The config **validates** that `max_undercut_atr < decisive_close_atr`, so an
ordinary retest cannot trip the failure rule it is supposed to survive.

## 15. Expiration methodology

Five clocks, one of them relative to the pattern's own length so a three-week
flag and a nine-month cup are not given the same window. Expiration never
overrides a failure or a confirmation reached on the same session. A confirmed
event that runs out its window ends `EXPIRED` with reason `RESOLVED` — neither a
success nor a failure.

## 16. Breakout Quality Score

Seven components, two required, weights stated in
[`BREAKOUT_SCORING.md`](BREAKOUT_SCORING.md). Computed once on the session of the
first qualifying close and **frozen**
([ADR-0021](adr/0021-breakout-quality-is-frozen.md)).

Extension is folded into the penetration component rather than weighted
separately: it is not an independent dimension, it is the other end of the same
measurement, and giving it its own weight would count one fact twice. Both raw
scores survive into the measurements.

## 17. Confirmation Score

Five components over post-breakout evidence only. Nothing about the breakout bar
appears in it, and that absence is load-bearing: folding the bar's quality back
in would correlate the two scores by construction.

## 18. Evidence coverage model

Separate from quality and never multiplied in, per
[ADR-0017](adr/0017-quality-and-coverage-are-separate.md). Every unavailable
component carries a mandatory reason. **Known characteristic:** an event that
never retests has `retest_quality` permanently unavailable, so a momentum
breakout cannot reach 100% confirmation coverage. The ceiling is
family-dependent and coverage should be read against comparable events.

## 19. Confirmation profiles

Three paths (momentum, retest, acceptance), three profiles (conservative,
balanced, aggressive), first match wins, path recorded on the event
([ADR-0023](adr/0023-confirmation-profiles-describe-evidence.md)). The names
describe evidence requirements and carry no risk field — asserted by test rather
than trusted to the reader.

Measured at n=200 across the corpus:

| Profile | Controlled confirmed | Adversarial confirmed |
| --- | --- | --- |
| conservative | 67% | 25% |
| balanced | 74% | 33% |
| aggressive | 86% | 45% |

That gap is what the names claim, published rather than asserted.

## 20. Gap-breakout methodology

Five ATR bands (`NONE` / `SMALL` / `MODERATE` / `LARGE` / `EXTREME`), classified
from the opening gap against the ATR frozen at event open. Deliberately coarse:
a finer scale would imply a precision the distinction does not have.

Item 24 asks that a large gap be neither automatically good nor automatically
bad, and it is not: `extreme_extension` scores penetration above 80 and extension
below 40, and both figures reach the consumer.

## 21. Relative strength and context methodology

RS is price versus a benchmark and is **not RSI**; the four facts item 23 asks
for are separate booleans rather than a rating, because they occur in informative
combinations.

Regime and sector are **context, never a veto**. A breakout in a bear market is
stored as a breakout in a bear market, weighted at 6% of a characterisation
score. Sector readings carry their source (`CLASSIFICATION` / `ETF_PROXY` /
`UNKNOWN`) so an ETF proxy is not averaged with a membership-weighted breadth
figure. Missing inputs lower coverage and never quality.

Earnings proximity is recorded as one of four values, and
`UNKNOWN_EVENT_CONTEXT` is **not** a synonym for `NO_EARNINGS_NEARBY` — a dataset
built without a calendar must not be readable as one where the answer was no.

## 22. Persistence model

Four tables, replacing the Phase 2 sketch outright (migration
`0006_phase5_breakouts`): `breakout_events` (one row per attempt, holding current
state and the frozen boundary), `breakout_observations` (append-only, one row per
session), `breakout_relationships` (five edge types), `breakout_labels`.

The observation log is append-only in the strong sense: the unique constraint on
`(event_id, session_date)` makes a second write a conflict, and the repository
skips rather than replaces. No delete path for terminal events — failed breakouts
are the evidence a false-breakout rate is computed from.

The migration was verified against the ORM metadata: `compare_metadata` reports
**zero** differences on the breakout tables.

## 23. Multi-timeframe support

Timeframe is part of the event identity, so a daily bull-flag breakout and a
weekly VCP breakout are different events and both truths coexist. The engine
never resamples; each timeframe's bars arrive already aggregated under the same
clock through `tradeit.patterns.series`.

## 24. Human labelling support

[`BREAKOUT_LABELING.md`](BREAKOUT_LABELING.md). Seven structural labels, none of
them a profitability judgement, with **per-label knowledge horizons** enforced at
construction and by a database check constraint
([ADR-0024](adr/0024-labels-carry-a-knowledge-horizon.md)). Six queue strategies,
default `FUTURE_BLIND`, deliberately not stratified by outcome.

Infrastructure only. Nothing has been labelled.

## 25. Synthetic validation corpus

Seventeen controlled scenarios, every one a parameterisation of a single builder
so a difference in output is attributable to the field that changed. Clean, weak,
low-volume, high-volume, wick, gap, extreme extension, immediate rejection,
strong follow-through, successful retest, failed retest, false breakout, delayed,
none, high volatility, low volatility, multiple attempts.

## 26. Adversarial corpus

Thirteen: random level crosses, noise around the level, broad volatile range,
repeated whipsaw, low-volume drift, liquidity spike, one-tick penetration,
intraday break closing below, gap-and-fade, poor-confidence boundary, no prior
resistance, already-invalidated pattern, future-data trap.

Two of them are adversarial in the **caller's inputs** rather than in the price
series — a deliberately weak boundary, and a pattern the caller declares
invalidated — and the harness handles both by directive so the report and the
tests agree about what the scenario is.

## 27. Causality tests

`tests/unit/test_breakout_causality.py`, 34 tests, one class per property:

1. future candles do not alter previous breakout states;
2. future volume does not improve historical breakout quality;
3. future closes cannot provide earlier confirmation;
4. future retests cannot change the original breakout score;
5. resistance stays tied to the causal pattern boundary;
6. weekly confirmation cannot see future days of the week;
7. intraday relative volume cannot see later session volume;
8. future sector and regime data cannot leak backward;
9. pattern re-identification does not attach a breakout to the wrong pattern.

Properties 2, 4 and 5 are true **by construction** — the quality freeze and the
boundary snapshot — and are tested anyway, because a guarantee resting on an
architectural decision needs a test that fails if someone changes the
architecture.

## 28. Prefix-consistency tests

Item 32 asks that evaluating through time *k* reproduce exactly what evaluating
the full series says about time *k*. The sweep runs every cut point from the end
of warm-up to the end of the series across three scenarios and compares
**observation payloads for equality**, not within a tolerance. One cut point can
pass by luck; sweeping the range makes an off-by-one in a lookback visible.

The same property is checked at the storage boundary in
`test_breakout_persistence.py`.

## 29. Perturbation results

Eight perturbation kinds across four scenarios, 5 trials per magnitude
(`BREAKOUT_VALIDATION.md`). Noise perturbations (price, volume, timing) move the
clean breakout by a **median of 0.06–0.17 points**. Definitional perturbations
(level, ATR period, gap, close location, retest depth) move it more, as they
should, and every large move is structurally explained.

**One unexplained large jump across the whole table**, and it is worth stating
precisely rather than rounding away. `weak_breakout` under 10% volume noise:
median 1.53, p90 4.39, one draw at 10.10. The scenario sits on the steep part of
the relative-volume ramp by construction — that is what makes it the weak case —
and the response is **proportional** (0.70 → 1.41 → 1.53 → 1.53 as magnitude
rises), so it is a slope with a tail draw rather than a cliff. Raising the
10-point jump threshold would have made the number zero; it was not raised.

Monotonicity is asserted only where the direction is **definitional**: quality
rises with breakout volume and with close location, the extension term falls as
the close moves further above the level, confidence rises with boundary touches.
It is deliberately *not* asserted for gap size, because a larger gap raises
penetration and lowers extension at once and the composite has no direction the
definition commits to.

## 30. Performance benchmarks

[`BREAKOUT_PERFORMANCE.md`](BREAKOUT_PERFORMANCE.md). One session across 4,000
instruments with three live patterns each projects to **2.4 seconds** at
~0.60 ms/instrument, linear in instruments (asserted). Per-session cost is **flat
in history length** (0.255 / 0.240 / 0.233 ms at 60 / 90 / 120 bars), which is
item 44's requirement measured rather than claimed. 3.2 KB per event in memory;
order 20,000 event rows and under a million observation rows per year.

## 31. Known limitations

**The engine cannot tell a level from a line.** `low_volume_drift` confirms at
96% and `no_prior_resistance` at 92%. Handed a level, the engine faithfully
reports that price closed above it; deciding whether the level is a *structure*
is Phase 4's job and whether it is still live is the monitor's filter. Two things
do discriminate and both are visible: median quality 49 and 55 against 93 for a
clean breakout, and confidence falls sharply for an unattached or single-touch
boundary. **A consumer reading state alone will be misled; one reading the four
numbers will not** — and that is a real risk to flag to Phase 7.

**Random walks confirm.** 47–51% of `random_level_crosses`, `noise_around_level`
and `liquidity_spike` reach `CONFIRMED` at median quality around 51. Reported,
not tuned away. Separation at quality 70 across the corpus is **0.592**.

**Momentum coverage has a ceiling.** An event that never retests cannot reach
100% confirmation coverage, so coverage figures are comparable within a shape and
not across shapes.

**The acceptance path is the loosest.** A slow drift above an arbitrary level
satisfies it. That is the mechanism behind the two weaknesses above.

**Rejection detection is bar-shape dependent.** It requires a large upper wick or
a low close location; a bar that penetrates and closes flat in the middle of its
range is classified `INTRADAY_BREAK`, not `REJECTED`. Real intraday data will
have shapes the synthetic generator does not produce.

**The synthetic calendar has no holidays.** Weekday sessions only. Nothing in the
engine reads the date except for ordering and identity, so this is sufficient —
but the weekly-aggregation path has not been exercised against a real exchange
calendar with half-days.

**No concurrency story.** The monitor is single-threaded and holds events in a
dict.

## 32. Open real-data requirements

Unchanged in kind from Phase 4 and now larger in scope:

1. **No real-market breakout accuracy has been measured**, and none is claimed.
2. **No relative-volume threshold is empirically optimal.** The per-family
   figures are structural arguments, not fitted values.
3. **No claim that confirmed breakouts are profitable.** No forward return has
   been computed anywhere in this phase, and there is no return series in the
   process to compute one from.
4. The real-market labelling protocol in
   [`BREAKOUT_LABELING.md`](BREAKOUT_LABELING.md) is a **plan awaiting
   execution**, including its stratification frame and its sampling rules.
5. Rejection, retest depth and gap behaviour on real intraday data are
   unvalidated.

## 33. ADRs and migrations

| Document | Decision |
| --- | --- |
| [ADR-0020](adr/0020-rejection-resolves-the-attempt.md) | a rejected breakout resolves the attempt; the next try is a new event |
| [ADR-0021](adr/0021-breakout-quality-is-frozen.md) | breakout quality is computed once and frozen; confirmation is not |
| [ADR-0022](adr/0022-the-boundary-is-snapshotted-at-open.md) | the breakout boundary is snapshotted when the event opens |
| [ADR-0023](adr/0023-confirmation-profiles-describe-evidence.md) | profiles describe evidence, not risk — and there are three paths |
| [ADR-0024](adr/0024-labels-carry-a-knowledge-horizon.md) | every breakout label carries an explicit knowledge horizon |

Migration `0006_phase5_breakouts` replaces the Phase 2 `breakout_events` sketch
and adds `breakout_observations`, `breakout_relationships` and
`breakout_labels`. The old table is dropped rather than migrated: nothing had
written to it, and carrying a column set no code reads is how a schema
accumulates fossils. The downgrade path restores the sketch.

## 34. Defects found and corrected during the phase

Three, all found by the harness rather than by review, and all worth recording
because each is a class of mistake rather than a typo.

**Adverse excursion counted the breakout bar's own low.** A wide-range breakout
bar has a low well below its close, so seeding `lowest_low_since_breakout` with
it charged the event for the very bar that made it. Every decisive breakout
failed itself on the next session whatever price did — `low_volatility_breakout`
was failing 83% of the time on rising prices. Fixed by starting the accumulator
at the first post-breakout bar; now a named test.

**Volume persistence divided by the breakout bar's volume.** The obvious
implementation, and perverse: it rewards a low-volume breakout for having had
little volume to fall from. Caught because `low_volume_breakout` was scoring a
*higher* confirmation than `clean_breakout`. Now measured against the
pre-breakout baseline.

**The generic failure rules pre-empted the retest engine.** A "two consecutive
closes below the zone" rule fired on a 0.2-ATR, two-session dip that the retest
tolerance explicitly permits, failing `successful_retest` 50% of the time. Fixed
by running the retest engine first and suspending the structural failure rules
while a retest is active; the unconditional rules, whose thresholds sit above the
retest tolerances by validated construction, still apply.

A fourth issue was diagnosed and *not* changed: the momentum path was reading
today's relative volume rather than the breakout bar's. That one was a genuine
logic error and was fixed; it is listed here separately because it is the only
one that would have let a later, unrelated busy session satisfy a requirement
about the breakout.

## 35. Recommendation for Phase 6

Phase 6 (fundamentals) is the natural next step and **nothing in Phase 5 blocks
it** — the two do not interact until Phase 7 combines them.

Three things are worth doing first or alongside, in this order:

1. **Unblock real-market data.** This is now the binding constraint on the whole
   platform. Two phases have produced characterisation on synthetic corpora and
   the marginal value of a third is low. Everything in `BREAKOUT_VALIDATION.md`
   is provisional until a real series has been through the engine, and the
   labelling protocol cannot start without one.
2. **Run the real-market labelling protocol for Phase 4 patterns and Phase 5
   breakouts together.** They share reviewers and a review surface, and the two
   corpora answer adjacent questions about the same instrument-sessions. Doing
   them separately doubles the reviewer cost for no gain.
3. **Carry the "level versus line" weakness into Phase 7's design.** The finding
   above is the single most important thing Phase 5 learned about itself: state
   alone is misleading and the four numbers together are not. Opportunity scoring
   should consume `confidence` and `evidence_coverage`, not just
   `breakout_quality` and `state`, and designing it otherwise would reintroduce
   the weakness at a layer where it is harder to see.

**Not recommended yet:** tuning any threshold in `BreakoutEngineConfig`. There is
still nothing to tune against, and Phase 9 has the harness for it.

---

## Verification

- 1,706 tests passing, 2 skipped (PostgreSQL integration, no DSN configured).
- `ruff check` and `ruff format` clean across `scripts`, `src` and `tests`.
- `mypy` strict: no issues in 108 source files.
- Migration `0006_phase5_breakouts` applies cleanly and matches the ORM metadata
  exactly.
- `docs/BREAKOUT_VALIDATION.md` regenerated at 200 trials per scenario under
  config digest `4e7f3568e0d8d28760ef0a6951aa5b23`, scorer version 1.
