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

### Factors carrying weight that have not been tested at all

| scoring factor | weight | why not |
|---|---|---|
| `breakout_confirmation` | 0.20 | needs the breakout engine; this study used only price and volume kernels |
| `fundamental_quality` | 0.15 | needs the fundamentals engine over `security_fundamental_facts` |
| `sector_strength` | 0.10 | **the corpus contains no sector classification.** This weight rides on data that does not exist |

**0.45 of the weight — 45% — has never been measured.**

## Scoring weights: still unchanged

```
relative_strength 0.25 · pattern_quality 0.20 · breakout_confirmation 0.20
fundamental_quality 0.15 · sector_strength 0.10 · volume_accumulation 0.10
```

Nothing measured justifies moving any of them. The one candidate that passed
every in-sample gate failed out of sample decisively. See §8.

## Next test: volatility, measured as a portfolio rather than a spread

**Status: starting.**

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
