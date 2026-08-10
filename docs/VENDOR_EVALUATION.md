# Data Vendor Evaluation

Prepared at the end of Phase 3, before Phase 4 begins, as the brief requires.

> ## Read this first
>
> **Pricing and feature details below are indicative and must be verified before
> any commitment.** Vendor pricing, tier boundaries and licensing terms change
> frequently and are often negotiated rather than listed. Every figure here
> should be treated as an order-of-magnitude starting point for a quote request,
> not a quotation.
>
> **The point-in-time claims are the ones that need direct testing**, not
> reading. Several vendors describe their data as "point-in-time" while meaning
> only that they timestamp their own ingestion. Section 6 is a concrete
> acceptance test to run against a trial before signing anything.

---

## 0. How to read the evidence in this document

Added at the empirical validation gate, because a document that mixes "we tested
this" with "their marketing page says this" is a document whose reader has to
guess which is which — and readers guess generously.

Every factual claim about a vendor carries one of four labels:

| Label | Means | What you may do with it |
|---|---|---|
| **VERIFIED** | Someone on this project ran the acceptance test in §6 against a trial and observed this. | Rely on it. Record the date and the trial's scope. |
| **DOCUMENTATION CLAIM** | The vendor's own docs or marketing say so. Not independently observed. | Use it to shortlist. Never to conclude. |
| **NEEDS VERIFICATION** | Plausible, commonly reported, not confirmed here. | Turn it into a question for the sales call. |
| **UNKNOWN** | Not established either way. | Say so. Do not fill the gap with a guess. |

**Everything in §2, §3 and §4 of this document is DOCUMENTATION CLAIM or
NEEDS VERIFICATION.** No vendor trial has been run. This environment's egress
policy denies the provider hosts (see `docs/PHASE_05_GATE.md` §2), so nothing
here has been observed directly, and the tier descriptions are a synthesis of
publicly described capability rather than measurement.

Pricing specifically is **UNKNOWN**. Figures are not quoted in this document
because vendor pricing is negotiated, tiered, and changes without notice; an
"order of magnitude" invented from memory would be treated as a quotation the
moment it was written down. Get quotes.

The one claim in this document that is **VERIFIED** is the negative one in §7:
the provider abstraction works, because Phase 3 was built and run against it.

### 0.1 What has since been observed against live free-tier accounts

Not a vendor trial in the §6 sense — no point-in-time acceptance test was run,
no fundamentals were touched, and the accounts are free tiers rather than
representative subscriptions. Recorded here because it is the only directly
observed vendor behaviour this project has, and because it changed a design
decision (see `ADR-0026`).

Observed by the project owner from their own machine, not from this build
environment, which still cannot reach any provider host:

| Claim | Label | Detail |
|---|---|---|
| Twelve Data `/time_series` serves daily bars on a free key | **VERIFIED** | Smoke test over SPY and AAPL succeeded. |
| Twelve Data `/splits` is **not** available on that free plan | **VERIFIED** | The endpoint returned a provider error naming the plan. |
| FMP `/stable/splits` serves split history on a free key | **VERIFIED** | `?symbol=AAPL` returned real records. |
| Twelve Data daily bars are split-adjusted | **DOCUMENTATION CLAIM** | The vendor documents it. Not independently confirmed, and the raw-price reconstruction is built on top of this claim. |
| Twelve Data's split factors are the reciprocal of the share count | **VERIFIED (2 observations)** | AAPL 2014 came back as 0.142857…, AAPL 2020 as 0.25 — 1/7 and 1/4 of the real corporate actions. See `docs/VENDOR_SEMANTICS.md` §1. |
| Twelve Data's `/time_series` `end_date` is exclusive | **NEEDS VERIFICATION** | A request through 2025-12-31 returned data through 2025-12-30 for two symbols; 2025-12-31 was a session. Best explanation, not confirmed against documentation. Handled by a probe-and-trim that is correct under either semantic. See §2. |
| FMP split coverage is complete for a given security | **UNKNOWN** | No way to establish it from one vendor. A missing split is invisible in the data. |
| Either vendor supplies corporate-action *announcement* timestamps | **NOT AVAILABLE** | FMP's `/stable/splits` carries an effective date only. Twelve Data's endpoint was not reachable on this plan to check. |

Nothing about fundamentals, filing timestamps, delisted coverage or historical
universe membership was tested at either vendor. Those remain as §2–§4 describe
them, which is to say unverified.

---

## 1. What we actually need

Ordered by the priority the brief sets, which is deliberately not price-first:

| # | Requirement | Why it ranks here | Phase blocked |
|---|---|---|---|
| 1 | **Point-in-time correctness** | A vendor that serves only current values makes every backtest fiction. Unfixable downstream. | 6, 9 |
| 2 | **Filing / announcement timestamps** | Without real filing dates, fundamental screens get ~6 weeks of hindsight per factor. An assumed lag is wrong in both directions. | 6 |
| 3 | **Delisted securities** | Survivorship bias. Overstates returns by an unknown amount that grows with lookback. | 4, 9 |
| 4 | **Historical universe integrity** | Membership and identity intervals. Without them, ticker recycling splices unrelated price histories. | 4 |
| 5 | **History depth** | Bounds how many regimes a backtest can span. Ten years covers one cycle; twenty covers three. | 9 |
| 6 | **Reliability** | Uptime, revision discipline, support responsiveness. | 11, 12 |
| 7 | **Cost** | Last. A cheap vendor without filing dates produces untrustworthy backtests, which is worse than none. | — |

### Datasets, and which are already unblocked

| Dataset | Needed by | Status |
|---|---|---|
| Historical OHLCV (daily, unadjusted) | Phase 3 ✅ | **Not blocking.** The provider abstraction works; the synthetic provider proves the pipeline. |
| Corporate actions | Phase 3 ✅ | Not blocking — bitemporal handling built and tested. |
| Point-in-time fundamentals | Phase 6 | **Blocking.** |
| Actual filing timestamps | Phase 6 | **Blocking, and the hardest to source.** |
| Earnings dates + surprise | Phase 5, 6 | Blocking Phase 6. |
| Delisted securities | Phase 4, 9 | **Blocking honest backtests.** |
| Historical symbol mappings | Phase 4 | Blocking a survivorship-safe universe. |
| Historical universe membership | Phase 4 | Blocking. |
| Historical sector/industry (GICS) | Phase 3 partial | **Degraded, not blocked** — ETF proxies work; constituent aggregation waits. |
| News / sentiment | Unscheduled | Not needed. |
| Macro | Phase 3 optional | Not blocking; FRED is free and bitemporal. |

---

## 2. Candidate landscape

Categories rather than a ranked list, because the right answer is almost
certainly a *combination* — no single affordable vendor covers all nine
datasets well.

### Tier A — Institutional, full point-in-time coverage

*Evidence label for this whole subsection: **DOCUMENTATION CLAIM**. No trial
run; the capabilities below are as publicly described by the vendors.*

Typified by S&P Global (Compustat point-in-time), Refinitiv/LSEG, FactSet,
MSCI (for GICS licensing), and CRSP for academic-grade survivorship-safe US
equity history.

- **Point-in-time**: genuine. Compustat's PIT product is the reference standard
  for as-filed fundamentals with restatement history.
- **Filing timestamps**: yes, this is precisely what distinguishes the tier.
- **Delisted**: complete, with delisting returns and reasons.
- **GICS**: MSCI/S&P own the standard; historical classification is licensable.
- **Depth**: decades.
- **Cost**: enterprise, typically five figures annually and up, often with
  multi-year terms and redistribution restrictions.
- **Verdict**: correct data, and likely out of proportion for a single-user
  platform unless the capital at risk justifies it.

### Tier B — Professional API vendors

*Evidence label for this whole subsection: **NEEDS VERIFICATION**. This is the
tier where the acceptance test in §6 actually decides the answer, because the
phrase "point-in-time" is used across it to mean at least two different things.*

Typified by Polygon.io, Nasdaq Data Link, Intrinio, FactSet's lighter offerings,
Sharadar (via Nasdaq Data Link), and Norgate Data.

- **Point-in-time**: **varies enormously, and this is the crux.** Sharadar's
  SF1 explicitly provides as-reported fundamentals with a `datekey` filing
  date — that is the shape we need. Others provide only the current revision.
- **Filing timestamps**: date-level rather than intraday-timestamp level for
  most. Date-level is workable: our model needs to know a filing was public
  before a decision, and a date plus a conservative same-day-close assumption is
  defensible if recorded as `ESTIMATED`.
- **Delisted**: Norgate and Sharadar are notable for including them; several
  price APIs quietly do not.
- **Depth**: typically 10–20 years.
- **Cost**: roughly tens to low hundreds of dollars per month per dataset for
  individual/professional tiers — the range where this project realistically
  sits.
- **Verdict**: **the tier to shortlist.** Expect to combine two vendors.

### Tier C — Retail / free

*Evidence label for this whole subsection: **NEEDS VERIFICATION**, except the
survivorship gap, which is **DOCUMENTATION CLAIM** — these sources generally
describe themselves as covering listed securities, which is the same thing as
not covering delisted ones.*

Typified by yfinance, Alpha Vantage free tiers, and similar.

- **Point-in-time**: no.
- **Filing timestamps**: no.
- **Delisted**: no — and this is disqualifying on its own.
- **Adjusted prices only** in several cases, which ADR-0005 rules out.
- **Verdict**: **unusable for backtesting.** Acceptable only for eyeballing a
  chart during development. `ProviderCapabilities.backtest_grade` returns
  `False` for such a source and its caveats propagate into every report.

### Free, and genuinely good for one thing

- **SEC EDGAR** — filing dates and accepted timestamps are public and
  authoritative. Extracting structured financials from XBRL is real work, but
  the *timestamps* are exactly what the expensive vendors are charging for.
  Worth serious consideration as the filing-date source layered over a cheaper
  fundamentals vendor.
- **FRED** — macro series with genuine vintage/ALFRED support, which is
  bitemporal in the sense we need. Free.

---

## 3. Realistic combinations

| Option | Composition | Covers | Gaps | Indicative cost |
|---|---|---|---|---|
| **A. Minimum viable honest** | Tier-B price vendor with delisted support + Sharadar-style as-reported fundamentals + EDGAR for filing dates + FRED | 1–8 | Historical GICS | Low hundreds/month |
| **B. Price-only start** | Tier-B price vendor with delisted support only | Phases 3–5 | Fundamentals entirely | Tens/month |
| **C. Institutional** | Compustat PIT + CRSP + GICS licence | Everything | — | Enterprise |

**Recommendation: Option B now, Option A before Phase 6.**

The reasoning is that Phases 4 and 5 — pattern recognition and breakout
confirmation — need only price, volume, corporate actions and a
survivorship-safe universe. Those are the cheapest things to buy correctly.
Fundamentals are not needed until Phase 6, which buys several months to run the
acceptance test in section 6 against trials rather than committing on
literature.

What must **not** happen is starting Phase 6 on a vendor that lacks filing
dates and papering over it with an assumed lag. The platform supports that path
(`assumed_filing_lag_days`, `KnowledgeTimeSource.ESTIMATED`, caveats in every
report) precisely so the degradation is visible — but it is a degradation, and
choosing it should be deliberate.

---

## 4. Sector classification specifically

GICS is a licensed standard owned by MSCI and S&P. Historical constituent
classification is a commercial product. Three options:

1. **Licence GICS history.** Correct, and priced accordingly.
2. **ETF proxies** (implemented). Sector strength from the SPDR sector ETF's own
   price history. A legitimate point-in-time series, marked `source="etf_proxy"`
   so it is never confused with a constituent aggregate. Loses constituent
   breadth (percent of sector above its 50DMA) and sector-relative ranking.
3. **A free classification with effective dates.** SIC codes are in EDGAR and
   are dated, but the taxonomy is poor for this purpose.

**Phase 3 shipped option 2 and the interval schema for option 1.** When
historical GICS is licensed it loads into the existing `sectors` table with no
code change — the point-in-time contract is already built and tested.

**What the platform will not do** is apply today's classification to history.
That is stated in ADR-0011 and enforced by `SectorRepository`, which resolves
classification as of the clock's date and returns `None` where none existed.

---

## 5. Unresolved questions

These need answers from vendors, not from documentation:

1. **Does the fundamentals product expose an as-reported filing date per line
   item, or only per filing?** Restatements are per-line, and a single filing
   date attached to a restated statement loses which figures actually changed.
2. **Are restatements retained as separate records, or does the vendor
   overwrite?** This decides whether `restatement_of` can be populated at all.
3. **Are delisted securities' final prices and delisting reasons included, or
   just the absence of further data?** "The series stops" and "the company went
   to zero" are different facts, and only the second is usable.
4. **Are historical ticker mappings supplied with effective dates?** Without
   them, ticker recycling is undetectable.
5. **Is universe membership supplied historically**, or must it be reconstructed
   from listing/delisting dates?
6. **Are prices available unadjusted?** Adjusted-only disqualifies a vendor
   under ADR-0005.
7. **What are the redistribution and derived-data terms?** Some licences restrict
   storing derived values — which would affect `indicator_values`.
8. **What is the revision/correction policy and notification mechanism?** Our
   ingestion handles revisions bitemporally; we need to know when they occur.
9. **Rate limits and bulk access.** A 4,000-name daily universe over 20 years is
   a large initial backfill; per-symbol REST calls at typical rate limits could
   take weeks.

---

## 6. Acceptance test to run against a trial

Concrete, and answers more than any datasheet. Run against a trial key before
committing.

**T1 — Delisted securities.** Request daily bars for a company that delisted
several years ago (a bankruptcy, not an acquisition). Does the vendor return
data? Does it say why the series ended?
*Fail = survivorship-unsafe. Disqualifying for Phase 9.*

**T2 — Ticker recycling.** Pick a ticker known to have been reassigned. Does the
vendor return one spliced series or two, and does it supply effective dates?
*Fail = the universe cannot be made point-in-time.*

**T3 — Filing date.** Pick a company that restated a quarter. Request that
quarter's revenue. Does the response carry the *original* filing date and the
*original* value, or only the restated one?
*Fail = fundamentals are not point-in-time, whatever the marketing says.*

**T4 — Unadjusted prices.** Request bars spanning a known split. Are raw prices
available? Is the split available as a separate corporate action with an
announcement date?
*Fail = ADR-0005 violated; the price series encodes future actions.*

**T5 — As-of reproducibility.** Request the same historical window twice, weeks
apart. Do the values differ? If so, is there any way to request the earlier
vintage?
*Silent change with no vintage access = replay is impossible.*

**T6 — Backfill throughput.** Time a 100-symbol, 10-year daily request. Multiply
out to 4,000 symbols and 20 years.
*If the projection exceeds a few days, bulk file access is required rather than
a REST API.*

Each test maps to a `ProviderCapabilities` field, so the result is recorded in
code rather than in someone's memory:

```python
ProviderCapabilities(
    supplies_reported_knowledge_time=...,   # T3
    supplies_delisted_instruments=...,      # T1
    supplies_unadjusted_prices=...,         # T4
    supplies_restatements=...,              # T3
    earliest_available=...,                 # depth
    rate_limit_per_minute=...,              # T6
)
```

`caveats()` renders whatever is `False` into the strings that appear in
`backtest_runs.data_caveats` and in API responses — so a limitation travels with
every result built on it.

---

## 7. What Phase 3 proved about vendor independence

The abstraction holds. Phase 3 built 58 indicators, four analytical engines and
a feature registry entirely against the synthetic provider, with **no vendor
coupling anywhere above `tradeit.data.providers`**.

Concretely:

- The analytics layer consumes `OhlcvBar` domain objects, never vendor payloads.
- Adding a vendor is one adapter module plus a config change.
- The conformance suite (`tests/unit/test_synthetic_provider.py`) states the
  properties any adapter must satisfy, and a new adapter must pass it.
- Provider limitations already propagate: `ProviderCapabilities` is recorded on
  every ingestion run and its caveats reach backtest reports.

So the vendor decision is genuinely deferrable without accumulating debt — which
was the point of doing it this way, and is why Phase 4 can start without it.
