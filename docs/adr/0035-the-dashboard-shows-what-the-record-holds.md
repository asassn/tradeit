# ADR-0035: The dashboard shows what the record holds, including where it holds nothing

**Status:** accepted
**Date:** 2026-11-16
**Applies** [ADR-0025](0025-breakout-state-is-not-trade-eligibility.md) and the
Phase 10 constraints in `ROADMAP.md` to the read side's first screen.
**Context for** [ADR-0027](0027-score-3-ranks-capital.md), whose ranking this
screen displays and must not re-aggregate.

## Context

Phase 10 asks for a *"dashboard / research interface"*. `src/tradeit/api/`
existed and served JSON; **nothing could open it.** `create_app` requires a
record DSN, `RUNBOOK.md` documented
`uvicorn --factory "tradeit.api:create_app"` and then said correctly that
*"a wrapper is needed to pass one"*, and the wrapper was never written — so the
documented start command raised `TypeError` before binding a port. Measured
2026-11-16, `tradeit.api` was imported by its own test and by two documents and
by **nothing else**: the shape the Phase 7 audit named, *"built, tested and
standing beside the decision rather than in it"*.

Pointing a screen at a real record found three things in the first hour, none
of which any test had failed on, because nothing had ever read these values:

1. **`backtest_runs.trade_count` was never written.** The column is
   `NOT NULL DEFAULT 0`; `record_backtest_result` copied nine metric fields and
   omitted this one, so the omission was silent. Every run in all three
   decision records on this machine claims **zero trades** beside real
   `backtest_trades` rows — 1,402, 106 and 17,392. `exposure_pct` and
   `benchmark_return_pct` were dropped the same way.
2. **No route returned an `instrument_id` or a `session_date`**, while every
   per-security and per-session route keyed on one. The only way into the
   service was a direct SQL query against the record it exists to stand in
   front of.
3. **The record holds no `symbol_mappings` rows at all**, so no instrument in
   it can be labelled with a ticker.

A screen is where a wrong number stops being a column in a database and starts
being something a person acts on. That is the whole reason to build one, and
the whole reason it has to be careful.

## Decision

**Four rules, each enforced in code rather than left to whoever edits the
page.**

### 1. A number the record does not support is not displayed

Where two sources could answer, the one that is *measured* wins and the
disagreement is **named rather than silently repaired**. `/runs` reports
`trades_recorded` (counted from the trade rows) beside `trade_count_column`
(what the run claims) and sets `trade_count_disagrees` when they differ.

*Rejected: substitute the counted value and show one number.* It is the
smaller diff and it repairs only the display. The record is still wrong on
disk, and the next reader to query the table directly — a script, a notebook,
the assistant this phase still owes — would get the old answer with nothing to
warn them. A dashboard that makes a defect invisible is worse than one that
shows it, because it removes the only pressure to fix it.

`statistically_meaningful` is likewise computed from the counted rows, or a
repaired record would go on reading *"not enough trades to interpret"* forever.

### 2. Identity fails closed, on a display field as everywhere else

An instrument with no `symbol_mappings` row is shown as its id with the note
*"the ticker is unrecorded, not absent"*. An instrument with **more than one**
mapping is shown the same way: it resolves to neither.

*Rejected: take the most recent mapping.* A security whose ticker changed, or
whose ticker was reused, has several rows. Labelling a 2004 score with a ticker
the security only carried in 2019 is precisely the splice the identity
architecture exists to prevent, and a chart is where somebody would believe it.
Interval-correct resolution needs the session date of the row being labelled,
which a per-instrument helper cannot answer; until a route needs it, ambiguity
is reported.

*Rejected: return `null` and let the page render a blank.* A blank reads as
*this security has no ticker*. Only *nobody wrote one down* is ever true here.

### 3. `missing` renders before results, never after

A session with empty lists and no `missing` is a quiet session. A session with
empty lists **and** `missing` is one nobody recorded. The second is the more
dangerous reading, so it is placed above the lists and in its own colour: a
reader scanning zero scores will otherwise conclude the system did nothing that
day rather than that the record is silent about it.

For the same reason `/sessions` lists dates that produced scores and says
explicitly that presence there is **not** a claim the session can be walked end
to end. Folding the end-to-end check into the menu would let an incomplete
session vanish from the list rather than be found and shown as incomplete —
removing exactly what the service exists to surface.

### 4. No aggregate across horizons, and no control that can trade

The panel renders Day, Swing and Retirement as separate columns with no
arithmetic taking more than one as input, and absent horizons print their
stated reason instead of an empty cell. There is no form, no button that
POSTs, and no route to receive one. *"A dashboard that can place an order is a
trading system with a chart attached."*

### Implementation: one served string of HTML

No build step, no package manager, no CDN; the page is served from the same
process as the data.

*Rejected: a front-end application.* It is a second toolchain to keep alive for
a page that renders five tables, it adds a dependency surface to a repository
whose research path is deliberately installable without even a web framework,
and it can drift from the routes it reads. The page as written works on a
machine with no network.

## Consequences

`tradeit dashboard --record <path>` starts the service. `--record` is required
and has no default, inherited from `create_app`'s reasoning — the record and
the corpus are different databases and there is more than one record. A path
that does not exist is **refused before the server starts**, because SQLite
creates an empty database on connect and a mistyped path would otherwise start
a service that answers every route with nothing, reading exactly like a record
in which nothing happened.

**A defect this ADR's own screen introduced, kept here because the class
matters more than the instance.** The first draft formatted scores with two
decimals, and **77% of the 1,956 recorded scores rendered as `0.00`** — the
real values run from 1.6e-06 to 1.0. A reader would have concluded the scorer
produced nothing, and the page would have been the evidence. The format now
switches to exponential below the point where fixed notation would swallow a
value, and an exact zero prints as a bare `0` so it cannot be confused with a
small number that was rounded. The lesson is the one this project already
applies to parsers: **the dangerous failure is not the crash, it is the
confident wrong number**, and a screen is the most confident surface in the
system.

**What this does not do.** The valuation callout, live news and attention, and
the onboard assistant are the remaining Phase 10 capabilities and none is
started. The callout is *"never shipped without the reasoning that produced
it"* and the reasoning does not exist yet; news is separately gated on a source
with honest publication timestamps; and the assistant is only as honest as this
record, which is one backtest and 18 forward sessions deep.
