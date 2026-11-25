# Pre-registration — does breadth protect against drawdown?

Committed **before 2010–2019 was read for this question, and before the
held-out universe was drawn.** Trial 145. Authorised by the owner on
2026-11-25, in reply to a recommendation that named this as the only line in
144 trials where all four samples agreed in sign.

## The hypothesis

*A portfolio holding about a hundred names has a smaller maximum drawdown than
one holding twelve, at the same total risk budget, because a wide book absorbs
a delisting write-off that a narrow one cannot.*

Registered as a **risk** claim, not a return claim. The return columns are
reported below and **are not criteria**: the in-sample reading on return was
+2.08 pp/yr for the narrow book at |t| 1.24, which is nothing, and registering
a return criterion here would be registering a hypothesis the screening data
does not even suggest.

## What was screened to find it, stated honestly

[`BREADTH_2026-11-24.md`](../BREADTH_2026-11-24.md), four samples, 2020–2025,
in sample. Paired on sample, twelve names against a hundred:

| column | difference (12 − 100) | |t| | samples agreeing |
|---|---|---|---|
| CAGR, recovery 1.0 | +2.08 pp | 1.24 | 3 of 4 |
| CAGR, recovery 0.0 | +2.19 pp | 1.05 | 3 of 4 |
| Sharpe, recovery 1.0 | +0.130 | 0.98 | 3 of 4 |
| maxDD, recovery 1.0 | +3.64 pp | 1.22 | 3 of 4 |
| **maxDD, recovery 0.0** | **+5.23 pp** | **3.26** | **4 of 4** (+0.62, +7.00, +7.76, +5.54) |

**That |t| of 3.26 never cleared this project's own bar and the registration
says so up front.** It was computed from four paired samples, so three degrees
of freedom, and `small_sample_hurdle(145, 4)` is **6.366**. The in-sample
observation is a screening signal of ordinary strength, not a near-miss. What
earns it a trial is the sign agreement across all four samples on the one
column whose mechanism was predicted in advance — `THE_FLOOR_2026-11-22.md` §2
described delisting absorption before anything was measured on breadth — and
the fact that the column is a *risk* column, where this platform's controls
live and where a result would be usable even if no signal is ever found.

Five breadth levels and one diversification arm were run to produce that table.
**A statistic found by screening is not the same statistic registered in
advance**, and the hurdle prices trials rather than screens. This registration
converts one screened observation into one spent trial.

## The thing that makes this a real hold-out, and not a re-run

The screening run used `lowvol_bt_population.json`, which
`backtest_lowvol_tilt.py` built as *"alive on 2020-01-02 and liquid across
2019"*. **Every name in it survived to 2020.** Re-using it on an earlier window
would test a write-off hypothesis on a universe selected for not having been
written off — the attrition the hypothesis is about would be largely absent by
construction, and a failure would mean nothing.

So this trial builds a new universe with **no survival condition**: alive on
2010-01-04 and liquid across 2009, screened exactly as the 2020 population was.
Measured from `security_spans_2026-09-18.csv` before registering, **5,823
securities are alive on 2010-01-04 and 2,726 of them — 46.8% — have no bar
after 2019-12-31.** That is the attrition this hypothesis needs in order to be
falsifiable, and it is the reason the window is 2010–2019 rather than another
slice of 2020–2025.

```
PRE-REGISTRATION -- breadth as drawdown protection, held-out decade
written 2026-11-25, BEFORE 2010-2019 was read for this question and
BEFORE the held-out universe was drawn.

WINDOW    2010-01-04 .. 2019-12-31. Never read for this question.
          The screening window 2020-2025 is NOT re-measured.
UNIVERSE  security_spans_2026-09-18.csv, securities whose span covers
          2010-01-04, screened on 2009 with the 2020 population's own
          rule: >= 100 bars with volume in 2009 and a median daily
          dollar volume >= $1,000,000, read through
          research01.series.price_series at as_of 2009-12-31 21:00 UTC.
          NO SURVIVAL CONDITION. A security delisted in 2011 is in.
SAMPLES   four disjoint samples of up to 500, drawn by
          numpy.random.default_rng(20261125).permutation(sorted(pop)),
          the 2020 construction with a new seed. Seed fixed here.
CAPITAL   $100,000, participation 2%, risk-free 3%. Unchanged.
ARMS      TWO, and only two:
            narrow  max_positions 12,  risk_per_trade_pct 0.06/12
            wide    max_positions 100, risk_per_trade_pct 0.06/100
          max_portfolio_heat_pct 0.06 in both, so the TOTAL risk budget
          is identical and only its spread differs. With a fixed 8%
          stop, notional per position is equity x 0.75/N, so gross
          exposure is 75% of equity in BOTH arms -- breadth is not
          confounded with leverage.
          The 25, 50, 150 and 200 arms and the diversification arm are
          NOT run. Adding a level after seeing a result is screening.
SOURCE    EqualWeightBenchmark, cadence 63 sessions, stop 8% of price,
          as the screening run and as THE_FLOOR.
DELISTING delisting_after_sessions 10. PRIMARY assumption recovery
          0.0 -- fixed in advance, because the mechanism IS the
          write-off and recovery 1.0 has no write-off to absorb.
          Recovery 1.0 is run and reported as the mechanism control in
          criterion 3.
BLOCKS    twenty non-overlapping six-month calendar blocks,
          2010-H1 .. 2019-H2. The statistic per block is the mean
          across the four samples of that block's maximum drawdown.
          The running peak RESETS at each block start.
          Rejected: carrying the running peak across blocks. A late
          block would then inherit an early peak, every block after a
          big one would read as deep, and the twenty observations
          would be a single correlated path wearing twenty labels --
          which is exactly the independence the block t needs.
          Rejected: one maximum drawdown per sample over the whole
          decade. That is four numbers, three degrees of freedom and a
          hurdle of 6.366, which is the statistical position the
          screening run was already in.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FOUR

  1 PRIMARY. The mean paired within-block drawdown difference
    (narrow - wide) at recovery 0.0 is POSITIVE with
    |t| > small_sample_hurdle(145, 20) = 2.9713, t from the twenty
    block means. The SIGN IS FIXED IN ADVANCE: a result showing the
    wide book drawing down MORE is a FAIL, not a discovery.
  2 BOTH HALVES AGREE. Positive in 2010-2014 and in 2015-2019, and
    the weaker half at least ONE THIRD the magnitude of the stronger.
    Shared sign alone is not agreement -- §52 passed a half of
    +0.024% against +1.360% on that weaker test, and the correction
    is charged to §52 and applied here.
  3 THE MECHANISM MUST BE THE WRITE-OFF. The mean difference at
    recovery 0.0 must exceed the mean difference at recovery 1.0 by
    at least HALF of the recovery-0.0 value. If a wide book draws
    down less by the same amount whether a delisting is written off
    or recovered in full, then what was measured is that wider books
    have smoother curves -- true, unsurprising, and NOT this
    hypothesis. Criterion 1 could pass on that alone, so this is the
    criterion that makes the registration about the stated mechanism.
  4 THE WIDE ARM MUST ACTUALLY BE WIDE. Mean open positions >= 80 in
    every sample at both recovery assumptions.
    min_position_notional is $500 and REFUSES rather than rounds, so
    at 100 names the arm needs equity above roughly $67,000 to fill a
    book; if a drawdown starves it, the comparison stops being 12
    against 100 and silently becomes 12 against whatever fitted. The
    150 and 200 arms of the screening run returned zero trades for
    exactly this reason, and this criterion is the lesson from them.

STOP RULE
  If it fails, **breadth as drawdown protection is closed on this
  corpus.** No other breadth level, capital level, window, block
  length, delisting assumption or drawdown definition is substituted,
  and the result is NOT re-read on the screening window. In
  particular, failing criterion 3 while passing criterion 1 closes the
  line as stated -- "wider is smoother" is not a finding this project
  needs a trial to establish and may not be harvested from this one.

  If it passes, that is ONE out-of-sample confirmation that breadth is
  a risk control on this corpus. It authorises raising max_positions
  and risk_per_trade_pct together as a portfolio default, a
  walk-forward on the same statistic, and reporting breadth in the
  readiness framework. It authorises NO return claim: the return
  columns here are diagnostics and the screening reading on them was
  inside its own sampling error. `config.py` gates live capital
  regardless, and ADR-0004's interlock stands.

TRIALS  1 -- the primary criterion. Criteria 2, 3 and 4 are
        falsification conditions on the same hypothesis and the
        recovery-1.0 run is a control; none is charged.
        Ledger 144 -> 145. Hurdle at 20 blocks: |t| > 2.9713.
```

## The limitations that travel with any result here

1. **Survivorship, and it cuts the unfamiliar way.** 52.0% of dated Exchange
   Act exits are priced (`EDGAR_DELISTING_DENOMINATOR.md` §7e). The securities
   the corpus cannot price to their end are the ones that fell furthest, so the
   write-offs in this window are **fewer and gentler than reality**. A pass is
   therefore conservative; a failure is not exonerated by survivorship, because
   the bias runs against the hypothesis rather than for it.
2. **One decade, and a benign one for drawdown.** 2010–2019 holds no 2008 and
   no 2020. The deepest index drawdown in it is 2011's. A drawdown hypothesis
   tested without a crash is tested at the easy end.
3. **The narrow arm is still thirteen arbitrary names.** Every candidate ties
   at a score of 1.0 and `DiversityAwareRanker` breaks ties on instrument id,
   so the twelve-name book is the twelve lowest ids in each sample
   (`BREADTH_2026-11-24.md` Finding 1). This trial compares *a book of twelve
   arbitrary names* with *a book of a hundred arbitrary names*; it says nothing
   about twelve **chosen** names, and a selection rule could change the answer
   in either direction.
4. **The fixed 8% stop's known defect travels.** §44 found it never binds for a
   calm security, and §47 found that fixing it made every arm worse. Both arms
   here carry it equally, so it is not a confound between them, but neither
   arm's absolute drawdown is the drawdown a better stop ladder would produce.
5. **The four samples are disjoint in securities and identical in calendar.**
   They share every market event, so averaging them reduces idiosyncratic noise
   and not market noise. The twenty blocks are the defence against that, and
   they are not a complete one.
