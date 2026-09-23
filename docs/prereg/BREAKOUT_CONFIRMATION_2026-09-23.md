# Pre-registration — does a confirmed bull-flag breakout continue?

Committed **before the Phase 5 breakout engine was run over any security** and
before any confirmation statistic existed.

## The question, in the owner's words

*When a bull flag breaks out and the breakout is confirmed by a retest of the
level, what is the probability that price continues up — and is waiting for that
confirmation better than entering at the breakout?*

## Why §44 did not answer it

§44 entered **at** the breakout: the session after a close above the boundary,
no confirmation required. It found bull flags earned **+0.1264R** a trade against
a placebo's **+0.1410R** on 2010–2019 — the market's return, slightly worse.
It never asked what the platform's own confirmation layer adds, because that
layer has never been run: `breakout_events`, `breakout_observations` and
`breakout_labels` are **empty** (§11), and the engine that fills them — thirteen
states, three confirmation paths, `RETEST_CONFIRMED` among them — has been built
and tested synthetically since Phase 5 and measured against real prices **never**.

**Nothing in this registration is tuned.** The confirmation rule is whatever
`BreakoutEngineConfig`'s active profile already declares. No threshold, zone,
evidence requirement or path is adjusted for this test, which is the one
advantage of measuring a policy the platform committed to before the question
was asked.

```
PRE-REGISTRATION -- confirmed breakouts, bull flag only
written 2026-09-23, BEFORE the breakout engine was run on real prices

WINDOW    2010-01-04 .. 2019-12-31. 2020-2025 IS NOT READ.
UNIVERSE  security_spans_2026-09-18.csv with §44's floors: close >= $5,
          20-session dollar volume >= $1,000,000 on the corrected read,
          atr_percent_14 <= 1.0, 100 sessions of own history.
PATTERN   bull_flag ONLY -- the question as asked. No other family is
          measured here; the machinery would allow it and reporting the
          best of twelve is the thing the ledger exists to prevent.

THE EVENT CHAIN, all from the platform as committed
  A bull flag in MATURE or NEAR_BREAKOUT with a resistance boundary
  opens a BreakoutEvent on that boundary, frozen as the scan knew it.
  The engine then advances session by session, and the three arms below
  differ ONLY in which state they enter on.

ARMS -- same securities, same stop, same exit, same costs
  AT-BREAKOUT       enter the session after CLOSED_ABOVE. §44's rule,
                    re-measured inside this run so the comparison is
                    within one sample rather than across two studies.
  RETEST-CONFIRMED  enter the session after RETEST_CONFIRMED: price
                    pulled back to the level, held it, and closed back
                    above. This is the owner's question.
  CONFIRMED-ANY     enter the session after CONFIRMED by any of the
                    profile's paths, retest included.
  PLACEBO           a security drawn at random (seed 20260923) from that
                    session's eligible universe, same stop distance as a
                    fraction of price, same exit. §44's control.

STOP      the pattern's invalidation price, taken only if it sits 3-13%
          below entry -- §44's registered band, reused so the two are
          comparable.
EXIT      the stop, or the close of the 63rd session. A session touching
          the stop counts as STOPPED even if it also traded higher.
COSTS     10 bps a side, and 20 bps as the stress reading.
DEAD      recovery 1.0 AND 0.0 (§50 measured the mix at 73% paid, 6%
          wiped; the bracket is kept because these securities have not
          been classified individually).
§0.10     trades whose window crosses a flagged discontinuity are dropped.

--------------------------------------------------------------------
WHAT IS REPORTED WHATEVER HAPPENS -- the question, answered descriptively

  For each arm: the share reaching +1R before the stop, the share
  positive at 21 and 63 sessions, median return, and the share ever
  reaching 2R and 3R. These are the probabilities asked for, and they
  are reported even when the criteria below fail.

WHAT COUNTS AS "CONFIRMATION ADDS SOMETHING" -- ALL FOUR, per arm,
at recovery 1.0 AND 0.0

  1 It beats AT-BREAKOUT on mean net R per trade, with a t from
    63-session calendar-block means past small_sample_hurdle(142, blocks).
    THIS IS THE CRITERION. Waiting has a cost -- a higher entry, a wider
    stop, and trades that never confirm -- and it has to be paid for.
  2 It beats its PLACEBO on the same statistic. §44's lesson: a rule
    that beats the alternative entry but not a random stock has
    discovered the market.
  3 Positive against the placebo in both halves, 2010-2014 and 2015-2019.
  4 It survives 20 bps a side.

STOP RULE
  If neither confirmed arm passes, **the Phase 5 confirmation layer is
  closed as a trade filter on this corpus**: no other profile, evidence
  threshold, confirmation path, pattern family or entry offset is tried.
  It remains what ADR-0025 says it is -- an observational state that
  authorises nothing -- and this registration will have established that
  it also predicts nothing.

TRIALS  2 -- RETEST-CONFIRMED and CONFIRMED-ANY. AT-BREAKOUT is §44's
        rule re-measured and PLACEBO is a control; neither is charged.
        Ledger 140 -> 142.
```
