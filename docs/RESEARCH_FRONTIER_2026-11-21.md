# Where the research has actually got to — 2026-11-21

**Not a trial. Nothing is registered and the ledger does not move: 144 before,
144 after.** This reads `SIGNAL_SCOREBOARD.md` and reports what sixty-one
measurements have covered, what is closed **by written rule**, and what is
left. It was written because the next sensible question was *"which signal do
we try next?"* — and the record answers it differently from how I expected.

## The headline

**The free search space inside this project's frame is close to exhausted, and
it was exhausted correctly.** Every closure is written down with a stop rule
that forbids re-opening it by tuning. That discipline is the most valuable
asset on the scoreboard, and the main risk now is spending trials against a
rising hurdle inside a frame the record already says is empty.

The hurdle is **|t| > 2.6563** on the normal scale and climbs with each trial.
Every further measurement inside the exhausted frame raises the bar for the one
that might matter.

## The frame everything was tested in

From the scoreboard's own specification table:

| | universe | period | horizons |
|---|---|---|---|
| A | 400 securities, no liquidity floor | 2000–2009 | 21, 63 sessions |
| B | 800 securities, liquidity floors | 2000–2009 | 21, 63 sessions |
| C | 800 securities, liquidity floors | **2010–2024, never seen** | 63 sessions |

So: **US equities, daily bars, cross-sectional rank, held 21–63 sessions,
long.** Nine signals and twenty-nine distinct indicator arms were measured
inside it, plus four fundamental signals, eight swing indicators and three
chart patterns.

The scoreboard states its own position plainly: *"Nine signals have been
tested. **No strategy has been evaluated.**"* And after §44: *"One signal
survives as a signal (§35); **nothing survives as a strategy**."*

## What is closed, by a written stop rule

Each of these forbids re-opening the line by trying another parameter.

| § | what closed | the rule |
|---|---|---|
| §27 | market capitalisation, and the whole §26 line | "No other capitalisation…" |
| §32 | 24 classical indicators | "No further indicator…" |
| §34 | `adx_14` | "The lead is closed by the stop rule" |
| §37 | low volatility **as a portfolio** | no other lookback, quintile, holding period, position count, sizing rule or cost assumption |
| §41/§43 | four fundamental signals, point-in-time | closed at the signal level |
| §42 | eight swing indicators | no further indicator, lookback, smoothing or oscillator "without new information" |
| §44 | pattern breakouts as complete trades | no further pattern, threshold or stop band **on daily bars** |
| §47 | the volatility-scaled stop | "no further stop variant is tried… until there is new evidence about the mechanism rather than a new parameter" |
| §48 | the low-vol × earnings-surprise combination | "the combination line closes. No third window, no re-weighting" |
| §49 | timing the market by its own trend | closed on this corpus |
| §51 | confirmed bull-flag breakouts | closed as a trade filter |
| §52 | `volatility_contraction` (VCP) | closed as a trade filter |
| §53 | the pennant | closed as a trade, long **and short** |

## The three things the record itself names as still open

Quoting §42 and §44, which are the sections that closed the largest lines:

> "…without new information, meaning data this corpus does not hold: **intraday
> bars, or a different market**."

> "What this does not close: **combinations** rather than single patterns, **the
> short side** (no detector here is bearish), and anything needing intraday
> data."

Measured against what has happened since:

1. **Combinations** — tried once (§46), passed in sample on the likelier
   delisting assumption, **failed to replicate** out of sample (§48), and §48
   closed the line. Not open any more.
2. **The short side** — partly addressed. §53 measured **32,313 short pairs**
   for the pennant and failed it. No *signal* has been tested short, and no
   detector in the platform is bearish. **Genuinely open, and the system has no
   short-side machinery**: `PositionState` and the sizer are built for long.
3. **Intraday bars, or a different market** — both need data this corpus does
   not hold. `intraday-01` is deferred by [ADR-0030](adr/0030-day-is-deferred-not-deleted.md)
   because it needs paid data and no further data spend is authorised.

## The number any future signal has to beat

> **Measured 2026-11-22**, and it corrected a premise of this document:
> [`THE_FLOOR_2026-11-22.md`](THE_FLOOR_2026-11-22.md). §37's RANDOM arm
> *already was* the buy-and-hold floor under another name — the arm told to hold
> the universe earns **7.74%/yr** against RANDOM's 7.77%, a difference of 0.03
> pp. The benchmark was never missing; it was implicit in the control.
>
> **Corrected 2026-11-24** — [`BREADTH_2026-11-24.md`](BREADTH_2026-11-24.md).
> The 7.74% reproduces exactly and remains the hurdle, but the arm **held
> thirteen names, not the universe**: all candidates tie at a score of 1.0 and
> the ranker breaks ties on instrument id, so the book is the twelve lowest ids
> in each sample. Two consequences for this document — the agreement with
> RANDOM is one coincidence rather than two measurements of the same thing, and
> the hurdle is not a diversified benchmark.

From §37, and it is not the risk-free rate:

> "a random selection run through the platform's stop ladder earned **+7.77% a
> year** on 2020–2025 with a 17.7% drawdown. Whatever a future signal claims, it
> must beat *that*, not beat cash."

For calibration, measured 2026-11-21 in
[`scripts/measure_unarmed_rules.py`](../scripts/measure_unarmed_rules.py): the
moving-average baseline on 27 names over 2010–2019 earns **1.70% a year at a
Sharpe of −0.087**. The baseline is a hurdle and a machinery exercise, never a
candidate — but it is worth seeing that it loses to cash, and that §37's random
arm beats it fourfold.

## The most instructive failure on the board

§44 diagnosed that the platform's fixed 8%-of-price stop **never binds for a
calm security**: the calm arm left by delisting on 6.2% of trades against 0.5%
everywhere else, because a security selected for moving less than everything
else rarely travels 8%. The obvious fix — 2.5 × ATR(14) — was authorised,
registered and run as §47, and **every arm got worse at both recovery
assumptions**, including the control.

> "a stop tight enough to bind on a quiet security is tight enough to sell it on
> noise. The two properties are the same property."

That is a real constraint on risk design, bought with two trials rather than a
live account. It also means the **fixed stop's known defect travels with every
low-volatility result on the board, §37 and §46 included**, as a recorded
limitation rather than a thing to tune away.

## What this implies for the next move

Stated as options rather than a decision, because two of the three cost money
or change scope and both are the owner's.

**A — Stop spending trials inside the frame.** The record says it is empty and
the hurdle rises with each attempt. This costs nothing and preserves the ledger
for a question with new information behind it.

**B — Buy the new information.** §42 and §44 both name intraday bars as the
thing that would make another indicator test meaningful. That is ADR-0030's
deferral and a purchase decision.

**C — Open the short side.** Free of data cost, explicitly named as not closed,
and never tested at the signal level. It is **not** a small change: the sizer,
`PositionState`, the stop ladder and the risk rules are long-only, so this is
platform work before it is research, and borrow cost and assignment risk are
modelling work the cost model does not have.

**What is not on this list is more daily-bar signal search.** Twenty-nine
indicator arms, nine signals, four fundamentals, eight swing indicators and
three patterns have been measured, and every line is closed by a rule written
at the time. Trying a thirtieth without new information would be spending a
trial against a rising hurdle to re-ask a question the record has answered.
