# Pre-registration — eight swing indicators at 5 and 10 sessions, 2010–2019

Committed **before any of the eight signals below was computed against a 5- or
10-session outcome**, and before the scan that would compute them existed as
code. 2020–2025 is **held out and not read at all** by this screen.

## Why this is not the closed screen asked again

§32's 24-indicator screen closed "the enquiry into single classical technical
indicators on this corpus … without a new registration that states what
specifically would be different and why the twenty-four did not already answer
it." Four things are different, and the first is the reason for the rest:

1. **The horizon.** Every indicator test on this scoreboard has predicted the
   21- or 63-session return. The Swing mandate is *days to about three months*
   (`MULTI_TIMEFRAME_MANDATES.md`), so its short end — **5 and 10 sessions** —
   has never been measured. Most of these indicators are used by traders at
   exactly that holding period, and an oscillator that says nothing about the
   next quarter may still say something about the next week. The 24 could not
   have answered it: they were computed against one horizon, 63.
2. **The decade.** §32 ran on 2000–2009, the window where this corpus's
   survivorship coverage is weakest (25–44% of exits priced by year). This runs
   on 2010–2019.
3. **The method.** §32 sampled each security on its own grid — a median of 50
   securities per date — and used a standard error later shown to be wrong.
   This uses the common calendar grid and the committed block estimator.
4. **Costs are a criterion, not a footnote.** At five sessions a portfolio turns
   over fifty times a year. An edge smaller than the round trip is not an edge,
   and §32 never asked that question.

## What was seen before this was written — disclosed

§32's readings for the five of these eight it also tested, at 63 sessions on
2000–2009 (IC, t): `rsi_14` +0.0035 (+0.23), `bollinger_percent_b_20` −0.0055
(−0.49), `macd_histogram_norm` +0.0041 (+0.37), `percent_rank_close_252` +0.0282
(+1.30) — the largest of the 24 — and, from the earlier single studies,
`rsi_14` "never detectable" and short-horizon reversal `momentum_21` IC −0.016
(t −2.15), consistent in sign across four in-sample specifications.
`adx_14` is **excluded**: closed by §34's stop rule.

```
PRE-REGISTRATION -- eight swing indicators, 5 and 10 sessions
written 2026-09-19, BEFORE any signal below was computed at either horizon

HORIZONS -- TWO, declared
  5 and 10 sessions. Both are charged to the ledger for every arm.

THE EIGHT SIGNALS, DIRECTION DECLARED IN ADVANCE
  Direction is declared from the indicator's classical use, never read
  from this data. Four say "buy weakness", four say "buy strength",
  which is deliberate: at this horizon the literature is split, and a
  screen that declared one prior for everything would be untestable.

  MEAN REVERSION -- the oscillator family, declared NEGATIVE
   1 rsi_14                  NEG  Wilder's RSI. Overbought is sold.
   2 stochastic_k_14_3       NEG  slow %K: 100*(close - min low 14) /
                                  (max high 14 - min low 14), smoothed
                                  by a 3-session mean. The one classical
                                  oscillator this corpus has never held.
   3 bollinger_percent_b_20  NEG  position in the 20-session bands, 2 sd.
   4 rate_of_change_10       NEG  the two-week reversal. momentum_21 was
                                  consistently negative in four earlier
                                  specifications; this is the same prior
                                  at the horizon it is traded at.

  TREND -- declared POSITIVE
   5 macd_histogram_norm     POS  (MACD 12/26 - signal 9) / close.
   6 ema_9_21_distance       POS  (EMA9 - EMA21)/EMA21. The crossover
                                  swing traders actually use, as a state
                                  rather than an event, so every session
                                  is scored rather than the few that
                                  cross.
   7 sma_50_200_distance     POS  (SMA50 - SMA200)/SMA200. The golden
                                  cross, same treatment.
   8 percent_rank_close_252  POS  where the close sits in its own year.
                                  §32's largest arm; disclosed above.

  Computed from DAILY bars only. No weekly or intraday input: this
  corpus has none, and a screen must not imply a timeframe it cannot
  read.

WINDOW, UNIVERSE, SAMPLING
  2010-01-04 .. 2019-12-31; every outcome ends on or before 2019-12-31.
  2020-2025 IS NOT READ. security_spans_2026-09-18.csv; 100 sessions of
  the security's own history, point-in-time; the common calendar grid
  (tradeit.signals.sampling), every 5th XNYS session.
  Arms needing more history than a security has are undefined there and
  that arm is judged on the securities where it is defined, as in §40.

INCLUSION ON A GRID DATE -- §35's, unchanged
  Signal bar priced and traded; atr_percent_14 <= 1.0; 20-session
  average dollar volume >= $1,000,000 on the corrected read (§0.9),
  UNDETERMINED-volume observations excluded with the 5% rule; close >= $5.

THE OUTCOME -- §41's rule, so nothing depends on survivors
  A traded bar on the outcome session, or -- if the security never
  trades again on or after it -- its last traded close, priced at
  recovery R = 1.0 AND R = 0.0 (tradeit.signals.sampling.
  outcome_or_terminal). A PASS REQUIRES BOTH.

ESTIMATOR  cross_sectional_ic as committed, horizon_sessions 5 or 10:
per-date Spearman IC on dates with >= 20 securities, mean over
horizon-length calendar blocks, t from block means, Newey-West lag 1.

HURDLE  Ledger 112 -> 128 (8 signals x 2 horizons). The normal-scale
hurdle is expected_max_of_normals(128) = 2.6163; the hurdle applied is
small_sample_hurdle(128, blocks), which equals it to two decimals at the
several hundred blocks these horizons produce.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FIVE, PER ARM, PER HORIZON, in its declared
direction, AT R = 1.0 AND AT R = 0.0

  1 Block t past the hurdle, IC signed as declared.
  2 The favoured fifth beats its date's universe: geometric mean return
    of the favoured fifth minus that of every eligible security on the
    date, positive averaged over blocks AND at the median across dates.
    At R = 0.0 the equal-weight buy-and-hold return, per §41 Amendment 1.
  3 The sign holds in both halves, 2010-2014 and 2015-2019.
  4 The sign holds in at least 4 of 5 within-date quintiles of
    realized_volatility_60 -- not a repackaging of §35's one effect.
  5 COSTS, which is why this screen exists at this horizon: the
    criterion-2 edge must exceed 0.20% PER HOLD at both the block mean
    and the median. 20 basis points is one round trip at 10 bps a side
    -- spread, slippage and commission together -- and is deliberately
    generous to the signal: a five-session portfolio pays it about fifty
    times a year. An edge below it is not tradeable however significant.

THE MACHINERY CHECK -- judged FIRST; a failure voids that horizon and no
arm of it is printed
  POSITIVE CONTROL  realized_volatility_60, declared NEGATIVE, measured
  on the same panel WITHOUT the liquidity floor, as §34 did. Low
  volatility is the one effect this corpus has established (§26, §35).
  It must clear the hurdle in the declared direction. If it cannot, the
  panel cannot see a known effect and nothing else on it may be read.
  CALIBRATION  each arm shuffled within date 100 times (seed 20260919);
  if the 95th percentile of |t| exceeds 2.3, that arm is VOID.

WHAT A PASS LICENSES
  One confirmation registration on the HELD-OUT 2020-2025, naming the
  arm and its horizon, before anything else. No weight, gate or strategy
  parameter moves on this screen alone.

STOP RULE
  If nothing passes, single classical technical indicators are CLOSED on
  this corpus at every horizon it can measure -- 5, 10, 21 and 63
  sessions -- and no further indicator, lookback, smoothing or
  oscillator variant is tried without new information, meaning data this
  corpus does not hold (intraday bars, or a different market).
  An arm that fails is closed at that horizon; an arm that fails at both
  is closed outright.

TRIALS  16 -- eight signals at two horizons. Ledger 112 -> 128.
```
