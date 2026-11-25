# Trial 145 — breadth as drawdown protection: FAILED, and the mechanism inverted

**Registered at `3a47c6b`**, committed and pushed to `origin` before a single
bar of 2010–2019 was read for this question.
[`docs/prereg/BREADTH_DRAWDOWN_2026-11-25.md`](prereg/BREADTH_DRAWDOWN_2026-11-25.md).

**Ledger 144 → 145. Hurdle at twenty blocks: |t| > 2.9713.**

Reproduce with `scripts/trial_breadth_drawdown.py --sample 0..3`, graded by
`scripts/trial_breadth_drawdown_verdict.py`.

## The verdict

| criterion | result | |
|---|---|---|
| 1 — mean within-block drawdown difference positive past `small_sample_hurdle(145, 20)` | **+1.54%, t 4.726** against 2.9713 | pass |
| 2 — both halves positive and agreeing in magnitude | 2010–14 **+1.08%**, 2015–19 **+2.00%** | pass |
| 3 — the advantage at least halves when delistings recover in full | r0.0 **+1.54%**, r1.0 **+1.87%**, ceiling +0.77% | **FAIL** |
| 4 — the wide arm actually holds ~100 names | narrowest mean **66.2** against 80 | **FAIL** |

**FAILED. All four were required.** Under the stop rule, **breadth as drawdown
protection is closed on this corpus.** No other breadth level, capital level,
window, block length, delisting assumption or drawdown definition is
substituted, and this is not re-read on the screening window.

## Criterion 3 is the one that decided it, and it did not merely fail

The hypothesis was that a wide book draws down less **because it absorbs a
delisting write-off that a narrow one cannot**. Criterion 3 tested that by
re-running both arms with delisted holdings recovered in full, where there is no
write-off to absorb. The advantage was supposed to shrink.

It grew.

| | narrow (12) | wide (100) | difference |
|---|---|---|---|
| within-block drawdown, recovery 0.0 | | | **+1.54%, t 4.726**, 18/20 blocks |
| within-block drawdown, recovery 1.0 | | | **+1.87%, t 7.120**, 19/20 blocks |
| whole-window max drawdown, recovery 0.0 | 19.40% | 12.64% | +6.76 pp |
| whole-window max drawdown, recovery 1.0 | 17.39% | 9.90% | +7.50 pp |

Read the last two rows the other way, which is the direction that matters:

> **Writing delistings off costs the narrow book 2.01 pp of drawdown and the
> wide book 2.74 pp.** The write-off hurts the wide book *more*.

That is the registered mechanism **inverted**, not absent. A hundred-name book
takes roughly eight times as many delisting hits as a twelve-name book; each one
is about an eighth the size, and in drawdown terms the greater frequency wins.
The screening run's 4-of-4 sign agreement on the `maxDD` recovery-0.0 column was
real, and the reason I gave for it was wrong.

**What the wide book's smaller drawdown actually is** is ordinary
diversification of idiosyncratic risk — true, well known, and *not* a finding
this project needed a trial to establish. The registration said so in advance
and forbade harvesting it:

> failing criterion 3 while passing criterion 1 closes the line as stated —
> "wider is smoother" is not a finding this project needs a trial to establish
> and may not be harvested from this one.

So criteria 1 and 2 passing changes nothing and authorises nothing. They are
recorded because suppressing them would be the same dishonesty in the other
direction.

## Criterion 4 failed on a threshold I mis-specified, and that is charged here

Criterion 4 was meant to catch the wide arm being **starved** —
`min_position_notional` is $500 and refuses rather than rounds, so a drawdown
could stop a hundred-name book refilling and the comparison would silently stop
being 12 against 100. It read "mean open positions ≥ 80". It measured 66.2.

**The arm was not starved.** The observer's own series on sample 3:

```
narrow (cap 12)   4 sessions with any proposal:  11,  9,  3,  4     mean  6.8
wide   (cap 100)  5 sessions with any proposal: 100, 58, 85, 39, 49  mean 66.2
```

The wide arm reached **100 of 100 on its first session.** The statistic is low
for a structural reason that has nothing to do with capital: *the observer only
records on sessions where a proposal reaches the risk layer, and a session only
has proposals when the book has room.* So "mean open positions" is the mean of
*how full the book was at the few moments it was being refilled* — necessarily
below the cap, in both arms. The narrow arm reads 57% of its cap and the wide
arm 66% of its; the wide book fills **better** relative to its own limit.

A correct criterion 4 would have been **peak open positions ≥ 95**, or the count
on the first fill session. Both would have passed. The defect is mine and it is
charged to this registration, in the shape §52 used: the next registration that
needs a book-width guard uses the peak, not a mean sampled at refills.

**The verdict does not hinge on it.** With criterion 4 corrected to the peak,
the trial reads pass / pass / **FAIL** / pass and is still a FAIL, because
criterion 3 is a statement about the mechanism and the mechanism inverted.
Stated explicitly so that nobody later reads this failure as a technicality.

## The window, and why it is a real hold-out

The screening run used `lowvol_bt_population.json` — *"alive on 2020-01-02 and
liquid across 2019"*. Every name in it survived to 2020, so a write-off
hypothesis tested there would be tested where the mechanism cannot act.

This trial built its own universe on the same liquidity rule with **no survival
condition**: span covering 2010-01-04, ≥ 100 bars with volume in 2009, median
daily dollar volume ≥ $1m, read through `price_series` at `as_of` 2009-12-31.

| | count | |
|---|---|---|
| alive on 2010-01-04 | 5,823 | |
| …and liquid in 2009 | **2,393** | the trial universe |
| …of those, no bar after 2019-12-31 | **925** | **38.7% attrition** |

Four disjoint samples of 500, seed 20261125, both registered in advance. The
liquidity screen does select for survivability — attrition falls from 46.8%
across all alive names to 38.7% — and that is a limitation, not a defect: the
window still carries 925 write-offs, which is what made criterion 3 answerable
at all.

## The return columns, which were never criteria

Registered as diagnostics, reported for completeness, and **not evidence**:

| recovery 0.0 | narrow (12) | wide (100) | difference | \|t\| (4 samples, hurdle 6.366) |
|---|---|---|---|---|
| CAGR | 5.16% | 3.84% | +1.32 pp | 1.87 |
| max drawdown | 19.40% | 12.64% | +6.76 pp | 2.05 |
| Sharpe | 0.240 | 0.141 | +0.099 | 1.27 |
| trades over ten years | 36 | 295 | | |
| costs over ten years | $103 | $508 | | |

The narrow book's return advantage is directionally the same as in the screening
window and is **nowhere near** the four-sample hurdle of 6.366. Costs are
$508 against $103 on $100,000 over ten years — 0.05%/yr against 0.01%/yr — so
breadth's lower return is again not a cost story.

## What this closes, and what it does not touch

**Closed.** Breadth as drawdown protection, on this corpus, by the stop rule.

**Not authorised, and not changed.** `max_positions` stays at 12 and
`risk_per_trade_pct` at 0.005. The registration authorised raising them only on
a pass. No default moved in this trial and no published result moved; both arms
were configured on local `StrategyConfig` objects and the observer reached the
engine through `build_engine`'s existing optional `extra_rules`.

**Still true and still recorded elsewhere.** `BREADTH_2026-11-24.md` Finding 1
stands: the twelve-name book is the twelve lowest instrument ids, because every
`EqualWeightBenchmark` candidate ties at a score of 1.0 and `DiversityAwareRanker`
breaks ties on instrument id. This trial compared *twelve arbitrary names* with
*a hundred arbitrary names*. It says nothing about twelve **chosen** names, and
a selection rule could change the answer in either direction.

## The limitations that travel with this failure

1. **Survivorship runs against the hypothesis, so it does not excuse the
   failure.** 52.0% of dated Exchange Act exits are priced
   (`EDGAR_DELISTING_DENOMINATOR.md` §7e); the names the corpus cannot price to
   their end fell furthest. The real write-offs are more numerous and deeper
   than the ones measured here — which, given that frequency is what hurt the
   wide book, would widen the inversion rather than reverse it.
2. **One decade, and a benign one.** 2010–2019 holds no 2008 and no 2020. A
   drawdown hypothesis tested without a crash is tested at the easy end, and a
   crash is the one condition under which concentration's tail could behave
   differently from its decade.
3. **The fixed 8% stop's known defect travels.** §44 found it never binds for a
   calm security and §47 found that fixing it made every arm worse. Both arms
   carry it equally so it is not a confound between them, but neither arm's
   absolute drawdown is what a better ladder would produce.
4. **The four samples share every market event.** They are disjoint in
   securities and identical in calendar, so averaging them removes idiosyncratic
   noise and not market noise. The twenty blocks are the defence and not a
   complete one.
