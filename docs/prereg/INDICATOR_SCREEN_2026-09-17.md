# Pre-registration — the 24-indicator screening pass

Committed **before the screen was run on any session**, which is the only thing
that makes a pre-registration worth anything. The git timestamp is the evidence
that the specification preceded the result.

The owner asked whether the indicator library should be widened — Bollinger
bands, DeMark's TD counts, triple EMA, Fibonacci retracement "and others". All
four are registered below, alongside twenty more, and the answer to *is this
relevant* is yes: every signal measured so far has been either a price kernel or
a bespoke engine, and none of the classical technical indicators has ever been
measured on this corpus.

```
PRE-REGISTRATION -- 24-indicator screening pass
written 2026-09-17, BEFORE any indicator below was computed on any session

WHY A SCREEN AND NOT 24 STUDIES
  Seventeen signals have been measured one at a time and all are null. A
  screen asks the cheaper question first -- is there ANYTHING here worth a
  full study -- and pays for it honestly by charging every arm to the
  trial ledger up front rather than reporting the best of 24 as if it
  were the only one tried.

HORIZON -- ONE, declared
  63 sessions. One horizon, not two, because a screen over 24 signals at
  two horizons costs 48 trials and raises the hurdle for everything that
  follows. 63 is chosen because it is where prior sections read their
  strongest (§13 IC t +4.05, §18 t +2.33, §26 t -27.54), and because it
  matches the Swing horizon the product is organised around.

UNIVERSE
  security_spans.csv (the 2026-09-11 file, unchanged), 2000-01-03 ..
  2009-12-31, both arms -- survived and died -- same construction as
  every prior run. Stride 21 sessions. Overlap correction 63 // 21 = 3
  applied to every t-statistic.

LIQUIDITY FLOOR -- IN THE SPECIFICATION, NOT ADDED AFTERWARDS
  20-session average dollar volume >= $1,000,000 at the scan session,
  computed point-in-time from the trailing window only.

  This is §27's lesson written into the design. §26 found volatility
  surviving every criterion; §27 established that the effect was
  "describing the corpus's coverage gaps rather than the market" and
  closed the line. A 24-indicator screen run without a floor would
  rediscover that same artefact 24 times, because almost every indicator
  here correlates with volatility somewhere.

MINIMUM OBSERVATIONS   500 after overlap correction
QUANTILE               0.2 (top vs bottom quintile)

PREDICTED RESOLUTION -- recorded so a large miss is visible
  §13's scan produced 280,865 tradable scan points on this universe at
  this stride with no floor. The floor is expected to remove most of
  them: predicted 60,000 - 130,000 observations. A result far outside
  that range means the floor is not doing what is described here, and is
  to be investigated before any verdict is read.

--------------------------------------------------------------------
THE 24 SIGNALS, WITH DIRECTION DECLARED IN ADVANCE

  Direction is DECLARED, never derived. A derived direction costs two
  trials and makes the prior unfalsifiable. Each justification below
  precedes the data.

  TREND AND MOVING AVERAGE
   1 tema_20_distance        POS  (close - TEMA20)/TEMA20. Triple EMA is
                                  the owner's ask; declared positive
                                  because a price above its own fast
                                  trend line is the strength reading the
                                  indicator exists to give.
   2 slope_sma_50_20         POS  per-bar slope of SMA50 over 20 bars.
   3 adx_14                  POS  trend strength; ScoringConfig already
                                  asserts trend quality is a virtue.
   4 aroon_oscillator_25     POS  how recently the window's high was
                                  touched, minus the low.
   5 macd_histogram_norm     POS  MACD histogram / close. Momentum
                                  acceleration.

  POSITION IN RANGE
   6 rsi_14                  NEG  classical overbought mean-reversion.
   7 bollinger_percent_b_20  NEG  the owner's ask. Position within the
                                  bands; declared negative on the same
                                  mean-reversion prior as RSI, which is
                                  what the bands are drawn for.
   8 cci_20                  NEG  same prior, money-flow-free.
   9 fib_retracement_126     NEG  the owner's ask. Fraction retraced from
                                  the 126-bar swing high toward its low:
                                  0 at the high, 1 at the low. Declared
                                  negative -- a deeper retracement is a
                                  weaker security -- which is the
                                  momentum-consistent prior and the
                                  OPPOSITE of the "buy the 61.8% level"
                                  folk reading. If the folk reading is
                                  right this fails with a significant
                                  wrong sign, which is a real result.
  10 percent_rank_close_252  POS  where the close sits in its own year.

  VOLATILITY AND COMPRESSION
  11 bollinger_bandwidth_20  NEG  the squeeze. Declared negative to match
                                  the low-volatility prior §26 measured.
  12 atr_contraction_10_50   POS  ATR(10 bars) / ATR(50 bars) < 1 is the
                                  VCP signature the detectors look for.
  13 ulcer_index_14          NEG  downside-only volatility; the only
                                  asymmetric risk measure in the set.
  14 atr_percent_14          NEG  ** NEGATIVE CONTROL. See below. **

  VOLUME AND FLOW
  15 money_flow_index_14     NEG  volume-weighted RSI.
  16 chaikin_money_flow_20   POS  accumulation by close-within-range.
  17 volume_contraction_10_50 POS volume dry-up before an advance.
  18 rolling_vwap_distance_20 POS (close - VWAP20)/VWAP20.

  STRUCTURE AND COUNTING
  19 td_buy_setup_count      POS  the owner's ask. DeMark buy-setup run
                                  length (closes below the close four
                                  bars earlier). Declared positive:
                                  DeMark reads a long buy setup as
                                  downside exhaustion preceding a bounce.
  20 td_sell_setup_count     NEG  the mirror, declared as the mirror.
  21 gap_frequency_63        NEG  share of sessions gapping over 2%.
  22 donchian_position_20    POS  position in the 20-bar channel.

  MOMENTUM
  23 rate_of_change_252      POS  ** POSITIVE CONTROL. See below. **
  24 rate_of_change_21       NEG  short-term reversal -- a separate and
                                  well-documented effect, declared in the
                                  opposite direction to 23 deliberately.

--------------------------------------------------------------------
TWO CONTROLS, DECLARED AS CONTROLS

  POSITIVE CONTROL -- rate_of_change_252.
    §18's diagnostic measured the 250-session lookback alone at IC
    +0.0217 (t +5.52) at 63 sessions on this corpus. If this screen
    cannot reproduce something in that region, the MACHINERY is broken
    and no null it reports may be believed. This is a test of the test.

  NEGATIVE CONTROL -- atr_percent_14.
    This is the §26 effect that §27 closed. Its unconditional IC should
    be large and negative, reproducing §26. Criterion 4 should then
    REMOVE it, because conditioning a variable on quintiles of itself
    leaves nothing. Both halves of that are checked: a large
    unconditional reading confirms the screen sees what §26 saw, and its
    removal by criterion 4 confirms the conditioning works on a variable
    we already know to be an artefact.

--------------------------------------------------------------------
WHAT COUNTS AS A SIGNAL BEING FLAGGED -- ALL FOUR, NO SUBSTITUTIONS

  1 IC clears the ledger hurdle |t| > 2.4228 IN THE DECLARED DIRECTION.
    A significant t with the wrong sign is a failure of the
    registration, not a success of the signal, and is reported as such.

  2 The quantile spread is in the declared direction and is NOT
    OUTLIER_DEPENDENT -- mean and median must agree in sign. §13, §18
    and §7 all turned on this and it is not negotiable here.

  3 The declared sign holds in BOTH halves, 2000-2004 and 2005-2009.
    This is the criterion that retired pattern_quality, relative_strength
    and obv_trend. An edge present in one half and reversed in the other
    is a period, not a signal.

  4 The declared sign holds in at least 4 of 5 atr_percent quintiles.
    §27's conditioning test, applied here to stop the screen
    rediscovering the volatility-and-coverage artefact under 24 names.

WHAT WOULD NOT COUNT
  A significant IC with no established spread -- the shape the SMA
  proxies produced in §12 and pattern_quality produced in §13. It did
  not license a weight then and does not now.
  The best of 24 read as if it were the only one tried. The hurdle
  already charges for the other 23; no result is to be quoted against
  the unadjusted 1.98.

--------------------------------------------------------------------
WHAT BEING FLAGGED LICENSES -- AND WHAT IT DOES NOT

  A flagged signal earns ONE thing: the right to a confirmation stage,
  registered and counted separately when it is run --
      out-of-sample 2010-2019, the 21-session horizon, four disjoint
      samples, and the half-period test again on the new decade.
  It licenses NO weight, NO gate, and NO change to any strategy
  parameter. Detector thresholds and scoring weights are strategy
  parameters and this screen does not move them.

TRIALS ADDED  24 -- one per signal at one horizon. Ledger 50 -> 74,
              hurdle |t| > 2.4228 (expected_max_of_normals(74),
              measured). The two controls are counted like every other
              arm; a control that is exempt from the ledger is a free
              look at the data.

STOP RULE -- declared before the result, as §27's was
  If NOTHING is flagged, the enquiry into single classical technical
  indicators on this corpus CLOSES, the way §27 closed the
  capitalisation line. No further indicator families -- Ichimoku,
  Elliott counts, Gann, further Fibonacci constructions, more oscillator
  variants -- will be tried on this data without a new registration that
  states what specifically would be different and why the twenty-four
  below did not already answer it.

  The reason this is binding: twenty-four failures would not mean the
  twenty-fifth is due. They would mean that single-indicator technical
  screening does not work on this corpus at this horizon, and the honest
  next step is combination, regime conditioning, or the corpus itself --
  not a twenty-fifth indicator.
```
