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

--------------------------------------------------------------------
AMENDMENT 1 -- 2026-09-17, before the screen was run and before any
signal above had been computed on any session.

WHAT IS ADDED
  A scan point is used only if BOTH endpoint bars -- session T and
  session T+63 -- have volume > 0.

WHY IT WAS MISSING AND WHY IT IS NOT OUTCOME-DRIVEN
  The $1M/day floor was assumed to imply it and does not: the floor is a
  TRAILING 20-session average, so a security can satisfy it on a session
  that itself never traded, and nothing in the floor constrains the
  outcome bar 63 sessions later at all. The corpus carries 2,245,866
  volume-0 bars (6.339%) across 7,582 securities for the reason
  PATTERN_QUALITY_2026-09-10 Amendment 2 records -- the vendor keeps
  emitting placeholder rows after a security stops trading.

  This is a statement about whether a return EXISTS, not about whether
  indicators work. You cannot buy at a price nobody transacted and sell
  at another price nobody transacted. It applies identically to all 24
  arms, it is written here before any of them has been computed, and it
  would have been the right rule had the result been spectacular.

TRIALS  Unchanged at 24. No signal specification moved.
```

```
AMENDMENT 2 -- 2026-09-17, written after the staged 100-security run
and BEFORE the full screen's verdict was read.

WHAT WAS SEEN WHEN THIS WAS WRITTEN, STATED SO THE AMENDMENT CANNOT BE
MISTAKEN FOR A REACTION TO A DISAPPOINTING NUMBER
  The staged run's full verdict table, on 1,137 observations from 23
  securities. NOTHING was flagged; all 24 arms returned not_detectable,
  both controls included. The amendment therefore cannot be an attempt to
  rescue a result -- there was no result to rescue -- and it makes the
  sample LESS extreme rather than more favourable to any arm.

THE DEFECT
  Security 79 prints single-session round trips of sixty-fold and back:
      2001-03-13  close    39.50
      2001-03-14  close 2,420.00
      2001-03-15  close    40.00
  119 such adjacent-session moves in its decade, and a later stretch
  holding 3,620 with real volume against a 1.05 print days earlier. Six
  of the staged run's 1,137 observations come from it, and they carry a
  63-session "return" of +344,662%.

  Neither existing guard catches it. Amendment 1's endpoint rule passes,
  because the bars carry volume. The $1M/day floor passes, and is in fact
  DEFEATED by the defect: a 3,620 print times any volume clears a
  million dollars on its own.

THE RULE ADDED
  A scan point is used only if atr_percent_14 <= 1.0 at the signal
  session -- that is, the 14-session average true range does not exceed
  the entire share price.

WHY THIS IS NOT OUTCOME-DRIVEN, ON FOUR GROUNDS
  1 It is computed from the TRAILING window only, at the signal session.
    It is point-in-time, it uses nothing from the holding period, and a
    live system could have applied it on the day.
  2 It is a SIGNAL-side property. No outcome, forward return or arm's
    statistic enters it. A rule keyed on the forward return would be
    outcome-driven; this is keyed on the price history.
  3 The threshold sits in an EMPTY gap, and the gap is what matters
    rather than the number chosen inside it.

    ** CORRECTED 2026-09-17, same day, before the full verdict was read.
    The first version of this ground said "NOTHING between 1.0 and 400",
    which was an inference from two counts rather than a measurement,
    and it was WRONG -- four readings lie in that range. The corrected
    measurement is below and the conclusion is unchanged, but the error
    is left recorded rather than silently overwritten, because the whole
    value of this document is that it says what was known when. **

    Measured on the staged run, the ten largest atr_percent readings:
        0.352  0.365  0.398  0.566 | 16.748  23.188  30.575  32.556
        477.968  524.474
    Every reading above 0.566 belongs to security 79. The largest
    reading on any OTHER security is 0.398.

    So the real gap is between 0.566 and 16.748 -- a factor of thirty,
    not three orders of magnitude. Any threshold in that range removes
    exactly the same rows, so the specific value of 1.0 cannot have been
    chosen to suit an answer. The gap is to be re-measured on the full
    sample and reported; if it is not empty there, this rule is to be
    reconsidered in the open rather than applied quietly.
  4 An average true range larger than the share price is not a volatile
    security. It is a series containing adjacent bars that differ by more
    than the whole price with no corporate action recorded, which is a
    bad print. The claim being refused is that the return EXISTS, the
    same claim Amendment 1 and PATTERN_QUALITY Amendment 2 refuse.

APPLIED IDENTICALLY to all 24 arms and to both universe arms, survived
and died. The count removed is reported with the result.

WHAT IS NOT CLAIMED
  This does not repair the corpus and does not change what price_series
  serves. It bounds what this screen reads. The corpus-level question is
  recorded as a new reading rule in RESEARCH_01_DATA_DICTIONARY.md and is
  a separate piece of work.

TRIALS  Unchanged at 24. No signal specification moved.
```

```
AMENDMENT 3 -- 2026-09-17. THE SCREEN AS REGISTERED IS VOID BY ITS OWN
CONTROL RULE. Written after the 24-arm table was read and after five
arms had been recomputed cross-sectionally; what was seen is listed
below.

WHAT THE CONTROL DID
  The registration said, of rate_of_change_252: "If this screen cannot
  reproduce something in that region, the MACHINERY is broken and no
  null it reports may be believed. This is a test of the test."

  It did not reproduce it. It came out INVERTED:
      §18 diagnostic, 250-session rank, 63 sessions : +0.0189 (t +8.49)
      this screen, rate_of_change_252               : -0.0249 (t -5.47)
  Declared POSITIVE, measured significantly NEGATIVE, sign holding in
  0 of 2 halves and 0 of 5 volatility bands.

  The rule is therefore binding and is applied: NO NULL IN THE 24-ARM
  TABLE MAY BE BELIEVED. The table is not evidence that these indicators
  do not work. It is evidence that the screen measured the wrong thing.

THE CAUSE, FOUND AND CONFIRMED BEFORE THIS WAS WRITTEN
  §18's number is a CROSS-SECTIONAL RANK -- each security's 250-session
  return ranked against the rest of the universe ON THAT DATE. This
  screen pooled raw indicator LEVELS across all 208 sample dates and
  took one Spearman correlation over all 144,766 observations.

  Those are different questions. Pooling across dates lets an indicator
  be rewarded for describing WHEN the market was cheap rather than WHICH
  security was worth holding. Over 2000-2009 -- two crashes and two
  rebounds -- twelve-month return is strongly negatively related to the
  next quarter POOLED, because the whole market fell and then rebounded
  together, while the cross-sectional question has the opposite sign.

  Measured, before this amendment was written, on five arms:
      arm                     pooled IC     cross-sectional IC (t)
      rate_of_change_252        -0.0249       +0.0141  (+0.54)
      rate_of_change_21         +0.0155       +0.0147  (+0.70)
      atr_percent_14            -0.0565       -0.0298  (-0.99)
      ulcer_index_14            -0.0434       -0.0281  (-1.14)
      chaikin_money_flow_20     +0.0150       +0.0291  (+1.45)
  The positive control's sign RECOVERS. The negative control's
  significance COLLAPSES. Both are what a correct method should do.

THE SECOND DEFECT, WHICH IS LARGER THAN THIS SCREEN
  The pooled t-statistic is not merely pointed at the wrong question, it
  is overstated. It treats 144,766 observations as independent after
  dividing only by the horizon overlap (63/21 = 3). It does not account
  for the fact that ~648 securities share EACH sample date and therefore
  share that date's market move. The independent unit is nearer the date
  than the observation: 208 dates, ~69 after overlap correction, not
  48,255.

  That is why atr_percent_14 reads t -12.42 pooled and t -0.99
  cross-sectionally. The pooled figure is inflated by roughly twelvefold.

  ** This applies to every pooled information coefficient in
  SIGNAL_SCOREBOARD.md, not only to this screen. ** It does not overturn
  any verdict that was NEGATIVE -- a signal that failed on an inflated
  t-statistic fails harder on an honest one -- but every t quoted as
  evidence FOR something must be re-read. This is recorded as an
  obligation, not discharged here.

WHAT IS RE-RUN, AND WHAT IT COSTS
  The same 24 arms, same data, same four criteria, with the information
  coefficient computed the Fama-MacBeth way: one IC per sample date,
  averaged, with the t-statistic taken from the TIME SERIES of those
  ICs and the same 3x overlap correction. Criteria 3 and 4 are applied
  to the sign of the same per-date average within each half and band.

  This is a SECOND LOOK at data already seen, and it is charged as one:
  24 further trials. Ledger 74 -> 98, hurdle |t| > 2.5235
  (expected_max_of_normals(98), measured).

  Charging it is the point. The cheaper move -- calling the first pass a
  "bug" and the second pass the "real" run at the old hurdle -- is how a
  screen gets two looks for the price of one.

WHAT WOULD COUNT, UNCHANGED
  All four criteria, in the declared direction, at |t| > 2.5235. The
  stop rule stands as written.
```
