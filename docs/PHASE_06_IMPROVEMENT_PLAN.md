# Phase 6 improvement plan — the permanent historical research corpus

**Data architecture and source verification only.** Nothing purchased, nothing
implemented, no thresholds touched, no performance computed. `full-01` frozen
and untouched.

> **Scope note.** TradeIt will run three portfolio mandates at different
> horizons, so **Daily is not the only timeframe the data plan must serve**. This
> document covers the **EOD corpus** (`research-01`), which serves the Swing and
> Retirement mandates. A second corpus — `intraday-01`, 1-minute base — is
> specified in [`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md) and
> probed separately (`KIBOT_DATA_PROBE.md` §H). **It does not gate anything
> below.**

Companion documents: [`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md) ·
[`EDGAR_DELISTING_DENOMINATOR.md`](EDGAR_DELISTING_DENOMINATOR.md) ·
[`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) ·
[`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) ·
[`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md) ·
[`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md) ·
[`RESEARCH_01_DATA_CONTRACT.md`](RESEARCH_01_DATA_CONTRACT.md) ·
[`FORWARD_SURVIVORSHIP_SYSTEM.md`](FORWARD_SURVIVORSHIP_SYSTEM.md) ·
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md)

---

## 1. Status — the retention question is answered, and it reshaped the plan

The blocker that dominated the previous revision — *are retention rights
permitted?* — has been resolved from primary sources by the project owner. The
answer eliminated the leading candidate and replaced it.

| finding | effect |
|---|---|
| **Sharadar Personal Use License**: on termination, discontinue use, delete all copies within 30 days, **and delete datasets derived from Services Data within 30 days** | **Sharadar eliminated.** `research-01` and every scan run over it *are* derived datasets. The one-month-download-and-keep model is prohibited |
| **EODHD**: deletion of stored provider data within one month after termination | **EODHD eliminated**, same reason |
| **Kibot**: licence explicitly permits keeping delivered data permanently; cancellation does not require deletion | **the only live candidate for the price spine** — and entirely unproven as *data* |

### The blockers as they now stand

1. **~~Retention rights unresolved~~ → resolved. Two vendors eliminated, one
   qualifies on licence.**
2. **Kibot's data is unverified.** A ~$14/month product claiming a
   64-year US equity history including delisted securities makes a claim no
   competitor in the matrix makes at any price. **Do not assume it works.**
   `KIBOT_DATA_PROBE.md` is the design of the test, written before any data is
   seen. The item most likely to fail is survivorship *completeness* (probe §G),
   and it can fail while everything else passes.
3. **New: our own subscriptions' retention terms are unverified.** Twelve Data
   and FMP were never checked, and `full-01` was built from Twelve Data prices.
   Having just eliminated two vendors for exactly this defect, the forward
   accumulation model must not be built on an unverified retention right.
   (Vendor matrix §1.4.) `full-01` is frozen and never cited for economic claims,
   so nothing published is at risk today.
4. **Pre-2009 point-in-time fidelity** is no longer a blocker — it is moot,
   because both vendors that could have supplied pre-2009 fundamental *values*
   are eliminated. The test is retained verbatim for any future candidate.
   Pre-2009 values are now a **deferred enhancement**, not a dependency
   (`RESEARCH_01_DATA_CONTRACT.md` §7.2).

## 2. What was and was not verifiable here

This session's egress policy blocked every vendor site and `sec.gov` — including
`kibot.com`. The proxy's own guidance is that such denials are organisation
policy and must not be routed around, so they were not.

Confidence grades are used strictly. A grade **USER-VERIFIED** has been added for
primary-source text read by the project owner outside this session; it is
authoritative for **licence text** and carries no weight at all for **capability
claims**, which the probe must measure. **All Kibot capability statements in this
plan are vendor claims, untested.**

## 3. Task 2 — what SEC EDGAR supplies, by era

The single most useful finding of this milestone: **EDGAR's filing-date coverage
does not begin with XBRL.** Two different products with two different start
dates, and conflating them is what would wrongly force a 2009 start.

| product | from | machine-readable | gives |
|---|---|---|---|
| **quarterly full-index** (`master.idx` / `form.idx`) | **1994 Q3** | yes — pipe-delimited | CIK, company name, **form type, filing date**, accession path |
| **Financial Statement Data Sets** | **2009 Q1** (first submissions 2009-04-15) | yes — `sub`/`num`/`tag`/`pre`, keyed on `adsh` | as-filed statement *values*, uncorrected |
| **submissions / companyfacts APIs** | XBRL era | yes — JSON | per-company filing history and XBRL facts |
| **filing documents** | 1994 Q3+ | **no** — HTML/text | everything else, by parsing |

### The hard three-way distinction

| | 1998–2008 | 2009– |
|---|---|---|
| **directly machine-readable** | filing dates, accessions, form types, CIK, company names — via full-index | all of the above **plus** statement values via XBRL/FSDS |
| **requires parsing filing text/HTML** | **all statement values**; delisting and merger detail inside 8-K/25/15 | 8-K narrative detail |
| **cannot be reliably reconstructed** | ticker↔CIK mapping for the era (EDGAR is CIK-centric; tickers are not authoritative in old filings); intraday or point-in-time *prices* (EDGAR has none) | prices |

**Implication.** EDGAR alone can give `research-01` an authoritative
`filed_at`/`accession`/`period_end` spine from **1994 Q3**, and *values* only from
2009. The commercial vendor's job is therefore narrower and clearer than it first
appears — and after the licence findings, narrower still: **the price and
lifecycle history, and nothing else.** Pre-2009 statement values are deferred
(§5 gap 2).

### EDGAR is also a survivorship *measuring instrument*, not just a source

The finding that most changes what is possible: **Forms 25 / 25-NSE (exchange
delisting), 15 (deregistration) and 8-A (registration of a class of securities)
are all in the free quarterly full-index from 1994 Q3, with filing dates.**

Counting them per year produces an **independent, primary-source, zero-cost
estimate of how many US securities stopped being listed in each year 1998–2026**.
That is a denominator. Without it, "the vendor's delisted roster has *N* names" is
a number with nothing to compare against; with it, coverage becomes measurable
rather than asserted. Known biases — the 2005 Rule 12d2-2 amendment changed the
Form 25 regime, not every delisting produces one, and the counts include
non-common securities — are recorded with the estimate. See
[`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md) §G1.

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

## 5. Task 8 — the revised decomposition, and the gaps

The licence findings force a cleaner split than the previous revision had. Prices
and fundamentals are now **separate problems with separate sources, separate
licences and separate start dates**, and the corpus no longer depends on any
single vendor for both.

| need | source | licence | recurring cost |
|---|---|---|---|
| historical prices 1998–present, active + delisted | **Kibot, one-time** — *pending probe* | **permanent retention permitted** | ~$14/month for as long as the backfill takes |
| historical corporate actions | **derived from Kibot's three adjustment bases** — *pending probe* | as above | included |
| historical filing dates / accessions / form types 1994 Q3–present | **SEC EDGAR full-index** | public domain | **free** |
| historical fundamental **values** 2009–present | **SEC EDGAR XBRL / FSDS** | public domain | **free** |
| historical fundamental **values** 1998–2008 | **deferred** — see gap 2 | — | **none** |
| delisting **reasons**, all eras | **SEC EDGAR** Forms 25 / 15 / 8-K 1.03 | public domain | **free** |
| the independent survivorship denominator | **SEC EDGAR** Form 25/15 counts | public domain | **free** |
| forward daily prices | **Twelve Data** | **UNVERIFIED — §1 blocker 3** | existing subscription |
| forward filings and fundamentals | **SEC EDGAR** | public domain | **free** |
| forward corporate actions | **FMP + Twelve Data** | **UNVERIFIED — §1 blocker 3** | existing subscriptions |
| the accumulating security master | **TradeIt** | ours | none |

**The shape of this table is the finding.** Everything permanent is either
public domain or under a licence that permits permanent retention. The only
recurring-subscription rows are *forward* data, which is re-acquirable and not
archival — except that its retention terms are unverified, which is why blocker 3
exists.

### Gaps, stated plainly rather than hidden

1. **Kibot is unproven.** Every capability in row 1 and row 2 above is a vendor
   claim. If the probe fails on survivorship completeness (`KIBOT_DATA_PROBE.md`
   §G), there is currently **no remaining candidate** for a retention-permitting
   1998 price spine, and the plan returns to sourcing — not to moving the date.
2. **Pre-2009 statement values have no free source and no qualifying paid one.**
   Both candidates are eliminated on licence. The recommendation is to **defer**
   them (`RESEARCH_01_DATA_CONTRACT.md` §7.2), because this platform's detectors
   are geometric and the dot-com objective is a price-and-survivorship problem.
   Narrow in-house parsing of EDGAR filing text is worth doing **for the control
   universe only**, where it is a few hundred documents and hand-checkable.
3. **Ticker↔CIK mapping before ~2009 is genuinely hard, and Kibot probably does
   not help.** A ticker-keyed price service supplies no permanent identifier
   (probe item D). EDGAR is CIK-centric and old filings carry no authoritative
   ticker. Expect best-effort mapping, `cik = NULL` where unresolved, and manual
   curation for the controls. **This is the largest remaining engineering item in
   Phase 6.**
4. **Delisting *reasons* are frequently absent.** Forms 25/15 say a security was
   delisted, not always why. `delisting_reason = unknown` is a first-class value
   for exactly this reason. A price vendor supplies none at all — reasons are
   EDGAR's job.
5. **Twelve Data fundamentals are ~5 years deep** (CORROBORATED) and cannot serve
   history. FMP is excluded from fundamentals by standing rule.
6. **Exchange/venue history before 2009** (moves between NYSE/AMEX/Nasdaq tiers)
   is poorly covered everywhere and may end up `unknown`.
7. **OTC / pink-sheet coverage is out of scope** and must be *declared* so in
   `CORPUS_REGISTRY.md`. A security that delisted from an exchange and continued
   trading OTC has, for our purposes, ended — a recorded modelling decision, not
   a silent gap.
8. **Our own forward vendors' retention terms are unverified** (§1 blocker 3).

## 6. Task 6 — dot-com reconstruction

Twelve control classes, named in advance, in
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md), **plus an expanded
1998–2002 set** (§2b there) covering short-lived listings, the CLEC/broadband
failure cluster, peak acquisitions, identity-changing mergers, splits and reverse
splits, spinoffs, and six seed ticker-reuse cases. They are chosen so a
survivorship-biased corpus **cannot** pass: every one either disappeared, changed
identity, or had its ticker reused.

Two entries carry more weight than the rest:

- **BBBY** (class 12) is the case `full-01` actually hit, not a hypothetical.
- **The short-lived listings** (§2b.1 — Pets.com, eToys, Webvan, and the smaller
  names beside them) are the ones a thin roster loses first. A corpus that
  returns WorldCom and Enron cleanly while returning `NOT_FOUND` for the small
  1999–2001 failures has kept the headlines and lost the population, and no
  aggregate statistic reveals it.

## 7. Task 7 — the pre-purchase probe

Fully specified in **[`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md)**, items A–G,
written before any data is seen so the acceptance rules cannot be adjusted to fit
the result. Summary of what it decides:

| item | question | can fail the vendor outright |
|---|---|---|
| **A** | does the ~$14 EOD tier actually deliver the full historical delisted universe, in bulk, inside one billing month? | yes |
| **B** | the census — active, delisted, earliest date, pre-1998 starts, terminations by year, short-history counts | informational, feeds G |
| **C** | named controls from `DOTCOM_CONTROL_UNIVERSE.md`, expanded for 1998–2002 | yes |
| **D** | identity semantics — permanent id? ticker changes? reused tickers? CIK/CUSIP/FIGI? | a concatenated reused-ticker file is disqualifying at face value |
| **E** | corporate actions and adjustment methodology, recovered from the three bases | yes if undocumented *and* unrecoverable |
| **F** | cross-vendor comparison against Twelve Data (and FMP for OHLCV only) on 10–20 overlaps | yes on systematic disagreement |
| **G** | **survivorship completeness** — the item most likely to fail while everything else passes | yes |

**Order of execution, cheapest first: free EDGAR work → free written pre-sales
questions → trial or one month → probe on a sample → bulk download only after the
probe passes.**

### 7b. The pre-2009 date test, retained for a future candidate

There is currently **no vendor to run it against** — both pre-2009 fundamentals
candidates are eliminated on licence. The rule is kept verbatim so it is not
reinvented more leniently later:

```
vendor filing-date field   vs   EDGAR full-index `filed` for the same accession
```

| observation | verdict |
|---|---|
| vendor date ≈ EDGAR `filed` across all eras | point-in-time |
| agreement 2009+, divergence before | **2009+ is PIT; pre-2009 is not** → reject the pre-2009 segment |
| vendor dates cluster on fiscal quarter-ends | **not point-in-time at all, whatever the vendor calls it** → reject the field |

The middle row is the one to watch for, because it is the failure that would
otherwise be invisible.

## 8. Milestones

| # | milestone | gate | cost |
|---|---|---|---|
| **0a** | **EDGAR full-index ingestion + the delisting denominator** — design complete in [`EDGAR_DELISTING_DENOMINATOR.md`](EDGAR_DELISTING_DENOMINATOR.md) | per-year termination counts by evidence strength; EDGAR-only cohort survival curves | **free** |
| **0b** | **Confirm the 30 control securities against EDGAR** to `MANUAL_VERIFIED` — names, CIKs, dates ([`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md) §2c) | 30/30 confirmed or replaced **before** any vendor data is seen | **free** |
| **0c** | **Send Kibot pre-sales questions Q1–Q26** ([`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md) §Q) | replies filed and graded VERIFIED / CORROBORATED / UNVERIFIED | **free** |
| **0d** | **Verify Twelve Data and FMP retention terms** in writing ([`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §3.1) | a classification per source, `UNCLEAR` treated as prohibited | **free** |
| 1 | Kibot probe on a trial or single month, **sample only** | **your approval of the probe result** — measured against [`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md). A failed item G is not overridden by a passed item A | ~$14 |
| 2 | Schema: securities, symbol_aliases, security_relationships, price_facts, corporate_action_facts, filings, fundamental_facts + migration | migration/ORM drift green | none |
| 3 | Importer + point-in-time policy for the new datasets | round-trip tests | none |
| 4 | Bulk price backfill → `research-01` | control universe reconstructed; §G acceptance rules evaluated and *reported*, pass or fail | within the same billing month |
| 5 | EDGAR XBRL/FSDS fundamentals 2009+ | reconciled against filings | free |
| 6 | Narrow EDGAR text parsing for the control universe only | headline metrics hand-checked | free |
| 7 | Forward survivorship daemon | detects a real event end-to-end | existing |
| 8 | Re-run the gate against `research-01` | survivorship result **reported with its measured limitations**, not asserted | none |
| **I** | **Intraday probe (H1–H12), written questions only** — runs in parallel, gates nothing | answers on file | **free** |

Milestone **I** is deliberately unnumbered and off the critical path. Its written
questions are free and can ride along with 0b to save a round trip, but no EOD
decision waits on its answers.

**Milestones 0a–0d can start now and cost nothing.** 0a and 0b are not merely
preparation: they build the instrument that *measures* the vendor, so they must
precede the probe rather than follow it.

### Status

| milestone | state |
|---|---|
| **0a** — denominator | **implemented** in `src/tradeit/edgar/` with 41 tests, CLI (`tradeit edgar denominator`), ruff and mypy clean. **Not yet run against real EDGAR data**: `sec.gov` returns `EGRESS_BLOCKED` here. Requires an operator to run `tradeit edgar fetch-recipe` in an unrestricted environment |
| **0b** — 30 controls to `MANUAL_VERIFIED` | **in progress; 9 of 30 still require `MANUAL_VERIFIED`.** Two measurements, and they are not the same number. **Identity:** 23/30 have evidence-backed resolutions — 21 `MANUAL_VERIFIED` (IPET, TGLO, ENE, BEL, LEH, MSFT, CSCO, AMZN, SPY, QQQ, ETYS, WBVN, KOOP, MPPP, WCOM, EXDS, PSIX, GCTY, BCST, CPQ, BBBY) and 2 `RESOLVED` (AAPL, GM) — leaving **7/30 `UNRESOLVED`**. **Milestone completion:** only `MANUAL_VERIFIED` clears this milestone, so the 2 `RESOLVED` controls still count as outstanding and **21/30 are done, 9/30 remain**. **0b completes only at 30/30 `MANUAL_VERIFIED`.** A `RESOLVED` control has a defensible mapping from a dated primary source; `MANUAL_VERIFIED` additionally means a person read a filing and cited it, which is what this milestone asks for. **No CIK has been guessed** — every mapping cites a primary source, and the fixture in `controls.py` still carries none. Both counts come from `tradeit edgar controls`, which prints them separately; do not restate either from memory |
| **0c** — Kibot Q1–Q26 | **ready to send**, verbatim, in [`VENDOR_QUESTIONS_READY_TO_SEND.md`](VENDOR_QUESTIONS_READY_TO_SEND.md) §1 |
| **0d** — Twelve Data and FMP retention | **ready to send** (§2, §3). Both vendor sites returned `EGRESS_BLOCKED`; classification stays `UNCLEAR`, treated as prohibited |

> **Do not pay for access before the instrument that measures it exists.**

## 9. Recommendation

1. **Do not purchase anything yet**, including Kibot. The licence is right; the
   data is unproven, and the specific claim being made is unusual enough at the
   price to deserve scrutiny rather than relief.
2. **Sharadar and EODHD are eliminated** for `research-01` on licence grounds,
   independent of data quality. Reconsider only under a different written licence
   that explicitly grants post-termination retention.
3. **Start Milestones 0a–0d now.** All free, all useful under every outcome, and
   0a/0b are the instruments that verify the vendor.
4. **Then run the Kibot probe** and bring the numbers back against
   [`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md). If it passes, one
   month at ~$14 buys a permanently retainable 1998–present price corpus, which
   is a materially better position than the previous revision's.
5. **Defer pre-2009 fundamental values.** Do not accept a subscription source for
   them if cancellation would force us to delete `research-01`.
6. **Plan for CONDITIONAL.** The realistic outcome is a corpus that is
   *materially* or *partially* survivorship-corrected rather than
   survivorship-safe. That is not a failure; the discipline is that the
   limitation is measured, classified and published beside the corpus, with the
   prohibited-conclusion list attached (`RESEARCH_01_DATA_CONTRACT.md` §10).

**1998-01-01: held, and no longer conditional on a fundamentals vendor.** XBRL's
start is a fact about machine-readable values, not about dates or prices, and
EDGAR supplies authoritative dates from 1994 Q3 — four years before the
research-01 start. The corpus is defined as prices from 1998, filing metadata
from 1994 Q3, and fundamental values from 2009, with the pre-2009 value gap
recorded as a declared limitation rather than hidden.
