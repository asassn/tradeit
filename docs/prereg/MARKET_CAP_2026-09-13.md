# Pre-registration — market capitalisation, 2010–2019

Committed before market capitalisation has been computed on any session, at any
horizon, in this project.

```
WHAT THIS IS FOR
  §26 found the volatility relationship passing its criteria, and then found
  most of it was something else. Price dominated volatility inside every
  volatility quintile. A liquidity floor then removed most of what was left:
  the shunned quintile went from -43.33%/yr to -0.86%/yr and the universe from
  -8.56%/yr to +4.75%/yr at $25M/day, collapsing the edge from +15.73pp/yr to
  +2.56pp/yr.

  Volatility, price and illiquidity are three views of one thing, and none of
  the three is well specified. MARKET CAPITALISATION is. Shares outstanding
  times price is the quantity the other three have been proxying for, it is
  point-in-time available in this corpus, and it has never been tested here.

  This test exists to determine WHICH VARIABLE IS PRIMARY, not to discover an
  effect. A pass is not a discovery.

SIGNAL     market_cap = (point-in-time common shares outstanding) x close.
           Shares come from security_fundamental_facts, metrics
           CommonStockSharesOutstanding and
           WeightedAverageNumberOfSharesOutstandingBasic, taking the LATEST
           fact whose knowledge_time is on or before the session. A session
           with no fact yet knowable is DROPPED, not back-filled -- fail
           closed. knowledge_time here is the SEC FSDS publication date, mean
           lag 240 days from period_end, which is later than a filer's own
           disclosure and therefore errs away from look-ahead.
           This is shares outstanding, NOT free float: dei:EntityPublicFloat is
           absent from the corpus. Insider and restricted shares are included,
           so this overstates tradable supply, and by more for closely held
           companies.

DIRECTION  POSITIVE -- LARGER capitalisation predicts HIGHER forward return.
           DISCLOSURE: this is DERIVED, and derived from a diagnostic on the
           very data it will be tested on. §26 measured the cheapest price
           quintile at -33.96%/yr and the priciest at +20.85%/yr on this
           panel. Declaring the direction the literature's size premium would
           predict (SMALL outperforms) when this corpus has already said the
           opposite would be theatre.
           The derivation is charged: 2 trials per horizon, not 1, exactly as
           SignalStudy charges Orientation.DERIVED and as the obv_trend
           registration charged it. Ledger 46 -> 50.

           Worth recording now rather than after: a pass means this corpus
           contradicts the classic size premium over 2010-2019. That is a
           claim about this corpus, whose gate reads 38.7%, before it is a
           claim about markets.

LIQUIDITY  A floor of $1,000,000 average daily dollar volume
           (avg_dollar_volume_20) is part of the SPECIFICATION, not a
           robustness check discovered afterwards. §26's addendum is the
           reason: a result measured without one is a result about securities
           nobody can trade.
           DISCLOSURE: the volatility-by-floor table in §26's addendum has been
           seen. $1M was chosen as a tradability threshold, not by picking the
           floor that produced the best volatility number -- $25M produced a
           cleaner one and is not the primary. $250k and $25M are reported as
           declared diagnostics so the choice is auditable.

PERIOD     2010-01-04 .. 2019-12-31, warm-up from 2009. Four disjoint samples,
           cap 400, the universes §19, §22, §24 and §26 used.
HORIZONS   21 and 63 sessions    STRIDE 21    QUANTILE 0.2
TRIALS     4 -- one signal, two horizons, doubled for the derived direction.
           Ledger 46 -> 50.
HURDLE     |t| > 2.2763, measured: expected_max_of_normals(50) = 2.2763031.

DISCLOSURE -- THIS IS THE MOST CONTAMINATED REGISTRATION HERE
  2010-2019 is not unseen in any sense. The sizing experiment covered
  2010-2024; §22 and §24 ran volume signals on it; §26 ran volatility on it;
  and the DIRECTION of this test comes from a §26 diagnostic on this same
  panel. A pass therefore confirms that the §26 diagnostic was not noise and
  identifies which variable is primary. It does not establish a new effect, and
  the write-up may not claim one. A FAIL is the more informative outcome,
  because it would say the price result does not survive proper specification.

WHAT THIS TEST CAN RESOLVE -- COMPUTED BEFORE IT RUNS
  From row counts and the outcome distribution only; market cap's relationship
  to returns is not touched.

    securities carrying a shares-outstanding fact   2,425 of 3,032  (80.0%)
    observations with a point-in-time fact          162,975 (69.2%)
    ... and passing the $1M/day floor                97,242 (41.3%)

    horizon 21  stride 21, no overlap   n_eff 97,242  resolves IC 0.0073
    horizon 63  overlap 3x              n_eff 31,441  resolves IC 0.0128

  §26's volatility IC was -0.079 and the price effect was larger still, so this
  resolves an effect roughly ten times smaller than the one in question.

IT SURVIVES AT A HORIZON ONLY IF ALL FOUR HOLD
  1. The geometric mean of the TOP (largest) quintile beats the geometric mean
     of all candidates in BOTH 2010-2014 and 2015-2019, bootstrap CI excluding
     zero in both halves.
  2. The Spearman IC is POSITIVE with |t| beyond 2.2763, overlap-corrected.
  3. The IC is POSITIVE in at least 3 of the 4 samples.
  4. NEW, AND THE POINT OF THE TEST. Market cap must survive conditioning on
     PRICE: inside each price quintile, the top-market-cap quintile's geometric
     edge over that band's own candidates must be positive in at least 4 of the
     5 bands, pooled over the period. §9 recorded that every test in this
     project has been univariate and that §26's confound was found only by a
     post-hoc double sort. This builds the double sort into the criteria.

     If criteria 1-3 pass and 4 FAILS, the finding is PRICE, not market cap,
     and the section says so.

DECLARED DIAGNOSTICS -- no trials charged, none promotable
  * the symmetric test: does PRICE survive conditioning on market cap? If both
    survive each other, they are different variables; if only one does, it is
    the primary one and the other is its shadow.
  * the AVOID side. §9 measured the quantile estimator as adequate for
    selection and far too coarse for refusal -- the buy side flat from the 20%
    bucket to the 0.2% bucket while the avoid side more than doubled. So the
    bottom quintile is reported explicitly, and both ends are reported at the
    20%, 5%, 1% and 0.2% fractions. This is the first registration written
    after that lesson and the first to look at the tail on purpose.
  * $250k and $25M/day floors beside the registered $1M.
  * the arithmetic quantile spread and its SignalStudy verdict, as §26 did, so
    that using a geometric criterion cannot hide a result.

NO TUNING
  One floor, one quantile, one direction, two horizons, four samples, all fixed
  above. If this fails, no other floor, quantile or capitalisation definition is
  tried on this data.

WHAT A PASS WOULD LICENSE
  A scoped proposition about UNIVERSE CONSTRUCTION, not about scoring. Size and
  liquidity are properly a mandate's admission rule -- what a portfolio is
  allowed to hold -- and that is a different layer from a signal that ranks what
  it already admits. Nothing here would license a weight, and volatility,
  price and market cap would all still be unweighted.

WHAT A FAIL WOULD LICENSE
  Closing the line of enquiry §26 opened. If the best-specified version of the
  variable does not survive its own criteria on the data that suggested it,
  then price, volatility and illiquidity were describing the corpus's coverage
  gaps rather than the market, and the honest next step is the corpus rather
  than another signal.
