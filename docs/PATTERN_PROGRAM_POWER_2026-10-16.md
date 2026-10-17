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

---

## Addendum — the portfolio route was checked and it fails in-sample

Added the same day, after §48 was re-read and the combination actually computed.
**It does not survive, and the recommendation above for portfolio-level testing
is withdrawn.**

### Why it looked promising

Diversification across setups is real. The seven cells' block differentials
correlate at a mean of **0.17**, giving **3.5 effective independent setups of 7**
and cutting the block sd from 0.96% to **0.52%** — which moves the detectable
effect from ~0.66% to **0.36%**, below several of the observed per-setup effects
for the first time.

### Why it fails anyway: the signs cancel

| setup | trades | in-sample effect |
|---|---|---|
| flat base / confirmed | 13,041 | **+0.837%** |
| VCP / confirmed | 40,513 | **+0.819%** |
| cup with handle / confirmed | 7,229 | +0.647% |
| ascending triangle / confirmed | 11,925 | +0.348% |
| tight consolidation / breakout | 79,362 | +0.191% |
| bull flag / breakout | 118,222 | **−0.125%** |
| pennant / breakout | 49,563 | **−0.752%** |

**Equal-weighted, all seven, no sign selection: +0.318% against a 0.36%
detection limit, t +2.66 against a 2.99 hurdle.** It fails *in-sample*. There
is nothing here to register.

The diversification worked exactly as predicted — the sd fell to 0.52% as
計算ed. The gain was then eaten by the mixed signs: the pennant's −0.752% and
the bull flag's −0.125% cancel against the positives, and the combination
measures the average of effects pointing in different directions.

### The trap, quantified

Selecting each setup's sign from the same data gives **+0.496%, t +3.93** —
comfortably past the hurdle. That number is what screening produces and it is
**not a legitimate test**: the signs are chosen from the window the test would
then be run on. It is recorded here at its exact size so that nobody — me
included — re-derives it later as a finding.

### What this closes

**The pattern program has reached its end on this corpus**, per-trade and
portfolio alike:

* individual effects (0.13%–0.84%) sit at or below what 10 years of disjoint
  blocks resolve (0.39%–0.93%);
* the hold-out window resolves ~1.5% and no setup produces that;
* the honest equal-weighted combination fails in-sample;
* and the placebo is sound, so none of this is a control problem.

Further screening of items 6–40 would add entries in the same band and raise
the ledger hurdle for everything after. **The three options that remain are
shorter horizons where the atlas says the edge is not, portfolio construction
on signals rather than patterns, or more history** — and only the last changes
the arithmetic.

---

## Addendum 2 — the corpus already holds the blocks. The window was the constraint.

Added the same day, when a purchase proposal was requested and **the first
check killed it.** Measured, `research01.sqlite`, raw basis, securities with
≥200 sessions in a calendar year:

| years | securities |
|---|---|
| 1998–2002 | 6,239 – 6,682 |
| 2003–2009 | 6,106 – 6,493 |
| **2010–2019** — what the atlas used | **5,800 – 6,088** |
| 2020–2025 | 5,932 – 6,786 |

**Coverage from 1998 onward matches or exceeds the decade the atlas ran on.**
6,264 securities in 1998 against 5,800 in 2019. `security_spans_2026-09-18.csv`
agrees: 4,194 securities have history beginning in 1997.

### What that does to this document's own conclusion

Section 3 above says a 0.5% effect needs **36 disjoint six-month blocks** and
treats that as out of reach. It is not:

| window | blocks | detectable, single setup | detectable, 7 combined |
|---|---|---|---|
| 2010–2019 (used) | 20 | 0.66% | 0.35% |
| **2003–2019 (available, clean)** | **~34** | **~0.47%** | **~0.25%** |
| 1998–2019 (available, caveated) | ~44 | ~0.45% | ~0.24% |

At ~0.25% the equal-weighted combination's **+0.318% would clear**, and so
would several individual setups. **The conclusion that the program had reached
its end on this corpus was wrong**, and it was wrong because the power analysis
computed what was needed without checking what was held.

### How the error happened, since it will recur

The atlas ran on 2010–2019 because `PATTERN_PROGRAM.md` declared that window,
and nobody — including the program that then spent two ledger trials — asked
why. The answer was two SQL queries away. A purchase was then recommended,
which is the failure `CLAUDE.md` names directly: *free work comes before paid
work, because most of what is needed is free and nobody has finished it yet.*

### The caveat that is real, and whose call it is

`EDGAR_DELISTING_DENOMINATOR.md` records that delisting evidence is **"not
reliably present in EDGAR for the 1998–2002 window"** and calls that "the
single most important caveat in this document". §7e adds that exit pricing is
thinnest in the oldest years — **25.4% for 1998, 52.9% for 1999**. So
1998–2002 is survivorship-compromised: the companies that died then are
under-represented.

A placebo-controlled test draws both legs from the same compromised universe,
which cancels much of that — **but not all, and the residual is not currently
quantified.** So the conservative window is **2003–2019, ~34 blocks**, which
still nearly doubles the blocks with none of the 1998–2002 problem.

### What an extension would and would not be

**It would be a better-powered Stage A, not independent confirmation.**
2003–2019 contains 2010–2019, so any setup that looked good on the shorter
window partly carries forward. That is legitimate for resolution — Stage A is
descriptive and charges no trials — but a setup clearing the hurdle on the
extended window has **not** been confirmed out of sample. The hold-out remains
2020–2025 and remains the only thing that can confirm anything.

**Changing the Stage A window reverses a written rule** in
`PATTERN_PROGRAM.md` ("the atlas is computed on 2010–2019"), so it is the
owner's decision rather than this program's, and is proposed rather than taken.

---

## Addendum 3 — the extension doubled the blocks and the variance rose with them

Measured 2026-10-17 on the extended Stage A window (2003–2019, authorised
2026-10-16). **1,247,565 long pairs and 606,817 short**, against 772,082 and
376,656 on 2010–2019.

Addendum 2 predicted the blocks would roughly double and the detection limit
roughly halve. **The first happened. The second did not.**

| cell | blocks | block sd | detectable | observed effect |
|---|---|---|---|---|
| pennant / breakout | 34 *(was 20)* | 1.00% *(1.12%)* | **0.49%** *(0.74%)* | −0.45% *(−0.75%)* |
| bull flag / breakout | 34 *(20)* | 1.01% *(0.59%)* | 0.49% *(0.39%)* | +0.13% *(−0.13%)* |
| VCP / confirmed | 33 *(19)* | **1.80%** *(0.89%)* | **0.89%** *(0.61%)* | +0.63% *(+0.82%)* |
| cup with handle / confirmed | 30 *(19)* | **2.60%** *(1.36%)* | **1.36%** *(0.93%)* | +0.76% *(+0.65%)* |

**Years of history needed to resolve 0.50%, at the median block sd: 18 became
35.** The median sd rose from 1.00% to 1.40%, because the seven added years
contain **2008**, and a decade containing a financial crisis does not supply
variance at the rate the preceding one did.

### What the extension actually bought

Two setups improved and two got worse. The pennant and bull flag reached a
0.49% limit, close to the 0.47% projected; VCP and cup-with-handle went the
other way. And **the effects themselves shrank** — VCP +0.82% → +0.63%, the
pennant −0.75% → −0.45%. **No setup's effect now exceeds its own detection
limit.**

So the extension did **not** reopen the program. It was still the right call —
it cost only compute, the resolution question needed answering empirically, and
a wash is a real answer — but the gain was assumed rather than measured.

### The error, which is the same one twice

Addendum 2's arithmetic scaled `hurdle × sd / √blocks` by adding blocks **and
held sd constant.** Block variance is a property of the period, not a constant
of the corpus, and 2003–2009 is more volatile than 2010–2019 by construction.
A projection that moves one term of a ratio and assumes the other is fixed is a
guess wearing arithmetic, and it is the second time in two days that a
confident projection about this window has been wrong in a checkable way.

**What stands:** the placebo still works (correlations 0.91–0.99, 54–83% of
market variance removed) and the per-trade route remains out of reach — now for
a better-understood reason. The constraint is not the number of blocks. It is
that **effects of 0.1–0.8% sit below what block variance of 1.0–2.6% can
resolve at any window length this corpus can offer.**
