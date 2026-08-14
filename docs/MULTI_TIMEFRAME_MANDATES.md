# Multi-timeframe & portfolio mandate architecture

**Design only. Nothing implemented, no thresholds touched, no profitability
computed, no timeframe combination optimised.** `full-01` is frozen and remains
the validated **Daily** machinery baseline.

This document exists because a decision made now — what historical data to
acquire — silently assumes an answer to a question that had not been asked: *is
Daily the only timeframe TradeIt needs?* It is not, and the acquisition plan must
stop assuming it.

---

## 1. The core principle

**A security has no global state.** There is no such thing as
`AAPL.breakout == true`.

State is scoped by:

```
instrument  ×  timeframe  ×  analytical episode
```

The same security may simultaneously be, with no contradiction whatsoever:

| timeframe | state |
|---|---|
| Monthly | long-term uptrend |
| Weekly | confirmed breakout |
| Daily | breakout retest |
| 1H | bull flag forming |
| 15m | near breakout |
| 5m | short-term pullback |

These are **independent causal observations at different resolutions**, each
derived from its own bar stream, each with its own lifecycle, each carrying its
own provenance. A later coordination layer (§8) may consume them together for a
mandate that cares about several at once. It does not merge them.

### The platform already works this way, and mostly enforces it

This is less a change of direction than the removal of an assumption. Verified in
the code:

- `Bartimeframe` already enumerates `1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo`
  (`core/enums.py:56`).
- `PatternInstance.timeframe`, `Pattern.timeframe`, `BreakoutEvent`'s
  `event_key`, `ScanRun.timeframe` and `OhlcvBar.timeframe` all exist and are
  populated.
- A pattern's `identity_key` is a content hash **including the timeframe**
  (`patterns/base.py:443`), so a Daily bull flag and a 5-minute bull flag on the
  same instrument are already different identities and cannot collide.
- `MultiTimeframeScanner.scan_timeframes` keys results by timeframe and derives
  cross-timeframe relations as a *separate* step (`patterns/scanner.py:290`).
- The detector registry already declares **which timeframes each family is
  meaningful on** and the scanner *refuses* the rest rather than reporting a
  number nobody can interpret (`patterns/registry.py:112`). `cup_handle`,
  `high_tight_flag` and `base_on_base` are swing-only today.

**What is missing is not the scoping. It is the mandate layer above it, the
intraday data beneath it, and the live incomplete-bar distinction beside it.**

---

## 2. Roadmap placement

Added as a **named gate, not a renumbered phase.** The canonical numbering in
`ROADMAP.md` is authoritative and is not touched; this follows the precedent
already set by the *Empirical data access & validation gate*, which sits
unnumbered between Phase 5 and Phase 6.

```
Phase 5  ──  Empirical gate  ──  Phase 6  ──  ★ Multi-Timeframe &
             (complete)          (data)       Portfolio Mandate Architecture
                                                        │
                                              must complete before
                                                        ▼
                                         Phase 7  Opportunity scoring
                                         Phase 8  Portfolio construction
                                         Phase 9  Backtesting
```

**It may overlap Phase 6**, and in the data dimension it must: the corpora being
specified right now have to be right for every mandate, not only the swing one.
It must **complete and validate before Phase 7**, because opportunity scoring is
the first stage that would blend timeframes into a ranking — and blending them
before the population is separated is precisely the error this document exists to
prevent.

Phases 3, 4 and 5 are unaffected and are not renumbered. `full-01` remains the
Daily baseline.

---

## 3. Canonical timeframe set

**No additions to `Bartimeframe`.** The existing nine are the canonical set. What
changes is the declaration of which are *stored* and which are *derived*.

| timeframe | role | source |
|---|---|---|
| `1m` | **base — stored** | vendor, intraday corpus |
| `5m` | derived | ← `1m` |
| `15m` | derived | ← `1m` |
| `30m` | derived | ← `1m` |
| `1h` | derived | ← `1m` |
| `4h` | derived, **not adopted — see §3.2** | ← `1m` |
| `1d` | **base — stored** | vendor, EOD corpus |
| `1w` | derived | ← `1d` |
| `1mo` | derived | ← `1d` |

`analytics/timeframes.py` already implements exactly this map
(`AGGREGATION_SOURCES`), already anchors intraday buckets to the **session open**
rather than the wall clock, and already refuses to emit a bar whose period has
not closed.

### 3.1 Two independent base timeframes, and why they must not be reconciled silently

Daily is **not** derived from 1-minute, for two separate reasons:

1. **Coverage.** The EOD corpus targets 1998; the intraday corpus will not go
   back nearly that far (§6). Deriving Daily from 1m would truncate the daily
   history to the intraday history.
2. **They genuinely differ.** A vendor's official daily bar reflects the
   consolidated tape, the opening and closing auctions, and off-exchange prints.
   A day's 1-minute bars summed and folded will *not* reproduce it exactly —
   particularly volume, and particularly the close.

**Therefore:** where the two overlap, a reconciliation check compares them and
**records the disagreement as a fact**, exactly as `price_facts` records two
vendors disagreeing about a split. Neither is corrected into the other. A derived
daily bar, if ever produced, carries a different `source` than the vendor's and
is never substituted for it.

### 3.2 4-hour bars — semantics defined, adoption withheld

The semantics are already explicit in code and are *not* inherited from any chart
platform: buckets are anchored to the session open, and the final bucket of a
session is complete once the session closes even though it is short
(`analytics/timeframes.py:212`).

For a 6.5-hour US regular session that yields **two unequal bars per day**:

```
09:30 ─────────────── 13:30    bar 1   (4h 00m)
13:30 ──────── 16:00           bar 2   (2h 30m)
```

Bar 2 is a legitimate bar covering real trading time. It is **not comparable in
duration to bar 1**, which means a "4-hour range contraction" statistic mixes two
different objects, and an early-close day makes bar 2 shorter still.

**Recommendation: do not adopt `4h` in any mandate hierarchy yet.** The
implementation stays as it is; the decision is separate. Three options, to be
decided before any mandate names it:

| option | result | cost |
|---|---|---|
| **A. keep as-is** | two unequal bars/day, declared and labelled | statistics must carry a duration field; `4h` means two different things |
| **B. split the session evenly** | 2 × 3h15m — equal, comparable, self-consistent | no longer "4 hours"; incomparable to any external chart |
| **C. do not use `4h`** | mandates use `1h` and `1d` | loses nothing that `1h`+`1d` do not cover |

**C is recommended** on current evidence, with B as the fallback if a genuine
intermediate horizon proves necessary. A is the one to avoid, because it looks
like it works.

### 3.3 Regular vs extended hours — currently absent, must be explicit

`core/calendar.py` models **regular hours only**: `REGULAR_OPEN`,
`REGULAR_CLOSE`, and early closes. There is no pre-market or post-market concept
anywhere in the system.

That is a defensible default and it must become an explicit, recorded one:

```
session_segment  enum: regular | pre_market | post_market
```

Rules, to be enforced rather than assumed:

1. **The canonical intraday timeframes are regular-hours only.** Every derived
   `5m/15m/30m/1h` bar is folded from regular-session minutes.
2. **Extended-hours minutes are stored, never silently merged.** They are real
   data; a gap-up that formed at 07:40 is genuine information and discarding it
   would be a loss. They are simply not part of the same series.
3. **Every bar records which segments it consumed.** A derived bar that included
   extended-hours minutes is a different object and says so.
4. **Session-anchored bucketing depends on this.** Intraday buckets are anchored
   to `session.open_utc`; if extended hours were folded in, "the session open"
   would move and every bucket boundary with it. That is exactly the kind of
   silent semantic drift that must be impossible.

**Timestamp convention, already correct and to be preserved:** bars are stamped
in UTC (`DateTime(timezone=True)`), the calendar converts from `America/New_York`
including DST, and `session_date` is the *local trading day*. Any intraday vendor
must be normalised into this convention on ingest, with its own convention
recorded — a vendor stamping bars at the bucket **open** and one stamping at the
bucket **close** differ by the bar width, and getting that wrong shifts every
signal by one bar.

---

## 4. Portfolio mandates

Three mandates, three separate statistical populations. **A 5-minute bull flag
for the day portfolio is not the same object as a Daily bull flag for the swing
portfolio, and they must never share a sample.**

| | **Day** | **Swing** | **Retirement** |
|---|---|---|---|
| objective | intraday moves | days → ~3 months | multi-year trends, compounding |
| **context** | `1d` / `1h` | `1w` / `1d` | `1mo` / `1w` |
| **setup** | `1h` / `15m` | `1d` / `1h` | `1w` / `1d` |
| **trigger / execution** | `5m` / `1m` | `1h` / `15m` | `1d` |
| holding horizon | hours | days → ~3 months | years |
| eligible timeframes | `1m … 1d` | `15m … 1w` | `1d … 1mo` |
| primary corpus | **intraday** | **EOD** (+ intraday for entries) | **EOD** |
| survivorship posture | **biased — see §6.4** | survivorship-safe target | survivorship-safe target |

`4h` appears in none of them, per §3.2. The Retirement mandate may use `1d`
tactically, but intraday resolution does not drive it — a rule expressed as an
eligible-timeframe set rather than as guidance.

### What each mandate owns separately

Every one of these is per-mandate, never global:

```
eligible timeframe hierarchy   ·   context timeframe   ·   setup timeframe
trigger timeframe   ·   expected holding horizon   ·   risk model
stop methodology   ·   position sizing   ·   backtest   ·   KPIs
paper-trading track record   ·   capital-graduation criteria
```

**The mechanism for the first of those already exists one level down.** The
detector registry's `SUPPORTED_TIMEFRAMES` records where each family is
*meaningful*, and the scanner refuses the rest. A mandate's eligible-timeframe
set is the same idea one layer up, and should reuse the same discipline: refuse,
rather than compute-and-hope.

### The separation rule, stated so it can be checked

> **No learning, backtesting, calibration or scoring population may contain
> observations from more than one mandate, or from more than one timeframe,
> unless the combination is itself the declared unit of study.**

This is the multi-timeframe analogue of the corpus rule `full-01` already lives
under. It is checkable: every pattern row already carries `timeframe`, and every
run already carries `scan_run_id`, so a population that mixes them can be
detected rather than argued about.

---

## 5. Live incomplete-bar semantics

**The most dangerous item in this document**, because it is the one that can
corrupt historical results retroactively.

### The rule

```
historical detection      →  COMPLETE bars only, always, with no exception
live operation            →  COMPLETE and IN_PROGRESS distinguished explicitly
```

On Wednesday, a weekly bar may be trading above resistance while the weekly bar
has not closed. TradeIt must distinguish:

| | meaning |
|---|---|
| `weekly breakout in progress` | true now, may be false on Friday |
| `weekly breakout confirmed` | the weekly bar closed above; permanent |

Identically for Monthly, Daily, 1H, 15m, 5m and 1m.

**The eventual close of a higher-timeframe candle must never leak backward into
historical replay.** A weekly bar that completed on Friday did not exist on
Wednesday, and a replay that shows it on Wednesday is reading the future.

### What already exists

More than expected, and it is the right foundation:

- `AggregatedBar` carries a `complete` flag, set by the **exchange calendar**
  rather than by data presence (`analytics/timeframes.py`).
- `completed_only()` and `resample_for_feature_use()` filter incomplete bars out
  of the feature path; `TimeframeConfig.emit_incomplete_bars` defaults to
  `False`.
- `causal_series()` hands the pattern scanner finished bars only, and the module
  docstring states the invariant explicitly: *"The scanner never resamples."*
- The breakout engine already threads an `is_partial` flag through
  `BreakoutMonitor.observe` → `engine` → `volume`, and refuses to compute a
  completed relative-volume figure from a partial bar
  (`breakouts/volume.py:60`).

### What is missing

1. **`is_partial` is a caller-supplied boolean, not a property of the bar.** It
   is passed into the monitor rather than travelling with the data. For live
   multi-timeframe operation it must become part of the bar contract — a bar
   *knows* whether its period has closed — so that no call site can forget it.
2. **It is scoped to volume.** Partial-bar handling is thorough in
   `breakouts/volume.py` and absent from pattern geometry: a partial bar's high
   and low are also provisional.
3. **No state vocabulary for "in progress".** `BreakoutStatus` has no term that
   distinguishes *closed above on a live bar* from *closed above on a completed
   bar*. Today the system avoids the problem by only ever being fed completed
   bars, which is correct and is not a live design.
4. **Nothing prevents an incomplete bar being persisted.** The discipline is in
   the feed, not in the schema.

**Proposed, not implemented:**

```
bar_status   enum: COMPLETE | IN_PROGRESS
```

carried on the bar itself, with a hard rule: **`IN_PROGRESS` bars are never
persisted to the historical corpus and never enter a detection population.** Live
consumers read them from a separate live-state surface that is explicitly
non-durable. A replay of any historical window then cannot see one, by
construction rather than by care.

---

## 6. Data requirements per timeframe

### 6.1 `research-01` — the EOD corpus (unchanged)

**Target held: 1998-01-01 → present, daily, survivorship-safe, active and
delisted.** Daily bars causally derive **Weekly** and **Monthly** through
machinery that already exists and is already calendar-gated. Nothing in this
document changes the EOD plan, its start date, or the Kibot probe driving it.

This corpus serves the **Swing** and **Retirement** mandates, which is most of
the platform's intended capital.

Scale, for comparison with §6.3: ~15,000 securities × 28 years × 252 sessions ≈
**106 million daily bars**. Comfortable.

### 6.2 What Daily alone can and cannot support

**Derivable from Daily, with no intraday data at all:**

- Weekly and Monthly bars, complete-only, calendar-gated;
- every Phase 4 pattern family on `1d/1w/1mo` — including the swing-only
  families (`cup_handle`, `high_tight_flag`, `base_on_base`) that the registry
  already restricts to exactly these timeframes;
- Phase 5 breakout lifecycles on those timeframes;
- daily relative volume, ATR, range contraction, relative strength, market
  regime — the whole Phase 3 feature layer as currently built;
- the entire **Retirement** mandate;
- the **Swing** mandate's context and setup layers.

**Requires true intraday history and cannot be faked:**

- any `1m/5m/15m/30m/1h` pattern or breakout — the whole **Day** mandate;
- the Swing mandate's `1h`/`15m` **trigger** layer;
- intraday volume profile, opening-range behaviour, and the elapsed-fraction
  volume projection `breakouts/volume.py` already implements;
- gap classification with intraday follow-through;
- realistic intraday entry/stop simulation, and therefore any honest execution
  model for Day or Swing;
- the difference between "the daily bar traded through the level" and "the daily
  bar closed above it having first traded 3% below" — invisible in EOD data and
  decisive for a stop.

**A daily bar cannot be de-aggregated. There is no interpolation that recovers
the path, and any attempt is fabrication.**

### 6.3 `intraday-01` — a separate corpus with a separate contract

Defined here as a **distinct corpus**, not an extension of `research-01`. It has
a different base timeframe, a different depth, a different vendor question and —
critically — a **different survivorship posture**.

Canonical base: **1-minute, regular trading hours**, from which `5m/15m/30m/1h`
are derived by existing machinery. Extended-hours minutes stored separately (§3.3).

### 6.4 The survivorship problem is worse intraday, and must be declared

`research-01` is being built to include delisted securities because omitting them
is the defining historical-data error. **Intraday history for delisted securities
is rarely available at any price.** Vendors that keep 1-minute history for a
company that stopped trading in 2003 are the exception.

Consequently, and stated in advance rather than discovered later:

> **`intraday-01` is expected to be survivorship-biased, and must be labelled as
> such in `CORPUS_REGISTRY.md` with a measured delisted fraction.**

This is not fatal — it is a *known and quantified* limitation of the **Day**
mandate specifically, and it is exactly why the mandates must not share a
statistical population. Swing and Retirement research runs on a
survivorship-controlled corpus; Day research does not, and any KPI derived from
it carries that caveat permanently.

**Mitigation, which is why depth matters less than it seems:** TradeIt
accumulates its own 1-minute archive going forward, and that archive *is*
survivorship-safe by construction, because it records what existed on each day it
ran. The historical intraday window is a head start; the forward archive is the
asset.

**And the limit on what the declaration buys.** Declaring the bias makes results
*interpretable*; it does not make them *sufficient*. A known survivor-biased
corpus may validate machinery and explore hypotheses; **it may not, by itself,
justify live capital.** Day-mandate capital graduation must rest on some
combination of survivorship-safe forward intraday collection, independent paper
trading on the live code path, and appropriately unbiased historical evidence
where any exists — stated explicitly at graduation. "The backtest was good" over
a corpus whose failures are missing is exactly the claim this section exists to
block.

---

## 7. Intraday depth, storage and API implications

### 7.1 How much history is actually needed — reasoned, not asserted

**Sample size is not the binding constraint; regime coverage is.**

A 5-minute setup firing even twice a month per instrument across 1,500
instruments produces ~36,000 observations a year. Statistical power arrives
quickly. What does *not* arrive quickly is variety of market conditions, and a
day-trading system validated only on a trending low-volatility year has been
validated on one regime.

Working back from the regimes that must be represented:

| period | condition |
|---|---|
| 2018 Q4 | volatility shock, no recession |
| 2020 Feb–Apr | COVID crash and V-recovery, limit-down sessions |
| 2020–21 | retail melt-up, meme volatility, zero-commission microstructure |
| 2022 | sustained bear, tightening |
| 2023–2026 | AI-led narrow leadership, modern microstructure |

**Recommendation: target 2015-01-01 → present (~11 years); accept 2018-01-01 →
present (~8 years) as the minimum.** Below 2018 the corpus loses the COVID
dislocation, which is the single most informative intraday stress episode
available.

Pre-2015 intraday is explicitly **not** required. Decimalisation, Reg NMS, the
rise of HFT and the 2010 flash crash make pre-2010 microstructure a different
market at the 1-minute scale, and the honest position is that a Day mandate
validated on 2004 microstructure has been validated on something else. This is a
real and interesting research question and it is not the one being asked.

**Note the asymmetry, which is the whole point of separating the corpora:** the
EOD corpus wants 1998 *because* the market was different then — regime variety at
the daily scale is the objective. The intraday corpus wants recent data *because*
the market was different then — microstructure comparability is the objective. One
rule cannot serve both.

### 7.2 Storage — the number that changes the architecture

1-minute regular-hours bars: 390 per session × 252 sessions = **98,280 bars per
instrument-year**.

| universe | depth | rows | Postgres, current row shape | Postgres, narrow row | columnar (zstd) |
|---|---|---|---|---|---|
| 500 | 5y | 0.25 B | 61 GB | 22 GB | **3.7 GB** |
| 1,500 | 5y | 0.74 B | 184 GB | 66 GB | **11 GB** |
| 1,500 | 10y | 1.47 B | 369 GB | 133 GB | **22 GB** |
| 3,000 | 10y | 2.95 B | 737 GB | 265 GB | **44 GB** |
| 6,000 | 10y | 5.90 B | 1.47 TB | 531 GB | **89 GB** |

Assumptions, stated so they can be challenged: ~250 B/row for the current
`ohlcv_bars` shape (24 B tuple header, `NUMERIC` prices, two `String` columns,
`TimestampMixin`, plus `uq_bar_revision` and `ix_bar_pit`); ~90 B/row for a
purpose-built intraday table (integer-scaled prices, smallint enums, one
composite index); ~15 B/bar for delta-encoded columnar storage.

**Three conclusions follow, and they are architectural rather than operational:**

1. **The intraday corpus is 14–56× the entire EOD corpus in row count.** 1.47 B
   rows against 106 M. Whatever is convenient at EOD scale is not automatically
   viable here.
2. **The intraday archive should not live in `ohlcv_bars` as it stands.** The
   current row shape costs ~2.8× a purpose-built one, and the design intent of
   `uq_bar_revision` (*one bar per instrument, timeframe, session-date,
   revision*) is **wrong for intraday** — 390 one-minute bars share a
   `session_date` and are separated only by `knowledge_time`, which is
   accidental rather than designed. See §9.
3. **Universe scoping is the strongest cost lever available.** 1,500 liquid
   names for 10 years is 22 GB columnar; 6,000 names is 89 GB. Since the Day
   mandate will not trade illiquid microcaps anyway, a declared liquidity floor
   is both cheaper *and* more honest than a universe nobody intends to use.

### 7.3 API and acquisition

At a typical 5,000-bars-per-request REST limit, 1,500 instruments × 10 years is
roughly **295,000 requests**. At 800 requests/minute that is ~6 hours of
continuous pulling; at a free tier's 8/minute it is over a year.

**This is a bulk-download problem, not an API problem.** Per-symbol REST
backfill of a 1-minute archive is not a viable acquisition strategy at any
universe size worth having, which makes bulk file delivery a hard requirement of
any intraday vendor — and reframes the intraday vendor question as *"who sells
1-minute history in bulk files, with retention rights?"* rather than *"whose API
is fastest?"*

**Forward maintenance is trivial by comparison:** 1,500 instruments × 390
bars/day ≈ 585,000 bars/day ≈ 9 MB/day columnar, ~2 GB/year. The archive is
cheap to *keep*; it is expensive to *acquire*.

---

## 8. Multi-timeframe coordinator — future layer

**Its job is not detection.** Every detector already produces independent,
timeframe-scoped state. The coordinator consumes those states and answers
relational questions:

- Is the Weekly trend aligned with the Daily setup?
- Is a 1H breakout occurring inside a Daily/Weekly uptrend?
- Is a 5m bull flag a countertrend bounce against a bearish Weekly structure?
- Has the Daily broken out while Weekly remains inside its larger base?
- Are lower timeframes confirming or contradicting the setup timeframe?

### Three constraints, fixed now

1. **It must not hard-code that alignment is profitable.** Whether confluence
   matters, in which direction, for which mandate, and at what horizon is an
   empirical question for later unbiased research. The coordinator *describes*
   the relationship; it does not score it.
2. **It must not produce a monolithic combined score.** This is the same
   invariant as `EvidenceBundle`, which deliberately carries every field a
   downstream stage needs and has no aggregate — precisely so that no single
   number can hide which evidence was missing.
3. **It must be causal per timeframe.** Reading a Weekly state on Wednesday means
   reading *last completed* week, never the week in progress (§5). The existing
   `align_to_daily()` already implements exactly this mapping — each daily
   session to the most recent *completed* higher-timeframe bar — and is the
   correct primitive to build on.

**Not implemented. Not designed further here.** The reason it appears at all is
that its existence changes what data must be acquired, and that decision is being
made now.

---

## 9. Existing schemas and interfaces that will need to change

Nothing below is being changed now. This is the impact assessment, verified
against the code rather than assumed.

| # | location | issue | severity |
|---|---|---|---|
| 1 | `ohlcv_bars.uq_bar_revision` — `(instrument_id, timeframe, session_date, knowledge_time)` | **The identity of an intraday bar is its `event_time`, not its `session_date`.** 390 one-minute bars share a session date; they are currently distinguished only by `knowledge_time`, which works by accident. `session_date` should remain as a grouping attribute (it is needed for session logic and RTH/ETH), but the uniqueness must key on `event_time`. | **high** |
| 2 | `ohlcv_bars.ix_bar_pit` — `(instrument_id, timeframe, session_date, knowledge_time)` | No `event_time`. Every intraday range scan and every "last N bars" read would sort within a session date. | **high** |
| 3 | `ohlcv_bars` row width | ~250 B/row × 1.47 B rows = 369 GB where ~133 GB would do. A separate narrow intraday table, or columnar storage outside Postgres, is the decision to make before ingesting anything. | **high** |
| 4 | no `bar_status` anywhere | `COMPLETE` / `IN_PROGRESS` exists only as `AggregatedBar.complete` in memory and as a caller-supplied `is_partial` bool in the breakout monitor. Nothing at the schema level prevents persisting an incomplete bar. | **high** |
| 5 | no `session_segment` anywhere | `core/calendar.py` models regular hours only. Extended-hours data has no place to go and no way to be distinguished. | **medium** |
| 6 | `PatternObservation.session_date` is a `Date` | An intraday pattern transitions many times per day; a `Date` cannot order them. Needs an event timestamp, as `observed_at` already provides — the `session_date` column becomes insufficient as the ordering key. | **medium** |
| 7 | `Pattern.structure_start_date` / `structure_end_date` / `structure_known_through` are `Date` | The Phase 4/5 causality provenance that `full-01` validated is date-grained. Intraday structures start and end at a *time*. **The causality gate itself would need re-specifying for intraday** — it is not merely a column type. | **medium** |
| 8 | `ScanRun.start_session` / `end_session` are `Date` | Same class of issue; a run over 1-minute bars is bounded by instants. | **medium** |
| 9 | `BreakoutMonitor.observe(is_partial=...)` | Caller-supplied rather than carried by the bar. Correct behaviour depends on every call site remembering. Should become a bar property. | **medium** |
| 10 | `registry.SUPPORTED_TIMEFRAMES` | Already the right mechanism, and correctly conservative today. Will need review — not relaxation by default — when intraday families are actually studied. A swing-only family staying swing-only is a valid outcome. | **low** |
| 11 | `analytics/timeframes.AGGREGATION_SOURCES` | Correct as-is. Depends on `1m` existing as a base, which is currently aspirational. | **low** |
| 12 | `CausalFeed(timeframe=...)` | Single-timeframe by construction. A mandate needs several concurrently; the natural shape is several feeds, not a multi-timeframe feed — preserving the property that each timeframe's causality is independently auditable. | **low** |
| 13 | mandate tables | Do not exist. `portfolio_mandates`, the eligible-timeframe hierarchy, per-mandate KPIs and graduation criteria are all new. | **new** |

**The through-line in items 1, 2, 6, 7 and 8: the platform is date-grained
because Daily was the only timeframe.** That was a correct simplification and it
is now the main structural change intraday requires. It is a migration, not a
rewrite — `event_time`, `observed_at` and `knowledge_time` are already
`timestamptz` and already carry the necessary precision.

**Detectors themselves need no changes.** They consume `Sequence[OhlcvBar]` and
know nothing about calendars; `patterns/series.py` already isolates resampling on
the way in. That was a good decision and it holds.

---

## 10. What this changes about the Kibot / Phase 6 probe

**It does not derail it.** The EOD investigation continues exactly as specified:
1998-01-01, survivorship-safe, active and delisted, retention rights confirmed.
That corpus serves two of three mandates and remains the priority.

What is added is a **separate, later, non-blocking intraday probe** —
`KIBOT_DATA_PROBE.md` §H — asking whether the same vendor can also supply
1-minute history, and on what terms. Kibot advertises intraday products, so the
question is natural, but it is a **second decision**, not a condition on the
first.

**Nothing intraday is purchased, and the EOD decision does not wait for it.**

---

## 11. Stop conditions observed

Not done, and not to be done under this document:

- `full-01` untouched and still frozen — it remains the validated **Daily**
  machinery baseline, and any future timeframe expansion gets its own derivation
  runs and corpora which must independently pass the same causality and
  provenance gates Daily passed;
- no detector or lifecycle threshold changed;
- no profitability, expectancy, forward return or outcome-conditioned statistic
  computed;
- no timeframe combination optimised, and no claim that timeframe alignment is
  profitable;
- no monolithic cross-timeframe score;
- no trading;
- no multi-timeframe engine implemented.
