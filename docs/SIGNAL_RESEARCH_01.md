# Signal research 01 — do the scoring factors predict anything?

**Run date:** 2026-09-09 · **Corpus:** `research-01` · **Code:** `611aa17`

A record of what was measured and what it means. Phase reports in this
repository state what was true when they were written and are not rewritten
later; this follows that convention.

---

## 0. The answer

**No signal tested is economically useful, at either horizon.** Several show
real and strong statistical relationships. None produces a quantile spread that
can be distinguished from noise.

That is a more specific finding than "nothing works", and the difference
matters for what to do next.

## 1. What was run

| | |
|---|---|
| universe | 400 securities tradeable on 2000-01-03 with ≥250 bars — **200 that survived to 2010 and 200 whose prices stopped before it** |
| period | 2000-01-03 to 2009-12-31 |
| observations | 27,923 at a 21-session horizon; 9,130 at 63 |
| sampling | **non-overlapping** — one observation per security per horizon |
| costs | `CostConfig` defaults, charged on both legs, scaled by the turnover the horizon forces |
| trials | 20 (9 signals × 2 horizons, with the one derived-direction signal counting double) |
| hurdle | multiple-testing floor of \|t\| > 1.90 on top of the 2.0 requirement |

Four ways a study like this flatters itself, and what was done about each:
survivorship (the universe includes the failures), overlapping observations
(sampling is non-overlapping), costs (reported separately from detectability,
and only the economic number gates), and multiple comparisons (every signal and
horizon is a counted trial).

## 2. Results

### 21-session horizon

```
signal               dir       n     IC      t   sp t  mean sp  med sp   verdict
realized_vol_60       dn  27,923 -0.060 -10.01   0.99  126.46%  -1.63%  outlier_dependent
atr_percent_14        dn  27,923 -0.049  -8.24   0.99  126.80%  -1.28%  outlier_dependent
momentum_252          up  27,923  0.023   3.88  -1.13 -144.72%   0.45%  outlier_dependent
dist_from_sma_50      up  27,923 -0.019  -3.14  -1.12 -142.90%  -0.35%  spread_not_established
momentum_126          up  27,923  0.018   3.00  -1.23  -11.26%   0.26%  outlier_dependent
dist_from_sma_200     up  27,923  0.015   2.58  -1.84  -25.35%   0.38%  outlier_dependent
momentum_21           dn  27,923 -0.013  -2.15  -1.09 -138.46%  -0.57%  spread_not_established
rsi_14                dn  27,923 -0.012  -1.97  -0.51   -5.95%  -0.34%  not_detectable
relative_volume_20    up  27,522  0.010   1.73  -0.93 -120.32%   0.43%  not_detectable
```

### 63-session horizon

```
signal               dir       n     IC      t   sp t  mean sp  med sp   verdict
atr_percent_14        dn   9,130 -0.078  -7.46   0.99  591.45%  -5.55%  outlier_dependent
realized_vol_60       dn   9,130 -0.071  -6.79   0.94  561.20%  -4.65%  outlier_dependent
relative_volume_20    dn   9,005 -0.029  -2.79  -1.00 -603.50%  -1.43%  spread_not_established
momentum_126          up   9,130  0.021   2.00  -0.42  -15.21%   0.88%  outlier_dependent
momentum_21           dn   9,130 -0.016  -1.51  -1.01 -603.25%  -2.72%  not_detectable
dist_from_sma_200     up   9,130  0.015   1.41  -0.74  -27.00%   0.76%  not_detectable
momentum_252          up   9,130  0.012   1.10  -1.10 -656.79%   0.71%  not_detectable
rsi_14                dn   9,130  0.004   0.41  -0.46  -16.45%  -0.53%  not_detectable
dist_from_sma_50      up   9,130  0.001   0.12  -1.02 -607.94%  -0.73%  not_detectable
```

**The `net/yr` column the runner also prints is omitted here on purpose.** It
is derived from the mean spread, and no mean spread in this study is
established, so the annualised figures (±1500% to ±2600%) are arithmetic on an
unusable estimate rather than returns. They are exactly the numbers a reader
would quote by mistake.

## 3. What is real

**The rank relationships are strong and several are textbook.**

* **Low volatility outperforms**, clearly and at both horizons, strengthening
  with horizon (IC −0.060 at 21 sessions, −0.078 at 63; t = −10.01 and −7.46).
  This is the largest effect in the study by a wide margin.
* **Momentum behaves the way the literature says it does**: positive at 252
  sessions (t = +3.88) and negative at 21 (t = −2.15). Reversal at one month,
  momentum at a year, reproduced independently here. That is a check on the
  measurement as much as a finding about the market.
* `dist_from_sma_50` was **declared** positive and the corpus disagrees
  (IC −0.019, t = −3.14). The declaration was left alone. A prior that flips
  whenever the data disagrees is not a prior, and avoiding that free parameter
  is the whole reason to declare a direction.

## 4. What is not real, and why it matters more

**Not one quantile spread is statistically established.** Every spread
t-statistic in both tables falls between **−1.84 and +0.99**, against a
threshold of 2.0.

That is the finding. A portfolio earns the arithmetic mean of its holdings, so
the mean quantile spread is the number that would have been realised — and on
this universe it cannot be estimated. The return distribution is heavy enough
that a single 21-session observation of **+7,609%** moves a bucket mean by more
than a hundred percentage points. With 27,923 observations the estimate is
still dominated by its tail.

The rank correlation is significant precisely *because* it ignores magnitude.
The spread is not, precisely *because* it cannot.

**So: a real ordering, with no demonstrable way to monetise it on this
universe.**

## 5. What this says about the scoring weights

`ScoringConfig` currently weights six factors by judgement:

```
relative_strength 0.25 · pattern_quality 0.20 · breakout_confirmation 0.20
fundamental_quality 0.15 · sector_strength 0.10 · volume_accumulation 0.10
```

* **`relative_strength` (0.25)** — its price proxies show the textbook momentum
  pattern, but sign-dependent on horizon. A single weight cannot express
  "positive at a year, negative at a month".
* **`pattern_quality` (0.20)** — the moving-average proxies are weak, and the
  50-day one points the wrong way.
* **`volume_accumulation` (0.10)** — the weakest signal tested at 21 sessions
  (t = 1.73) and sign-unstable across horizons.
* **`breakout_confirmation`, `fundamental_quality`, `sector_strength`** — not
  tested. The first two need the pattern and fundamentals engines; **the third
  cannot be tested at all, because the corpus has no sector classification.**

**No weight change is proposed.** Nothing here establishes a tradeable edge for
any factor, and replacing one guess with another guess dressed in a t-statistic
would be worse than leaving the guesses labelled as guesses.

## 6. The obvious next experiment

**Apply a liquidity filter and re-run.** This universe has a median price of
$12 and includes microcaps whose 21-session returns reach +7,609%. `LiquidityConfig`
already declares `min_avg_dollar_volume` and its neighbours, and the study
simply did not use them. A tradeable universe would have far thinner tails, and
the spread that is currently inestimable may become estimable.

That is the single change most likely to convert "a real ordering nobody can
monetise" into a number worth acting on — or to confirm that it is not one.

Second: the study drops observations in the final horizon before a delisting,
because a forward return needs a forward window. That systematically excludes
the terminal collapse, which is the largest move a failing company makes. It is
a small fraction of observations and it biases in the flattering direction, so
it should be fixed before any positive result is believed.

---

## 7. The liquidity experiment, run — 2026-09-09, code `ccf9405`

§6 predicted that a tradeable universe would thin the tails enough to make the
spread estimable. **It partly did, and the part that did not is the more
interesting half.**

The filter is `LiquidityConfig`'s own floors — price ≥ $5, 20-day average
dollar volume ≥ $10M — applied **per observation on trailing data**, so a name
contributes only for the stretches it was actually tradeable. Filtering
securities by their average liquidity over the window would have selected the
ones that *became* liquid, which is the look-ahead this study exists to avoid.
About 24% of candidate observations survive.

### What improved

| | unfiltered | filtered |
|---|---|---|
| worst-case outcome (50 securities) | +7,609% | **+57%** |
| 99th percentile outcome (800 securities, 21s) | — | **+39.8%** |
| spreads with \|t\| > 2 at 21 sessions | **0 of 9** | **4 of 9** |
| annualised nets | ±1,500% | −42% to +34% |

Four spreads became statistically established for the first time
(`dist_from_sma_50` −2.61, `relative_volume_20` −2.46, `dist_from_sma_200`
−2.21, `rsi_14` −2.17). The numbers stopped being arithmetic on unusable
estimates.

### One signal passed everything

```
horizon 63, 4,310 observations
relative_volume_20   dn   IC -0.069   t -4.54   sp t -2.15
                     mean sp -8.39%   med sp -3.24%   net +32.7%/yr
                     ECONOMICALLY_USEFUL
```

**It should not be believed yet, and here is why.** It is the one signal whose
direction was `DERIVED` rather than declared, so it spent two of the twenty
trials. It shows nothing at the 21-session horizon in the same run (t = −1.17).
And its sign is unstable across specifications — in the unfiltered study it was
*positive* at 21 sessions and negative at 63. A relationship that changes sign
when the universe or the horizon changes is the signature of fitting noise,
and one pass out of twenty trials is what chance produces.

**It is a hypothesis to test out of sample, not a finding.** That is precisely
what the walk-forward harness is for, and it is the next thing to do with it.

### The half that did not improve, and cannot

The volatility signals remain the strongest relationships anywhere in this
study — `atr_percent_14` at t = −5.15 and `realized_vol_60` at t = −4.33 over
63 sessions, significant at *every* specification tried, filtered and
unfiltered, at both horizons, always with the same sign. And both remain
`OUTLIER_DEPENDENT`: mean spreads of +0.69% and +1.03% against medians of
−5.54% and −5.28%.

More data will not fix this, because the problem is structural. **Sorting on
volatility sorts on the variance of the very thing being averaged.** The
high-volatility bucket contains the extreme outcomes by construction — that is
what putting it there means — so the mean-spread estimator has its worst
variance exactly where the signal is strongest. A liquidity filter thins the
tail; it cannot make a volatility-sorted bucket well-behaved.

The consequence is worth stating plainly: **the clearest relationship in this
corpus is one whose tradeable value cannot be established by a quantile spread
at all.** Measuring it needs a different estimator — a volatility-targeted
position size rather than an equal-weight bucket — which is a portfolio
construction question, and Phase 8 already has the machinery for it.

### A declared prior the data keeps contradicting

`dist_from_sma_50` was declared POSITIVE from the trend-following literature.
It has now come out negative twice: t = −3.14 unfiltered, t = −2.77 filtered,
with an established spread the second time. It is still declared positive here,
and it is still not flipped.

If a future study wants to test it as a mean-reversion signal, **the
declaration has to be made before that run, not inherited from this paragraph.**
Writing down that the data disagreed is honest; treating that note as
permission to flip the sign next time is how a prior quietly becomes a fitted
parameter.

### Where this leaves the weights

Unchanged, and for the same reason as §5. Nothing here has survived
out-of-sample testing, because nothing has been tested out of sample yet.

---

## 8. The out-of-sample test — 2026-09-09, code `754bde3`

§7 recorded `relative_volume_20` as a hypothesis and named the test. Here it is.

**Pre-registered before the run:** the signal, the direction (`NEGATIVE`, taken
from the in-sample result), the horizon (63 sessions), the quantile fraction,
the liquidity floors, and the period. One signal, one horizon, **one trial** —
so no multiple-testing penalty applies, and the runner computed the hurdle as
zero rather than being told.

**The period had never been looked at.** The study covered 2000–2009; the
corpus runs to 2026. The test used 2010-01-04 to 2024-12-31: 800 securities
drawn the same way, 400 that survived and 400 whose prices stopped, 53% of the
eligible population failing within the window.

### It failed

| | in-sample 2000–2009 | out-of-sample 2010–2024 |
|---|---|---|
| observations | 4,310 | **9,583** |
| information coefficient | **−0.069** | **+0.009** |
| IC t-statistic | **−4.54** | **+0.93** |
| spread t | −2.15 | −0.21 |
| mean spread | −8.39% | −0.14% |
| net of costs | **+32.7%/yr** | **−0.2%/yr** |
| verdict | `ECONOMICALLY_USEFUL` | `NOT_DETECTABLE` |

**The sign flipped and the magnitude went to zero.** Not weakened — reversed
and vanished. The net after costs is −0.2% a year, which is nothing.

This is not a power problem: the out-of-sample sample is **more than twice the
size** of the one that produced the finding. It is not a regime excuse either,
though the periods do differ — equal-weight buy-and-hold ran at −10.07%/yr
in-sample and +3.64%/yr out.

### What this vindicates

One signal passed every in-sample gate out of twenty trials. **One in twenty is
exactly what chance produces at the 5% level, and that is what it was.**

Everything built to catch this, caught it:

* the **trial ledger** counted twenty and said the hurdle had risen
* the **derived-direction rule** charged that signal two trials rather than one
* §7 refused to call it a finding, listing the sign instability and the single
  passing horizon as reasons
* **pre-registration** made the out-of-sample test unambiguous — there was no
  room to adjust the direction, the horizon or the universe after seeing the
  answer

Had the weights been changed on the in-sample result, the system would now be
trading noise with a t-statistic attached to it.

### Where this leaves the weights, finally

**Unchanged, and now for a demonstrated reason rather than an absence of
evidence.** The single best candidate this research produced does not survive
contact with data it has not seen. The declared weights in `ScoringConfig`
remain judgement, they remain labelled as judgement, and nothing measured here
justifies moving any of them.

That is a real result. A research loop that can only ever confirm is not a
research loop, and this one just rejected its own best finding.

### What is worth doing next

Not more signals of this kind. The two things this study says are worth
pursuing:

1. **The volatility relationship**, which was significant at every
   specification tried and is the only candidate never to change sign. §7
   explains why a quantile spread cannot value it — sorting on volatility sorts
   on the variance of the thing being averaged — and points at
   volatility-targeted sizing, which is a Phase 8 mechanism rather than a
   signal.
2. **The factors that could not be tested at all.** `breakout_confirmation` and
   `fundamental_quality` need engines this study did not use, and
   `sector_strength` carries a 0.10 weight on a classification the corpus does
   not contain.
