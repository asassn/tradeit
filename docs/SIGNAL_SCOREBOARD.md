# Signal scoreboard

**As of 2026-09-13, code `1a0154f`.** A status record, not a findings document —
[`SIGNAL_RESEARCH_01.md`](SIGNAL_RESEARCH_01.md) holds the methodology and the
reasoning. **Every number here is measured and every one will rot**; re-run the
studies rather than quoting this later.

> **Read every section below with this, 2026-09-15.** Sections up to and
> including §28 read prices through `price_series` and `CorpusSessionData`
> **before** those paths learned that some EODHD `raw` closes are already
> split-adjusted (`RESEARCH_01_DATA_DICTIONARY.md` §0.1a). At 671 recorded
> splits in 523 securities a split was applied twice — a 3-for-2 read as a 50%
> move nobody earned — and 1,872 more are now withheld as contradicted. No
> **§26, §27 and §18 have since been re-run — see §29 and §30 — and none moved**:
> every number softened, by 3–5% in §26 and rather more in §18, and no verdict
> changed. The rest have not been re-run. Findings that rest on large, many-sample effects are unlikely
> to be reversed; any that rest on a few extreme returns could be.

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
| `realized_vol_60` | *(none — unweighted)* | low volatility outperforms | **IC −0.060, t −10.01** | **yes, 4/4** | **IC −0.0790, t −38.25, 4/4** | **PASSES a registered out-of-sample test on tradeable securities (§35: 2020–2025, above $1M/day, IC −0.137, t −3.32).** §27's "vanishes above $1M/day" rested on the §0.9 volume defect (§34). Concentrated in lower-priced stocks. **As a portfolio it is NOT profitable (§37): lost to a random portfolio in 4 of 4 samples, −4.46 pp/yr.** A signal, not a strategy |
| `atr_percent_14` | *(none — unweighted)* | low volatility outperforms | **IC −0.078, t −7.46** | **yes, 4/4** | **IC −0.0790, t −38.27, 4/4** | same effect as the row above (rank correlation +0.91); not separately re-tested in §35, which registered one signal to spend one trial |
| `relative_volume_20` | volume_accumulation 0.25 | *derived* | IC −0.069, t −4.54 | no — flipped | **IC +0.009, t +0.93** | **REJECTED.** Sign flipped, net +32.7%/yr → −0.2%/yr |
| `momentum_21` | relative_strength 0.25 | one-month reversal | IC −0.016, t −2.15 | yes, 4/4 | not run | weak but consistent; never economically useful |
| `momentum_126` | relative_strength 0.25 | 6-month momentum | IC +0.021, t +3.00 | yes, 4/4 | not run | weak but consistent; never economically useful |
| `momentum_252` | relative_strength 0.25 | 12-month momentum | IC +0.023, t +3.88 | no — flipped | not run | inconsistent across specifications |
| `dist_from_sma_200` | pattern_quality 0.25 | trend following | IC +0.015, t +2.58 | no — flipped | not run | inconsistent |
| `dist_from_sma_50` | pattern_quality 0.25 | trend following | IC −0.024, t −2.77 | no — flipped | not run | **contradicts its declared prior twice.** Not flipped — see §7 |
| `rsi_14` | pattern_quality 0.25 | mean reversion | IC −0.015, t −1.70 | no — flipped | not run | never detectable |
| `sector_strength` | ~~sector_strength 0.10~~ → retired | **strong sectors outperform** | 2000s IC +0.026 t +2.93; 2010s IC −0.018 t −2.48 | **no — reverses by decade** | n/a | **significant in both directions.** Worse than a null: the relationship inverts |
| `volume_momentum` | volume_accumulation 0.25 | rising volume is accumulation | **IC +0.043, t +20.67** | **yes, 4/4 samples** | **IC +0.004, t +1.95** | **REJECTED.** Passed every criterion in sample, net +32.7%/yr → +3.4%/yr, geometric edge +20.1%/yr → **−11.4%/yr** — see §21, §22 |
| `market_cap` | *(none — unweighted)* | larger capitalisation outperforms | not run | n/a | **IC −0.0002, t −0.07** | **REJECTED.** Nothing, at a test resolving 0.0073. Closes the §26 line — see §27 |
| `obv_trend` | volume_accumulation 0.25 | accumulation, **signed** | IC −0.012, t −5.60 | **consistently NEGATIVE, 4/4** | **IC −0.034, t −16.27, 4/4** | **replicated and strengthened, inverted to the factor's own prior.** Fails half-period consistency as a signal (§24) and as an exclusion gate (§25); see §21 |

### Factors that used to carry weight and no longer do

| scoring factor | weight | why not |
|---|---|---|
| `breakout_confirmation` | ~~0.20~~ → **gate** | **retired as a weight 2026-09-10**; now a conditional gate — see §11 |
| `sector_strength` | ~~0.10~~ → **removed** | measured 2026-09-10 and retired: the relationship reverses by decade. `issuer_sic_observations` does classify 12,871 issuers, so the factor was computable — it was dropped on its result, not for want of data |

**None are left.** Every factor still carrying weight has now been measured:
`fundamental_quality` in §10, `pattern_quality` in §13, `relative_strength` in
§18, `volume_accumulation` in §21, §22 and §24. **All four returned nulls**,
which is why §12 could not use evidence to separate them.

## Scoring weights: two factors retired, and the rest set equal

Two removals on 2026-09-10 — `sector_strength` on the measured reversal below,
`breakout_confirmation` to a gate in §11 — left four weighted factors. §12 then
re-derived all four **equal at 0.25**, because no evidence separates them:

| factor | weight | validated? |
|---|---|---|
| `relative_strength` | 0.25 | **measured, null — §18** |
| `pattern_quality` | 0.25 | **measured, null — §13** |
| `fundamental_quality` | 0.25 | **measured, null — §10** |
| `volume_accumulation` | 0.25 | **measured, null — §21, §22, §24** |

**Read `src/tradeit/strategy/config.py`, not this table.** It is the live answer
and carries the three alternatives §12 rejected.

**Equal is not a finding, it is the absence of one.** Every one of the four has
now been run through its real engine and none predicts returns on this corpus,
so nothing licenses weighting any of them above another. **The strategy digest
changed** with the weights, which is correct: this is a different strategy, and
results tied to the old digest belong to the old one.

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

### Addendum 2026-09-16 — a second source agrees, and names most of the residue

§16 read one kind of evidence from one source: 8-K item numbers, Form 15 holder
counts and bankruptcy headings, all from the companies' own EDGAR filings.
Sharadar publishes lifecycle events of its own — `bankruptcyliquidation`,
`acquisitionby`, `regulatorydelisting`, `voluntarydelisting` — and the month
bought for prices answers this for free. **1,617 of the 1,737 classified
securities carry a Sharadar row under the same CIK.** Nothing was written to the
corpus; this is a comparison.

| EDGAR says | bankrupt | acquired | delisted by exchange | delisted voluntarily | delisted, unstated | total |
|---|---|---|---|---|---|---|
| acquired | 18 | **879** | 9 | 1 | 8 | 915 |
| acquisition_indicated | 22 | **182** | 7 | 10 | 8 | 229 |
| extinguished | 15 | **161** | 3 | 1 | 4 | 184 |
| distress_indicated | **39** | 5 | 14 | 16 | 5 | 79 |
| bankrupt | **68** | 1 | 1 | 0 | 0 | 70 |
| deregistered_unexplained | 18 | 22 | 9 | 7 | 4 | 60 |
| kept_reporting | 19 | 8 | 18 | 0 | 2 | 47 |
| unresolved | 17 | 12 | 0 | 3 | 1 | 33 |

**Where both speak plainly they agree.** 879 of 915 `acquired` (96.1%) and 68 of
70 `bankrupt` (97.1%). That is independent corroboration of the two categories
§17's recovery assumption rests on, from a source that never saw the filings.

**It names most of what EDGAR could not.** Every one of the 60
`deregistered_unexplained` and all 33 `unresolved` carry some Sharadar event —
40 and 29 of them an acquisition or a bankruptcy. Half of `distress_indicated`
(39 of 79) is named outright as bankruptcy. §16's 17.2% unexplained residue is
substantially explainable by a second source.

**The disagreements are mostly not contradictions.** 18 companies EDGAR reads as
acquired carry a Sharadar bankruptcy event, which is the ordinary shape of an
acquisition out of bankruptcy — both true, in that order. The 19 `kept_reporting`
with a bankruptcy event are the same shape seen from the other side: a company
whose security died while the registrant went on filing.

**What this does and does not license.** It does not change a single recorded
cause: EDGAR is primary, a vendor is not, and rewriting a classification on a
vendor event would undo the distinction §16 exists to make. What it supports is
confidence in §17's recovery assumption — bought-out holdings paid, bankrupt
ones did not — which the survivorship gap measurement depends on.

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

---

## §25 — signed accumulation as a gate: both tests fail, and the primary one was blind — 2026-09-12

**Registered in [`docs/prereg/OBV_TREND_GATE_2026-09-12.md`](prereg/OBV_TREND_GATE_2026-09-12.md)
at `abf7644`, hurdle corrected to the measured 2.209 at `f3881e0`, before any
2020s run of this signal.** §24 found `obv_trend`'s inverse 21-session
relationship in eight samples across two decades and refused it as a selection
signal because its compounded edge reversed between halves of the decade. A
gate is a different claim, and this tested it: refuse any candidate whose
`obv_trend` percentile is at or above 0.80, entry only, threshold fixed in
advance and never retuned.

### Verdict: both tests fail

| test | criterion | result |
|---|---|---|
| PRIMARY, 21 sessions | refused below admitted, \|t\| > 2.209 | **fail** — difference **+0.02%**, t +0.01 |
| PRIMARY, 63 sessions | as above | **fail** — difference **+0.15%**, t +0.01 |
| SECONDARY, recovery 1.0 | beats ungated in ≥ 6 of 8 | **fail** — 3 of 8, mean CAGR **−0.84%** |
| SECONDARY, recovery 0.0 | as above | **fail** — 5 of 8, mean CAGR **+1.57%** |

4,899 candidate observations across eight disjoint samples — every candidate
the baseline rule emitted that resolved a forward return at either horizon —
of which **1,549 were refused**. The point estimate at 21 sessions is not merely insignificant,
it is the wrong sign: refused candidates returned **+1.59%** and admitted ones
**+1.57%**.

### The registration named the wrong test as primary, and that is measurable

It said so in writing, which is what makes it checkable:

> PRIMARY — the candidate stream. … Thousands of observations rather than eight
> paired differences, and it tests exactly what a gate does.

**Those thousands are not thousands.** Candidates are emitted every session, so
a 21-session forward return overlaps the next twenty days of them. §18's
correction divides the count by the horizon, and 4,778 usable observations
become **228 effective ones**. The resolution that follows is printed beside
every result in the verdict script, pass or fail:

| | smallest difference it could resolve | §24's measured effect |
|---|---|---|
| 21 sessions, registered correction | **9.39%** | −1.49% |
| 21 sessions, sessions as clusters | **2.23%** | −1.49% |
| 63 sessions, registered correction | 32.85% | — |
| 63 sessions, sessions as clusters | 4.48% | — |

**Under either treatment of dependence the primary test could not have detected
the effect it was registered to look for** — by a factor of six under its own
correction, and still short under the most generous one available. That was
knowable in advance from §24's own number and nobody computed it.

**The secondary test was the sharper one.** Its paired CAGR difference has
se 0.52pp at recovery 1.0, so it resolves about **1.2 points a year**. The
registration predicted the opposite ordering and predicted it from §20's
24.8pp — a figure that is the spread of *five-year total* returns, converted
there into "about 17.5pp a year" as though it were annual. Measured
like-for-like on these eight samples, the total-return spread is **7.63%** at
recovery 1.0 and **11.25%** at 0.0. The design was roughly fifteen times
sharper than its own registration claimed.

So the honest reading of the primary test is **not** that the gate's
candidate-level claim was refuted. It is that the test was built to answer a
question it could not resolve, while the test called secondary could.

### What the secondary test did resolve: the sign depends on how the dead are priced

| recovery | gated beat ungated | mean CAGR change | t | mean drawdown change |
|---|---|---|---|---|
| 1.0 | 3 of 8 | **−0.84%** | −1.64 | −3.25 pp |
| 0.0 | 5 of 8 | **+1.57%** | +1.66 | −8.36 pp |

Neither reaches the hurdle, so neither is a finding on its own. **What the pair
shows is that the gate's sign flips with the delisting assumption**, and that is
a finding: what it is really trading is exposure to companies that stop trading.
Refusing them helps when they die worthless and hurts when they are bought. §16
classified the died arm as **78.8% acquired and 4.0% bankrupt**, so recovery 1.0
is the nearer of the two assumptions to this corpus — and under it the gate
loses money.

The drawdown column improves under both, by 3.25 and 8.36 points. That is what a
rule holding fewer positions does, not evidence the refused ones were bad.

### The rule it should have helped most

Candidates arrive with a mean `obv_trend` percentile of **0.599** and a median
of **0.65**, against 0.50 for a draw from the universe. **A moving-average
crossover systematically buys names that are being accumulated**, which is why
the 0.80 threshold refuses 32.3% of candidates rather than the 20% it would
refuse from the whole cross-section. §20 found the mirror image: there the
rule's trend filter left only 13–15% in the refused fifth, and the gate had
little to act on.

So this was the favourable case. If §24's inverse relationship were tradeable,
this is the rule and this is the threshold where it should have shown up.

### What this closes

**The obvious portfolio question after §24 is answered at portfolio level and
unanswered at candidate level**, and the registration is the reason the
difference is visible rather than a matter of opinion. No threshold other than
0.80 was tried, and none will be tried on this data.

**A design lesson, and it is the opposite of §20's.** §20 concluded that four
samples could not resolve a point a year and recommended eight. Eight delivered:
se 0.52pp. The mistake this time was assuming a large *count* of observations
means a large *effective* count — 4,899 candidate returns carry the information
of 228, because they overlap. **Count the effective observations before
declaring which test is primary**, not after it fails.

**Ledger: 39 → 42 trials**, hurdle 2.209 (`expected_max_of_normals(42)` =
2.2087, measured). Weights unchanged. No result here is evidence of
profitability; the corpus gate reads `PARTIALLY_SURVIVORSHIP_CORRECTED` at
38.7% and the standing rule against believing its numbers has not lifted.

---

## §26 — the volatility relationship survives its criteria, and is mostly a price effect — 2026-09-13

**Registered in [`docs/prereg/VOLATILITY_OUT_OF_SAMPLE_2026-09-12.md`](prereg/VOLATILITY_OUT_OF_SAMPLE_2026-09-12.md)
at `8754e1c`, before either signal had been computed on any session after
2009-12-31.** Four disjoint samples of the 2010 population, 3,069 securities,
**235,640 observations**, horizons 21 and 63, stride 21, quintiles, direction
declared NEGATIVE.

### Verdict: both signals survive at both horizons — the first thing here that has

| signal | horizon | IC (t) | bottom quintile, 2010–14 | 2015–19 | samples | verdict |
|---|---|---|---|---|---|---|
| `realized_volatility_60` | 21 | **−0.0790 (−38.25)** | **+16.83%/yr** | **+14.56%/yr** | 4/4 | **SURVIVES** |
| `realized_volatility_60` | 63 | **−0.0996 (−27.54)** | **+14.64%/yr** | **+15.09%/yr** | 4/4 | **SURVIVES** |
| `atr_percent` | 21 | **−0.0790 (−38.27)** | **+14.99%/yr** | **+13.14%/yr** | 4/4 | **SURVIVES** |
| `atr_percent` | 63 | **−0.1066 (−29.50)** | **+15.22%/yr** | **+14.75%/yr** | 4/4 | **SURVIVES** |

All three criteria hold in all four cells, every bootstrap interval excludes
zero, and **criterion 1 — the half-period test that retired `pattern_quality`,
`relative_strength` and `obv_trend` — passes with the two halves within two
points of each other** rather than reversing. Forty-six trials in, this is the
first signal to survive.

### Four things that must be read with it

**1. The two signals are one signal.** Rank correlation **+0.9076** across
235,414 shared observations. Four cells passed; they are not four independent
confirmations, they are one effect measured twice at two horizons. The ledger
charges 4 trials because 4 tests were run, but the evidence is nearer one.

**2. The aggregator decided the verdict, and the choice was registered first.**
Every cell grades `OUTLIER_DEPENDENT` on the arithmetic spread, which points
the **other way**:

| | mean spread | median spread | geometric verdict |
|---|---|---|---|
| `realized_volatility_60`, 21 | **+2.14%** | **−3.29%** | survives |
| `realized_volatility_60`, 63 | **+2.28%** | **−8.44%** | survives |

Mean and median disagree in sign in all four cells — exactly what §7 predicted
in writing, and the reason the registration made criterion 1 geometric before
any number existed. **A reader who prefers the arithmetic estimator should read
this section as a failure**, and that is why both are printed.

**3. Most of the information is avoidance, not selection.** The favoured
quintile gains far less than the shunned one loses:

| horizon | bottom (least volatile) | top (most volatile) |
|---|---|---|
| 21 | +1.40%/hold | **−4.28%/hold** |
| 63 | +3.65%/hold | **−10.80%/hold** |

§19 found the same shape in the 250-session rank and it is why the
top-quintile number was a declared diagnostic rather than a discovery.

**4. Costs do not kill it, and survivorship does not flatter it.** Under the
project's own `annual_cost_drag` — 6.50bps per leg plus 5.10bps commission at
the $9.81 median, 0.232% per round trip — the bottom quintile still compounds
**+4.24%/yr net at 21 sessions and +5.80%/yr at 63**, against a universe
compounding at **−8.56%/yr**. And the corpus's survivorship hole works
*against* this result rather than for it: died-arm securities run **1.22–1.26×**
the volatility of survivors, so the 61% of dated exits the corpus cannot price
are plausibly the volatile ones, and their absence flatters the high-volatility
bucket. That is an inference from the died arm, not a measurement of the
missing.

### And then the decomposition, which is the real finding

Volatility and price are rank-correlated **−0.6732**. The least-volatile
quintile has a median price of **$40.74**; the most-volatile quintile **$1.20**.
So the registered test may have been sorting on price. Double-sorted —
**diagnostic, no trials charged, nothing here promotable**:

**Does volatility still predict inside a price quintile?**

| price quintile | least volatile | most volatile |
|---|---|---|
| 1 (cheapest) | **+26.24%/yr** | **−27.27%/yr** |
| 2 | +11.86%/yr | −18.30%/yr |
| 3 | +4.38%/yr | −7.15%/yr |
| 4 | **−1.16%/yr** | **−1.22%/yr** — gone |
| 5 (priciest) | **−4.03%/yr** | **+7.17%/yr** — reversed |

**Does price still predict inside a volatility quintile?**

| volatility quintile | cheapest | priciest |
|---|---|---|
| 1 (least volatile) | −2.00%/yr | +4.02%/yr |
| 3 | −10.94%/yr | +12.56%/yr |
| 5 (most volatile) | **−29.41%/yr** | **+25.16%/yr** |

**Price is the more robust variable.** It predicts inside every volatility
quintile and strengthens monotonically. Volatility predicts only among cheap
securities: it **vanishes** in the fourth price quintile and **reverses** in the
fifth. Among securities above roughly $40, the low-volatility premium is
negative.

So the honest description of what passed is **not** "low volatility
outperforms". It is closer to **"cheap and volatile securities lose money, and
the two conditions are largely the same condition"** — which is a real and
tradeable-sounding fact, and also the oldest confound in cross-sectional equity
research.

### What this licenses, and what it does not

**It does not license a weight**, a strategy, or a position. The registration
said a pass would license a scoped proposition, and the decomposition narrows
even that: any proposition has to name **price** as the competing explanation
and test against it, not alongside it.

**The obvious next test is price itself, and it is not registered.** Sorting on
price alone gives −33.96%/yr for the cheapest quintile and +20.85%/yr for the
priciest — larger than the volatility sort produced. Nothing in this section
promotes that. It needs its own registration, its own trials, and its own
liquidity and cost treatment, because a $1.20 security's tradability is not the
$9.81 median's.

**The standing rule does not lift.** The corpus gate reads
`PARTIALLY_SURVIVORSHIP_CORRECTED` at 38.7%, and no result computed on
`research-01` today is evidence of profitability. This one included.

### The result that matters most is about the pipeline

Fifteen signals had been measured and all fifteen failed. That is consistent
with two very different worlds: one where this corpus has no exploitable
structure, and one where **the machinery cannot detect structure that is there**.
Those could not be told apart from nulls alone.

The low-volatility anomaly is among the most documented effects in the
literature. The pipeline found it, out of the box, at t −38, holding in both
halves, in 4 of 4 samples, surviving costs — **and then correctly identified
that most of it is a price effect.** That is what a working instrument looks
like. It does not make the fifteen nulls more likely to be wrong; it makes them
more likely to be right.

**Ledger: 42 → 46 trials**, hurdle 2.2442 (`expected_max_of_normals(46)` =
2.2441708, measured). Weights unchanged — volatility carries none, and §12's
argument concerns the four factors that do.

### Addendum, same day — the effect does not survive a liquidity floor

Added after §26 was committed, from a diagnostic §26 itself named as missing:
the 2010–2019 runs carried **no liquidity floor**, and the effect concentrated
in $1.20 securities. The panel was rebuilt carrying the engine's own
`avg_dollar_volume_20`. **No trials charged; this promotes nothing and retracts
nothing that was registered.** It changes what the registered result *means*.

`realized_volatility_60`, 21 sessions, by minimum average dollar volume:

| floor | observations kept | least volatile | most volatile | universe |
|---|---|---|---|---|
| none | 233,012 | +7.17%/yr | **−43.33%/yr** | −8.56%/yr |
| $250k/day | 158,050 | +6.77%/yr | −11.43%/yr | +1.54%/yr |
| $1M/day | 131,539 | +6.32%/yr | −6.03%/yr | +2.89%/yr |
| $5M/day | 95,682 | +6.36%/yr | −1.86%/yr | +4.16%/yr |
| **$25M/day** | 51,789 | **+7.31%/yr** | **−0.86%/yr** | **+4.75%/yr** |

**The favoured quintile barely moves — +7.17% to +7.31%.** Everything else
does. The shunned quintile goes from −43.33%/yr to **−0.86%/yr**, and the
universe from −8.56%/yr to **+4.75%/yr**. So the low-volatility *edge over the
universe* collapses from **+15.73 pp/yr to +2.56 pp/yr** once the universe is
restricted to securities anyone could actually trade.

**§26's headline number was mostly measuring illiquidity.** The −8.56%/yr
universe it was scored against was dominated by securities trading under
$250k/day. The honest one-line description of what survived the registered
criteria is now: **"illiquid securities lose money over multi-day holds, and
illiquidity, cheapness and volatility are largely the same condition."** The
registered verdict stands as recorded — the criteria were met — but nothing
should be built on it as a volatility finding.

A residual of **+2.56 pp/yr** at a $25M/day floor may still be real. It has not
been tested against the hurdle, in halves, or across samples, and doing so is a
new registration.

### And the same shape appears in the extreme tail

Quintiles are what every one of the sixteen signals was scored on. Measured at
finer fractions, 21 sessions, no liquidity floor:

| tail | least volatile | most volatile |
|---|---|---|
| 20% | +7.17%/yr | −43.33%/yr |
| 5% | +5.68%/yr | −67.80%/yr |
| 1% | +3.27%/yr | −74.87%/yr |
| **0.2%** | +4.28%/yr | **−92.83%/yr** |

**The buy side is flat across four orders of magnitude of selectivity; the avoid
side more than doubles.** A quintile average is the right granularity for
finding something to hold and the wrong one for finding something to refuse —
which is precisely what §20 and §25 tried to build gates on, with an estimator
that had already averaged the signal away. Read with the table above, both
diagnostics are the same fact seen twice: the extreme tail of "volatile" is the
extreme tail of "untradeable".

---

## §27 — market capitalisation fails, and it closes the whole §26 line — 2026-09-13

**Registered in [`docs/prereg/MARKET_CAP_2026-09-13.md`](prereg/MARKET_CAP_2026-09-13.md)
at `1a0154f`,** before market cap had been computed on any session at any
horizon. Point-in-time shares outstanding × close, four samples, a $1M/day
liquidity floor **in the specification**, 2010–2019.

### Verdict: fails all four criteria at both horizons

| | 21 sessions | 63 sessions |
|---|---|---|
| n above the floor | 97,241 | 94,804 |
| IC (t) | **−0.0002 (−0.07)** | **−0.0021 (−0.38)** |
| criterion 1, both halves | fail | fail |
| criterion 2, IC past 2.2763 | fail | fail |
| criterion 3, sign in ≥3 of 4 | **2/4** fail | **2/4** fail |
| criterion 4, survives price conditioning | **2/5 bands** fail | **2/5 bands** fail |
| `SignalStudy` verdict | `not_detectable` | `not_detectable` |

An IC of −0.0002 against a test that resolves 0.0073 is not a weak effect. It is
nothing. **The pre-registered resolution estimate was accurate**: 97,242
observations predicted, 97,241 measured.

### The registration said a fail would be more informative. It was

Criterion 4 asked whether market cap survives conditioning on price: **2 of 5
bands.** The declared symmetric diagnostic asked the reverse, whether price
survives conditioning on market cap: **1 of 5.** Neither variable survives the
other. That is not "price wins" — it is both losing once the universe is
restricted to securities anyone can trade.

This is the measurement that closes it. Both variables, by minimum average
dollar volume, 21 sessions:

| floor | cheapest | priciest | smallest cap | largest cap | universe |
|---|---|---|---|---|---|
| none | **−24.67%/yr** | −11.25%/yr | −20.54%/yr | −9.23%/yr | −7.32%/yr |
| $250k/day | −4.04%/yr | −0.97%/yr | −2.64%/yr | +1.61%/yr | +2.30%/yr |
| **$1M/day** | **−0.55%/yr** | **+0.55%/yr** | +0.61%/yr | +2.24%/yr | +3.70%/yr |
| $5M/day | +2.94%/yr | +2.24%/yr | +3.58%/yr | +3.44%/yr | +4.90%/yr |
| $25M/day | **+5.69%/yr** | **+2.85%/yr** | +4.27%/yr | +3.50%/yr | +5.32%/yr |

**§26 measured the cheapest price quintile at −33.96%/yr and the priciest at
+20.85%/yr. That was with no liquidity floor.** At $1M/day the same spread is
1.1 points. At $5M/day it inverts. At $25M/day the *cheapest* quintile beats the
priciest by 2.8 points — the opposite sign, and the direction the classical value
premium would predict.

### What the whole line amounted to

Four variables were chased through §26, its addendum and this section —
volatility, price, market capitalisation, liquidity. **They are one variable,
and it is liquidity.** Every apparent edge was the gap between securities that
can be traded and securities that cannot, and it disappears the moment the
universe is restricted to the former. The universe's own return tells the story
without any signal at all: **−7.32%/yr unrestricted, +5.32%/yr above $25M/day.**

`realized_volatility_60` and `atr_percent` passed their registered criteria in
§26 and that record stands — the criteria were met and the run was honest. But
**nothing should be built on any of it**, and the master table above now says so.

### What this licenses

**Closing the enquiry, exactly as the registration said a fail would.** Its own
words, committed before the run:

> If the best-specified version of the variable does not survive its own
> criteria on the data that suggested it, then price, volatility and
> illiquidity were describing the corpus's coverage gaps rather than the
> market, and the honest next step is the corpus rather than another signal.

That is now the measured outcome and it is binding. No other capitalisation
definition, floor or quantile will be tried on this data.

**One observation that is not a finding and must not become one.** Above
$25M/day the cheapest quintile beats the priciest by 2.84 points a year. It is
unregistered, untested against the hurdle, unmeasured in halves and across
samples, and sits inside a corpus whose gate reads 38.7%. It is recorded so that
nobody rediscovers it as a surprise, not as a result.

**Ledger: 46 → 50 trials**, hurdle 2.2763 (`expected_max_of_normals(50)` =
2.2763031, measured). Seventeen signals measured, none surviving.

---

## §28 — equal-risk sizing on a tradeable universe: a trade-off, not an improvement — 2026-09-14

**Descriptive, not registered, no trials charged.** §7h showed paired designs
survive the corpus's coverage hole, and the only positive result this project
has — §7's equal-risk sizing beating equal-dollar — had never been run on a
universe anyone could trade. A power check before launch showed a registered
test could not resolve §7's effect (+0.34 to +0.81 pp/yr) with the four disjoint
samples a $1M/day floor allows, so this was run **to measure the paired standard
deviation** a properly powered design would need, and declared as such before
any number existed.

Four disjoint samples of 298 securities (149 survived, 149 died), admitted on
**trailing-year** median dollar volume ≥ $1M/day, 2010–2019, recovery 1.0, same
entry rule, same costs, arms differing only in the stop that sets size.

| sample | CAGR A → B | Sharpe A → B | max drawdown A → B |
|---|---|---|---|
| 0 | 3.20% → 5.10% (**+1.89**) | 0.06 → 0.26 | 11.95% → 14.64% (**+2.69**) |
| 1 | 5.82% → 5.49% (**−0.33**) | 0.40 → 0.29 | 12.83% → 20.30% (**+7.46**) |
| 2 | 6.03% → 8.11% (**+2.07**) | 0.45 → 0.54 | 8.09% → 12.58% (**+4.49**) |
| 3 | 2.21% → 4.52% (**+2.32**) | −0.07 → 0.20 | 12.71% → 14.24% (**+1.53**) |

| paired difference, B − A | mean | sd | t | B better |
|---|---|---|---|---|
| CAGR | **+1.49 pp/yr** | 1.22 pp | +2.44 | 3 of 4 |
| Sharpe | +0.11 | 0.17 | +1.37 | 3 of 4 |
| max drawdown | **+4.04 pp worse** | 2.59 pp | **+3.13** | **0 of 4** |

### What it shows

**Equal-risk sizing buys return with drawdown.** It raised CAGR in three samples
of four and **worsened drawdown in all four**, and the drawdown effect is the
more consistent of the two. Sizing inversely to ATR puts the largest positions in
the quietest names, which concentrates notional exactly where a gap hurts most.
Sharpe moves little and not reliably.

Under §7's own criterion — Sharpe up **and** drawdown not worse — **not one of
the four samples is a capture.** That is the same shape §7 recorded out of sample
(return and Sharpe up, drawdown worse, graded `MIXED`), now seen on a tradeable
universe and in every sample rather than once. So the project's only positive
result is better described as **a risk dial** than as an edge: it moves the
portfolio along a return/drawdown line rather than off it.

### What it must not be read as

**A t of +2.44 on CAGR is not a finding.** It would clear the 2.306 hurdle a
four-trial registration would have faced, and it was not registered; that is
precisely the result the discipline exists to refuse. It was computed on data
already used by §7's 2010–2024 run and by §§22–27.

### The number it was run for

**Measured paired CAGR sd: 1.22 pp.** At a four-trial hurdle, four samples resolve
about **1.41 pp/yr**. So:

| effect to resolve | disjoint samples needed | feasible at $1M/day? |
|---|---|---|
| 0.34 pp/yr (§7 out of sample) | ~69 | no |
| 0.81 pp/yr (§7 in sample) | ~13 | no |
| ≥ 1.5 pp/yr | 4 | yes |

A registered test of this comparison is feasible **only** for an effect of about
1.5 pp/yr or more, and only on a period this one has not used. There is no such
period for 2010–2024, and the pre-2010 decade is where the liquid died arm is
thinnest. That is recorded as the design constraint, not worked around.

**Ledger unchanged at 50 trials.**

---

## §29 — §26 and §27 re-measured on the corrected corpus: nothing moved — 2026-09-16

Limitation (4) of the owner's decision requires every result measured before
2026-09-15 to be re-run before it is relied on, because those ran on reads that
double-counted splits. This is that re-run for the strongest finding.

**A strict re-run, so only one thing changed.** Same universe file
(`security_spans.csv`, untouched since 2026-09-11), same four disjoint samples,
same signals, same registered criteria, same verdict script. What changed is
underneath: **1,980 splits no longer applied twice, 8,895 applied correctly
(1,807 of them recovered from Sharadar), 1,029,023 prints withheld** before
splits whose records contradict the prices, and history reaching back to
1990-01-02. Adding the 2,877 newly priced dead companies would have changed the
universe as well, so they are deliberately **not** in this run: it isolates the
corrections.

### §26's criteria: still passing, on slightly smaller numbers

| | §26 (2026-09-13) | re-measured |
|---|---|---|
| observations | 235,640 | 232,830 |
| `realized_volatility_60` IC, 21 | −0.0790 (t −38.25) | **−0.0767 (t −36.93)** |
| `realized_volatility_60` IC, 63 | −0.0996 (t −27.54) | **−0.0960 (t −26.37)** |
| `atr_percent` IC, 21 | −0.0790 (t −38.27) | **−0.0765 (t −36.82)** |
| `atr_percent` IC, 63 | −0.1066 (t −29.50) | **−0.1027 (t −28.24)** |
| bottom quintile, 2010–14 / 2015–19 (rv60, 21) | +16.83% / +14.56% a year | **+15.02% / +13.97%** |
| criteria 1, 2, 3 | pass, pass, 4/4 | **pass, pass, 4/4** |
| arithmetic spread | `outlier_dependent`, mean and median opposite in sign | **unchanged in kind** |

Every number softened by roughly 3–5% in relative terms and not one changed a
verdict. Both signals still survive at both horizons.

### §27's explanation: still the whole story

`realized_volatility_60`, 21 sessions, by minimum average dollar volume:

| floor | least volatile | most volatile | universe | edge over universe |
|---|---|---|---|---|
| none | +7.30%/yr | **−39.66%/yr** | −7.09%/yr | +14.4 pp |
| $1M/day | +6.35%/yr | −5.86%/yr | +2.92%/yr | +3.4 pp |
| **$25M/day** | **+7.34%/yr** | **−0.63%/yr** | **+4.80%/yr** | **+2.54 pp** |

§27 measured that same edge at +15.73 pp with no floor and **+2.56 pp** at
$25M/day. The re-measurement lands on **+2.54 pp**. The conclusion is untouched:
the apparent volatility effect is liquidity, and what survives a tradeable
universe is about two and a half points a year that has never been tested
against a hurdle.

### What this says about the corrections, and what it does not

**It is reassuring about the corpus, not about the signal.** A double-counted
split injects a fabricated ±50% or ten-fold return at a single session — exactly
the shape a quantile mean is sensitive to. That 1,980 of them were being applied
and the geometric edges still moved only 3–5% says the finding never rested on
them. Had it rested on them, this run is what would have shown it.

**Seventeen signals remain measured and null, and §27's closure stands.** Nothing
here reopens anything.

**Still un-re-run:** every other section through §28. This one was taken first
because it was the strongest, and the rest are a queue, not a formality — the
sections resting on a handful of extreme returns are the ones most likely to move.

---

## §30 — §18 re-measured: the mean/median disagreement was real, not a split artefact — 2026-09-17

§18 failed on one fact: at 63 sessions `relative_strength`'s quantile spread was
**negative by mean and positive by median**, which `SignalStudy` grades
`OUTLIER_DEPENDENT`. That disagreement is exactly the shape a fabricated return
produces — one session carrying a spurious ±50% or ten-fold move drags a mean and
leaves a median alone — and the 2000s are where the corrected splits are densest.
So this was the section most likely to have failed for the wrong reason.

**Strict re-run**, as §29: same universe file, same four disjoint samples, same
criteria, same verdict script, 2000–2009. Only the prices changed.

| | §18 (2026-09-12) | re-measured |
|---|---|---|
| observations | 207,121 | 204,130 |
| IC, 21 sessions | −0.0021 (t −0.93) | **−0.0039 (t −1.74)** |
| IC, 63 sessions | **+0.0129 (t +3.32)** | **+0.0091 (t +2.33)** |
| spread, 63 sessions | mean −2.32%, median **+0.82%** | mean −2.91%, median **+0.72%** |
| geometric edge, 63: 2000–04 / 2005–09 | +6.85%/yr / −0.88%/yr | **+5.48%/yr / −0.87%/yr** |
| declared sign, 63 | 4 of 4 samples | **3 of 4** |
| verdict | `OUTLIER_DEPENDENT`, does not survive | **`OUTLIER_DEPENDENT`, does not survive** |

Under the FLAT benchmark the same shape holds: IC +0.0081 (t +2.07), mean −3.16%
against median +0.65%, and the same failure.

### What this settles

**The spread's sign disagreement is a property of the data.** It survived the
removal of 1,980 double-counted splits and the addition of 1,807 that were
missing. §18's criterion 1 failed because the top quintile's mean is dragged by a
handful of enormous winners while its median sits slightly the other way — the
same structural fact §7 described for volatility, and not an artefact of a
corrupted bar.

**Everything moved the way §29 did, and slightly further.** The 63-session IC
fell from t +3.32 to t +2.33 and the sample agreement from 4 of 4 to 3 of 4, so
the corrected data makes this signal look *weaker*, not stronger. It still clears
its own hurdle of 2.01 and still fails on the spread, which is what decided it.

**The diagnostic that mattered is unchanged.** The 250-session lookback alone
still reads IC +0.0217 (t +5.52) at 63 sessions while the engine's composite
does not survive — the finding §18 recorded about the engine blending opposite
signals, now on corrected prices.

**Two of the queue re-run, both unmoved.** §26, §27 and now §18 stand as
recorded. The sections that remain are §13 `pattern_quality`, whose detector scan
is hours rather than minutes, and the rest through §28.

---

## §31 — `pattern_quality` re-measured: the least scale-invariant section, and it did not move — 2026-09-17

§13 is the section the split double count had the best chance of having broken,
and it was left until last for that reason.

**Why this one and not the others.** §29 and §30 re-ran ratio signals, and a
ratio barely notices a mis-levelled price — a volatility or a momentum kernel
divides the error out. Pattern detectors do not. The double count did not merely
scale a series, it put a **spurious step inside the trailing window**: prices
before an already-applied ex-date were divided by the ratio a second time, so a
2-for-1 split left a 50% cliff in the middle of the 236 bars a detector reads. A
fabricated gap is precisely what a bull flag, a breakout-retest or a tight
consolidation is built to react to. Measured on this universe before the run:

| | |
|---|---|
| splits with an ex-date inside the study decade | 2,761 |
| of those, landed from Sharadar since §13 ran | 426 |
| splits now arbitrated by a recorded verdict | 741 (508 `already_adjusted`, 233 `in_raw`) |
| securities carrying a split that can fall inside a scan window | **1,703 of 4,306 (39.5%)** |

Two securities in five had something change underneath them, against the one
factor in the scoreboard whose measurement is not scale-invariant.

**Strict re-run**, as §29 and §30: the same `security_spans.csv` from 2026-09-11,
the same twelve enabled D1 detectors, the same 236-bar window, the same stride,
the same `ACTIONABLE` states, the same tradability filter from Amendment 2, the
same robustness script and seed. Only the prices changed. Eight shards, ~7.8
CPU-hours.

### More data, because fewer prints are withheld

| | §13 (2026-09-10) | re-measured |
|---|---|---|
| securities scanned | 3,876 | **3,898** |
| scan points | 285,177 | **308,129** |
| removed for an untraded endpoint | 10.7% | **8.8%** (14,431 signal bars, 12,833 outcome bars) |
| analysed after the filter | — | 280,865 points, 3,856 securities |
| carrying a live pattern | 94.7% | **94.7%** (266,029) |

The corrected corpus yields **8.0% more scan points**, which is the withheld-print
floor falling from 4.23% to 2.49% showing up as bars a detector can read, plus 22
securities that now clear the minimum bar count.

### Reason 1 — it still cannot discriminate

| | §13 | re-measured |
|---|---|---|
| any structure fired on | 99.6% of scan points | **99.9%** |
| concurrent structures | median 8, max 28 | **median 8, max 28** |
| of those live | median 5 | **median 5** |
| quality p25 / median / p75 | 69 / 78 / 86 | **69 / 78 / 86** |

Identical to the digit. A factor defined for almost every security on almost
every day, reading about 78 whenever it is defined, cannot narrow a slate — and
that was never a statistical claim, so no correction to the prices could touch
it. `double_bottom` supplies the best structure on 106,698 of 266,029 graded
points, which is the same crowding read a different way.

### Reason 2 — the sign still depends on the statistic

| statistic | 21s, §13 | 21s, now | 63s, §13 | 63s, now |
|---|---|---|---|---|
| Spearman (ranks) | +0.0056 (t +2.93) | **+0.0058 (t +2.98)** | +0.0154 (t +4.63) | **+0.0141 (t +4.20)** |
| Pearson (levels) | −0.0246 (t −12.81) | **−0.0260 (t −13.39)** | −0.0177 (t −5.32) | **−0.0209 (t −6.24)** |
| Pearson winsorized 1/99 | −0.0135 (t −7.03) | **−0.0141 (t −7.29)** | −0.0065 (t −1.94) | **−0.0090 (t −2.68)** |
| Pearson less top 0.1% | −0.0144 (t −7.47) | **−0.0154 (t −7.95)** | −0.0101 (t −3.04) | **−0.0128 (t −3.80)** |

`SIGN FLIPS between statistics` at both horizons, as before. **Every
level-based reading is more negative than it was**, and the winsorized 63-session
figure crossed its own threshold from t −1.94 to t −2.68 — so on corrected
prices the negative side of the contradiction is *better* established, not worse.
Ranks say positive and significant; levels say negative and more significant;
both still cannot describe the same edge.

### Reason 3 — the geometric edge is still a period, not a signal

| horizon | period | §13 edge/yr | re-measured | CI |
|---|---|---|---|---|
| 21 | full sample | +2.17 pp | **+1.55 pp** | excludes 0 |
| 21 | 2000–2004 | +6.45 pp | **+5.97 pp** | excludes 0 |
| 21 | 2005–2009 | −1.33 pp | **−1.85 pp** | **includes 0** |
| 63 | full sample | +2.90 pp | **+2.29 pp** | excludes 0 |
| 63 | 2000–2004 | +8.10 pp | **+7.32 pp** | excludes 0 |
| 63 | 2005–2009 | −1.13 pp | **−1.46 pp** | **includes 0** |

The strongest case the factor had — that a portfolio *compounding* high quality
beats ranking at random — survives in the full sample and **shrinks at both
horizons**. The reversal in the second half of the decade survives and deepens.
`sector_strength` was retired for this shape and the standard does not move.

### The one verdict that changed, and it changed against the factor

`SignalStudy` graded the 21-session cut `SPREAD_NOT_ESTABLISHED` in §13; on
corrected prices it grades **`OUTLIER_DEPENDENT`** — spread −30.29% by mean
against +0.43% by median, opposite signs. The 63-session cut was already
`OUTLIER_DEPENDENT` and remains so (−55.47% against +1.01%). The information
coefficients themselves barely moved: t +2.86 → **+2.87** at 21 sessions,
t +4.51 → **+4.05** at 63.

`pattern_present` fails at both horizons as before — `NOT_DETECTABLE` at 21
(t −1.99) and at 63 (t +1.71 on 93,622 effective observations).

### Verdict

**§13 stands, on all three of its reasons, and two of them read slightly worse.**
`pattern_quality` is not validated by its real detector on a corpus with the
splits fixed. Nothing in the re-measurement rescues it, and the pre-registration's
declared failure condition — *"a significant t on the IC alone with no
established spread"* — is met at both horizons exactly as it was.

**The weight does not move**, for §12's reason, which this does not touch:
down-weighting a factor because it was measured would penalise the two factors
that have been examined and reward the two that have not.

### Registered and spent

**No trials added.** This is a re-measurement of a registered test on corrected
data, not a new test; the signal specification in
[`prereg/PATTERN_QUALITY_2026-09-10.md`](prereg/PATTERN_QUALITY_2026-09-10.md)
did not move, and neither did the ledger. It stands at **50 trials**, hurdle
|t| > 2.2763.

The run reported against §13's own hurdle of |t| > 1.98 at 24 trials, which is
what makes the two tables comparable line by line. Judged against today's 50-trial
hurdle instead, the 63-session information coefficient (t +4.05) still clears it
and the 21-session one (t +2.87) still clears it — and it changes nothing,
because what killed the factor was the spread and the sign flip, neither of which
is a hurdle question.

### What is left in the re-measurement queue

The four highest-exposure sections — §26, §27, §18 and now §13 — have all been
re-run on the corrected corpus and **none of them moved**. That is now four
independent checks on limitation (4) of §7e, covering a volatility kernel, a
size kernel, a momentum engine and a pattern detector suite, and the double count
changed no verdict in any of them. The sections below §13 remain formally
un-re-run; on this evidence the prior that any of them moves is weak, and they
are re-run on demand rather than ahead of the next measurement.

---

## §32 — 24 classical indicators, and the control that caught the method — 2026-09-17

**Registered in [`prereg/INDICATOR_SCREEN_2026-09-17.md`](prereg/INDICATOR_SCREEN_2026-09-17.md)
at `c99ace9`**, before any of the 24 had been computed on any session. The owner
asked whether the indicator library should widen — Bollinger bands, DeMark's TD
counts, triple EMA, Fibonacci retracement "and others". All four are here, with
twenty more, across trend, oscillator, volatility, volume and structure families.

144,806 observations, 2,513 securities, 2000–2009, horizon 63, stride 21, a
**$1,000,000/day liquidity floor in the specification** because §27 established
that without one this corpus answers with its coverage gaps.

### The headline is not about any indicator. It is about the method

**The positive control failed, and failed inverted.** `rate_of_change_252` was
declared POSITIVE on the strength of §18's diagnostic, which measured that family
at **+0.0189 (t +8.49)**. The screen returned **−0.0249 (t −5.47)** — significant,
and pointing the wrong way, in 0 of 2 halves and 0 of 5 volatility bands.

The registration had already written down what that means:

> If this screen cannot reproduce something in that region, the **machinery** is
> broken and no null it reports may be believed. This is a test of the test.

So the 24-arm table was **void on its own terms** before a single indicator
verdict was read. That is the control doing precisely the job it was charged a
trial for.

### What was wrong, and it is larger than this screen

§18's number is a **cross-sectional rank** — each security's 250-session return
ranked against the universe *on that date*. This screen pooled raw indicator
**levels** across every sample date and took one correlation over all 144,766
rows. Those answer different questions, and over a decade containing two crashes
and two rebounds they have **opposite signs**: pooled, twelve-month return is
negatively related to the next quarter because the whole market fell and rebounded
together; cross-sectionally, it is not.

The second defect is worse, because it is not confined here. **The pooled
t-statistic is inflated.** It divides by the horizon overlap (63/21 = 3) and then
treats what remains as independent — but ~648 securities share each sample date
and therefore share that date's market move. The independent unit is nearer the
date than the row: **69 effective periods, not 48,255**.

| arm | pooled IC (t) | Fama-MacBeth IC (t) |
|---|---|---|
| `atr_percent_14` | −0.0565 (**t −12.42**) | −0.0298 (**t −0.99**) |
| `atr_contraction_10_50` | −0.0439 (t −9.64) | **+0.0171 (t +0.99)** — sign flips |
| `ulcer_index_14` | −0.0434 (t −9.55) | −0.0281 (t −1.14) |
| `gap_frequency_63` | −0.0360 (t −7.92) | −0.0127 (t −0.48) |
| `rate_of_change_252` | −0.0249 (t −5.47) | **+0.0141 (t +0.54)** — sign flips |

**This applies to every pooled information coefficient in this document.** It
cannot rescue a negative verdict — a signal that failed on an inflated statistic
fails harder on an honest one — but **every t quoted as evidence *for* something
must be re-read**, including §26's t −27.54 and §18's t +8.49. Recorded as an
obligation, not discharged here.

### The re-run, charged as the second look it is

The same 24 arms, same data, same four criteria, with the IC computed
Fama-MacBeth: one IC per sample date, averaged, t from the time series of those
ICs, same 3× overlap correction. **24 further trials, ledger 74 → 98, hurdle
|t| > 2.5235.** Calling the first pass a bug and the second the real run at the
old hurdle would have been two looks for the price of one.

### Result: nothing clears, and both controls now behave

| | |
|---|---|
| arms clearing all four criteria | **none** |
| best arm | `percent_rank_close_252` — 52-week price position — **+0.0403 (t +1.90)** |
| next | `chaikin_money_flow_20` +0.0291 (t +1.45), `adx_14` +0.0222 (t +1.33) |
| the owner's four | Bollinger %b −0.0018 (t −0.09); bandwidth −0.0249 (t −1.02); TD buy setup +0.0069 (t +0.41); TD sell setup −0.0077 (t −0.43); TEMA distance −0.0121 (t −0.57); Fibonacci retracement −0.0235 (t −1.14) |
| positive control | sign **recovers**: +0.0141 |
| negative control | significance **collapses**: t −12.42 → −0.99, and it no longer flags |

The controls now do what a correct method requires of them, which is the evidence
that the corrected reading is the trustworthy one.

**Two of the owner's four came out with the declared sign and no significance;
two came out against it.** Fibonacci retracement is the interesting failure: it
was declared NEGATIVE — deeper retracement, weaker security — and the folk
reading ("buy the 61.8% level") would need a positive sign. It produced neither,
at t −1.14.

### The null is bounded, and must not be quoted as more than that

| | |
|---|---|
| usable sample dates (≥20 securities) | 208 of 2,006, carrying 93.2% of observations |
| effective independent periods | **69** |
| smallest cross-sectional IC this design could detect | **+0.0510** |
| largest any arm produced | +0.0403 |

**This rules out an effect above ~0.051. It does not distinguish zero from an
effect below it** — and the best arm sits just under the line. Saying "24
classical indicators do not work" would be claiming an absence this test could
not establish.

The reason resolution is this poor is a design fault worth naming: each security
was sampled on **its own** 21-session grid from its own first bar, so the grids
do not align — the median usable date carries only **50** securities. A common
calendar grid would put hundreds on every date and raise resolution several-fold
at identical scanning cost.

### What this licenses

**The stop rule applies to what it was written for.** No further indicator
*family* — Ichimoku, Gann, Elliott counts, more oscillator variants — is tried on
this corpus without a new registration. Twenty-four failures do not make a
twenty-fifth due.

**It does not close the question the resolution left open.** A re-test of these
same 24 on a common calendar grid is a *different test of the same hypothesis*,
with a measured reason to expect it to resolve what this one could not, and the
stop rule's own words ask for exactly that: a registration stating "what
specifically would be different and why". It would cost another 24 trials.
Whether that is worth spending is a decision, not a consequence, and it is not
taken here.

**No weight, no gate, no strategy parameter moves.**

### Registered and spent

**Ledger: 50 → 98 trials**, hurdle 2.5235 (`expected_max_of_normals(98)`,
measured). 24 for the screen as registered, 24 for the corrected re-reading.
Forty-one signals measured, none surviving.

Two corpus findings came out of the run and are recorded where they belong rather
than here: `RESEARCH_01_DATA_DICTIONARY.md` **§0.8**, a bar that carries volume,
clears a liquidity floor, and is still a bad print — security 79 round-trips
39.50 → 2,420 → 40.00, 119 times, and the $1M floor is *defeated* by it because
the defect creates the turnover. And the registration's own Amendment 2 records
that six such rows moved a sample's mean forward return from **+408.6% to +3.2%**.

---

## §33 — §26 re-read with honest standard errors: it survives, and §27 still closes it — 2026-09-18

§32 found that every pooled information coefficient in this document carries an
inflated t-statistic, and recorded an obligation: *every t quoted as evidence
**for** something must be re-read.* §26 is the only section where that obligation
bites, because it is the only section whose verdict was a pass —
`realized_volatility_60` and `atr_percent` **SURVIVE** at both horizons, at t −26
to −38. If any verdict on this scoreboard was a pooled-t artefact, it was this
one. So it was re-read first.

### The estimator, now in `src` rather than in a script

[`tradeit.signals.cross_section`](../src/tradeit/signals/cross_section.py) — one
IC per sample date, averaged over **non-overlapping calendar blocks one horizon
wide**, t from those block means with a Newey-West lag-1 correction. Three
failures shaped it, and each is a test:

| defect | what it did | the test that now guards it |
|---|---|---|
| pooling across dates | rewarded *when* over *which*; §32's positive control came back inverted | a pure market-timing panel reads large pooled, ~0 cross-sectional |
| dividing dates by horizon/stride | assumed dates sit a stride apart; they don't — each security samples on its own grid, so §26's panel has **1,770 usable dates in a decade holding ~40 non-overlapping 63-session windows** | the same decade sampled daily vs quarterly yields the same block count |
| estimate over dates, t over blocks | reported two different numbers as one — a positive control read **IC +0.0141 with t −0.01**, and the mismatch alone lifted `adx_14` from t +1.33 to +2.55 | a panel built to separate the two averages; **shown to fail against the broken code** before it was trusted |

The last row is worth its own sentence. The first test written for that defect
used evenly spaced dates — where the two averages are identical — and passed
against the broken code, 0 disagreements in 58 panels. A test written after a fix
proves nothing until it has been seen to fail without it.

### §26 under honest standard errors: every criterion still passes

Same corrected-corpus panel §29 used (232,830 observations), same four samples,
same halves, **same registered hurdle** (2.2442 at the 46 trials §26 was judged
at — a later hurdle would change two things at once). Only the standard errors
change. Criterion 1's interval now resamples whole calendar blocks instead of
single observations.

| | 21s, `rv60` | 63s, `rv60` | 21s, `atr%` | 63s, `atr%` |
|---|---|---|---|---|
| pooled t (§29) | −36.93 | −26.37 | −36.82 | −28.24 |
| **cross-sectional t** | **−15.19** | **−12.20** | **−14.41** | **−12.14** |
| C1, both halves, block CI | holds | holds | holds | holds |
| C3, samples negative | 4/4 | 4/4 | 4/4 | 4/4 |
| verdict | **SURVIVES** | **SURVIVES** | **SURVIVES** | **SURVIVES** |

The inflation was real but modest here — about 2.4×, not the twelvefold §32
measured on the screen — and the effect is far past it. **The prediction that
§26 would not survive, made in this project's last checkpoint, was wrong.**
Measure, do not predict.

### But the headline magnitude is not the typical one

The estimate weights *dates*, and the dates are wildly uneven: a few broad dates
where the common grid puts ~2,000 securities, and many thin dates of 20–30 recent
listings on their own grids. The thin ones win by count.

| cross-section | `rv60` 21s | `rv60` 63s | `atr%` 21s | `atr%` 63s |
|---|---|---|---|---|
| all usable dates | −0.1235 (t −15.19) | −0.1647 (−12.20) | −0.1227 (−14.41) | −0.1764 (−12.14) |
| thin, 20–199 names (**22%** of obs) | −0.1290 | −0.1713 | −0.1281 | −0.1836 |
| **broad, ≥200 names (73% of obs)** | **−0.0562 (−4.29)** | **−0.0806 (−4.44)** | **−0.0553 (−3.97)** | **−0.0836 (−4.50)** |
| broad, **above $1M/day** | −0.0214 (−1.28) | −0.0403 (−1.89) | −0.0198 (−1.11) | −0.0421 (−1.95) |

On the dates that hold three-quarters of the data, the effect is **less than half
the headline** — and still clears its hurdle. The module now reports
`median_breadth` beside every estimate so this cannot be missed again.

### What this settles

**§26 was not a pooled-t artefact.** Its criteria pass under an estimator that
removes both defects §32 found, on the broad cross-sections alone as well as on
all of them.

**§27's closure stands, and now has an independent confirmation.** Among
securities trading above $1M/day the effect is **not distinguishable from zero at
either horizon, for either signal** — t between −1.11 and −1.95 against a hurdle
of 2.24. §27 reached that conclusion by conditioning on price and market cap;
this reaches it by a different route. The low-volatility effect in this corpus is
real, and it lives in the securities a portfolio of any size could not trade.
**Nothing is built on it.**

### §32 corrected, and one lead recorded without being promoted

§32's resolution and best-arm figures came from the date-stride method this
section replaces. Re-run on the module: **35 calendar blocks, not 69 periods**;
smallest detectable cross-sectional IC **+0.0347**, not +0.0510. §32 stands as a
record of what was measured then.

**Its registered verdict is unchanged — nothing flagged.** One arm crosses the
line under the corrected estimator: `adx_14`, IC +0.0264, **t +2.55 against
2.5235**, sign holding in both halves and all five volatility bands. It is **not
a result**. It exists only on the fourth reading of one dataset, the margin is
0.03, and the same estimator reads the positive control at −0.0004 (t −0.01) — so
the machinery that produced the pass cannot see the one effect it was built to
find. Recorded in the screen registration's Amendment 4 with what it earns: the
right to be *named* in a future registration on 2010–2019 data, on a common
calendar grid, with the estimator fixed in advance. **Nothing about `adx_14` may
be promoted on 2000–2009 data.**

### Registered and spent

**No trials added.** The §26 re-read can only withdraw a pass, never confer one —
an honest standard error widens intervals and shrinks t-statistics — so it is an
audit, not a trial. The `adx_14` reading cannot confer a result either; whatever
registration pursues it pays. **Ledger stands at 98 trials, hurdle 2.5235.**

**The obligation §32 recorded is discharged for the one section it could change.**
Every other section's verdict was negative, and a negative verdict cannot be
rescued by a smaller t.

---

## §34 — `adx_14` out of sample: closed, clearly; and the floor that closed §27 was biased — 2026-09-18

**Registered in [`prereg/ADX_CONFIRMATION_2026-09-18.md`](prereg/ADX_CONFIRMATION_2026-09-18.md)
at `324e26b`**, Amendment 1 at `f87a15c`, verdict code committed at `9b790e1` —
all before any `adx_14` statistic on 2010–2019 existed. The lead came from the
indicator screen's Amendment 4: t +2.55 against 2.5235 on the fourth reading of
2000–2009, ruled not a result and granted only a name here.

2,176,880 observations, 8,377 securities, 2010–2019, **on the common calendar
grid** (`0c7a521`) — every security sampled on the same 491 exchange sessions,
every outcome on a date measured to the same session 63 later. Universe from the
corpus as it now stands, **793 more dead companies** than the 2026-09-11 file,
and no whole-life bar filter.

### The machinery passed before `adx_14` was looked at

| check | reading | verdict |
|---|---|---|
| positive control — `realized_volatility_60`, no floor | IC −0.1325, t −8.89, median breadth **4,453** | passes |
| calibration — `adx_14` shuffled within date ×200 | 95th percentile \|t\| **1.90** (limit 2.3) | calibrated |
| Amendment 1 — undetermined volume in the floored sample | **3.6%** (bound 5%) | judged once |

Breadth **4,453** securities on a median date — against **50** under the old
per-security grids. That is the design fix doing what it was built for.

### `adx_14`: not confirmed, and not inconclusive

| criterion | reading | |
|---|---|---|
| 1 — block t > 2.5306, IC positive | IC **−0.0132**, t **−3.30** | fail |
| 2 — quintile spread positive by block mean and median | −1.94% / −0.45% | fail |
| 3 — positive in both halves | −0.0146 / −0.0109 | fail |
| 4 — positive in ≥4 of 5 within-date volatility bands | 2 of 5 | fail |

**Smallest detectable IC: 0.0102.** The registration predicted ~0.046 at stride 21
and warned that a null might not resolve the lead's +0.026. It resolved it
comfortably, and found the opposite sign. **The lead is closed by the stop rule.**

The significant *negative* reading is **not** a short-ADX finding. Direction was
declared positive before the data; reading the reverse off the result would be
deriving it, which costs two trials and makes the prior unfalsifiable. It is
recorded as what it is: the screen's crossing was noise, on a dataset read four
ways.

### The finding that matters more: §33's floor result does not survive the volume fix

The adx scan carried `realized_volatility_60` and **corrected** dollar volume
(`67894dd`, DATA_DICTIONARY §0.9) on the same years as §26 and §33. §33 had said
the low-volatility effect is *"not distinguishable from zero"* above $1M/day, and
called that an independent confirmation of §27's closure. Re-read:

| low-volatility effect, 63 sessions, 2010–2019 | IC | t |
|---|---|---|
| §33, old read, broad dates above $1M/day | −0.0403 | −1.89 |
| **now, above $1M/day** | **−0.0840** | **−4.34** |
| **now, above $10M/day** | **−0.0700** | **−3.31** |
| now, below $1M/day | −0.1690 | −12.74 |

Above both floors it clears the hurdle §33 was judged at (2.2442) and today's
(2.5306). **§33's conclusion is withdrawn**, and so is its claim to confirm §27.

**One attribution check, and only one** — every further slice of a closed line is
another chance to fool ourselves. Restricted to the securities the old universe
already held, above $1M/day: **−0.0794, t −4.05**. So the added dead companies are
not what moved it (they read stronger still, −0.1098, but the old set carries the
effect alone). The change comes from the **corrected volume floor, the common
grid, or both**; separating them would mean rebuilding the broken read, and it is
not done.

**What this is and is not.** It is a diagnostic on the ninth reading of 2010–2019
for volatility, unregistered, and it confers nothing. It is evidence that §27's
closure rested on a floor that — measured in §0.9 — let future winners in and kept
future distressed names out, which is exactly the tilt that would hide this
effect among liquid names. Whether to re-open the line is a reversal of §27's
written decision and is put to the owner rather than taken here.

**If re-opened, the data exists to do it honestly.** The corpus reaches 2026-09-14:
8,688 securities print in 2020–2025, 2,730 of them dying in it — a window no
volatility study has read.

### Registered and spent

**Ledger: 98 → 100 trials**, hurdle 2.5306. `adx_14` and its positive control.
The §33 re-read is a diagnostic and adds none. Forty-two signals measured; none
survives as a registered, out-of-sample, tradeable result.

---

## §35 — low volatility, out of sample among tradeable securities: it passes — 2026-09-18

**Re-opened by the owner's decision of 2026-09-18**, reversing §27's written
closure for this one test. **Registered in
[`prereg/LOW_VOLATILITY_2020_2025_2026-09-18.md`](prereg/LOW_VOLATILITY_2020_2025_2026-09-18.md)
at `e12ec7e`**, scan and verdict code committed at `900531f` — all before
`realized_volatility_60` had been computed on any session after 2019.

**2020–2025 — a window no volatility study had read.** 1,455,128 observations,
7,947 securities, the common calendar grid (289 dates), horizon 63, the corpus's
current universe with point-in-time history only. **One signal, one horizon, one
floor: one trial.**

### Result: all four criteria pass

| check | reading | |
|---|---|---|
| calibration — shuffled within date ×200 | 95th percentile \|t\| **1.76** (limit 2.3) | calibrated |
| undetermined volume in the floored sample | **1.2%** (bound 5%) | judged once |
| **1** — block t < −2.5341 | **IC −0.1368, t −3.32**, 24 blocks, median breadth 3,154 | **pass** |
| **2** — least-volatile quintile compounds ahead of its date's universe | **+3.32% a hold (+13.96%/yr)**, median across dates **+4.36%** | **pass** |
| **3** — negative in both halves | 2020–22 **−0.1319** (t −1.81); 2023–25 **−0.1224** (t −3.35) | **pass** |
| **4** — negative in ≥ 4 of 5 within-date **price** bands | **5 of 5** | **pass** |

**This is the first signal in forty-three to pass a registered, out-of-sample
test on a tradeable universe at an honest standard error.** It was re-opened
because §34 found the floor that closed it biased; it passed on data it had
never touched.

### Four things the pass does not hide

**1. It fades as the share price rises.** Criterion 4 asked for the sign in each
within-date price band, and it holds in all five — but the size does not:

| price quintile, within date | cheapest | 2 | 3 | 4 | priciest |
|---|---|---|---|---|---|
| IC | **−0.2103** | −0.0943 | −0.0547 | −0.0128 | −0.0085 |

In the dearest two-fifths of the tradeable universe the effect is **close to
nothing**. §27's reading — *"mostly a price effect"* — had substance: price does
not explain the effect away, but it is where most of it lives. That matters
directly for cost, because cheap stocks carry the widest spreads.

**2. The first half alone is not significant** (t −1.81). The registration asked
for the sign in each half, not significance, and it holds; but 2020–2022 on its
own would not have cleared the hurdle.

**3. Resolution came in worse than predicted** — smallest detectable IC **0.104**,
against a registered forecast of 0.063. 2020–2025's cross-sections were noisier
than 2010–2019's. The effect cleared it because it was larger (0.137) than
§34's reading (0.084), not because the test was sharper.

**4. Among the most liquid names it is weaker** — above $10M/day, IC −0.0973,
t −2.30, below the hurdle. A declared diagnostic; it decides nothing, and it is
recorded so a strategy built on this does not assume the effect holds equally
where it would be cheapest to trade.

**One bias runs against the finding, not for it.** Where the corpus is missing
dead companies, the missing ones are disproportionately volatile names that fell
— so survivorship gaps would *hide* this effect rather than manufacture it.

### What it licenses

**A scoped proposition, and nothing else.** No weight, gate or strategy parameter
moves because of a measurement. The proposition would have to face what a signal
test does not: **trading cost in exactly the cheap, wide-spread stocks where the
effect concentrates**, turnover at a 63-session rebalance, capacity, and a
walk-forward backtest on the corrected read. *"Lifting the rule is not evidence of
profitability"* applies with full force: a factor that predicts is not yet a
trade that pays.

**One re-measurement becomes due.** §28 (equal-risk sizing on a tradeable
universe) conditioned on dollar volume through the §0.9 defect. It must be re-run
before it informs any strategy built on this.

### Registered and spent

**Ledger: 100 → 101 trials**, hurdle 2.5341. `atr_percent_14` — the same effect,
rank correlation +0.91 — was not separately tested: one signal was registered to
spend one trial. **Forty-three signals measured; one survives.**

---

## §36 — §28 re-run with corrected volume: the drawdown half of its verdict does not survive — 2026-09-18

§28 was measured through the volume defect of DATA_DICTIONARY §0.9: its
admission rule multiplied the stored close by the stored volume in SQL, a raw
price times a volume already restated for every later split. §35 put §28 on the
list of results that must be re-run before anything is built on them. This is
that re-run.

**Strict: one change.** Same universe file, same 2010–2019 window, same four
disjoint samples of 149 + 149, same entry rule, costs, recovery and both arms.
Only the admission read moved, to `price_series`, so each session's dollar
volume is the money that traded. The admission window is kept at **two years**
(2008–2009) — that is what §28 measured, though its docstring says one; the
discrepancy is recorded, not fixed alongside.

### The universe it admitted

| clearing $1M/day | survived | died |
|---|---|---|
| §28 as recorded | 1,321 | 635 |
| corrected | **1,419** | **695** |

More of both, and proportionally more dead companies (+9.4% against +7.4%) —
the direction §0.9 predicted, since the old read excluded companies whose later
reverse splits deflated their stored volume.

### The result

| paired, equal-risk (B) minus equal-dollar (A) | as recorded | corrected |
|---|---|---|
| CAGR | +1.49 pp, sd 1.22, B better 3/4 | **+1.32 pp, sd 0.24, B better 4/4** |
| Sharpe | +0.11, sd 0.17, B better 3/4 | **+0.09, sd 0.04, B better 4/4** |
| max drawdown | **+4.04 pp worse, sd 2.59, 4 of 4** | **+0.59 pp, sd 5.23, 3 of 4** |

Per sample, corrected:

| sample | CAGR A → B | Sharpe A → B | max drawdown A → B |
|---|---|---|---|
| 0 | 4.02% → 5.09% | 0.17 → 0.25 | 11.50% → 13.61% |
| 1 | 5.15% → 6.34% | 0.32 → 0.38 | 12.68% → 17.63% |
| 2 | 5.24% → 6.66% | 0.34 → 0.41 | 10.32% → 12.62% |
| 3 | 3.16% → 4.77% | 0.06 → 0.22 | **20.74% → 13.73%** |

### What changes

**§28's headline does not survive.** It said equal-risk sizing *"worsened drawdown
in all four"* samples and that the drawdown effect was *"the more consistent of
the two"*. Corrected, drawdown is worse in three of four and the mean difference
is indistinguishable from zero (t +0.22); sample 3 reverses outright. The return
side, by contrast, becomes *more* consistent — four of four, with a paired
standard deviation a fifth of what §28 measured.

**Under §7's own criterion — Sharpe up and drawdown not worse — one sample of four
is now a capture** (sample 3), where §28 recorded none.

### What it must not be read as

**Still descriptive, unregistered, and on reused data.** A paired t of +10.96 on
CAGR is no more a finding than §28's +2.44 was; the discipline that refused that
number refuses this one. And a standard deviation estimated from four points is
itself noisy, so §28's power table — *"a registered test is feasible only for an
effect of about 1.5 pp/yr"* — cannot now be trusted in either direction.

**The baseline underneath it has a second, unrelated defect.**
`MovingAverageCross` keeps raw closes and never restates them at a split, so its
crossover signal, and arm B's 2 × ATR stop, are distorted in any window that
spans an ex-date. This strict re-run changed only the volume read, so that defect
is still in both columns. The new `FactorTilt` restates its history at splits and
was tested failing without it; the baseline should be brought to the same
standard before §28's comparison informs any sizing decision.

**No trials added; ledger unchanged at 102** (the §37 registration's trial is
already counted).

---

## §37 — low volatility as a portfolio: it predicts, and it does not pay — 2026-09-18

**Registered in [`prereg/LOW_VOLATILITY_BACKTEST_2026-09-18.md`](prereg/LOW_VOLATILITY_BACKTEST_2026-09-18.md)
at `452e59d`, before the strategy existed as code**; strategy, runner and verdict
committed at `073b4f6`, before any result. §35 found low volatility to *predict*
among tradeable securities on 2020–2025. This asked the other half — does a
portfolio built on it, run through the platform's own sizer, risk rules, stop
ladder, costs, fills and delisting handling, **make money**, and more than an
identical portfolio that chooses at random?

Four disjoint samples of 500, drawn in proportion from the 2,806 securities alive
and liquid on 2020-01-02; two arms differing only in selection; equal-dollar
sizing; a 63-session hold; 23 rebalances; delisting recovery 1.0, the assumption
least favourable to CALM.

### Verdict: not profitable

| sample | CALM CAGR | RANDOM CAGR | paired |
|---|---|---|---|
| 0 | 2.97% | 8.30% | −5.33 pp |
| 1 | 3.56% | 7.72% | −4.16 pp |
| 2 | 3.53% | 6.49% | −2.96 pp |
| 3 | 3.19% | 8.57% | −5.37 pp |

| criterion | reading | |
|---|---|---|
| 1 — CALM beats RANDOM in all four | **0 of 4** | fail |
| 2 — paired t > 2.5376 | mean **−4.46 pp**, t **−7.79** | fail |
| 3 — CALM beats 3% risk-free in ≥ 3 of 4 | 3 of 4 | pass |
| 4 — edge in both halves (walk-forward) | −4.20 pp / −4.73 pp | fail |
| 5 — survives 5× spread and slippage | 0 of 4 beat RANDOM, 0 of 4 beat risk-free | fail |

**The stop rule applies.** Low volatility stays a measured signal (§35) and does
**not** become a candidate strategy on this corpus. No other lookback, quintile,
holding period, position count, sizing rule or cost assumption is tried to
rescue it.

This is not a marginal miss. The random portfolio beat the calm one in every
sample, in both halves, by a margin eight standard errors from zero.

### Why a signal that predicts made a portfolio that loses — diagnostics, deciding nothing

**Two measurements, and a mechanism they fit.**

| sample 0, base costs, recovery 1.0 | CALM | RANDOM |
|---|---|---|
| positions closed because the security **stopped printing** | **25** | **2** |
| stop-loss exits | 56 | 134 |
| partial-profit exits | 30 | 99 |
| distinct securities held | 92 | 207 |

| CAGR by delisting assumption | recovery 1.0 | recovery 0.0 |
|---|---|---|
| CALM | +3.31% | **−17.57%** (max drawdown 70%) |
| RANDOM | +7.77% | +6.43% |

**1. The calmest names are disproportionately securities about to disappear.**
CALM held twelve times as many positions that stopped printing, and valuing them
at zero instead of their last price costs it 21 points a year against RANDOM's
1.3. The shape — near-zero volatility, then the series ends, with the last price
a fair value — is the shape of a **pending cash acquisition**, whose price is
pinned to the deal until it closes. That is **consistent with** these being
takeover targets, not established: the exits were not matched to acquisition
records. If they are, the extreme-calm tail of any volatility screen is partly a
merger-arbitrage book earning a thin spread, which is not what the factor means.

**2. The platform's own risk management already captures what low volatility
offered.** RANDOM's 134 stop-losses cut its falling names; its 99 partial profits
let its rising ones run. §35's edge was measured against a universe *held through*
its losers — much of low volatility's advantage there was that volatile losers
drag an unmanaged average down. A stop ladder removes most of that drag from the
random portfolio, and what remains of the calm one's edge does not cover a strong
market in which volatile stocks also rose.

**3. It is not costs.** CALM loses to RANDOM at default costs, before the stress.

**4. The 70% drawdown at recovery 0.0 is a warning for any future screen** on this
corpus: a low-volatility filter concentrates exactly the delisting assumption a
backtest is most exposed to.

### What stands

**§35 stands.** Low volatility does predict the cross-section of 63-session
returns among tradeable securities out of sample; that was measured and is not
withdrawn. What §37 establishes is that **predicting the cross-section and paying
as a portfolio are different claims**, and on this corpus the second fails — the
distinction §35 itself warned about: *"a factor that predicts is not yet a trade
that pays."*

**The result a strategy should take from this is about the platform, not the
factor:** a random selection run through the platform's stop ladder earned
+7.77% a year on 2020–2025 with a 17.7% drawdown. Whatever a future signal
claims, it must beat *that*, not beat cash.

### Registered and spent

**Ledger: 101 → 102 trials.** Forty-four measurements; one signal survives as a
signal, none as a strategy.

---

## §38 — the stop ladder against holding: inconclusive, and a trade of return for drawdown — 2026-09-18

**Registered in [`prereg/STOP_LADDER_2026-09-18.md`](prereg/STOP_LADDER_2026-09-18.md)
at `dc3499a`**, two-sided, on **2010–2019** because the question came from
2020–2025. Amendment 1 (engine fix) and Amendment 2 (a minimum price) were both
written before any verdict was read.

**Two defects surfaced first, and both are bigger than this test.**

### 1. The backtest engine never moved a stop

`EventDrivenEngine` built every position with its **initial** stop and discarded
`CyclePlan.stop_updates`. The ladder computed breakeven at 1R and a trailing stop
from 1.5R, and the engine threw both away. The pilot's exit mix exposed it —
66 partial profits at 2R, not one trailing exit, a shape the ladder cannot
produce. **Every backtest this platform has run held its initial stop for the life
of the position:** §7, §28, §36, §37. Fixed at `485a613`; trailing exits went from
0 to about 70 per sample on real data. §7, §28 and §36 are due to be re-run.

### 2. A $0.0001 stock could be bought

Two of the four registered samples went to **negative equity**. Securities
printing at $0.0001–$0.0002 passed eligibility — no minimum price, a 20-session
turnover window still holding their last normal weeks, a flat sub-penny window
reading as calm — and equal-dollar sizing bought **20.6 and 77.6 million shares**.
At $0.005 a share the commission was **$102,911 and $388,007** on ~$2,000
positions; equity reached −$520,343 and −$650,233. FactorTilt now requires
`LiquidityConfig.min_price` ($5), the platform's own declared minimum, which both
registrations' "every other value at its default" clause already included but the
strategy never read (`29111dc`). **The platform's sizer will still open a position
whose commission is fifty times its value** — that is trading logic, raised with
the owner separately.

### §37 holds through all three runs

| §37 run | CALM − RANDOM | t | criteria failed |
|---|---|---|---|
| original engine | −4.46 pp/yr | −7.79 | 4 of 5 |
| stop moves applied | −4.14 pp/yr | −3.68 | 4 of 5 |
| stop moves + $5 minimum | **−4.35 pp/yr** | **−3.04** | **4 of 5** |

Low volatility is not profitable as a portfolio on this corpus, under any version
of the machinery. The verdict never came close to moving.

### The registered result: INCONCLUSIVE

2010–2019, four disjoint samples of 500 from the 2,393 securities alive and liquid
on 2010-01-04, identical RANDOM selections, one change between the arms.

| sample | LADDER CAGR | HOLD CAGR | paired |
|---|---|---|---|
| 0 | 4.82% | 5.65% | −0.83 pp |
| 1 | 5.26% | 9.81% | −4.54 pp |
| 2 | 2.83% | 4.33% | −1.50 pp |
| 3 | 5.20% | 5.95% | −0.75 pp |

| criterion, "the ladder costs money" direction | reading | |
|---|---|---|
| 1 — HOLD ahead in all four | **4 of 4** | met |
| 2 — paired t below −2.5444 | mean **−1.90 pp**, t **−2.13** | **not met** |
| 3 — HOLD's Sharpe ahead in ≥ 3 of 4 | 3 of 4 | met |
| 4 — negative in both halves | −1.48 pp / −2.32 pp | met |
| 5 — HOLD ahead in all four at 5× costs | 4 of 4 | met |

**Four of five, short on significance.** By the rule fixed in advance that is
**inconclusive**, and nothing moves: the default exits stand, and there is no
licence to revisit them. The direction is consistent — every sample, both
halves, both cost settings — but a paired t of −2.13 across four samples is not
past a two-sided hurdle of 2.5444, and the registration does not bend for a
result that is consistent without being significant.

### What the ladder does, as diagnostics that decide nothing

| 2010–2019, base costs, recovery 1.0 | LADDER | HOLD |
|---|---|---|
| CAGR | +4.53% | +6.43% |
| **maximum drawdown** | **13.17%** | **23.66%** |
| Sharpe | 0.21 | 0.29 |
| exits: stop-loss / trailing / partial | 172 / 70 / 104 | — |

**The ladder trades return for drawdown.** It gave up about 1.9 points a year and
cut the worst drawdown by more than ten points. It is a risk control doing what a
risk control does, and on this decade its cost was larger than its protection in
Sharpe terms.

**On the years that suggested the question, the picture inverts.**

| 2020–2025 — the data that prompted this test | LADDER | HOLD |
|---|---|---|
| CAGR | **+7.71%** | +3.67% |
| maximum drawdown | 16.97% | **34.82%** |

A decade of steady rises (2010–2019) against one containing the 2020 crash and
the 2022 bear market: stops paid for themselves only where the market fell hard.
**That is why the question could not be tested on 2020–2025** — the observation
that prompted it was a property of that regime, and on the decade it had not seen
it did not hold. This is recorded as a regime contrast, not a finding: one
decade each, unregistered on the second.

### What stands

- **The default exits stand**, unconfirmed and unrefuted.
- **Every engine result before `485a613` ran a different stop ladder** from the
  one the platform declares; §7, §28 and §36 are due to be re-run.
- **Any strategy's benchmark is now two numbers, not one:** a random selection
  through the ladder earned +4.53% on 2010–2019 and +7.71% on 2020–2025, and held
  to the clock +6.43% and +3.67%. A signal worth building must beat the better of
  the two in its own window.

### Registered and spent

**Ledger: 102 → 104 trials** (two-sided), hurdle 2.5444. Forty-five measurements;
one signal survives as a signal, none as a strategy, and the platform's own risk
control is neither confirmed nor refuted.

---

## §39 — §7, §28 and §36 on a faithful engine: equal-risk sizing is not an improvement — 2026-09-18

§38 recorded that every engine result before `485a613` ran without the stop
ladder's breakeven and trailing moves, and that the moving-average baseline under
§7, §28 and §36 never restated its history at splits. Both are now fixed —
stop moves at `485a613`, the baseline's split restatement at `35b10f7`, which
also makes the baseline refuse to run without a split source. This re-runs the
three sizing results, **one correction at a time**, so each change can be
attributed.

Strict re-runs otherwise: same script (`backtest_volatility_sizing.py`), same
universe files, same periods, samples, costs and recovery as recorded; §36's
corrected admission read from §0.9 retained.

### §7 — equal dollar (A) against equal risk (B), 800 securities

| | as recorded | + stop moves | + split restatement |
|---|---|---|---|
| **2000–09** return A → B | −14.69% → −6.61% | +2.87% → +19.90% | **−4.41% → +9.09%** |
| Sharpe A → B | −0.40 → −0.30 | −0.27 → −0.06 | **−0.36 → −0.16** |
| max drawdown A → B | 35.82% → 33.25% | 22.70% → 26.46% | **26.44% → 26.18%** |
| **2010–24** return A → B | +22.02% → +27.14% | +80.70% → +56.52% | **+71.40% → +57.58%** |
| Sharpe A → B | −0.14 → −0.07 | 0.15 → 0.06 | **0.11 → 0.06** |
| max drawdown A → B | 28.37% → 29.92% | 26.94% → 37.49% | **25.53% → 36.59%** |

**In sample, B still captures** — return up, Sharpe up, drawdown fractionally
better. **Out of sample it now loses on all three**: lower return, lower Sharpe,
and eleven points more drawdown. §7 recorded *"a small, consistent, replicating
improvement in risk-adjusted return"*; on a faithful engine it did not replicate.
The out-of-sample reversal appeared with the stop-move fix alone.

### §36 / §28 — the tradeable universe, 2010–2019, four samples

| paired, B minus A | as recorded (§36) | + stop moves | + split restatement |
|---|---|---|---|
| CAGR | +1.32 pp, 4/4 | +0.50 pp, t +0.62 | **+0.57 pp, t +0.49, 3/4** |
| Sharpe | +0.09, 4/4 | +0.01 | **+0.01, 2/4** |
| max drawdown | +0.59 pp, 3/4 | +4.22 pp | **+5.37 pp worse, t +3.79, 4 of 4** |

**§36's consistent return and Sharpe gain was an artefact of the missing stop
moves**, and it is withdrawn: on a faithful engine the return difference is
noise (t +0.49) and the Sharpe difference nothing. **§36's withdrawal of §28's
drawdown finding is itself reversed** — drawdown is worse under equal-risk sizing
in all four samples again, by 5.4 points. §28's original description, *"equal-risk
sizing buys return with drawdown"*, was half right: it buys drawdown, and on a
faithful engine it does not buy return.

### What stands

**Equal-risk sizing is not an improvement on this corpus.** No reliable return or
Sharpe gain on a tradeable universe; reliably worse drawdown; and out of sample
over 2010–2024 it is worse on every measure. The project's only prior positive
result — §7 — does not survive a faithful engine.

All three remain descriptive: §7 was registered but its criterion was graded
before these defects were known, and §28/§36 were never registered. No trials are
added; **ledger unchanged at 104.**

**Every engine result on record now runs on the corrected engine**: §37 (three
runs, §38), §38's registered test, and §7/§28/§36 here.

---

## §40 — four fundamental signals, point-in-time: none passes — 2026-09-18

**Registered in [`prereg/FUNDAMENTALS_2026-09-18.md`](prereg/FUNDAMENTALS_2026-09-18.md)
at `adc037d`**, before any signal was computed; the point-in-time module, scan and
verdict committed at `d345110` before any result. Phase 6's first measurement on
the fundamentals the corpus holds — every earlier line on this scoreboard read
price and volume.

Four published anomalies with directions fixed by the literature: earnings
surprise (SUE, Bernard & Thomas), gross profitability (Novy-Marx), asset growth
(Cooper, Gulen & Schill), accruals (Sloan). Every value **as first filed**, usable
only if filed strictly before the session; fiscal Q4 derived from annual minus
nine-month and known at the 10-K date. 2013–2025, common grid, above $1M/day and
$5, the §35 inclusion rules. 2,511,378 rows scanned, 1,458,292 through the
inclusion rules.

### Result

| signal | declared | IC (t) | favoured fifth vs universe, per year | halves | vol bands | verdict |
|---|---|---|---|---|---|---|
| **SUE** | + | **+0.0174 (+2.13)** | **+2.40%** | +0.0147 / +0.0193 | 4/5 | **fails C1 only** |
| gross profitability | + | +0.0191 (+1.50) | −1.67% | +0.0286 / +0.0114 | 5/5 | fails |
| asset growth | − | +0.0001 (+0.00) | −3.89% | +0.0033 / −0.0033 | 0/5 | fails |
| accruals | − | **+0.0155 (+1.81), wrong sign** | −5.37% | wrong sign both | 2/5 | fails |

Calibration 1.94–2.13 across the four (limit 2.3); undetermined volume 1.8–2.1%
(bound 5%); median breadth 996–2,047 securities per date; 52 calendar blocks.

**SUE is the near miss.** Three of four criteria pass — the high-surprise fifth
compounds 2.4 points a year ahead of its universe, the sign holds in both halves
and in four of five volatility bands — and the information coefficient misses
the hurdle, t +2.13 against 2.5576. The smallest effect the test could detect was
0.0208; SUE measured 0.0174. Consistent with post-earnings drift that exists but
has weakened below what thirteen years of this corpus can resolve. **It is closed
by the stop rule** all the same: no other definition, lookback, scaling or
freshness window of it is tried on this corpus.

**The other three are not near misses.** Asset growth is exactly zero. Accruals
points the wrong way. And for all three the favoured fifth *underperforms* its
universe compounded — low asset growth and low accruals select shrinking,
cash-hoarding firms that lagged over a strong market.

### What stands

**The four are closed on this corpus**, and per the stop rule no further
fundamental signal is tested without a registration stating what it would add
that these did not already ask.

**What the result does not rule out**, recorded so it is not mistaken for a
licence: these were measured at the 63-session Swing horizon. The literature
measures most of them over twelve months, which is the Retirement mandate's
horizon, not this one's. A registration at that horizon would ask a different
question and would have to say so, disclose §40, and pay its own trials. It is
not taken here.

### Registered and spent

**Ledger: 104 → 108 trials**, hurdle 2.5576. Forty-nine measurements; one signal
survives as a signal (§35), none as a strategy.

---

## §41 — the same four at twelve months, dead companies kept: none passes — 2026-09-18

**Registered in [`prereg/FUNDAMENTALS_12M_2026-09-18.md`](prereg/FUNDAMENTALS_12M_2026-09-18.md)
at `9547237`**, before any signal was computed against a twelve-month outcome;
scan, verdict and Amendment 1 committed at `5b18006` before any result. §40's
four signals, definitions unchanged, asked the Retirement horizon's question —
the 252-session return, which is also the horizon the literature measured them
at. §40's results were disclosed in the registration.

Three things are new, all decided before any result:

- **Dead companies are kept.** §40's sampler dropped any security with no print
  on its outcome date. Over a year that drops every company that died in it.
  `tradeit.signals.sampling.outcome_or_terminal` keeps a security that never
  trades again, measured to its last traded close, and the verdict prices it at
  recovery **1.0 and 0.0; a pass needed both**. They were 72,294 of 1,386,822
  included observations (**5.2%**). Spot-checked: ODP and SPNS, terminal in
  December 2025, were both taken private that month, and securities stop at a
  steady 25–55 a month through 2025, so no gap in coverage is being read as a
  death.
- **A small-sample hurdle.** Twelve years hold twelve independent yearly
  outcomes. A t from 12 block means clears the normal hurdle (2.5702) by chance
  2.6% of the time, not 1.0%, so the hurdle was the Student-t threshold with
  the same tail: **3.0967** (`small_sample_hurdle`).
- **Criterion 5**: the signal must also point the declared way over the part of
  the year after the first 63 sessions, which §40 had already seen.

Amendment 1: the shared geometric-mean helper silently dropped −100% returns,
which at recovery 0.0 would have quietly removed every dead company; criterion 2
at 0.0 used the equal-weight buy-and-hold return instead. Synthetic check before
the run: planted + and − effects passed, noise failed, and a first-quarter-only
effect failed criterion 5 alone.

### Result

ICs in the declared direction (positive supports the registration).

| signal | R | IC (t) | detectable | favoured fifth/yr | halves | vol bands | after Q1 | verdict |
|---|---|---|---|---|---|---|---|---|
| **SUE** | 1.0 | +0.0274 (+1.73) | 0.0490 | **+2.37%** | +0.0139 / +0.0409 | 5/5 | +0.0243 | **fails C1 only** |
| **SUE** | 0.0 | **+0.0429 (+2.92)** | 0.0455 | **+2.80%** | +0.0336 / +0.0522 | 5/5 | +0.0344 | **fails C1 only** |
| gross profitability | 1.0 | +0.0336 (+1.54) | 0.0675 | −1.48% | pass | 5/5 | +0.0276 | fails |
| gross profitability | 0.0 | +0.0277 (+1.34) | 0.0638 | −0.67% | pass | 4/5 | +0.0235 | fails |
| asset growth | 1.0 | +0.0075 (+0.28) | 0.0843 | −3.57% | fail | 0/5 | +0.0055 | fails |
| asset growth | 0.0 | −0.0016 (−0.06) | 0.0765 | −2.49% | fail | 0/5 | +0.0004 | fails |
| accruals | 1.0 | **−0.0291 (−1.95)** | 0.0462 | −5.06% | wrong both | 3/5 | −0.0326 | fails |
| accruals | 0.0 | **−0.0318 (−2.42)** | 0.0407 | +0.38% (median −0.68%) | wrong both | 2/5 | −0.0349 | fails |

Calibration 2.06–2.29 (limit 2.583); undetermined volume 1.9–2.1% (bound 5%);
12 blocks; median breadth 998–2,063 securities per date.

**SUE is the near miss again, and more clearly than at 63 sessions.** Criteria 2
to 5 pass at both recoveries: the high-surprise fifth compounds about 2.4–2.8
points a year ahead of its universe, the sign holds in both halves, in all five
volatility bands, and after the first quarter. Only significance fails. **The
test could not have seen it:** the smallest detectable IC is 0.049 against a
measured 0.027 at R = 1.0. Twelve years give twelve yearly outcomes, and that is
the ceiling on this corpus, not a defect of the test.

**The recovery moves SUE more than anything else here.** Its IC rises from
0.027 to 0.043 when dead holdings are worth nothing: low-surprise companies die
more often, so the signal partly works by avoiding them. §16 found most of the
dead were bought out, so R = 1.0 is the realistic reading, and on that reading
SUE is at t +1.73.

**The hurdle choice did not decide it.** At R = 0.0 SUE's t of +2.92 would have
cleared the normal 2.5702 and fails the registered 3.0967. At R = 1.0 it fails
either one, and a pass needed both.

**Accruals points the wrong way at both horizons**: high-accrual companies did
better, t −2.42 at R = 0.0, the opposite of Sloan (1996). It is not a finding
either way. No test was registered in that direction, and turning one around
after the fact is what the ledger exists to stop. Asset growth is zero again.
Gross profitability has a positive IC while its favoured fifth lags, the same
pattern as §40.

### What stands

**All four are closed at both horizons on this corpus.** Per the stop rule no third
horizon is tried. The fundamentals held today, four as-filed metrics from 2009,
have answered what they can. The next fundamental question needs **different
information**: analyst consensus for a real earnings surprise, which FMP does not
supply in usable form (PHASE_06_VENDOR_MATRIX.md, measured 2026-09-18), or
shares outstanding for value signals, which the corpus holds for 143 securities.

**Recorded, not acted on:** SUE is the one fundamental line that is consistent in
direction on every cut at both horizons. What the corpus lacks is enough
independent years to establish it, not evidence against it. That is a reason
to add information, not to loosen a threshold.

### Registered and spent

**Ledger: 108 → 112 trials.** The normal-scale hurdle is 2.5702; tests from few
blocks use `small_sample_hurdle`. Fifty measurements; one signal survives as a
signal (§35), none as a strategy.
