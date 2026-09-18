# Pre-registration — `adx_14` confirmation, out of sample, on a common calendar grid

Committed **before `adx_14` was computed on any session after 2009-12-31**, and
before the scan below was run on any security. The git timestamp is the evidence.

**Where this comes from.** The 24-indicator screen (§32, registered in
`INDICATOR_SCREEN_2026-09-17.md`) flagged nothing under its registered method.
Its Amendment 4 recorded that one arm, `adx_14`, crossed the hurdle under the
corrected estimator built afterwards — t +2.55 against 2.5235 — and ruled that it
is **not a result**: it appeared only on the fourth reading of one dataset, by a
margin of 0.03. What it earned was the right to be named here, on data the screen
never read, with the method fixed in advance. This is that registration.

```
PRE-REGISTRATION -- adx_14 confirmation
written 2026-09-18, BEFORE adx_14 was computed on any session after
2009-12-31 and before the scan was run on any security

HYPOTHESIS
  Among the securities available on a date, those with higher 14-session
  ADX (trend strength) earn higher 63-session forward returns.
  Direction DECLARED POSITIVE -- unchanged from the screen, where it was
  declared before any data because ScoringConfig already asserts trend
  quality is a virtue. Not re-derived from the screen's result.

DATA -- none of it read by the screen
  2010-01-04 .. 2019-12-31. Every outcome ends on or before 2019-12-31;
  grid dates whose outcome would fall later are not sampled.

UNIVERSE -- the corpus as it stands, not the 2026-09-11 file
  security_spans_2026-09-18.csv, built by scripts/research01_build_spans.py
  (read-only, committed): every security with a raw print overlapping the
  window. 8,946 of them have 250+ bars there; 3,495 died inside it, against
  2,702 in the older file -- 793 more dead companies, which is the point.

  NO whole-life bar-count filter. Every earlier study required >= 250
  bars over a security's ENTIRE life, which conditions on the future: it
  drops companies that died within a year of listing, precisely the
  failures a survivorship-corrected corpus exists to keep. Here history
  is required point-in-time only -- 100 of the security's own bars up to
  and including the signal session.

SAMPLING -- tradeit.signals.sampling, committed at 0c7a521
  A common grid: every 5th XNYS session from 2010-01-04. Every security
  is sampled on the same sessions, and every outcome on a date is
  measured to the same exchange session 63 sessions on.

  Stride 5, not 21, and why. The sample size is set by calendar blocks,
  not by grid dates (see ESTIMATOR), so a denser grid adds no false
  independence. It does average more dates into each block, which
  lowers the block-to-block noise the t is built on. Indicators are
  computed once per security, so the extra dates cost almost nothing.
  Chosen before any data was read, on that argument alone.

INCLUSION ON A GRID DATE -- all point-in-time
  price_series close > 0 at both endpoints; volume > 0 at both endpoints
  (the screen's Amendment 1); atr_percent_14 <= 1.0 at the signal session
  (the screen's Amendment 2, DATA_DICTIONARY §0.8).
  For adx_14 ONLY: 20-session average dollar volume >= $1,000,000 at the
  signal session. §33 showed that below this floor this corpus answers
  with effects no portfolio could trade.

ESTIMATOR -- tradeit.signals.cross_section.cross_sectional_ic, as
committed at 36b616c, fixed now and not to be changed after the run
  Per-date Spearman IC on dates with >= 20 securities; mean over
  non-overlapping 63-session calendar blocks; t from the block means with
  Newey-West lag 1. median_breadth reported beside every figure.

HURDLE
  Ledger 98 -> 100 (adx_14 + the positive control below).
  |t| > 2.5306 = expected_max_of_normals(100), measured.

--------------------------------------------------------------------
WHAT COUNTS AS CONFIRMATION -- ALL FOUR, as the screen defined them

  1 Block t > 2.5306, IC positive.
  2 The per-date quintile spread (mean 63-session return of the top ADX
    quintile minus the bottom, on each date) is positive when averaged
    over calendar blocks, AND its median across dates is positive -- so
    it is not carried by a few dates.
  3 The IC is positive in both halves, 2010-2014 and 2015-2019.
  4 The IC is positive in at least 4 of 5 atr_percent quintiles, with the
    quintiles assigned WITHIN each date -- so a band is "the least
    volatile fifth of that day", not a fifth of the pooled decade.

WHAT CONFIRMATION WOULD LICENSE
  adx_14 becomes the first classical indicator measured on this corpus to
  pass out of sample, on a tradeable universe, at an honest standard
  error. It licenses a SCOPED PROPOSITION for its use -- a strategy
  change is still not a consequence of a measurement -- and nothing else.

--------------------------------------------------------------------
TWO CHECKS ON THE MACHINERY, EITHER OF WHICH VOIDS THE RUN

  POSITIVE CONTROL -- realized_volatility_60, declared NEGATIVE, measured
  on the same grid WITHOUT the liquidity floor. §33 measured this effect
  on these same years at t -4.29 to -4.50 on its broad dates (the
  cross-sections a common grid produces), IC -0.056 at 21 sessions and
  -0.081 at 63. The control passes if its block IC is negative with
  |t| > 2.5306. If it does not, the grid, the scan or the estimator is
  broken, and NO reading of adx_14 from this run may be believed.
  Charged one trial: a control exempt from the ledger is a free look.

  CALIBRATION -- a permutation null, charged nothing because it tests no
  hypothesis. adx_14 is shuffled WITHIN each date 200 times (seed
  20260918) and the block t recomputed each time. Shuffling within a date
  destroys any cross-sectional signal while keeping every date's
  structure, so a calibrated t should exceed 2.0 in magnitude about 5% of
  the time. If the 95th percentile of |t| under permutation exceeds 2.3,
  the estimator's standard error is too narrow on this panel and the run
  is void. This checks the thing §32 and §33 found broken twice.

--------------------------------------------------------------------
RESOLUTION -- predicted, so a null cannot be over-read

  From §33's broad-date reading at 63 sessions (IC -0.0806, t -4.44 on
  39 blocks) the block standard error is about 0.018, so at this hurdle
  the smallest detectable IC is about 0.046 at stride 21. Stride 5 should
  lower that; by how much is not known in advance and will be printed.

  adx_14's lead was IC +0.0264. THAT IS BELOW 0.046. If the printed
  resolution is also above 0.026, a failure here cannot distinguish "no
  effect" from "an effect of the size the lead suggested".

STOP RULE -- either way, declared now
  If adx_14 fails any criterion, the lead is CLOSED -- including if the
  failure is inconclusive for resolution. An effect this corpus cannot
  resolve across a decade of fresh data, on its broadest cross-sections,
  is not one a portfolio can be built to rely on. No further ADX
  variant -- other lookbacks, +DI/-DI, ADX slope -- is tried on this data.

TRIALS  2 (adx_14, the positive control). Ledger 98 -> 100.
```

```
AMENDMENT 1 -- 2026-09-18, BEFORE THE SCAN. No statistic of adx_14 has
been computed on any session after 2009-12-31.

WHAT WAS SEEN WHEN THIS WAS WRITTEN
  An 8-security pilot of the scan wrote adx_14 for those securities and
  two of its rows were displayed, to check the columns. No outcome, IC
  or spread was computed or looked at. That pilot is what exposed the
  defect below: security 1's 20-session dollar volume read $102bn a day.

WHY THE REGISTERED FLOOR COULD NOT HAVE MEANT WHAT IT SAID
  DATA_DICTIONARY §0.9. Stored raw volume is usually already restated for
  every split the vendor knew on delivery, and the read multiplied it
  again, so dollar volume carried every LATER split -- forward splits in,
  reverse splits out. On §26's panel, as an upper bound, up to 7.1% of
  $1M-floor decisions were wrong, and both errors tilted the floored
  sample toward survivors: future winners admitted (median 63-session
  return +3.34%), future distressed names excluded (-6.25%).

  Fixed at 67894dd in both read paths: served price times served volume
  is now the money that traded. On AAPL 2010-01-04 the read now serves
  the actual 17,633,200 shares and $3.77bn, where it served $105.7bn.

  The registered floor -- "20-session average dollar volume >= $1M" --
  was always meant as the money that traded. It now IS that. The
  specification does not change; the measurement under it was repaired.

THE ONE NEW DECISION, TAKEN NOW
  Where a security has splits and its volume basis cannot be
  established, the read serves the old number and marks the bar
  UNDETERMINED. For the adx_14 floor such bars are EXCLUDED and counted,
  because a floor resting on a number known to be possibly wrong in a
  survivor-favouring direction is the defect this amendment exists to
  remove.

  Excluding them is itself a choice that could bias, so it is bounded in
  advance: IF UNDETERMINED BARS ARE MORE THAN 5% OF THE OBSERVATIONS THAT
  WOULD OTHERWISE PASS THE FLOOR, the verdict reports adx_14 computed both
  ways -- excluded and included -- and a confirmation requires all four
  criteria to hold under BOTH. Below 5%, the count is reported and the
  exclusion stands.

  The positive control is measured without a floor and is unaffected.

TRIALS  Unchanged at 2.
```
