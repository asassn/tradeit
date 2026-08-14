# Phase 6 Milestone 1 — vendor and data-contract verification

**Nothing was purchased. No implementation began.** This is the verification
report for approval.

## 0. What I could and could not verify, and why that matters

This environment's egress proxy blocks the primary sources. `data.nasdaq.com`,
`sharadar.com`, `quantrocket.com`, `sec.gov` and `resources.quandl.com` all
returned `EGRESS_BLOCKED`. What follows is therefore assembled from sources that
*were* reachable — chiefly a vendor-integration client's own source code, which
is authoritative about the data contract it consumes — plus search-result
extracts from the blocked pages.

Every row below is marked with its confidence, and **pricing is not verified at
all**. I will not put a number you might act on into this document on the
strength of a second-hand 2024 figure.

| confidence | meaning |
|---|---|
| **A** | read from a primary or integration source in this session |
| **B** | consistent across multiple independent search extracts |
| **C** | single second-hand extract; treat as a lead, not a fact |
| **—** | could not verify |

## 1. Sharadar — Core US Equities Bundle

| criterion | finding | conf. |
|---|---|---|
| **Point-in-time filing dates** | `DATEKEY` **is the filing date** and is the field to index on for point-in-time work. QuantRocket's client shifts it **forward one day** to avoid lookahead. Distinct fields exist for `CALENDARDATE`, `REPORTPERIOD` (fiscal period end) and `LASTUPDATED`. | **A** |
| **Restatements** | Explicit dimension pairs: `ARQ`/`ARY`/`ART` = **As Reported**, `MRQ`/`MRY`/`MRT` = **Most Recent Reported** (Q/Y/T = quarterly/annual/TTM). As-reported is the point-in-time view; most-recent is the restated view. Both are carried, which is exactly the raw-vs-derived split Phase 6 needs. | **A** |
| **Delisted securities** | SF1 covers active **and delisted** companies; described as "99% survivorship bias free". SEP covers 25,000+ **active and delisted** securities. | **B** |
| **Universe size** | ~16,000–18,000 companies (SF1); 25,000+ securities (SEP). Sources disagree on the exact figure. | **C** |
| **Historical coverage** | "Deep history to 1998" for both SF1 and SEP, repeated across sources. One extract instead says fundamentals reach 1990. **Unresolved and material — see §4.** | **B**/**C** |
| **Prices for `research-01`** | SEP provides three adjustment methods: unadjusted, split-adjusted, and split+dividend+spinoff adjusted. **Unadjusted is present**, which matters: the platform's `AdjustmentPolicyDeclaration` wants the raw series, and the scale-invariance work exists because adjusted-only series are lossy. | **B** |
| **Corporate actions** | Companion `ACTIONS` table (splits, dividends, spinoffs, ticker changes); `TICKERS` table for identifiers. | **B** |
| **Stable identifiers** | `permaticker` as a permanent security identifier alongside the mutable `ticker`. This maps cleanly onto the platform's existing surrogate-`instrument_id` model. | **C** |
| **Ticker changes / aliases** | Provided via `TICKERS`/`ACTIONS`. Maps onto the existing `symbol_mappings` dataset. | **C** |
| **Bulk vs API** | Both, via Nasdaq Data Link (bulk table export and REST). | **C** |
| **Pricing** | **NOT VERIFIED.** One extract cites $69/month from a January 2024 source; another cites a competitor advertising against Sharadar at $49/month. Sharadar's own subscribe page is blocked here. | **—** |
| **Licensing** | Personal/non-commercial tier exists; **professional or institutional licence required if the use relates to professional activity of any sort**. | **C** |

**The licensing line is the one to read carefully before purchase**, not the
price. "Professional activities of any sort" is broad, and which tier this
project falls under is a question about your intent, not about the data.

## 2. SEC EDGAR — the independent cross-check

Two distinct products, and the difference decides how they are used.

| product | coverage | contents | use here |
|---|---|---|---|
| **Financial Statement Data Sets** | **2009 Q1 onward** (first submissions 2009-04-15) | `sub` / `num` / `tag` / `pre`, keyed on `adsh` (accession), "as filed", uncorrected | **value-level** cross-check, 2009+ only |
| **full-index** (`master.idx`, `form.idx`) | **1993 Q1 onward** | pipe-delimited: company, CIK, form type, **filing date**, accession URL | **filing-date** cross-check, whole span |

**This changes the cross-check design from the one proposed in
`PHASE_06_DESIGN.md`.** That document assumed the Financial Statement Data Sets
would serve as the cross-check; they cannot, before 2009. But the *full-index*
runs from 1993 and carries exactly the field that matters — the filing date —
so a `datekey` verification is possible across the entire proposed span,
including the pre-2009 years where the risk is highest.

Both are free and public. XBRL, and therefore machine-readable values, only
exists from 2009; pre-2009 fundamentals must have been parsed from filing text
by whoever supplies them.

## 3. Other options, briefly

| vendor | verdict |
|---|---|
| **Compustat Point-in-Time** (S&P) | The reference implementation, and almost certainly disproportionate in cost and licensing for this project. Not investigated further. |
| **Tiingo fundamentals** | Already an authenticated provider here, so cheapest to integrate, but delisted coverage is the weak point and that is the entire problem being solved. Would close the fundamentals BLOCK and leave the survivorship FAIL open. |
| **FMP** | **Excluded by standing project rule** for fundamentals, ratios, earnings, estimates, statements, OHLCV, insider and institutional data. |

## 4. `research-01` start date

**Recommendation: 1998-01-01, conditional on one verification. Fall back to
2003-01-01 if it fails.**

The conditional is not hedging — it is the single highest-risk assumption in
this plan, and it is cheap to test.

**The risk.** Sharadar advertises history to 1998, but XBRL does not exist
before 2009. Pre-2009 fundamentals were necessarily derived by parsing filing
documents. The values are one question; **the `datekey` is a different and more
important one**, because a `datekey` that was *reconstructed* rather than taken
from the filing record would make the pre-2009 segment silently non-point-in-time
— and a corpus that is point-in-time after 2009 and quietly is not before it is
worse than one that starts in 2009, because the defect is invisible in
aggregate.

**The test, which needs no purchase beyond a trial and no code beyond a
script.** Take a sample of SF1 rows with `datekey` in 1998–2008, look each
company's filings up in the EDGAR quarterly full-index for the same period, and
compare `datekey` against the index's filing date for the corresponding
accession. Agreement within the expected filing-to-availability lag confirms the
pre-2009 segment is genuinely point-in-time. Systematic disagreement — or
`datekey` values that cluster on period-ends rather than filing dates — condemns
it.

**Why 1998 rather than 2003 if it passes.** 2002-12-31 gives one full bear
market (2000–02, partially) plus 2007–09. Starting 1998-01-01 gives the complete
2000–02 decline plus its run-up, and 2007–09 — two full cycles rather than one
and a half. A universe that has only seen one regime teaches one regime. If the
verification fails, 2003-01-01 is the honest floor, because it starts *after*
the questionable segment rather than straddling it.

## 5. Recommendation

**Sharadar Core US Equities Bundle (SF1 + SEP + TICKERS + ACTIONS) as the
corpus source, with SEC EDGAR full-index as the independent filing-date
cross-check and the Financial Statement Data Sets as a value-level cross-check
from 2009.**

It is the only option found that closes the fundamentals BLOCK *and* the
survivorship FAIL in one acquisition, and it carries the two things the Phase 6
architecture actually depends on: a filing date distinct from the fiscal period,
and an as-reported dimension distinct from the restated one. The unadjusted
price series makes it usable for `research-01` prices too, so `research-01` need
not straddle two vendors — which was the reason for rejecting option B.

**Before any purchase, three things need answers I could not get from here:**

1. **Current price and tier** — `sharadar.com/subscribe` and
   `data.nasdaq.com/databases/SFA`. Five minutes on an unblocked network.
2. **Which licence tier this project requires.** "Professional activities of any
   sort" is broad. This is a question about your intent and I should not answer
   it for you.
3. **Whether redistribution/retention terms permit** keeping an immutable local
   raw-facts table indefinitely, which is what the Phase 6 architecture requires
   and what makes provenance reproducible.

## 6. Proposed next milestone

**Milestone 1b — a trial-data contract probe.** Take the trial or lowest tier,
pull a bounded sample, and answer the §4 question empirically before committing
to a start date or a full acquisition. Concretely:

* sample SF1 `ARQ` rows across 1998–2008 and 2009–2026;
* fetch the matching EDGAR quarterly full-index files (free);
* report `datekey` versus index filing date, by year;
* report `permaticker` stability across known ticker changes;
* report delisted-security coverage against a list of names known to have
  delisted in the window.

Output: a written verification with real numbers. Gate: **your approval before
any full purchase or any Phase 6 schema work.**

Nothing in Milestone 1b computes a return, a win rate, or any performance
statistic, and no detector threshold is touched.

## Sources

- [Sharadar Core US Equities Bundle — Nasdaq Data Link](https://data.nasdaq.com/databases/SFA) *(blocked from this environment)*
- [Sharadar documentation — Fundamentals](https://sharadar.com/docs/fundamentals) *(blocked)*
- [Sharadar — Stock Price Data](https://sharadar.com/prices) *(blocked)*
- [Sharadar — Subscribe](https://sharadar.com/subscribe) *(blocked; the authoritative pricing page)*
- [quantrocket-client `fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py) — read directly; source of the DATEKEY/dimension findings
- [QuantRocket — Sharadar data](https://www.quantrocket.com/sharadar/) *(blocked)*
- [SEC — Financial Statement Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets) *(blocked)*
- [SEC — Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) *(blocked)*
- [python-edgar — quarterly index files since 1993](https://github.com/edgarminers/python-edgar)
- [Notre Dame SRAF — SEC/EDGAR master index data](https://sraf.nd.edu/sec-edgar-data/master-index-data/)
