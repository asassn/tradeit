# What this corpus can resolve, and why two Stage B tests failed the same way

Measured 2026-10-16 after §52 and §53. Reproduce with
`scripts/pattern_power_analysis.py --atlas <atlas40_long.csv>`.

**The program was measuring things slightly smaller than its own resolution.**
That is the finding, and it was available before either trial was spent.

## The two failures share one signature

| | in-sample t | held-out t |
|---|---|---|
| §52 VCP confirmed | 3.82 | 1.02 |
| §53 pennant long | −3.02 | −0.53 |
| §53 pennant short | +4.44 | +2.05 |

Both kept their **direction** and lost roughly two thirds of their
**magnitude**. That is what an effect near the detection limit looks like — not
what a rule that stopped working looks like.

## 1. The minimum detectable effect, beside the effect observed

`hurdle × block_sd / √blocks`, on disjoint six-month blocks, legs beyond 400%
excluded:

| cell | blocks | block sd | hurdle | **detectable** | observed |
|---|---|---|---|---|---|
| VCP confirmed | 19 | 0.89% | 2.99 | **0.61%** | +0.82% |
| pennant breakout | 20 | 1.12% | 2.97 | **0.74%** | −0.75% |
| bull flag breakout | 20 | 0.59% | 2.97 | **0.39%** | −0.13% |
| cup confirmed | 19 | 1.36% | 2.99 | **0.93%** | +0.65% |

The design window resolves **0.4%–0.9%**. The observed effects are
**0.1%–0.8%**. Every setup sits on the boundary, which is why two cleared, three
did not, and the two that cleared did not replicate.

**The held-out window is worse: 11 blocks at roughly double the block sd puts
its limit near 1.5%.** No pattern here produces a 1.5% six-month edge, so the
confirmation window as used **cannot confirm what these patterns do.** That is
arithmetic about the window, not a property of the patterns.

## 2. The placebo is not the problem — checked, because it was the fixable answer

If the block variance were market variance the control had failed to remove,
the limit would be a design flaw and cheap to fix by matching the placebo on
volatility rather than only on stop distance. It is not.

| cell | rule sd | placebo sd | correlation | difference sd | market removed |
|---|---|---|---|---|---|
| VCP confirmed | 5.09% | 4.97% | **0.98** | 0.89% | **83%** |
| pennant breakout | 4.58% | 4.17% | **0.97** | 1.12% | 76% |
| bull flag breakout | 4.85% | 4.61% | **0.99** | 0.59% | **88%** |
| cup confirmed | 5.09% | 4.97% | 0.96 | 1.36% | 73% |

Rule and placebo block means correlate at **0.96–0.99** and the control strips
**73–88%** of the rule leg's own block volatility. The residual 0.6–1.4% is
real dispersion in how pattern trades do against random ones. **There is no
design flaw here to blame**, which is the useful half of this measurement.

## 3. How much history a target effect needs

`(hurdle × sd / target)²` blocks, at the **median** block sd of the four cells
above, which is **1.00%**:

| effect | disjoint 6-month blocks | years |
|---|---|---|
| **0.50%** | 36 | **18** |
| 0.75% | 16 | 8 |
| 1.00% | 9 | 4 |

The corpus holds **10 design years (20 blocks) and 6 held-out (11)**.

*Corrected on first write:* this table was first computed from VCP's own block
sd of 0.89% and labelled "median", giving 14 years. The median of
0.59/0.89/1.12/1.36 is **1.00%**, and the answer is **18 years**. Writing the
calculation as a script rather than a one-liner is what caught it — the same
lesson as `pattern_atlas_report.py`, and the reason section 3 now prints the
sd it used.

## What follows for items 6–40

**Screening the remaining thirty-five setups will not produce a confirmable
result** unless one shows an effect near 1% — roughly double anything seen
across five setups. Not impossible; just a poor use of ~100 hours of compute to
generate more entries in the same 0.1–0.8% band.

Three options, different in kind:

1. **Shorter horizons.** 21 sessions gives ~6× the disjoint blocks for the same
   calendar span: ~120 blocks on 2010–2019, cutting the limit to ~0.25%. The
   catch is that the atlas found every setup *loses* at 21 sessions and gains
   only at 126 — measurable where the edge isn't.
2. **Portfolio-level testing.** "Does a strategy trading these patterns beat the
   market" is one equity curve rather than a differential across overlapping
   trades, and has far better statistical properties. It is also the question
   the roadmap actually asks, and **a portfolio can be profitable on a
   per-trade effect too small to prove trade by trade.**
3. **Buy more history.** 14 years of disjoint blocks means ~28 years of data.
   A purchase proposition, not a compute one.

**Recommended: (2).** It changes the question to the one profitability turns on,
uses the data already held, and does not require the per-trade effect to be
individually resolvable.

## What this does not say

It does not say the patterns work. It says the per-trade test cannot settle it
either way at these effect sizes, and that **§52 and §53 are properly read as
"not established" rather than "refuted"** — a distinction the stop rules
deliberately do not make, because a line that stays open on that reasoning
never closes. Both remain closed.
