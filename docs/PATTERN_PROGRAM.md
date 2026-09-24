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
2. **One horizon, one timeframe.** Everything used 63 sessions on daily bars.
   **The atlas reports 5, 10, 21, 63 and 126 sessions**, and weekly-bar variants
   where the detector supports them.
3. **Re-break censoring.** §44 and §51 take a pattern's *first* breakout and
   score a fixed window. A trader who sees a failed breakout and waits for the
   next one is living a different experiment. **The atlas scores both**: the
   first attempt, and the pattern's eventual outcome across repeated attempts,
   reported separately and never mixed.

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

| # | setup | detector | status | where |
|---|---|---|---|---|
| 1 | **bull flag** | built | tested (§44, §51) — **re-opening for the atlas** | in progress |
| 2 | flat base / flat top breakout | built | tested as a family (§44) | atlas pending |
| 3 | ascending triangle | built | tested as a family (§44) | atlas pending |
| 4 | pennant | built | tested as a family (§44) | atlas pending |
| 5 | cup with handle | built | tested as a family (§44) | atlas pending |
| 6 | high tight flag | built | tested as a family (§44) | atlas pending |
| 7 | base on base | built | tested as a family (§44) | atlas pending |
| 8 | tight consolidation / VCP | built | tested as a family (§44) | atlas pending |
| 9 | double bottom | built | tested as a family (§44) | atlas pending |
| 10 | inverse head and shoulders | built | tested as a family (§44) | atlas pending |
| 11 | breakout-retest | built | tested (§51) | atlas pending |
| 12 | ABCD | needs build | — | queued |
| 13 | moving-average pullback | needs build | — | queued |
| 14 | recent-IPO breakout | needs build (needs listing dates) | — | queued |

### Breakdown patterns — short

**No bearish detector exists and the engine has never held a short position.**
Both are builds, and they are the owner's stated next priority after the bull
flag.

| # | setup | status |
|---|---|---|
| 15 | **bear flag breakdown + confirmation + retest** | needs build — **next after item 1** |
| 16 | double top | needs build |
| 17 | head and shoulders top | needs build |
| 18 | descending triangle | needs build |
| 19 | rising wedge | needs build |
| 20 | bull trap / failed breakout | needs build |

### Candlestick patterns — from the owner's reference chart

None is implemented. They are 1–3 bar timing triggers; the atlas will measure
them **as entry filters on the patterns above**, not as standalone signals,
which is how they are used.

| # | setups |
|---|---|
| 21–26 | hammer · inverted hammer · hanging man · shooting star · spinning top · doji |
| 27–30 | dragonfly doji · gravestone doji · bullish engulfing · bearish engulfing |
| 31–32 | tweezer top · tweezer bottom |
| 33–36 | morning star · morning doji star · evening star · evening doji star |
| 37–40 | three white soldiers · three black crows · rising three · falling three |

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
