# The forward record — the owner's decision of 2026-10-20, and why it is the stronger position

**Decided: no CRSP enquiry, and no further money for data.** Research turns to
evidence collected going forward instead.

This document records the reasoning, because the decision looks like a
compromise and is not one.

## Why forward evidence is worth more per observation

**The 144 trials were spent because one fixed history was interrogated
repeatedly.** Every look at fixed data costs ledger budget, because repeated
looks at the same data manufacture false positives — which is exactly why the
hurdle has risen to 2.6563 and why §52 and §53 both died.

**A future that has not happened cannot be data-mined.** A forward observation
carries no multiple-comparisons cost for the period it covers, so it is worth
more than a historical one even though there will be fewer of them.

And the purchase would have made this *worse*. More history is more surface to
search, which raises the hurdle for everything measured on it. The costing of
2026-10-19 had already found that no purchasable product solved the real
constraint; this adds that the purchase would have carried a statistical cost
as well as a financial one.

## Why survivorship cannot go wrong forward

The corpus problem that consumed months exists because a *historical* universe
must be reconstructed, and the companies that failed are the hardest to find —
`EDGAR_DELISTING_DENOMINATOR.md` §7be records the denominator as blind before
roughly 2002, and gate coverage of dated exits is 25.4% for 1998.

**Forward, the universe is written down before anyone knows which members will
fail.** A delisting becomes an *observation* — a symbol we asked about on a
dated day and got nothing for — rather than an inference from an absence that
might equally be a gap in a vendor's coverage.

That is the entire design of `scripts/forward_capture.py`, and the acquisition
framework already supported it: `FetchStatus` separates `EMPTY` from
`REJECTED`, and `SymbolStatus` separates `NOT_FOUND` from
`UNAVAILABLE_HISTORICALLY` and from `UNKNOWN`. **Every outcome is written,
including the silent ones.** Demonstrated against the live feed: a nonexistent
symbol is recorded with its status, not skipped.

## The caveat, stated rather than buried

**Forward collection does not make small effects detectable faster.** Roughly
two independent blocks accumulate per year, so confirming a 0.5%-per-trade edge
still takes many years whichever direction the data comes from. The power
analysis's constraint is unchanged: effects of 0.1–0.8% sit below what block
variance of 1.0–2.6% can resolve.

**But thatchanges what is worth trading, usefully.** The pattern program was
hunting effects too small to ever confirm — which is why it closed. Forward
evaluation imposes a filter no amount of history provides: *only consider edges
large enough to show up within a year or two.* That is a sharper discipline
than the ledger alone.

## What was measured, not assumed

**Tiingo is the forward price source, and that was determined rather than
preferred.** `EodhdAcquisition` is a deliberate stub — `has_credential()`
returns False and `fetch` returns `REJECTED` — so the EODHD acquisition path
does not exist whatever key is set. `TiingoAcquisition` is fully implemented
and declares `AdjustmentPolicyDeclaration.RAW_UNADJUSTED`, which is what
ADR-0005 requires: raw exchange prints, so no future split is baked into a past
price.

Verified against the live API on 2026-10-20: credential present, status `ok`,
raw-unadjusted rows returned, and a nonexistent symbol recorded as silent.

**No key is ever printed, logged or stored.** The adapter redacts URLs and error
text before they reach an outcome, so the stored note for a failed request
carries the endpoint without its token — checked, not assumed. `.env` is
git-ignored and nothing has been committed.

## Moved into `src/` with tests, 2026-10-20

The capture now lives in `tradeit.forward.capture`; the script is a driver that
parses arguments and calls it. The move was not tidying.

**The behaviour that matters is invisible when it breaks.** If a change made
the capture skip silent symbols — a plausible *"we only need the rows that have
data"* — the record would lose exactly the observations that make it
survivorship-honest, with no error, no failing test and no visible change until
somebody tried to measure a delisting years later. That is the same defect the
historical corpus has, which took months to characterise and cannot be fixed.
Here it is one line away and silent.

So it has a test, and the mutant proves it: **skipping silences fails five
tests.** In a script it would have failed none.

The source is a **stub** in those tests, deliberately — a live feed cannot be
made to delist a company on demand, and a capture whose silent path is
exercised only in production has no test at all.

Six mutants run, six caught: skipping silences, running without a credential,
reading the clock per row instead of taking one instant, capturing metadata
requests as price observations, dropping idempotency so a retry doubles the
record, and withholding outcomes from the journal.

**`ForwardObservation` is an ORM table with no migration.** The forward corpus
is a separate database built from the ORM, as the decision record is; the
numbered migrations are research-01's chain and do not run against it. The
schema drift guard agrees — it compares what that chain *creates*, and this
table is not part of it.

**Asking twice is two observations, not a duplicate.** `asked_at` is part of
the key, because a vendor may revise and *"we asked Monday and got nothing, we
asked Friday and got a price"* is two facts. A retry at the same instant is
idempotent; a later ask adds a row.

**No scheduled job has been created.** That is standing configuration on the
owner's machine and is his to approve.

## The clock that now matters

Track A is effectively complete. **The binding constraint is no longer
engineering effort — it is elapsed time**, because a day not captured is a day
of out-of-sample evidence permanently lost. That is the one deadline that
cannot be shortened by working faster, and it is the argument for starting the
capture now rather than when a strategy is ready.
