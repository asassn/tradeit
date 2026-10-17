# Delivery plan — two tracks, one of which has dates

Authorised by the owner 2026-10-17 ("we are aligned. Decouple").

**The roadmap's phase numbering is unchanged.** `ROADMAP.md` is fixed by written
authorisation and nothing here renumbers, merges or redefines a phase. This is
an overlay that says *which phases can carry a date and which cannot*, and why.

---

## The problem this fixes

Measured from git: the project began **2026-08-07**, and **Phases 1–5 landed in
two days** — point-in-time data layer, architecture, analytics, pattern
recognition, breakout engine. The ten weeks since went to corpus correctness
and to **61 measurements across 144 trials that produced no strategy.**

The engineering was never slow. The roadmap bundles two activities with
different natures and schedules the pair as one:

| | nature | can it carry a date? |
|---|---|---|
| building the platform | engineering | **yes** |
| finding a profitable strategy | research | **no** |

Treating discovery as a milestone between the others is why the completion date
kept moving. **A platform is complete when it works; it does not become
incomplete because the strategies it evaluates currently fail their bar.**

---

## Track A — the platform. Dated, and committed to.

**Target: paper-trading-ready by 2026-12-12 (8 weeks).**

A platform that correctly reports *"no setup currently meets the bar"* is
finished, working and honest. Track A does **not** depend on Track B
succeeding.

| order | phase | why here | target |
|---|---|---|---|
| 1 | **9** — portfolio backtesting & Monte Carlo | **CLOSED 2026-10-17** — see [`PHASE_09_CLOSURE_2026-10-17.md`](PHASE_09_CLOSURE_2026-10-17.md) | ~~2026-11-07~~ **done** |
| 2 | **8** — portfolio construction, risk & compounding | partly exists (`portfolio/sizing.py`, `stops.py`, `allocation.py`, `cycle.py` — 1,695 lines); needs the same audit treatment | **2026-10-31** |
| 3 | **7** — opportunity scoring | three separated scores; needs 8 for portfolio fit | **2026-11-07** |
| 4 | **11** — paper trading execution | cost and fill models, paper broker, reconciliation, trade journal, on the live code path | **2026-11-21** |
| 5 | **12** — readiness framework & production readiness | `RESEARCH → ROBUST_BACKTEST → PAPER → LIMITED_LIVE → SCALED_LIVE`, demotion, risk controls outside a strategy's reach, explainability | **2026-11-28** |

### Re-dated 2026-10-17, and why the estimate was wrong

**Paper-trading-ready moves from 2026-12-12 to 2026-11-28** — two weeks earlier.

Phase 9 was estimated at three weeks and took one day, because **five of its
nine modules were already complete and the remaining work was running three of
them on real data** plus implementing three baselines the module had promised
and not shipped. The estimate was built from the roadmap's description of the
phase rather than from the code, which is the same error that produced the
purchase recommendation two days earlier.

**The revised dates assume the same shape holds for Phases 8, 7, 11 and 12 —
and that assumption is now explicit rather than buried.** Phase 8 already has
1,695 lines across four modules. If its audit finds what Phase 9's did, it
closes in days. If it finds genuinely missing machinery, these dates move and I
will say so at the time rather than at the end.

**The honest reading: Phases 7–12 are probably far more built than the roadmap
implies, and the remaining work is auditing, exercising and closing gaps rather
than building.** Every phase gets the Phase 9 treatment — audit against stated
requirements, separate complete from present-but-unexercised, run the
unexercised parts on real data — before any date is defended.

**Phase 10 (dashboard, assistant, news and sentiment) is deliberately deferred
and does not gate paper-ready.** It is the largest remaining phase and nothing
in Track A depends on it. Scheduling it inside the 8 weeks would be the
corner-cut that makes the other dates fiction.

**Phase 9 is first on purpose.** It is the only phase that advances both tracks,
and the research track is currently blocked on exactly the machinery it
delivers.

---

## Track B — strategy discovery. No date. A budget and a stopping rule.

**Budget: 20 registered trials, from 144 to 164.**

A deadline here does not speed discovery; it changes what counts as a
discovery. Under schedule pressure **§52 and §53 both pass** — VCP showed
in-sample t 3.82, the pennant t 4.44 with coherence across five horizons and
both directions. Out of sample they returned t 1.02 and t −0.53. A deadline
would not have made them work; it would have stopped the clock before anyone
found out, and the platform would be paper-trading two dead rules.

**That is the one corner that cannot be cut**, and schedule pressure cuts it
first.

### The stopping rule

**If 20 further registered trials produce no out-of-sample survivor, the
recorded conclusion is that this corpus does not support a pattern-based edge
at the effect sizes measurable in it.** That is a result, not a failure, and it
is a decision point rather than an open loop:

* it would redirect Track B to signals, to fundamentals, or to purchased
  history, each a separate proposition;
* it would **not** stop or delay Track A.

### What Track B spends its budget on, in order

1. **The portfolio-level question**, once Phase 9 exists — one equity curve
   against the market rather than a per-trade differential. Better statistics
   and the question profitability actually turns on.
2. **The extended Stage A window** (2003–2019, ~34 blocks, authorised
   2026-10-16) to choose candidates at roughly double the resolution.
3. **Items 6–40 of the pattern program only if** the extended window shows an
   effect near 1%. Five setups found 0.13%–0.84% and the first five cost hours
   of compute each.

---

## How the owner's decisions move the lead time

Written down so it can be used rather than re-derived each time.

**Shortens it**

* **Decoupling the tracks** — the single largest lever, taken today.
* Approving resolution improvements that cost only compute. The window widening
  of 2026-10-16 cost nothing and halved the detection limit.
* "You decide" on engineering choices. Every question upward costs a round trip
  and the answer is usually already in the code or the documents.

**Lengthens it**

* More pattern screening before the extended window reports. Each setup is
  hours of compute for a sub-resolution answer, and 35 remain.
* Requiring per-trade proof before Phase 9 exists. The portfolio question has
  better statistics and its machinery is not built.
* Purchasing data. Weeks of integration, and on 2026-10-16 the data turned out
  to be already present.

**Neutral but worth knowing**

* Stop rules closing a line do not lengthen the schedule; they stop it being
  spent on something already measured as not working.

---

## What this plan does not claim

It does not claim a profitable strategy will be found, by 2026-12-12 or ever.
It claims the **platform** will be paper-trading-ready by then, and that
whether a strategy exists is a separate question with its own budget and its
own stopping rule. Live trading remains gated by the interlock in
`src/tradeit/config.py` regardless of either track.
