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
