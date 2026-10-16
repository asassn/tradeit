# Pre-registration — do pennant breakouts underperform the market?

Committed **before 2020–2025 was read for this question.** The second Stage B
test of the pattern program, and the first registering a **negative** edge.

## The hypothesis

*A pennant that breaks out in an uptrend, bought at the next session's open and
held 126 sessions, **underperforms** a random security bought the same session
with the same stop distance.*

Registered as an **exclusion rule**: the claim is that this setup is worse than
nothing, not better.

## Why this one, and what was screened to find it

Stage A measured eleven detectors in both directions. The pennant is the only
setup whose result is **coherent across both directions and monotonic across
every horizon** — which is what makes it worth a trial rather than a curiosity:

| horizon | pennant bought | t | pennant shorted | t |
|---|---|---|---|---|
| 5 | −0.061% | −1.31 | +0.073% | 1.40 |
| 10 | −0.046% | −0.68 | +0.125% | 2.42 |
| 21 | −0.115% | −1.13 | +0.365% | 3.49 |
| 63 | −0.371% | −2.50 | +0.740% | 4.37 |
| **126** | **−0.752%** | **−3.02** | **+1.096%** | **4.44** |

A pennant is a symmetric triangle, so its mirror is also a pennant: these are
**the same events scored from both sides**, not two findings. A noise artefact
has no reason to strengthen at every one of five horizons in both directions at
once.

**Robustness, measured before registering:** 19 of 20 blocks positive on the
short side, median +0.712% against a mean of +1.096%, and dropping the single
best block *raises* t to 4.51. Excluding every leg beyond 400% changes the long
side from −0.742% to **−0.752%** and drops one pair, so the §0.10 guard-rail's
withheld mean in this cell is immaterial to the result. That was checked
**because the withheld leg (+549%) pushed in the same direction as the
finding**, and a result resting on it would have been an artefact.

**What was screened.** Ten pennant cells, after roughly twenty-four for the
ascending triangle, across eleven detectors and two directions. **A t of 4.44
found by screening is not a t of 4.44 registered in advance**, and the ledger
hurdle prices trials rather than screens. This registration exists to convert
one screened observation into one spent trial.

```
PRE-REGISTRATION -- pennant breakouts underperform, held-out window
written 2026-10-15, BEFORE 2020-2025 was read for this question

WINDOW    entries 2020-01-02 .. 2025-12-31, outcomes to the corpus end
          2026-09-14. A trade without a full 126 sessions is excluded.
UNIVERSE  security_spans_2026-09-18.csv with the design window's floors:
          close >= $5, 20-session dollar volume >= $1m,
          atr_percent_14 <= 1.0, 100 sessions of own history, 250 bars.
PATTERN   pennant ONLY, --patterns pennant. The other ten are not written.
ARM       closed_above ONLY -- the plain breakout. The confirmed and
          retest arms are NOT measured here; the long side's confirmed
          arm read -0.116% at t -0.43 in the design window and is not
          the hypothesis.
REGIME    uptrend only, by the corpus's own index against its
          200-session mean. market_index_full.csv, as_of 2026-09-14,
          the build pinned in VCP_CONFIRMED_HOLDOUT amendment 1.
HORIZON   126 sessions, stop searched over the WHOLE 126 (9aafe41).
ATTEMPTS  up to 3 per pattern identity, as the design window.
ENTRY     the open of the session after CLOSED_ABOVE is first reached.
STOP      the pattern's invalidation price, 3-13% below entry.
COSTS     10 bps a side; 20 bps as the stress reading.
PLACEBO   one random security from that session's eligible universe per
          trade, seed 20260924, same stop fraction, same horizon.
§0.10     all eight jumps40 shards, at the 4.0 factor adopted 2026-10-15.
BORROW    the short-side arm charges 3%/yr, as SHORT_SIDE_DESIGN.md.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FOUR

  1 Mean paired net_126 on the LONG side is NEGATIVE with |t| past
    small_sample_hurdle(144, blocks), t from non-overlapping six-month
    calendar-block means. THIS IS THE CRITERION, and its sign is fixed
    in advance: a positive result is a FAIL, not a discovery.
  2 Negative in BOTH halves, 2020-2022 and 2023-2025, AND the halves
    must AGREE -- the weaker at least one third the magnitude of the
    stronger. §52's criterion 2 asked only for a shared sign and passed
    a half of +0.024% against +1.360%, which is a wash dressed as
    agreement. That defect is corrected here and charged to §52.
  3 The MIRROR must hold: the short side of the same events, priced with
    short arithmetic and borrow, must be POSITIVE out of sample. The
    long/short coherence is why this was registered at all, so losing it
    falsifies the hypothesis even if criterion 1 passes. This is a
    conjunction on one hypothesis, not a second trial.
  4 No leg beyond 400% in either arm. A pass resting on a corpus defect
    is not a pass.

STOP RULE
  If it fails, **the pennant is closed on this corpus, as a trade filter
  AND as an exclusion rule.** No other arm, horizon, regime, stop band,
  cost assumption or direction is substituted -- in particular the short
  side is NOT re-tested on its own, which is the obvious move after a
  long-side failure and is exactly what this rule forbids.

  If it passes, that is ONE out-of-sample confirmation that a named
  setup predicts UNDERperformance. It authorises using the pennant as a
  negative screen inside a strategy, and a walk-forward. It does NOT
  authorise shorting pennants: the short's own six-month return in the
  design window is -1.309%, so it beats its placebo while losing money,
  and `config.py` gates live capital regardless.

TRIALS  1 -- the long side's negative edge at 126 sessions. The mirror is
        a falsification criterion on the same hypothesis and the
        placebo is a control; neither is charged.
        Ledger 143 -> 144. Hurdle at 20 blocks: |t| > 2.97.
```

## The limitations that travel with any result here

1. **This is an exclusion rule, and exclusion rules are cheap to believe.**
   "Do not buy X" costs nothing to follow and cannot be checked by trading it.
   Its value is only as a filter inside a strategy that trades something else.
2. **Survivorship.** 52.0% of dated Exchange Act exits are priced (§7e). For a
   rule predicting *under*performance this cuts the unfamiliar way: the
   companies the corpus cannot price to their end are the ones that fell
   furthest, so a long-side underperformance edge measured here is **
   conservative**, not flattered.
3. **The hold-out is 65.9% uptrend against the design window's 79.4%**, and the
   regime filter means 2022 contributes almost nothing — §52 found two trades in
   all of it. A rule conditioned on uptrends cannot be tested by a bear market.
4. **Overlapping trades.** 126-session holds opened days apart share most of
   their path; the block statistic is the defence and not a complete one.
