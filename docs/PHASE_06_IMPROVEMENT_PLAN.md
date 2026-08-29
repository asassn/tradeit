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
| **Kibot**: licence explicitly permits keeping delivered data permanently; cancellation does not require deletion | **licence correct, but ELIMINATED ON PRICE.** The archive is a one-time purchase of $990–$2,400, not the ~$14/month this plan assumed — that figure is the *subscription* that refreshes already-purchased data. See `PHASE_06_VENDOR_MATRIX.md` §1.3 |

### The blockers as they now stand

1. **~~Retention rights unresolved~~ → resolved. Two vendors eliminated, one
   qualifies on licence.**
2. **No price vendor is selected.** Kibot was the only candidate whose licence
   permitted permanent retention, and it is eliminated on price: the archive
   costs $990–$2,400, not the ~$14/month this plan was built on. **The
   requirement it satisfied has not gone away** — permanent retention is
   reaffirmed as a hard rule, which keeps every deletion-on-termination
   subscription disqualified however cheap it is. A replacement must clear both
   bars at once, and none has been identified. `KIBOT_DATA_PROBE.md` is retained
   as the acceptance standard: its rules were written before any data was seen,
   and the item most likely to fail is survivorship *completeness* (§G), which
   can fail while everything else passes.
3. **Our own subscriptions' retention terms are unverified, and this is now the
   live retention question.** Twelve Data and FMP were never checked, and
   `full-01` was built from Twelve Data prices.
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
| **0b** | **Confirm the 30 control securities against primary regulatory evidence** to `MANUAL_VERIFIED` — names, issuer identifiers, dates ([`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md) §2c). **EDGAR is the normal source**, and is the only one for an SEC-reporting issuer; where an issuer legally reports its Exchange Act filings to a different federal regulator, that regulator's **direct** filing is equally admissible. **This is not a general widening**: it covers direct primary regulatory filings only — never a corporate website, an aggregator, a press release, or a search result, however official-looking | 30/30 confirmed or replaced **before** any vendor data is seen | **free** |
| **0c** | ~~**Send Kibot pre-sales questions Q1–Q26**~~ **MOOT — vendor eliminated on price.** The questions themselves remain the model for interrogating any replacement | not applicable to a vendor that is out | **free** |
| **0d** | **Verify retention terms in writing for every vendor with a working adapter** — Twelve Data, FMP **and Tiingo** ([`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §3.1). Tiingo was outside the milestone's original wording and should not have been: its adapter is functional, not a stub, so it can write vendor facts into a permanent corpus today under terms nobody has read | a classification per source, `UNCLEAR` treated as prohibited | **free** |
| 1 | ~~Kibot probe on a trial or single month~~ **VOID — vendor eliminated on price.** Replaced by: **select a price vendor that permits permanent retention and covers delisted securities**, held to the same acceptance rules | **your approval**, measured against [`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md). A failed item G is still not overridden by a passed item A | **deferred to Phase 9** |
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
| **0a** — denominator | **implemented** in `src/tradeit/edgar/`, CLI (`tradeit edgar denominator`), ruff and mypy clean; measure the test count with `pytest tests/unit/test_edgar_*.py --collect-only -q` rather than reading it here — the number written in this row was stale for several milestones. **The curated control identity is now supplied to the build by default** (`security_mappings_by_cik`); until that wiring existed `BuildOptions.mappings` was accepted and never passed, so the identity section reported every registrant `UNRESOLVED` on a corpus whose thirty control identities had been verified against filings. `--no-control-mappings` reproduces the old behaviour deliberately. **Not yet run against real EDGAR data**: `sec.gov` returns `EGRESS_BLOCKED` here. Requires an operator to run `tradeit edgar fetch-recipe` in an unrestricted environment |
| **0b** — 30 controls to `MANUAL_VERIFIED` | **COMPLETE. 30 of 30 fully adjudicated.** All three measurements now read **30/30**: **identity** — every control has an evidence-backed resolution, **0 `UNRESOLVED`** and **0 at `RESOLVED`**; **mapping quality** — every *recorded* mapping is `MANUAL_VERIFIED`; **milestone completion** — every control's issuer question is settled as well as cited. That they coincide is the *result* here rather than a coincidence, but the three remain distinct questions and a single control regressing on either condition reopens the milestone. Each of the 30 cites a primary regulatory source and **no identifier was guessed**; the fixture in `controls.py` still carries none. **`FRC` is the first control whose issuer key is not an SEC CIK**: a bank with no holding company files its Exchange Act reports with the FDIC, so it has no SEC filer account, and it is discriminated by `FDIC_CERT:59017` (primary) with `FRB_RSSD:4114567` corroborating — the regulator-neutral identity architecture exercised on shipped evidence rather than only in tests. Its SEC subject-company CIK `1132979` is recorded as an **unresolved related identity**, not as an identifier: that record carries EIN `88-0157485` while the FDIC registrant reports `80-0513856`, and sameness is not established in either direction. The distinctions are held open by synthetic tests rather than by any shipped control demonstrating them, which is what keeps them from collapsing into one number now that all three read the same. A control is fully adjudicated when every recorded mapping is `MANUAL_VERIFIED` **and** its issuer question is settled — three controls (BBBY, GM, AOL) must adjudicate whether a *second* issuer held their ticker, discharged either by evidencing one or by a cited finding that none was established. **All three are now discharged by evidence**, each with two independently cited issuers and `identity_break: true`: BBBY (CIK 886158 on Nasdaq, `$.01` par; CIK 1130713 on the NYSE, `$0.0001` par), AOL (CIK 883780; CIK 1468516), GM (CIK 40730; CIK 1467858). AOL is the strictest: both its filings name the New York Stock Exchange and the same symbol and the registrant names are close, so **the CIK is the only discriminator** — which is why no series may run across the two. **No recorded mapping now rests on `company_tickers.json`** — a dated primary source, but a reference file rather than a filing someone read, which is a categorical gap from `MANUAL_VERIFIED` rather than a matter of confidence. AAPL and GM's second issuer were the last two to rest on it and both now cite filings, so **no control remains at `RESOLVED`**; the rule that a ticker file cannot by itself carry a mapping to `MANUAL_VERIFIED` is curatorial and is held open by test rather than by the loader. All three counts come from `tradeit edgar controls`, which prints them separately; do not restate any from memory |
| **0c** — Kibot Q1–Q26 | **MOOT — the vendor is eliminated on price.** The questions were first not sent because the owner elected to buy and measure instead; that purchase is now off, so there is nothing to ask this vendor. They are kept in [`VENDOR_QUESTIONS_READY_TO_SEND.md`](VENDOR_QUESTIONS_READY_TO_SEND.md) §1 as the **model for interrogating a replacement** — question 1, whether "tradable" includes delisted securities, is the one that decides any price vendor and was never answered for Kibot either |
| **0d** — vendor retention terms | **OUTSTANDING, unsent, and now the only licence question left standing.** The questions are written and sendable verbatim ([`VENDOR_QUESTIONS_READY_TO_SEND.md`](VENDOR_QUESTIONS_READY_TO_SEND.md) §2–§3); nothing has been sent, so Twelve Data, FMP and Tiingo all remain `UNCLEAR — WRITTEN CONFIRMATION REQUIRED`, which the register treats as prohibited. **Three things changed around it rather than in it.** (1) *Permanent retention is now a stated hard requirement from the owner*, not a preference — so an `UNCLEAR` answer is disqualifying rather than merely unresolved. (2) *Kibot's elimination removed the alternative*: 0d was one licence question among several and is now the whole of it, because Sharadar and EODHD are already excluded on licence and Kibot is out on price. (3) *`full-01` was built from Twelve Data prices*, so this governs the one real corpus the project already holds — it is frozen and machinery-only, and that is the shape of the exposure rather than a present breach. **The blocker was re-measured, not recalled**: `twelvedata.com`, `site.financialmodelingprep.com` and `financialmodelingprep.com` each returned `EGRESS_BLOCKED` again in this session, so the published terms cannot be read from here and no classification can be inferred from the fact that the APIs currently work. Advancing 0d needs the operator's own browser or an email — it cannot be advanced from inside a session |

> **Do not pay for access before the instrument that measures it exists.**

## 9. Recommendation

1. **Do not purchase anything yet** — restored, and now for a different reason
   than it was first written. **No price vendor is currently selected, and no
   purchase happens before Phase 9.**

   This recommendation was reversed once, on the belief that a ~$14 Kibot month
   would buy the corpus, and is now restored because that belief was wrong: the
   archive costs $990–$2,400 and is out of budget (`PHASE_06_VENDOR_MATRIX.md`
   §1.3). Both turns are left visible rather than tidied into a single position,
   because the reversal and its cause are the useful record — the plan committed
   to a purchase on a misread price, and the correction came from the vendor's
   own pricing page.

   **Two constraints now govern the vendor question, and they are decided:**

   - **Permanent retention is a hard requirement**, reaffirmed by the owner. A
     licence that requires deleting the data — or anything derived from it — on
     cancellation is disqualifying, whatever it costs. This keeps Sharadar and
     EODHD eliminated and applies equally to any subscription-model replacement.
   - **Nothing is bought until Phase 9 needs it.** Prices are the *only* dataset
     in this plan that costs money: filing dates, delisting events, fundamentals
     from 2009 and macro are all free (EDGAR and FRED), and issuer identity is
     already done. The corpus gates honest *backtesting*, not the construction
     of Phases 7 and 8, so the spend moves to the point of use.
2. **Sharadar and EODHD are eliminated** for `research-01` on licence grounds,
   independent of data quality. Reconsider only under a different written licence
   that explicitly grants post-termination retention.
3. **Milestones 0a–0d: 0b is complete, 0a is implemented but has never been run
   against real EDGAR data, 0c is moot now that its vendor is eliminated, and 0d
   is still outstanding.** 0d matters more than before, not less: `full-01` was
   built from Twelve Data prices whose retention terms remain `UNVERIFIED`, and
   with the permanent-retention rule reaffirmed it is now the *governing*
   question about the one real corpus this project already holds. It is also the
   **last** licence question — Sharadar and EODHD are out on licence, Kibot on
   price — so there is no vendor left whose terms are both acceptable and known.
   Its scope now includes **Tiingo**, which has a working adapter and unread
   terms; that was an omission in the original wording rather than a decision.
   Nothing about it can be settled from inside a session: all three vendor
   domains are `EGRESS_BLOCKED`, re-measured rather than assumed, so it needs a
   browser or an email and costs nothing but a reply.
4. ~~**Run the Kibot probe.**~~ **Void — its vendor is eliminated on price.**
   The free work is what proceeds: **0a against a real EDGAR archive** is the
   largest unrealised item in this plan. It has been implemented with 41 tests
   and never once run against real data, it costs nothing, and it produces the
   per-year termination denominator — which is also the instrument that would
   *evaluate* any replacement vendor. Building the measuring device and never
   taking the measurement is the gap to close first.
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
