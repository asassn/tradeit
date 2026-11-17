# ADR-0036: Valuation is a panel, not a verdict

**Status:** accepted
**Date:** 2026-11-17
**Approved by:** the owner, 2026-11-17, who asked for the panel form after the
argument below was put to him.
**Supersedes** the unapproved label threshold introduced with
[ADR-0035](0035-the-dashboard-shows-what-the-record-holds.md)'s valuation work,
which is deleted rather than left pending.

## Context

The roadmap specifies, for Phase 10:

> **Valuation and the undervalued / fairly valued / overvalued callout.** A
> short human-readable verdict when browsing or selecting a name, backed by
> multiples, the security's own valuation history, and sector-relative
> comparison. **The callout is never shipped without the reasoning that
> produced it** --- a one-word label with no visible methodology is an opinion
> wearing a badge, and it would be acted on far more confidently than its
> evidence deserves.

The reasoning was built first (`src/tradeit/valuation/`). Building it made the
verdict look unsupportable for three independent reasons, each of which would
be sufficient on its own.

**1. A multiple has no absolute meaning.** For a stable company,
`P/E ≈ payout / (r − g)`: the multiple is *determined by* growth and risk. A
P/E of 10 can be expensive and a P/E of 40 can be cheap. A label requires a
cut-off; any cut-off that does not carry growth and risk is comparing nothing,
and one that does is a forecast wearing a threshold's clothes.

**2. Valuation is horizon-dependent, and this system has three horizons.**
Expensive barely matters over a swing holding period and matters enormously
over a retirement one. A single callout per security would be exactly the
*"universal Buy/Sell number"* the architecture exists to prevent, arriving in
the one surface a person browses rather than through the scoring path where it
would have been caught.

**3. The measured evidence here is negative.** `SIGNAL_SCOREBOARD` §40–41
tested four fundamental signals point-in-time on this corpus — earnings
surprise, gross profitability, asset growth, accruals — and **none passed**.
All four are closed. A confident valuation verdict on a corpus where
fundamentals have not yet predicted anything asserts more than the evidence
carries.

The first implementation held the gap open with `ThresholdSource.UNAPPROVED`,
so the verdict read `UNRESOLVED` and named what it was waiting for. That was
honest and still wrong: it framed the missing piece as an approval that would
one day arrive, when the three reasons above say the label should not be
printed at all.

## Decision

**Ship a panel of checkable facts; print no verdict.** `ValuationPanel`
reports:

1. **Free cash flow yield on enterprise value** — cash against the whole
   company's price, rather than accounting earnings against the equity slice.
2. **Where that yield sits in the security's own history**, with the number of
   observations it rests on.
3. **What the price implies** — the perpetual growth the yield pays for, at
   several required returns, so the role of the discount rate is visible
   rather than buried in one number.
4. **Whether cheapness arrives with deterioration** — the value-trap check.
5. P/E and P/B, as explicitly secondary.

A test asserts the panel has no `verdict`, `label`, `rating` or `callout`
attribute, so the reasoning cannot quietly return to somebody's memory.

**This removes a threshold rather than deferring one.** Every number in the
panel is measured or supplied by the caller. There is nothing left to approve,
which is a better outcome than an approved cut-off would have been.

### Why FCF/EV rather than P/E as the headline

*Cash rather than earnings*: earnings are the output of accrual choices —
revenue recognition, depreciation, capitalisation. Cash from operations less
capital spending is far harder to present favourably.

*Enterprise value rather than price*: price ignores the balance sheet. Two
companies on the same P/E, one debt-free and one levered three times, are not
equally priced.

### Why the reverse perpetuity rather than a forward DCF

A forward DCF needs a growth forecast and returns a price, so every argument
happens inside the forecast, invisibly. Run backwards from `EV = FCF/(r − g)`,
it needs no forecast and returns a statement about the price: *the market is
paying for this much growth, forever.* One assumption rather than three — no
horizon, no terminal rate, no fade — and the arithmetic is simple enough to
check mentally, which matters: a model whose output cannot be checked is a
model that gets believed. Its limitation is stated in the code: constant
growth forever understates what a genuinely fast-growing company can justify,
so a high implied growth is evidence the price needs defending, not proof that
it is wrong.

*Rejected: a multi-stage DCF.* More realistic and less checkable. The extra
realism lives entirely in assumptions a reader cannot see, which is the thing
this ADR is about.

## Consequences

**Debt fails closed, and this is the load-bearing rule.** Debt is tagged a
dozen ways in XBRL. Treating an absent tag as zero would understate enterprise
value, overstate every yield on it, and sort the **most levered companies in
the universe to the top of a cheapness ranking** — the same inversion negative
earnings produce, by a different door. Measured over 40 corpus securities on
2026-11-17: operating cash flow resolves for 39, cash for 39, capital
expenditure for 29, a long-term debt tag for 26. So a quarter of the sample
would have silently become debt-free. Enterprise value is `UNRESOLVED` without
a positively established debt tag, and where only a current portion resolves
the answer carries a note saying so.

Cash is treated the opposite way on purpose: a missing cash tag *overstates*
enterprise value and *understates* the yield, which cannot make a company look
cheaper than it is, so it passes through as zero with the absence recorded.

**A read rule discovered by getting it wrong.** The first version summed four
stand-alone quarters of operating cash flow, the way earnings are summed, and
resolved for **no security at all**. Cash-flow statements are tagged
**cumulatively** — year-to-date at every filing, so `duration_qtrs` runs
1, 2, 3, 4 once each per year and stand-alone quarters exist only for fiscal
Q1. Measured on corpus security 1: `NetIncomeLoss` carries 138 stand-alone
quarterly rows, `NetCashProvidedByUsedInOperatingActivities` carries 28.
Summing four consecutive *rows* of that spans four *years*. The span check in
`trailing_twelve_months` caught it and returned nothing — a guard written for
a missing quarter caught a different bug than it was aimed at, which is the
argument for keeping such guards even when they look redundant. Both legs are
now read as the fiscal year, and named that way rather than called trailing
twelve months.

**What is still owed.** Sector-relative comparison, which the roadmap asks for
and this does not ship: it needs the sector population resolved as of the
session, a corpus-wide read rather than a per-security one. The panel says so
in its own caveats rather than letting a two-way comparison pass for a
three-way one.

**If a verdict is ever wanted anyway**, this ADR is the thing to reverse, and
the conditions are specific: it would need to be per horizon, to carry its
reasoning inline, to state that it has no trading authority, and to rest on a
fundamental signal that has actually passed on this corpus. None of those
holds today.
