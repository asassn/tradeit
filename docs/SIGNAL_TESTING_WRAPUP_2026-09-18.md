# Signal testing — where it ended, and what it leaves — 2026-09-18

A plain summary for deciding whether to move on. The detail, every number and
every correction, is in [`SIGNAL_SCOREBOARD.md`](SIGNAL_SCOREBOARD.md) §1–§40 and
the registrations in [`prereg/`](prereg/). This document repeats only what a
decision needs.

## The answer in one paragraph

**Nothing measured on this corpus is yet profitable enough to trade.** One signal
survives as a *signal*: low volatility predicts which tradeable stocks do better
over the next quarter, out of sample (§35). It does **not** survive as a
*strategy*: a portfolio built on it lost to a randomly chosen one in every
sample, under every version of the engine (§37, three runs). Everything else —
forty-eight other measurements across price, volume, momentum, patterns, size,
twenty-four classical indicators, a trend-strength lead, two sizing rules and
four point-in-time fundamental signals — is null, failed out of sample, or
inconclusive. The closest miss is earnings surprise (§40): three of four
criteria, short only on significance. Along the way the corpus, the
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

Ledger: **108 trials**, current hurdle |t| > 2.5576. Every result above is
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
2. **Four fundamental signals have now been measured (§40), at the 63-session
   Swing horizon, and none passes.** The literature measures most of them over
   twelve months -- the Retirement mandate's horizon -- which this corpus has not
   been asked. A registration there would be a new question, would have to
   disclose §40, and is not yet taken.
3. Sections below §13 of the scoreboard have not been re-run on the corrected
   corpus. The four highest-exposure ones were, and none moved; the rest are
   re-run on demand.

## Is signal testing complete?

**For price and volume signals on this corpus: yes.** The method is now sound and
tested, the obvious single-signal space has been measured with honest standard
errors, the indicator families are closed by a registered stop rule, and the one
survivor has been carried all the way to a portfolio test and failed there.
Another price-based indicator would be the twenty-sixth attempt at a question
twenty-five have answered.

**For fundamentals at the Swing horizon: yes, as of §40.** The four classic
anomalies were measured point-in-time on 2013–2025 and none passes; earnings
surprise came closest and is closed by the stop rule all the same.

**What is left is not more of the same.** Every free, single-signal question this
corpus can answer at the 21- and 63-session horizons has now been asked with
honest methods. The remaining routes each ask something different -- a different
horizon (Retirement, twelve months), different information (analyst
expectations, which the corpus does not hold), or combining signals rather than
testing them one at a time -- and each needs a decision, not a continuation.
