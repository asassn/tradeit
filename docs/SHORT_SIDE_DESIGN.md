# The short side: how it is measured, and what it cannot be asked

Written 2026-09-24, before any bear-flag number existed, at the owner's
direction: *"after we are done doing a deep into this bull flag breakout +
confirmation + retest, i also want to look into bear flag breakdown +
confirmation + retest to look at shorting opportunities."*

This is a **method and assumption record**, not a pre-registration. Stage A of
`PATTERN_PROGRAM.md` charges no trial and claims nothing. It exists because
shorting introduces costs and constraints the long side does not have, and
those had to be fixed in writing before a number could be chosen to suit.

---

## 1. There is no bearish detector, and there does not need to be

The platform has twelve detectors and an 11,000-line breakout engine, all
long-directional, all audited. A bearish twin would be a second copy of both,
needing its own audit and drifting from the first.

**So the series is mirrored instead of the machinery.** Under the reciprocal map

```
open' = 1/open    high' = 1/low    low' = 1/high    close' = 1/close
```

a bear flag *is* a bull flag, a breakdown through support *is* a breakout
through resistance, and the existing detectors and engine measure the short
setup unchanged. `src/tradeit/patterns/mirror.py` implements it and
`tests/unit/test_pattern_mirror.py` proves it inverts direction rather than
doing nothing — the synthetic bull flag is found, its mirror is not, and
mirroring back finds it again. Each of those assertions was shown to fail
against a deliberately broken mirror.

**Why the reciprocal and not `C − price`.** Reflection about a constant also
swaps highs for lows, but it destroys the multiplicative structure every
threshold in this codebase is written in — a percentage gain, `atr_percent`, a
volume ratio. The reciprocal is the only map that inverts direction and leaves
ratios intact.

### What the mirror is exactly right about

**The order in which price levels are touched.** `1/x` is strictly decreasing
on positive prices, so a bar whose low crosses below a level is exactly a bar
whose mirrored high crosses above the mirrored level. Every engine state —
`TESTING_RESISTANCE`, `CLOSED_ABOVE`, `RETEST_CONFIRMED`, `FAILED_BREAKOUT` —
therefore fires on exactly the session its bearish twin would have.

### What it is wrong about, and is never used for

**Returns.** A short sold at `E` and covered at `X` returns `1 − X/E`; the
mirrored long returns `E/X − 1`. Halve the price and the short makes 50% while
the mirror makes 100%. **Every short return in this project is computed on the
original prices with short arithmetic**; the mirror is unwound in the scan and
never reaches the replay. A test asserts the two numbers differ, so a future
change that quietly routed returns through the mirror would fail.

**Eligibility.** A $5 floor applied to a reciprocal is meaningless. Liquidity
is judged on real prices, always.

**Symmetry of percentages.** A detector wanting a 20% pole accepts a 20%
mirrored move, which is a 16.7% decline. This is log-symmetry, which is the
defensible choice and the only self-consistent one — but it means a bear flag
here is the log-mirror of a bull flag, not a separately specified structure.

### The check that the mirror works on real prices, not just synthetic ones

The unit tests prove the mirror inverts a *generated* bull flag. They cannot
prove the detectors, run through it, pick out bearish structures in this
corpus. So a 3-security pilot was scanned and every short entry's prior price
action measured — if the mirror were subtly wrong, entries would follow rallies
rather than declines.

| | |
|---|---|
| short entries in the pilot | 57 |
| price change over the 5 sessions before entry | median **−4.06%**, mean −4.42% |
| price change over the 20 sessions before entry | median **−6.00%**, mean −7.19% |
| share of entries preceded by a 5-session fall | **100%** |

Every entry follows a decline, and the decline is of bear-flag magnitude. This
is the measurement that licenses running the real scan; it is descriptive, and
no trial is charged for it.

---

## 2. Costs a short pays that a long does not

| cost | how it is handled | why |
|---|---|---|
| commission and spread | **charged, identically to the long side** | 10 bps a side, as §44 onward |
| **borrow fee** | **charged at 3%/yr, as an assumption** | no vendor here carries a borrow rate |
| dividends paid to the lender | **not charged — a known understatement** | the corpus has actions but the study is not wired to them |
| locate availability | **cannot be modelled at all** | see below |
| recall risk | **cannot be modelled at all** | see below |
| SSR / uptick rules | **not modelled** | needs intraday, which §2a-i rules out |

**The borrow rate is an assumption, stated as one.** 3%/yr sits above general
collateral (a liquid large cap lends for a handful of basis points) and far
below a hard-to-borrow name (which can cost tens of percent). At 126 sessions
it removes 1.5% from every trade — larger than the entire long-side six-month
edge the bull-flag atlas found, which is exactly why it is charged before the
result is seen rather than after.

**Locate is not a cost, it is a gate.** A security nobody will lend is not
expensive to short; it is impossible. This corpus has no short-interest, no
utilisation and no locate data, so the study **cannot distinguish a profitable
short from an unborrowable one** — and the names most likely to be
unborrowable are small, heavily-shorted and falling, which is precisely the
population a bear-flag screen selects. **Any short edge found here must be
read as an upper bound on what was tradeable.**

---

## 3. The survivorship bias runs the other way, and that matters

Every long result in this project carries `§7e`'s warning: 52.0% of dated
Exchange Act exits are priced, so the corpus under-represents companies that
died, and long backtests are flattered.

**For a short, the same gap is a penalty.** The companies this corpus cannot
price to their end are the ones that went to zero — and those are the trades a
short would have profited most from. A short study run here systematically
omits its best outcomes.

So the two biases point in opposite directions, and it is worth being plain
about which way each cuts:

- a long edge measured here is **optimistic** and needs discounting;
- a short edge measured here is **conservative on survivorship** and
  **optimistic on borrow and locate**, and the two do not cancel in any
  quantity anybody here can compute.

Neither statement is a licence. They are the two limitations that travel with
every short number this project produces, and they belong beside it every time.

---

## 4. What would settle the questions this cannot

Listed so the gap is a known one rather than a surprise later. **None of this
is proposed for purchase**; it is what a purchase would have to buy.

1. **Borrow rates and utilisation history** — would replace the 3% assumption
   with a measurement and, more importantly, would say which of these trades
   could have been opened at all.
2. **Short interest and days-to-cover** — would let the crowding effect be
   tested rather than assumed away.
3. **Intraday bars with real depth** — would let SSR, the uptick rule and
   borrow-driven squeezes be modelled. §2a-i rules the current free feed out.

Until then the short side is measured with the limitations above attached, and
a short result is not promoted past Stage A on this evidence alone.
