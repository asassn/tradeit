# ADR-0037: The interface is designed before its pages

**Status:** accepted
**Date:** 2026-11-17
**Approved by:** the owner, 2026-11-17, who stopped page-at-a-time development
and asked for the whole product to be designed first.
**Builds on** [ADR-0035](0035-the-dashboard-shows-what-the-record-holds.md), whose
constraints are unchanged and are now enforced by components rather than by
comments, and [ADR-0036](0036-valuation-is-a-panel-not-a-verdict.md), whose
no-verdict rule is now asserted by a test over the whole front-end source.

## Context

The read side was built page-first. `src/tradeit/api/page.py` was a single
hand-written HTML string that grew a valuation screen, then a search box, then
four tabs. Every number on it was correct and it looked like a database
browser, because that is what it was: it asked the reader to type a numeric
`security_id` — the screen demanding the answer it was supposed to give — and
printed `4730362129360` where a person reads `$4.73T`.

The owner's objection was structural rather than cosmetic: *"This is not the
proper method to develop a UI… We need to design the whole website first and
then further develop individual pages."* That is correct, and the measurement
that followed proved it was more correct than expected.

**An eight-area product map was drafted, then the databases were measured, and
most of it would have been empty screens.** Roughly thirty tables in this
schema are declared and hold **zero rows in every database**: portfolios,
positions, trades, risk snapshots, portfolio snapshots, patterns, pattern
observations, breakout events, watchlists, screen rejections, indicator
values, market regime states, breadth, relative strength, sector strength,
volatility regimes, strategies, strategy configurations, data quality issues,
job runs, system logs, instruments, symbol mappings. A Portfolios area, a
Technicals tab, a Screener and a Strategy Builder would all have been
furniture over nothing.

## Decision

### 1. The information architecture follows the data, not the code layout

Four areas that have substance, and one that honestly reports what does not:

| Area | Source | Holds |
|---|---|---|
| **Securities** | `research01` | 41,698 securities · 83.8M price rows · 92M fundamental facts · 20,124 ticker intervals |
| **Evidence** | the records | 53 runs · 17,392 trades · 2 Monte Carlo runs |
| **Audit** | the decision record | 1,956 scores · 2,574 orders · 2,532 executions · 553 journal entries |
| **Not yet produced** | — | every unpopulated capability, naming what it needs |

**"Not yet produced" is a feature, not an apology.** Each card states what the
capability *is* — "Breakouts: thirteen states, four scores per event, 9,746
lines" — and **what it needs before it can have a screen**. This is the
`UNRESOLVED` discipline of the data layer applied to the product surface, and
it is the opposite of a demo filled with sample data.

### 2. Absence is primary content, and gets components of its own

The most important finding from reading the whole codebase: **its richest
content is not results, it is reasons.** Almost every dataclass carries an
absence field beside its value field — `missing`, `unresolved`, `caveats`,
`refused`, `skipped`, `blocked`, `remedy`, `minimum_detectable_shortfall`,
`is_our_defect` — and several are explicitly ordered *before* the results they
accompany. The validation harness reports blocked checks above passed ones.
`reconstruct` returns `missing` beside the chain.

A UI that renders these as error states or footnotes inverts the system's
design intent. So they are components with the same visual weight as a result:
`<Unresolved>`, `<MissingRecord>`, `<NotYetProduced>`, `<Caution>`,
`<Provenance>`.

### 3. The invariants move from comments into tested code

| Enforced | How |
|---|---|
| The read side cannot write | a test greps the whole source for any `POST`/`PUT`/`PATCH`/`DELETE`; there is no component that can submit |
| No verdict is rendered | a test greps for `undervalued`/`overvalued`/`fairly valued` outside comments (ADR-0036) |
| Colour follows the theme | a test forbids a raw hex outside `tokens.css`; dark mode is *selected*, so a stray hex is a bug visible in one theme only |
| A non-zero score never renders as zero | `<Figure kind="score">`, with a test over values down to 1.6e-06 |
| `missing` renders above results | `<MissingRecord>` raises `role="alert"` only when something is missing |

All five were mutation-tested on 2026-11-17: a deliberate violation of each was
introduced and every one was caught.

### 4. React, TypeScript and Vite; the bundle is committed

*Rejected: continuing with hand-written HTML in a Python string.* It is what
produced the problem. A component system across thirty views written by hand is
unmaintainable, and nothing about it would look like a product.

The bundle builds into `src/tradeit/api/static/` and **is committed**, so
`pip install` alone still yields a working dashboard: a developer needs Node, an
operator does not. The hand-written `PAGE` is kept as the fallback for a
checkout that has not been built, which is what makes the toolchain genuinely
optional rather than nominally so.

**A hash router**, deliberately: the bundle is served from a static mount with
no catch-all rewrite, so a browser-history route would 404 on a hard refresh or
a pasted link. A hash route needs no server cooperation, which keeps the
deployment story "FastAPI serves a directory".

### 5. The colour palette is validated by script, not by eye

The categorical, status, sequential and diverging values are taken from the
validated reference palette of the `dataviz` method and were **re-validated
against these surfaces on 2026-11-17** — light and dark both pass every hard
gate. The light run returns a contrast WARN on three slots (aqua 2.74, yellow
2.11, magenta 2.62 against `#fcfcfb`), which is **a binding obligation**: any
chart using them ships visible direct labels or a table view.

The chrome — surfaces, ink, the single accent — is ours and is deliberately
restrained. In this class of product the expensive look comes from type, space
and alignment, not from colour.

**Every number uses tabular figures**, globally. A ragged column of digits
cannot be scanned down, which is the entire reason the column exists.

## Consequences

**Two bugs found by building it.** A static mount matches greedily, so mounting
it before the JSON routes would have `/runs` serve a 404 out of the asset
directory — it is mounted last and a test asserts the ordering. And the entry
document must be served `no-store`: Vite hashes asset filenames and
`emptyOutDir` deletes the previous build, so a cached `index.html` asks for a
bundle that no longer exists and renders **nothing at all**, with the server
healthy and every route answering. Observed directly, then fixed and tested.

**Source maps are not shipped.** The map is 877 kB against a 237 kB bundle and
the output is committed; every rebuild would add close to a megabyte of churn.
`npm run dev` has full maps.

**CI gains a second job** — typecheck, lint, test, build — which does *not*
gate on the committed bundle being current. The build proves the source
compiles; the commit proves what ships, and a staleness check would be a second
source of truth for the same question.

**What this does not do.** The Securities view is one tab of the five planned
(Overview, Fundamentals, Filings, Provenance are owed), and the backend needs
three changes before the rest can be built: the app must open four databases
rather than two (the forward record, the only live-updating data in the system,
still has no HTTP surface); `instrument_id` and `security_id` must stay
unbridged in the UI as they are in the code; and anything that *lists*
securities needs a read model, because a single valuation takes ~3.5 s against
a 73 GB corpus.
