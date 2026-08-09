# Breakout architecture

Phase 5. One question, asked once per session per live boundary:

> **What is price doing relative to an established structural boundary, and how
> convincing is that behaviour?**

And one question it does not ask, cannot ask, and has nowhere to record an
answer to:

> Should we buy it?

## Where this sits

    PATTERN DETECTION        Phase 4 — what structural setup exists?
      → BREAKOUT DETECTION   Phase 5 — is the boundary being challenged?
      → BREAKOUT CONFIRMATION Phase 5 — how convincing is that?
      → FUNDAMENTALS         Phase 6
      → OPPORTUNITY SCORING  Phase 7
      → PORTFOLIO FIT        Phase 7
      → RISK / POSITION SIZE Phase 8
      → EXECUTION            Phase 10+

The separation is structural rather than a matter of discipline.
`tradeit.breakouts` imports from `tradeit.patterns`, `tradeit.analytics` and
`tradeit.core`, and from nothing downstream. `BreakoutEvent` has no field for a
size, a stop, a target or a direction, and the Phase 2 placeholder that carried
an `is_tradable` property was removed rather than left alongside — see the note
in `tradeit/strategy/base.py`.

## The modules

| Module | Answers |
| --- | --- |
| `config` | every number the engine uses, and the reasoning for each default |
| `lifecycle` | which state transitions exist, and which are forbidden |
| `boundary` | where the level is, how wide the zone around it is |
| `measures` | approach, penetration, candle shape, acceptance, follow-through, rejection |
| `volume` | relative volume, expansion, and the partial-session problem |
| `context` | market regime, sector, relative strength, earnings proximity |
| `retest` | whether a return to the level is a test or a break |
| `scoring` | the four numbers and how they combine |
| `profiles` | the three confirmation paths and the policies that gate them |
| `engine` | one event, advanced one session at a time |
| `monitor` | which boundaries are worth watching, and attempt numbering |
| `persistence` | append-only storage |
| `labeling` | human structural labels with explicit knowledge horizons |
| `synthetic` | the controlled and adversarial corpora |
| `validation` | the characterisation harness |
| `perturbation` | stability under perturbed inputs |

## The event

A **breakout event** is one *attempt* at one boundary. Not one pattern's whole
relationship with its level, and not one session — one attempt, with a stable
identity and an append-only observation history.

```
event_key = hash(instrument_id, timeframe, pattern_identity, attempt_number)
```

`attempt_number` is in the key deliberately. A pattern with three goes at the
same level produces three records, none overwriting another, which is what makes
item 26's attempt history possible. See [ADR-0020](adr/0020-rejection-resolves-the-attempt.md).

Each event carries:

- the **frozen boundary** it is judged against — level, slope, anchor, touch
  count, confidence, tolerance, and the ATR current when the event opened
  ([ADR-0022](adr/0022-the-boundary-is-snapshotted-at-open.md));
- **three timestamps** kept apart because they are frequently three different
  days: first approach, first penetration, first qualifying close;
- **four scores** — see below;
- **provenance** across both layers: the pattern detector's name and version,
  the pattern config digest, the breakout config digest, the scorer version, the
  data snapshot digest, and the confirmation profile;
- an **append-only observation log**, one row per session, carrying every
  measurement taken that day.

## Four numbers

Collapsing any pair destroys a distinction that cannot be recovered downstream.

| Number | Answers | Moves? |
| --- | --- | --- |
| `breakout_quality` | how favourable were the observed breakout characteristics? | **No.** Frozen at the breakout bar. |
| `confirmation_score` | how much subsequent evidence corroborates it? | Yes, each session. |
| `evidence_coverage` | how much of the intended evidence was available? | Yes, as inputs appear. |
| `confidence` | how sure are we this is the structural event we think it is? | Yes. |

Quality 94 / confirmation 42 is a strong breakout that has not yet proved
anything. Quality 94 / confirmation 42 / coverage 55 is the same thing with half
the evidence missing. Quality 94 / confidence 47 is a strong-looking break of a
level nobody's detector claimed. A single blended 68 is none of them.

The freeze is the load-bearing decision:
[ADR-0021](adr/0021-breakout-quality-is-frozen.md).

## The causal contract

Everything the engine does is a function of *(the event as it stood yesterday,
the bars up to and including today)*. There is no argument through which a
future bar could arrive.

`SessionInputs` enforces the boundary at the door: the last bar must be the
evaluation session, and no bar may postdate it. The engine refuses a second
observation for a session that already has one, because a history with a
duplicated session cannot be replayed against the prefix that produced it.

The incremental path is not an optimisation of a batch path — it **is** the
path, and full replay is defined as calling it repeatedly. That is why the
prefix-consistency sweep in `test_breakout_causality.py` can compare payloads
for equality rather than within a tolerance.

Three orderings inside `advance` are deliberate:

1. **Failure before confirmation.** An event that closed decisively back inside
   the pattern today does not confirm on yesterday's follow-through.
2. **Confirmation before expiration.** An event that satisfies its profile on
   the last session of its window confirmed; it did not expire.
3. **The retest engine before the generic failure rules.** Deciding whether a
   return to the level is a test or a break is what the retest engine is for; a
   generic "two closes back inside" rule evaluated first would pre-empt that
   judgement and fail every retest that dipped for two sessions.

## What the monitor decides

The engine evaluates one event. The monitor decides which events should exist.

**Active-pattern filtering.** Only patterns in `MATURE`, `NEAR_BREAKOUT` or
`BROKEN_OUT_UNCONFIRMED` carry a boundary worth watching, and only within
`max_pattern_staleness` sessions of their last observation. `FORMING` is
excluded: its boundary is still resolving, so a "breakout" of it is a breakout
of a guess. The runtime saving is real; the correctness argument is larger — a
long-expired structure's resistance is not a level anyone trades against, so
scoring a cross of it manufactures an event rather than finding one.

**Attempt numbering.** Existing events are advanced *before* new ones are
considered, so an attempt resolving today cannot also open its own successor on
the same session. One bar doing two contradictory things is not a thing.

**Skips are recorded.** "No breakout was found" and "the boundary was never
watched" are different facts, and a dataset that cannot separate them cannot
support any statement about how often breakouts occur.

## Multi-timeframe

Breakouts are timeframe-specific. A daily bull-flag breakout and a weekly VCP
breakout are different events with different boundaries and different clocks, and
the identity hash includes the timeframe so they never merge.

The engine never resamples. Each timeframe's bars are supplied by the caller,
already aggregated under the same clock through `tradeit.patterns.series` — a
higher-timeframe candle built from bars the clock has not released is the leak
the whole timeframe module exists to prevent. Mid-week weekly evaluation sees the
weeks that have finished and no partial one.

Whether the two timeframes agree is a later phase's question. Phase 5 records
both truths.

## What is deliberately absent

- No trade, entry, exit, stop or target — no field, no enum member, no column.
- No fundamentals.
- No ranking of events against each other.
- No regime veto. A breakout in a bear market is stored as a breakout in a bear
  market, weighted at 6% of a *characterisation* score. Whether that
  disqualifies a setup is Phase 7's judgement to make with the evidence Phase 5
  hands it.

## Reading further

- [`BREAKOUT_LIFECYCLE.md`](BREAKOUT_LIFECYCLE.md) — the state machine, failure and expiry
- [`BREAKOUT_SCORING.md`](BREAKOUT_SCORING.md) — the four numbers in detail
- [`BREAKOUT_VOLUME.md`](BREAKOUT_VOLUME.md) — volume and the partial-session problem
- [`BREAKOUT_RETEST.md`](BREAKOUT_RETEST.md) — retest methodology
- [`BREAKOUT_LABELING.md`](BREAKOUT_LABELING.md) — the human labelling protocol
- [`BREAKOUT_VALIDATION.md`](BREAKOUT_VALIDATION.md) — generated characterisation
- [`BREAKOUT_PERFORMANCE.md`](BREAKOUT_PERFORMANCE.md) — measured costs
- [`PHASE_05.md`](PHASE_05.md) — the phase report
