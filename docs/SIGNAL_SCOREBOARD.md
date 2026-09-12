# Signal scoreboard

**As of 2026-09-09, code `4164053`.** A status record, not a findings document —
[`SIGNAL_RESEARCH_01.md`](SIGNAL_RESEARCH_01.md) holds the methodology and the
reasoning. **Every number here is measured and every one will rot**; re-run the
studies rather than quoting this later.

## What is being tested, and what is not

A **signal** answers one question: does this measurement predict future
returns? A **strategy** is a complete system — entry, exit, sizing, risk. Nine
signals have been tested. **No strategy has been evaluated.** The
moving-average baseline that appears in the backtests exists to exercise the
machinery and to serve as a hurdle, not as a candidate.

Four specifications have been run against each signal, which is why "held its
sign" below means something:

| # | universe | period | horizons | observations |
|---|---|---|---|---|
| A | 400 securities, no liquidity floor | 2000–2009 | 21, 63 | 27,923 / 9,130 |
| B | 800 securities, liquidity floors applied per observation | 2000–2009 | 21, 63 | 13,419 / 4,310 |
| C | 800 securities, liquidity floors | **2010–2024, never seen** | 63 | 9,583 |

Every universe includes securities whose prices stop inside the window — 40%
of the 2000 cohort, 53% of the 2010 one.

## The scoreboard

`IC` is the rank correlation with the forward return; `t` its significance
after correcting for overlap. **Sign held** means the relationship pointed the
same way in all four in-sample specifications.

| signal | scoring factor | hypothesis | best in-sample | sign held? | out-of-sample | status |
|---|---|---|---|---|---|---|
| `realized_vol_60` | *(none — unweighted)* | low volatility outperforms | **IC −0.060, t −10.01** | **yes, 4/4** | not yet run | **strongest candidate.** Spread unmeasurable by quantile — see below |
| `atr_percent_14` | *(none — unweighted)* | low volatility outperforms | **IC −0.078, t −7.46** | **yes, 4/4** | not yet run | as above; same effect, second measure |
| `relative_volume_20` | volume_accumulation 0.10 | *derived* | IC −0.069, t −4.54 | no — flipped | **IC +0.009, t +0.93** | **REJECTED.** Sign flipped, net +32.7%/yr → −0.2%/yr |
| `momentum_21` | relative_strength 0.25 | one-month reversal | IC −0.016, t −2.15 | yes, 4/4 | not run | weak but consistent; never economically useful |
| `momentum_126` | relative_strength 0.25 | 6-month momentum | IC +0.021, t +3.00 | yes, 4/4 | not run | weak but consistent; never economically useful |
| `momentum_252` | relative_strength 0.25 | 12-month momentum | IC +0.023, t +3.88 | no — flipped | not run | inconsistent across specifications |
| `dist_from_sma_200` | pattern_quality 0.20 | trend following | IC +0.015, t +2.58 | no — flipped | not run | inconsistent |
| `dist_from_sma_50` | pattern_quality 0.20 | trend following | IC −0.024, t −2.77 | no — flipped | not run | **contradicts its declared prior twice.** Not flipped — see §7 |
| `rsi_14` | pattern_quality 0.20 | mean reversion | IC −0.015, t −1.70 | no — flipped | not run | never detectable |
| `sector_strength` | sector_strength 0.10 | **strong sectors outperform** | 2000s IC +0.026 t +2.93; 2010s IC −0.018 t −2.48 | **no — reverses by decade** | n/a | **significant in both directions.** Worse than a null: the relationship inverts |
| `volume_momentum` | volume_accumulation 0.25 | rising volume is accumulation | **IC +0.043, t +20.67** | **yes, 4/4 samples** | **IC +0.004, t +1.95** | **REJECTED.** Passed every criterion in sample, net +32.7%/yr → +3.4%/yr, geometric edge +20.1%/yr → **−11.4%/yr** — see §21, §22 |
| `obv_trend` | volume_accumulation 0.25 | accumulation, **signed** | IC −0.012, t −5.60 | **consistently NEGATIVE, 4/4** | **IC −0.034, t −16.27, 4/4** | **replicated and strengthened, inverted to the factor's own prior.** Fails half-period consistency; see §21, §24 |

### Factors carrying weight that have not been tested at all

| scoring factor | weight | why not |
|---|---|---|
| `breakout_confirmation` | ~~0.20~~ → **gate** | **retired as a weight 2026-09-10**; now a conditional gate — see §11 |
| `fundamental_quality` | 0.15 → 0.1667 | **tested 2026-09-10: no detectable relationship** — see §10 |
| `sector_strength` | 0.10 | ~~no sector classification in the corpus~~ — **resolved 2026-09-09.** `issuer_sic_observations` now classifies 12,871 issuers, covering 93.5% of priced securities, point-in-time from SEC filing headers. The factor is computable; it has still never been *tested* |

**Updated 2026-09-10:** `sector_strength` was measured and retired; `fundamental_quality` has now been measured and shows nothing; `breakout_confirmation` remains untested and cannot be weighted the way the others are. See §10.

## Scoring weights: one factor retired on evidence

**`sector_strength` was removed on 2026-09-10**, by the owner, on the measured
finding below. Effective weights are now:

| factor | declared | effective | validated? |
|---|---|---|---|
| `relative_strength` | 0.25 | **0.2778** | no |
| `pattern_quality` | 0.20 | **0.2222** | no |
| `breakout_confirmation` | 0.20 | **0.2222** | **never tested** |
| `fundamental_quality` | 0.15 | **0.1667** | **never tested** |
| `volume_accumulation` | 0.10 | **0.1111** | no |

Weights normalise on load, so the survivors keep their declared numbers and the
proportions between them are unchanged. **The strategy digest changed**, which
is correct: this is a different strategy, and results tied to the old digest
belong to the old one.

**Dropping the failed factor does not promote the other four.** They remain
unvalidated; what changed is that a factor which *was* measured, and failed, no
longer carries weight. Two of the survivors have still never been tested at all.

Nothing else measured justifies moving any remaining weight. The one candidate
that passed every in-sample gate failed out of sample decisively — see §8.

## Volatility, measured as a portfolio rather than a spread — RUN

**Status: complete. Result is MIXED, and under the registered criterion that
means it did not replicate.**

The volatility relationship is the only one that never changed sign, and it is
the largest effect found. §7 explains why a quantile spread cannot value it:
sorting on volatility sorts on the variance of the very thing being averaged,
so the high-volatility bucket holds the extreme outcomes *by construction* and
the mean-spread estimator is worst exactly where the signal is strongest.

**The fix is not a better estimator; it is a different portfolio.** A portfolio
earns the arithmetic mean of its holdings *because it holds equal dollar
amounts*. One that holds equal **risk** amounts — smaller positions in more
volatile names — genuinely earns something else. That is not a statistical
trick, it is an implementable change, and Phase 8 already does it: sizing by
risk with an ATR-based stop makes position size inversely proportional to
volatility.

The experiment, to be pre-registered before it runs:

| arm | sizing | stop |
|---|---|---|
| A | risk-based, fixed-percentage stop → size ∝ 1/price, ≈ equal dollar | `stop_pct` |
| B | risk-based, **ATR stop** → size ∝ 1/ATR, equal risk | `initial_stop_atr_multiple` |

Same universe, same entry rule, same costs, same period. If the volatility
relationship is real and capturable, B beats A on risk-adjusted return. If it
does not, the relationship is real and not monetisable, which is also an
answer worth having in writing.


### Result — 2026-09-09

Same universe, same entry rule, same costs, same risk budget, 800 securities
per period with the failures included. The arms differ only in where the stop
sits, which is what sets position size.

| | in-sample 2000–2009 | out-of-sample 2010–2024 |
|---|---|---|
| **A** equal dollar — return | −14.69% | +22.02% |
| **B** equal risk — return | **−6.61%** | **+27.14%** |
| A Sharpe | −0.40 | −0.14 |
| B Sharpe | **−0.30** | **−0.07** |
| Sharpe change | **+0.10** | **+0.07** |
| A max drawdown | 35.82% | 28.37% |
| B max drawdown | **33.25%** | 29.92% |
| drawdown change | **−2.58pp** | **+1.55pp** |
| exposure, both arms | 90.5% / 91.0% | 93.6% / 93.6% |
| verdict | **CAPTURES** | **MIXED** |

**What replicated.** Equal-risk sizing improved return in both periods (+8.08pp
and +5.12pp) and improved Sharpe in both (+0.10 and +0.07), at effectively
identical market exposure. The direction of the effect held on data the
volatility finding had never seen, which is more than `relative_volume_20`
managed — that one reversed sign.

**What did not.** Drawdown improved in-sample and worsened out-of-sample. The
registered criterion required both, so the out-of-sample verdict is MIXED, not
a capture. That criterion was fixed before the run and is not being relaxed
now.

**The honest reading.** There is a small, consistent, replicating improvement in
risk-adjusted return from sizing by risk rather than by dollars — and it is not
large enough, or clean enough on drawdown, to call the volatility relationship
captured. Both arms lose money on a risk-adjusted basis in both periods
(Sharpe negative throughout, against a 3% risk-free rate). **An improvement
from −0.14 to −0.07 is less bad, not good.**

Nothing here changes a scoring weight. It is evidence about *sizing*, which is
a Phase 8 mechanism, and the mechanism it supports — `RiskBasedSizer` with an
ATR stop — is already what the system does.

### A data defect this run exposed

The out-of-sample arm crashed on first attempt: the corpus holds **11,580 bars
priced at exactly zero** across 248 securities, clustered at the end of a
series, over 11,000 of them after 2010. `OhlcvBar` refused them, which is how
they were found. They are now excluded and counted separately by
`CorpusSessionData`, and recorded in the data dictionary. Treating one as a
real print books a −100% return on a session nobody traded, and they sit
exactly where a survivorship study is most sensitive.


---

## sector_strength, tested — 2026-09-10, code `92493ea`

The factor became computable on 2026-09-09 and this is its first test. It puts
`SectorStrengthEngine` itself on trial — the declared composite of relative and
absolute return, relative momentum, breadth above two moving averages and
participation — not a trailing-return proxy for it.

**Universe:** 800 securities tradeable on 2015-01-02, 400 that survived and 400
whose prices stopped, liquidity floors applied on trailing data,
non-overlapping sampling, costs charged against the horizon's turnover.

**Declared before the run:** direction POSITIVE — *securities in strong sectors
outperform*. That is the premise behind giving the factor any weight, so it is
the hypothesis on trial.

| | 21 sessions | 63 sessions |
|---|---|---|
| observations | 19,524 | 6,167 |
| dropped, no classification yet | 2.5% | 2.5% |
| information coefficient | **−0.018** | **−0.023** |
| IC t-statistic | **−2.48** | −1.83 |
| spread t | +0.68 | −1.37 |
| mean spread | +0.20% | −1.17% |
| median spread | −0.50% | −2.50% |
| net of costs | +0.1%/yr | −5.4%/yr |
| verdict | `OUTLIER_DEPENDENT` | `NOT_DETECTABLE` |

### The declared hypothesis is rejected

**The relationship is negative at both horizons, and at 21 sessions it is
significant** — t = −2.48, clearing both the 2.0 floor and the 0.52
multiple-testing hurdle for two trials. The sign is consistent across horizons,
which more of the original nine signals failed than managed.

So on this window, securities in *strong* sectors slightly **underperform**.
That is sector mean-reversion, and it is the opposite of what the weight
assumes.

**The direction is not being flipped.** `dist_from_sma_50` set that precedent
and it holds here: a prior that reverses whenever the data disagrees is not a
prior, and the whole value of declaring one is refusing that move. If sector
*weakness* is to be tested as a signal, **the declaration has to precede a run
on data not used here** — this window has now been spent.

Note also that neither horizon produced an established tradeable spread, so
even the inverted reading is not yet a trade.

### What it says about the 0.10 weight

The weight assumes strong sectors are worth buying. **Measured, they are not,
and the evidence mildly favours the reverse.** That is the first factor in this
scoreboard for which there is direct evidence *against* its declared
specification rather than merely an absence of evidence for it.

No weight has been changed. This is a decision, and it is the owner's.

### A corpus limitation this surfaced

A 2000–2009 window dropped **87.9%** of security-sessions for want of any
classification in force. The cause is not the SIC fetch: **the corpus's own
`filings` index is skewed recent**, with no filing on record before 2010 for
nearly 10,000 of the 12,940 issuers. Fetching more headers cannot fix it, and
any point-in-time study needing pre-2010 fundamentals or classification is
bounded by the same gap. The 2015–2024 window drops 2.5%.


---

## sector_strength on the 2000s — 2026-09-10, code `8c31d02`

The 2015–2024 test dropped 87.9% of security-sessions on a 2000–2009 window for
want of any classification in force. Closing the filings-ingest gap took that to
**2.7%**, so the same declared hypothesis could be put to genuinely different
data. Same signal, same engine, same direction declared **POSITIVE**, same
horizons, universe drawn the same way.

| | 2000–2009 | 2015–2024 |
|---|---|---|
| observations, 21 sessions | 12,590 | 19,524 |
| **IC, 21 sessions** | **+0.026** | **−0.018** |
| **IC t, 21 sessions** | **+2.93** | **−2.48** |
| IC, 63 sessions | +0.042 | −0.023 |
| IC t, 63 sessions | +2.72 | −1.83 |
| verdict, both horizons | `OUTLIER_DEPENDENT` | `OUTLIER_DEPENDENT` / `NOT_DETECTABLE` |

### The sign reverses between decades, and both directions are significant

In the 2000s strong sectors **outperformed** — which is what the weight assumes,
at t = +2.93. In 2015–2024 they **underperformed**, at t = −2.48. Both clear the
2.0 floor and the 0.52 two-trial hurdle. Both horizons agree within each decade
and disagree across them.

**This is worse for the factor than a null result would be.** A signal that
measures nothing is merely useless. A signal that is significant in both
directions depending on the decade is one whose sign you would have to know in
advance — and knowing it in advance is the whole problem.

### It corrects the previous entry

The 2015–2024 run alone read as *"hypothesis rejected, and the relationship
points the other way."* With the 2000s in hand that was **the wrong
conclusion** — or rather, a conclusion drawn from one period and stated as
though it were about the signal. The right statement is that
`sector_strength` **has no stable direction**.

This is also the case for the standing rule against changing a weight on one
window. Had the weight been inverted on the 2015–2024 evidence, the 2000s would
now be showing it inverted the wrong way.

### Neither decade produced a tradeable spread

Every verdict is `OUTLIER_DEPENDENT` or `NOT_DETECTABLE`: mean and median
quantile spreads disagree in sign in three of the four runs. So even setting the
instability aside, nothing here is a trade.

### Where that leaves the 0.10 weight

`sector_strength` is the only factor on this scoreboard measured on two
independent decades. It is significant in both and consistent in neither. The
weight assumes a direction the evidence supplies in one decade and reverses in
the next.

No weight has been changed. The decision is the owner's, and it is now the
best-evidenced one on the board.


---

## §10 — the last two weighted factors — 2026-09-10, code `9cf9773`

### `fundamental_quality`: measured, and it shows nothing

The criteria are the ones `FundamentalConfig` **already declares**, not
criteria invented for the test: ROE ≥ 0.10, debt-to-equity ≤ 2.0, revenue
growth ≥ 0.10, earnings growth ≥ 0.15. The signal is the fraction met, so what
was on trial is the system's own definition of quality.

| | 21 sessions | 63 sessions |
|---|---|---|
| liquid security-sessions | 24,026 | 7,837 |
| with fundamentals knowable and fresh | 17,931 (74.6%) | 5,820 (74.3%) |
| information coefficient | **−0.009** | **−0.015** |
| IC t | **−1.16** | **−1.11** |
| mean spread | −0.45% | −1.61% |
| median spread | −0.28% | −0.82% |
| verdict | `NOT_DETECTABLE` | `NOT_DETECTABLE` |

**No detectable relationship at either horizon.** The sign is weakly negative
both times — higher declared quality very slightly underperforming — but
nowhere near the 2.0 threshold, so the honest reading is *nothing here*, not
*quality is bad*.

This is a cleaner null than `sector_strength` produced: consistent, unremarkable
and not significant in either direction.

### The fundamentals are a backfill, and that bounds every study using them

`knowledge_time` on `security_fundamental_facts` begins in **2009**, and the
median gap from `period_end` to `knowledge_time` is **1,574 days** — 97.6%
exceed 400. SEC's XBRL datasets start around 2009 and restate history, so the
recorded instant is when the corpus learned a fact, not when the market could
have.

Securities with an annual figure both knowable at the session and fresh:

| session | securities |
|---|---|
| 2005-06-30 | **0** |
| 2010-06-30 | 409 |
| 2013-06-30 | 7,584 |

**A point-in-time fundamental study before roughly 2013 is not possible on this
corpus**, and one that ignored `knowledge_time` would be reading a 2016
restatement into a 2013 decision. This is a constraint on `fundamental_quality`
and on any future study that touches fundamentals.

### `breakout_confirmation`: not merely untested — hard to weight at all

Two findings, and the second matters more than the first.

**Nothing has been computed.** `breakout_events`, `breakout_observations`,
`breakout_labels` and `pattern_observations` are all **empty**. Testing it needs
the Phase 4→5 pipeline run across the universe: pattern detection produces a
boundary, `BreakoutEngine.open_event` mints an event, `advance` walks it
session by session, and only then is there a confirmation score. That is a
project, not a study.

**It is conditional by construction.** `ConfirmationInputs` requires
`acceptance_score` as a mandatory field — post-breakout evidence only. A
security with no open breakout does not have a low confirmation score; **it has
none**. So under the coverage contract in `tradeit.strategy.factors`, a slate
containing any security without an open breakout can never have uniform
coverage while `breakout_confirmation` carries weight.

That is a design question about the weight rather than a measurement:
`breakout_confirmation` behaves like a **gate on a subset of candidates**, not
like a factor every security scores on. Weighting it at 0.2222 alongside four
factors every security *can* score treats two different kinds of thing as one.

**No weight change is proposed.** The measurement for `fundamental_quality` is
a null, and the issue with `breakout_confirmation` is structural — the right
response is a decision about how a conditional factor should enter a score, not
a number moved.


---

## §11 — `breakout_confirmation` becomes a gate — 2026-09-10

Authorised by the owner. **Retired as a weight for a different reason from
`sector_strength`**: not measured and failed, but declared as the wrong
instrument.

A weight says every candidate has some amount of a quality and the score blends
them. A gate says most candidates are unaffected and a few are refused.
Confirmation is the second — `ConfirmationInputs` requires post-breakout
evidence, so a security with no breakout has **no** confirmation score rather
than a low one, and a slate containing any such security could never satisfy
the uniform-coverage rule in `tradeit.strategy.factors` while it carried weight.

### The policy already existed; the gate reads it

Nothing new was invented. `ProfileConfig` already declares what evidence a
breakout needs before it may be called confirmed — required closes,
follow-through, relative volume, retest quality, and a `min_evidence_coverage`
below which it "declines to confirm at all". The engine applies that policy and
records a `BreakoutState`. `breakout_confirmation_gate` reads the verdict.

### Four outcomes, and two of them are refusals

| candidate state | outcome | blocks? |
|---|---|---|
| no open event, or not yet broken out (incl. `REJECTED`) | `NOT_APPLICABLE` | no |
| `CONFIRMED`, `RETEST_CONFIRMED` | `PASSED` | no |
| `CLOSED_ABOVE`, `CONFIRMATION_PENDING`, `RETEST_*` | `BLOCKED_AWAITING` | **yes** |
| `FAILED_BREAKOUT` | `BLOCKED_FAILED` | **yes** |

The gate turns on `BreakoutState.has_broken_out`, which the lifecycle calls
*"the dividing line the whole machine turns on"*. **Approaching a level is not
a reason to refuse a stock.**

Awaiting and failed are reported separately because they are different facts: a
developing setup and a dead one. And `applied` distinguishes "did not apply"
from "applied and passed", so a slate that confirmation never filtered is
visible rather than inferred from an absence of vetoes.

**The gate cannot promote.** Passing removes a refusal and adds nothing to any
score — which is what keeps it a gate rather than a weight under a new name. A
test asserts the result carries no score field at all.

### Weights after

| factor | declared | effective | validated? |
|---|---|---|---|
| `relative_strength` | 0.25 | **0.3571** | no |
| `pattern_quality` | 0.20 | **0.2857** | no |
| `fundamental_quality` | 0.15 | **0.2143** | **measured, showed nothing** |
| `volume_accumulation` | 0.10 | **0.1429** | no |

Two things worth noticing rather than acting on. `relative_strength` now
carries **36%** of the score, having absorbed both retirements. And
`fundamental_quality` keeps 21% despite §10 finding no detectable relationship
— the null was reported and no weight change was proposed for it, so it
inherited weight from a factor that was removed. Neither is wrong; both are
consequences of removing factors rather than re-deriving the remainder, and
both are decisions still open. **§12 closes them.**

---

## §12 — the four remaining weights, re-derived — 2026-09-10

§11 left `relative_strength` holding **36%** of the score, not because anything
was measured about it but because it happened to be the largest weight when two
other factors were removed and the remainder renormalised. That is an artefact
of arithmetic, and an artefact carrying 36% of a scoring decision is exactly the
kind of number this project treats as worse than no number: it will be acted on,
and it cannot be traced to evidence.

So the question was put properly. **What do the runs actually license?**

### What the evidence says about each survivor

| factor | measured how | best result | verdict |
|---|---|---|---|
| `relative_strength` | **proxy** — `momentum_21/126/252` | `momentum_252` IC t **+3.88** | largest t of the four, and it **flipped sign** across specifications; `momentum_21` stable but **negative** |
| `pattern_quality` | **proxy** — `dist_sma_50/200`, `rsi_14` | `dist_sma_200` t **+2.58** | **no stable sign at all**; both SMA-distance kernels flipped, `rsi_14` never detectable |
| `fundamental_quality` | **direct** | IC **−0.009**, t **−1.16** (21s); **−0.015**, t **−1.11** (63s) | not detectable, both horizons |
| `volume_accumulation` | **proxy** — `relative_volume_20` | passed in-sample | **failed out-of-sample**, +32.7%/yr → −0.2%/yr, sign flipped |

Against the multiple-testing hurdle for the **20 trials actually run**,
|t| > **1.90** — and with the 2.0 floor the practical bar is |t| ≈ 2.0–3.9 to
claim anything at all. **No surviving factor produced an `ESTABLISHED` tradeable
spread at any specification tried.** Every quantile spread came back
`OUTLIER_DEPENDENT`, `NOT_DETECTABLE` or `SPREAD_NOT_ESTABLISHED`.

### The derivation

The four factors are **not distinguishable from one another** on this evidence.
Two of the three largest t-statistics flipped sign; the one that did not is
negative; the only directly measured factor is null. There is no ordering here
that survives its own hurdle.

**Ordering by numbers that do not clear their thresholds manufactures
confidence.** It takes noise and prints it as a ranking, and the ranking then
looks like a finding to whoever reads the config next.

So the weighting the evidence supports is **equal — 0.25 each**:

| factor | before (effective) | after |
|---|---|---|
| `relative_strength` | 0.3571 | **0.25** |
| `pattern_quality` | 0.2857 | **0.25** |
| `fundamental_quality` | 0.2143 | **0.25** |
| `volume_accumulation` | 0.1429 | **0.25** |

This is a **statement of ignorance held deliberately**, not a claim that the
four are equally good. It is a derivation because the evidence *rules out*
differentiation, and equal weight is the only weighting consistent with that.

### Three alternatives, rejected

**Weight by t-statistic.** The ordering it produces is the ordering of numbers
that do not clear their own hurdle, and its top-ranked factor is the one whose
sign flipped. Rejected.

**Weight by sign stability.** Only `relative_strength` has a proxy whose sign
held across all four specifications — so this concentrates the score in one
factor on evidence far too weak to carry it, which is a reconstruction of the
36% artefact being removed. Rejected.

**Zero the factors whose proxies failed.** Three of the four were measured only
by **proxy** — price kernels standing in for engines that have never been run.
`pattern_quality`'s real detector is not distance-from-a-moving-average, and
`volume_accumulation`'s is not `relative_volume_20`. Punishing a factor for its
substitute's failure is not evidence about the factor. Rejected.

### The asymmetry worth naming

`fundamental_quality` is the **only survivor measured directly**, and it
returned a null. Down-weighting it for that while leaving the unmeasured factors
higher would mean **measuring a factor is punished relative to leaving it
alone** — which is a poor property for a research loop to have, because it makes
ignorance the safest place for a weight to sit.

The answer to its null is to **measure the other three directly**, not to
reshuffle weights among them.

### What would change this

Equal weight is the correct answer *to the evidence that exists*, and it should
not survive better evidence. It moves when a factor produces an `ESTABLISHED`
spread from its **real** engine — pattern detection, not SMA distance;
accumulation, not raw relative volume — out-of-sample, on a corpus whose
survivorship gate says something other than `SURVIVOR_BIASED`. Until then, no
factor has earned more than a quarter.

---

## §13 — `pattern_quality`, measured by its real detector — 2026-09-10

§12 refused to zero this factor on the strength of its proxies failing, on the
grounds that **punishing a factor for its substitute's failure is not evidence
about the factor**. That left an obligation: run the real thing. This is it.

Twelve enabled D1 detectors — VCP, cup-and-handle, bull flag, flat base,
ascending triangle, pennant, high tight flag, double bottom, inverse head and
shoulders, base-on-base, tight consolidation, breakout-retest — over a trailing
236-bar window at every sampled session. **3,876 securities, 285,177 scan points,
270,081 carrying a live pattern.** Direction, horizon, quantile and universe were
[pre-registered](prereg/PATTERN_QUALITY_2026-09-10.md) and committed to git
before the run finished.

### The finding that does not depend on any statistic

**Some structure was present on 99.6% of scan points**, a median of 8 concurrent
and up to 28; a median of 5 of them live. Quality reads p25 69, median 78, p75 86
— half the mass inside a 17-point band near the top of a 0–100 scale, because
"best of eight" is a maximum order statistic.

A factor that is defined for almost every security on almost every day, and that
reads about 78 when it does, **cannot narrow a slate**. That is true regardless
of what follows.

### Where the corpus lied, and how it was caught

The first pooled run reported a mean forward return of **+8,511,217%** and a
quantile spread of **−1,163,218%**. Not a finding — a defect, now recorded as
§0.7 of the data dictionary. Security 4565:

```
2005-11-09   o/h/l/c  0.0001   volume 0
2005-11-10   o/h/l/c  92000    volume 0
2005-11-11   o/h/l/c  0.0001   volume 0
```

Placeholder rows the vendor emits after a security stops trading — **2,245,866
raw bars, 6.339%, across 7,582 securities**. `price_series` refuses `close <= 0`
and serves `0.0001` happily. Requiring `volume > 0` at both endpoints removed
10.7% of observations. **The medians were normal throughout**, which is exactly
why the means went unchallenged as long as they did.

### Three cuts, three different answers

| horizon | study verdict | IC t | spread t |
|---|---|---|---|
| 21 | `SPREAD_NOT_ESTABLISHED` | **+2.86** | +0.16 |
| 63 | `OUTLIER_DEPENDENT` | **+4.51** | −0.04 |

The IC clears the 24-trial hurdle of 1.98 at both horizons. The spread is
indistinguishable from zero at both. When those two disagree, the tie is broken
by asking whether the sign survives the choice of statistic:

| statistic | 21 sessions | 63 sessions |
|---|---|---|
| Spearman (ranks) | **+0.0056** (t +2.93) | **+0.0154** (t +4.63) |
| Pearson (levels) | −0.0246 (t −12.81) | −0.0177 (t −5.32) |
| Pearson winsorized 1/99 | −0.0135 (t −7.03) | −0.0065 (t −1.94) |
| Pearson less top 0.1% | −0.0144 (t −7.47) | −0.0101 (t −3.04) |

**The sign flips.** Ranks say positive and significant; levels say negative and
more significant. Both cannot be a description of the same edge.

### The aggregator matters more than expected — and this cuts *for* the factor

`SignalStudy` judges quantile spreads **arithmetically**, and on this corpus the
arithmetic cross-sectional mean is dominated by a few enormous winners —
`signal_research` already caught it reporting a 21.9%/yr buy-and-hold for a
decade the market spent flat. What a portfolio compounds is the **geometric**
mean, and here the two disagree in sign:

| horizon | top quintile | all candidates | bottom quintile |
|---|---|---|---|
| 21 | −0.19%/yr | −2.37%/yr | **−9.35%/yr** |
| 63 | −0.23%/yr | −3.12%/yr | **−11.60%/yr** |

On a compounded basis high quality looks genuinely useful — **+2.17 and +2.90
percentage points a year over ranking at random**, bootstrap CI excluding zero at
both horizons. The mechanism is coherent: a high-quality structure is by
construction a *tight* one, low-quality "patterns" are volatile junk, and
volatility drag destroys the junk even though its arithmetic mean is higher.

This is recorded prominently because **it is the strongest case the factor has**,
and it was found by testing an objection to a null rather than an objection to a
result.

### And then it dies on its own sample

| horizon | 2000–2004 | 2005–2009 |
|---|---|---|
| 21 | **+6.45 pp/yr**, CI excludes 0 | **−1.33 pp/yr**, CI includes 0 |
| 63 | **+8.10 pp/yr**, CI excludes 0 | **−1.13 pp/yr**, CI includes 0 |

The entire geometric edge is the first half of the decade. It **reverses sign**
in the second. `sector_strength` was retired for precisely this shape — t +2.93
in the 2000s and −2.48 in the 2010s — and the standard does not move because a
different factor is the one failing it.

### Verdict

**`pattern_quality` is not validated by its real detector.** Three independent
reasons, any one sufficient:

1. **It cannot discriminate** — present on 99.6% of scan points, reading ~78 for
   almost everything.
2. **Its sign depends on the statistic chosen** — Spearman positive, Pearson
   negative, both significant.
3. **Its only robust-looking edge is a period, not a signal** — strong in
   2000–2004, reversed in 2005–2009.

The pre-registration named exactly this outcome in advance: *"a significant t on
the IC alone with no established spread"* was declared insufficient before the
run, because that is what the SMA proxies produced and it did not license a
weight then either.

### What this does and does not change

**The weight does not move.** §12's argument survives intact and now has a second
worked example: down-weighting a factor *because it was measured* would mean two
of the four are penalised for having been examined while two keep their weight by
remaining unexamined. `pattern_quality` joins `fundamental_quality` as
**measured and null**; `relative_strength` and `volume_accumulation` remain
**unmeasured by their real engines**. Equal weight at 0.25 remains the only
weighting consistent with that, and remains a statement of ignorance rather than
a claim of equality.

**A caution that now applies to every row above it.** The `SignalStudy` verdicts
throughout this document rest on arithmetic quantile spreads. This run is the
first demonstration that on this corpus the arithmetic and geometric readings can
**disagree in sign**, which means a `NOT_DETECTABLE` verdict here is weaker
evidence of absence than it looks. It does not overturn any recorded result — the
sub-period test killed this factor on the geometric reading too — but a factor
that failed only on an arithmetic spread deserves the geometric check before
anyone calls it dead.

### Registered and spent

4 trials — quality × 2 horizons, presence × 2 horizons. Ledger **20 → 24**;
hurdle now |t| > 1.98. `pattern_present` was tested separately and separately
counted, so a failure of the grading question could not be quietly replaced by
the filtering one; it returned `NOT_DETECTABLE` at 21 sessions and
`SPREAD_NOT_ESTABLISHED` at 63.

---

## §14 — the read rule changed underneath every result above — 2026-09-10

§13 found security 4565 serving `0.0001` and `92000` on zero volume and left
the fix open, because fixing it in `price_series` changes what every recorded
result means. It is now fixed. **Every section above was computed under the old
rule**, and this note exists so nobody compares a pre-§14 number with a post-§14
one without knowing that.

**The rule** (`tradeit.research01.series.admit_prints`, applied identically by
`price_series`, `CorpusSessionData` and both price views): a zero-volume bar is
served only as an exact flat copy of the last traded close, never across a split.
**560,100 raw bars refused (1.581%) across 3,353 securities.** Full measurement
and the rejected alternative in data dictionary §0.7.

**Refusing every zero-volume bar was the obvious fix and was rejected on
measurement.** 637,214 of 640,333 zero-volume runs are followed by trading
again, and the backtester retires a holding after ten sessions of silence —
blanket refusal would have delisted 23,136 live, quiet stocks. That would have
moved the survivorship results in the pessimistic direction for a reason that
has nothing to do with survivorship.

**Which results are most exposed**, in order:

1. **The survivorship backtest** (commit `823f2f3`, recorded only in that
   commit's message until §15 below). In a backtest a refused bar was a mark
   and a stop trigger. Most exposed, and the headline 8–46 pp finding rests on
   it. **Re-run in §15.**
2. **`relative_volume_20` (§9).** A volume ratio; the refused bars are zero-volume
   by construction, though the carried ones that remain are too.
3. **The price-signal studies.** Returns across a refused bar are gone; returns
   ending on a carried close are unchanged.
4. **`pattern_quality` (§13) least of all.** Its return endpoints already required
   `volume > 0`; only its detector windows contained refused bars.

None has been re-run yet. Until one is, its recorded number is a statement about
the code at its recorded commit, which is what it always was.

---

## §15 — the survivorship gap, re-measured — 2026-09-11

> **Superseded by §17.** The "robust ~50 points at recovery 0.0" below came from
> booking bought-out companies as total losses. With each delisted holding's
> recovery taken from its filings, the gap is −10.7 points on average and not
> established. The rest of this section stands as the record of how that was
> found.

The headline result of the corpus effort — *excluding the companies that failed
was worth 8 to 46 percentage points to a moving-average rule over 2000–2009* —
was recorded only in the message of commit `823f2f3`. It is recorded here now,
re-run under §14's read rule, and tested for the first time against the
question a single run cannot answer: **how much does it move if you draw a
different, equally valid sample?**

### It reproduces exactly

Same spans (one SQL over raw bars, regenerated: 14,257 rows, identical), same
arguments (`--cap 400`, recovery 1.0 and 0.0, defaults otherwise), run from a
worktree at each code state:

| code state | survivors | everybody @1.0 | everybody @0.0 |
|---|---|---|---|
| `823f2f3`, original | −13.10% | −21.16% | −58.65% |
| `8b9563d`, zero-price fix | −13.10% | −21.16% | −58.65% |
| `5566423`, §14 read rule | **−0.10%** | **+0.64%** | **−51.21%** |

The zero-price fix changes nothing here because this universe holds **no**
zero-priced bars in 2000–2009 — measured, not assumed. The §14 rule refuses
17,347 bars in the survivors arm (68 securities) and 3,740 in the died arm (50).

### A 1.8% change moved one arm thirteen points — and that is the finding

The survivors arm went from −13.10% to −0.10% on 1.8% of its bars. Attributing
that trade by trade: **273 of 318 traded securities changed P&L**, and the
securities the rule actually touched account for only +4,425 of the +13,003
difference. The two runs are identical for 271 sessions; on **2001-01-30** one
admits security 2464 and the other 2164, and 190 of ~1,050 trades differ from
there on.

A capacity-limited portfolio is path-dependent: one different admission
reallocates slots, heat and cash for everything after it. So a single sample's
number carries noise of that size — and the *gap* between two such runs carries
it twice.

### Four disjoint samples

`--offset 0..3` draws four samples with the same construction and no died-arm
security in common. The "old rule" arm runs at `8b9563d`, because the original
code **crashes** on offsets 2 and 3: they contain zero-priced bars, which that
version handed to `OhlcvBar`. On offset 0 the two states are identical.

| rule | sample | survivors | gap @1.0 | gap @0.0 | delisted |
|---|---|---|---|---|---|
| old | 0 | −13.10% | −8.06 | −45.55 | 13 |
| old | 1 | +6.54% | −22.59 | −40.28 | 7 |
| old | 2 | +20.78% | −0.85 | −56.02 | 8 |
| old | 3 | +44.80% | −16.61 | −51.55 | 11 |
| **new** | 0 | −0.10% | **+0.74** | −51.11 | 15 |
| **new** | 1 | +16.47% | −32.93 | −51.59 | 9 |
| **new** | 2 | +29.41% | −6.90 | −57.34 | 9 |
| **new** | 3 | +33.89% | −3.14 | −55.24 | 16 |

| | gap @0.0 | gap @1.0 | survivors alone |
|---|---|---|---|
| old rule | mean **−48.4**, sd 6.9 | mean −12.0, sd 9.5 | sd 24.4 |
| new rule | mean **−53.8**, sd 3.0 | mean −10.6, sd 15.2 | sd 15.3 |

### What holds and what does not

**If delisted holdings recover nothing, survivorship bias is about 50 points,
and that is robust.** 51 to 57 under the new rule in every sample, standard
deviation three points. The new rule widens it in all four pairs, by 1.3 to 11.3
points, with more delisted exits in every sample — refused untraded prices no
longer keep a dying holding alive long enough to dodge the silence rule. That is
the direction the fix should push.

**If they recover their last price, the bias is small and its size is not
established.** Seven of eight samples put everybody below the survivors, so the
*sign* is fairly consistent — but the magnitude runs from +0.7 to −32.9, and on
the original sample under the new rule it is **+0.74**: no bias at all. With
four samples and a standard deviation of 15, a mean of −10.6 is not
distinguishable from zero.

**So the finding is restated, not retracted:** *survivorship bias for this rule
over 2000–2009 lies between roughly zero and 57 points, and which end is true
depends almost entirely on what a delisted holding was worth.* The original
"8–46" was one draw from each end; its upper end strengthens, its lower end was
never a number.

### Two consequences

**The delisting recovery assumption is now the whole question.** An
acquisition pays at or above the last price; a bankruptcy pays close to
nothing; the system cannot yet tell them apart, which is why recovery is a
required argument. Classifying *why* each died-arm security's prices stopped
would replace a 0-to-57 bracket with a measurement, and the evidence is already
in the corpus — measured in `filings`: 16,124 Form 25-NSE exchange delistings,
28,387 Form 15 deregistrations (15-12G 14,919 · 15-15D 7,737 · 15-12B 5,731),
6,520 DEFM14A merger proxies, and 4,497 tender-offer filings (SC TO-T 2,131 ·
SC 14D9 2,366). A merger proxy or tender offer before the prices stop points to
an acquisition; a Form 25 or 15 with neither points elsewhere. It is free.

**No single-sample backtest here should be quoted as a number again.** The
survivors arm alone spans −13.1% to +44.8% across four equally valid samples of
the same decade. Every portfolio result in this document that came from one
sample — the volatility experiment included — carries noise of that order and
is at best a direction.

**No result here is evidence of profitability** — the gate still reads
`SURVIVOR_BIASED`, and nothing in this section changes that.

---

## §16 — why the dead companies died — 2026-09-11/12

§15 left survivorship bias bracketed between about zero and 57 points, decided
almost entirely by what a delisted holding was worth: an acquisition pays about
the last price, a bankruptcy about nothing. This reads the answer from EDGAR for
**all 1,737 died-population securities** — every one `backtest_survivorship.py`
can sample, across every `--offset`.

### The answer

| cause | securities | share | what a holder got |
|---|---|---|---|
| acquired — proposal *and* completion | 931 | 53.6% | paid |
| acquisition indicated — one of the two | 247 | 14.2% | paid |
| extinguished — Form 15 certifies 0–1 holders | 191 | 11.0% | paid |
| **bankrupt — confirmed by the document** | **70** | **4.0%** | **wiped out** |
| kept reporting after the prices stopped | 50 | 2.9% | unknown |
| distress indicated | 119 | 6.9% | unknown |
| deregistered, unexplained | 88 | 5.1% | unknown |
| unresolved | 41 | 2.4% | unknown |

**78.8% were bought out. 4.0% are confirmed bankruptcies. 17.2% are unexplained.**
The mix barely moves between the four backtest samples: 298–320 paid, 12–20
wiped out and 65–82 residual in each died arm of 400.

**This undercuts §15's robust number.** The ~50-point gap assumed every dead
holding went to zero, and for four in five of them the filings say it did not.
The evidence sits near the other end of the bracket, where §15 found the gap
small and not established. What survivorship bias is for this rule is now a
question the next backtest can answer, not one it has to assume.

### How it was read

* **8-K item numbers are in the filing's header, in both schemes** — measured on
  sec.gov before building on it: `1.01 8.01 9.01` on a 2005 filing, `5 7` on a
  2002 one. The full-index has none, which is why `tradeit.edgar.evidence` marks
  every 8-K signal `requires_document_text`. All **14,445** headers in the
  windows were read; none left unread.
* **Transaction pointers** from the index: merger proxies, tender offers,
  going-private schedules, deal communications. A proposal alone is only
  *indicated*; a proposal plus a completion makes *acquired*.
* **Form 15 holders of record**, from 1,660 documents. After a merger the only
  holder is the acquirer, so zero or one means the public class is gone.

### What the first versions got wrong, all found before the numbers were used

**A header can lie.** General Instrument's 1999 8-K header declares *bankruptcy*;
its text says "Item 2. ACQUISITION OR DISPOSITION OF ASSETS", and Motorola
bought it nine months later. So a bankruptcy header counts only when the
document agrees. Across 119 bankruptcy 8-Ks, **five headers are genuinely
wrong**: General Instrument, Vivid Technologies, CFI ProServices, AMSTAR, and
one of Comdisco's.

**The check that caught it was wrong twice before it was right.** Exact headings
missed "Bankruptcy **and** Receivership" (Kentucky Electric Steel), "Item 1.03
**and Item 8.01**" (Federal-Mogul), "Item 3. Bankruptcy." (Webvan, Bio-Plexus),
"RECEIV**O**RSHIP" (Winstar), "**Item 2.** Bankruptcy or Receivership" (Kmart)
and an untitled "Item 3." (Luminant). A document now confirms a bankruptcy by a
bankrupt-titled item-3/1.03 heading **or** a stated Chapter 7 or 11 petition,
and merger-agreement boilerplate ("laws … relating to bankruptcy, insolvency")
still fails. Each of those phrasings is a test.

**An item says what kind of proceeding, never whose.** Conning's 8-Ks disclosed
its *parent's* insurance receivership; MetLife then tendered for Conning's
shares and paid them. Nobody tenders for equity a bankruptcy wiped out, so an
offer to shareholders *after* the bankruptcy item overrides it — which happens
exactly once in 1,737. Seven other bankruptcies had a deal *before* the filing,
deals that collapsed first (Edge Petroleum's with Chaparral), and stay bankrupt.

**"Deregistered, unexplained" was mostly mergers** until Form 15s were read:
Dal-Tile certified ZERO holders, Lamar Capital 0, Eagle Bancshares 1 — their
merger papers were filed under the acquirers' CIKs. DSI Toys, in Chapter 11,
certified 25.

**Joint filings list several filers.** Equity Office's 8-Ks were filed with its
operating partnership, and reading only the first `<FILER>` refused 23 of the
first 702 headers.

### Precision, checked by name

A seeded random sample of eight from each acquisition class — PETCO's buyout,
Eskimo Pie, Flashnet, BHA Group, Primus Knowledge, and the rest — is **16 for 16**
against known history. All 70 bankruptcies were read by name and are the decade's
familiar failures: LTV, Lernout & Hauspie, Webvan, Winstar, Kmart, Finova,
Adelphia, Teligent, and bank receiverships in 2009.

**The residual is where the misses are**, and it is left as a residual rather than
forced: MMC Networks, iXL and SkyMall were acquisitions the filings did not reveal;
Vanguard Airlines and National Equipment Services went bankrupt more than six
months after their last price; most of the rest went dark or moved to the pink
sheets. 172 findings carry a *silent final year* flag — no periodic report in the
year before the last price — which marks doubt without changing the class.

### Two observations left open

**The corpus binds the listed EOP security to Equity Office's operating
partnership's CIK**, not the trust's. The joint filings make the classification
right either way — which is the "right answer by accident" pattern, and an
identity question for another day.

**The windows are research windows, not tuned thresholds** — twelve months
before the stop to six after for 8-Ks, eighteen months for transaction pointers
— and nothing here has measured how many findings move if they are widened.

### What it enables

A survivorship backtest with **per-security recovery**: paid holdings at their
last price, confirmed bankruptcies at zero, and only the 17% residual bracketed.
That is a change to a backtesting assumption, the delisting recovery, which the
engine now takes as one number for everybody, so it is proposed rather than
made.

**No result here is evidence of profitability** — the corpus gate still reads
`SURVIVOR_BIASED`.

---

## §17 — the survivorship gap with each dead company's actual fate — 2026-09-12

§16 classified why each dead company stopped trading. This puts that into the
backtest: a delisted holding recovers **its last price if the company was
bought out, zero if it went bankrupt**, and only the 17% residual keeps the old
assumption, run at both 0.0 and 1.0. The specification — classes, recoveries,
samples — was committed in `78caef7` before any run.

### The result

| sample | survivors | everybody | gap, residual 0.0 | gap, residual 1.0 |
|---|---|---|---|---|
| 0 | −0.10% | +0.64% | +0.74 | +0.74 |
| 1 | +16.47% | −16.46% | −32.93 | −32.93 |
| 2 | +29.41% | +22.51% | −6.90 | −6.90 |
| 3 | +33.89% | +30.01% / +30.75% | −3.88 | −3.14 |
| **mean** | | | **−10.74, sd 15.1** | **−10.56, sd 15.2** |

**The bracket collapsed.** §15 put survivorship bias anywhere between about zero
and 57 points depending on the recovery assumption. With the assumption replaced
by evidence, the unexplained residual moves the answer by **at most 0.74 points**,
in one sample of four.

**The gap is about −11 points on average and not established.** Three samples of
four put everybody below the survivors; one puts it above. With a standard
deviation of 15 across four samples, t ≈ −1.4. The honest statement is that
including the companies that died *probably* costs this rule something over the
decade, and **this measurement cannot say how much**.

**§15's robust ~50 points is retracted.** It came from booking bought-out
companies as total losses. It was robust in the sense of repeatable, and wrong in
the sense that mattered.

### Why, measured rather than explained

**Of 49 delisted exits across the four samples, 44 were buyouts** (acquired 31,
acquisition indicated 8, extinguished 5), 2 were residual, 3 were survivors that
went briefly silent — and **none was a bankruptcy**. The strategy was still
holding the companies that got bought, which is unsurprising for a trend rule: a
takeover lifts the price and keeps it there.

**The strategy never held a bankrupt company at the end.** Across the four
samples it traded bankrupt-class stocks 32 times, and every trade ended through
its own exits — 15 time stops, 12 stop-losses, 5 partial profits. The closest any
exit came to the stock's last price was **408 days** before it; the median was
1,392. An 8% stop leaves a failing company long before the company leaves the
exchange.

**So for this rule the recovery assumption was never about bankruptcies.** Every
dollar of §15's width came from what a buyout paid, which the filings state.
What the survivorship gap still measures is the cost of *trading* the companies
that later died, on the way down — stop-losses in stocks the survivors-only
universe never offered — and that is small, noisy, and real.

### What this does not change

**The gate still reads `SURVIVOR_BIASED`**, and nothing here lifts it. That
verdict is about coverage — the corpus holds 2.7% of the dated exits EDGAR knows
about — and a better recovery for the exits it does hold says nothing about the
ones it is missing.

**The conclusion is specific to a rule with a stop.** A buy-and-hold rule, or one
without a stop, would still be holding the bankrupt companies at the end, and for
it the 4% that went bankrupt would matter a great deal. Per-stock recovery
serves any rule; the small gap belongs to this one.

**No result here is evidence of profitability.** Every arm's Sharpe ratio, in
every sample, lies between −0.42 and +0.05; the question answered is how much
excluding the dead flatters the rule, not whether the rule works.

---

## §18 — `relative_strength`, measured by its real engine — 2026-09-12

Every earlier test of this factor used stand-ins (`momentum_21/126/252`). This
ran `RelativeStrengthEngine` itself — `compare`, `rank_cross_section` per
lookback, `score` — on four disjoint samples of 800 securities (400 survived,
400 died), 207,121 observations, 2000–2009. Specification and survival criteria
[pre-registered](prereg/RELATIVE_STRENGTH_2026-09-12.md) and committed in
`644dcad` before any forward return existed.

### Verdict: does not survive, at either horizon, under either benchmark

| horizon | IC (t) | spread | geometric edge 2000–04 | 2005–09 | declared sign | verdict |
|---|---|---|---|---|---|---|
| 21 | −0.0021 (−0.93) | mean −2.62%, median +0.20% | +0.47%/yr | −0.96%/yr | 1 of 4 | `NOT_DETECTABLE` |
| 63 | **+0.0129 (+3.32)** | mean −2.32%, median +0.82% | **+6.85%/yr** | −0.88%/yr | **4 of 4** | `OUTLIER_DEPENDENT` |

At 63 sessions two of the three criteria come close: the IC clears the 26-trial
hurdle of 2.01, and all four samples agree on its sign. It fails the other: the
quantile spread is negative by mean and positive by median, and the geometric
edge belongs entirely to 2000–2004 and reverses after. **That is §13's shape
exactly** — `pattern_quality` failed the same way, and the standard does not move.

### Why it fails: the engine blends opposite signals

Diagnostics — each lookback's percentile alone, **not trials**, and so not
promotable from this run:

| lookback | engine weight | IC, 21 sessions | IC, 63 sessions |
|---|---|---|---|
| 20 | 0.15 | **−0.0330 (t −14.89)** | −0.0211 (t −5.43) |
| 60 | 0.25 | −0.0144 (t −6.48) | +0.0003 (t +0.08) |
| 120 | 0.30 | +0.0046 (t +2.09) | +0.0193 (t +4.96) |
| 250 | 0.30 | **+0.0189 (t +8.49)** | **+0.0252 (t +6.46)** |

The 20-session rank **reverses**: last month's leaders underperform next month.
That is the best-known short-horizon result in the literature, and the
pre-registration flagged it in advance from `momentum_21`'s stable negative sign.
The 250-session rank carries classic twelve-month momentum. The engine gives
40% of its weight to the two short horizons and 60% to the two long ones, and
the composite washes out what the long ones carry.

**The engine's lookback weights are a strategy parameter**, declared and never
validated, and the evidence now says one of them points the wrong way. Changing
them is a scoped proposition, not a consequence of this run.

### The benchmark substitution, measured

SPY is not in the corpus; the run used QQQ and, per amendment 1, a flat
benchmark on the same calendar. The scores differ on 97.7% of observations —
the engine matches each security's own sessions, so gaps make the benchmark
matter — but the rank correlation between them is **0.9956**, and **every
criterion lands the same way under both**. The substitution does not change the
verdict, so SPY is not needed for it.

The check that established this fired twice on the way. First it caught the
script, which gave the benchmark too short a window and let the engine cut the
start off gappy securities' histories. Then, with that fixed, it caught the
pre-registration's own argument that a common benchmark cannot move a rank. Both
are recorded in the registration.

### Where this leaves the weights

`relative_strength` joins `pattern_quality` and `fundamental_quality` as
**measured by its real engine and null**. `volume_accumulation` is now the only
weighted factor never measured directly. §12's reasoning holds, and holds
harder: three measured nulls and one unmeasured factor is still no evidence
that any factor beats another, so **the weights stay equal at 0.25**.

### The lead, and why it is not a finding

The 250-session component alone (t +6.46 at 63 sessions) is the strongest single
number any factor has produced here. **It is a diagnostic from a run registered
for the composite**, it was looked at after the fact, and on this decade it can
no longer be tested cleanly. The honest next test pre-registers it alone and runs
it on **2010–2019**, data this study never read — which is how
`relative_volume_20`'s in-sample pass was exposed as nothing.

**Ledger: 24 → 26 trials.** No result here is evidence of profitability.

---

## §19 — the 250-session rank, out of sample on 2010–2019 — 2026-09-12

§18's diagnostic — the engine's 250-session percentile alone — was the best
number this project had produced, and it had no standing: it was seen after the
fact in a run registered for the composite. This is its registered test, on a
decade no momentum result had been read on. Criteria committed in `3ad2c16`
before any 2010s return was computed. **229,680 observations**, four disjoint
samples of the 2010 population, both benchmarks.

### Verdict: does not survive

| horizon | IC (t) | spread | 2010–14 edge | 2015–19 edge | sign | verdict |
|---|---|---|---|---|---|---|
| 21 | **+0.0466 (+22.22)** | mean −1.30%, median **+1.65%** | −2.24%/yr | +1.01%/yr | **4 of 4** | `OUTLIER_DEPENDENT` |
| 63 | **+0.0594 (+16.16)** | mean −0.61%, median **+4.29%** | −0.65%/yr | +0.73%/yr | **4 of 4** | `OUTLIER_DEPENDENT` |

Criterion 3 passes emphatically. Criteria 1 and 2 fail, under QQQ and FLAT
alike. By the rules as written: **it does not survive.**

### What did replicate, and it is the first thing that has

**The direction held and the relationship got stronger.** In sample it read
IC +0.0189 (t +8.49) at 21 sessions and +0.0252 (t +6.46) at 63; out of sample,
on data it had never seen, **+0.0466 (t +22.22)** and **+0.0594 (t +16.16)** —
the same sign in every sample, under both benchmarks. Set against
`relative_volume_20`, whose in-sample pass reversed to nothing out of sample,
this is the opposite outcome, and **no other signal in this document has
replicated at all.**

The typical outcomes separate cleanly at 63 sessions:

| | top quintile | bottom quintile |
|---|---|---|
| median return | **+0.67%** | **−3.62%** |
| win rate | **51.6%** | **43.3%** |
| mean return | +2.70% | **+3.31%** |
| 99.9th percentile | +308.8% | **+713.8%** |

### Why it fails anyway

**The losers' lottery tickets own the mean.** The bottom quintile loses more
often and by more — and its best 0.1% of observations contribute +0.81 points of
its +3.31% mean. Drop the best 1% of each quintile and the mean spread flips from
−0.61% to **+1.78%**. That is precisely what `OUTLIER_DEPENDENT` is for, and the
verdict machinery reached it without being told.

**A long-only top quintile is not where the separation lives.** Compounded —
diagnostic, not a registered criterion — the top quintile runs at **−7.16%/yr**
and the bottom at **−31.58%/yr** over the decade, a 24-point difference holding
in both halves (+24.8 and +23.7). But the top quintile is roughly the universe
average, which is why criterion 2, which compares top against *all*, fails. **The
information is in avoidance, not selection.** On this survivorship-honest
universe both ends lose money compounding; the signal says which end loses far
more.

### The benchmark, again

QQQ and FLAT differ on 81.2% of observations, rank-correlate at **0.9950**, and
return identical verdicts on every criterion at both horizons. SPY is still not
needed.

### What this licenses, and what it does not

**It does not move a weight.** `relative_strength`'s composite was measured and
was null (§18); this component failed its registered test. The weights stay
equal at 0.25, and `volume_accumulation` remains the only factor never measured
directly.

**The obvious next question is a gate, not a weight** — exclude the bottom
quintile rather than tilt toward the top, the same move `breakout_confirmation`
made in §11. The diagnostic above is where that idea comes from, so **it cannot
also be its evidence**: it would need its own pre-registration and a period
neither decade has touched. 2020–2024 is unread, and the corpus runs to 2026.

**Nothing here is evidence of profitability.** Both quintiles compound
negatively, the gate is untested, and the corpus gate still reads
`SURVIVOR_BIASED`.

**Ledger: 26 → 28 trials.**

---

## §20 — the 250-session rank as an exclusion gate, 2020–2024 — 2026-09-12

§19 found the information is in avoidance: compounded over 2010–2019 the bottom
quintile of `pct_250` ran at −31.6%/yr against −7.2%/yr for the top. That was a
diagnostic, so it could not be its own evidence. This is its test — the baseline
rule run twice on each of four disjoint samples, refusing candidates in the
bottom quintile on the session they are decided, or not. Criteria committed in
`94910d8` before any 2020s run.

### Verdict: the gate fails

| recovery | gated beat ungated | mean CAGR change | mean drawdown change | verdict |
|---|---|---|---|---|
| 1.0 | 2 of 4 | **−0.99%** | +0.05 pp | **fails** |
| 0.0 | 2 of 4 | **−1.68%** | +2.02 pp | **fails** |

Criterion 1 fails under both, criterion 2 fails under both, criterion 3 passes
at 1.0 and fails at 0.0. The gate refused 13.1–14.8% of candidates — not 20%,
because a moving-average crossover already selects names that have been rising,
so fewer of them sit in the weakest fifth. Two or three candidates per sample
had no rank and were not gated.

### And the test could not have established the opposite either

| recovery | effect on total return, by sample | mean | sd |
|---|---|---|---|
| 1.0 | −8.9, +12.3, +20.6, **−34.9** | −2.7 pp | **24.8 pp** |
| 0.0 | −9.1, +3.1, +3.6, −16.6 | −4.7 pp | **9.8 pp** |

**The spread between samples is several times the effect being looked for**, and
the paired difference's own t-statistic is −0.22 at recovery 1.0 and −0.96 at
0.0: not distinguishable from no effect in either direction.
§17 measured why: a capacity-limited portfolio is path-dependent, so one
different admission reshuffles every later decision — and a gate that refuses
165 of 1,227 candidates changes the path everywhere. With four samples and a
standard deviation of 25 points, this design could not have detected a
one-point-a-year improvement had there been one.

So the honest reading is two-sided: **the gate did not help, and this test was
not capable of proving it did.** The registered criteria decide the verdict —
that is what registering them is for — but the width belongs in the record next
to it.

### What this closes and what it leaves

**The avoidance idea is not supported at portfolio level.** A 24-point-a-year
gap between quintiles of a ranked cross-section did not become a portfolio
improvement for this rule in this period. The most likely reasons are visible in
the run and are not tested here: the rule's own trend filter already excludes
most of what the gate would refuse, and the 8% stop removes a failing holding
long before the rank does (§17 measured that exits came a median of 1,392 days
before a bankrupt company's last price).

**No threshold hunting.** The registration fixed 0.20 and said no other
threshold would be tried on this data. None was.

**A design lesson for the next portfolio test.** Four samples is too few for an
effect of this size. The 2020 population supports eight disjoint samples at cap
150, and a design that needs to resolve one point a year should be sized against
the 9.8–24.8-point spread measured here rather than against hope.

**Ledger: 28 → 29 trials.** Weights unchanged; `volume_accumulation` remains the
only weighted factor never measured directly. Nothing here is evidence of
profitability, and the corpus gate still reads `SURVIVOR_BIASED`.

---

## §21 — `volume_accumulation`, the last unmeasured factor — 2026-09-12

The fourth and final weighted factor, measured on the same decade and the same
four universes as `pattern_quality` (§13) and `relative_strength` (§18).
Criteria [pre-registered](prereg/VOLUME_ACCUMULATION_2026-09-12.md) in `d930876`
before the run. **236,988 observations.**

### First, what the "real engine" turned out to be

**There isn't one.** No module, no scorer, nothing in the codebase reads the
name `volume_accumulation` except the weights dictionary. What exists is the
indicator registry, and two of its features are accumulation claims in their own
words: `volume_momentum` ("change in average volume — accumulation building or
fading") and the OBV feature.

**And the OBV feature was broken.** The registry published
`slope(abs(obv) + 1, lookback)`, and the absolute value destroys the only thing
OBV carries. Measured: a security closing **up** every session for forty
sessions and one closing **down** every session both returned `+0.052632`.
Replaced in `920f703` by `obv_trend` — net signed volume over the window as a
share of the volume traded in it — with the sign, the bounds and the split
invariance each a test. **A factor nobody had measured was carrying 25% of the
score with one of its two features unable to tell buying from selling.**

### The result

| signal | horizon | IC (t) | spread | halves | samples | verdict |
|---|---|---|---|---|---|---|
| `volume_momentum` | 21 | **+0.0428 (+20.67)** | mean **+2.94%**, median +0.83% | +20.05%/yr, +5.22%/yr | **4/4** | **`ECONOMICALLY_USEFUL`** |
| `volume_momentum` | 63 | **+0.0231 (+6.34)** | mean +2.68%, median +1.12% | +3.66%/yr, +0.79%/yr | **4/4** | **`ECONOMICALLY_USEFUL`** |
| `obv_trend` | 21 | −0.0116 (−5.60) | mean −0.73% | −1.04%/yr, −2.49%/yr | 0/4 | `DETECTABLE_NOT_PROFITABLE` |
| `obv_trend` | 63 | +0.0049 (+1.35) | mean +0.46% | +1.94%/yr, +0.36%/yr | 3/4 | `NOT_DETECTABLE` |

**`volume_momentum` passes all three criteria at both horizons — the first
signal in this document to pass every registered criterion.** Mean and median
spreads agree in sign, so it is not outlier-dependent; the geometric edge is
positive in both halves with confidence intervals excluding zero at 21 sessions;
all four samples agree. Net of costs it reads **+32.7%/yr** at 21 sessions.

### Why that number should not be celebrated yet

**`relative_volume_20` produced `ECONOMICALLY_USEFUL` and +32.7%/yr net in
sample too, and out of sample its sign flipped and it went to −0.2%/yr.** The
identical figure is a coincidence of the cost arithmetic — its spread was −8.39%
at 63 sessions against +2.94% here — and it is stated so nobody reads the two as
the same run. But the *shape* is the same shape: a volume signal, a large
in-sample net, a factor that has never survived contact with unseen data. §8
said it plainly: one signal passing every in-sample gate out of twenty trials is
what chance produces at the 5% level. This is one signal passing out of
thirty-three.

### The contrast inside the factor is the finding

**Direction-blind volume growth predicts; signed buying pressure does not.**
`volume_momentum` does not know whether the volume was buying or selling, and it
works. `obv_trend` carries exactly that sign, and at 21 sessions it points the
*wrong* way in all four samples.

So whatever is being measured, **it is not accumulation.** Rising turnover is
attention — a name being traded more than it was — and the factor's own name
asserts something the evidence does not support. That is worth more than the
t-statistic: it says the 25% weight is labelled wrong even if a signal underneath
it is real.

The two signals rank-correlate **+0.045** across 236,888 shared observations:
they are very nearly independent measurements, not two views of one thing.

**It is not only microcaps** (diagnostic, not a criterion). The 21-session IC
runs +0.0510 under $5, +0.0435 from $5 to $20, and **+0.0258 above $20**
(t +7.56, geometric quintile spread +1.1% per hold). The effect weakens with
price but survives in every band.

### What this changes

**No weight moves.** §8's lesson is the governing one: the weights were not
changed on `relative_volume_20`'s in-sample pass, and the system was right not
to. `volume_momentum` has earned an out-of-sample test, not a weight.

**The factor is now measured**, and all four weighted factors have been through
their real engines: `pattern_quality` null (§13), `relative_strength` null (§18),
`fundamental_quality` null (§10), and `volume_accumulation` carrying one signal
that passed in sample and one that failed — with the passing one measuring
something other than what the factor is called.

**Ledger: 29 → 33 trials**, hurdle 2.112. No result here is evidence of
profitability; the corpus gate still reads `SURVIVOR_BIASED`.

---

## §22 — `volume_momentum` out of sample: it did not replicate — 2026-09-12

§21's pass was the first in this document, and §8's precedent said what to do
with it: test it on data it has never seen. Criteria committed in `72006b0`
before any 2010s run of this signal. **235,640 observations**, four disjoint
samples of the 2010 population.

### It failed, and not narrowly

| | in sample 2000–2009 | out of sample 2010–2019 |
|---|---|---|
| IC, 21 sessions | **+0.0428 (t +20.67)** | **+0.0040 (t +1.95)** |
| IC, 63 sessions | **+0.0231 (t +6.34)** | −0.0005 (t −0.13) |
| samples with the declared sign | **4 of 4** | **2 of 4** |
| geometric edge, first half | **+20.05%/yr** | **−11.38%/yr** |
| geometric edge, second half | **+5.22%/yr** | **−8.76%/yr** |
| net of costs, 21 sessions | **+32.7%/yr** | +3.4%/yr |
| verdict | `ECONOMICALLY_USEFUL` | `NOT_DETECTABLE` |

All three criteria fail at both horizons. **The geometric edge did not merely
weaken — it reversed**, from +20.05%/yr to −11.38%/yr in the first half and from
+5.22%/yr to −8.76%/yr in the second, with confidence intervals excluding zero on
the wrong side. Buying the top quintile by volume growth *lost* to the universe
in both halves of the out-of-sample decade.

### This is `relative_volume_20` again, and that was foreseen in writing

| | `relative_volume_20` (§8) | `volume_momentum` (§21–22) |
|---|---|---|
| in-sample verdict | `ECONOMICALLY_USEFUL` | `ECONOMICALLY_USEFUL` |
| in-sample net | +32.7%/yr | +32.7%/yr |
| out-of-sample | IC +0.009, t +0.93 | IC +0.004, t +1.95 |
| outcome | **rejected** | **rejected** |

The identical net figure remains a coincidence of the cost arithmetic; the
outcome is not. **Two volume signals have now passed every in-sample gate and
died on unseen data.** §21 said in advance that this was the shape to distrust,
and the registration for this test said in advance what a failure would mean.

### The state of the four weighted factors

| factor | measured by its real engine | result |
|---|---|---|
| `relative_strength` | §18 | null — the engine blends a reversal horizon with a momentum one |
| `pattern_quality` | §13 | null — present on 99.6% of scan points; edge lived in one half |
| `fundamental_quality` | §10 | null — no detectable relationship |
| `volume_accumulation` | §21–22 | one signal wrong-signed, one passed in sample and **failed out of it** |

**Every weighted factor has now been measured directly, and none survives.** The
weights stay equal at 0.25 — for the fourth time on evidence rather than for want
of it.

### What was gained, since it was not a signal

**A defect that had been shipping.** `obv_slope` could not distinguish
accumulation from distribution (§21), and it was found only because the hunt for
this factor's engine went looking.

**A name that does not describe its contents.** The direction-blind measure
passed in sample and the signed one failed, so what `volume_accumulation` was
weighting was never accumulation. That remains true whether or not the signal
replicated.

**A second demonstration that the process works.** The trial ledger, the
pre-registration and the out-of-sample discipline caught a `+32.7%/yr`
in-sample result — one that would have been extremely tempting to act on — for
the second time. Had the weights moved on §21, the system would now be carrying
a factor whose out-of-sample edge is *negative* in both halves of a decade.

**Ledger: 33 → 35 trials.** No result here is evidence of profitability.

---

## §23 — the gate's denominator changed; nothing above it did — 2026-09-12

Every section from §13 to §22 closes by saying the corpus gate reads
`SURVIVOR_BIASED`. **It now reads `PARTIALLY_SURVIVORSHIP_CORRECTED`, and not
one of those results changes.** This section exists so that is impossible to
misread.

### What happened

`bounded_coverage` divided the priced dead companies by **every registrant EDGAR
has ever seen** — 96,822, including 50,354 that never reported under the
Exchange Act and 12,143 the pipeline had determined *did not exit at all*. §5 of
`EDGAR_DELISTING_DENOMINATOR.md` defines it over dated exit **entries**. §7e
recorded the discrepancy on 2026-09-05, the owner kept the pessimistic reading
because nothing depended on it, and a later measurement showed the kept reading
was **unreachable**: perfect identity resolution at the observed price-hit rate
tops out near 23.8% against a 0.25 threshold, with a denominator that grows
every quarter as EDGAR grows.

Measured 2026-09-12, on the same corpus, the same day:

| reading | coverage | grade |
|---|---|---|
| §5's denominator (adopted) | 8,079 / 21,618 = **37.4%** | `PARTIALLY_SURVIVORSHIP_CORRECTED` |
| previous (still published beside it) | 8,079 / 96,822 = 8.3% | `survivor_biased` |

The corpus did not improve that morning. The arithmetic did. Priced dated exits
did rise 5,423 → 8,079 in a week (**+49%**), which the old ratio hid behind a
crawl from 4.72% to 8.34%.

### Why no factor verdict moves

**A denominator is not evidence about a signal.** §13's `pattern_quality` fired
on 99.6% of scan points; §18's `relative_strength` blended a reversal horizon
against a momentum one; §22's `volume_momentum` reversed out of sample. None of
those findings referenced the gate, and none is softened by it. The weights stay
equal at 0.25.

**And the standing rule did not lift with the grade.** Its trigger — "lifts when
the gate says something other than `SURVIVOR_BIASED`" — was written when that was
the only grade the gate could return, so it would have fired on the arithmetic.
It now lifts at `MATERIALLY_SURVIVORSHIP_CORRECTED` plus a recorded decision.

**The code enforced that, not just the prose.** `admissibility()` permitted
evidence for every class except `SURVIVOR_BIASED`, so the change would have
silently unlocked promoting a strategy above `BACKTESTING` — on a corpus where
**nearly two thirds of the companies EDGAR shows exiting are still unpriced**.
`PARTIALLY_SURVIVORSHIP_CORRECTED` now refuses evidence too, and a test asserts
the refusal survives, because the regression to fear is a promotion unlocked by
arithmetic rather than by data.

### What is actually still missing

37.4% is a real correction and an incomplete one. The remaining gap is
13,539 dated exits with no price, of which 4,228 have a ticker resolved and
9,311 have no identity yet. **Coverage above 45% — the next grade, where the
rule does lift — needs roughly 1,650 more priced exits**, which is the first
target in this project that is both concrete and reachable.

---

## §24 — `obv_trend` out of sample: the sign replicated, the edge did not — 2026-09-12

§22's registration said this signal would not be carried forward, because it
failed in sample. That was right about a *neutral* failure and wrong about this
one: `obv_trend` failed by being **consistently negative** against a declared
positive direction — the shape `relative_volume_20` had, which §8 tested rather
than assumed. Registered in `9dedd93` with the direction taken from the
in-sample result and **charged as a derivation, 2 trials per horizon**, before
its out-of-sample numbers had been computed by anyone. They existed in the §22
observations and had never been passed to a verdict.

### The strongest replication in this document, and it still fails

| | in sample 2000–2009 | out of sample 2010–2019 |
|---|---|---|
| IC, 21 sessions | −0.0116 (t −5.60) | **−0.0337 (t −16.27)** |
| samples with the negative sign | **4 of 4** | **4 of 4** |
| quantile spread | — | mean −1.49%, median −0.77%, spread t −8.07 |
| verdict | `DETECTABLE_NOT_PROFITABLE` | **`ECONOMICALLY_USEFUL`**, +15.1%/yr net |
| criterion 2, both halves | — | **fail**: +8.00%/yr, then −2.51%/yr |
| 63 sessions | +0.0049 (t +1.35) | +0.0048 (t +1.33), 0 of 4 — nothing |

**Eight samples across two decades, the same sign in every one, three times
stronger out of sample than in.** Nothing else here has done that:
`relative_volume_20` reversed, `volume_momentum` collapsed, `pattern_quality`
flipped with the statistic chosen, and the 250-session rank replicated but with
a spread that rested on outliers.

**And it does not survive.** Criterion 2 fails exactly where §13 and §19 failed:
the compounded edge of the favoured quintile is +8.00%/yr in 2010–2014 and
−2.51%/yr in 2015–2019. A relationship that reverses between halves of its own
out-of-sample decade is not a tradeable edge, whatever its t-statistic. The
standard does not move because this is the best candidate yet.

### What it says about the factor

**`volume_accumulation` is weighted positively, and the only measure of actual
accumulation says the opposite.** Names bought on balance — more volume on up
days than down — underperform over the following month, consistently, in both
decades. §21 found the *direction-blind* measure passed in sample; §22 found it
died out of sample; §24 finds the *signed* one replicates inversely.

Three signals, one factor, and not one of them supports the thing the factor is
named after. The weight stays at 0.25 — by §12's argument, which is that no
factor has been shown superior to another, and which four measured nulls have
now reinforced rather than weakened.

**What would make this actionable** is not another study of the same shape. The
21-session inverse relationship is strong enough and stable enough in *sign*
that the honest next question is a portfolio one: does refusing the top quintile
of signed accumulation improve a rule that trades? §20 tested that shape for the
250-session rank and found the paired difference swamped by path noise at four
samples — so it would need the eight-sample design §20 recommended, and it is a
proposition rather than a run.

**Ledger: 35 → 39 trials**, hurdle 2.179. No result here is evidence of
profitability; the corpus gate reads `PARTIALLY_SURVIVORSHIP_CORRECTED` and the
standing rule against believing its numbers has not lifted.
