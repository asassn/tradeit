# Pre-registration — four fundamental signals, point-in-time, 2013–2025

Committed **before any of the four signals below was computed on any session**,
and before the scan that would compute them existed as code. The first test of
Phase 6's purpose — *growth, quality and balance-sheet screens on as-filed data,
earnings surprise* — on the fundamentals the corpus already holds.

**Why these four, and why only four.** Each is a published, replicated anomaly
with a direction fixed by the literature decades before this corpus existed, so
no direction is read from this data:

| signal | direction | source of the prior |
|---|---|---|
| earnings surprise (SUE) | higher is better | Bernard & Thomas (1989), post-earnings-announcement drift |
| gross profitability | higher is better | Novy-Marx (2013) |
| asset growth | **lower** is better | Cooper, Gulen & Schill (2008) |
| accruals | **lower** is better | Sloan (1996) |

Published anomalies are known to weaken after publication. That is a reason they
may fail here, not a reason to test something invented instead: a prior that
precedes the data is worth more than a fresher one that does not.

Value signals (earnings yield, book-to-market) are **not** registered: they need
shares outstanding, and the corpus holds that figure for 143 securities.

```
PRE-REGISTRATION -- four fundamental signals
written 2026-09-18, BEFORE any signal below was computed on any session

POINT IN TIME -- THE RULE EVERYTHING ELSE RESTS ON
  A fundamental value is the value AS FIRST FILED: for each (security,
  metric, period end, duration), the row with the earliest knowledge_time.
  knowledge_time is the filing date (tradeit.research01.fsds), stamped at
  midnight, and a filing can arrive after the close -- so a value is usable
  on signal session D only if its filing date is STRICTLY BEFORE D.
  Later restatements and the comparatives repeated in later filings are
  never used, even where they differ, because on D they did not exist.

  Fiscal Q4 is derived, not read. Companies file Q4 only inside the annual
  figure; the stand-alone Q4 value appears months later as a comparative
  (Apple FY2012 Q4: first stand-alone print 2013-04-24, six months after
  the 10-K). Q4 = the annual (4-quarter) value minus the nine-month
  (3-quarter) year-to-date ending about three months earlier, known on the
  later of their two filing dates. Checked on Apple FY2012: $41,733M -
  $33,510M = $8,223M, the stand-alone figure exactly.

THE FOUR SIGNALS -- all from NetIncomeLoss, Assets, GrossProfit and
NetCashProvidedByUsedInOperatingActivities, as filed
  SUE   the latest quarter's net income minus the same quarter a year
        earlier, divided by the standard deviation of that seasonal
        difference over the up-to-eight quarters before it (at least six
        required). The latest quarter must have been filed within the 120
        days before D -- drift follows an announcement, it does not persist
        from a stale one. DECLARED POSITIVE.
  GP/A  latest fiscal-year gross profit over total assets at that
        fiscal-year end. DECLARED POSITIVE.
  AG    total assets at the latest fiscal-year end over total assets a year
        earlier, minus one. DECLARED NEGATIVE.
  ACC   (fiscal-year net income - fiscal-year operating cash flow) over
        the average of the two year-end total assets. DECLARED NEGATIVE.
  For GP/A, AG and ACC the latest fiscal year must have been filed within
  the 15 months before D, so a company that stopped filing drops out rather
  than carrying a stale number.
  A year-earlier or prior-quarter period is matched by period end within
  20 days of the expected date; no match, no signal.

WINDOW, UNIVERSE, SAMPLING
  2013-01-02 .. 2025-12-31; every outcome ends on or before 2025-12-31.
  §10 measured that before about 2013 too few securities have a fundamental
  figure both knowable and fresh for a point-in-time study.
  security_spans_2026-09-18.csv, every security with raw prints in the
  window, 100 sessions of its own history required point-in-time.
  The common calendar grid (tradeit.signals.sampling), every 5th XNYS
  session, horizon 63.

INCLUSION ON A GRID DATE -- identical to §35 and §37, all point-in-time
  Both endpoints priced and traded; atr_percent_14 <= 1.0; 20-session
  average dollar volume >= $1,000,000 on the corrected read (§0.9), with
  UNDETERMINED-volume observations excluded and the 5% rule of §35
  applied; close >= $5 (LiquidityConfig.min_price). Each signal is judged
  on the securities for which it is defined on that date.

ESTIMATOR -- tradeit.signals.cross_section.cross_sectional_ic as
committed: per-date Spearman IC on dates with >= 20 securities, mean over
63-session calendar blocks, t from block means with Newey-West lag 1.

HURDLE  Ledger 104 -> 108. |t| > 2.5576 = expected_max_of_normals(108).

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FOUR, PER SIGNAL, in its declared direction

  1 Block t past the hurdle, IC signed as declared.
  2 The favoured quintile compounds ahead of its date's universe: on each
    date, the GEOMETRIC mean 63-session return of the favoured fifth
    (highest SUE and GP/A; lowest AG and ACC) minus the geometric mean of
    every eligible security; positive averaged over calendar blocks AND
    at the median across dates. §35's criterion 2, unchanged.
  3 The IC signed as declared in both halves, 2013-2018 and 2019-2025.
  4 The IC signed as declared in at least 4 of 5 within-date quintiles of
    realized_volatility_60. Low volatility is the one effect this corpus
    has shown (§35); a fundamental signal that is only a proxy for it
    must not pass as something new.

THE MACHINERY CHECK
  CALIBRATION -- for each signal, the signal shuffled within date 200
  times (seed 20260918); if the 95th percentile of |t| exceeds 2.3, that
  signal's run is VOID. No positive control: nothing fundamental is known
  in this window, and the one known effect (§35) is price-based.

WHAT A PASS LICENSES
  A separate registration testing that signal AS A PORTFOLIO against the
  random-selection benchmark of §37/§38 through the platform's own
  machinery. §37 is the reason: a signal that predicts is not yet a
  portfolio that pays. It licenses nothing else -- no weight, gate or
  strategy parameter moves.

STOP RULE
  A signal that fails is closed on this corpus: no other definition,
  lookback, scaling or freshness window of it is tried. If all four fail,
  no further fundamental signal is tested on this corpus without a new
  registration stating what specifically it would add that these four did
  not already ask.

TRIALS  4 -- one per signal, one horizon. Ledger 104 -> 108.
```
