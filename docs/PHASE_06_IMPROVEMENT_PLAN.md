# Phase 6 improvement plan — the permanent historical research corpus

**Data architecture and source verification only.** Nothing purchased, nothing
implemented, no thresholds touched, no performance computed. `full-01` frozen
and untouched.

Companion documents: [`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) ·
[`RESEARCH_01_DATA_CONTRACT.md`](RESEARCH_01_DATA_CONTRACT.md) ·
[`FORWARD_SURVIVORSHIP_SYSTEM.md`](FORWARD_SURVIVORSHIP_SYSTEM.md) ·
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md)

---

## 1. The two blockers, stated first

**Neither is a design problem. Both must be resolved before money is spent.**

1. **Retention rights are unresolved.** Whether "subscribe one month → download
   → cancel → keep and use indefinitely" is permitted could not be established
   from a primary source. The one adjacent Nasdaq-family licence that *was*
   reachable requires deletion on termination. Per your rule, **no purchase is
   recommended.** (Vendor matrix §1.)
2. **Pre-2009 point-in-time fidelity is unverified.** It decides whether
   1998-01-01 is achievable, and it is cheaply testable against free EDGAR data
   before purchase. (Vendor matrix §4.)

Everything below is ready to execute the moment those two are answered.

## 2. What was and was not verifiable here

This session's egress policy blocked every vendor site and `sec.gov`. The
proxy's own guidance is that such denials are organisation policy and must not
be routed around, so they were not. Confidence grades in the vendor matrix are
used strictly and **no secondary claim is promoted to VERIFIED**. All pricing is
UNVERIFIED.

## 3. Task 2 — what SEC EDGAR supplies, by era

The single most useful finding of this milestone: **EDGAR's filing-date coverage
does not begin with XBRL.** Two different products with two different start
dates, and conflating them is what would wrongly force a 2009 start.

| product | from | machine-readable | gives |
|---|---|---|---|
| **quarterly full-index** (`master.idx` / `form.idx`) | **1993 Q1** | yes — pipe-delimited | CIK, company name, **form type, filing date**, accession path |
| **Financial Statement Data Sets** | **2009 Q1** (first submissions 2009-04-15) | yes — `sub`/`num`/`tag`/`pre`, keyed on `adsh` | as-filed statement *values*, uncorrected |
| **submissions / companyfacts APIs** | XBRL era | yes — JSON | per-company filing history and XBRL facts |
| **filing documents** | 1993+ | **no** — HTML/text | everything else, by parsing |

### The hard three-way distinction

| | 1998–2008 | 2009– |
|---|---|---|
| **directly machine-readable** | filing dates, accessions, form types, CIK, company names — via full-index | all of the above **plus** statement values via XBRL/FSDS |
| **requires parsing filing text/HTML** | **all statement values**; delisting and merger detail inside 8-K/25/15 | 8-K narrative detail |
| **cannot be reliably reconstructed** | ticker↔CIK mapping for the era (EDGAR is CIK-centric; tickers are not authoritative in old filings); intraday or point-in-time *prices* (EDGAR has none) | prices |

**Implication.** EDGAR alone can give `research-01` an authoritative
`filed_at`/`accession`/`period_end` spine from **1993**, and *values* only from
2009. The commercial vendor's job is therefore narrower and clearer than it
first appears: **pre-2009 statement values and the whole price/lifecycle
history.** EDGAR verifies the dates the vendor claims for them.

## 4. Task 4 — raw / derived architecture

Approved shape, with the rules made exact.

```
  raw immutable vendor facts        price_facts, fundamental_facts,
        │                           corporate_action_facts, filings
        │                           append-only; never updated in place
        ▼
  normalized canonical facts        vendor metric names → canonical vocabulary,
        │                           units harmonised; carries derivation_version
        ▼
  versioned derived features        adjusted series, ratios, indicators
        │                           carries derivation_version
        ▼
  scanners / strategies / backtests  read through the as-of clock only
```

### Rules

1. **The raw layer is append-only.** No `UPDATE`, no `DELETE`. A correction is a
   new row with a later `knowledge_time`; a restatement is a new row under a new
   accession. This is the discipline `pattern_observations` already enforces and
   the reason `full-01` could answer "what did we think on 14 March?".
2. **Vendor disagreement is stored, not resolved.** Two sources, two rows, both
   with `source` and `source_version`. Resolution happens in the normalisation
   layer, is versioned, and is therefore reversible.
3. **Every derived row names its inputs**: `source_version` +
   `derivation_version` + `config_digest` + `as_of` + `data_snapshot_digest`.
   Four of those five already exist in the schema and are populated by the
   scanner.
4. **Reads go through the as-of clock, always**: `WHERE knowledge_time <=
   :as_of`. There is no second read path, and there never has been.

### "What did TradeIt know on 2000-03-10?"

Answerable by construction, with no special machinery:

```sql
-- the universe as of that date
SELECT s.instrument_id, a.ticker
FROM securities s
JOIN symbol_aliases a USING (instrument_id)
WHERE a.valid_from <= DATE '2000-03-10'
  AND (a.valid_to IS NULL OR a.valid_to > DATE '2000-03-10')
  AND (s.delisting_date IS NULL OR s.delisting_date > DATE '2000-03-10')
  AND s.knowledge_time <= TIMESTAMP '2000-03-10 23:59';

-- and each fundamental as believed then
SELECT DISTINCT ON (instrument_id, metric, period_end) value
FROM fundamental_facts
WHERE knowledge_time <= TIMESTAMP '2000-03-10 23:59'
ORDER BY instrument_id, metric, period_end, knowledge_time DESC;
```

Reproducing a *result* additionally pins `data_snapshot_digest` and the code and
config versions — the mechanism `scan_runs` already implements and
`0012_run_scoped_derivation` makes an isolation boundary rather than a label.

## 5. Task 8 — cost minimisation, and the gaps

| need | source | recurring cost |
|---|---|---|
| historical prices 1998–present, active + delisted | **commercial, one-time** | one month, *if retention is permitted* |
| historical fundamentals pre-2009 | **commercial, one-time** | same |
| historical fundamentals 2009–present | **SEC EDGAR** | **free** |
| historical filing dates / accessions 1993–present | **SEC EDGAR full-index** | **free** |
| forward daily prices | **Twelve Data** | existing subscription |
| forward filings and fundamentals | **SEC EDGAR** | **free** |
| forward corporate actions | **FMP + Twelve Data** | existing subscriptions |
| security lifecycle, forward | **EDGAR + FMP + TD + TradeIt registry** | existing |
| the accumulating security master | **TradeIt** | none |

### Gaps, stated plainly rather than hidden

1. **If retention is prohibited, the model breaks.** Not the architecture — the
   funding. It becomes a recurring subscription, or `research-01` starts at 2009
   from EDGAR alone with no pre-2009 fundamentals and no commercial delisted
   price history. That is a materially worse corpus and would not meet the
   dot-com objective.
2. **Pre-2009 statement values have no free source.** EDGAR has the filings;
   extracting values means parsing HTML/text for ~11 years across thousands of
   issuers. Feasible, expensive, and error-prone — a project in itself, not a
   fallback.
3. **Ticker↔CIK mapping before ~2009 is genuinely hard.** EDGAR is CIK-centric.
   The commercial vendor's permanent identifier is what bridges it; without one,
   linking a 1999 price series to a 1999 filing is guesswork.
4. **Delisting *reasons* are frequently absent.** Forms 25/15 say a security was
   delisted, not always why. `delisting_reason = unknown` is a first-class value
   for exactly this reason.
5. **Twelve Data fundamentals are ~5 years deep** (CORROBORATED) and cannot
   serve history. FMP is excluded from fundamentals by standing rule. So there
   is **no in-house path to pre-2009 fundamentals** without the one-time purchase.
6. **Exchange/venue history before 2009** (moves between NYSE/AMEX/Nasdaq tiers)
   is poorly covered everywhere and may end up `unknown`.

## 6. Task 6 — dot-com reconstruction

Twelve control classes, named in advance, in
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md). They are chosen so a
survivorship-biased corpus **cannot** pass: every one either disappeared, changed
identity, or had its ticker reused. Class 12 — BBBY ticker reuse — is the case
`full-01` actually hit and is not hypothetical.

## 7. Task 7 — the pre-purchase probe

**No purchase. Free tier or trial only, and if neither exists, the probe is the
first thing the one month is spent on — before any bulk download.**

### 7a. Price history — 18 securities

Drawn from the control classes: 4 survivors, 3 dot-com bankruptcies, 2
peak-acquisitions, 2 identity-changing mergers, 2 telecom/accounting failures, 2
financial-crisis failures, 1 large-split name, 1 recent IPO, and **BBBY** for
ticker reuse.

For each: can the vendor return daily bars at all; are they unadjusted; does the
series stop at delisting; is there a `delisting_date` and a *reason*; and for
BBBY, **are the two issuers separated or spliced?**

### 7b. Fundamentals and the date test — the decisive one

Sample filings at **1998, 2000, 2002, 2008, 2009, 2020 and a recent period**,
and for each sampled record compare:

```
vendor filing-date field   vs   EDGAR full-index `filed` for the same accession
```

**Acceptance rule, fixed before the data is seen:**

| observation | verdict |
|---|---|
| vendor date ≈ EDGAR `filed` (within the expected availability lag), across all eras | **point-in-time — 1998 start approved** |
| agreement 2009+, divergence before | **2009+ is PIT; pre-2009 is not** → start 2003-01-01 or re-source |
| vendor dates cluster on fiscal quarter-ends | **not point-in-time at all, whatever the vendor calls it** → reject the field |

The middle row is the one to watch for, because it is the failure that would
otherwise be invisible.

### 7c. Identity test

`permaticker`-equivalent stability across every class-5 and class-8 control;
CIK present where the issuer filed; ticker alias intervals non-overlapping.

### Probe output

A written verification with real numbers, per era and per control. **Gate: your
approval before any bulk download or full purchase.**

## 8. Milestones

| # | milestone | gate |
|---|---|---|
| **0** | **Get retention rights in writing** (vendor matrix §6). No code. | **your decision on the reply** |
| 1 | Probe §7 against a trial or free tier | your approval of the results |
| 2 | Schema: securities, symbol_aliases, security_relationships, price_facts, corporate_action_facts, filings, fundamental_facts + migration | migration/ORM drift green |
| 3 | Importer + point-in-time policy for the new datasets | round-trip tests |
| 4 | EDGAR full-index + FSDS ingestion (free, independent of any purchase) | control filings reconciled |
| 5 | One-time commercial backfill → `research-01` | control universe reconstructed |
| 6 | Forward survivorship daemon | detects a real event end-to-end |
| 7 | Re-run the gate against `research-01` | survivorship PASS, fundamentals BLOCK cleared |

**Milestone 4 can start before any purchase** and is free: EDGAR ingestion is
useful whatever the vendor decision turns out to be, and it builds the very
instrument that verifies the vendor.

## 9. Recommendation

**Do not purchase.** Two blockers, in order: retention rights unresolved, and
pre-2009 point-in-time fidelity unverified.

**Do proceed** with Milestone 0 (a written question to the vendor) and, if you
want work to continue in parallel, Milestone 4 (free EDGAR ingestion), which is
useful under every outcome.

**Provisional vendor preference:** Sharadar Core US Equities Bundle, with EODHD
evaluated on the same probe rather than dismissed. Polygon is ruled out for
delisted coverage; Twelve Data is ruled out for historical fundamentals and
retained for forward prices.

**1998-01-01: conditionally approved**, subject to §7b. I specifically do *not*
recommend defaulting to 2009 — XBRL's start is a fact about machine-readable
values, not about dates, and EDGAR supplies authoritative dates from 1993.
