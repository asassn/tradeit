# The pattern program — every setup, its probabilities, one at a time

Opened 2026-09-23 at the owner's direction. This is the tracking document: what
is on the list, what has been measured, what it said. **It is updated after
every item.**

---

## How this is run, and why it is in two stages

A program that tests fifty setups against one corpus will find five that look
significant by chance. The ledger exists for exactly that, and at 142 trials the
hurdle is already |t| > 2.65. Testing fifty more setups the usual way would push
it past 2.9 and make every result harder to establish than the last.

So the work splits, and the split is the whole method:

**Stage A — the atlas. Descriptive, unlimited, spends no trials.**
For each setup: how often it occurs, and the *conditional probabilities* a
trader would live — reaching +1R before the stop, still up at 5/10/21/63/126
sessions, the median and the distribution — **broken down by market regime and
by horizon**, each beside a matched placebo. No hypothesis is tested; nothing
is claimed; no trial is charged. An atlas entry is a fact about what happened,
not a claim that it will happen again.

**Stage B — registered tests. One per setup that the atlas flags.**
Only setups whose atlas entry shows a *placebo-adjusted* edge worth the trial
proceed. Each gets its own pre-registration, out-of-sample window and stop rule,
exactly as §35 through §51 did.

**The rule that keeps Stage A honest:** the atlas is computed on **2010–2019**.
**2020–2025 is never read in Stage A.** A setup promoted to Stage B is tested
there, and that window is spent once. An atlas number is not evidence and may
never be quoted as one.

### Three design faults in the work so far, which this program fixes

Raised by the owner 2026-09-23 and accepted:

1. **Regime was never a condition.** Every pattern probability so far is pooled
   over a decade that mostly rose. §49 tested regime as a *timing gate*; nobody
   asked whether a setup behaves differently inside an uptrend, a downtrend or a
   flat market. **Every atlas entry is broken down by regime.**
   *Widened 2026-09-24* — trend was still only one reading, so the atlas now
   tags every session **twice**: trend (index against its 200-session average)
   and **volatility** (its trailing 21-session deviation against its own median
   over the year before). The two disagree often enough to matter: this
   corpus's downtrends are **89% turbulent** but its uptrends are **69%
   quiet**, and the 686 sessions where the readings part are the only ones that
   can separate *"this setup needs a rising market"* from *"this setup needs a
   calm one."*
2. **One horizon, one timeframe.** Everything used 63 sessions on daily bars.
   **The atlas reports 5, 10, 21, 63 and 126 sessions**, and weekly-bar variants
   where the detector supports them.
3. **Re-break censoring.** §44 and §51 take a pattern's *first* breakout and
   score a fixed window. A trader who sees a failed breakout and waits for the
   next one is living a different experiment. **The atlas scores both**: the
   first attempt, and the pattern's eventual outcome across repeated attempts,
   reported separately and never mixed. *Closed 2026-09-24* —
   `pattern_atlas_scan.py` allows up to three events per pattern identity, each
   required to open only after the previous one resolved, and stamps every row
   with its attempt number. Item 1's scan could not see this at all.

**One more column, added 2026-09-24.** Every table now also carries the rule
leg's **own** return, beside the placebo-adjusted edge. A difference cancels
anything both legs pay -- costs, and on the short side the borrow fee -- so a
rule that beats its placebo by half a point while both lose money reads as a
success in an edge column and as what it is in this one. It is also the fastest
way to see the owner's original objection: the bull flag's own six-month return
is **+2.5% to +4.3%**, of which at most **+1.4pp** is the pattern. The rest is
the market.

**What does not change.** The placebo stays: every probability is reported
beside a random security bought the same session with the same stop and horizon,
because in a rising decade a setup that does nothing still looks profitable.
Costs stay charged. Dead companies stay in, priced by §50's measured recovery.

---

## The list

Status: **atlas** = descriptively measured · **tested** = registered test run ·
**closed** = a stop rule forbids further work · **built** = detector exists ·
**needs build** = no detector · **needs intraday** = untestable on daily bars.

### Continuation and breakout patterns — long

**Items 2-11 are measured in one pass, not ten.** `PatternScanner.scan` already
evaluates every detector on every window; item 1 threw eleven results away at
`!= "bull_flag"`. Keeping them costs the breakout engine's advance loop and
nothing else, so the marginal price of the other ten setups is a fraction of
item 1's. Running them one at a time would have cost ten times the compute for
identical numbers — **the *reporting* is one at a time, the measuring is not.**

**The detector names are measured, not assumed.** This table used to list
"tight consolidation / VCP" as one row; the scanner emits
``tight_consolidation`` and ``volatility_contraction`` as **two** pattern
types, and a merged row would have pooled two setups under one heading. A
3-security pilot on 2026-09-24 produced eleven distinct types and
``high_tight_flag`` was not among them — the detector exists, so the row stays,
but it is rare enough not to appear in a small sample and may not clear the
reporting floor.

| # | setup | detector (`pattern_type`) | status |
|---|---|---|---|
| 1 | **bull flag** | `bull_flag` | **atlas done** + tested (§44, §51) |
| 2 | **flat base / flat top breakout** | `flat_base` | **atlas running** 2026-09-24 |
| 3 | ascending triangle | `ascending_triangle` | atlas running (same pass) |
| 4 | pennant | `pennant` | atlas running (same pass) |
| 5 | cup with handle | `cup_with_handle` | atlas running (same pass) |
| 6 | high tight flag | `high_tight_flag` | atlas running — **absent from the pilot** |
| 7 | base on base | `base_on_base` | atlas running (same pass) |
| 8 | tight consolidation | `tight_consolidation` | atlas running (same pass) |
| 8b | **VCP** | `volatility_contraction` | atlas running — **was pooled with 8** |
| 9 | double bottom | `double_bottom` | atlas running (same pass) |
| 10 | inverse head and shoulders | `inverse_head_and_shoulders` | atlas running (same pass) |
| 11 | breakout-retest | `breakout_retest` | atlas running (same pass) |
| 12 | ABCD | needs build | queued |
| 13 | moving-average pullback | needs build | queued |
| 14 | recent-IPO breakout | needs build (needs listing dates) | queued |

### Breakdown patterns — short

**Unblocked 2026-09-24 without building a bearish detector.** The platform has
twelve long detectors and an 11,000-line long-directional breakout engine, all
audited; a bearish twin would be a second copy of both, needing its own audit
and drifting from the first. So the *series* is mirrored instead of the
machinery: under the reciprocal map a bear flag **is** a bull flag and a
breakdown through support **is** a breakout through resistance. See
[`SHORT_SIDE_DESIGN.md`](SHORT_SIDE_DESIGN.md) and
`src/tradeit/patterns/mirror.py`; `tests/unit/test_pattern_mirror.py` proves
the mirror inverts direction rather than doing nothing, and each assertion was
shown to fail against a deliberately broken mirror.

**The mirror locates events. It never prices them.** A short sold at `E` and
covered at `X` returns `1 − X/E`; the mirrored long returns `E/X − 1`, which is
a different and larger number. Every short return is computed on real prices
with short arithmetic, a stop *above* the entry, and a borrow fee charged at
3%/yr as a stated assumption.

**The survivorship bias runs the other way for a short**, and this is the one
place in the project where §7e's coverage gap is a penalty rather than a
flattery: the companies the corpus cannot price to their end went to zero, and
those are exactly the trades a short would have won biggest. Against that,
locate availability is unmodellable and the bear-flag population is precisely
the one most likely to be unborrowable. **The two do not cancel in any quantity
anybody here can compute.**

| # | setup | status |
|---|---|---|
| 15 | **bear flag breakdown + confirmation + retest** | **atlas running** 2026-09-24 |
| 16 | double top | mirror of item 9 — running in the same pass |
| 17 | head and shoulders top | mirror of item 10 — running in the same pass |
| 18 | descending triangle | mirror of item 3 — running in the same pass |
| 19 | rising wedge | needs build (no long twin in the registry) |
| 20 | bull trap / failed breakout | **no build needed** — see below |

Items 16-18 come free: the mirror of a double bottom is a double top, the
mirror of an inverse head and shoulders is a head and shoulders top, and the
mirror of an ascending triangle is a descending one. Item 19 has no long twin
in the registry and remains a build.

**Item 20 needs no build either.** A bull trap *is* a breakout whose event
ended in `failed_breakout`, and the scan already records where every event
ended. It is a report-time restriction (`--where final_state=failed_breakout`)
on data being written now, not a second scan.

### Candlestick patterns — from the owner's reference chart

**Implemented 2026-09-24, and they cost no scan.** They are 1-3 bar timing
triggers, and the atlas measures them **as entry filters on the patterns
above**, not as standalone signals, which is how the reference chart uses them.
A filter is evaluated at the **signal bar** -- the close that triggered the
entry, one session before the entry open -- and the replay already has that
bar loaded, so items 21-40 ride on the scan that was already running.

`src/tradeit/patterns/candlesticks.py`. They deliberately sit **outside the
detector registry**: the twelve structural detectors fit geometry, swings and a
quality model, and `bull_flag.py` is 1,387 lines for one of them. A candlestick
is an arithmetic relation between four numbers on up to three bars. Registering
them would buy a scanner pass none of them needs and would cost a re-scan of
the decade.

**Every threshold is a published convention, written down before it was
measured** -- a 10% body for the doji family, a 2x shadow for "long", 5% of
range for a tweezer's tolerance. None was tuned, and none may be tuned to
improve a result without the scoped proposition `CLAUDE.md` requires.

**Shape and context are kept apart.** A hammer and a hanging man are the *same*
bar, and so are an inverted hammer and a shooting star; only what preceded them
differs. Each is reported as a shape plus a separate prior-trend reading, never
as one combined flag, so the atlas can ask whether "hammer" pays because of the
wick or because of the decline it followed. A combined flag has already decided
that. (The two tweezers are *not* such a pair — they differ in the order of the
two bars' colours, which is shape, so they are defined separately.)

| # | setups |
|---|---|
| 21–26 | hammer · inverted hammer · hanging man · shooting star · spinning top · doji |
| 27–30 | dragonfly doji · gravestone doji · bullish engulfing · bearish engulfing |
| 31–32 | tweezer top · tweezer bottom |
| 33–36 | morning star · morning doji star · evening star · evening doji star |
| 37–40 | three white soldiers · three black crows · rising three · falling three |

Two findings from building them, both recorded because they would otherwise
recur:

* **A doji is never also a hammer here.** A bar with a long lower shadow and no
  body is a *dragonfly doji*, item 27, not a hammer, item 21. Letting both fire
  would count one bar under two of the program's numbered setups.
* **"Engulfing" barely needs its size clause.** If the second bar spans the
  first's open *and* close it cannot have a smaller body, so the comparison
  only decides the exact tie. A mutation removing it survived every test until
  one was written for that single case.

### Intraday setups — from the Warrior Trading guide

**Untestable on this corpus.** The free intraday feed is survivors-only and
accurate only for the most liquid names (§2a-i). Listed so the list is complete,
not because they are queued.

Opening-range breakout · pre-market high/pivot breaks · first 5-minute new high ·
red-to-green · micro pullbacks · break of high of day · whole/half-dollar ·
VWAP fade · gap fades · halt-resumption shorts · parabolic multi-day momentum.

---

## What has been measured so far

| setup | finding | section |
|---|---|---|
| all 12 detectors, as a quality score | null at 21 and 63 sessions | §13, §31 |
| all 12 detectors, as complete trades | **earned exactly the market's return** | §44 |
| bull flag, at the breakout | +0.1303R against a placebo's +0.1358R | §44, §51 |
| bull flag, retest-confirmed | reaches +1R first **48.7%** vs 49.1% unconfirmed; waiting earns +0.049R at t +1.54 | §51 |
| bull flag, confirmed any path | +0.0105R over entering at the breakout, t +0.72 | §51 |

**Read these as the pooled, unconditional numbers they are.** The program exists
because pooling over a decade that rose is exactly what the owner objected to.


---

## The atlas of 2026-09-25 was built twice, and the first one was wrong

Recorded because it is the most useful thing that happened in this program so
far, and because the shape of it will recur.

**What happened.** The overnight pipeline completed: 16 scan shards over the
full 5,782-security universe, then two replays producing 773,308 long pairs and
377,309 short pairs, then the consolidated book. Everything reported success.
One cell then read **−44.30%** at five sessions — a mean that large over 10,411
trades is not a result, it is one row.

**The row.** Security 3953 on 2013-05-15, a placebo leg returning **+460,516%
over five sessions**: a §0.10 price step with no recorded corporate action. The
jump guard exists to remove exactly this and had flagged it.

**Why the guard did not fire.** It was handed five of its eight shards. The
file list was built from a `ls | grep jump | head`, which truncates at ten lines
— five CSVs and five logs — and the truncation was read as the whole listing.
`jumps_s5`, `s6` and `s7` were never passed. **The guard ran at 62% coverage,
6,003 of 9,701 flagged sessions**, and every one of the ten worst contaminated
legs was flagged in exactly the three omitted shards.

**How far it reached.** Not one cell. Removing the unguarded securities moved
**15 of 27 cells by more than 0.10pp and flipped six signs** — 
`tight_consolidation/closed_above/uptrend` went from −1.59% to +0.68% at six
months, `breakout_retest/closed_above` from −3.08% to +0.95%, `pennant` from
+0.13% to −0.66% the other way. **No number from that run was reported**, and
the corrected replay was re-run with all eight shards.

### The two safeguards this bought

**A guard that depends on being handed every one of its own shards is not a
guard.** `pattern_atlas_report.py` now checks the *numbers*: any cell containing
a leg beyond `EXTREME_RETURN` (4.0, set from §0.10's own 5x signature) is
**refused and its security named**, rather than averaged. Run against the
contaminated atlas it refuses **19 of 27 cells** — the visible −44.30 was the
smaller half of the problem. Fail closed: `UNRESOLVED` is a real answer.

**A check must live on the path that runs.** The guard-rail was added to the
shared table builder while the terminal path still had its own copy of the
printing loop, so the refusal never appeared where anyone would look. The
duplicate is gone and `tests/unit/test_pattern_atlas_report.py` drives both
output formats. Each of its assertions was shown to fail against the guard-rail
removed, checked at only one horizon, made one-sided, not suppressing the row,
and with the threshold loosened.

`scripts/` had **no test coverage at all** before this. That is how a safety
mechanism came to sit in one of two code paths.

---

## Item 1 — bull flag: the atlas

**2010–2019, 180,410 paired trades, five horizons, both regimes, every row
beside a placebo drawn from the same session.** Descriptive. No trial charged.
**None of this is evidence** and none of it may be quoted as such.

### The regime split the owner asked for

By this corpus's own index, the decade is **2,038 uptrend sessions against 511
downtrend** — 80/20. Every pattern number this project published before today
was pooled across that.

### Does it continue? (reaching +1R before the stop)

| arm | uptrend | its placebo | edge | downtrend | its placebo | edge |
|---|---|---|---|---|---|---|
| at the breakout | 49.4% | 46.6% | **+2.8 pp** | 47.0% | 47.0% | **0.0 pp** |
| retest-confirmed | 48.8% | 46.6% | **+2.2 pp** | 48.0% | 49.8% | **−1.8 pp** |
| confirmed, any path | 47.0% | 45.9% | +1.2 pp | 45.5% | 47.9% | −2.4 pp |

*Corrected 2026-09-24:* the confirmed/uptrend edge was published as +1.1 pp,
which is what subtracting the two rounded percentages beside it gives.
Recomputed from the unrounded values by `pattern_atlas_report.py` it is
**+1.2 pp**. Small, and the reason the report is now a script rather than a
one-liner: a table assembled by eye subtracts what it displays.

**The edge is an uptrend phenomenon.** In a downtrend a bull flag reaches its
target before its stop exactly as often as a random security does — and a
*confirmed* one does so slightly less often. The owner's objection is answered
in the direction he suspected, though not by the mechanism he proposed: the
placebo already removed the drift, and what regime conditioning reveals is that
the small edge itself only exists on one side of the 200-session average.

**Raw continuation is higher in downtrends** (58.0% still up at 63 sessions
against 55.7% in uptrends) — and the placebo shows the same, because a
"downtrend" by a 200-session average includes the sharp recoveries of 2011,
2016 and 2019. Without the control that number would read as a discovery.

### Does it pay? (net per trade, stop honoured, 10 bps a side, rule − placebo)

| arm | regime | 5 sessions | 10 | 21 | 63 | 126 |
|---|---|---|---|---|---|---|
| at the breakout | uptrend | −0.05% | −0.09% | +0.01% | +0.05% | **+0.33%** |
| at the breakout | downtrend | −0.28% | −0.26% | −0.63% | −0.35% | +0.05% |
| retest-confirmed | uptrend | −0.04% | −0.00% | −0.07% | **+0.28%** | **+0.57%** |
| retest-confirmed | downtrend | −0.12% | −0.39% | −0.80% | +0.28% | +0.50% |
| confirmed | uptrend | −0.07% | −0.08% | −0.09% | +0.16% | **+0.42%** |
| confirmed | downtrend | +0.01% | −0.10% | −0.47% | **+0.68%** | **+1.37%** |

**Every arm loses at 5, 10 and 21 sessions and every arm gains at 126.** That is
the single most useful thing in this table and it was invisible to §44 and §51,
which measured one horizon. Short-horizon pattern trading on this corpus pays
costs for nothing; the edge, such as it is, needs **six months**.

### What this does and does not license

It licenses **one Stage B registration**: retest-confirmed or confirmed entries
held **126 sessions**, tested on the held-out 2020–2025. Nothing else.

It does not license belief. The largest edge here is **+1.37% per trade over six
months** on 5,682 overlapping trades in the thinner regime, measured in the
window the rule was chosen from. §46 produced a bigger number than that on seven
years and inverted on the next six.
