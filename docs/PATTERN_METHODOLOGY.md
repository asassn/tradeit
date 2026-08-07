# Pattern Methodology

What each of the twelve families claims, how the claim is measured, and where the
families genuinely overlap.

The purpose of the matrix in §2 is to stop a future developer treating twelve
detectors as twelve unrelated black boxes. Most of them share machinery and
several share most of their geometry; what differs is the **claim**, and the
claim is what a component set encodes.

---

## 1. The three kinds

| Kind | Families | Context required | What it asserts |
| --- | --- | --- | --- |
| **Continuation** | bull flag, VCP, flat base, ascending triangle, pennant, cup and handle, high tight flag | a prior **advance** | this pause is a digestion of the advance, not its end |
| **Reversal** | double bottom, inverse head and shoulders | a prior **decline** | selling is exhausting |
| **Structural** | base on base, tight consolidation, breakout retest | varies | a statement about *relationship* or *state* rather than about a single shape |

The context requirement is the single most important dividing line, and it is why
a reversal detector cannot be built by flipping a continuation detector's sign.
The flag's `prior_trend` asks *how strong was the advance we are pausing inside*;
the double bottom's `prior_decline` asks *how much damage is there to repair*,
measured peak-to-low rather than across a lookback, and scored as a band because
more damage is not more evidence — a first bounce is rarely the end of a 60% fall.

---

## 2. Interpretation matrix

| Pattern | Kind | Prior trend | Impulse | Horizontal resistance | Converging boundaries | Progressive contraction | Reversal lows | Neckline | Handle | Parent structure | Prior breakout |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Bull Flag | cont. | ● up | ● | ○ | — | ○ | — | — | — | — | — |
| VCP | cont. | ● up | — | ○ | — | ● | — | — | — | — | — |
| Flat Base | cont. | ● up | — | ● | — | — | — | — | — | — | — |
| Ascending Triangle | cont. | ○ up | — | ● | ● | — | — | — | — | — | — |
| Pennant | cont. | ○ up | ● | — | ● | ○ | — | — | — | — | — |
| Cup and Handle | cont. | ○ up | — | ● rims | — | — | — | — | ● | — | — |
| High Tight Flag | cont. | ● extreme | ● | — | — | — | — | — | — | — | — |
| Double Bottom | rev. | ● down | — | ○ | — | — | ● two | ● | — | — | — |
| Inverse H&S | rev. | ● down | — | — | — | — | ● three | ● sloped | — | — | — |
| Base on Base | struct. | ● up | — | ● both | — | ○ | — | — | — | ● two bases | — |
| Tight Consolidation | struct. | ● up | — | — | — | ● vs own past | — | — | — | — | — |
| Breakout Retest | struct. | — | ○ | ● | — | — | — | — | — | ● the level | ● |

● required — ○ scored but not required — — not part of the definition

### Reading the matrix

**Bull Flag and Pennant** differ in one cell: converging boundaries. Both need an
impulse and a pause; a pennant's boundaries close, a flag's run parallel. They
coexist constantly and that is correct.

**VCP and Flat Base** differ in `progressive contraction`. A flat base asserts a
*level* held; a VCP asserts a *trajectory* of narrowing legs. A base that is
consistently shallow is a flat base and a poor VCP; a base that tightens is both.

**Tight Consolidation** shares geometry with almost everything and is
distinguished by what it measures against: **the instrument's own recent past**,
not an absolute threshold. Under an absolute rule a utility trading in a 2% weekly
range qualifies every week of its life, which is why every measurement in that
detector is a ratio.

**Base on Base** is the only family whose defining component rewards the
*absence* of movement. A large advance between the two bases means they are two
bases in a rising sequence — a stair-step, which is more common and a weaker
claim.

**Breakout Retest** is the only family that presupposes a prior breakout, which
is why it never occupies NEAR_BREAKOUT and why the lifecycle carries a family
constraint for it.

---

## 3. Per-family summary

Each detector's module docstring carries the full methodology. This is the index.

### Bull Flag
A sharp advance, then a shallow drift that gives back part of it on contracting
range and drying volume. Components: flagpole, consolidation, retracement,
duration, volume structure, volatility contraction, relative strength, resistance
quality. The pole ends at the highest **confirmed** high — not at a searched
split, which is the selection-bias bug that produced ADR-0014.

### VCP
Successively tighter contractions inside a base. The defining component is
`contraction_progression`, which measures the *trajectory* rather than the net
change: a base ending tight without being a staircase scores below one that
tightened at every step.

> **Causal property worth knowing.** The final contraction of a forming base is
> never confirmed — its low sits in the provisional tail — so the detector always
> evaluates the progression **one leg behind**. A textbook three-leg VCP scores
> its progression at about 54 while forming and about 96 once the last leg
> confirms. This is not a defect; it is what causal pivot confirmation costs, and
> it means a forming VCP's progression score systematically understates it.

### Flat Base
A shallow horizontal range after an advance. Single-anchor: the base starts at
the earliest confirmed high at the peak level.

### Ascending Triangle
A flat ceiling with lows climbing into it. Touches must be **contiguous** — an
early draft let the resistance cluster reach back through the whole series and
produced an 83-session "triangle" from a 40-session one with negative rising-lows
slope.

### Pennant
A sharp impulse then a symmetric converging wedge. Symmetry is what separates it
from a descending wedge, where only the highs come down.

### Cup and Handle
A rounded decline and recovery, then a shallow handle. **Roundness is measured as
time spent near the low**, not by fitting a curve — fitting rewards charts that
look like a picture of a cup; this rewards charts where supply was absorbed. The
band is a fraction of the cup's **own depth**, because a fixed 10%-of-price band
is a sliver inside a 45% cup and the whole range of a 12% one.

Discovery anchors on the **rim pair**, not on the deepest low. An earlier draft
took the deepest confirmed low in range, which on a realistic series is the
pre-advance level — so the "cup" it described spanned the advance itself.

### High Tight Flag
A near-vertical advance (≥70%) and a shallow pause (≤25%). **Precision over
recall, deliberately**: a detector tuned until it fires on ordinary bull flags is
not a rarer pattern, it is a duplicate under a name that implies more. The
magnitude floor is a **discovery gate**, not a score, so a 40% advance produces
no instance rather than a weak one a low threshold could recover.

### Double Bottom
Two lows at a level after a decline, split by a real rally. Requires that the
level **held**: a confirmed low between the two that breaks it means it did not,
and the structure is something else — most obviously an inverse head and
shoulders, whose shoulders sit at a common level with the head beneath.

A small undercut of the first low is **constructive** and scores above an exact
match: it takes out the obvious stops before turning.

### Inverse Head and Shoulders
Three lows, the middle materially deepest, with a neckline through the two
intervening highs. Asymmetry tolerances are deliberately loose — textbook
illustrations are symmetric and real structures are not, so the match is scored
rather than required.

### Base on Base
Two consecutive bases at a similar level, the second giving up no ground. Two
ceilings inside the touch tolerance describe **one** base, not two, and the
detector says so — which is why its candidate rate is intermittent right at that
boundary.

### Tight Consolidation
A window that is tight **for this instrument**. Range against its own prior
range, true range against its own prior true range, volume against its own
baseline.

> True range is measured on each window's own bars, not from the smoothed ATR
> series. ATR carries a fourteen-session memory and these windows are five to
> twenty-five sessions long, so ATR *inside* a tight window is mostly the
> volatility that preceded it — measured that way the component reported no
> contraction for a window whose bars had genuinely halved in range.

### Breakout Retest
A level crossed and revisited. Every measurement is geometric; whether the
breakout is *confirmed* is Phase 5's question and this module has no vocabulary
for it. The level is built only from data **preceding** the break, because
clustering the pullback's own highs lets the retest help define the line it is
retesting.

---

## 4. What no detector does

- Claim a breakout is valid.
- Use future bars to define structure.
- Select a window because it scored well.
- Compare its score to another family's.

The last is worth restating: **scores are not comparable across families.** A
cup's 88 and a pennant's 88 mean different things, because each is relative to its
own structural definition and no cross-family calibration exists. Establishing one
requires labelled real data, which Phase 4 does not have.
