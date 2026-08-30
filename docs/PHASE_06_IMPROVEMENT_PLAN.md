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
[`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) ·
[`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md) ·
[`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md) ·
[`RESEARCH_01_DATA_CONTRACT.md`](RESEARCH_01_DATA_CONTRACT.md) ·
[`FORWARD_SURVIVORSHIP_SYSTEM.md`](FORWARD_SURVIVORSHIP_SYSTEM.md) ·
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md)

---

## 1. Status — no vendor is selected, and none has been probed

| finding | effect |
|---|---|
| **Kibot**: the archive is a one-time purchase of $990–$2,400, not the ~$14/month this plan assumed — that figure is the *subscription* that refreshes already-purchased data. *USER-VERIFIED from the vendor's own pricing page* | **eliminated on price.** See `PHASE_06_VENDOR_MATRIX.md` §1.1 |
| **Sharadar, EODHD**: never capability-tested for the archive role | **candidates, unprobed.** Sharadar has the strongest claimed feature set for the fundamentals spine — `permaticker`, as-reported/restated split, `DATEKEY` |
| **Twelve Data**: 5 years of fundamentals | ruled out for historical fundamentals on depth alone; retained for forward daily prices |

**Vendor selection is decided on coverage, data quality and price.** Licence
retention and deletion terms are the operator's concern, managed at the
operator's discretion, and are not a criterion here.

### The blockers as they now stand

1. **No price vendor is selected and none has been probed.** Kibot is out on
   price; Sharadar and EODHD have never been tested. `KIBOT_DATA_PROBE.md` is
   retained as the acceptance standard — its rules were written before any data
   was seen, and the item most likely to fail is survivorship *completeness*
   (§G), which can fail while everything else passes.
2. **The denominator has been run, its dating rule was found broken on 30.7% of
   dated exits, and it has been fixed.** First real run 2026-08-29: 90,548
   registrants, parser-integrity gate PASSED on 27,084,668 rows — but
   `resolve_exit` took the *earliest* confirming filing, so Intel was recorded as
   exiting in 1994 while still filing in 2026. The supersession rule
   (`EDGAR_DELISTING_DENOMINATOR.md` §7bc) removes the contradiction: 12,549 → 0,
   enforced at construction by `assert_exit_not_contradicted()`. **The per-year
   curve is now buildable from a rule with no known contradictions, and has not
   yet been read** — more than half the 2005–2007 peak turned out to be
   registrants that never exited, so what the corrected curve says about vendor
   coverage is an open question, not a settled one. See milestone 0a.
3. **Pre-2009 point-in-time fidelity is untested.** Sharadar and EODHD both claim
   pre-2009 fundamentals; the acceptance rule is stated in advance in
   `PHASE_06_VENDOR_MATRIX.md` §4. Pre-2009 values are a **deferred enhancement**,
   not a dependency (`RESEARCH_01_DATA_CONTRACT.md` §7.2).

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

Prices and fundamentals are **separate problems with separate sources and
separate start dates**, and the corpus does not depend on any single vendor for
both.

| need | source | recurring cost |
|---|---|---|
| historical prices 1998–present, active + delisted | **no vendor selected** — see §1 | to be established |
| historical corporate actions | derivable from a vendor's adjustment bases — *pending probe* | included |
| historical filing dates / accessions / form types 1994 Q3–present | **SEC EDGAR full-index** | **free** |
| historical fundamental **values** 2009–present | **SEC EDGAR XBRL / FSDS** | **free** |
| historical fundamental **values** 1998–2008 | **deferred** — see gap 2 | **none** |
| delisting **reasons**, all eras | **SEC EDGAR** Forms 25 / 15 / 8-K 1.03 | **free** |
| the independent survivorship denominator | **SEC EDGAR** Form 25/15 counts | **free** |
| forward daily prices | **Twelve Data** | existing subscription |
| forward filings and fundamentals | **SEC EDGAR** | **free** |
| forward corporate actions | **FMP + Twelve Data** | existing subscriptions |
| the accumulating security master | **TradeIt** | none |

**The shape of this table is the finding.** Almost everything the corpus needs is
public domain and free. **Prices are the only row that costs money** — which is
why the vendor question, though unresolved, blocks far less than it appears to.

### Gaps, stated plainly rather than hidden

1. **No price vendor has been proven, or even probed.** Every capability claim
   in rows 1 and 2 above belongs to a vendor not yet selected. The failure mode
   to test for is survivorship *completeness* (`KIBOT_DATA_PROBE.md` §G) — a
   delisted roster that exists but is thin, heavy on large well-known failures
   and light on exactly the short-lived 1999–2002 listings whose absence *is* the
   bias.
2. **Pre-2009 statement values have no free source and no tested paid one.**
   The recommendation is to **defer** them (`RESEARCH_01_DATA_CONTRACT.md` §7.2),
   because this platform's detectors are geometric and the dot-com objective is a
   price-and-survivorship problem.
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
| **0c** | ~~**Send Kibot pre-sales questions Q1–Q26**~~ — its vendor is out on price, but **the questions were not**: they are vendor-neutral and have been **re-aimed and sent** to Sharadar and EODHD | answers on file, or the absence of one recorded as a finding in its own right | **free** |
| 1 | ~~Kibot probe on a trial or single month~~ **VOID — vendor eliminated on price.** Replaced by: **select and probe a price vendor covering delisted securities**, held to the same acceptance rules | **your approval**, measured against [`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md). A failed item G is still not overridden by a passed item A | **deferred to Phase 9** |
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

**Milestones 0a–0c can start now and cost nothing.** 0a and 0b are not merely
preparation: they build the instrument that *measures* the vendor, so they must
precede the probe rather than follow it.

### Status

| milestone | state |
|---|---|
| **0a** — denominator | **RUN, DEFECT FOUND, DEFECT FIXED — all on 2026-08-29.** The first real run (128 index files, 1994 Q3–2026 Q2) passed the parser-integrity gate on all **27,084,668** rows with zero mismatches across form type, CIK, date, path and accession, and measured **90,548 registrants**. It then exposed a dating defect: `resolve_exit` took the *earliest* confirming filing, so **12,549 exits — 30.7% of the dated population — were dated before the registrant's own last periodic report**. `INTEL CORP` was recorded as exiting 1994-08-02 while still filing in 2026; so were Adobe, ConAgra and TJX. **The fix is supersession, proposed explicitly in [`EDGAR_DELISTING_DENOMINATOR.md`](EDGAR_DELISTING_DENOMINATOR.md) §7bc** rather than patched in: a confirming filing dates an exit only if the registrant filed no periodic report after it. The obvious repair — take the latest filing instead — was **considered and rejected**, because it would still have handed Intel a date, and the defect is not that Intel's date is early but that Intel has not exited. A registrant whose every confirming filing is superseded now resolves to `NON_EXIT_REGISTRANT_STILL_REPORTING`, carries **no date**, and is deliberately **not** counted among undated exits — folding it there would reconcile the totals while still claiming an exit. `assert_exit_not_contradicted()` is the companion to `assert_cessation_undated()` and runs at `Denominator` construction, so a denominator containing a self-contradicting exit **cannot be built at all**. Measured on the re-run: dated confirmed exits **40,920 → 29,180**, non-exits **11,740**, contradictions **12,549 → 0**, with registrant total, undated population (49,628) and identity mapping unchanged, as a dating-only change requires. All four named registrants now carry no exit date. Every figure closes arithmetically against the independently measured 12,549 (§7bc). **A consequence worth its own line: more than half of the 2005–2007 peak §7bb could not explain was registrants that never exited** (2005: 2,827 → 1,393; 2006: 3,012 → 1,205; 2007: 3,113 → 1,417). The per-year curve is now built from a dating rule with no known contradictions; **reading it is separate work and has not been done**. Do not restate any count here from memory — re-run the command |
| **0b** — 30 controls to `MANUAL_VERIFIED` | **COMPLETE. 30 of 30 fully adjudicated.** All three measurements now read **30/30**: **identity** — every control has an evidence-backed resolution, **0 `UNRESOLVED`** and **0 at `RESOLVED`**; **mapping quality** — every *recorded* mapping is `MANUAL_VERIFIED`; **milestone completion** — every control's issuer question is settled as well as cited. That they coincide is the *result* here rather than a coincidence, but the three remain distinct questions and a single control regressing on either condition reopens the milestone. Each of the 30 cites a primary regulatory source and **no identifier was guessed**; the fixture in `controls.py` still carries none. **`FRC` is the first control whose issuer key is not an SEC CIK**: a bank with no holding company files its Exchange Act reports with the FDIC, so it has no SEC filer account, and it is discriminated by `FDIC_CERT:59017` (primary) with `FRB_RSSD:4114567` corroborating — the regulator-neutral identity architecture exercised on shipped evidence rather than only in tests. Its SEC subject-company CIK `1132979` is recorded as an **unresolved related identity**, not as an identifier: that record carries EIN `88-0157485` while the FDIC registrant reports `80-0513856`, and sameness is not established in either direction. The distinctions are held open by synthetic tests rather than by any shipped control demonstrating them, which is what keeps them from collapsing into one number now that all three read the same. A control is fully adjudicated when every recorded mapping is `MANUAL_VERIFIED` **and** its issuer question is settled — three controls (BBBY, GM, AOL) must adjudicate whether a *second* issuer held their ticker, discharged either by evidencing one or by a cited finding that none was established. **All three are now discharged by evidence**, each with two independently cited issuers and `identity_break: true`: BBBY (CIK 886158 on Nasdaq, `$.01` par; CIK 1130713 on the NYSE, `$0.0001` par), AOL (CIK 883780; CIK 1468516), GM (CIK 40730; CIK 1467858). AOL is the strictest: both its filings name the New York Stock Exchange and the same symbol and the registrant names are close, so **the CIK is the only discriminator** — which is why no series may run across the two. **No recorded mapping now rests on `company_tickers.json`** — a dated primary source, but a reference file rather than a filing someone read, which is a categorical gap from `MANUAL_VERIFIED` rather than a matter of confidence. AAPL and GM's second issuer were the last two to rest on it and both now cite filings, so **no control remains at `RESOLVED`**; the rule that a ticker file cannot by itself carry a mapping to `MANUAL_VERIFIED` is curatorial and is held open by test rather than by the loader. All three counts come from `tradeit edgar controls`, which prints them separately; do not restate any from memory |
| **0c** — vendor capability questions | **SENT to both surviving candidates; EODHD has replied, Sharadar has not.** Kibot is eliminated on price, so the questions were re-aimed at Sharadar (via Nasdaq Data Link) and EODHD; the 22 are vendor-neutral and went verbatim ([`VENDOR_QUESTIONS_READY_TO_SEND.md`](VENDOR_QUESTIONS_READY_TO_SEND.md)). **The one established result is a licensing fact, not a capability one**: EODHD classifies our use — internal, non-public organisational research, one-time backfill, no redistribution — as **commercial**, and states its public pricing page is intended for personal use. **EODHD's published price is therefore withdrawn rather than merely unverified**, and a commercial quote is requested and not received ([`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) §1.3, row 18). The rest of the reply is deliberately **not transcribed** into the repository; quote it verbatim or not at all. **Two defects in our own send, both corrected on the same thread**: it carried two questions from Sharadar's schema (`permaticker`, `ACTIONS`) that are not EODHD concepts, and it **omitted the single EODHD-specific question** — its documentation says *"from January 2000"* in one place and *"30+ years"* in another, and if US history really begins in 2000 it cannot span 1998–2002 and EODHD is out regardless of price. That question is now asked and unanswered. **The completeness question has been reframed for both vendors, and this is the part that generalises**: 0a built an EDGAR-derived benchmark, so we no longer ask a vendor to certify that its delisted coverage is complete — we ask only for the least expensive access that lets us *measure* it. A vendor's assurance was never admissible evidence here; it now does not need to be. **Question 1 still has no answer from anybody** — whether "delisted" means those securities are actually included, or only currently active ones. It has outlived three vendors |

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

   **One constraint now governs the vendor question, and it is decided:**

   - **Nothing is bought until Phase 9 needs it.** Prices are the *only* dataset
     in this plan that costs money: filing dates, delisting events, fundamentals
     from 2009 and macro are all free (EDGAR and FRED), and issuer identity is
     already done. The corpus gates honest *backtesting*, not the construction
     of Phases 7 and 8, so the spend moves to the point of use.
2. **Sharadar and EODHD are untested candidates**, not eliminations. Neither has
   been probed for the archive role, and Sharadar has the strongest claimed
   feature set for the fundamentals spine. Probe before buying, on the rules in
   `KIBOT_DATA_PROBE.md`.
3. **Milestones: 0a and 0b are done, 0c is sent.** 0a has been run against a real
   EDGAR archive; it found a dating defect in its own output and the defect is
   fixed. 0b is 30/30. 0c's questions are with both surviving vendors.
   **The live thread is now reading what 0a produced** — the corrected per-year
   termination curve exists and has not been read.
4. ~~**Run the Kibot probe.**~~ **Void — its vendor is eliminated on price.** And
   ~~*0a has never been run*~~ — **superseded on 2026-08-29**; the measuring
   device was built, taken to the corpus, and caught a defect that no synthetic
   test had. **The gap that replaces it is smaller and specific: the curve has
   been built and not yet read.** Note what the sequence cost — two explanations
   of the *uncorrected* curve were offered and both were falsified by
   measurement, and the shape they were explaining was partly an artefact of the
   dating defect. A third explanation is not owed. Read the corrected curve
   first and report what it says.
5. **Defer pre-2009 fundamental values.** Machine-readable values do not exist in
   XBRL before 2009, so a vendor is the only route to them; that is a separate
   purchase, on a separate spine, with its own probe, and it is not on the
   critical path for `research-01`. Defer it on scope and cost, which are the
   only grounds this plan judges a vendor on.
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
