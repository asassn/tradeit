# Pre-registration — does a confirmed VCP breakout beat a placebo out of sample?

Committed **before any part of 2020–2025 was read for this question**, and
before the eligibility file, the market index or the scan existed for that
window. The scan refuses to run past 2019 unless handed the path of a
pre-registration that is already committed, and prints that commit's SHA into
its own output; this is that registration.

## The question

*A volatility-contraction pattern whose breakout the platform's own engine
calls `CONFIRMED`, entered in an uptrend and held 126 sessions — does it beat a
random security bought the same session with the same stop distance?*

## Why this candidate and not the one that was advertised

`PATTERN_PROGRAM.md`'s Stage A measured eleven long detectors and their mirrors
on 2010–2019. The bull flag was reported first and most prominently, on its
**+1R hit rate** (+2.4pp against its placebo). Converted to a block t-statistic
that candidate is **1.93** and would fail. The hit-rate column is the wrong
lens for a six-month horizon, and three candidates beat it on the statistic
that decides:

| candidate | blocks | mean/block | t | hurdle 2.98 |
|---|---|---|---|---|
| **VCP, confirmed, uptrend** | 19 | **+0.706%** | **3.68** | clears |
| cup-with-handle, confirmed, uptrend | 19 | +1.108%* | 3.28* | clears* |
| flat base, confirmed, uptrend | 19 | +1.067%* | 2.91* | just under |

\* measured before the stop-window fix below and **not** re-measured; they are
listed to record what was looked at, not as results.

**The design-window number fell by 40% before it got here.** It was first
measured at +1.175% a block, t = 4.38. `outcomes()` searched for the stop over
63 sessions while reporting returns at 126, so a position stopped out on
session 80 was scored at its session-126 close as though it had been held. The
fix is at `9aafe41`; re-measured with stops honoured the whole way the
candidate reads **+0.706% a block, t = 3.68, 14 of 19 blocks positive**, and
the rule's own six-month return falls from +3.19% to **+2.71%**.

**One horizon is registered, not two.** The same re-measure put 63 sessions at
+0.539%, t = 3.38, and 17 of 20 blocks positive — *more consistent, smaller*.
126 sessions is registered because that is what was recommended and authorised
before either number existed. Choosing the better of two after seeing both
would be selection on the design window, which is the thing this document
exists to prevent. **The 63-session reading is descriptive and charges no
trial.**

```
PRE-REGISTRATION -- VCP confirmed breakouts, held-out window
written 2026-10-14, BEFORE 2020-2025 was read for this question

WINDOW    entries 2020-01-02 .. 2025-12-31. Outcomes read forward to the
          corpus end (2026-09-14) so a 126-session horizon can complete;
          a trade without a full 126 sessions reports blank and is
          excluded, as on the design window.
UNIVERSE  security_spans_2026-09-18.csv, the SAME file as the design
          window, with the SAME floors: close >= $5, 20-session dollar
          volume >= $1,000,000, atr_percent_14 <= 1.0, 100 sessions of
          own history, 250 bars minimum.
PATTERN   volatility_contraction ONLY. The scan is passed
          --patterns volatility_contraction so the other ten are not
          even written. The window is spent one question at a time, and
          having the others on disk would invite a second look.
ARM       CONFIRMED only -- the engine's CONFIRMED state by any of the
          active profile's paths. AT-BREAKOUT and RETEST_CONFIRMED are
          NOT measured here.
REGIME    uptrend only: the corpus's own equal-weighted index at or above
          its 200-session mean on the entry session.
HORIZON   126 sessions.
ATTEMPTS  up to 3 events per pattern identity, each opening only after
          the previous resolved -- the design window's rule.
ENTRY     the open of the session after CONFIRMED is first reached.
STOP      the pattern's invalidation price, taken only if it sits 3-13%
          below entry. The design window's registered band.
EXIT      the stop, or the close of the 126th session. The stop is
          searched over the WHOLE 126 sessions (9aafe41).
COSTS     10 bps a side; 20 bps as the stress reading.
PLACEBO   one security drawn at random from that session's eligible
          universe per trade, seed 20260924, same stop distance as a
          fraction of price, same horizon, same costs.
§0.10     trades whose window crosses a flagged discontinuity are
          dropped, using ALL EIGHT jumps shards. The atlas of 2026-09-25
          was run with five of them and had to be discarded.
GUARD     any cell containing a leg beyond 400% has its mean withheld
          and the security named, rather than averaged.

INFRASTRUCTURE BUILT AFTER THIS FILE IS COMMITTED, NOT BEFORE
  The eligibility file and the market index currently stop at
  2019-12-31 and must be extended to cover the window. Both are built
  by rules already fixed on the design window -- the same floors, the
  same WINSOR, MIN_SECURITIES and REGIME_LOOKBACK constants, the same
  scripts -- so no choice in their construction is informed by the data
  they will classify. The index is rebuilt continuously from 2009 so the
  200-session lookback at 2020-01-02 is real history and not a cold
  start. Nothing about the rule above may change once they exist.

--------------------------------------------------------------------
WHAT IS REPORTED WHATEVER HAPPENS

  Occurrence count, the share reaching +1R before the stop against the
  placebo's, mean and median paired net return at 5/10/21/63/126
  sessions, the rule leg's own return, the exact-tie share, and the win
  share among decided pairs. Reported even when the criteria fail.

WHAT COUNTS AS A PASS -- ALL FOUR

  1 Mean paired net_126 beats the placebo with |t| past
    small_sample_hurdle(143, blocks), t from non-overlapping six-month
    calendar-block means. THIS IS THE CRITERION.
  2 Positive against the placebo in BOTH halves of the window,
    2020-2022 and 2023-2025. The design window's edge was an uptrend
    phenomenon; a result carried by one half is not a result.
  3 Survives 20 bps a side.
  4 The cell is not withheld by the 400% guard. A pass resting on a
    corpus defect is not a pass.

STOP RULE
  If it fails, **volatility_contraction is closed as a trade filter on
  this corpus.** No other arm, horizon, regime, stop band, cost
  assumption or pattern family is substituted, and the window is not
  read again for this setup. The atlas entry stands as a description of
  2010-2019 and nothing more.

  If it passes, that is ONE out-of-sample confirmation of a +0.7%
  per-trade edge over six months. It authorises a walk-forward and
  paper trading under the readiness framework. It does NOT authorise
  live capital, which `src/tradeit/config.py` gates regardless.

TRIALS  1 -- the CONFIRMED arm at 126 sessions. The placebo is a control
        and the 63-session reading is descriptive; neither is charged.
        Ledger 142 -> 143. Hurdle at 19 blocks: |t| > 2.98.
```

## The limitations that travel with any result here

1. **Survivorship.** 52.0% of dated Exchange Act exits are priced (§7e). A
   long result measured on this corpus is optimistic and this one is no
   exception.
2. **The window is six years, and the first of them is 2020.** A result driven
   by the February–March 2020 crash and its recovery is a result about one
   event. Criterion 2 is there for that and may not be enough.
3. **Overlapping trades.** 126-session holds opened days apart share most of
   their path. The block statistic is the defence; it is not a complete one.
4. **This is one setup out of eleven measured.** The ledger hurdle prices that
   in, and the hurdle is why a +0.7% edge needs t > 2.98 rather than t > 2.

---

## Amendment 1 — the index's `as_of` is pinned, 2026-10-14

Written **after the eligibility build began and before any outcome existed**,
which is the only window in which an amendment is legitimate. It changes no
rule, no arm, no horizon and no pass criterion. It records a fact the original
text left open and the recomputed baseline that follows from closing it.

**What was left open.** The registration said *"the corpus's own
equal-weighted index"* without naming which build. Two now exist, and they
disagree about the design window: the one used to pick this candidate ran
`as_of` 2019-12-31, and the one the hold-out test needs runs `as_of`
2026-09-14 so the window has a continuous 200-session lookback.

**Why they disagree at all, which is the part worth recording.**
`price_series` back-adjusts for every split it knows at its `as_of`. At
`as_of` 2026 a security that split 2-for-1 in 2022 has its 2015 prices halved,
so a $6 stock then reads as $3 and **fails the $5 eligibility floor**. The
index therefore contains ~35 fewer securities a session across 2009-2019
(1,996 against 2,031) and its level path differs by up to 8.7%.

**This is weak look-ahead and it is named as such.** A 2015 session's regime
label depends on whether a security split in 2022. The index moves on
*returns*, which split adjustment leaves alone, so the level path is only
mildly affected; what moves is which securities clear the price floor. A truly
point-in-time index would need rebuilding at every session's own `as_of` and is
a project rather than a correction. **It is recorded as a limitation of the
regime variable, not fixed here, and it applies to both windows equally.**

**The pin.** `market_index_full.csv`, built `as_of` 2026-09-14, is the index
for this test and for the baseline it is judged against. Using one build for
the design window and another for the hold-out would have meant the two were
never comparable.

**The baseline, recomputed on that index.** The candidate survives the switch:

| index | uptrend pairs | mean/block | t | hurdle | blocks + |
|---|---|---|---|---|---|
| `as_of` 2019 | 43,435 | +0.706% | 3.68 | 2.98 | 14/19 |
| **`as_of` 2026 (pinned)** | 42,985 | **+0.710%** | **3.82** | 2.98 | 14/19 |

450 fewer pairs, the same conclusion. **+0.710% a block, t = 3.82** is the
design-window figure the out-of-sample result is compared against.

**The held-out window is a harder test than the design window**, measured
before it was scanned: **65.9% of its sessions are uptrend against the design
window's 79.4%**. Criterion 2 splits it at 2022/2023 for the reason given
above, and a 2020-2022 half containing the February-March 2020 crash and its
recovery is a half about one event.
