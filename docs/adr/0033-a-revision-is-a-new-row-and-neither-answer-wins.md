# ADR-0033: A revision is a new row, and neither answer wins

**Status:** accepted
**Date:** 2026-11-15
**Supersedes:** nothing. **Amends:** nothing. **Conforms to**
[ADR-0005](0005-raw-prices-with-read-time-adjustment.md), which settled this in
Phase 1; this records that the forward capture was not using it, and makes it.

## Context

`ELME_VENUE_BREAK_2026-11-15.md` §3 found three of sixteen ELME bars revised by
EODHD after we captured them — one by 53% on volume. It recorded the gap
plainly: we keep the first-seen values, which is right bitemporally, *"and
nothing in the record says so. The divergence is only visible by re-asking,
which nothing does."*

`_write_bars` carried the reason:

> Bars already held for the same session are **left alone rather than updated**.
> A later ask that disagrees should add a revision, not overwrite one — but
> writing that revision needs a decision about which vendor wins, and this does
> not invent one.

That was the right instinct and the wrong conclusion. **The decision it was
waiting for had already been taken.** `OhlcvBar`'s unique key has been
`(instrument_id, timeframe, session_date, knowledge_time)` since
`0001_initial`; its docstring reads *"Append-only; revisions are new rows"*; and
`BarRepository.history` has always resolved them with `row_number() over
(partition by session_date order by knowledge_time desc)` bounded by
`knowledge_time <= clock.as_of`, documented as *"Bars up to the clock, newest
revision of each session, oldest first."* The research-01 corpus holds 327,924
multi-revision keys and `RESEARCH_01_DATA_DICTIONARY.md` §4 tells every reader to
filter on `knowledge_time`.

The forward capture simply skipped any session it already held. Measured
2026-11-15: **every one of the 4,327 bars in the forward record held exactly one
`knowledge_time`.** The revision path was latent, not working — and latent code
is the shape this project treats as most dangerous, because the first time it
runs is in production.

A side table of disagreements was the obvious alternative and is worse: it would
duplicate a mechanism the schema already provides, and no existing reader would
consult it.

## Decision

**Write a revision as an additional `OhlcvBar` row carrying its own
`knowledge_time`. Never modify a row. Declare no vendor authoritative.**

Five rules, each with a test shown to fail without it:

1. **A later ask with different numbers appends.** Both answers are kept; the
   earlier one is never touched.
2. **Point-in-time reads give first-seen for free.** A backtest clock set before
   a revision cannot see it, so it reads the price that was knowable on the day;
   a reader asking today gets the vendor's current answer. This is what the
   never-overwrite rule was protecting by hand, and the bounded read does it
   better — because it is right at *every* instant, not just the first.
3. **Identical numbers are not a revision.** The capture re-asks a seven-session
   window nightly, so writing a row per ask would add roughly 1,350 rows a night
   carrying no information and bury the ones that do.
4. **Compared as `Decimal`, not text.** `0.05` and `0.050000` are one number.
   The database returns a fixed scale and the vendor does not; comparing text
   would report a revision on every bar whose trailing zeros the vendor dropped,
   and a detector that fires on everything is read as firing on nothing.
5. **The report names the field that moved.** ELME's 2026-11-05 bar moved 53% on
   volume while its close moved a tenth of a cent. "1 bar revised" would have
   hidden the fact worth seeing.

### Which vendor wins: nobody, and that is the answer

The question `_write_bars` deferred assumed a choice had to be made. It does
not. `knowledge_time` makes both answers true statements about different
instants, so the record holds the disagreement instead of resolving it, and each
reader resolves it against its own clock. **No vendor is ranked, no price is
preferred, and nothing is discarded.**

What this does *not* license: pooling two vendors' bars into one series. Tiingo
declares `RAW_UNADJUSTED` and EODHD declares `UNKNOWN`, and the one-vendor-per-
security split in `ops/forward_daily.py` stands untouched. A revision here is the
*same* vendor changing its mind, which is the only case the forward universe can
produce.

### Two answers cannot share one `knowledge_time`

They are unrepresentable — `knowledge_time` is part of the unique key — so a
replay at the same instant whose numbers have moved keeps the first answer and
**reports the contradiction** rather than dropping it. Silently discarding
evidence about a vendor is the one thing this record exists not to do.

## Consequences

**A bug this would have introduced, caught before shipping.** The discontinuity
scan of [ADR-0032](0032-a-move-too-large-to-be-trading-is-reported-not-judged.md)
reads bars and pairs consecutive ones. A revised session holds two rows, so a
flat read would have handed it two bars for one date and reported the revision as
a price move — a confident wrong answer, which this project treats as worse than
no answer. `scan_record` now resolves to the newest revision per session, and
`find_discontinuities` additionally skips any pair sharing a session date, so a
future caller reading the table flat cannot reproduce the fault. Defence in
depth, both tested.

**The nightly output gains a line** when a vendor revises, naming the session and
the fields. No new command and no new table.

**Every existing reader is already correct.** `BarRepository.history` resolved
revisions before this change; nothing had produced one for it to resolve.

**What stays open.** §2 of the ELME record — a vendor re-mapping a ticker with no
price move — leaves no trace in a close series and none in a revision either,
because the bars arrive under a symbol we no longer ask about. That is an
identity question, not a price question, and it is not answered here.

## Alternatives rejected

| | why not |
|---|---|
| **A side table of disagreements** | Duplicates a mechanism the schema already provides, and no existing reader would consult it. |
| **Overwrite with the newest answer** | Destroys what was knowable on the day, which is the look-ahead leak ADR-0002 and ADR-0005 exist to prevent. |
| **Keep skipping, and re-ask on demand to compare** | Leaves the record diverging from the vendor with nothing saying so — the exact state §3 flagged. |
| **Rank the vendors** | Asserts a quality ordering no measurement here supports, and the forward split means two vendors never cover one security anyway. |
| **Write a row per ask regardless of content** | ~1,350 rows a night of nothing, burying the revisions that matter. |
| **Flag a revision as a data-quality issue** | Labels a legitimate vendor restatement a defect and pollutes the table that tracks real ones. |
