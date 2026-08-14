# Phase 6 — point-in-time fundamentals: architecture and acquisition plan

**Status: design for approval. Nothing here is implemented.** No profitability
optimisation, forward-return labelling, threshold tuning or strategy selection
is proposed, designed or begun.

---

## 1. What already exists, and the gap

The dataset *contracts* were written during the empirical gate and are live in
`DATASET_SPECS`. The **tables are not**:

| dataset | contract | table |
|---|---|---|
| `fundamentals` | declared — `instrument_id, metric, fiscal_period, fiscal_year, period_end` + `value, unit, filing_timestamp, is_restatement, restates_period_end, accession` | **absent** |
| `filings` | declared — `instrument_id, form_type, filed_at` + `accession, period_end, accepted_at` | **absent** |
| `delistings` | declared — `instrument_id, delisted_date` + `reason` | **absent** |
| `universe_membership` | declared — `universe, instrument_id, valid_from` + `valid_to, exit_reason` | **absent** |

So Phase 6's first work is schema, not acquisition: the importer can already
*read* these files and the gate already knows to BLOCK without them, but there
is nowhere to put the rows.

The contracts are also already correct on the point that matters — they require
`filed_at` on a filing and offer `filing_timestamp`, `is_restatement` and
`restates_period_end` on a fact. The design below builds on them rather than
replacing them.

## 2. Architecture

### 2.1 Two layers, and why the split is load-bearing

**Raw facts (immutable, append-only).** Exactly what the vendor said, with the
vendor's own identifiers, never updated in place. A restatement is a *new row*,
never an edit. This is the layer a dispute is settled against.

**Derived normalisation (recomputable, versioned).** Metric names mapped onto a
canonical vocabulary, units harmonised, per-share figures split-adjusted,
ratios computed. Every derived row carries the `derivation_version` that
produced it, so a change to normalisation is visible rather than retroactive.

The split is what makes "we changed how we compute book value" a re-derivation
rather than a data loss, and it is the same discipline `patterns` already
follows with its mutable current-state row over an append-only observation log.

### 2.2 The point-in-time rule, stated once

Every fundamental fact carries **three** dates, and conflating any two of them
is the classic lookahead bug:

| field | meaning |
|---|---|
| `period_end` | the fiscal period the fact describes |
| `filed_at` | when the filing containing it was submitted |
| `knowledge_time` | when this platform may first use it |

`knowledge_time` is derived from `filed_at` (plus the existing
`KnowledgeTimePolicy` publication lag), **never** from `period_end`. A quarter
ending 31 March was not knowable on 31 March; the existing importer already
quarantines fundamentals with no filing timestamp rather than guessing, and that
behaviour stays.

Reads go through the same as-of clock every other read in the platform uses:
`WHERE knowledge_time <= :as_of`, latest row per `(instrument, metric,
period_end)`. That single query shape gives restatement handling for free —
the "latest as of then" is the value the platform believed then, and a
restatement filed later simply does not exist yet at that as-of.

### 2.3 Tables

```
filings                     one row per submission — the causal anchor
  instrument_id, accession (unique), form_type,
  period_end, filed_at, accepted_at, knowledge_time, source

fundamental_facts           immutable, append-only, one row per reported number
  instrument_id, metric, fiscal_period, fiscal_year, period_end,
  value, unit, filing_id -> filings, accession,
  is_restatement, restates_period_end,
  knowledge_time, knowledge_source, ingested_at, source
  UNIQUE (instrument_id, metric, period_end, fiscal_period, accession)

fundamental_values          derived, recomputable, versioned
  instrument_id, metric, period_end, value, unit,
  derivation_version, source_fact_id -> fundamental_facts,
  knowledge_time
  UNIQUE (instrument_id, metric, period_end, derivation_version)

delistings                  instrument_id, delisted_date, reason, knowledge_time
universe_membership         universe, instrument_id, valid_from, valid_to, exit_reason
```

`fundamental_facts` keys on `accession` so a restatement of the same period is a
distinct row rather than a conflict — that uniqueness choice is the whole
restatement design.

### 2.4 Survivorship-safe universe reads

`universe_membership` with `valid_from`/`valid_to` is what makes "the S&P 500 on
2008-06-30" answerable without hindsight. Combined with `delistings`, a
point-in-time universe read is:

> members where `valid_from <= as_of < coalesce(valid_to, ∞)`, joined to
> instruments not yet delisted as of `as_of`.

Without both tables, any cross-sectional statistic silently ranks over survivors.
This is the concrete mechanism behind §4.

### 2.5 Vendor provenance and capability reporting

Unchanged from the existing package model, and it already carries what is
needed: `data_packages` records provenance and digests, `data_package_files`
records which datasets actually imported, and `CapabilityIndex` records
per-instrument what the acquisition could and could not obtain — including the
distinction between "no rows because nothing happened" and "no rows because the
vendor returned 402". Phase 6 adds `fundamentals` and `filings` to the
capability vocabulary; it does not need a new mechanism.

### 2.6 What Phase 6 must *not* contain

No scoring, ranking, screening or eligibility. Phase 6 makes point-in-time
fundamentals *available and trustworthy*. Deciding what to do with them is a
later phase, and putting a score here would repeat the conflation the phase
order exists to prevent.

## 3. Vendors and datasets

Constraints already in force: **FMP must not be used** for fundamentals,
ratios, earnings, estimates, statements, OHLCV, insider or institutional data.
Prices come from the existing Tiingo path.

| option | point-in-time? | delisted coverage | notes |
|---|---|---|---|
| **SEC EDGAR Financial Statement Data Sets** | **yes** — quarterly ZIPs keyed by `adsh` with a `filed` date | complete for filers | Free, authoritative, and the natural **raw layer**. Costs normalisation work: XBRL tags, not a curated metric vocabulary. Does not cover pre-2009 well. |
| **Sharadar Core US Equities (SF1)** via Nasdaq Data Link | **yes** — `datekey` (filing) distinct from `calendardate` (fiscal period), explicit `ARQ/ART/MRQ` dimensions | **includes delisted tickers**; companion `ACTIONS`/`TICKERS` tables carry delisting events | The strongest fit for both Phase 6 *and* the survivorship problem in one purchase. Curated metric vocabulary, so far less normalisation. |
| **Compustat Point-in-Time** (S&P) | yes, the reference implementation | complete | Institutional pricing and licensing; almost certainly out of proportion here. |
| **Tiingo fundamentals** | partial — statement data with as-of dates | weaker on delisted | Already an authenticated provider, so the cheapest integration; coverage needs verification before relying on it. |

> **Superseded by `docs/PHASE_06_VENDOR_MATRIX.md` (Milestone 1 / 1C).** Two
> corrections, in order of importance:
>
> 1. **Sharadar is eliminated, on licence rather than capability.** The current
>    Sharadar Personal Use License requires, on termination, deleting all copies
>    of Services Data *and datasets derived from it* within 30 days. `research-01`
>    and every scan run over it are derived datasets, so the one-time-download
>    model is prohibited. **EODHD is eliminated for the same reason** (one-month
>    deletion). The table below is retained as a record of the capability
>    assessment; the recommendation it led to no longer stands.
> 2. The cross-check design below assumed the SEC *Financial Statement Data Sets*
>    could verify filing dates. They start in 2009 Q1 and cannot. The EDGAR
>    *full-index* runs from 1994 Q3 and carries the filing date, so it is the
>    cross-check across the whole span; the Financial Statement Data Sets remain a
>    value-level check from 2009.

**Current recommendation, replacing the one below:** prices 1998+ from **Kibot**
(the only candidate whose licence permits permanent retention — *pending the data
probe in `KIBOT_DATA_PROBE.md`*), filing metadata from **EDGAR full-index 1994
Q3+**, fundamental values from **EDGAR XBRL/FSDS 2009+**, and pre-2009
fundamental *values* deferred rather than sourced from a retention-encumbered
vendor.

The reasoning that pointed at Sharadar remains sound and is worth keeping: an
as-reported/restated split with a real filing date, alongside delisted coverage,
would have closed the fundamentals BLOCK and the survivorship FAIL in one
acquisition. The licence is what makes it unavailable, and EDGAR — free, public
domain, and with no termination clause to fail — now serves both as the
fundamentals source from 2009 and as the independent instrument that measures a
price vendor's delisted coverage.

**On cost and coverage I am explicitly uncertain.** I have not verified current
pricing, licensing terms or coverage start dates, and I will not guess at them
in a document you may act on. Verifying those is part of Milestone 1 below.

## 4. The survivorship FAIL — recommendation

You offered A (separate survivorship package from ≤2002-12-31), B (augment the
existing snapshot), C (keep `full-01` as machinery-only, build research universe
later).

**Recommended: C as the framing, A as the build. Explicitly not B.**

**Why not B.** A data package is a provenance unit: one manifest, one digest set,
one declared adjustment policy, one `data_snapshot_digest` that everything
derived from it carries. Mixing a second vendor's delisted history into
`twelve_data-daily-e3ddc03209bb25b4` would make that digest span two vendors with
different adjustment conventions and different coverage rules, and every pattern
already derived from it would silently come to mean something else. The
package model is doing its job by making this awkward.

**Why C is the right framing.** `full-01` has already served its purpose and the
gate says so: it validated the machinery and found seven defects. It is a
*machinery-validation corpus* and should be frozen as one — kept, cited for that,
and never used for a cross-sectional claim.

**Why A is the right build.** A new package, from a vendor with genuine delisted
coverage, is the only thing that makes cross-sectional work honest. On the start
date: your ≤2002-12-31 is a sound floor because it includes the 2000–2002
bear market. I would argue for **1998-01-01 or earlier** if cost permits, so the
corpus spans two full bear markets (2000–02 and 2007–09) rather than one and a
half — a universe that has only ever seen one regime teaches one regime. That is
a cost question, not a design one.

**Naming, so the two can never be confused:** `full-01` stays; the new one is
`research-01` and is the only corpus a cross-sectional statistic may cite.

## 5. Milestones

| # | milestone | output | gate |
|---|---|---|---|
| **1** | **Vendor verification** — confirm Sharadar SF1's point-in-time fields, delisted coverage, earliest date, licence and price; same for EDGAR bulk data | a written comparison with real numbers, not estimates | **your approval before any purchase** |
| 2 | Schema — `filings`, `fundamental_facts`, `fundamental_values`, `delistings`, `universe_membership` + migration | tables, constraints, tests | migration/ORM drift green |
| 3 | Importer + point-in-time policy for the new datasets | `tradeit data import` accepts them; quarantine on missing `filed_at` | round-trip tests |
| 4 | `research-01` acquisition and import | a survivorship-safe package | coverage report |
| 5 | Re-run the gate against `research-01` | `data.survivorship_coverage` PASS, fundamentals BLOCK cleared | **a clean gate** |

**Milestone 1 is the next action and it needs no code.** Everything after it
depends on facts I do not currently have, and designing Milestone 2's schema
against a guessed vendor contract is how a schema acquires a column nobody can
fill.

## 6. What I am waiting on

1. **Approval of the architecture in §2** — particularly the raw/derived split
   and the three-date rule.
2. **Approval of the vendor recommendation in §3**, or a different vendor.
3. **A decision on the `research-01` start date** — 2002-12-31 as you proposed,
   or earlier for a second bear market.
4. **Confirmation that `full-01` is frozen** as a machinery-validation corpus.

Phase 6 implementation does not begin until you say so.
