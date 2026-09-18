# Pre-registration — the same four fundamental signals, twelve months ahead

Committed **before any of the four signals was computed against a twelve-month
outcome**, and before the scan that would compute them existed as code.

## Why this is a new question and not a retry

§40 (`FUNDAMENTALS_2026-09-18.md`, registered at `adc037d`) measured earnings
surprise, gross profitability, asset growth and accruals against the **63-session
return** — the Swing horizon. None passed, and its stop rule closed them *on this
corpus* against any other definition, lookback, scaling or freshness window.

This does not change any of those. Every definition, freshness window and
inclusion rule below is §40's, word for word. What changes is **the question**:
whether these signals predict the **twelve-month** return, which is the
Retirement horizon's (ROADMAP: three horizons, never collapsed; a ticker may
reach different conclusions at each). It is also the horizon the literature
measured all four at — Novy-Marx, Cooper-Gulen-Schill and Sloan report annual
returns — so §40 asked a harder question than the anomalies claim to answer.

## What was seen before this was written — disclosed in full

§40's results, read on 2026-09-18 before this document:

| signal | 63-session IC | t | favoured fifth | halves | vol bands |
|---|---|---|---|---|---|
| SUE | +0.0174 | +2.13 | +2.40%/yr, pass | pass | 4/5 pass |
| gross profitability | +0.0191 | +1.50 | −1.67%/yr, fail | pass | 5/5 pass |
| asset growth (declared −) | +0.0001 | +0.00 | −3.89%/yr, fail | fail | 0/5 fail |
| accruals (declared −) | +0.0155 | +1.81 (wrong sign) | −5.37%/yr, fail | fail | 2/5 fail |

**This matters for a reason beyond disclosure: the twelve-month return contains
the 63-session return §40 already measured.** A signal can clear a twelve-month
test entirely on the first quarter §40 saw. Criterion 5 exists for that.

## What §40 did not do, and this does

§40 dropped any observation whose security stopped trading before its outcome
date, because the sampler requires a print on that date. Over 63 sessions that
removes few; over 252 it removes every company that died during the year — which
is survivorship bias, and CLAUDE.md principle 4 forbids it. Here a security that
**never trades again on or after its outcome date** is kept, measured to its
last traded close, and priced under two recovery assumptions (below). §40 is not
retroactively changed; its limitation is recorded here.

```
PRE-REGISTRATION -- four fundamental signals at twelve months
written 2026-09-18, BEFORE any signal was computed against a 252-session outcome

SIGNALS -- exactly §40's, unchanged
  SUE, GP/A, AG, ACC as defined in FUNDAMENTALS_2026-09-18.md, from the
  same four as-first-filed metrics, usable only if filed STRICTLY before
  the signal session, fiscal Q4 derived as FY - 9M, the same 120-day and
  15-month freshness windows, the same 20-day period matching.
  Directions as there: SUE +, GP/A +, AG -, ACC -.

WINDOW, UNIVERSE, SAMPLING
  2013-01-02 .. 2025-12-31; every outcome ends on or before 2025-12-31,
  so signal dates run to about 2024-12. security_spans_2026-09-18.csv,
  100 sessions of own history point-in-time. The common calendar grid,
  every 5th XNYS session, HORIZON 252.

INCLUSION ON A GRID DATE -- §40's, applied at the signal session
  Signal bar priced and traded; atr_percent_14 <= 1.0; 20-session
  average dollar volume >= $1,000,000 on the corrected read (§0.9), with
  UNDETERMINED-volume observations excluded and the 5% rule applied;
  close >= $5.

THE OUTCOME
  ALIVE     the security has a traded bar (close > 0, volume > 0) ON the
            outcome session: outcome = close(outcome) / close(signal) - 1.
  TERMINAL  the security has NO traded bar on or after the outcome
            session: it is measured to its last traded close L after the
            signal, and outcome = R * close(L) / close(signal) - 1.
  Otherwise (untraded on the outcome session but trading later) the
  point is dropped, as in every study before this one.
  R, the recovery, is run at BOTH 1.0 (a dead holding is paid its last
  price -- an acquisition) and 0.0 (it is worth nothing -- a
  bankruptcy). §16 measured the dead mostly paid; 1.0 is the realistic
  reading and 0.0 the pessimistic one. A PASS REQUIRES BOTH, so no
  verdict rests on a guess about what dead companies were worth.

ESTIMATOR -- tradeit.signals.cross_section.cross_sectional_ic as
committed, horizon_sessions 252: per-date Spearman IC on dates with >= 20
securities, mean over 252-session (one-year) calendar blocks, t from the
block means with Newey-West lag 1. About twelve blocks.

HURDLE -- small-sample, decided here and not after
  Ledger 108 -> 112. The normal hurdle is expected_max_of_normals(112)
  = 2.5702. A t from ~12 block means has ~11 degrees of freedom and fat
  tails: it clears 2.57 by chance about 2.6% of the time, not 1.0%. The
  hurdle is therefore small_sample_hurdle(112, blocks) -- the Student-t
  threshold with the same two-sided tail probability, with blocks as the
  whole-sample estimator reports it. At 12 blocks: 3.0967.
  Rejected: the normal hurdle every 63-session test used. At 52 blocks
  the two differ by 0.1; at 12, by half a point, all toward passing.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FIVE, PER SIGNAL, in its declared direction,
AT R = 1.0 AND AT R = 0.0

  1 Block t past the small-sample hurdle, IC signed as declared.
  2 The favoured fifth compounds ahead of its date's universe: the
    GEOMETRIC mean 252-session return of the favoured fifth minus that of
    every eligible security, positive averaged over calendar blocks AND at
    the median across dates. §35/§40's criterion 2 at this horizon.
  3 The IC signed as declared in both halves, signal dates 2013-2018 and
    2019-2024.
  4 The IC signed as declared in at least 4 of 5 within-date quintiles of
    realized_volatility_60 -- not a proxy for low volatility (§35).
  5 NEW, for what §40 saw: the IC against the part of the year AFTER the
    first 63 sessions, signed as declared (sign only). Measured on the
    points that have a bar on the 63rd session:
      late = (1 + outcome) / (close(63rd) / close(signal)) - 1.
    A signal whose twelve-month result is only its first quarter fails.

THE MACHINERY CHECK
  CALIBRATION -- each signal shuffled within date 200 times (seed
  20260918). §40's limit, 2.3, allowed 17% above the normal's 95th
  percentile (1.96). The same 17% above Student-t's with blocks - 1
  degrees of freedom: 2.3 / 1.96 * student_t_quantile(0.95, blocks - 1),
  2.583 at 12 blocks. Above it, that signal's run is VOID and its reading
  is not printed.

POWER -- stated now so a null is read correctly
  Twelve years hold twelve independent twelve-month outcomes, whatever
  the number of stocks. The smallest detectable IC is printed for each
  signal; a signal that fails with a detectable IC above its own
  estimate has been shown not to be large, not to be absent.

WHAT A PASS LICENSES
  A separate registration testing that signal AS A RETIREMENT-HORIZON
  PORTFOLIO against a random selection held the same way. Nothing else:
  no weight, gate or strategy parameter moves.

STOP RULE
  A signal that fails here is closed at BOTH horizons on this corpus.
  No third horizon is tried for these four. If all four fail, the
  fundamentals held today -- four as-filed metrics from 2009 -- have
  answered what they can, and the next fundamental question needs
  different information, not a different cut of this.

TRIALS  4 -- one per signal, one horizon. Ledger 108 -> 112.
```
