# Signal testing — where it ended, and what it leaves — 2026-09-18

A plain summary for deciding whether to move on. The detail, every number and
every correction, is in [`SIGNAL_SCOREBOARD.md`](SIGNAL_SCOREBOARD.md) §1–§50 and
the registrations in [`prereg/`](prereg/). This document repeats only what a
decision needs.

## The answer in one paragraph

**Nothing measured on this corpus is yet profitable enough to trade.** One signal
survives as a *signal*: low volatility predicts which tradeable stocks do better
over the next quarter, out of sample (§35). It does **not** survive as a
*strategy*: a portfolio built on it lost to a randomly chosen one in every
sample, under every version of the engine (§37, three runs). Everything else —
fifty other measurements across price, volume, momentum, patterns, size,
thirty-two classical indicator arms across four horizons, a trend-strength lead,
two sizing rules and four point-in-time fundamental signals at two horizons — is
null, failed out of sample, or inconclusive. The closest miss is earnings surprise, at both horizons (§40,
§41): every criterion but significance passes, and twelve years of data cannot
resolve an effect that size. Along the way the corpus, the
statistics and the backtest engine were each found to be wrong in ways that
flattered results, and each was fixed.

## What was tested

| line | verdict | where |
|---|---|---|
| 17 price/volume signals (momentum, trend distance, RSI, sector, relative volume, OBV…) | null or failed out of sample | §1–§25 |
| `pattern_quality`, by its real twelve detectors | null; re-measured on corrected data, unchanged | §13, §31 |
| `relative_strength` engine | fails out of sample | §18, §19, §30 |
| market capitalisation | null | §27 |
| **24 classical indicators** — Bollinger, TD counts, TEMA, Fibonacci, RSI, CCI, MFI, ADX, Aroon… | **null**, and the null is bounded: nothing above IC ~0.035 exists to find | §32, §33 |
| ADX, the screen's one lead, on fresh data | **closed** — significant in the wrong direction | §34 |
| **low volatility, as a signal** | **passes** out of sample among tradeable stocks, 2020–2025 | §35 |
| **low volatility, as a portfolio** | **not profitable** — −4.35 pp/yr against random picks | §37, §38 |
| the platform's stop ladder against holding | **inconclusive** — trades ~2 pp/yr of return for half the drawdown on 2010–19 | §38 |
| equal-risk against equal-dollar sizing | **not an improvement** on a faithful engine | §39 |
| **four fundamental signals** — earnings surprise, gross profitability, asset growth, accruals — point-in-time | **none passes**; earnings surprise misses only on significance (t +2.13 vs 2.56) | §40 |
| the same four at **twelve months**, companies that died kept | **none passes**; earnings surprise again misses only on significance (t +1.73 vs 3.10), undetectable at this size | §41 |
| the **delisting-recovery assumption**, measured from filings | **settled**: 73% of dead holdings were paid, 6.1% wiped out. The zero-recovery reading was counterfactual, and low volatility's −20.65%/yr was an artefact of it | §50 |
| a **market-regime gate** — invest only above a 50-session trend | **fails**: the control loses 1.37 pp/yr in 0 of 4 samples and its drawdown *rises*. Timing this corpus by its own trend is closed | §49 |
| the combination **on the held-out 2020–2025** | **does not replicate**: −2.30 pp/yr against random in 0 of 4 samples, having been +2.54 pp in 4 of 4 on 2013–2019. The line closes | §48 |
| a **volatility-scaled stop** against the platform's fixed 8% | **refused**: every arm worse, including the control, and the calm arm's delisting exits rose. A stop tight enough to bind on a quiet security sells it on noise | §47 |
| **low volatility × earnings surprise, as a portfolio** | **fails as registered** — but beats random by +2.54 pp/yr and beats **both** its own components, if delisted holdings kept value; loses if they did not | §46 |
| the daily-bar **execution assumption**, audited on real minutes | stops fill below the stop 57.6% of the time, costing a median 0.007R in §44's band; **stops tighter than ~2% cannot be honestly simulated on daily bars** | §45 |
| **pattern breakouts as complete trades** — entry, stop, 63-session exit, costs, 147,916 trades | **fails**: +0.91% a trade, and a random stock bought the same day with the same stop made +0.91% too | §44 |
| **eight swing indicators at 5 and 10 sessions** — RSI, stochastic, Bollinger %b, 10-day reversal, MACD, 9/21 EMA cross, 50/200 cross, 52-week rank | **none passes**; the best edge is +0.11% a hold against a 0.20% round trip. **Costs fail before significance does** | §42 |

Ledger: **140 trials**; hurdle |t| > 2.5702 on the normal scale, restated with `small_sample_hurdle` for tests built from few blocks (3.0967 at twelve). Every result above is
judged against the hurdle in force when it was registered.

## The benchmark any future idea must beat

A **random** selection, run through the platform's own sizer and stop ladder:

| | 2010–2019 | 2020–2025 |
|---|---|---|
| random picks, with stops | +4.53% a year, 13.2% max drawdown | +7.71%, 17.0% |
| random picks, held 63 days | +6.43%, 23.7% | +3.67%, 34.8% |
| risk-free | 3.0% | 3.0% |

A signal is worth building only if its portfolio beats the better of these in its
own window. Beating cash is not the bar.

## What was wrong, and is now fixed

Each of these made results look better than they were. None was found by
looking for a good number; each was found by a check that failed.

**The data**
- Splits applied twice (§0.1a); placeholder bars with no trade behind them (§0.7);
  bad prints that carry volume and defeat a liquidity floor (§0.8).
- **Stored volume already adjusted for future splits** (§0.9) — a look-ahead that
  let future winners into every liquidity-floored sample and kept future
  distressed stocks out.

**The statistics**
- **Pooled t-statistics were inflated** up to twelvefold, because hundreds of
  stocks sharing one day's market move were counted as independent. Replaced by
  a per-date estimator with calendar-block standard errors
  (`tradeit.signals.cross_section`).
- **Each stock was sampled on its own calendar**, so a typical date held 50
  stocks; a common calendar grid now puts thousands on each
  (`tradeit.signals.sampling`).
- Universes required 250 bars of *whole-life* history — a filter on the future
  that removed companies dying within a year. Now point-in-time only.

**The backtest engine**
- **Stops never moved.** Breakeven and trailing were computed and discarded, so
  every backtest ran a different ladder from the one the platform declares.
- The moving-average baseline read every split as a crash.
- A $0.0001 stock could be bought; per-share commission on tens of millions of
  shares sank two portfolios. Strategies now require the platform's $5 minimum.

## What is still open

1. **The sizer will open a position whose commission is fifty times its value.**
   Trading logic, so it needs the owner's approval; a task to prepare the
   proposition is queued.
2. **The four fundamental signals are closed at both horizons** (§40 at 63
   sessions, §41 at twelve months). The next fundamental question needs
   information the corpus lacks: analyst consensus, for a true earnings
   surprise (FMP's plan does not supply it in usable form, measured
   2026-09-18), or shares outstanding, for value.
3. Sections below §13 of the scoreboard have not been re-run on the corrected
   corpus. The four highest-exposure ones were, and none moved; the rest are
   re-run on demand.

## Is signal testing complete?

**For price and volume signals on this corpus: yes, at every horizon it can
measure.** §42 closed the short end — 5 and 10 sessions, where swing trading
actually happens — and with it the registered stop rule covers 5, 10, 21 and 63
sessions. Twenty-nine indicator arms, none tradeable. Anything shorter needs
intraday data, which this corpus does not hold.

**For fundamentals: yes, at both horizons, as of §41.** The four classic
anomalies were measured point-in-time on 2013–2025 at 63 sessions and at twelve
months, the second keeping the companies that died. None passes. Earnings
surprise is consistent in direction on every cut and too small for twelve years
to establish.

**For chart patterns as trades: yes, as of §44.** The twelve detectors were
carried all the way to a complete trade — entry above a frozen boundary, the
pattern's own stop, a fixed exit, real costs, dead companies included — and
measured against a placebo that bought a random stock the same day on the same
terms. The rule earned the market's return and not one basis point more.

**What is left is not more of the same.** Every free, single-signal question this
corpus can answer at the 21- and 63-session horizons has now been asked with
honest methods. The remaining routes each ask something different -- a different
horizon (now asked for fundamentals in §41 and for indicators in §42), different
information (analyst
expectations, which the corpus does not hold), or combining signals rather than
testing them one at a time -- and each needs a decision, not a continuation.
