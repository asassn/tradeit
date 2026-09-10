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

### Factors carrying weight that have not been tested at all

| scoring factor | weight | why not |
|---|---|---|
| `breakout_confirmation` | 0.20 → 0.2222 | **still untested, and structurally hard to weight** — see §10 |
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
